"""
Compare AWS Secrets Manager States (Source vs Destination)
============================================================
Compares AWS Secrets Manager states between Source and Destination environments.

This Lambda:
- Receives parsed states from DynamoDB
- Applies name mapping (Source path -> Destination path)
- Compares secrets by mapped names
- Identifies expected vs unexpected differences based on transformations
- Produces structured comparison report

Environment Variables:
- LOG_LEVEL: Logging level (default: INFO)

Security:
- Works only with hashes, never accesses actual secret values
"""

import json
import logging
import os
import re
from datetime import datetime, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)
logger.setLevel(os.environ.get("LOG_LEVEL", "INFO"))


# Default mapping configuration for secret paths
# Maps Source (Legacy) naming conventions to Destination (NH)
# Based on migrate-secrets/config.yaml patterns
#
# Two types of mappings:
# 1. App secrets: /rubix/{instance}-{env}/app/{app}/{secret} -> /digital/{env}/app/mro-{instance}/{app}/{secret}
# 2. DB secrets:  /rubix/{instance}-{env}/{eks-cluster}/{db} -> /digital/{env}/infra/databases/{rds-cluster}/{db}
DEFAULT_PATH_MAPPING = {
    # MI1 (EU) - note: stg uses mro-eu, ppd/prd use mro-mi1
    "mi1": {
        "source_patterns": {
            "stg": "/rubix/mi1-staging/app/",
            "ppd": "/rubix/mi1-preprod/app/",
            "prd": "/rubix/mi1-prod/app/",
        },
        "destination_patterns": {
            "stg": "/digital/stg/app/mro-eu/",
            "ppd": "/digital/ppd/app/mro-mi1/",
            "prd": "/digital/prd/app/mro-mi1/",
        },
        # DB secrets mapping (legacy_eks_cluster -> nh_rds_cluster)
        "db_source_patterns": {
            "stg": "/rubix/mi1-staging/mi1-staging-eks-cluster/",
            "ppd": "/rubix/mi1-preprod/mi1-preprod-eks-cluster/",
            "prd": "/rubix/mi1-prod/mi1-prod-eks-cluster/",
        },
        "db_destination_patterns": {
            "stg": "/digital/stg/infra/databases/rds-dig-stg-mro-eu/",
            "ppd": "/digital/ppd/infra/databases/rds-dig-ppd-mro-mi1/",
            "prd": "/digital/prd/infra/databases/rds-dig-prd-mro-mi1/",
        },
    },
    # MI2 (UK)
    "mi2": {
        "source_patterns": {
            "stg": "/rubix/mi2-staging/app/",
            "ppd": "/rubix/mi2-preprod/app/",
            "prd": "/rubix/mi2-prod/app/",
        },
        "destination_patterns": {
            "stg": "/digital/stg/app/mro-mi2/",
            "ppd": "/digital/ppd/app/mro-mi2/",
            "prd": "/digital/prd/app/mro-mi2/",
        },
        "db_source_patterns": {
            "stg": "/rubix/mi2-staging/mi2-staging-eks-cluster/",
            "ppd": "/rubix/mi2-preprod/mi2-preprod-eks-cluster/",
            "prd": "/rubix/mi2-prod/mi2-prod-eks-cluster/",
        },
        "db_destination_patterns": {
            "stg": "/digital/stg/infra/databases/rds-dig-stg-mro-mi2/",
            "ppd": "/digital/ppd/infra/databases/rds-dig-ppd-mro-mi2/",
            "prd": "/digital/prd/infra/databases/rds-dig-prd-mro-mi2/",
        },
    },
    # MI3 (DEZ)
    "mi3": {
        "source_patterns": {
            "stg": "/rubix/mi3-staging/app/",
            "ppd": "/rubix/mi3-preprod/app/",
            "prd": "/rubix/mi3-prod/app/",
        },
        "destination_patterns": {
            "stg": "/digital/stg/app/mro-mi3/",
            "ppd": "/digital/ppd/app/mro-mi3/",
            "prd": "/digital/prd/app/mro-mi3/",
        },
        "db_source_patterns": {
            "stg": "/rubix/mi3-staging/mi3-staging-eks-cluster/",
            "ppd": "/rubix/mi3-preprod/mi3-preprod-eks-cluster/",
            "prd": "/rubix/mi3-prod/mi3-prod-eks-cluster/",
        },
        "db_destination_patterns": {
            "stg": "/digital/stg/infra/databases/rds-dig-stg-mro-mi3/",
            "ppd": "/digital/ppd/infra/databases/rds-dig-ppd-mro-mi3/",
            "prd": "/digital/prd/infra/databases/rds-dig-prd-mro-mi3/",
        },
    },
    # FR (France)
    "fr": {
        "source_patterns": {
            "stg": "/rubix/fr-staging/app/",
            "ppd": "/rubix/fr-preprod/app/",
            "prd": "/rubix/fr-prod/app/",
        },
        "destination_patterns": {
            "stg": "/digital/stg/app/mro-fr/",
            "ppd": "/digital/ppd/app/mro-fr/",
            "prd": "/digital/prd/app/mro-fr/",
        },
        "db_source_patterns": {
            "stg": "/rubix/fr-staging/fr-staging-eks-cluster/",
            "ppd": "/rubix/fr-preprod/fr-preprod-eks-cluster/",
            "prd": "/rubix/fr-prod/fr-prod-eks-cluster/",
        },
        "db_destination_patterns": {
            "stg": "/digital/stg/infra/databases/rds-dig-stg-mro-fr/",
            "ppd": "/digital/ppd/infra/databases/rds-dig-ppd-mro-fr/",
            "prd": "/digital/prd/infra/databases/rds-dig-prd-mro-fr/",
        },
    },
    # BENE (Benelux)
    "bene": {
        "source_patterns": {
            "stg": "/rubix/bene-staging/app/",
            "ppd": "/rubix/bene-preprod/app/",
            "prd": "/rubix/bene-prod/app/",
        },
        "destination_patterns": {
            "stg": "/digital/stg/app/mro-bene/",
            "ppd": "/digital/ppd/app/mro-bene/",
            "prd": "/digital/prd/app/mro-bene/",
        },
        "db_source_patterns": {
            "stg": "/rubix/bene-staging/bene-staging-eks-cluster/",
            "ppd": "/rubix/bene-preprod/bene-preprod-eks-cluster/",
            "prd": "/rubix/bene-prod/bene-prod-eks-cluster/",
        },
        "db_destination_patterns": {
            "stg": "/digital/stg/infra/databases/rds-dig-stg-mro-bene/",
            "ppd": "/digital/ppd/infra/databases/rds-dig-ppd-mro-bene/",
            "prd": "/digital/prd/infra/databases/rds-dig-prd-mro-bene/",
        },
    },
    # IT (Italy)
    "it": {
        "source_patterns": {
            "stg": "/rubix/it-staging/app/",
            "ppd": "/rubix/it-preprod/app/",
            "prd": "/rubix/it-prod/app/",
        },
        "destination_patterns": {
            "stg": "/digital/stg/app/mro-it/",
            "ppd": "/digital/ppd/app/mro-it/",
            "prd": "/digital/prd/app/mro-it/",
        },
        "db_source_patterns": {
            "stg": "/rubix/it-staging/it-staging-eks-cluster/",
            "ppd": "/rubix/it-preprod/it-preprod-eks-cluster/",
            "prd": "/rubix/it-prod/it-prod-eks-cluster/",
        },
        "db_destination_patterns": {
            "stg": "/digital/stg/infra/databases/rds-dig-stg-mro-it/",
            "ppd": "/digital/ppd/infra/databases/rds-dig-ppd-mro-it/",
            "prd": "/digital/prd/infra/databases/rds-dig-prd-mro-it/",
        },
    },
}

# Expected transformations that cause value differences
# These are configured per secret type
# Based on migrate-secrets/config.yaml transformations
DEFAULT_EXPECTED_TRANSFORMATIONS = {
    # Hybris config-keys - IP replacements and additional keys
    # From migrate-secrets config.yaml:
    # - keys_with_ip_replacement: datalake.* (IPs replaced with hostnames)
    # - additional_keys: mail.from, ftp3.server, ftpdatalake.server, ftpdatalakesearch.server
    # - keys_to_remove: sb2.token.configuration
    "**/hybris/config-keys": {
        "expected_diff_keys": [
            "datalake.search.servers.configuration",
            "datalake.exports.servers.configuration",
            "datalake.exports.servers.new.configuration",
        ],
        "expected_new_keys": [
            "mail.from",
            "ftp3.server",
            "ftpdatalake.server",
            "ftpdatalakesearch.server",
        ],
        "expected_removed_keys": ["sb2.token.configuration"],
        "reason": "IP replacements and NH-specific overrides from migration",
    },
    # Database credentials - host changes between environments
    "**/db-credentials": {
        "expected_diff_keys": ["host", "endpoint"],
        "ignore_diff_keys": ["password"],  # Managed by rotation
        "reason": "Database endpoint changes between Source and Destination",
    },
    "**/rds-credentials": {
        "expected_diff_keys": ["host", "endpoint"],
        "ignore_diff_keys": ["password"],
        "reason": "RDS endpoint changes between Source and Destination",
    },
    "**/aurora-credentials": {
        "expected_diff_keys": ["host", "endpoint", "writer_endpoint", "reader_endpoint"],
        "ignore_diff_keys": ["password"],
        "reason": "Aurora endpoint changes between Source and Destination",
    },
    # Database secrets from migrate-secrets (prod_uk, prod_fr, etc.)
    # These have different endpoints between Legacy and NH
    "**/prod_*": {
        "expected_diff_keys": ["host", "host_ro", "endpoint", "dbClusterIdentifier", "jdbcUrl", "jdbcReadOnlyUrl", "masterarn"],
        "ignore_diff_keys": ["password"],
        "reason": "Database endpoint changes between Legacy and NH (migrate-secrets db_transforms)",
    },
    "**/smui_*": {
        "expected_diff_keys": ["host", "host_ro", "endpoint", "dbClusterIdentifier", "jdbcUrl", "jdbcReadOnlyUrl", "masterarn"],
        "ignore_diff_keys": ["password"],
        "reason": "SMUI database endpoint changes between Legacy and NH",
    },
    # Solr credentials
    "**/solr-credentials": {
        "expected_diff_keys": ["host", "endpoint", "url"],
        "ignore_diff_keys": ["password"],
        "reason": "Solr endpoint changes between Source and Destination",
    },
    "**/solr-*": {
        "expected_diff_keys": ["host", "endpoint", "url"],
        "ignore_diff_keys": ["password"],
        "reason": "Solr endpoint changes between Source and Destination",
    },
    # Redis/ElastiCache
    "**/redis-*": {
        "expected_diff_keys": ["host", "endpoint", "primary_endpoint", "reader_endpoint"],
        "ignore_diff_keys": ["auth_token"],
        "reason": "ElastiCache endpoint changes between Source and Destination",
    },
    "**/elasticache-*": {
        "expected_diff_keys": ["host", "endpoint", "primary_endpoint", "reader_endpoint"],
        "ignore_diff_keys": ["auth_token"],
        "reason": "ElastiCache endpoint changes between Source and Destination",
    },
    # OpenSearch/Elasticsearch
    "**/opensearch-*": {
        "expected_diff_keys": ["host", "endpoint"],
        "ignore_diff_keys": ["password", "master_password"],
        "reason": "OpenSearch endpoint changes between Source and Destination",
    },
    "**/elasticsearch-*": {
        "expected_diff_keys": ["host", "endpoint"],
        "ignore_diff_keys": ["password", "master_password"],
        "reason": "Elasticsearch endpoint changes between Source and Destination",
    },
    # Hybris DB specific
    "**/hybris/db-*": {
        "expected_diff_keys": ["host", "endpoint"],
        "ignore_diff_keys": ["password"],
        "reason": "Hybris DB endpoint changes between Source and Destination",
    },
}


def parse_dynamodb_item(raw_state: dict) -> Optional[dict]:
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


def extract_payload(state_result: dict) -> Optional[dict]:
    """Extract payload from state result structure."""
    if not state_result:
        return None

    # Handle parallel result format
    if "SourceState" in state_result:
        state_result = state_result["SourceState"]
    elif "DestinationState" in state_result:
        state_result = state_result["DestinationState"]

    # Try item/itemData first (from parallel branch result)
    item = state_result.get("item") or state_result.get("itemData")
    if item and item is not False:
        parsed = parse_dynamodb_item(item)
        if parsed and "payload" in parsed:
            payload = parsed.get("payload")
            if isinstance(payload, dict) and "M" in payload:
                return parse_dynamodb_item(payload)
            return payload

    # Check if found
    found = state_result.get("found")
    if found is False:
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

    # Try direct payload access
    if "payload" in state_result:
        return state_result["payload"]

    return None


def map_source_to_destination_name(
    source_name: str,
    instance: str,
    environment: str,
    custom_mapping: dict = None,
) -> str:
    """
    Map Source secret name to Destination naming convention.

    Supports two types of mappings:
    1. App secrets: /rubix/{instance}-{env}/app/* -> /digital/{env}/app/mro-{instance}/*
    2. DB secrets:  /rubix/{instance}-{env}/{eks-cluster}/* -> /digital/{env}/infra/databases/{rds-cluster}/*
    """
    mapping = custom_mapping or DEFAULT_PATH_MAPPING
    instance_lower = instance.lower()

    if instance_lower not in mapping:
        # No mapping configured, return as-is
        return source_name

    instance_mapping = mapping[instance_lower]

    # Try app secrets mapping first
    source_prefix = instance_mapping.get("source_patterns", {}).get(environment, "")
    destination_prefix = instance_mapping.get("destination_patterns", {}).get(environment, "")

    if source_prefix and destination_prefix and source_name.startswith(source_prefix):
        suffix = source_name[len(source_prefix):]
        return f"{destination_prefix}{suffix}"

    # Try DB secrets mapping
    db_source_prefix = instance_mapping.get("db_source_patterns", {}).get(environment, "")
    db_destination_prefix = instance_mapping.get("db_destination_patterns", {}).get(environment, "")

    if db_source_prefix and db_destination_prefix and source_name.startswith(db_source_prefix):
        suffix = source_name[len(db_source_prefix):]
        return f"{db_destination_prefix}{suffix}"

    return source_name


def match_pattern(name: str, pattern: str) -> bool:
    """Check if secret name matches pattern (supports ** and * wildcards)."""
    # Convert pattern to regex
    # Use placeholder for ** to avoid * in .* being replaced
    regex = pattern.replace("**", "\x00DOUBLESTAR\x00")
    regex = regex.replace("*", "[^/]*")
    regex = regex.replace("\x00DOUBLESTAR\x00", ".*")
    return bool(re.match(f"^{regex}$", name))


def get_transformation_config(secret_name: str, custom_config: dict = None) -> dict:
    """Get transformation config for a secret based on its name."""
    config = custom_config or DEFAULT_EXPECTED_TRANSFORMATIONS

    for pattern, transformation in config.items():
        if match_pattern(secret_name, pattern):
            return transformation

    return {}


def compare_single_secret(
    source_secret: dict,
    destination_secret: dict,
    transformation_config: dict,
) -> dict:
    """
    Compare two secrets and categorize differences.

    Returns:
        dict with comparison result
    """
    source_keys = set(source_secret.get("keysList", []))
    destination_keys = set(destination_secret.get("keysList", []))
    source_hashes = source_secret.get("valueHashes", {})
    destination_hashes = destination_secret.get("valueHashes", {})

    # Find key differences
    common_keys = source_keys & destination_keys
    only_source_keys = source_keys - destination_keys
    only_destination_keys = destination_keys - source_keys

    # Compare values for common keys
    same_keys = []
    diff_keys = []

    for key in common_keys:
        if source_hashes.get(key) == destination_hashes.get(key):
            same_keys.append(key)
        else:
            diff_keys.append(key)

    # Categorize differences based on transformation config
    expected_diff_keys = set(transformation_config.get("expected_diff_keys", []))
    ignore_diff_keys = set(transformation_config.get("ignore_diff_keys", []))
    expected_new_keys = set(transformation_config.get("expected_new_keys", []))
    expected_removed_keys = set(transformation_config.get("expected_removed_keys", []))

    # Categorize diff_keys
    expected_diffs = []
    ignored_diffs = []
    unexpected_diffs = []

    for key in diff_keys:
        if key in ignore_diff_keys:
            ignored_diffs.append(key)
        elif key in expected_diff_keys:
            expected_diffs.append(key)
        else:
            unexpected_diffs.append(key)

    # Categorize only_source
    expected_only_source = []
    unexpected_only_source = []

    for key in only_source_keys:
        if key in expected_removed_keys:
            expected_only_source.append(key)
        else:
            unexpected_only_source.append(key)

    # Categorize only_destination
    expected_only_destination = []
    unexpected_only_destination = []

    for key in only_destination_keys:
        if key in expected_new_keys:
            expected_only_destination.append(key)
        else:
            unexpected_only_destination.append(key)

    # Determine status
    has_unexpected = unexpected_diffs or unexpected_only_source or unexpected_only_destination
    has_expected_diff = expected_diffs or expected_only_source or expected_only_destination or ignored_diffs

    if has_unexpected:
        status = "differs_unexpected"
    elif has_expected_diff:
        status = "differs_expected"
    else:
        status = "synced"

    return {
        "status": status,
        "keysComparison": {
            "sameKeys": sorted(same_keys),
            "expectedDiffKeys": sorted(expected_diffs),
            "ignoredDiffKeys": sorted(ignored_diffs),
            "unexpectedDiffKeys": sorted(unexpected_diffs),
            "expectedOnlySource": sorted(expected_only_source),
            "unexpectedOnlySource": sorted(unexpected_only_source),
            "expectedOnlyDestination": sorted(expected_only_destination),
            "unexpectedOnlyDestination": sorted(unexpected_only_destination),
        },
        "transformationApplied": transformation_config.get("reason"),
    }


def compare_secrets(
    source_secrets: dict,
    destination_secrets: dict,
    instance: str,
    environment: str,
    path_mapping: dict = None,
    transformation_config: dict = None,
) -> dict:
    """
    Compare all secrets between Source and Destination.

    Returns structured comparison with categories:
    - synced: Same config
    - differs_expected: Only expected differences
    - differs_unexpected: Has unexpected differences
    - only_source_expected: Only in Source (expected removal)
    - only_source_unexpected: Only in Source (should be migrated)
    - only_destination_expected: Only in Destination (new feature)
    - only_destination_unexpected: Only in Destination (unexpected)
    """
    synced = []
    differs_expected = []
    differs_unexpected = []
    only_source_expected = []
    only_source_unexpected = []
    only_destination_expected = []
    only_destination_unexpected = []

    # Build Destination lookup by mapped name
    source_names = set(source_secrets.keys())
    destination_names = set(destination_secrets.keys())

    # Map source names to destination names
    source_to_destination_map = {}
    for source_name in source_names:
        destination_name = map_source_to_destination_name(source_name, instance, environment, path_mapping)
        source_to_destination_map[source_name] = destination_name

    # Reverse map for finding unmatched Destination secrets
    destination_to_source_map = {v: k for k, v in source_to_destination_map.items()}

    # Compare mapped secrets
    for source_name, source_secret in source_secrets.items():
        destination_name = source_to_destination_map[source_name]

        if destination_name in destination_secrets:
            destination_secret = destination_secrets[destination_name]

            # Get transformation config for this secret
            transform_config = get_transformation_config(source_name, transformation_config)

            # Compare
            comparison = compare_single_secret(source_secret, destination_secret, transform_config)

            result = {
                "sourceSecret": source_name,
                "destinationSecret": destination_name,
                **comparison,
            }

            if comparison["status"] == "synced":
                synced.append(result)
            elif comparison["status"] == "differs_expected":
                differs_expected.append(result)
            else:
                differs_unexpected.append(result)
        else:
            # Secret only in Source
            only_source_unexpected.append({
                "secret": source_name,
                "expectedDestinationName": destination_name,
                "reason": "Secret not migrated to Destination",
            })

    # Find secrets only in Destination
    matched_destination_names = set(source_to_destination_map.values())
    for destination_name in destination_names:
        if destination_name not in matched_destination_names:
            # Check if there's a pattern match that suggests this is expected
            only_destination_unexpected.append({
                "secret": destination_name,
                "reason": "Secret exists only in Destination (no Source equivalent)",
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


def identify_issues(comparison: dict, source_summary: dict, destination_summary: dict) -> list:
    """Identify issues that need attention."""
    issues = []

    # Check for unexpected differences
    if comparison.get("differs_unexpected"):
        count = len(comparison["differs_unexpected"])
        issues.append({
            "severity": "critical",
            "issue": "UnexpectedDifferences",
            "message": f"{count} secrets have unexpected value differences",
            "secrets": [d["sourceSecret"] for d in comparison["differs_unexpected"][:5]],
        })

    # Check for missing secrets in Destination
    if comparison.get("only_source_unexpected"):
        count = len(comparison["only_source_unexpected"])
        issues.append({
            "severity": "warning",
            "issue": "MissingInDestination",
            "message": f"{count} secrets exist in Source but not in Destination",
            "secrets": [d["secret"] for d in comparison["only_source_unexpected"][:5]],
        })

    # Check for unexpected secrets in Destination
    if comparison.get("only_destination_unexpected"):
        count = len(comparison["only_destination_unexpected"])
        issues.append({
            "severity": "info",
            "issue": "OnlyInDestination",
            "message": f"{count} secrets exist only in Destination (no Source equivalent)",
            "secrets": [d["secret"] for d in comparison["only_destination_unexpected"][:5]],
        })

    # Check rotation status
    source_rotation = source_summary.get("rotationEnabled", 0)
    destination_rotation = destination_summary.get("rotationEnabled", 0)
    if source_rotation > 0 and destination_rotation < source_rotation:
        issues.append({
            "severity": "warning",
            "issue": "RotationMismatch",
            "message": f"Destination has fewer secrets with rotation enabled ({destination_rotation}) than Source ({source_rotation})",
        })

    # Check for errors
    source_errors = source_summary.get("withErrors", 0)
    destination_errors = destination_summary.get("withErrors", 0)
    if source_errors > 0 or destination_errors > 0:
        issues.append({
            "severity": "warning",
            "issue": "AccessErrors",
            "message": f"Some secrets could not be accessed: Source={source_errors}, Destination={destination_errors}",
        })

    return issues


def determine_comparison_status(comparison: dict, issues: list) -> str:
    """Determine overall comparison status."""
    # Check for critical issues
    critical_issues = [i for i in issues if i.get("severity") == "critical"]
    if critical_issues:
        return "critical"

    if comparison.get("differs_unexpected"):
        return "differs"
    if comparison.get("only_source_unexpected"):
        return "differs"

    # Warning issues don't change status to differs
    warning_issues = [i for i in issues if i.get("severity") == "warning"]
    if warning_issues:
        if comparison.get("differs_expected"):
            return "synced_with_expected_diffs"
        return "synced_with_warnings"

    if comparison.get("differs_expected"):
        return "synced_with_expected_diffs"

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
        "PathMapping": { ... optional custom mapping ... },
        "TransformationConfig": { ... optional custom config ... }
    }

    Returns:
    {
        "statusCode": 200,
        "payload": {
            "status": "synced | differs | synced_with_expected_diffs | critical",
            "instance": "MI1",
            "environment": "ppd",
            "summary": {
                "totalSecrets": 35,
                "synced": 28,
                "differs_expected": 5,
                "differs_unexpected": 1,
                "only_source_expected": 0,
                "only_source_unexpected": 1,
                "only_destination_expected": 1,
                "only_destination_unexpected": 0
            },
            "details": { ... },
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
    path_mapping = event.get("PathMapping")
    transformation_config = event.get("TransformationConfig")

    # Parse states from DynamoDB format
    source_state = event.get("source_state", {})
    destination_state = event.get("destination_state", {})

    source_payload = extract_payload(source_state)
    destination_payload = extract_payload(destination_state)

    issues = []

    # Check if states exist
    if not source_payload:
        issues.append({
            "severity": "critical",
            "issue": "SourceStateMissing",
            "message": "Source state not found in DynamoDB",
        })
    if not destination_payload:
        issues.append({
            "severity": "critical",
            "issue": "DestinationStateMissing",
            "message": "Destination state not found in DynamoDB",
        })

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
                },
                "issues": issues,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
        }

    # Extract secrets
    source_secrets = source_payload.get("secrets", {}) if source_payload else {}
    destination_secrets = destination_payload.get("secrets", {}) if destination_payload else {}
    source_summary = source_payload.get("summary", {}) if source_payload else {}
    destination_summary = destination_payload.get("summary", {}) if destination_payload else {}

    logger.info(f"Comparing {len(source_secrets)} source vs {len(destination_secrets)} destination secrets")

    # Perform comparison
    comparison = compare_secrets(
        source_secrets=source_secrets,
        destination_secrets=destination_secrets,
        instance=instance,
        environment=environment,
        path_mapping=path_mapping,
        transformation_config=transformation_config,
    )

    # Identify issues
    comparison_issues = identify_issues(comparison, source_summary, destination_summary)
    issues.extend(comparison_issues)

    # Build summary
    summary = {
        "sourceFound": source_payload is not None,
        "destinationFound": destination_payload is not None,
        "sourceCount": len(source_secrets),
        "destinationCount": len(destination_secrets),
        "synced": len(comparison.get("synced", [])),
        "differs_expected": len(comparison.get("differs_expected", [])),
        "differs_unexpected": len(comparison.get("differs_unexpected", [])),
        "only_source_expected": len(comparison.get("only_source_expected", [])),
        "only_source_unexpected": len(comparison.get("only_source_unexpected", [])),
        "only_destination_expected": len(comparison.get("only_destination_expected", [])),
        "only_destination_unexpected": len(comparison.get("only_destination_unexpected", [])),
    }

    # Determine overall status
    status = determine_comparison_status(comparison, issues)

    payload = {
        "status": status,
        "instance": instance,
        "environment": environment,
        "summary": summary,
        "details": comparison,
        "issues": issues,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    return {
        "statusCode": 200,
        "payload": payload,
    }
