"""
Save State Lambda
==================
Simple Lambda to save check results to DynamoDB with change detection.
Called by Step Functions after all checks complete.

Supports two formats:
1. New format (Dashborion integration): project/env/category/check_type
   - pk: {project}#{env}
   - sk: check:{category}:{check_type}:current

2. Legacy format: domain/target/check_type
   - pk: {domain}#{target}
   - sk: check:{check_type}:current

Environment Variables:
- STATE_TABLE_NAME: DynamoDB table name for state storage
- LOG_LEVEL: Logging level (default: INFO)
"""

import json
import logging
import os
import sys

# Add shared modules to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "shared"))
from state_manager import get_state_manager

logger = logging.getLogger(__name__)
logger.setLevel(os.environ.get("LOG_LEVEL", "INFO"))


def lambda_handler(event, context):
    """
    Save state to DynamoDB with change detection.

    New format (Dashborion):
    {
        "project": "mro-mi2",
        "env": "nh-staging",
        "category": "k8s",
        "check_type": "pods",
        "payload": {...},
        "metadata": {...}  # optional
    }

    Legacy format:
    {
        "domain": "mro",
        "target": "mi2-preprod",
        "check_type": "readiness",
        "payload": {...},
        "metadata": {...}  # optional
    }
    """
    logger.info(f"Event: {json.dumps(event)}")

    # Detect format and extract parameters
    if "project" in event and "env" in event:
        # New Dashborion format
        project = event.get("project")
        env = event.get("env")
        category = event.get("category")
        check_type = event.get("check_type")

        if not project:
            return {"statusCode": 400, "error": "Missing project"}
        if not env:
            return {"statusCode": 400, "error": "Missing env"}
        if not category:
            return {"statusCode": 400, "error": "Missing category"}
        if not check_type:
            return {"statusCode": 400, "error": "Missing check_type"}

        # Build pk and full check_type for state_manager
        pk_domain = project
        pk_target = env
        full_check_type = f"{category}:{check_type}"

        response_extra = {
            "project": project,
            "env": env,
            "category": category,
            "check_type": check_type,
        }
    else:
        # Legacy format
        pk_domain = event.get("domain")
        pk_target = event.get("target")
        full_check_type = event.get("check_type", "readiness")

        if not pk_domain:
            return {"statusCode": 400, "error": "Missing domain"}
        if not pk_target:
            return {"statusCode": 400, "error": "Missing target"}

        response_extra = {
            "domain": pk_domain,
            "target": pk_target,
            "check_type": full_check_type,
        }

    payload = event.get("payload", {})
    metadata = event.get("metadata")

    if not payload:
        return {"statusCode": 400, "error": "Missing payload"}

    # Get updated_by from context or event
    updated_by = event.get("updated_by", f"lambda:{context.function_name}" if context else "step-function")

    # Save state
    try:
        state_manager = get_state_manager()
        result = state_manager.update_state(
            domain=pk_domain,
            target=pk_target,
            check_type=full_check_type,
            payload=payload,
            updated_by=updated_by,
            metadata=metadata,
        )

        return {
            "statusCode": 200,
            **response_extra,
            "state_update": result,
        }

    except Exception as e:
        logger.exception(f"Error saving state: {e}")
        return {
            "statusCode": 500,
            "error": str(e),
        }
