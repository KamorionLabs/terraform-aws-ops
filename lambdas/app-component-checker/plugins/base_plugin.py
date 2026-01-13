"""
Base Plugin for Application Component Checks
=============================================
Abstract base class that all component plugins must inherit from.

Each plugin implements component-specific checks for:
- Pod status
- Container health
- Application logs analysis
- Database connectivity
- Component-specific validations

Check Result Format:
{
    "name": "check_name",
    "status": "ok|warning|critical|unknown",
    "message": "Human readable description",
    "details": {...}  # Check-specific details
}

Status Definitions:
- ok: Check passed, component is healthy
- warning: Non-critical issue detected, requires attention
- critical: Critical issue, component is unhealthy
- unknown: Unable to determine status (missing data, etc.)
"""

import logging
import re
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)


class CheckResult:
    """Represents the result of a single check."""

    def __init__(
        self,
        name: str,
        status: str,
        message: str,
        details: Optional[dict] = None,
    ):
        if status not in ("ok", "warning", "critical", "unknown"):
            raise ValueError(f"Invalid status: {status}")
        self.name = name
        self.status = status
        self.message = message
        self.details = details or {}
        self.timestamp = datetime.now(timezone.utc).isoformat()

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "status": self.status,
            "message": self.message,
            "details": self.details,
            "timestamp": self.timestamp,
        }


class BasePlugin(ABC):
    """
    Abstract base class for component plugins.

    Subclasses must implement:
    - COMPONENT_NAME: str - The component identifier
    - get_checks(): list - Return list of check methods to run
    - Each check method that returns a CheckResult
    """

    COMPONENT_NAME: str = None

    # Common patterns for log analysis
    LOG_PATTERNS = {}

    def __init__(self, config: dict):
        """
        Initialize plugin with configuration.

        Args:
            config: Component-specific configuration dict
        """
        self.config = config
        self.custom_checks = config.get("custom_checks", {})

    @abstractmethod
    def get_checks(self) -> list:
        """
        Return list of check methods to run.

        Returns:
            List of bound methods that return CheckResult
        """
        pass

    def run_all_checks(
        self,
        pods: list[dict],
        logs: Optional[dict] = None,
    ) -> list[dict]:
        """
        Run all checks and return results.

        Args:
            pods: List of pod data from K8s API
            logs: Optional dict of {pod_name: log_content}

        Returns:
            List of check results as dicts
        """
        self.pods = pods
        self.logs = logs or {}
        self.all_logs = "\n".join(self.logs.values()) if self.logs else ""

        results = []
        for check_method in self.get_checks():
            try:
                result = check_method()
                if isinstance(result, CheckResult):
                    results.append(result.to_dict())
                elif isinstance(result, dict):
                    results.append(result)
            except Exception as e:
                logger.exception(f"Error running check {check_method.__name__}: {e}")
                results.append(CheckResult(
                    name=check_method.__name__,
                    status="unknown",
                    message=f"Check failed with error: {str(e)}",
                    details={"error": str(e)},
                ).to_dict())

        return results

    # Common check implementations that plugins can use

    def check_pod_status(self) -> CheckResult:
        """Check if pods are running and ready."""
        if not self.pods:
            return CheckResult(
                name="pod_status",
                status="critical",
                message="No pods found",
                details={"pods_found": 0},
            )

        pods_info = []
        for pod in self.pods:
            metadata = pod.get("metadata", {})
            status = pod.get("status", {})

            pod_name = metadata.get("name", "unknown")
            phase = status.get("phase", "Unknown")

            # Check Ready condition
            ready = False
            for condition in status.get("conditions", []):
                if condition.get("type") == "Ready":
                    ready = condition.get("status") == "True"
                    break

            pods_info.append({
                "name": pod_name,
                "phase": phase,
                "ready": ready,
            })

        total = len(pods_info)
        ready_count = sum(1 for p in pods_info if p["ready"])
        running_count = sum(1 for p in pods_info if p["phase"] == "Running")

        if ready_count == total and running_count == total:
            return CheckResult(
                name="pod_status",
                status="ok",
                message=f"All {total} pod(s) running and ready",
                details={"pods": pods_info},
            )
        elif ready_count > 0:
            return CheckResult(
                name="pod_status",
                status="warning",
                message=f"Only {ready_count}/{total} pod(s) ready",
                details={"pods": pods_info},
            )
        else:
            return CheckResult(
                name="pod_status",
                status="critical",
                message=f"No pods ready (0/{total})",
                details={"pods": pods_info},
            )

    def check_container_restarts(self, threshold: int = 5) -> CheckResult:
        """Check container restart counts."""
        if not self.pods:
            return CheckResult(
                name="container_restarts",
                status="unknown",
                message="No pods to check",
            )

        containers_info = []
        max_restarts = 0

        for pod in self.pods:
            metadata = pod.get("metadata", {})
            status = pod.get("status", {})
            pod_name = metadata.get("name", "unknown")

            for container_status in status.get("containerStatuses", []):
                container_name = container_status.get("name", "unknown")
                restart_count = container_status.get("restartCount", 0)
                max_restarts = max(max_restarts, restart_count)

                containers_info.append({
                    "pod": pod_name,
                    "container": container_name,
                    "restarts": restart_count,
                })

        if max_restarts >= threshold * 2:
            status = "critical"
            message = f"High restart count detected: {max_restarts} restarts"
        elif max_restarts >= threshold:
            status = "warning"
            message = f"Elevated restart count: {max_restarts} restarts"
        else:
            status = "ok"
            message = f"Container restarts within limits ({max_restarts} < {threshold})"

        return CheckResult(
            name="container_restarts",
            status=status,
            message=message,
            details={
                "containers": containers_info,
                "max_restarts": max_restarts,
                "threshold": threshold,
            },
        )

    def check_image_pull(self) -> CheckResult:
        """Check for image pull errors (ImagePullBackOff, ErrImagePull, etc.)."""
        if not self.pods:
            return CheckResult(
                name="image_pull",
                status="unknown",
                message="No pods to check",
            )

        image_issues = []

        for pod in self.pods:
            metadata = pod.get("metadata", {})
            status = pod.get("status", {})
            pod_name = metadata.get("name", "unknown")

            # Check containerStatuses for waiting state with image issues
            for container_status in status.get("containerStatuses", []):
                container_name = container_status.get("name", "unknown")
                waiting = container_status.get("state", {}).get("waiting", {})
                reason = waiting.get("reason", "")

                if reason in ("ImagePullBackOff", "ErrImagePull", "InvalidImageName"):
                    image_issues.append({
                        "pod": pod_name,
                        "container": container_name,
                        "reason": reason,
                        "message": waiting.get("message", ""),
                        "image": container_status.get("image", "unknown"),
                    })

            # Also check initContainerStatuses
            for container_status in status.get("initContainerStatuses", []):
                container_name = container_status.get("name", "unknown")
                waiting = container_status.get("state", {}).get("waiting", {})
                reason = waiting.get("reason", "")

                if reason in ("ImagePullBackOff", "ErrImagePull", "InvalidImageName"):
                    image_issues.append({
                        "pod": pod_name,
                        "container": f"init:{container_name}",
                        "reason": reason,
                        "message": waiting.get("message", ""),
                        "image": container_status.get("image", "unknown"),
                    })

        if image_issues:
            return CheckResult(
                name="image_pull",
                status="critical",
                message=f"Image pull errors detected on {len(image_issues)} container(s)",
                details={
                    "issues": image_issues,
                    "common_causes": [
                        "Image tag doesn't exist",
                        "Image cleaned by ECR lifecycle policy",
                        "Registry authentication failure",
                        "Network connectivity to registry",
                    ],
                },
            )

        return CheckResult(
            name="image_pull",
            status="ok",
            message="All images pulled successfully",
            details={"pods_checked": len(self.pods)},
        )

    def search_logs(self, pattern: str, flags: int = 0) -> list[str]:
        """Search logs for a pattern and return matching lines."""
        if not self.all_logs:
            return []
        try:
            regex = re.compile(pattern, flags)
            return regex.findall(self.all_logs)
        except re.error as e:
            logger.warning(f"Invalid regex pattern '{pattern}': {e}")
            return []

    def count_log_matches(self, pattern: str, flags: int = 0) -> int:
        """Count occurrences of a pattern in logs."""
        return len(self.search_logs(pattern, flags))

    def check_log_pattern(
        self,
        name: str,
        success_pattern: str,
        failure_pattern: Optional[str] = None,
        success_message: str = "Pattern found in logs",
        failure_message: str = "Pattern not found in logs",
        error_message: str = "Error pattern detected",
    ) -> CheckResult:
        """
        Generic log pattern check.

        Args:
            name: Check name
            success_pattern: Regex pattern indicating success
            failure_pattern: Optional regex pattern indicating failure
            success_message: Message when success pattern found
            failure_message: Message when success pattern not found
            error_message: Message when failure pattern found
        """
        if not self.all_logs:
            return CheckResult(
                name=name,
                status="unknown",
                message="No logs available for analysis",
            )

        # Check for failure pattern first
        if failure_pattern:
            failure_matches = self.search_logs(failure_pattern)
            if failure_matches:
                return CheckResult(
                    name=name,
                    status="critical",
                    message=error_message,
                    details={
                        "pattern": failure_pattern,
                        "matches": failure_matches[:5],  # Limit to 5 matches
                    },
                )

        # Check for success pattern
        success_matches = self.search_logs(success_pattern)
        if success_matches:
            return CheckResult(
                name=name,
                status="ok",
                message=success_message,
                details={
                    "pattern": success_pattern,
                    "matches_count": len(success_matches),
                },
            )

        return CheckResult(
            name=name,
            status="warning",
            message=failure_message,
            details={"pattern": success_pattern},
        )
