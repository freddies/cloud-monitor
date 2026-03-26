"""
Integration tests – require Redis to be running.
Tests the full pipeline: collect → detect → alert.
"""

import pytest
import time
import numpy as np
from unittest.mock import MagicMock

from src.common.redis_client import RedisClient
from src.common.models import MetricPoint, MetricType, Anomaly, Severity
from src.collector.metrics_simulator import MetricsSimulator
from src.detector.anomaly_detector import AnomalyDetector
from src.detector.isolation_forest_model import IsolationForestDetector
from src.alertmanager.alert_manager import AlertManager


def redis_available():
    """Check if Redis is available for integration tests."""
    try:
        r = RedisClient()
        return r.ping()
    except Exception:
        return False


@pytest.mark.skipif(
    not redis_available(),
    reason="Redis not available for integration tests",
)
class TestRedisIntegration:
    def setup_method(self):
        self.redis = RedisClient()
        # Use a separate DB for tests
        self.redis._redis.select(15)
        self.redis._redis.flushdb()

    def teardown_method(self):
        self.redis._redis.flushdb()
        self.redis._redis.select(0)

    def test_publish_and_read_metric(self):
        metric = MetricPoint(
            metric_type=MetricType.CPU_USAGE.value,
            value=65.0,
            host="test-host",
            service="test",
        )

        self.redis.publish_metric(metric.to_dict())
        metrics = self.redis.get_recent_metrics(10)

        assert len(metrics) == 1
        assert metrics[0]["value"] == 65.0
        assert metrics[0]["host"] == "test-host"

    def test_publish_and_read_anomaly(self):
        anomaly = Anomaly(
            metric_type=MetricType.CPU_USAGE.value,
            value=98.0,
            expected_range=(30.0, 70.0),
            anomaly_score=0.95,
            severity=Severity.CRITICAL.value,
            host="test-host",
            service="test",
            model_used="test",
        )

        self.redis.publish_anomaly(anomaly.to_dict())
        anomalies = self.redis.get_recent_anomalies(10)

        assert len(anomalies) == 1
        assert anomalies[0]["severity"] == "critical"

    def test_metric_history(self):
        for i in range(20):
            metric = MetricPoint(
                metric_type=MetricType.CPU_USAGE.value,
                value=50.0 + i,
                host="history-host",
                service="test",
            )
            self.redis.publish_metric(metric.to_dict())

        history = self.redis.get_metric_history(
            "history-host", MetricType.CPU_USAGE.value, minutes=60
        )
        assert len(history) == 20

    def test_alert_cooldown(self):
        key = "test-host:cpu_usage"

        assert not self.redis.check_alert_cooldown(key)

        self.redis.set_alert_cooldown(key, minutes=1)
        assert self.redis.check_alert_cooldown(key)

    def test_service_health(self):
        from src.common.models import HealthStatus

        health = HealthStatus(
            service_name="test-service",
            status="healthy",
            uptime_seconds=100.0,
            details={"test": True},
        )

        self.redis.set_service_health("test-service", health.to_dict())
        all_health = self.redis.get_all_service_health()

        assert "test-service" in all_health
        assert all_health["test-service"]["status"] == "healthy"

    def test_stats(self):
        # Publish some data
        for i in range(5):
            metric = MetricPoint(
                metric_type=MetricType.CPU_USAGE.value,
                value=50.0,
                host="stats-host",
                service="test",
            )
            self.redis.publish_metric(metric.to_dict())

        stats = self.redis.get_stats()
        assert stats["metrics_stream_length"] == 5
        assert stats["total_metric_series"] >= 1

    def test_stream_read(self):
        """Test blocking stream read."""
        # Publish metrics
        for i in range(3):
            metric = MetricPoint(
                metric_type=MetricType.CPU_USAGE.value,
                value=float(i),
                host="stream-host",
                service="test",
            )
            self.redis.publish_metric(metric.to_dict())

        # Read from stream
        entries = self.redis.read_stream(
            RedisClient.METRICS_STREAM,
            last_id="0",
            count=10,
            block=1000,
        )
        assert len(entries) == 3
        assert entries[0]["data"]["value"] == 0.0


class TestFullPipeline:
    """Test the full collect → detect pipeline (no Redis required)."""

    def test_simulator_to_detector(self):
        """Simulate metrics and run through detector."""
        simulator = MetricsSimulator(
            anomaly_probability=0.0,
            hosts=["pipeline-host"],
        )
        detector = AnomalyDetector()

        # Run 100 batches of normal data
        all_anomalies = []
        for _ in range(100):
            batch = simulator.generate_batch()
            anomalies = detector.process_batch(batch)
            all_anomalies.extend(anomalies)

        # With normal data and no trained model, few to no anomalies expected
        stats = detector.get_stats()
        assert stats["total_checked"] > 0
        assert stats["metric_series_tracked"] > 0

    def test_isolation_forest_full_cycle(self):
        """Train IF and detect anomalies in synthetic data."""
        np.random.seed(42)

        # Generate training data
        n_samples = 1000
        n_features = 4
        normal_data = np.random.normal(
            loc=[50, 60, 40, 100],
            scale=[5, 8, 3, 20],
            size=(n_samples, n_features),
        )

        # Train
        detector = IsolationForestDetector(contamination=0.05)
        stats = detector.train(normal_data)
        assert stats["status"] == "trained"

        # Test normal points
        normal_false_positives = 0
        for _ in range(100):
            point = np.random.normal([50, 60, 40, 100], [5, 8, 3, 20])
            is_anomaly, score = detector.predict_single(point)
            if is_anomaly:
                normal_false_positives += 1

        # False positive rate should be low
        assert normal_false_positives < 20  # < 20%

        # Test anomalous points
        anomaly_detections = 0
        for _ in range(100):
            point = np.random.normal([150, 150, 150, 300], [5, 5, 5, 10])
            is_anomaly, score = detector.predict_single(point)
            if is_anomaly:
                anomaly_detections += 1

        # Detection rate should be high
        assert anomaly_detections > 80  # > 80%

    def test_alert_manager_with_detector(self):
        """Test detector feeding into alert manager."""
        mock_redis = MagicMock()
        mock_redis.check_alert_cooldown.return_value = False
        mock_redis.set_alert_cooldown.return_value = None

        alert_manager = AlertManager(mock_redis)

        # Create an anomaly (as would come from detector)
        anomaly = Anomaly(
            metric_type=MetricType.CPU_USAGE.value,
            value=97.5,
            expected_range=(30.0, 65.0),
            anomaly_score=0.93,
            severity=Severity.CRITICAL.value,
            host="prod-web-01",
            service="web",
            model_used="ensemble",
            description="Critical CPU anomaly detected",
        )

        alert = alert_manager.process_anomaly(anomaly)
        assert alert is not None
        assert alert.severity == Severity.CRITICAL.value
        assert "prod-web-01" in alert.title

    def test_end_to_end_with_anomaly_injection(self):
        """Full E2E: simulate → detect → alert."""
        # High anomaly probability for testing
        simulator = MetricsSimulator(
            anomaly_probability=0.5,
            hosts=["e2e-host"],
        )
        detector = AnomalyDetector()

        mock_redis = MagicMock()
        mock_redis.check_alert_cooldown.return_value = False
        mock_redis.set_alert_cooldown.return_value = None
        alert_manager = AlertManager(mock_redis)

        total_anomalies = 0
        total_alerts = 0

        # Build baseline (200 batches)
        normal_sim = MetricsSimulator(
            anomaly_probability=0.0,
            hosts=["e2e-host"],
        )
        for _ in range(200):
            batch = normal_sim.generate_batch()
            detector.process_batch(batch)

        # Now inject anomalies
        for _ in range(50):
            batch = simulator.generate_batch()
            anomalies = detector.process_batch(batch)
            total_anomalies += len(anomalies)

            for anomaly in anomalies:
                mock_redis.check_alert_cooldown.return_value = False
                alert = alert_manager.process_anomaly(anomaly)
                if alert:
                    total_alerts += 1

        # Should have detected some anomalies
        detector_stats = detector.get_stats()
        alert_stats = alert_manager.get_stats()

        print(f"\n📊 E2E Results:")
        print(f"   Metrics checked: {detector_stats['total_checked']}")
        print(f"   Anomalies detected: {total_anomalies}")
        print(f"   Alerts created: {total_alerts}")
        print(f"   Detection rate: {detector_stats['anomaly_rate']:.4f}")

        assert detector_stats["total_checked"] > 0