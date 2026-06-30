# Phase 8: Orchestrator Integration - Pattern Map

**Mapped:** 2026-06-22
**Files analyzed:** 3 modified targets (1 ASL, 1 orchestrator Terraform module, 1 root main.tf) + 4 frozen S3 contract inputs
**Analogs found:** 3 / 3 (all exact, in-repo)

> Goal: weave optional S3 cross-account replication into `refresh_orchestrator.asl.json`, mirroring the existing **EFS weave** (Parallel + Choice guard + `startExecution.sync:2`). All targets have an exact in-repo analog. The S3 SFN contracts (Phase 7) are FROZEN — extract their input shapes only, never modify them.

---

## File Classification

| Modified File | Role | Data Flow | Closest Analog | Match |
|---------------|------|-----------|----------------|-------|
| `modules/step-functions/orchestrator/refresh_orchestrator.asl.json` | orchestrator (ASL state machine) | event-driven / sub-SFN orchestration | EFS weave in same file (`CheckEFSReplicationMode` → `Phase1WithReplication`, `CheckEFSEnabled`) | exact |
| `modules/step-functions/orchestrator/main.tf` + `variables.tf` | config (Terraform module, templatefile wiring) | transform (ARN injection) | ConfigSync bolt-on wiring (`sync_config_items_arn` flat var) **and** EFS input-block style | role-match (two styles, see notes) |
| `main.tf` (root) | config (Terraform root wiring) | transform | `module "orchestrator"` block passing `efs_step_function_arns` | exact |

**Frozen contracts (READ-ONLY — input shape extraction only, DO NOT modify):**

| File | Role | Expected Input Shape |
|------|------|----------------------|
| `modules/step-functions/s3/setup_cross_account_replication.asl.json` | service | `SourceBucket`, `Destinations[]`, `ReplicationRoleArn`, `SourceAccount.RoleArn` |
| `modules/step-functions/s3/run_batch_replication.asl.json` | service | `SourceBucket`, `SourceBucketArn`, `BatchReplicationRoleArn`, `ReportBucketArn?`, `SourceAccount.{AccountId,RoleArn}` |
| `modules/step-functions/s3/check_batch_replication.asl.json` | service | `BatchJob.JobId`, `SourceAccount.{AccountId,RoleArn}` |
| `modules/step-functions/s3/main.tf` / `outputs.tf` | config | outputs `step_function_arns` map (keys: `setup_cross_account_replication`, `run_batch_replication`, `check_batch_replication`, `delete_replication`) |

---

## Pattern Assignments

### Target 1 — `refresh_orchestrator.asl.json` (orchestrator ASL weave + guard)

**Analog:** the EFS weave already in this same file. Mirror it closely.

#### 1a. Activation guard (Choice short-circuit) — D-03 / ORCH-05 retrocompat

**Analog `CheckEFSReplicationMode`** (lines 169-198) routes to the Parallel weave only when EFS is enabled AND cross-account, else `Default` to the standard path (no-op):

```json
"CheckEFSReplicationMode": {
  "Type": "Choice",
  "Choices": [
    {
      "And": [
        { "Variable": "$.EFS.Enabled", "IsPresent": true },
        { "Variable": "$.EFS.Enabled", "BooleanEquals": true },
        { "Variable": "$.EFS.Source.Account.AccountId", "IsPresent": true },
        { "Not": { "Variable": "$.EFS.Source.Account.AccountId", "StringEqualsPath": "$.DestinationAccount.AccountId" } }
      ],
      "Next": "Phase1WithReplication"
    }
  ],
  "Default": "Phase1DataRefresh"
}
```

**Simpler in-branch guard analog `CheckEFSEnabled`** (lines 883-901) — a `Choice` at the head of a Parallel branch that `Succeed`s immediately (true no-op) when disabled:

```json
"CheckEFSEnabled": {
  "Type": "Choice",
  "Choices": [
    { "And": [
        { "Variable": "$.EFS.Enabled", "IsPresent": true },
        { "Variable": "$.EFS.Enabled", "BooleanEquals": false }
      ], "Next": "EFSRefreshComplete" }
  ],
  "Default": "CheckEFSCrossAccount"
}
```

**For S3 (D-03, D-09):** build `CheckS3Enabled` on `$.S3` present + `$.S3.Enabled == true`. Because the weave is a *new Parallel branch* (D-01/D-02), the cleanest retrocompat-safe shape is the `CheckEFSEnabled` style **inside the S3 branch**: head the branch with `CheckS3Enabled`, `Default`→ S3 work, the disabled/absent path → a `Succeed` (no-op) so a run without an `S3` block produces a strictly identical behavior diff of zero (critère #2). Do NOT gate on cross-account (S3 replication is always cross-account by design); gate only on `$.S3.Enabled`.

> Hard constraint (specifics): a run with no `S3` block must behave identically to today. Use `IsPresent` + `BooleanEquals: true` (the absent key fails `IsPresent`, falling to the no-op `Succeed`).

#### 1b. The Parallel weave — branch placement (D-01 / D-02)

**Analog `Phase1WithReplication`** (lines 199-431): a `Type: Parallel` with sibling branches running concurrently; one branch does mysqldump, another does the EFS replication chain (`SetupCrossAccountReplication_Repl` → `CheckReplicationSync_Repl` → ...). Parallel-level scaffolding:

```json
"Phase1WithReplication": {
  "Type": "Parallel",
  "Branches": [ /* ...sibling branches... */ ],
  "ResultPath": "$.ReplicationPhase1",
  "Next": "SynchronizedCutoffAndRestore",
  "Catch": [
    { "ErrorEquals": ["States.ALL"], "ResultPath": "$.Phase1Error", "Next": "CheckNotifyEnabled_Failure" }
  ]
}
```

**Note the simplest in-repo precedent for an optional branch:** `Phase1DataRefresh` (lines 649-1129) is a 3-branch Parallel where branch C (`CheckEFSEnabled` → EFS chain) is fully self-guarded and `Succeed`s when disabled. **Recommended for S3:** add the S3 replication chain as a new self-guarded branch (`CheckS3Enabled` head → no-op `Succeed` when off) inside an existing long-running Parallel (e.g. `Phase1DataRefresh`), so setup+backfill overlap the refresh phases (D-01) and a disabled S3 adds an instantly-succeeding branch = zero behavior change. The exact host Parallel is plan discretion (D-02), but `Phase1DataRefresh` is the closest precedent.

#### 1c. `startExecution.sync:2` sub-SFN call shape

**Analog `SetupCrossAccountReplication`** (standard EFS branch, JSONPath style, lines 924-958) — the cleanest sync:2 Task to mirror (the `_Repl` variant at 274-295 uses JSONata `Arguments`; pick one style consistently):

```json
"SetupCrossAccountReplication": {
  "Type": "Task",
  "Resource": "arn:aws:states:::states:startExecution.sync:2",
  "Parameters": {
    "StateMachineArn.$": "$.StepFunctions.EFS.SetupCrossAccountReplication",
    "Input": {
      "SourceFileSystemId.$": "$.EFS.Source.FileSystemId",
      "SourceAccount.$": "$.EFS.Source.Account",
      "DestinationAccount.$": "$.DestinationAccount",
      "...": "..."
    },
    "Name.$": "States.Format('{}-SetupReplication', $$.Execution.Name)"
  },
  "ResultPath": "$.ReplicationSetupResult",
  "Next": "CheckReplicationSyncInitial",
  "Catch": [
    { "ErrorEquals": ["States.ALL"], "ResultPath": "$.Error", "Next": "EFSRefreshFailed" }
  ]
}
```

Key elements to replicate for each S3 Task: `Resource: states:::states:startExecution.sync:2`, `StateMachineArn.$` from `$.StepFunctions.S3.*` (input-block style — see Target 2), unique `Name.$` via `States.Format('{}-...', $$.Execution.Name)`, a `ResultPath` to capture output, and a `Catch` → S3 `Fail` state.

> **`Credentials.RoleArn` note (code_context):** the orchestrator does NOT set `Credentials` on the sub-SFN `startExecution` Task — the role assumption to source/destination accounts happens *inside* each S3 SFN (`Credentials.RoleArn.$: $.SourceAccount.RoleArn`, see all S3 Tasks). The orchestrator only passes the payload (incl. `SourceAccount`) + the sub-SFN ARN.

#### 1d. The `$.Output.*` envelope + JobId re-extraction (code_context, D-10)

A `.sync:2` Task wraps the child's output under `$.<ResultPath>.Output.*`. EFS handles this at line 304 (`$.ReplicationSetupResult.Output.DestinationFileSystemId`) and 967.

**For S3 backfill the contract chain is:**
- `run_batch_replication` emits `$.Output.JobId` (run_batch `PrepareOutput`, lines 151-160) → after sync:2 with `ResultPath: $.BatchResult` it is at `$.BatchResult.Output.JobId`.
- `check_batch_replication` expects its JobId at **`$.BatchJob.JobId`** (check_batch `DescribeBatchJob`, line 18) plus `$.SourceAccount.*`.

So between run_batch and check_batch insert a `Pass`/`ResultSelector` reshape so check_batch's `Input` is `{ "BatchJob": { "JobId.$": "$.BatchResult.Output.JobId" }, "SourceAccount.$": "$.S3.Source.Account" }`. Mirror the `ResultSelector` + `ResultPath` re-extraction pattern used on EFS Tasks (e.g. `ValidateInputs` lines 57-61, `PrepareRefresh` lines 128-134).

#### 1e. Reshape to the frozen contract (D-10)

The `S3` input block is the EFS-mirror caller API: `S3: { Enabled, Source, Destinations[], Replication, Backfill: { Enabled } }`. The frozen SFN contracts need different field names, so reshape **in the orchestrator** (Pass states or per-Task `Arguments`). Two precedents:
- JSONPath per-Task `Input` mapping — `SetupCrossAccountReplication` (lines 928-945).
- JSONata `$merge` per-Task `Arguments` (conditional fields) — `_Repl` variant (line 281) and `RestoreDatabase` (line 777).

Required reshapes per S3 Task (from frozen contracts):

| S3 Task | Frozen input fields (from contract) | Source in `S3` block |
|---------|-------------------------------------|----------------------|
| `setup` | `SourceBucket`, `Destinations[]` (each `{Bucket, AccountId, RoleArn, Region, StorageClass?, Prefix?, RTC?, Metrics?, DeleteMarkerReplication?}`), `ReplicationRoleArn`, `SourceAccount.RoleArn` | `$.S3.Source.Bucket`, `$.S3.Destinations`, `$.S3.Replication.RoleArn`, `$.S3.Source.Account` |
| `run_batch` | `SourceBucket`, `SourceBucketArn`, `BatchReplicationRoleArn`, `ReportBucketArn?`, `SourceAccount.{AccountId,RoleArn}` | `$.S3.Source.Bucket` / `$.S3.Source.BucketArn`, `$.S3.Replication.BatchReplicationRoleArn`, `$.S3.Replication.ReportBucketArn?`, `$.S3.Source.Account` |
| `check_batch` | `BatchJob.JobId`, `SourceAccount` | reshaped from run_batch `$.Output.JobId` + `$.S3.Source.Account` |

#### 1f. Backfill sub-toggle (D-05)

Guard `run_batch` → `check_batch` behind `$.S3.Backfill.Enabled`. Mirror the `CheckEFSCrossAccount`/`CheckStoreInSSMOption` Choice idiom (lines 902-923 / 1059-1077): a `Choice` that `Next`s to `RunBatch` when `$.S3.Backfill.Enabled == true`, else `Default` to the branch's `Succeed` (skip backfill). `setup` always runs when `S3.Enabled` (D-04); only the batch pair is sub-toggled.

#### 1g. NO teardown (D-06)

Do NOT add any `delete_replication` call. Contrast the EFS path, which calls `DeleteEFSReplication_Sync` at cutoff (line 440) because EFS replication is temporary. S3 replication is persistent/live — `delete_replication` stays a manually-callable SFN, never invoked by the orchestrator. There is no S3 counterpart to `SynchronizedCutoffAndRestore`.

#### 1h. Manifest filter — do not regress (specifics)

`run_batch` already filters `ObjectReplicationStatuses: ["NONE","FAILED"]` + `EligibleForReplication: true` (lines 84-87, 128-131). This is what makes a re-run on already-synced buckets cheap (delta only). The orchestrator must NOT pass any input that disables/overrides this filter (the contract takes no manifest-override field; just don't add one).

---

### Target 2 — Orchestrator Terraform wiring (`main.tf` + `variables.tf` of the orchestrator module)

**CRITICAL FINDING — two ARN-injection styles coexist; the EFS/weave style needs NO module change.**

The `templatefile()` map vars `efs_step_function_arns`/`db_step_function_arns`/`eks_step_function_arns`/`utils_step_function_arns` (orchestrator `main.tf` lines 33-36) are **passed but NOT referenced** anywhere in the ASL. Verified: the ASL only interpolates `${cluster_switch_sequence_arn}` and `${sync_config_items_arn}` (2 occurrences total). The EFS/DB/EKS/Utils sub-SFN ARNs arrive at runtime via the **execution input** as `$.StepFunctions.EFS.*`, `$.StepFunctions.Database.*`, etc. (e.g. `$.StepFunctions.EFS.SetupCrossAccountReplication`, line 929).

**Two valid wiring styles for S3:**

**Style A — input-block (mirrors the chosen EFS weave; RECOMMENDED):** the caller/stack passes the S3 sub-SFN ARNs inside the execution-input `StepFunctions` block as `$.StepFunctions.S3.SetupCrossAccountReplication` / `RunBatchReplication` / `CheckBatchReplication`. ASL references them via `StateMachineArn.$: "$.StepFunctions.S3.SetupCrossAccountReplication"`. **No change to the orchestrator Terraform module** (no new templatefile var); the root only needs to make the S3 ARNs available to the caller building the execution input (Target 3). This is exactly how EFS works today.

**Style B — templatefile flat var (mirrors ConfigSync bolt-on):** add flat vars and reference them as `${...}` in the ASL. Analog in orchestrator `main.tf` (lines 38-40):

```hcl
# Flat ARN variables for sub-SFN calls in ASL (avoids map lookup syntax in JSON)
cluster_switch_sequence_arn = var.db_step_function_arns["cluster_switch_sequence"]
sync_config_items_arn       = lookup(var.sync_step_function_arns, "sync_config_items", "")
```

and in the ASL: `"StateMachineArn": "${sync_config_items_arn}"` (line 1603). For S3 this would mean adding `s3_step_function_arns` to orchestrator `variables.tf` (mirror `efs_step_function_arns`, lines 56-60) and three flat lookups in `main.tf`.

**Recommendation for the plan:** use **Style A** to stay consistent with the EFS weave model chosen in D-01. It keeps the orchestrator module untouched and the S3 ARNs flow through the same `StepFunctions` input contract as every other sub-SFN. (Plan may choose Style B if it prefers compile-time ARN baking like ConfigSync; D-discretion allows either, but A matches the analog.)

Analog `variables.tf` block to mirror only if Style B chosen:

```hcl
variable "efs_step_function_arns" {
  description = "Map of EFS Step Function ARNs"
  type        = map(string)
  default     = {}
}
```

---

### Target 3 — Root `main.tf` wiring

**Analog:** the `module "orchestrator"` block (root `main.tf` lines 117-134) wiring sub-SFN module outputs into the orchestrator:

```hcl
module "orchestrator" {
  source = "./modules/step-functions/orchestrator"
  # ...
  db_step_function_arns    = module.step_functions_db.step_function_arns
  efs_step_function_arns   = module.step_functions_efs.step_function_arns
  eks_step_function_arns   = module.step_functions_eks.step_function_arns
  utils_step_function_arns = module.step_functions_utils.step_function_arns
  sync_step_function_arns  = module.step_functions_sync.step_function_arns
}
```

There is currently **no `module "step_functions_s3"` block in the root `main.tf`** (the s3 module exists on disk but is not yet instantiated at root). Mirror an existing sub-SFN module block, e.g. `module "step_functions_efs"` (lines 42-52):

```hcl
module "step_functions_efs" {
  source = "./modules/step-functions/efs"
  prefix                = var.prefix
  tags                  = var.tags
  orchestrator_role_arn = module.iam.orchestrator_role_arn
  enable_logging      = var.enable_step_functions_logging
  log_retention_days  = var.log_retention_days
  enable_xray_tracing = var.enable_xray_tracing
}
```

The s3 module's `variables.tf` is already a drop-in match for these inputs (`prefix`, `tags`, `orchestrator_role_arn`, `enable_logging`, `log_retention_days`, `enable_xray_tracing`, `naming_convention`). Its `outputs.tf` exposes `step_function_arns` (map keyed by `setup_cross_account_replication`, `run_batch_replication`, `check_batch_replication`, `delete_replication`).

**For Style A wiring:** add `module "step_functions_s3"` and expose its `step_function_arns` to whatever builds the execution-input `StepFunctions.S3` block (caller/stack — out of phase scope per `<domain>`, but the root must instantiate the module so the ARNs exist). **For Style B:** also add `s3_step_function_arns = module.step_functions_s3.step_function_arns` to the `module "orchestrator"` block (mirror line 130).

---

## Shared Patterns

### `startExecution.sync:2` sub-SFN invocation
**Source:** `refresh_orchestrator.asl.json` lines 924-958 (`SetupCrossAccountReplication`)
**Apply to:** all 3 S3 Tasks. `Resource: arn:aws:states:::states:startExecution.sync:2`, `StateMachineArn.$` from `$.StepFunctions.S3.*`, unique `Name.$` via `States.Format`, `ResultPath` capture, `Catch` → `Fail`.

### `.sync:2` output envelope re-extraction
**Source:** lines 57-61 (`ValidateInputs` ResultSelector), line 304 (`...Output.DestinationFileSystemId`)
**Apply to:** run_batch → check_batch JobId hand-off (`$.<RP>.Output.JobId` → `BatchJob.JobId`).

### Choice activation guard (no-op short-circuit)
**Source:** `CheckEFSEnabled` lines 883-901 (in-branch, → `Succeed`) and `CheckEFSReplicationMode` lines 169-198 (mode router)
**Apply to:** `CheckS3Enabled` (ORCH-05 retrocompat) and `S3.Backfill.Enabled` sub-toggle.

### Per-Task input reshape to a frozen child contract
**Source:** JSONPath `Input` map (lines 928-945) or JSONata `$merge` `Arguments` (line 777 / 281)
**Apply to:** reshape `S3` block → frozen `setup`/`run_batch`/`check_batch` inputs (D-10).

### Credentials live inside the child SFN, not the orchestrator Task
**Source:** all S3 Tasks set `Credentials.RoleArn.$: $.SourceAccount.RoleArn`; orchestrator sync:2 Tasks set no `Credentials`.
**Apply to:** orchestrator passes `SourceAccount` in the payload; never sets `Credentials` on the S3 `startExecution` Tasks.

### Sub-SFN module wiring (Terraform)
**Source:** root `main.tf` lines 42-52 (`module "step_functions_efs"`) + 117-134 (orchestrator block)
**Apply to:** add `module "step_functions_s3"`; wire per chosen ARN-injection style.

---

## No Analog Found

None. Every Phase 8 target has an exact in-repo analog (EFS weave for the ASL, ConfigSync bolt-on as an alternate guard/wiring reference, existing sub-SFN module blocks for Terraform). Planner should rely on these analogs over RESEARCH.md generic patterns.

---

## Metadata

**Analog search scope:** `modules/step-functions/{orchestrator,efs,s3}/`, root `main.tf`
**Files scanned:** orchestrator ASL (1967 lines, targeted reads of EFS weave 169-431, standard Phase1 649-1129, ConfigSync 1480-1626), orchestrator `main.tf`/`variables.tf`, root `main.tf`, all 3 S3 contract ASLs + s3 `main.tf`/`outputs.tf`/`variables.tf`, efs `setup_cross_account_replication.asl.json` head
**Pattern extraction date:** 2026-06-22
