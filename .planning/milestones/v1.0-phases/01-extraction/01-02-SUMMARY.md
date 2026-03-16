---
phase: 01-extraction
plan: 02
subsystem: step-functions
tags: [step-functions, asl, efs, terraform, jsonata, filesystem-policy]

# Dependency graph
requires:
  - phase: 01-extraction plan 01
    provides: strip_credentials helper, TestASLComment, TestASLCatchSelfContained, extended CI matrix
provides:
  - ManageFileSystemPolicy sub-SFN (12 states, ADD/REMOVE actions on EFS FileSystem policies)
  - 3 sub-SFN keys registered in local.step_functions (manage_filesystem_policy, manage_access_point, manage_lambda_lifecycle)
  - Stub ASL files for manage_access_point and manage_lambda_lifecycle (Plan 03)
  - README.md with I/O contracts and templatefile() integration pattern for Phase 2
affects: [01-03-PLAN, Phase 2 refactoring]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Sub-SFN ASL pattern: Comment I/O contract, self-contained Catch, named Fail error"
    - "JSONata policy manipulation: filter/append statements by Sid for idempotent merge/remove"
    - "Stub ASL files: minimal valid SFN with NotImplemented Fail state for Terraform plan compatibility"

key-files:
  created:
    - modules/step-functions/efs/manage_filesystem_policy.asl.json
    - modules/step-functions/efs/manage_access_point.asl.json
    - modules/step-functions/efs/manage_lambda_lifecycle.asl.json
    - modules/step-functions/efs/README.md
  modified:
    - modules/step-functions/efs/main.tf

key-decisions:
  - "ManageFileSystemPolicy uses Action parameter (ADD/REMOVE) to handle both setup and cleanup in one sub-SFN"
  - "Empty policy after REMOVE triggers deleteFileSystemPolicy instead of putting empty Statement array"
  - "Stubs created for manage_access_point and manage_lambda_lifecycle to keep Terraform plan valid"

patterns-established:
  - "Sub-SFN Comment format: Name | Input: {fields} | Output: {fields}"
  - "Sub-SFN error naming: {PascalName}Failed (e.g., ManageFileSystemPolicyFailed)"
  - "Policy merge via JSONata: filter by Sid, append new statement, stringify"

requirements-completed: [SUB-03, SUB-04, SUB-05, SUB-06, PRE-02]

# Metrics
duration: 2min
completed: 2026-03-13
---

# Phase 1 Plan 02: Extraction ManageFileSystemPolicy Summary

**ManageFileSystemPolicy sub-SFN with 12 states handling ADD/REMOVE policy actions, 3 sub-SFN keys in main.tf, and README with I/O contracts and templatefile() integration pattern**

## Performance

- **Duration:** 2 min
- **Started:** 2026-03-13T11:33:44Z
- **Completed:** 2026-03-13T11:35:53Z
- **Tasks:** 2
- **Files modified:** 5

## Accomplishments
- ManageFileSystemPolicy sub-SFN extracted from setup_cross_account_replication and delete_replication with generalized ADD/REMOVE behavior
- 3 sub-SFN keys registered in local.step_functions (for_each auto-deploys, outputs auto-export ARNs)
- README.md documents I/O contract, Catch behavior, templatefile() pattern, and naming conventions
- All 939 ASL validation tests pass including 3 new files (auto-discovered via rglob)

## Task Commits

Each task was committed atomically:

1. **Task 1: Create manage_filesystem_policy.asl.json** - `2a93777` (feat)
2. **Task 2: Register 3 sub-SFN keys in main.tf, create stubs, and README.md** - `3b7a1f1` (feat)

## Files Created/Modified
- `modules/step-functions/efs/manage_filesystem_policy.asl.json` - Complete sub-SFN with 12 states for ADD/REMOVE policy actions
- `modules/step-functions/efs/manage_access_point.asl.json` - Stub (NotImplemented, Plan 03)
- `modules/step-functions/efs/manage_lambda_lifecycle.asl.json` - Stub (NotImplemented, Plan 03)
- `modules/step-functions/efs/README.md` - I/O contracts, integration pattern, naming docs
- `modules/step-functions/efs/main.tf` - Added 3 keys in local.step_functions

## Decisions Made
- ManageFileSystemPolicy uses a single `Action` parameter (ADD/REMOVE) rather than separate sub-SFNs for add and remove -- this mirrors how both setup_cross_account_replication (ADD) and delete_replication (REMOVE) use the same underlying API pattern
- When REMOVE removes the last statement, the sub-SFN calls `deleteFileSystemPolicy` instead of putting an empty Statement array -- matches the pattern in delete_replication.asl.json
- Stub ASL files created immediately for manage_access_point and manage_lambda_lifecycle to keep Terraform plan valid (for_each requires all files to exist)

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered
None.

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness
- ManageFileSystemPolicy ready for consumption by Phase 2 refactoring (setup_cross_account_replication, delete_replication)
- manage_access_point and manage_lambda_lifecycle stubs ready for Plan 03 implementation
- TestASLCatchSelfContained will auto-validate named Fail errors when Plan 03 replaces stubs
- templatefile() pattern documented in README for Phase 2 callers

## Self-Check: PASSED

All files found, all commits verified.

---
*Phase: 01-extraction*
*Completed: 2026-03-13*
