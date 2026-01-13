"""
Process Transit Gateway Data
=============================
Processes Transit Gateway data from AWS SDK calls, enriches with expected
attachments validation, and computes status.

This Lambda:
- Extracts TGW details, options, and tags
- Processes attachments and their states
- Processes route tables and routes
- Validates expected attachments are present
- Identifies issues (blackholes, missing attachments, unavailable states)
- Computes summary and status based on business rules

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


def extract_tags(tags_list: list) -> dict:
    """Convert AWS tags list to dict."""
    if not tags_list:
        return {}
    return {tag.get("Key", ""): tag.get("Value", "") for tag in tags_list}


def get_name_from_tags(tags: dict) -> str:
    """Extract Name tag value."""
    return tags.get("Name", "")


def process_transit_gateway(tgw: dict) -> dict:
    """Process Transit Gateway details."""
    if not tgw:
        return None

    tags = extract_tags(tgw.get("Tags", []))
    options = tgw.get("Options", {})

    return {
        "id": tgw.get("TransitGatewayId", ""),
        "arn": tgw.get("TransitGatewayArn", ""),
        "state": tgw.get("State", "unknown"),
        "ownerId": tgw.get("OwnerId", ""),
        "description": tgw.get("Description", ""),
        "options": {
            "amazonSideAsn": options.get("AmazonSideAsn"),
            "autoAcceptSharedAttachments": options.get("AutoAcceptSharedAttachments", "disable"),
            "defaultRouteTableAssociation": options.get("DefaultRouteTableAssociation", "disable"),
            "defaultRouteTablePropagation": options.get("DefaultRouteTablePropagation", "disable"),
            "vpnEcmpSupport": options.get("VpnEcmpSupport", "disable"),
            "dnsSupport": options.get("DnsSupport", "disable"),
            "multicastSupport": options.get("MulticastSupport", "disable"),
        },
        "tags": tags,
        "name": get_name_from_tags(tags),
    }


def process_attachment(attachment: dict) -> dict:
    """Process a single TGW attachment."""
    if not attachment:
        return None

    tags = extract_tags(attachment.get("Tags", []))
    association = attachment.get("Association", {})

    return {
        "id": attachment.get("TransitGatewayAttachmentId", ""),
        "type": attachment.get("ResourceType", "unknown"),
        "state": attachment.get("State", "unknown"),
        "resourceId": attachment.get("ResourceId", ""),
        "resourceOwnerId": attachment.get("ResourceOwnerId", ""),
        "name": get_name_from_tags(tags),
        "tags": tags,
        "association": {
            "transitGatewayRouteTableId": association.get("TransitGatewayRouteTableId"),
            "state": association.get("State", ""),
        } if association else None,
    }


def process_route_table(route_table: dict, routes_data: list) -> dict:
    """Process a single TGW route table with its routes."""
    if not route_table:
        return None

    tags = extract_tags(route_table.get("Tags", []))
    rt_id = route_table.get("TransitGatewayRouteTableId", "")

    # Find routes for this route table
    routes = []
    for route_result in routes_data or []:
        if route_result.get("RouteTableId") == rt_id:
            for route in route_result.get("Routes", []):
                routes.append({
                    "destinationCidrBlock": route.get("DestinationCidrBlock", ""),
                    "prefixListId": route.get("PrefixListId"),
                    "type": route.get("Type", ""),
                    "state": route.get("State", ""),
                    "attachmentId": (route.get("TransitGatewayAttachments", [{}])[0].get("TransitGatewayAttachmentId")
                                     if route.get("TransitGatewayAttachments") else None),
                })
            break

    return {
        "id": rt_id,
        "name": get_name_from_tags(tags),
        "state": route_table.get("State", "unknown"),
        "defaultAssociationRouteTable": route_table.get("DefaultAssociationRouteTable", False),
        "defaultPropagationRouteTable": route_table.get("DefaultPropagationRouteTable", False),
        "tags": tags,
        "routes": routes,
        "routeCount": len(routes),
        "blackholeCount": sum(1 for r in routes if r["state"] == "blackhole"),
    }


def match_expected_attachments(
    attachments: list,
    expected: list
) -> tuple[list, list]:
    """
    Match actual attachments against expected attachments.

    Returns (found, missing) lists.
    """
    found = []
    missing = []

    if not expected:
        return found, missing

    for exp in expected:
        exp_name = exp.get("name", "")
        exp_account = exp.get("accountId", "")
        exp_type = exp.get("type", "")

        matched = None
        for att in attachments:
            # Match by name tag
            if att.get("name") and att["name"].lower() == exp_name.lower():
                matched = att
                break
            # Match by account ID and type
            if exp_account and att.get("resourceOwnerId") == exp_account and att.get("type") == exp_type:
                matched = att
                break

        if matched:
            found.append({
                "name": exp_name,
                "expectedType": exp_type,
                "expectedAccountId": exp_account,
                "attachmentId": matched["id"],
                "actualType": matched["type"],
                "state": matched["state"],
                "status": "available" if matched["state"] == "available" else matched["state"],
            })
        else:
            missing.append({
                "name": exp_name,
                "expectedType": exp_type,
                "expectedAccountId": exp_account,
                "status": "missing",
            })

    return found, missing


def collect_issues(
    tgw: dict,
    attachments: list,
    route_tables: list,
    missing_attachments: list
) -> list:
    """Collect all issues found in TGW configuration."""
    issues = []

    # TGW state issues
    if tgw and tgw.get("state") != "available":
        issues.append(f"Transit Gateway state is {tgw.get('state', 'unknown')}")

    # Attachment issues
    for att in attachments:
        state = att.get("state", "unknown")
        name = att.get("name") or att.get("id", "unknown")

        if state == "rejected":
            issues.append(f"Attachment '{name}' is rejected")
        elif state == "failed":
            issues.append(f"Attachment '{name}' has failed")
        elif state == "deleted" or state == "deleting":
            issues.append(f"Attachment '{name}' is being deleted")
        elif state in ("pending", "pendingAcceptance", "modifying"):
            issues.append(f"Attachment '{name}' is in transitional state: {state}")
        elif state != "available":
            issues.append(f"Attachment '{name}' has unexpected state: {state}")

    # Missing expected attachments
    for missing in missing_attachments:
        issues.append(f"Expected attachment '{missing['name']}' not found")

    # Route table issues
    for rt in route_tables:
        if rt.get("blackholeCount", 0) > 0:
            issues.append(f"Route table '{rt.get('name') or rt.get('id')}' has {rt['blackholeCount']} blackhole routes")
        if rt.get("state") != "available":
            issues.append(f"Route table '{rt.get('name') or rt.get('id')}' state is {rt.get('state')}")

    return issues


def determine_status(
    tgw: dict,
    attachments: list,
    route_tables: list,
    missing_attachments: list,
    issues: list
) -> str:
    """
    Determine overall status based on TGW state.

    Returns: ok, warning, or critical
    """
    # Critical conditions
    if not tgw:
        return "critical"

    if tgw.get("state") != "available":
        return "critical"

    if missing_attachments:
        return "critical"

    critical_states = {"rejected", "failed", "deleted"}
    if any(att.get("state") in critical_states for att in attachments):
        return "critical"

    # Warning conditions
    warning_states = {"pending", "pendingAcceptance", "modifying", "deleting"}
    if any(att.get("state") in warning_states for att in attachments):
        return "warning"

    # Blackhole routes
    if any(rt.get("blackholeCount", 0) > 0 for rt in route_tables):
        return "warning"

    # Route table state issues
    if any(rt.get("state") != "available" for rt in route_tables):
        return "warning"

    # Attachments without associations
    unassociated = [att for att in attachments if not att.get("association")]
    if unassociated:
        return "warning"

    return "ok"


def lambda_handler(event: dict, context: Any) -> dict:
    """
    Lambda entry point.

    Event structure (from Step Function):
    {
        "Domain": "network",
        "Target": "global",
        "TransitGatewayId": "tgw-xxx",
        "TransitGateway": {...},        # From DescribeTransitGateways
        "Attachments": [...],           # From DescribeTransitGatewayAttachments
        "RouteTables": [...],           # From DescribeTransitGatewayRouteTables
        "Routes": [...],                # From SearchTransitGatewayRoutes (Map result)
        "ExpectedAttachments": [        # From input
            {"name": "rubix-dig-stg-webshop", "accountId": "281127105461", "type": "vpc"},
            ...
        ]
    }

    Returns:
    {
        "statusCode": 200,
        "payload": {
            "status": "ok | warning | critical",
            "summary": {...},
            "transitGateway": {...},
            "attachments": [...],
            "routeTables": [...],
            "expectedAttachments": {"found": [...], "missing": [...]},
            "issues": [...],
            "healthy": true,
            "timestamp": "ISO8601"
        }
    }
    """
    logger.info(f"Event keys: {list(event.keys())}")

    # Extract parameters
    domain = event.get("Domain", "network")
    target = event.get("Target", "global")
    tgw_id = event.get("TransitGatewayId", "")
    tgw_raw = event.get("TransitGateway")
    attachments_raw = event.get("Attachments", [])
    route_tables_raw = event.get("RouteTables", [])
    routes_raw = event.get("Routes", [])
    expected_attachments = event.get("ExpectedAttachments", [])

    logger.info(f"Processing TGW {tgw_id}: {len(attachments_raw)} attachments, {len(route_tables_raw)} route tables")

    # Process Transit Gateway
    tgw = process_transit_gateway(tgw_raw)

    # Process attachments
    attachments = [process_attachment(att) for att in attachments_raw if att]
    attachments = [att for att in attachments if att]  # Remove None values

    # Process route tables with routes
    route_tables = [process_route_table(rt, routes_raw) for rt in route_tables_raw if rt]
    route_tables = [rt for rt in route_tables if rt]  # Remove None values

    # Match expected attachments
    found_attachments, missing_attachments = match_expected_attachments(
        attachments, expected_attachments
    )

    # Collect issues
    issues = collect_issues(tgw, attachments, route_tables, missing_attachments)

    # Determine status
    status = determine_status(tgw, attachments, route_tables, missing_attachments, issues)

    # Count attachments by type
    by_type = {}
    for att in attachments:
        att_type = att.get("type", "unknown")
        by_type[att_type] = by_type.get(att_type, 0) + 1

    # Calculate summary
    available_attachments = sum(1 for att in attachments if att.get("state") == "available")
    summary = {
        "transitGatewayId": tgw_id,
        "state": tgw.get("state", "unknown") if tgw else "not_found",
        "totalAttachments": len(attachments),
        "availableAttachments": available_attachments,
        "routeTables": len(route_tables),
        "totalRoutes": sum(rt.get("routeCount", 0) for rt in route_tables),
        "blackholeRoutes": sum(rt.get("blackholeCount", 0) for rt in route_tables),
        "byType": by_type,
        "expectedAttachmentsFound": len(found_attachments),
        "expectedAttachmentsMissing": len(missing_attachments),
    }

    # Build payload
    payload = {
        "status": status,
        "summary": summary,
        "transitGateway": tgw,
        "attachments": attachments,
        "routeTables": route_tables,
        "expectedAttachments": {
            "found": found_attachments,
            "missing": missing_attachments,
        },
        "issues": issues,
        "healthy": status == "ok",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    return {
        "statusCode": 200,
        "payload": payload,
    }
