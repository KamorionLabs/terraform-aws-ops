"""
Fetch CloudFront Distributions
==============================
Lists CloudFront distributions with tags.
Returns AWS NATIVE format - NO transformation.

All transformation, filtering, and enrichment is done by process-cloudfront.

Environment Variables:
- LOG_LEVEL: Logging level (default: INFO)
"""

import json
import logging
import os
from datetime import datetime
from typing import Any, Optional

import boto3

logger = logging.getLogger(__name__)
logger.setLevel(os.environ.get("LOG_LEVEL", "INFO"))


def serialize_datetime(obj: Any) -> Any:
    """Recursively convert datetime objects to ISO format strings."""
    if isinstance(obj, datetime):
        return obj.isoformat()
    elif isinstance(obj, dict):
        return {k: serialize_datetime(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [serialize_datetime(item) for item in obj]
    return obj


def get_cloudfront_client(cross_account_role_arn: Optional[str] = None):
    """Get CloudFront client, optionally with cross-account credentials."""
    if cross_account_role_arn:
        sts = boto3.client("sts")
        assumed = sts.assume_role(
            RoleArn=cross_account_role_arn,
            RoleSessionName="fetch-cloudfront-session",
            DurationSeconds=900,
        )
        creds = assumed["Credentials"]
        return boto3.client(
            "cloudfront",
            aws_access_key_id=creds["AccessKeyId"],
            aws_secret_access_key=creds["SecretAccessKey"],
            aws_session_token=creds["SessionToken"],
        )
    return boto3.client("cloudfront")


def extract_essential_fields(distribution: dict) -> dict:
    """
    Extract only essential fields from a CloudFront distribution.

    This reduces payload size significantly by removing unnecessary fields
    like CacheBehaviors, CustomErrorResponses, Logging, etc.
    """
    # Extract simple Origins (only Id and DomainName)
    origins = []
    for origin in distribution.get("Origins", {}).get("Items", []):
        origins.append({
            "Id": origin.get("Id", ""),
            "DomainName": origin.get("DomainName", ""),
        })

    # Extract ViewerCertificate essentials
    viewer_cert = distribution.get("ViewerCertificate", {})
    cert = {
        "ACMCertificateArn": viewer_cert.get("ACMCertificateArn"),
        "IAMCertificateId": viewer_cert.get("IAMCertificateId"),
        "MinimumProtocolVersion": viewer_cert.get("MinimumProtocolVersion"),
    }

    return {
        "Id": distribution.get("Id", ""),
        "ARN": distribution.get("ARN", ""),
        "DomainName": distribution.get("DomainName", ""),
        "Status": distribution.get("Status", ""),
        "Enabled": distribution.get("Enabled", False),
        "Aliases": distribution.get("Aliases", {}),
        "WebACLId": distribution.get("WebACLId", ""),
        "PriceClass": distribution.get("PriceClass", ""),
        "HttpVersion": distribution.get("HttpVersion", ""),
        "Origins": {"Items": origins},
        "ViewerCertificate": cert,
        "Comment": distribution.get("Comment", ""),
        # Tags will be added separately
    }


def list_all_distributions(client) -> list:
    """List all CloudFront distributions with pagination."""
    distributions = []
    marker = None

    while True:
        params = {}
        if marker:
            params["Marker"] = marker

        response = client.list_distributions(**params)
        dist_list = response.get("DistributionList", {})

        items = dist_list.get("Items", [])
        # Extract only essential fields to reduce payload size
        for item in items:
            distributions.append(extract_essential_fields(item))

        if dist_list.get("IsTruncated", False) and dist_list.get("NextMarker"):
            marker = dist_list["NextMarker"]
        else:
            break

    return distributions


def get_distribution_tags(client, arn: str) -> dict:
    """Get tags for a CloudFront distribution."""
    try:
        response = client.list_tags_for_resource(Resource=arn)
        tags = {}
        for tag in response.get("Tags", {}).get("Items", []):
            tags[tag["Key"]] = tag["Value"]
        return tags
    except Exception as e:
        logger.warning(f"Failed to get tags for {arn}: {e}")
        return {}


def lambda_handler(event, context):
    """
    Lambda entry point.

    Input:
    {
        "CrossAccountRoleArn": "arn:aws:iam::...",  # optional
        "IncludeTags": true                         # optional, default true
    }

    Output:
    {
        "statusCode": 200,
        "distributions": [
            {
                // AWS native format from list_distributions
                "Id": "EXAMPLEID",
                "ARN": "arn:aws:cloudfront::...",
                "DomainName": "d123.cloudfront.net",
                "Status": "Deployed",
                "Enabled": true,
                "Aliases": {"Quantity": 2, "Items": ["example.com", "www.example.com"]},
                "Origins": {...},
                "DefaultCacheBehavior": {...},
                "CacheBehaviors": {...},
                "ViewerCertificate": {...},
                "WebACLId": "...",
                "PriceClass": "PriceClass_All",
                "HttpVersion": "http2",
                // Only addition: Tags
                "Tags": {"Project": "mro-mi2", "Environment": "ppd"}
            },
            ...
        ],
        "count": 23,
        "error": null
    }
    """
    cross_account_role_arn = event.get("CrossAccountRoleArn")
    include_tags = event.get("IncludeTags", True)

    try:
        client = get_cloudfront_client(cross_account_role_arn)
        distributions = list_all_distributions(client)

        logger.info(f"Found {len(distributions)} distributions")

        # Add tags to each distribution if requested
        if include_tags:
            for dist in distributions:
                arn = dist.get("ARN", "")
                if arn:
                    dist["Tags"] = get_distribution_tags(client, arn)

        # Convert datetime objects to ISO format strings for JSON serialization
        distributions = serialize_datetime(distributions)

        return {
            "statusCode": 200,
            "distributions": distributions,
            "count": len(distributions),
            "error": None,
        }

    except Exception as e:
        logger.exception(f"Error fetching CloudFront distributions: {e}")
        return {
            "statusCode": 500,
            "distributions": [],
            "count": 0,
            "error": str(e),
        }
