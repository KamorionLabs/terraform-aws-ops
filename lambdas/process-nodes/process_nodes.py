"""
Process Nodes Lambda
=====================
Parses Kubernetes node data from eks:call response and generates
summary, issues detection, and structured node information.

Optionally enriches with EC2 instance data (status checks, state).

Called by Step Function after eks:call to /api/v1/nodes

Environment Variables:
- LOG_LEVEL: Logging level (default: INFO)
"""

import json
import logging
import os
import re
from datetime import datetime, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)
logger.setLevel(os.environ.get("LOG_LEVEL", "INFO"))

# Thresholds for status determination
MEMORY_PRESSURE_CRITICAL = True
DISK_PRESSURE_CRITICAL = True
PID_PRESSURE_WARNING = True
POD_CAPACITY_WARNING_PERCENT = 80
POD_CAPACITY_CRITICAL_PERCENT = 95


def parse_age(creation_timestamp: str) -> str:
    """Convert creation timestamp to human-readable age."""
    try:
        created = datetime.fromisoformat(creation_timestamp.replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        delta = now - created

        days = delta.days
        hours = delta.seconds // 3600
        minutes = (delta.seconds % 3600) // 60

        if days > 0:
            return f"{days}d"
        elif hours > 0:
            return f"{hours}h"
        else:
            return f"{minutes}m"
    except Exception:
        return "unknown"


def parse_memory(memory_str: str) -> Optional[int]:
    """Parse memory string to bytes."""
    if not memory_str:
        return None
    try:
        if memory_str.endswith("Ki"):
            return int(memory_str[:-2]) * 1024
        elif memory_str.endswith("Mi"):
            return int(memory_str[:-2]) * 1024 * 1024
        elif memory_str.endswith("Gi"):
            return int(memory_str[:-2]) * 1024 * 1024 * 1024
        elif memory_str.endswith("Ti"):
            return int(memory_str[:-2]) * 1024 * 1024 * 1024 * 1024
        else:
            return int(memory_str)
    except Exception:
        return None


def format_memory(bytes_val: Optional[int]) -> Optional[str]:
    """Format bytes to human-readable string."""
    if bytes_val is None:
        return None
    if bytes_val >= 1024 * 1024 * 1024:
        return f"{bytes_val // (1024 * 1024 * 1024)}Gi"
    elif bytes_val >= 1024 * 1024:
        return f"{bytes_val // (1024 * 1024)}Mi"
    elif bytes_val >= 1024:
        return f"{bytes_val // 1024}Ki"
    return str(bytes_val)


def get_node_status(conditions: list) -> tuple[str, dict]:
    """
    Get node status from conditions.
    Returns (status, conditions_dict)
    """
    status = "Unknown"
    conditions_dict = {}

    for condition in conditions:
        cond_type = condition.get("type")
        cond_status = condition.get("status") == "True"
        conditions_dict[cond_type] = cond_status

        if cond_type == "Ready":
            status = "Ready" if cond_status else "NotReady"

    return status, conditions_dict


def extract_instance_id(provider_id: str) -> Optional[str]:
    """Extract EC2 instance ID from provider ID."""
    if not provider_id:
        return None
    # Format: aws:///eu-central-1a/i-0abc123def456
    match = re.search(r"i-[a-f0-9]+", provider_id)
    return match.group(0) if match else None


def process_ec2_data(ec2_data: dict) -> tuple[dict, dict]:
    """
    Process EC2 data from Step Function.

    Args:
        ec2_data: Dict containing EC2Data (instances) and EC2StatusData (status checks)

    Returns:
        Tuple of (instances_by_id, status_by_id)
    """
    instances_by_id = {}
    status_by_id = {}

    # Process EC2 instances
    ec2_instances = ec2_data.get("EC2Data", {}).get("Reservations", [])
    for reservation in ec2_instances:
        for instance in reservation.get("Instances", []):
            instance_id = instance.get("InstanceId")
            if instance_id:
                instances_by_id[instance_id] = {
                    "instance_id": instance_id,
                    "state": instance.get("State", {}).get("Name", "unknown"),
                    "instance_type": instance.get("InstanceType"),
                    "private_ip": instance.get("PrivateIpAddress"),
                    "availability_zone": instance.get("Placement", {}).get("AvailabilityZone"),
                    "launch_time": instance.get("LaunchTime"),
                }

    # Process EC2 status checks
    ec2_statuses = ec2_data.get("EC2StatusData", {}).get("InstanceStatuses", [])
    for status in ec2_statuses:
        instance_id = status.get("InstanceId")
        if instance_id:
            system_status = status.get("SystemStatus", {}).get("Status", "unknown")
            instance_status = status.get("InstanceStatus", {}).get("Status", "unknown")
            status_by_id[instance_id] = {
                "instance_id": instance_id,
                "system_status": system_status,
                "instance_status": instance_status,
                "status_ok": system_status == "ok" and instance_status == "ok",
            }

    return instances_by_id, status_by_id


def enrich_node_with_ec2(
    node: dict,
    instances_by_id: dict,
    status_by_id: dict,
) -> dict:
    """
    Enrich a processed node with EC2 data.

    Args:
        node: Processed node dict
        instances_by_id: EC2 instances indexed by instance ID
        status_by_id: EC2 status checks indexed by instance ID

    Returns:
        Node dict enriched with EC2 data
    """
    instance_id = node.get("instance_id")
    if not instance_id:
        node["ec2"] = None
        return node

    ec2_instance = instances_by_id.get(instance_id, {})
    ec2_status = status_by_id.get(instance_id, {})

    node["ec2"] = {
        "found": bool(ec2_instance),
        "state": ec2_instance.get("state"),
        "private_ip": ec2_instance.get("private_ip"),
        "system_status": ec2_status.get("system_status"),
        "instance_status": ec2_status.get("instance_status"),
        "status_ok": ec2_status.get("status_ok", False) if ec2_status else None,
    }

    return node


def detect_ec2_issues(nodes: list[dict]) -> list[dict]:
    """Detect EC2-related issues in nodes."""
    issues = []

    for node in nodes:
        node_name = node["name"]
        instance_id = node.get("instance_id")
        ec2 = node.get("ec2")

        if not ec2:
            continue

        # EC2 instance not found
        if instance_id and not ec2.get("found"):
            issues.append({
                "node": node_name,
                "severity": "warning",
                "issue": "EC2NotFound",
                "message": f"EC2 instance {instance_id} not found for node {node_name}",
            })
            continue

        # EC2 not running
        if ec2.get("state") and ec2["state"] != "running":
            issues.append({
                "node": node_name,
                "severity": "critical",
                "issue": "EC2NotRunning",
                "message": f"EC2 instance {instance_id} is {ec2['state']} (not running)",
            })

        # EC2 status check failed
        if ec2.get("status_ok") is False:
            system_status = ec2.get("system_status", "unknown")
            instance_status = ec2.get("instance_status", "unknown")
            issues.append({
                "node": node_name,
                "severity": "critical",
                "issue": "EC2StatusCheckFailed",
                "message": f"EC2 status check failed: system={system_status}, instance={instance_status}",
            })

    return issues


def process_node(node: dict) -> dict:
    """Process a single node and extract relevant information."""
    metadata = node.get("metadata", {})
    spec = node.get("spec", {})
    status = node.get("status", {})
    labels = metadata.get("labels", {})

    name = metadata.get("name", "unknown")

    # Extract node info from labels
    instance_type = labels.get("node.kubernetes.io/instance-type", "unknown")
    zone = labels.get("topology.kubernetes.io/zone")
    region = labels.get("topology.kubernetes.io/region")
    nodegroup = labels.get("eks.amazonaws.com/nodegroup", labels.get("karpenter.sh/nodepool", "unknown"))

    # Get status from conditions
    conditions_list = status.get("conditions", [])
    node_status, conditions = get_node_status(conditions_list)

    # Capacity and allocatable
    capacity = status.get("capacity", {})
    allocatable = status.get("allocatable", {})

    # Extract instance ID from provider ID
    provider_id = spec.get("providerID", "")
    instance_id = extract_instance_id(provider_id)

    return {
        "name": name,
        "status": node_status,
        "instance_type": instance_type,
        "zone": zone,
        "region": region,
        "nodegroup": nodegroup,
        "age": parse_age(metadata.get("creationTimestamp", "")),
        "capacity_cpu": capacity.get("cpu"),
        "capacity_memory": capacity.get("memory"),
        "capacity_pods": int(capacity.get("pods", 0)),
        "allocatable_cpu": allocatable.get("cpu"),
        "allocatable_memory": allocatable.get("memory"),
        "allocatable_pods": int(allocatable.get("pods", 0)),
        "instance_id": instance_id,
        "conditions": conditions,
        "labels": labels,
        "taints": spec.get("taints", []),
        "unschedulable": spec.get("unschedulable", False),
    }


def detect_issues(nodes: list[dict]) -> list[dict]:
    """Detect issues in nodes and return list of issues."""
    issues = []

    for node in nodes:
        node_name = node["name"]
        conditions = node.get("conditions", {})

        # Check NotReady
        if node["status"] == "NotReady":
            issues.append({
                "node": node_name,
                "severity": "critical",
                "issue": "NodeNotReady",
                "message": f"Node {node_name} is NotReady",
            })

        # Check MemoryPressure
        if conditions.get("MemoryPressure"):
            issues.append({
                "node": node_name,
                "severity": "critical",
                "issue": "MemoryPressure",
                "message": f"Node {node_name} has memory pressure",
            })

        # Check DiskPressure
        if conditions.get("DiskPressure"):
            issues.append({
                "node": node_name,
                "severity": "critical",
                "issue": "DiskPressure",
                "message": f"Node {node_name} has disk pressure",
            })

        # Check PIDPressure
        if conditions.get("PIDPressure"):
            issues.append({
                "node": node_name,
                "severity": "warning",
                "issue": "PIDPressure",
                "message": f"Node {node_name} has PID pressure",
            })

        # Check unschedulable (cordoned)
        if node.get("unschedulable"):
            issues.append({
                "node": node_name,
                "severity": "warning",
                "issue": "Unschedulable",
                "message": f"Node {node_name} is cordoned (unschedulable)",
            })

    return issues


def determine_status(summary: dict, issues: list[dict]) -> str:
    """Determine overall status based on summary and issues."""
    # Critical conditions
    critical_issues = [i for i in issues if i.get("severity") == "critical"]
    if critical_issues:
        return "critical"

    if summary["notReady"] > 0:
        return "critical"

    # No ready nodes is critical
    if summary["total"] > 0 and summary["ready"] == 0:
        return "critical"

    # Warning conditions
    warning_issues = [i for i in issues if i.get("severity") == "warning"]
    if warning_issues:
        return "warning"

    return "ok"


def lambda_handler(event: dict, context: Any) -> dict:
    """
    Process nodes data from eks:call response.

    Event structure (from Step Function):
    {
        "nodes": [<raw node objects from K8s API>],
        "cluster_name": "k8s-dig-stg-webshop",
        "cluster_version": "1.29",
        "ec2_data": {  // Optional - if IncludeEC2 was true
            "EC2Data": {"Reservations": [...]},
            "EC2StatusData": {"InstanceStatuses": [...]}
        }
    }

    Returns:
    {
        "status": "ok" | "warning" | "critical",
        "summary": {...},
        "nodes": [...],
        "issues": [...],
        "healthy": true | false,
        "timestamp": "ISO8601",
        "ec2_enriched": true | false
    }
    """
    logger.info(f"Processing {len(event.get('nodes', []))} nodes")

    raw_nodes = event.get("nodes", [])
    cluster_name = event.get("cluster_name", "unknown")
    cluster_version = event.get("cluster_version", "unknown")
    ec2_data = event.get("ec2_data")

    # Process EC2 data if provided
    instances_by_id = {}
    status_by_id = {}
    ec2_enriched = False

    if ec2_data:
        logger.info("EC2 data provided, enriching nodes")
        instances_by_id, status_by_id = process_ec2_data(ec2_data)
        ec2_enriched = True
        logger.info(f"Found {len(instances_by_id)} EC2 instances, {len(status_by_id)} status checks")

    # Initialize summary
    summary = {
        "total": 0,
        "ready": 0,
        "notReady": 0,
        "cordoned": 0,
        "byNodegroup": {},
        "byInstanceType": {},
        "byZone": {},
    }

    # Add EC2 summary if enriched
    if ec2_enriched:
        summary["ec2"] = {
            "instances_found": len(instances_by_id),
            "instances_running": sum(1 for i in instances_by_id.values() if i.get("state") == "running"),
            "status_checks_ok": sum(1 for s in status_by_id.values() if s.get("status_ok")),
        }

    # Process each node
    processed_nodes = []
    for node in raw_nodes:
        processed = process_node(node)

        # Enrich with EC2 data if available
        if ec2_enriched:
            processed = enrich_node_with_ec2(processed, instances_by_id, status_by_id)

        processed_nodes.append(processed)

        # Update summary
        summary["total"] += 1

        if processed["status"] == "Ready":
            summary["ready"] += 1
        else:
            summary["notReady"] += 1

        if processed.get("unschedulable"):
            summary["cordoned"] += 1

        # By nodegroup
        nodegroup = processed.get("nodegroup", "unknown")
        summary["byNodegroup"][nodegroup] = summary["byNodegroup"].get(nodegroup, 0) + 1

        # By instance type
        instance_type = processed.get("instance_type", "unknown")
        summary["byInstanceType"][instance_type] = summary["byInstanceType"].get(instance_type, 0) + 1

        # By zone
        zone = processed.get("zone", "unknown")
        if zone:
            summary["byZone"][zone] = summary["byZone"].get(zone, 0) + 1

    # Detect K8s issues
    issues = detect_issues(processed_nodes)

    # Detect EC2 issues if enriched
    if ec2_enriched:
        ec2_issues = detect_ec2_issues(processed_nodes)
        issues.extend(ec2_issues)

    # Determine overall status
    status = determine_status(summary, issues)

    # Build response
    timestamp = datetime.now(timezone.utc).isoformat()

    result = {
        "status": status,
        "cluster_name": cluster_name,
        "cluster_version": cluster_version,
        "summary": summary,
        "nodes": processed_nodes,
        "issues": issues,
        "healthy": status == "ok",
        "timestamp": timestamp,
        "ec2_enriched": ec2_enriched,
    }

    logger.info(f"Node processing complete: status={status}, total={summary['total']}, "
                f"ready={summary['ready']}, issues={len(issues)}, ec2_enriched={ec2_enriched}")

    return result
