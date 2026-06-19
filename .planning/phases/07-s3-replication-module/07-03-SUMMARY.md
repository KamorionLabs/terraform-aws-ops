---
phase: 07-s3-replication-module
plan: 03
subsystem: infra
tags: [terraform, iam, s3, s3-replication, s3control, batch-operations, cross-account]

# Dependency graph
requires:
  - phase: source-account (existing EFS pattern)
    provides: aws_iam_role_policy.efs_access + aws_iam_role.efs_replication pattern (count guard, PassRole condition, optional toggle)
provides:
  - var.enable_s3 feature toggle (default false) gating all S3 replication IAM
  - s3_access policy on the orchestrator-assumed source role (replication management + batch control + scoped PassRole)
  - combined s3_replication role trusted by s3.amazonaws.com and batchoperations.s3.amazonaws.com
  - s3_replication permissions policy (live replication + InitiateReplication/GetReplicationConfiguration/PutInventoryConfiguration)
  - s3_replication_role_arn / s3_replication_role_name outputs
affects: [08-orchestrator-integration, client-wiring (NewHorizon-IaC-AWS-Refresh)]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Optional IAM via count = ... && var.enable_s3 ? 1 : 0 (mirror enable_efs)"
    - "Scoped iam:PassRole via iam:PassedToService condition listing both S3 service principals (no wildcard)"
    - "Combined assume-role trust policy (two sts:AssumeRole statements for two service principals)"

key-files:
  created: []
  modified:
    - modules/source-account/variables.tf
    - modules/source-account/main.tf
    - modules/source-account/outputs.tf

key-decisions:
  - "enable_s3 defaults false (S3 is new; keeps current consumers plan-noop per A3/D-10), unlike enable_efs which defaults true"
  - "s3control:CreateJob/DescribeJob expressed as s3:CreateJob/s3:DescribeJob (s3control maps to the s3: IAM namespace)"
  - "Module-level Resources kept */arn:aws:s3:::* by design (generic opensource module); concrete bucket/KMS ARN tightening is Phase 8 client wiring (T-07-09 accepted)"

patterns-established:
  - "PassRoleToS3Replication: iam:PassRole scoped to the -s3-replication-role ARN with iam:PassedToService = [s3, batchoperations.s3]"
  - "Combined replication role trust: AllowAssumeByS3Service + AllowAssumeByS3BatchOperations"

requirements-completed: [IAM-01, IAM-02]

# Metrics
duration: 8min
completed: 2026-06-19
---

# Phase 07 Plan 03: Source-Account S3 Replication IAM Summary

**Optional `enable_s3`-gated S3 replication IAM in modules/source-account/: a scoped-PassRole source policy, a combined-trust replication role, its permissions policy, and outputs — mirroring the EFS pattern.**

## Performance

- **Duration:** ~8 min
- **Started:** 2026-06-19
- **Completed:** 2026-06-19
- **Tasks:** 3
- **Files modified:** 3

## Accomplishments
- Added `var.enable_s3` toggle (type bool, default false) gating all new S3 IAM (T-07-08 — existing deployments stay plan-noop).
- Added `aws_iam_role_policy.s3_access` on the orchestrator-assumed source role: `S3ReplicationManage` (PutBucketReplication / GetBucketReplication / GetBucketVersioning / DeleteBucketReplication), `S3BatchControl` (s3:CreateJob / s3:DescribeJob), and `PassRoleToS3Replication` (iam:PassRole scoped to the `-s3-replication-role` ARN, `iam:PassedToService` = both S3 service principals, no wildcard — T-07-01) (IAM-02).
- Added `aws_iam_role.s3_replication` with combined trust for `s3.amazonaws.com` and `batchoperations.s3.amazonaws.com` (T-07-07), plus `aws_iam_role_policy.s3_replication` granting live-replication read/write and `s3:InitiateReplication` / `GetReplicationConfiguration` / `PutInventoryConfiguration` (IAM-01).
- Added `s3_replication_role_arn` and `s3_replication_role_name` outputs, both gated `var.enable_s3 ? ... : null`.

## Task Commits

Each task was committed atomically:

1. **Task 1: Add var.enable_s3 toggle** - `c511057` (feat)
2. **Task 2: Add s3_access policy + s3_replication role + s3_replication policy** - `b5dfe5d` (feat)
3. **Task 3: Add s3_replication outputs** - `162afe9` (feat)

## Files Created/Modified
- `modules/source-account/variables.tf` - Added `variable "enable_s3"` (bool, default false) in the Feature Toggles section.
- `modules/source-account/main.tf` - Added `aws_iam_role_policy.s3_access`, `aws_iam_role.s3_replication`, `aws_iam_role_policy.s3_replication` (all `var.enable_s3`-gated, appended after the EFS replication resources).
- `modules/source-account/outputs.tf` - Added `s3_replication_role_arn` and `s3_replication_role_name` outputs.

## Decisions Made
- Followed plan as specified. `enable_s3` defaults false (vs `enable_efs` default true) per A3/D-10 so current consumers see plan-noop.
- `s3control` actions written under the `s3:` IAM namespace (`s3:CreateJob`, `s3:DescribeJob`) per RESEARCH line 511.
- Resources kept broad (`*` / `arn:aws:s3:::*`) by design — generic module; ARN tightening deferred to Phase 8 (T-07-09 accepted disposition).

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered

**terraform/tofu validation could not be run in this environment.** The Bash tool denied every `terraform`/`tofu` invocation (validate, fmt -check, plan), while `git` commands were permitted. As a result the automated `<verify>` steps for all three tasks (`terraform -chdir=modules/source-account validate`, `terraform fmt -check -recursive modules/source-account`, and the `enable_s3` false/true plan checks) were NOT executed by the agent.

Mitigation applied:
- HCL was authored by mirroring the existing, already-`fmt`-clean EFS resources verbatim (same indentation, attribute alignment, `jsonencode` block shape), minimizing the risk of `fmt`/`validate` failures.
- The new resources reuse only plan-time-safe locals already validated in this module (`local.should_attach_policies`, `local.role_id`, `local.prefixes.*`, `local.account_id`) and the standard `count` guard idiom.

**Required follow-up (manual):** Before merge, run locally:
```
terraform -chdir=modules/source-account validate
terraform fmt -check -recursive modules/source-account
terraform -chdir=modules/source-account plan   # with enable_s3 both false (noop) and true
```

## Must-Haves Verification (static)

- [x] `var.enable_s3` toggle (default false) gates all new S3 IAM resources — variables.tf + `count` on all 3 resources.
- [x] `s3_replication` role trusted by BOTH `s3.amazonaws.com` and `batchoperations.s3.amazonaws.com` — two `sts:AssumeRole` statements.
- [x] Source role gains `s3:PutBucketReplication`, `GetBucketReplication`, `GetBucketVersioning`, `DeleteBucketReplication`, `s3:CreateJob`, `s3:DescribeJob`.
- [x] `iam:PassRole` scoped to the `-s3-replication-role` ARN via `iam:PassedToService` listing both principals — no wildcard PassRole.
- [x] `s3_replication` policy grants `s3:InitiateReplication`, `GetReplicationConfiguration`, `PutInventoryConfiguration` plus live-replication object actions.
- [ ] `terraform validate` / `plan` pass both toggle ways — NOT VERIFIED (Bash denied terraform); see Issues Encountered.

## Next Phase Readiness
- Source-account IAM ready: Phase 8 orchestrator wiring can consume `s3_replication_role_arn` and pass it as `ReplicationRoleArn` to the S3 SFN, and assume the source role (now carrying replication + batch-control + scoped PassRole permissions).
- **Blocker before merge:** run the deferred `terraform validate`/`fmt`/`plan` checks (Bash terraform was unavailable to the executor).

---
*Phase: 07-s3-replication-module*
*Completed: 2026-06-19*
