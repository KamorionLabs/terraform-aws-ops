"""
Compare Secrets Lambda
=======================
Compares K8s Secrets state between Source and Destination environments.
Supports both native Secrets and ExternalSecrets CRD modes.
Generates a detailed comparison report for migration validation.

Called by Step Function k8s-secrets-compare after fetching both states.

Environment Variables:
- LOG_LEVEL: Logging level (default: INFO)
"""

import logging
import os
from datetime import datetime, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)
logger.setLevel(os.environ.get("LOG_LEVEL", "INFO"))

# Expected differences between Source and Destination
EXPECTED_PROVIDER_MIGRATION = {
    "vault": "aws-secrets-manager",  # Vault -> AWS SM
    "vault-store": "aws-secrets-manager",
}


def extract_payload_from_dynamo(dynamo_item: dict) -> Optional[dict]:
    """Extract payload from DynamoDB item structure."""
    if not dynamo_item:
        return None

    # Handle Step Function nested structure: {SourceState: {item, itemData, source}}
    # First check if we have the nested state structure
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

    # If it's DynamoDB format with type descriptors at the item level
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


def compare_secret_counts(source: dict, destination: dict) -> dict:
    """Compare secret counts between environments."""
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


def compare_sync_status(source: dict, destination: dict) -> dict:
    """Compare sync status distribution between environments."""
    source_summary = source.get("summary", {})
    destination_summary = destination.get("summary", {})

    # Handle both native mode (byType/byCategory) and external mode (synced/failed/stale)
    source_stats = {
        "synced": source_summary.get("synced", source_summary.get("total", 0)),
        "failed": source_summary.get("failed", 0),
        "stale": source_summary.get("stale", 0),
    }

    destination_stats = {
        "synced": destination_summary.get("synced", destination_summary.get("total", 0)),
        "failed": destination_summary.get("failed", 0),
        "stale": destination_summary.get("stale", 0),
    }

    # Determine status
    status = "synced"
    if source_stats != destination_stats:
        status = "differs"

    return {
        "source": source_stats,
        "destination": destination_stats,
        "status": status,
    }


def compare_secrets(source_secrets: list, destination_secrets: list) -> dict:
    """Compare individual secrets between environments."""
    # Build maps by secret name
    source_map = {s["name"]: s for s in source_secrets}
    destination_map = {s["name"]: s for s in destination_secrets}

    all_names = set(source_map.keys()) | set(destination_map.keys())

    same_secrets = []
    different_config = []
    only_source = []
    only_destination = []

    for name in sorted(all_names):
        source_secret = source_map.get(name)
        destination_secret = destination_map.get(name)

        if source_secret and destination_secret:
            # Both exist - compare configuration
            # For native mode, compare type and dataKeys
            # For external mode, compare secretStore
            source_store = source_secret.get("secretStore", source_secret.get("type", ""))
            destination_store = destination_secret.get("secretStore", destination_secret.get("type", ""))

            # Check if this is an expected provider migration
            is_expected_migration = (
                source_store in EXPECTED_PROVIDER_MIGRATION
                and EXPECTED_PROVIDER_MIGRATION.get(source_store) == destination_store
            )

            if source_store == destination_store:
                same_secrets.append(name)
            else:
                different_config.append({
                    "secret": name,
                    "source": {
                        "provider": source_store,
                        "status": source_secret.get("status"),
                    },
                    "destination": {
                        "provider": destination_store,
                        "status": destination_secret.get("status"),
                    },
                    "expected": is_expected_migration,
                    "reason": (
                        "Expected provider migration"
                        if is_expected_migration
                        else "Provider differs"
                    ),
                })

        elif source_secret and not destination_secret:
            only_source.append(name)

        else:  # destination_secret and not source_secret
            only_destination.append(name)

    return {
        "sameSecrets": same_secrets,
        "differentConfig": different_config,
        "onlySource": only_source,
        "onlyDestination": only_destination,
    }


def compare_stores(source_stores: list, destination_stores: list) -> dict:
    """Compare secret stores between environments."""
    source_names = sorted([s["name"] for s in source_stores]) if source_stores else []
    destination_names = sorted([s["name"] for s in destination_stores]) if destination_stores else []

    # Check for expected differences
    source_has_vault = any("vault" in name.lower() for name in source_names)
    destination_has_aws = any("aws" in name.lower() for name in destination_names)

    expected = source_has_vault and destination_has_aws

    return {
        "source": source_names,
        "destination": destination_names,
        "expected": expected,
        "reason": (
            "Destination uses native AWS secret stores"
            if expected
            else "Store configuration differs" if source_names != destination_names else "Stores match"
        ),
    }


def identify_issues(
    source: dict,
    destination: dict,
    secret_count: dict,
    sync_status: dict,
    secrets_comparison: dict,
) -> list:
    """Identify comparison issues that need attention."""
    issues = []

    # Check secret count difference
    if secret_count["status"] == "differs":
        if secret_count["difference"] < 0:
            issues.append({
                "severity": "warning",
                "issue": "SecretCountMismatch",
                "message": f"Destination has fewer secrets than Source ({secret_count['destination']} vs {secret_count['source']})",
            })
        else:
            issues.append({
                "severity": "info",
                "issue": "SecretCountDifference",
                "message": f"Destination has more secrets than Source ({secret_count['destination']} vs {secret_count['source']})",
            })

    # Check for secrets only in Source (may be missing in Destination)
    only_source = secrets_comparison.get("onlySource", [])
    if only_source:
        issues.append({
            "severity": "warning",
            "issue": "SecretsOnlyInSource",
            "message": f"Secrets present in Source but not in Destination: {', '.join(only_source[:5])}{'...' if len(only_source) > 5 else ''}",
        })

    # Check sync status differences
    source_sync = sync_status.get("source", {})
    destination_sync = sync_status.get("destination", {})

    if source_sync.get("failed", 0) == 0 and destination_sync.get("failed", 0) > 0:
        issues.append({
            "severity": "warning",
            "issue": "DestinationHasFailedSecrets",
            "message": f"Destination has {destination_sync['failed']} failed secrets while Source has none",
        })

    # Check unexpected provider differences
    different_config = secrets_comparison.get("differentConfig", [])
    unexpected_differences = [d for d in different_config if not d.get("expected", False)]
    if unexpected_differences:
        issues.append({
            "severity": "warning",
            "issue": "UnexpectedProviderDifference",
            "message": f"{len(unexpected_differences)} secrets have unexpected provider differences",
        })

    return issues


def lambda_handler(event: dict, context: Any) -> dict:
    """
    Compare secrets state between Source and Destination environments.

    Event structure (from Step Function):
    {
        "domain": "mro",
        "instance": "MI1",
        "environment": "ppd",
        "source_state": {
            "SourceState": {
                "source": "source",
                "item": {...},
                "itemData": {...}
            },
            ...
        },
        "destination_state": {
            "DestinationState": {
                "source": "destination",
                "item": {...},
                "itemData": {...}
            },
            ...
        }
    }

    Returns comparison payload as per spec.
    """
    logger.info(
        f"Comparing secrets for {event.get('domain')}/{event.get('instance')}-{event.get('environment')}"
    )

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
                "secretCount": "source_missing",
                "syncStatus": "source_missing",
                "providers": "source_missing",
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
                "secretCount": "destination_missing",
                "syncStatus": "destination_missing",
                "providers": "destination_missing",
            },
            "message": "Destination state is missing, cannot compare",
            "source_only": {
                "status": source_payload.get("status"),
                "healthy": source_payload.get("healthy"),
                "summary": source_payload.get("summary"),
            },
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    # Determine mode (native or external) and get secrets list
    source_mode = source_payload.get("summary", {}).get("mode", "external")
    destination_mode = destination_payload.get("summary", {}).get("mode", "external")

    if source_mode == "native":
        source_secrets = source_payload.get("secrets", [])
    else:
        source_secrets = source_payload.get("externalSecrets", [])

    if destination_mode == "native":
        destination_secrets = destination_payload.get("secrets", [])
    else:
        destination_secrets = destination_payload.get("externalSecrets", [])

    source_stores = source_payload.get("secretStores", [])
    destination_stores = destination_payload.get("secretStores", [])

    secret_count = compare_secret_counts(source_payload, destination_payload)
    sync_status = compare_sync_status(source_payload, destination_payload)
    secrets_comparison = compare_secrets(source_secrets, destination_secrets)
    store_comparison = compare_stores(source_stores, destination_stores)

    # Identify issues
    issues = identify_issues(
        source_payload,
        destination_payload,
        secret_count,
        sync_status,
        secrets_comparison,
    )

    # Determine overall status
    overall_status = "synced"
    if (
        secret_count["status"] == "differs"
        or sync_status["status"] == "differs"
        or secrets_comparison.get("onlySource")
    ):
        overall_status = "differs"

    timestamp = datetime.now(timezone.utc).isoformat()

    result = {
        "status": overall_status,
        "summary": {
            "secretCount": secret_count["status"],
            "syncStatus": sync_status["status"],
            "providers": (
                "synced"
                if not secrets_comparison.get("differentConfig")
                or all(
                    d.get("expected", False)
                    for d in secrets_comparison.get("differentConfig", [])
                )
                else "differs"
            ),
        },
        "secretsComparison": secrets_comparison,
        "syncStatusComparison": sync_status,
        "storeComparison": store_comparison,
        "issues": issues,
        "sourceTimestamp": source_payload.get("timestamp"),
        "destinationTimestamp": destination_payload.get("timestamp"),
        "timestamp": timestamp,
    }

    logger.info(f"Comparison complete: status={overall_status}, issues={len(issues)}")

    return result
