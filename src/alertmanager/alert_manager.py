"""
Alert manager – converts anomalies into alerts, handles deduplication,
cooldowns, escalation, and notification routing.
"""

import time
from typing import Optional, List, Dict
from collections import defaultdict
from datetime import datetime, timezone

from src.common.config import config
from src.common.models import Anomaly, Alert, AlertStatus, Severity
from src.common.redis_client import RedisClient
from src.common.logger import get_logger

logger = get_logger(__name__)


class AlertManager:
    """
    Manages the full alert lifecycle:
    - Anomaly → Alert conversion
    - Deduplication (suppress duplicate alerts for same host/metric)
    - Cooldowns (don't re-alert within cooldown window)
    - Severity-based routing
    - Alert acknowledgement and resolution
    """

    # Escalation: how many consecutive anomalies before escalating severity
    ESCALATION_THRESHOLD = 5

    def __init__(self, redis_client: RedisClient):
        self.redis = redis_client
        self._active_alerts: Dict[str, Alert] = {}
        self._anomaly_counts: Dict[str, int] = defaultdict(int)
        self._alerts_created = 0
        self._alerts_suppressed = 0

    def process_anomaly(self, anomaly: Anomaly) -> Optional[Alert]:
        """
        Process an anomaly and decide whether to create/update an alert.
        Returns Alert if one was created, None if suppressed.
        """
        alert_key = f"{anomaly.host}:{anomaly.metric_type}"

        # Track consecutive anomalies
        self._anomaly_counts[alert_key] += 1

        # Check cooldown
        if self.redis.check_alert_cooldown(alert_key):
            self._alerts_suppressed += 1
            logger.debug(
                f"Alert suppressed (cooldown): {alert_key}"
            )
            return None

        # Check if there's already an active alert for this key
        existing_alert = self._active_alerts.get(alert_key)
        if existing_alert and existing_alert.status == AlertStatus.FIRING.value:
            # Update severity if escalation threshold reached
            count = self._anomaly_counts[alert_key]
            if count >= self.ESCALATION_THRESHOLD:
                return self._escalate_alert(existing_alert, anomaly)
            self._alerts_suppressed += 1
            return None

        # Create new alert
        severity = self._maybe_escalate_severity(
            anomaly.severity,
            self._anomaly_counts[alert_key],
        )

        alert = Alert(
            anomaly_id=anomaly.anomaly_id,
            severity=severity,
            title=self._generate_title(anomaly),
            description=anomaly.description,
            metric_type=anomaly.metric_type,
            host=anomaly.host,
            service=anomaly.service,
            status=AlertStatus.FIRING.value,
        )

        self._active_alerts[alert_key] = alert
        self._alerts_created += 1

        # Set cooldown
        cooldown = self._get_cooldown_minutes(severity)
        self.redis.set_alert_cooldown(alert_key, cooldown)

        logger.info(
            f"🔔 Alert created: {alert.title} "
            f"[{alert.severity}] (cooldown={cooldown}min)"
        )

        return alert

    def acknowledge_alert(self, alert_id: str, user: str) -> Optional[Alert]:
        """Acknowledge an alert."""
        for key, alert in self._active_alerts.items():
            if alert.alert_id == alert_id:
                alert.status = AlertStatus.ACKNOWLEDGED.value
                alert.acknowledged_by = user
                logger.info(f"Alert {alert_id} acknowledged by {user}")
                return alert
        return None

    def resolve_alert(self, alert_id: str) -> Optional[Alert]:
        """Resolve an alert."""
        for key, alert in list(self._active_alerts.items()):
            if alert.alert_id == alert_id:
                alert.status = AlertStatus.RESOLVED.value
                alert.resolved_at = datetime.now(timezone.utc).isoformat()
                self._anomaly_counts[key] = 0
                del self._active_alerts[key]
                logger.info(f"Alert {alert_id} resolved")
                return alert
        return None

    def auto_resolve_stale_alerts(self, stale_minutes: int = 30):
        """Auto-resolve alerts that haven't seen new anomalies."""
        now = time.time()
        to_resolve = []

        for key, alert in list(self._active_alerts.items()):
            try:
                alert_time = datetime.fromisoformat(
                    alert.timestamp
                ).timestamp()
                age_minutes = (now - alert_time) / 60

                if age_minutes > stale_minutes:
                    to_resolve.append(alert.alert_id)
            except Exception:
                pass

        for alert_id in to_resolve:
            self.resolve_alert(alert_id)
            logger.info(f"Auto-resolved stale alert: {alert_id}")

    def get_active_alerts(self) -> List[Alert]:
        """Get all currently active (firing or acknowledged) alerts."""
        return list(self._active_alerts.values())

    def _escalate_alert(self, alert: Alert, anomaly: Anomaly) -> Optional[Alert]:
        """Escalate an existing alert's severity."""
        old_severity = alert.severity
        new_severity = self._get_next_severity(old_severity)

        if new_severity == old_severity:
            return None

        alert.severity = new_severity
        alert.description = (
            f"ESCALATED from {old_severity} to {new_severity}. "
            f"{anomaly.description}"
        )

        logger.warning(
            f"⬆️ Alert escalated: {alert.title} "
            f"{old_severity} → {new_severity}"
        )
        return alert

    def _maybe_escalate_severity(self, severity: str, count: int) -> str:
        """Escalate severity based on consecutive anomaly count."""
        if count >= self.ESCALATION_THRESHOLD * 3:
            return Severity.CRITICAL.value
        elif count >= self.ESCALATION_THRESHOLD * 2:
            return self._get_next_severity(severity)
        return severity

    @staticmethod
    def _get_next_severity(current: str) -> str:
        """Get the next higher severity level."""
        order = [
            Severity.LOW.value,
            Severity.MEDIUM.value,
            Severity.HIGH.value,
            Severity.CRITICAL.value,
        ]
        try:
            idx = order.index(current)
            return order[min(idx + 1, len(order) - 1)]
        except ValueError:
            return current

    @staticmethod
    def _get_cooldown_minutes(severity: str) -> int:
        """Get cooldown duration based on severity."""
        cooldowns = {
            Severity.LOW.value: 30,
            Severity.MEDIUM.value: 15,
            Severity.HIGH.value: 10,
            Severity.CRITICAL.value: 5,
        }
        return cooldowns.get(severity, config.alert.cooldown_minutes)

    @staticmethod
    def _generate_title(anomaly: Anomaly) -> str:
        """Generate a concise alert title."""
        return (
            f"[{anomaly.severity.upper()}] {anomaly.metric_type} anomaly "
            f"on {anomaly.host} (score: {anomaly.anomaly_score:.2f})"
        )

    def get_stats(self) -> dict:
        return {
            "active_alerts": len(self._active_alerts),
            "alerts_created_total": self._alerts_created,
            "alerts_suppressed_total": self._alerts_suppressed,
            "active_alert_details": [
                {
                    "id": a.alert_id,
                    "severity": a.severity,
                    "host": a.host,
                    "metric": a.metric_type,
                    "status": a.status,
                }
                for a in self._active_alerts.values()
            ],
        }