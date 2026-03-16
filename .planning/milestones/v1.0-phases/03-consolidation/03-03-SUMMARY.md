---
phase: 03-consolidation
plan: 03
subsystem: infra
tags: [step-functions, asl, utils, consolidation, choice-state, run-archive-job]

# Dependency graph
requires:
  - phase: 03-consolidation
    provides: CheckAccessMode and InitPrivateDefaults patterns from EKS (03-01) and DB (03-02) consolidation
provides:
  - 1 consolidated Utils ASL file with CheckAccessModeForJob Choice routing (pub/priv dual job lifecycle)
  - Utils Terraform module without _eks_suffix logic or eks_access_mode variable
  - Zero eks_access_mode references across all 3 step-functions modules
  - Zero _private.asl.json files anywhere in codebase
affects: []

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Complex pair consolidation: shared prep states -> CheckAccessModeForJob Choice -> eks:runJob.sync (public) or lambda:invoke create/wait/check/delete cycle (private)"

key-files:
  created: []
  modified:
    - modules/step-functions/utils/run_archive_job.asl.json
    - modules/step-functions/utils/main.tf
    - modules/step-functions/utils/variables.tf

key-decisions:
  - "run_archive_job consolidation follows complex pair pattern: shared prep -> CheckAccessModeForJob -> dual lifecycle paths converging at PrepareOutput"
  - "No InitPrivateDefaults needed for run_archive_job (no Map with ItemSelector referencing EksCluster fields)"

patterns-established:
  - "All 3 consolidation patterns complete: simple fork (03-01), hybrid Map fork (03-02 mysqldump), complex outer fork (03-02 mysqlimport, 03-03 archive)"

requirements-completed: [CON-04]

# Metrics
duration: 3min
completed: 2026-03-16
---

# Phase 3 Plan 3: Utils ASL Consolidation + Final Cleanup Summary

**Consolidated run_archive_job ASL with dual job lifecycle (eks:runJob.sync / lambda:invoke cycle) via CheckAccessModeForJob, eliminated eks_access_mode from all modules, zero _private files remaining**

## Performance

- **Duration:** 3 min
- **Started:** 2026-03-16T09:09:24Z
- **Completed:** 2026-03-16T09:12:30Z
- **Tasks:** 2
- **Files modified:** 3 (1 ASL consolidated, 2 Terraform updated) + 1 ASL deleted

## Accomplishments
- Consolidated run_archive_job with CheckAccessModeForJob Choice state routing between public (GetEksClusterInfo + RunKubernetesJob via eks:runJob.sync) and private (CreateArchiveJob/WaitForArchiveJob/CheckArchiveJobStatus/DeleteArchiveJob via lambda:invoke)
- Deleted run_archive_job_private.asl.json (last _private file in codebase)
- Removed _eks_suffix local variable and eks_access_mode variable from Utils Terraform module
- Verified zero eks_access_mode references across all 3 step-functions modules (EKS, DB, Utils)
- Verified zero _private.asl.json files remain anywhere under modules/step-functions/
- All 916 tests pass (906 ASL validation + 10 other), 81 skipped (Docker-dependent)

## Task Commits

Each task was committed atomically:

1. **Task 1: Consolidate run_archive_job ASL pair into unified file** - `03b3dc8` (feat)
2. **Task 2: Update Utils Terraform module and final cleanup verification** - `66e6b7b` (chore)

## Files Created/Modified
- `modules/step-functions/utils/run_archive_job.asl.json` - Consolidated ASL with CheckAccessModeForJob, 17 states (4 shared prep + 1 Choice + 2 public + 7 private + 3 shared terminal)
- `modules/step-functions/utils/main.tf` - Removed _eks_suffix local, direct ASL filename for run_archive_job
- `modules/step-functions/utils/variables.tf` - Removed eks_access_mode variable block

## Files Deleted
- `modules/step-functions/utils/run_archive_job_private.asl.json`

## Decisions Made
- **Complex pair pattern for run_archive_job**: Shared preparation states (GetClusterInfo, EnsureNodegroupCapacity, GetDatabaseCredentials, ParseSecretString) flow into CheckAccessModeForJob Choice state. Public path adds GetEksClusterInfo + RunKubernetesJob (2 states), private path adds the full lambda:invoke create/wait/check/delete cycle (7 states). Both converge at PrepareOutput.
- **No InitPrivateDefaults needed**: Unlike run_mysqldump (03-02), run_archive_job has no Map with ItemSelector referencing EksCluster fields. The public-only GetEksClusterInfo stores to $.ClusterInfo which is only used by RunKubernetesJob (also public-only). The private path never references $.ClusterInfo.

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered
None

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- Phase 3 consolidation complete: all 6 ASL pairs merged into 6 unified files
- All 3 step-functions modules cleaned: no _suffix variables, no eks_access_mode, direct filenames
- Runtime access mode switching via $.EKS.AccessMode fully replaces compile-time var.eks_access_mode
- 916 tests pass across full suite

## Self-Check: PASSED

All 3 modified files verified present. Deleted file confirmed absent. Both commit hashes (03b3dc8, 66e6b7b) found in git log.

---
*Phase: 03-consolidation*
*Completed: 2026-03-16*
