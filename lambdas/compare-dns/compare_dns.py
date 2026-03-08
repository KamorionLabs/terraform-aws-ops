"""
Compare DNS States (Legacy vs New Horizon)
==========================================
Compares DNS resolution states between Legacy and New Horizon environments.

This Lambda:
- Receives parsed states from DynamoDB
- Compares DNS records by key/hostname
- Identifies expected vs unexpected differences
- Produces structured comparison report

Environment Variables:
- LOG_LEVEL: Logging level (default: INFO)
"""

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)
logger.setLevel(os.environ.get("LOG_LEVEL", "INFO"))


# Expected differences between Legacy and New Horizon DNS
EXPECTED_DIFFERENCES = {
    "recordType": {
        "description": "NH uses CNAME/Alias to CloudFront instead of A records to ALB",
        "legacy": ["A"],
        "nh": ["CNAME", "A"],  # A for alias records
    },
    "ttl": {
        "description": "NH may use different TTL values",
        "legacy_range": [60, 3600],
        "nh_range": [60, 300],
    },
    "targetType": {
        "description": "NH routes through CloudFront CDN",
        "legacy": ["elb", "alb", "direct"],
        "nh": ["cloudfront"],
    },
}


def parse_dynamodb_item(raw_state: dict) -> dict:
    """
    Parse DynamoDB item format to regular dict.

    DynamoDB format: {"S": "value"}, {"N": "123"}, {"BOOL": true}
    """
    if not raw_state:
        return None

    def parse_value(val):
        if isinstance(val, dict):
            if "S" in val:
                return val["S"]
            elif "N" in val:
                return float(val["N"]) if "." in val["N"] else int(val["N"])
            elif "BOOL" in val:
                return val["BOOL"]
            elif "L" in val:
                return [parse_value(v) for v in val["L"]]
            elif "M" in val:
                return {k: parse_value(v) for k, v in val["M"].items()}
            elif "NULL" in val:
                return None
            else:
                return {k: parse_value(v) for k, v in val.items()}
        elif isinstance(val, list):
            return [parse_value(v) for v in val]
        return val

    return parse_value(raw_state)


def extract_payload(state_result: dict) -> dict:
    """Extract payload from state result structure."""
    if not state_result:
        return None

    # Handle parallel result format
    if "LegacyState" in state_result:
        state_result = state_result["LegacyState"]
    elif "NHState" in state_result:
        state_result = state_result["NHState"]

    # Check if found
    found = state_result.get("found")
    if found is False or found is None:
        return None

    # Parse raw DynamoDB item if present
    raw = state_result.get("raw")
    if raw:
        parsed = parse_dynamodb_item(raw)
        if parsed and "payload" in parsed:
            payload = parsed.get("payload")
            if isinstance(payload, dict) and "M" in payload:
                return parse_dynamodb_item(payload)
            return payload

    # If found is the actual data
    if isinstance(found, dict) and "payload" in found:
        return found.get("payload")

    return None


def compare_domains(legacy_domains: list, nh_domains: list, domain_type: str = "managed") -> dict:
    """
    Compare domains between Legacy and NH.

    Returns structured comparison with categories:
    - sameRecords: Domains in both with same DNS config
    - differentValues: Domains in both with different DNS config
    - onlyLegacy: Only in Legacy
    - onlyNH: Only in NH
    """
    # Index domains by key
    legacy_by_key = {d.get("key"): d for d in legacy_domains if d.get("key")}
    nh_by_key = {d.get("key"): d for d in nh_domains if d.get("key")}

    legacy_keys = set(legacy_by_key.keys())
    nh_keys = set(nh_by_key.keys())

    # Find common and unique keys
    common_keys = legacy_keys & nh_keys
    only_legacy_keys = legacy_keys - nh_keys
    only_nh_keys = nh_keys - legacy_keys

    same_records = []
    different_values = []
    only_legacy = []
    only_nh = []

    # Compare common domains
    for key in common_keys:
        legacy = legacy_by_key[key]
        nh = nh_by_key[key]

        differences = compare_single_domain(legacy, nh)

        if not differences:
            same_records.append({
                "key": key,
                "hostname": nh.get("hostname", legacy.get("hostname")),
                "status": "synced"
            })
        else:
            different_values.append({
                "key": key,
                "hostname": nh.get("hostname", legacy.get("hostname")),
                "legacy": extract_dns_summary(legacy),
                "nh": extract_dns_summary(nh),
                "differences": differences
            })

    # Only legacy
    for key in only_legacy_keys:
        legacy = legacy_by_key[key]
        only_legacy.append({
            "key": key,
            "hostname": legacy.get("hostname"),
            "resolved": legacy.get("resolved"),
            "status": legacy.get("status"),
            "migrationStatus": legacy.get("status")
        })

    # Only NH
    for key in only_nh_keys:
        nh = nh_by_key[key]
        only_nh.append({
            "key": key,
            "hostname": nh.get("hostname"),
            "resolved": nh.get("resolved"),
            "status": nh.get("status"),
            "migrationStatus": nh.get("status")
        })

    return {
        "sameRecords": same_records,
        "differentValues": different_values,
        "onlyLegacy": only_legacy,
        "onlyNH": only_nh
    }


def extract_dns_summary(domain: dict) -> dict:
    """Extract DNS summary from domain result."""
    return {
        "recordType": domain.get("recordType"),
        "recordValue": domain.get("recordValue"),
        "ttl": domain.get("ttl"),
        "resolved": domain.get("resolved"),
        "resolvedIPs": domain.get("resolvedIPs", []),
        "isAlias": domain.get("isAlias", False),
    }


def compare_single_domain(legacy: dict, nh: dict) -> list:
    """Compare two domain records and return list of differences."""
    differences = []

    # Compare resolution status
    legacy_resolved = legacy.get("resolved", False)
    nh_resolved = nh.get("resolved", False)
    if legacy_resolved != nh_resolved:
        differences.append({
            "field": "resolved",
            "legacy": legacy_resolved,
            "nh": nh_resolved,
            "expected": False,
            "reason": None
        })

    # Compare record type
    legacy_type = legacy.get("recordType", "")
    nh_type = nh.get("recordType", "")
    if legacy_type and nh_type and legacy_type != nh_type:
        # Check if expected (A -> CNAME for CloudFront)
        expected_diff = EXPECTED_DIFFERENCES.get("recordType", {})
        is_expected = (
            legacy_type in expected_diff.get("legacy", []) and
            nh_type in expected_diff.get("nh", [])
        )
        differences.append({
            "field": "recordType",
            "legacy": legacy_type,
            "nh": nh_type,
            "expected": is_expected,
            "reason": expected_diff.get("description") if is_expected else None
        })

    # Compare record value (target)
    legacy_value = legacy.get("recordValue", "")
    nh_value = nh.get("recordValue", "")
    if legacy_value and nh_value and legacy_value != nh_value:
        # Determine if this is expected (ALB -> CloudFront)
        is_expected = is_expected_target_change(legacy_value, nh_value)
        reason = None
        if is_expected:
            if "cloudfront.net" in str(nh_value).lower():
                reason = "NH routes through CloudFront CDN"
            else:
                reason = "Different infrastructure targets"

        differences.append({
            "field": "recordValue",
            "legacy": legacy_value,
            "nh": nh_value,
            "expected": is_expected,
            "reason": reason
        })

    # Compare TTL
    legacy_ttl = legacy.get("ttl", 0)
    nh_ttl = nh.get("ttl", 0)
    if legacy_ttl and nh_ttl and legacy_ttl != nh_ttl:
        # TTL differences are often expected
        expected_diff = EXPECTED_DIFFERENCES.get("ttl", {})
        legacy_in_range = expected_diff.get("legacy_range", [0, 0])
        nh_in_range = expected_diff.get("nh_range", [0, 0])

        is_expected = (
            legacy_in_range[0] <= legacy_ttl <= legacy_in_range[1] and
            nh_in_range[0] <= nh_ttl <= nh_in_range[1]
        )

        differences.append({
            "field": "ttl",
            "legacy": legacy_ttl,
            "nh": nh_ttl,
            "expected": is_expected,
            "reason": expected_diff.get("description") if is_expected else None
        })

    # Compare resolved IPs (if both are A records)
    if legacy_type == "A" and nh_type == "A":
        legacy_ips = set(legacy.get("resolvedIPs", []))
        nh_ips = set(nh.get("resolvedIPs", []))
        if legacy_ips != nh_ips:
            differences.append({
                "field": "resolvedIPs",
                "legacy": list(legacy_ips),
                "nh": list(nh_ips),
                "expected": False,
                "reason": None
            })

    return differences


def is_expected_target_change(legacy_value: str, nh_value: str) -> bool:
    """Check if target change is expected (ALB -> CloudFront)."""
    legacy_str = str(legacy_value).lower()
    nh_str = str(nh_value).lower()

    # ALB/ELB to CloudFront is expected
    if ("elb." in legacy_str or "amazonaws.com" in legacy_str) and "cloudfront.net" in nh_str:
        return True

    # Direct IP to CloudFront is expected
    if is_ip_address(legacy_str) and "cloudfront.net" in nh_str:
        return True

    return False


def is_ip_address(value: str) -> bool:
    """Check if value looks like an IP address."""
    import re
    ip_pattern = r'^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$'
    return bool(re.match(ip_pattern, value.strip()))


def compare_migration_status(legacy_domains: list, nh_domains: list) -> dict:
    """Analyze migration status across domains."""
    migrating = []
    migrated = []
    pending = []

    # Build lookup
    nh_keys = {d.get("key") for d in nh_domains if d.get("key")}

    for domain in legacy_domains:
        key = domain.get("key")
        if not key:
            continue

        status = domain.get("status", "active")

        if status == "migrating":
            migrating.append(key)
        elif status == "migrated" or domain.get("migrated"):
            migrated.append(key)
        elif key in nh_keys:
            pending.append(key)
        else:
            pending.append(key)

    return {
        "migrating": migrating,
        "migrated": migrated,
        "pending": pending
    }


def compare_resolution_stats(legacy_domains: list, nh_domains: list) -> dict:
    """Compare resolution statistics between Legacy and NH."""
    def calc_stats(domains: list) -> dict:
        total = len(domains)
        resolved = sum(1 for d in domains if d.get("resolved"))
        failed = sum(1 for d in domains if not d.get("resolved"))
        avg_response = 0
        if resolved > 0:
            response_times = [d.get("responseTimeMs", 0) for d in domains if d.get("resolved")]
            avg_response = sum(response_times) / len(response_times) if response_times else 0

        return {
            "total": total,
            "resolved": resolved,
            "failed": failed,
            "avgResponseTimeMs": round(avg_response, 2)
        }

    legacy_stats = calc_stats(legacy_domains)
    nh_stats = calc_stats(nh_domains)

    return {
        "legacy": legacy_stats,
        "nh": nh_stats,
        "status": "synced" if legacy_stats["failed"] == 0 and nh_stats["failed"] == 0 else "differs"
    }


def determine_comparison_status(comparison: dict, issues: list) -> str:
    """Determine overall comparison status."""
    managed_comparison = comparison.get("managedDomainsComparison", {})
    api_comparison = comparison.get("apiDomainsComparison", {})

    # Check for unexpected differences
    managed_different = managed_comparison.get("differentValues", [])
    api_different = api_comparison.get("differentValues", [])

    all_different = managed_different + api_different

    unexpected_diffs = [
        d for d in all_different
        if any(not diff.get("expected") for diff in d.get("differences", []))
    ]

    if unexpected_diffs:
        return "differs"

    # Check for domains only in one side
    if (managed_comparison.get("onlyLegacy") or managed_comparison.get("onlyNH") or
        api_comparison.get("onlyLegacy") or api_comparison.get("onlyNH")):
        return "differs"

    # Check for resolution failures
    resolution = comparison.get("resolutionStats", {})
    if resolution.get("legacy", {}).get("failed", 0) > 0 or resolution.get("nh", {}).get("failed", 0) > 0:
        return "differs"

    if issues:
        return "differs"

    return "synced"


def lambda_handler(event: dict, context: Any) -> dict:
    """
    Lambda entry point.

    Event structure (from Step Function):
    {
        "Domain": "network",
        "Country": "DE",
        "Environment": "ppd",
        "LegacyState": { ... from DynamoDB ... },
        "NHState": { ... from DynamoDB ... }
    }

    Returns:
    {
        "statusCode": 200,
        "payload": {
            "status": "synced | differs",
            "summary": {...},
            "managedDomainsComparison": {...},
            "apiDomainsComparison": {...},
            "migrationStatus": {...},
            "resolutionStats": {...},
            "issues": [...],
            "timestamp": "ISO8601"
        }
    }
    """
    logger.info(f"Event keys: {list(event.keys())}")

    # Extract parameters
    domain = event.get("Domain", "network")
    country = event.get("Country", "")
    environment = event.get("Environment", "")

    # Parse states from DynamoDB format
    legacy_state = event.get("LegacyState", {})
    nh_state = event.get("NHState", {})

    legacy_payload = extract_payload(legacy_state)
    nh_payload = extract_payload(nh_state)

    issues = []

    # Check if states exist
    if not legacy_payload:
        issues.append("Legacy state not found in DynamoDB")
    if not nh_payload:
        issues.append("NH state not found in DynamoDB")

    if not legacy_payload and not nh_payload:
        return {
            "statusCode": 200,
            "payload": {
                "status": "error",
                "summary": {
                    "legacyFound": False,
                    "nhFound": False,
                    "recordCount": "unknown"
                },
                "issues": issues,
                "timestamp": datetime.now(timezone.utc).isoformat()
            }
        }

    # Extract domains
    legacy_managed = legacy_payload.get("managedDomains", []) if legacy_payload else []
    nh_managed = nh_payload.get("managedDomains", []) if nh_payload else []
    legacy_api = legacy_payload.get("apiDomains", []) if legacy_payload else []
    nh_api = nh_payload.get("apiDomains", []) if nh_payload else []

    logger.info(f"Comparing: {len(legacy_managed)} legacy managed + {len(legacy_api)} api vs "
                f"{len(nh_managed)} NH managed + {len(nh_api)} api")

    # Perform comparisons
    managed_comparison = compare_domains(legacy_managed, nh_managed, "managed")
    api_comparison = compare_domains(legacy_api, nh_api, "api")

    all_legacy = legacy_managed + legacy_api
    all_nh = nh_managed + nh_api

    migration_status = compare_migration_status(all_legacy, all_nh)
    resolution_stats = compare_resolution_stats(all_legacy, all_nh)

    # Build summary
    summary = {
        "legacyFound": legacy_payload is not None,
        "nhFound": nh_payload is not None,
        "recordCount": "synced" if len(all_legacy) == len(all_nh) else "differs",
        "legacyCount": len(all_legacy),
        "nhCount": len(all_nh),
        "sameRecords": (
            len(managed_comparison.get("sameRecords", [])) +
            len(api_comparison.get("sameRecords", []))
        ),
        "differentValues": (
            len(managed_comparison.get("differentValues", [])) +
            len(api_comparison.get("differentValues", []))
        ),
        "onlyLegacy": (
            len(managed_comparison.get("onlyLegacy", [])) +
            len(api_comparison.get("onlyLegacy", []))
        ),
        "onlyNH": (
            len(managed_comparison.get("onlyNH", [])) +
            len(api_comparison.get("onlyNH", []))
        ),
    }

    # Build full comparison result
    comparison = {
        "managedDomainsComparison": managed_comparison,
        "apiDomainsComparison": api_comparison,
        "migrationStatus": migration_status,
        "resolutionStats": resolution_stats
    }

    # Determine overall status
    status = determine_comparison_status(comparison, issues)

    payload = {
        "status": status,
        "country": country,
        "environment": environment,
        "summary": summary,
        **comparison,
        "issues": issues,
        "timestamp": datetime.now(timezone.utc).isoformat()
    }

    return {
        "statusCode": 200,
        "payload": payload
    }
