"""
Application Component Health Checker Lambda
============================================
Generic health checker for Rubix application components (SMUI, Hybris, Solr, Apache).
Uses a plugin architecture to support component-specific checks.

This Lambda is designed to be called from a Step Function that:
1. Fetches pod info via eks:call
2. Optionally fetches pod logs via eks:call
3. Invokes this Lambda with pod data + logs

The Lambda then:
1. Selects the appropriate plugin based on component type
2. Runs all checks defined by the plugin
3. Saves state to DynamoDB

Environment Variables:
- STATE_TABLE_NAME: DynamoDB table name for state storage
- LOG_LEVEL: Logging level (default: INFO)
"""

import json
import logging
import os
import sys
from datetime import datetime, timezone
from typing import Any, Optional

# Add shared modules to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "shared", "python"))
from state_manager import get_state_manager

logger = logging.getLogger(__name__)
logger.setLevel(os.environ.get("LOG_LEVEL", "INFO"))

# Plugin registry - populated by imports
COMPONENT_PLUGINS = {}


def register_plugin(component_name: str):
    """Decorator to register a plugin for a component."""
    def decorator(cls):
        COMPONENT_PLUGINS[component_name] = cls
        return cls
    return decorator


def get_plugin(component_name: str):
    """Get plugin class for a component."""
    if component_name not in COMPONENT_PLUGINS:
        raise ValueError(f"Unknown component: {component_name}. Available: {list(COMPONENT_PLUGINS.keys())}")
    return COMPONENT_PLUGINS[component_name]


# Import plugins to register them
from plugins import smui_plugin  # noqa: E402
from plugins import apache_plugin  # noqa: E402
from plugins import solr_plugin  # noqa: E402
from plugins import hybris_plugin  # noqa: E402


def run_component_checks(
    component: str,
    pods: list[dict],
    logs: Optional[dict] = None,
    config: Optional[dict] = None,
) -> dict:
    """
    Run health checks for a component.

    Args:
        component: Component name (smui, hybris, solr, apache)
        pods: List of pod data from K8s API
        logs: Optional dict of {pod_name: log_content}
        config: Optional component-specific configuration

    Returns:
        dict with check results
    """
    plugin_cls = get_plugin(component)
    plugin = plugin_cls(config or {})
    return plugin.run_all_checks(pods, logs)


def determine_overall_status(checks: list[dict]) -> str:
    """
    Determine overall status from individual check results.

    Status priority: critical > warning > unknown > ok
    """
    statuses = [check.get("status", "unknown") for check in checks]

    if "critical" in statuses:
        return "critical"
    if "warning" in statuses:
        return "warning"
    if "unknown" in statuses:
        return "unknown"
    return "ok"


def extract_issues(checks: list[dict]) -> list[dict]:
    """Extract issues from check results."""
    issues = []
    for check in checks:
        if check.get("status") in ("warning", "critical"):
            issues.append({
                "severity": check["status"],
                "check": check.get("name"),
                "message": check.get("message"),
            })
    return issues


def build_payload(
    component: str,
    checks: list[dict],
    pods: list[dict],
    config: dict,
) -> dict:
    """Build the payload to store in DynamoDB."""
    overall_status = determine_overall_status(checks)
    issues = extract_issues(checks)

    # Build summary
    pods_total = len(pods)
    pods_ready = sum(1 for pod in pods if _is_pod_ready(pod))

    return {
        "component": component,
        "status": overall_status,
        "healthy": overall_status == "ok",
        "summary": {
            "pods_total": pods_total,
            "pods_ready": pods_ready,
            "checks_total": len(checks),
            "checks_ok": sum(1 for c in checks if c.get("status") == "ok"),
            "checks_warning": sum(1 for c in checks if c.get("status") == "warning"),
            "checks_critical": sum(1 for c in checks if c.get("status") == "critical"),
        },
        "checks": checks,
        "issues": issues,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "config_used": {
            "label_selector": config.get("label_selector"),
            "logs_source": config.get("logs", {}).get("primary", "eks"),
        },
    }


def _is_pod_ready(pod: dict) -> bool:
    """Check if a pod is ready based on K8s status."""
    status = pod.get("status", {})
    conditions = status.get("conditions", [])
    for condition in conditions:
        if condition.get("type") == "Ready" and condition.get("status") == "True":
            return True
    return False


def _transform_logs_to_dict(logs_input: Any) -> dict:
    """
    Transform logs from Step Function format to dict.

    Step Function Map returns an array of {podName, logs} objects.
    This function converts it to {pod_name: log_content} dict format.
    """
    if isinstance(logs_input, dict):
        # Already in correct format
        return logs_input
    elif isinstance(logs_input, list):
        # Array from Step Function Map state
        result = {}
        for item in logs_input:
            if isinstance(item, dict):
                pod_name = item.get("podName", item.get("pod_name"))
                log_content = item.get("logs", item.get("log", ""))
                if pod_name:
                    result[pod_name] = log_content if log_content else ""
        return result
    return {}


def lambda_handler(event: dict, context: Any) -> dict:
    """
    Lambda entry point.

    Event structure (from Step Function):
    {
        "Project": "mro-mi2",
        "Env": "legacy-stg",
        "Component": "smui",
        "ClusterName": "rubix-nonprod-admin",
        "Namespace": "mi2-staging",
        "Pods": [...],           # Pod data from eks:call
        "Logs": [...] or {...},  # Logs from Step Function Map or dict format
        "ComponentConfig": {     # Optional: component-specific config
            "label_selector": "app.kubernetes.io/name=smui",
            "logs": {
                "primary": "eks",
                "tail_lines": 100
            },
            "custom_checks": {
                "jdbc_database": "smui_mi1",
                "jdbc_host": "mi1-rds-writer-prod.iph.nbs-aws.com"
            }
        }
    }

    Returns:
        dict with statusCode and body
    """
    logger.info(f"Event: {json.dumps(event, default=str)}")

    # Extract parameters
    project = event.get("Project")
    env = event.get("Env")
    component = event.get("Component")
    cluster_name = event.get("ClusterName")
    namespace = event.get("Namespace")
    pods = event.get("Pods", [])
    logs_raw = event.get("Logs", {})
    config = event.get("ComponentConfig", {})

    # Transform logs from Step Function array format to dict
    logs = _transform_logs_to_dict(logs_raw)

    # Validation
    if not all([project, env, component]):
        return {
            "statusCode": 400,
            "body": json.dumps({
                "error": "Missing required parameters: Project, Env, Component"
            }),
        }

    if component not in COMPONENT_PLUGINS:
        return {
            "statusCode": 400,
            "body": json.dumps({
                "error": f"Unknown component: {component}. Available: {list(COMPONENT_PLUGINS.keys())}"
            }),
        }

    try:
        # Run component checks
        checks = run_component_checks(
            component=component,
            pods=pods,
            logs=logs,
            config=config,
        )

        # Build payload
        payload = build_payload(
            component=component,
            checks=checks,
            pods=pods,
            config=config,
        )

        # Save state to DynamoDB
        state_manager = get_state_manager()
        updated_by = f"lambda:{context.function_name}" if context else "unknown"

        state_result = state_manager.update_state(
            domain=project,
            target=env,
            check_type=f"app:{component}",
            payload=payload,
            updated_by=updated_by,
            metadata={
                "cluster": cluster_name,
                "namespace": namespace,
            },
        )

        logger.info(f"State update result: {state_result}")

        return {
            "statusCode": 200,
            "body": json.dumps({
                "project": project,
                "env": env,
                "component": component,
                "result": payload,
                "state_update": state_result,
            }),
        }

    except Exception as e:
        logger.exception(f"Error running component checks: {e}")
        return {
            "statusCode": 500,
            "body": json.dumps({"error": str(e)}),
        }
