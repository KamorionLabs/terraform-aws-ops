"""
CloudFront Alias Manager Lambda
===============================
Minimal Lambda to find and remove CloudFront distribution aliases, optionally
in a cross-account target via STS AssumeRole.

Actions:
- FIND_BY_ALIASES: Given a list of aliases, return distributions that contain any of them.
- REMOVE_ALIASES:  Given a distribution id and a list of aliases, remove them from the distribution.

Environment Variables:
- LOG_LEVEL: Logging level (default: INFO)
"""

import logging
import os
from typing import Any, Optional

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger()
logger.setLevel(os.environ.get("LOG_LEVEL", "INFO"))


def _get_client(cross_account_role_arn: Optional[str]):
    if not cross_account_role_arn:
        return boto3.client("cloudfront")

    sts = boto3.client("sts")
    assumed = sts.assume_role(
        RoleArn=cross_account_role_arn,
        RoleSessionName="cloudfront-alias-manager",
        DurationSeconds=900,
    )
    c = assumed["Credentials"]
    return boto3.client(
        "cloudfront",
        aws_access_key_id=c["AccessKeyId"],
        aws_secret_access_key=c["SecretAccessKey"],
        aws_session_token=c["SessionToken"],
    )


def _list_all_distributions(client) -> list[dict]:
    distributions: list[dict] = []
    marker: Optional[str] = None
    while True:
        params = {"Marker": marker} if marker else {}
        resp = client.list_distributions(**params)
        dlist = resp.get("DistributionList", {})
        for item in dlist.get("Items", []) or []:
            aliases_data = item.get("Aliases", {}) or {}
            aliases = aliases_data.get("Items", []) if aliases_data.get("Quantity", 0) > 0 else []
            distributions.append({
                "id": item.get("Id", ""),
                "arn": item.get("ARN", ""),
                "domain_name": item.get("DomainName", ""),
                "status": item.get("Status", ""),
                "enabled": item.get("Enabled", False),
                "aliases": aliases,
                "comment": item.get("Comment", ""),
            })
        if dlist.get("IsTruncated") and dlist.get("NextMarker"):
            marker = dlist["NextMarker"]
        else:
            break
    return distributions


def _find_by_aliases(client, target_aliases: list[str]) -> list[dict]:
    target_set = {a.lower() for a in target_aliases}
    all_dists = _list_all_distributions(client)
    matching = []
    for dist in all_dists:
        dist_aliases = {a.lower() for a in dist.get("aliases", [])}
        matched = dist_aliases & target_set
        if matched:
            matching.append({**dist, "matched_aliases": sorted(matched)})
    return matching


def _remove_aliases(client, distribution_id: str, aliases_to_remove: list[str]) -> dict:
    cfg_resp = client.get_distribution_config(Id=distribution_id)
    config = cfg_resp["DistributionConfig"]
    etag = cfg_resp["ETag"]

    current = list(config.get("Aliases", {}).get("Items", []) or [])
    remove_lower = {a.lower() for a in aliases_to_remove}
    remaining = [a for a in current if a.lower() not in remove_lower]
    removed = [a for a in current if a.lower() in remove_lower]

    if not removed:
        logger.info("No matching aliases on distribution %s", distribution_id)
        return {
            "distribution_id": distribution_id,
            "modified": False,
            "removed_aliases": [],
            "remaining_aliases": current,
            "message": "No matching aliases found",
        }

    config["Aliases"] = {"Quantity": len(remaining), "Items": remaining}
    try:
        resp = client.update_distribution(Id=distribution_id, DistributionConfig=config, IfMatch=etag)
    except ClientError as e:
        logger.error("update_distribution failed for %s: %s", distribution_id, e)
        raise

    logger.info(
        "Removed %d alias(es) from %s: %s (remaining=%d)",
        len(removed), distribution_id, removed, len(remaining),
    )
    return {
        "distribution_id": distribution_id,
        "modified": True,
        "removed_aliases": removed,
        "remaining_aliases": remaining,
        "distribution_status": resp.get("Distribution", {}).get("Status"),
    }


def lambda_handler(event: dict, context: Any) -> dict:
    action = event.get("Action")
    role_arn = event.get("CrossAccountRoleArn") or None

    logger.info("Invoked with Action=%s, cross_account=%s", action, bool(role_arn))

    if action == "FIND_BY_ALIASES":
        aliases = event.get("Aliases") or []
        if not aliases:
            return {"statusCode": 400, "error": "Missing Aliases"}
        client = _get_client(role_arn)
        matches = _find_by_aliases(client, aliases)
        logger.info("FIND_BY_ALIASES: %d matching distribution(s) for %d alias(es)", len(matches), len(aliases))
        return {"statusCode": 200, "distributions": matches, "count": len(matches)}

    if action == "REMOVE_ALIASES":
        distribution_id = event.get("DistributionId")
        aliases = event.get("Aliases") or []
        if not distribution_id:
            return {"statusCode": 400, "error": "Missing DistributionId"}
        if not aliases:
            return {"statusCode": 400, "error": "Missing Aliases"}
        client = _get_client(role_arn)
        return {"statusCode": 200, **_remove_aliases(client, distribution_id, aliases)}

    return {"statusCode": 400, "error": f"Unknown Action: {action}"}
