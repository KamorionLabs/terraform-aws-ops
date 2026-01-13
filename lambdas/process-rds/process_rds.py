"""
Process RDS/Aurora Cluster Information
======================================
Processes raw RDS API results from Step Function and formats for DynamoDB storage.

This Lambda:
- Filters parameter groups to only user-modified parameters (Source=user)
- Extracts relevant cluster and instance information
- Calculates health status based on cluster/instance states
- Formats output for save-state Lambda

Environment Variables:
- LOG_LEVEL: Logging level (default: INFO)
"""

import logging
import os
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)
logger.setLevel(os.environ.get("LOG_LEVEL", "INFO"))


# Status values that indicate healthy state
HEALTHY_CLUSTER_STATUSES = {"available"}
WARNING_CLUSTER_STATUSES = {"backing-up", "maintenance", "modifying", "upgrading"}
CRITICAL_CLUSTER_STATUSES = {"failed", "failing-over", "inaccessible-encryption-credentials"}

HEALTHY_INSTANCE_STATUSES = {"available"}
WARNING_INSTANCE_STATUSES = {"backing-up", "maintenance", "modifying", "upgrading", "configuring-enhanced-monitoring"}
CRITICAL_INSTANCE_STATUSES = {"failed", "incompatible-parameters", "incompatible-restore", "storage-full"}


def filter_user_parameters(parameters: list) -> dict:
    """
    Filter parameters to only include user-modified ones (Source=user).

    Returns dict of {parameter_name: parameter_value}
    """
    user_params = {}
    for param in parameters:
        source = param.get("Source", "")
        if source == "user":
            name = param.get("ParameterName", "")
            value = param.get("ParameterValue", "")
            if name:
                user_params[name] = value
    return user_params


def extract_cluster_info(cluster: dict) -> dict:
    """Extract relevant cluster information."""
    return {
        "identifier": cluster.get("DbClusterIdentifier", ""),
        "status": cluster.get("Status", "unknown"),
        "engine": cluster.get("Engine", ""),
        "engineVersion": cluster.get("EngineVersion", ""),
        "endpoint": cluster.get("Endpoint", ""),
        "readerEndpoint": cluster.get("ReaderEndpoint", ""),
        "port": cluster.get("Port", 0),
        "multiAZ": cluster.get("MultiAZ", False),
        "storageEncrypted": cluster.get("StorageEncrypted", False),
        "deletionProtection": cluster.get("DeletionProtection", False),
        "dbClusterParameterGroup": cluster.get("DbClusterParameterGroup", ""),
        "engineMode": cluster.get("EngineMode", "provisioned"),
        "dbClusterInstanceClass": cluster.get("DbClusterInstanceClass"),
        "iamDatabaseAuthenticationEnabled": cluster.get("IAMDatabaseAuthenticationEnabled", False),
    }


def extract_instance_info(instance: dict) -> dict:
    """Extract relevant instance information."""
    # Get parameter group status
    param_groups = instance.get("DbParameterGroups", [])
    param_group_name = ""
    param_group_status = "unknown"
    if param_groups:
        param_group_name = param_groups[0].get("DbParameterGroupName", "")
        param_group_status = param_groups[0].get("ParameterApplyStatus", "unknown")

    return {
        "identifier": instance.get("DbInstanceIdentifier", ""),
        "status": instance.get("DbInstanceStatus", "unknown"),
        "instanceClass": instance.get("DbInstanceClass", ""),
        "availabilityZone": instance.get("AvailabilityZone", ""),
        "isWriter": instance.get("IsClusterWriter", False),
        "performanceInsightsEnabled": instance.get("PerformanceInsightsEnabled", False),
        "dbParameterGroupName": param_group_name,
        "dbParameterGroupStatus": param_group_status,
        "engine": instance.get("Engine", ""),
        "engineVersion": instance.get("EngineVersion", ""),
        "publiclyAccessible": instance.get("PubliclyAccessible", False),
        "autoMinorVersionUpgrade": instance.get("AutoMinorVersionUpgrade", False),
    }


def extract_instances_from_cluster(cluster: dict) -> list:
    """Extract basic instance info from cluster members."""
    instances = []
    members = cluster.get("DbClusterMembers", [])
    for member in members:
        instances.append({
            "identifier": member.get("DbInstanceIdentifier", ""),
            "isWriter": member.get("IsClusterWriter", False),
            "promotionTier": member.get("PromotionTier", 0),
        })
    return instances


def calculate_status(cluster: dict, instances: list, param_results: list) -> tuple:
    """
    Calculate overall health status and identify issues.

    Returns (status, issues) tuple.
    """
    issues = []
    status = "ok"

    # Check cluster status
    cluster_status = cluster.get("Status", "unknown").lower()
    if cluster_status in CRITICAL_CLUSTER_STATUSES:
        status = "critical"
        issues.append(f"Cluster status is {cluster_status}")
    elif cluster_status in WARNING_CLUSTER_STATUSES:
        if status != "critical":
            status = "warning"
        issues.append(f"Cluster status is {cluster_status}")
    elif cluster_status not in HEALTHY_CLUSTER_STATUSES:
        if status != "critical":
            status = "warning"
        issues.append(f"Cluster status is {cluster_status}")

    # Check for writer instance
    writers = [m for m in cluster.get("DbClusterMembers", []) if m.get("IsClusterWriter")]
    if not writers:
        status = "critical"
        issues.append("No writer instance available")

    # Check deletion protection (warning if disabled)
    if not cluster.get("DeletionProtection", False):
        if status != "critical":
            status = "warning"
        issues.append("Deletion protection is disabled")

    # Check backup retention
    backup_retention = cluster.get("BackupRetentionPeriod", 0)
    if backup_retention < 7:
        if status != "critical":
            status = "warning"
        issues.append(f"Backup retention period is only {backup_retention} days")

    # Check instance parameter group status from param results
    for result in param_results:
        if result.get("type") == "instance":
            instance_info = result.get("instanceInfo", {})
            if instance_info:
                param_groups = instance_info.get("DbParameterGroups", [])
                for pg in param_groups:
                    pg_status = pg.get("ParameterApplyStatus", "")
                    if pg_status == "pending-reboot":
                        if status != "critical":
                            status = "warning"
                        issues.append(f"Instance parameter group requires reboot")

    # Check for errors in parameter fetching
    for result in param_results:
        if result.get("error"):
            if status != "critical":
                status = "warning"
            issues.append(f"Failed to fetch {result.get('type')} parameters: {result.get('error')}")

    return status, issues


def lambda_handler(event: dict, context: Any) -> dict:
    """
    Lambda entry point.

    Event structure (from Step Function):
    {
        "Input": {
            "Project": "mro-mi2",
            "Env": "nh-ppd",
            "Instance": "MI2",
            "Environment": "ppd",
            "DbClusterIdentifier": "rubix-dig-ppd-aurora-mi2",
            "CrossAccountRoleArn": "arn:aws:iam::..."
        },
        "Timestamp": "2024-01-15T10:30:00Z",
        "Cluster": { ... raw DescribeDBClusters result ... },
        "ParameterGroupResults": [
            {"type": "cluster", "parameters": [...]},
            {"type": "instance", "parameters": [...], "instanceInfo": {...}}
        ]
    }

    Returns formatted state for save-state Lambda.
    """
    logger.info(f"Processing RDS cluster info for {event.get('Input', {}).get('DbClusterIdentifier')}")

    input_data = event.get("Input", {})
    timestamp = event.get("Timestamp", datetime.now(timezone.utc).isoformat())
    cluster = event.get("Cluster", {})
    param_results = event.get("ParameterGroupResults", [])

    # Extract cluster info
    cluster_info = extract_cluster_info(cluster)

    # Extract instances from cluster members
    instances = extract_instances_from_cluster(cluster)

    # Process parameter groups
    cluster_params = {}
    instance_params = {}
    instance_info = None
    cluster_param_group_name = cluster.get("DbClusterParameterGroup", "")
    instance_param_group_name = ""

    for result in param_results:
        params = result.get("parameters", [])
        if result.get("type") == "cluster":
            cluster_params = filter_user_parameters(params)
        elif result.get("type") == "instance":
            instance_params = filter_user_parameters(params)
            instance_info = result.get("instanceInfo")
            if instance_info:
                param_groups = instance_info.get("DbParameterGroups", [])
                if param_groups:
                    instance_param_group_name = param_groups[0].get("DbParameterGroupName", "")

    # Build detailed instance info if available
    detailed_instances = []
    if instance_info:
        detailed_instances.append(extract_instance_info(instance_info))
    else:
        # Use basic info from cluster members
        detailed_instances = instances

    # Calculate status
    status, issues = calculate_status(cluster, instances, param_results)

    # Build parameter groups structure
    parameter_groups = {
        "cluster": {
            "name": cluster_param_group_name,
            "family": "",  # Not available from cluster response
            "parameters": cluster_params,
            "parameterCount": len(cluster_params),
        },
        "instance": {
            "name": instance_param_group_name,
            "family": "",
            "parameters": instance_params,
            "parameterCount": len(instance_params),
        },
    }

    # Build backup info
    backup_info = {
        "backupRetentionPeriod": cluster.get("BackupRetentionPeriod", 0),
        "preferredBackupWindow": cluster.get("PreferredBackupWindow", ""),
        "preferredMaintenanceWindow": cluster.get("PreferredMaintenanceWindow", ""),
        "latestRestorableTime": cluster.get("LatestRestorableTime", ""),
    }

    # Build storage info
    storage_info = {
        "storageType": cluster.get("StorageType", "aurora"),
        "allocatedStorage": cluster.get("AllocatedStorage", 0),
        "iops": cluster.get("Iops"),
    }

    # Build summary
    summary = {
        "instanceCount": len(instances),
        "writerCount": len([i for i in instances if i.get("isWriter")]),
        "readerCount": len([i for i in instances if not i.get("isWriter")]),
        "clusterParameterCount": len(cluster_params),
        "instanceParameterCount": len(instance_params),
    }

    payload = {
        "status": status,
        "healthy": status == "ok",
        "instance": input_data.get("Instance", ""),
        "environment": input_data.get("Environment", ""),
        "cluster": cluster_info,
        "parameterGroups": parameter_groups,
        "instances": detailed_instances,
        "storage": storage_info,
        "backup": backup_info,
        "summary": summary,
        "issues": issues,
        "timestamp": timestamp,
    }

    return {
        "project": input_data.get("Project", ""),
        "env": input_data.get("Env", ""),
        "category": "infra",
        "check_type": "rds",
        "payload": payload,
        "updated_by": "step-function:infra-rds-checker",
    }
