---
phase: 07-s3-replication-module
plan: 01
subsystem: infra
tags: [step-functions, asl, s3, cross-account-replication, jsonata, aws-sdk]

# Dependency graph
requires:
  - phase: v1.1 sync module
    provides: file()-based step-functions module structure and ASL test auto-discovery
provides:
  - setup_cross_account_replication.asl.json (live replication via read-merge-write on the single ReplicationConfiguration, versioning validate-only gate, MaxConcurrency:1 Map fan-out)
  - delete_replication.asl.json (symmetric read-filter-write teardown, idempotent on not-found)
  - deterministic Rule-ID convention 'repl-<DestAccountId>-<DestBucketBasename>' shared by setup and delete (D-02)
affects: [07-02 (Terraform module main.tf/variables.tf/outputs.tf wiring of these ASL), 07-03 (run_batch/check_batch ASL), 08 orchestrator integration, 09 spec repl-s3-sync]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "S3 read-merge-write: GetBucketReplication -> JSONata $filter by deterministic Rule ID -> PutBucketReplication (whole-config replace) keeps spokes independent"
    - "MaxConcurrency:1 Map over Destinations[] prevents lost-update race on the single per-bucket ReplicationConfiguration"
    - "Validate-only versioning gate (GetBucketVersioning + Choice + Fail), never PutBucketVersioning"
    - "JSONata Task with Arguments/Credentials.RoleArn (string template) for SDK Tasks that consume Assigned variables"

key-files:
  created:
    - modules/step-functions/s3/setup_cross_account_replication.asl.json
    - modules/step-functions/s3/delete_replication.asl.json
  modified: []

key-decisions:
  - "Rule-ID convention (D-02): 'repl-<DestAccountId>-<DestBucketBasename>' truncated to 255 chars, string-identical in setup and delete"
  - "Priority derived from (existing kept-rule count + Map index) to give each destination a unique Priority required by filtered multi-rule configs (Pitfall 3)"
  - "RTC enabled forces Metrics{Enabled, EventThreshold 15m} + ReplicationTime{Enabled, Time 15m} (Pitfall 4); DeleteMarkerReplication defaults Disabled (D-05)"
  - "Broad Catch (S3.ReplicationConfigurationNotFoundError + S3.S3Exception + States.ALL) on setup's read to seed empty config; delete's read routes not-found to idempotent NoReplicationExists (A1 mitigation)"
  - "Map ItemProcessor uses JSONata Pass for merge and JSONata Task (Arguments) for PutBucketReplication so Assigned mergedRules/replicationRole flow into the write"

patterns-established:
  - "Single-config-per-bucket read-merge-write keyed by deterministic Rule ID for independent spoke management"
  - "Symmetric teardown: same ID convention, Choice between DeleteBucketReplication (none remain) and PutBucketReplication (remaining)"

requirements-completed: [REPL-01, REPL-04, REPL-05]

# Metrics
duration: 4min
completed: 2026-06-19
---

# Phase 7 Plan 01: S3 Replication ASL (setup + delete) Summary

**Two read-merge-write ASL state machines for the new s3/ module: setup_cross_account_replication (versioning validate-only gate + MaxConcurrency:1 Map fan-out keyed by deterministic Rule ID) and delete_replication (symmetric read-filter-write teardown), both assuming the source role imperatively, no Lambda.**

## Performance

- **Duration:** ~4 min
- **Started:** 2026-06-19T09:27Z
- **Completed:** 2026-06-19T09:31Z
- **Tasks:** 2
- **Files modified:** 2 (both created)

## Accomplishments
- `setup_cross_account_replication.asl.json`: validate-only versioning gate (Choice on `$.Versioning.Status` -> `SourceVersioningNotEnabled` Fail), then a `MaxConcurrency:1` Map over `Destinations[]` that reads the existing config (seeding `{Role, Rules:[]}` on not-found), JSONata-filters out this destination's prior Rule by deterministic ID, appends a freshly built Rule (unique Priority, Filter+Status+DeleteMarkerReplication, RTC->Metrics coupling), and PutBucketReplication-writes the full merged Rules array.
- `delete_replication.asl.json`: symmetric teardown computing the removal ID set from `Destinations[]` (same `repl-` convention), JSONata-filtering the existing Rules, and a Choice routing to `DeleteBucketReplication` when none remain or `PutBucketReplication` with the remaining Rules; not-found is an idempotent `NoReplicationExists` Pass.
- Every cross-account S3 Task assumes the source role via `Credentials.RoleArn` (imperative).
- Both files auto-discovered and validated by `tests/test_asl_validation.py` (954/954 tests green, full suite).

## Task Commits

Each task was committed atomically:

1. **Task 1: setup_cross_account_replication.asl.json** - `3cb8772` (feat)
2. **Task 2: delete_replication.asl.json** - `fe501a1` (feat)

## Files Created/Modified
- `modules/step-functions/s3/setup_cross_account_replication.asl.json` - live replication setup via read-merge-write with versioning gate and Map fan-out
- `modules/step-functions/s3/delete_replication.asl.json` - symmetric teardown of replication rules per targeted destination

## Decisions Made
- **Rule-ID convention (D-02):** `repl-<DestAccountId>-<DestBucketBasename>` (basename = last `/`-split segment of the destination bucket ARN), truncated to 255 chars. String-identical in both files so delete reliably matches setup's rules.
- **Priority:** `count(kept rules) + Map index` per destination — guarantees a unique Priority for each filtered Rule, required when a Rule has a Filter (Pitfall 3).
- **RTC coupling (Pitfall 4):** when `Destination.RTC.Status == Enabled`, emit both `Metrics` and `ReplicationTime` (15-min windows); otherwise honor optional `Metrics.Status` alone.
- **Catch strategy (A1):** setup's `ReadExistingConfig` uses a broad Catch (specific S3 errors + `States.ALL`) to seed empty config; delete's read routes the not-found S3 errors to an idempotent Pass and `States.ALL` to `DeleteFailed`.
- **JSONata mixing:** the Map iterator's merge is a JSONata `Pass` using `Assign`; the subsequent `PutBucketReplication` is a JSONata `Task` using `Arguments` + `Credentials.RoleArn` string templates so the Assigned `mergedRules`/`replicationRole` variables are consumed in the write.

## Deviations from Plan

None - plan executed exactly as written. The plan's task action sketched the merge/write using JSONPath ResultPath wording, but the read-merge-write necessarily mixes JSONata (`Assign` for `$filter`/concat) with the SDK Task that consumes those variables; this was realized with per-state `QueryLanguage: "JSONata"` + `Arguments`, which is the canonical ASL idiom for variable-driven SDK Tasks and matches the EFS `CleanSourcePolicyJSONata` analog the plan referenced. No scope change, no extra functionality.

## Issues Encountered
None. The ASL validation suite (structural: JSON validity, StartAt/States, valid types, Next/Choice/Catch references, terminal states, Task Resource, Choice Choices, Credentials RoleArn, top-level Comment) passed on first run for both files.

## User Setup Required
None - no external service configuration required. Phase 7 is module authoring; no AWS apply.

## Next Phase Readiness
- The two core live-replication ASL files exist and validate. Ready for 07-02 (Terraform `main.tf`/`variables.tf`/`outputs.tf`/`versions.tf` wiring via `file()` map + `for_each aws_sfn_state_machine`) and 07-03 (`run_batch_replication` / `check_batch_replication` ASL).
- The deterministic Rule-ID convention is now fixed and must be reused by any future state machine touching the same per-bucket ReplicationConfiguration.
- Note: ASL tests are structural only; deep execution-semantics tests (Map iterator, JSONata evaluation, Credentials) are Phase 9 scope (SFN Local strips Credentials/JSONata), per RESEARCH Validation Architecture.

## Self-Check: PASSED
- FOUND: modules/step-functions/s3/setup_cross_account_replication.asl.json
- FOUND: modules/step-functions/s3/delete_replication.asl.json
- FOUND commit: 3cb8772
- FOUND commit: fe501a1

---
*Phase: 07-s3-replication-module*
*Completed: 2026-06-19*
