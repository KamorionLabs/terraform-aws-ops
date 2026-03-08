"""
Fetch SSM Parameters
====================
Retrieves SSM Parameter Store parameters from AWS accounts and stores in DynamoDB.

This Lambda:
- Fetches SSM parameters by path from specified AWS account
- Computes hash of values (without decrypting SecureString)
- Returns structured payload for DynamoDB storage

Environment Variables:
- LOG_LEVEL: Logging level (default: INFO)
- STATE_TABLE_NAME: DynamoDB table name for state storage

Note: This Lambda runs in the ops-dashboard account and uses assumed roles
to access cross-account SSM parameters.
"""

import hashlib
import json
import logging
import os
from datetime import datetime, timezone
from typing import Any

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)
logger.setLevel(os.environ.get("LOG_LEVEL", "INFO"))


# Instance to environment mapping for path transformations
INSTANCE_ENV_MAPPING = {
    # Legacy naming convention
    "mi1": {"stg": "staging", "ppd": "preprod", "prd": "prod"},
    "mi2": {"stg": "staging", "ppd": "preprod", "prd": "prod"},
    "mi3": {"stg": "staging", "ppd": "preprod", "prd": "prod"},
    "fr": {"stg": "staging", "ppd": "preprod", "prd": "prod"},
    "bene": {"stg": "staging", "ppd": "preprod", "prd": "prod"},
    "indus": {"stg": "staging", "ppd": "preprod", "prd": "prod"},
}


def get_ssm_client(role_arn: str = None, region: str = "eu-central-1"):
    """
    Get SSM client, optionally assuming a cross-account role.

    Args:
        role_arn: Optional IAM role ARN to assume for cross-account access
        region: AWS region

    Returns:
        boto3 SSM client
    """
    if role_arn:
        sts = boto3.client("sts")
        assumed = sts.assume_role(
            RoleArn=role_arn,
            RoleSessionName="ops-dashboard-fetch-ssm",
            DurationSeconds=900,
        )
        credentials = assumed["Credentials"]
        return boto3.client(
            "ssm",
            region_name=region,
            aws_access_key_id=credentials["AccessKeyId"],
            aws_secret_access_key=credentials["SecretAccessKey"],
            aws_session_token=credentials["SessionToken"],
        )
    return boto3.client("ssm", region_name=region)


def compute_value_hash(value: str) -> str:
    """Compute SHA256 hash of a parameter value."""
    if value is None:
        return "sha256:null"
    return f"sha256:{hashlib.sha256(value.encode()).hexdigest()[:16]}"


def get_parameters_by_path(
    ssm_client,
    path: str,
    recursive: bool = True,
    with_decryption: bool = False,
) -> list[dict]:
    """
    Get all parameters under a path.

    Args:
        ssm_client: boto3 SSM client
        path: Parameter path prefix
        recursive: Whether to recurse into subpaths
        with_decryption: Whether to decrypt SecureString values

    Returns:
        List of parameter dictionaries
    """
    parameters = []
    paginator = ssm_client.get_paginator("get_parameters_by_path")

    try:
        for page in paginator.paginate(
            Path=path,
            Recursive=recursive,
            WithDecryption=with_decryption,
            MaxResults=10,
        ):
            for param in page.get("Parameters", []):
                parameters.append({
                    "name": param["Name"],
                    "type": param["Type"],
                    "version": param["Version"],
                    "lastModified": param["LastModifiedDate"].isoformat()
                    if param.get("LastModifiedDate")
                    else None,
                    "valueHash": compute_value_hash(param.get("Value", "")),
                    "dataType": param.get("DataType", "text"),
                })
    except ClientError as e:
        error_code = e.response["Error"]["Code"]
        if error_code == "AccessDeniedException":
            logger.warning(f"Access denied for path {path}")
            return []
        raise

    return parameters


def normalize_parameter_key(name: str, source: str, instance: str, env: str) -> str:
    """
    Normalize parameter name to a common key format for comparison.

    This handles the path transformation between Legacy and NH:
    - Legacy: /rubix/mi1/preprod/key
    - NH: /rubix/ppd/mi1/key

    Returns a normalized key like: /rubix/{env}/{instance}/key
    """
    # Get the legacy environment name
    legacy_env = INSTANCE_ENV_MAPPING.get(instance.lower(), {}).get(env.lower(), env)

    if source == "legacy":
        # Legacy format: /rubix/{instance}/{legacy_env}/key
        # Convert to: /rubix/{env}/{instance}/key
        prefix_legacy = f"/{instance.lower()}/{legacy_env}/"
        for base in ["/rubix", "/hybris"]:
            if name.startswith(f"{base}{prefix_legacy}"):
                suffix = name[len(f"{base}{prefix_legacy}"):]
                return f"{base}/{env.lower()}/{instance.lower()}/{suffix}"
    else:
        # NH format: /rubix/{env}/{instance}/key - already normalized
        pass

    return name


def extract_relative_key(name: str) -> str:
    """
    Extract the relative key from a full parameter path.

    Examples:
    - /rubix/mi1/preprod/db/endpoint -> db/endpoint
    - /rubix/ppd/mi1/db/endpoint -> db/endpoint
    """
    parts = name.split("/")
    # Skip first empty, base, and 2 context parts (instance/env or env/instance)
    if len(parts) > 4:
        return "/".join(parts[4:])
    return name


def lambda_handler(event: dict, context: Any) -> dict:
    """
    Lambda entry point.

    Event structure:
    {
        "Domain": "config",
        "Target": "mi1-ppd-legacy",
        "Instance": "MI1",
        "Environment": "ppd",
        "Source": "legacy",
        "RoleArn": "arn:aws:iam::073290922796:role/ops-dashboard-ssm-read",
        "Region": "eu-central-1",
        "ParameterPaths": [
            "/rubix/mi1/preprod/",
            "/hybris/mi1/preprod/"
        ]
    }

    Returns:
    {
        "statusCode": 200,
        "payload": {
            "status": "ok | warning | critical",
            "source": "legacy | nh",
            "instance": "MI1",
            "environment": "ppd",
            "summary": {...},
            "parameters": {...},
            "parametersList": [...],
            "timestamp": "ISO8601"
        }
    }
    """
    logger.info(f"Event keys: {list(event.keys())}")

    # Extract parameters
    domain = event.get("Domain", "config")
    target = event.get("Target", "")
    instance = event.get("Instance", "")
    environment = event.get("Environment", "")
    source = event.get("Source", "")
    role_arn = event.get("RoleArn")
    region = event.get("Region", "eu-central-1")
    parameter_paths = event.get("ParameterPaths", [])

    if not parameter_paths:
        return {
            "statusCode": 400,
            "payload": {
                "status": "error",
                "error": "No ParameterPaths provided",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
        }

    # Get SSM client (with cross-account role if specified)
    try:
        ssm_client = get_ssm_client(role_arn=role_arn, region=region)
    except ClientError as e:
        logger.error(f"Failed to assume role: {e}")
        return {
            "statusCode": 500,
            "payload": {
                "status": "error",
                "error": f"Failed to assume role: {str(e)}",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
        }

    # Fetch parameters from all paths
    all_parameters = []
    parameters_by_path = {}
    errors = []

    for path in parameter_paths:
        try:
            params = get_parameters_by_path(ssm_client, path)
            all_parameters.extend(params)
            parameters_by_path[path] = len(params)
            logger.info(f"Fetched {len(params)} parameters from {path}")
        except ClientError as e:
            error_msg = f"Error fetching {path}: {str(e)}"
            logger.error(error_msg)
            errors.append(error_msg)

    # Build parameters dict indexed by name
    parameters_dict = {}
    for param in all_parameters:
        name = param["name"]
        # Compute normalized key for comparison
        normalized_key = normalize_parameter_key(name, source, instance, environment)
        relative_key = extract_relative_key(name)

        parameters_dict[name] = {
            "type": param["type"],
            "version": param["version"],
            "lastModified": param["lastModified"],
            "valueHash": param["valueHash"],
            "dataType": param["dataType"],
            "normalizedKey": normalized_key,
            "relativeKey": relative_key,
        }

    # Calculate summary
    string_params = sum(1 for p in all_parameters if p["type"] == "String")
    secure_string_params = sum(1 for p in all_parameters if p["type"] == "SecureString")
    string_list_params = sum(1 for p in all_parameters if p["type"] == "StringList")

    summary = {
        "totalParameters": len(all_parameters),
        "stringParams": string_params,
        "secureStringParams": secure_string_params,
        "stringListParams": string_list_params,
        "byPath": parameters_by_path,
        "pathsChecked": len(parameter_paths),
        "pathsWithErrors": len(errors),
    }

    # Determine status
    if errors and len(errors) == len(parameter_paths):
        status = "critical"
    elif errors:
        status = "warning"
    elif len(all_parameters) == 0:
        status = "warning"
    else:
        status = "ok"

    # Build issues list
    issues = errors.copy()
    if len(all_parameters) == 0 and not errors:
        issues.append("No parameters found in any path")

    # Build payload
    payload = {
        "status": status,
        "source": source,
        "instance": instance,
        "environment": environment,
        "region": region,
        "summary": summary,
        "parameters": parameters_dict,
        "parametersList": list(parameters_dict.keys()),
        "issues": issues,
        "healthy": status == "ok",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    return {
        "statusCode": 200,
        "payload": payload,
    }
