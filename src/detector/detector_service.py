"""
Main anomaly detection service – consumes metrics from Redis streams,
runs ML detection, and publishes anomalies.
"""

import time
import signal
import sys
import threading
from datetime import datetime, timezone

from src.common.config import config
from src.common.logger import get_logger
from src.common.redis_client import RedisClient
from src.common.models import MetricPoint, HealthStatus
from .anomaly_detector import AnomalyDetector
from .model_trainer import ModelTrainer

logger = get_logger("detector-service")


class DetectorService:
    """Anomaly detection microservice."""

    def __init__(self):
        self.redis_client = RedisClient()
        self.detector = AnomalyDetector()
        self.trainer = ModelTrainer(self.redis_client)
        self._running = False
        self._start_time = time.time()
        self._last_stream_id = "0"
        self._metrics_processed = 0
        self._anomalies_published = 0
        self._train_lock = threading.Lock()

    def start(self):
        """Start the detection service."""
        logger.info("🚀 Detector service starting...")
        self._running = True

        # Wait for Redis
        self._wait_for_redis()

        # Load pre-trained models
        self.detector.initialize()

        # Start training thread
        train_thread = threading.Thread(
            target=self._training_loop, daemon=True
        )
        train_thread.start()

        # Graceful shutdown
        signal.signal(signal.SIGTERM, self._handle_shutdown)
        signal.signal(signal.SIGINT, self._handle_shutdown)

        # Main detection loop
        logger.info("Listening for metrics on Redis stream...")
        self._detection_loop()

    def _detection_loop(self):
        """Main loop: consume metrics and detect anomalies."""
        while self._running:
            try:
                entries = self.redis_client.read_stream(
                    RedisClient.METRICS_STREAM,
                    last_id=self._last_stream_id,
                    count=100,
                    block=2000,
                )

                if not entries:
                    self._report_health()
                    continue

                for entry in entries:
                    self._last_stream_id = entry["id"]
                    metric_data = entry["data"]

                    try:
                        metric = MetricPoint.from_dict(metric_data)
                        self._process_metric(metric)
                    except Exception as e:
                        logger.error(
                            f"Error processing metric: {e}", exc_info=True
                        )

                self._metrics_processed += len(entries)

                # Periodic health report
                if self._metrics_processed % 500 == 0:
                    self._report_health()

            except Exception as e:
                logger.error(f"Detection loop error: {e}", exc_info=True)
                time.sleep(1)

    def _process_metric(self, metric: MetricPoint):
        """Process a single metric through the detector."""
        anomaly = self.detector.process_metric(metric)

        if anomaly:
            try:
                self.redis_client.publish_anomaly(anomaly.to_dict())
                self._anomalies_published += 1

                logger.warning(
                    f"🚨 Published anomaly: {anomaly.host}/{anomaly.metric_type} "
                    f"severity={anomaly.severity} score={anomaly.anomaly_score}"
                )
            except Exception as e:
                logger.error(f"Failed to publish anomaly: {e}")

    def _training_loop(self):
        """Background thread for periodic model retraining."""
        # Initial training after collecting some data
        initial_wait = 120  # Wait 2 minutes for data to accumulate
        logger.info(
            f"Training will start in {initial_wait}s (waiting for data)..."
        )
        time.sleep(initial_wait)

        while self._running:
            try:
                if self.trainer.should_retrain() or not self.detector.if_detector.is_trained:
                    with self._train_lock:
                        logger.info("Starting model retraining...")
                        results = self.trainer.train_all_models()

                        report = self.trainer.generate_training_report(results)
                        logger.info(f"\n{report}")

                        # Reload models
                        self.detector.initialize()

                        # Save training state to Redis
                        self.redis_client.save_model_state(
                            "latest_training",
                            {
                                "timestamp": datetime.now(
                                    timezone.utc
                                ).isoformat(),
                                "results": {
                                    k: str(v) for k, v in results.items()
                                },
                            },
                        )

            except Exception as e:
                logger.error(f"Training error: {e}", exc_info=True)

            # Sleep until next training cycle
            sleep_secs = config.ml.retrain_interval_hours * 3600
            # Check every 60s if we should stop
            for _ in range(int(sleep_secs / 60)):
                if not self._running:
                    return
                time.sleep(60)

    def _report_health(self):
        """Publish health status."""
        detector_stats = self.detector.get_stats()
        health = HealthStatus(
            service_name="detector",
            status="healthy",
            uptime_seconds=time.time() - self._start_time,
            details={
                "metrics_processed": self._metrics_processed,
                "anomalies_published": self._anomalies_published,
                "last_stream_id": self._last_stream_id,
                **detector_stats,
            },
        )
        self.redis_client.set_service_health("detector", health.to_dict())

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
    service = DetectorService()
    service.start()


if __name__ == "__main__":
    main()