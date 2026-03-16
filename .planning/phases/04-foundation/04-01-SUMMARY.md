---
phase: 04-foundation
plan: 01
subsystem: infra
tags: [lambda, step-functions, asl, secrets-manager, ssm, cross-account]

# Dependency graph
requires: []
provides:
  - "Lambda stub sync_config_items with input/output contract (SM/SSM)"
  - "ASL SyncConfigItems with ValidateInput, MapOverItems, CheckType Choice routing SM/SSM"
  - "templatefile placeholder ${SyncConfigItemsLambdaArn} for Terraform wiring"
affects: [04-02, 05-implementation]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Lambda stub pattern with Phase 5 placeholder for get_cross_account_client"
    - "ASL Map iterator with internal Choice state for per-item type routing"
    - "Named Fail states in Map iterator (SUB-05 pattern)"

key-files:
  created:
    - "lambdas/sync-config-items/sync_config_items.py"
    - "tests/test_sync_config_items.py"
    - "modules/step-functions/sync/sync_config_items.asl.json"
  modified: []

key-decisions:
  - "Choice state inside Map iterator (per-item routing) rather than before Map"
  - "SyncSMItem and SyncSSMItem call same Lambda but are separate states for Phase 5 extensibility"
  - "MaxConcurrency 1 for sequential processing in Phase 4 (can increase in Phase 5)"

patterns-established:
  - "Lambda stub with full input/output contract docstring for future implementation"
  - "ASL Choice routing inside Map ItemProcessor for mixed-type collections"

requirements-completed: [SYNC-08, SYNC-01]

# Metrics
duration: 4min
completed: 2026-03-16
---

# Phase 4 Plan 01: Lambda Stub + ASL SyncConfigItems Summary

**Generic Lambda stub with SM/SSM input/output contract and ASL SyncConfigItems with Choice-routed Map iterator**

## Performance

- **Duration:** 4 min
- **Started:** 2026-03-16T16:23:31Z
- **Completed:** 2026-03-16T16:27:37Z
- **Tasks:** 2
- **Files created:** 3

## Accomplishments
- Lambda stub sync_config_items returns structured output (statusCode + result with status/source/destination/type/message) for both SecretsManager and SSMParameter types
- ASL SyncConfigItems implements full flow: ValidateInput -> MapOverItems (with internal CheckType Choice routing to SyncSMItem or SyncSSMItem) -> PrepareOutput -> SyncSucceeded
- All 924 tests pass (6 Lambda unit tests + 918 ASL validation tests including auto-discovered new ASL)

## Task Commits

Each task was committed atomically:

1. **Task 1: Lambda stub sync_config_items + tests unitaires** - `f5bd859` (test: TDD RED) + `06457c1` (feat: TDD GREEN)
2. **Task 2: ASL SyncConfigItems avec Choice state SM/SSM dans Map iterator** - `106c7a7` (feat)

_Note: Task 1 used TDD with separate RED/GREEN commits_

## Files Created/Modified
- `lambdas/sync-config-items/sync_config_items.py` - Lambda stub with input/output contract, placeholder get_cross_account_client
- `tests/test_sync_config_items.py` - 6 unit tests covering SM/SSM types, empty input, paths, stub message, no hardcoded names
- `modules/step-functions/sync/sync_config_items.asl.json` - ASL definition with ValidateInput, MapOverItems, CheckType, SyncSMItem, SyncSSMItem, UnsupportedType, ItemFailed, PrepareOutput, SyncSkipped, SyncSucceeded

## Decisions Made
- Choice state placed inside Map iterator (per-item) not before Map, because Items[] can contain a mix of SM and SSM types
- SyncSMItem and SyncSSMItem are separate Task states calling the same Lambda, allowing Phase 5 to add type-specific parameters if needed
- MaxConcurrency set to 1 (sequential) for Phase 4 to avoid race conditions on secrets; can be increased in Phase 5

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered

None

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness
- Lambda stub and ASL ready for Plan 02 (Terraform module wrapping)
- The ${SyncConfigItemsLambdaArn} placeholder is ready for templatefile() resolution in the Terraform module
- 6 Lambda tests + ASL auto-discovery ensure regression safety for Plan 02 changes

## Self-Check: PASSED

All files verified present. All commits verified in git log.

---
*Phase: 04-foundation*
*Completed: 2026-03-16*
