---
phase: 08-orchestrator-integration
reviewed: 2026-06-23T10:00:00Z
depth: deep
files_reviewed: 2
files_reviewed_list:
  - modules/step-functions/orchestrator/refresh_orchestrator.asl.json
  - main.tf
findings:
  critical: 1
  warning: 2
  info: 1
  total: 4
status: resolved
resolution:
  cr-01: "FIXED in 2c6b5de — $.S3 forwarded through MergePrepareResults via CheckS3Present/InjectS3Default normalisation"
  wr-01: "deferred — mixed JSONPath/JSONata, advisory (mirrors existing file state)"
  wr-02: "deferred — no ResultSelector trim on S3*Result, advisory (DataLimit risk only at extreme scale)"
---

> **RESOLUTION (2026-06-23):** The BLOCKER **CR-01** was fixed in commit `2c6b5de` —
> `$.S3` is now forwarded through `MergePrepareResults` (`"S3.$": "$.S3"`), guarded by a
> `CheckS3Present` Choice + `InjectS3Default` Pass that normalise an absent S3 block to
> `{Enabled:false}` so legacy inputs do not crash the hard reference (preserves ORCH-05).
> Re-validated: `validate_asl.py` ✓, 987 tests pass, `refresh_orchestrator` snapshot unchanged.
> The two WARNINGs (WR-01 mixed query-language, WR-02 ResultSelector trim) are advisory and
> deferred — neither affects ORCH-04/ORCH-05 correctness.

# Phase 8: Code Review Report

**Reviewed:** 2026-06-23T10:00:00Z
**Depth:** deep
**Files Reviewed:** 2
**Status:** issues_found

## Summary

Phase 8 weaves an optional S3 cross-account replication branch (Branch D) into the `Phase1DataRefresh` Parallel of `refresh_orchestrator.asl.json`, and instantiates `module "step_functions_s3"` in `main.tf` (Style A). The structural intent is sound: the self-guarded `CheckS3Enabled` choice, the state topology, the JobId re-extraction chain, and the frozen-contract reshaping are all correctly implemented in isolation. The Terraform module block is a correct mirror of `step_functions_efs`.

One **critical bug** makes ORCH-04 non-functional on every normal execution path: the pre-existing `MergePrepareResults` Pass state rewrites the effective state using `Parameters` (which enumerates keys explicitly) and does not include `$.S3`. The `$.S3` key is therefore absent from the state when `Phase1DataRefresh` is reached on the `PrepareRefresh` success path. `CheckS3Enabled` evaluates `$.S3.Enabled IsPresent: false` → Default → `S3ReplicationComplete` (no-op), silently discarding the caller's intent regardless of what they set in the execution input.

Two warnings concern query-language consistency and an error-path state-loss edge case. One info item flags a minor naming deviation from the EFS analog.

---

## Critical Issues

### CR-01: `MergePrepareResults` drops `$.S3` — ORCH-04 silently broken on every normal execution

**File:** `modules/step-functions/orchestrator/refresh_orchestrator.asl.json:146`

**Issue:** The `MergePrepareResults` Pass state (line 146) uses a `Parameters` block that exhaustively lists every key to forward. In Step Functions, a Pass state with `Parameters` (and no `OutputPath`) replaces the effective state with exactly those keys — keys not listed are dropped. The `$.S3` key from the execution input is absent from this `Parameters` map. As a result, every execution that goes through `PrepareRefresh` (success path) → `MergePrepareResults` enters `Phase1DataRefresh` without `$.S3`. `CheckS3Enabled` then evaluates `$.S3.Enabled IsPresent` → `false` → Default → `S3ReplicationComplete` (immediate no-op Succeed). S3 replication is silently skipped on every normal execution regardless of `S3.Enabled=true`.

This makes ORCH-04 non-functional. The `PrepareRefresh` error path (`Catch → Phase1DataRefresh` directly, line 142) does preserve `$.S3` — so S3 replication would only trigger on a PrepareRefresh failure, which is the wrong condition.

The same omission affects any downstream state that reads `$.S3.*` (all of which are inside Branch D, so the blast radius is contained).

**Flow diagram of the bug:**

```
ExecutionInput ($.S3.Enabled=true)
  → PrepareRefresh (ResultPath: $.PrepareResult — $.S3 preserved ✓)
  → MergePrepareResults (Parameters: { Database, EFS, EKS, Tags, ... } — no S3 key ✗)
  → CheckEFSReplicationMode → Phase1DataRefresh
       Branch D: CheckS3Enabled
         $.S3.Enabled IsPresent → false (key absent)
         → Default → S3ReplicationComplete (no-op Succeed)
```

**Fix:** Add `"S3.$": "$.S3"` to `MergePrepareResults.Parameters`. Because `$.S3` is optional (callers that don't use S3 replication won't set it), the reference must handle the absent-key case. In JSONPath mode, `Parameters` will throw a runtime error if `$.S3` is absent. Use a conditional approach — either split into two Pass states (one for S3-present, one for S3-absent via a preceding Choice) or adopt the JSONata merge idiom already used elsewhere in the file:

Option A — JSONata merge (follows the `RestoreDatabase`/`DeleteEFSReplication` pattern already in this file):

```json
"MergePrepareResults": {
  "Type": "Pass",
  "QueryLanguage": "JSONata",
  "Output": "{% $merge([$states.input, {
    'ResourceNames':        $states.result.ResourceNames,
    'CurrentDatabaseTags':  $states.result.CurrentDatabaseTags,
    'CurrentEfsTags':       $states.result.CurrentEfsTags,
    'OldEfsFileSystemId':   $states.result.OldEfsFileSystemId,
    'MergedDatabaseTags':   $states.input.Tags,
    'MergedEfsTags':        $states.input.Tags,
    'ComputedDumpSubPath':  $states.context.Execution.Name & '-...'
  }]) %}",
  ...
}
```

Option B — minimal fix in JSONPath mode, guarded by a preceding `CheckS3Present` Choice that routes to two `MergePrepareResults` variants (with and without `"S3.$": "$.S3"`). This is verbose but stays in pure JSONPath.

Option C — minimal JSONPath fix using `ConfigSync` as precedent: note that `ConfigSync.$` is already forwarded in `MergePrepareResults` (line 164). `ConfigSync` faces the same optional-key problem and is already listed. If the project convention is to require optional blocks to always be present in the input (even as `null` or an empty object), then simply adding `"S3.$": "$.S3"` is sufficient and consistent with the existing `ConfigSync` pattern. Confirm with the caller-side contract.

---

## Warnings

### WR-01: Mixed query-language context in `S3SetupReplication.Input` — JSONPath `Parameters` vs enclosing JSONata branch states

**File:** `modules/step-functions/orchestrator/refresh_orchestrator.asl.json:1141`

**Issue:** `S3SetupReplication` (and all Branch D states) uses JSONPath mode (`Parameters`, `ResultPath`) without a `QueryLanguage` override. Several adjacent states in the same `Phase1DataRefresh` Parallel — `RestoreDatabase` (Branch B, line 772) and `CreateEFSMountTargets_Repl` / `DeleteEFSReplication_Sync` (other Parallels) — use `"QueryLanguage": "JSONata"`. Within a given branch, all states share the same effective state context. This is not a correctness bug (JSONPath and JSONata states can coexist in a Parallel's branches), but it introduces a maintenance hazard: a future editor copying a Branch D state as a template may inadvertently omit or add `QueryLanguage` inconsistently.

More concretely, the file now uses JSONata's `$merge` idiom to handle optional fields (e.g., `SetupCrossAccountReplication_Repl` builds the input with `$exists` guards). `S3SetupReplication.Input` passes `SourceAccount.$: "$.S3.Source.Account"` as a hard JSONPath reference — if `$.S3.Source.Account` is absent (which happens when `$.S3` is dropped by `MergePrepareResults`, per CR-01, or if the caller omits the Account sub-key), this throws a `States.Runtime` error rather than a clean Catch-able failure. The existing EFS analog `SetupCrossAccountReplication` (line 924) passes mandatory fields the same way, so this is consistent with the pre-existing pattern — but the missing `$.S3` propagation (CR-01) makes this a latent crash path.

**Fix:** Ensure CR-01 is fixed first (so `$.S3` is always present when `S3.Enabled=true`). If the project decides to standardize Branch D on JSONata (to gain `$exists` guards for optional sub-fields), add `"QueryLanguage": "JSONata"` to each Branch D Task and switch to the `Arguments`/`Output` idiom — but only do so consistently across the whole branch. Do not mix JSONPath and JSONata within the same branch.

---

### WR-02: `S3SetupReplication` has no `ResultSelector` — entire `.sync:2` API envelope is captured in `$.S3SetupResult`

**File:** `modules/step-functions/orchestrator/refresh_orchestrator.asl.json:1141`

**Issue:** `S3SetupReplication` uses `ResultPath: "$.S3SetupResult"` with no `ResultSelector`. With `startExecution.sync:2`, the raw captured result includes `Output`, `Status`, `Name`, `StartDate`, `StopDate`, `ExecutionArn`, `StateMachineArn`, `Input`, etc. — the full `DescribeExecution` API response. This balloons the effective state unnecessarily. The orchestrator never reads `$.S3SetupResult.*` downstream (the setup contract has no structured output the orchestrator consumes), so the captured data is pure dead weight in the state.

This is not a correctness bug: the branch succeeds regardless. However, if the execution input + state approaches the 256 KB Step Functions state size limit (a realistic concern for orchestrators that accumulate results across many parallel branches), this unnecessarily large capture could contribute to a `States.DataLimitExceeded` error.

For comparison, `S3RunBatch` and `S3CheckBatch` correctly capture `$.S3BatchResult` and `$.S3CheckResult` — but `$.S3BatchResult.Output.JobId` is the only field ever consumed downstream. A `ResultSelector` would trim both to just the needed field.

**Fix:** Add `ResultSelector` to `S3SetupReplication` to discard the unused output, mirroring the pattern used by `ValidateInputs` (line 57) and `PrepareRefresh` (line 128) which both use `ResultSelector` to extract only what they need:

```json
"S3SetupReplication": {
  ...
  "ResultSelector": {
    "Status": "SETUP_COMPLETE"
  },
  "ResultPath": "$.S3SetupResult",
  ...
}
```

Or use `ResultPath: null` to discard entirely (since nothing reads `$.S3SetupResult`):

```json
"ResultPath": null,
```

Consider applying the same trimming to `S3CheckBatch` (`$.S3CheckResult` is also never read downstream).

---

## Info

### IN-01: Execution name collision risk — `S3RunBatch` reuses `{}-S3RunBatch` format identical to what a naive re-run would produce

**File:** `modules/step-functions/orchestrator/refresh_orchestrator.asl.json:1199`

**Issue:** `S3RunBatch.Name.$: "States.Format('{}-S3RunBatch', $$.Execution.Name)"` produces a child execution name deterministic from the parent execution name, which is the established convention in this file. This is correct for idempotency. However, `run_batch_replication` internally uses `States.UUID()` as `ClientRequestToken` for the S3 Batch job — so the batch job itself is idempotent at the S3 API level. No bug, but the child execution name uniqueness depends on the parent execution name being unique, which is Step Functions' responsibility. No action required, noting for awareness.

Also, the `S3RunBatch` comment says "the NONE/FAILED filter stays inside the frozen SFN (D-08)." A grep confirms zero occurrences of `ManifestGenerator`, `ObjectReplicationStatuses`, or any manifest-override field in the Branch D states. D-08 compliance verified. ✓

**Fix:** No action required. Noted for completeness.

---

## Verification Checklist

| Concern from review scope | Finding |
|---------------------------|---------|
| ORCH-05 no-op: `CheckS3Enabled` Default reaches `S3ReplicationComplete` when `$.S3` absent | Correct in isolation — but the path to `Phase1DataRefresh` via `MergePrepareResults` **drops** `$.S3`, so no-op fires even when caller sets `S3.Enabled=true` (CR-01) |
| State graph integrity: `Next`/`Default`/`Catch` all resolve | Verified. All 8 Branch D states have valid transitions. Branch D added as `Branches[3]`, not disturbing existing indices 0/1/2. `$.Phase1Results[1]` and `[2]` references downstream remain correct. |
| JobId re-extraction: `$.S3BatchResult.Output.JobId` → `$.S3BatchJob.JobId` → `BatchJob.JobId` | Chain is correct. `run_batch_replication` emits `$.Output.JobId` in its terminal Pass; `.sync:2` places the sub-execution output at `$.S3BatchResult`; `S3PrepareJobId` extracts it to `$.S3BatchJob.JobId`; `S3CheckBatch.Input.BatchJob.JobId` consumes it. |
| D-10 frozen-contract reshape: exact field names | All three Tasks (`S3SetupReplication`, `S3RunBatch`, `S3CheckBatch`) pass the correct frozen Phase-7 field names. `SourceAccount` passed as the full object (contains `AccountId` + `RoleArn` consumed by sub-SFN `Credentials.RoleArn.$`). |
| D-06: no `delete_replication` call | Confirmed. No reference to `delete_replication` or `DeleteReplication` in Branch D. |
| D-08: no manifest/override fields in `S3RunBatch.Input` | Confirmed. No `ManifestGenerator`, `ObjectReplicationStatuses`, or manifest fields in Branch D. |
| No `Credentials` on orchestrator S3 Tasks | Confirmed. All three S3 Tasks use `startExecution.sync:2`; none carry a `Credentials` block. Cross-account assume-role happens inside each frozen sub-SFN. |
| Terraform: `module "step_functions_s3"` mirrors `step_functions_efs` | Confirmed. Same 6 inputs, same values. `naming_convention` defaults to `"pascal"` in both (not passed in either module block). |
| Style A: no `s3_step_function_arns` in orchestrator module | Confirmed. Not in `modules/step-functions/orchestrator/variables.tf` or `module "orchestrator"` block. |
| JSONPath/JSONata consistency within Branch D | Branch D uses JSONPath throughout (`Parameters`/`ResultPath`). Consistent within the branch. No rogue `QueryLanguage` override. |

---

_Reviewed: 2026-06-23T10:00:00Z_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: deep_
