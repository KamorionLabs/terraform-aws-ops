---
phase: 02-refactoring
plan: 01
subsystem: infra
tags: [step-functions, asl, efs, templatefile, sub-sfn, refactoring]

# Dependency graph
requires:
  - phase: 01-extraction
    provides: ManageLambdaLifecycle, ManageAccessPoint, ManageFileSystemPolicy sub-SFNs
provides:
  - CheckFlagFileSync sub-SFN (26 states) for flag file replication verification
  - Refactored check_replication_sync (72 -> 27 states) calling sub-SFNs
  - Refactored setup_cross_account_replication (53 -> 45 states) calling ManageFileSystemPolicy
  - Three-tier Terraform resource architecture (efs, efs_sub_templated, efs_templated)
  - Interface snapshot test framework (REF-05) with pre-refactoring baselines for all 5 SFNs
affects: [02-02, 02-03, consolidation]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Three-tier Terraform resource architecture to avoid circular ARN references"
    - "moved blocks for zero-downtime resource address migration"
    - "Interface snapshot testing for output schema non-regression (REF-05)"
    - "SSM parameter materialization pattern for $$.Execution.Input elimination"

key-files:
  created:
    - modules/step-functions/efs/check_flag_file_sync.asl.json
    - tests/test_interface_snapshots.py
    - tests/snapshots/check_replication_sync_outputs.json
    - tests/snapshots/setup_cross_account_replication_outputs.json
    - tests/snapshots/prepare_snapshot_for_restore_outputs.json
    - tests/snapshots/restore_cluster_outputs.json
    - tests/snapshots/refresh_orchestrator_outputs.json
  modified:
    - modules/step-functions/efs/check_replication_sync.asl.json
    - modules/step-functions/efs/setup_cross_account_replication.asl.json
    - modules/step-functions/efs/main.tf
    - modules/step-functions/efs/outputs.tf
    - modules/step-functions/efs/README.md
    - tests/test_asl_validation.py

key-decisions:
  - "Three-tier resource architecture (efs/efs_sub_templated/efs_templated) to avoid circular ARN references between check_flag_file_sync and check_replication_sync"
  - "Materialize $$.Execution.Input.SourceSubpathSSMParameter into state data using States.ArrayGetItem default pattern instead of separate Choice/Pass states"
  - "Destination replication policy uses 3 sequential ManageFileSystemPolicy calls (one per statement) since sub-SFN handles single statement per call"
  - "CheckFlagFileSync sub-SFN at 26 states (vs plan ~21) due to comprehensive cleanup paths for error and timeout scenarios"

patterns-established:
  - "Three-tier Terraform resource pattern: file() -> sub-SFN templatefile() -> caller templatefile()"
  - "moved blocks for declarative resource address migration in Terraform"
  - "Interface snapshot tests: capture output schemas before refactoring, assert unchanged after"
  - "$$.Execution.Input materialization via States.ArrayGetItem default-value pattern"

requirements-completed: [REF-01, REF-02, REF-05]

# Metrics
duration: 13min
completed: 2026-03-13
---

# Phase 2 Plan 1: EFS Module Refactoring Summary

**Refactored EFS ASL files (check_replication_sync 72->27 states, setup_cross_account_replication 53->45 states) with CheckFlagFileSync sub-SFN, three-tier Terraform resource architecture, and interface snapshot tests**

## Performance

- **Duration:** 13 min
- **Started:** 2026-03-13T17:41:35Z
- **Completed:** 2026-03-13T17:54:27Z
- **Tasks:** 3
- **Files modified:** 12

## Accomplishments
- Reduced check_replication_sync from 72 to 27 states by replacing inline Lambda lifecycle, access point, and flag file sync with sub-SFN calls
- Created CheckFlagFileSync sub-SFN (26 states) with 3-level nesting calling ManageLambdaLifecycle and ManageAccessPoint
- Eliminated all $$.Execution.Input references in check_replication_sync via materialization pattern
- Established three-tier Terraform resource architecture with moved blocks for zero-downtime migration
- Created interface snapshot test framework covering all 5 SFNs being refactored in Phase 2

## Task Commits

Each task was committed atomically:

1. **Task 1: Create interface snapshot tests + CheckFlagFileSync sub-SFN** - `d10b8e5` (feat)
2. **Task 2: Refactor check_replication_sync.asl.json** - `7225d0a` (feat)
3. **Task 3: Refactor setup_cross_account_replication + Terraform dual-map + outputs + README** - `4eb286c` (feat)

## Files Created/Modified
- `modules/step-functions/efs/check_flag_file_sync.asl.json` - New sub-SFN for flag file sync verification (26 states)
- `modules/step-functions/efs/check_replication_sync.asl.json` - Refactored from 72 to 27 states
- `modules/step-functions/efs/setup_cross_account_replication.asl.json` - Refactored from 53 to 45 states
- `modules/step-functions/efs/main.tf` - Three-tier resource architecture with moved blocks
- `modules/step-functions/efs/outputs.tf` - Merged outputs from all three resource maps
- `modules/step-functions/efs/README.md` - Added CheckFlagFileSync docs + integration pattern
- `tests/test_interface_snapshots.py` - Interface non-regression tests (REF-05)
- `tests/test_asl_validation.py` - Expanded sub-SFN discovery across modules
- `tests/snapshots/*.json` - Pre-refactoring output schema baselines (5 files)

## Decisions Made
- **Three-tier resource architecture:** check_flag_file_sync is both a sub-SFN (needs Phase 1 ARNs) and is called by check_replication_sync (which is also templated). Putting both in the same for_each would create a self-reference cycle. Solution: three separate resources (efs, efs_sub_templated, efs_templated).
- **SSM materialization pattern:** Instead of keeping SetSubpathDefaults/ApplySubpathSSMParams/MergeSubpathParams intermediary states, materialized $$.Execution.Input values directly in InitializeState using `States.ArrayGetItem(States.Array($.SourceSubpathSSMParameter, ''), 0)` for default values. This eliminated 4 states.
- **3 sequential ManageFileSystemPolicy calls for destination:** The destination replication policy requires 3 separate IAM statements (AllowCrossAccountReplication, AllowReplicationRole, AllowReplicationService). Since ManageFileSystemPolicy handles one statement per call, 3 sequential calls are needed. Parallel execution was rejected due to read-modify-write race conditions on the policy document.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] Three-tier resource architecture instead of dual-map**
- **Found during:** Task 3 (Terraform dual-map migration)
- **Issue:** Plan specified dual resource maps (step_functions + step_functions_templated) but check_flag_file_sync is both in step_functions_templated AND referenced by check_replication_sync in the same map, creating a Terraform self-reference cycle
- **Fix:** Created three-tier architecture: efs (file()), efs_sub_templated (Phase 2 sub-SFNs), efs_templated (refactored callers)
- **Files modified:** modules/step-functions/efs/main.tf, modules/step-functions/efs/outputs.tf
- **Verification:** Terraform fmt passes, no circular references

**2. [Rule 1 - Bug] setup_cross_account_replication needs 3 destination policy calls instead of 1**
- **Found during:** Task 3 (setup_cross_account_replication refactoring)
- **Issue:** ManageFileSystemPolicy handles one PolicyStatement per call, but destination replication requires 3 statements. Plan assumed 2 total calls (1 source + 1 destination)
- **Fix:** Used 3 sequential ManageFileSystemPolicy calls for destination (AllowCrossAccountReplication, AllowReplicationRole, AllowReplicationService), resulting in 45 states instead of target ~39-41
- **Files modified:** modules/step-functions/efs/setup_cross_account_replication.asl.json
- **Verification:** Interface snapshot test passes, all ASL validation tests pass

---

**Total deviations:** 2 auto-fixed (1 blocking, 1 bug)
**Impact on plan:** Both deviations were necessary for correctness. Three-tier architecture prevents Terraform cycles. Sequential policy calls prevent race conditions. setup_cross_account_replication ended at 45 states (vs target 39-41) due to 3 destination policy calls instead of 1.

## Issues Encountered
None

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- EFS module refactoring complete, ready for Plan 02-02 (DB module)
- Interface snapshot baselines captured for prepare_snapshot_for_restore and restore_cluster
- Three-tier resource pattern established as reference for DB module migration
- TestASLCatchSelfContained already discovers future DB sub-SFN files (ensure_snapshot_available, cluster_switch_sequence)

## Self-Check: PASSED

All 10 key files verified present. All 3 task commits verified in git log.

---
*Phase: 02-refactoring*
*Completed: 2026-03-13*
