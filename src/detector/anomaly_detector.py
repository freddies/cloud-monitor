"""
Ensemble anomaly detector that combines Isolation Forest and LSTM models.
Manages feature extraction from raw metrics and produces Anomaly objects.
"""

import time
import numpy as np
from collections import defaultdict
from typing import List, Optional, Tuple, Dict

from src.common.config import config
from src.common.models import MetricPoint, Anomaly, MetricType, Severity
from src.common.logger import get_logger
from .isolation_forest_model import IsolationForestDetector
from .lstm_model import LSTMDetector

logger = get_logger(__name__)


# Thresholds for severity classification per metric type
SEVERITY_THRESHOLDS = {
    MetricType.CPU_USAGE.value: {
        Severity.LOW: 70,
        Severity.MEDIUM: 80,
        Severity.HIGH: 90,
        Severity.CRITICAL: 95,
    },
    MetricType.MEMORY_USAGE.value: {
        Severity.LOW: 75,
        Severity.MEDIUM: 85,
        Severity.HIGH: 92,
        Severity.CRITICAL: 97,
    },
    MetricType.DISK_USAGE.value: {
        Severity.LOW: 80,
        Severity.MEDIUM: 88,
        Severity.HIGH: 94,
        Severity.CRITICAL: 98,
    },
    MetricType.ERROR_RATE.value: {
        Severity.LOW: 1,
        Severity.MEDIUM: 5,
        Severity.HIGH: 10,
        Severity.CRITICAL: 25,
    },
    MetricType.REQUEST_LATENCY.value: {
        Severity.LOW: 500,
        Severity.MEDIUM: 1000,
        Severity.HIGH: 2000,
        Severity.CRITICAL: 5000,
    },
}


class AnomalyDetector:
    """
    Ensemble detector combining:
    1. Isolation Forest (multi-feature point anomalies)
    2. LSTM Autoencoder (temporal anomalies)
    3. Statistical thresholds (absolute value checks)

    Produces final anomaly decisions via weighted voting.
    """

    # Weights for the ensemble
    IF_WEIGHT = 0.4
    LSTM_WEIGHT = 0.35
    STAT_WEIGHT = 0.25

    def __init__(self):
        self.if_detector = IsolationForestDetector()
        self.lstm_detectors: Dict[str, LSTMDetector] = {}
        self._metric_buffers: Dict[str, List[float]] = defaultdict(list)
        self._metric_stats: Dict[str, Dict] = {}
        self.BUFFER_MAX_SIZE = 5000
        self._anomaly_count = 0
        self._total_checked = 0

    def initialize(self) -> bool:
        """Load pre-trained models if available."""
        loaded_if = self.if_detector.load()
        if loaded_if:
            logger.info("✅ Isolation Forest model loaded")

        # Load LSTM models for each metric type
        for mt in MetricType:
            lstm = LSTMDetector(model_name=f"lstm_{mt.value}")
            if lstm.load():
                self.lstm_detectors[mt.value] = lstm
                logger.info(f"✅ LSTM model loaded for {mt.value}")

        return loaded_if

    def process_metric(self, metric: MetricPoint) -> Optional[Anomaly]:
        """
        Process a single metric point through the ensemble detector.
        Returns an Anomaly if detected, else None.
        """
        self._total_checked += 1
        key = f"{metric.host}:{metric.metric_type}"

        # Update buffer
        self._metric_buffers[key].append(metric.value)
        if len(self._metric_buffers[key]) > self.BUFFER_MAX_SIZE:
            self._metric_buffers[key] = self._metric_buffers[key][
                -self.BUFFER_MAX_SIZE:
            ]

        # Update running statistics
        self._update_stats(key, metric.value)

        # Run detection ensemble
        scores = {}
        detections = {}

        # 1. Statistical detection
        stat_anomaly, stat_score = self._statistical_check(
            key, metric.metric_type, metric.value
        )
        scores["statistical"] = stat_score
        detections["statistical"] = stat_anomaly

        # 2. Isolation Forest detection
        if_anomaly, if_score = self._isolation_forest_check(key, metric)
        scores["isolation_forest"] = if_score
        detections["isolation_forest"] = if_anomaly

        # 3. LSTM detection
        lstm_anomaly, lstm_score = self._lstm_check(key, metric)
        scores["lstm"] = lstm_score
        detections["lstm"] = lstm_anomaly

        # Ensemble decision
        weighted_score = (
            self.STAT_WEIGHT * scores["statistical"]
            + self.IF_WEIGHT * scores["isolation_forest"]
            + self.LSTM_WEIGHT * scores["lstm"]
        )

        is_anomaly = weighted_score >= config.ml.anomaly_threshold or (
            sum(detections.values()) >= 2  # majority vote
        )

        if is_anomaly:
            self._anomaly_count += 1
            severity = self._determine_severity(
                metric.metric_type, metric.value, weighted_score
            )
            expected = self._get_expected_range(key)

            anomaly = Anomaly(
                metric_type=metric.metric_type,
                value=metric.value,
                expected_range=expected,
                anomaly_score=round(weighted_score, 4),
                severity=severity,
                host=metric.host,
                service=metric.service,
                model_used="ensemble",
                description=self._generate_description(
                    metric, severity, scores, detections
                ),
            )

            logger.warning(
                f"🚨 ANOMALY DETECTED: {metric.host}/{metric.metric_type} "
                f"value={metric.value:.2f} score={weighted_score:.4f} "
                f"severity={severity}"
            )
            return anomaly

        return None

    def process_batch(self, metrics: List[MetricPoint]) -> List[Anomaly]:
        """Process a batch of metrics. Returns list of detected anomalies."""
        anomalies = []
        for metric in metrics:
            result = self.process_metric(metric)
            if result:
                anomalies.append(result)
        return anomalies

    # ── Detection Methods ──────────────────────────────────────

    def _statistical_check(
        self, key: str, metric_type: str, value: float
    ) -> Tuple[bool, float]:
        """Z-score and IQR based statistical anomaly check."""
        stats = self._metric_stats.get(key)
        if not stats or stats["count"] < 30:
            return False, 0.0

        mean = stats["mean"]
        std = stats["std"]

        if std < 1e-10:
            return False, 0.0

        # Z-score
        z_score = abs(value - mean) / std

        # IQR check
        q1 = stats.get("q1", mean - std)
        q3 = stats.get("q3", mean + std)
        iqr = q3 - q1
        lower_bound = q1 - 1.5 * iqr
        upper_bound = q3 + 1.5 * iqr
        iqr_anomaly = value < lower_bound or value > upper_bound

        # Absolute threshold check
        thresholds = SEVERITY_THRESHOLDS.get(metric_type)
        abs_anomaly = False
        if thresholds:
            abs_anomaly = value >= thresholds.get(Severity.HIGH, float("inf"))

        # Combined score
        z_norm = min(1.0, z_score / 5.0)  # Normalize to 0-1
        iqr_score = 0.8 if iqr_anomaly else 0.0
        abs_score = 0.9 if abs_anomaly else 0.0

        combined_score = max(z_norm, iqr_score, abs_score)
        is_anomaly = z_score > 3.0 or iqr_anomaly or abs_anomaly

        return is_anomaly, combined_score

    def _isolation_forest_check(
        self, key: str, metric: MetricPoint
    ) -> Tuple[bool, float]:
        """Run Isolation Forest on the metric's feature vector."""
        if not self.if_detector.is_trained:
            return False, 0.0

        try:
            features = self._extract_features(key, metric)
            if features is None:
                return False, 0.0

            is_anomaly, score = self.if_detector.predict_single(features)
            return is_anomaly, score

        except Exception as e:
            logger.debug(f"IF check failed for {key}: {e}")
            return False, 0.0

    def _lstm_check(
        self, key: str, metric: MetricPoint
    ) -> Tuple[bool, float]:
        """Run LSTM detector on the metric's time series."""
        lstm = self.lstm_detectors.get(metric.metric_type)
        if not lstm or not lstm.is_trained:
            return False, 0.0

        buffer = self._metric_buffers.get(key, [])
        if len(buffer) < lstm.sequence_length:
            return False, 0.0

        try:
            history = np.array(buffer[-(lstm.sequence_length):])
            is_anomaly, score = lstm.predict_point(
                history[:-1], history[-1]
            )
            return is_anomaly, score

        except Exception as e:
            logger.debug(f"LSTM check failed for {key}: {e}")
            return False, 0.0

    # ── Feature Engineering ────────────────────────────────────

    def _extract_features(
        self, key: str, metric: MetricPoint
    ) -> Optional[np.ndarray]:
        """
        Extract a feature vector from the metric and its buffer.
        Features:
        - current value
        - rolling mean (5, 15, 30)
        - rolling std (5, 15, 30)
        - rate of change
        - z-score
        - min/max in window
        - percentile rank
        """
        buffer = self._metric_buffers.get(key, [])
        if len(buffer) < 30:
            return None

        values = np.array(buffer[-30:])
        current = metric.value

        # Rolling statistics
        mean_5 = np.mean(values[-5:])
        mean_15 = np.mean(values[-15:])
        mean_30 = np.mean(values)

        std_5 = np.std(values[-5:]) + 1e-10
        std_15 = np.std(values[-15:]) + 1e-10
        std_30 = np.std(values) + 1e-10

        # Rate of change
        if len(buffer) >= 2:
            roc = current - buffer[-2]
        else:
            roc = 0.0

        # Acceleration (second derivative)
        if len(buffer) >= 3:
            accel = (buffer[-1] - 2 * buffer[-2] + buffer[-3])
        else:
            accel = 0.0

        # Z-score relative to 30-point window
        z_score = (current - mean_30) / std_30

        # Range position
        min_30 = np.min(values)
        max_30 = np.max(values)
        range_30 = max_30 - min_30 + 1e-10
        range_position = (current - min_30) / range_30

        # Percentile rank
        percentile = np.searchsorted(np.sort(values), current) / len(values)

        # Deviation from rolling means
        dev_5 = (current - mean_5) / std_5
        dev_15 = (current - mean_15) / std_15

        features = np.array([
            current,
            mean_5,
            mean_15,
            mean_30,
            std_5,
            std_15,
            std_30,
            roc,
            accel,
            z_score,
            min_30,
            max_30,
            range_position,
            percentile,
            dev_5,
            dev_15,
        ])

        return features

    # ── Statistics ─────────────────────────────────────────────

    def _update_stats(self, key: str, value: float):
        """Update running statistics using Welford's algorithm."""
        if key not in self._metric_stats:
            self._metric_stats[key] = {
                "count": 0,
                "mean": 0.0,
                "m2": 0.0,
                "std": 0.0,
                "min": float("inf"),
                "max": float("-inf"),
                "q1": 0.0,
                "q3": 0.0,
            }

        stats = self._metric_stats[key]
        stats["count"] += 1
        n = stats["count"]

        # Welford's online algorithm
        delta = value - stats["mean"]
        stats["mean"] += delta / n
        delta2 = value - stats["mean"]
        stats["m2"] += delta * delta2
        stats["std"] = np.sqrt(stats["m2"] / max(n, 1))

        stats["min"] = min(stats["min"], value)
        stats["max"] = max(stats["max"], value)

        # Approximate quartiles using buffer
        buffer = self._metric_buffers.get(key, [])
        if len(buffer) >= 100:
            sorted_recent = sorted(buffer[-200:])
            stats["q1"] = sorted_recent[len(sorted_recent) // 4]
            stats["q3"] = sorted_recent[3 * len(sorted_recent) // 4]

    def _get_expected_range(self, key: str) -> Tuple[float, float]:
        """Get the expected normal range for a metric."""
        stats = self._metric_stats.get(key)
        if not stats or stats["count"] < 30:
            return (0.0, 100.0)

        lower = stats["mean"] - 2 * stats["std"]
        upper = stats["mean"] + 2 * stats["std"]
        return (round(lower, 2), round(upper, 2))

    # ── Severity & Description ─────────────────────────────────

    def _determine_severity(
        self, metric_type: str, value: float, score: float
    ) -> str:
        """Determine anomaly severity based on value and score."""
        thresholds = SEVERITY_THRESHOLDS.get(metric_type)

        if thresholds:
            if value >= thresholds.get(Severity.CRITICAL, float("inf")):
                return Severity.CRITICAL.value
            elif value >= thresholds.get(Severity.HIGH, float("inf")):
                return Severity.HIGH.value
            elif value >= thresholds.get(Severity.MEDIUM, float("inf")):
                return Severity.MEDIUM.value
            elif value >= thresholds.get(Severity.LOW, float("inf")):
                return Severity.LOW.value

        # Fall back to score-based severity
        if score >= 0.95:
            return Severity.CRITICAL.value
        elif score >= 0.85:
            return Severity.HIGH.value
        elif score >= 0.70:
            return Severity.MEDIUM.value
        return Severity.LOW.value

    def _generate_description(
        self,
        metric: MetricPoint,
        severity: str,
        scores: dict,
        detections: dict,
    ) -> str:
        """Generate a human-readable anomaly description."""
        detecting_models = [
            name for name, detected in detections.items() if detected
        ]
        stats = self._metric_stats.get(
            f"{metric.host}:{metric.metric_type}", {}
        )
        expected_mean = stats.get("mean", 0)
        expected_std = stats.get("std", 0)

        desc = (
            f"{severity.upper()} anomaly detected on {metric.host} "
            f"for {metric.metric_type}. "
            f"Current value: {metric.value:.2f}, "
            f"Expected: {expected_mean:.2f} ± {expected_std:.2f}. "
            f"Detected by: {', '.join(detecting_models)}. "
            f"Scores: {', '.join(f'{k}={v:.3f}' for k, v in scores.items())}."
        )
        return desc

    def get_stats(self) -> dict:
        """Return detector statistics."""
        return {
            "total_checked": self._total_checked,
            "anomalies_detected": self._anomaly_count,
            "anomaly_rate": (
                round(self._anomaly_count / max(self._total_checked, 1), 4)
            ),
            "if_trained": self.if_detector.is_trained,
            "lstm_models_loaded": len(self.lstm_detectors),
            "metric_series_tracked": len(self._metric_buffers),
            "buffer_sizes": {
                k: len(v) for k, v in list(self._metric_buffers.items())[:10]
            },
        }