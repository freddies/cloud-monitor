"""Tests for the collector service."""

import pytest
from src.collector.metrics_simulator import MetricsSimulator
from src.collector.telemetry_collector import TelemetryCollector
from src.common.models import MetricPoint, MetricType


class TestMetricsSimulator:
    def setup_method(self):
        self.simulator = MetricsSimulator(
            anomaly_probability=0.0,
            hosts=["test-host-01", "test-host-02"],
        )

    def test_generate_batch_returns_metrics(self):
        batch = self.simulator.generate_batch()
        assert len(batch) > 0
        assert all(isinstance(m, MetricPoint) for m in batch)

    def test_generate_batch_covers_all_metric_types(self):
        batch = self.simulator.generate_batch()
        metric_types = {m.metric_type for m in batch}
        for mt in MetricsSimulator.METRIC_PROFILES:
            assert mt in metric_types

    def test_generate_batch_covers_all_hosts(self):
        batch = self.simulator.generate_batch()
        hosts = {m.host for m in batch}
        assert "test-host-01" in hosts
        assert "test-host-02" in hosts

    def test_values_within_bounds(self):
        for _ in range(10):
            batch = self.simulator.generate_batch()
            for m in batch:
                profile = MetricsSimulator.METRIC_PROFILES.get(m.metric_type, {})
                assert m.value >= profile.get("min", float("-inf"))
                assert m.value <= profile.get("max", float("inf"))

    def test_anomaly_injection(self):
        sim = MetricsSimulator(
            anomaly_probability=1.0,  # Always inject
            hosts=["anomaly-host"],
        )
        anomaly_found = False
        for _ in range(20):
            batch = sim.generate_batch()
            for m in batch:
                if m.tags.get("is_anomaly"):
                    anomaly_found = True
                    break
            if anomaly_found:
                break
        assert anomaly_found

    def test_metric_point_serialization(self):
        batch = self.simulator.generate_batch()
        for m in batch:
            d = m.to_dict()
            assert "metric_type" in d
            assert "value" in d
            assert "host" in d
            assert "timestamp" in d

            json_str = m.to_json()
            restored = MetricPoint.from_json(json_str)
            assert restored.metric_type == m.metric_type
            assert restored.value == m.value


class TestTelemetryCollector:
    def test_collect_all(self):
        collector = TelemetryCollector(hostname="test-host")
        metrics = collector.collect_all()

        assert len(metrics) > 0
        assert all(isinstance(m, MetricPoint) for m in metrics)

        metric_types = {m.metric_type for m in metrics}
        assert MetricType.CPU_USAGE.value in metric_types
        assert MetricType.MEMORY_USAGE.value in metric_types

    def test_host_name_set(self):
        collector = TelemetryCollector(hostname="custom-host")
        metrics = collector.collect_all()

        for m in metrics:
            assert m.host == "custom-host"

    def test_cpu_usage_in_range(self):
        collector = TelemetryCollector()
        metrics = collector.collect_all()

        cpu_metrics = [m for m in metrics if m.metric_type == MetricType.CPU_USAGE.value]
        assert len(cpu_metrics) > 0
        for m in cpu_metrics:
            assert 0 <= m.value <= 100