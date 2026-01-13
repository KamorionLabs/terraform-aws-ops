"""
Process Secrets Lambda
=======================
Parses Kubernetes Secrets from eks:call response and generates
summary, issues detection, and structured secret information.

Supports two modes:
- native: Native K8s Secrets (/api/v1/namespaces/{ns}/secrets)
- external: ExternalSecrets CRDs (requires RBAC for external-secrets.io)

Called by Step Function k8s-secrets-sync-checker.

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
STALE_MULTIPLIER = 2  # Consider stale if lastSyncTime > 2x refreshInterval

# Storage-related secret patterns (for native mode)
STORAGE_SECRET_PATTERNS = [
    r".*-media.*",
    r".*-storage.*",
    r".*subpath.*",
    r".*-shared.*",
    r".*-efs.*",
    r".*-s3.*",
]


# =============================================================================
# Native K8s Secrets Processing
# =============================================================================

def process_native_secret(secret: dict) -> dict:
    """Process a native Kubernetes Secret."""
    metadata = secret.get("metadata", {})
    secret_type = secret.get("type", "Opaque")
    data = secret.get("data", {})

    name = metadata.get("name", "unknown")
    namespace = metadata.get("namespace", "default")
    creation_timestamp = metadata.get("creationTimestamp", "")

    # Calculate age
    age = parse_age(creation_timestamp)

    # Get data keys (don't expose values)
    data_keys = list(data.keys()) if data else []

    # Determine category based on name patterns
    category = "other"
    for pattern in STORAGE_SECRET_PATTERNS:
        if re.match(pattern, name, re.IGNORECASE):
            category = "storage"
            break

    if secret_type == "kubernetes.io/service-account-token":
        category = "service-account"
    elif secret_type == "kubernetes.io/dockerconfigjson":
        category = "docker-registry"
    elif secret_type == "kubernetes.io/tls":
        category = "tls"

    return {
        "name": name,
        "namespace": namespace,
        "type": secret_type,
        "category": category,
        "dataKeys": data_keys,
        "keyCount": len(data_keys),
        "createdAt": creation_timestamp,
        "age": age,
        "labels": metadata.get("labels", {}),
        "annotations": {
            k: v for k, v in metadata.get("annotations", {}).items()
            if not k.startswith("kubectl.kubernetes.io/")
        },
    }


def parse_age(creation_timestamp: str) -> str:
    """Convert creation timestamp to human-readable age."""
    if not creation_timestamp:
        return "unknown"
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


def detect_native_issues(
    processed_secrets: list[dict],
    expected_secrets: list[str],
) -> list[dict]:
    """Detect issues in native K8s secrets."""
    issues = []

    found_secrets = {s["name"] for s in processed_secrets}

    # Check expected secrets
    for expected in expected_secrets or []:
        if expected not in found_secrets:
            issues.append({
                "name": expected,
                "type": "Secret",
                "severity": "warning",
                "error": "Expected secret not found",
                "lastAttempt": None,
            })

    # Check for empty secrets
    for secret in processed_secrets:
        if secret["keyCount"] == 0:
            issues.append({
                "name": secret["name"],
                "type": "Secret",
                "severity": "warning",
                "error": "Secret has no data keys",
                "lastAttempt": None,
            })

    return issues


# =============================================================================
# ExternalSecrets CRD Processing (preserved for future use)
# =============================================================================

def parse_duration(duration_str: str) -> int:
    """
    Parse duration string (e.g., '1h', '30m', '24h', '1h30m') to seconds.
    """
    if not duration_str:
        return 3600  # Default 1h

    total_seconds = 0
    pattern = r"(\d+)([hms])"
    matches = re.findall(pattern, duration_str.lower())

    for value, unit in matches:
        value = int(value)
        if unit == "h":
            total_seconds += value * 3600
        elif unit == "m":
            total_seconds += value * 60
        elif unit == "s":
            total_seconds += value

    return total_seconds if total_seconds > 0 else 3600


def parse_iso_timestamp(timestamp_str: str) -> Optional[datetime]:
    """Parse ISO timestamp string to datetime object."""
    if not timestamp_str:
        return None
    try:
        if timestamp_str.endswith("Z"):
            timestamp_str = timestamp_str[:-1] + "+00:00"
        return datetime.fromisoformat(timestamp_str)
    except Exception:
        return None


def calculate_age_seconds(timestamp_str: str) -> Optional[int]:
    """Calculate age in seconds from timestamp."""
    parsed = parse_iso_timestamp(timestamp_str)
    if not parsed:
        return None
    now = datetime.now(timezone.utc)
    return int((now - parsed).total_seconds())


def is_sync_stale(last_sync_time: str, refresh_interval: str) -> bool:
    """Check if the sync is stale (older than 2x refresh interval)."""
    age = calculate_age_seconds(last_sync_time)
    if age is None:
        return True

    interval_seconds = parse_duration(refresh_interval)
    threshold = interval_seconds * STALE_MULTIPLIER

    return age > threshold


def extract_data_keys(external_secret: dict) -> list[str]:
    """Extract data keys from ExternalSecret spec."""
    keys = []
    spec = external_secret.get("spec", {})

    data = spec.get("data", [])
    for item in data:
        secret_key = item.get("secretKey")
        if secret_key:
            keys.append(secret_key)

    data_from = spec.get("dataFrom", [])
    for item in data_from:
        extract = item.get("extract", {})
        if extract.get("key"):
            keys.append(f"extract:{extract['key']}")

        find = item.get("find", {})
        if find:
            keys.append(f"find:{find.get('path', '*')}")

    return keys


def process_external_secret(es: dict) -> dict:
    """Process a single ExternalSecret and extract relevant information."""
    metadata = es.get("metadata", {})
    spec = es.get("spec", {})
    status = es.get("status", {})

    name = metadata.get("name", "unknown")
    namespace = metadata.get("namespace", "default")

    store_ref = spec.get("secretStoreRef", {})
    store_name = store_ref.get("name", "unknown")
    store_kind = store_ref.get("kind", "SecretStore")

    refresh_interval = spec.get("refreshInterval", "1h")

    target = spec.get("target", {})
    target_secret = target.get("name", name)

    conditions = status.get("conditions", [])
    ready_condition = None

    for condition in conditions:
        if condition.get("type") == "Ready":
            ready_condition = condition
            break

    is_ready = ready_condition and ready_condition.get("status") == "True"

    if is_ready:
        sync_status = "SecretSynced"
    else:
        reason = ready_condition.get("reason", "Unknown") if ready_condition else "Unknown"
        sync_status = reason

    last_sync_time = status.get("refreshTime") or status.get("syncedResourceVersion")
    if ready_condition and ready_condition.get("lastTransitionTime"):
        last_sync_time = ready_condition.get("lastTransitionTime")

    stale = False
    if last_sync_time and is_ready:
        stale = is_sync_stale(last_sync_time, refresh_interval)

    data_keys = extract_data_keys(es)

    return {
        "name": name,
        "namespace": namespace,
        "secretStore": store_name,
        "secretStoreKind": store_kind,
        "status": sync_status,
        "lastSyncTime": last_sync_time,
        "refreshInterval": refresh_interval,
        "targetSecret": target_secret,
        "conditions": {
            "Ready": is_ready,
            "message": ready_condition.get("message", "") if ready_condition else "",
        },
        "dataKeys": data_keys,
        "sourceRef": {
            "kind": store_kind,
            "name": store_name,
        },
        "stale": stale,
    }


def process_secret_store(store: dict, kind: str = "SecretStore") -> dict:
    """Process a SecretStore or ClusterSecretStore."""
    metadata = store.get("metadata", {})
    spec = store.get("spec", {})
    status = store.get("status", {})

    name = metadata.get("name", "unknown")

    provider_config = spec.get("provider", {})
    provider = "unknown"
    region = ""

    if "aws" in provider_config:
        provider = "aws"
        aws_config = provider_config["aws"]
        service = aws_config.get("service", "SecretsManager")
        region = aws_config.get("region", "")
        if service == "SecretsManager":
            provider = "aws-secrets-manager"
        elif service == "ParameterStore":
            provider = "aws-parameter-store"
    elif "vault" in provider_config:
        provider = "vault"
        vault_config = provider_config["vault"]
        region = vault_config.get("server", "")
    elif "azurekv" in provider_config:
        provider = "azure-keyvault"
    elif "gcpsm" in provider_config:
        provider = "gcp-secret-manager"

    conditions = status.get("conditions", [])
    is_ready = False
    status_text = "Unknown"

    for condition in conditions:
        if condition.get("type") == "Ready":
            is_ready = condition.get("status") == "True"
            status_text = "Valid" if is_ready else "Invalid"
            break

    return {
        "name": name,
        "kind": kind,
        "status": status_text,
        "provider": provider,
        "region": region,
        "conditions": {
            "Ready": is_ready,
        },
    }


def detect_external_issues(
    processed_secrets: list[dict],
    processed_stores: list[dict],
    expected_secrets: list[str],
) -> list[dict]:
    """Detect issues in external secrets and stores."""
    issues = []

    for es in processed_secrets:
        name = es["name"]

        if es["status"] != "SecretSynced":
            issues.append({
                "name": name,
                "type": "ExternalSecret",
                "severity": "critical",
                "error": es["conditions"].get("message", es["status"]),
                "lastAttempt": es.get("lastSyncTime"),
            })
        elif es.get("stale"):
            issues.append({
                "name": name,
                "type": "ExternalSecret",
                "severity": "warning",
                "error": f"Sync is stale (last sync: {es.get('lastSyncTime')})",
                "lastAttempt": es.get("lastSyncTime"),
            })

    for store in processed_stores:
        if store["status"] != "Valid":
            issues.append({
                "name": store["name"],
                "type": store["kind"],
                "severity": "critical",
                "error": f"Store is {store['status']}",
                "lastAttempt": None,
            })

    found_secrets = {s["name"] for s in processed_secrets}
    for expected in expected_secrets or []:
        if expected not in found_secrets:
            issues.append({
                "name": expected,
                "type": "ExpectedSecret",
                "severity": "warning",
                "error": "Expected secret not found",
                "lastAttempt": None,
            })

    return issues


# =============================================================================
# Main Handler
# =============================================================================

def determine_status(summary: dict, issues: list[dict]) -> str:
    """Determine overall status based on summary and issues."""
    critical_issues = [i for i in issues if i.get("severity") == "critical"]
    warning_issues = [i for i in issues if i.get("severity") == "warning"]

    if critical_issues or summary.get("failed", 0) > 0:
        return "critical"

    if warning_issues or summary.get("stale", 0) > 0:
        return "warning"

    return "ok"


def lambda_handler(event: dict, context: Any) -> dict:
    """
    Process Secrets data from eks:call response.

    Supports two modes:
    - native: Native K8s Secrets (default)
    - external: ExternalSecrets CRDs (requires RBAC)

    Event structure (from Step Function):
    {
        "mode": "native" | "external",
        # For native mode:
        "secrets": [<raw Secret objects from K8s API>],
        # For external mode:
        "externalSecrets": [<raw ExternalSecret objects>],
        "secretStores": [<raw SecretStore objects>],
        "clusterSecretStores": [<raw ClusterSecretStore objects>],
        # Common:
        "expectedSecrets": ["hybris-secrets", "db-credentials"],
        "namespace": "hybris",
        "source": "legacy" | "nh",
        "cluster_name": "rubix-nonprod",
        "domain": "mro",
        "target": "mi1-ppd-legacy"
    }
    """
    mode = event.get("mode", "native")
    source = event.get("source", "unknown")
    namespace = event.get("namespace", "unknown")
    expected_secrets = event.get("expectedSecrets") or []

    logger.info(f"Processing secrets for {event.get('target')} in {mode} mode")

    if mode == "external":
        # ExternalSecrets CRD mode
        raw_external_secrets = event.get("externalSecrets") or []
        raw_secret_stores = event.get("secretStores") or []
        raw_cluster_secret_stores = event.get("clusterSecretStores") or []

        summary = {
            "mode": "external",
            "total": 0,
            "synced": 0,
            "failed": 0,
            "stale": 0,
            "byProvider": {},
        }

        processed_secrets = []
        for es in raw_external_secrets:
            processed = process_external_secret(es)
            processed_secrets.append(processed)

            summary["total"] += 1

            if processed["status"] == "SecretSynced":
                if processed.get("stale"):
                    summary["stale"] += 1
                else:
                    summary["synced"] += 1
            else:
                summary["failed"] += 1

            store = processed["secretStore"]
            summary["byProvider"][store] = summary["byProvider"].get(store, 0) + 1

        processed_stores = []
        for store in raw_secret_stores:
            processed = process_secret_store(store, "SecretStore")
            processed_stores.append(processed)

        for store in raw_cluster_secret_stores:
            processed = process_secret_store(store, "ClusterSecretStore")
            processed_stores.append(processed)

        issues = detect_external_issues(processed_secrets, processed_stores, expected_secrets)

        # Include stores in output
        result_secrets = processed_secrets

    else:
        # Native K8s Secrets mode (default)
        raw_secrets = event.get("secrets") or []
        data_limit_exceeded = event.get("dataLimitExceeded", False)

        summary = {
            "mode": "native",
            "total": 0,
            "byType": {},
            "byCategory": {},
        }

        # Handle data limit exceeded case (too many secrets for Step Functions)
        if data_limit_exceeded:
            summary["dataLimitExceeded"] = True
            summary["note"] = "Secrets data exceeded Step Functions 256KB limit. Use kubectl to inspect secrets directly."
            processed_secrets = []
            issues = [{
                "name": "data_limit",
                "type": "System",
                "severity": "warning",
                "error": "Cannot retrieve secrets list - data exceeds Step Functions limit. Secrets exist but cannot be enumerated.",
                "lastAttempt": None,
            }]
            result_secrets = []
        else:
            processed_secrets = []
            for secret in raw_secrets:
                processed = process_native_secret(secret)
                processed_secrets.append(processed)

                summary["total"] += 1

                secret_type = processed["type"]
                summary["byType"][secret_type] = summary["byType"].get(secret_type, 0) + 1

                category = processed["category"]
                summary["byCategory"][category] = summary["byCategory"].get(category, 0) + 1

            issues = detect_native_issues(processed_secrets, expected_secrets)
            result_secrets = processed_secrets

    status = determine_status(summary, issues)
    timestamp = datetime.now(timezone.utc).isoformat()

    result = {
        "status": status,
        "source": source,
        "namespace": namespace,
        "summary": summary,
        "secrets": result_secrets,
        "issues": issues,
        "healthy": status == "ok",
        "timestamp": timestamp,
    }

    logger.info(
        f"Secret processing complete: mode={mode}, status={status}, "
        f"total={summary['total']}, issues={len(issues)}"
    )

    return result
