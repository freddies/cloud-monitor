"""
Model trainer – collects historical data from Redis and trains/retrains models.
Runs periodically as part of the detector service.
"""

import time
import numpy as np
from collections import defaultdict
from typing import Dict, List, Optional

from src.common.config import config
from src.common.redis_client import RedisClient
from src.common.models import MetricType
from src.common.logger import get_logger
from .isolation_forest_model import IsolationForestDetector
from .lstm_model import LSTMDetector

logger = get_logger(__name__)


class ModelTrainer:
    """Handles model training and retraining from stored metric data."""

    def __init__(self, redis_client: RedisClient):
        self.redis = redis_client
        self._last_train_time = 0

    def should_retrain(self) -> bool:
        """Check if enough time has passed for retraining."""
        elapsed_hours = (time.time() - self._last_train_time) / 3600
        return elapsed_hours >= config.ml.retrain_interval_hours

    def train_all_models(
        self,
    ) -> Dict[str, dict]:
        """
        Train all models using data from Redis.
        Returns dict of model_name -> training_stats.
        """
        logger.info("🏋️ Starting model training...")
        results = {}

        # Gather data from Redis
        training_data = self._gather_training_data()

        if not training_data:
            logger.warning("No training data available")
            return {"status": "no_data"}

        # 1. Train Isolation Forest (multi-feature)
        results["isolation_forest"] = self._train_isolation_forest(training_data)

        # 2. Train LSTM models per metric type
        for metric_type in MetricType:
            mt = metric_type.value
            lstm_results = self._train_lstm(mt, training_data)
            results[f"lstm_{mt}"] = lstm_results

        self._last_train_time = time.time()
        logger.info(f"✅ Training complete. Results: {list(results.keys())}")
        return results

    def _gather_training_data(self) -> Dict[str, Dict[str, List[float]]]:
        """
        Gather metric data from Redis organized by host:metric_type.
        Returns: {host: {metric_type: [values...]}}
        """
        data = defaultdict(lambda: defaultdict(list))

        # Get all metric time-series keys
        keys = self.redis.get_all_metric_keys()
        logger.info(f"Found {len(keys)} metric series in Redis")

        for key in keys:
            try:
                # Key format: ts:metrics:host:metric_type
                parts = key.split(":")
                if len(parts) >= 4:
                    host = parts[2]
                    metric_type = parts[3]
                else:
                    continue

                # Get historical data
                history = self.redis.get_metric_history(
                    host=host,
                    metric_type=metric_type,
                    minutes=config.collector.retention_hours * 60,
                )

                values = [point.get("value", 0) for point in history]
                if values:
                    data[host][metric_type] = values

            except Exception as e:
                logger.error(f"Error gathering data from {key}: {e}")

        total_points = sum(
            len(v) for host_data in data.values() for v in host_data.values()
        )
        logger.info(
            f"Gathered {total_points} data points from {len(data)} hosts"
        )
        return dict(data)

    def _train_isolation_forest(
        self, training_data: Dict[str, Dict[str, List[float]]]
    ) -> dict:
        """Train Isolation Forest on feature vectors from all metric series."""
        logger.info("Training Isolation Forest...")

        feature_vectors = []

        for host, metrics_by_type in training_data.items():
            # For each host, create feature vectors from available metrics
            # Align metric types by index (assuming synchronized collection)
            metric_types = sorted(metrics_by_type.keys())
            if not metric_types:
                continue

            # Find minimum length across all metric types for this host
            min_len = min(len(metrics_by_type[mt]) for mt in metric_types)
            if min_len < 30:
                continue

            for i in range(30, min_len):
                features = []
                for mt in metric_types:
                    values = metrics_by_type[mt]
                    window = values[max(0, i - 30): i + 1]
                    current = values[i]

                    # Extract features
                    arr = np.array(window)
                    features.extend([
                        current,
                        np.mean(arr[-5:]),
                        np.mean(arr[-15:]),
                        np.mean(arr),
                        np.std(arr[-5:]),
                        np.std(arr[-15:]),
                        np.std(arr),
                        current - values[i - 1] if i > 0 else 0,
                        (
                            values[i] - 2 * values[i - 1] + values[i - 2]
                            if i >= 2
                            else 0
                        ),
                        (current - np.mean(arr)) / (np.std(arr) + 1e-10),
                        np.min(arr),
                        np.max(arr),
                        (
                            (current - np.min(arr))
                            / (np.max(arr) - np.min(arr) + 1e-10)
                        ),
                        np.searchsorted(np.sort(arr), current) / len(arr),
                        (current - np.mean(arr[-5:])) / (np.std(arr[-5:]) + 1e-10),
                        (current - np.mean(arr[-15:])) / (np.std(arr[-15:]) + 1e-10),
                    ])

                feature_vectors.append(features)

        if not feature_vectors:
            return {"status": "no_feature_vectors"}

        # Ensure consistent feature dimensions
        min_features = min(len(fv) for fv in feature_vectors)
        feature_vectors = [fv[:min_features] for fv in feature_vectors]

        X = np.array(feature_vectors)
        logger.info(f"Training IF on matrix of shape {X.shape}")

        # Replace NaN/Inf
        X = np.nan_to_num(X, nan=0.0, posinf=1e6, neginf=-1e6)

        detector = IsolationForestDetector()
        stats = detector.train(X)
        detector.save()

        return stats

    def _train_lstm(
        self, metric_type: str, training_data: Dict[str, Dict[str, List[float]]]
    ) -> dict:
        """Train an LSTM model for a specific metric type."""
        logger.info(f"Training LSTM for {metric_type}...")

        # Concatenate all series for this metric type
        all_values = []
        for host, metrics_by_type in training_data.items():
            if metric_type in metrics_by_type:
                values = metrics_by_type[metric_type]
                if len(values) > config.ml.lstm_sequence_length:
                    all_values.extend(values)

        if len(all_values) < config.ml.lstm_sequence_length + 50:
            return {
                "status": "insufficient_data",
                "metric_type": metric_type,
                "samples": len(all_values),
            }

        data = np.array(all_values, dtype=np.float32)
        data = np.nan_to_num(data, nan=0.0)

        lstm = LSTMDetector(model_name=f"lstm_{metric_type}")

        try:
            stats = lstm.train(data)
            lstm.save()
            return stats
        except Exception as e:
            logger.error(f"LSTM training failed for {metric_type}: {e}")
            return {"status": "error", "error": str(e)}

    def generate_training_report(self, results: Dict[str, dict]) -> str:
        """Generate a human-readable training report."""
        lines = ["=" * 60, "MODEL TRAINING REPORT", "=" * 60, ""]

        for model_name, stats in results.items():
            lines.append(f"📊 {model_name}")
            lines.append("-" * 40)
            for key, value in stats.items():
                lines.append(f"  {key}: {value}")
            lines.append("")

        return "\n".join(lines)