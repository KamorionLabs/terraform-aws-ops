# EFS Sub-State Machines

This module contains reusable sub-State Machines for EFS operations. They are deployed via the existing `for_each` in `main.tf` alongside the core EFS State Machines.

Sub-SFNs are self-contained building blocks: each handles its own errors via a named `Fail` state, making error routing predictable for parent workflows.

## ManageFileSystemPolicy

**File:** `manage_filesystem_policy.asl.json`
**States:** 12

Manages EFS FileSystem policies by adding or removing IAM policy statements. Generalizes the policy management pattern used in `setup_cross_account_replication` (source + destination policies) and `delete_replication` (cleanup).

### Input

```json
{
  "FileSystemId": "fs-0123456789abcdef0",
  "Account": {
    "RoleArn": "arn:aws:iam::123456789012:role/EFSCrossAccountRole"
  },
  "Action": "ADD",
  "PolicyStatement": {
    "Sid": "AllowReplicationWrite",
    "Effect": "Allow",
    "Principal": {
      "Service": "elasticfilesystem.amazonaws.com"
    },
    "Action": [
      "elasticfilesystem:ClientMount",
      "elasticfilesystem:ClientWrite"
    ],
    "Resource": "arn:aws:elasticfilesystem:eu-west-3:123456789012:file-system/fs-0123456789abcdef0",
    "Condition": {
      "Bool": {
        "elasticfilesystem:AccessedViaMountTarget": "true"
      }
    }
  }
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `FileSystemId` | string | Yes | EFS file system ID to manage the policy on |
| `Account.RoleArn` | string | Yes | IAM role ARN for cross-account access |
| `Action` | string | Yes | `ADD` to merge a statement, `REMOVE` to delete by Sid |
| `PolicyStatement` | object | Yes | IAM policy statement with `Sid` (used for dedup/removal) |

### Output

```json
{
  "FileSystemId": "fs-0123456789abcdef0",
  "PolicyApplied": true,
  "Status": "Completed"
}
```

### Behavior

- **ADD**: Reads current policy, removes any existing statement with the same `Sid`, appends the new statement, puts the merged policy. If no policy exists, creates one from scratch.
- **REMOVE**: Reads current policy, filters out the statement matching `Sid`. If the last statement is removed, deletes the entire policy. If no policy exists, returns success (nothing to remove).
- **Errors**: All Task states catch `States.ALL` and route to `ManageFileSystemPolicyFailed` (Type: Fail). Parent workflows detect this via `ErrorEquals: ["ManageFileSystemPolicyFailed"]`.

### Catch

All failures terminate on state `Fail` with `Error: "ManageFileSystemPolicyFailed"`.
The parent detects via `ErrorEquals: ["ManageFileSystemPolicyFailed"]`.

## ManageAccessPoint

**File:** `manage_access_point.asl.json`

> To be documented after implementation in Plan 03

## ManageLambdaLifecycle

**File:** `manage_lambda_lifecycle.asl.json`

> To be documented after implementation in Plan 03

## Integration Pattern

Parent workflows (Phase 2 refactoring) will call these sub-SFNs via `states:startExecution.sync:2`. The sub-SFN ARNs are injected using `templatefile()`:

```hcl
# Phase 2 pattern — callers inject sub-SFN ARNs via templatefile()
definition = templatefile("${path.module}/caller.asl.json", {
  manage_filesystem_policy_arn = module.efs_sfn.step_function_arns["manage_filesystem_policy"]
  manage_access_point_arn      = module.efs_sfn.step_function_arns["manage_access_point"]
  manage_lambda_lifecycle_arn  = module.efs_sfn.step_function_arns["manage_lambda_lifecycle"]
})
```

The caller ASL references the ARN as a template variable:

```json
{
  "Type": "Task",
  "Resource": "arn:aws:states:::states:startExecution.sync:2",
  "Parameters": {
    "StateMachineArn": "${manage_filesystem_policy_arn}",
    "Input": {
      "FileSystemId.$": "$.SourceFileSystemId",
      "Account.$": "$.SourceAccount",
      "Action": "ADD",
      "PolicyStatement": { "..." : "..." }
    }
  }
}
```

## Naming

Keys in `local.step_functions` use `snake_case`. The naming logic in `main.tf` converts them automatically:

| Key | Kebab (default) | Pascal |
|-----|-----------------|--------|
| `manage_filesystem_policy` | `{prefix}-efs-manage-filesystem-policy` | `{prefix}-EFS-ManageFilesystemPolicy` |
| `manage_access_point` | `{prefix}-efs-manage-access-point` | `{prefix}-EFS-ManageAccessPoint` |
| `manage_lambda_lifecycle` | `{prefix}-efs-manage-lambda-lifecycle` | `{prefix}-EFS-ManageLambdaLifecycle` |

No additional naming code is required. The wildcard IAM pattern `{prefix}-*` covers all sub-SFNs automatically.
