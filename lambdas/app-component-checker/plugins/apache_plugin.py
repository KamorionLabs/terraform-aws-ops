"""
Apache Plugin for Application Component Health Checker.

Checks specific to Apache reverse proxy instances in Rubix environments.
Focus on:
- Media/assets path configuration and accessibility
- Backend (Hybris) connectivity
- HTTP error rates (404, 502, 503)
- Configuration errors
"""

import re
import sys
import os
from datetime import datetime, timezone
from typing import Optional

# Add parent directory to import from app_component_checker
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from app_component_checker import register_plugin
from plugins.base_plugin import BasePlugin, CheckResult


@register_plugin("apache")
class ApachePlugin(BasePlugin):
    """Plugin for Apache reverse proxy health checks."""

    # Log patterns for Apache issues
    LOG_PATTERNS = {
        # HTTP errors
        "http_404": r'" 404 ',
        "http_502": r'" 502 ',
        "http_503": r'" 503 ',
        "http_500": r'" 500 ',

        # Media/assets paths - detect 404s on these
        "media_path": r'(?:GET|HEAD) (?:/medias/|/_ui/|/assets/)',
        "media_404": r'(?:GET|HEAD) (?:/medias/|/_ui/|/assets/)[^\s]+ HTTP/[0-9.]+" 404',

        # Proxy/upstream errors
        "proxy_error": r'AH0111[0-9]',  # AH01114, AH01110, etc.
        "upstream_timeout": r'(?:upstream timed out|proxy_connect|Connection timed out)',
        "backend_refused": r'(?:Connection refused|proxy:error)',

        # Configuration errors
        "config_error": r'(?:AH00526|AH00016|Syntax error)',
        "ssl_error": r'(?:AH00898|SSL|certificate)',
        "permission_denied": r'Permission denied',

        # Health check (to filter out noise)
        "health_check": r'(?:ELB-HealthChecker|/server-status|/health)',
    }

    # Environment variables to check for media config
    MEDIA_ENV_VARS = [
        "SHARED_DATA_MEDIA_EFS_ID",
        "SHARED_DATA_EFS_ID",
        "SHARED_DATA_MEDIA_SUB_PATH",
        "SHARED_DATA_SUB_PATH",
    ]

    def __init__(self, config: Optional[dict] = None):
        super().__init__(config or {})
        self.error_threshold = config.get("error_threshold", 10) if config else 10
        self.media_404_threshold = config.get("media_404_threshold", 5) if config else 5

    def get_checks(self) -> list:
        """Return list of checks to run for Apache."""
        return [
            self.check_pod_status,
            self.check_container_restarts,
            self.check_image_pull,
            self.check_media_config,
            self.check_media_accessibility,
            self.check_http_errors,
            self.check_upstream_health,
            self.check_config_errors,
        ]

    def check_media_config(self) -> CheckResult:
        """Check if media/EFS configuration is present in pod env vars."""
        check_name = "media_config"
        pods = self.pods

        if not pods:
            return CheckResult(
                name=check_name,
                status="unknown",
                message="No pods to check media configuration",
                details={},
            ).to_dict()

        issues = []
        pod_configs = []

        for pod in pods:
            pod_name = pod.get("metadata", {}).get("name", "unknown")
            containers = pod.get("spec", {}).get("containers", [])

            for container in containers:
                if container.get("name") == "apache":
                    env_vars = {
                        e.get("name"): self._get_env_value(e)
                        for e in container.get("env", [])
                    }

                    # Check for required env vars
                    missing = []
                    present = {}
                    for var in self.MEDIA_ENV_VARS:
                        if var in env_vars:
                            present[var] = env_vars[var]
                        else:
                            missing.append(var)

                    pod_configs.append({
                        "pod": pod_name,
                        "present": present,
                        "missing": missing,
                    })

                    if missing:
                        issues.append(f"{pod_name}: missing {', '.join(missing)}")

        if not pod_configs:
            return CheckResult(
                name=check_name,
                status="unknown",
                message="No Apache containers found in pods",
                details={},
            ).to_dict()

        if issues:
            return CheckResult(
                name=check_name,
                status="warning",
                message=f"Media config incomplete: {len(issues)} issue(s)",
                details={"issues": issues, "configs": pod_configs},
            ).to_dict()

        # Check for suspicious sub-path values (like backup restore paths)
        warnings = []
        for pc in pod_configs:
            for var, value in pc.get("present", {}).items():
                if "SUB_PATH" in var and value:
                    if "backup" in value.lower() or "restore" in value.lower():
                        warnings.append(f"{pc['pod']}: {var} appears to be a backup path: {value}")

        if warnings:
            return CheckResult(
                name=check_name,
                status="warning",
                message="Media sub-path may be misconfigured (backup/restore path detected)",
                details={"warnings": warnings, "configs": pod_configs},
            ).to_dict()

        return CheckResult(
            name=check_name,
            status="ok",
            message="Media configuration present in all pods",
            details={"configs": pod_configs},
        ).to_dict()

    def _get_env_value(self, env_entry: dict) -> str:
        """Extract environment variable value from pod spec."""
        if "value" in env_entry:
            return env_entry["value"]
        elif "valueFrom" in env_entry:
            vf = env_entry["valueFrom"]
            if "secretKeyRef" in vf:
                return f"<secret:{vf['secretKeyRef'].get('name')}/{vf['secretKeyRef'].get('key')}>"
            elif "configMapKeyRef" in vf:
                return f"<configmap:{vf['configMapKeyRef'].get('name')}/{vf['configMapKeyRef'].get('key')}>"
        return "<unknown>"

    def check_media_accessibility(self) -> CheckResult:
        """Check for 404 errors on media/assets paths."""
        check_name = "media_accessibility"
        logs = self.logs

        if not logs:
            return CheckResult(
                name=check_name,
                status="unknown",
                message="No logs available to check media accessibility",
                details={},
            ).to_dict()

        media_404_count = 0
        media_404_samples = []
        total_media_requests = 0

        for pod_name, log_content in logs.items():
            if not log_content:
                continue

            lines = log_content.split("\n") if isinstance(log_content, str) else []

            for line in lines:
                # Skip health checks
                if re.search(self.LOG_PATTERNS["health_check"], line, re.IGNORECASE):
                    continue

                # Check for media requests
                if re.search(self.LOG_PATTERNS["media_path"], line):
                    total_media_requests += 1

                    # Check if it's a 404
                    if re.search(self.LOG_PATTERNS["media_404"], line):
                        media_404_count += 1
                        if len(media_404_samples) < 5:
                            # Extract the path
                            match = re.search(r'((?:GET|HEAD) [^\s]+)', line)
                            if match:
                                media_404_samples.append({
                                    "pod": pod_name,
                                    "request": match.group(1)[:100],
                                })

        details = {
            "total_media_requests": total_media_requests,
            "media_404_count": media_404_count,
            "samples": media_404_samples,
        }

        if media_404_count == 0:
            return CheckResult(
                name=check_name,
                status="ok",
                message="No 404 errors on media paths detected",
                details=details,
            ).to_dict()

        if media_404_count >= self.media_404_threshold:
            return CheckResult(
                name=check_name,
                status="critical",
                message=f"High number of media 404 errors: {media_404_count} (threshold: {self.media_404_threshold})",
                details=details,
            ).to_dict()

        return CheckResult(
            name=check_name,
            status="warning",
            message=f"Some media 404 errors detected: {media_404_count}",
            details=details,
        ).to_dict()

    def check_http_errors(self) -> CheckResult:
        """Check for HTTP error rates (500, 502, 503)."""
        check_name = "http_errors"
        logs = self.logs

        if not logs:
            return CheckResult(
                name=check_name,
                status="unknown",
                message="No logs available for HTTP error analysis",
                details={},
            ).to_dict()

        error_counts = {"500": 0, "502": 0, "503": 0, "404": 0}
        total_requests = 0

        for pod_name, log_content in logs.items():
            if not log_content:
                continue

            lines = log_content.split("\n") if isinstance(log_content, str) else []

            for line in lines:
                # Skip health checks for error analysis
                if re.search(self.LOG_PATTERNS["health_check"], line, re.IGNORECASE):
                    continue

                # Count as a request if it looks like an access log line
                if '" ' in line and ' HTTP/' in line:
                    total_requests += 1

                    if re.search(self.LOG_PATTERNS["http_500"], line):
                        error_counts["500"] += 1
                    elif re.search(self.LOG_PATTERNS["http_502"], line):
                        error_counts["502"] += 1
                    elif re.search(self.LOG_PATTERNS["http_503"], line):
                        error_counts["503"] += 1
                    elif re.search(self.LOG_PATTERNS["http_404"], line):
                        error_counts["404"] += 1

        total_errors = sum(error_counts.values())
        server_errors = error_counts["500"] + error_counts["502"] + error_counts["503"]

        details = {
            "total_requests": total_requests,
            "error_counts": error_counts,
            "total_errors": total_errors,
            "server_errors": server_errors,
        }

        if server_errors == 0:
            return CheckResult(
                name=check_name,
                status="ok",
                message="No server errors (5xx) detected",
                details=details,
            ).to_dict()

        if server_errors >= self.error_threshold:
            return CheckResult(
                name=check_name,
                status="critical",
                message=f"High server error rate: {server_errors} errors (502: {error_counts['502']}, 503: {error_counts['503']})",
                details=details,
            ).to_dict()

        return CheckResult(
            name=check_name,
            status="warning",
            message=f"Some server errors detected: {server_errors} (502: {error_counts['502']}, 503: {error_counts['503']})",
            details=details,
        ).to_dict()

    def check_upstream_health(self) -> CheckResult:
        """Check for upstream/proxy errors indicating backend issues."""
        check_name = "upstream_health"
        logs = self.logs

        if not logs:
            return CheckResult(
                name=check_name,
                status="unknown",
                message="No logs available for upstream health analysis",
                details={},
            ).to_dict()

        proxy_errors = 0
        timeout_errors = 0
        connection_refused = 0
        error_samples = []

        for pod_name, log_content in logs.items():
            if not log_content:
                continue

            lines = log_content.split("\n") if isinstance(log_content, str) else []

            for line in lines:
                if re.search(self.LOG_PATTERNS["proxy_error"], line, re.IGNORECASE):
                    proxy_errors += 1
                    if len(error_samples) < 3:
                        error_samples.append({"pod": pod_name, "type": "proxy_error", "line": line[:150]})

                if re.search(self.LOG_PATTERNS["upstream_timeout"], line, re.IGNORECASE):
                    timeout_errors += 1
                    if len(error_samples) < 3:
                        error_samples.append({"pod": pod_name, "type": "timeout", "line": line[:150]})

                if re.search(self.LOG_PATTERNS["backend_refused"], line, re.IGNORECASE):
                    connection_refused += 1
                    if len(error_samples) < 3:
                        error_samples.append({"pod": pod_name, "type": "connection_refused", "line": line[:150]})

        total_upstream_errors = proxy_errors + timeout_errors + connection_refused

        details = {
            "proxy_errors": proxy_errors,
            "timeout_errors": timeout_errors,
            "connection_refused": connection_refused,
            "total": total_upstream_errors,
            "samples": error_samples,
        }

        if total_upstream_errors == 0:
            return CheckResult(
                name=check_name,
                status="ok",
                message="No upstream/backend errors detected",
                details=details,
            ).to_dict()

        if connection_refused > 0 or timeout_errors > 3:
            return CheckResult(
                name=check_name,
                status="critical",
                message=f"Backend connectivity issues: {connection_refused} refused, {timeout_errors} timeouts",
                details=details,
            ).to_dict()

        return CheckResult(
            name=check_name,
            status="warning",
            message=f"Some upstream errors detected: {total_upstream_errors}",
            details=details,
        ).to_dict()

    def check_config_errors(self) -> CheckResult:
        """Check for Apache configuration or SSL errors."""
        check_name = "config_errors"
        logs = self.logs

        if not logs:
            return CheckResult(
                name=check_name,
                status="unknown",
                message="No logs available for config error analysis",
                details={},
            ).to_dict()

        config_errors = 0
        ssl_errors = 0
        permission_errors = 0
        error_samples = []

        for pod_name, log_content in logs.items():
            if not log_content:
                continue

            lines = log_content.split("\n") if isinstance(log_content, str) else []

            for line in lines:
                if re.search(self.LOG_PATTERNS["config_error"], line, re.IGNORECASE):
                    config_errors += 1
                    if len(error_samples) < 3:
                        error_samples.append({"pod": pod_name, "type": "config", "line": line[:150]})

                if re.search(self.LOG_PATTERNS["ssl_error"], line, re.IGNORECASE):
                    ssl_errors += 1
                    if len(error_samples) < 3:
                        error_samples.append({"pod": pod_name, "type": "ssl", "line": line[:150]})

                if re.search(self.LOG_PATTERNS["permission_denied"], line, re.IGNORECASE):
                    permission_errors += 1
                    if len(error_samples) < 3:
                        error_samples.append({"pod": pod_name, "type": "permission", "line": line[:150]})

        total_config_issues = config_errors + ssl_errors + permission_errors

        details = {
            "config_errors": config_errors,
            "ssl_errors": ssl_errors,
            "permission_errors": permission_errors,
            "total": total_config_issues,
            "samples": error_samples,
        }

        if total_config_issues == 0:
            return CheckResult(
                name=check_name,
                status="ok",
                message="No configuration or SSL errors detected",
                details=details,
            ).to_dict()

        if config_errors > 0 or ssl_errors > 0:
            return CheckResult(
                name=check_name,
                status="critical",
                message=f"Configuration issues: {config_errors} config errors, {ssl_errors} SSL errors",
                details=details,
            ).to_dict()

        return CheckResult(
            name=check_name,
            status="warning",
            message=f"Some issues detected: {permission_errors} permission errors",
            details=details,
        ).to_dict()
