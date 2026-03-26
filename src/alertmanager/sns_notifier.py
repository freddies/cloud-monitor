"""
AWS SNS and multi-channel notification sender.
Supports SNS, Slack webhooks, and email (via SNS).
"""

import json
import requests
from typing import Optional

import boto3
from botocore.exceptions import ClientError

from src.common.config import config
from src.common.models import Alert, Severity
from src.common.logger import get_logger

logger = get_logger(__name__)


class SNSNotifier:
    """Multi-channel alert notifier."""

    SEVERITY_EMOJI = {
        Severity.LOW.value: "🟡",
        Severity.MEDIUM.value: "🟠",
        Severity.HIGH.value: "🔴",
        Severity.CRITICAL.value: "🚨",
    }

    def __init__(self, boto_client=None):
        try:
            self.sns_client = boto_client or boto3.client(
                "sns",
                region_name=config.aws.region,
                aws_access_key_id=config.aws.access_key_id or None,
                aws_secret_access_key=config.aws.secret_access_key or None,
            )
            self.sns_enabled = bool(config.aws.sns_topic_arn)
        except Exception as e:
            logger.warning(f"SNS client not available: {e}")
            self.sns_client = None
            self.sns_enabled = False

        self.slack_enabled = bool(config.alert.slack_webhook_url)

    def send_alert(self, alert: Alert) -> dict:
        """Send alert through all configured channels."""
        results = {}

        # SNS
        if self.sns_enabled:
            results["sns"] = self._send_sns(alert)

        # Slack
        if self.slack_enabled:
            results["slack"] = self._send_slack(alert)

        # Log
        results["log"] = True
        self._log_alert(alert)

        return results

    def _send_sns(self, alert: Alert) -> bool:
        """Send alert via AWS SNS."""
        if not self.sns_client:
            return False

        try:
            emoji = self.SEVERITY_EMOJI.get(alert.severity, "⚠️")
            subject = f"{emoji} {alert.title}"[:100]  # SNS subject limit

            message = self._format_sns_message(alert)

            response = self.sns_client.publish(
                TopicArn=config.aws.sns_topic_arn,
                Subject=subject,
                Message=message,
                MessageAttributes={
                    "severity": {
                        "DataType": "String",
                        "StringValue": alert.severity,
                    },
                    "host": {
                        "DataType": "String",
                        "StringValue": alert.host,
                    },
                },
            )

            message_id = response.get("MessageId")
            logger.info(f"SNS notification sent: {message_id}")
            return True

        except ClientError as e:
            logger.error(f"SNS send failed: {e}")
            return False

    def _send_slack(self, alert: Alert) -> bool:
        """Send alert via Slack webhook."""
        try:
            emoji = self.SEVERITY_EMOJI.get(alert.severity, "⚠️")
            color_map = {
                Severity.LOW.value: "#FFC107",
                Severity.MEDIUM.value: "#FF9800",
                Severity.HIGH.value: "#F44336",
                Severity.CRITICAL.value: "#B71C1C",
            }

            payload = {
                "username": "Cloud Monitor",
                "icon_emoji": ":warning:",
                "attachments": [
                    {
                        "color": color_map.get(alert.severity, "#757575"),
                        "title": f"{emoji} {alert.title}",
                        "text": alert.description,
                        "fields": [
                            {
                                "title": "Host",
                                "value": alert.host,
                                "short": True,
                            },
                            {
                                "title": "Metric",
                                "value": alert.metric_type,
                                "short": True,
                            },
                            {
                                "title": "Severity",
                                "value": alert.severity.upper(),
                                "short": True,
                            },
                            {
                                "title": "Status",
                                "value": alert.status,
                                "short": True,
                            },
                            {
                                "title": "Time",
                                "value": alert.timestamp,
                                "short": False,
                            },
                        ],
                        "footer": "AI Cloud Monitor",
                        "ts": alert.timestamp,
                    }
                ],
            }

            response = requests.post(
                config.alert.slack_webhook_url,
                json=payload,
                timeout=10,
            )
            response.raise_for_status()
            logger.info("Slack notification sent")
            return True

        except Exception as e:
            logger.error(f"Slack send failed: {e}")
            return False

    def _format_sns_message(self, alert: Alert) -> str:
        """Format a detailed SNS message."""
        emoji = self.SEVERITY_EMOJI.get(alert.severity, "⚠️")
        return f"""
{emoji} CLOUD MONITOR ALERT {emoji}
{'=' * 50}
Title:      {alert.title}
Severity:   {alert.severity.upper()}
Status:     {alert.status}
Time:       {alert.timestamp}

Host:       {alert.host}
Service:    {alert.service}
Metric:     {alert.metric_type}
Alert ID:   {alert.alert_id}

Description:
{alert.description}

{'=' * 50}
This is an automated alert from AI-Powered Cloud Monitor.
To acknowledge: POST /api/alerts/{alert.alert_id}/acknowledge
To resolve:     POST /api/alerts/{alert.alert_id}/resolve
"""

    @staticmethod
    def _log_alert(alert: Alert):
        """Log the alert for audit trail."""
        logger.warning(
            f"ALERT [{alert.severity.upper()}] "
            f"{alert.host}/{alert.metric_type}: {alert.title}"
        )