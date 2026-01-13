"""
Base Checker for Ops Dashboard
==============================
Abstract base class for all checkers. Provides common patterns for:
- Running checks and collecting results
- Determining overall status
- Saving state to DynamoDB

Usage:
    class InfraChecker(BaseChecker):
        domain = "mro"
        check_type = "infrastructure"

        def run_checks(self, config: dict) -> dict:
            # Implement your check logic here
            return {"rds": {...}, "efs": {...}, "eks": {...}}

    # In Lambda handler
    checker = InfraChecker()
    result = checker.execute(event, context)
"""

import json
import logging
import os
from abc import ABC, abstractmethod
from typing import Any, Optional

logger = logging.getLogger(__name__)
logger.setLevel(os.environ.get("LOG_LEVEL", "INFO"))


class BaseChecker(ABC):
    """
    Abstract base class for all checkers.

    Subclasses must define:
    - domain: str - The domain category (e.g., "mro", "webshop", "cost")
    - check_type: str - The type of check (e.g., "infrastructure", "pods")
    - run_checks(config) -> dict - The actual check implementation
    """

    domain: str = None
    check_type: str = None

    def __init__(self, state_manager=None):
        from state_manager import get_state_manager

        self.state_manager = state_manager or get_state_manager()

    @abstractmethod
    def run_checks(self, config: dict) -> dict:
        """
        Run the actual checks.

        Args:
            config: Configuration dict with target-specific settings

        Returns:
            dict with check results, each key being a component with its status
        """
        pass

    def determine_status(self, checks: dict) -> str:
        """
        Determine overall status based on individual check results.

        Override this method for custom status logic.

        Returns: "ready", "degraded", "not_ready", or "error"
        """
        statuses = []

        for component, check in checks.items():
            if isinstance(check, dict):
                if check.get("available") or check.get("status") in ("ok", "ready", "healthy"):
                    statuses.append("ok")
                elif check.get("status") == "error":
                    statuses.append("error")
                elif check.get("status") in ("not_found", "missing"):
                    statuses.append("missing")
                else:
                    statuses.append("not_ready")
            elif check is True:
                statuses.append("ok")
            elif check is False:
                statuses.append("not_ready")

        if not statuses:
            return "unknown"
        if all(s == "ok" for s in statuses):
            return "ready"
        elif "error" in statuses:
            return "error"
        elif "ok" in statuses:
            return "degraded"
        else:
            return "not_ready"

    def build_payload(self, checks: dict, extra: dict = None) -> dict:
        """
        Build the payload to store in DynamoDB.

        Args:
            checks: The check results
            extra: Optional extra data to include

        Returns:
            dict with overall_status, ready flag, checks, and any extra data
        """
        overall_status = self.determine_status(checks)

        payload = {
            "overall_status": overall_status,
            "ready": overall_status == "ready",
            "checks": checks,
        }

        if extra:
            payload.update(extra)

        return payload

    def execute(
        self,
        event: dict,
        context: Any = None,
        save_state: bool = True,
    ) -> dict:
        """
        Execute the checker.

        Args:
            event: Lambda event with target and config
            context: Lambda context
            save_state: Whether to save state to DynamoDB

        Expected event structure:
        {
            "target": "mi2-preprod",  # Required: target identifier
            "config": {...},          # Required: check-specific config
            "domain": "mro",          # Optional: override default domain
            "check_type": "infra",    # Optional: override default check_type
            "save_state": true,       # Optional: override save_state
            "metadata": {...}         # Optional: additional metadata
        }

        Returns:
            dict with statusCode and body
        """
        logger.info(f"Event: {json.dumps(event)}")

        # Extract parameters
        target = event.get("target")
        config = event.get("config", {})
        domain = event.get("domain", self.domain)
        check_type = event.get("check_type", self.check_type)
        save_state = event.get("save_state", save_state)
        metadata = event.get("metadata")

        # Validation
        if not target:
            return self._error_response(400, "Missing required parameter: target")

        if not domain:
            return self._error_response(400, "Missing domain (set class attribute or pass in event)")

        if not check_type:
            return self._error_response(400, "Missing check_type (set class attribute or pass in event)")

        # Run checks
        try:
            checks = self.run_checks(config)
        except Exception as e:
            logger.exception(f"Error running checks: {e}")
            return self._error_response(500, f"Error running checks: {str(e)}")

        # Build payload
        payload = self.build_payload(checks)

        # Save state
        state_result = None
        if save_state:
            try:
                updated_by = f"lambda:{context.function_name}" if context else "unknown"
                state_result = self.state_manager.update_state(
                    domain=domain,
                    target=target,
                    check_type=check_type,
                    payload=payload,
                    updated_by=updated_by,
                    metadata=metadata,
                )
                logger.info(f"State update result: {state_result}")
            except Exception as e:
                logger.exception(f"Error saving state: {e}")
                state_result = {"error": str(e)}

        return {
            "statusCode": 200,
            "body": json.dumps({
                "domain": domain,
                "target": target,
                "check_type": check_type,
                "result": payload,
                "state_update": state_result,
            }),
        }

    def _error_response(self, status_code: int, message: str) -> dict:
        """Create an error response."""
        return {
            "statusCode": status_code,
            "body": json.dumps({"error": message}),
        }


class GenericChecker(BaseChecker):
    """
    Generic checker that can be configured at runtime.

    Useful for simple checks or testing.

    Usage:
        checker = GenericChecker(
            domain="webshop",
            check_type="pods",
            check_fn=my_check_function
        )
        result = checker.execute(event, context)
    """

    def __init__(
        self,
        domain: str,
        check_type: str,
        check_fn: callable,
        state_manager=None,
    ):
        super().__init__(state_manager)
        self.domain = domain
        self.check_type = check_type
        self._check_fn = check_fn

    def run_checks(self, config: dict) -> dict:
        return self._check_fn(config)
