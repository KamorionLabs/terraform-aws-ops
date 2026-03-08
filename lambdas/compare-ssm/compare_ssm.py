"""
Compare SSM Parameters (Source vs Destination)
===============================================
Compares SSM Parameter Store states between Source and Destination environments.

This Lambda:
- Receives parsed states from DynamoDB
- Applies path transformations to map Source -> Destination parameters
- Compares parameter values by hash
- Identifies expected vs unexpected differences
- Produces structured comparison report

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


# Expected value transformations between Source and Destination
# These are patterns that should differ and are NOT considered drift
EXPECTED_TRANSFORMATIONS = {
    # RDS endpoints
    "rds": {
        "patterns": [
            (r"rubix-nonprod-aurora", r"rubix-dig-\w+-aurora"),
            (r"rubix-prod-aurora", r"rubix-dig-prd-aurora"),
        ],
        "reason": "RDS cluster naming convention changed",
    },
    # EKS clusters
    "eks": {
        "patterns": [
            (r"rubix-nonprod", r"rubix-dig-(stg|ppd)-webshop"),
            (r"rubix-prod", r"rubix-dig-prd-webshop"),
        ],
        "reason": "EKS cluster naming convention changed",
    },
    # Internal hostnames
    "hostname": {
        "patterns": [
            (r"\.rubix-nonprod\.internal", r"\.rubix-dig-(stg|ppd)\.internal"),
            (r"\.rubix-prod\.internal", r"\.rubix-dig-prd\.internal"),
        ],
        "reason": "Internal DNS domain changed",
    },
    # AWS Account IDs
    "account": {
        "patterns": [
            (r"073290922796", r"(281127105461|287223952330|366483377530)"),
        ],
        "reason": "AWS account ID changed (Source -> Destination)",
    },
    # OIDC provider ARNs
    "oidc": {
        "patterns": [
            (r"oidc\.eks\.eu-\w+-\d\.amazonaws\.com/id/\w+", r"oidc\.eks\.eu-\w+-\d\.amazonaws\.com/id/\w+"),
        ],
        "reason": "OIDC provider ID changed with new cluster",
    },
}

# Keys that are expected to only exist in Destination (new features)
EXPECTED_NEW_KEYS = [
    "eks/oidc-provider-arn",
    "irsa/",
    "karpenter/",
    "pod-identity/",
    "ebs-csi/",
    "efs-csi/",
]

# Keys that are expected to be removed in Destination (deprecated)
EXPECTED_REMOVED_KEYS = [
    "legacy/",
    "deprecated/",
    "old-",
]


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


def extract_payload_from_dynamo(dynamo_item: dict) -> Optional[dict]:
    """Extract payload from DynamoDB item structure."""
    if not dynamo_item:
        return None

    # Handle Step Function nested structure: {SourceState: {item, itemData, source}}
    if "SourceState" in dynamo_item:
        dynamo_item = dynamo_item.get("SourceState", {})
    elif "DestinationState" in dynamo_item:
        dynamo_item = dynamo_item.get("DestinationState", {})

    # Handle nested DynamoDB format (with type descriptors)
    item = dynamo_item.get("item") or dynamo_item.get("itemData") or dynamo_item.get("found") or dynamo_item

    if not item:
        return None

    # If it's already a dict with 'payload' key
    if "payload" in item and isinstance(item["payload"], dict):
        # Check if it's DynamoDB format with type descriptors
        if "M" in item["payload"]:
            try:
                return deserialize_dynamo_value(item["payload"])
            except Exception:
                pass
        return item["payload"]

    return item.get("payload")


def is_expected_transformation(source_hash: str, destination_hash: str, key: str) -> tuple[bool, str]:
    """
    Check if a value difference is expected due to known transformations.

    Note: Since we only have hashes, we can't verify the actual transformation.
    We assume differences in certain key patterns are expected.

    Returns:
        Tuple of (is_expected: bool, reason: str)
    """
    key_lower = key.lower()

    # Check if key matches patterns where transformations are expected
    for transform_type, config in EXPECTED_TRANSFORMATIONS.items():
        # Check if key suggests this type of value
        if transform_type in key_lower:
            return True, config["reason"]

        # Special patterns
        if transform_type == "rds" and any(p in key_lower for p in ["endpoint", "host", "aurora"]):
            return True, config["reason"]
        if transform_type == "eks" and any(p in key_lower for p in ["cluster", "eks"]):
            return True, config["reason"]
        if transform_type == "account" and any(p in key_lower for p in ["arn", "account"]):
            return True, config["reason"]
        if transform_type == "oidc" and "oidc" in key_lower:
            return True, config["reason"]

    return False, ""


def is_expected_new_key(key: str) -> bool:
    """Check if a key is expected to only exist in Destination."""
    key_lower = key.lower()
    return any(pattern in key_lower for pattern in EXPECTED_NEW_KEYS)


def is_expected_removed_key(key: str) -> bool:
    """Check if a key is expected to be removed in Destination."""
    key_lower = key.lower()
    return any(pattern in key_lower for pattern in EXPECTED_REMOVED_KEYS)


def build_key_mapping(
    source_params: dict,
    destination_params: dict,
    mapping_config: dict = None,
) -> dict:
    """
    Build mapping between Source and Destination parameter keys.

    Uses normalizedKey or relativeKey from the parameter metadata.

    Returns:
        Dict mapping source keys to destination keys
    """
    mapping = {}

    # Build lookup by normalized key for Destination
    destination_by_normalized = {}
    destination_by_relative = {}
    for dest_key, dest_param in destination_params.items():
        if isinstance(dest_param, dict):
            normalized = dest_param.get("normalizedKey", dest_key)
            relative = dest_param.get("relativeKey", "")
            destination_by_normalized[normalized] = dest_key
            if relative:
                destination_by_relative[relative] = dest_key

    # Map source keys to destination keys
    for source_key, source_param in source_params.items():
        if isinstance(source_param, dict):
            normalized = source_param.get("normalizedKey", source_key)
            relative = source_param.get("relativeKey", "")

            # Try normalized key first
            if normalized in destination_by_normalized:
                mapping[source_key] = destination_by_normalized[normalized]
            # Then try relative key
            elif relative and relative in destination_by_relative:
                mapping[source_key] = destination_by_relative[relative]

    return mapping


def compare_parameters(
    source_params: dict,
    destination_params: dict,
    mapping_config: dict = None,
) -> dict:
    """
    Compare parameters between Source and Destination.

    Returns structured comparison with categories:
    - synced: Parameters with same values
    - differs_expected: Parameters with expected differences
    - differs_unexpected: Parameters with unexpected differences (drift)
    - only_source_expected: Parameters removed as expected
    - only_source_unexpected: Parameters missing (should be migrated)
    - only_destination_expected: New parameters as expected
    - only_destination_unexpected: Unexpected new parameters
    """
    # Build key mapping
    key_mapping = build_key_mapping(source_params, destination_params, mapping_config)

    synced = []
    differs_expected = []
    differs_unexpected = []
    only_source_expected = []
    only_source_unexpected = []
    only_destination_expected = []
    only_destination_unexpected = []

    # Track which destination keys have been matched
    matched_destination_keys = set()

    # Compare source parameters
    for source_key, source_param in source_params.items():
        if not isinstance(source_param, dict):
            continue

        source_hash = source_param.get("valueHash", "")
        relative_key = source_param.get("relativeKey", source_key)

        if source_key in key_mapping:
            # Found matching destination parameter
            dest_key = key_mapping[source_key]
            matched_destination_keys.add(dest_key)

            dest_param = destination_params.get(dest_key, {})
            if isinstance(dest_param, dict):
                dest_hash = dest_param.get("valueHash", "")

                if source_hash == dest_hash:
                    # Same value
                    synced.append({
                        "sourceKey": source_key,
                        "destinationKey": dest_key,
                        "relativeKey": relative_key,
                    })
                else:
                    # Different value - check if expected
                    is_expected, reason = is_expected_transformation(
                        source_hash, dest_hash, relative_key
                    )
                    diff_entry = {
                        "sourceKey": source_key,
                        "destinationKey": dest_key,
                        "relativeKey": relative_key,
                        "sourceValueHash": source_hash,
                        "destinationValueHash": dest_hash,
                    }
                    if is_expected:
                        diff_entry["reason"] = reason
                        differs_expected.append(diff_entry)
                    else:
                        diff_entry["reason"] = "drift - values differ without expected transformation"
                        differs_unexpected.append(diff_entry)
        else:
            # No matching destination parameter
            if is_expected_removed_key(relative_key):
                only_source_expected.append({
                    "key": source_key,
                    "relativeKey": relative_key,
                    "reason": "Key marked as deprecated/legacy",
                })
            else:
                only_source_unexpected.append({
                    "key": source_key,
                    "relativeKey": relative_key,
                    "reason": "Not migrated to Destination",
                })

    # Find destination parameters not in Source
    for dest_key, dest_param in destination_params.items():
        if not isinstance(dest_param, dict):
            continue
        if dest_key in matched_destination_keys:
            continue

        relative_key = dest_param.get("relativeKey", dest_key)

        if is_expected_new_key(relative_key):
            only_destination_expected.append({
                "key": dest_key,
                "relativeKey": relative_key,
                "reason": "New Destination-specific parameter",
            })
        else:
            only_destination_unexpected.append({
                "key": dest_key,
                "relativeKey": relative_key,
                "reason": "Parameter exists only in Destination",
            })

    return {
        "synced": synced,
        "differs_expected": differs_expected,
        "differs_unexpected": differs_unexpected,
        "only_source_expected": only_source_expected,
        "only_source_unexpected": only_source_unexpected,
        "only_destination_expected": only_destination_expected,
        "only_destination_unexpected": only_destination_unexpected,
    }


def determine_status(comparison: dict, issues: list) -> str:
    """Determine overall comparison status."""
    # If there are unexpected differences or missing parameters, status is differs
    if comparison.get("differs_unexpected"):
        return "differs"
    if comparison.get("only_source_unexpected"):
        return "differs"
    if comparison.get("only_destination_unexpected"):
        return "differs"

    if issues:
        return "differs"

    # If only expected differences and all synced/expected
    if comparison.get("synced") or comparison.get("differs_expected"):
        return "synced"

    # Edge cases
    if comparison.get("only_source_expected") and not comparison.get("synced"):
        return "only_source"
    if comparison.get("only_destination_expected") and not comparison.get("synced"):
        return "only_destination"

    return "synced"


def lambda_handler(event: dict, context: Any) -> dict:
    """
    Lambda entry point.

    Event structure (from Step Function):
    {
        "domain": "config",
        "instance": "MI1",
        "environment": "ppd",
        "source_state": { ... from DynamoDB ... },
        "destination_state": { ... from DynamoDB ... },
        "mapping_config": { ... optional transformations ... }
    }

    Returns:
    {
        "statusCode": 200,
        "payload": {
            "status": "synced | differs | only_source | only_destination",
            "summary": {...},
            "details": {...},
            "issues": [...],
            "timestamp": "ISO8601"
        }
    }
    """
    logger.info(f"Event keys: {list(event.keys())}")

    # Extract parameters
    domain = event.get("domain", "config")
    instance = event.get("instance", "")
    environment = event.get("environment", "")
    mapping_config = event.get("mapping_config", {})

    # Parse states from DynamoDB format
    source_state = event.get("source_state", {})
    destination_state = event.get("destination_state", {})

    source_payload = extract_payload_from_dynamo(source_state)
    destination_payload = extract_payload_from_dynamo(destination_state)

    issues = []

    # Check if states exist
    if not source_payload:
        issues.append("Source state not found in DynamoDB")
    if not destination_payload:
        issues.append("Destination state not found in DynamoDB")

    if not source_payload and not destination_payload:
        return {
            "statusCode": 200,
            "payload": {
                "status": "error",
                "instance": instance,
                "environment": environment,
                "summary": {
                    "sourceFound": False,
                    "destinationFound": False,
                    "totalParameters": "unknown",
                },
                "issues": issues,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
        }

    # Extract parameters
    source_params = source_payload.get("parameters", {}) if source_payload else {}
    destination_params = destination_payload.get("parameters", {}) if destination_payload else {}

    logger.info(
        f"Comparing {len(source_params)} source vs {len(destination_params)} destination parameters"
    )

    # Perform comparison
    comparison = compare_parameters(source_params, destination_params, mapping_config)

    # Build summary
    summary = {
        "sourceFound": source_payload is not None,
        "destinationFound": destination_payload is not None,
        "sourceCount": len(source_params),
        "destinationCount": len(destination_params),
        "synced": len(comparison["synced"]),
        "differs_expected": len(comparison["differs_expected"]),
        "differs_unexpected": len(comparison["differs_unexpected"]),
        "only_source_expected": len(comparison["only_source_expected"]),
        "only_source_unexpected": len(comparison["only_source_unexpected"]),
        "only_destination_expected": len(comparison["only_destination_expected"]),
        "only_destination_unexpected": len(comparison["only_destination_unexpected"]),
    }

    # Determine status
    status = determine_status(comparison, issues)

    # Build details (limit to avoid large payloads)
    details = {
        "synced": [item["relativeKey"] for item in comparison["synced"][:50]],
        "expected_differences": comparison["differs_expected"][:20],
        "unexpected_differences": comparison["differs_unexpected"][:20],
        "only_source_expected": [
            item["relativeKey"] for item in comparison["only_source_expected"][:20]
        ],
        "only_source_unexpected": [
            item["relativeKey"] for item in comparison["only_source_unexpected"][:20]
        ],
        "only_destination_expected": [
            item["relativeKey"] for item in comparison["only_destination_expected"][:20]
        ],
        "only_destination_unexpected": [
            item["relativeKey"] for item in comparison["only_destination_unexpected"][:20]
        ],
    }

    # Add truncation notes
    if len(comparison["synced"]) > 50:
        details["synced_truncated"] = True
        details["synced_total"] = len(comparison["synced"])

    payload = {
        "status": status,
        "instance": instance,
        "environment": environment,
        "summary": summary,
        "details": details,
        "issues": issues,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    return {
        "statusCode": 200,
        "payload": payload,
    }
