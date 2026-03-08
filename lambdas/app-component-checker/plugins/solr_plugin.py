"""
Solr Plugin for Application Component Health Checker.

Checks specific to Solr search instances in Rubix environments.
Handles both Leader and Follower pods.

Focus on:
- Replication sync status (follower in sync with leader)
- Core loading status
- Query errors and timeouts
- Memory/GC issues
- Update operations (leader)

Architecture:
- Leader pods: container name 'solrleader', handles indexing
- Follower pods: container name 'solrfollower', handles queries + replication

Label selectors:
- app=solr (all pods)
- app.kubernetes.io/name=solr-leader (leader only)
- app.kubernetes.io/name=solr-follower (follower only)
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


@register_plugin("solr")
class SolrPlugin(BasePlugin):
    """Plugin for Solr health checks."""

    COMPONENT_NAME = "solr"

    # Log patterns for Solr issues
    LOG_PATTERNS = {
        # Replication patterns (follower) - detailed version tracking
        "replication_sync": r"Follower in sync with leader",
        "replication_behind": r"Follower is ([\d.]+) versions? behind",
        "replication_error": r"(?:Replication failed|Error replicating|IndexFetcher.*error)",
        # Detailed version patterns for lag detection
        "leader_generation": r"IndexFetcher Leader's generation: (\d+)",
        "leader_version": r"IndexFetcher Leader's version: (\d+)",
        "follower_generation": r"IndexFetcher Follower's generation: (\d+)",
        "follower_version": r"IndexFetcher Follower's version: (\d+)",

        # Core patterns
        "core_loaded": r"SolrCore.*registered",
        "core_error": r"(?:Error opening|Unable to create core|CoreInitializationException)",
        "core_not_found": r"(?:No core found|Core not found)",

        # Query/Update patterns
        "query_error": r"org\.apache\.solr\.common\.SolrException",
        "update_error": r"(?:Update failed|AddDocError|Error adding)",
        "timeout": r"(?:Query timeout|Socket timeout|Read timed out)",

        # Memory patterns
        "oom": r"(?:OutOfMemoryError|java\.lang\.OutOfMemoryError)",
        "gc_overhead": r"(?:GC overhead limit exceeded|GC pause)",
        "heap_warning": r"(?:heap space|memory warning)",

        # General errors
        "error": r"\b(?:ERROR|SEVERE)\b",
        "exception": r"Exception(?::|$|\s)",

        # Health indicators
        "started": r"(?:Solr.*started|Server.*started|listening on port)",
        "registered": r"SolrCore.*registered",
    }

    def __init__(self, config: Optional[dict] = None):
        super().__init__(config or {})
        self.error_threshold = config.get("error_threshold", 5) if config else 5
        self.replication_lag_threshold = config.get("replication_lag_threshold", 10) if config else 10

    def get_checks(self) -> list:
        """Return list of checks to run for Solr."""
        return [
            self.check_pod_status,
            self.check_container_restarts,
            self.check_image_pull,
            self.check_solr_started,
            self.check_replication_status,
            self.check_core_status,
            self.check_query_errors,
            self.check_memory_issues,
            self.check_general_errors,
        ]

    def _is_follower_pod(self, pod: dict) -> bool:
        """Determine if pod is a follower based on labels or name."""
        labels = pod.get("metadata", {}).get("labels", {})
        name = pod.get("metadata", {}).get("name", "")

        if labels.get("app.kubernetes.io/instance") == "follower":
            return True
        if labels.get("app.kubernetes.io/name") == "solr-follower":
            return True
        if "follower" in name.lower():
            return True
        return False

    def _is_leader_pod(self, pod: dict) -> bool:
        """Determine if pod is a leader based on labels or name."""
        labels = pod.get("metadata", {}).get("labels", {})
        name = pod.get("metadata", {}).get("name", "")

        if labels.get("app.kubernetes.io/instance") == "leader":
            return True
        if labels.get("app.kubernetes.io/name") == "solr-leader":
            return True
        if "leader" in name.lower():
            return True
        return False

    def check_solr_started(self) -> CheckResult:
        """Check if Solr has started successfully."""
        check_name = "solr_started"

        if not self.all_logs:
            return CheckResult(
                name=check_name,
                status="unknown",
                message="No logs available to check Solr startup",
            )

        # Check for startup indicators
        started_matches = len(re.findall(self.LOG_PATTERNS["started"], self.all_logs, re.IGNORECASE))
        registered_matches = len(re.findall(self.LOG_PATTERNS["registered"], self.all_logs))

        if registered_matches > 0 or started_matches > 0:
            return CheckResult(
                name=check_name,
                status="ok",
                message=f"Solr started ({registered_matches} cores registered)",
                details={
                    "cores_registered": registered_matches,
                    "startup_indicators": started_matches,
                },
            )

        return CheckResult(
            name=check_name,
            status="warning",
            message="No Solr startup indicators found in logs",
            details={"note": "May be normal if logs are from running instance"},
        )

    def check_replication_status(self) -> CheckResult:
        """Check replication status (relevant for follower pods)."""
        check_name = "replication_status"

        if not self.pods:
            return CheckResult(
                name=check_name,
                status="unknown",
                message="No pods to check replication status",
            )

        # Determine if we have follower pods
        follower_pods = [p for p in self.pods if self._is_follower_pod(p)]

        if not follower_pods:
            return CheckResult(
                name=check_name,
                status="ok",
                message="No follower pods - replication check skipped (leader only)",
                details={"pod_count": len(self.pods), "followers": 0},
            )

        if not self.all_logs:
            return CheckResult(
                name=check_name,
                status="unknown",
                message="No logs available to check replication status",
            )

        # Check for sync status messages
        sync_matches = len(re.findall(self.LOG_PATTERNS["replication_sync"], self.all_logs))
        error_matches = re.findall(self.LOG_PATTERNS["replication_error"], self.all_logs, re.IGNORECASE)
        behind_matches = re.findall(self.LOG_PATTERNS["replication_behind"], self.all_logs)

        # Parse detailed version info to detect mismatches
        version_analysis = self._analyze_replication_versions()

        details = {
            "follower_pods": len(follower_pods),
            "sync_messages": sync_matches,
            "error_count": len(error_matches),
            "behind_count": len(behind_matches),
            "version_analysis": version_analysis,
        }

        if error_matches:
            return CheckResult(
                name=check_name,
                status="critical",
                message=f"Replication errors detected: {len(error_matches)}",
                details={**details, "errors": error_matches[:5]},
            )

        # Check version mismatches
        if version_analysis.get("has_mismatch"):
            mismatches = version_analysis.get("mismatches", [])
            return CheckResult(
                name=check_name,
                status="warning",
                message=f"Replication lag detected: {len(mismatches)} version mismatch(es)",
                details=details,
            )

        if behind_matches:
            return CheckResult(
                name=check_name,
                status="warning",
                message=f"Follower behind leader ({len(behind_matches)} occurrences)",
                details={**details, "behind_versions": behind_matches[:5]},
            )

        if sync_matches > 0:
            return CheckResult(
                name=check_name,
                status="ok",
                message=f"Follower in sync with leader ({sync_matches} sync confirmations)",
                details=details,
            )

        # Check if we found version info but no explicit sync message
        if version_analysis.get("checks_performed", 0) > 0:
            if not version_analysis.get("has_mismatch"):
                return CheckResult(
                    name=check_name,
                    status="ok",
                    message=f"Versions match ({version_analysis.get('checks_performed')} replication checks)",
                    details=details,
                )

        return CheckResult(
            name=check_name,
            status="warning",
            message="No replication status found in logs",
            details=details,
        )

    def _analyze_replication_versions(self) -> dict:
        """
        Analyze IndexFetcher logs to detect leader/follower version mismatches.

        Parses log lines like:
        - IndexFetcher Leader's generation: 214
        - IndexFetcher Leader's version: 1758885528186
        - IndexFetcher Follower's generation: 214
        - IndexFetcher Follower's version: 1758885528186

        Returns dict with analysis results including any mismatches.
        """
        if not self.all_logs:
            return {"checks_performed": 0}

        # Extract all version entries
        leader_gens = re.findall(self.LOG_PATTERNS["leader_generation"], self.all_logs)
        leader_vers = re.findall(self.LOG_PATTERNS["leader_version"], self.all_logs)
        follower_gens = re.findall(self.LOG_PATTERNS["follower_generation"], self.all_logs)
        follower_vers = re.findall(self.LOG_PATTERNS["follower_version"], self.all_logs)

        # Group by position (each check has 4 consecutive lines)
        checks_performed = min(len(leader_gens), len(leader_vers), len(follower_gens), len(follower_vers))

        if checks_performed == 0:
            return {"checks_performed": 0}

        mismatches = []
        for i in range(checks_performed):
            l_gen = int(leader_gens[i])
            l_ver = int(leader_vers[i])
            f_gen = int(follower_gens[i])
            f_ver = int(follower_vers[i])

            # Check for mismatch (excluding 0 versions which are empty/new cores)
            if l_gen != f_gen or l_ver != f_ver:
                # Skip if both are 0 (empty core)
                if l_ver == 0 and f_ver == 0:
                    continue
                mismatches.append({
                    "leader_generation": l_gen,
                    "leader_version": l_ver,
                    "follower_generation": f_gen,
                    "follower_version": f_ver,
                    "generation_diff": abs(l_gen - f_gen),
                    "version_diff": abs(l_ver - f_ver),
                })

        return {
            "checks_performed": checks_performed,
            "has_mismatch": len(mismatches) > 0,
            "mismatch_count": len(mismatches),
            "mismatches": mismatches[:5],  # Limit to 5 samples
            "last_leader_gen": int(leader_gens[-1]) if leader_gens else None,
            "last_leader_ver": int(leader_vers[-1]) if leader_vers else None,
            "last_follower_gen": int(follower_gens[-1]) if follower_gens else None,
            "last_follower_ver": int(follower_vers[-1]) if follower_vers else None,
        }

    def check_core_status(self) -> CheckResult:
        """Check Solr core loading status."""
        check_name = "core_status"

        if not self.all_logs:
            return CheckResult(
                name=check_name,
                status="unknown",
                message="No logs available to check core status",
            )

        # Check for core errors
        core_errors = re.findall(self.LOG_PATTERNS["core_error"], self.all_logs, re.IGNORECASE)
        core_not_found = re.findall(self.LOG_PATTERNS["core_not_found"], self.all_logs, re.IGNORECASE)
        cores_registered = len(re.findall(self.LOG_PATTERNS["core_loaded"], self.all_logs))

        details = {
            "cores_registered": cores_registered,
            "core_errors": len(core_errors),
            "core_not_found": len(core_not_found),
        }

        if core_errors:
            return CheckResult(
                name=check_name,
                status="critical",
                message=f"Core errors detected: {len(core_errors)}",
                details={**details, "errors": core_errors[:5]},
            )

        if core_not_found:
            return CheckResult(
                name=check_name,
                status="warning",
                message=f"Core not found errors: {len(core_not_found)}",
                details={**details, "not_found": core_not_found[:5]},
            )

        return CheckResult(
            name=check_name,
            status="ok",
            message=f"Cores OK ({cores_registered} registered in logs)",
            details=details,
        )

    def check_query_errors(self) -> CheckResult:
        """Check for query and update errors."""
        check_name = "query_errors"

        if not self.all_logs:
            return CheckResult(
                name=check_name,
                status="unknown",
                message="No logs available to check query errors",
            )

        query_errors = re.findall(self.LOG_PATTERNS["query_error"], self.all_logs)
        update_errors = re.findall(self.LOG_PATTERNS["update_error"], self.all_logs, re.IGNORECASE)
        timeouts = re.findall(self.LOG_PATTERNS["timeout"], self.all_logs, re.IGNORECASE)

        total_errors = len(query_errors) + len(update_errors) + len(timeouts)

        details = {
            "query_errors": len(query_errors),
            "update_errors": len(update_errors),
            "timeouts": len(timeouts),
            "total": total_errors,
        }

        if total_errors == 0:
            return CheckResult(
                name=check_name,
                status="ok",
                message="No query/update errors detected",
                details=details,
            )

        if total_errors >= self.error_threshold:
            return CheckResult(
                name=check_name,
                status="critical",
                message=f"High error count: {total_errors} (threshold: {self.error_threshold})",
                details=details,
            )

        return CheckResult(
            name=check_name,
            status="warning",
            message=f"Some errors detected: {total_errors}",
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

        oom_errors = re.findall(self.LOG_PATTERNS["oom"], self.all_logs, re.IGNORECASE)
        gc_issues = re.findall(self.LOG_PATTERNS["gc_overhead"], self.all_logs, re.IGNORECASE)
        heap_warnings = re.findall(self.LOG_PATTERNS["heap_warning"], self.all_logs, re.IGNORECASE)

        details = {
            "oom_errors": len(oom_errors),
            "gc_issues": len(gc_issues),
            "heap_warnings": len(heap_warnings),
        }

        if oom_errors:
            return CheckResult(
                name=check_name,
                status="critical",
                message=f"OutOfMemoryError detected ({len(oom_errors)} occurrences)",
                details=details,
            )

        if gc_issues:
            return CheckResult(
                name=check_name,
                status="critical",
                message=f"GC overhead issues detected ({len(gc_issues)} occurrences)",
                details=details,
            )

        if heap_warnings:
            return CheckResult(
                name=check_name,
                status="warning",
                message=f"Heap warnings detected ({len(heap_warnings)} occurrences)",
                details=details,
            )

        return CheckResult(
            name=check_name,
            status="ok",
            message="No memory issues detected",
            details=details,
        )

    def check_general_errors(self) -> CheckResult:
        """Check for general ERROR/SEVERE messages in logs."""
        check_name = "general_errors"

        if not self.all_logs:
            return CheckResult(
                name=check_name,
                status="unknown",
                message="No logs available to check for errors",
            )

        error_lines = re.findall(r'^.*\b(?:ERROR|SEVERE)\b.*$', self.all_logs, re.MULTILINE | re.IGNORECASE)
        exception_lines = re.findall(r'^.*Exception(?::|$|\s).*$', self.all_logs, re.MULTILINE)

        # Deduplicate and limit
        unique_errors = list(set(error_lines))[:10]
        error_count = len(error_lines)
        exception_count = len(exception_lines)

        details = {
            "error_count": error_count,
            "exception_count": exception_count,
            "sample_errors": [e[:150] for e in unique_errors[:5]],
        }

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
                message=f"High error count: {error_count} errors, {exception_count} exceptions",
                details=details,
            )

        if error_count >= self.error_threshold or exception_count > 0:
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
