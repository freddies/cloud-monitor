"""
Collects real system telemetry (CPU, memory, disk, network) via psutil.
"""

import time
import psutil
from typing import List

from src.common.models import MetricPoint, MetricType
from src.common.logger import get_logger

logger = get_logger(__name__)


class TelemetryCollector:
    """Collects host-level system metrics."""

    def __init__(self, hostname: str = None, service_name: str = "host"):
        import socket

        self.hostname = hostname or socket.gethostname()
        self.service_name = service_name
        self._prev_net = psutil.net_io_counters()
        self._prev_disk = psutil.disk_io_counters()
        self._prev_time = time.time()

    def collect_all(self) -> List[MetricPoint]:
        """Collect all system metrics and return as MetricPoint list."""
        metrics = []
        try:
            metrics.extend(self._collect_cpu())
            metrics.extend(self._collect_memory())
            metrics.extend(self._collect_disk())
            metrics.extend(self._collect_network())
        except Exception as e:
            logger.error(f"Error collecting system metrics: {e}")
        return metrics

    def _collect_cpu(self) -> List[MetricPoint]:
        cpu_percent = psutil.cpu_percent(interval=1)
        per_cpu = psutil.cpu_percent(interval=0, percpu=True)
        metrics = [
            MetricPoint(
                metric_type=MetricType.CPU_USAGE.value,
                value=cpu_percent,
                host=self.hostname,
                service=self.service_name,
                tags={"unit": "percent", "cpu": "total"},
            )
        ]
        for i, pct in enumerate(per_cpu):
            metrics.append(
                MetricPoint(
                    metric_type=MetricType.CPU_USAGE.value,
                    value=pct,
                    host=self.hostname,
                    service=self.service_name,
                    tags={"unit": "percent", "cpu": f"core_{i}"},
                )
            )
        return metrics

    def _collect_memory(self) -> List[MetricPoint]:
        mem = psutil.virtual_memory()
        return [
            MetricPoint(
                metric_type=MetricType.MEMORY_USAGE.value,
                value=mem.percent,
                host=self.hostname,
                service=self.service_name,
                tags={
                    "unit": "percent",
                    "total_gb": round(mem.total / (1024**3), 2),
                    "available_gb": round(mem.available / (1024**3), 2),
                },
            )
        ]

    def _collect_disk(self) -> List[MetricPoint]:
        disk = psutil.disk_usage("/")
        disk_io = psutil.disk_io_counters()
        now = time.time()
        elapsed = max(now - self._prev_time, 0.1)

        read_rate = (disk_io.read_bytes - self._prev_disk.read_bytes) / elapsed
        write_rate = (disk_io.write_bytes - self._prev_disk.write_bytes) / elapsed

        self._prev_disk = disk_io

        return [
            MetricPoint(
                metric_type=MetricType.DISK_USAGE.value,
                value=disk.percent,
                host=self.hostname,
                service=self.service_name,
                tags={
                    "unit": "percent",
                    "total_gb": round(disk.total / (1024**3), 2),
                },
            ),
            MetricPoint(
                metric_type=MetricType.DISK_IO_READ.value,
                value=round(read_rate / (1024**2), 2),
                host=self.hostname,
                service=self.service_name,
                tags={"unit": "MB/s"},
            ),
            MetricPoint(
                metric_type=MetricType.DISK_IO_WRITE.value,
                value=round(write_rate / (1024**2), 2),
                host=self.hostname,
                service=self.service_name,
                tags={"unit": "MB/s"},
            ),
        ]

    def _collect_network(self) -> List[MetricPoint]:
        net = psutil.net_io_counters()
        now = time.time()
        elapsed = max(now - self._prev_time, 0.1)

        bytes_in = (net.bytes_recv - self._prev_net.bytes_recv) / elapsed
        bytes_out = (net.bytes_sent - self._prev_net.bytes_sent) / elapsed

        self._prev_net = net
        self._prev_time = now

        return [
            MetricPoint(
                metric_type=MetricType.NETWORK_IN.value,
                value=round(bytes_in / (1024**2), 4),
                host=self.hostname,
                service=self.service_name,
                tags={"unit": "MB/s"},
            ),
            MetricPoint(
                metric_type=MetricType.NETWORK_OUT.value,
                value=round(bytes_out / (1024**2), 4),
                host=self.hostname,
                service=self.service_name,
                tags={"unit": "MB/s"},
            ),
        ]