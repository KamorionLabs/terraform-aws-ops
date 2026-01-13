"""
Compare PVC Lambda
===================
Compares K8s PVC and PV state between Source and Destination environments.
Generates a detailed comparison report for migration validation.

Called by Step Function k8s-pvc-compare after fetching both states.

Environment Variables:
- LOG_LEVEL: Logging level (default: INFO)
- EXPECTED_SC_MAPPINGS: JSON string of expected storage class mappings (optional)
"""

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)
logger.setLevel(os.environ.get("LOG_LEVEL", "INFO"))

# Expected storage class mappings (source -> destination)
# Can be overridden via EXPECTED_SC_MAPPINGS env var
DEFAULT_STORAGE_CLASS_MAPPINGS = {
    "gp2": "gp3",  # Common migration pattern
}

def get_expected_sc_mappings() -> dict:
    """Get expected storage class mappings from env or default."""
    env_mappings = os.environ.get("EXPECTED_SC_MAPPINGS")
    if env_mappings:
        try:
            return json.loads(env_mappings)
        except json.JSONDecodeError:
            logger.warning("Invalid EXPECTED_SC_MAPPINGS, using defaults")
    return DEFAULT_STORAGE_CLASS_MAPPINGS


def extract_payload_from_dynamo(dynamo_item: dict) -> Optional[dict]:
    """Extract payload from DynamoDB item structure."""
    if not dynamo_item:
        return None

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


def compare_pvc_counts(source: dict, destination: dict) -> dict:
    """Compare PVC counts between environments."""
    source_summary = source.get("summary", {})
    dest_summary = destination.get("summary", {})

    source_total = source_summary.get("total", 0)
    dest_total = dest_summary.get("total", 0)

    return {
        "source": source_total,
        "destination": dest_total,
        "status": "synced" if source_total == dest_total else "differs",
        "difference": dest_total - source_total,
    }


def compare_storage_classes(source: dict, destination: dict) -> dict:
    """Compare storage class distribution between environments."""
    sc_mappings = get_expected_sc_mappings()

    source_by_sc = source.get("summary", {}).get("byStorageClass", {})
    dest_by_sc = destination.get("summary", {}).get("byStorageClass", {})

    # Check for expected mappings
    expected = True
    reason = None

    # Build normalized comparison (apply expected mappings to source)
    source_normalized = {}
    for sc, count in source_by_sc.items():
        mapped = sc_mappings.get(sc, sc)
        source_normalized[mapped] = source_normalized.get(mapped, 0) + count

    if source_normalized == dest_by_sc:
        expected = True
        reason = "Storage classes match after expected mappings"
    elif source_by_sc != dest_by_sc:
        expected = False
        reason = "Unexpected storage class differences"
    else:
        expected = True
        reason = "Identical storage class distribution"

    return {
        "source": source_by_sc,
        "destination": dest_by_sc,
        "expected": expected,
        "reason": reason,
        "status": "synced" if expected else "differs",
    }


def compare_capacity(source: dict, destination: dict) -> dict:
    """Compare total capacity between environments."""
    source_capacity = source.get("summary", {}).get("totalCapacity", "0")
    dest_capacity = destination.get("summary", {}).get("totalCapacity", "0")

    if source_capacity == dest_capacity:
        return {
            "source": source_capacity,
            "destination": dest_capacity,
            "expected": True,
            "reason": "Identical capacity",
            "status": "synced",
        }

    return {
        "source": source_capacity,
        "destination": dest_capacity,
        "expected": True,  # Capacity differences are often expected
        "reason": "Capacity differs between environments",
        "status": "differs",
    }


def compare_pvcs_by_name(source_pvcs: list, dest_pvcs: list) -> dict:
    """Compare PVCs by name between environments."""
    sc_mappings = get_expected_sc_mappings()

    source_by_name = {pvc["name"]: pvc for pvc in source_pvcs}
    dest_by_name = {pvc["name"]: pvc for pvc in dest_pvcs}

    source_names = set(source_by_name.keys())
    dest_names = set(dest_by_name.keys())

    same_pvcs = sorted(list(source_names & dest_names))
    only_source = sorted(list(source_names - dest_names))
    only_destination = sorted(list(dest_names - source_names))

    different_config = []

    for name in same_pvcs:
        source_pvc = source_by_name[name]
        dest_pvc = dest_by_name[name]

        diffs = []

        # Check storage class
        source_sc = source_pvc.get("storageClass", "")
        dest_sc = dest_pvc.get("storageClass", "")
        if source_sc != dest_sc:
            expected_sc = sc_mappings.get(source_sc, source_sc)
            is_expected = (dest_sc == expected_sc)
            diffs.append({
                "field": "storageClass",
                "source": source_sc,
                "destination": dest_sc,
                "expected": is_expected,
                "reason": f"Expected mapping from {source_sc} to {expected_sc}" if is_expected else "Unexpected storage class change",
            })

        # Check capacity
        source_cap = source_pvc.get("capacity", "")
        dest_cap = dest_pvc.get("capacity", "")
        if source_cap != dest_cap:
            diffs.append({
                "field": "capacity",
                "source": source_cap,
                "destination": dest_cap,
                "expected": True,
                "reason": "Capacity difference",
            })

        # Check access modes
        source_modes = set(source_pvc.get("accessModes", []))
        dest_modes = set(dest_pvc.get("accessModes", []))
        if source_modes != dest_modes:
            diffs.append({
                "field": "accessModes",
                "source": list(source_modes),
                "destination": list(dest_modes),
                "expected": False,
                "reason": "Access mode mismatch",
            })

        if diffs:
            different_config.append({
                "pvc": name,
                "differences": diffs,
                "allExpected": all(d.get("expected", False) for d in diffs),
            })

    return {
        "samePvcs": same_pvcs,
        "differentConfig": different_config,
        "onlySource": only_source,
        "onlyDestination": only_destination,
    }


def compare_pvs_by_name(source_pvs: list, dest_pvs: list) -> dict:
    """Compare PersistentVolumes between environments."""
    if not source_pvs and not dest_pvs:
        return {
            "status": "no_data",
            "message": "No PV data available in either environment",
            "samePvs": [],
            "differentConfig": [],
            "onlySource": [],
            "onlyDestination": [],
        }

    sc_mappings = get_expected_sc_mappings()

    source_by_name = {pv["name"]: pv for pv in source_pvs}
    dest_by_name = {pv["name"]: pv for pv in dest_pvs}

    source_names = set(source_by_name.keys())
    dest_names = set(dest_by_name.keys())

    same_pvs = sorted(list(source_names & dest_names))
    only_source = sorted(list(source_names - dest_names))
    only_destination = sorted(list(dest_names - source_names))

    different_config = []

    for name in same_pvs:
        source_pv = source_by_name[name]
        dest_pv = dest_by_name[name]

        diffs = []

        # Check storage class
        source_sc = source_pv.get("storageClass", "")
        dest_sc = dest_pv.get("storageClass", "")
        if source_sc != dest_sc:
            expected_sc = sc_mappings.get(source_sc, source_sc)
            is_expected = (dest_sc == expected_sc)
            diffs.append({
                "field": "storageClass",
                "source": source_sc,
                "destination": dest_sc,
                "expected": is_expected,
                "reason": f"Expected mapping from {source_sc} to {expected_sc}" if is_expected else "Unexpected storage class change",
            })

        # Check capacity
        source_cap = source_pv.get("capacity", "")
        dest_cap = dest_pv.get("capacity", "")
        if source_cap != dest_cap:
            diffs.append({
                "field": "capacity",
                "source": source_cap,
                "destination": dest_cap,
                "expected": True,
                "reason": "Capacity difference",
            })

        # Check reclaim policy
        source_reclaim = source_pv.get("reclaimPolicy", "")
        dest_reclaim = dest_pv.get("reclaimPolicy", "")
        if source_reclaim != dest_reclaim:
            diffs.append({
                "field": "reclaimPolicy",
                "source": source_reclaim,
                "destination": dest_reclaim,
                "expected": False,
                "reason": "Reclaim policy mismatch",
            })

        # Check CSI driver
        source_csi = source_pv.get("csiDriver", "")
        dest_csi = dest_pv.get("csiDriver", "")
        if source_csi != dest_csi:
            diffs.append({
                "field": "csiDriver",
                "source": source_csi,
                "destination": dest_csi,
                "expected": False,
                "reason": "CSI driver mismatch",
            })

        # Check EFS config
        source_efs = source_pv.get("efsConfig", {})
        dest_efs = dest_pv.get("efsConfig", {})
        if source_efs or dest_efs:
            source_fs_id = source_efs.get("fileSystemId", "")
            dest_fs_id = dest_efs.get("fileSystemId", "")
            if source_fs_id and dest_fs_id and source_fs_id != dest_fs_id:
                diffs.append({
                    "field": "efsFileSystemId",
                    "source": source_fs_id,
                    "destination": dest_fs_id,
                    "expected": True,  # Different EFS is often expected
                    "reason": "Different EFS filesystem",
                })

        # Check status
        source_status = source_pv.get("status", "")
        dest_status = dest_pv.get("status", "")
        if source_status != dest_status:
            diffs.append({
                "field": "status",
                "source": source_status,
                "destination": dest_status,
                "expected": False,
                "reason": "PV status mismatch",
            })

        if diffs:
            different_config.append({
                "pv": name,
                "differences": diffs,
                "allExpected": all(d.get("expected", False) for d in diffs),
            })

    return {
        "status": "compared",
        "samePvs": same_pvs,
        "differentConfig": different_config,
        "onlySource": only_source,
        "onlyDestination": only_destination,
    }


def compare_pv_counts(source: dict, destination: dict) -> dict:
    """Compare PV counts between environments."""
    source_pvs = source.get("persistentVolumes", [])
    dest_pvs = destination.get("persistentVolumes", [])

    source_total = len(source_pvs)
    dest_total = len(dest_pvs)

    # Count by status
    source_by_status = {}
    for pv in source_pvs:
        status = pv.get("status", "Unknown")
        source_by_status[status] = source_by_status.get(status, 0) + 1

    dest_by_status = {}
    for pv in dest_pvs:
        status = pv.get("status", "Unknown")
        dest_by_status[status] = dest_by_status.get(status, 0) + 1

    return {
        "source": source_total,
        "destination": dest_total,
        "status": "synced" if source_total == dest_total else "differs",
        "difference": dest_total - source_total,
        "sourceByStatus": source_by_status,
        "destinationByStatus": dest_by_status,
    }


def identify_issues(
    source: dict,
    destination: dict,
    pvc_count: dict,
    pvcs_comparison: dict,
    pv_count: Optional[dict] = None,
    pvs_comparison: Optional[dict] = None
) -> list:
    """Identify comparison issues that need attention."""
    issues = []

    # Check PVC count difference
    if pvc_count["status"] == "differs":
        if pvc_count["difference"] < 0:
            issues.append({
                "severity": "warning",
                "issue": "PVCCountMismatch",
                "message": f"Destination has fewer PVCs than Source ({pvc_count['destination']} vs {pvc_count['source']})",
            })
        else:
            issues.append({
                "severity": "info",
                "issue": "PVCCountDifference",
                "message": f"Destination has more PVCs than Source ({pvc_count['destination']} vs {pvc_count['source']})",
            })

    # Check missing PVCs
    if pvcs_comparison.get("onlySource"):
        issues.append({
            "severity": "warning",
            "issue": "PVCsMissingInDestination",
            "message": f"PVCs exist in Source but not in Destination: {', '.join(pvcs_comparison['onlySource'])}",
            "pvcs": pvcs_comparison["onlySource"],
        })

    # Check unexpected config differences
    unexpected_diffs = [
        d for d in pvcs_comparison.get("differentConfig", [])
        if not d.get("allExpected", True)
    ]
    if unexpected_diffs:
        issues.append({
            "severity": "warning",
            "issue": "UnexpectedPVCConfigDifferences",
            "message": f"{len(unexpected_diffs)} PVC(s) have unexpected configuration differences",
            "pvcs": [d["pvc"] for d in unexpected_diffs],
        })

    # Check health status difference
    source_healthy = source.get("healthy", False)
    dest_healthy = destination.get("healthy", False)

    if source_healthy and not dest_healthy:
        issues.append({
            "severity": "warning",
            "issue": "DestinationNotHealthy",
            "message": "Source PVCs are healthy but Destination PVCs are not",
        })

    # PV-specific issues
    if pv_count and pvs_comparison:
        # Check PV count difference
        if pv_count.get("status") == "differs":
            if pv_count["difference"] < 0:
                issues.append({
                    "severity": "warning",
                    "issue": "PVCountMismatch",
                    "message": f"Destination has fewer PVs than Source ({pv_count['destination']} vs {pv_count['source']})",
                })

        # Check for Released/Failed PVs
        source_released = pv_count.get("sourceByStatus", {}).get("Released", 0)
        dest_released = pv_count.get("destinationByStatus", {}).get("Released", 0)
        if source_released > 0 or dest_released > 0:
            issues.append({
                "severity": "warning",
                "issue": "ReleasedPVs",
                "message": f"Released PVs found: Source={source_released}, Destination={dest_released}",
            })

        source_failed = pv_count.get("sourceByStatus", {}).get("Failed", 0)
        dest_failed = pv_count.get("destinationByStatus", {}).get("Failed", 0)
        if source_failed > 0 or dest_failed > 0:
            issues.append({
                "severity": "critical",
                "issue": "FailedPVs",
                "message": f"Failed PVs found: Source={source_failed}, Destination={dest_failed}",
            })

        # Check unexpected PV config differences
        if pvs_comparison.get("status") == "compared":
            pv_unexpected_diffs = [
                d for d in pvs_comparison.get("differentConfig", [])
                if not d.get("allExpected", True)
            ]
            if pv_unexpected_diffs:
                issues.append({
                    "severity": "warning",
                    "issue": "UnexpectedPVConfigDifferences",
                    "message": f"{len(pv_unexpected_diffs)} PV(s) have unexpected configuration differences",
                    "pvs": [d["pv"] for d in pv_unexpected_diffs],
                })

    return issues


def lambda_handler(event: dict, context: Any) -> dict:
    """
    Compare PVC and PV state between Source and Destination environments.

    Event structure (from Step Function):
    {
        "project": "mro-mi2",
        "sourceEnv": "legacy-ppd",
        "destinationEnv": "nh-ppd",
        "source_state": {
            "source": "source",
            "hasData": true,
            "itemData": {...}
        },
        "destination_state": {
            "source": "destination",
            "hasData": true,
            "itemData": {...}
        }
    }

    Returns comparison payload.
    """
    logger.info(f"Comparing PVCs for project={event.get('project')}, "
                f"source={event.get('sourceEnv')}, destination={event.get('destinationEnv')}")

    project = event.get("project")
    source_env = event.get("sourceEnv")
    dest_env = event.get("destinationEnv")

    source_state = event.get("source_state", {})
    dest_state = event.get("destination_state", {})

    # Extract payloads from DynamoDB items
    source_payload = extract_payload_from_dynamo(source_state)
    dest_payload = extract_payload_from_dynamo(dest_state)

    # Handle missing states
    if not source_payload and not dest_payload:
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
                "pvcCount": "source_missing",
                "pvCount": "source_missing",
                "storageClasses": "source_missing",
                "totalCapacity": "source_missing",
            },
            "message": "Source state is missing, cannot compare",
            "destinationOnly": {
                "status": dest_payload.get("status"),
                "healthy": dest_payload.get("healthy"),
                "summary": dest_payload.get("summary"),
            },
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    if not dest_payload:
        return {
            "status": "partial",
            "summary": {
                "pvcCount": "destination_missing",
                "pvCount": "destination_missing",
                "storageClasses": "destination_missing",
                "totalCapacity": "destination_missing",
            },
            "message": "Destination state is missing, cannot compare",
            "sourceOnly": {
                "status": source_payload.get("status"),
                "healthy": source_payload.get("healthy"),
                "summary": source_payload.get("summary"),
            },
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    # Perform PVC comparisons
    source_pvcs = source_payload.get("pvcs", [])
    dest_pvcs = dest_payload.get("pvcs", [])

    pvc_count_comparison = compare_pvc_counts(source_payload, dest_payload)
    storage_class_comparison = compare_storage_classes(source_payload, dest_payload)
    capacity_comparison = compare_capacity(source_payload, dest_payload)
    pvcs_comparison = compare_pvcs_by_name(source_pvcs, dest_pvcs)

    # Perform PV comparisons
    source_pvs = source_payload.get("persistentVolumes", [])
    dest_pvs = dest_payload.get("persistentVolumes", [])

    pv_count_comparison = compare_pv_counts(source_payload, dest_payload)
    pvs_comparison = compare_pvs_by_name(source_pvs, dest_pvs)

    # Identify issues
    issues = identify_issues(
        source_payload,
        dest_payload,
        pvc_count_comparison,
        pvcs_comparison,
        pv_count_comparison,
        pvs_comparison,
    )

    # Determine overall status
    overall_status = "synced"
    if (
        pvc_count_comparison["status"] == "differs"
        or not storage_class_comparison.get("expected", True)
        or pvcs_comparison.get("onlySource")
        or any(i.get("severity") == "critical" for i in issues)
    ):
        overall_status = "differs"

    timestamp = datetime.now(timezone.utc).isoformat()

    result = {
        "status": overall_status,
        "summary": {
            "pvcCount": pvc_count_comparison["status"],
            "pvCount": pv_count_comparison["status"],
            "storageClasses": storage_class_comparison["status"],
            "totalCapacity": capacity_comparison["status"],
        },
        "pvcCountComparison": pvc_count_comparison,
        "pvCountComparison": pv_count_comparison,
        "pvcsComparison": pvcs_comparison,
        "pvsComparison": pvs_comparison,
        "capacityComparison": capacity_comparison,
        "storageClassComparison": storage_class_comparison,
        "issues": issues,
        "sourceTimestamp": source_payload.get("timestamp"),
        "destinationTimestamp": dest_payload.get("timestamp"),
        "timestamp": timestamp,
    }

    logger.info(f"PVC/PV comparison complete: status={overall_status}, issues={len(issues)}")

    return result
