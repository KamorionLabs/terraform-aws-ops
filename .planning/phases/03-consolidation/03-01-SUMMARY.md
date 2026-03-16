---
phase: 03-consolidation
plan: 01
subsystem: infra
tags: [step-functions, asl, eks, consolidation, choice-state]

# Dependency graph
requires:
  - phase: 02-refactoring
    provides: Refactored EKS step functions with sub-SFN patterns
provides:
  - 3 consolidated EKS ASL files with CheckAccessMode Choice routing (pub/priv dual paths)
  - EKS Terraform module without _suffix logic or eks_access_mode variable
affects: [03-02, 03-03]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "CheckAccessMode Choice state pattern for routing between eks:call and lambda:invoke"
    - "InitPrivateDefaults Pass state to inject stub EksCluster for Map ItemSelector path resolution"
    - "Dual-variant state naming: <StateName> (public) / <StateName>Private (private)"

key-files:
  created: []
  modified:
    - modules/step-functions/eks/manage_storage.asl.json
    - modules/step-functions/eks/scale_services.asl.json
    - modules/step-functions/eks/verify_and_restart_services.asl.json
    - modules/step-functions/eks/main.tf
    - modules/step-functions/eks/variables.tf

key-decisions:
  - "Hybrid consolidation approach for manage_storage: fork at ChooseAction/ChooseActionPrivate, share Wait states with CheckAccessMode routing before each K8s operation"
  - "InitPrivateDefaults stub pattern for Map-based ASLs to resolve ItemSelector paths when EksCluster data is absent in private mode"
  - "AccessMode passed as $.AccessMode inside Map ItemSelector (from outer $.EKS.AccessMode) for internal Choice routing"

patterns-established:
  - "CheckAccessMode Choice state: $.EKS.AccessMode == 'public' -> GetEksClusterInfo, Default -> private flow"
  - "InitPrivateDefaults: Pass state injecting stub $.EksCluster with 'unused' values for Map ItemSelector path resolution"
  - "<StateName>Private naming convention for lambda:invoke variant states"

requirements-completed: [CON-01, CON-02, CON-03]

# Metrics
duration: 6min
completed: 2026-03-16
---

# Phase 3 Plan 1: EKS ASL Consolidation Summary

**Consolidated 3 EKS ASL pairs into unified files using CheckAccessMode Choice routing on $.EKS.AccessMode, eliminating 3 _private files and compile-time _suffix branching**

## Performance

- **Duration:** 6 min
- **Started:** 2026-03-16T08:52:06Z
- **Completed:** 2026-03-16T08:58:27Z
- **Tasks:** 2
- **Files modified:** 5 (3 ASL consolidated, 2 Terraform updated) + 3 ASL deleted

## Accomplishments
- Consolidated manage_storage (31 states), scale_services (14 outer states), and verify_and_restart_services (8 outer states) into unified ASL files with dual public/private paths
- Deleted 3 _private ASL files (manage_storage_private, scale_services_private, verify_and_restart_services_private)
- Removed _suffix local variable and eks_access_mode variable from EKS Terraform module
- All 942 ASL validation tests pass

## Task Commits

Each task was committed atomically:

1. **Task 1: Consolidate 3 EKS ASL pairs into unified files** - `24b99df` (feat)
2. **Task 2: Update EKS Terraform module** - `742e244` (chore)

## Files Created/Modified
- `modules/step-functions/eks/manage_storage.asl.json` - Consolidated ASL with CheckAccessMode, 31 states (pub+priv Delete/Create flows)
- `modules/step-functions/eks/scale_services.asl.json` - Consolidated ASL with CheckAccessMode, dual PatchService/GetSecret/DeleteSecret variants + InitPrivateDefaults
- `modules/step-functions/eks/verify_and_restart_services.asl.json` - Consolidated ASL with CheckAccessMode, dual GetService/RestartService variants inside Map + InitPrivateDefaults
- `modules/step-functions/eks/main.tf` - Removed _suffix local, direct ASL filenames in step_functions map
- `modules/step-functions/eks/variables.tf` - Removed eks_access_mode variable block

## Files Deleted
- `modules/step-functions/eks/manage_storage_private.asl.json`
- `modules/step-functions/eks/scale_services_private.asl.json`
- `modules/step-functions/eks/verify_and_restart_services_private.asl.json`

## Decisions Made
- **Hybrid approach for manage_storage**: Used ChooseAction/ChooseActionPrivate fork at the top with shared Wait states and CheckAccessMode routing before each K8s operation. This avoids full branch duplication while keeping error handling correct (EKS.404 catches route to same-mode variant).
- **InitPrivateDefaults stub pattern**: For scale_services and verify_and_restart_services, added a Pass state that injects a stub `$.EksCluster` with "unused" values when in private mode. This resolves Map ItemSelector JSONPath references without duplicating the Map state. The stub values are never used because the internal Choice state routes to lambda:invoke variants.
- **AccessMode propagation in Map**: The outer `$.EKS.AccessMode` is passed as `$.AccessMode` in the Map ItemSelector, enabling internal CheckAccessModeForPatch/CheckAccessModeForGet/CheckAccessModeForRestart Choice states.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] Added InitPrivateDefaults stub for Map ItemSelector path resolution**
- **Found during:** Task 1 (scale_services consolidation)
- **Issue:** Map ItemSelector references `$.EksCluster.Cluster.Name`, `$.EksCluster.Cluster.CertificateAuthority.Data`, `$.EksCluster.Cluster.Endpoint` which don't exist when GetEksClusterInfo is skipped in private mode
- **Fix:** Added InitPrivateDefaults Pass state that injects a stub EksCluster object with "unused" values
- **Files modified:** scale_services.asl.json, verify_and_restart_services.asl.json
- **Verification:** JSON validates, all tests pass, stub values are never used in private path
- **Committed in:** 24b99df (Task 1 commit)

---

**Total deviations:** 1 auto-fixed (1 blocking)
**Impact on plan:** Essential for correctness -- without the stub, private mode execution would fail on Map ItemSelector path resolution. No scope creep.

## Issues Encountered
None

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- EKS module consolidation complete, pattern validated for DB module (03-02) and Utils module (03-03)
- CheckAccessMode and InitPrivateDefaults patterns established for reuse in remaining consolidation plans
- 942 ASL validation tests continue to pass

## Self-Check: PASSED

All 5 created/modified files verified present. All 3 deleted files confirmed absent. Both commit hashes (24b99df, 742e244) found in git log.

---
*Phase: 03-consolidation*
*Completed: 2026-03-16*
