"""
Process EFS Replication Lambda
==============================
Processes EFS replication data and determines status based on:
- Replication state (ENABLED, PAUSED, ERROR, etc.)
- Time since last sync
- Filesystem sizes comparison
- Mount target availability

Environment Variables:
- LOG_LEVEL: Logging level (default: INFO)
"""

import logging
import os
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)
logger.setLevel(os.environ.get("LOG_LEVEL", "INFO"))

# Status thresholds
DEFAULT_MAX_SYNC_DELAY_MINUTES = 60
SIZE_DIFF_WARNING_PERCENT = 5
SIZE_DIFF_CRITICAL_PERCENT = 10


def parse_timestamp(ts: str | None) -> datetime | None:
    """Parse ISO timestamp string to datetime."""
    if not ts:
        return None
    try:
        # Handle various timestamp formats
        if ts.endswith("Z"):
            ts = ts[:-1] + "+00:00"
        return datetime.fromisoformat(ts)
    except (ValueError, TypeError):
        return None


def calculate_sync_delay_minutes(last_sync_time: str | None, now: datetime) -> float | None:
    """Calculate minutes since last sync."""
    last_sync = parse_timestamp(last_sync_time)
    if not last_sync:
        return None
    delta = now - last_sync
    return delta.total_seconds() / 60


def calculate_size_difference(source_bytes: int, dest_bytes: int) -> dict:
    """Calculate size difference between source and destination."""
    source_gb = source_bytes / (1024**3)
    dest_gb = dest_bytes / (1024**3)
    diff_gb = abs(source_gb - dest_gb)

    if source_gb > 0:
        diff_percent = (diff_gb / source_gb) * 100
    else:
        diff_percent = 0 if dest_gb == 0 else 100

    return {
        "sourceSizeGB": round(source_gb, 2),
        "destinationSizeGB": round(dest_gb, 2),
        "differenceGB": round(diff_gb, 2),
        "differencePercent": round(diff_percent, 2),
    }


def determine_replication_status(
    replication_state: str,
    sync_delay_minutes: float | None,
    max_sync_delay: int,
    size_diff_percent: float,
    source_lifecycle: str,
    dest_lifecycle: str,
) -> tuple[str, list[str]]:
    """
    Determine overall status based on conditions.

    Returns:
        Tuple of (status, list of issues)
    """
    issues = []

    # Critical conditions
    if replication_state in ("ERROR", "DELETING"):
        issues.append(f"Replication state is {replication_state}")
        return "critical", issues

    if source_lifecycle in ("error", "deleting"):
        issues.append(f"Source filesystem in {source_lifecycle} state")
        return "critical", issues

    if dest_lifecycle in ("error", "deleting"):
        issues.append(f"Destination filesystem in {dest_lifecycle} state")
        return "critical", issues

    if sync_delay_minutes is not None and sync_delay_minutes > max_sync_delay:
        issues.append(
            f"Sync delay ({sync_delay_minutes:.0f} min) exceeds threshold ({max_sync_delay} min)"
        )
        return "critical", issues

    if size_diff_percent > SIZE_DIFF_CRITICAL_PERCENT:
        issues.append(f"Size difference ({size_diff_percent:.1f}%) exceeds critical threshold")
        return "critical", issues

    # Warning conditions
    if replication_state in ("PAUSING", "PAUSED"):
        issues.append(f"Replication is {replication_state}")
        return "warning", issues

    if source_lifecycle == "updating":
        issues.append("Source filesystem is updating")

    if dest_lifecycle == "updating":
        issues.append("Destination filesystem is updating")

    if sync_delay_minutes is not None:
        warning_threshold = max_sync_delay * 0.5
        if sync_delay_minutes > warning_threshold:
            issues.append(
                f"Sync delay ({sync_delay_minutes:.0f} min) approaching threshold"
            )

    if size_diff_percent > SIZE_DIFF_WARNING_PERCENT:
        issues.append(f"Size difference ({size_diff_percent:.1f}%) exceeds warning threshold")

    if issues:
        return "warning", issues

    # OK conditions
    if replication_state != "ENABLED":
        issues.append(f"Replication state is {replication_state} (expected ENABLED)")
        return "warning", issues

    return "ok", []


def process_replication_results(event: dict) -> dict:
    """Process EFS replication results and return structured payload."""
    input_data = event.get("Input", {})
    timestamp = event.get("Timestamp", datetime.now(timezone.utc).isoformat())
    replication_config = event.get("ReplicationConfig", {})
    filesystem_results = event.get("FilesystemResults", [])

    # Extract input parameters - support both old (Domain/Target) and new (Project/Env) format
    project = input_data.get("Project", input_data.get("Domain", "replication"))
    env = input_data.get("Env", input_data.get("Target", ""))
    instance = input_data.get("Instance", "")
    environment = input_data.get("Environment", "")
    source_fs_id = input_data.get("SourceFileSystemId")
    max_sync_delay = input_data.get("MaxSyncDelayMinutes", DEFAULT_MAX_SYNC_DELAY_MINUTES)

    # Parse current time
    now = parse_timestamp(timestamp) or datetime.now(timezone.utc)

    # Extract replication info
    replications = replication_config.get("Replications", [])
    if not replications:
        return {
            "project": project,
            "env": env,
            "category": "repl",
            "check_type": "efs-sync",
            "payload": {
                "status": "critical",
                "healthy": False,
                "instance": instance,
                "environment": environment,
                "summary": {
                    "sourceFileSystemId": source_fs_id,
                    "error": "No replication data found",
                },
                "issues": ["No replication configuration in results"],
                "timestamp": timestamp,
            },
            "updated_by": "step-function:repl-efs-sync-checker",
        }

    replication = replications[0]
    destinations = replication.get("Destinations", [])

    if not destinations:
        return {
            "project": project,
            "env": env,
            "category": "repl",
            "check_type": "efs-sync",
            "payload": {
                "status": "critical",
                "healthy": False,
                "instance": instance,
                "environment": environment,
                "summary": {
                    "sourceFileSystemId": source_fs_id,
                    "error": "No destination configured",
                },
                "issues": ["No replication destination found"],
                "timestamp": timestamp,
            },
            "updated_by": "step-function:repl-efs-sync-checker",
        }

    destination = destinations[0]
    dest_fs_id = destination.get("FileSystemId")
    replication_state = destination.get("Status", "UNKNOWN")
    last_sync_time = destination.get("LastReplicatedTimestamp")

    # Calculate sync delay
    sync_delay_minutes = calculate_sync_delay_minutes(last_sync_time, now)

    # Parse filesystem results
    source_fs = None
    dest_fs = None
    for fs in filesystem_results:
        if fs.get("type") == "source":
            source_fs = fs
        elif fs.get("type") == "destination":
            dest_fs = fs

    source_lifecycle = source_fs.get("lifeCycleState", "unknown") if source_fs else "error"
    dest_lifecycle = dest_fs.get("lifeCycleState", "unknown") if dest_fs else "error"

    # Calculate size comparison
    source_size = source_fs.get("sizeInBytes", 0) if source_fs else 0
    dest_size = dest_fs.get("sizeInBytes", 0) if dest_fs else 0
    size_comparison = calculate_size_difference(source_size, dest_size)

    # Determine status
    status, issues = determine_replication_status(
        replication_state=replication_state,
        sync_delay_minutes=sync_delay_minutes,
        max_sync_delay=max_sync_delay,
        size_diff_percent=size_comparison["differencePercent"],
        source_lifecycle=source_lifecycle,
        dest_lifecycle=dest_lifecycle,
    )

    # Build response
    payload = {
        "status": status,
        "instance": instance,
        "environment": environment,
        "summary": {
            "sourceFileSystemId": source_fs_id,
            "destinationFileSystemId": dest_fs_id,
            "replicationState": replication_state,
            "lastSyncTime": last_sync_time,
            "timeSinceLastSyncMinutes": round(sync_delay_minutes, 1) if sync_delay_minutes else None,
        },
        "replication": {
            "sourceFileSystemArn": replication.get("SourceFileSystemArn"),
            "sourceFileSystemRegion": replication.get("SourceFileSystemRegion"),
            "destinations": [
                {
                    "fileSystemId": dest_fs_id,
                    "region": destination.get("Region"),
                    "status": replication_state,
                    "lastReplicatedTimestamp": last_sync_time,
                }
            ],
        },
        "sourceFilesystem": {
            "fileSystemId": source_fs.get("fileSystemId") if source_fs else source_fs_id,
            "lifeCycleState": source_lifecycle,
            "sizeInBytes": source_size,
            "numberOfMountTargets": source_fs.get("numberOfMountTargets", 0) if source_fs else 0,
        },
        "destinationFilesystem": {
            "fileSystemId": dest_fs.get("fileSystemId") if dest_fs else dest_fs_id,
            "lifeCycleState": dest_lifecycle,
            "sizeInBytes": dest_size,
            "numberOfMountTargets": dest_fs.get("numberOfMountTargets", 0) if dest_fs else 0,
        },
        "sizeComparison": size_comparison,
        "healthy": status == "ok",
        "issues": issues,
        "timestamp": timestamp,
    }

    return {
        "project": project,
        "env": env,
        "category": "repl",
        "check_type": "efs-sync",
        "payload": payload,
        "updated_by": "step-function:repl-efs-sync-checker",
    }


def lambda_handler(event: dict, context: Any) -> dict:
    """
    Lambda entry point.

    Event structure (from Step Function):
    {
        "Input": {
            "Project": "mro-mi1",
            "Env": "nh-ppd",
            "Instance": "MI1",
            "Environment": "ppd",
            "SourceFileSystemId": "fs-xxxxxxxx",
            "MaxSyncDelayMinutes": 60
        },
        "Timestamp": "2025-01-09T10:00:00Z",
        "ReplicationConfig": {
            "Replications": [...]
        },
        "FilesystemResults": [
            {"type": "source", ...},
            {"type": "destination", ...}
        ]
    }

    Returns:
        Processed result ready for SaveState Lambda with new format:
        project/env/category/check_type
    """
    input_data = event.get("Input", {})
    logger.info(f"Processing EFS replication results for {input_data.get('Project')}/{input_data.get('Env')}")

    try:
        result = process_replication_results(event)
        logger.info(f"Processed result status: {result['payload']['status']}")
        return result
    except Exception as e:
        logger.exception(f"Error processing replication results: {e}")
        raise
