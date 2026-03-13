# DB Sub-State Machines

This module contains reusable sub-State Machines for RDS/Aurora database operations. They are deployed via `for_each` in `main.tf` alongside the core DB State Machines.

Sub-SFNs are self-contained building blocks: each handles its own errors via a named `Fail` state, making error routing predictable for parent workflows.

## EnsureSnapshotAvailable

**File:** `ensure_snapshot_available.asl.json`
**States:** 8

Waits for an RDS cluster snapshot to become available. Handles both same-region (direct SDK) and cross-region (Lambda proxy) scenarios. Does NOT create snapshots -- only verifies availability of existing ones.

### Input

```json
{
  "SnapshotIdentifier": "refresh-copy-abc123",
  "Account": {
    "RoleArn": "arn:aws:iam::123456789012:role/DBCrossAccountRole"
  },
  "ProxyLambdaArn": "arn:aws:lambda:eu-west-3:123456789012:function:rds-proxy",
  "SourceRegion": "us-east-1"
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `SnapshotIdentifier` | string | Yes | DB cluster snapshot identifier to check |
| `Account.RoleArn` | string | Yes | IAM role ARN for cross-account access |
| `ProxyLambdaArn` | string | No | Lambda ARN for cross-region proxy (if snapshot is in a different region) |
| `SourceRegion` | string | No | Region where the snapshot resides (required if ProxyLambdaArn is set) |

### Output

```json
{
  "SnapshotIdentifier": "refresh-copy-abc123",
  "SnapshotArn": "arn:aws:rds:eu-west-3:123456789012:cluster-snapshot:refresh-copy-abc123",
  "Status": "available",
  "EngineVersion": "8.0.mysql_aurora.3.04.0"
}
```

### Behavior

- Routes to direct SDK or Lambda proxy based on `ProxyLambdaArn` presence.
- Polls `rds:describeDBClusterSnapshots` in a Wait(30s)/Check loop until `Status` is `available`.
- If snapshot status is `failed`, immediately fails without further polling.
- **Errors**: All Task states catch `States.ALL` and route to `EnsureSnapshotAvailableFailed` (Type: Fail).

### Catch

All failures terminate on state `Fail` with `Error: "EnsureSnapshotAvailableFailed"`.
The parent detects via `ErrorEquals: ["EnsureSnapshotAvailableFailed"]`.

## Integration Pattern

Parent workflows call sub-SFNs via `states:startExecution.sync:2`. The module uses two resource tiers:

```hcl
# Tier 1: file() based -- sub-SFNs and unchanged SFNs (no ARN injection)
resource "aws_sfn_state_machine" "db" { ... }

# Tier 2: templatefile() -- Refactored callers (reference Tier 1 ARNs)
resource "aws_sfn_state_machine" "db_templated" { ... }
```

The caller ASL references the ARN as a template variable:

```json
{
  "Type": "Task",
  "Resource": "arn:aws:states:::states:startExecution.sync:2",
  "Arguments": {
    "StateMachineArn": "${ensure_snapshot_available_arn}",
    "Input": {
      "SnapshotIdentifier": "{% $states.input.CopiedSnapshot.SnapshotIdentifier %}",
      "Account": {
        "RoleArn": "{% $states.input.SourceAccount.RoleArn %}"
      }
    }
  }
}
```

## Naming

Keys in `local.step_functions` (and related maps) use `snake_case`. The naming logic in `main.tf` converts them automatically:

| Key | Kebab (default) | Pascal |
|-----|-----------------|--------|
| `ensure_snapshot_available` | `{prefix}-db-ensure-snapshot-available` | `{prefix}-DB-EnsureSnapshotAvailable` |

No additional naming code is required. The wildcard IAM pattern `{prefix}-*` covers all sub-SFNs automatically.
