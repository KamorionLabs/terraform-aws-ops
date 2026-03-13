# EFS Sub-State Machines

This module contains reusable sub-State Machines for EFS operations. They are deployed via `for_each` in `main.tf` alongside the core EFS State Machines.

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
**States:** 6

Creates an EFS access point and waits for it to become available. Generalizes the access point creation pattern used in `check_replication_sync` (source + destination access points) and `get_subpath_and_store_in_ssm` (temporary access point for Lambda mount).

### Input

```json
{
  "FileSystemId": "fs-0123456789abcdef0",
  "AccessPointConfig": {
    "ClientToken": "unique-token-123",
    "PosixUser": {
      "Uid": 0,
      "Gid": 0
    },
    "RootDirectory": {
      "Path": "/",
      "CreationInfo": {
        "OwnerUid": 0,
        "OwnerGid": 0,
        "Permissions": "755"
      }
    },
    "Tags": [
      { "Key": "Name", "Value": "temp-access-point" },
      { "Key": "ManagedBy", "Value": "StepFunctions" }
    ]
  },
  "Account": {
    "RoleArn": "arn:aws:iam::123456789012:role/EFSCrossAccountRole"
  }
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `FileSystemId` | string | Yes | EFS file system ID to create the access point on |
| `AccessPointConfig.ClientToken` | string | Yes | Idempotency token for the access point |
| `AccessPointConfig.PosixUser` | object | No | POSIX user identity (Uid, Gid) |
| `AccessPointConfig.RootDirectory` | object | No | Root directory configuration (Path, CreationInfo) |
| `AccessPointConfig.Tags` | array | No | Tags for the access point |
| `Account.RoleArn` | string | Yes | IAM role ARN for cross-account access |

### Output

```json
{
  "AccessPointId": "fsap-0123456789abcdef0",
  "AccessPointArn": "arn:aws:elasticfilesystem:eu-west-3:123456789012:access-point/fsap-0123456789abcdef0",
  "FileSystemId": "fs-0123456789abcdef0",
  "Status": "Available"
}
```

### Behavior

- Creates the access point via `efs:createAccessPoint` with cross-account credentials.
- Polls `efs:describeAccessPoints` in a Wait/Check loop until `LifeCycleState` is `available`.
- **Errors**: All Task states catch `States.ALL` and route to `ManageAccessPointFailed` (Type: Fail). Parent workflows detect this via `ErrorEquals: ["ManageAccessPointFailed"]`.

### Catch

All failures terminate on state `Fail` with `Error: "ManageAccessPointFailed"`.
The parent detects via `ErrorEquals: ["ManageAccessPointFailed"]`.

## ManageLambdaLifecycle

**File:** `manage_lambda_lifecycle.asl.json`
**States:** 8

Ensures a Lambda function exists, creating it if needed. Optionally updates the function code for existing Lambdas. Generalizes the Lambda lifecycle pattern used in `check_replication_sync` (source + destination Lambdas) and `get_subpath_and_store_in_ssm` (subpath discovery Lambda).

### Input

```json
{
  "LambdaConfig": {
    "FunctionName": "prefix-check-flag-file-context",
    "Runtime": "python3.11",
    "Handler": "check_flag_file.lambda_handler",
    "Role": "arn:aws:iam::123456789012:role/LambdaExecutionRole",
    "Code": {
      "S3Bucket": "deployment-bucket",
      "S3Key": "lambdas/check_flag_file.zip"
    },
    "Timeout": 60,
    "MemorySize": 128,
    "Architectures": ["arm64"],
    "VpcConfig": {
      "SubnetIds": ["subnet-abc123"],
      "SecurityGroupIds": ["sg-abc123"]
    },
    "Environment": {
      "Variables": {
        "LOG_LEVEL": "INFO",
        "EFS_MOUNT_PATH": "/mnt/efs"
      }
    },
    "Tags": {
      "ManagedBy": "StepFunctions",
      "Purpose": "EFS-FlagFile-Check"
    },
    "ForceUpdateCode": false
  },
  "Account": {
    "RoleArn": "arn:aws:iam::123456789012:role/EFSCrossAccountRole"
  }
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `LambdaConfig.FunctionName` | string | Yes | Lambda function name |
| `LambdaConfig.Runtime` | string | Yes | Lambda runtime (e.g., `python3.11`) |
| `LambdaConfig.Handler` | string | Yes | Lambda handler (e.g., `module.handler`) |
| `LambdaConfig.Role` | string | Yes | IAM execution role ARN for the Lambda |
| `LambdaConfig.Code.S3Bucket` | string | Yes | S3 bucket containing the Lambda code |
| `LambdaConfig.Code.S3Key` | string | Yes | S3 key for the Lambda code zip |
| `LambdaConfig.Timeout` | number | No | Lambda timeout in seconds |
| `LambdaConfig.MemorySize` | number | No | Lambda memory in MB |
| `LambdaConfig.Architectures` | array | No | CPU architectures (e.g., `["arm64"]`) |
| `LambdaConfig.VpcConfig` | object | No | VPC configuration (SubnetIds, SecurityGroupIds) |
| `LambdaConfig.Environment` | object | No | Environment variables |
| `LambdaConfig.Tags` | object | No | Tags for the Lambda function |
| `LambdaConfig.ForceUpdateCode` | boolean | No | If `true`, update code of existing Lambda from S3 |
| `Account.RoleArn` | string | Yes | IAM role ARN for cross-account access |

### Output

```json
{
  "FunctionName": "prefix-check-flag-file-context",
  "FunctionArn": "arn:aws:lambda:*:*:function:prefix-check-flag-file-context",
  "Status": "Ready"
}
```

### Behavior

- Checks if the Lambda function exists via `lambda:getFunction`.
- **If exists**: Optionally updates code via `lambda:updateFunctionCode` when `ForceUpdateCode` is `true`, then returns Ready.
- **If not found**: Creates the function via `lambda:createFunction` with all provided configuration, waits 5s for activation, then returns Ready.
- **Race condition**: If `createFunction` throws `ResourceConflictException` (concurrent creation), treats it as existing and returns Ready.
- **Errors**: All Task states catch `States.ALL` and route to `ManageLambdaLifecycleFailed` (Type: Fail). Parent workflows detect this via `ErrorEquals: ["ManageLambdaLifecycleFailed"]`.

### Catch

All failures terminate on state `Fail` with `Error: "ManageLambdaLifecycleFailed"`.
The parent detects via `ErrorEquals: ["ManageLambdaLifecycleFailed"]`.

## CheckFlagFileSync

**File:** `check_flag_file_sync.asl.json`
**States:** 26

Verifies EFS replication sync by writing a flag file to the source EFS and checking for its appearance on the destination EFS. Manages the full lifecycle: Lambda creation (via ManageLambdaLifecycle), access point creation (via ManageAccessPoint), flag write/check, and cleanup. Uses 3-level nesting (parent -> CheckFlagFileSync -> ManageLambdaLifecycle/ManageAccessPoint).

### Input

```json
{
  "SourceFileSystemId": "fs-source123",
  "DestinationFileSystemId": "fs-dest456",
  "SourceAccount": {
    "RoleArn": "arn:aws:iam::111111111111:role/EFSCrossAccountRole"
  },
  "DestinationAccount": {
    "RoleArn": "arn:aws:iam::222222222222:role/EFSCrossAccountRole"
  },
  "LambdaConfig": {
    "SourceLambdaPrefix": "prefix",
    "DestinationLambdaPrefix": "prefix",
    "SourceLambdaRoleArn": "arn:aws:iam::111111111111:role/LambdaRole",
    "DestinationLambdaRoleArn": "arn:aws:iam::222222222222:role/LambdaRole",
    "CodeS3Bucket": "deployment-bucket",
    "CodeS3Key": "lambdas/check_flag_file.zip",
    "SourceSubnetIds": ["subnet-abc"],
    "SourceSecurityGroupIds": ["sg-abc"],
    "DestinationSubnetIds": ["subnet-def"],
    "DestinationSecurityGroupIds": ["sg-def"],
    "ForceUpdateLambdaCode": false
  },
  "SourceSubpath": "",
  "DestinationSubpath": "",
  "FlagId": "uuid-string",
  "Context": "my-context"
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `SourceFileSystemId` | string | Yes | Source EFS file system ID |
| `DestinationFileSystemId` | string | Yes | Destination EFS file system ID |
| `SourceAccount.RoleArn` | string | Yes | IAM role for source account access |
| `DestinationAccount.RoleArn` | string | Yes | IAM role for destination account access |
| `LambdaConfig` | object | Yes | Lambda configuration for flag file check Lambdas |
| `SourceSubpath` | string | Yes | Subpath within source EFS (empty for root) |
| `DestinationSubpath` | string | Yes | Subpath within destination EFS (empty for root) |
| `FlagId` | string | Yes | Unique flag file identifier (UUID) |
| `Context` | string | Yes | Context identifier for Lambda naming |

### Output

```json
{
  "SyncVerified": true,
  "FlagId": "uuid-string",
  "SourceTime": "2024-01-01T00:00:00Z",
  "DestinationTime": "2024-01-01T00:02:30Z",
  "Status": "Verified"
}
```

### Behavior

- Ensures source and destination Lambdas exist via `ManageLambdaLifecycle` sub-SFN (2 calls).
- Creates temporary access points on both file systems via `ManageAccessPoint` sub-SFN (2 calls).
- Binds access points to Lambdas via `updateFunctionConfiguration`.
- Writes a flag file to source EFS, waits for replication, then checks destination EFS.
- Polls up to 15 times with 60s intervals for flag file appearance.
- Cleans up flag files and access points (keeps Lambdas for reuse).
- On timeout or error: best-effort cleanup of access points before failing.

### Catch

All failures terminate on state `Fail` with `Error: "CheckFlagFileSyncFailed"`.
The parent detects via `ErrorEquals: ["CheckFlagFileSyncFailed"]`.

## Integration Pattern

Parent workflows call sub-SFNs via `states:startExecution.sync:2`. The module uses three resource tiers to avoid circular ARN references:

```hcl
# Tier 1: file() based — Phase 1 sub-SFNs (no ARN injection)
resource "aws_sfn_state_machine" "efs" { ... }

# Tier 2: templatefile() — Phase 2 sub-SFNs (reference Tier 1 ARNs)
resource "aws_sfn_state_machine" "efs_sub_templated" { ... }

# Tier 3: templatefile() — Refactored callers (reference Tier 1 + Tier 2 ARNs)
resource "aws_sfn_state_machine" "efs_templated" { ... }
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

Keys in `local.step_functions` (and related maps) use `snake_case`. The naming logic in `main.tf` converts them automatically:

| Key | Kebab (default) | Pascal |
|-----|-----------------|--------|
| `manage_filesystem_policy` | `{prefix}-efs-manage-filesystem-policy` | `{prefix}-EFS-ManageFilesystemPolicy` |
| `manage_access_point` | `{prefix}-efs-manage-access-point` | `{prefix}-EFS-ManageAccessPoint` |
| `manage_lambda_lifecycle` | `{prefix}-efs-manage-lambda-lifecycle` | `{prefix}-EFS-ManageLambdaLifecycle` |
| `check_flag_file_sync` | `{prefix}-efs-check-flag-file-sync` | `{prefix}-EFS-CheckFlagFileSync` |

No additional naming code is required. The wildcard IAM pattern `{prefix}-*` covers all sub-SFNs automatically.
