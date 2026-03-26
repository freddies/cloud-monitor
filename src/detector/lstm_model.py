"""
LSTM-based anomaly detector for time-series metric data.
Uses reconstruction error to detect anomalies.
"""

import os
import numpy as np
from typing import Tuple, Optional

from src.common.config import config
from src.common.logger import get_logger

logger = get_logger(__name__)

MODEL_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "data", "models")
os.makedirs(MODEL_DIR, exist_ok=True)


class LSTMDetector:
    """
    LSTM Autoencoder for time-series anomaly detection.
    Learns normal patterns and flags high reconstruction errors as anomalies.
    """

    def __init__(
        self,
        sequence_length: int = None,
        n_features: int = 1,
        model_name: str = "lstm_detector",
    ):
        self.sequence_length = sequence_length or config.ml.lstm_sequence_length
        self.n_features = n_features
        self.model_name = model_name
        self.model = None
        self.threshold = None
        self.is_trained = False
        self._mean = None
        self._std = None

    def _build_model(self):
        """Build LSTM Autoencoder architecture."""
        try:
            import tensorflow as tf
            from tensorflow.keras.models import Model
            from tensorflow.keras.layers import (
                Input,
                LSTM,
                Dense,
                RepeatVector,
                TimeDistributed,
                Dropout,
            )

            tf.random.set_seed(42)

            inputs = Input(shape=(self.sequence_length, self.n_features))

            # Encoder
            x = LSTM(64, activation="relu", return_sequences=True)(inputs)
            x = Dropout(0.2)(x)
            x = LSTM(32, activation="relu", return_sequences=False)(x)
            encoded = Dropout(0.2)(x)

            # Decoder
            x = RepeatVector(self.sequence_length)(encoded)
            x = LSTM(32, activation="relu", return_sequences=True)(x)
            x = Dropout(0.2)(x)
            x = LSTM(64, activation="relu", return_sequences=True)(x)
            decoded = TimeDistributed(Dense(self.n_features))(x)

            self.model = Model(inputs, decoded)
            self.model.compile(optimizer="adam", loss="mse")

            logger.info(
                f"LSTM model built: seq_len={self.sequence_length}, "
                f"features={self.n_features}"
            )

        except ImportError:
            logger.error(
                "TensorFlow not installed – LSTM detector unavailable"
            )
            raise

    def train(self, data: np.ndarray, validation_split: float = 0.1) -> dict:
        """
        Train the LSTM autoencoder.
        data: shape (n_samples,) or (n_samples, n_features)
        """
        if len(data) < self.sequence_length + 50:
            return {"status": "insufficient_data", "samples": len(data)}

        logger.info(f"Training LSTM on {len(data)} data points...")

        # Normalize
        if data.ndim == 1:
            data = data.reshape(-1, 1)
        self._mean = data.mean(axis=0)
        self._std = data.std(axis=0) + 1e-8
        normalized = (data - self._mean) / self._std

        # Create sequences
        X = self._create_sequences(normalized)
        logger.info(f"Created {len(X)} sequences of length {self.sequence_length}")

        # Build and train
        self.n_features = X.shape[2]
        self._build_model()

        history = self.model.fit(
            X,
            X,  # autoencoder: input = target
            epochs=config.ml.lstm_epochs,
            batch_size=config.ml.lstm_batch_size,
            validation_split=validation_split,
            shuffle=True,
            verbose=0,
        )

        # Compute reconstruction error threshold
        reconstructions = self.model.predict(X, verbose=0)
        mse = np.mean(np.power(X - reconstructions, 2), axis=(1, 2))
        self.threshold = np.percentile(mse, 95)  # 95th percentile

        self.is_trained = True

        stats = {
            "status": "trained",
            "samples": len(data),
            "sequences": len(X),
            "final_loss": float(history.history["loss"][-1]),
            "val_loss": float(history.history.get("val_loss", [0])[-1]),
            "threshold": float(self.threshold),
            "mse_mean": float(mse.mean()),
            "mse_std": float(mse.std()),
        }

        logger.info(f"LSTM training complete: {stats}")
        return stats

    def predict_sequence(
        self, sequence: np.ndarray
    ) -> Tuple[bool, float, float]:
        """
        Predict if a sequence is anomalous.
        Returns: (is_anomaly, anomaly_score 0-1, reconstruction_error)
        """
        if not self.is_trained:
                        return False, 0.0, 0.0

        # Normalize
        if sequence.ndim == 1:
            sequence = sequence.reshape(-1, 1)
        normalized = (sequence - self._mean) / self._std

        # Ensure correct shape
        if len(normalized) < self.sequence_length:
            # Pad with zeros
            pad_len = self.sequence_length - len(normalized)
            normalized = np.vstack(
                [np.zeros((pad_len, self.n_features)), normalized]
            )
        elif len(normalized) > self.sequence_length:
            normalized = normalized[-self.sequence_length:]

        # Reshape for prediction
        X = normalized.reshape(1, self.sequence_length, self.n_features)

        # Reconstruct
        reconstruction = self.model.predict(X, verbose=0)
        mse = np.mean(np.power(X - reconstruction, 2))

        # Compute anomaly score (0-1, higher = more anomalous)
        if self.threshold > 0:
            anomaly_score = min(1.0, mse / (2 * self.threshold))
        else:
            anomaly_score = 0.0

        is_anomaly = mse > self.threshold

        return is_anomaly, float(anomaly_score), float(mse)

    def predict_point(
        self, history: np.ndarray, new_point: float
    ) -> Tuple[bool, float]:
        """
        Given recent history and a new point, determine if the new point is anomalous.
        history: array of recent values (at least sequence_length - 1)
        new_point: the latest value
        """
        if not self.is_trained:
            return False, 0.0

        # Append new point to history
        if history.ndim == 1:
            full_seq = np.append(history, new_point)
        else:
            new_row = np.array([[new_point]])
            full_seq = np.vstack([history, new_row])

        # Take the last sequence_length points
        if len(full_seq) < self.sequence_length:
            return False, 0.0

        seq = full_seq[-self.sequence_length:]
        is_anomaly, score, mse = self.predict_sequence(seq)
        return is_anomaly, score

    def _create_sequences(self, data: np.ndarray) -> np.ndarray:
        """Create overlapping sequences from time-series data."""
        sequences = []
        for i in range(len(data) - self.sequence_length + 1):
            sequences.append(data[i: i + self.sequence_length])
        return np.array(sequences)

    def save(self, path: str = None):
        """Save model and metadata."""
        if not self.is_trained:
            return

        path = path or os.path.join(MODEL_DIR, self.model_name)
        os.makedirs(path, exist_ok=True)

        # Save Keras model
        self.model.save(os.path.join(path, "model.keras"))

        # Save metadata
        import json
        metadata = {
            "threshold": float(self.threshold),
            "mean": self._mean.tolist(),
            "std": self._std.tolist(),
            "sequence_length": self.sequence_length,
            "n_features": self.n_features,
        }
        with open(os.path.join(path, "metadata.json"), "w") as f:
            json.dump(metadata, f)

        logger.info(f"LSTM model saved to {path}")

    def load(self, path: str = None) -> bool:
        """Load model and metadata."""
        path = path or os.path.join(MODEL_DIR, self.model_name)

        model_path = os.path.join(path, "model.keras")
        meta_path = os.path.join(path, "metadata.json")

        if not os.path.exists(model_path) or not os.path.exists(meta_path):
            logger.warning(f"No LSTM model found at {path}")
            return False

        try:
            import tensorflow as tf
            import json

            self.model = tf.keras.models.load_model(model_path)

            with open(meta_path, "r") as f:
                metadata = json.load(f)

            self.threshold = metadata["threshold"]
            self._mean = np.array(metadata["mean"])
            self._std = np.array(metadata["std"])
            self.sequence_length = metadata["sequence_length"]
            self.n_features = metadata["n_features"]
            self.is_trained = True

            logger.info(f"LSTM model loaded from {path}")
            return True

        except Exception as e:
            logger.error(f"Error loading LSTM model: {e}")
            return False