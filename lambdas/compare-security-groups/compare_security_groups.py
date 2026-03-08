"""
Compare Security Groups
=======================
Compares security groups between Source and Destination environments.
Reads pre-fetched states from DynamoDB and produces a comparison report.

This Lambda:
- Compares SG counts, names, and rules
- Identifies expected vs unexpected differences
- Compares compliance posture between environments
- Highlights security improvements in Destination

Environment Variables:
- LOG_LEVEL: Logging level (default: INFO)
"""

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)
logger.setLevel(os.environ.get("LOG_LEVEL", "INFO"))


# SGs that are expected to be different between Source and Destination
EXPECTED_DIFFERENCES = {
    "bastion-sg": "Destination removes bastion in favor of SSM",
    "eks-fargate-profile-sg": "Destination uses Fargate profiles",
    "nat-gateway-sg": "Different NAT architecture",
}

# SGs patterns expected only in Destination (new architecture)
DESTINATION_ONLY_PATTERNS = [
    "eks-fargate",
    "karpenter",
    "aws-load-balancer-controller",
]

# SGs patterns expected only in Source
SOURCE_ONLY_PATTERNS = [
    "bastion",
    "legacy",
]


def extract_dynamo_payload(state: dict) -> dict:
    """Extract payload from DynamoDB item format."""
    # Support both old (raw) and new (itemData) field names
    item_data = state.get("itemData", state.get("raw", {}))
    if not item_data:
        return {}

    # Handle DynamoDB M (map) type for payload
    payload_m = item_data.get("payload", {}).get("M", {})
    if payload_m:
        return parse_dynamo_map(payload_m)

    return {}


def parse_dynamo_map(dynamo_map: dict) -> dict:
    """Parse DynamoDB map format to regular dict."""
    result = {}
    for key, value in dynamo_map.items():
        if "S" in value:
            result[key] = value["S"]
        elif "N" in value:
            result[key] = float(value["N"]) if "." in value["N"] else int(value["N"])
        elif "BOOL" in value:
            result[key] = value["BOOL"]
        elif "L" in value:
            result[key] = [parse_dynamo_value(v) for v in value["L"]]
        elif "M" in value:
            result[key] = parse_dynamo_map(value["M"])
        elif "NULL" in value:
            result[key] = None
    return result


def parse_dynamo_value(value: dict) -> Any:
    """Parse a single DynamoDB value."""
    if "S" in value:
        return value["S"]
    elif "N" in value:
        return float(value["N"]) if "." in value["N"] else int(value["N"])
    elif "BOOL" in value:
        return value["BOOL"]
    elif "L" in value:
        return [parse_dynamo_value(v) for v in value["L"]]
    elif "M" in value:
        return parse_dynamo_map(value["M"])
    elif "NULL" in value:
        return None
    return value


def normalize_sg_name(sg_name: str) -> str:
    """
    Normalize SG name for comparison by removing environment-specific prefixes.

    Examples:
    - "rubix-dig-ppd-eks-cluster-sg" -> "eks-cluster-sg"
    - "rubix-nonprod-eks-cluster-sg" -> "eks-cluster-sg"
    """
    # Common prefixes to strip
    prefixes = [
        "rubix-dig-prd-", "rubix-dig-ppd-", "rubix-dig-stg-", "rubix-dig-int-",
        "rubix-nonprod-", "rubix-prod-", "rubix-legacy-",
        "rubix-nh-lz-", "nh-lz-",
    ]

    name = sg_name.lower()
    for prefix in prefixes:
        if name.startswith(prefix):
            name = name[len(prefix):]
            break

    return name


def get_sg_by_normalized_name(sgs: list, normalized_name: str) -> dict:
    """Find SG by normalized name."""
    for sg in sgs:
        if normalize_sg_name(sg.get("name", "")) == normalized_name:
            return sg
    return None


def compare_rules(source_rules: list, dest_rules: list) -> dict:
    """
    Compare ingress/egress rules between environments.
    """
    # Normalize rules for comparison
    def rule_key(rule: dict) -> str:
        protocol = rule.get("protocol", "-1")
        from_port = rule.get("fromPort", 0)
        to_port = rule.get("toPort", 65535)
        sources = sorted([s.get("value", "") for s in rule.get("sources", [])])
        return f"{protocol}:{from_port}-{to_port}:{','.join(sources)}"

    source_keys = set(rule_key(r) for r in source_rules)
    dest_keys = set(rule_key(r) for r in dest_rules)

    common = source_keys & dest_keys
    only_source = source_keys - dest_keys
    only_dest = dest_keys - source_keys

    return {
        "common": len(common),
        "onlySource": len(only_source),
        "onlyDestination": len(only_dest),
        "match": len(only_source) == 0 and len(only_dest) == 0,
    }


def is_expected_difference(sg_name: str, difference_type: str) -> tuple:
    """
    Check if a difference is expected between Source and Destination.

    Returns (is_expected, reason)
    """
    normalized = normalize_sg_name(sg_name)

    # Check explicit expected differences
    if normalized in EXPECTED_DIFFERENCES:
        return True, EXPECTED_DIFFERENCES[normalized]

    # Check Destination-only patterns
    if difference_type == "only_destination":
        for pattern in DESTINATION_ONLY_PATTERNS:
            if pattern in normalized:
                return True, f"Destination-specific resource: {pattern}"

    # Check Source-only patterns
    if difference_type == "only_source":
        for pattern in SOURCE_ONLY_PATTERNS:
            if pattern in normalized:
                return True, f"Source-specific resource: {pattern}"

    return False, None


def compare_security_groups(source_payload: dict, dest_payload: dict) -> dict:
    """
    Compare security groups between Source and Destination.
    """
    source_sgs = source_payload.get("securityGroups", [])
    dest_sgs = dest_payload.get("securityGroups", [])

    # Build name mappings
    source_names = {normalize_sg_name(sg.get("name", "")): sg for sg in source_sgs}
    dest_names = {normalize_sg_name(sg.get("name", "")): sg for sg in dest_sgs}

    # Find common, only-source, only-destination
    common_names = set(source_names.keys()) & set(dest_names.keys())
    only_source_names = set(source_names.keys()) - set(dest_names.keys())
    only_dest_names = set(dest_names.keys()) - set(source_names.keys())

    same_sgs = []
    different_config = []

    # Compare common SGs
    for name in common_names:
        source_sg = source_names[name]
        dest_sg = dest_names[name]

        # Compare rules
        ingress_comparison = compare_rules(
            source_sg.get("ingressRules", []),
            dest_sg.get("ingressRules", [])
        )
        egress_comparison = compare_rules(
            source_sg.get("egressRules", []),
            dest_sg.get("egressRules", [])
        )

        if ingress_comparison["match"] and egress_comparison["match"]:
            same_sgs.append(name)
        else:
            # Determine if difference is expected
            is_expected = False
            reason = None

            # Destination should generally have stricter rules
            if ingress_comparison["onlyDestination"] < ingress_comparison["onlySource"]:
                is_expected = True
                reason = "Destination has fewer/stricter ingress rules"
            elif dest_sg.get("compliant") and not source_sg.get("compliant"):
                is_expected = True
                reason = "Destination is compliant while Source has violations"

            different_config.append({
                "sg": name,
                "source": {
                    "name": source_sg.get("name"),
                    "ingressRules": source_sg.get("rulesCount", {}).get("ingress", 0),
                    "egressRules": source_sg.get("rulesCount", {}).get("egress", 0),
                    "compliant": source_sg.get("compliant", False),
                    "findings": len(source_sg.get("findings", [])),
                },
                "destination": {
                    "name": dest_sg.get("name"),
                    "ingressRules": dest_sg.get("rulesCount", {}).get("ingress", 0),
                    "egressRules": dest_sg.get("rulesCount", {}).get("egress", 0),
                    "compliant": dest_sg.get("compliant", False),
                    "findings": len(dest_sg.get("findings", [])),
                },
                "ingressComparison": ingress_comparison,
                "egressComparison": egress_comparison,
                "expected": is_expected,
                "reason": reason,
            })

    # Process only-source SGs
    only_source = []
    for name in only_source_names:
        is_expected, reason = is_expected_difference(name, "only_source")
        only_source.append({
            "name": name,
            "fullName": source_names[name].get("name"),
            "expected": is_expected,
            "reason": reason,
        })

    # Process only-destination SGs
    only_destination = []
    for name in only_dest_names:
        is_expected, reason = is_expected_difference(name, "only_destination")
        only_destination.append({
            "name": name,
            "fullName": dest_names[name].get("name"),
            "expected": is_expected,
            "reason": reason,
        })

    return {
        "sameSGs": same_sgs,
        "differentConfig": different_config,
        "onlySource": only_source,
        "onlyDestination": only_destination,
    }


def compare_compliance(source_payload: dict, dest_payload: dict) -> dict:
    """
    Compare compliance posture between environments.
    """
    source_summary = source_payload.get("summary", {})
    dest_summary = dest_payload.get("summary", {})

    source_compliance = {
        "total": source_summary.get("total", 0),
        "compliant": source_summary.get("compliant", 0),
        "violations": source_summary.get("violations", 0),
        "warnings": source_summary.get("warnings", 0),
    }

    dest_compliance = {
        "total": dest_summary.get("total", 0),
        "compliant": dest_summary.get("compliant", 0),
        "violations": dest_summary.get("violations", 0),
        "warnings": dest_summary.get("warnings", 0),
    }

    # Determine if Destination is better
    is_expected = True
    reason = None

    if dest_compliance["violations"] < source_compliance["violations"]:
        reason = "Destination has fewer security violations"
    elif dest_compliance["compliant"] > source_compliance["compliant"]:
        reason = "Destination has more compliant security groups"
    elif dest_compliance["violations"] > source_compliance["violations"]:
        is_expected = False
        reason = "Warning: Destination has more violations than Source"
    else:
        reason = "Similar compliance levels"

    return {
        "source": source_compliance,
        "destination": dest_compliance,
        "expected": is_expected,
        "reason": reason,
    }


def compare_rules_count(source_payload: dict, dest_payload: dict) -> dict:
    """
    Compare total rules count between environments.
    """
    source_sgs = source_payload.get("securityGroups", [])
    dest_sgs = dest_payload.get("securityGroups", [])

    source_ingress = sum(sg.get("rulesCount", {}).get("ingress", 0) for sg in source_sgs)
    source_egress = sum(sg.get("rulesCount", {}).get("egress", 0) for sg in source_sgs)
    dest_ingress = sum(sg.get("rulesCount", {}).get("ingress", 0) for sg in dest_sgs)
    dest_egress = sum(sg.get("rulesCount", {}).get("egress", 0) for sg in dest_sgs)

    is_expected = True
    reason = None

    if dest_ingress < source_ingress:
        reason = "Destination has consolidated ingress rules"
    elif dest_ingress > source_ingress:
        is_expected = False
        reason = "Warning: Destination has more ingress rules than expected"
    else:
        reason = "Similar rule counts"

    return {
        "source": {
            "totalIngress": source_ingress,
            "totalEgress": source_egress,
        },
        "destination": {
            "totalIngress": dest_ingress,
            "totalEgress": dest_egress,
        },
        "expected": is_expected,
        "reason": reason,
    }


def determine_overall_status(sg_comparison: dict, compliance_comparison: dict) -> str:
    """
    Determine overall comparison status.

    Returns: synced, differs, or error
    """
    # Check for unexpected differences
    unexpected_source = [s for s in sg_comparison.get("onlySource", []) if not s.get("expected")]
    unexpected_dest = [s for s in sg_comparison.get("onlyDestination", []) if not s.get("expected")]
    unexpected_config = [s for s in sg_comparison.get("differentConfig", []) if not s.get("expected")]

    if not compliance_comparison.get("expected"):
        return "differs"

    if unexpected_source or unexpected_dest or unexpected_config:
        return "differs"

    if sg_comparison.get("sameSGs") and not sg_comparison.get("differentConfig"):
        return "synced"

    return "differs"


def lambda_handler(event: dict, context: Any) -> dict:
    """
    Lambda entry point.

    Event structure (from Step Function):
    {
        "Project": "mro-mi2",
        "Env": "nh-ppd",
        "Instance": "MI2",
        "Environment": "ppd",
        "SourceState": {
            "source": "source",
            "itemData": {...}
        },
        "DestinationState": {
            "source": "destination",
            "itemData": {...}
        }
    }

    Returns:
    {
        "statusCode": 200,
        "payload": {
            "status": "synced | differs",
            "instance": "MI2",
            "environment": "ppd",
            "summary": {...},
            "sgComparison": {...},
            "complianceComparison": {...},
            "rulesComparison": {...},
            "issues": [],
            "timestamp": "ISO8601"
        }
    }
    """
    logger.info(f"Event keys: {list(event.keys())}")

    # Extract parameters - support both old (Domain) and new (Project/Env) format
    project = event.get("Project", event.get("Domain", "network"))
    env = event.get("Env", "")
    instance = event.get("Instance", "")
    environment = event.get("Environment", "")
    source_state = event.get("SourceState", event.get("LegacyState", {}))
    dest_state = event.get("DestinationState", event.get("NHState", {}))

    issues = []

    # Check if both states were found
    source_found = source_state.get("itemData") not in (False, None, {})
    dest_found = dest_state.get("itemData") not in (False, None, {})

    if not source_found:
        issues.append("Source state not found in DynamoDB")
    if not dest_found:
        issues.append("Destination state not found in DynamoDB")

    if not source_found or not dest_found:
        return {
            "statusCode": 200,
            "payload": {
                "status": "error",
                "instance": instance,
                "environment": environment,
                "summary": {
                    "sgCount": "unknown",
                    "compliance": "unknown",
                    "rulesCount": "unknown",
                },
                "sgComparison": {},
                "complianceComparison": {},
                "rulesComparison": {},
                "issues": issues,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
        }

    # Extract payloads from DynamoDB format
    source_payload = extract_dynamo_payload(source_state)
    dest_payload = extract_dynamo_payload(dest_state)

    logger.info(f"Comparing {project}/{env}: Source ({len(source_payload.get('securityGroups', []))} SGs) "
                f"vs Destination ({len(dest_payload.get('securityGroups', []))} SGs)")

    # Perform comparisons
    sg_comparison = compare_security_groups(source_payload, dest_payload)
    compliance_comparison = compare_compliance(source_payload, dest_payload)
    rules_comparison = compare_rules_count(source_payload, dest_payload)

    # Determine summary status
    sg_count_status = "synced" if len(sg_comparison.get("differentConfig", [])) == 0 else "differs"
    compliance_status = "synced" if compliance_comparison.get("expected") else "differs"
    rules_status = "synced" if rules_comparison.get("expected") else "differs"

    # Determine overall status
    status = determine_overall_status(sg_comparison, compliance_comparison)

    # Build payload
    payload = {
        "status": status,
        "instance": instance,
        "environment": environment,
        "summary": {
            "sgCount": sg_count_status,
            "compliance": compliance_status,
            "rulesCount": rules_status,
        },
        "sgComparison": sg_comparison,
        "complianceComparison": compliance_comparison,
        "rulesComparison": rules_comparison,
        "issues": issues,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    return {
        "statusCode": 200,
        "payload": payload,
    }
