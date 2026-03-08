"""
Hybris Plugin for Application Component Health Checker.

Checks specific to SAP Commerce (Hybris) instances in Rubix environments.
Handles both Frontend (FO) and Backoffice (BO) pods.

Focus on:
- Server startup status
- Database connectivity (HikariCP pool)
- Indexation errors (from Hybris logs)
- CronJob/Scheduler status (BO only)
- Memory/GC issues
- General application errors

Architecture:
- Frontend pods (hybris-fo-*): handle storefront traffic
- Backoffice pods (hybris-bo-*): handle admin, scheduler, batch jobs

Label selectors:
- app=hybris (all pods)
- app.kubernetes.io/name=hybris
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


@register_plugin("hybris")
class HybrisPlugin(BasePlugin):
    """Plugin for SAP Commerce (Hybris) health checks."""

    COMPONENT_NAME = "hybris"

    # Log patterns for Hybris issues
    LOG_PATTERNS = {
        # Startup patterns
        "server_started": r"Server startup in \d+ (?:ms|seconds?)",
        "server_starting": r"Starting.*Server|Server.*starting",
        "spring_context_ok": r"Root WebApplicationContext.*initialization completed",
        "spring_context_error": r"Context initialization failed|Failed to initialize.*context",
        "hybris_started": r"(?:Hybris Platform started|hybris Platform started successfully)",
        "tomcat_started": r"org\.apache\.catalina\.startup\.(?:Catalina\.start|HostConfig\.deployDescriptor)",
        "type_system_loading": r"Loading type system",
        "type_system_loaded": r"Loaded type system.*in \d+",
        "type_system_error": r"Error loading type system|Type system initialization failed",

        # Database/HikariCP patterns
        "hikari_pool_started": r"HikariPool-\d+ - Start(?:ed|ing)?",
        "hikari_pool_stats": r"HikariPool-\d+ - Pool stats.*active=(\d+).*idle=(\d+)",
        "connection_timeout": r"Connection is not available, request timed out",
        "connection_refused": r"(?:Connection refused|Unable to acquire JDBC Connection)",
        "connection_closed": r"Connection.*closed|Lost connection to MySQL",
        "deadlock": r"Deadlock found when trying to get lock",
        "too_many_connections": r"Too many connections",
        "access_denied": r"Access denied for user",
        "jdbc_error": r"(?:JDBC.*Exception|SQLException|DataAccessException)",

        # Indexation patterns (from Hybris perspective)
        "indexation_started": r"(?:Starting full indexation|Starting indexation for|IndexOperation started)",
        "indexation_completed": r"(?:Indexation completed|Full indexation finished|IndexOperation finished)",
        "indexation_failed": r"(?:Indexation failed|Error during indexation|IndexOperation.*error)",
        "solr_connection_error": r"(?:SolrServerException|Cannot connect to Solr|Solr.*connection.*failed)",
        "index_commit": r"(?:Committing index|Index committed)",

        # CronJob/Scheduler patterns
        "scheduler_started": r"(?:Scheduler started|CronJobService started|Scheduler.*enabled)",
        "scheduler_stopped": r"(?:Scheduler stopped|CronJobService stopped|Scheduler.*disabled)",
        "job_started": r"Job \[([^\]]+)\] started|Starting job[:\s]+(\w+)",
        "job_finished": r"Job \[([^\]]+)\] finished|Job[:\s]+(\w+) completed",
        "job_failed": r"Job \[([^\]]+)\] failed|Job[:\s]+(\w+).*(?:error|failed|exception)",
        "job_aborted": r"Job \[([^\]]+)\] aborted|Aborting job",
        "cronjob_trigger": r"CronJob.*triggered|Triggering cronjob",

        # Import/Export patterns
        "import_started": r"(?:Import started|ImpEx import|Starting import)",
        "import_completed": r"(?:Import completed|ImpEx.*finished|Import finished)",
        "import_failed": r"(?:Import failed|ImpEx.*error|Import error)",
        "export_started": r"(?:Export started|Starting export)",
        "export_completed": r"(?:Export completed|Export finished)",
        "export_failed": r"(?:Export failed|Export error)",

        # Memory patterns
        "oom": r"(?:OutOfMemoryError|java\.lang\.OutOfMemoryError)",
        "gc_overhead": r"(?:GC overhead limit exceeded|GC pause exceeded)",
        "heap_space": r"(?:Java heap space|Heap space exhausted)",
        "metaspace": r"(?:Metaspace|PermGen.*space)",
        "gc_pause": r"GC pause.*(\d+)ms",

        # General errors
        "error": r"\b(?:ERROR|SEVERE)\b",
        "exception": r"(?:Exception|Throwable)(?::|$|\s)",
        "null_pointer": r"NullPointerException",
        "class_not_found": r"(?:ClassNotFoundException|NoClassDefFoundError)",
        "stack_trace": r"^\s+at\s+[\w.$]+\(",

        # Health indicators
        "ready": r"(?:Application is ready|Server is ready|Started Application)",
        "shutdown": r"(?:Shutting down|Shutdown initiated|Stopping server)",
    }

    def __init__(self, config: Optional[dict] = None):
        super().__init__(config or {})
        self.error_threshold = config.get("error_threshold", 10) if config else 10
        self.restart_threshold = config.get("restart_threshold", 3) if config else 3

    def get_checks(self) -> list:
        """Return list of checks to run for Hybris."""
        return [
            self.check_pod_status,
            self.check_container_restarts,
            self.check_image_pull,
            self.check_server_startup,
            self.check_database_connection,
            self.check_indexation_status,
            self.check_scheduler_status,
            self.check_import_status,
            self.check_memory_issues,
            self.check_general_errors,
        ]

    def _get_pod_type(self, pod: dict) -> str:
        """Determine pod type (frontend/backoffice) from name or labels."""
        name = pod.get("metadata", {}).get("name", "").lower()
        labels = pod.get("metadata", {}).get("labels", {})

        # Check labels first
        component = labels.get("app.kubernetes.io/component", "").lower()
        if component in ("frontend", "fo", "storefront"):
            return "frontend"
        if component in ("backoffice", "bo", "admin"):
            return "backoffice"

        # Check pod name
        if "-fo-" in name or "-fo" in name or "front" in name:
            return "frontend"
        if "-bo-" in name or "-bo" in name or "back" in name:
            return "backoffice"

        return "unknown"

    def _get_pod_summary(self) -> dict:
        """Get summary of pod types."""
        summary = {"frontend": 0, "backoffice": 0, "unknown": 0, "total": 0}
        for pod in self.pods:
            pod_type = self._get_pod_type(pod)
            summary[pod_type] = summary.get(pod_type, 0) + 1
            summary["total"] += 1
        return summary

    def check_server_startup(self) -> CheckResult:
        """Check if Hybris server has started successfully."""
        check_name = "server_startup"

        if not self.all_logs:
            return CheckResult(
                name=check_name,
                status="unknown",
                message="No logs available to check server startup",
            )

        # Check for startup indicators
        server_started = len(re.findall(self.LOG_PATTERNS["server_started"], self.all_logs, re.IGNORECASE))
        spring_ok = len(re.findall(self.LOG_PATTERNS["spring_context_ok"], self.all_logs, re.IGNORECASE))
        hybris_started = len(re.findall(self.LOG_PATTERNS["hybris_started"], self.all_logs, re.IGNORECASE))
        type_system_ok = len(re.findall(self.LOG_PATTERNS["type_system_loaded"], self.all_logs, re.IGNORECASE))

        # Check for startup errors
        spring_error = len(re.findall(self.LOG_PATTERNS["spring_context_error"], self.all_logs, re.IGNORECASE))
        type_system_error = len(re.findall(self.LOG_PATTERNS["type_system_error"], self.all_logs, re.IGNORECASE))

        details = {
            "server_started_indicators": server_started,
            "spring_context_ok": spring_ok,
            "hybris_started": hybris_started,
            "type_system_loaded": type_system_ok,
            "spring_errors": spring_error,
            "type_system_errors": type_system_error,
        }

        # Critical: startup errors
        if spring_error > 0 or type_system_error > 0:
            return CheckResult(
                name=check_name,
                status="critical",
                message=f"Startup errors detected (Spring: {spring_error}, TypeSystem: {type_system_error})",
                details=details,
            )

        # OK: clear startup success indicators
        if server_started > 0 or hybris_started > 0:
            return CheckResult(
                name=check_name,
                status="ok",
                message=f"Server started successfully ({server_started} startup confirmations)",
                details=details,
            )

        # OK: Spring context initialized (common pattern)
        if spring_ok > 0:
            return CheckResult(
                name=check_name,
                status="ok",
                message="Spring context initialized successfully",
                details=details,
            )

        # Warning: no clear indicators
        return CheckResult(
            name=check_name,
            status="warning",
            message="No clear startup indicators found in logs",
            details={**details, "note": "May be normal if logs are from running instance"},
        )

    def check_database_connection(self) -> CheckResult:
        """Check database connectivity via HikariCP pool status."""
        check_name = "database_connection"

        if not self.all_logs:
            return CheckResult(
                name=check_name,
                status="unknown",
                message="No logs available to check database connection",
            )

        # Check for pool startup
        pool_started = len(re.findall(self.LOG_PATTERNS["hikari_pool_started"], self.all_logs, re.IGNORECASE))

        # Check for connection issues
        conn_timeout = len(re.findall(self.LOG_PATTERNS["connection_timeout"], self.all_logs, re.IGNORECASE))
        conn_refused = len(re.findall(self.LOG_PATTERNS["connection_refused"], self.all_logs, re.IGNORECASE))
        deadlocks = len(re.findall(self.LOG_PATTERNS["deadlock"], self.all_logs, re.IGNORECASE))
        too_many = len(re.findall(self.LOG_PATTERNS["too_many_connections"], self.all_logs, re.IGNORECASE))
        access_denied = len(re.findall(self.LOG_PATTERNS["access_denied"], self.all_logs, re.IGNORECASE))
        jdbc_errors = len(re.findall(self.LOG_PATTERNS["jdbc_error"], self.all_logs, re.IGNORECASE))

        total_errors = conn_timeout + conn_refused + deadlocks + too_many + access_denied

        details = {
            "pool_started": pool_started,
            "connection_timeouts": conn_timeout,
            "connection_refused": conn_refused,
            "deadlocks": deadlocks,
            "too_many_connections": too_many,
            "access_denied": access_denied,
            "jdbc_errors": jdbc_errors,
            "total_connection_errors": total_errors,
        }

        # Critical: authentication or connection failures
        if access_denied > 0:
            return CheckResult(
                name=check_name,
                status="critical",
                message=f"Database access denied ({access_denied} occurrences)",
                details=details,
            )

        if conn_refused > 0:
            return CheckResult(
                name=check_name,
                status="critical",
                message=f"Database connection refused ({conn_refused} occurrences)",
                details=details,
            )

        if too_many > 0:
            return CheckResult(
                name=check_name,
                status="critical",
                message=f"Too many database connections ({too_many} occurrences)",
                details=details,
            )

        # Warning: connection pool issues
        if conn_timeout > 0:
            return CheckResult(
                name=check_name,
                status="warning",
                message=f"Connection pool timeouts detected ({conn_timeout} occurrences)",
                details=details,
            )

        if deadlocks > 0:
            return CheckResult(
                name=check_name,
                status="warning",
                message=f"Database deadlocks detected ({deadlocks} occurrences)",
                details=details,
            )

        # OK: pool started and no errors
        if pool_started > 0 and total_errors == 0:
            return CheckResult(
                name=check_name,
                status="ok",
                message=f"Database connection pool healthy ({pool_started} pool(s) started)",
                details=details,
            )

        # Unknown: no pool info in logs
        if pool_started == 0:
            return CheckResult(
                name=check_name,
                status="unknown",
                message="No HikariCP pool information in logs",
                details={**details, "note": "Pool may be started before log window"},
            )

        return CheckResult(
            name=check_name,
            status="ok",
            message="No database connection issues detected",
            details=details,
        )

    def check_indexation_status(self) -> CheckResult:
        """Check Solr indexation status from Hybris perspective."""
        check_name = "indexation_status"

        if not self.all_logs:
            return CheckResult(
                name=check_name,
                status="unknown",
                message="No logs available to check indexation status",
            )

        # Check indexation patterns
        idx_started = len(re.findall(self.LOG_PATTERNS["indexation_started"], self.all_logs, re.IGNORECASE))
        idx_completed = len(re.findall(self.LOG_PATTERNS["indexation_completed"], self.all_logs, re.IGNORECASE))
        idx_failed = len(re.findall(self.LOG_PATTERNS["indexation_failed"], self.all_logs, re.IGNORECASE))
        solr_errors = len(re.findall(self.LOG_PATTERNS["solr_connection_error"], self.all_logs, re.IGNORECASE))

        details = {
            "indexation_started": idx_started,
            "indexation_completed": idx_completed,
            "indexation_failed": idx_failed,
            "solr_connection_errors": solr_errors,
        }

        # Critical: Solr connection errors
        if solr_errors > 0:
            return CheckResult(
                name=check_name,
                status="critical",
                message=f"Solr connection errors detected ({solr_errors} occurrences)",
                details=details,
            )

        # Critical/Warning: indexation failures
        if idx_failed > 0:
            status = "critical" if idx_failed > idx_completed else "warning"
            return CheckResult(
                name=check_name,
                status=status,
                message=f"Indexation failures detected ({idx_failed} failed, {idx_completed} completed)",
                details=details,
            )

        # OK: indexations running successfully
        if idx_completed > 0:
            return CheckResult(
                name=check_name,
                status="ok",
                message=f"Indexation healthy ({idx_completed} completed, {idx_started} started)",
                details=details,
            )

        # Unknown: no indexation activity
        return CheckResult(
            name=check_name,
            status="ok",
            message="No indexation activity in logs",
            details={**details, "note": "No indexation jobs ran during log window"},
        )

    def check_scheduler_status(self) -> CheckResult:
        """Check CronJob/Scheduler status (primarily relevant for BO pods)."""
        check_name = "scheduler_status"

        if not self.all_logs:
            return CheckResult(
                name=check_name,
                status="unknown",
                message="No logs available to check scheduler status",
            )

        # Pod type info
        pod_summary = self._get_pod_summary()

        # Check scheduler patterns
        sched_started = len(re.findall(self.LOG_PATTERNS["scheduler_started"], self.all_logs, re.IGNORECASE))
        sched_stopped = len(re.findall(self.LOG_PATTERNS["scheduler_stopped"], self.all_logs, re.IGNORECASE))

        # Check job patterns
        jobs_started = len(re.findall(self.LOG_PATTERNS["job_started"], self.all_logs, re.IGNORECASE))
        jobs_finished = len(re.findall(self.LOG_PATTERNS["job_finished"], self.all_logs, re.IGNORECASE))
        jobs_failed = len(re.findall(self.LOG_PATTERNS["job_failed"], self.all_logs, re.IGNORECASE))
        jobs_aborted = len(re.findall(self.LOG_PATTERNS["job_aborted"], self.all_logs, re.IGNORECASE))

        total_job_errors = jobs_failed + jobs_aborted

        details = {
            "pod_types": pod_summary,
            "scheduler_started": sched_started,
            "scheduler_stopped": sched_stopped,
            "jobs_started": jobs_started,
            "jobs_finished": jobs_finished,
            "jobs_failed": jobs_failed,
            "jobs_aborted": jobs_aborted,
        }

        # Warning: job failures
        if total_job_errors > 0:
            return CheckResult(
                name=check_name,
                status="warning",
                message=f"Job failures detected ({jobs_failed} failed, {jobs_aborted} aborted)",
                details=details,
            )

        # OK: scheduler running, jobs executing
        if sched_started > 0 or jobs_started > 0:
            return CheckResult(
                name=check_name,
                status="ok",
                message=f"Scheduler healthy ({jobs_finished} jobs completed, {total_job_errors} errors)",
                details=details,
            )

        # Info: no scheduler activity (might be FO pods)
        if pod_summary.get("frontend", 0) > 0 and pod_summary.get("backoffice", 0) == 0:
            return CheckResult(
                name=check_name,
                status="ok",
                message="Frontend pods only - scheduler runs on backoffice",
                details=details,
            )

        return CheckResult(
            name=check_name,
            status="ok",
            message="No scheduler activity in logs",
            details={**details, "note": "Scheduler may be disabled or no jobs during log window"},
        )

    def check_import_status(self) -> CheckResult:
        """Check ImpEx import/export status."""
        check_name = "import_status"

        if not self.all_logs:
            return CheckResult(
                name=check_name,
                status="unknown",
                message="No logs available to check import status",
            )

        # Check import patterns
        import_started = len(re.findall(self.LOG_PATTERNS["import_started"], self.all_logs, re.IGNORECASE))
        import_completed = len(re.findall(self.LOG_PATTERNS["import_completed"], self.all_logs, re.IGNORECASE))
        import_failed = len(re.findall(self.LOG_PATTERNS["import_failed"], self.all_logs, re.IGNORECASE))

        # Check export patterns
        export_started = len(re.findall(self.LOG_PATTERNS["export_started"], self.all_logs, re.IGNORECASE))
        export_completed = len(re.findall(self.LOG_PATTERNS["export_completed"], self.all_logs, re.IGNORECASE))
        export_failed = len(re.findall(self.LOG_PATTERNS["export_failed"], self.all_logs, re.IGNORECASE))

        total_operations = import_started + export_started
        total_failures = import_failed + export_failed

        details = {
            "import_started": import_started,
            "import_completed": import_completed,
            "import_failed": import_failed,
            "export_started": export_started,
            "export_completed": export_completed,
            "export_failed": export_failed,
        }

        # Warning/Critical: failures
        if total_failures > 0:
            status = "critical" if total_failures > (import_completed + export_completed) else "warning"
            return CheckResult(
                name=check_name,
                status=status,
                message=f"Import/Export failures ({import_failed} import, {export_failed} export)",
                details=details,
            )

        # OK: operations successful
        if total_operations > 0:
            return CheckResult(
                name=check_name,
                status="ok",
                message=f"Import/Export healthy ({import_completed} imports, {export_completed} exports completed)",
                details=details,
            )

        return CheckResult(
            name=check_name,
            status="ok",
            message="No import/export activity in logs",
            details=details,
        )

    def check_memory_issues(self) -> CheckResult:
        """Check for memory and GC issues."""
        check_name = "memory_issues"

        if not self.all_logs:
            return CheckResult(
                name=check_name,
                status="unknown",
                message="No logs available to check memory issues",
            )

        oom_errors = len(re.findall(self.LOG_PATTERNS["oom"], self.all_logs, re.IGNORECASE))
        gc_overhead = len(re.findall(self.LOG_PATTERNS["gc_overhead"], self.all_logs, re.IGNORECASE))
        heap_space = len(re.findall(self.LOG_PATTERNS["heap_space"], self.all_logs, re.IGNORECASE))
        metaspace = len(re.findall(self.LOG_PATTERNS["metaspace"], self.all_logs, re.IGNORECASE))

        # Check for long GC pauses (> 1000ms)
        gc_pauses = re.findall(self.LOG_PATTERNS["gc_pause"], self.all_logs, re.IGNORECASE)
        long_gc_pauses = [int(p) for p in gc_pauses if int(p) > 1000]

        details = {
            "oom_errors": oom_errors,
            "gc_overhead": gc_overhead,
            "heap_space_errors": heap_space,
            "metaspace_errors": metaspace,
            "long_gc_pauses": len(long_gc_pauses),
            "max_gc_pause_ms": max(long_gc_pauses) if long_gc_pauses else 0,
        }

        # Critical: OOM or GC overhead
        if oom_errors > 0:
            return CheckResult(
                name=check_name,
                status="critical",
                message=f"OutOfMemoryError detected ({oom_errors} occurrences)",
                details=details,
            )

        if gc_overhead > 0:
            return CheckResult(
                name=check_name,
                status="critical",
                message=f"GC overhead limit exceeded ({gc_overhead} occurrences)",
                details=details,
            )

        if heap_space > 0 or metaspace > 0:
            return CheckResult(
                name=check_name,
                status="critical",
                message=f"Memory space exhausted (heap: {heap_space}, metaspace: {metaspace})",
                details=details,
            )

        # Warning: long GC pauses
        if long_gc_pauses:
            return CheckResult(
                name=check_name,
                status="warning",
                message=f"Long GC pauses detected ({len(long_gc_pauses)} pauses > 1s, max: {max(long_gc_pauses)}ms)",
                details=details,
            )

        return CheckResult(
            name=check_name,
            status="ok",
            message="No memory issues detected",
            details=details,
        )

    def check_general_errors(self) -> CheckResult:
        """Check for general ERROR/Exception messages in logs."""
        check_name = "general_errors"

        if not self.all_logs:
            return CheckResult(
                name=check_name,
                status="unknown",
                message="No logs available to check for errors",
            )

        # Count error types
        error_lines = re.findall(r'^.*\b(?:ERROR|SEVERE)\b.*$', self.all_logs, re.MULTILINE | re.IGNORECASE)
        exception_lines = re.findall(r'^.*(?:Exception|Throwable)(?::|$|\s).*$', self.all_logs, re.MULTILINE)
        npe_count = len(re.findall(self.LOG_PATTERNS["null_pointer"], self.all_logs))
        cnf_count = len(re.findall(self.LOG_PATTERNS["class_not_found"], self.all_logs))

        # Deduplicate for samples
        unique_errors = list(set(error_lines))
        error_count = len(error_lines)
        exception_count = len(exception_lines)

        details = {
            "error_count": error_count,
            "exception_count": exception_count,
            "null_pointer_exceptions": npe_count,
            "class_not_found_errors": cnf_count,
            "sample_errors": [e[:200] for e in unique_errors[:5]],
        }

        # Critical: class loading issues (usually fatal)
        if cnf_count > 0:
            return CheckResult(
                name=check_name,
                status="critical",
                message=f"Class loading errors detected ({cnf_count} ClassNotFound)",
                details=details,
            )

        # Determine status based on error count
        if error_count == 0 and exception_count == 0:
            return CheckResult(
                name=check_name,
                status="ok",
                message="No errors or exceptions in logs",
                details=details,
            )

        if error_count >= self.error_threshold * 2:
            return CheckResult(
                name=check_name,
                status="critical",
                message=f"High error count: {error_count} errors (threshold: {self.error_threshold})",
                details=details,
            )

        if error_count >= self.error_threshold:
            return CheckResult(
                name=check_name,
                status="warning",
                message=f"Errors detected: {error_count} errors, {exception_count} exceptions",
                details=details,
            )

        return CheckResult(
            name=check_name,
            status="ok",
            message=f"Low error count: {error_count} (below threshold {self.error_threshold})",
            details=details,
        )
