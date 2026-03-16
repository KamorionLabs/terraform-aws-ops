---
phase: 01-extraction
plan: 01
subsystem: testing
tags: [step-functions, asl, ci, pytest, github-actions]

# Dependency graph
requires: []
provides:
  - strip_credentials helper for SFN Local testing (removes Credentials blocks)
  - create_state_machine fixture with auto-stripping
  - TestASLComment validation class for sub-SFN Comment field
  - TestASLCatchSelfContained validation class for named Fail errors
  - Extended EFS CI matrix (11 entries covering all existing + 3 future sub-SFNs)
  - Automated $$.Execution.Input audit step in CI
affects: [01-02-PLAN, 01-03-PLAN]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "strip_credentials for SFN Local compatibility (recursive Credentials removal)"
    - "Fixture-based state machine creation with auto-cleanup"
    - "CI audit step for scope-loss prevention in sub-SFNs"

key-files:
  created: []
  modified:
    - tests/conftest.py
    - tests/test_asl_validation.py
    - tests/test_stepfunctions_local.py
    - .github/workflows/step-functions.yml

key-decisions:
  - "Recursive strip_credentials handles Parallel/Map nested states"
  - "TestStateMachineCreation refactored to use fixture (single point of stripping)"
  - "EFS matrix keeps exit 0 for missing files (future sub-SFNs not yet created)"
  - "TestASLCatchSelfContained scoped to manage_*.asl.json only (not all SFNs)"

patterns-established:
  - "strip_credentials: single function in conftest.py for all SFN Local tests"
  - "create_state_machine fixture: always strips credentials before local creation"
  - "CI audit step: automated regression prevention for $$.Execution.Input in sub-SFNs"

requirements-completed: [PRE-01, TST-02]

# Metrics
duration: 3min
completed: 2026-03-13
---

# Phase 1 Plan 01: CI Fix & Test Infrastructure Summary

**strip_credentials helper with recursive Credentials removal, extended EFS CI matrix (11 entries), and automated $$.Execution.Input audit step**

## Performance

- **Duration:** 3 min
- **Started:** 2026-03-13T11:27:21Z
- **Completed:** 2026-03-13T11:30:29Z
- **Tasks:** 2
- **Files modified:** 4

## Accomplishments
- strip_credentials function handles Credentials removal recursively (Parallel branches, Map iterators)
- TestStateMachineCreation refactored to use create_state_machine fixture (single stripping point)
- EFS CI matrix expanded from 1 to 11 entries (8 existing + 3 future sub-SFNs)
- TestASLComment and TestASLCatchSelfContained ready to validate future sub-SFNs
- Automated $$.Execution.Input scope-loss audit in CI pipeline

## Task Commits

Each task was committed atomically:

1. **Task 1: Ajouter strip_credentials et mettre a jour les fixtures de test** - `99c3b15` (feat)
2. **Task 2: Etendre le matrix CI EFS et formaliser l'audit $$.Execution.Input** - `e164622` (feat)

## Files Created/Modified
- `tests/conftest.py` - Added strip_credentials() with recursive handling, updated create_state_machine fixture
- `tests/test_asl_validation.py` - Added TestASLComment and TestASLCatchSelfContained classes
- `tests/test_stepfunctions_local.py` - Refactored TestStateMachineCreation to use fixture
- `.github/workflows/step-functions.yml` - Extended EFS matrix to 11 entries, added Execution.Input audit step

## Decisions Made
- Recursive strip_credentials handles Parallel/Map nested states (future-proof for complex ASL)
- TestStateMachineCreation refactored to use fixture instead of direct client call (single point of credential stripping)
- EFS matrix keeps permissive exit 0 for missing files (future sub-SFNs from Plan 02/03)
- TestASLCatchSelfContained scoped only to manage_*.asl.json (existing SFNs not required to follow pattern)

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered
- Missing Python virtualenv: created .venv and installed requirements-dev.txt to run pytest locally (not a code issue, local dev setup)

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness
- strip_credentials integrated in test path: Plans 02/03 can create sub-SFNs with Credentials and test locally
- TestASLComment will auto-validate Comment field on new manage_*.asl.json files
- TestASLCatchSelfContained will auto-validate named Fail errors on new sub-SFNs
- CI matrix already includes manage_filesystem_policy, manage_access_point, manage_lambda_lifecycle

## Self-Check: PASSED

All files found, all commits verified.

---
*Phase: 01-extraction*
*Completed: 2026-03-13*
