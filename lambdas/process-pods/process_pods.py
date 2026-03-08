"""
Process Pods Lambda
====================
Parses Kubernetes pod data from eks:call response and generates
summary, issues detection, and structured pod information.

Called by Step Function after eks:call to /api/v1/namespaces/{ns}/pods

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
RESTART_WARNING_THRESHOLD = 3  # restarts in container
PENDING_WARNING_SECONDS = 300  # 5 minutes
PENDING_CRITICAL_SECONDS = 300  # 5 minutes
NOT_READY_CRITICAL_SECONDS = 120  # 2 minutes

# Critical waiting reasons
CRITICAL_WAITING_REASONS = {"CrashLoopBackOff", "CreateContainerConfigError", "ErrImageNeverPull"}
WARNING_WAITING_REASONS = {"ImagePullBackOff", "ContainerCreating", "PodInitializing"}


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


def parse_resources(container: dict) -> dict:
    """Extract resource requests and limits from container spec."""
    resources = container.get("resources", {})
    return {
        "requests": resources.get("requests", {}),
        "limits": resources.get("limits", {}),
    }


def get_container_state(container_status: dict) -> tuple[str, Optional[str]]:
    """Get container state and termination reason if applicable."""
    state = container_status.get("state", {})

    if "running" in state:
        return "running", None
    elif "waiting" in state:
        waiting = state["waiting"]
        return "waiting", waiting.get("reason")
    elif "terminated" in state:
        terminated = state["terminated"]
        return "terminated", terminated.get("reason")

    return "unknown", None


def get_last_termination_reason(container_status: dict) -> Optional[str]:
    """Get the reason for last container termination."""
    last_state = container_status.get("lastState", {})
    if "terminated" in last_state:
        return last_state["terminated"].get("reason")
    return None


def calculate_pending_duration(pod: dict) -> Optional[int]:
    """Calculate how long a pod has been in Pending state (seconds)."""
    phase = pod.get("status", {}).get("phase")
    if phase != "Pending":
        return None

    creation_timestamp = pod.get("metadata", {}).get("creationTimestamp")
    if not creation_timestamp:
        return None

    try:
        created = datetime.fromisoformat(creation_timestamp.replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        return int((now - created).total_seconds())
    except Exception:
        return None


def calculate_not_ready_duration(pod: dict) -> Optional[int]:
    """Calculate how long a pod has been not ready (seconds)."""
    conditions = pod.get("status", {}).get("conditions", [])

    for condition in conditions:
        if condition.get("type") == "Ready" and condition.get("status") == "False":
            last_transition = condition.get("lastTransitionTime")
            if last_transition:
                try:
                    transition_time = datetime.fromisoformat(last_transition.replace("Z", "+00:00"))
                    now = datetime.now(timezone.utc)
                    return int((now - transition_time).total_seconds())
                except Exception:
                    pass
    return None


def extract_image_version(image: str) -> str:
    """Extract version tag from container image."""
    # Pattern: repo/image:tag or image:tag
    match = re.search(r":([^:]+)$", image)
    return match.group(1) if match else "latest"


def process_pod(pod: dict) -> dict:
    """Process a single pod and extract relevant information."""
    metadata = pod.get("metadata", {})
    spec = pod.get("spec", {})
    status = pod.get("status", {})

    name = metadata.get("name", "unknown")
    phase = status.get("phase", "Unknown")

    # Extract conditions
    conditions_list = status.get("conditions", [])
    conditions = {c["type"]: c["status"] == "True" for c in conditions_list}

    # Process containers
    container_statuses = status.get("containerStatuses", [])
    containers_spec = spec.get("containers", [])

    # Build container spec map for resources
    container_spec_map = {c.get("name"): c for c in containers_spec}

    containers = []
    total_restarts = 0
    ready_containers = 0
    total_containers = len(container_statuses) if container_statuses else len(containers_spec)

    for cs in container_statuses:
        container_name = cs.get("name", "unknown")
        is_ready = cs.get("ready", False)
        restart_count = cs.get("restartCount", 0)
        total_restarts += restart_count

        if is_ready:
            ready_containers += 1

        state, waiting_reason = get_container_state(cs)
        last_termination = get_last_termination_reason(cs)

        # Get resources from spec
        container_spec = container_spec_map.get(container_name, {})
        resources = parse_resources(container_spec)

        containers.append({
            "name": container_name,
            "ready": is_ready,
            "restartCount": restart_count,
            "state": state,
            "waitingReason": waiting_reason,
            "image": cs.get("image", "unknown"),
            "imageVersion": extract_image_version(cs.get("image", "")),
            "lastTerminationReason": last_termination,
            "resources": resources,
        })

    # If no containerStatuses yet (pod initializing), use spec
    if not container_statuses and containers_spec:
        for c in containers_spec:
            containers.append({
                "name": c.get("name", "unknown"),
                "ready": False,
                "restartCount": 0,
                "state": "waiting",
                "waitingReason": "ContainerCreating",
                "image": c.get("image", "unknown"),
                "imageVersion": extract_image_version(c.get("image", "")),
                "lastTerminationReason": None,
                "resources": parse_resources(c),
            })

    return {
        "name": name,
        "namespace": metadata.get("namespace", "unknown"),
        "status": phase,
        "ready": f"{ready_containers}/{total_containers}",
        "readyCount": ready_containers,
        "totalContainers": total_containers,
        "restarts": total_restarts,
        "age": parse_age(metadata.get("creationTimestamp", "")),
        "node": spec.get("nodeName", "unscheduled"),
        "conditions": conditions,
        "containers": containers,
        "labels": metadata.get("labels", {}),
    }


def detect_issues(pods: list[dict]) -> list[dict]:
    """Detect issues in pods and return list of issues."""
    issues = []

    for pod in pods:
        pod_name = pod["name"]

        # Check for Failed phase
        if pod["status"] == "Failed":
            issues.append({
                "pod": pod_name,
                "severity": "critical",
                "issue": "PodFailed",
                "message": f"Pod is in Failed state",
            })

        # Check container issues
        for container in pod.get("containers", []):
            waiting_reason = container.get("waitingReason")

            if waiting_reason in CRITICAL_WAITING_REASONS:
                issues.append({
                    "pod": pod_name,
                    "container": container["name"],
                    "severity": "critical",
                    "issue": waiting_reason,
                    "message": f"Container {container['name']} is in {waiting_reason}",
                })
            elif waiting_reason in WARNING_WAITING_REASONS:
                issues.append({
                    "pod": pod_name,
                    "container": container["name"],
                    "severity": "warning",
                    "issue": waiting_reason,
                    "message": f"Container {container['name']} is in {waiting_reason}",
                })

            # Check restart count
            if container.get("restartCount", 0) >= RESTART_WARNING_THRESHOLD:
                issues.append({
                    "pod": pod_name,
                    "container": container["name"],
                    "severity": "warning",
                    "issue": "HighRestarts",
                    "message": f"Container {container['name']} has {container['restartCount']} restarts",
                })

            # Check last termination reason (indicates previous crash)
            last_term = container.get("lastTerminationReason")
            if last_term and last_term not in ("Completed",):
                issues.append({
                    "pod": pod_name,
                    "container": container["name"],
                    "severity": "warning",
                    "issue": "PreviousTermination",
                    "message": f"Container {container['name']} was previously terminated: {last_term}",
                })

        # Check Ready condition
        if not pod.get("conditions", {}).get("Ready", True):
            if pod["status"] == "Running":
                issues.append({
                    "pod": pod_name,
                    "severity": "warning",
                    "issue": "NotReady",
                    "message": f"Pod is Running but not Ready ({pod['ready']})",
                })

    return issues


def determine_status(summary: dict, issues: list[dict], raw_pods: list[dict]) -> str:
    """Determine overall status based on summary and issues."""
    # Critical conditions
    critical_issues = [i for i in issues if i.get("severity") == "critical"]
    if critical_issues:
        return "critical"

    if summary["failed"] > 0:
        return "critical"

    # Check pending pods duration
    for pod in raw_pods:
        pending_duration = calculate_pending_duration(pod)
        if pending_duration and pending_duration > PENDING_CRITICAL_SECONDS:
            return "critical"

        not_ready_duration = calculate_not_ready_duration(pod)
        if not_ready_duration and not_ready_duration > NOT_READY_CRITICAL_SECONDS:
            return "critical"

    # No running pods is critical
    if summary["total"] > 0 and summary["running"] == 0:
        return "critical"

    # Warning conditions
    warning_issues = [i for i in issues if i.get("severity") == "warning"]
    if warning_issues:
        return "warning"

    if summary["pending"] > 0:
        return "warning"

    if summary["notReady"] > 0:
        return "warning"

    # Check expected count
    if summary.get("expectedCount"):
        if summary["actualCount"] < summary["expectedCount"]:
            return "warning"

    return "ok"


def lambda_handler(event: dict, context: Any) -> dict:
    """
    Process pods data from eks:call response.

    Event structure (from Step Function):
    {
        "pods": [<raw pod objects from K8s API>],
        "expected_pod_count": 3,  # optional
        "namespace": "hybris",
        "source": "legacy" | "nh",
        "cluster_name": "rubix-nonprod",
        "domain": "mro",
        "target": "mi1-ppd-legacy"
    }

    Returns:
    {
        "status": "ok" | "warning" | "critical",
        "source": "legacy" | "nh",
        "summary": {...},
        "pods": [...],
        "issues": [...],
        "healthy": true | false,
        "timestamp": "ISO8601"
    }
    """
    logger.info(f"Processing {len(event.get('pods', []))} pods")

    raw_pods = event.get("pods", [])
    expected_count = event.get("expected_pod_count")
    source = event.get("source", "unknown")
    namespace = event.get("namespace", "unknown")

    # Initialize summary
    summary = {
        "total": 0,
        "running": 0,
        "pending": 0,
        "failed": 0,
        "succeeded": 0,
        "unknown": 0,
        "ready": 0,
        "notReady": 0,
    }

    # Process each pod
    processed_pods = []
    for pod in raw_pods:
        processed = process_pod(pod)
        processed_pods.append(processed)

        # Update summary
        summary["total"] += 1
        phase = processed["status"]

        if phase == "Running":
            summary["running"] += 1
        elif phase == "Pending":
            summary["pending"] += 1
        elif phase == "Failed":
            summary["failed"] += 1
        elif phase == "Succeeded":
            summary["succeeded"] += 1
        else:
            summary["unknown"] += 1

        # Check readiness
        if processed["conditions"].get("Ready", False):
            summary["ready"] += 1
        else:
            summary["notReady"] += 1

    # Add expected count if provided
    if expected_count is not None:
        summary["expectedCount"] = expected_count
        summary["actualCount"] = summary["total"]

    # Detect issues
    issues = detect_issues(processed_pods)

    # Determine overall status
    status = determine_status(summary, issues, raw_pods)

    # Build response
    timestamp = datetime.now(timezone.utc).isoformat()

    result = {
        "status": status,
        "source": source,
        "namespace": namespace,
        "summary": summary,
        "pods": processed_pods,
        "issues": issues,
        "healthy": status == "ok",
        "timestamp": timestamp,
    }

    logger.info(f"Pod processing complete: status={status}, total={summary['total']}, "
                f"running={summary['running']}, ready={summary['ready']}, issues={len(issues)}")

    return result
