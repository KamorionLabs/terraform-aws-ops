"""
Filter ALBs by Tags Lambda

This Lambda filters Application Load Balancers by tags and returns the full ALB details.
It solves the DataLimitExceeded issue when using tag-based filtering on accounts with many ALBs.

Input:
- CrossAccountRoleArn: Optional role ARN for cross-account access
- LoadBalancerTags: Dict of tags to filter by (e.g., {"elbv2.k8s.aws/cluster": "rubix-nonprod"})

Output:
- LoadBalancers: List of ALB objects that match the specified tags (full details)
- totalScanned: Total number of ALBs scanned
- matchCount: Number of ALBs matching the tags
"""

import boto3
from datetime import datetime
from typing import Any


def serialize_lb(lb: dict) -> dict:
    """Convert datetime fields to ISO strings and normalize field names for Step Functions."""
    # Field name mapping from boto3 to Step Functions sdk:call format
    field_mapping = {
        "DNSName": "DnsName",
        "VPCId": "VpcId",
    }

    result = {}
    for key, value in lb.items():
        # Normalize field name
        normalized_key = field_mapping.get(key, key)

        if isinstance(value, datetime):
            result[normalized_key] = value.isoformat()
        elif isinstance(value, dict):
            result[normalized_key] = serialize_lb(value)
        elif isinstance(value, list):
            result[normalized_key] = [
                serialize_lb(item) if isinstance(item, dict) else
                (item.isoformat() if isinstance(item, datetime) else item)
                for item in value
            ]
        else:
            result[normalized_key] = value
    return result


def get_elbv2_client(cross_account_role_arn: str | None = None):
    """Get ELBv2 client, optionally assuming a cross-account role."""
    if cross_account_role_arn:
        sts_client = boto3.client("sts")
        assumed_role = sts_client.assume_role(
            RoleArn=cross_account_role_arn,
            RoleSessionName="filter-alb-by-tags"
        )
        credentials = assumed_role["Credentials"]
        return boto3.client(
            "elbv2",
            aws_access_key_id=credentials["AccessKeyId"],
            aws_secret_access_key=credentials["SecretAccessKey"],
            aws_session_token=credentials["SessionToken"]
        )
    return boto3.client("elbv2")


def get_all_load_balancers(elbv2_client) -> list[dict]:
    """Get all ALBs using pagination."""
    load_balancers = []
    paginator = elbv2_client.get_paginator("describe_load_balancers")

    for page in paginator.paginate():
        for lb in page.get("LoadBalancers", []):
            # Only include Application Load Balancers
            if lb.get("Type") == "application":
                load_balancers.append(lb)

    return load_balancers


def get_tags_for_load_balancers(elbv2_client, arns: list[str]) -> dict[str, dict[str, str]]:
    """Get tags for a list of load balancer ARNs."""
    if not arns:
        return {}

    tags_by_arn = {}

    # DescribeTags accepts max 20 ARNs at a time
    batch_size = 20
    for i in range(0, len(arns), batch_size):
        batch = arns[i:i + batch_size]
        response = elbv2_client.describe_tags(ResourceArns=batch)

        for tag_desc in response.get("TagDescriptions", []):
            arn = tag_desc["ResourceArn"]
            tags = {tag["Key"]: tag["Value"] for tag in tag_desc.get("Tags", [])}
            tags_by_arn[arn] = tags

    return tags_by_arn


def filter_load_balancers_by_tags(
    load_balancers: list[dict],
    tags_by_arn: dict[str, dict[str, str]],
    filter_tags: dict[str, str]
) -> list[dict]:
    """Filter ALBs that match ALL specified tags."""
    if not filter_tags:
        return load_balancers

    matching_lbs = []

    for lb in load_balancers:
        arn = lb["LoadBalancerArn"]
        tags = tags_by_arn.get(arn, {})

        # Check if all filter tags match
        matches = True
        for key, value in filter_tags.items():
            if tags.get(key) != value:
                matches = False
                break

        if matches:
            matching_lbs.append(lb)

    return matching_lbs


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """
    Lambda handler for filtering ALBs by tags.

    Args:
        event: Input event containing:
            - CrossAccountRoleArn: Optional cross-account role ARN
            - LoadBalancerTags: Dict of tags to filter by

    Returns:
        Dict with:
            - statusCode: 200 on success
            - LoadBalancers: List of matching ALB objects (full details)
            - totalScanned: Total number of ALBs scanned
            - matchCount: Number of ALBs matching the tags
    """
    cross_account_role_arn = event.get("CrossAccountRoleArn")
    filter_tags = event.get("LoadBalancerTags", {})

    try:
        # Get ELBv2 client
        elbv2_client = get_elbv2_client(cross_account_role_arn)

        # Get all ALBs with full details
        all_load_balancers = get_all_load_balancers(elbv2_client)

        if not all_load_balancers:
            return {
                "statusCode": 200,
                "LoadBalancers": [],
                "totalScanned": 0,
                "matchCount": 0
            }

        # Get ARNs for tag lookup
        all_arns = [lb["LoadBalancerArn"] for lb in all_load_balancers]

        # Get tags for all ALBs
        tags_by_arn = get_tags_for_load_balancers(elbv2_client, all_arns)

        # Filter by tags
        matching_lbs = filter_load_balancers_by_tags(
            all_load_balancers, tags_by_arn, filter_tags
        )

        return {
            "statusCode": 200,
            "LoadBalancers": [serialize_lb(lb) for lb in matching_lbs],
            "totalScanned": len(all_load_balancers),
            "matchCount": len(matching_lbs)
        }

    except Exception as e:
        return {
            "statusCode": 500,
            "error": str(e),
            "LoadBalancers": []
        }


if __name__ == "__main__":
    # Test locally
    test_event = {
        "CrossAccountRoleArn": "arn:aws:iam::073290922796:role/dashboard-read",
        "LoadBalancerTags": {"elbv2.k8s.aws/cluster": "rubix-nonprod"}
    }
    result = handler(test_event, None)
    print(json.dumps(result, indent=2, default=str))
