"""
Sync Config Items Lambda
=========================
Synchronizes configuration items (Secrets Manager secrets or SSM Parameters)
between AWS accounts. Called per-item by the SyncConfigItems Step Function
via Map state.

Implements cross-account fetch via STS AssumeRole, path mapping with glob
wildcards and {name} placeholder, JSON value transforms (replace/skip),
merge mode for preserving destination-only keys, auto-create/update for
missing secrets/parameters, and recursive SSM parameter traversal.

Input (per item, from SFN Map state):
{
    "Item": {
        "Type": "SecretsManager | SSMParameter",
        "SourcePath": "/path/to/source/secret-or-param",
        "DestinationPath": "/path/to/destination/secret-or-param",
        "MergeMode": true,  # optional, default false
        "Transforms": {
            "key": {"replace": [{"from": "old", "to": "new"}]},
            "other_key": {"skip": true}
        }
    },
    "SourceAccount": {
        "AccountId": "111111111111",
        "RoleArn": "arn:aws:iam::111111111111:role/source-role",
        "Region": "eu-central-1"
    },
    "DestinationAccount": {
        "AccountId": "222222222222",
        "RoleArn": "arn:aws:iam::222222222222:role/destination-role",
        "Region": "eu-central-1"
    }
}

Output:
{
    "statusCode": 200,
    "result": {
        "status": "synced | created | updated | skipped | error",
        "source": "/path/to/source/secret-or-param",
        "destination": "/path/to/destination/secret-or-param",
        "type": "SecretsManager | SSMParameter",
        "message": "Description of what happened"
    }
}

Environment Variables:
- LOG_LEVEL: Logging level (default: INFO)
"""

import fnmatch
import json
import logging
import os
from typing import Any

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)
logger.setLevel(os.environ.get("LOG_LEVEL", "INFO"))


# ---------------------------------------------------------------------------
# Cross-account access
# ---------------------------------------------------------------------------


def get_cross_account_client(
    service: str,
    role_arn: str,
    region: str,
    session_name: str = "SyncConfigItems",
) -> Any:
    """
    Get a boto3 client for cross-account access via STS AssumeRole.

    Args:
        service: AWS service name (e.g., 'secretsmanager', 'ssm')
        role_arn: ARN of the role to assume in the target account
        region: AWS region
        session_name: Session name for the assumed role

    Returns:
        boto3 client configured with assumed role credentials
    """
    sts_client = boto3.client("sts")

    try:
        assumed_role = sts_client.assume_role(
            RoleArn=role_arn,
            RoleSessionName=session_name,
            DurationSeconds=900,  # 15 minutes
        )

        credentials = assumed_role["Credentials"]

        return boto3.client(
            service,
            region_name=region,
            aws_access_key_id=credentials["AccessKeyId"],
            aws_secret_access_key=credentials["SecretAccessKey"],
            aws_session_token=credentials["SessionToken"],
        )
    except ClientError as e:
        logger.error(f"Failed to assume role {role_arn}: {e}")
        raise


# ---------------------------------------------------------------------------
# Wildcard / path mapping
# ---------------------------------------------------------------------------


def list_matching_secrets(source_client: Any, source_path: str) -> list[str]:
    """
    List secret names matching a glob pattern.

    If source_path has no wildcard, returns [source_path].
    Otherwise, lists all secrets via paginator and filters with fnmatch.

    Args:
        source_client: boto3 Secrets Manager client
        source_path: Glob pattern or literal path

    Returns:
        List of matching secret names
    """
    if "*" not in source_path and "?" not in source_path:
        return [source_path]

    matching = []
    paginator = source_client.get_paginator("list_secrets")
    for page in paginator.paginate():
        for secret in page.get("SecretList", []):
            name = secret.get("Name", "")
            if fnmatch.fnmatch(name, source_path):
                matching.append(name)

    return matching


def list_matching_parameters(
    source_client: Any, source_path: str
) -> list[dict]:
    """
    List SSM parameters matching a glob pattern via recursive traversal.

    If source_path has no wildcard, returns [{"Name": source_path}].
    Otherwise, extracts the prefix before the first wildcard and uses
    get_parameters_by_path(Recursive=True) to list, then filters with fnmatch.

    Args:
        source_client: boto3 SSM client
        source_path: Glob pattern or literal path

    Returns:
        List of matching parameter dicts (Name, Value, Type)
    """
    if "*" not in source_path and "?" not in source_path:
        return [{"Name": source_path}]

    # Extract prefix before first wildcard
    wildcard_idx = source_path.index("*")
    prefix = source_path[:wildcard_idx]
    # Ensure prefix ends at a path boundary for get_parameters_by_path
    if prefix and not prefix.endswith("/"):
        prefix = prefix[: prefix.rfind("/") + 1]

    matching = []
    paginator = source_client.get_paginator("get_parameters_by_path")
    for page in paginator.paginate(
        Path=prefix if prefix else "/",
        Recursive=True,
        WithDecryption=True,
    ):
        for param in page.get("Parameters", []):
            name = param.get("Name", "")
            if fnmatch.fnmatch(name, source_path):
                matching.append(param)

    return matching


def resolve_wildcard_items(
    source_path: str,
    dest_pattern: str,
    source_client: Any,
    item_type: str,
) -> list[tuple[str, str]]:
    """
    Expand wildcard SourcePath to concrete (source, destination) pairs.

    If source_path has no wildcard, returns [(source_path, dest_pattern)].
    Otherwise, lists matching items and applies {name} placeholder mapping.

    Args:
        source_path: Source path (may contain glob wildcards)
        dest_pattern: Destination path pattern (may contain {name})
        source_client: boto3 client for the source service
        item_type: "SecretsManager" or "SSMParameter"

    Returns:
        List of (source_name, dest_path) tuples
    """
    if "*" not in source_path and "?" not in source_path:
        return [(source_path, dest_pattern)]

    # Find prefix before first wildcard
    wildcard_idx = source_path.index("*")
    prefix = source_path[:wildcard_idx]

    if item_type == "SecretsManager":
        names = list_matching_secrets(source_client, source_path)
        pairs = []
        for name in names:
            dest_path = map_destination_path(name, source_path, dest_pattern)
            pairs.append((name, dest_path))
        return pairs
    else:
        params = list_matching_parameters(source_client, source_path)
        pairs = []
        for param in params:
            name = param["Name"]
            dest_path = map_destination_path(name, source_path, dest_pattern)
            pairs.append((name, dest_path))
        return pairs


def map_destination_path(
    source_path: str, source_pattern: str, dest_pattern: str
) -> str:
    """
    Map a concrete source path to a destination path using {name} placeholder.

    The {name} is the part of source_path after the prefix (part before
    first wildcard in source_pattern).

    Args:
        source_path: Concrete source path (e.g., "/app/prod/secret-a")
        source_pattern: Source pattern with wildcard (e.g., "/app/prod/*")
        dest_pattern: Destination pattern with {name} (e.g., "/dest/{name}")

    Returns:
        Resolved destination path
    """
    wildcard_idx = source_pattern.index("*")
    prefix = source_pattern[:wildcard_idx]
    name_part = source_path[len(prefix):]
    return dest_pattern.replace("{name}", name_part)


# ---------------------------------------------------------------------------
# Transforms
# ---------------------------------------------------------------------------


def apply_transforms(value: str, transforms: dict) -> str:
    """
    Apply transforms to a secret/parameter value.

    For JSON values: transforms apply per-key (replace values, skip keys).
    For string values: transforms apply on the raw string.

    Args:
        value: The raw value string
        transforms: Dict of key -> transform spec

    Returns:
        Transformed value string
    """
    if not transforms:
        return value

    try:
        data = json.loads(value)
        if isinstance(data, dict):
            return apply_json_transforms(data, transforms)
    except (json.JSONDecodeError, TypeError):
        pass

    return apply_string_transforms(value, transforms)


def apply_json_transforms(data: dict, transforms: dict) -> str:
    """
    Apply per-key transforms to a JSON dict.

    - skip: true -> remove key from result
    - replace: [{from, to}] -> apply replacements on the key's value

    Args:
        data: Parsed JSON dict
        transforms: Dict of key -> transform spec

    Returns:
        JSON string with transforms applied
    """
    result = dict(data)

    for key, transform in transforms.items():
        if transform.get("skip"):
            if key in result:
                del result[key]
            continue
        if key in result and "replace" in transform:
            for replacement in transform["replace"]:
                val = str(result[key])
                result[key] = val.replace(
                    replacement["from"], replacement["to"]
                )

    return json.dumps(result)


def apply_string_transforms(value: str, transforms: dict) -> str:
    """
    Apply transforms on a raw string value.

    All replace rules across all keys are applied sequentially on the
    full string.

    Args:
        value: Raw string value
        transforms: Dict of key -> transform spec

    Returns:
        Transformed string
    """
    result = value
    for _key, transform in transforms.items():
        for replacement in transform.get("replace", []):
            result = result.replace(replacement["from"], replacement["to"])
    return result


# ---------------------------------------------------------------------------
# Merge mode
# ---------------------------------------------------------------------------


def merge_values(
    source_value: dict, destination_value: dict, transforms: dict
) -> dict:
    """
    Merge source into destination, preserving destination-only keys.

    Rules:
    - Keys with explicit Transform + skip: preserve destination value
    - Keys with explicit Transform (replace): use transformed source value
    - Common keys without Transform: keep destination value (destination wins)
    - Source-only keys: copy from source
    - Destination-only keys: preserve

    Args:
        source_value: Parsed source JSON dict
        destination_value: Parsed destination JSON dict
        transforms: Dict of key -> transform spec

    Returns:
        Merged dict (caller serializes)
    """
    result = dict(destination_value)

    for key, value in source_value.items():
        if key in transforms:
            if transforms[key].get("skip"):
                continue  # Preserve destination value for skipped keys
            # Apply transform and use transformed source value
            transformed = str(value)
            for replacement in transforms[key].get("replace", []):
                transformed = transformed.replace(
                    replacement["from"], replacement["to"]
                )
            result[key] = transformed
        elif key not in destination_value:
            # Source-only key: copy from source
            result[key] = value
        # else: common key without transform -> keep destination (already in result)

    return result


# ---------------------------------------------------------------------------
# SM sync
# ---------------------------------------------------------------------------


def sync_secret(event: dict) -> dict:
    """
    Sync a Secrets Manager secret from source to destination.

    Handles: cross-account fetch, wildcard expansion, transforms,
    merge mode, and auto-create.

    Args:
        event: Full Lambda event with Item, SourceAccount, DestinationAccount

    Returns:
        Result dict with status, source, destination, type, message
    """
    item = event["Item"]
    source_account = event["SourceAccount"]
    dest_account = event["DestinationAccount"]

    source_path = item["SourcePath"]
    dest_path = item["DestinationPath"]
    transforms = item.get("Transforms", {})
    merge_mode = item.get("MergeMode", False)

    # Get cross-account clients
    source_client = get_cross_account_client(
        "secretsmanager",
        source_account["RoleArn"],
        source_account["Region"],
    )
    dest_client = get_cross_account_client(
        "secretsmanager",
        dest_account["RoleArn"],
        dest_account["Region"],
    )

    # Resolve wildcards
    pairs = resolve_wildcard_items(
        source_path, dest_path, source_client, "SecretsManager"
    )

    if len(pairs) == 0:
        return {
            "status": "synced",
            "source": source_path,
            "destination": dest_path,
            "type": "SecretsManager",
            "items_synced": 0,
            "items_failed": 0,
            "message": "No matching secrets found",
        }

    # Single item (no wildcard or single match)
    if len(pairs) == 1 and "*" not in source_path:
        src, dst = pairs[0]
        result = _sync_single_secret(
            source_client, dest_client, src, dst, transforms, merge_mode
        )
        return result

    # Multiple items (wildcard expansion)
    details = []
    synced = 0
    failed = 0

    for src, dst in pairs:
        try:
            result = _sync_single_secret(
                source_client, dest_client, src, dst, transforms, merge_mode
            )
            details.append(
                {"source": src, "destination": dst, "status": result["status"]}
            )
            if result["status"] == "error":
                failed += 1
            else:
                synced += 1
        except Exception as e:
            logger.error(f"Error syncing {src} -> {dst}: {e}")
            details.append(
                {"source": src, "destination": dst, "status": "error"}
            )
            failed += 1

    status = "synced" if failed == 0 else "partial" if synced > 0 else "error"
    return {
        "status": status,
        "source": source_path,
        "destination": dest_path,
        "type": "SecretsManager",
        "items_synced": synced,
        "items_failed": failed,
        "details": details,
        "message": f"{synced} items synced"
        + (f", {failed} failed" if failed else ""),
    }


def _sync_single_secret(
    source_client: Any,
    dest_client: Any,
    source_path: str,
    dest_path: str,
    transforms: dict,
    merge_mode: bool,
) -> dict:
    """
    Sync a single secret from source to destination.

    Args:
        source_client: Source SM client
        dest_client: Destination SM client
        source_path: Source secret name
        dest_path: Destination secret name
        transforms: Transform specs
        merge_mode: Whether to merge with existing destination

    Returns:
        Result dict
    """
    # Fetch source value
    source_response = source_client.get_secret_value(SecretId=source_path)
    source_value = source_response["SecretString"]

    # Apply transforms
    transformed_value = apply_transforms(source_value, transforms)

    # Handle merge mode
    if merge_mode:
        transformed_value = _handle_merge_mode(
            dest_client, dest_path, source_value, transformed_value, transforms
        )

    # Write to destination
    write_status = _write_secret(dest_client, dest_path, transformed_value)

    return {
        "status": write_status,
        "source": source_path,
        "destination": dest_path,
        "type": "SecretsManager",
        "message": f"Secret {write_status} successfully",
    }


def _handle_merge_mode(
    dest_client: Any,
    dest_path: str,
    source_value: str,
    transformed_value: str,
    transforms: dict,
) -> str:
    """
    Handle merge mode for a secret.

    For JSON secrets: merge source into destination preserving dest-only keys.
    For non-JSON secrets: keep destination value if exists, else use source.

    Args:
        dest_client: Destination SM client
        dest_path: Destination secret name
        source_value: Raw source value
        transformed_value: Source value after transforms
        transforms: Transform specs

    Returns:
        Final value to write
    """
    try:
        dest_response = dest_client.get_secret_value(SecretId=dest_path)
        dest_value = dest_response["SecretString"]
    except Exception:
        # Destination doesn't exist -- use transformed source
        return transformed_value

    # Try JSON merge
    try:
        source_data = json.loads(source_value)
        dest_data = json.loads(dest_value)
        if isinstance(source_data, dict) and isinstance(dest_data, dict):
            merged = merge_values(source_data, dest_data, transforms)
            return json.dumps(merged)
    except (json.JSONDecodeError, TypeError):
        pass

    # Non-JSON merge: keep destination value (it exists)
    return dest_value


def _write_secret(dest_client: Any, dest_path: str, value: str) -> str:
    """
    Write a secret to the destination account.

    Tries put_secret_value first (update). If ResourceNotFoundException,
    falls back to create_secret.

    Args:
        dest_client: Destination SM client
        dest_path: Secret name
        value: Secret value string

    Returns:
        "synced" (update) or "created"
    """
    try:
        dest_client.put_secret_value(SecretId=dest_path, SecretString=value)
        return "synced"
    except dest_client.exceptions.ResourceNotFoundException:
        dest_client.create_secret(Name=dest_path, SecretString=value)
        return "created"


# ---------------------------------------------------------------------------
# SSM sync
# ---------------------------------------------------------------------------


def sync_parameter(event: dict) -> dict:
    """
    Sync an SSM parameter from source to destination.

    Handles: cross-account fetch, wildcard expansion with recursive
    traversal, transforms, and auto-create with Type preservation.

    Args:
        event: Full Lambda event with Item, SourceAccount, DestinationAccount

    Returns:
        Result dict with status, source, destination, type, message
    """
    item = event["Item"]
    source_account = event["SourceAccount"]
    dest_account = event["DestinationAccount"]

    source_path = item["SourcePath"]
    dest_path = item["DestinationPath"]
    transforms = item.get("Transforms", {})

    # Get cross-account clients
    source_client = get_cross_account_client(
        "ssm",
        source_account["RoleArn"],
        source_account["Region"],
    )
    dest_client = get_cross_account_client(
        "ssm",
        dest_account["RoleArn"],
        dest_account["Region"],
    )

    # Check for wildcard -- use recursive traversal
    if "*" in source_path or "?" in source_path:
        return _sync_parameters_recursive(
            source_client,
            dest_client,
            source_path,
            dest_path,
            transforms,
        )

    # Single parameter
    response = source_client.get_parameter(
        Name=source_path, WithDecryption=True
    )
    param = response["Parameter"]
    value = param["Value"]
    param_type = param["Type"]

    # Apply transforms
    transformed_value = apply_transforms(value, transforms)

    # Write to destination
    dest_client.put_parameter(
        Name=dest_path,
        Value=transformed_value,
        Type=param_type,
        Overwrite=True,
    )

    return {
        "status": "synced",
        "source": source_path,
        "destination": dest_path,
        "type": "SSMParameter",
        "message": "Parameter synced successfully",
    }


def _sync_parameters_recursive(
    source_client: Any,
    dest_client: Any,
    source_path: str,
    dest_pattern: str,
    transforms: dict,
) -> dict:
    """
    Sync multiple SSM parameters matching a wildcard pattern.

    Uses get_parameters_by_path for recursive traversal and applies
    path mapping per parameter.

    Args:
        source_client: Source SSM client
        dest_client: Destination SSM client
        source_path: Source path pattern with wildcards
        dest_pattern: Destination pattern with {name} placeholder
        transforms: Transform specs

    Returns:
        Aggregated result dict
    """
    params = list_matching_parameters(source_client, source_path)

    if not params:
        return {
            "status": "synced",
            "source": source_path,
            "destination": dest_pattern,
            "type": "SSMParameter",
            "items_synced": 0,
            "items_failed": 0,
            "message": "No matching parameters found",
        }

    details = []
    synced = 0
    failed = 0

    for param in params:
        name = param["Name"]
        value = param.get("Value", "")
        param_type = param.get("Type", "String")
        dest_path = map_destination_path(name, source_path, dest_pattern)

        try:
            transformed_value = apply_transforms(value, transforms)
            dest_client.put_parameter(
                Name=dest_path,
                Value=transformed_value,
                Type=param_type,
                Overwrite=True,
            )
            details.append(
                {"source": name, "destination": dest_path, "status": "synced"}
            )
            synced += 1
        except Exception as e:
            logger.error(f"Error syncing param {name} -> {dest_path}: {e}")
            details.append(
                {"source": name, "destination": dest_path, "status": "error"}
            )
            failed += 1

    status = "synced" if failed == 0 else "partial" if synced > 0 else "error"
    return {
        "status": status,
        "source": source_path,
        "destination": dest_pattern,
        "type": "SSMParameter",
        "items_synced": synced,
        "items_failed": failed,
        "details": details,
        "message": f"{synced} parameters synced"
        + (f", {failed} failed" if failed else ""),
    }


# ---------------------------------------------------------------------------
# Lambda entry point
# ---------------------------------------------------------------------------


def lambda_handler(event: dict, context: Any) -> dict:
    """
    Lambda entry point -- error-safe wrapper.

    Routes to sync_secret or sync_parameter based on Item.Type.
    Catches ALL exceptions and returns structured error results (never raises).

    Args:
        event: Per-item input with Item, SourceAccount, DestinationAccount
        context: Lambda context (unused)

    Returns:
        Structured output with statusCode and result
    """
    item = event.get("Item", {})
    source_path = item.get("SourcePath", "")
    dest_path = item.get("DestinationPath", "")
    item_type = item.get("Type", "unknown")

    try:
        logger.info(
            "Processing item: type=%s, source=%s, destination=%s",
            item_type,
            source_path,
            dest_path,
        )

        if item_type == "SecretsManager":
            result = sync_secret(event)
        elif item_type == "SSMParameter":
            result = sync_parameter(event)
        else:
            return {
                "statusCode": 200,
                "result": {
                    "status": "error",
                    "source": source_path,
                    "destination": dest_path,
                    "type": item_type,
                    "message": f"Unsupported item type: {item_type}",
                },
            }

        return {"statusCode": 200, "result": result}

    except Exception as e:
        logger.exception("Unhandled error syncing item")
        return {
            "statusCode": 200,
            "result": {
                "status": "error",
                "source": source_path,
                "destination": dest_path,
                "type": item_type,
                "message": str(e),
            },
        }
