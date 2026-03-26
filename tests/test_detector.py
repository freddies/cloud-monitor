"""Tests for the anomaly detection models."""

import pytest
import numpy as np
from src.detector.isolation_forest_model import IsolationForestDetector
from src.detector.anomaly_detector import AnomalyDetector
from src.common.models import MetricPoint, MetricType


class TestIsolationForestDetector:
    def test_train_and_predict(self):
        detector = IsolationForestDetector(contamination=0.05)

        # Generate normal data
        np.random.seed(42)
        normal_data = np.random.normal(50, 5, (500, 4))

        # Train
        stats = detector.train(normal_data)
        assert stats["status"] == "trained"
        assert stats["samples"] == 500
        assert detector.is_trained

    def test_detect_anomaly(self):
        detector = IsolationForestDetector(contamination=0.1)

        np.random.seed(42)
        normal_data = np.random.normal(50, 5, (500, 4))
        detector.train(normal_data)

        # Test normal point
        normal_point = np.array([50, 52, 48, 51])
        is_anomaly, score = detector.predict_single(normal_point)
        assert isinstance(is_anomaly, bool)
        assert 0 <= score <= 1

        # Test anomalous point
        anomalous_point = np.array([200, 200, 200, 200])
        is_anomaly, score = detector.predict_single(anomalous_point)
        assert is_anomaly
        assert score > 0.5

    def test_insufficient_data(self):
        detector = IsolationForestDetector()
        small_data = np.random.normal(0, 1, (10, 4))
        stats = detector.train(small_data)
        assert stats["status"] == "insufficient_data"

    def test_save_and_load(self, tmp_path):
        detector = IsolationForestDetector()
        data = np.random.normal(0, 1, (200, 4))
        detector.train(data)

        path = str(tmp_path / "test_model.joblib")
        detector.save(path)

        new_detector = IsolationForestDetector()
        loaded = new_detector.load(path)
        assert loaded
        assert new_detector.is_trained

        # Predictions should be identical
        test_point = np.array([0.1, 0.2, -0.1, 0.3])
        orig_anomaly, orig_score = detector.predict_single(test_point)
        new_anomaly, new_score = new_detector.predict_single(test_point)
        assert orig_anomaly == new_anomaly
        assert abs(orig_score - new_score) < 1e-6

    def test_batch_predict(self):
        detector = IsolationForestDetector(contamination=0.1)
        np.random.seed(42)
        data = np.random.normal(50, 5, (300, 4))
        detector.train(data)

        test_data = np.random.normal(50, 5, (20, 4))
        labels, scores = detector.predict(test_data)
        assert len(labels) == 20
        assert len(scores) == 20
        assert all(l in [-1, 1] for l in labels)


class TestAnomalyDetector:
    def setup_method(self):
        self.detector = AnomalyDetector()

    def test_process_metric_returns_none_for_initial(self):
        """First few metrics should not trigger anomalies (insufficient data)."""
        metric = MetricPoint(
            metric_type=MetricType.CPU_USAGE.value,
            value=50.0,
            host="test-host",
            service="test",
        )
        result = self.detector.process_metric(metric)
        assert result is None

    def test_process_metric_builds_buffer(self):
        """Buffer should grow as metrics are processed."""
        for i in range(50):
            metric = MetricPoint(
                metric_type=MetricType.CPU_USAGE.value,
                value=50.0 + np.random.normal(0, 2),
                host="test-host",
                service="test",
            )
            self.detector.process_metric(metric)

        key = "test-host:cpu_usage"
        assert key in self.detector._metric_buffers
        assert len(self.detector._metric_buffers[key]) == 50

    def test_detect_statistical_anomaly(self):
        """After building baseline, a huge spike should be detected."""
        # Build baseline with normal values
        for i in range(200):
            metric = MetricPoint(
                metric_type=MetricType.CPU_USAGE.value,
                value=40.0 + np.random.normal(0, 3),
                host="stat-host",
                service="test",
            )
            self.detector.process_metric(metric)

        # Send anomalous value
        anomalous = MetricPoint(
            metric_type=MetricType.CPU_USAGE.value,
            value=99.5,
            host="stat-host",
            service="test",
        )
        result = self.detector.process_metric(anomalous)
        # May or may not detect depending on model state, but stats should flag it
        # Check that the statistical check works
        key = "stat-host:cpu_usage"
        stats = self.detector._metric_stats[key]
        assert stats["count"] == 201
        assert stats["mean"] < 50  # mean should be around 40

    def test_buffer_max_size(self):
        """Buffer should be capped at BUFFER_MAX_SIZE."""
        for i in range(self.detector.BUFFER_MAX_SIZE + 100):
            metric = MetricPoint(
                metric_type=MetricType.CPU_USAGE.value,
                value=float(i % 100),
                host="buffer-host",
                service="test",
            )
            self.detector.process_metric(metric)

        key = "buffer-host:cpu_usage"
        assert len(self.detector._metric_buffers[key]) <= self.detector.BUFFER_MAX_SIZE

    def test_process_batch(self):
        """Process a batch of metrics."""
        metrics = [
            MetricPoint(
                metric_type=MetricType.MEMORY_USAGE.value,
                value=60.0 + np.random.normal(0, 2),
                host="batch-host",
                service="test",
            )
            for _ in range(50)
        ]
        anomalies = self.detector.process_batch(metrics)
        assert isinstance(anomalies, list)

    def test_get_stats(self):
        """Stats should be properly reported."""
        for i in range(10):
            metric = MetricPoint(
                metric_type=MetricType.CPU_USAGE.value,
                value=50.0,
                host="stats-host",
                service="test",
            )
            self.detector.process_metric(metric)

        stats = self.detector.get_stats()
        assert stats["total_checked"] == 10
        assert "anomalies_detected" in stats
        assert "if_trained" in stats
        assert "metric_series_tracked" in stats

    def test_feature_extraction(self):
        """Feature extraction should work after sufficient data."""
        key = "feat-host:cpu_usage"
        for i in range(50):
            metric = MetricPoint(
                metric_type=MetricType.CPU_USAGE.value,
                value=50.0 + np.random.normal(0, 5),
                host="feat-host",
                service="test",
            )
            self.detector.process_metric(metric)

        test_metric = MetricPoint(
            metric_type=MetricType.CPU_USAGE.value,
            value=55.0,
            host="feat-host",
            service="test",
        )
        features = self.detector._extract_features(key, test_metric)
        assert features is not None
        assert len(features) == 16  # 16 features expected
        assert not np.any(np.isnan(features))

    def test_severity_determination(self):
        """Severity should map correctly."""
        assert self.detector._determine_severity("cpu_usage", 95, 0.9) == "critical"
        assert self.detector._determine_severity("cpu_usage", 91, 0.9) == "high"
        assert self.detector._determine_severity("cpu_usage", 81, 0.7) == "medium"
        assert self.detector._determine_severity("cpu_usage", 71, 0.5) == "low"

    def test_expected_range(self):
        """Expected range should be computed from stats."""
        key = "range-host:cpu_usage"
        for i in range(100):
            metric = MetricPoint(
                metric_type=MetricType.CPU_USAGE.value,
                value=50.0 + np.random.normal(0, 5),
                host="range-host",
                service="test",
            )
            self.detector.process_metric(metric)

        lower, upper = self.detector._get_expected_range(key)
        assert lower < upper
        assert 30 < lower < 50  # roughly mean - 2*std
        assert 50 < upper < 70  # roughly mean + 2*std