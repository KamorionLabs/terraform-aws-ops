"""
Infrastructure Checker Lambda
=============================
Checks the status of core infrastructure components:
- RDS/Aurora clusters
- EFS file systems
- EKS cluster availability

Can be used for:
- Migration readiness (mro domain)
- Ongoing health monitoring (webshop/platform domain)

Environment Variables:
- STATE_TABLE_NAME: DynamoDB table name for state storage
- LOG_LEVEL: Logging level (default: INFO)
"""

import json
import logging
import os
import sys

import boto3
from botocore.exceptions import ClientError

# Add shared modules to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "shared"))
from base_checker import BaseChecker

logger = logging.getLogger(__name__)
logger.setLevel(os.environ.get("LOG_LEVEL", "INFO"))


def check_rds_cluster(cluster_identifier: str, region: str = None) -> dict:
    """Check RDS/Aurora cluster status."""
    rds = boto3.client("rds", region_name=region)

    try:
        response = rds.describe_db_clusters(DBClusterIdentifier=cluster_identifier)

        if not response.get("DBClusters"):
            return {
                "status": "not_found",
                "cluster_identifier": cluster_identifier,
                "available": False,
            }

        cluster = response["DBClusters"][0]
        status = cluster.get("Status", "unknown")

        return {
            "status": status,
            "cluster_identifier": cluster_identifier,
            "available": status == "available",
            "endpoint": cluster.get("Endpoint"),
            "reader_endpoint": cluster.get("ReaderEndpoint"),
            "engine": cluster.get("Engine"),
            "engine_version": cluster.get("EngineVersion"),
            "multi_az": cluster.get("MultiAZ", False),
        }

    except ClientError as e:
        error_code = e.response.get("Error", {}).get("Code", "Unknown")
        if error_code == "DBClusterNotFoundFault":
            return {
                "status": "not_found",
                "cluster_identifier": cluster_identifier,
                "available": False,
            }
        logger.error(f"Error checking RDS cluster {cluster_identifier}: {e}")
        return {
            "status": "error",
            "cluster_identifier": cluster_identifier,
            "available": False,
            "error": str(e),
        }


def check_efs_filesystem(filesystem_id: str, region: str = None) -> dict:
    """Check EFS file system status."""
    efs = boto3.client("efs", region_name=region)

    try:
        response = efs.describe_file_systems(FileSystemId=filesystem_id)

        if not response.get("FileSystems"):
            return {
                "status": "not_found",
                "filesystem_id": filesystem_id,
                "available": False,
            }

        fs = response["FileSystems"][0]
        lifecycle_state = fs.get("LifeCycleState", "unknown")

        # Get mount targets
        mt_response = efs.describe_mount_targets(FileSystemId=filesystem_id)
        mount_targets = mt_response.get("MountTargets", [])
        available_mount_targets = [
            mt for mt in mount_targets if mt.get("LifeCycleState") == "available"
        ]

        return {
            "status": lifecycle_state,
            "filesystem_id": filesystem_id,
            "available": lifecycle_state == "available",
            "size_bytes": fs.get("SizeInBytes", {}).get("Value", 0),
            "performance_mode": fs.get("PerformanceMode"),
            "throughput_mode": fs.get("ThroughputMode"),
            "mount_targets_total": len(mount_targets),
            "mount_targets_available": len(available_mount_targets),
        }

    except ClientError as e:
        error_code = e.response.get("Error", {}).get("Code", "Unknown")
        if error_code == "FileSystemNotFound":
            return {
                "status": "not_found",
                "filesystem_id": filesystem_id,
                "available": False,
            }
        logger.error(f"Error checking EFS filesystem {filesystem_id}: {e}")
        return {
            "status": "error",
            "filesystem_id": filesystem_id,
            "available": False,
            "error": str(e),
        }


def check_eks_cluster(cluster_name: str, region: str = None) -> dict:
    """Check EKS cluster status."""
    eks = boto3.client("eks", region_name=region)

    try:
        response = eks.describe_cluster(name=cluster_name)

        if not response.get("cluster"):
            return {
                "status": "not_found",
                "cluster_name": cluster_name,
                "available": False,
            }

        cluster = response["cluster"]
        status = cluster.get("status", "unknown")

        return {
            "status": status,
            "cluster_name": cluster_name,
            "available": status == "ACTIVE",
            "endpoint": cluster.get("endpoint"),
            "version": cluster.get("version"),
            "platform_version": cluster.get("platformVersion"),
            "vpc_id": cluster.get("resourcesVpcConfig", {}).get("vpcId"),
        }

    except ClientError as e:
        error_code = e.response.get("Error", {}).get("Code", "Unknown")
        if error_code == "ResourceNotFoundException":
            return {
                "status": "not_found",
                "cluster_name": cluster_name,
                "available": False,
            }
        logger.error(f"Error checking EKS cluster {cluster_name}: {e}")
        return {
            "status": "error",
            "cluster_name": cluster_name,
            "available": False,
            "error": str(e),
        }


class InfraChecker(BaseChecker):
    """
    Infrastructure checker for RDS, EFS, and EKS.

    Config structure:
    {
        "region": "eu-central-1",  # optional
        "rds_cluster": "rds-dig-ppd-mro-mi2",
        "efs_filesystem": "fs-xxxxx",  # optional
        "eks_cluster": "eks-dig-ppd-webshop"
    }
    """

    domain = "mro"  # Default domain, can be overridden in event
    check_type = "infrastructure"

    def run_checks(self, config: dict) -> dict:
        region = config.get("region", os.environ.get("AWS_REGION"))
        checks = {}

        if rds_cluster := config.get("rds_cluster"):
            checks["rds"] = check_rds_cluster(rds_cluster, region)

        if efs_filesystem := config.get("efs_filesystem"):
            checks["efs"] = check_efs_filesystem(efs_filesystem, region)

        if eks_cluster := config.get("eks_cluster"):
            checks["eks"] = check_eks_cluster(eks_cluster, region)

        return checks


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
            "rds_cluster": "rds-dig-ppd-mro-mi2",
            "efs_filesystem": "fs-xxxxx",
            "eks_cluster": "eks-dig-ppd-webshop"
        },
        "save_state": true  # optional, defaults to true
    }
    """
    global _checker
    if _checker is None:
        _checker = InfraChecker()

    return _checker.execute(event, context)
