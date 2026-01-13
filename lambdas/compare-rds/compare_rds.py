"""
Compare RDS/Aurora Cluster States (Source vs Destination)
==========================================================
Compares RDS/Aurora cluster configurations and parameter groups between
Source and Destination environments.

This Lambda:
- Receives parsed states from DynamoDB
- Compares cluster configuration
- Compares cluster parameter groups
- Compares instance parameter groups
- Categorizes differences as expected or unexpected
- Produces structured comparison report

Environment Variables:
- LOG_LEVEL: Logging level (default: INFO)
"""

import logging
import os
from datetime import datetime, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)
logger.setLevel(os.environ.get("LOG_LEVEL", "INFO"))


# Default expected differences configuration
# These parameters are commonly different between environments
DEFAULT_EXPECTED_DIFFERENCES = {
    "clusterParameters": {
        # Connection and performance parameters that scale with instance size
        "max_connections": {
            "reason": "Scaled based on instance size",
            "severity": "info",
        },
        "innodb_buffer_pool_size": {
            "reason": "Scaled based on instance memory",
            "severity": "info",
        },
        # Replication parameters
        "binlog_format": {
            "reason": "May differ based on replication requirements",
            "severity": "info",
        },
        "binlog_row_image": {
            "reason": "May differ based on replication requirements",
            "severity": "info",
        },
        # Logging parameters
        "slow_query_log": {
            "reason": "Logging configuration may vary by environment",
            "severity": "info",
        },
        "general_log": {
            "reason": "Logging configuration may vary by environment",
            "severity": "info",
        },
        "log_output": {
            "reason": "Logging destination may vary",
            "severity": "info",
        },
    },
    "instanceParameters": {
        "max_allowed_packet": {
            "reason": "May be increased for large queries",
            "severity": "info",
        },
        "performance_schema": {
            "reason": "Performance monitoring may vary",
            "severity": "info",
        },
        "innodb_print_all_deadlocks": {
            "reason": "Debugging settings may vary",
            "severity": "info",
        },
    },
    "clusterConfig": {
        "engineVersion": {
            "reason": "Version upgrade expected between environments",
            "severity": "info",
        },
        "dbClusterInstanceClass": {
            "reason": "Instance class may differ between environments",
            "severity": "info",
        },
        "endpoint": {
            "reason": "Endpoints are environment-specific",
            "severity": "info",
        },
        "readerEndpoint": {
            "reason": "Endpoints are environment-specific",
            "severity": "info",
        },
        "port": {
            "reason": "Port may be configured differently",
            "severity": "info",
        },
    },
}

# Cluster config keys to compare
CLUSTER_CONFIG_KEYS = [
    "engine",
    "engineVersion",
    "multiAZ",
    "storageEncrypted",
    "deletionProtection",
    "engineMode",
    "dbClusterInstanceClass",
    "iamDatabaseAuthenticationEnabled",
]


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
                return {k: parse_value(v) for k, v in val.items()}
        elif isinstance(val, list):
            return [parse_value(v) for v in val]
        return val

    return parse_value(raw_state)


def extract_payload(state_result: dict) -> Optional[dict]:
    """Extract payload from state result structure."""
    if not state_result:
        return None

    # Try itemData first (from parallel branch result)
    item = state_result.get("itemData")
    if item and item is not False:
        parsed = parse_dynamodb_item(item)
        if parsed and "payload" in parsed:
            payload = parsed.get("payload")
            if isinstance(payload, dict) and "M" in payload:
                return parse_dynamodb_item(payload)
            return payload

    # Check hasData flag
    if state_result.get("hasData") is False:
        return None

    return None


def merge_expected_differences(default: dict, override: dict) -> dict:
    """Merge override expected differences with defaults."""
    if not override:
        return default

    result = {}
    for category in ["clusterParameters", "instanceParameters", "clusterConfig"]:
        result[category] = {**default.get(category, {})}
        if category in override:
            result[category].update(override[category])

    return result


def compare_parameters(
    source_params: dict,
    dest_params: dict,
    expected_diffs: dict,
) -> dict:
    """
    Compare parameter groups between source and destination.

    Returns structured comparison with categories.
    """
    source_keys = set(source_params.keys())
    dest_keys = set(dest_params.keys())

    common_keys = source_keys & dest_keys
    only_source = source_keys - dest_keys
    only_dest = dest_keys - source_keys

    synced = []
    expected_differences = []
    unexpected_differences = []

    for key in sorted(common_keys):
        source_val = str(source_params[key])
        dest_val = str(dest_params[key])

        if source_val == dest_val:
            synced.append(key)
        elif key in expected_diffs:
            expected_differences.append({
                "parameter": key,
                "source": source_val,
                "destination": dest_val,
                "reason": expected_diffs[key].get("reason", "Expected difference"),
                "severity": expected_diffs[key].get("severity", "info"),
            })
        else:
            unexpected_differences.append({
                "parameter": key,
                "source": source_val,
                "destination": dest_val,
            })

    # Categorize only_source and only_dest
    only_source_expected = []
    only_source_unexpected = []
    only_dest_expected = []
    only_dest_unexpected = []

    for key in sorted(only_source):
        if key in expected_diffs:
            only_source_expected.append({
                "parameter": key,
                "value": str(source_params[key]),
                "reason": expected_diffs[key].get("reason", "Expected to be only in source"),
            })
        else:
            only_source_unexpected.append({
                "parameter": key,
                "value": str(source_params[key]),
            })

    for key in sorted(only_dest):
        if key in expected_diffs:
            only_dest_expected.append({
                "parameter": key,
                "value": str(dest_params[key]),
                "reason": expected_diffs[key].get("reason", "Expected to be only in destination"),
            })
        else:
            only_dest_unexpected.append({
                "parameter": key,
                "value": str(dest_params[key]),
            })

    # Determine status
    has_unexpected = unexpected_differences or only_source_unexpected or only_dest_unexpected
    has_expected = expected_differences or only_source_expected or only_dest_expected

    if has_unexpected:
        status = "differs"
    elif has_expected:
        status = "synced_with_expected_diffs"
    else:
        status = "synced"

    return {
        "status": status,
        "synced": synced,
        "expectedDifferences": expected_differences,
        "unexpectedDifferences": unexpected_differences,
        "onlySourceExpected": only_source_expected,
        "onlySourceUnexpected": only_source_unexpected,
        "onlyDestinationExpected": only_dest_expected,
        "onlyDestinationUnexpected": only_dest_unexpected,
        "summary": {
            "syncedCount": len(synced),
            "expectedDiffCount": len(expected_differences) + len(only_source_expected) + len(only_dest_expected),
            "unexpectedDiffCount": len(unexpected_differences) + len(only_source_unexpected) + len(only_dest_unexpected),
        },
    }


def compare_cluster_config(
    source_cluster: dict,
    dest_cluster: dict,
    expected_diffs: dict,
) -> dict:
    """Compare cluster configuration values."""
    synced = []
    expected_differences = []
    unexpected_differences = []

    for key in CLUSTER_CONFIG_KEYS:
        source_val = source_cluster.get(key)
        dest_val = dest_cluster.get(key)

        # Skip if both are None/missing
        if source_val is None and dest_val is None:
            continue

        source_str = str(source_val) if source_val is not None else "<not set>"
        dest_str = str(dest_val) if dest_val is not None else "<not set>"

        if source_val == dest_val:
            synced.append(key)
        elif key in expected_diffs:
            expected_differences.append({
                "parameter": key,
                "source": source_str,
                "destination": dest_str,
                "reason": expected_diffs[key].get("reason", "Expected difference"),
                "severity": expected_diffs[key].get("severity", "info"),
            })
        else:
            unexpected_differences.append({
                "parameter": key,
                "source": source_str,
                "destination": dest_str,
            })

    has_unexpected = len(unexpected_differences) > 0
    has_expected = len(expected_differences) > 0

    if has_unexpected:
        status = "differs"
    elif has_expected:
        status = "synced_with_expected_diffs"
    else:
        status = "synced"

    return {
        "status": status,
        "synced": synced,
        "expectedDifferences": expected_differences,
        "unexpectedDifferences": unexpected_differences,
    }


def identify_issues(
    cluster_config_comparison: dict,
    cluster_param_comparison: dict,
    instance_param_comparison: dict,
    source_payload: dict,
    dest_payload: dict,
) -> list:
    """Identify issues that need attention."""
    issues = []

    # Check for unexpected differences
    if cluster_config_comparison.get("unexpectedDifferences"):
        count = len(cluster_config_comparison["unexpectedDifferences"])
        issues.append({
            "severity": "warning",
            "issue": "UnexpectedClusterConfigDifferences",
            "message": f"{count} cluster config parameter(s) differ unexpectedly",
            "parameters": [d["parameter"] for d in cluster_config_comparison["unexpectedDifferences"]],
        })

    if cluster_param_comparison.get("unexpectedDifferences"):
        count = len(cluster_param_comparison["unexpectedDifferences"])
        issues.append({
            "severity": "warning",
            "issue": "UnexpectedClusterParameterDifferences",
            "message": f"{count} cluster parameter(s) differ unexpectedly",
            "parameters": [d["parameter"] for d in cluster_param_comparison["unexpectedDifferences"]],
        })

    if instance_param_comparison.get("unexpectedDifferences"):
        count = len(instance_param_comparison["unexpectedDifferences"])
        issues.append({
            "severity": "warning",
            "issue": "UnexpectedInstanceParameterDifferences",
            "message": f"{count} instance parameter(s) differ unexpectedly",
            "parameters": [d["parameter"] for d in instance_param_comparison["unexpectedDifferences"]],
        })

    # Check for parameters only in source (might indicate missing migration)
    if cluster_param_comparison.get("onlySourceUnexpected"):
        count = len(cluster_param_comparison["onlySourceUnexpected"])
        issues.append({
            "severity": "info",
            "issue": "ClusterParametersOnlyInSource",
            "message": f"{count} cluster parameter(s) exist only in source",
            "parameters": [d["parameter"] for d in cluster_param_comparison["onlySourceUnexpected"]],
        })

    if instance_param_comparison.get("onlySourceUnexpected"):
        count = len(instance_param_comparison["onlySourceUnexpected"])
        issues.append({
            "severity": "info",
            "issue": "InstanceParametersOnlyInSource",
            "message": f"{count} instance parameter(s) exist only in source",
            "parameters": [d["parameter"] for d in instance_param_comparison["onlySourceUnexpected"]],
        })

    # Check instance count
    source_instances = source_payload.get("summary", {}).get("instanceCount", 0)
    dest_instances = dest_payload.get("summary", {}).get("instanceCount", 0)
    if source_instances != dest_instances:
        issues.append({
            "severity": "info",
            "issue": "InstanceCountDiffers",
            "message": f"Instance count differs: source={source_instances}, destination={dest_instances}",
        })

    return issues


def determine_overall_status(
    cluster_config_comparison: dict,
    cluster_param_comparison: dict,
    instance_param_comparison: dict,
    issues: list,
) -> str:
    """Determine overall comparison status."""
    # Check for critical issues
    critical_issues = [i for i in issues if i.get("severity") == "critical"]
    if critical_issues:
        return "critical"

    # Check if any comparison has unexpected differences
    statuses = [
        cluster_config_comparison.get("status", "synced"),
        cluster_param_comparison.get("status", "synced"),
        instance_param_comparison.get("status", "synced"),
    ]

    if "differs" in statuses:
        return "differs"
    if "synced_with_expected_diffs" in statuses:
        return "synced_with_expected_diffs"

    return "synced"


def lambda_handler(event: dict, context: Any) -> dict:
    """
    Lambda entry point.

    Event structure (from Step Function):
    {
        "project": "mro-mi2",
        "sourceEnv": "legacy-ppd",
        "destinationEnv": "nh-ppd",
        "source_state": { ... from DynamoDB parallel fetch ... },
        "destination_state": { ... from DynamoDB parallel fetch ... },
        "ExpectedDifferences": { ... optional override ... }
    }

    Returns:
    {
        "status": "synced | differs | synced_with_expected_diffs | critical",
        "summary": { ... },
        "clusterConfigComparison": { ... },
        "parameterGroupComparison": { ... },
        "issues": [ ... ],
        "timestamp": "ISO8601"
    }
    """
    logger.info(f"Comparing RDS states: {event.get('sourceEnv')} vs {event.get('destinationEnv')}")

    project = event.get("project", "")
    source_env = event.get("sourceEnv", "")
    dest_env = event.get("destinationEnv", "")
    expected_diff_override = event.get("ExpectedDifferences", {})

    # Merge expected differences with defaults
    expected_diffs = merge_expected_differences(DEFAULT_EXPECTED_DIFFERENCES, expected_diff_override)

    # Parse states from DynamoDB format
    source_state = event.get("source_state", {})
    dest_state = event.get("destination_state", {})

    source_payload = extract_payload(source_state)
    dest_payload = extract_payload(dest_state)

    issues = []

    # Check if states exist
    if not source_payload:
        issues.append({
            "severity": "critical",
            "issue": "SourceStateMissing",
            "message": "Source state not found in DynamoDB",
        })
    if not dest_payload:
        issues.append({
            "severity": "critical",
            "issue": "DestinationStateMissing",
            "message": "Destination state not found in DynamoDB",
        })

    if not source_payload or not dest_payload:
        return {
            "status": "error",
            "project": project,
            "sourceEnv": source_env,
            "destinationEnv": dest_env,
            "summary": {
                "sourceFound": source_payload is not None,
                "destinationFound": dest_payload is not None,
            },
            "issues": issues,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    # Extract data for comparison
    source_cluster = source_payload.get("cluster", {})
    dest_cluster = dest_payload.get("cluster", {})

    source_param_groups = source_payload.get("parameterGroups", {})
    dest_param_groups = dest_payload.get("parameterGroups", {})

    source_cluster_params = source_param_groups.get("cluster", {}).get("parameters", {})
    dest_cluster_params = dest_param_groups.get("cluster", {}).get("parameters", {})

    source_instance_params = source_param_groups.get("instance", {}).get("parameters", {})
    dest_instance_params = dest_param_groups.get("instance", {}).get("parameters", {})

    # Perform comparisons
    cluster_config_comparison = compare_cluster_config(
        source_cluster,
        dest_cluster,
        expected_diffs.get("clusterConfig", {}),
    )

    cluster_param_comparison = compare_parameters(
        source_cluster_params,
        dest_cluster_params,
        expected_diffs.get("clusterParameters", {}),
    )

    instance_param_comparison = compare_parameters(
        source_instance_params,
        dest_instance_params,
        expected_diffs.get("instanceParameters", {}),
    )

    # Identify issues
    comparison_issues = identify_issues(
        cluster_config_comparison,
        cluster_param_comparison,
        instance_param_comparison,
        source_payload,
        dest_payload,
    )
    issues.extend(comparison_issues)

    # Determine overall status
    status = determine_overall_status(
        cluster_config_comparison,
        cluster_param_comparison,
        instance_param_comparison,
        issues,
    )

    # Build summary
    summary = {
        "sourceFound": True,
        "destinationFound": True,
        "clusterConfig": cluster_config_comparison.get("status", "synced"),
        "clusterParameters": cluster_param_comparison.get("status", "synced"),
        "instanceParameters": instance_param_comparison.get("status", "synced"),
        "sourceInstanceCount": source_payload.get("summary", {}).get("instanceCount", 0),
        "destinationInstanceCount": dest_payload.get("summary", {}).get("instanceCount", 0),
        "totalExpectedDiffs": (
            len(cluster_config_comparison.get("expectedDifferences", []))
            + cluster_param_comparison.get("summary", {}).get("expectedDiffCount", 0)
            + instance_param_comparison.get("summary", {}).get("expectedDiffCount", 0)
        ),
        "totalUnexpectedDiffs": (
            len(cluster_config_comparison.get("unexpectedDifferences", []))
            + cluster_param_comparison.get("summary", {}).get("unexpectedDiffCount", 0)
            + instance_param_comparison.get("summary", {}).get("unexpectedDiffCount", 0)
        ),
    }

    return {
        "status": status,
        "project": project,
        "sourceEnv": source_env,
        "destinationEnv": dest_env,
        "summary": summary,
        "clusterConfigComparison": cluster_config_comparison,
        "parameterGroupComparison": {
            "cluster": cluster_param_comparison,
            "instance": instance_param_comparison,
        },
        "issues": issues,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
