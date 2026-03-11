"""
Cross-region AWS API proxy for Step Functions.

Step Functions SDK integrations always call APIs in the SFN's own region.
This Lambda bridges the gap for cross-region operations by:
1. Assuming the provided IAM role (cross-account if needed)
2. Making the API call in the specified region via boto3
3. Normalizing response keys to match SFN SDK integration casing (DB->Db)

Used by: prepare_snapshot_for_restore (cross-region snapshot discovery/creation)

Input:
  Service: AWS service name (e.g., "rds")
  Action: boto3 method name in snake_case (e.g., "describe_db_cluster_snapshots")
  Region: Target AWS region (e.g., "eu-west-3")
  RoleArn: IAM role to assume for the API call
  Parameters: Dict of API parameters (boto3 format, PascalCase keys)

Output:
  The API response with keys normalized to match Step Functions SDK integration
  casing (e.g., DBClusterSnapshots -> DbClusterSnapshots).
"""

import json
import logging
import os
from datetime import datetime, timezone

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger()
log_level = os.environ.get('LOG_LEVEL', 'INFO')
logger.setLevel(getattr(logging, log_level))


def lambda_handler(event, context):
    service = event['Service']
    action = event['Action']
    region = event['Region']
    role_arn = event['RoleArn']
    parameters = event.get('Parameters', {})

    logger.info(
        "Cross-region proxy: %s.%s in %s via %s",
        service, action, region, role_arn
    )

    # Assume role (cross-account and/or cross-region)
    sts = boto3.client('sts')
    assumed = sts.assume_role(
        RoleArn=role_arn,
        RoleSessionName='sfn-cross-region-proxy',
        DurationSeconds=3600
    )['Credentials']

    # Create service client in target region with assumed credentials
    client = boto3.client(
        service,
        region_name=region,
        aws_access_key_id=assumed['AccessKeyId'],
        aws_secret_access_key=assumed['SecretAccessKey'],
        aws_session_token=assumed['SessionToken']
    )

    try:
        response = getattr(client, action)(**parameters)
    except ClientError as e:
        code = e.response['Error']['Code']
        msg = e.response['Error']['Message']
        logger.error("API error: %s - %s", code, msg)
        # Raise with AWS error code as exception class name.
        # SFN catches this via ErrorEquals matching the class name.
        exc_class = type(code, (Exception,), {})
        raise exc_class(msg)

    # Remove non-serializable metadata
    response.pop('ResponseMetadata', None)

    # Normalize keys and serialize datetimes
    result = _normalize_keys(response)
    logger.info("Proxy call successful, returning %d top-level keys", len(result))
    return result


def _normalize_keys(obj):
    """Recursively normalize response keys to match SFN SDK integration casing."""
    if isinstance(obj, dict):
        return {_normalize_key(k): _normalize_keys(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [_normalize_keys(item) for item in obj]
    elif isinstance(obj, (datetime,)):
        return obj.isoformat()
    return obj


def _normalize_key(key):
    """
    SFN SDK integrations normalize multi-char uppercase prefixes:
      DB  -> Db   (DBClusterSnapshots -> DbClusterSnapshots)
      VPC -> Vpc
      IAM -> Iam
      IO  -> Io
    Single uppercase chars are unchanged (StorageEncrypted stays as-is).
    """
    prefixes = ('DB', 'VPC', 'IAM', 'IO')
    for prefix in prefixes:
        if key.startswith(prefix) and len(key) > len(prefix):
            return prefix.capitalize() + key[len(prefix):]
    return key
