"""
Fetch Secrets Manager State
============================
Fetches AWS Secrets Manager secrets and stores metadata in DynamoDB.
Does NOT store secret values - only hashes for comparison.

This Lambda:
- Lists secrets matching configured patterns
- Extracts metadata (ARN, dates, rotation status)
- Extracts JSON key names from secret values
- Computes SHA256 hashes of values for secure comparison
- Stores state in DynamoDB

Environment Variables:
- STATE_TABLE_NAME: DynamoDB table name
- LOG_LEVEL: Logging level (default: INFO)

Security:
- Secret values are NEVER stored or logged
- Only key names and value hashes are persisted
"""

import hashlib
import json
import logging
import os
import re
from datetime import datetime, timezone
from typing import Any, Optional

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)
logger.setLevel(os.environ.get("LOG_LEVEL", "INFO"))


def get_cross_account_client(
    service: str,
    role_arn: str,
    region: str,
    session_name: str = "DashboardFetchSecrets",
) -> Any:
    """
    Get a boto3 client for cross-account access via STS AssumeRole.

    Args:
        service: AWS service name (e.g., 'secretsmanager')
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


def compute_hash(value: str) -> str:
    """Compute SHA256 hash of a value."""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def compute_keys_hash(keys: list) -> str:
    """Compute hash of sorted key list."""
    sorted_keys = sorted(keys)
    return compute_hash(json.dumps(sorted_keys))


def match_patterns(name: str, patterns: list) -> bool:
    """Check if secret name matches any of the patterns."""
    for pattern in patterns:
        # Convert glob pattern to regex
        regex = pattern.replace("*", ".*").replace("?", ".")
        if re.match(f"^{regex}$", name):
            return True
    return False


def parse_secret_value(value_str: str) -> tuple[list, dict]:
    """
    Parse secret value and extract keys with their value hashes.

    Returns:
        tuple: (list of key names, dict of key -> value_hash)
    """
    try:
        data = json.loads(value_str)
        if isinstance(data, dict):
            keys_list = list(data.keys())
            value_hashes = {
                k: compute_hash(json.dumps(v, sort_keys=True) if isinstance(v, (dict, list)) else str(v))
                for k, v in data.items()
            }
            return keys_list, value_hashes
        else:
            # Not a JSON object, treat as single value
            return [], {"_value": compute_hash(value_str)}
    except json.JSONDecodeError:
        # Not JSON, treat as plain text
        return [], {"_value": compute_hash(value_str)}


def fetch_secrets_metadata(
    client: Any,
    patterns: list,
    include_values: bool = True,
) -> dict:
    """
    Fetch secrets metadata from AWS Secrets Manager.

    Args:
        client: boto3 Secrets Manager client
        patterns: List of glob patterns to match secret names
        include_values: Whether to fetch values for key extraction

    Returns:
        dict with secrets metadata
    """
    secrets = {}
    secrets_list = []

    # List all secrets
    paginator = client.get_paginator("list_secrets")

    for page in paginator.paginate():
        for secret in page.get("SecretList", []):
            name = secret.get("Name", "")

            # Check if name matches any pattern
            if patterns and not match_patterns(name, patterns):
                continue

            secret_metadata = {
                "arn": secret.get("ARN"),
                "name": name,
                "createdDate": secret.get("CreatedDate").isoformat() if secret.get("CreatedDate") else None,
                "lastChangedDate": secret.get("LastChangedDate").isoformat() if secret.get("LastChangedDate") else None,
                "lastAccessedDate": secret.get("LastAccessedDate").isoformat() if secret.get("LastAccessedDate") else None,
                "rotationEnabled": secret.get("RotationEnabled", False),
                "rotationRules": secret.get("RotationRules"),
                "versionIdsToStages": secret.get("SecretVersionsToStages"),
                "tags": {t.get("Key"): t.get("Value") for t in secret.get("Tags", [])},
            }

            # Fetch secret value if requested (for key extraction and hashing)
            if include_values:
                try:
                    value_response = client.get_secret_value(SecretId=name)
                    secret_string = value_response.get("SecretString", "")

                    if secret_string:
                        keys_list, value_hashes = parse_secret_value(secret_string)
                        secret_metadata["keysList"] = keys_list
                        secret_metadata["keysHash"] = compute_keys_hash(keys_list)
                        secret_metadata["valueHashes"] = value_hashes
                        secret_metadata["isJson"] = len(keys_list) > 0
                    else:
                        # Binary secret
                        binary = value_response.get("SecretBinary", b"")
                        secret_metadata["keysList"] = []
                        secret_metadata["keysHash"] = ""
                        secret_metadata["valueHashes"] = {"_binary": compute_hash(binary.hex())}
                        secret_metadata["isJson"] = False
                        secret_metadata["isBinary"] = True

                except ClientError as e:
                    error_code = e.response.get("Error", {}).get("Code", "")
                    logger.warning(f"Could not get value for secret {name}: {error_code}")
                    secret_metadata["error"] = error_code
                    secret_metadata["keysList"] = []
                    secret_metadata["keysHash"] = ""
                    secret_metadata["valueHashes"] = {}

            secrets[name] = secret_metadata
            secrets_list.append(name)

    return {
        "secrets": secrets,
        "secretsList": secrets_list,
    }


def determine_status(secrets: dict) -> str:
    """Determine overall status based on secrets state."""
    if not secrets:
        return "warning"

    errors = [s for s in secrets.values() if s.get("error")]
    if errors:
        if len(errors) == len(secrets):
            return "critical"
        return "warning"

    return "ok"


def lambda_handler(event: dict, context: Any) -> dict:
    """
    Lambda entry point.

    Supports two formats:

    New format (Dashborion):
    {
        "Project": "mro-mi2",
        "Env": "legacy-ppd",
        "Instance": "MI2",
        "Environment": "ppd",
        "Region": "eu-central-1",
        "RoleArn": "arn:aws:iam::...",
        "SecretPatterns": ["rubix/*"],
        "SaveState": false
    }

    Legacy format:
    {
        "Domain": "config",
        "Target": "mi1-ppd-source",
        "Instance": "MI1",
        "Environment": "ppd",
        "Source": "source",
        "Region": "eu-central-1",
        "RoleArn": "arn:aws:iam::...",
        "SecretPatterns": ["rubix/*", "hybris/*"],
        "SaveState": true
    }

    Returns:
    {
        "statusCode": 200,
        "payload": {
            "status": "ok | warning | critical",
            "summary": {
                "totalSecrets": 35,
                "rotationEnabled": 10,
                "recentlyAccessed": 30,
                "withErrors": 0
            },
            "secrets": { ... },
            "secretsList": [...],
            "timestamp": "ISO8601"
        }
    }
    """
    logger.info(f"Event: {json.dumps(event)}")

    # Detect format and extract parameters
    if "Project" in event and "Env" in event:
        # New Dashborion format
        project = event.get("Project", "")
        env = event.get("Env", "")
        domain = project  # For state saving compatibility
        target = env
        source = "legacy" if "legacy" in env.lower() else "nh"
    else:
        # Legacy format
        domain = event.get("Domain", "config")
        target = event.get("Target", "")
        source = event.get("Source", "")
        project = None
        env = None

    instance = event.get("Instance", "")
    environment = event.get("Environment", "")
    region = event.get("Region", "eu-central-1")
    role_arn = event.get("RoleArn", "")
    patterns = event.get("SecretPatterns", [])
    save_state = event.get("SaveState", True)

    if not target:
        return {
            "statusCode": 400,
            "body": json.dumps({"error": "Missing required parameter: Target or Env"})
        }

    # Initialize Secrets Manager client
    # Use cross-account role if provided, otherwise use Lambda's own role
    if role_arn:
        logger.info(f"Using cross-account role: {role_arn}")
        client = get_cross_account_client("secretsmanager", role_arn, region)
    else:
        client = boto3.client("secretsmanager", region_name=region)

    try:
        # Fetch secrets metadata
        result = fetch_secrets_metadata(
            client=client,
            patterns=patterns,
            include_values=True,
        )

        secrets = result["secrets"]
        secrets_list = result["secretsList"]

        # Compute summary
        now = datetime.now(timezone.utc)
        recent_threshold = 90  # days

        recently_accessed = 0
        rotation_enabled = 0
        with_errors = 0

        for s in secrets.values():
            if s.get("rotationEnabled"):
                rotation_enabled += 1
            if s.get("error"):
                with_errors += 1
            # Check if accessed recently
            last_accessed = s.get("lastAccessedDate")
            if last_accessed:
                try:
                    accessed_dt = datetime.fromisoformat(last_accessed.replace("Z", "+00:00"))
                    if (now - accessed_dt).days <= recent_threshold:
                        recently_accessed += 1
                except (ValueError, TypeError):
                    pass

        summary = {
            "totalSecrets": len(secrets),
            "rotationEnabled": rotation_enabled,
            "recentlyAccessed": recently_accessed,
            "withErrors": with_errors,
        }

        status = determine_status(secrets)

        payload = {
            "status": status,
            "source": source,
            "instance": instance,
            "environment": environment,
            "summary": summary,
            "secrets": secrets,
            "secretsList": secrets_list,
            "timestamp": now.isoformat(),
        }

        # Save state to DynamoDB if requested
        state_result = None
        if save_state:
            try:
                from state_manager import get_state_manager

                manager = get_state_manager()
                state_result = manager.update_state(
                    domain=domain,
                    target=target,
                    check_type="config-sm",
                    payload=payload,
                    updated_by=f"lambda:{context.function_name}" if context else "lambda:fetch-secrets",
                    metadata={
                        "instance": instance,
                        "environment": environment,
                        "source": source,
                        "region": region,
                    },
                )
                logger.info(f"State update result: {state_result}")
            except Exception as e:
                logger.exception(f"Error saving state: {e}")
                state_result = {"error": str(e)}

        return {
            "statusCode": 200,
            "payload": payload,
            "stateUpdate": state_result,
        }

    except ClientError as e:
        error_msg = str(e)
        logger.exception(f"AWS error: {error_msg}")
        return {
            "statusCode": 500,
            "body": json.dumps({"error": error_msg})
        }
    except Exception as e:
        error_msg = str(e)
        logger.exception(f"Error: {error_msg}")
        return {
            "statusCode": 500,
            "body": json.dumps({"error": error_msg})
        }
