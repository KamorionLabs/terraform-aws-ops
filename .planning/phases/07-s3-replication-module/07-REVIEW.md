---
phase: 07-s3-replication-module
reviewed: 2026-06-19T00:00:00Z
depth: standard
files_reviewed: 11
files_reviewed_list:
  - modules/source-account/main.tf
  - modules/source-account/outputs.tf
  - modules/source-account/variables.tf
  - modules/step-functions/s3/check_batch_replication.asl.json
  - modules/step-functions/s3/delete_replication.asl.json
  - modules/step-functions/s3/main.tf
  - modules/step-functions/s3/outputs.tf
  - modules/step-functions/s3/run_batch_replication.asl.json
  - modules/step-functions/s3/setup_cross_account_replication.asl.json
  - modules/step-functions/s3/variables.tf
  - modules/step-functions/s3/versions.tf
findings:
  critical: 3
  warning: 6
  info: 4
  total: 13
status: issues_found
---

# Phase 7: Code Review Report

**Reviewed:** 2026-06-19
**Depth:** standard
**Files Reviewed:** 11
**Status:** issues_found

## Summary

Reviewed the new S3 cross-account replication module (4 SFN state machines + source-account IAM additions). The Terraform wiring is clean and mirrors the EFS/sync modules correctly (file()-based definitions, naming, logging, X-Ray). The D-09 "no Lambda" constraint is honored — all four ASL files use native `aws-sdk` integrations only.

The serious problems are in the ASL error-handling and the read-merge-write logic, where the module diverges from the safer EFS pattern in ways that can **silently destroy other spokes' replication rules** or **report success when teardown/setup did nothing**. The single shared `ReplicationConfiguration` per bucket makes these failure modes high-impact: a swallowed transient error (throttle, AccessDenied) becomes a full-config overwrite. There is also a Priority-collision bug in the merge that produces invalid `PutBucketReplication` payloads under realistic multi-destination / re-run scenarios.

## Critical Issues

### CR-01: setup overwrites the entire ReplicationConfiguration when a transient/permission error is mistaken for "no config"

**File:** `modules/step-functions/s3/setup_cross_account_replication.asl.json:72-82`
**Issue:** The `ReadExistingConfig` Catch lumps `States.ALL` together with `S3.ReplicationConfigurationNotFoundError` and routes all of them to `SeedEmptyConfig`, which sets `Rules: []`. The very next state (`MergeRule` -> `WriteConfig`) then does `PutBucketReplication`, which **replaces the entire config** (acknowledged in the Pitfall 2 comment). So if `GetBucketReplication` fails for any reason other than "not found" — throttling (`SlowDown`/`503`), `AccessDenied`, an assume-role hiccup, a transient `S3.S3Exception` — the workflow treats the bucket as having no replication, seeds an empty Rules array, and writes a config that contains **only the current destination's rule**, silently deleting every other spoke's replication rule. This is irreversible data-path loss (replication stops for the dropped destinations) and the run still reports `Status: Completed`.

This is a regression vs. the EFS pattern, which catches only the specific not-found exception (`Efs.ReplicationNotFoundException`) for the no-op path and sends everything else to `DeleteFailed`.

**Fix:** Catch only the genuine not-found case for the seed path; route real errors to `SetupFailed`.
```json
"Catch": [
  {
    "ErrorEquals": ["S3.ReplicationConfigurationNotFoundError"],
    "ResultPath": "$.ExistingError",
    "Next": "SeedEmptyConfig"
  },
  {
    "ErrorEquals": ["States.ALL"],
    "ResultPath": "$.ExistingError",
    "Next": "MergeDestinationFailed"
  }
]
```
Add a `Fail` state for the error path (the Map-level `Catch` will propagate it to `SetupFailed`). Do NOT keep `S3.S3Exception` / `States.ALL` on the seed branch — `S3Exception` is the generic SDK exception class and matches AccessDenied/throttle, not just not-found.

### CR-02: delete_replication reports success on any S3 error, masking failed teardowns

**File:** `modules/step-functions/s3/delete_replication.asl.json:17-31`
**Issue:** `GetReplicationConfig`'s first Catch matches both `S3.ReplicationConfigurationNotFoundError` AND `S3.S3Exception`, routing both to `NoReplicationExists` (which returns `Status: NoReplication` and Succeeds). `S3.S3Exception` is the base SDK exception type and will match `AccessDenied`, `SlowDown`, and most other S3 errors. The result: a teardown that fails because the role lost permission, or because of throttling, is reported as "no replication found; teardown is a no-op" and the state machine **Succeeds**. The caller believes replication was removed when it is still live — a correctness/data-governance failure (e.g. data continues replicating cross-account after an intended decommission).

**Fix:** Match only the not-found error on the no-op branch:
```json
"Catch": [
  {
    "ErrorEquals": ["S3.ReplicationConfigurationNotFoundError"],
    "ResultPath": "$.Error",
    "Next": "NoReplicationExists"
  },
  {
    "ErrorEquals": ["States.ALL"],
    "ResultPath": "$.Error",
    "Next": "DeleteFailed"
  }
]
```

### CR-03: Priority collisions produce invalid PutBucketReplication payloads on re-run and multi-destination merges

**File:** `modules/step-functions/s3/setup_cross_account_replication.asl.json:101`
**Issue:** Rule priority is computed as `$priority := $count($kept) + $states.input.Index`, where `$kept` is the existing rules minus the one matching this destination's ID, and `Index` is the Map item index (0-based). S3 V2 replication rules with a `Filter` (here `Filter.Prefix` is always set) **require a unique `Priority` across all rules in the config** — duplicate priorities are rejected by `PutBucketReplication`. The formula collides in realistic cases:

- **Re-run / idempotent update:** Suppose 3 destinations already configured (priorities 0,1,2). Re-running setup with the same 3 destinations: iteration Index=0 reads 3 rules, filters out its own (kept=2), priority = `2 + 0 = 2`. But priority 2 already belongs to dest #3, which has NOT yet been re-processed this run — the written config now has two rules at priority 2. `PutBucketReplication` rejects it -> `WriteConfigFailed`.
- **Mixed new+existing:** Adding a 2nd destination when 1 exists (priority 0): Index=0, kept filters its own id (not present, kept=1), priority = `1 + 0 = 1` (ok). But if the orchestrator passes both destinations in one `Destinations[]` array on a fresh bucket: Index 0 -> kept 0 -> prio 0; Index 1 reads back the 1-rule config, kept 1 -> prio `1+1 = 2`, skipping priority 1 and leaving a gap (tolerable), but any subsequent re-run reshuffles and collides as above.

The root cause is deriving priority from a count that changes as rules are added/removed rather than from a stable per-destination value.

**Fix:** Derive a deterministic, collision-free priority that does not depend on the live rule count. Options:
- Carry an explicit `Priority` on each destination input and use it verbatim, or
- Preserve the existing rule's priority when updating in place, and for new rules use `max(existing priorities) + 1`:
```
$maxPrio := $count($kept) = 0 ? -1 : $max($kept.Priority);
$existingSelf := $existingRules[ID = $ruleId];
$priority := $exists($existingSelf) ? $existingSelf.Priority : $maxPrio + 1;
```
This keeps a destination's priority stable across re-runs and guarantees uniqueness. Add a unit/integration assertion that priorities are distinct before `PutBucketReplication`.

## Warnings

### WR-01: Map iteration error aborts the whole setup, leaving a partially-merged config with no rollback

**File:** `modules/step-functions/s3/setup_cross_account_replication.asl.json:42-148`
**Issue:** `MergeDestinations` runs `MaxConcurrency:1` and each iteration does its own read-merge-write. If destination N fails at `WriteConfig` (e.g. CR-03 priority collision, or a transient error), `WriteConfigFailed` fails the iteration, the Map-level Catch routes to `SetupFailed`, and the workflow stops — but destinations 1..N-1 have **already been written** to the live config. There is no compensating action and the output is `SetupReplicationFailed` with no record of which destinations were applied. The caller cannot tell the bucket is in a half-configured state. The EFS analog at least keeps operations idempotent and re-runnable; here a partial failure plus the CR-03 priority bug can wedge the bucket into a state where re-runs keep failing.

**Fix:** Either make the whole merge atomic (read once before the Map, accumulate all rules in memory, single `PutBucketReplication` after the loop) or emit which destinations succeeded in the failure output so the operator/orchestrator can reconcile. The single-write approach also eliminates the per-iteration read-back that drives CR-03.

### WR-02: check_batch_replication treats describeJob errors and terminal failures with one ambiguous Fail

**File:** `modules/step-functions/s3/check_batch_replication.asl.json:24-30, 68-72`
**Issue:** The `DescribeBatchJob` Catch (`States.ALL`) and the terminal `Failed`/`Cancelled` choices all converge on `BatchJobFailed`. A transient `describeJob` error (throttle while polling a long-running batch job) is indistinguishable from a genuine job failure and aborts the poll loop permanently — the batch job may actually still be running and eventually succeed, but the SFN already reported failure. Polling loops should retry transient describe errors, not fail on first error.

**Fix:** Add a `Retry` block on `DescribeBatchJob` for transient errors before the Catch:
```json
"Retry": [
  { "ErrorEquals": ["S3Control.S3ControlException", "States.TaskFailed"],
    "IntervalSeconds": 15, "MaxAttempts": 5, "BackoffRate": 2 }
],
```
and keep `States.ALL` -> `BatchJobFailed` only as the final fallback.

### WR-03: No upper bound on the batch-job poll loop (unbounded execution)

**File:** `modules/step-functions/s3/check_batch_replication.asl.json:5-52`
**Issue:** `WaitForJob` -> `DescribeBatchJob` -> `EvaluateJobStatus` -> (Default) `WaitForJob` loops with no maximum iteration count. If a batch job is stuck in `Suspended`/`Paused` (e.g. awaiting manual confirmation, or an account-level pause) the state machine polls every 30s forever until the SFN service execution limit (1 year / 25k history events) is hit, then dies with an opaque `States.ExecutionLimitExceeded`. There is no timeout guard.

**Fix:** Add `"TimeoutSeconds"` at the top level of the state machine (e.g. matching the expected max batch duration), or maintain an iteration counter and fail after N polls with a clear `BatchReplicationTimedOut` error. Note `Paused`/`Suspended` are currently silently treated as "keep waiting" — confirm that is intended vs. surfacing them.

### WR-04: `S3ReplicationManage` PassRole and bucket wildcards are broad; PassRole self-referential trust gap

**File:** `modules/source-account/main.tf:679-715, 755-802`
**Issue:** Two least-privilege concerns beyond the acknowledged `arn:aws:s3:::*` TODO:
1. `S3BatchControl` (`s3:CreateJob`, `s3:DescribeJob`) is `Resource: "*"` with no condition. `CreateJob` is powerful (can target arbitrary operations); for a generic module this is acceptable but should be documented as a Phase-8 tightening item alongside the bucket ARNs, not left silent.
2. The `s3_replication` role policy grants `S3LiveReplicationWrite` including `s3:ObjectOwnerOverrideToBucketOwner` on `Resource: "*"`. Combined with `s3:ReplicateDelete` on `*`, the replication role can replicate-delete and override ownership on **any** bucket it can reach in the account. For the live-replication service role this is the standard shape, but `Resource: "*"` (vs. the destination bucket ARNs) is broader than necessary and should carry the same Phase-8 tightening note as the manage policy.

**Fix:** Add an explicit comment/TODO on `S3BatchControl` and `S3LiveReplicationWrite` mirroring the bucket-ARN tightening note, and ensure Phase-8 wiring scopes these to concrete source/destination/KMS ARNs. Verify the `iam:PassRole` `iam:PassedToService` list (`s3.amazonaws.com`, `batchoperations.s3.amazonaws.com`) matches the actual services that consume the role — `s3.amazonaws.com` is correct for live replication config, `batchoperations.s3.amazonaws.com` for batch; both are justified here.

### WR-05: `run_batch_replication` does not validate that a live replication config exists before creating the batch job

**File:** `modules/step-functions/s3/run_batch_replication.asl.json:17-108`
**Issue:** `S3ReplicateObject` batch jobs require an existing live `ReplicationConfiguration` on the source bucket (the batch job replicates per the configured rules). The state machine creates the job directly with no precondition check. If `run_batch_replication` is invoked before `setup_cross_account_replication` (or after a failed/partial setup per WR-01), `createJob` may succeed but the job will fail at runtime, or `createJob` errors with a non-obvious message. The EFS module validates state before acting.

**Fix:** Add a `getBucketReplication` precondition Task (assuming the source role) before `ShouldEnableReport`, with a Choice that fails fast with a clear `NoReplicationConfigForBatch` error if no rules exist. At minimum document the ordering contract in the Comment so the orchestrator enforces setup-before-run.

### WR-06: `local.role_id` falls back to `existing_role_name` for the ARN-less branch, which is correct only for `aws_iam_role_policy.role`

**File:** `modules/source-account/main.tf:31-33`
**Issue:** `role_id = var.create_role ? aws_iam_role.source[0].id : var.existing_role_name`. For an IAM role, `.id` equals the role name, so this happens to work for the `role = local.role_id` attribute on the inline policies. But `role_arn` uses `existing_role_arn` while `role_id`/`role_name` use `existing_role_name` — if a caller sets `create_role=false` and provides `existing_role_arn` but forgets `existing_role_name` (only required "if attach_policies"), `should_attach_policies` is false so no policy is attached, yet `s3_replication`/`backup_efs`/`efs_replication` roles (gated only on `enable_s3`/`enable_efs`, NOT on `should_attach_policies`) are still created and reference `local.account_id` — fine — but the `PassRoleToS3Replication` statement in `s3_access` references `${local.prefixes.iam_role}-s3-replication-role` by name, which only matches the created `s3_replication` role name. If a client supplies their own replication role with a different name, PassRole will silently not cover it. There is no validation linking these.

**Fix:** Add an input validation (or `precondition`) asserting that when `create_role=false` and `attach_policies=true`, `existing_role_name != null`; and document that `enable_s3` always creates the `-s3-replication-role` whose name the PassRole statement is hard-bound to. If clients may bring their own replication role, parameterize the PassRole resource.

## Info

### IN-01: Dead/empty section header left in source-account main.tf

**File:** `modules/source-account/main.tf:534-537`
**Issue:** The "EFS Replication Role" banner comment block (lines 534-537) is immediately followed by the "AWS Backup Role" banner with no resource between them — the EFS replication role is actually defined later at line 607. The orphaned header is misleading.
**Fix:** Remove the stray banner at 534-537 or move it adjacent to `aws_iam_role.efs_replication` at line 607.

### IN-02: `Filter.Prefix: ''` empty-prefix filter is intentional but undocumented as a "replicate all" choice

**File:** `modules/step-functions/s3/setup_cross_account_replication.asl.json:101`
**Issue:** `'Filter': {'Prefix': $exists($dest.Prefix) ? $dest.Prefix : ''}` uses an empty-string prefix to mean "replicate everything". This is valid V2 syntax, but `{"Filter": {"Prefix": ""}}` vs `{"Filter": {}}` have historically had subtle differences in how AWS normalizes them, which can cause spurious Terraform/SFN drift if the config is ever read back and compared.
**Fix:** Confirm with a real `PutBucketReplication` + `GetBucketReplication` round-trip that `Prefix: ""` reads back identically; otherwise emit `{"Filter": {}}` when no prefix is supplied.

### IN-03: Magic polling/threshold values are hardcoded

**File:** `modules/step-functions/s3/check_batch_replication.asl.json:8`, `setup_cross_account_replication.asl.json:101`
**Issue:** `Wait.Seconds: 30` (poll interval) and the RTC/Metrics `Minutes: 15` thresholds are hardcoded in the ASL. The D-11 comment acknowledges the poll interval is discretionary, but these are not surfaced as inputs. 15-minute RTC is the only valid value for S3 RTC, so that one is fine; the poll interval is a reasonable candidate for an input if callers ever need faster feedback.
**Fix:** Optional — leave as-is given file()-based (non-templated) definitions; note in README that the poll interval is fixed at 30s.

### IN-04: setup `BucketName` parsing assumes ARN/path form, no guard against empty `Destinations[]`

**File:** `modules/step-functions/s3/setup_cross_account_replication.asl.json:101`; `delete_replication.asl.json:38-39`
**Issue:** `$bucketName := $bucketParts[$count($bucketParts) - 1]` derives the rule-ID basename by splitting `$dest.Bucket` on `/`. If `Destinations[]` is empty, the `Map` simply does nothing and reports success (setup) — arguably fine, but worth an explicit guard/choice so an empty-destinations invocation isn't silently a no-op success. In `delete_replication`, an empty `Destinations[]` means `$removeIds` is empty, so `remainingRules` == all existing rules and the config is re-put unchanged — a no-op write rather than the likely-intended "nothing to delete".
**Fix:** Add a leading Choice on `$count($.Destinations) = 0` that short-circuits to a clear `NoDestinationsProvided` Pass/Succeed in both setup and delete.

---

_Reviewed: 2026-06-19_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: standard_
