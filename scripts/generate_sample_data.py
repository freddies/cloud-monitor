"""
Generates sample training data and seeds Redis for testing.
"""

import sys
import os
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.common.redis_client import RedisClient
from src.common.logger import get_logger
from src.collector.metrics_simulator import MetricsSimulator

logger = get_logger("data-generator")


def main():
    redis = RedisClient()

    if not redis.ping():
        logger.error("❌ Cannot connect to Redis. Is it running?")
        sys.exit(1)

    logger.info("🚀 Generating sample data...")

    simulator = MetricsSimulator(anomaly_probability=0.03)
    total_metrics = 0
    total_batches = 500  # ~83 minutes of data at 10s intervals

    for i in range(total_batches):
        batch = simulator.generate_batch()
        for metric in batch:
            redis.publish_metric(metric.to_dict())
            total_metrics += 1

        if (i + 1) % 50 == 0:
            logger.info(
                f"Progress: {i + 1}/{total_batches} batches, "
                f"{total_metrics} metrics published"
            )

    logger.info(f"✅ Done! Published {total_metrics} metrics across {total_batches} batches")

    # Print Redis stats
    stats = redis.get_stats()
    logger.info(f"Redis stats: {stats}")


if __name__ == "__main__":
    main()