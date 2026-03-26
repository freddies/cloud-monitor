"""
Sets up required AWS resources (SNS topic, S3 bucket).
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import boto3
from botocore.exceptions import ClientError

from src.common.config import config
from src.common.logger import get_logger

logger = get_logger("aws-setup")


def main():
    region = config.aws.region
    logger.info(f"Setting up AWS resources in {region}...")

    # ── SNS Topic ──────────────────────────────────────────
    try:
        sns = boto3.client("sns", region_name=region)
        response = sns.create_topic(
            Name="cloud-monitor-alerts",
            Tags=[
                {"Key": "Project", "Value": "cloud-monitor"},
                {"Key": "Environment", "Value": config.app_env},
            ],
        )
        topic_arn = response["TopicArn"]
        logger.info(f"✅ SNS Topic created/found: {topic_arn}")

        # Subscribe email if configured
        if config.alert.email:
            sns.subscribe(
                TopicArn=topic_arn,
                Protocol="email",
                Endpoint=config.alert.email,
            )
            logger.info(f"📧 Subscribed {config.alert.email} to SNS topic")
            logger.info("   Check email to confirm subscription!")
    except ClientError as e:
        logger.error(f"SNS setup failed: {e}")

    # ── S3 Bucket ──────────────────────────────────────────
    try:
        s3 = boto3.client("s3", region_name=region)
        bucket = config.aws.s3_bucket

        try:
            s3.head_bucket(Bucket=bucket)
            logger.info(f"✅ S3 bucket already exists: {bucket}")
        except ClientError:
            create_kwargs = {"Bucket": bucket}
            if region != "us-east-1":
                create_kwargs["CreateBucketConfiguration"] = {
                    "LocationConstraint": region
                }
            s3.create_bucket(**create_kwargs)
            logger.info(f"✅ S3 bucket created: {bucket}")

        # Enable versioning
        s3.put_bucket_versioning(
            Bucket=bucket,
            VersioningConfiguration={"Status": "Enabled"},
        )
    except ClientError as e:
        logger.error(f"S3 setup failed: {e}")

    # ── CloudWatch ─────────────────────────────────────────
    try:
        cw = boto3.client("cloudwatch", region_name=region)
        cw.put_metric_data(
            Namespace=config.aws.cloudwatch_namespace,
            MetricData=[
                {
                    "MetricName": "SetupTest",
                    "Value": 1.0,
                    "Unit": "Count",
                }
            ],
        )
        logger.info(
            f"✅ CloudWatch namespace ready: {config.aws.cloudwatch_namespace}"
        )
    except ClientError as e:
        logger.error(f"CloudWatch setup failed: {e}")

    logger.info("\n🎉 AWS setup complete!")
    logger.info(f"   SNS_TOPIC_ARN={topic_arn}")
    logger.info(f"   S3_BUCKET={config.aws.s3_bucket}")


if __name__ == "__main__":
    main()