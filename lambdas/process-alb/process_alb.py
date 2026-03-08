"""
Process ALB Data
================
Processes ALB data collected by the Step Function, applies business logic,
determines status, and prepares payload for DynamoDB storage.

This Lambda:
- Filters ALBs by tags if LoadBalancerTags is provided
- Aggregates target health across all target groups
- Computes status based on ALB state and target health
- Returns structured payload matching spec output format

Environment Variables:
- LOG_LEVEL: Logging level (default: INFO)
"""

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any

import boto3
from botocore.config import Config

logger = logging.getLogger(__name__)
logger.setLevel(os.environ.get("LOG_LEVEL", "INFO"))

# Boto3 config with retries
BOTO_CONFIG = Config(retries={"max_attempts": 3, "mode": "adaptive"})


def get_elbv2_client(cross_account_role_arn: str = None):
    """
    Get an ELBv2 client, optionally assuming a cross-account role.
    """
    if cross_account_role_arn:
        sts_client = boto3.client("sts", config=BOTO_CONFIG)
        assumed = sts_client.assume_role(
            RoleArn=cross_account_role_arn,
            RoleSessionName="process-alb-describe-tags",
        )
        credentials = assumed["Credentials"]
        return boto3.client(
            "elbv2",
            config=BOTO_CONFIG,
            aws_access_key_id=credentials["AccessKeyId"],
            aws_secret_access_key=credentials["SecretAccessKey"],
            aws_session_token=credentials["SessionToken"],
        )
    return boto3.client("elbv2", config=BOTO_CONFIG)


def fetch_lb_tags(lb_arns: list[str], cross_account_role_arn: str = None) -> dict[str, dict]:
    """
    Fetch tags for the given load balancer ARNs.
    Returns a dict mapping LB ARN to its tags dict.
    """
    if not lb_arns:
        return {}

    try:
        client = get_elbv2_client(cross_account_role_arn)
        # DescribeTags can handle up to 20 ARNs at once
        tags_map = {}
        for i in range(0, len(lb_arns), 20):
            batch = lb_arns[i : i + 20]
            response = client.describe_tags(ResourceArns=batch)
            for desc in response.get("TagDescriptions", []):
                arn = desc.get("ResourceArn")
                tags = {t["Key"]: t["Value"] for t in desc.get("Tags", [])}
                tags_map[arn] = tags
        return tags_map
    except Exception as e:
        logger.warning(f"Failed to fetch LB tags: {e}")
        return {}


# SSL policies considered modern (TLS 1.2+)
MODERN_SSL_POLICIES = [
    "ELBSecurityPolicy-TLS13-1-2-2021-06",
    "ELBSecurityPolicy-TLS13-1-2-Res-2021-06",
    "ELBSecurityPolicy-TLS13-1-2-Ext1-2021-06",
    "ELBSecurityPolicy-TLS13-1-2-Ext2-2021-06",
    "ELBSecurityPolicy-TLS-1-2-2017-01",
    "ELBSecurityPolicy-TLS-1-2-Ext-2018-06",
    "ELBSecurityPolicy-FS-1-2-2019-08",
    "ELBSecurityPolicy-FS-1-2-Res-2019-08",
    "ELBSecurityPolicy-FS-1-2-Res-2020-10",
]

# Obsolete SSL policies
OBSOLETE_SSL_POLICIES = [
    "ELBSecurityPolicy-2016-08",
    "ELBSecurityPolicy-TLS-1-1-2017-01",
    "ELBSecurityPolicy-TLS-1-0-2015-04",
    "ELBSecurityPolicy-2015-05",
]


def matches_tags(alb: dict, filter_tags: dict, alb_tags: dict = None) -> bool:
    """
    Check if an ALB matches the required tags.

    Supports:
    - elbv2.k8s.aws/cluster: tag_value (matches exact cluster name in ALB tags)
    - kubernetes.io/cluster/xxx: owned (matches cluster name in ALB name)
    """
    if not filter_tags:
        return True

    # Get ALB name for kubernetes pattern matching
    alb_name = alb.get("LoadBalancerName", "")

    for tag_key, tag_value in filter_tags.items():
        # New format: elbv2.k8s.aws/cluster
        if tag_key == "elbv2.k8s.aws/cluster":
            # If we have actual ALB tags, check them
            if alb_tags:
                actual_value = alb_tags.get(tag_key)
                if actual_value == tag_value:
                    return True
            # Fallback: match by ALB name pattern (k8s- prefix + cluster name in ARN/name)
            # AWS LB Controller names ALBs like k8s-{namespace}-{service}-{hash}
            # The cluster tag value should match ingress class/cluster name
            if tag_value in alb_name:
                return True

        # Legacy format: kubernetes.io/cluster/xxx
        elif "kubernetes.io/cluster/" in tag_key:
            cluster_name = tag_key.replace("kubernetes.io/cluster/", "")
            # ALBs created by AWS LB Controller often have cluster name in the name
            if cluster_name in alb_name:
                return True

    return False


def process_listener(listener: dict) -> dict:
    """Process a single listener and extract relevant information."""
    ssl_policy = listener.get("SslPolicy")
    default_actions = listener.get("DefaultActions", [])

    # Get default action type
    default_action = "unknown"
    target_group_arn = None
    if default_actions:
        first_action = default_actions[0]
        default_action = first_action.get("Type", "unknown")
        if first_action.get("TargetGroupArn"):
            target_group_arn = first_action.get("TargetGroupArn")
        elif first_action.get("ForwardConfig", {}).get("TargetGroups"):
            target_groups = first_action["ForwardConfig"]["TargetGroups"]
            if target_groups:
                target_group_arn = target_groups[0].get("TargetGroupArn")

    return {
        "arn": listener.get("ListenerArn"),
        "protocol": listener.get("Protocol"),
        "port": listener.get("Port"),
        "sslPolicy": ssl_policy,
        "defaultAction": default_action,
        "targetGroupArn": target_group_arn,
    }


def process_target_group(tg: dict) -> dict:
    """Process a target group with its health data."""
    target_health = tg.get("TargetHealthDescriptions", [])

    # Count targets by health state
    health_counts = {
        "healthy": 0,
        "unhealthy": 0,
        "draining": 0,
        "initial": 0,
        "unused": 0,
    }

    targets = []
    for thd in target_health:
        target = thd.get("Target", {})
        health = thd.get("TargetHealth", {})
        state = health.get("State", "unknown").lower()

        # Count by state
        if state in health_counts:
            health_counts[state] += 1
        elif state == "unavailable":
            health_counts["unhealthy"] += 1

        targets.append({
            "id": target.get("Id"),
            "port": target.get("Port"),
            "az": target.get("AvailabilityZone"),
            "health": state,
            "reason": health.get("Reason"),
            "description": health.get("Description"),
        })

    return {
        "arn": tg.get("TargetGroupArn"),
        "name": tg.get("TargetGroupName"),
        "protocol": tg.get("Protocol"),
        "port": tg.get("Port"),
        "targetType": tg.get("TargetType"),
        "healthCheck": {
            "path": tg.get("HealthCheckPath"),
            "protocol": tg.get("HealthCheckProtocol"),
            "intervalSeconds": tg.get("HealthCheckIntervalSeconds"),
            "timeoutSeconds": tg.get("HealthCheckTimeoutSeconds"),
            "healthyThresholdCount": tg.get("HealthyThresholdCount"),
            "unhealthyThresholdCount": tg.get("UnhealthyThresholdCount"),
        },
        "healthyCount": health_counts["healthy"],
        "unhealthyCount": health_counts["unhealthy"],
        "drainingCount": health_counts["draining"],
        "targets": targets,
    }


def process_load_balancer(alb: dict) -> dict:
    """Process a single load balancer and extract relevant information."""
    # Extract AZ names
    azs = [az.get("ZoneName") for az in alb.get("AvailabilityZones", [])]

    # Process listeners
    raw_listeners = alb.get("Listeners", [])
    listeners = [process_listener(l) for l in raw_listeners]

    # Process target groups
    raw_target_groups = alb.get("TargetGroups", [])
    target_groups = [process_target_group(tg) for tg in raw_target_groups]

    # Calculate totals
    total_targets = sum(len(tg["targets"]) for tg in target_groups)
    healthy_targets = sum(tg["healthyCount"] for tg in target_groups)
    unhealthy_targets = sum(tg["unhealthyCount"] for tg in target_groups)

    # Get state
    state = alb.get("State", {})
    state_code = state.get("Code", "unknown") if isinstance(state, dict) else str(state)

    return {
        "arn": alb.get("LoadBalancerArn"),
        "name": alb.get("LoadBalancerName"),
        "state": state_code,
        "type": alb.get("Type", "application"),
        "scheme": alb.get("Scheme"),
        "ipAddressType": alb.get("IpAddressType"),
        "dnsName": alb.get("DNSName"),
        "vpcId": alb.get("VpcId"),
        "securityGroups": alb.get("SecurityGroups", []),
        "availabilityZones": azs,
        "listeners": listeners,
        "targetGroups": target_groups,
        "totalTargets": total_targets,
        "healthyTargets": healthy_targets,
        "unhealthyTargets": unhealthy_targets,
    }


def determine_alb_issues(alb: dict) -> list:
    """Determine issues for a single ALB."""
    issues = []

    # Check ALB state
    if alb["state"] != "active":
        if alb["state"] == "provisioning":
            issues.append(f"{alb['name']}: ALB is provisioning")
        elif alb["state"] == "failed":
            issues.append(f"{alb['name']}: ALB is in failed state")
        else:
            issues.append(f"{alb['name']}: ALB state is {alb['state']}")

    # Check target groups
    for tg in alb.get("targetGroups", []):
        tg_name = tg["name"]

        # No healthy targets
        if tg["healthyCount"] == 0:
            total = len(tg["targets"])
            if total > 0:
                issues.append(f"{tg_name}: No healthy targets (0/{total})")
            else:
                issues.append(f"{tg_name}: No targets registered")

        # Less than 50% healthy
        elif tg["targets"]:
            health_ratio = tg["healthyCount"] / len(tg["targets"])
            if health_ratio < 0.5:
                issues.append(
                    f"{tg_name}: Less than 50% healthy "
                    f"({tg['healthyCount']}/{len(tg['targets'])})"
                )

        # Draining targets
        if tg.get("drainingCount", 0) > 0:
            issues.append(f"{tg_name}: {tg['drainingCount']} targets draining")

    # Check SSL policies
    for listener in alb.get("listeners", []):
        ssl_policy = listener.get("sslPolicy")
        if ssl_policy and ssl_policy in OBSOLETE_SSL_POLICIES:
            issues.append(
                f"{alb['name']}:{listener['port']}: Obsolete SSL policy {ssl_policy}"
            )

    return issues


def determine_overall_status(load_balancers: list, issues: list) -> str:
    """
    Determine overall status based on ALB states and target health.

    Returns: ok, warning, or critical
    """
    if not load_balancers:
        return "critical"

    # Check for critical conditions
    for alb in load_balancers:
        # ALB in failed state
        if alb["state"] == "failed":
            return "critical"

        # Any target group with no healthy targets (when there should be targets)
        for tg in alb.get("targetGroups", []):
            if tg["targets"] and tg["healthyCount"] == 0:
                return "critical"

    # Check for warning conditions
    for alb in load_balancers:
        # ALB provisioning
        if alb["state"] == "provisioning":
            return "warning"

        # Draining targets
        for tg in alb.get("targetGroups", []):
            if tg.get("drainingCount", 0) > 0:
                return "warning"

            # Less than 50% healthy
            if tg["targets"]:
                health_ratio = tg["healthyCount"] / len(tg["targets"])
                if health_ratio < 0.5:
                    return "warning"

    # Check for SSL policy warnings in issues
    ssl_warnings = [i for i in issues if "SSL policy" in i]
    if ssl_warnings:
        return "warning"

    # All good
    return "ok"


def lambda_handler(event: dict, context: Any) -> dict:
    """
    Lambda entry point.

    Event structure (from Step Function - new format):
    {
        "Project": "mro-mi2",
        "Env": "nh-ppd",
        "Instance": "MI2",
        "Environment": "ppd",
        "LoadBalancerTags": {"kubernetes.io/cluster/rubix-dig-ppd-webshop": "owned"},
        "ProcessedLoadBalancers": [
            {
                "LoadBalancerArn": "...",
                "LoadBalancerName": "...",
                "State": {"Code": "active"},
                "Listeners": [...],
                "TargetGroups": [...]
            }
        ]
    }

    Returns:
    {
        "statusCode": 200,
        "payload": {
            "status": "ok | warning | critical",
            "instance": "MI2",
            "environment": "ppd",
            "summary": {...},
            "loadBalancers": [...],
            "issues": [...],
            "healthy": true,
            "timestamp": "ISO8601"
        }
    }
    """
    logger.info(f"Event keys: {list(event.keys())}")

    # Extract parameters - support both old (Domain/Target) and new (Project/Env) format
    project = event.get("Project", event.get("Domain", "network"))
    env = event.get("Env", event.get("Target", ""))
    instance = event.get("Instance", "")
    environment = event.get("Environment", "")
    filter_tags = event.get("LoadBalancerTags", {})
    cross_account_role_arn = event.get("CrossAccountRoleArn")
    raw_load_balancers = event.get("ProcessedLoadBalancers", [])

    logger.info(f"Processing {len(raw_load_balancers)} load balancers")

    # Fetch actual LB tags if filtering is needed
    lb_tags_map = {}
    if filter_tags and raw_load_balancers:
        lb_arns = [alb.get("LoadBalancerArn") for alb in raw_load_balancers if alb and alb.get("LoadBalancerArn")]
        logger.info(f"Fetching tags for {len(lb_arns)} load balancers")
        lb_tags_map = fetch_lb_tags(lb_arns, cross_account_role_arn)
        logger.info(f"Fetched tags for {len(lb_tags_map)} load balancers")

    # Filter by tags if specified
    filtered_albs = []
    for alb in raw_load_balancers:
        if not alb:
            continue
        if filter_tags:
            alb_arn = alb.get("LoadBalancerArn")
            alb_tags = lb_tags_map.get(alb_arn, {})
            if matches_tags(alb, filter_tags, alb_tags):
                filtered_albs.append(alb)
        else:
            filtered_albs.append(alb)

    logger.info(f"After tag filtering: {len(filtered_albs)} load balancers")

    # Process load balancers
    processed_load_balancers = []
    all_issues = []

    for alb in filtered_albs:
        processed = process_load_balancer(alb)
        processed_load_balancers.append(processed)

        # Check for issues
        issues = determine_alb_issues(processed)
        all_issues.extend(issues)

    # Calculate summary
    total_targets = sum(alb["totalTargets"] for alb in processed_load_balancers)
    healthy_targets = sum(alb["healthyTargets"] for alb in processed_load_balancers)
    active_albs = sum(1 for alb in processed_load_balancers if alb["state"] == "active")
    healthy_albs = sum(
        1 for alb in processed_load_balancers
        if alb["state"] == "active" and alb["healthyTargets"] > 0
    )

    summary = {
        "total": len(processed_load_balancers),
        "active": active_albs,
        "healthy": healthy_albs,
        "totalTargets": total_targets,
        "healthyTargets": healthy_targets,
    }

    # Determine overall status
    status = determine_overall_status(processed_load_balancers, all_issues)

    # Build payload
    payload = {
        "status": status,
        "instance": instance,
        "environment": environment,
        "summary": summary,
        "loadBalancers": processed_load_balancers,
        "issues": all_issues,
        "healthy": status == "ok",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    return {
        "statusCode": 200,
        "payload": payload,
    }
