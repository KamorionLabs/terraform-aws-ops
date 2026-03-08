"""
Replication Checker Lambda
==========================
Checks the status of data replication components.

This Lambda focuses on DMS replication monitoring.
For EFS replication, use the existing check-efs-replications.py script
or the Step Function check_replication_sync.asl.json.

Environment Variables:
- STATE_TABLE_NAME: DynamoDB table name for state storage
- LOG_LEVEL: Logging level (default: INFO)
"""

import logging
import os
import sys
from datetime import datetime, timezone

import boto3
from botocore.exceptions import ClientError

# Add shared modules to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "shared"))
from base_checker import BaseChecker

logger = logging.getLogger(__name__)
logger.setLevel(os.environ.get("LOG_LEVEL", "INFO"))


def check_dms_replication_task(task_arn: str, region: str = None) -> dict:
    """Check DMS replication task status and lag."""
    dms = boto3.client("dms", region_name=region)

    try:
        response = dms.describe_replication_tasks(
            Filters=[{"Name": "replication-task-arn", "Values": [task_arn]}]
        )

        if not response.get("ReplicationTasks"):
            return {
                "status": "not_found",
                "task_arn": task_arn,
                "available": False,
            }

        task = response["ReplicationTasks"][0]
        status = task.get("Status", "unknown")

        # Get replication statistics
        stats = task.get("ReplicationTaskStats", {})

        # Calculate lag in seconds
        cdc_latency_source = stats.get("CDCLatencySource", 0)
        cdc_latency_target = stats.get("CDCLatencyTarget", 0)

        return {
            "status": status,
            "task_arn": task_arn,
            "task_identifier": task.get("ReplicationTaskIdentifier"),
            "available": status == "running",
            "migration_type": task.get("MigrationType"),
            "tables_loaded": stats.get("TablesLoaded", 0),
            "tables_loading": stats.get("TablesLoading", 0),
            "tables_errored": stats.get("TablesErrored", 0),
            "cdc_latency_source_seconds": cdc_latency_source,
            "cdc_latency_target_seconds": cdc_latency_target,
            "total_lag_seconds": cdc_latency_source + cdc_latency_target,
            "full_load_progress_percent": stats.get("FullLoadProgressPercent", 0),
        }

    except ClientError as e:
        error_code = e.response.get("Error", {}).get("Code", "Unknown")
        if error_code == "ResourceNotFoundFault":
            return {
                "status": "not_found",
                "task_arn": task_arn,
                "available": False,
            }
        logger.error(f"Error checking DMS task {task_arn}: {e}")
        return {
            "status": "error",
            "task_arn": task_arn,
            "available": False,
            "error": str(e),
        }


def check_dms_replication_instance(instance_arn: str, region: str = None) -> dict:
    """Check DMS replication instance status."""
    dms = boto3.client("dms", region_name=region)

    try:
        response = dms.describe_replication_instances(
            Filters=[{"Name": "replication-instance-arn", "Values": [instance_arn]}]
        )

        if not response.get("ReplicationInstances"):
            return {
                "status": "not_found",
                "instance_arn": instance_arn,
                "available": False,
            }

        instance = response["ReplicationInstances"][0]
        status = instance.get("ReplicationInstanceStatus", "unknown")

        return {
            "status": status,
            "instance_arn": instance_arn,
            "instance_identifier": instance.get("ReplicationInstanceIdentifier"),
            "available": status == "available",
            "instance_class": instance.get("ReplicationInstanceClass"),
            "engine_version": instance.get("EngineVersion"),
            "multi_az": instance.get("MultiAZ", False),
            "publicly_accessible": instance.get("PubliclyAccessible", False),
        }

    except ClientError as e:
        logger.error(f"Error checking DMS instance {instance_arn}: {e}")
        return {
            "status": "error",
            "instance_arn": instance_arn,
            "available": False,
            "error": str(e),
        }


class ReplicationChecker(BaseChecker):
    """
    Replication checker for DMS.

    Note: For EFS replication, use the existing check-efs-replications.py script
    or the Step Function check_replication_sync.asl.json instead.

    Config structure:
    {
        "region": "eu-central-1",  # optional
        "dms_tasks": ["arn:aws:dms:..."],  # optional - list of task ARNs
        "dms_instances": ["arn:aws:dms:..."],  # optional - list of instance ARNs
        "max_lag_seconds": 300  # optional, threshold for acceptable lag
    }
    """

    domain = "mro"  # Default domain, can be overridden in event
    check_type = "replication"

    def run_checks(self, config: dict) -> dict:
        region = config.get("region", os.environ.get("AWS_REGION"))
        max_lag = config.get("max_lag_seconds", 300)
        checks = {}

        # Check DMS replication instances
        dms_instances = config.get("dms_instances", [])
        if dms_instances:
            checks["dms_instances"] = {}
            for instance_arn in dms_instances:
                instance_id = instance_arn.split(":")[-1] if ":" in instance_arn else instance_arn
                checks["dms_instances"][instance_id] = check_dms_replication_instance(instance_arn, region)

        # Check DMS replication tasks
        dms_tasks = config.get("dms_tasks", [])
        if dms_tasks:
            checks["dms_tasks"] = {}
            for task_arn in dms_tasks:
                task_id = task_arn.split(":")[-1] if ":" in task_arn else task_arn
                result = check_dms_replication_task(task_arn, region)
                # Add lag threshold check
                if result.get("total_lag_seconds", 0) > max_lag:
                    result["lag_warning"] = True
                    result["lag_exceeded_threshold"] = True
                checks["dms_tasks"][task_id] = result

        return checks

    def determine_status(self, checks: dict) -> str:
        """
        Custom status determination for replication checks.
        Takes into account lag thresholds.
        """
        all_available = []
        has_lag_warning = False

        for check_type, check_data in checks.items():
            if isinstance(check_data, dict):
                for item_id, item in check_data.items():
                    if isinstance(item, dict):
                        all_available.append(item.get("available", False))
                        if item.get("lag_warning"):
                            has_lag_warning = True

        if not all_available:
            return "unknown"
        if all(all_available):
            return "ready" if not has_lag_warning else "degraded"
        elif any(all_available):
            return "degraded"
        else:
            return "not_ready"


# Lambda handler - singleton pattern for warm starts
_checker = None


def lambda_handler(event, context):
    """
    Lambda entry point.

    Event structure:
    {
        "target": "mi2-preprod",
        "domain": "mro",  # optional, defaults to "mro"
        "config": {
            "region": "eu-central-1",
            "dms_tasks": ["arn:aws:dms:eu-central-1:123456789:task:xxx"],
            "dms_instances": ["arn:aws:dms:eu-central-1:123456789:rep:xxx"],
            "max_lag_seconds": 300
        },
        "save_state": true  # optional, defaults to true
    }
    """
    global _checker
    if _checker is None:
        _checker = ReplicationChecker()

    return _checker.execute(event, context)
