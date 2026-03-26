"""
Data models used across all services.
"""

import json
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from typing import Optional


class Severity(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class MetricType(str, Enum):
    CPU_USAGE = "cpu_usage"
    MEMORY_USAGE = "memory_usage"
    DISK_USAGE = "disk_usage"
    NETWORK_IN = "network_in"
    NETWORK_OUT = "network_out"
    REQUEST_LATENCY = "request_latency"
    ERROR_RATE = "error_rate"
    REQUEST_COUNT = "request_count"
    DISK_IO_READ = "disk_io_read"
    DISK_IO_WRITE = "disk_io_write"


class AlertStatus(str, Enum):
    FIRING = "firing"
    ACKNOWLEDGED = "acknowledged"
    RESOLVED = "resolved"


@dataclass
class MetricPoint:
    metric_type: str
    value: float
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    host: str = "unknown"
    service: str = "unknown"
    tags: dict = field(default_factory=dict)
    metric_id: str = field(default_factory=lambda: str(uuid.uuid4()))

    def to_dict(self) -> dict:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict())

    @classmethod
    def from_dict(cls, data: dict) -> "MetricPoint":
        return cls(**data)

    @classmethod
    def from_json(cls, json_str: str) -> "MetricPoint":
        return cls.from_dict(json.loads(json_str))


@dataclass
class Anomaly:
    metric_type: str
    value: float
    expected_range: tuple
    anomaly_score: float
    severity: str
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    host: str = "unknown"
    service: str = "unknown"
    model_used: str = "unknown"
    anomaly_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    description: str = ""

    def to_dict(self) -> dict:
        d = asdict(self)
        d["expected_range"] = list(self.expected_range)
        return d

    def to_json(self) -> str:
        return json.dumps(self.to_dict())

    @classmethod
    def from_dict(cls, data: dict) -> "Anomaly":
        if isinstance(data.get("expected_range"), list):
            data["expected_range"] = tuple(data["expected_range"])
        return cls(**data)


@dataclass
class Alert:
    anomaly_id: str
    severity: str
    title: str
    description: str
    metric_type: str
    host: str = "unknown"
    service: str = "unknown"
    status: str = AlertStatus.FIRING.value
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    alert_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    acknowledged_by: Optional[str] = None
    resolved_at: Optional[str] = None

    def to_dict(self) -> dict:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict())

    @classmethod
    def from_dict(cls, data: dict) -> "Alert":
        return cls(**data)


@dataclass
class HealthStatus:
    service_name: str
    status: str  # "healthy", "degraded", "unhealthy"
    uptime_seconds: float
    last_check: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    details: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)