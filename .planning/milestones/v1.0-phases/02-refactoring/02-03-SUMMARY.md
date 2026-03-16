---
phase: 02-refactoring
plan: 03
subsystem: infra
tags: [step-functions, asl, orchestrator, db, sub-sfn, refactoring, cluster-switch]

# Dependency graph
requires:
  - phase: 01-extraction
    provides: Phase 1 sub-SFN patterns (named Fail, self-contained Catch, Comment I/O contract)
  - phase: 02-refactoring
    plan: 02
    provides: Dual-map Terraform resource architecture for DB module, EnsureSnapshotAvailable sub-SFN, interface snapshot test framework (REF-05)
provides:
  - ClusterSwitchSequence sub-SFN (12 states) for cluster rename/delete/tag sequence
  - Refactored refresh_orchestrator (51 -> 42 states) calling ClusterSwitchSequence
  - Interface snapshot tests pass for refresh_orchestrator (REF-05)
affects: [consolidation]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Flat ARN template variables in ASL (avoids map lookup syntax breaking JSON)"
    - "ClusterSwitchSequence as self-contained cluster switch sub-SFN with Options-based branching"

key-files:
  created:
    - modules/step-functions/db/cluster_switch_sequence.asl.json
  modified:
    - modules/step-functions/orchestrator/refresh_orchestrator.asl.json
    - modules/step-functions/orchestrator/main.tf
    - modules/step-functions/db/main.tf
    - modules/step-functions/db/README.md

key-decisions:
  - "Flat ARN template variable (cluster_switch_sequence_arn) instead of map lookup syntax (db_step_functions[\"...\"]]) to keep raw ASL file as valid JSON for tests"
  - "ClusterSwitchSequence at 12 states (not 10) -- includes PrepareOutput Pass and SwitchComplete Succeed for proper output formatting"
  - "refresh_orchestrator at 42 states (not ~30) -- only cluster switch extraction performed, additional Choice simplification deferred"

patterns-established:
  - "Flat ARN template variable pattern: pass individual sub-SFN ARNs as named variables to avoid JSON-breaking map lookup syntax in templatefile()"
  - "ClusterSwitchSequence as reusable sub-SFN receiving other sub-SFN ARNs via input ($.StepFunctions.*) not template variables"

requirements-completed: [REF-03, REF-05]

# Metrics
duration: 5min
completed: 2026-03-13
---

# Phase 2 Plan 3: Orchestrator Module Refactoring Summary

**ClusterSwitchSequence sub-SFN (12 states) extracting cluster rename/delete/tag sequence, refresh_orchestrator refactored from 51 to 42 states with template variable ARN injection**

## Performance

- **Duration:** 5 min
- **Started:** 2026-03-13T18:48:06Z
- **Completed:** 2026-03-13T18:53:00Z
- **Tasks:** 2
- **Files modified:** 5

## Accomplishments
- Created ClusterSwitchSequence sub-SFN (12 states) with named Fail state, self-contained Catch, Comment I/O contract
- Refactored refresh_orchestrator from 51 to 42 states by replacing 10 inline cluster switch states with single ExecuteClusterSwitchSequence Task
- Established flat ARN template variable pattern for orchestrator ASL (keeps raw JSON valid for tests)
- Interface snapshot test passes for refresh_orchestrator (REF-05)
- Full test suite green (988 passed)

## Task Commits

Each task was committed atomically:

1. **Task 1: Create ClusterSwitchSequence sub-SFN + register in DB module** - `5e7fa0e` (feat)
2. **Task 2: Refactor refresh_orchestrator to call ClusterSwitchSequence** - `0159ccc` (feat)

## Files Created/Modified
- `modules/step-functions/db/cluster_switch_sequence.asl.json` - New sub-SFN for cluster switch sequence (12 states)
- `modules/step-functions/db/main.tf` - Registered cluster_switch_sequence in local.step_functions map
- `modules/step-functions/db/README.md` - ClusterSwitchSequence I/O contract, behavior, catch pattern, naming table
- `modules/step-functions/orchestrator/refresh_orchestrator.asl.json` - Replaced 10 inline states with single ExecuteClusterSwitchSequence Task (51 -> 42 states)
- `modules/step-functions/orchestrator/main.tf` - Added cluster_switch_sequence_arn flat template variable

## Decisions Made
- **Flat ARN template variable pattern:** Used `cluster_switch_sequence_arn` as a flat string variable instead of `db_step_functions["cluster_switch_sequence"]` map lookup. The map lookup syntax contains double quotes that break JSON parsing of the raw ASL file. Flat variables (like `${cluster_switch_sequence_arn}`) are valid within JSON string values. This matches the pattern used by EFS module's templated ASL files (`${check_flag_file_sync_arn}`, etc.).
- **ClusterSwitchSequence at 12 states (not 10):** Added PrepareOutput (Pass) and SwitchComplete (Succeed) for proper output formatting with SwitchCompletedAt timestamp. The sub-SFN is self-contained -- parent makes one call.
- **refresh_orchestrator at 42 states (not ~30):** The plan target of ~30 would require additional Choice state simplification beyond the cluster switch extraction. The "simplifiant les Choice states" mentioned in ROADMAP would need merging conditional chains, which is deferred. The ~38-42 range was the realistic target per research.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] JSON-valid template variable syntax for ASL files**
- **Found during:** Task 2 (refactoring refresh_orchestrator)
- **Issue:** Using `${db_step_functions["cluster_switch_sequence"]}` in ASL JSON broke JSON parsing because the double quotes inside the template expression conflict with JSON string delimiters. All ASL validation tests failed.
- **Fix:** Added flat template variable `cluster_switch_sequence_arn` in orchestrator main.tf, used `${cluster_switch_sequence_arn}` in ASL file instead.
- **Files modified:** modules/step-functions/orchestrator/main.tf, modules/step-functions/orchestrator/refresh_orchestrator.asl.json
- **Verification:** JSON parsing works, all 988 tests pass
- **Committed in:** 0159ccc (Task 2 commit)

---

**Total deviations:** 1 auto-fixed (1 blocking)
**Impact on plan:** Template variable syntax adapted for JSON compatibility. No scope creep.

## Issues Encountered
None beyond the template variable syntax issue documented above.

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- Phase 2 refactoring complete across all 3 modules (EFS, DB, Orchestrator)
- All sub-SFN patterns established: ManageLambdaLifecycle, ManageAccessPoint, ManageFileSystemPolicy, CheckFlagFileSync, EnsureSnapshotAvailable, ClusterSwitchSequence
- Ready for Phase 3 (Consolidation) to merge pub/priv variants and finalize
- TestASLCatchSelfContained discovers all 6 sub-SFN files automatically

## Self-Check: PASSED
