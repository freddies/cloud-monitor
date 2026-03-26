"""Tests for the alert manager."""

import pytest
from unittest.mock import MagicMock, patch
from src.alertmanager.alert_manager import AlertManager
from src.alertmanager.sns_notifier import SNSNotifier
from src.common.models import Anomaly, Alert, Severity, AlertStatus
from src.common.redis_client import RedisClient


class MockRedisClient:
    """Mock Redis client for testing."""

    def __init__(self):
        self._cooldowns = {}

    def check_alert_cooldown(self, key):
        return key in self._cooldowns

    def set_alert_cooldown(self, key, minutes=15):
        self._cooldowns[key] = minutes

    def clear_cooldowns(self):
        self._cooldowns = {}


class TestAlertManager:
    def setup_method(self):
        self.mock_redis = MockRedisClient()
        self.manager = AlertManager(self.mock_redis)

    def _create_anomaly(
        self,
        host="test-host",
        metric_type="cpu_usage",
        severity="high",
        value=95.0,
        score=0.9,
    ):
        return Anomaly(
            metric_type=metric_type,
            value=value,
            expected_range=(30.0, 60.0),
            anomaly_score=score,
            severity=severity,
            host=host,
            service="test",
            model_used="ensemble",
            description="Test anomaly description",
        )

    def test_create_alert_from_anomaly(self):
        anomaly = self._create_anomaly()
        alert = self.manager.process_anomaly(anomaly)

        assert alert is not None
        assert isinstance(alert, Alert)
        assert alert.severity == "high"
        assert alert.status == AlertStatus.FIRING.value
        assert alert.host == "test-host"
        assert alert.metric_type == "cpu_usage"

    def test_alert_cooldown_suppression(self):
        anomaly1 = self._create_anomaly()
        alert1 = self.manager.process_anomaly(anomaly1)
        assert alert1 is not None

        # Second anomaly for same host/metric should be suppressed
        anomaly2 = self._create_anomaly()
        alert2 = self.manager.process_anomaly(anomaly2)
        assert alert2 is None

    def test_different_hosts_get_separate_alerts(self):
        anomaly1 = self._create_anomaly(host="host-1")
        alert1 = self.manager.process_anomaly(anomaly1)
        assert alert1 is not None

        anomaly2 = self._create_anomaly(host="host-2")
        alert2 = self.manager.process_anomaly(anomaly2)
        assert alert2 is not None

        assert alert1.alert_id != alert2.alert_id

    def test_different_metrics_get_separate_alerts(self):
        anomaly1 = self._create_anomaly(metric_type="cpu_usage")
        alert1 = self.manager.process_anomaly(anomaly1)
        assert alert1 is not None

        anomaly2 = self._create_anomaly(metric_type="memory_usage")
        alert2 = self.manager.process_anomaly(anomaly2)
        assert alert2 is not None

    def test_acknowledge_alert(self):
        anomaly = self._create_anomaly()
        alert = self.manager.process_anomaly(anomaly)

        ack_alert = self.manager.acknowledge_alert(alert.alert_id, "test-user")
        assert ack_alert is not None
        assert ack_alert.status == AlertStatus.ACKNOWLEDGED.value
        assert ack_alert.acknowledged_by == "test-user"

    def test_resolve_alert(self):
        anomaly = self._create_anomaly()
        alert = self.manager.process_anomaly(anomaly)

        resolved = self.manager.resolve_alert(alert.alert_id)
        assert resolved is not None
        assert resolved.status == AlertStatus.RESOLVED.value
        assert resolved.resolved_at is not None

        # Active alerts should be empty
        active = self.manager.get_active_alerts()
        assert len(active) == 0

    def test_acknowledge_nonexistent_alert(self):
        result = self.manager.acknowledge_alert("fake-id", "user")
        assert result is None

    def test_resolve_nonexistent_alert(self):
        result = self.manager.resolve_alert("fake-id")
        assert result is None

    def test_escalation(self):
        """Consecutive anomalies should escalate severity."""
        self.mock_redis.clear_cooldowns()

        # Send many anomalies to trigger escalation
        for i in range(AlertManager.ESCALATION_THRESHOLD + 1):
            anomaly = self._create_anomaly(severity="medium")
            self.mock_redis.clear_cooldowns()  # Clear cooldown each time
            alert = self.manager.process_anomaly(anomaly)

        # After enough anomalies, severity should be escalated
        count = self.manager._anomaly_counts["test-host:cpu_usage"]
        assert count >= AlertManager.ESCALATION_THRESHOLD

    def test_severity_cooldown_mapping(self):
        """Critical alerts should have shorter cooldowns."""
        critical_cooldown = AlertManager._get_cooldown_minutes("critical")
        low_cooldown = AlertManager._get_cooldown_minutes("low")
        assert critical_cooldown < low_cooldown

    def test_get_stats(self):
        anomaly = self._create_anomaly()
        self.manager.process_anomaly(anomaly)

        stats = self.manager.get_stats()
        assert stats["active_alerts"] == 1
        assert stats["alerts_created_total"] == 1
        assert len(stats["active_alert_details"]) == 1

    def test_auto_resolve_stale(self):
        """Stale alerts should be auto-resolved."""
        anomaly = self._create_anomaly()
        alert = self.manager.process_anomaly(anomaly)
        assert alert is not None

        # Force the timestamp to be old
        from datetime import datetime, timedelta, timezone
        old_time = datetime.now(timezone.utc) - timedelta(minutes=60)
        alert.timestamp = old_time.isoformat()

        self.manager.auto_resolve_stale_alerts(stale_minutes=30)
        active = self.manager.get_active_alerts()
        assert len(active) == 0


class TestSNSNotifier:
    def test_format_message(self):
        alert = Alert(
            anomaly_id="test-anomaly-id",
            severity="high",
            title="[HIGH] CPU anomaly on web-01",
            description="CPU is at 95%",
            metric_type="cpu_usage",
            host="web-01",
            service="web",
        )

        notifier = SNSNotifier(boto_client=MagicMock())
        message = notifier._format_sns_message(alert)

        assert "HIGH" in message
        assert "web-01" in message
        assert "cpu_usage" in message
        assert alert.alert_id in message

    def test_disabled_when_no_config(self):
        """Should gracefully handle missing configuration."""
        with patch.object(SNSNotifier, '__init__', lambda self, **kwargs: None):
            notifier = SNSNotifier.__new__(SNSNotifier)
            notifier.sns_client = None
            notifier.sns_enabled = False
            notifier.slack_enabled = False

            alert = Alert(
                anomaly_id="id",
                severity="low",
                title="test",
                description="test",
                metric_type="cpu",
            )
            result = notifier._send_sns(alert)
            assert result is False