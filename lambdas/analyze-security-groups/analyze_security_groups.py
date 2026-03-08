"""
Analyze Security Groups
=======================
Analyzes security groups for compliance with security policies.
Checks for sensitive ports exposed, overly permissive rules, and unused SGs.

This Lambda:
- Parses security group rules (ingress/egress)
- Checks for sensitive ports (SSH, RDP, DB ports) exposed to 0.0.0.0/0
- Identifies overly permissive CIDR blocks
- Determines which SGs are unused (via ENI analysis)
- Computes compliance summary and findings

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

# Sensitive ports that should never be open to the internet
SENSITIVE_PORTS = {
    22: {"name": "SSH", "severity": "HIGH"},
    3389: {"name": "RDP", "severity": "HIGH"},
    3306: {"name": "MySQL", "severity": "HIGH"},
    5432: {"name": "PostgreSQL", "severity": "HIGH"},
    27017: {"name": "MongoDB", "severity": "HIGH"},
    6379: {"name": "Redis", "severity": "HIGH"},
    9200: {"name": "Elasticsearch", "severity": "HIGH"},
    9300: {"name": "Elasticsearch-Cluster", "severity": "HIGH"},
    11211: {"name": "Memcached", "severity": "HIGH"},
    5984: {"name": "CouchDB", "severity": "HIGH"},
    2181: {"name": "Zookeeper", "severity": "MEDIUM"},
    9092: {"name": "Kafka", "severity": "MEDIUM"},
    8080: {"name": "HTTP-Alt", "severity": "MEDIUM"},
    9001: {"name": "Hybris", "severity": "MEDIUM"},
    23: {"name": "Telnet", "severity": "HIGH"},
    21: {"name": "FTP", "severity": "MEDIUM"},
    25: {"name": "SMTP", "severity": "MEDIUM"},
    445: {"name": "SMB", "severity": "HIGH"},
    135: {"name": "RPC", "severity": "HIGH"},
    139: {"name": "NetBIOS", "severity": "HIGH"},
}

# CIDR blocks considered "open to internet"
INTERNET_CIDRS = ["0.0.0.0/0", "::/0"]

# Overly broad CIDR blocks that warrant warnings
BROAD_CIDRS = [
    ("10.0.0.0/8", "Class A private - very broad"),
    ("172.16.0.0/12", "Class B private - broad"),
    ("192.168.0.0/16", "Class C private - moderately broad"),
]


def is_port_in_range(port: int, from_port: int, to_port: int) -> bool:
    """Check if a port is within a rule's port range."""
    if from_port == -1 or to_port == -1:
        # -1 means all ports
        return True
    return from_port <= port <= to_port


def is_all_traffic(rule: dict) -> bool:
    """Check if rule allows all traffic."""
    protocol = rule.get("IpProtocol", "")
    return protocol == "-1"


def extract_sources(rule: dict) -> list:
    """Extract all sources from an ingress rule."""
    sources = []

    # IPv4 ranges
    for ip_range in rule.get("IpRanges", []):
        cidr = ip_range.get("CidrIp", "")
        desc = ip_range.get("Description", "")
        sources.append({
            "type": "cidr",
            "value": cidr,
            "description": desc,
            "isInternet": cidr in INTERNET_CIDRS,
        })

    # IPv6 ranges
    for ip_range in rule.get("Ipv6Ranges", []):
        cidr = ip_range.get("CidrIpv6", "")
        desc = ip_range.get("Description", "")
        sources.append({
            "type": "cidr_v6",
            "value": cidr,
            "description": desc,
            "isInternet": cidr in INTERNET_CIDRS,
        })

    # Security group references
    for sg_ref in rule.get("UserIdGroupPairs", []):
        sg_id = sg_ref.get("GroupId", "")
        desc = sg_ref.get("Description", "")
        sources.append({
            "type": "securityGroup",
            "value": sg_id,
            "description": desc,
            "isInternet": False,
        })

    # Prefix lists
    for prefix in rule.get("PrefixListIds", []):
        pl_id = prefix.get("PrefixListId", "")
        desc = prefix.get("Description", "")
        sources.append({
            "type": "prefixList",
            "value": pl_id,
            "description": desc,
            "isInternet": False,
        })

    return sources


def analyze_rule(rule: dict, direction: str) -> tuple:
    """
    Analyze a single rule for compliance issues.

    Returns (parsed_rule, findings)
    """
    from_port = rule.get("FromPort", 0)
    to_port = rule.get("ToPort", 65535)
    protocol = rule.get("IpProtocol", "-1")

    # Handle all traffic
    if is_all_traffic(rule):
        from_port = 0
        to_port = 65535

    sources = extract_sources(rule)
    findings = []

    # Check each source for compliance issues
    internet_sources = [s for s in sources if s["isInternet"]]

    if internet_sources:
        # Check for sensitive ports exposed to internet
        for port, info in SENSITIVE_PORTS.items():
            if is_port_in_range(port, from_port, to_port):
                findings.append({
                    "severity": info["severity"],
                    "type": "SENSITIVE_PORT_EXPOSED",
                    "rule": f"{info['name']} ({port}) open to internet",
                    "recommendation": f"Restrict {info['name']} to specific IPs or security groups",
                    "port": port,
                    "protocol": protocol,
                })

        # Check for all-traffic rule to internet
        if is_all_traffic(rule):
            findings.append({
                "severity": "HIGH",
                "type": "ALL_TRAFFIC_TO_INTERNET",
                "rule": f"All traffic open to internet ({direction})",
                "recommendation": "Restrict to specific ports and protocols",
            })

    # Check for overly broad CIDR ranges
    for source in sources:
        if source["type"] == "cidr" and not source["isInternet"]:
            for broad_cidr, reason in BROAD_CIDRS:
                if source["value"] == broad_cidr:
                    findings.append({
                        "severity": "LOW",
                        "type": "BROAD_CIDR_RANGE",
                        "rule": f"Broad CIDR range: {broad_cidr}",
                        "recommendation": f"{reason} - consider narrowing scope",
                    })

    # Check for missing descriptions
    missing_desc = [s for s in sources if not s.get("description")]
    if missing_desc:
        findings.append({
            "severity": "LOW",
            "type": "MISSING_DESCRIPTION",
            "rule": f"Rule missing description ({len(missing_desc)} source(s))",
            "recommendation": "Add descriptions to document rule purpose",
        })

    parsed_rule = {
        "protocol": protocol,
        "fromPort": from_port,
        "toPort": to_port,
        "sources": sources,
        "hasInternetAccess": len(internet_sources) > 0,
    }

    return parsed_rule, findings


def get_sg_usage(sg_id: str, network_interfaces: list) -> dict:
    """
    Determine what resources are using a security group.
    """
    usage = {
        "enis": [],
        "instances": [],
        "lambdas": [],
        "rds": [],
        "efs": [],
        "elb": [],
        "eks": [],
        "count": 0,
    }

    for eni in network_interfaces:
        eni_sg_ids = [g.get("GroupId") for g in eni.get("Groups", [])]
        if sg_id in eni_sg_ids:
            eni_id = eni.get("NetworkInterfaceId", "")
            usage["enis"].append(eni_id)
            usage["count"] += 1

            # Categorize by interface type or description
            eni_type = eni.get("InterfaceType", "")
            description = eni.get("Description", "").lower()
            attachment = eni.get("Attachment", {})
            instance_id = attachment.get("InstanceId")

            if instance_id:
                if instance_id not in usage["instances"]:
                    usage["instances"].append(instance_id)
            elif "lambda" in description:
                usage["lambdas"].append(eni_id)
            elif "rds" in description or eni_type == "rds":
                usage["rds"].append(eni_id)
            elif "efs" in description or eni_type == "efs":
                usage["efs"].append(eni_id)
            elif "elb" in description or "loadbalancer" in description:
                usage["elb"].append(eni_id)
            elif "eks" in description or "amazon-eks" in description:
                usage["eks"].append(eni_id)

    return usage


def analyze_security_group(sg: dict, network_interfaces: list) -> dict:
    """
    Analyze a single security group for compliance.
    """
    sg_id = sg.get("GroupId", "")
    sg_name = sg.get("GroupName", "")
    vpc_id = sg.get("VpcId", "")
    description = sg.get("Description", "")
    tags = {t["Key"]: t["Value"] for t in sg.get("Tags", [])}

    all_findings = []

    # Analyze ingress rules
    ingress_rules = []
    for rule in sg.get("IpPermissions", []):
        parsed, findings = analyze_rule(rule, "ingress")
        ingress_rules.append(parsed)
        for f in findings:
            f["sgId"] = sg_id
            f["sgName"] = sg_name
            all_findings.append(f)

    # Analyze egress rules
    egress_rules = []
    for rule in sg.get("IpPermissionsEgress", []):
        parsed, findings = analyze_rule(rule, "egress")
        egress_rules.append(parsed)
        for f in findings:
            f["sgId"] = sg_id
            f["sgName"] = sg_name
            all_findings.append(f)

    # Check usage
    usage = get_sg_usage(sg_id, network_interfaces)

    # Warning if unused
    if usage["count"] == 0:
        all_findings.append({
            "sgId": sg_id,
            "sgName": sg_name,
            "severity": "LOW",
            "type": "UNUSED_SECURITY_GROUP",
            "rule": "Security group is not attached to any ENI",
            "recommendation": "Consider removing if not needed",
        })

    # Warning if too many rules
    total_rules = len(ingress_rules) + len(egress_rules)
    if total_rules > 50:
        all_findings.append({
            "sgId": sg_id,
            "sgName": sg_name,
            "severity": "LOW",
            "type": "EXCESSIVE_RULES",
            "rule": f"Security group has {total_rules} rules",
            "recommendation": "Consider consolidating rules or splitting into multiple SGs",
        })

    # Check for missing description
    if not description or description == "Managed by Terraform":
        all_findings.append({
            "sgId": sg_id,
            "sgName": sg_name,
            "severity": "LOW",
            "type": "MISSING_SG_DESCRIPTION",
            "rule": "Security group has no meaningful description",
            "recommendation": "Add a description explaining the SG purpose",
        })

    # Determine compliance status
    high_findings = [f for f in all_findings if f.get("severity") == "HIGH"]
    medium_findings = [f for f in all_findings if f.get("severity") == "MEDIUM"]
    low_findings = [f for f in all_findings if f.get("severity") == "LOW"]

    if high_findings:
        compliant = False
        status = "violation"
    elif medium_findings:
        compliant = False
        status = "warning"
    elif low_findings:
        compliant = True
        status = "info"
    else:
        compliant = True
        status = "ok"

    return {
        "id": sg_id,
        "name": sg_name,
        "vpcId": vpc_id,
        "description": description,
        "ingressRules": ingress_rules,
        "egressRules": egress_rules,
        "usedBy": usage,
        "findings": all_findings,
        "compliant": compliant,
        "status": status,
        "tags": tags,
        "rulesCount": {
            "ingress": len(ingress_rules),
            "egress": len(egress_rules),
            "total": total_rules,
        },
    }


def determine_overall_status(summary: dict, all_findings: list) -> str:
    """
    Determine overall status based on findings.

    Returns: ok, warning, or critical
    """
    if summary["violations"] > 0:
        return "critical"
    if summary["warnings"] > 0:
        return "warning"
    if summary["total"] == 0:
        return "warning"
    return "ok"


def lambda_handler(event: dict, context: Any) -> dict:
    """
    Lambda entry point.

    Event structure (from Step Function):
    {
        "Project": "mro-mi2",
        "Env": "nh-preprod",
        "Instance": "MI2",
        "Environment": "ppd",
        "SecurityGroups": [...],  # Raw EC2 DescribeSecurityGroups response
        "NetworkInterfaces": [...]  # Raw EC2 DescribeNetworkInterfaces response
    }

    Returns:
    {
        "statusCode": 200,
        "payload": {
            "status": "ok | warning | critical",
            "instance": "MI2",
            "environment": "ppd",
            "summary": {...},
            "securityGroups": [...],
            "findings": [...],
            "issues": [],
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
    security_groups_raw = event.get("SecurityGroups", [])
    network_interfaces = event.get("NetworkInterfaces", [])

    logger.info(f"Analyzing SGs for {project}/{env}: {len(security_groups_raw)} SGs with {len(network_interfaces)} ENIs")

    # Analyze each security group
    analyzed_sgs = []
    all_findings = []
    issues = []

    for sg in security_groups_raw:
        if not sg:
            continue

        try:
            analyzed = analyze_security_group(sg, network_interfaces)
            analyzed_sgs.append(analyzed)
            all_findings.extend(analyzed["findings"])
        except Exception as e:
            logger.exception(f"Error analyzing SG {sg.get('GroupId', 'unknown')}: {e}")
            issues.append(f"Error analyzing SG {sg.get('GroupId', 'unknown')}: {str(e)}")

    # Calculate summary
    summary = {
        "total": len(analyzed_sgs),
        "compliant": sum(1 for sg in analyzed_sgs if sg["compliant"]),
        "warnings": sum(1 for sg in analyzed_sgs if sg["status"] == "warning"),
        "violations": sum(1 for sg in analyzed_sgs if sg["status"] == "violation"),
        "unused": sum(1 for sg in analyzed_sgs if sg["usedBy"]["count"] == 0),
        "findings": {
            "high": sum(1 for f in all_findings if f.get("severity") == "HIGH"),
            "medium": sum(1 for f in all_findings if f.get("severity") == "MEDIUM"),
            "low": sum(1 for f in all_findings if f.get("severity") == "LOW"),
            "total": len(all_findings),
        },
    }

    # Determine overall status
    status = determine_overall_status(summary, all_findings)

    # Filter to only HIGH and MEDIUM findings for the summary
    significant_findings = [
        f for f in all_findings
        if f.get("severity") in ("HIGH", "MEDIUM")
    ]

    # Build payload
    payload = {
        "status": status,
        "instance": instance,
        "environment": environment,
        "summary": summary,
        "securityGroups": analyzed_sgs,
        "findings": significant_findings,
        "issues": issues,
        "healthy": status == "ok",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    return {
        "statusCode": 200,
        "payload": payload,
    }
