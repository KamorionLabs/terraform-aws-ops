---
phase: 04-foundation
plan: 02
subsystem: infra
tags: [terraform, step-functions, lambda, iam, cross-account, sync]

# Dependency graph
requires:
  - phase: 04-foundation/04-01
    provides: "Lambda stub sync_config_items.py + ASL sync_config_items.asl.json"
provides:
  - "Terraform module modules/step-functions/sync/ (SFN + Lambda + IAM + CloudWatch)"
  - "Root wiring in main.tf with cross_account_role_arns"
  - "Root outputs for sync SFN ARNs in all_step_function_arns"
affects: [orchestrator-integration, phase-05-implementation]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Inline Lambda deployment (archive_file) in step-functions sub-module"
    - "IAM STS-only policy for cross-account Lambda (no direct SM/SSM permissions)"

key-files:
  created:
    - modules/step-functions/sync/main.tf
    - modules/step-functions/sync/variables.tf
    - modules/step-functions/sync/outputs.tf
    - modules/step-functions/sync/versions.tf
  modified:
    - main.tf
    - outputs.tf

key-decisions:
  - "Lambda deployed inline (archive_file) like audit/ module, not via lambda-code S3"
  - "IAM policy has only sts:AssumeRole + CloudWatch Logs -- SM/SSM permissions are on cross-account roles"
  - "cross_account_role_arns receives concat(source_role_arns, destination_role_arns) for read+write access"

patterns-established:
  - "Sync module follows exact audit/ pattern: locals, archive_file, lambda, iam, sfn, permission, cloudwatch"
  - "Lambda timeout 300s for SM/SSM operations (vs 60s for audit CloudTrail)"

requirements-completed: [INFRA-01]

# Metrics
duration: 2min
completed: 2026-03-16
---

# Phase 4 Plan 2: Sync Module Terraform Summary

**Terraform module modules/step-functions/sync/ deploying SFN SyncConfigItems + Lambda sync_config_items with IAM STS-only cross-account policy, wired in root main.tf and outputs.tf**

## Performance

- **Duration:** 2 min
- **Started:** 2026-03-16T16:30:44Z
- **Completed:** 2026-03-16T16:33:13Z
- **Tasks:** 2
- **Files modified:** 6

## Accomplishments
- Complete Terraform module sync/ with main.tf (SFN + Lambda inline + IAM + CloudWatch + permission), variables.tf, outputs.tf, versions.tf
- Lambda IAM policy restricted to sts:AssumeRole and CloudWatch Logs only (no SM/SSM -- permissions are on cross-account assumed roles)
- Root main.tf wires sync module with cross_account_role_arns from both source and destination accounts
- Root outputs.tf exports sync ARNs and includes sync in all_step_function_arns consolidated map

## Task Commits

Each task was committed atomically:

1. **Task 1: Module Terraform sync/ (main.tf, variables.tf, outputs.tf, versions.tf)** - `82cc54a` (feat)
2. **Task 2: Wiring root main.tf + outputs.tf** - `7b86cfa` (feat)

## Files Created/Modified
- `modules/step-functions/sync/main.tf` - SFN + Lambda inline + IAM STS-only + CloudWatch + lambda_permission
- `modules/step-functions/sync/variables.tf` - Standard module variables (prefix, orchestrator_role_arn, cross_account_role_arns, etc.)
- `modules/step-functions/sync/outputs.tf` - step_function_arns, sync_config_items_arn, lambda_function_arn, lambda_role_arn
- `modules/step-functions/sync/versions.tf` - terraform >= 1.0, aws >= 5.0, archive >= 2.0
- `main.tf` - Added module "step_functions_sync" block with cross_account_role_arns
- `outputs.tf` - Added step_functions_sync output + sync key in all_step_function_arns

## Decisions Made
- Lambda deployed inline via archive_file (same pattern as audit/ module) rather than via lambda-code S3 module -- the Lambda runs in the orchestrator account and is invoked directly by the SFN
- IAM policy contains only sts:AssumeRole + CloudWatch Logs -- SM/SSM permissions exist on the cross-account roles (source-account and destination-account modules)
- Lambda timeout set to 300s (5 minutes) vs audit's 60s -- SM/SSM operations are slower than CloudTrail lookups

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered
None

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- Phase 4 Foundation complete: Lambda stub + ASL (04-01) and Terraform module + root wiring (04-02) are done
- Module is ready for terraform plan/apply once connected to a real AWS environment
- Phase 5 can implement the actual Lambda logic (fetch/transform/write) without any infrastructure changes

## Self-Check: PASSED

All files verified present. All commits verified in git log.

---
*Phase: 04-foundation*
*Completed: 2026-03-16*
