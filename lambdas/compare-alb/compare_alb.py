"""
Compare ALB Lambda
==================
Compares ALB state between Source and Destination environments.
Generates a detailed comparison report for migration validation.

Called by Step Function net-alb-compare after fetching both states.

Environment Variables:
- LOG_LEVEL: Logging level (default: INFO)
"""

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

    # Check for payload field
    payload = item.get("payload")
    if not payload:
        return None

    # If it's DynamoDB format with type descriptors ({"M": {...}})
    if isinstance(payload, dict) and "M" in payload:
        try:
            return deserialize_dynamo_value(payload)
        except Exception as e:
            logger.warning(f"Failed to deserialize DynamoDB payload: {e}")
            return None

    # Already deserialized
    if isinstance(payload, dict):
        return payload

    return None


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
        return {k: deserialize_dynamo_value(v) for k, v in value.items()}


def compare_alb_counts(source: dict, dest: dict) -> dict:
    """Compare ALB counts between environments."""
    source_summary = source.get("summary", {})
    dest_summary = dest.get("summary", {})

    source_total = source_summary.get("total", 0)
    dest_total = dest_summary.get("total", 0)

    return {
        "source": source_total,
        "destination": dest_total,
        "status": "synced" if source_total == dest_total else "differs",
        "difference": dest_total - source_total,
    }


def compare_target_health(source: dict, dest: dict) -> dict:
    """Compare target health between environments."""
    source_summary = source.get("summary", {})
    dest_summary = dest.get("summary", {})

    source_healthy = source_summary.get("healthyTargets", 0)
    source_total = source_summary.get("totalTargets", 0)
    dest_healthy = dest_summary.get("healthyTargets", 0)
    dest_total = dest_summary.get("totalTargets", 0)

    # Calculate health ratios
    source_ratio = source_healthy / source_total if source_total > 0 else 0
    dest_ratio = dest_healthy / dest_total if dest_total > 0 else 0

    # Status based on ratios being similar (within 10%)
    ratio_diff = abs(source_ratio - dest_ratio)
    if ratio_diff <= 0.1 and source_healthy == dest_healthy:
        status = "synced"
    elif ratio_diff <= 0.1:
        status = "similar"
    else:
        status = "differs"

    return {
        "source": {
            "healthy": source_healthy,
            "total": source_total,
            "ratio": round(source_ratio * 100, 1),
        },
        "destination": {
            "healthy": dest_healthy,
            "total": dest_total,
            "ratio": round(dest_ratio * 100, 1),
        },
        "status": status,
    }


def normalize_alb_name(name: str) -> str:
    """
    Normalize ALB name for comparison.

    AWS LB Controller generates names like: k8s-{namespace}-{service}-{hash}
    We want to match by namespace-service portion.
    """
    # Remove common hash suffixes (last segment if it looks like a hash)
    parts = name.split("-")
    if len(parts) > 3 and len(parts[-1]) >= 8:
        # Likely a hash suffix, remove it for comparison
        return "-".join(parts[:-1])
    return name


def compare_albs_by_name(source_albs: list, dest_albs: list, expected_diff: dict = None) -> dict:
    """
    Compare ALBs by name and configuration.

    Returns comparison of ALBs between environments.
    """
    expected_diff = expected_diff or {}

    # Build lookup by normalized name
    source_by_name = {}
    for alb in source_albs:
        name = alb.get("name", "")
        normalized = normalize_alb_name(name)
        source_by_name[normalized] = alb
        # Also index by exact name
        if name != normalized:
            source_by_name[name] = alb

    dest_by_name = {}
    for alb in dest_albs:
        name = alb.get("name", "")
        normalized = normalize_alb_name(name)
        dest_by_name[normalized] = alb
        if name != normalized:
            dest_by_name[name] = alb

    source_names = set(source_by_name.keys())
    dest_names = set(dest_by_name.keys())

    # Find matches and differences
    common_names = source_names & dest_names
    only_source = source_names - dest_names
    only_dest = dest_names - source_names

    same_albs = []
    different_config = []

    for name in sorted(common_names):
        source_alb = source_by_name[name]
        dest_alb = dest_by_name[name]

        differences = []
        expected = True
        reasons = []

        # Compare state
        if source_alb.get("state") != dest_alb.get("state"):
            differences.append(f"state: {source_alb.get('state')} -> {dest_alb.get('state')}")
            # State difference might be temporary
            if dest_alb.get("state") == "provisioning":
                reasons.append("ALB is provisioning")
            else:
                expected = False

        # Compare scheme (internal vs internet-facing)
        if source_alb.get("scheme") != dest_alb.get("scheme"):
            differences.append(f"scheme: {source_alb.get('scheme')} -> {dest_alb.get('scheme')}")
            expected = False

        # Compare target group count
        source_tg_count = len(source_alb.get("targetGroups", []))
        dest_tg_count = len(dest_alb.get("targetGroups", []))
        if source_tg_count != dest_tg_count:
            differences.append(f"targetGroups: {source_tg_count} -> {dest_tg_count}")
            expected = False

        # Compare listener count
        source_listener_count = len(source_alb.get("listeners", []))
        dest_listener_count = len(dest_alb.get("listeners", []))
        if source_listener_count != dest_listener_count:
            differences.append(f"listeners: {source_listener_count} -> {dest_listener_count}")
            expected = False

        # Compare health
        source_healthy = source_alb.get("healthyTargets", 0)
        dest_healthy = dest_alb.get("healthyTargets", 0)
        if source_healthy != dest_healthy:
            differences.append(f"healthyTargets: {source_healthy} -> {dest_healthy}")
            # Health difference might be expected during migration
            if dest_healthy >= source_healthy:
                reasons.append("Destination has equal or more healthy targets")
                expected = True
            else:
                expected = False

        if differences:
            different_config.append({
                "alb": source_alb.get("name"),
                "source": {
                    "state": source_alb.get("state"),
                    "scheme": source_alb.get("scheme"),
                    "targetGroups": source_tg_count,
                    "listeners": source_listener_count,
                    "healthyTargets": source_healthy,
                },
                "destination": {
                    "state": dest_alb.get("state"),
                    "scheme": dest_alb.get("scheme"),
                    "targetGroups": dest_tg_count,
                    "listeners": dest_listener_count,
                    "healthyTargets": dest_healthy,
                },
                "differences": differences,
                "expected": expected,
                "reason": "; ".join(reasons) if reasons else "Configuration differs",
            })
        else:
            same_albs.append(source_alb.get("name"))

    # Filter out normalized duplicates from only_source and only_dest
    filtered_only_source = []
    for name in sorted(only_source):
        # Check if this is a normalized name that has an exact match
        alb = source_by_name[name]
        if alb.get("name") == name or normalize_alb_name(alb.get("name")) == name:
            filtered_only_source.append(alb.get("name"))

    filtered_only_dest = []
    for name in sorted(only_dest):
        alb = dest_by_name[name]
        if alb.get("name") == name or normalize_alb_name(alb.get("name")) == name:
            filtered_only_dest.append(alb.get("name"))

    # Deduplicate
    filtered_only_source = list(set(filtered_only_source))
    filtered_only_dest = list(set(filtered_only_dest))

    return {
        "sameALBs": same_albs,
        "differentConfig": different_config,
        "onlySource": sorted(filtered_only_source),
        "onlyDestination": sorted(filtered_only_dest),
    }


def compare_listener_config(source_albs: list, dest_albs: list) -> dict:
    """Compare listener configuration between environments."""
    source_listeners = []
    for alb in source_albs:
        for listener in alb.get("listeners", []):
            source_listeners.append({
                "alb": alb.get("name"),
                "protocol": listener.get("protocol"),
                "port": listener.get("port"),
                "sslPolicy": listener.get("sslPolicy"),
            })

    dest_listeners = []
    for alb in dest_albs:
        for listener in alb.get("listeners", []):
            dest_listeners.append({
                "alb": alb.get("name"),
                "protocol": listener.get("protocol"),
                "port": listener.get("port"),
                "sslPolicy": listener.get("sslPolicy"),
            })

    # Count by protocol
    source_by_protocol = {}
    for l in source_listeners:
        proto = l["protocol"]
        source_by_protocol[proto] = source_by_protocol.get(proto, 0) + 1

    dest_by_protocol = {}
    for l in dest_listeners:
        proto = l["protocol"]
        dest_by_protocol[proto] = dest_by_protocol.get(proto, 0) + 1

    # Determine status
    if source_by_protocol == dest_by_protocol:
        status = "synced"
    else:
        status = "differs"

    return {
        "source": {
            "total": len(source_listeners),
            "byProtocol": source_by_protocol,
        },
        "destination": {
            "total": len(dest_listeners),
            "byProtocol": dest_by_protocol,
        },
        "status": status,
    }


def identify_issues(
    source: dict,
    dest: dict,
    alb_count: dict,
    target_health: dict,
    albs_comparison: dict,
    expected_diff: dict = None,
) -> list:
    """Identify comparison issues that need attention."""
    issues = []
    expected_diff = expected_diff or {}

    # Check ALB count difference
    if alb_count["status"] == "differs":
        if alb_count["difference"] < 0:
            issues.append({
                "severity": "warning",
                "issue": "ALBCountMismatch",
                "message": f"Destination has fewer ALBs than Source ({alb_count['destination']} vs {alb_count['source']})",
            })
        else:
            issues.append({
                "severity": "info",
                "issue": "ALBCountDifference",
                "message": f"Destination has more ALBs than Source ({alb_count['destination']} vs {alb_count['source']})",
            })

    # Check for ALBs only in Source (missing in Destination)
    only_source = albs_comparison.get("onlySource", [])
    if only_source:
        issues.append({
            "severity": "warning",
            "issue": "ALBsMissingInDestination",
            "message": f"ALBs only in Source: {', '.join(only_source[:5])}" + (
                f" (+{len(only_source) - 5} more)" if len(only_source) > 5 else ""
            ),
        })

    # Check for unexpected configuration differences
    different_config = albs_comparison.get("differentConfig", [])
    unexpected_diff = [d for d in different_config if not d.get("expected", True)]
    if unexpected_diff:
        for diff in unexpected_diff[:3]:  # Limit to first 3
            issues.append({
                "severity": "warning",
                "issue": "UnexpectedConfigDifference",
                "message": f"Unexpected difference in {diff['alb']}: {', '.join(diff['differences'][:2])}",
            })
        if len(unexpected_diff) > 3:
            issues.append({
                "severity": "warning",
                "issue": "MoreConfigDifferences",
                "message": f"{len(unexpected_diff) - 3} more ALBs with unexpected differences",
            })

    # Check target health
    if target_health["status"] == "differs":
        source_ratio = target_health["source"]["ratio"]
        dest_ratio = target_health["destination"]["ratio"]
        if dest_ratio < source_ratio - 10:  # More than 10% less healthy
            issues.append({
                "severity": "warning",
                "issue": "LowerHealthInDestination",
                "message": f"Destination has lower target health ({dest_ratio}% vs {source_ratio}%)",
            })

    # Check if Source is healthier overall
    source_healthy = source.get("healthy", False)
    dest_healthy = dest.get("healthy", False)

    if source_healthy and not dest_healthy:
        issues.append({
            "severity": "warning",
            "issue": "DestinationNotHealthy",
            "message": "Source ALBs are healthy but Destination ALBs are not",
        })

    return issues


def lambda_handler(event: dict, context: Any) -> dict:
    """
    Compare ALB state between Source and Destination environments.

    Event structure (from Step Function):
    {
        "Project": "mro-mi2",
        "Env": "legacy-staging:nh-staging",
        "Instance": "MI2",
        "Environment": "staging",
        "SourceState": {...},
        "DestinationState": {...},
        "ExpectedDifferences": {}  # Optional
    }

    Returns comparison payload for DynamoDB storage.
    """
    logger.info(f"Comparing ALBs for {event.get('Project')}/{event.get('Instance')}-{event.get('Environment')}")

    project = event.get("Project")
    env = event.get("Env")
    instance = event.get("Instance")
    environment = event.get("Environment")
    expected_diff = event.get("ExpectedDifferences", {})

    source_state = event.get("SourceState", {})
    dest_state = event.get("DestinationState", {})

    # Extract payloads from DynamoDB items
    source_payload = extract_payload_from_dynamo(source_state)
    dest_payload = extract_payload_from_dynamo(dest_state)

    timestamp = datetime.now(timezone.utc).isoformat()

    # Handle missing states
    if not source_payload and not dest_payload:
        return {
            "statusCode": 200,
            "payload": {
                "status": "error",
                "instance": instance,
                "environment": environment,
                "summary": {
                    "albCount": "unknown",
                    "targetHealth": "unknown",
                    "listenerConfig": "unknown",
                },
                "issues": [{"severity": "critical", "issue": "NoData", "message": "Both Source and Destination states are missing"}],
                "timestamp": timestamp,
            },
        }

    if not source_payload:
        return {
            "statusCode": 200,
            "payload": {
                "status": "partial",
                "instance": instance,
                "environment": environment,
                "summary": {
                    "albCount": "source_missing",
                    "targetHealth": "source_missing",
                    "listenerConfig": "source_missing",
                },
                "message": "Source state is missing, cannot compare",
                "destinationOnly": {
                    "status": dest_payload.get("status"),
                    "healthy": dest_payload.get("healthy"),
                    "summary": dest_payload.get("summary"),
                },
                "issues": [{"severity": "warning", "issue": "SourceMissing", "message": "Source state not found"}],
                "timestamp": timestamp,
            },
        }

    if not dest_payload:
        return {
            "statusCode": 200,
            "payload": {
                "status": "partial",
                "instance": instance,
                "environment": environment,
                "summary": {
                    "albCount": "destination_missing",
                    "targetHealth": "destination_missing",
                    "listenerConfig": "destination_missing",
                },
                "message": "Destination state is missing, cannot compare",
                "sourceOnly": {
                    "status": source_payload.get("status"),
                    "healthy": source_payload.get("healthy"),
                    "summary": source_payload.get("summary"),
                },
                "issues": [{"severity": "warning", "issue": "DestinationMissing", "message": "Destination state not found"}],
                "timestamp": timestamp,
            },
        }

    # Perform comparisons
    source_albs = source_payload.get("loadBalancers", [])
    dest_albs = dest_payload.get("loadBalancers", [])

    alb_count_comparison = compare_alb_counts(source_payload, dest_payload)
    target_health_comparison = compare_target_health(source_payload, dest_payload)
    albs_comparison = compare_albs_by_name(source_albs, dest_albs, expected_diff)
    listener_comparison = compare_listener_config(source_albs, dest_albs)

    # Identify issues
    issues = identify_issues(
        source_payload,
        dest_payload,
        alb_count_comparison,
        target_health_comparison,
        albs_comparison,
        expected_diff,
    )

    # Determine overall status
    overall_status = "synced"
    if (
        alb_count_comparison["status"] == "differs"
        or albs_comparison.get("onlySource")
        or any(not d.get("expected", True) for d in albs_comparison.get("differentConfig", []))
    ):
        overall_status = "differs"
    elif target_health_comparison["status"] == "differs":
        overall_status = "differs"

    result = {
        "statusCode": 200,
        "payload": {
            "status": overall_status,
            "instance": instance,
            "environment": environment,
            "summary": {
                "albCount": alb_count_comparison["status"],
                "targetHealth": target_health_comparison["status"],
                "listenerConfig": listener_comparison["status"],
            },
            "albCountComparison": alb_count_comparison,
            "targetHealthComparison": target_health_comparison,
            "albsComparison": albs_comparison,
            "listenerComparison": listener_comparison,
            "issues": issues,
            "sourceTimestamp": source_payload.get("timestamp"),
            "destinationTimestamp": dest_payload.get("timestamp"),
            "timestamp": timestamp,
        },
    }

    logger.info(f"Comparison complete: status={overall_status}, issues={len(issues)}")

    return result
