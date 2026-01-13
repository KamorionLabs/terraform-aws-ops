"""
State Manager for Ops Dashboard
================================
Manages operational state in DynamoDB with change detection.
Supports multiple domains: migration, health, cost, compliance, performance.

Only writes new state if it has changed, maintains history on changes.

DynamoDB Schema:
- pk: domain#target (e.g., "mro#mi2-preprod", "health#webshop-prod", "cost#2025-01")
- sk: "check:<check_type>:current" or "check:<check_type>:history#<timestamp>"
- state_hash: SHA256 hash of the state payload
- payload: The actual state data
- updated_at: ISO timestamp
- updated_by: Source of the update (lambda name, manual, etc.)

Domains (extensible):
- mro: MRO instances migration & health (mi2, orexad, minetti, etc.)
- webshop: Webshop environments health (int, stg, ppd, prd)
- platform: Platform-level resources (network, shared-services, eks-tooling)
- cost: Cost tracking per period/service
- compliance: Compliance and security checks
- performance: Performance metrics and SLOs

Check Types (extensible):
- infrastructure: RDS, EFS, EKS availability
- replication: DMS lag, EFS sync status
- pods: Kubernetes pod health
- secrets: Secrets Manager sync status
- dns: Route53 validation
- certificates: ACM certificate validity
- cost_daily/cost_monthly: Cost breakdowns
- security_groups: SG audit
- iam_policies: IAM audit
"""

import hashlib
import json
import logging
import os
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Optional

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)
logger.setLevel(os.environ.get("LOG_LEVEL", "INFO"))


class DecimalEncoder(json.JSONEncoder):
    """Handle Decimal types from DynamoDB."""

    def default(self, obj):
        if isinstance(obj, Decimal):
            return float(obj) if obj % 1 else int(obj)
        return super().default(obj)


def _serialize_for_dynamo(obj: Any) -> Any:
    """Convert floats to Decimal for DynamoDB compatibility."""
    if isinstance(obj, float):
        return Decimal(str(obj))
    elif isinstance(obj, dict):
        return {k: _serialize_for_dynamo(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [_serialize_for_dynamo(v) for v in obj]
    return obj


def _compute_hash(payload: dict) -> str:
    """Compute SHA256 hash of payload for change detection."""
    # Sort keys for consistent hashing
    canonical = json.dumps(payload, sort_keys=True, cls=DecimalEncoder)
    return hashlib.sha256(canonical.encode()).hexdigest()


class StateManager:
    """
    Manages state in DynamoDB with change detection.

    Usage:
        manager = StateManager(table_name="ops-dashboard-state")

        # Migration check (MRO instance)
        result = manager.update_state(
            domain="mro",
            target="mi2-preprod",
            check_type="infrastructure",
            payload={"rds": "ok", "efs": "ok", "eks": "ok"},
            updated_by="lambda:infra-checker"
        )

        # Health check (webshop)
        result = manager.update_state(
            domain="webshop",
            target="prod",
            check_type="pods",
            payload={"hybris": {"running": 2, "ready": 2}, "apache": {"running": 2, "ready": 2}},
            updated_by="lambda:pod-checker"
        )

        # Cost tracking
        result = manager.update_state(
            domain="cost",
            target="2025-01",
            check_type="monthly",
            payload={"total": 45000, "by_service": {"ec2": 15000, "rds": 12000, ...}},
            updated_by="lambda:cost-collector"
        )

        # Get current state
        state = manager.get_current_state("mro", "mi2-preprod", "infrastructure")

        # Get state history
        history = manager.get_state_history("mro", "mi2-preprod", "infrastructure", limit=10)
    """

    def __init__(self, table_name: str = None, region: str = None):
        self.table_name = table_name or os.environ.get(
            "STATE_TABLE_NAME", "ops-dashboard-state"
        )
        self.region = region or os.environ.get("AWS_REGION", "eu-central-1")
        self._dynamodb = None
        self._table = None

    @property
    def dynamodb(self):
        if self._dynamodb is None:
            self._dynamodb = boto3.resource("dynamodb", region_name=self.region)
        return self._dynamodb

    @property
    def table(self):
        if self._table is None:
            self._table = self.dynamodb.Table(self.table_name)
        return self._table

    def _make_pk(self, domain: str, target: str) -> str:
        """Create partition key from domain and target."""
        return f"{domain}#{target}"

    def _make_sk_current(self, check_type: str) -> str:
        """Create sort key for current state."""
        return f"check:{check_type}:current"

    def _make_sk_history(self, check_type: str, timestamp: str) -> str:
        """Create sort key for history entry."""
        return f"check:{check_type}:history#{timestamp}"

    def get_current_state(
        self, domain: str, target: str, check_type: str
    ) -> Optional[dict]:
        """
        Get the current state for a domain/target/check_type.

        Returns None if no state exists.
        """
        pk = self._make_pk(domain, target)
        sk = self._make_sk_current(check_type)

        try:
            response = self.table.get_item(Key={"pk": pk, "sk": sk})
            item = response.get("Item")
            if item:
                # Convert Decimals back to native types
                return json.loads(json.dumps(item, cls=DecimalEncoder))
            return None
        except ClientError as e:
            logger.error(f"Error getting state: {e}")
            raise

    def update_state(
        self,
        domain: str,
        target: str,
        check_type: str,
        payload: dict,
        updated_by: str = "unknown",
        force: bool = False,
        metadata: dict = None,
    ) -> dict:
        """
        Update state if it has changed.

        Args:
            domain: Domain category (e.g., "mro", "webshop", "cost")
            target: Target identifier (e.g., "mi2-preprod", "prod", "2025-01")
            check_type: Type of check (e.g., "infrastructure", "pods", "monthly")
            payload: The state data to store
            updated_by: Source of the update
            force: If True, write even if state hasn't changed
            metadata: Optional additional metadata (not included in hash)

        Returns:
            dict with:
                - changed: bool - Whether the state was updated
                - state_hash: str - Hash of the current state
                - previous_hash: str | None - Hash of the previous state
                - timestamp: str - Current timestamp
        """
        pk = self._make_pk(domain, target)
        sk_current = self._make_sk_current(check_type)
        now = datetime.now(timezone.utc)
        timestamp = now.isoformat()

        # Compute hash of new payload
        new_hash = _compute_hash(payload)

        # Get current state to compare
        current_state = self.get_current_state(domain, target, check_type)
        previous_hash = current_state.get("state_hash") if current_state else None

        # Check if state has changed
        if not force and previous_hash == new_hash:
            logger.info(
                f"State unchanged for {pk}/{check_type} (hash: {new_hash[:8]}...)"
            )
            return {
                "changed": False,
                "state_hash": new_hash,
                "previous_hash": previous_hash,
                "timestamp": timestamp,
            }

        # State has changed - write new state and history
        logger.info(
            f"State changed for {pk}/{check_type}: {previous_hash[:8] if previous_hash else 'None'}... -> {new_hash[:8]}..."
        )

        # Prepare item for DynamoDB
        item = {
            "pk": pk,
            "sk": sk_current,
            "domain": domain,
            "target": target,
            "check_type": check_type,
            "state_hash": new_hash,
            "payload": _serialize_for_dynamo(payload),
            "updated_at": timestamp,
            "updated_by": updated_by,
        }

        # Add previous hash if exists (for tracking)
        if previous_hash:
            item["previous_hash"] = previous_hash

        # Add optional metadata
        if metadata:
            item["metadata"] = _serialize_for_dynamo(metadata)

        try:
            # Write current state
            self.table.put_item(Item=item)

            # Write history entry
            history_item = {
                "pk": pk,
                "sk": self._make_sk_history(check_type, timestamp),
                "domain": domain,
                "target": target,
                "check_type": check_type,
                "state_hash": new_hash,
                "payload": _serialize_for_dynamo(payload),
                "updated_at": timestamp,
                "updated_by": updated_by,
                "previous_hash": previous_hash,
                # TTL for history entries (90 days)
                "ttl": int(now.timestamp()) + (90 * 24 * 60 * 60),
            }
            if metadata:
                history_item["metadata"] = _serialize_for_dynamo(metadata)
            self.table.put_item(Item=history_item)

            return {
                "changed": True,
                "state_hash": new_hash,
                "previous_hash": previous_hash,
                "timestamp": timestamp,
            }

        except ClientError as e:
            logger.error(f"Error updating state: {e}")
            raise

    def get_state_history(
        self,
        domain: str,
        target: str,
        check_type: str,
        limit: int = 20,
        ascending: bool = False,
    ) -> list[dict]:
        """
        Get history of state changes.

        Args:
            domain: Domain category
            target: Target identifier
            check_type: Type of check
            limit: Maximum number of history entries to return
            ascending: If True, return oldest first; if False, newest first

        Returns:
            List of state history entries
        """
        pk = self._make_pk(domain, target)
        sk_prefix = f"check:{check_type}:history#"

        try:
            response = self.table.query(
                KeyConditionExpression="pk = :pk AND begins_with(sk, :sk_prefix)",
                ExpressionAttributeValues={":pk": pk, ":sk_prefix": sk_prefix},
                ScanIndexForward=ascending,
                Limit=limit,
            )

            items = response.get("Items", [])
            # Convert Decimals back to native types
            return [json.loads(json.dumps(item, cls=DecimalEncoder)) for item in items]

        except ClientError as e:
            logger.error(f"Error getting state history: {e}")
            raise

    def get_all_states_for_target(self, domain: str, target: str) -> list[dict]:
        """
        Get all current states for a domain/target.

        Returns list of current state entries for all check types.
        """
        pk = self._make_pk(domain, target)

        try:
            response = self.table.query(
                KeyConditionExpression="pk = :pk AND begins_with(sk, :sk_prefix)",
                FilterExpression="contains(sk, :current)",
                ExpressionAttributeValues={
                    ":pk": pk,
                    ":sk_prefix": "check:",
                    ":current": ":current",
                },
            )

            items = response.get("Items", [])
            return [json.loads(json.dumps(item, cls=DecimalEncoder)) for item in items]

        except ClientError as e:
            logger.error(f"Error getting all states: {e}")
            raise

    def list_targets_for_domain(self, domain: str) -> list[str]:
        """
        List all targets for a given domain.

        Returns list of unique target identifiers.
        """
        try:
            response = self.table.query(
                IndexName="domain_index",
                KeyConditionExpression="#d = :domain",
                ExpressionAttributeNames={"#d": "domain"},
                ExpressionAttributeValues={":domain": domain},
                ProjectionExpression="target",
            )

            items = response.get("Items", [])
            # Get unique targets
            return list(set(item.get("target") for item in items if item.get("target")))

        except ClientError as e:
            logger.error(f"Error listing targets: {e}")
            raise

    def delete_state(self, domain: str, target: str, check_type: str) -> bool:
        """
        Delete current state (history is preserved until TTL expires).

        Returns True if deleted, False if not found.
        """
        pk = self._make_pk(domain, target)
        sk = self._make_sk_current(check_type)

        try:
            response = self.table.delete_item(
                Key={"pk": pk, "sk": sk},
                ReturnValues="ALL_OLD",
            )
            return "Attributes" in response

        except ClientError as e:
            logger.error(f"Error deleting state: {e}")
            raise


# Singleton instance for Lambda reuse
_default_manager: Optional[StateManager] = None


def get_state_manager() -> StateManager:
    """Get or create the default StateManager instance."""
    global _default_manager
    if _default_manager is None:
        _default_manager = StateManager()
    return _default_manager
