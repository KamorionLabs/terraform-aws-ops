# Phase 07: S3 Replication Module - Pattern Map

**Mapped:** 2026-06-17
**Files analyzed:** 11 (6 new module files + 1 new test-discovered + 4 source-account edits — see classification)
**Analogs found:** 9 / 11 (2 partial — s3control batch SFN have no direct EFS SDK analog, structural skeleton mapped)

## Scope Summary

New module `modules/step-functions/s3/` (file()-based, **NO Lambda** per D-09) with 4 `*.asl.json` + `main.tf`/`variables.tf`/`outputs.tf`/`versions.tf`. Plus additive, `var.enable_s3`-gated IAM in `modules/source-account/` (role + policy + variable + output). The orchestrator/root wiring is **Phase 8** (out of scope). Tests auto-discover the 4 new ASL files — no test wiring (Phase 9 owns deeper exec tests).

## File Classification

| New/Modified File | Role | Data Flow | Closest Analog | Match Quality |
|-------------------|------|-----------|----------------|---------------|
| `modules/step-functions/s3/versions.tf` (new) | config | n/a | `modules/step-functions/efs/versions.tf` | exact (copy verbatim) |
| `modules/step-functions/s3/variables.tf` (new) | config | n/a | `modules/step-functions/efs/variables.tf` | exact (copy verbatim) |
| `modules/step-functions/s3/main.tf` (new) | config | n/a | `modules/step-functions/efs/main.tf` (file()-map + for_each block only) | role-match (drop templatefile/sub-SFN maps; no Lambda) |
| `modules/step-functions/s3/outputs.tf` (new) | config | n/a | `modules/step-functions/efs/outputs.tf` | role-match (single resource map, not 3-way merge) |
| `modules/step-functions/s3/setup_cross_account_replication.asl.json` (new) | state-machine | request-response (CRUD on bucket replication config) | `efs/setup_cross_account_replication.asl.json` | exact (assume-role + existing-config check + Choice + Catch->Fail) |
| `modules/step-functions/s3/delete_replication.asl.json` (new) | state-machine | request-response (read-merge-write teardown) | `efs/delete_replication.asl.json` | exact (NotFound Catch -> idempotent Succeed; empty -> Delete vs Put branch) |
| `modules/step-functions/s3/run_batch_replication.asl.json` (new) | state-machine | event-driven (async job dispatch) | `efs/setup` Task-state shell (assume-role + Catch->Fail) | partial (no EFS analog for `s3control:createJob`; structural skeleton only — see Pattern 2 in RESEARCH) |
| `modules/step-functions/s3/check_batch_replication.asl.json` (new) | state-machine | request-response (polling loop) | `efs/delete_replication.asl.json` `WaitReplicationDeleted`->`CheckReplicationDeleted`->`IsReplicationDeleted` loop | partial (Wait+Task+Choice loop structure; `s3control:describeJob` itself has no EFS analog) |
| `modules/source-account/main.tf` (edit) | iam | n/a | `aws_iam_role_policy.efs_access` (lines 166-220) + `aws_iam_role.efs_replication`/`aws_iam_role_policy.efs_replication` (lines 607-661) | exact |
| `modules/source-account/variables.tf` (edit) | config | n/a | `variable "enable_efs"` (lines 85-89) | exact |
| `modules/source-account/outputs.tf` (edit) | config | n/a | `output "efs_replication_role_arn"` (lines 53-61) | exact |
| `tests/*` (no file change) | test | n/a | `tests/test_asl_validation.py:18` `rglob("*.asl.json")` | auto-discovery — NO wiring needed |

## Pattern Assignments

### `modules/step-functions/s3/versions.tf` (config)

**Analog:** `modules/step-functions/efs/versions.tf` — copy verbatim.
```hcl
terraform {
  required_version = ">= 1.0"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = ">= 5.0"
    }
  }
}
```

---

### `modules/step-functions/s3/variables.tf` (config)

**Analog:** `modules/step-functions/efs/variables.tf` lines 1-44 — copy verbatim. All 7 variables transfer unchanged: `prefix`, `tags`, `orchestrator_role_arn`, `enable_logging` (default true), `log_retention_days` (default 30), `enable_xray_tracing` (default false), `naming_convention` (default "pascal", validated `contains(["pascal","kebab"])`). Adjust only the `naming_convention` doc-string examples to S3.

**NB:** Do NOT copy `sync/variables.tf` — that one carries Lambda vars (`lambda_role_arn`, `cross_account_role_arns`, `log_level`) which D-09 forbids here.

---

### `modules/step-functions/s3/main.tf` (config)

**Analog:** `modules/step-functions/efs/main.tf` — but use ONLY the plain `file()` map + the single `aws_sfn_state_machine.efs` block + the log group. **Drop** the `step_functions_sub`/`step_functions_templated` maps, both `templatefile()` resources, and the `moved` blocks (those exist for EFS's sub-SFN ARN injection, which s3/ does not have).

**locals map + naming** (mirror efs/main.tf lines 6-47, S3-flavored):
```hcl
locals {
  step_functions = {
    setup_cross_account_replication = "setup_cross_account_replication.asl.json"
    run_batch_replication           = "run_batch_replication.asl.json"
    check_batch_replication         = "check_batch_replication.asl.json"
    delete_replication              = "delete_replication.asl.json"
  }
  sfn_names = {
    for k, v in local.step_functions : k => (
      var.naming_convention == "pascal"
      ? "${var.prefix}-S3-${replace(title(replace(k, "_", " ")), " ", "")}"
      : "${var.prefix}-s3-${replace(k, "_", "-")}"
    )
  }
}
```

**Core resource block** (copy efs/main.tf lines 53-75 verbatim, rename `efs`->`s3`, `Module = "s3"`):
```hcl
resource "aws_sfn_state_machine" "s3" {
  for_each = local.step_functions

  name     = local.sfn_names[each.key]
  role_arn = var.orchestrator_role_arn

  definition = file("${path.module}/${each.value}")

  logging_configuration {
    log_destination        = var.enable_logging ? "${aws_cloudwatch_log_group.sfn[0].arn}:*" : null
    include_execution_data = var.enable_logging
    level                  = var.enable_logging ? "ALL" : "OFF"
  }

  tracing_configuration {
    enabled = var.enable_xray_tracing
  }

  tags = merge(var.tags, {
    Module = "s3"
    Name   = local.sfn_names[each.key]
  })
}
```

**Log group** (copy efs/main.tf lines 160-167, name `/aws/stepfunctions/${var.prefix}-s3`):
```hcl
resource "aws_cloudwatch_log_group" "sfn" {
  count             = var.enable_logging ? 1 : 0
  name              = "/aws/stepfunctions/${var.prefix}-s3"
  retention_in_days = var.log_retention_days
  tags              = var.tags
}
```

---

### `modules/step-functions/s3/outputs.tf` (config)

**Analog:** `modules/step-functions/efs/outputs.tf` — but collapse the 3-way `merge()` (efs has efs/efs_sub_templated/efs_templated) to a single map since s3/ has one `aws_sfn_state_machine.s3` resource:
```hcl
output "step_function_arns" {
  description = "Map of Step Function names to ARNs"
  value       = { for k, v in aws_sfn_state_machine.s3 : k => v.arn }
}
output "step_function_names" {
  description = "Map of Step Function keys to actual names"
  value       = { for k, v in aws_sfn_state_machine.s3 : k => v.name }
}
output "log_group_arn" {
  description = "ARN of the CloudWatch log group"
  value       = var.enable_logging ? aws_cloudwatch_log_group.sfn[0].arn : null
}
```

---

### `modules/step-functions/s3/setup_cross_account_replication.asl.json` (state-machine, CRUD on replication config)

**Analog:** `efs/setup_cross_account_replication.asl.json` + `efs/delete_replication.asl.json`. Strip ALL the EFS cross-region-proxy / Lambda-invoke branching (`CheckProxyFor*`, `arn:aws:states:::lambda:invoke`) — s3/ is same-region, no Lambda. Keep the imperative-assume-role + existing-config-check + Choice-guard + Catch->Fail skeleton.

**Imperative assume-role Task pattern** (efs setup lines 108-127 — the canonical shape to copy for every S3 Task; swap Resource to `arn:aws:states:::aws-sdk:s3:getBucketReplication`, params to `{"Bucket.$": "$.SourceBucket"}`):
```json
"CheckExistingReplication": {
  "Type": "Task",
  "Resource": "arn:aws:states:::aws-sdk:efs:describeReplicationConfigurations",
  "Parameters": { "FileSystemId.$": "$.SourceFileSystemId" },
  "Credentials": { "RoleArn.$": "$.SourceAccount.RoleArn" },
  "ResultPath": "$.ExistingReplication",
  "Next": "HasExistingReplication",
  "Catch": [
    { "ErrorEquals": ["States.ALL"], "ResultPath": "$.CheckError", "Next": "CheckProxyForValidateSource" }
  ]
}
```

**Existing-config Choice guard** (efs setup lines 128-138 — mirror for `GetBucketReplication` NotFound seeding and versioning gate; note the `IsPresent`/`Default` idiom):
```json
"HasExistingReplication": {
  "Type": "Choice",
  "Choices": [
    { "Variable": "$.ExistingReplication.Replications[0]", "IsPresent": true, "Next": "ExtractExistingReplication" }
  ],
  "Default": "CheckProxyForValidateSource"
}
```
For S3, the versioning gate is a Choice on `$.Versioning.Status StringEquals "Enabled"` -> proceed, `Default` -> a `Fail` state (validate-only, D-03 — never mutate source). Use this same Choice idiom.

**Catch->Fail terminal states** (efs setup lines ~286-291; same in delete lines 369-375):
```json
"SetupFailed": { "Type": "Fail", "Error": "...", "Cause": "..." },
"SetupSucceeded": { "Type": "Succeed" }
```

**Rule merge (JSONata $filter/concat)** — the EFS `CleanSourcePolicyJSONata` state (delete_replication lines 219-227) is the direct analog for the read-merge-write rule array logic. It uses `$filter($stmts, function($s){ $not($s.Sid in $sids) })` + array concat in an `Assign` block. S3 setup replaces this Sid-filter with a Rule-ID filter (`$filter(Rules, fn($r){ $r.ID != conv(dest) })` then concat the new Rule):
```json
"CleanSourcePolicyJSONata": {
  "Type": "Pass",
  "QueryLanguage": "JSONata",
  "Assign": {
    "cleaned": "{% ($stmts := ...; $filtered := $filter($stmts, function($s){ $not($s.Sid in $sids) }); ...) %}",
    "empty": "{% ($count(...) = 0) %}"
  },
  "Next": "CheckCleanedPolicyEmpty"
}
```

**Resource-key casing rule** (verified across EFS ASL): action in Resource ARN is **camelCase** (`getBucketReplication`); `Parameters` keys are **PascalCase** (`Bucket`, `ReplicationConfiguration`, `Role`, `Rules`).

---

### `modules/step-functions/s3/delete_replication.asl.json` (state-machine, read-merge-write teardown)

**Analog:** `efs/delete_replication.asl.json` (read in full). Strip cross-region/proxy branches; keep the structural backbone:

**Idempotent NotFound Catch -> Succeed-ish** (delete lines 12-36 + 354-362 — the `NoReplicationExists` Pass): `GetReplicationConfig` Catches the not-found exception and routes to a benign `NoReplicationExists` Pass -> `Succeed`. S3 mirror: Catch `S3.ReplicationConfigurationNotFoundError`/`S3.S3Exception` -> `NoReplicationExists`.
```json
"GetReplicationConfig": {
  "Type": "Task",
  "Resource": "arn:aws:states:::aws-sdk:efs:describeReplicationConfigurations",
  "Parameters": { "FileSystemId.$": "$.DescribeFileSystemId" },
  "Credentials": { "RoleArn.$": "$.DeletionRoleArn" },
  "ResultPath": "$.ReplicationConfig",
  "Next": "HasReplicationConfig",
  "Catch": [
    { "ErrorEquals": ["Efs.ReplicationNotFoundException"], "ResultPath": "$.Error", "Next": "NoReplicationExists" },
    { "ErrorEquals": ["States.ALL"], "ResultPath": "$.Error", "Next": "DeleteFailed" }
  ]
}
```

**Empty-after-filter branch: Delete vs Put** (delete lines 229-283 — `CheckCleanedPolicyEmpty` Choice -> `DeleteSourcePolicy` (entire delete) vs `PutCleanedSourcePolicy`). This is the EXACT pattern D-04 needs: after filtering out the removed destinations' Rules, if `Rules` empty -> `deleteBucketReplication`; else `putBucketReplication` with remaining Rules.
```json
"CheckCleanedPolicyEmpty": {
  "Type": "Choice",
  "Choices": [ { "Variable": "$cleanedPolicyEmpty", "BooleanEquals": true, "Next": "DeleteSourcePolicy" } ],
  "Default": "PutCleanedSourcePolicy"
}
```

---

### `modules/step-functions/s3/run_batch_replication.asl.json` (state-machine, async job dispatch)

**Analog (partial):** No EFS analog exists for `s3control:createJob`. Map the **structural shell** from any EFS Task: imperative `Credentials.RoleArn.$` + `Catch->Fail`. The exact CreateJob `Parameters` block (AccountId, ClientRequestToken via `States.UUID()`, `Operation:{S3ReplicateObject:{}}`, `Report`, `ManifestGenerator.S3JobManifestGenerator`, `ConfirmationRequired:false`) is **verified in RESEARCH.md Pattern 2** — use that shape, NOT an EFS one. Wrap it in the EFS Task error-handling shell:
```json
"CreateBatchJob": {
  "Type": "Task",
  "Resource": "arn:aws:states:::aws-sdk:s3control:createJob",
  "Parameters": { /* per RESEARCH Pattern 2 — AccountId, ClientRequestToken States.UUID(), Operation, Report, ManifestGenerator */ },
  "Credentials": { "RoleArn.$": "$.SourceAccount.RoleArn" },
  "ResultSelector": { "JobId.$": "$.JobId" },
  "ResultPath": "$.BatchJob",
  "Next": "PrepareOutput",
  "Catch": [ { "ErrorEquals": ["States.ALL"], "ResultPath": "$.Error", "Next": "BatchJobFailed" } ]
}
```
`BatchJobFailed` = `Fail` state; chain into a `Succeed`. (`ResultSelector`/`ResultPath` idiom present throughout EFS ASL.)

---

### `modules/step-functions/s3/check_batch_replication.asl.json` (state-machine, polling loop)

**Analog (partial):** The `s3control:describeJob` call has no EFS analog, but the **Wait + Task + Choice polling loop** does — `efs/delete_replication.asl.json` lines 103-149 (`WaitReplicationDeleted` -> `CheckReplicationDeleted` -> `IsReplicationDeleted`, with the Choice routing back to `WaitReplicationDeleted` on a non-terminal status and `Default` to a terminal path). Copy this loop structure exactly; swap the Task to `describeJob` and the Choice terminals to the `Job.Status` enum (per RESEARCH Pattern 3 / D-08: `Complete`->Succeed, `Failed`/`Cancelled`->Fail, `Default`->Wait):
```json
"WaitReplicationDeleted": { "Type": "Wait", "Seconds": 10, "Next": "CheckReplicationDeleted" },
"CheckReplicationDeleted": {
  "Type": "Task",
  "Resource": "arn:aws:states:::aws-sdk:efs:describeReplicationConfigurations",
  "Parameters": { "FileSystemId.$": "$.DescribeFileSystemId" },
  "Credentials": { "RoleArn.$": "$.DeletionRoleArn" },
  "ResultPath": "$.CheckResult",
  "Next": "IsReplicationDeleted",
  "Catch": [ /* ... */ ]
},
"IsReplicationDeleted": {
  "Type": "Choice",
  "Choices": [
    { "Variable": "$.CheckResult.Replications[0].Destinations[0].Status", "StringEquals": "DELETING", "Next": "WaitReplicationDeleted" }
  ],
  "Default": "CheckSkipSourcePolicyCleanup"
}
```
S3 mapping: Choice terminals on `$.JobStatus.Job.Status` (`Complete`/`Failed`/`Cancelled`), `Default` -> `WaitForJob`. Polling interval/timeout/backoff are D-11 plan discretion.

---

### `modules/source-account/main.tf` (iam — add s3_access policy + s3_replication role/policy)

**Analog 1 — orchestrator-assumed source policy:** `aws_iam_role_policy.efs_access` (lines 166-220). Mirror the **count guard** and the **`PassRoleToEFSReplication` Sid** (lines 199-209) — the single most important excerpt to copy:
```hcl
resource "aws_iam_role_policy" "efs_access" {
  count = local.should_attach_policies && var.enable_efs ? 1 : 0
  name  = "${local.prefixes.iam_policy}-efs-access"
  role  = local.role_id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      # ... EFSRead / EFSReplication Sids ...
      {
        Sid      = "PassRoleToEFSReplication"
        Effect   = "Allow"
        Action   = ["iam:PassRole"]
        Resource = "arn:aws:iam::${local.account_id}:role/${local.prefixes.iam_role}-efs-replication-role"
        Condition = {
          StringEquals = { "iam:PassedToService" = "elasticfilesystem.amazonaws.com" }
        }
      }
    ]
  })
}
```
S3 version (`aws_iam_role_policy.s3_access`, `count = local.should_attach_policies && var.enable_s3 ? 1 : 0`): Sids per D-10/RESEARCH Security Domain — `S3ReplicationManage` (`s3:PutBucketReplication`/`GetBucketReplication`/`GetBucketVersioning`/`DeleteBucketReplication`), `S3BatchControl` (`s3:CreateJob`/`s3:DescribeJob` — note: s3control maps to the `s3:` IAM namespace), and `PassRoleToS3Replication` with `iam:PassedToService = ["s3.amazonaws.com","batchoperations.s3.amazonaws.com"]` targeting `${local.prefixes.iam_role}-s3-replication-role`.

**Analog 2 — combined replication role + its policy:** `aws_iam_role.efs_replication` + `aws_iam_role_policy.efs_replication` (lines 607-661). Mirror the `count = var.enable_efs ? 1 : 0` guard and the trust-policy shape — but the S3 role trusts **BOTH** service principals (D-10):
```hcl
resource "aws_iam_role" "efs_replication" {
  count = var.enable_efs ? 1 : 0
  name  = "${local.prefixes.iam_role}-efs-replication-role"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "AllowAssumeByEFSService"
        Effect    = "Allow"
        Principal = { Service = "elasticfilesystem.amazonaws.com" }
        Action    = "sts:AssumeRole"
      }
    ]
  })
  tags = var.tags
}
```
S3 version (`aws_iam_role.s3_replication`, name `${local.prefixes.iam_role}-s3-replication-role`): two trust Statements — `Principal.Service = "s3.amazonaws.com"` and `Principal.Service = "batchoperations.s3.amazonaws.com"`. Permissions policy per RESEARCH Security Domain (b): live-replication actions + `s3:InitiateReplication`/`GetReplicationConfiguration`/`PutInventoryConfiguration` + manifest/report `GetObject`/`PutObject`. Keep Resources `*`/parameterized (generic module; tighten at Phase 8 wiring).

---

### `modules/source-account/variables.tf` (config — add enable_s3)

**Analog:** `variable "enable_efs"` (lines 85-89):
```hcl
variable "enable_efs" {
  description = "Enable EFS-related permissions (for backup copy)"
  type        = bool
  default     = true
}
```
S3 version: `variable "enable_s3"` — **default `false`** (RESEARCH A3: keeps existing deployments plan-noop since S3 is new; confirm at plan if team wants `enable_efs` parity).

---

### `modules/source-account/outputs.tf` (config — add s3_replication outputs)

**Analog:** `output "efs_replication_role_arn"` (lines 53-61):
```hcl
output "efs_replication_role_arn" {
  description = "ARN of the EFS replication role (for cross-account replication)"
  value       = var.enable_efs ? aws_iam_role.efs_replication[0].arn : null
}
```
S3 version: `output "s3_replication_role_arn"` (and `_name`) gated `var.enable_s3 ? aws_iam_role.s3_replication[0].arn : null`.

---

## Shared Patterns

### Imperative cross-account assume-role (apply to ALL 4 S3 SFN Task states)
**Source:** every EFS Task, e.g. `efs/setup_cross_account_replication.asl.json` lines 115-117, `efs/delete_replication.asl.json` lines 19-21.
```json
"Credentials": { "RoleArn.$": "$.SourceAccount.RoleArn" }
```
Source bucket owned by an external stack -> always assume the source role; never run in the SFN's own identity.

### Catch -> named Fail terminal (apply to ALL Task states)
**Source:** `efs/delete_replication.asl.json` lines 90-101 (Catch) + 369-375 (Fail/Succeed terminals).
```json
"Catch": [ { "ErrorEquals": ["States.ALL"], "ResultPath": "$.Error", "Next": "DeleteFailed" } ]
```
Plus a domain-specific Catch listed BEFORE `States.ALL` (e.g. NotFound -> idempotent path). RESEARCH A1: include a broad `States.ALL` fallback alongside the specific S3 error name since the exact mapped error string is unverified.

### Optional-resource count guard (apply to ALL new source-account resources)
**Source:** `efs_access` line 167 (`local.should_attach_policies && var.enable_efs`), `efs_replication` line 608 (`var.enable_efs`).
```hcl
count = local.should_attach_policies && var.enable_s3 ? 1 : 0   # policies on the source role
count = var.enable_s3 ? 1 : 0                                    # standalone replication role
```

### Module Terraform skeleton (apply to all 4 s3/*.tf)
**Source:** `efs/main.tf` (file()-map + for_each + log group), `efs/variables.tf`, `efs/outputs.tf`, `efs/versions.tf`. Naming via `naming_convention` pascal/kebab. `Module = "s3"` tag.

### ASL test auto-discovery (no action needed)
**Source:** `tests/test_asl_validation.py:18` — `PROJECT_ROOT.rglob("*.asl.json")`. The 4 new files are picked up automatically for structural validation. Do NOT add test wiring. Deeper exec tests = Phase 9 (INFRA-04 = ASL validation only, no Lambda tests per D-09).

## No Direct Analog Found

These have no codebase analog for the specific SDK call and must use RESEARCH.md verified shapes (the structural shell is still mapped above):

| File / state | Role | Data Flow | Reason |
|------|------|-----------|--------|
| `run_batch_replication.asl.json` `s3control:createJob` Task | state-machine | event-driven | No EFS state calls `s3control`; CreateJob request shape is in RESEARCH Pattern 2 (VERIFIED against AWS API). Error-handling shell mapped from EFS Task. |
| `check_batch_replication.asl.json` `s3control:describeJob` Task | state-machine | request-response | Same — `Job.Status` enum + poll-loop semantics from RESEARCH Pattern 3. Wait+Task+Choice loop shell mapped from `efs/delete_replication.asl.json` lines 103-149. |

## Metadata

**Analog search scope:** `modules/step-functions/efs/`, `modules/step-functions/sync/`, `modules/source-account/`, `tests/`
**Files scanned:** efs (4 .tf + 2 key .asl.json read fully/structurally), sync/main.tf, source-account (main.tf targeted reads + variables.tf + outputs.tf), test_asl_validation.py
**Pattern extraction date:** 2026-06-17
