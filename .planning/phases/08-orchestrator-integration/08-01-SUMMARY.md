---
phase: 08-orchestrator-integration
plan: 01
subsystem: infra
tags: [step-functions, asl, orchestrator, s3, cross-account-replication, sync2, terraform]

# Dependency graph
requires:
  - phase: 07 s3-replication-module
    provides: 4 frozen S3 SFN (setup/run_batch/check_batch/delete) + module step_function_arns outputs
  - phase: orchestrator EFS weave
    provides: Phase1DataRefresh Parallel pattern, CheckEFSEnabled self-guarded branch, startExecution.sync:2 idiom
provides:
  - optional S3 replication branch in refresh_orchestrator.asl.json (mirror of EFS, ORCH-04)
  - strict no-op guard CheckS3Enabled (ORCH-05 — zero behavioral diff when S3 absent/disabled)
  - root-level module "step_functions_s3" instantiation (Style A ARN wiring)
affects: [09 spec repl-s3-sync + ASL validation coverage, client wiring of $.StepFunctions.S3.* input block (out of scope)]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Self-guarded Parallel branch: a new branch in an existing Parallel whose first state is a Choice (CheckS3Enabled) that Defaults to a no-op Succeed — adds optional behavior with zero diff to the reachable-set when disabled"
    - "In-orchestrator reshape (D-10): Pass/Input Arguments map the EFS-mirror S3 input block to the frozen Phase-7 SFN contract without touching the SFN ASL"
    - "sync:2 envelope re-extraction: $.S3BatchResult.Output.JobId -> S3PrepareJobId Pass -> $.S3BatchJob.JobId consumed by check_batch (BatchJob.JobId)"
    - "Style A ARN wiring: sub-SFN ARNs transit via execution-input ($.StepFunctions.S3.*) like EFS/DB/EKS, NOT via templatefile — no orchestrator Terraform module change"

key-files:
  created: []
  modified:
    - modules/step-functions/orchestrator/refresh_orchestrator.asl.json
    - main.tf

key-decisions:
  - "Host Parallel = Phase1DataRefresh (D-02 discretion resolved): the EFS branch C (CheckEFSEnabled -> Succeed) is the exact self-guarded precedent; S3 added as Branch D, branches A/B/C and the Parallel ResultPath/Next/Catch untouched"
  - "Guard inverts the EFS Default polarity for strict no-op (D-03/ORCH-05): CheckS3Enabled requires And($.S3.Enabled IsPresent:true, BooleanEquals:true); absent key fails IsPresent -> Default -> S3ReplicationComplete (Succeed). No S3 op reachable without explicit S3.Enabled=true"
  - "setup always runs when enabled (D-04); backfill gated by CheckS3BackfillEnabled sub-toggle on $.S3.Backfill.Enabled (D-05)"
  - "No teardown (D-06): orchestrator never calls delete_replication; S3 replication is persistent"
  - "No manifest/override field passed to S3RunBatch (D-08): NONE/FAILED filter stays in the frozen SFN -> re-run = delta only"
  - "Style A wiring: root main.tf instantiates module step_functions_s3 (6 inputs mirroring step_functions_efs); no s3_step_function_arns var added to the orchestrator module"

patterns-established:
  - "Optional-phase weave with strict no-op guard inside an existing Parallel branch"
  - "Frozen-contract integration via in-orchestrator reshape (caller-facing API stays EFS-shaped; sub-SFN contract unchanged)"

requirements-completed: [ORCH-04, ORCH-05]

# Metrics

tasks: 2
commits:
  - 0f149c3 feat(08-01) orchestrator ASL weave (Task 1)
  - 82eed0b feat(08-01) root step_functions_s3 module (Task 2)
files-changed: 2 (+161 lines)

---

# Plan 08-01 Summary — Orchestrator S3 Weave

## What was built

Wove the optional S3 cross-account replication phase into `refresh_orchestrator.asl.json` as **Branch D** of the `Phase1DataRefresh` Parallel, mirroring the existing EFS branch. The branch is fully self-guarded: a run without an `S3` block (or with `S3.Enabled=false`) hits `CheckS3Enabled` → `S3ReplicationComplete` (no-op `Succeed`) immediately, leaving the reachable-set strictly identical to before (ORCH-05). When `S3.Enabled=true`, the branch runs `setup` (always) and, behind the `S3.Backfill.Enabled` sub-toggle, `run_batch` → `check_batch`, all via `startExecution.sync:2`, with the input reshaped in-orchestrator to the frozen Phase-7 contracts. The root `main.tf` instantiates `module "step_functions_s3"` (Style A — no orchestrator-module change).

### States added (Branch D)
`CheckS3Enabled` (Choice) · `S3SetupReplication` (Task sync:2) · `CheckS3BackfillEnabled` (Choice sub-toggle) · `S3RunBatch` (Task sync:2) · `S3PrepareJobId` (Pass — JobId re-extraction) · `S3CheckBatch` (Task sync:2) · `S3ReplicationComplete` (Succeed) · `S3ReplicationFailed` (Fail). No `delete_replication`.

## Verification evidence (gates run from repo root)

| Gate | Result |
|------|--------|
| `python3 scripts/validate_asl.py …/refresh_orchestrator.asl.json` | **exit 0, ✓ Valid** — 0 errors/0 warnings, refs resolve, no orphans (ORCH-05 reachability holds) |
| `tofu init -backend=false` + `tofu validate` | **Success! The configuration is valid.** — new module registered |
| `pytest tests/test_asl_validation.py tests/test_interface_snapshots.py` | **987 passed, 1 failed** (the 1 failure is pre-existing EFS dette, see below) |
| `pytest …[refresh_orchestrator]` (both snapshot tests) | **PASSED** |

### No snapshot regeneration needed (Task 2 acceptance refinement)

Task 2 anticipated that adding S3 terminal states would diverge `tests/snapshots/refresh_orchestrator_outputs.json`, requiring a delete+regen. It did **not**: the new S3 `Succeed`/`Fail` states live **inside** the `Phase1DataRefresh` Parallel branch, not as top-level orchestrator terminal outputs, so the captured interface schema is unchanged and `test_interface_snapshots.py[refresh_orchestrator]` already passes. The snapshot was left untouched — Task 2's acceptance criterion ("test_interface_snapshots.py passes") is satisfied as-is.

## Deviations / notes

- **`tofu init` was required** before validate, because the new local `module "step_functions_s3"` was not yet registered. Used `-backend=false` (validation only, no remote state touched).
- **Permission gate during execution:** the executor sandbox denied all `python3`/`terraform`/`tofu` invocations; the orchestrator ran the verification gates and confirmed green before commit. No work was committed unverified.
- **Out-of-scope observation (pre-existing dette):** `test_interface_snapshots.py[setup_cross_account_replication]` fails — the **EFS** module's stale snapshot (last committed `d10b8e5`, Phase 2), already flagged in project notes as "à régénérer hors de cette phase". Not caused by Phase 8; left untouched.

## Requirements

- **ORCH-04** ✓ — optional S3 input block (Enabled/Source/Destinations[]/Replication/Backfill, EFS-mirror) drives the S3 replication phase via in-orchestrator reshape.
- **ORCH-05** ✓ — `CheckS3Enabled` guard reduces the branch to a no-op `Succeed` when absent/disabled; reachable-set unchanged, proven by `validate_asl.py`.

## Self-Check: PASSED
