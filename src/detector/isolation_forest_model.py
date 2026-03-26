"""
Isolation Forest based anomaly detector.
Good for detecting point anomalies in multi-dimensional data.
"""

import os
import numpy as np
import joblib
from typing import Tuple, Optional, List
from sklearn.ensemble import IsolationForest
from sklearn.preprocessing import StandardScaler

from src.common.config import config
from src.common.logger import get_logger

logger = get_logger(__name__)

MODEL_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "data", "models")
os.makedirs(MODEL_DIR, exist_ok=True)


class IsolationForestDetector:
    """Isolation Forest anomaly detector with online-style re-training."""

    def __init__(
        self,
        contamination: float = None,
        n_estimators: int = 200,
        model_name: str = "isolation_forest",
    ):
        self.contamination = (
            contamination or config.ml.isolation_forest_contamination
        )
        self.n_estimators = n_estimators
        self.model_name = model_name
        self.model: Optional[IsolationForest] = None
        self.scaler = StandardScaler()
        self.is_trained = False
        self._training_samples = 0

    def train(self, data: np.ndarray) -> dict:
        """
        Train the Isolation Forest model.
        data shape: (n_samples, n_features)
        """
        if len(data) < 50:
            logger.warning("Not enough data to train (need >= 50 samples)")
            return {"status": "insufficient_data", "samples": len(data)}

        logger.info(f"Training Isolation Forest on {data.shape} data...")

        # Scale features
        scaled_data = self.scaler.fit_transform(data)

        self.model = IsolationForest(
            n_estimators=self.n_estimators,
            contamination=self.contamination,
            max_samples="auto",
            random_state=42,
            n_jobs=-1,
            warm_start=False,
        )

        self.model.fit(scaled_data)
        self.is_trained = True
        self._training_samples = len(data)

        # Compute training stats
        scores = self.model.decision_function(scaled_data)
        predictions = self.model.predict(scaled_data)
        n_anomalies = (predictions == -1).sum()

        stats = {
            "status": "trained",
            "samples": len(data),
            "features": data.shape[1],
            "anomalies_in_training": int(n_anomalies),
            "anomaly_rate": round(n_anomalies / len(data), 4),
            "score_mean": round(float(scores.mean()), 4),
            "score_std": round(float(scores.std()), 4),
        }

        logger.info(f"Training complete: {stats}")
        return stats

    def predict(self, data: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
        """
        Predict anomalies.
        Returns:
            labels: 1 = normal, -1 = anomaly
            scores: anomaly scores (lower = more anomalous)
        """
        if not self.is_trained:
            raise RuntimeError("Model not trained yet")

        scaled_data = self.scaler.transform(data)
        labels = self.model.predict(scaled_data)
        scores = self.model.decision_function(scaled_data)
        return labels, scores

    def predict_single(self, features: np.ndarray) -> Tuple[bool, float]:
        """
        Predict whether a single data point is anomalous.
        Returns: (is_anomaly: bool, anomaly_score: float 0-1)
        """
        if not self.is_trained:
            return False, 0.0

        features_2d = features.reshape(1, -1)
        labels, scores = self.predict(features_2d)

        is_anomaly = labels[0] == -1
        # Normalize score to 0-1 (higher = more anomalous)
        raw_score = -scores[0]  # negate so higher = more anomalous
        norm_score = 1 / (1 + np.exp(-raw_score))  # sigmoid normalization
        return is_anomaly, float(norm_score)

    def save(self, path: str = None):
        """Save model to disk."""
        if not self.is_trained:
            return
        path = path or os.path.join(MODEL_DIR, f"{self.model_name}.joblib")
        joblib.dump(
            {
                "model": self.model,
                "scaler": self.scaler,
                "training_samples": self._training_samples,
                "contamination": self.contamination,
            },
            path,
        )
        logger.info(f"Model saved to {path}")

    def load(self, path: str = None) -> bool:
        """Load model from disk."""
        path = path or os.path.join(MODEL_DIR, f"{self.model_name}.joblib")
        if not os.path.exists(path):
            logger.warning(f"No model found at {path}")
            return False
        data = joblib.load(path)
        self.model = data["model"]
        self.scaler = data["scaler"]
        self._training_samples = data["training_samples"]
        self.contamination = data["contamination"]
        self.is_trained = True
        logger.info(f"Model loaded from {path} ({self._training_samples} training samples)")
        return True