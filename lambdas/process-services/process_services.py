"""
Process Services Lambda
========================
Parses Kubernetes services and endpoints data from eks:call response and generates
summary, issues detection, and structured service information.

Called by Step Function after eks:call to:
- /api/v1/namespaces/{ns}/services
- /api/v1/namespaces/{ns}/endpoints

Environment Variables:
- LOG_LEVEL: Logging level (default: INFO)
"""

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)
logger.setLevel(os.environ.get("LOG_LEVEL", "INFO"))

# Thresholds
LOADBALANCER_PENDING_WARNING_SECONDS = 60  # 1 minute
LOADBALANCER_PENDING_CRITICAL_SECONDS = 300  # 5 minutes

# Critical services by namespace
CRITICAL_SERVICES = {
    "hybris": {"hybris", "hybris-admin", "solr", "solr-leader", "apache"},
}


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


def calculate_pending_duration(creation_timestamp: str) -> Optional[int]:
    """Calculate how long since creation (seconds)."""
    if not creation_timestamp:
        return None

    try:
        created = datetime.fromisoformat(creation_timestamp.replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        return int((now - created).total_seconds())
    except Exception:
        return None


def extract_ports(service_spec: dict) -> list[dict]:
    """Extract ports from service spec."""
    ports = []
    for port in service_spec.get("ports", []):
        ports.append({
            "name": port.get("name", ""),
            "port": port.get("port"),
            "targetPort": port.get("targetPort"),
            "protocol": port.get("protocol", "TCP"),
            "nodePort": port.get("nodePort"),
        })
    return ports


def extract_loadbalancer_info(service_status: dict) -> Optional[dict]:
    """Extract LoadBalancer information from service status."""
    lb = service_status.get("loadBalancer", {})
    ingress = lb.get("ingress", [])

    if not ingress:
        return {"status": "Pending", "hostname": None, "ip": None}

    first_ingress = ingress[0]
    return {
        "status": "Active",
        "hostname": first_ingress.get("hostname"),
        "ip": first_ingress.get("ip"),
    }


def get_endpoints_for_service(service_name: str, endpoints_list: list) -> dict:
    """Find endpoints matching a service name."""
    for ep in endpoints_list:
        ep_name = ep.get("metadata", {}).get("name")
        if ep_name == service_name:
            return ep
    return {}


def parse_endpoint_addresses(endpoint: dict) -> dict:
    """Parse endpoint subsets to get addresses."""
    subsets = endpoint.get("subsets", [])

    ready_addresses = []
    not_ready_addresses = []

    for subset in subsets:
        # Ready addresses
        for addr in subset.get("addresses", []):
            ready_addresses.append(addr.get("ip", "unknown"))

        # Not ready addresses
        for addr in subset.get("notReadyAddresses", []):
            not_ready_addresses.append(addr.get("ip", "unknown"))

    return {
        "ready": len(ready_addresses),
        "notReady": len(not_ready_addresses),
        "addresses": ready_addresses[:10],  # Limit to first 10 for readability
        "notReadyAddresses": not_ready_addresses[:10],
    }


def process_service(service: dict, endpoints_list: list) -> dict:
    """Process a single service and correlate with its endpoints."""
    metadata = service.get("metadata", {})
    spec = service.get("spec", {})
    status = service.get("status", {})

    name = metadata.get("name", "unknown")
    namespace = metadata.get("namespace", "unknown")
    service_type = spec.get("type", "ClusterIP")
    creation_timestamp = metadata.get("creationTimestamp", "")

    # Get endpoints for this service
    endpoint = get_endpoints_for_service(name, endpoints_list)
    endpoint_info = parse_endpoint_addresses(endpoint)

    # Build base service info
    service_info = {
        "name": name,
        "namespace": namespace,
        "type": service_type,
        "clusterIP": spec.get("clusterIP"),
        "externalIP": None,
        "ports": extract_ports(spec),
        "selector": spec.get("selector", {}),
        "endpoints": endpoint_info,
        "age": parse_age(creation_timestamp),
        "creationTimestamp": creation_timestamp,
        "labels": metadata.get("labels", {}),
        "annotations": {},  # Filtered annotations below
    }

    # Extract relevant AWS annotations
    annotations = metadata.get("annotations", {})
    aws_annotations = {k: v for k, v in annotations.items() if k.startswith("service.beta.kubernetes.io/")}
    service_info["annotations"] = aws_annotations

    # Handle LoadBalancer specific info
    if service_type == "LoadBalancer":
        lb_info = extract_loadbalancer_info(status)
        service_info["loadBalancer"] = lb_info
        service_info["externalIP"] = lb_info.get("hostname") or lb_info.get("ip")

    # Handle ExternalName (no endpoints)
    if service_type == "ExternalName":
        service_info["externalName"] = spec.get("externalName")
        service_info["endpoints"] = {"ready": 0, "notReady": 0, "addresses": [], "note": "ExternalName service"}

    # Determine health
    is_healthy = determine_service_health(service_info)
    service_info["healthy"] = is_healthy

    return service_info


def determine_service_health(service_info: dict) -> bool:
    """Determine if a service is healthy based on its configuration and endpoints."""
    service_type = service_info.get("type")
    endpoints = service_info.get("endpoints", {})

    # ExternalName services are always considered healthy
    if service_type == "ExternalName":
        return True

    # Check for ready endpoints
    ready_count = endpoints.get("ready", 0)
    if ready_count == 0:
        return False

    # LoadBalancer should have external IP/hostname
    if service_type == "LoadBalancer":
        lb = service_info.get("loadBalancer", {})
        if lb.get("status") == "Pending":
            return False

    return True


def detect_issues(services: list[dict], namespace: str) -> list[dict]:
    """Detect issues in services and return list of issues."""
    issues = []
    critical_services = CRITICAL_SERVICES.get(namespace, set())

    for service in services:
        service_name = service["name"]
        service_type = service["type"]
        endpoints = service.get("endpoints", {})
        ready_count = endpoints.get("ready", 0)
        not_ready_count = endpoints.get("notReady", 0)

        # Skip ExternalName services
        if service_type == "ExternalName":
            continue

        # No ready endpoints
        if ready_count == 0:
            severity = "critical" if service_name in critical_services else "warning"
            issues.append({
                "service": service_name,
                "severity": severity,
                "issue": "NoReadyEndpoints",
                "message": f"Service {service_name} has no ready endpoints",
            })

        # Some not ready endpoints
        if not_ready_count > 0:
            issues.append({
                "service": service_name,
                "severity": "warning",
                "issue": "NotReadyEndpoints",
                "message": f"Service {service_name} has {not_ready_count} not-ready endpoints",
            })

        # LoadBalancer pending
        if service_type == "LoadBalancer":
            lb = service.get("loadBalancer", {})
            if lb.get("status") == "Pending":
                creation_timestamp = service.get("creationTimestamp")
                pending_duration = calculate_pending_duration(creation_timestamp)

                if pending_duration and pending_duration > LOADBALANCER_PENDING_CRITICAL_SECONDS:
                    issues.append({
                        "service": service_name,
                        "severity": "critical",
                        "issue": "LoadBalancerPendingTooLong",
                        "message": f"LoadBalancer {service_name} has been pending for {pending_duration}s",
                    })
                elif pending_duration and pending_duration > LOADBALANCER_PENDING_WARNING_SECONDS:
                    issues.append({
                        "service": service_name,
                        "severity": "warning",
                        "issue": "LoadBalancerPending",
                        "message": f"LoadBalancer {service_name} is pending ({pending_duration}s)",
                    })

    # Check for missing critical services
    existing_service_names = {s["name"] for s in services}
    missing_critical = critical_services - existing_service_names
    for missing in missing_critical:
        issues.append({
            "service": missing,
            "severity": "critical",
            "issue": "CriticalServiceMissing",
            "message": f"Critical service {missing} is not present in namespace {namespace}",
        })

    return issues


def determine_status(summary: dict, issues: list[dict]) -> str:
    """Determine overall status based on summary and issues."""
    # Critical issues
    critical_issues = [i for i in issues if i.get("severity") == "critical"]
    if critical_issues:
        return "critical"

    # All services unhealthy
    if summary["total"] > 0 and summary["healthy"] == 0:
        return "critical"

    # Warning conditions
    warning_issues = [i for i in issues if i.get("severity") == "warning"]
    if warning_issues:
        return "warning"

    if summary["unhealthy"] > 0:
        return "warning"

    return "ok"


def lambda_handler(event: dict, context: Any) -> dict:
    """
    Process services and endpoints data from eks:call responses.

    Event structure (from Step Function):
    {
        "services": [<raw service objects from K8s API>],
        "endpoints": [<raw endpoint objects from K8s API>],
        "namespace": "hybris",
        "source": "legacy" | "nh",
        "cluster_name": "rubix-nonprod",
        "domain": "mro",
        "target": "mi1-ppd-legacy"
    }

    Returns:
    {
        "status": "ok" | "warning" | "critical",
        "source": "legacy" | "nh",
        "summary": {...},
        "services": [...],
        "issues": [...],
        "healthy": true | false,
        "timestamp": "ISO8601"
    }
    """
    logger.info(f"Processing {len(event.get('services', []))} services and {len(event.get('endpoints', []))} endpoints")

    raw_services = event.get("services", [])
    raw_endpoints = event.get("endpoints", [])
    source = event.get("source", "unknown")
    namespace = event.get("namespace", "unknown")

    # Initialize summary
    summary = {
        "total": 0,
        "healthy": 0,
        "unhealthy": 0,
        "byType": {
            "ClusterIP": 0,
            "LoadBalancer": 0,
            "NodePort": 0,
            "ExternalName": 0,
        },
    }

    # Process each service
    processed_services = []
    for service in raw_services:
        processed = process_service(service, raw_endpoints)
        processed_services.append(processed)

        # Update summary
        summary["total"] += 1
        service_type = processed["type"]
        if service_type in summary["byType"]:
            summary["byType"][service_type] += 1

        if processed["healthy"]:
            summary["healthy"] += 1
        else:
            summary["unhealthy"] += 1

    # Remove zero counts from byType
    summary["byType"] = {k: v for k, v in summary["byType"].items() if v > 0}

    # Detect issues
    issues = detect_issues(processed_services, namespace)

    # Determine overall status
    status = determine_status(summary, issues)

    # Build response
    timestamp = datetime.now(timezone.utc).isoformat()

    result = {
        "status": status,
        "source": source,
        "namespace": namespace,
        "summary": summary,
        "services": processed_services,
        "issues": issues,
        "healthy": status == "ok",
        "timestamp": timestamp,
    }

    logger.info(f"Service processing complete: status={status}, total={summary['total']}, "
                f"healthy={summary['healthy']}, issues={len(issues)}")

    return result
