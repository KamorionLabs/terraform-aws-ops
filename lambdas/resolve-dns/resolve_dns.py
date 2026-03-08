"""
Resolve DNS
===========
Simple DNS resolution Lambda.
Resolves a list of hostnames and measures response times.

This Lambda does ONE thing: DNS resolution.
NO ADO, NO HCL parsing, NO Route53, NO status calculation.

Environment Variables:
- LOG_LEVEL: Logging level (default: INFO)
"""

import logging
import os
import socket
import time
from typing import Any

logger = logging.getLogger(__name__)
logger.setLevel(os.environ.get("LOG_LEVEL", "INFO"))

# DNS resolution timeout
DNS_TIMEOUT = 5.0


def resolve_hostname(hostname: str, record_type: str = "A") -> dict:
    """
    Perform DNS resolution for a single hostname.

    Returns dict with resolution results.
    """
    start_time = time.perf_counter()
    result = {
        "hostname": hostname,
        "resolved": False,
        "resolvedIPs": [],
        "responseTimeMs": 0,
        "error": None,
    }

    try:
        # Set socket timeout
        socket.setdefaulttimeout(DNS_TIMEOUT)

        # Perform resolution
        if record_type in ("A", "CNAME"):
            # Get canonical name and IPs
            canonical, aliases, ips = socket.gethostbyname_ex(hostname)
            result["resolvedIPs"] = ips
            result["resolved"] = len(ips) > 0
            if canonical != hostname:
                result["canonicalName"] = canonical
        else:
            # Default A record resolution
            ips = socket.gethostbyname_ex(hostname)[2]
            result["resolvedIPs"] = ips
            result["resolved"] = len(ips) > 0

    except socket.gaierror as e:
        result["error"] = f"DNS resolution failed: {str(e)}"
        result["resolved"] = False
    except socket.timeout:
        result["error"] = "DNS resolution timeout"
        result["resolved"] = False
    except Exception as e:
        result["error"] = f"Unexpected error: {str(e)}"
        result["resolved"] = False
    finally:
        end_time = time.perf_counter()
        result["responseTimeMs"] = round((end_time - start_time) * 1000, 2)

    return result


def lambda_handler(event: dict, context: Any) -> dict:
    """
    Lambda entry point.

    Input:
    {
        "Domains": [
            {"hostname": "example.com", "key": "example", "metadata": {...}},
            {"hostname": "test.com", "key": "test"},
            "simple-hostname.com"  # String format also supported
        ],
        "RecordType": "A"  # optional, default: A
    }

    Output:
    {
        "statusCode": 200,
        "resolutions": [
            {
                "key": "example",
                "hostname": "example.com",
                "resolved": true,
                "resolvedIPs": ["1.2.3.4"],
                "responseTimeMs": 12.5,
                "canonicalName": "example.com",  # if different
                "error": null,
                "metadata": {...}  # preserved from input
            }
        ],
        "summary": {
            "total": 10,
            "resolved": 9,
            "failed": 1,
            "avgResponseTimeMs": 15.3
        },
        "error": null
    }
    """
    logger.info(f"Event keys: {list(event.keys())}")

    domains = event.get("Domains", [])
    record_type = event.get("RecordType", "A")

    if not domains:
        return {
            "statusCode": 400,
            "resolutions": [],
            "summary": {"total": 0, "resolved": 0, "failed": 0, "avgResponseTimeMs": 0},
            "error": "No domains provided",
        }

    logger.info(f"Resolving {len(domains)} domains")

    resolutions = []
    total_time = 0

    for entry in domains:
        # Support both string and dict format
        if isinstance(entry, str):
            hostname = entry
            key = entry.split(".")[0]
            metadata = {}
        else:
            hostname = entry.get("hostname", "")
            key = entry.get("key", hostname.split(".")[0] if hostname else "unknown")
            metadata = entry.get("metadata", {})

        if not hostname:
            resolutions.append({
                "key": key,
                "hostname": "",
                "resolved": False,
                "resolvedIPs": [],
                "responseTimeMs": 0,
                "error": "No hostname provided",
                "metadata": metadata,
            })
            continue

        # Perform resolution
        result = resolve_hostname(hostname, record_type)
        result["key"] = key
        if metadata:
            result["metadata"] = metadata

        resolutions.append(result)
        total_time += result["responseTimeMs"]

    # Calculate summary
    resolved_count = sum(1 for r in resolutions if r.get("resolved"))
    failed_count = sum(1 for r in resolutions if not r.get("resolved"))
    avg_time = total_time / len(resolutions) if resolutions else 0

    summary = {
        "total": len(resolutions),
        "resolved": resolved_count,
        "failed": failed_count,
        "avgResponseTimeMs": round(avg_time, 2),
    }

    logger.info(f"Resolution summary: {summary}")

    return {
        "statusCode": 200,
        "resolutions": resolutions,
        "summary": summary,
        "error": None,
    }
