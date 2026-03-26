"""
Collects metrics from AWS CloudWatch for EC2, RDS, ELB, etc.
"""

from datetime import datetime, timedelta, timezone
from typing import List, Optional

import boto3
from botocore.exceptions import ClientError

from src.common.config import config
from src.common.models import MetricPoint
from src.common.logger import get_logger

logger = get_logger(__name__)


class CloudWatchCollector:
    """Pulls metrics from AWS CloudWatch."""

    METRIC_MAPPINGS = {
        "AWS/EC2": {
            "CPUUtilization": "cpu_usage",
            "NetworkIn": "network_in",
            "NetworkOut": "network_out",
            "DiskReadBytes": "disk_io_read",
            "DiskWriteBytes": "disk_io_write",
        },
        "AWS/RDS": {
            "CPUUtilization": "cpu_usage",
            "FreeableMemory": "memory_usage",
            "ReadLatency": "request_latency",
            "WriteLatency": "request_latency",
        },
        "AWS/ApplicationELB": {
            "TargetResponseTime": "request_latency",
            "HTTPCode_Target_5XX_Count": "error_rate",
            "RequestCount": "request_count",
        },
    }

    def __init__(self, boto_client=None):
        try:
            self.client = boto_client or boto3.client(
                "cloudwatch",
                region_name=config.aws.region,
                aws_access_key_id=config.aws.access_key_id or None,
                aws_secret_access_key=config.aws.secret_access_key or None,
            )
            self.enabled = True
        except Exception as e:
            logger.warning(f"CloudWatch not available: {e}")
            self.enabled = False

    def collect(
        self,
        namespace: str = "AWS/EC2",
        instance_ids: Optional[List[str]] = None,
        period: int = 300,
        minutes_back: int = 10,
    ) -> List[MetricPoint]:
        """Collect metrics from CloudWatch."""
        if not self.enabled:
            return []

        metrics = []
        metric_names = self.METRIC_MAPPINGS.get(namespace, {})
        dimensions_list = self._build_dimensions(namespace, instance_ids)

        end_time = datetime.now(timezone.utc)
        start_time = end_time - timedelta(minutes=minutes_back)

        for cw_metric, local_type in metric_names.items():
            for dimensions in dimensions_list:
                try:
                    response = self.client.get_metric_statistics(
                        Namespace=namespace,
                        MetricName=cw_metric,
                        Dimensions=dimensions,
                        StartTime=start_time,
                        EndTime=end_time,
                        Period=period,
                        Statistics=["Average", "Maximum"],
                    )

                    for dp in response.get("Datapoints", []):
                        instance_id = self._extract_instance_id(dimensions)
                        metrics.append(
                            MetricPoint(
                                metric_type=local_type,
                                value=dp.get("Average", dp.get("Maximum", 0)),
                                timestamp=dp["Timestamp"].isoformat(),
                                host=instance_id,
                                service=namespace,
                                tags={
                                    "source": "cloudwatch",
                                    "cw_metric": cw_metric,
                                    "max": dp.get("Maximum", 0),
                                },
                            )
                        )
                except ClientError as e:
                    logger.error(
                        f"CloudWatch API error for {cw_metric}: {e}"
                    )

        logger.info(f"Collected {len(metrics)} metrics from CloudWatch ({namespace})")
        return metrics

    def _build_dimensions(
        self, namespace: str, instance_ids: Optional[List[str]]
    ) -> List[List[dict]]:
        if not instance_ids:
            # Try to discover instances
            instance_ids = self._discover_instances(namespace)

        dim_name = {
            "AWS/EC2": "InstanceId",
            "AWS/RDS": "DBInstanceIdentifier",
            "AWS/ApplicationELB": "LoadBalancer",
        }.get(namespace, "InstanceId")

        return [[{"Name": dim_name, "Value": iid}] for iid in instance_ids]

    def _discover_instances(self, namespace: str) -> List[str]:
        """Auto-discover instance IDs."""
        try:
            if namespace == "AWS/EC2":
                ec2 = boto3.client(
                    "ec2",
                    region_name=config.aws.region,
                )
                resp = ec2.describe_instances(
                    Filters=[{"Name": "instance-state-name", "Values": ["running"]}]
                )
                ids = []
                for res in resp.get("Reservations", []):
                    for inst in res.get("Instances", []):
                        ids.append(inst["InstanceId"])
                return ids[:20]  # cap
        except Exception as e:
            logger.warning(f"Auto-discovery failed for {namespace}: {e}")
        return []

    @staticmethod
    def _extract_instance_id(dimensions: List[dict]) -> str:
        for d in dimensions:
            return d.get("Value", "unknown")
        return "unknown"