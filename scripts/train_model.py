"""
Manually trigger model training from stored Redis data.
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.common.redis_client import RedisClient
from src.common.logger import get_logger
from src.detector.model_trainer import ModelTrainer

logger = get_logger("model-trainer")


def main():
    redis = RedisClient()

    if not redis.ping():
        logger.error("❌ Cannot connect to Redis")
        sys.exit(1)

    trainer = ModelTrainer(redis)

    logger.info("🏋️ Starting model training...")
    results = trainer.train_all_models()

    report = trainer.generate_training_report(results)
    print(report)

    logger.info("✅ Training complete!")


if __name__ == "__main__":
    main()