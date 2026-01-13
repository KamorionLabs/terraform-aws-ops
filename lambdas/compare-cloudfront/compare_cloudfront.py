"""
Compare CloudFront States (Source vs Destination)
==================================================
Compares CloudFront distribution states between Source and Destination environments.

This Lambda:
- Receives parsed states from DynamoDB
- Compares distributions by key/hostname
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


# Expected differences between Source and Destination environments
EXPECTED_DIFFERENCES = {
    "httpVersion": {
        "source": ["http2", "http1.1"],
        "destination": ["http2and3"],
        "reason": "Destination uses HTTP/3 for better performance"
    },
    "priceClass": {
        "source": ["PriceClass_All"],
        "destination": ["PriceClass_100", "PriceClass_200"],
        "reason": "Destination uses optimized price class for cost efficiency"
    },
    "waf": {
        "source": False,
        "destination": True,
        "reason": "Destination has WAF protection enabled"
    },
    "originShield": {
        "source": False,
        "destination": True,
        "reason": "Destination uses Origin Shield to reduce origin load"
    },
    "tlsVersion": {
        "source": ["TLSv1.2_2019", "TLSv1.2_2018", "TLSv1.1_2016"],
        "destination": ["TLSv1.2_2021"],
        "reason": "Destination uses latest TLS configuration"
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
                # Already parsed or nested structure
                return {k: parse_value(v) for k, v in val.items()}
        elif isinstance(val, list):
            return [parse_value(v) for v in val]
        return val

    return parse_value(raw_state)


def extract_payload(state_result: dict) -> dict:
    """Extract payload from state result structure."""
    if not state_result:
        return None

    # Handle parallel result format - look for nested state object
    # Support both old (Legacy/NH) and new (Source/Destination) naming
    if "SourceState" in state_result:
        state_result = state_result["SourceState"]
    elif "DestinationState" in state_result:
        state_result = state_result["DestinationState"]
    elif "LegacyState" in state_result:
        state_result = state_result["LegacyState"]
    elif "NHState" in state_result:
        state_result = state_result["NHState"]

    # Get raw DynamoDB item - supports both 'raw' and 'itemData' keys
    raw = state_result.get("raw") or state_result.get("itemData")

    # If no data found
    if not raw:
        # Check if found flag exists
        found = state_result.get("found")
        if found is False or found is None:
            return None
        # If found is the actual data
        if isinstance(found, dict) and "payload" in found:
            return found.get("payload")
        return None

    # Parse raw DynamoDB item
    parsed = parse_dynamodb_item(raw)

    if parsed and "payload" in parsed:
        # Handle nested DynamoDB format
        payload = parsed.get("payload")
        if isinstance(payload, dict) and "M" in payload:
            return parse_dynamodb_item(payload)
        return payload

    return None


def normalize_distribution_key(subdomain: str) -> str:
    """
    Normalize a subdomain for matching between Source and Destination.

    Handles naming convention differences:
    - Source: {country}-webshop (e.g., fr-webshop, de-webshop)
    - Destination: mro-{country} (e.g., mro-fr, mro-de)

    Also removes suffixes that differ:
    - -nh (NH suffix on aliases)
    - -cache (legacy caching pattern)
    - -webshop (legacy suffix for country distributions)
    - -punchout (punchout suffix - same distribution as webshop)

    Examples:
    - fr-webshop-nh -> fr
    - fr-webshop-cache -> fr
    - fr-punchout-nh -> fr
    - mro-fr -> fr
    - mro-bo -> mro-bo (kept as-is for backoffice)
    - mro-it-fluidmec -> it-fluidmec
    - mro-fr-legoueix -> fr-legoueix
    - de-webshop -> de
    - de-punchout -> de
    - media-pim -> media-pim (kept for special distributions)
    """
    # Remove -nh suffix (NH naming on aliases)
    if subdomain.endswith("-nh"):
        subdomain = subdomain[:-3]

    # Remove -cache suffix (legacy caching pattern)
    if subdomain.endswith("-cache"):
        subdomain = subdomain[:-6]

    # Handle mro-{...} pattern (NH tfvars naming)
    # Remove mro- prefix except for mro-bo (backoffice)
    if subdomain.startswith("mro-"):
        rest = subdomain[4:]  # Remove "mro-" prefix
        # Keep mro-bo as-is (backoffice distribution)
        if rest == "bo":
            return subdomain
        # For everything else, return without mro- prefix
        # Examples: mro-fr -> fr, mro-it-fluidmec -> it-fluidmec
        return rest

    # Handle {country}-webshop pattern (legacy naming)
    # This normalizes "fr-webshop" to "fr"
    if subdomain.endswith("-webshop"):
        return subdomain[:-8]  # Remove "-webshop" suffix

    # Handle {country}-punchout pattern
    # In Legacy, webshop and punchout are often on same distribution
    # In NH, they may be separate but should match on country
    if subdomain.endswith("-punchout"):
        return subdomain[:-9]  # Remove "-punchout" suffix

    return subdomain


def extract_distribution_key(dist: dict) -> str:
    """
    Extract a normalized key for matching distributions.

    For tfvars mode: uses the 'key' field directly
    For discovery mode: extracts subdomain from first alias and normalizes

    Examples:
    - tfvars: {"key": "de-webshop-bo"} -> "de-webshop-bo"
    - discovery: {"aliases": ["fr-webshop.preprod.rubix.com"]} -> "fr-webshop"
    - discovery with NH: {"aliases": ["fr-webshop-nh.preprod.rubix.com"]} -> "fr-webshop"
    - discovery with cache: {"aliases": ["fr-webshop-cache.preprod.rubix.com"]} -> "fr-webshop"
    """
    # Prefer explicit key (tfvars mode) - normalize it too
    if dist.get("key"):
        return normalize_distribution_key(dist["key"])

    # Extract from hostname if available
    if dist.get("hostname"):
        hostname = dist["hostname"].lower()
        parts = hostname.split(".")
        if parts:
            subdomain = parts[0]
            return normalize_distribution_key(subdomain)

    # Extract from first alias (discovery mode)
    aliases = dist.get("aliases", [])
    if aliases:
        alias = aliases[0].lower()
        parts = alias.split(".")
        if parts:
            subdomain = parts[0]
            return normalize_distribution_key(subdomain)

    # Fallback to distribution ID
    return dist.get("id", "unknown")


def compare_distributions(source_dists: list, dest_dists: list) -> dict:
    """
    Compare distributions between Source and Destination.

    Returns structured comparison with categories:
    - sameDistributions: Distributions in both with same config
    - differentConfig: Distributions in both with different config
    - onlySource: Only in Source
    - onlyDestination: Only in Destination
    """
    # Index distributions by extracted key (supports both tfvars and discovery modes)
    source_by_key = {}
    for d in source_dists:
        key = extract_distribution_key(d)
        if key:
            source_by_key[key] = d

    dest_by_key = {}
    for d in dest_dists:
        key = extract_distribution_key(d)
        if key:
            dest_by_key[key] = d

    source_keys = set(source_by_key.keys())
    dest_keys = set(dest_by_key.keys())

    # Find common and unique keys
    common_keys = source_keys & dest_keys
    only_source_keys = source_keys - dest_keys
    only_dest_keys = dest_keys - source_keys

    same_distributions = []
    different_config = []
    only_source = []
    only_destination = []

    # Compare common distributions
    for key in common_keys:
        source = source_by_key[key]
        dest = dest_by_key[key]

        differences = compare_single_distribution(source, dest)

        # Get hostname from either env (prefer explicit, fallback to first alias)
        hostname = (
            dest.get("hostname") or
            source.get("hostname") or
            (dest.get("aliases", []) or [""])[0] or
            (source.get("aliases", []) or [""])[0]
        )

        if not differences:
            same_distributions.append({
                "key": key,
                "hostname": hostname,
                "sourceId": source.get("id"),
                "destinationId": dest.get("id"),
                "status": "synced"
            })
        else:
            different_config.append({
                "key": key,
                "hostname": hostname,
                "sourceId": source.get("id"),
                "destinationId": dest.get("id"),
                "differences": differences
            })

    # Only source
    for key in only_source_keys:
        source = source_by_key[key]
        hostname = source.get("hostname") or (source.get("aliases", []) or [""])[0]
        only_source.append({
            "key": key,
            "hostname": hostname,
            "id": source.get("id"),
            "aliases": source.get("aliases", [])[:3],  # First 3 aliases for context
            "status": source.get("status"),
            "migrationStatus": source.get("migrationStatus", "not_migrated")
        })

    # Only destination
    for key in only_dest_keys:
        dest = dest_by_key[key]
        hostname = dest.get("hostname") or (dest.get("aliases", []) or [""])[0]
        only_destination.append({
            "key": key,
            "hostname": hostname,
            "id": dest.get("id"),
            "aliases": dest.get("aliases", [])[:3],  # First 3 aliases for context
            "status": dest.get("status"),
            "migrationStatus": dest.get("migrationStatus", "new")
        })

    return {
        "sameDistributions": same_distributions,
        "differentConfig": different_config,
        "onlySource": only_source,
        "onlyDestination": only_destination
    }


def compare_single_distribution(source: dict, dest: dict) -> list:
    """Compare two distributions and return list of differences."""
    differences = []

    # Compare HTTP version
    source_http = source.get("httpVersion", "http2")
    dest_http = dest.get("httpVersion", "http2")
    if source_http != dest_http:
        expected_diff = EXPECTED_DIFFERENCES.get("httpVersion", {})
        is_expected = (
            source_http in expected_diff.get("source", []) and
            dest_http in expected_diff.get("destination", [])
        )
        differences.append({
            "field": "httpVersion",
            "source": source_http,
            "destination": dest_http,
            "expected": is_expected,
            "reason": expected_diff.get("reason") if is_expected else None
        })

    # Compare price class
    source_price = source.get("priceClass", "PriceClass_All")
    dest_price = dest.get("priceClass", "PriceClass_All")
    if source_price != dest_price:
        expected_diff = EXPECTED_DIFFERENCES.get("priceClass", {})
        is_expected = (
            source_price in expected_diff.get("source", []) and
            dest_price in expected_diff.get("destination", [])
        )
        differences.append({
            "field": "priceClass",
            "source": source_price,
            "destination": dest_price,
            "expected": is_expected,
            "reason": expected_diff.get("reason") if is_expected else None
        })

    # Compare WAF
    source_waf = source.get("waf", {}).get("enabled", False)
    dest_waf = dest.get("waf", {}).get("enabled", False)
    if source_waf != dest_waf:
        expected_diff = EXPECTED_DIFFERENCES.get("waf", {})
        is_expected = (
            source_waf == expected_diff.get("source") and
            dest_waf == expected_diff.get("destination")
        )
        differences.append({
            "field": "waf",
            "source": {"enabled": source_waf},
            "destination": {"enabled": dest_waf, "webAclId": dest.get("waf", {}).get("webAclId")},
            "expected": is_expected,
            "reason": expected_diff.get("reason") if is_expected else None
        })

    # Compare TLS version
    source_cert = source.get("certificate", {})
    dest_cert = dest.get("certificate", {})
    source_tls = source_cert.get("minimumProtocolVersion", "")
    dest_tls = dest_cert.get("minimumProtocolVersion", "")
    if source_tls != dest_tls:
        expected_diff = EXPECTED_DIFFERENCES.get("tlsVersion", {})
        is_expected = (
            source_tls in expected_diff.get("source", []) and
            dest_tls in expected_diff.get("destination", [])
        )
        differences.append({
            "field": "tlsVersion",
            "source": source_tls,
            "destination": dest_tls,
            "expected": is_expected,
            "reason": expected_diff.get("reason") if is_expected else None
        })

    # Compare Origin Shield
    source_origins = source.get("origins", [])
    dest_origins = dest.get("origins", [])
    source_has_shield = any(o.get("originShield", {}).get("enabled") for o in source_origins)
    dest_has_shield = any(o.get("originShield", {}).get("enabled") for o in dest_origins)
    if source_has_shield != dest_has_shield:
        expected_diff = EXPECTED_DIFFERENCES.get("originShield", {})
        is_expected = (
            source_has_shield == expected_diff.get("source") and
            dest_has_shield == expected_diff.get("destination")
        )
        differences.append({
            "field": "originShield",
            "source": source_has_shield,
            "destination": dest_has_shield,
            "expected": is_expected,
            "reason": expected_diff.get("reason") if is_expected else None
        })

    # Compare origin count
    if len(source_origins) != len(dest_origins):
        differences.append({
            "field": "originCount",
            "source": len(source_origins),
            "destination": len(dest_origins),
            "expected": False,
            "reason": None
        })

    return differences


def compare_migration_status(source_dists: list, dest_dists: list) -> dict:
    """Analyze migration status across distributions."""
    migrating = []
    migrated = []
    pending = []

    # Build lookup using extracted keys
    dest_keys = {extract_distribution_key(d) for d in dest_dists}

    for dist in source_dists:
        key = extract_distribution_key(dist)
        if not key or key == "unknown":
            continue

        status = dist.get("migrationStatus", "active")

        if status == "migrating":
            migrating.append(key)
        elif status == "migrated" or dist.get("migrated"):
            migrated.append(key)
        elif key in dest_keys:
            # In both but status is active - migrated
            migrated.append(key)
        else:
            pending.append(key)

    return {
        "migrating": migrating,
        "migrated": migrated,
        "pending": pending
    }


def compare_origins(source_dists: list, dest_dists: list) -> dict:
    """Compare origins between Source and Destination."""
    def count_by_type(dists: list) -> dict:
        counts = {"s3Origins": 0, "albOrigins": 0, "customOrigins": 0}
        for dist in dists:
            for origin in dist.get("origins", []):
                origin_type = origin.get("type", "Custom")
                if origin_type == "S3":
                    counts["s3Origins"] += 1
                elif origin_type == "ALB":
                    counts["albOrigins"] += 1
                else:
                    counts["customOrigins"] += 1
        return counts

    source_counts = count_by_type(source_dists)
    dest_counts = count_by_type(dest_dists)

    # Destination typically has more origins (API, additional services)
    is_expected = (
        dest_counts["s3Origins"] >= source_counts["s3Origins"] and
        dest_counts["albOrigins"] >= source_counts["albOrigins"]
    )

    return {
        "source": source_counts,
        "destination": dest_counts,
        "expected": is_expected,
        "reason": "Destination added origins for API/additional services" if is_expected and source_counts != dest_counts else None
    }


def compare_waf_config(source_dists: list, dest_dists: list) -> dict:
    """Compare WAF configuration between Source and Destination."""
    source_with_waf = sum(1 for d in source_dists if d.get("waf", {}).get("enabled"))
    dest_with_waf = sum(1 for d in dest_dists if d.get("waf", {}).get("enabled"))

    # Destination should have WAF on all distributions
    is_expected = dest_with_waf >= len(dest_dists) and source_with_waf <= dest_with_waf

    # Get unique WAF ACL IDs
    dest_waf_ids = list(set(
        d.get("waf", {}).get("webAclId")
        for d in dest_dists
        if d.get("waf", {}).get("webAclId")
    ))

    return {
        "source": {
            "enabled": source_with_waf > 0,
            "count": source_with_waf,
            "total": len(source_dists)
        },
        "destination": {
            "enabled": dest_with_waf > 0,
            "count": dest_with_waf,
            "total": len(dest_dists),
            "webAclIds": dest_waf_ids
        },
        "expected": is_expected,
        "reason": "Destination has WAF protection on all distributions" if is_expected else None
    }


def compare_certificates(source_dists: list, dest_dists: list) -> dict:
    """Compare certificate configuration between Source and Destination."""
    source_tls = set()
    dest_tls = set()

    for dist in source_dists:
        cert = dist.get("certificate", {})
        if cert.get("minimumProtocolVersion"):
            source_tls.add(cert["minimumProtocolVersion"])

    for dist in dest_dists:
        cert = dist.get("certificate", {})
        if cert.get("minimumProtocolVersion"):
            dest_tls.add(cert["minimumProtocolVersion"])

    # Destination should use newer TLS
    is_expected = all("2021" in tls or "2022" in tls or "2023" in tls for tls in dest_tls)

    return {
        "source": {
            "protocolVersions": list(source_tls)
        },
        "destination": {
            "protocolVersions": list(dest_tls)
        },
        "status": "synced" if source_tls == dest_tls else "differs",
        "expected": is_expected,
        "reason": "Destination uses updated TLS configuration" if is_expected else None
    }


def determine_comparison_status(comparison: dict, issues: list) -> str:
    """Determine overall comparison status."""
    dist_comparison = comparison.get("distributionsComparison", {})

    # If only expected differences, status is synced
    different = dist_comparison.get("differentConfig", [])
    unexpected_diffs = [
        d for d in different
        if any(not diff.get("expected") for diff in d.get("differences", []))
    ]

    if unexpected_diffs:
        return "differs"

    if dist_comparison.get("onlySource") or dist_comparison.get("onlyDestination"):
        return "differs"

    if issues:
        return "differs"

    return "synced"


def lambda_handler(event: dict, context: Any) -> dict:
    """
    Lambda entry point.

    Event structure (from Step Function):
    {
        "Project": "net",
        "Env": "de-ppd-compare",
        "Instance": "DE",
        "Environment": "ppd",
        "SourceState": { ... from DynamoDB ... },
        "DestinationState": { ... from DynamoDB ... }
    }

    Also supports old format for backward compatibility:
    {
        "Domain": "network",
        "Country": "DE",
        "Environment": "ppd",
        "LegacyState": { ... },
        "NHState": { ... }
    }

    Returns:
    {
        "statusCode": 200,
        "payload": {
            "status": "synced | differs",
            "summary": {...},
            "distributionsComparison": {...},
            "migrationStatus": {...},
            "originsComparison": {...},
            "wafComparison": {...},
            "certificateComparison": {...},
            "issues": [...],
            "timestamp": "ISO8601"
        }
    }
    """
    logger.info(f"Event keys: {list(event.keys())}")

    # Extract parameters - support both old and new format
    project = event.get("Project", event.get("Domain", "network"))
    env = event.get("Env", event.get("Target", ""))
    instance = event.get("Instance", event.get("Country", ""))
    environment = event.get("Environment", "")

    # Parse states from DynamoDB format - support both old (Legacy/NH) and new (Source/Destination)
    source_state = event.get("SourceState") or event.get("LegacyState", {})
    dest_state = event.get("DestinationState") or event.get("NHState", {})

    source_payload = extract_payload(source_state)
    dest_payload = extract_payload(dest_state)

    issues = []

    # Check if states exist
    if not source_payload:
        issues.append("Source state not found in DynamoDB")
    if not dest_payload:
        issues.append("Destination state not found in DynamoDB")

    if not source_payload and not dest_payload:
        return {
            "statusCode": 200,
            "payload": {
                "status": "error",
                "summary": {
                    "sourceFound": False,
                    "destinationFound": False,
                    "distributionCount": "unknown"
                },
                "issues": issues,
                "timestamp": datetime.now(timezone.utc).isoformat()
            }
        }

    # Extract distributions
    source_dists = source_payload.get("distributions", []) if source_payload else []
    dest_dists = dest_payload.get("distributions", []) if dest_payload else []

    logger.info(f"Comparing {len(source_dists)} source vs {len(dest_dists)} destination distributions")

    # Perform comparisons
    dist_comparison = compare_distributions(source_dists, dest_dists)
    migration_status = compare_migration_status(source_dists, dest_dists)
    origins_comparison = compare_origins(source_dists, dest_dists)
    waf_comparison = compare_waf_config(source_dists, dest_dists)
    cert_comparison = compare_certificates(source_dists, dest_dists)

    # Build summary
    summary = {
        "sourceFound": source_payload is not None,
        "destinationFound": dest_payload is not None,
        "distributionCount": "synced" if len(source_dists) == len(dest_dists) else "differs",
        "sourceCount": len(source_dists),
        "destinationCount": len(dest_dists),
        "sameConfig": len(dist_comparison.get("sameDistributions", [])),
        "differentConfig": len(dist_comparison.get("differentConfig", [])),
        "onlySource": len(dist_comparison.get("onlySource", [])),
        "onlyDestination": len(dist_comparison.get("onlyDestination", []))
    }

    # Summary status by category
    summary["origins"] = "synced" if origins_comparison.get("expected") else "differs"
    summary["wafConfig"] = "synced" if waf_comparison.get("expected") else "differs"
    summary["cachePolicy"] = "synced"  # Would need deeper analysis
    summary["migrationStatus"] = "synced" if not migration_status.get("migrating") else "in_progress"

    # Build full comparison result
    comparison = {
        "distributionsComparison": dist_comparison,
        "migrationStatus": migration_status,
        "originsComparison": origins_comparison,
        "wafComparison": waf_comparison,
        "certificateComparison": cert_comparison
    }

    # Determine overall status
    status = determine_comparison_status(comparison, issues)

    payload = {
        "status": status,
        "instance": instance,
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
