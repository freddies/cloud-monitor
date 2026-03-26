"""
Redis client wrapper for pub/sub messaging and metric storage.
"""

import json
import time
from typing import Optional, List, Callable
from datetime import datetime, timezone

import redis

from .config import config
from .logger import get_logger

logger = get_logger(__name__)


class RedisClient:
    """Thread-safe Redis client with pub/sub and time-series support."""

    # Redis key prefixes
    METRICS_STREAM = "stream:metrics"
    ANOMALIES_STREAM = "stream:anomalies"
    ALERTS_STREAM = "stream:alerts"
    METRICS_TS_PREFIX = "ts:metrics:"
    ANOMALY_HISTORY = "list:anomalies"
    ALERT_HISTORY = "list:alerts"
    MODEL_STATE = "hash:model_state"
    SERVICE_HEALTH = "hash:service_health"

    def __init__(self, redis_instance: Optional[redis.Redis] = None):
        if redis_instance:
            self._redis = redis_instance
        else:
            self._redis = redis.Redis(
                host=config.redis.host,
                port=config.redis.port,
                db=config.redis.db,
                password=config.redis.password or None,
                decode_responses=True,
                socket_connect_timeout=5,
                retry_on_timeout=True,
            )
        self._pubsub = None

    def ping(self) -> bool:
        try:
            return self._redis.ping()
        except redis.ConnectionError:
            return False

    # ── Stream Operations ──────────────────────────────────────

    def publish_metric(self, metric_data: dict) -> str:
        """Publish a metric to the metrics stream."""
        entry_id = self._redis.xadd(
            self.METRICS_STREAM,
            {"data": json.dumps(metric_data)},
            maxlen=100000,
        )
        # Also store in time-series sorted set
        metric_type = metric_data.get("metric_type", "unknown")
        host = metric_data.get("host", "unknown")
        ts_key = f"{self.METRICS_TS_PREFIX}{host}:{metric_type}"
        score = time.time()
        self._redis.zadd(ts_key, {json.dumps(metric_data): score})
        # Trim old entries (keep last 72h worth assuming 10s intervals)
        max_entries = (config.collector.retention_hours * 3600) // max(
            config.collector.interval_seconds, 1
        )
        self._redis.zremrangebyrank(ts_key, 0, -(max_entries + 1))
        return entry_id

    def publish_anomaly(self, anomaly_data: dict) -> str:
        entry_id = self._redis.xadd(
            self.ANOMALIES_STREAM,
            {"data": json.dumps(anomaly_data)},
            maxlen=10000,
        )
        self._redis.lpush(self.ANOMALY_HISTORY, json.dumps(anomaly_data))
        self._redis.ltrim(self.ANOMALY_HISTORY, 0, 9999)
        return entry_id

    def publish_alert(self, alert_data: dict) -> str:
        entry_id = self._redis.xadd(
            self.ALERTS_STREAM,
            {"data": json.dumps(alert_data)},
            maxlen=10000,
        )
        self._redis.lpush(self.ALERT_HISTORY, json.dumps(alert_data))
        self._redis.ltrim(self.ALERT_HISTORY, 0, 9999)
        return entry_id

    def read_stream(
        self, stream: str, last_id: str = "0", count: int = 100, block: int = 5000
    ) -> list:
        """Read from a Redis stream (blocking)."""
        try:
            results = self._redis.xread(
                {stream: last_id}, count=count, block=block
            )
            if results:
                entries = []
                for stream_name, messages in results:
                    for msg_id, msg_data in messages:
                        entries.append(
                            {
                                "id": msg_id,
                                "data": json.loads(msg_data.get("data", "{}")),
                            }
                        )
                return entries
            return []
        except redis.ConnectionError as e:
            logger.error(f"Redis connection error in read_stream: {e}")
            return []

    # ── Time-Series Queries ────────────────────────────────────

    def get_metric_history(
        self,
        host: str,
        metric_type: str,
        minutes: int = 60,
    ) -> List[dict]:
        """Get metric history for a host/metric in the last N minutes."""
        ts_key = f"{self.METRICS_TS_PREFIX}{host}:{metric_type}"
        min_score = time.time() - (minutes * 60)
        raw = self._redis.zrangebyscore(ts_key, min_score, "+inf")
        return [json.loads(r) for r in raw]

    def get_all_metric_keys(self) -> List[str]:
        """Return all time-series metric keys."""
        keys = self._redis.keys(f"{self.METRICS_TS_PREFIX}*")
        return keys

    def get_recent_metrics(self, count: int = 100) -> List[dict]:
        """Get most recent metrics from the stream."""
        results = self._redis.xrevrange(self.METRICS_STREAM, count=count)
        return [json.loads(data.get("data", "{}")) for _id, data in results]

    # ── Anomaly & Alert History ────────────────────────────────

    def get_recent_anomalies(self, count: int = 50) -> List[dict]:
        raw = self._redis.lrange(self.ANOMALY_HISTORY, 0, count - 1)
        return [json.loads(r) for r in raw]

    def get_recent_alerts(self, count: int = 50) -> List[dict]:
        raw = self._redis.lrange(self.ALERT_HISTORY, 0, count - 1)
        return [json.loads(r) for r in raw]

    # ── Model State ────────────────────────────────────────────

    def save_model_state(self, model_name: str, state_data: dict):
        self._redis.hset(
            self.MODEL_STATE, model_name, json.dumps(state_data)
        )

    def get_model_state(self, model_name: str) -> Optional[dict]:
        raw = self._redis.hget(self.MODEL_STATE, model_name)
        return json.loads(raw) if raw else None

    # ── Service Health ─────────────────────────────────────────

    def set_service_health(self, service_name: str, health_data: dict):
        self._redis.hset(
            self.SERVICE_HEALTH, service_name, json.dumps(health_data)
        )
        self._redis.expire(self.SERVICE_HEALTH, 300)

    def get_all_service_health(self) -> dict:
        raw = self._redis.hgetall(self.SERVICE_HEALTH)
        return {k: json.loads(v) for k, v in raw.items()}

    # ── Alert Cooldown ─────────────────────────────────────────

    def check_alert_cooldown(self, key: str) -> bool:
        """Return True if we should suppress (still in cooldown)."""
        return self._redis.exists(f"cooldown:{key}") > 0

    def set_alert_cooldown(self, key: str, minutes: int = None):
        if minutes is None:
            minutes = config.alert.cooldown_minutes
        self._redis.setex(f"cooldown:{key}", minutes * 60, "1")

    # ── Stats ──────────────────────────────────────────────────

    def get_stream_length(self, stream: str) -> int:
        try:
            return self._redis.xlen(stream)
        except Exception:
            return 0

    def get_stats(self) -> dict:
        return {
            "metrics_stream_length": self.get_stream_length(self.METRICS_STREAM),
            "anomalies_stream_length": self.get_stream_length(
                self.ANOMALIES_STREAM
            ),
            "alerts_stream_length": self.get_stream_length(self.ALERTS_STREAM),
            "anomaly_history_count": self._redis.llen(self.ANOMALY_HISTORY),
            "alert_history_count": self._redis.llen(self.ALERT_HISTORY),
            "total_metric_series": len(self.get_all_metric_keys()),
        }

    def flush_all(self):
        """Flush all data – USE ONLY IN TESTING."""
        self._redis.flushdb()