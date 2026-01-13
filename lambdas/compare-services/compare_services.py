"""
Compare Services Lambda
========================
Compares K8s services state between Legacy and New Horizon environments.
Generates a detailed comparison report for migration validation.

Called by Step Function k8s-services-compare after fetching both states.

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

# Expected differences between Legacy and NH
EXPECTED_DIFFERENCES = {
    # Legacy uses NodePort, NH uses LoadBalancer with Ingress
    "type_nodeport_to_loadbalancer": "NH uses ALB Ingress instead of NodePort",
    # NH may have additional services
    "nh_only_redis": "Redis added in NH for caching",
}


def extract_payload_from_dynamo(dynamo_item: dict) -> Optional[dict]:
    """Extract payload from DynamoDB item structure."""
    if not dynamo_item:
        return None

    # Handle parallel branch result format (LegacyState/NHState nested inside)
    if "LegacyState" in dynamo_item:
        dynamo_item = dynamo_item["LegacyState"]
    elif "NHState" in dynamo_item:
        dynamo_item = dynamo_item["NHState"]

    # Handle nested DynamoDB format (with type descriptors)
    item = dynamo_item.get("item") or dynamo_item.get("itemData") or dynamo_item.get("found") or dynamo_item

    if not item:
        return None

    # If it's already a dict with 'payload' key
    if "payload" in item and isinstance(item["payload"], dict):
        return item["payload"]

    # If it's DynamoDB format with type descriptors
    if "payload" in item and isinstance(item["payload"], dict) and "M" in item["payload"]:
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
        return {k: deserialize_dynamo_value(v) for k, v in value.items()}


def compare_service_counts(legacy: dict, nh: dict) -> dict:
    """Compare service counts between environments."""
    legacy_summary = legacy.get("summary", {})
    nh_summary = nh.get("summary", {})

    legacy_total = legacy_summary.get("total", 0)
    nh_total = nh_summary.get("total", 0)

    return {
        "legacy": legacy_total,
        "nh": nh_total,
        "status": "synced" if legacy_total == nh_total else "differs",
        "difference": nh_total - legacy_total,
    }


def compare_service_types(legacy: dict, nh: dict) -> dict:
    """Compare service type distribution between environments."""
    legacy_summary = legacy.get("summary", {})
    nh_summary = nh.get("summary", {})

    legacy_types = legacy_summary.get("byType", {})
    nh_types = nh_summary.get("byType", {})

    # Get all unique types
    all_types = set(legacy_types.keys()) | set(nh_types.keys())

    type_comparison = {}
    for svc_type in all_types:
        legacy_count = legacy_types.get(svc_type, 0)
        nh_count = nh_types.get(svc_type, 0)
        type_comparison[svc_type] = {
            "legacy": legacy_count,
            "nh": nh_count,
            "status": "synced" if legacy_count == nh_count else "differs",
        }

    overall_status = "synced" if legacy_types == nh_types else "differs"

    return {
        "types": type_comparison,
        "status": overall_status,
    }


def compare_services_by_name(legacy_services: list, nh_services: list) -> dict:
    """Compare services by name and configuration."""
    legacy_by_name = {s["name"]: s for s in legacy_services}
    nh_by_name = {s["name"]: s for s in nh_services}

    legacy_names = set(legacy_by_name.keys())
    nh_names = set(nh_by_name.keys())

    # Services in both
    common_names = legacy_names & nh_names
    only_legacy = legacy_names - nh_names
    only_nh = nh_names - legacy_names

    same_services = []
    different_config = []

    for name in sorted(common_names):
        legacy_svc = legacy_by_name[name]
        nh_svc = nh_by_name[name]

        # Compare key attributes
        differences = []
        expected = True
        reasons = []

        # Type comparison
        if legacy_svc["type"] != nh_svc["type"]:
            legacy_type = legacy_svc["type"]
            nh_type = nh_svc["type"]
            differences.append(f"type: {legacy_type} -> {nh_type}")

            # Check if this is an expected difference
            if legacy_type == "NodePort" and nh_type in ("LoadBalancer", "ClusterIP"):
                reasons.append(EXPECTED_DIFFERENCES.get("type_nodeport_to_loadbalancer", "Expected migration change"))
            else:
                expected = False

        # Port comparison
        legacy_ports = {(p["port"], p.get("targetPort")): p for p in legacy_svc.get("ports", [])}
        nh_ports = {(p["port"], p.get("targetPort")): p for p in nh_svc.get("ports", [])}

        if set(legacy_ports.keys()) != set(nh_ports.keys()):
            differences.append(f"ports differ")
            expected = False

        if differences:
            different_config.append({
                "service": name,
                "legacy": {
                    "type": legacy_svc["type"],
                    "ports": [{"port": p["port"], "targetPort": p.get("targetPort")} for p in legacy_svc.get("ports", [])],
                },
                "nh": {
                    "type": nh_svc["type"],
                    "ports": [{"port": p["port"], "targetPort": p.get("targetPort")} for p in nh_svc.get("ports", [])],
                },
                "expected": expected,
                "reason": "; ".join(reasons) if reasons else "Configuration differs",
            })
        else:
            same_services.append(name)

    return {
        "sameServices": same_services,
        "differentConfig": different_config,
        "onlyLegacy": sorted(list(only_legacy)),
        "onlyNH": sorted(list(only_nh)),
    }


def compare_endpoint_health(legacy: dict, nh: dict) -> dict:
    """Compare endpoint health between environments."""
    legacy_services = legacy.get("services", [])
    nh_services = nh.get("services", [])

    legacy_ready = sum(s.get("endpoints", {}).get("ready", 0) for s in legacy_services)
    legacy_not_ready = sum(s.get("endpoints", {}).get("notReady", 0) for s in legacy_services)

    nh_ready = sum(s.get("endpoints", {}).get("ready", 0) for s in nh_services)
    nh_not_ready = sum(s.get("endpoints", {}).get("notReady", 0) for s in nh_services)

    # Determine status
    if legacy_ready == nh_ready and legacy_not_ready == nh_not_ready:
        status = "synced"
    else:
        status = "differs"

    return {
        "legacy": {"totalReady": legacy_ready, "totalNotReady": legacy_not_ready},
        "nh": {"totalReady": nh_ready, "totalNotReady": nh_not_ready},
        "status": status,
    }


def identify_issues(
    legacy: dict,
    nh: dict,
    service_count: dict,
    services_comparison: dict,
    endpoints_comparison: dict,
) -> list:
    """Identify comparison issues that need attention."""
    issues = []

    # Check service count difference
    if service_count["status"] == "differs":
        if service_count["difference"] < 0:
            issues.append({
                "severity": "warning",
                "issue": "ServiceCountMismatch",
                "message": f"NH has fewer services than Legacy ({service_count['nh']} vs {service_count['legacy']})",
            })
        else:
            issues.append({
                "severity": "info",
                "issue": "ServiceCountDifference",
                "message": f"NH has more services than Legacy ({service_count['nh']} vs {service_count['legacy']})",
            })

    # Check for services only in Legacy (may be missing in NH)
    only_legacy = services_comparison.get("onlyLegacy", [])
    if only_legacy:
        issues.append({
            "severity": "warning",
            "issue": "ServicesMissingInNH",
            "message": f"Services only in Legacy: {', '.join(only_legacy)}",
        })

    # Check for unexpected configuration differences
    different_config = services_comparison.get("differentConfig", [])
    unexpected_diff = [d for d in different_config if not d.get("expected", True)]
    if unexpected_diff:
        for diff in unexpected_diff:
            issues.append({
                "severity": "warning",
                "issue": "UnexpectedConfigDifference",
                "message": f"Unexpected difference in {diff['service']}: {diff['reason']}",
            })

    # Check endpoint health difference
    if endpoints_comparison["status"] == "differs":
        legacy_ready = endpoints_comparison["legacy"]["totalReady"]
        nh_ready = endpoints_comparison["nh"]["totalReady"]

        if nh_ready < legacy_ready:
            issues.append({
                "severity": "warning",
                "issue": "FewerReadyEndpointsInNH",
                "message": f"NH has fewer ready endpoints ({nh_ready} vs {legacy_ready})",
            })

    # Check if Legacy is healthier overall
    legacy_healthy = legacy.get("healthy", False)
    nh_healthy = nh.get("healthy", False)

    if legacy_healthy and not nh_healthy:
        issues.append({
            "severity": "warning",
            "issue": "NHNotHealthy",
            "message": "Legacy services are healthy but NH services are not",
        })

    return issues


def lambda_handler(event: dict, context: Any) -> dict:
    """
    Compare services state between Legacy and NH environments.

    Event structure (from Step Function):
    {
        "domain": "mro",
        "instance": "MI1",
        "environment": "ppd",
        "legacy_state": {
            "source": "legacy",
            "found": {...},
            "item": {...}
        },
        "nh_state": {
            "source": "nh",
            "found": {...},
            "item": {...}
        }
    }

    Returns comparison payload as per spec.
    """
    logger.info(f"Comparing services for {event.get('domain')}/{event.get('instance')}-{event.get('environment')}")

    domain = event.get("domain")
    instance = event.get("instance")
    environment = event.get("environment")

    legacy_state = event.get("legacy_state", {})
    nh_state = event.get("nh_state", {})

    # Extract payloads from DynamoDB items
    legacy_payload = extract_payload_from_dynamo(legacy_state)
    nh_payload = extract_payload_from_dynamo(nh_state)

    # Handle missing states
    if not legacy_payload and not nh_payload:
        return {
            "status": "error",
            "error": "NoData",
            "message": "Both Legacy and NH states are missing",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    if not legacy_payload:
        return {
            "status": "partial",
            "summary": {
                "serviceCount": "legacy_missing",
                "serviceTypes": "legacy_missing",
                "endpointHealth": "legacy_missing",
            },
            "message": "Legacy state is missing, cannot compare",
            "nh_only": {
                "status": nh_payload.get("status"),
                "healthy": nh_payload.get("healthy"),
                "summary": nh_payload.get("summary"),
            },
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    if not nh_payload:
        return {
            "status": "partial",
            "summary": {
                "serviceCount": "nh_missing",
                "serviceTypes": "nh_missing",
                "endpointHealth": "nh_missing",
            },
            "message": "NH state is missing, cannot compare",
            "legacy_only": {
                "status": legacy_payload.get("status"),
                "healthy": legacy_payload.get("healthy"),
                "summary": legacy_payload.get("summary"),
            },
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    # Perform comparisons
    legacy_services = legacy_payload.get("services", [])
    nh_services = nh_payload.get("services", [])

    service_count_comparison = compare_service_counts(legacy_payload, nh_payload)
    service_types_comparison = compare_service_types(legacy_payload, nh_payload)
    services_comparison = compare_services_by_name(legacy_services, nh_services)
    endpoints_comparison = compare_endpoint_health(legacy_payload, nh_payload)

    # Identify issues
    issues = identify_issues(
        legacy_payload,
        nh_payload,
        service_count_comparison,
        services_comparison,
        endpoints_comparison,
    )

    # Determine overall status
    overall_status = "synced"
    if (
        service_count_comparison["status"] == "differs"
        or service_types_comparison["status"] == "differs"
        or services_comparison.get("onlyLegacy")
        or services_comparison.get("differentConfig")
    ):
        overall_status = "differs"

    timestamp = datetime.now(timezone.utc).isoformat()

    result = {
        "status": overall_status,
        "summary": {
            "serviceCount": service_count_comparison["status"],
            "serviceTypes": service_types_comparison["status"],
            "endpointHealth": endpoints_comparison["status"],
        },
        "servicesComparison": services_comparison,
        "endpointsComparison": endpoints_comparison,
        "issues": issues,
        "legacyTimestamp": legacy_payload.get("timestamp"),
        "nhTimestamp": nh_payload.get("timestamp"),
        "timestamp": timestamp,
    }

    logger.info(f"Comparison complete: status={overall_status}, issues={len(issues)}")

    return result
