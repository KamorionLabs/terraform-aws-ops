"""
Compare Pods Lambda
====================
Compares K8s pods state between Source and New Horizon environments.
Generates a detailed comparison report for migration validation.

Called by Step Function k8s-pods-compare after fetching both states.

Environment Variables:
- LOG_LEVEL: Logging level (default: INFO)
"""

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)
logger.setLevel(os.environ.get("LOG_LEVEL", "INFO"))


def extract_payload_from_dynamo(dynamo_item: dict) -> Optional[dict]:
    """Extract payload from DynamoDB item structure."""
    if not dynamo_item:
        return None

    # Handle parallel branch result format (SourceState/DestinationState nested inside)
    if "SourceState" in dynamo_item:
        dynamo_item = dynamo_item["SourceState"]
    elif "DestinationState" in dynamo_item:
        dynamo_item = dynamo_item["DestinationState"]

    # Handle nested DynamoDB format (with type descriptors)
    item = dynamo_item.get("item") or dynamo_item.get("itemData") or dynamo_item.get("found") or dynamo_item

    if not item:
        return None

    # If it's already a dict with 'payload' key
    if "payload" in item and isinstance(item["payload"], dict):
        return item["payload"]

    # If it's DynamoDB format with type descriptors
    if "payload" in item and isinstance(item["payload"], dict) and "M" in item["payload"]:
        # Need to deserialize DynamoDB format - simplified extraction
        try:
            return deserialize_dynamo_value(item["payload"])
        except Exception:
            pass

    return item.get("payload")


def deserialize_dynamo_value(value: Any) -> Any:
    """Recursively deserialize DynamoDB typed values."""
    if not isinstance(value, dict):
        return value

    if "S" in value:
        return value["S"]
    elif "N" in value:
        num = value["N"]
        return float(num) if "." in num else int(num)
    elif "BOOL" in value:
        return value["BOOL"]
    elif "NULL" in value:
        return None
    elif "L" in value:
        return [deserialize_dynamo_value(item) for item in value["L"]]
    elif "M" in value:
        return {k: deserialize_dynamo_value(v) for k, v in value["M"].items()}
    else:
        # Try to deserialize as map if it has nested structure
        return {k: deserialize_dynamo_value(v) for k, v in value.items()}


def compare_pod_counts(source: dict, destination: dict) -> dict:
    """Compare pod counts between environments."""
    source_summary = source.get("summary", {})
    destination_summary = destination.get("summary", {})

    source_total = source_summary.get("total", 0)
    destination_total = destination_summary.get("total", 0)

    return {
        "source": source_total,
        "destination": destination_total,
        "status": "synced" if source_total == destination_total else "differs",
        "difference": destination_total - source_total,
    }


def compare_pod_status(source: dict, destination: dict) -> dict:
    """Compare pod status distribution between environments."""
    source_summary = source.get("summary", {})
    destination_summary = destination.get("summary", {})

    source_stats = {
        "running": source_summary.get("running", 0),
        "ready": source_summary.get("ready", 0),
        "issues": len(source.get("issues", [])),
    }

    destination_stats = {
        "running": destination_summary.get("running", 0),
        "ready": destination_summary.get("ready", 0),
        "issues": len(destination.get("issues", [])),
    }

    # Determine status
    status = "synced"
    if source_stats != destination_stats:
        status = "differs"
        # Warning if both have different issue counts
        if source_stats["issues"] != destination_stats["issues"]:
            status = "differs"

    return {
        "source": source_stats,
        "destination": destination_stats,
        "status": status,
    }


def compare_images(source_pods: list, destination_pods: list) -> dict:
    """Compare container images/versions between environments."""
    comparisons = {}

    # Build image maps by container name
    source_images = {}
    for pod in source_pods:
        for container in pod.get("containers", []):
            name = container.get("name")
            if name:
                source_images[name] = container.get("image", "unknown")

    destination_images = {}
    for pod in destination_pods:
        for container in pod.get("containers", []):
            name = container.get("name")
            if name:
                destination_images[name] = container.get("image", "unknown")

    # Compare all unique container names
    all_containers = set(source_images.keys()) | set(destination_images.keys())

    for container_name in all_containers:
        source_img = source_images.get(container_name)
        destination_img = destination_images.get(container_name)

        if source_img == destination_img:
            comparisons[container_name] = {
                "source": source_img,
                "destination": destination_img,
                "expected": True,
                "reason": "Identical images",
            }
        elif source_img and destination_img:
            # Both exist but differ
            comparisons[container_name] = {
                "source": source_img,
                "destination": destination_img,
                "expected": True,  # Destination may have newer versions
                "reason": "Destination has different version (expected during migration)",
            }
        elif source_img and not destination_img:
            comparisons[container_name] = {
                "source": source_img,
                "destination": None,
                "expected": False,
                "reason": "Container missing in Destination",
            }
        else:
            comparisons[container_name] = {
                "source": None,
                "destination": destination_img,
                "expected": False,
                "reason": "Container only in Destination (new addition?)",
            }

    return comparisons


def compare_resources(source_pods: list, destination_pods: list) -> dict:
    """Compare resource requests/limits between environments."""

    def sum_resources(pods: list) -> dict:
        total_cpu = 0
        total_memory = 0

        for pod in pods:
            for container in pod.get("containers", []):
                resources = container.get("resources", {})
                requests = resources.get("requests", {})

                # Parse CPU (e.g., "2", "500m")
                cpu = requests.get("cpu", "0")
                if isinstance(cpu, str):
                    if cpu.endswith("m"):
                        total_cpu += int(cpu[:-1])
                    else:
                        total_cpu += int(float(cpu) * 1000)

                # Parse Memory (e.g., "8Gi", "512Mi")
                memory = requests.get("memory", "0")
                if isinstance(memory, str):
                    if memory.endswith("Gi"):
                        total_memory += int(float(memory[:-2]) * 1024)
                    elif memory.endswith("Mi"):
                        total_memory += int(float(memory[:-2]))
                    elif memory.endswith("Ki"):
                        total_memory += int(float(memory[:-2]) / 1024)

        return {
            "totalCpuRequests": f"{total_cpu}m",
            "totalMemoryRequests": f"{total_memory}Mi",
        }

    source_resources = sum_resources(source_pods)
    destination_resources = sum_resources(destination_pods)

    return {
        "source": source_resources,
        "destination": destination_resources,
        "status": "synced" if source_resources == destination_resources else "differs",
    }


def identify_issues(source: dict, destination: dict, pod_count: dict, pod_status: dict) -> list:
    """Identify comparison issues that need attention."""
    issues = []

    # Check pod count difference
    if pod_count["status"] == "differs":
        if pod_count["difference"] < 0:
            issues.append({
                "severity": "warning",
                "issue": "PodCountMismatch",
                "message": f"Destination has fewer pods than Source ({pod_count["destination"]} vs {pod_count["source"]})",
            })
        else:
            issues.append({
                "severity": "info",
                "issue": "PodCountDifference",
                "message": f"Destination has more pods than Source ({pod_count["destination"]} vs {pod_count["source"]})",
            })

    # Check if Source is healthier
    source_healthy = source.get("healthy", False)
    destination_healthy = destination.get("healthy", False)

    if source_healthy and not destination_healthy:
        issues.append({
            "severity": "warning",
            "issue": "DestinationNotHealthy",
            "message": "Source is healthy but Destination is not",
        })
    elif not source_healthy and destination_healthy:
        issues.append({
            "severity": "info",
            "issue": "SourceNotHealthy",
            "message": "Destination is healthy but Source is not (good for migration)",
        })

    # Check ready ratio difference
    source_ready_ratio = (
        pod_status["source"]["ready"] / max(pod_status["source"]["running"], 1)
        if pod_status["source"]["running"] > 0 else 0
    )
    destination_ready_ratio = (
        pod_status["destination"]["ready"] / max(pod_status["destination"]["running"], 1)
        if pod_status["destination"]["running"] > 0 else 0
    )

    if abs(source_ready_ratio - destination_ready_ratio) > 0.1:  # More than 10% difference
        issues.append({
            "severity": "warning",
            "issue": "ReadyRatioDifference",
            "message": f"Ready ratio differs significantly (Source: {source_ready_ratio:.0%}, Destination: {destination_ready_ratio:.0%})",
        })

    return issues


def lambda_handler(event: dict, context: Any) -> dict:
    """
    Compare pods state between Source and Destination environments.

    Event structure (from Step Function):
    {
        "domain": "mro",
        "instance": "MI1",
        "environment": "ppd",
        "source_state": {
            "source": "source",
            "found": {...},  # DynamoDB item or empty
            "item": {...}
        },
        "destination_state": {
            "source": "destination",
            "found": {...},
            "item": {...}
        }
    }

    Returns comparison payload as per spec.
    """
    logger.info(f"Comparing pods for {event.get('domain')}/{event.get('instance')}-{event.get('environment')}")

    domain = event.get("domain")
    instance = event.get("instance")
    environment = event.get("environment")

    source_state = event.get("source_state", {})
    destination_state = event.get("destination_state", {})

    # Extract payloads from DynamoDB items
    source_payload = extract_payload_from_dynamo(source_state)
    destination_payload = extract_payload_from_dynamo(destination_state)

    # Handle missing states
    if not source_payload and not destination_payload:
        return {
            "status": "error",
            "error": "NoData",
            "message": "Both Source and Destination states are missing",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    if not source_payload:
        return {
            "status": "partial",
            "summary": {
                "podCount": "source_missing",
                "podStatus": "source_missing",
                "imageVersions": "source_missing",
            },
            "message": "Source state is missing, cannot compare",
            "destination_only": {
                "status": destination_payload.get("status"),
                "healthy": destination_payload.get("healthy"),
                "summary": destination_payload.get("summary"),
            },
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    if not destination_payload:
        return {
            "status": "partial",
            "summary": {
                "podCount": "destination_missing",
                "podStatus": "destination_missing",
                "imageVersions": "destination_missing",
            },
            "message": "Destination state is missing, cannot compare",
            "source_only": {
                "status": source_payload.get("status"),
                "healthy": source_payload.get("healthy"),
                "summary": source_payload.get("summary"),
            },
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    # Perform comparisons
    source_pods = source_payload.get("pods", [])
    destination_pods = destination_payload.get("pods", [])

    pod_count_comparison = compare_pod_counts(source_payload, destination_payload)
    pod_status_comparison = compare_pod_status(source_payload, destination_payload)
    image_comparison = compare_images(source_pods, destination_pods)
    resources_comparison = compare_resources(source_pods, destination_pods)

    # Identify issues
    issues = identify_issues(
        source_payload,
        destination_payload,
        pod_count_comparison,
        pod_status_comparison,
    )

    # Determine overall status
    overall_status = "synced"
    if (
        pod_count_comparison["status"] == "differs"
        or pod_status_comparison["status"] == "differs"
    ):
        overall_status = "differs"

    # Check for any warning-level issues
    has_warnings = any(i.get("severity") == "warning" for i in issues)

    timestamp = datetime.now(timezone.utc).isoformat()

    result = {
        "status": overall_status,
        "summary": {
            "podCount": pod_count_comparison["status"],
            "podStatus": pod_status_comparison["status"],
            "imageVersions": "synced" if all(
                c.get("expected", True) for c in image_comparison.values()
            ) else "differs",
        },
        "podCountComparison": pod_count_comparison,
        "podStatusComparison": pod_status_comparison,
        "imageComparison": image_comparison,
        "resourcesComparison": resources_comparison,
        "issues": issues,
        "sourceTimestamp": source_payload.get("timestamp"),
        "destinationTimestamp": destination_payload.get("timestamp"),
        "timestamp": timestamp,
    }

    logger.info(f"Comparison complete: status={overall_status}, issues={len(issues)}")

    return result
