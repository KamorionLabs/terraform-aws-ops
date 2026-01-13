"""
Compare Ingress Lambda
=======================
Compares Kubernetes Ingress states between Source and New Horizon environments.
Generates detailed comparison report with rule-by-rule analysis.

Called by Step Function after fetching states from DynamoDB.

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

# Expected differences that should not be flagged as issues
EXPECTED_DIFFERENCES = {
    # Destination uses separate services (front, bo, api) vs source single service
    "backend_separation": {
        "description": "Destination uses separate front/bo/api services",
        "patterns": [
            ("hybris:9001", "hybris-front:9001"),
            ("hybris:9001", "hybris-bo:9001"),
            ("hybris:9001", "hybris-api:9001"),
        ],
    },
    # Internal domain differences
    "internal_domain": {
        "description": "Destination uses different internal domain suffix",
        "patterns": [
            (".rubix-nonprod.internal", ".rubix.internal"),
            (".rubix-prod.internal", ".rubix.internal"),
        ],
    },
}


def normalize_host(host: str) -> str:
    """Normalize host for comparison (remove trailing dots, lowercase)."""
    if not host:
        return ""
    return host.lower().rstrip(".")


def normalize_path(path: str) -> str:
    """Normalize path for comparison (ensure leading slash, no trailing)."""
    if not path:
        return "/"
    path = "/" + path.lstrip("/")
    return path.rstrip("/") if path != "/" else path


def normalize_backend(backend: str) -> str:
    """Normalize backend for comparison."""
    if not backend:
        return ""
    return backend.lower()


def create_rule_key(rule: dict) -> str:
    """Create a unique key for a rule for comparison."""
    host = normalize_host(rule.get("host", "*"))
    path = normalize_path(rule.get("path", "/"))
    return f"{host}|{path}"


def is_expected_difference(source_value: str, destination_value: str, diff_type: str) -> tuple[bool, Optional[str]]:
    """Check if a difference is expected based on known patterns."""
    for diff_name, diff_config in EXPECTED_DIFFERENCES.items():
        if diff_type == "backend" and diff_name == "backend_separation":
            for source_pattern, destination_pattern in diff_config["patterns"]:
                if source_pattern in source_value and destination_pattern in destination_value:
                    return True, diff_config["description"]
        elif diff_type == "host" and diff_name == "internal_domain":
            for source_pattern, destination_pattern in diff_config["patterns"]:
                if source_pattern in source_value and destination_pattern in destination_value:
                    return True, diff_config["description"]

    return False, None


def compare_rules(source_rules: list[dict], destination_rules: list[dict]) -> dict:
    """Compare rules between Source and Destination."""
    # Index rules by normalized key
    source_by_key = {}
    for rule in source_rules:
        key = create_rule_key(rule)
        if key not in source_by_key:
            source_by_key[key] = rule

    destination_by_key = {}
    for rule in destination_rules:
        key = create_rule_key(rule)
        if key not in destination_by_key:
            destination_by_key[key] = rule

    source_keys = set(source_by_key.keys())
    destination_keys = set(destination_by_key.keys())

    # Find common, only-legacy, only-nh
    common_keys = source_keys & destination_keys
    only_source_keys = source_keys - destination_keys
    only_destination_keys = destination_keys - source_keys

    same_rules = []
    different_rules = []
    only_source = []
    only_destination = []

    # Process common rules
    for key in common_keys:
        source_rule = source_by_key[key]
        destination_rule = destination_by_key[key]

        source_backend = normalize_backend(source_rule.get("backend", ""))
        destination_backend = normalize_backend(destination_rule.get("backend", ""))

        if source_backend == destination_backend:
            same_rules.append({
                "host": source_rule.get("host"),
                "path": source_rule.get("path"),
            })
        else:
            expected, reason = is_expected_difference(source_backend, destination_backend, "backend")
            different_rules.append({
                "host": source_rule.get("host"),
                "path": source_rule.get("path"),
                "source": {"backend": source_rule.get("backend")},
                "destination": {"backend": destination_rule.get("backend")},
                "expected": expected,
                "reason": reason or "Backend differs",
            })

    # Process only-legacy rules
    for key in only_source_keys:
        rule = source_by_key[key]
        only_source.append({
            "host": rule.get("host"),
            "path": rule.get("path"),
            "backend": rule.get("backend"),
            "reason": "Rule exists only in Source",
        })

    # Process only-Destination rules
    for key in only_destination_keys:
        rule = destination_by_key[key]
        only_destination.append({
            "host": rule.get("host"),
            "path": rule.get("path"),
            "backend": rule.get("backend"),
            "reason": "New rule in Destination",
        })

    # Determine status
    if different_rules or only_source:
        # Check if all differences are expected
        all_expected = all(d.get("expected", False) for d in different_rules)
        if all_expected and not only_source:
            status = "synced"
        else:
            status = "differs"
    else:
        status = "synced"

    return {
        "status": status,
        "sameRules": same_rules,
        "differentRules": different_rules,
        "onlySource": only_source,
        "onlyDestination": only_destination,
    }


def compare_ingress_type(source_ingress: Optional[dict], destination_ingress: Optional[dict]) -> dict:
    """Compare a single ingress type between Source and Destination."""
    if source_ingress is None and destination_ingress is None:
        return {
            "status": "missing",
            "message": "Ingress not found in either environment",
        }

    if source_ingress is None:
        return {
            "status": "only_destination",
            "message": "Ingress exists only in Destination",
            "destination": {
                "name": destination_ingress.get("name"),
                "rules": destination_ingress.get("rules", []),
            },
        }

    if destination_ingress is None:
        return {
            "status": "only_source",
            "message": "Ingress exists only in Source",
            "source": {
                "name": source_ingress.get("name"),
                "rules": source_ingress.get("rules", []),
            },
        }

    # Both exist - compare rules
    source_rules = source_ingress.get("rules", [])
    destination_rules = destination_ingress.get("rules", [])

    return compare_rules(source_rules, destination_rules)


def compare_hosts(source_state: dict, destination_state: dict) -> dict:
    """Compare all hosts between Source and Destination."""
    source_hosts = set()
    destination_hosts = set()

    # Extract hosts from legacy
    source_ingresses = source_state.get("ingresses", {})
    for ingress in source_ingresses.values():
        if ingress:
            for rule in ingress.get("rules", []):
                host = rule.get("host")
                if host and host != "*":
                    source_hosts.add(normalize_host(host))

    # Extract hosts from Destination
    destination_ingresses = destination_state.get("ingresses", {})
    for ingress in destination_ingresses.values():
        if ingress:
            for rule in ingress.get("rules", []):
                host = rule.get("host")
                if host and host != "*":
                    destination_hosts.add(normalize_host(host))

    return {
        "source": sorted(list(source_hosts)),
        "destination": sorted(list(destination_hosts)),
        "onlySource": sorted(list(source_hosts - destination_hosts)),
        "onlyDestination": sorted(list(destination_hosts - source_hosts)),
        "common": sorted(list(source_hosts & destination_hosts)),
    }


def compare_sftp(source_sftp: Optional[dict], destination_sftp: Optional[dict]) -> dict:
    """Compare SFTP services between Source and Destination."""
    if source_sftp is None and destination_sftp is None:
        return {"status": "missing", "message": "SFTP service not found in either environment"}

    if source_sftp is None:
        return {"status": "only_destination", "message": "SFTP service exists only in Destination"}

    if destination_sftp is None:
        return {"status": "only_source", "message": "SFTP service exists only in Source"}

    # Both exist - compare basic properties
    source_ports = set(p.get("port") for p in source_sftp.get("ports", []))
    destination_ports = set(p.get("port") for p in destination_sftp.get("ports", []))

    if source_ports == destination_ports:
        return {"status": "synced", "ports": sorted(list(source_ports))}
    else:
        return {
            "status": "differs",
            "sourcePorts": sorted(list(source_ports)),
            "destinationPorts": sorted(list(destination_ports)),
        }


def compare_target_group_bindings(
    source_tgbs: list[dict], destination_tgbs: list[dict]
) -> dict:
    """Compare TargetGroupBindings between Source and Destination."""
    # Index TGBs by type for comparison
    source_by_type = {}
    for tgb in source_tgbs:
        tgb_type = tgb.get("type", "other")
        if tgb_type not in source_by_type:
            source_by_type[tgb_type] = []
        source_by_type[tgb_type].append(tgb)

    destination_by_type = {}
    for tgb in destination_tgbs:
        tgb_type = tgb.get("type", "other")
        if tgb_type not in destination_by_type:
            destination_by_type[tgb_type] = []
        destination_by_type[tgb_type].append(tgb)

    source_types = set(source_by_type.keys())
    destination_types = set(destination_by_type.keys())

    common_types = source_types & destination_types
    only_source_types = source_types - destination_types
    only_destination_types = destination_types - source_types

    comparison = {
        "status": "synced",
        "summary": {
            "sourceCount": len(source_tgbs),
            "destinationCount": len(destination_tgbs),
            "commonTypes": sorted(list(common_types)),
            "onlySourceTypes": sorted(list(only_source_types)),
            "onlyDestinationTypes": sorted(list(only_destination_types)),
        },
        "byType": {},
    }

    # Compare common types
    for tgb_type in common_types:
        source_list = source_by_type[tgb_type]
        destination_list = destination_by_type[tgb_type]

        # Compare by service reference
        source_services = {t.get("serviceRef", {}).get("name") for t in source_list}
        destination_services = {t.get("serviceRef", {}).get("name") for t in destination_list}

        if source_services == destination_services:
            comparison["byType"][tgb_type] = {
                "status": "synced",
                "services": sorted(list(source_services)),
            }
        else:
            comparison["byType"][tgb_type] = {
                "status": "differs",
                "sourceServices": sorted(list(source_services)),
                "destinationServices": sorted(list(destination_services)),
            }
            comparison["status"] = "differs"

    # Record only-source types
    for tgb_type in only_source_types:
        comparison["byType"][tgb_type] = {
            "status": "only_source",
            "source": [t.get("name") for t in source_by_type[tgb_type]],
        }
        comparison["status"] = "differs"

    # Record only-destination types (expected for NH which has more TGBs)
    for tgb_type in only_destination_types:
        comparison["byType"][tgb_type] = {
            "status": "only_destination",
            "destination": [t.get("name") for t in destination_by_type[tgb_type]],
            "expected": True,  # NH typically has more TGBs
            "reason": "Destination has additional TargetGroupBindings",
        }
        # Don't mark as differs if only destination has more (expected)

    return comparison


def compare_alb_health(source_summary: dict, destination_summary: dict) -> dict:
    """
    Compare ALB health data between Source and Destination.

    This compares the enriched ALB target health data when IncludeALBHealth=true
    was used during the ingress check.
    """
    source_alb = source_summary.get("albHealth", {})
    destination_alb = destination_summary.get("albHealth", {})

    # Check if ALB health data is available
    if not source_alb and not destination_alb:
        return {
            "status": "not_enriched",
            "message": "ALB health data not available. Run ingress checker with IncludeALBHealth=true",
        }

    if not source_alb:
        return {
            "status": "source_not_enriched",
            "destination": destination_alb,
        }

    if not destination_alb:
        return {
            "status": "destination_not_enriched",
            "source": source_alb,
        }

    # Both have ALB health data - compare
    source_ratio = source_alb.get("healthRatio", 0)
    destination_ratio = destination_alb.get("healthRatio", 0)

    source_healthy = source_alb.get("healthyTargets", 0)
    source_total = source_alb.get("totalTargets", 0)
    destination_healthy = destination_alb.get("healthyTargets", 0)
    destination_total = destination_alb.get("totalTargets", 0)

    # Determine status
    if source_ratio == destination_ratio == 100:
        status = "synced"
    elif abs(source_ratio - destination_ratio) < 5:
        status = "similar"
    elif destination_ratio < source_ratio:
        status = "destination_degraded"
    elif destination_ratio > source_ratio:
        status = "destination_better"
    else:
        status = "differs"

    return {
        "status": status,
        "source": {
            "healthy": source_healthy,
            "total": source_total,
            "healthRatio": source_ratio,
        },
        "destination": {
            "healthy": destination_healthy,
            "total": destination_total,
            "healthRatio": destination_ratio,
        },
        "comparison": {
            "healthyDiff": destination_healthy - source_healthy,
            "totalDiff": destination_total - source_total,
            "ratioDiff": round(destination_ratio - source_ratio, 1),
        },
    }


def compare_tgb_health_details(
    source_tgbs: list[dict], destination_tgbs: list[dict]
) -> dict:
    """
    Compare individual TGB ALB health details.

    Returns detailed comparison of each TGB's target health when enriched.
    """
    # Check if any TGBs have albHealth data
    source_has_health = any(t.get("albHealth") for t in source_tgbs)
    destination_has_health = any(t.get("albHealth") for t in destination_tgbs)

    if not source_has_health and not destination_has_health:
        return {"status": "not_enriched"}

    # Index TGBs by type for comparison
    def index_by_type(tgbs):
        by_type = {}
        for tgb in tgbs:
            tgb_type = tgb.get("type", "other")
            if tgb_type not in by_type:
                by_type[tgb_type] = []
            by_type[tgb_type].append(tgb)
        return by_type

    source_by_type = index_by_type(source_tgbs)
    destination_by_type = index_by_type(destination_tgbs)

    all_types = set(source_by_type.keys()) | set(destination_by_type.keys())

    health_comparison = {
        "status": "synced",
        "byType": {},
    }

    for tgb_type in sorted(all_types):
        source_list = source_by_type.get(tgb_type, [])
        destination_list = destination_by_type.get(tgb_type, [])

        # Aggregate health for this type
        source_healthy = sum(
            (t.get("albHealth") or {}).get("healthy", 0) for t in source_list
        )
        source_total = sum(
            (t.get("albHealth") or {}).get("total", 0) for t in source_list
        )
        destination_healthy = sum(
            (t.get("albHealth") or {}).get("healthy", 0) for t in destination_list
        )
        destination_total = sum(
            (t.get("albHealth") or {}).get("total", 0) for t in destination_list
        )

        source_ratio = (source_healthy / source_total * 100) if source_total > 0 else 0
        destination_ratio = (destination_healthy / destination_total * 100) if destination_total > 0 else 0

        type_status = "synced"
        if source_total == 0 and destination_total == 0:
            type_status = "no_targets"
        elif abs(source_ratio - destination_ratio) >= 10:
            type_status = "differs"
            health_comparison["status"] = "differs"
        elif destination_ratio < source_ratio - 5:
            type_status = "destination_degraded"
            health_comparison["status"] = "differs"

        health_comparison["byType"][tgb_type] = {
            "status": type_status,
            "source": {
                "healthy": source_healthy,
                "total": source_total,
                "ratio": round(source_ratio, 1),
            },
            "destination": {
                "healthy": destination_healthy,
                "total": destination_total,
                "ratio": round(destination_ratio, 1),
            },
        }

    return health_comparison


def extract_state_payload(raw_state: dict) -> dict:
    """Extract payload from DynamoDB state format."""
    if not raw_state:
        return {}

    # Handle parallel branch result format (SourceState/DestinationState nested inside)
    if "SourceState" in raw_state:
        raw_state = raw_state["SourceState"]
    elif "DestinationState" in raw_state:
        raw_state = raw_state["DestinationState"]

    # Handle DynamoDB item format with type markers
    item = raw_state.get("item") or raw_state.get("itemData") or raw_state.get("found") or raw_state

    if item and "payload" in item:
        payload_raw = item["payload"]
        if isinstance(payload_raw, dict) and "S" in payload_raw:
            # DynamoDB string format - parse JSON
            return json.loads(payload_raw["S"])
        elif isinstance(payload_raw, dict) and "M" in payload_raw:
            # DynamoDB map format - needs conversion
            return convert_dynamodb_to_json(payload_raw["M"])
        else:
            return payload_raw

    # Try direct payload access
    if "payload" in raw_state:
        return raw_state["payload"]

    return raw_state


def convert_dynamodb_to_json(dynamodb_item: dict) -> dict:
    """Convert DynamoDB AttributeValue format to regular JSON."""
    result = {}
    for key, value in dynamodb_item.items():
        if "S" in value:
            result[key] = value["S"]
        elif "N" in value:
            result[key] = float(value["N"]) if "." in value["N"] else int(value["N"])
        elif "BOOL" in value:
            result[key] = value["BOOL"]
        elif "NULL" in value:
            result[key] = None
        elif "L" in value:
            result[key] = [convert_dynamodb_to_json({"item": v})["item"] for v in value["L"]]
        elif "M" in value:
            result[key] = convert_dynamodb_to_json(value["M"])
        else:
            result[key] = value
    return result


def lambda_handler(event: dict, context: Any) -> dict:
    """
    Compare ingress states between Source and Destination.

    Event structure (from Step Function):
    {
        "domain": "mro",
        "instance": "MI1",
        "environment": "ppd",
        "source_state": {...},  # State from DynamoDB
        "destination_state": {...}       # State from DynamoDB
    }

    Returns:
    {
        "status": "synced" | "differs",
        "summary": {
            "front": "synced | differs | only_source | only_destination",
            "bo": "synced | differs",
            "private": "synced | differs",
            "sftp": "synced | differs"
        },
        "rulesComparison": {...},
        "hostsComparison": {...},
        "issues": [...],
        "timestamp": "ISO8601"
    }
    """
    logger.info(f"Comparing ingress states for {event.get('instance')}-{event.get('environment')}")

    domain = event.get("domain", "mro")
    instance = event.get("instance", "unknown")
    environment = event.get("environment", "unknown")

    # Extract payloads from states
    source_raw = event.get("source_state", {})
    destination_raw = event.get("destination_state", {})

    source_state = extract_state_payload(source_raw)
    destination_state = extract_state_payload(destination_raw)

    # Check if states are valid (handle nested SourceState/DestinationState)
    def check_found(raw: dict) -> bool:
        if not raw:
            return False
        # Handle nested SourceState/DestinationState from parallel branches
        if "SourceState" in raw:
            nested = raw["SourceState"]
            return bool(nested.get("itemData") or nested.get("item"))
        if "DestinationState" in raw:
            nested = raw["DestinationState"]
            return bool(nested.get("itemData") or nested.get("item"))
        return bool(raw.get("itemData") or raw.get("item") or raw.get("found"))

    source_found = check_found(source_raw) if isinstance(source_raw, dict) else bool(source_state)
    destination_found = check_found(destination_raw) if isinstance(destination_raw, dict) else bool(destination_state)

    if not source_found and not destination_found:
        return {
            "status": "error",
            "error": "BothStatesMissing",
            "message": "Neither Source nor Destination state found in DynamoDB",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    if not source_found:
        return {
            "status": "only_destination",
            "message": "Source state not found",
            "destination_status": destination_state.get("status"),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    if not destination_found:
        return {
            "status": "only_source",
            "message": "Destination state not found",
            "source_status": source_state.get("status"),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    # Get ingresses from states
    source_ingresses = source_state.get("ingresses", {})
    destination_ingresses = destination_state.get("ingresses", {})

    # Compare each ingress type
    ingress_types = ["front", "bo", "private"]
    rules_comparison = {}
    summary = {}

    for ingress_type in ingress_types:
        source_ingress = source_ingresses.get(ingress_type)
        destination_ingress = destination_ingresses.get(ingress_type)

        comparison = compare_ingress_type(source_ingress, destination_ingress)
        rules_comparison[ingress_type] = comparison
        summary[ingress_type] = comparison.get("status", "unknown")

    # Compare SFTP services
    source_sftp = source_state.get("sftpService")
    destination_sftp = destination_state.get("sftpService")
    sftp_comparison = compare_sftp(source_sftp, destination_sftp)
    summary["sftp"] = sftp_comparison.get("status", "unknown")

    # Compare TargetGroupBindings
    source_tgbs = source_state.get("targetGroupBindings", [])
    destination_tgbs = destination_state.get("targetGroupBindings", [])
    tgb_comparison = compare_target_group_bindings(source_tgbs, destination_tgbs)
    summary["targetGroupBindings"] = tgb_comparison.get("status", "unknown")

    # Compare ALB health (if enriched)
    source_summary = source_state.get("summary", {})
    destination_summary = destination_state.get("summary", {})
    alb_health_comparison = compare_alb_health(source_summary, destination_summary)
    tgb_health_comparison = compare_tgb_health_details(source_tgbs, destination_tgbs)

    # Add ALB health status to summary if enriched
    if alb_health_comparison.get("status") not in ("not_enriched", "source_not_enriched", "destination_not_enriched"):
        summary["albHealth"] = alb_health_comparison.get("status", "unknown")

    # Compare hosts
    hosts_comparison = compare_hosts(source_state, destination_state)

    # Detect issues
    issues = []

    # Check for missing hosts in Destination
    for host in hosts_comparison.get("onlySource", []):
        # Skip internal hosts as they may differ
        if not host.endswith(".internal"):
            issues.append({
                "severity": "warning",
                "issue": "HostMissingInDestination",
                "host": host,
                "message": f"Host {host} exists in Source but not in Destination",
            })

    # Check for unexpected rule differences
    for ingress_type, comparison in rules_comparison.items():
        if comparison.get("status") == "differs":
            for diff in comparison.get("differentRules", []):
                if not diff.get("expected"):
                    issues.append({
                        "severity": "warning",
                        "issue": "UnexpectedRuleDifference",
                        "ingressType": ingress_type,
                        "host": diff.get("host"),
                        "path": diff.get("path"),
                        "message": diff.get("reason", "Rule differs unexpectedly"),
                    })

            for rule in comparison.get("onlySource", []):
                issues.append({
                    "severity": "warning",
                    "issue": "RuleMissingInDestination",
                    "ingressType": ingress_type,
                    "host": rule.get("host"),
                    "path": rule.get("path"),
                    "message": f"Rule {rule.get('host')}{rule.get('path')} missing in Destination",
                })

    # Check for ALB health issues
    if alb_health_comparison.get("status") == "destination_degraded":
        source_ratio = alb_health_comparison.get("source", {}).get("healthRatio", 0)
        dest_ratio = alb_health_comparison.get("destination", {}).get("healthRatio", 0)
        issues.append({
            "severity": "warning",
            "issue": "ALBHealthDegraded",
            "message": f"Destination ALB health ({dest_ratio}%) is lower than Source ({source_ratio}%)",
        })

    # Check for TGB type health degradation
    if tgb_health_comparison.get("status") == "differs":
        for tgb_type, health_data in tgb_health_comparison.get("byType", {}).items():
            if health_data.get("status") == "destination_degraded":
                source_ratio = health_data.get("source", {}).get("ratio", 0)
                dest_ratio = health_data.get("destination", {}).get("ratio", 0)
                issues.append({
                    "severity": "warning",
                    "issue": "TGBHealthDegraded",
                    "tgbType": tgb_type,
                    "message": f"TGB type '{tgb_type}' health degraded: {dest_ratio}% vs {source_ratio}%",
                })

    # Determine overall status
    status_values = list(summary.values())
    if all(s in ("synced", "missing") for s in status_values):
        overall_status = "synced"
    else:
        overall_status = "differs"

    timestamp = datetime.now(timezone.utc).isoformat()

    result = {
        "status": overall_status,
        "summary": summary,
        "rulesComparison": rules_comparison,
        "tgbComparison": tgb_comparison,
        "albHealthComparison": alb_health_comparison,
        "tgbHealthComparison": tgb_health_comparison,
        "sftpComparison": sftp_comparison,
        "hostsComparison": hosts_comparison,
        "issues": issues,
        "timestamp": timestamp,
    }

    logger.info(
        f"Comparison complete: status={overall_status}, "
        f"front={summary.get('front')}, bo={summary.get('bo')}, "
        f"private={summary.get('private')}, sftp={summary.get('sftp')}, "
        f"tgbs={summary.get('targetGroupBindings')}, issues={len(issues)}"
    )

    return result
