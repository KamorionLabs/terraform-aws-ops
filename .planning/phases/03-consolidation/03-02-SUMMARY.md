---
phase: 03-consolidation
plan: 02
subsystem: infra
tags: [step-functions, asl, db, consolidation, choice-state, mysqldump, mysqlimport]

# Dependency graph
requires:
  - phase: 03-consolidation
    provides: CheckAccessMode and InitPrivateDefaults patterns from EKS ASL consolidation (03-01)
provides:
  - 2 consolidated DB ASL files with CheckAccessMode Choice routing (pub/priv dual paths)
  - DB Terraform module without _eks_suffix logic or eks_access_mode variable
affects: [03-03]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Hybrid Map consolidation: CheckAccessModeForDump inside Map ItemProcessor routes to eks:runJob.sync or lambda:invoke cycle"
    - "Complex outer consolidation: CheckAccessModeForImport after shared prep routes to eks:runJob.sync (2 states) or lambda create/wait/check/delete cycle (8 states)"
    - "InitPrivateDefaults reuse for Map ItemSelector path resolution in run_mysqldump"

key-files:
  created: []
  modified:
    - modules/step-functions/db/run_mysqldump_on_eks.asl.json
    - modules/step-functions/db/run_mysqlimport_on_eks.asl.json
    - modules/step-functions/db/main.tf
    - modules/step-functions/db/variables.tf

key-decisions:
  - "run_mysqldump uses InitPrivateDefaults + inner Map CheckAccessModeForDump -- divergence inside Map iterator, not outer flow"
  - "run_mysqlimport uses dual CheckAccessMode: outer for GetEksClusterInfo skip, inner CheckAccessModeForImport for job lifecycle fork"
  - "CheckSkipDeletion debug feature preserved in private import path only (not reachable from public path)"

patterns-established:
  - "Inner Map Choice routing: CheckAccessModeForDump inside Map ItemProcessor for per-table job execution mode"
  - "Complex outer fork: CheckAccessModeForImport after FormatSecretString branches to 2-state public or 8-state private path, both converge at PrepareOutput"

requirements-completed: [CON-05, CON-06]

# Metrics
duration: 4min
completed: 2026-03-16
---

# Phase 3 Plan 2: DB ASL Consolidation Summary

**Consolidated 2 DB ASL pairs (run_mysqldump + run_mysqlimport) into unified files with dual pub/priv paths via CheckAccessMode, eliminating 2 _private files and _eks_suffix branching**

## Performance

- **Duration:** 4 min
- **Started:** 2026-03-16T09:01:42Z
- **Completed:** 2026-03-16T09:06:21Z
- **Tasks:** 2
- **Files modified:** 4 (2 ASL consolidated, 2 Terraform updated) + 2 ASL deleted

## Accomplishments
- Consolidated run_mysqldump_on_eks with inner Map Choice routing (CheckAccessModeForDump) between eks:runJob.sync and lambda:invoke create/wait/check/delete cycle
- Consolidated run_mysqlimport_on_eks with outer CheckAccessModeForImport fork after shared prep states, routing to 2-state public or 8-state private path including CheckSkipDeletion debug support
- Deleted 2 _private ASL files (run_mysqldump_on_eks_private, run_mysqlimport_on_eks_private)
- Removed _eks_suffix local variable and eks_access_mode variable from DB Terraform module
- All 918 ASL validation tests pass, full suite 928 passed

## Task Commits

Each task was committed atomically:

1. **Task 1: Consolidate 2 DB ASL pairs into unified files** - `18ae608` (feat)
2. **Task 2: Update DB Terraform module -- remove _eks_suffix and eks_access_mode** - `0d02130` (chore)

## Files Created/Modified
- `modules/step-functions/db/run_mysqldump_on_eks.asl.json` - Consolidated ASL with CheckAccessMode outer routing + InitPrivateDefaults + CheckAccessModeForDump inside Map iterator (12 outer states, 9 Map internal states)
- `modules/step-functions/db/run_mysqlimport_on_eks.asl.json` - Consolidated ASL with CheckAccessMode for GetEksClusterInfo skip + CheckAccessModeForImport for job lifecycle fork (19 states total)
- `modules/step-functions/db/main.tf` - Removed _eks_suffix local, direct ASL filenames in step_functions map
- `modules/step-functions/db/variables.tf` - Removed eks_access_mode variable block

## Files Deleted
- `modules/step-functions/db/run_mysqldump_on_eks_private.asl.json`
- `modules/step-functions/db/run_mysqlimport_on_eks_private.asl.json`

## Decisions Made
- **run_mysqldump uses InitPrivateDefaults + inner Map routing**: The divergence in run_mysqldump is inside the Map iterator (RunEksJobForTable vs CreateDumpJob cycle). Used InitPrivateDefaults to inject stub EksCluster for Map ItemSelector path resolution (same pattern as 03-01), then CheckAccessModeForDump inside the Map routes to the correct execution path.
- **run_mysqlimport uses dual Choice states**: Outer CheckAccessMode between EnsureNodegroupCapacity and GetDbSecretValue routes public to GetEksClusterInfo (private skips). CheckAccessModeForImport after FormatSecretString branches to RunEksJobForImport (public, 2 states) or CreateImportJob (private, 8-state cycle including CheckSkipDeletion).
- **No InitPrivateDefaults needed for run_mysqlimport**: Unlike run_mysqldump, the import does not use a Map with ItemSelector referencing EksCluster fields. The public path accesses EksCluster directly in RunEksJobForImport, the private path never references it.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] Added InitPrivateDefaults for run_mysqldump Map ItemSelector**
- **Found during:** Task 1 (run_mysqldump consolidation)
- **Issue:** Map ItemSelector references `$.EksCluster.Cluster.Name`, `$.EksCluster.Cluster.CertificateAuthority.Data`, `$.EksCluster.Cluster.Endpoint` which don't exist when GetEksClusterInfo is skipped in private mode
- **Fix:** Added InitPrivateDefaults Pass state (reusing pattern from 03-01) that injects stub EksCluster with "unused" values
- **Files modified:** run_mysqldump_on_eks.asl.json
- **Verification:** JSON validates, all tests pass, stub values never used in private path
- **Committed in:** 18ae608 (Task 1 commit)

---

**Total deviations:** 1 auto-fixed (1 blocking)
**Impact on plan:** Essential for correctness -- without the stub, private mode execution would fail on Map ItemSelector path resolution. Same pattern validated in 03-01. No scope creep.

## Issues Encountered
None

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- DB module consolidation complete, pattern validated for Utils module (03-03: run_archive_job)
- All three consolidation patterns now established: simple fork (03-01), hybrid Map fork (03-02 mysqldump), complex outer fork (03-02 mysqlimport)
- 918 ASL validation tests continue to pass

## Self-Check: PASSED

All 4 modified files verified present. Both deleted files confirmed absent. Both commit hashes (18ae608, 0d02130) found in git log.

---
*Phase: 03-consolidation*
*Completed: 2026-03-16*
