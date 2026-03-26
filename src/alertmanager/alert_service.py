"""
Alert manager microservice – consumes anomalies from Redis,
manages alerts, and sends notifications.
"""

import time
import signal
import sys
import threading

from src.common.config import config
from src.common.logger import get_logger
from src.common.redis_client import RedisClient
from src.common.models import Anomaly, HealthStatus
from .alert_manager import AlertManager
from .sns_notifier import SNSNotifier

logger = get_logger("alert-service")


class AlertService:
    """Alert management microservice."""

    def __init__(self):
        self.redis_client = RedisClient()
        self.alert_manager = AlertManager(self.redis_client)
        self.notifier = SNSNotifier()
        self._running = False
        self._start_time = time.time()
        self._last_stream_id = "0"
        self._anomalies_processed = 0
        self._notifications_sent = 0

    def start(self):
        """Start the alert service."""
        logger.info("🚀 Alert service starting...")
        self._running = True

        self._wait_for_redis()

        signal.signal(signal.SIGTERM, self._handle_shutdown)
        signal.signal(signal.SIGINT, self._handle_shutdown)

        # Start auto-resolve thread
        resolve_thread = threading.Thread(
            target=self._auto_resolve_loop, daemon=True
        )
        resolve_thread.start()

        # Main loop
        self._alert_loop()

    def _alert_loop(self):
        """Main loop: consume anomalies and manage alerts."""
        while self._running:
            try:
                entries = self.redis_client.read_stream(
                    RedisClient.ANOMALIES_STREAM,
                    last_id=self._last_stream_id,
                    count=50,
                    block=3000,
                )

                if not entries:
                    self._report_health()
                    continue

                for entry in entries:
                    self._last_stream_id = entry["id"]
                    anomaly_data = entry["data"]

                    try:
                        anomaly = Anomaly.from_dict(anomaly_data)
                        self._process_anomaly(anomaly)
                    except Exception as e:
                        logger.error(
                            f"Error processing anomaly: {e}", exc_info=True
                        )

                self._anomalies_processed += len(entries)

                if self._anomalies_processed % 100 == 0:
                    self._report_health()

            except Exception as e:
                logger.error(f"Alert loop error: {e}", exc_info=True)
                time.sleep(2)

    def _process_anomaly(self, anomaly: Anomaly):
        """Process an anomaly through the alert manager and notify."""
        alert = self.alert_manager.process_anomaly(anomaly)

        if alert:
            # Publish alert to Redis
            try:
                self.redis_client.publish_alert(alert.to_dict())
            except Exception as e:
                logger.error(f"Failed to publish alert: {e}")

            # Send notifications
            try:
                results = self.notifier.send_alert(alert)
                self._notifications_sent += 1
                logger.info(
                    f"📨 Notification results: {results}"
                )
            except Exception as e:
                logger.error(f"Notification failed: {e}")

    def _auto_resolve_loop(self):
        """Periodically auto-resolve stale alerts."""
        while self._running:
            try:
                self.alert_manager.auto_resolve_stale_alerts(stale_minutes=30)
            except Exception as e:
                logger.error(f"Auto-resolve error: {e}")
            time.sleep(60)

    def _report_health(self):
        alert_stats = self.alert_manager.get_stats()
        health = HealthStatus(
            service_name="alertmanager",
            status="healthy",
            uptime_seconds=time.time() - self._start_time,
            details={
                "anomalies_processed": self._anomalies_processed,
                "notifications_sent": self._notifications_sent,
                "last_stream_id": self._last_stream_id,
                **alert_stats,
            },
        )
        self.redis_client.set_service_health("alertmanager", health.to_dict())

    def _wait_for_redis(self, max_retries: int = 30):
        for i in range(max_retries):
            if self.redis_client.ping():
                logger.info("✅ Connected to Redis")
                return
            logger.warning(f"Waiting for Redis... ({i + 1}/{max_retries})")
            time.sleep(2)
        raise ConnectionError("Could not connect to Redis")

    def _handle_shutdown(self, signum, frame):
        logger.info(f"Received signal {signum}, shutting down...")
        self._running = False


def main():
    service = AlertService()
    service.start()


if __name__ == "__main__":
    main()