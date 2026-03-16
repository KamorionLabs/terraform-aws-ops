---
phase: 02-refactoring
plan: 02
subsystem: infra
tags: [step-functions, asl, db, rds, templatefile, sub-sfn, refactoring, snapshot]

# Dependency graph
requires:
  - phase: 01-extraction
    provides: Phase 1 sub-SFN patterns (named Fail, self-contained Catch, Comment I/O contract)
  - phase: 02-refactoring
    plan: 01
    provides: Three-tier Terraform resource pattern, interface snapshot test framework (REF-05), moved blocks pattern
provides:
  - EnsureSnapshotAvailable sub-SFN (8 states) for snapshot wait/verify loops
  - Refactored prepare_snapshot_for_restore (39 -> 33 states) calling EnsureSnapshotAvailable
  - Dual-map Terraform resource architecture for DB module (db + db_templated)
  - Interface snapshot tests pass for both prepare_snapshot_for_restore and restore_cluster (REF-05)
affects: [02-03, consolidation]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Dual-map Terraform resource architecture (db + db_templated) for DB module"
    - "moved blocks for zero-downtime resource address migration in DB module"
    - "EnsureSnapshotAvailable sub-SFN with proxy routing for cross-region snapshots"

key-files:
  created:
    - modules/step-functions/db/ensure_snapshot_available.asl.json
    - modules/step-functions/db/README.md
  modified:
    - modules/step-functions/db/prepare_snapshot_for_restore.asl.json
    - modules/step-functions/db/main.tf
    - modules/step-functions/db/outputs.tf

key-decisions:
  - "Dual-map (not three-tier) for DB module since EnsureSnapshotAvailable has no circular ARN dependency"
  - "restore_cluster refactoring minimal -- no inline snapshot wait loops to extract (cluster wait != snapshot wait)"
  - "prepare_snapshot_for_restore reduced from 39 to 33 states (not 18) -- inline snapshot operations preserved, only wait/verify loops extracted"

patterns-established:
  - "Dual-map Terraform pattern: file() for sub-SFNs + templatefile() for callers"
  - "EnsureSnapshotAvailable as reusable snapshot availability checker with cross-region proxy routing"

requirements-completed: [REF-04, REF-05]

# Metrics
duration: 7min
completed: 2026-03-13
---

# Phase 2 Plan 2: DB Module Refactoring Summary

**EnsureSnapshotAvailable sub-SFN (8 states) with cross-region proxy routing, prepare_snapshot_for_restore refactored to call it, and dual-map Terraform migration for DB module**

## Performance

- **Duration:** 7 min
- **Started:** 2026-03-13T18:18:00Z
- **Completed:** 2026-03-13T18:25:24Z
- **Tasks:** 2
- **Files modified:** 5

## Accomplishments
- Created EnsureSnapshotAvailable sub-SFN (8 states) with proxy routing for cross-region snapshots, named Fail state, self-contained Catch
- Refactored prepare_snapshot_for_restore from 39 to 33 states by replacing inline snapshot wait/verify loops with EnsureSnapshotAvailable sub-SFN calls
- Established dual-map Terraform resource architecture for DB module with moved blocks for zero-downtime migration
- Interface snapshot tests pass for both prepare_snapshot_for_restore and restore_cluster (REF-05)
- Full test suite green (975 passed)

## Task Commits

Each task was committed atomically:

1. **Task 1: Create EnsureSnapshotAvailable sub-SFN + refactor prepare_snapshot_for_restore** - `552ca6f` (feat)
2. **Task 2: DB module Terraform dual-map migration + outputs + README** - `258c4c7` (feat)

## Files Created/Modified
- `modules/step-functions/db/ensure_snapshot_available.asl.json` - New sub-SFN for snapshot availability verification (8 states)
- `modules/step-functions/db/prepare_snapshot_for_restore.asl.json` - Refactored from 39 to 33 states, calls EnsureSnapshotAvailable
- `modules/step-functions/db/main.tf` - Dual resource architecture (db + db_templated) with moved blocks
- `modules/step-functions/db/outputs.tf` - Merged outputs from both resource maps
- `modules/step-functions/db/README.md` - EnsureSnapshotAvailable I/O contract, integration pattern, naming table

## Decisions Made
- **Dual-map (not three-tier) for DB module:** Unlike the EFS module which needed three tiers to avoid circular ARN references between check_flag_file_sync and check_replication_sync, the DB module has no such circularity. EnsureSnapshotAvailable is a simple file() sub-SFN consumed by templatefile() callers. Two tiers suffice.
- **restore_cluster refactoring minimal:** After analysis, restore_cluster's WaitClusterRestore/CheckClusterStatus/IsClusterAvailable loop waits for the cluster (not a snapshot). It already delegates snapshot preparation to PrepareSnapshot. No inline snapshot wait loops exist to extract. restore_cluster moved to db_templated for consistency but the ASL content is unchanged.
- **prepare_snapshot_for_restore at 33 states (not 18):** The plan estimated reduction from 39 to ~18 states. Actual reduction is 39 to 33. The plan target of 18 was optimistic -- the file retains all snapshot copy initiation, cross-region proxy routing, manual snapshot creation, KMS grant, and sharing logic. Only the wait/verify polling loops were extractable to EnsureSnapshotAvailable.

## Deviations from Plan

None - plan executed as written with documented target adjustments:

1. **State count delta:** prepare_snapshot_for_restore at 33 states instead of plan target ~18. The "~18" target assumed more states were extractable, but the snapshot copy/create/share logic must remain inline (not snapshot wait/verify). The plan explicitly qualified with "~" prefix.
2. **restore_cluster minimal refactoring:** Plan anticipated this: "If restore_cluster does not have significant snapshot wait loops to extract, the refactoring may be minimal. Document the actual delta." No ASL changes were needed -- only Terraform resource migration.

## Issues Encountered
None

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- DB module refactoring complete, ready for Plan 02-03 (Orchestrator module)
- EnsureSnapshotAvailable ARN exported via merged output map for orchestrator consumption
- Dual-map pattern established as reference for future module migrations
- TestASLCatchSelfContained already discovers ensure_snapshot_available.asl.json

## Self-Check: PASSED

All 5 key files verified present. All 2 task commits verified in git log.

---
*Phase: 02-refactoring*
*Completed: 2026-03-13*
