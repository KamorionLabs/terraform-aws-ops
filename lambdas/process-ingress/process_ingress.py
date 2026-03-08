"""
Process Ingress Lambda
=======================
Parses Kubernetes Ingress and Service (LoadBalancer) data from eks:call response
and generates summary, issues detection, and structured ingress information.

Called by Step Function after eks:call to:
- GET /apis/networking.k8s.io/v1/namespaces/{ns}/ingresses
- GET /api/v1/namespaces/{ns}/services
- GET /apis/elbv2.k8s.aws/v1beta1/namespaces/{ns}/targetgroupbindings

Optionally enriches TargetGroupBindings with ALB target health data when
IncludeALBHealth=true is passed.

Environment Variables:
- LOG_LEVEL: Logging level (default: INFO)
"""

import boto3
import logging
import os
from datetime import datetime, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)
logger.setLevel(os.environ.get("LOG_LEVEL", "INFO"))

# Ingress type classification based on name patterns
INGRESS_TYPE_PATTERNS = {
    "front": ["front", "storefront", "public"],
    "bo": ["bo", "backoffice", "admin", "hac", "hmc"],
    "private": ["private", "internal", "api"],
}

# SFTP service patterns
SFTP_SERVICE_PATTERNS = ["sftp", "ftp"]

# TargetGroupBinding classification patterns
TGB_TYPE_PATTERNS = {
    "sftp": ["sftp", "ftp"],
    "front": ["front", "storefront", "apache"],
    "bo": ["bo", "backoffice", "admin", "hybris-bo"],
    "api": ["api"],
    "solr": ["solr"],
    "smui": ["smui"],
}

# Critical annotations that should be present
REQUIRED_ALB_ANNOTATIONS = [
    "alb.ingress.kubernetes.io/scheme",
    "alb.ingress.kubernetes.io/target-type",
]

# Warning thresholds
NO_ADDRESS_WARNING_SECONDS = 60  # 1 minute
NO_ADDRESS_CRITICAL_SECONDS = 300  # 5 minutes


def parse_age(creation_timestamp: str) -> str:
    """Convert creation timestamp to human-readable age."""
    try:
        created = datetime.fromisoformat(creation_timestamp.replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        delta = now - created

        days = delta.days
        hours = delta.seconds // 3600
        minutes = (delta.seconds % 3600) // 60

        if days > 0:
            return f"{days}d"
        elif hours > 0:
            return f"{hours}h"
        else:
            return f"{minutes}m"
    except Exception:
        return "unknown"


def get_age_seconds(creation_timestamp: str) -> Optional[int]:
    """Get age in seconds from creation timestamp."""
    try:
        created = datetime.fromisoformat(creation_timestamp.replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        return int((now - created).total_seconds())
    except Exception:
        return None


def classify_ingress_type(ingress_name: str, annotations: dict) -> str:
    """Classify ingress type based on name and annotations."""
    name_lower = ingress_name.lower()

    # Check annotations first for explicit type
    for annotation_key, annotation_value in annotations.items():
        if "type" in annotation_key.lower():
            value_lower = str(annotation_value).lower()
            for ingress_type, patterns in INGRESS_TYPE_PATTERNS.items():
                for pattern in patterns:
                    if pattern in value_lower:
                        return ingress_type

    # Check name patterns
    for ingress_type, patterns in INGRESS_TYPE_PATTERNS.items():
        for pattern in patterns:
            if pattern in name_lower:
                return ingress_type

    return "unknown"


def extract_load_balancer_info(ingress: dict) -> dict:
    """Extract load balancer information from ingress status."""
    status = ingress.get("status", {})
    load_balancer = status.get("loadBalancer", {})
    ingresses = load_balancer.get("ingress", [])

    if not ingresses:
        return {"hostname": None, "scheme": None}

    # Take the first ingress entry
    lb_entry = ingresses[0]
    hostname = lb_entry.get("hostname")

    # Determine scheme from annotations
    annotations = ingress.get("metadata", {}).get("annotations", {})
    scheme = annotations.get("alb.ingress.kubernetes.io/scheme", "unknown")

    return {
        "hostname": hostname,
        "scheme": scheme,
    }


def extract_rules(ingress: dict) -> list[dict]:
    """Extract routing rules from ingress spec."""
    spec = ingress.get("spec", {})
    rules = spec.get("rules", [])

    extracted_rules = []
    for rule in rules:
        host = rule.get("host", "*")
        http = rule.get("http", {})
        paths = http.get("paths", [])

        for path_entry in paths:
            path = path_entry.get("path", "/")
            path_type = path_entry.get("pathType", "Prefix")
            backend = path_entry.get("backend", {})

            # Extract service info
            service = backend.get("service", {})
            service_name = service.get("name", "unknown")
            service_port = service.get("port", {})
            port_name = service_port.get("name")
            port_number = service_port.get("number")
            port_str = port_name or str(port_number) if port_number else "unknown"

            extracted_rules.append({
                "host": host,
                "path": path,
                "pathType": path_type,
                "backend": f"{service_name}:{port_str}",
                "serviceName": service_name,
                "servicePort": port_str,
            })

    return extracted_rules


def extract_tls(ingress: dict) -> list[dict]:
    """Extract TLS configuration from ingress spec."""
    spec = ingress.get("spec", {})
    tls_list = spec.get("tls", [])

    extracted_tls = []
    for tls in tls_list:
        extracted_tls.append({
            "hosts": tls.get("hosts", []),
            "secretName": tls.get("secretName"),
        })

    return extracted_tls


def process_ingress(ingress: dict) -> dict:
    """Process a single ingress and extract relevant information."""
    metadata = ingress.get("metadata", {})
    spec = ingress.get("spec", {})
    annotations = metadata.get("annotations", {})

    name = metadata.get("name", "unknown")
    namespace = metadata.get("namespace", "unknown")
    creation_timestamp = metadata.get("creationTimestamp", "")

    # Classify ingress type
    ingress_type = classify_ingress_type(name, annotations)

    # Extract ingress class
    ingress_class = spec.get("ingressClassName") or annotations.get(
        "kubernetes.io/ingress.class", "unknown"
    )

    # Extract load balancer info
    load_balancer = extract_load_balancer_info(ingress)

    # Extract rules
    rules = extract_rules(ingress)

    # Extract TLS
    tls = extract_tls(ingress)

    # Determine if healthy (has address assigned)
    has_address = load_balancer.get("hostname") is not None
    age_seconds = get_age_seconds(creation_timestamp)

    # Healthy if has address or is very new
    healthy = has_address or (age_seconds is not None and age_seconds < NO_ADDRESS_WARNING_SECONDS)

    return {
        "name": name,
        "namespace": namespace,
        "type": ingress_type,
        "class": ingress_class,
        "loadBalancer": load_balancer,
        "rules": rules,
        "tls": tls,
        "annotations": annotations,
        "healthy": healthy,
        "hasAddress": has_address,
        "age": parse_age(creation_timestamp),
        "ageSeconds": age_seconds,
    }


def is_sftp_service(service_name: str) -> bool:
    """Check if service is an SFTP/FTP service."""
    name_lower = service_name.lower()
    return any(pattern in name_lower for pattern in SFTP_SERVICE_PATTERNS)


def classify_tgb_type(tgb_name: str) -> str:
    """Classify TargetGroupBinding type based on name."""
    name_lower = tgb_name.lower()
    for tgb_type, patterns in TGB_TYPE_PATTERNS.items():
        for pattern in patterns:
            if pattern in name_lower:
                return tgb_type
    return "other"


def process_target_group_binding(tgb: dict) -> dict:
    """Process a single TargetGroupBinding and extract relevant information."""
    metadata = tgb.get("metadata", {})
    spec = tgb.get("spec", {})
    status = tgb.get("status", {})

    name = metadata.get("name", "unknown")
    namespace = metadata.get("namespace", "unknown")
    creation_timestamp = metadata.get("creationTimestamp", "")

    # Extract service reference
    service_ref = spec.get("serviceRef", {})
    service_name = service_ref.get("name", "unknown")
    service_port = service_ref.get("port")

    # Extract target group info
    target_group_arn = spec.get("targetGroupARN", "")
    target_group_name = spec.get("targetGroupName", "")
    target_type = spec.get("targetType", "ip")
    protocol = spec.get("targetGroupProtocol", "TCP")
    vpc_id = spec.get("vpcID", "")

    # Classify TGB type
    tgb_type = classify_tgb_type(name)

    # Check if it's an SFTP TGB
    is_sftp = tgb_type == "sftp"

    # Determine health (observedGeneration present means controller has processed it)
    observed_generation = status.get("observedGeneration")
    healthy = observed_generation is not None

    return {
        "name": name,
        "namespace": namespace,
        "type": tgb_type,
        "isSftp": is_sftp,
        "serviceRef": {
            "name": service_name,
            "port": service_port,
        },
        "targetGroup": {
            "arn": target_group_arn,
            "name": target_group_name,
            "targetType": target_type,
            "protocol": protocol,
        },
        "vpcId": vpc_id,
        "healthy": healthy,
        "age": parse_age(creation_timestamp),
        "ageSeconds": get_age_seconds(creation_timestamp),
    }


def get_elbv2_client(cross_account_role_arn: str | None = None):
    """Get ELBv2 client, optionally assuming a cross-account role."""
    if cross_account_role_arn:
        sts_client = boto3.client("sts")
        assumed_role = sts_client.assume_role(
            RoleArn=cross_account_role_arn,
            RoleSessionName="process-ingress-alb-health"
        )
        credentials = assumed_role["Credentials"]
        return boto3.client(
            "elbv2",
            aws_access_key_id=credentials["AccessKeyId"],
            aws_secret_access_key=credentials["SecretAccessKey"],
            aws_session_token=credentials["SessionToken"]
        )
    return boto3.client("elbv2")


def fetch_target_health(elbv2_client, target_group_arn: str) -> dict:
    """Fetch target health for a specific target group."""
    try:
        response = elbv2_client.describe_target_health(
            TargetGroupArn=target_group_arn
        )

        targets = response.get("TargetHealthDescriptions", [])

        healthy = 0
        unhealthy = 0
        draining = 0
        unused = 0

        target_details = []
        for target in targets:
            health = target.get("TargetHealth", {})
            state = health.get("State", "unknown")

            if state == "healthy":
                healthy += 1
            elif state == "unhealthy":
                unhealthy += 1
            elif state == "draining":
                draining += 1
            elif state in ("unused", "initial"):
                unused += 1

            target_details.append({
                "id": target.get("Target", {}).get("Id"),
                "port": target.get("Target", {}).get("Port"),
                "state": state,
                "reason": health.get("Reason"),
                "description": health.get("Description"),
            })

        total = len(targets)
        health_ratio = (healthy / total * 100) if total > 0 else 0

        return {
            "healthy": healthy,
            "unhealthy": unhealthy,
            "draining": draining,
            "unused": unused,
            "total": total,
            "healthRatio": round(health_ratio, 1),
            "targets": target_details,
            "status": "healthy" if healthy == total and total > 0 else (
                "degraded" if healthy > 0 else "unhealthy"
            ),
        }
    except Exception as e:
        logger.warning(f"Failed to fetch target health for {target_group_arn}: {e}")
        return {
            "healthy": 0,
            "unhealthy": 0,
            "draining": 0,
            "unused": 0,
            "total": 0,
            "healthRatio": 0,
            "targets": [],
            "status": "error",
            "error": str(e),
        }


def enrich_tgbs_with_alb_health(
    tgbs: list[dict],
    cross_account_role_arn: str | None = None
) -> tuple[list[dict], dict]:
    """
    Enrich TargetGroupBindings with ALB target health data.

    Returns:
        Tuple of (enriched_tgbs, alb_health_summary)
    """
    if not tgbs:
        return tgbs, {"totalTargets": 0, "healthyTargets": 0, "healthRatio": 0}

    # Get ELBv2 client
    elbv2_client = get_elbv2_client(cross_account_role_arn)

    total_healthy = 0
    total_targets = 0

    enriched_tgbs = []
    for tgb in tgbs:
        tgb_copy = dict(tgb)
        target_group = tgb_copy.get("targetGroup", {})
        arn = target_group.get("arn")

        if arn:
            health_data = fetch_target_health(elbv2_client, arn)
            tgb_copy["albHealth"] = health_data
            total_healthy += health_data.get("healthy", 0)
            total_targets += health_data.get("total", 0)

            # Update TGB healthy status based on actual target health
            if health_data.get("status") == "healthy":
                tgb_copy["healthy"] = True
            elif health_data.get("status") in ("degraded", "unhealthy"):
                tgb_copy["healthy"] = False
        else:
            tgb_copy["albHealth"] = None

        enriched_tgbs.append(tgb_copy)

    health_ratio = (total_healthy / total_targets * 100) if total_targets > 0 else 0

    alb_summary = {
        "totalTargets": total_targets,
        "healthyTargets": total_healthy,
        "healthRatio": round(health_ratio, 1),
    }

    logger.info(f"ALB health enrichment: {total_healthy}/{total_targets} targets healthy ({health_ratio:.1f}%)")

    return enriched_tgbs, alb_summary


def process_service(service: dict) -> Optional[dict]:
    """Process a LoadBalancer service (for SFTP/NLB)."""
    metadata = service.get("metadata", {})
    spec = service.get("spec", {})
    status = service.get("status", {})

    service_type = spec.get("type", "")
    if service_type != "LoadBalancer":
        return None

    name = metadata.get("name", "unknown")
    namespace = metadata.get("namespace", "unknown")
    annotations = metadata.get("annotations", {})
    creation_timestamp = metadata.get("creationTimestamp", "")

    # Extract ports
    ports = []
    for port in spec.get("ports", []):
        ports.append({
            "port": port.get("port"),
            "targetPort": port.get("targetPort"),
            "protocol": port.get("protocol", "TCP"),
            "name": port.get("name"),
        })

    # Extract load balancer info
    load_balancer = status.get("loadBalancer", {})
    ingresses = load_balancer.get("ingress", [])
    hostname = ingresses[0].get("hostname") if ingresses else None

    # Determine load balancer type
    lb_type = "nlb" if "nlb" in annotations.get(
        "service.beta.kubernetes.io/aws-load-balancer-type", ""
    ).lower() else "clb"

    has_address = hostname is not None
    age_seconds = get_age_seconds(creation_timestamp)
    healthy = has_address or (age_seconds is not None and age_seconds < NO_ADDRESS_WARNING_SECONDS)

    return {
        "name": name,
        "namespace": namespace,
        "type": "LoadBalancer",
        "isSftp": is_sftp_service(name),
        "loadBalancer": {
            "hostname": hostname,
            "type": lb_type,
        },
        "ports": ports,
        "annotations": annotations,
        "healthy": healthy,
        "hasAddress": has_address,
        "age": parse_age(creation_timestamp),
        "ageSeconds": age_seconds,
    }


def detect_issues(
    ingresses: dict,
    sftp_service: Optional[dict],
    target_group_bindings: list[dict],
    services: list[dict],
) -> list[dict]:
    """Detect issues in ingresses, services, and TargetGroupBindings."""
    issues = []

    for ingress_type, ingress in ingresses.items():
        if ingress is None:
            continue

        name = ingress.get("name", "unknown")
        age_seconds = ingress.get("ageSeconds")

        # Check for missing address
        if not ingress.get("hasAddress"):
            if age_seconds and age_seconds > NO_ADDRESS_CRITICAL_SECONDS:
                issues.append({
                    "resource": name,
                    "type": "ingress",
                    "ingressType": ingress_type,
                    "severity": "critical",
                    "issue": "NoAddress",
                    "message": f"Ingress {name} has no load balancer address after {age_seconds}s",
                })
            elif age_seconds and age_seconds > NO_ADDRESS_WARNING_SECONDS:
                issues.append({
                    "resource": name,
                    "type": "ingress",
                    "ingressType": ingress_type,
                    "severity": "warning",
                    "issue": "NoAddress",
                    "message": f"Ingress {name} has no load balancer address after {age_seconds}s",
                })

        # Check for missing TLS on public ingresses
        if ingress_type == "front":
            tls = ingress.get("tls", [])
            rules = ingress.get("rules", [])
            hosts_with_tls = set()
            for tls_entry in tls:
                hosts_with_tls.update(tls_entry.get("hosts", []))

            for rule in rules:
                host = rule.get("host", "*")
                if host != "*" and host not in hosts_with_tls:
                    if not host.endswith(".internal"):
                        issues.append({
                            "resource": name,
                            "type": "ingress",
                            "ingressType": ingress_type,
                            "severity": "warning",
                            "issue": "NoTLS",
                            "message": f"Host {host} on public ingress {name} has no TLS",
                        })

        # Check for missing required annotations
        annotations = ingress.get("annotations", {})
        for required_annotation in REQUIRED_ALB_ANNOTATIONS:
            if required_annotation not in annotations:
                issues.append({
                    "resource": name,
                    "type": "ingress",
                    "ingressType": ingress_type,
                    "severity": "warning",
                    "issue": "MissingAnnotation",
                    "message": f"Ingress {name} missing annotation: {required_annotation}",
                })

    # Check SFTP service (legacy LoadBalancer type)
    if sftp_service:
        name = sftp_service.get("name", "unknown")
        if not sftp_service.get("hasAddress"):
            age_seconds = sftp_service.get("ageSeconds")
            if age_seconds and age_seconds > NO_ADDRESS_CRITICAL_SECONDS:
                issues.append({
                    "resource": name,
                    "type": "service",
                    "severity": "critical",
                    "issue": "NoAddress",
                    "message": f"SFTP service {name} has no load balancer address after {age_seconds}s",
                })

    # Check TargetGroupBindings
    # Build a set of service names for quick lookup
    service_names = {svc.get("metadata", {}).get("name") for svc in services}

    for tgb in target_group_bindings:
        name = tgb.get("name", "unknown")
        service_ref = tgb.get("serviceRef", {})
        referenced_service = service_ref.get("name")

        # Check if TGB is not healthy (no observedGeneration)
        if not tgb.get("healthy"):
            issues.append({
                "resource": name,
                "type": "targetgroupbinding",
                "tgbType": tgb.get("type"),
                "severity": "warning",
                "issue": "NotProcessed",
                "message": f"TargetGroupBinding {name} has not been processed by controller",
            })

        # Check if referenced service exists
        if referenced_service and referenced_service not in service_names:
            issues.append({
                "resource": name,
                "type": "targetgroupbinding",
                "tgbType": tgb.get("type"),
                "severity": "critical",
                "issue": "MissingService",
                "message": f"TargetGroupBinding {name} references missing service {referenced_service}",
            })

        # Check if target group ARN is empty
        target_group = tgb.get("targetGroup", {})
        if not target_group.get("arn"):
            issues.append({
                "resource": name,
                "type": "targetgroupbinding",
                "tgbType": tgb.get("type"),
                "severity": "critical",
                "issue": "MissingTargetGroupARN",
                "message": f"TargetGroupBinding {name} has no target group ARN",
            })

    return issues


def determine_status(summary: dict, issues: list[dict]) -> str:
    """Determine overall status based on summary and issues."""
    critical_issues = [i for i in issues if i.get("severity") == "critical"]
    if critical_issues:
        return "critical"

    warning_issues = [i for i in issues if i.get("severity") == "warning"]
    if warning_issues:
        return "warning"

    # Check if all expected ingresses are healthy
    if summary.get("healthy", 0) < summary.get("totalIngresses", 0):
        return "warning"

    return "ok"


def flatten_rules(ingresses: dict) -> list[dict]:
    """Flatten all rules from all ingresses for comparison."""
    all_rules = []

    for ingress_type, ingress in ingresses.items():
        if ingress is None:
            continue

        for rule in ingress.get("rules", []):
            all_rules.append({
                "type": ingress_type,
                "host": rule.get("host"),
                "path": rule.get("path"),
                "backend": rule.get("backend"),
            })

    return all_rules


def lambda_handler(event: dict, context: Any) -> dict:
    """
    Process ingress, service, and TargetGroupBinding data from eks:call responses.

    Event structure (from Step Function):
    {
        "ingresses": [<raw ingress objects from K8s API>],
        "services": [<raw service objects from K8s API>],
        "targetgroupbindings": [<raw TGB objects from K8s API>],
        "namespace": "hybris",
        "source": "legacy" | "nh",
        "cluster_name": "rubix-nonprod",
        "domain": "mro",
        "target": "mi1-ppd-legacy",
        "ingress_types": ["front", "bo", "private", "sftp"],
        "include_alb_health": false,
        "cross_account_role_arn": "arn:aws:iam::..."
    }

    Returns:
    {
        "status": "ok" | "warning" | "critical",
        "source": "legacy" | "nh",
        "summary": {...},
        "ingresses": {...},
        "targetGroupBindings": [...],
        "albHealthSummary": {...},  # If include_alb_health=true
        "sftpService": {...},
        "allRules": [...],
        "issues": [...],
        "timestamp": "ISO8601"
    }
    """
    raw_ingresses = event.get("ingresses", [])
    raw_services = event.get("services", [])
    raw_tgbs = event.get("targetgroupbindings", [])

    # ALB health enrichment parameters (from nested input or top-level)
    input_params = event.get("input", {})
    include_alb_health = input_params.get("IncludeALBHealth", False) or event.get("include_alb_health", False)
    cross_account_role_arn = input_params.get("CrossAccountRoleArn") or event.get("cross_account_role_arn")

    logger.info(
        f"Processing {len(raw_ingresses)} ingresses, "
        f"{len(raw_services)} services, and "
        f"{len(raw_tgbs)} targetgroupbindings "
        f"(ALB health enrichment: {include_alb_health})"
    )

    source = event.get("source", "unknown")
    namespace = event.get("namespace", "unknown")
    requested_types = event.get("ingress_types", ["front", "bo", "private", "sftp"])

    # Process ingresses
    processed_ingresses = {}
    for ingress in raw_ingresses:
        processed = process_ingress(ingress)
        ingress_type = processed["type"]

        # Store by type (first one wins if multiple of same type)
        if ingress_type not in processed_ingresses:
            processed_ingresses[ingress_type] = processed

    # Process TargetGroupBindings
    processed_tgbs = []
    sftp_tgb = None
    for tgb in raw_tgbs:
        processed = process_target_group_binding(tgb)
        processed_tgbs.append(processed)
        # Track SFTP TGB separately
        if processed.get("isSftp"):
            sftp_tgb = processed

    # Enrich TGBs with ALB health data if requested
    alb_health_summary = None
    if include_alb_health and processed_tgbs:
        processed_tgbs, alb_health_summary = enrich_tgbs_with_alb_health(
            processed_tgbs, cross_account_role_arn
        )
        # Update sftp_tgb reference if it was enriched
        for tgb in processed_tgbs:
            if tgb.get("isSftp"):
                sftp_tgb = tgb
                break

    # Process services (find SFTP LoadBalancer - legacy pattern)
    sftp_service = None
    for service in raw_services:
        processed = process_service(service)
        if processed and processed.get("isSftp"):
            sftp_service = processed
            break

    # Build summary
    total_ingresses = len(processed_ingresses)
    healthy_ingresses = sum(1 for i in processed_ingresses.values() if i.get("healthy"))
    with_tls = sum(1 for i in processed_ingresses.values() if i.get("tls"))

    total_tgbs = len(processed_tgbs)
    healthy_tgbs = sum(1 for t in processed_tgbs if t.get("healthy"))

    # Determine SFTP status: prefer TGB over LoadBalancer service
    has_sftp = sftp_tgb is not None or sftp_service is not None
    sftp_healthy = None
    if sftp_tgb:
        sftp_healthy = sftp_tgb.get("healthy")
    elif sftp_service:
        sftp_healthy = sftp_service.get("healthy")

    summary = {
        "totalIngresses": total_ingresses,
        "healthy": healthy_ingresses,
        "withTLS": with_tls,
        "totalTargetGroupBindings": total_tgbs,
        "healthyTargetGroupBindings": healthy_tgbs,
        "sftpService": has_sftp,
        "sftpHealthy": sftp_healthy,
        "sftpType": "tgb" if sftp_tgb else ("loadbalancer" if sftp_service else None),
    }

    # Add ALB health summary if enrichment was performed
    if alb_health_summary:
        summary["albHealth"] = alb_health_summary

    # Detect issues
    issues = detect_issues(processed_ingresses, sftp_service, processed_tgbs, raw_services)

    # Determine overall status
    status = determine_status(summary, issues)

    # Flatten all rules for comparison
    all_rules = flatten_rules(processed_ingresses)

    # Build response
    timestamp = datetime.now(timezone.utc).isoformat()

    result = {
        "status": status,
        "source": source,
        "namespace": namespace,
        "summary": summary,
        "ingresses": processed_ingresses,
        "targetGroupBindings": processed_tgbs,
        "sftpService": sftp_service,
        "allRules": all_rules,
        "issues": issues,
        "timestamp": timestamp,
    }

    logger.info(
        f"Ingress processing complete: status={status}, "
        f"ingresses={total_ingresses}, tgbs={total_tgbs}, "
        f"sftp={has_sftp} (type={summary['sftpType']}), issues={len(issues)}"
    )

    return result
