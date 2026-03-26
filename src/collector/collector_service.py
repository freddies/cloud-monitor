"""
Main collector service – runs as a standalone microservice.
Collects metrics and publishes them to Redis streams.
"""

import time
import signal
import sys
from datetime import datetime, timezone

from src.common.config import config
from src.common.logger import get_logger
from src.common.redis_client import RedisClient
from src.common.models import HealthStatus
from .telemetry_collector import TelemetryCollector
from .aws_cloudwatch_collector import CloudWatchCollector
from .metrics_simulator import MetricsSimulator

logger = get_logger("collector-service")


class CollectorService:
    """Orchestrates metric collection from all sources."""

    def __init__(self, use_simulator: bool = True, use_system: bool = True):
        self.redis_client = RedisClient()
        self.use_simulator = use_simulator
        self.use_system = use_system
        self._running = False
        self._start_time = time.time()
        self._metrics_collected = 0

        # Initialize collectors
        if use_system:
            self.system_collector = TelemetryCollector()
        if use_simulator:
            self.simulator = MetricsSimulator(anomaly_probability=0.03)

        self.cloudwatch_collector = CloudWatchCollector()

        # Graceful shutdown
        signal.signal(signal.SIGTERM, self._handle_shutdown)
        signal.signal(signal.SIGINT, self._handle_shutdown)

    def start(self):
        """Start the collection loop."""
        logger.info("🚀 Collector service starting...")
        self._running = True

        # Wait for Redis
        self._wait_for_redis()

        logger.info(
            f"Collecting every {config.collector.interval_seconds}s | "
            f"simulator={self.use_simulator} | system={self.use_system}"
        )

        while self._running:
            try:
                cycle_start = time.time()
                self._collect_and_publish()
                self._report_health()

                elapsed = time.time() - cycle_start
                sleep_time = max(0, config.collector.interval_seconds - elapsed)
                time.sleep(sleep_time)

            except Exception as e:
                logger.error(f"Collection cycle error: {e}", exc_info=True)
                time.sleep(5)

        logger.info("Collector service stopped.")

    def _collect_and_publish(self):
        """Single collection cycle."""
        all_metrics = []

        # Simulated metrics
        if self.use_simulator:
            sim_metrics = self.simulator.generate_batch()
            all_metrics.extend(sim_metrics)

        # Real system metrics
        if self.use_system:
            sys_metrics = self.system_collector.collect_all()
            all_metrics.extend(sys_metrics)

        # AWS CloudWatch metrics (if configured)
        if self.cloudwatch_collector.enabled:
            try:
                cw_metrics = self.cloudwatch_collector.collect()
                all_metrics.extend(cw_metrics)
            except Exception as e:
                logger.warning(f"CloudWatch collection failed: {e}")

        # Publish to Redis
        published = 0
        for metric in all_metrics:
            try:
                self.redis_client.publish_metric(metric.to_dict())
                published += 1
            except Exception as e:
                logger.error(f"Failed to publish metric: {e}")

        self._metrics_collected += published
        logger.info(f"📊 Published {published}/{len(all_metrics)} metrics")

    def _report_health(self):
        """Report health status to Redis."""
        health = HealthStatus(
            service_name="collector",
            status="healthy",
            uptime_seconds=time.time() - self._start_time,
            details={
                "metrics_collected_total": self._metrics_collected,
                "simulator_enabled": self.use_simulator,
                "system_enabled": self.use_system,
                "cloudwatch_enabled": self.cloudwatch_collector.enabled,
            },
        )
        self.redis_client.set_service_health("collector", health.to_dict())

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
    """Entry point for the collector service."""
    use_sim = "--no-simulator" not in sys.argv
    use_sys = "--no-system" not in sys.argv

    service = CollectorService(use_simulator=use_sim, use_system=use_sys)
    service.start()


if __name__ == "__main__":
    main()