"""
Process PVC Lambda
===================
Parses Kubernetes PVC and PV data from eks:call responses and generates
summary, issues detection, and structured storage information.

Called by Step Function after eks:call to:
- /api/v1/namespaces/{ns}/persistentvolumeclaims
- /api/v1/persistentvolumes

Environment Variables:
- LOG_LEVEL: Logging level (default: INFO)
"""

import logging
import os
import re
from datetime import datetime, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)
logger.setLevel(os.environ.get("LOG_LEVEL", "INFO"))

# Thresholds for status determination
PENDING_WARNING_SECONDS = 120  # 2 minutes - warning
PENDING_CRITICAL_SECONDS = 300  # 5 minutes - critical

# Storage class mappings
EXPECTED_STORAGE_CLASSES = {
    "legacy": {"gp2", "efs-sc", "efs-static"},
    "nh": {"gp3", "efs-sc", "efs-static"},
}

# CSI drivers
EBS_CSI_DRIVER = "ebs.csi.aws.com"
EFS_CSI_DRIVER = "efs.csi.aws.com"


def parse_capacity(capacity_str: str) -> int:
    """Parse capacity string to bytes."""
    if not capacity_str:
        return 0

    multipliers = {
        "Ki": 1024,
        "Mi": 1024**2,
        "Gi": 1024**3,
        "Ti": 1024**4,
        "Pi": 1024**5,
        "K": 1000,
        "M": 1000**2,
        "G": 1000**3,
        "T": 1000**4,
        "P": 1000**5,
    }

    for suffix, multiplier in multipliers.items():
        if capacity_str.endswith(suffix):
            try:
                value = float(capacity_str[: -len(suffix)])
                return int(value * multiplier)
            except ValueError:
                return 0

    try:
        return int(capacity_str)
    except ValueError:
        return 0


def format_capacity(bytes_value: int) -> str:
    """Format bytes to human-readable capacity."""
    if bytes_value >= 1024**4:
        return f"{bytes_value / 1024**4:.0f}Ti"
    elif bytes_value >= 1024**3:
        return f"{bytes_value / 1024**3:.0f}Gi"
    elif bytes_value >= 1024**2:
        return f"{bytes_value / 1024**2:.0f}Mi"
    elif bytes_value >= 1024:
        return f"{bytes_value / 1024:.0f}Ki"
    return f"{bytes_value}"


def parse_age(creation_timestamp: str) -> str:
    """Convert creation timestamp to human-readable age."""
    try:
        created = datetime.fromisoformat(creation_timestamp.replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        delta = now - created

        days = delta.days
        hours = delta.seconds // 3600
        minutes = (delta.seconds % 3600) // 60

        if days > 0:
            return f"{days}d"
        elif hours > 0:
            return f"{hours}h"
        else:
            return f"{minutes}m"
    except Exception:
        return "unknown"


def calculate_pending_duration(pvc: dict) -> Optional[int]:
    """Calculate how long a PVC has been in Pending state (seconds)."""
    phase = pvc.get("status", {}).get("phase")
    if phase != "Pending":
        return None

    creation_timestamp = pvc.get("metadata", {}).get("creationTimestamp")
    if not creation_timestamp:
        return None

    try:
        created = datetime.fromisoformat(creation_timestamp.replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        return int((now - created).total_seconds())
    except Exception:
        return None


def extract_efs_config(pv: dict) -> Optional[dict]:
    """Extract EFS configuration from PV spec."""
    csi = pv.get("spec", {}).get("csi", {})
    if csi.get("driver") != EFS_CSI_DRIVER:
        return None

    volume_handle = csi.get("volumeHandle", "")
    # EFS volume handle format: fs-xxxxx::fsap-xxxxx or just fs-xxxxx
    parts = volume_handle.split("::")

    result = {}
    if parts:
        # First part is always fileSystemId
        fs_match = re.match(r"(fs-[a-f0-9]+)", parts[0])
        if fs_match:
            result["fileSystemId"] = fs_match.group(1)

    if len(parts) > 1:
        # Second part is accessPointId
        ap_match = re.match(r"(fsap-[a-f0-9]+)", parts[1])
        if ap_match:
            result["accessPointId"] = ap_match.group(1)

    return result if result else None


def extract_ebs_config(pv: dict) -> Optional[dict]:
    """Extract EBS configuration from PV spec."""
    csi = pv.get("spec", {}).get("csi", {})
    if csi.get("driver") != EBS_CSI_DRIVER:
        return None

    volume_handle = csi.get("volumeHandle", "")
    # EBS volume handle format: vol-xxxxx
    vol_match = re.match(r"(vol-[a-f0-9]+)", volume_handle)

    if vol_match:
        return {"volumeId": vol_match.group(1)}

    return None


def process_pv(pv: dict) -> dict:
    """Process a single PersistentVolume."""
    metadata = pv.get("metadata", {})
    spec = pv.get("spec", {})
    status = pv.get("status", {})

    name = metadata.get("name", "unknown")
    capacity_raw = spec.get("capacity", {}).get("storage", "0")
    capacity_bytes = parse_capacity(capacity_raw)

    # Extract claim reference
    claim_ref = spec.get("claimRef", {})
    claim_namespace = claim_ref.get("namespace", "")
    claim_name = claim_ref.get("name", "")
    claim_ref_str = f"{claim_namespace}/{claim_name}" if claim_namespace and claim_name else None

    # Determine CSI driver and backend config
    csi = spec.get("csi", {})
    csi_driver = csi.get("driver")

    result = {
        "name": name,
        "capacity": capacity_raw,
        "capacityBytes": capacity_bytes,
        "accessModes": spec.get("accessModes", []),
        "reclaimPolicy": spec.get("persistentVolumeReclaimPolicy", "unknown"),
        "storageClass": spec.get("storageClassName", ""),
        "status": status.get("phase", "Unknown"),
        "claimRef": claim_ref_str,
        "csiDriver": csi_driver,
        "volumeHandle": csi.get("volumeHandle", ""),
        "createdAt": metadata.get("creationTimestamp"),
        "age": parse_age(metadata.get("creationTimestamp", "")),
    }

    # Add EFS or EBS specific config
    if csi_driver == EFS_CSI_DRIVER:
        efs_config = extract_efs_config(pv)
        if efs_config:
            result["efsConfig"] = efs_config
    elif csi_driver == EBS_CSI_DRIVER:
        ebs_config = extract_ebs_config(pv)
        if ebs_config:
            result["ebsConfig"] = ebs_config

    return result


def process_pvc(pvc: dict, pv_map: dict) -> dict:
    """Process a single PersistentVolumeClaim."""
    metadata = pvc.get("metadata", {})
    spec = pvc.get("spec", {})
    status = pvc.get("status", {})

    name = metadata.get("name", "unknown")
    namespace = metadata.get("namespace", "unknown")

    # Get capacity from status (actual) or spec (requested)
    capacity_raw = status.get("capacity", {}).get("storage") or \
                   spec.get("resources", {}).get("requests", {}).get("storage", "0")
    capacity_bytes = parse_capacity(capacity_raw)

    phase = status.get("phase", "Unknown")
    volume_name = spec.get("volumeName") or status.get("volumeName", "")

    result = {
        "name": name,
        "namespace": namespace,
        "status": phase,
        "volume": volume_name,
        "capacity": capacity_raw,
        "capacityBytes": capacity_bytes,
        "accessModes": status.get("accessModes") or spec.get("accessModes", []),
        "storageClass": spec.get("storageClassName", ""),
        "volumeMode": spec.get("volumeMode", "Filesystem"),
        "createdAt": metadata.get("creationTimestamp"),
        "age": parse_age(metadata.get("creationTimestamp", "")),
        "labels": metadata.get("labels", {}),
    }

    # Check if bound and add PV details
    if phase == "Bound" and volume_name and volume_name in pv_map:
        pv = pv_map[volume_name]
        result["boundAt"] = pv.get("createdAt")  # Approximation

        # Propagate EFS/EBS config from PV
        if "efsConfig" in pv:
            result["efsConfig"] = pv["efsConfig"]
        if "ebsConfig" in pv:
            result["ebsConfig"] = pv["ebsConfig"]

    return result


def detect_issues(
    pvcs: list[dict],
    pvs: list[dict],
    raw_pvcs: list[dict],
    source: str,
    pv_check_enabled: bool = True
) -> list[dict]:
    """Detect issues in PVCs and PVs.

    Args:
        pvcs: Processed PVC objects
        pvs: Processed PV objects
        raw_pvcs: Raw PVC objects from K8s API
        source: Source identifier (legacy/nh)
        pv_check_enabled: If False, skip PV-related checks (when PVs not fetched)
    """
    issues = []

    # Create PV lookup for validation (only if PVs were fetched)
    pv_names = {pv["name"] for pv in pvs} if pv_check_enabled else set()

    for i, pvc in enumerate(pvcs):
        pvc_name = pvc["name"]
        phase = pvc["status"]

        # Check Lost status
        if phase == "Lost":
            issues.append({
                "pvc": pvc_name,
                "severity": "critical",
                "issue": "PVCLost",
                "message": f"PVC is in Lost state - associated PV may be deleted",
            })

        # Check Pending status with duration
        if phase == "Pending":
            pending_duration = calculate_pending_duration(raw_pvcs[i]) if i < len(raw_pvcs) else None

            if pending_duration and pending_duration > PENDING_CRITICAL_SECONDS:
                issues.append({
                    "pvc": pvc_name,
                    "severity": "critical",
                    "issue": "PendingTooLong",
                    "message": f"PVC has been Pending for {pending_duration}s (> {PENDING_CRITICAL_SECONDS}s)",
                })
            elif pending_duration and pending_duration > PENDING_WARNING_SECONDS:
                issues.append({
                    "pvc": pvc_name,
                    "severity": "warning",
                    "issue": "PendingLong",
                    "message": f"PVC has been Pending for {pending_duration}s",
                })
            else:
                issues.append({
                    "pvc": pvc_name,
                    "severity": "warning",
                    "issue": "Pending",
                    "message": f"PVC is in Pending state",
                })

        # Check if bound PVC has valid PV (only if PV check is enabled)
        if pv_check_enabled and phase == "Bound":
            volume_name = pvc.get("volume")
            if volume_name and volume_name not in pv_names:
                issues.append({
                    "pvc": pvc_name,
                    "severity": "critical",
                    "issue": "PVMissing",
                    "message": f"Bound PVC references missing PV: {volume_name}",
                })

        # Validate storage class
        storage_class = pvc.get("storageClass", "")
        expected = EXPECTED_STORAGE_CLASSES.get(source, set())
        if storage_class and expected and storage_class not in expected:
            issues.append({
                "pvc": pvc_name,
                "severity": "warning",
                "issue": "UnexpectedStorageClass",
                "message": f"StorageClass '{storage_class}' not in expected list for {source}",
            })

    # Check PV issues
    for pv in pvs:
        pv_name = pv["name"]
        pv_status = pv["status"]

        if pv_status == "Failed":
            issues.append({
                "pv": pv_name,
                "severity": "critical",
                "issue": "PVFailed",
                "message": f"PersistentVolume is in Failed state",
            })
        elif pv_status == "Released":
            issues.append({
                "pv": pv_name,
                "severity": "warning",
                "issue": "PVReleased",
                "message": f"PersistentVolume is Released (claim deleted but PV retained)",
            })

    return issues


def determine_status(summary: dict, issues: list[dict]) -> str:
    """Determine overall status based on summary and issues."""
    # Critical conditions
    critical_issues = [i for i in issues if i.get("severity") == "critical"]
    if critical_issues:
        return "critical"

    if summary["lost"] > 0:
        return "critical"

    # No bound PVCs is critical (if there are PVCs at all)
    if summary["total"] > 0 and summary["bound"] == 0:
        return "critical"

    # Warning conditions
    warning_issues = [i for i in issues if i.get("severity") == "warning"]
    if warning_issues:
        return "warning"

    if summary["pending"] > 0:
        return "warning"

    return "ok"


def lambda_handler(event: dict, context: Any) -> dict:
    """
    Process PVC and PV data from eks:call responses.

    Event structure (from Step Function):
    {
        "pvcs": [<raw PVC objects from K8s API>],
        "pvs": [<raw PV objects from K8s API>],
        "namespace": "hybris",
        "source": "legacy" | "nh",
        "cluster_name": "rubix-nonprod",
        "domain": "mro",
        "target": "mi1-ppd-legacy"
    }

    Returns:
    {
        "status": "ok" | "warning" | "critical",
        "source": "legacy" | "nh",
        "summary": {...},
        "pvcs": [...],
        "persistentVolumes": [...],
        "issues": [...],
        "healthy": true | false,
        "timestamp": "ISO8601"
    }
    """
    logger.info(f"Processing {len(event.get('pvcs', []))} PVCs and {len(event.get('pvs', []))} PVs")

    raw_pvcs = event.get("pvcs", [])
    raw_pvs = event.get("pvs", [])
    source = event.get("source", "unknown")
    namespace = event.get("namespace", "unknown")

    # Process PVs first to build lookup map
    processed_pvs = []
    pv_map = {}
    total_pv_capacity = 0

    for pv in raw_pvs:
        processed = process_pv(pv)
        processed_pvs.append(processed)
        pv_map[processed["name"]] = processed
        total_pv_capacity += processed["capacityBytes"]

    # Process PVCs
    processed_pvcs = []
    total_pvc_capacity = 0
    by_storage_class = {}

    summary = {
        "total": 0,
        "bound": 0,
        "pending": 0,
        "lost": 0,
        "totalCapacity": "0",
        "byStorageClass": {},
    }

    for pvc in raw_pvcs:
        processed = process_pvc(pvc, pv_map)
        processed_pvcs.append(processed)

        summary["total"] += 1
        phase = processed["status"]

        if phase == "Bound":
            summary["bound"] += 1
            total_pvc_capacity += processed["capacityBytes"]
        elif phase == "Pending":
            summary["pending"] += 1
        elif phase == "Lost":
            summary["lost"] += 1

        # Count by storage class
        sc = processed.get("storageClass", "unknown")
        by_storage_class[sc] = by_storage_class.get(sc, 0) + 1

    summary["totalCapacity"] = format_capacity(total_pvc_capacity)
    summary["byStorageClass"] = by_storage_class

    # Detect issues (skip PV checks if PVs were not fetched)
    pv_check_enabled = len(raw_pvs) > 0
    issues = detect_issues(processed_pvcs, processed_pvs, raw_pvcs, source, pv_check_enabled)

    # Determine overall status
    status = determine_status(summary, issues)

    timestamp = datetime.now(timezone.utc).isoformat()

    result = {
        "status": status,
        "source": source,
        "namespace": namespace,
        "summary": summary,
        "pvcs": processed_pvcs,
        "persistentVolumes": processed_pvs,
        "issues": issues,
        "healthy": status == "ok",
        "timestamp": timestamp,
    }

    logger.info(
        f"PVC processing complete: status={status}, total={summary['total']}, "
        f"bound={summary['bound']}, pending={summary['pending']}, issues={len(issues)}"
    )

    return result
