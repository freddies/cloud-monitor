"""
Centralized configuration management.
Loads from environment variables / .env file.
"""

import os
from dataclasses import dataclass, field
from dotenv import load_dotenv

load_dotenv()


@dataclass
class RedisConfig:
    host: str = os.getenv("REDIS_HOST", "localhost")
    port: int = int(os.getenv("REDIS_PORT", 6379))
    db: int = int(os.getenv("REDIS_DB", 0))
    password: str = os.getenv("REDIS_PASSWORD", "")


@dataclass
class AWSConfig:
    region: str = os.getenv("AWS_REGION", "us-east-1")
    access_key_id: str = os.getenv("AWS_ACCESS_KEY_ID", "")
    secret_access_key: str = os.getenv("AWS_SECRET_ACCESS_KEY", "")
    sns_topic_arn: str = os.getenv("AWS_SNS_TOPIC_ARN", "")
    s3_bucket: str = os.getenv("AWS_S3_BUCKET", "cloud-monitor-models")
    cloudwatch_namespace: str = os.getenv("AWS_CLOUDWATCH_NAMESPACE", "CloudMonitor")


@dataclass
class MLConfig:
    anomaly_threshold: float = float(os.getenv("ANOMALY_THRESHOLD", 0.85))
    retrain_interval_hours: int = int(os.getenv("MODEL_RETRAIN_INTERVAL_HOURS", 24))
    detection_window_seconds: int = int(os.getenv("DETECTION_WINDOW_SECONDS", 300))
    isolation_forest_contamination: float = float(
        os.getenv("ISOLATION_FOREST_CONTAMINATION", 0.05)
    )
    lstm_sequence_length: int = 30
    lstm_epochs: int = 50
    lstm_batch_size: int = 32


@dataclass
class CollectorConfig:
    interval_seconds: int = int(os.getenv("COLLECTION_INTERVAL_SECONDS", 10))
    retention_hours: int = int(os.getenv("METRICS_RETENTION_HOURS", 72))


@dataclass
class AlertConfig:
    cooldown_minutes: int = int(os.getenv("ALERT_COOLDOWN_MINUTES", 15))
    email: str = os.getenv("ALERT_EMAIL", "")
    slack_webhook_url: str = os.getenv("SLACK_WEBHOOK_URL", "")


@dataclass
class DashboardConfig:
    host: str = os.getenv("DASHBOARD_HOST", "0.0.0.0")
    port: int = int(os.getenv("DASHBOARD_PORT", 5000))


@dataclass
class Config:
    app_env: str = os.getenv("APP_ENV", "development")
    log_level: str = os.getenv("LOG_LEVEL", "INFO")
    secret_key: str = os.getenv("SECRET_KEY", "dev-secret-key")

    redis: RedisConfig = field(default_factory=RedisConfig)
    aws: AWSConfig = field(default_factory=AWSConfig)
    ml: MLConfig = field(default_factory=MLConfig)
    collector: CollectorConfig = field(default_factory=CollectorConfig)
    alert: AlertConfig = field(default_factory=AlertConfig)
    dashboard: DashboardConfig = field(default_factory=DashboardConfig)

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"


# Global singleton
config = Config()