---
phase: 07-s3-replication-module
plan: 02
subsystem: infra
tags: [step-functions, asl, s3, s3control, batch-replication, aws-sdk-integration]

# Dependency graph
requires:
  - phase: 07-01
    provides: "setup_cross_account_replication ASL posting the live ReplicationConfiguration that the batch job reuses"
provides:
  - "run_batch_replication.asl.json — dispatches an S3 Batch Operations job (S3ReplicateObject) backfilling existing objects to all destinations in one job, returning JobId"
  - "check_batch_replication.asl.json — polls s3control:describeJob in a Wait+Choice loop until Complete/Failed/Cancelled"
affects: [07-03, 07-04, orchestrator-integration]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "S3 Batch Operations job dispatch via aws-sdk:s3control:createJob with GeneratedManifest (no S3 Inventory precondition)"
    - "Wait+Task+Choice poll loop on a job-status enum (s3control:describeJob), mirroring the EFS delete_replication poll shell"

key-files:
  created:
    - modules/step-functions/s3/run_batch_replication.asl.json
    - modules/step-functions/s3/check_batch_replication.asl.json
  modified: []

key-decisions:
  - "ShouldEnableReport Choice on $.ReportBucketArn IsPresent (O1) — two near-identical createJob states (with/without Report.Bucket) rather than dynamic Report assembly, keeping each state statically valid"
  - "Polling interval 30s (D-11 discretion); non-terminal statuses loop back to Wait via Choice Default"
  - "BatchSucceeded as a Pass normalizing output (Status/ModuleName/JobId or ProgressSummary) before the Succeed terminal, consistent with the project's PrepareOutput idiom"

patterns-established:
  - "s3control batch job dispatch+poll split across two ASL files chained by $.BatchJob.JobId"

requirements-completed: [REPL-02, REPL-03, REPL-05, REPL-06]

# Metrics
duration: 5min
completed: 2026-06-19
---

# Phase 07 Plan 02: S3 Batch Replication ASL Summary

**Two native-SDK ASL state machines — run_batch_replication (s3control:createJob + S3ReplicateObject + GeneratedManifest backfill) and check_batch_replication (s3control:describeJob Wait-loop) — with no Lambda.**

## Performance

- **Duration:** ~5 min
- **Started:** 2026-06-19T09:34:42Z
- **Completed:** 2026-06-19T09:40:00Z
- **Tasks:** 2
- **Files modified:** 2 (both created)

## Accomplishments
- `run_batch_replication.asl.json` dispatches a single S3 Batch Operations job (`S3ReplicateObject`) that reuses the live `ReplicationConfiguration` to backfill existing objects to ALL configured destinations in one job (REPL-02, REPL-05), using `S3JobManifestGenerator` filtering `NONE`/`FAILED` objects (no S3 Inventory precondition).
- Fresh `ClientRequestToken` via `States.UUID()` with `ConfirmationRequired:false` (idempotency token, mitigates T-07-04 replay).
- `check_batch_replication.asl.json` polls `s3control:describeJob` in a Wait+Task+Choice loop on `$.JobStatus.Job.Status`, succeeding on `Complete`, failing on `Failed`/`Cancelled`, looping on all non-terminal states.
- Both SFN assume the source role imperatively via `Credentials.RoleArn.$` and pass `AccountId` explicitly; native SDK only, no `lambda:invoke` (REPL-06).

## Task Commits

Each task was committed atomically:

1. **Task 1: run_batch_replication.asl.json (createJob + GeneratedManifest)** - `ec321bd` (feat)
2. **Task 2: check_batch_replication.asl.json (describeJob poll loop)** - `74da191` (feat)

## Files Created/Modified
- `modules/step-functions/s3/run_batch_replication.asl.json` - S3 Batch Replication job dispatch; Choice on optional report bucket, createJob with/without Report, returns `$.BatchJob.JobId`.
- `modules/step-functions/s3/check_batch_replication.asl.json` - describeJob polling loop to completion, consuming `$.BatchJob.JobId`.

## Decisions Made
- **Two createJob states instead of one:** The CreateJob API requires the `Report` object, but `Report.Bucket` is only valid when `Enabled:true`. Branching via a `ShouldEnableReport` Choice into `CreateBatchJobWithReport` / `CreateBatchJobNoReport` keeps each state's Parameters statically valid rather than conditionally injecting `Report.Bucket` (O1: the generic module owns no report bucket).
- **30s poll interval** (D-11 discretion); Choice `Default` routes all non-terminal `Job.Status` values back to `WaitForJob`.
- **Output normalization Pass** (`BatchSucceeded`/`PrepareOutput`) before the Succeed terminal, matching the project-wide `Status`/`ModuleName` output idiom.

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered
None.

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- The two batch ASL files are in place and structurally valid; the full ASL validation suite passes (978 passed). Ready for Plan 03 (source-account IAM: `s3control:CreateJob`/`DescribeJob`, `iam:PassRole` to the replication role with `iam:PassedToService` for `batchoperations.s3.amazonaws.com`, mitigating T-07-06) and Plan 04 (delete_replication / orchestrator wiring).
- No blockers.

## Self-Check: PASSED
- `modules/step-functions/s3/run_batch_replication.asl.json` — FOUND
- `modules/step-functions/s3/check_batch_replication.asl.json` — FOUND
- commit `ec321bd` — FOUND
- commit `74da191` — FOUND

---
*Phase: 07-s3-replication-module*
*Completed: 2026-06-19*
