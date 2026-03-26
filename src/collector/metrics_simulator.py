"""
Simulates realistic cloud metrics for development/testing/demo.
Injects anomalies at configurable intervals to test detection.
"""

import math
import random
import time
from datetime import datetime, timezone
from typing import List

import numpy as np

from src.common.models import MetricPoint, MetricType
from src.common.logger import get_logger

logger = get_logger(__name__)


class MetricsSimulator:
    """
    Generates realistic synthetic metrics with:
    - Daily/weekly seasonality
    - Noise
    - Trend drift
    - Injected anomalies (spikes, dips, gradual degradation, pattern changes)
    """

    HOSTS = [
        "web-server-01",
        "web-server-02",
        "api-server-01",
        "api-server-02",
        "db-primary",
        "db-replica",
        "cache-01",
        "worker-01",
    ]

    METRIC_PROFILES = {
        MetricType.CPU_USAGE.value: {
            "base": 35.0,
            "amplitude": 20.0,
            "noise_std": 3.0,
            "min": 0.0,
            "max": 100.0,
        },
        MetricType.MEMORY_USAGE.value: {
            "base": 55.0,
            "amplitude": 10.0,
            "noise_std": 2.0,
            "min": 0.0,
            "max": 100.0,
        },
        MetricType.DISK_USAGE.value: {
            "base": 45.0,
            "amplitude": 2.0,
            "noise_std": 0.5,
            "min": 0.0,
            "max": 100.0,
        },
        MetricType.NETWORK_IN.value: {
            "base": 50.0,
            "amplitude": 30.0,
            "noise_std": 8.0,
            "min": 0.0,
            "max": 500.0,
        },
        MetricType.NETWORK_OUT.value: {
            "base": 30.0,
            "amplitude": 20.0,
            "noise_std": 5.0,
            "min": 0.0,
            "max": 500.0,
        },
        MetricType.REQUEST_LATENCY.value: {
            "base": 120.0,
            "amplitude": 40.0,
            "noise_std": 15.0,
            "min": 1.0,
            "max": 5000.0,
        },
        MetricType.ERROR_RATE.value: {
            "base": 0.5,
            "amplitude": 0.3,
            "noise_std": 0.2,
            "min": 0.0,
            "max": 100.0,
        },
        MetricType.REQUEST_COUNT.value: {
            "base": 500.0,
            "amplitude": 300.0,
            "noise_std": 50.0,
            "min": 0.0,
            "max": 10000.0,
        },
    }

    def __init__(
        self,
        anomaly_probability: float = 0.02,
        hosts: List[str] = None,
    ):
        self.anomaly_probability = anomaly_probability
        self.hosts = hosts or self.HOSTS
        self._step = 0
        self._active_anomaly = {}  # host -> {type, remaining_steps}

    def generate_batch(self) -> List[MetricPoint]:
        """Generate one batch of metrics for all hosts and metric types."""
        metrics = []
        self._step += 1

        for host in self.hosts:
            for metric_type, profile in self.METRIC_PROFILES.items():
                value = self._generate_value(host, metric_type, profile)
                value = self._maybe_inject_anomaly(host, metric_type, value, profile)
                value = max(profile["min"], min(profile["max"], value))

                metrics.append(
                    MetricPoint(
                        metric_type=metric_type,
                        value=round(value, 4),
                        host=host,
                        service=self._host_to_service(host),
                        tags={
                            "source": "simulator",
                            "step": self._step,
                            "is_anomaly": self._is_in_anomaly(host, metric_type),
                        },
                    )
                )

        logger.debug(
            f"Generated {len(metrics)} simulated metrics (step={self._step})"
        )
        return metrics

    def _generate_value(self, host: str, metric_type: str, profile: dict) -> float:
        """Generate a realistic value with seasonality and noise."""
        t = self._step
        host_offset = hash(host) % 100 / 10.0

        # Daily seasonality (24h period at 10s intervals = 8640 steps)
        daily = math.sin(2 * math.pi * t / 8640 + host_offset)
        # Weekly seasonality
        weekly = 0.3 * math.sin(2 * math.pi * t / (8640 * 7) + host_offset)

        value = (
            profile["base"]
            + profile["amplitude"] * (daily + weekly)
            + np.random.normal(0, profile["noise_std"])
            + host_offset
        )

        # Slight upward trend for disk usage (simulates filling)
        if metric_type == MetricType.DISK_USAGE.value:
            value += t * 0.0001

        return value

    def _maybe_inject_anomaly(
        self, host: str, metric_type: str, value: float, profile: dict
    ) -> float:
        """Randomly inject anomalies."""
        key = f"{host}:{metric_type}"

        # Continue existing anomaly
        if key in self._active_anomaly:
            anom = self._active_anomaly[key]
            anom["remaining"] -= 1
            if anom["remaining"] <= 0:
                del self._active_anomaly[key]
                return value
            return self._apply_anomaly(value, anom, profile)

        # Start new anomaly?
        if random.random() < self.anomaly_probability:
            anomaly_type = random.choice(
                ["spike", "dip", "gradual_rise", "flatline", "oscillation"]
            )
            duration = random.randint(3, 20)
            self._active_anomaly[key] = {
                "type": anomaly_type,
                "remaining": duration,
                "duration": duration,
                "magnitude": random.uniform(2.0, 5.0),
            }
            logger.info(
                f"💥 Injecting anomaly: {anomaly_type} on {host}/{metric_type} "
                f"for {duration} steps"
            )
            return self._apply_anomaly(
                value, self._active_anomaly[key], profile
            )

        return value

    def _apply_anomaly(self, value: float, anom: dict, profile: dict) -> float:
        """Apply the anomaly transformation."""
        mag = anom["magnitude"]
        progress = 1 - (anom["remaining"] / max(anom["duration"], 1))

        if anom["type"] == "spike":
            return value + profile["amplitude"] * mag
        elif anom["type"] == "dip":
            return max(profile["min"], value - profile["amplitude"] * mag)
        elif anom["type"] == "gradual_rise":
            return value + profile["amplitude"] * mag * progress
        elif anom["type"] == "flatline":
            return profile["base"] * 0.1
        elif anom["type"] == "oscillation":
            return value + profile["amplitude"] * mag * math.sin(progress * 20)
        return value

    def _is_in_anomaly(self, host: str, metric_type: str) -> bool:
        return f"{host}:{metric_type}" in self._active_anomaly

    @staticmethod
    def _host_to_service(host: str) -> str:
        if "web" in host:
            return "web"
        elif "api" in host:
            return "api"
        elif "db" in host:
            return "database"
        elif "cache" in host:
            return "cache"
        elif "worker" in host:
            return "worker"
        return "unknown"