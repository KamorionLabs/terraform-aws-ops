"""
SMUI (Search Management UI) Plugin
===================================
Health checks for SMUI application instances.

Based on real incident patterns observed:
- JDBC connection issues (missing jdbcUrl in secrets)
- Database access denied errors
- HikariPool startup failures
- Application startup failures

SMUI Configuration Per Environment:
- FR:   jdbc:mysql://fr-rds-writer-prod.iph.nbs-aws.com/prod_smui_fr
- IT:   jdbc:mysql://it-rds-writer-prod.iph.nbs-aws.com/smui_it
- BENE: jdbc:mysql://bene-rds-writer-prod.iph.nbs-aws.com/smui_nl
- MI1:  jdbc:mysql://mi1-rds-writer-prod.iph.nbs-aws.com/smui_mi1

Checks implemented:
- pod_status: Pod running and ready
- container_restarts: Restart count within threshold
- jdbc_connection: HikariPool-mysql Start completed
- jdbc_url_valid: No "jdbcUrl, null" in logs
- database_access: No "Access denied" in logs
- application_startup: Application started successfully
- application_errors: Error/Exception count in logs
"""

import re
import sys
import os

# Add parent directory to import from app_component_checker
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from app_component_checker import register_plugin
from plugins.base_plugin import BasePlugin, CheckResult


@register_plugin("smui")
class SMUIPlugin(BasePlugin):
    """Plugin for SMUI health checks."""

    COMPONENT_NAME = "smui"

    # Log patterns based on real SMUI logs
    LOG_PATTERNS = {
        # Success patterns
        "hikari_start_completed": r"HikariPool-mysql\s*-\s*Start completed",
        "application_started": r"Application started",
        "play_started": r"p\.c\.s\.AkkaHttpServer\s*-\s*Listening for HTTP",

        # Failure patterns - JDBC
        "jdbc_url_null": r"jdbcUrl,\s*null",
        "access_denied": r"Access denied for user\s*['\"]?([^'\"@]+)['\"]?",
        "connection_refused": r"Connection refused|Communications link failure",
        "hikari_exception": r"HikariPool-mysql.*Exception",

        # Failure patterns - Application
        "migration_error": r"MigrationService.*[Ee]xception",
        "startup_exception": r"Application startup exception|play\.api\.UnexpectedException",
        "fatal_error": r"FATAL|fatal error",

        # Warning patterns
        "connection_timeout": r"HikariPool-mysql.*Connection is not available.*timeout",
        "slow_query": r"slow query|long query",
    }

    def get_checks(self) -> list:
        """Return list of checks to run for SMUI."""
        return [
            self.check_pod_status,
            self.check_container_restarts,
            self.check_image_pull,
            self.check_jdbc_connection,
            self.check_jdbc_url_valid,
            self.check_database_access,
            self.check_application_startup,
            self.check_application_errors,
            self.check_hikari_pool_health,
        ]

    def check_jdbc_connection(self) -> CheckResult:
        """Check if JDBC connection was established successfully."""
        if not self.all_logs:
            return CheckResult(
                name="jdbc_connection",
                status="unknown",
                message="No logs available to verify JDBC connection",
            )

        # Check for HikariPool startup success
        success_matches = self.search_logs(self.LOG_PATTERNS["hikari_start_completed"])
        if success_matches:
            return CheckResult(
                name="jdbc_connection",
                status="ok",
                message="JDBC connection pool started successfully",
                details={
                    "hikari_status": "Start completed",
                    "matches": len(success_matches),
                },
            )

        # Check for exceptions
        exception_matches = self.search_logs(self.LOG_PATTERNS["hikari_exception"])
        if exception_matches:
            return CheckResult(
                name="jdbc_connection",
                status="critical",
                message="HikariPool encountered exception during startup",
                details={
                    "exceptions": exception_matches[:3],
                },
            )

        return CheckResult(
            name="jdbc_connection",
            status="warning",
            message="JDBC connection status could not be determined",
            details={"note": "HikariPool start message not found in logs"},
        )

    def check_jdbc_url_valid(self) -> CheckResult:
        """Check that jdbcUrl is properly configured (not null)."""
        if not self.all_logs:
            return CheckResult(
                name="jdbc_url_valid",
                status="unknown",
                message="No logs available to verify JDBC URL",
            )

        # This is the critical pattern from the incident
        null_url_matches = self.search_logs(self.LOG_PATTERNS["jdbc_url_null"])
        if null_url_matches:
            return CheckResult(
                name="jdbc_url_valid",
                status="critical",
                message="JDBC URL is null - secret missing jdbcUrl field",
                details={
                    "error_pattern": "jdbcUrl, null",
                    "probable_cause": "Secret in Secrets Manager is missing jdbcUrl key",
                    "fix_hint": "Add jdbcUrl to the secret with format: jdbc:mysql://host/database?params",
                },
            )

        return CheckResult(
            name="jdbc_url_valid",
            status="ok",
            message="JDBC URL configuration appears valid",
        )

    def check_database_access(self) -> CheckResult:
        """Check for database access denied errors."""
        if not self.all_logs:
            return CheckResult(
                name="database_access",
                status="unknown",
                message="No logs available to verify database access",
            )

        # Check for access denied errors
        access_denied_matches = self.search_logs(self.LOG_PATTERNS["access_denied"])
        if access_denied_matches:
            # Try to extract username
            user_match = re.search(
                r"Access denied for user\s*['\"]?([^'\"@]+)['\"]?",
                self.all_logs,
            )
            denied_user = user_match.group(1) if user_match else "unknown"

            return CheckResult(
                name="database_access",
                status="critical",
                message=f"Database access denied for user '{denied_user}'",
                details={
                    "denied_user": denied_user,
                    "probable_causes": [
                        "Wrong database name in jdbcUrl",
                        "User not granted access to database",
                        "Incorrect password in secret",
                    ],
                },
            )

        # Check for connection refused
        refused_matches = self.search_logs(self.LOG_PATTERNS["connection_refused"])
        if refused_matches:
            return CheckResult(
                name="database_access",
                status="critical",
                message="Database connection refused",
                details={
                    "error": "Connection refused or communications link failure",
                    "probable_causes": [
                        "Wrong host in jdbcUrl",
                        "Database not accessible from pod network",
                        "Database is down",
                    ],
                },
            )

        return CheckResult(
            name="database_access",
            status="ok",
            message="No database access errors detected",
        )

    def check_application_startup(self) -> CheckResult:
        """Check if application started successfully."""
        if not self.all_logs:
            return CheckResult(
                name="application_startup",
                status="unknown",
                message="No logs available to verify application startup",
            )

        # Check for startup exceptions first
        startup_exceptions = self.search_logs(self.LOG_PATTERNS["startup_exception"])
        if startup_exceptions:
            return CheckResult(
                name="application_startup",
                status="critical",
                message="Application startup failed with exception",
                details={"exceptions": startup_exceptions[:3]},
            )

        # Check for fatal errors
        fatal_matches = self.search_logs(self.LOG_PATTERNS["fatal_error"])
        if fatal_matches:
            return CheckResult(
                name="application_startup",
                status="critical",
                message="Fatal error during startup",
                details={"fatal_errors": fatal_matches[:3]},
            )

        # Check for successful startup indicators
        app_started = self.search_logs(self.LOG_PATTERNS["application_started"])
        play_started = self.search_logs(self.LOG_PATTERNS["play_started"])

        if app_started or play_started:
            return CheckResult(
                name="application_startup",
                status="ok",
                message="Application started successfully",
                details={
                    "application_started": bool(app_started),
                    "play_listening": bool(play_started),
                },
            )

        return CheckResult(
            name="application_startup",
            status="warning",
            message="Application startup status uncertain",
            details={"note": "No startup confirmation found in logs"},
        )

    def check_application_errors(self) -> CheckResult:
        """Check for errors and exceptions in application logs."""
        if not self.all_logs:
            return CheckResult(
                name="application_errors",
                status="unknown",
                message="No logs available for error analysis",
            )

        # Count errors and exceptions (excluding expected startup messages)
        error_pattern = r"\b(?:ERROR|Exception|SEVERE)\b"
        error_matches = self.search_logs(error_pattern, re.IGNORECASE)

        # Filter out common non-critical messages
        filtered_errors = [
            e for e in error_matches
            if "Exception.class" not in e  # Exclude class references
            and "NoClassDefFoundError" not in e  # Usually harmless in Play
        ]

        error_count = len(filtered_errors)

        if error_count == 0:
            return CheckResult(
                name="application_errors",
                status="ok",
                message="No errors detected in logs",
                details={"error_count": 0},
            )
        elif error_count <= 5:
            return CheckResult(
                name="application_errors",
                status="warning",
                message=f"Found {error_count} error(s) in logs",
                details={
                    "error_count": error_count,
                    "sample_errors": filtered_errors[:5],
                },
            )
        else:
            return CheckResult(
                name="application_errors",
                status="critical" if error_count > 20 else "warning",
                message=f"High error count: {error_count} errors in logs",
                details={
                    "error_count": error_count,
                    "sample_errors": filtered_errors[:10],
                },
            )

    def check_hikari_pool_health(self) -> CheckResult:
        """Check HikariPool health (connection timeouts, pool exhaustion)."""
        if not self.all_logs:
            return CheckResult(
                name="hikari_pool_health",
                status="unknown",
                message="No logs available for HikariPool analysis",
            )

        # Check for connection timeout warnings
        timeout_matches = self.search_logs(self.LOG_PATTERNS["connection_timeout"])
        if timeout_matches:
            return CheckResult(
                name="hikari_pool_health",
                status="warning",
                message="HikariPool connection timeout detected",
                details={
                    "issue": "Connection pool may be exhausted",
                    "occurrences": len(timeout_matches),
                    "recommendation": "Check pool size configuration or database load",
                },
            )

        return CheckResult(
            name="hikari_pool_health",
            status="ok",
            message="HikariPool health appears normal",
        )
