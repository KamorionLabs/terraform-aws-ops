# Phase 7: S3 Replication Module - Research

**Researched:** 2026-06-17
**Domain:** AWS Step Functions ASL — native SDK integrations for S3 / s3control (cross-account replication), Terraform module structure, source-account IAM
**Confidence:** HIGH

## Summary

Phase 7 builds `modules/step-functions/s3/` with 4 ASL state machines (no Lambda — D-09) plus an optional combined S3 replication role in `modules/source-account/`. The established EFS patterns transfer directly for: imperative assume-role (`Credentials.RoleArn.$`), Choice guards, `Catch -> Fail` states, Terraform module layout (`for_each` over a `step_functions` map of `file()`-loaded ASL), pascal/kebab naming, optional CloudWatch logging, and `step_function_arns` outputs. **None of those need re-research** and are reused verbatim.

The genuine deltas — all verified against authoritative AWS API docs — are the S3/s3control request/response shapes and the IAM model. S3 has **one** `ReplicationConfiguration` per source bucket (N Rules), and `PutBucketReplication` **replaces the entire configuration** every call. This is the structural driver behind D-01's Map-state read-merge-write pattern: read existing config via `GetBucketReplication`, replace/insert this destination's Rule (by a deterministic Rule ID), write the whole thing back. `GetBucketReplication` **throws `ReplicationConfigurationNotFoundError`** when no config exists — this dictates a `Catch` that seeds an empty `{Role, Rules:[]}` for the first destination. Batch backfill uses `s3control:createJob` with `Operation={"S3ReplicateObject":{}}` and a `S3JobManifestGenerator` (no S3 Inventory precondition); it returns a `JobId`, polled via `s3control:describeJob` whose `Job.Status` cycles through `New/Preparing/Suspended/Ready/Active/Pausing/Paused/Complete/Cancelling/Cancelled/Failing/Failed`.

**Primary recommendation:** Build 4 plain `file()`-loaded ASL state machines mirroring EFS's JSONPath style. For s3control states, pass `AccountId` explicitly (not auto-injected), set `ConfirmationRequired:false`, generate `ClientRequestToken` via `States.UUID()`, and use a Map state (`MaxConcurrency:1`) in setup/delete to iterate destinations against the single bucket replication config. Add the combined `s3_replication` role (trusted by both `s3.amazonaws.com` and `batchoperations.s3.amazonaws.com`) gated by a new `var.enable_s3`.

## User Constraints (from CONTEXT.md)

### Locked Decisions
- **D-01:** S3 has **one** `ReplicationConfiguration` per source bucket (N Rules, one per destination) — unlike EFS. `setup` uses a **Map state (`MaxConcurrency: 1`)** iterating destinations: `GetBucketReplication` -> merge/replace this destination's Rule -> `PutBucketReplication`. Lets a spoke be added/managed independently without touching existing rules. Idempotent, in the spirit of EFS's existing-config check.
- **D-02:** Rule-ID-to-destination keying convention (for the merge) is left to the plan's discretion.
- **D-03:** **Validate-only** versioning. `GetBucketVersioning`; if != `Enabled` -> explicit `Fail`. Never mutate the source bucket (owned by an external stack — the whole reason for imperative assume-role). Versioning is a precondition the client stack must satisfy.
- **D-04:** `delete_replication` is **symmetric** to setup: receives `Destinations[]`, `GetBucketReplication` -> remove those destinations' Rules -> `PutBucketReplication` (or `DeleteBucketReplication` if no rule remains). Teardown a spoke without breaking others.
- **D-05:** S3 options (RTC, Metrics, StorageClass, DeleteMarkerReplication) exposed as **optional per-destination input fields**, with safe defaults.
- **D-06:** `run_batch_replication` uses **S3 Batch Replication**: `s3control:CreateJob` with `Operation = S3ReplicateObject`, reusing the `ReplicationConfiguration` already posted by `setup` -> backfills to all configured destinations in **1 job**.
- **D-07:** Manifest = **`GeneratedManifest`** (S3 generates manifest at job creation, eligibility filter on non-replicated objects). No S3 Inventory precondition.
- **D-08:** `check_batch_replication` polls `s3control:DescribeJob` (native SDK) until completion (Active/Complete/Failed).
- **D-09:** **No Lambda** in this module. Sync-status read via native SDK states only: `GetBucketReplication` (rules `Enabled`) + `s3control:DescribeJob` (backfill status). Object-by-object compare is out of scope v1.2. **Cancelled, not deferred.**
- **D-10:** Optional S3 replication role (guarded by `var.enable_s3`, analogous `enable_efs`), **combined**: assumable by `s3.amazonaws.com` (live) **and** `batchoperations.s3.amazonaws.com` (batch). Orchestrator-assumed source role perms: `s3:PutBucketReplication`, `s3:GetBucketReplication`, `s3:GetBucketVersioning`, `s3control:CreateJob`, `s3control:DescribeJob`, `iam:PassRole` to the S3 replication role (`iam:PassedToService` condition). Exact list refined at plan.

### Claude's Discretion
- **D-11:** Exact input contract of the 4 SFN (mirror EFS `Source`/`Destination`/`Replication`: `SourceBucket` + `Destinations[]` with `AccountId`/`RoleArn`/`Bucket`/`Region`/replication-options + `ReplicationRoleArn`) — derived at plan, aligned to EFS.
- Rule-ID convention for the merge (D-02).
- `check_batch_replication` polling params (interval, timeout, backoff).
- Terraform module structure (lighter than `sync/` — no Lambda to package) — follows existing `step-functions/` modules.

### Deferred Ideas (OUT OF SCOPE)
- Object-by-object source/destination compare (S3 Inventory, object/size counts) — v2 (S3REPL-DR-02). **NOT** as the cancelled Lambda; reconceive on its own terms if needed.
- Replication state monitoring/alerting (lag, failed objects) on Dashborion — v2 (S3REPL-DR-01).
- Cross-region replication via Lambda proxy — v2 (S3REPL-DR-03).
- Destination-side grants (bucket/KMS policy), client wiring, orchestrator branching (Phase 8), spec + tests (Phase 9).

## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| REPL-01 | `setup_cross_account_replication` via `s3:PutBucketReplication`, imperative assume-role | PutBucketReplication shape (Architecture Pattern 1); read-merge-write Map; `GetBucketVersioning` validate-only |
| REPL-02 | `run_batch_replication` via `s3control:CreateJob` (S3ReplicateObject) | CreateJob shape + GeneratedManifest (Pattern 2); IAM batch role |
| REPL-03 | `check_batch_replication` polls `s3control:DescribeJob` | DescribeJob `Job.Status` enum + ProgressSummary (Pattern 3) |
| REPL-04 | `delete_replication` removes config (teardown) | Read-filter-write Map mirroring setup; DeleteBucketReplication when empty (Pattern 4) |
| REPL-05 | Fan-out hub-and-spoke, N destinations, same-region, independently configurable | Single ReplicationConfig with N Rules; Map MaxConcurrency:1 (D-01) |
| REPL-06 | Native SDK only, no Lambda; sync via GetBucketReplication + DescribeJob | Confirmed all 4 SFN realizable with `aws-sdk:s3:*` / `aws-sdk:s3control:*` |
| IAM-01 | Optional combined S3 replication role in source-account, `enable_s3` | Combined trust policy (Security Domain); mirror `efs_replication` block |
| IAM-02 | Source role perms incl `iam:PassRole` (`iam:PassedToService`) | s3control + PassRole condition values (Security Domain) |

## Architectural Responsibility Map

| Capability | Primary Tier | Secondary Tier | Rationale |
|------------|-------------|----------------|-----------|
| Setup live replication config | SFN (orchestrator-assumed source role) | S3 control plane (source bucket) | `PutBucketReplication` is a bucket-level config write; SFN assumes source role imperatively because the bucket is owned by an external stack |
| Versioning precondition check | SFN (read-only) | S3 control plane | `GetBucketVersioning`; validate-only, never mutate (D-03) |
| Backfill existing objects | SFN -> S3 Batch Operations | `batchoperations.s3.amazonaws.com` (assumes replication role) | `createJob` launches an async S3-managed job; the *job* assumes the replication role, not the SFN |
| Job status polling | SFN (read-only) | S3 control plane | `describeJob` native poll loop (Wait + Choice) |
| Teardown a spoke | SFN (source role) | S3 control plane | Symmetric read-merge-write; `DeleteBucketReplication` only when last rule removed |
| Live data replication execution | S3 service (`s3.amazonaws.com`) | Replication role | S3 performs the actual object copies using the role in the ReplicationConfiguration |
| Source replication IAM role | Terraform `modules/source-account` | — | Optional, `var.enable_s3`-gated, combined trust for both service principals |

## Standard Stack

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| Terraform / OpenTofu | `>= 1.0` (provider `hashicorp/aws >= 5.0`) | Module IaC | Matches existing `versions.tf` across all step-functions modules [VERIFIED: codebase `modules/step-functions/efs/versions.tf`] |
| AWS Step Functions ASL (Amazon States Language) | current | State machine definitions | Native SDK integrations preferred over Lambda per project locked decision (CLAUDE.md, D-09) |
| `aws-sdk:s3` integration | n/a (built-in) | `getBucketReplication`, `putBucketReplication`, `deleteBucketReplication`, `getBucketVersioning` | Step Functions optimized AWS SDK integration; Resource ARN form `arn:aws:states:::aws-sdk:s3:<action>` [CITED: docs.aws.amazon.com/step-functions/latest/dg/supported-services-awssdk.html] |
| `aws-sdk:s3control` integration | n/a (built-in) | `createJob`, `describeJob` | Resource ARN form `arn:aws:states:::aws-sdk:s3control:<action>`; `AccountId` passed explicitly [CITED: docs.aws.amazon.com/step-functions/latest/dg/supported-services-awssdk.html] |

### Supporting
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| pytest | existing (`tests/`) | ASL JSON validation auto-discovery via `rglob("*.asl.json")` | All new `*.asl.json` files auto-covered — no test wiring needed [VERIFIED: codebase `tests/test_asl_validation.py:18`] |

### Alternatives Considered
| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| `aws-sdk:s3control:createJob` | Lambda proxy calling boto3 | Rejected by D-09 (no Lambda); native integration covers all needs |
| Map read-merge-write per destination | One `putBucketReplication` with all Rules computed up front | Map keeps spokes independent/idempotent and mirrors EFS existing-config check (D-01) |
| GeneratedManifest | S3 Inventory report manifest or user CSV | GeneratedManifest needs no pre-existing inventory (D-07) — simpler, no client precondition |

**Installation:** No package installs. This is Terraform + ASL JSON only. No `npm`/`pip`/`cargo` dependencies are added by this phase.

## Package Legitimacy Audit

Not applicable — this phase installs **no external packages**. It adds Terraform `.tf` files, ASL `.asl.json` files, and IAM policy JSON inline in HCL. No npm/PyPI/crates dependencies are introduced. (pytest already exists in the repo's test harness and is unchanged.)

## Architecture Patterns

### System Architecture Diagram

```
ORCHESTRATOR (Phase 8, out of scope here)
   |  assumes source role (Credentials.RoleArn.$ = SourceAccount.RoleArn)
   v
+----------------------------------------------------------------+
| setup_cross_account_replication (SFN)                          |
|  GetBucketVersioning --[!=Enabled]--> Fail(VersioningNotEnabled)|
|        |  Enabled                                              |
|        v                                                       |
|  Map(Destinations[], MaxConcurrency:1):                        |
|    GetBucketReplication --[NotFound]--> seed {Role,Rules:[]}    |
|        |  (existing config)                                    |
|        v                                                       |
|    merge/replace Rule[ID=conv(dest)] with this dest's Rule     |
|        v                                                       |
|    PutBucketReplication(Role, Rules[all])  <-- REPLACES whole  |
|  --> Succeed                                                   |
+----------------------------------------------------------------+
                          | (config now live on source bucket)
                          v
+----------------------------------------------------------------+
| run_batch_replication (SFN)                                    |
|  s3control:CreateJob {                                         |
|     AccountId, Priority, ConfirmationRequired:false,           |
|     ClientRequestToken: States.UUID(),                         |
|     Operation:{S3ReplicateObject:{}},                          |
|     Report:{...}, RoleArn: <batch replication role>,           |
|     ManifestGenerator:{S3JobManifestGenerator:{                |
|        SourceBucket, ExpectedBucketOwner,                      |
|        Filter:{EligibleForReplication:true,                    |
|                ObjectReplicationStatuses:[NONE,FAILED]}}}}     |
|  --> result.JobId                                              |
+----------------------------------------------------------------+
        | JobId                       (job assumes batch repl role,
        v                              not the SFN -> replicates to
+----------------------------------+   ALL configured destinations)
| check_batch_replication (SFN)    |
|  Wait(interval)                  |
|   -> s3control:DescribeJob       |
|   -> Choice on Job.Status:       |
|       Active/Preparing/... -> Wait loop
|       Complete -> Succeed        |
|       Failed/Cancelled -> Fail   |
+----------------------------------+

delete_replication (SFN)  [symmetric to setup]
   Map(Destinations[], MaxConcurrency:1):
     GetBucketReplication --[NotFound]--> NoReplicationExists(Succeed-ish)
       -> filter out Rules for these destinations
       -> remaining Rules empty ? DeleteBucketReplication : PutBucketReplication(remaining)
```

### Recommended Project Structure
```
modules/step-functions/s3/
├── versions.tf                              # copy verbatim from efs/versions.tf
├── variables.tf                             # prefix, tags, orchestrator_role_arn, enable_logging,
│                                            #   log_retention_days, enable_xray_tracing, naming_convention
├── main.tf                                  # locals.step_functions map + for_each aws_sfn_state_machine + log group
├── outputs.tf                               # step_function_arns, step_function_names, log_group_arn
├── setup_cross_account_replication.asl.json
├── run_batch_replication.asl.json
├── check_batch_replication.asl.json
└── delete_replication.asl.json
```

No `templatefile()` is needed (no sub-SFN ARN injection, no Lambda ARN). Use plain `file()` like EFS's `local.step_functions` map. This is **lighter than `sync/`** — drop the `archive_file`, `aws_lambda_function`, `aws_lambda_permission`, lambda IAM role, and lambda log group entirely.

### Pattern 1: setup — read-merge-write a single replication config (D-01)

**What:** S3 stores ONE `ReplicationConfiguration` (Role + Rules[]) per bucket; `PutBucketReplication` REPLACES it wholesale. To add/manage one spoke without disturbing others, read the current config, replace this destination's Rule by a deterministic ID, write the full set back.

**When to use:** `setup_cross_account_replication` and (symmetrically) `delete_replication`.

**Example (JSONPath style, mirroring EFS):**
```jsonc
// GetBucketVersioning first — validate-only (D-03), never mutate source
"GetSourceVersioning": {
  "Type": "Task",
  "Resource": "arn:aws:states:::aws-sdk:s3:getBucketVersioning",
  "Parameters": { "Bucket.$": "$.SourceBucket" },
  "Credentials": { "RoleArn.$": "$.SourceAccount.RoleArn" },
  "ResultPath": "$.Versioning",
  "Next": "IsVersioningEnabled",
  "Catch": [{ "ErrorEquals": ["States.ALL"], "ResultPath": "$.Error", "Next": "SetupFailed" }]
},
"IsVersioningEnabled": {
  "Type": "Choice",
  "Choices": [{ "Variable": "$.Versioning.Status", "StringEquals": "Enabled", "Next": "ReadExistingConfig" }],
  "Default": "VersioningNotEnabled"
},
"VersioningNotEnabled": {
  "Type": "Fail", "Error": "SourceVersioningNotEnabled",
  "Cause": "Source bucket versioning must be Enabled before replication. The client stack must enable it; this module never mutates the source bucket."
},

// GetBucketReplication THROWS when no config exists -> Catch seeds empty config
"ReadExistingConfig": {
  "Type": "Task",
  "Resource": "arn:aws:states:::aws-sdk:s3:getBucketReplication",
  "Parameters": { "Bucket.$": "$.SourceBucket" },
  "Credentials": { "RoleArn.$": "$.SourceAccount.RoleArn" },
  "ResultPath": "$.Existing",
  "Next": "MergeRules",
  "Catch": [{
    "ErrorEquals": ["S3.ReplicationConfigurationNotFoundError", "S3.S3Exception"],
    "ResultPath": "$.ExistingError",
    "Next": "SeedEmptyConfig"   // -> { Role: ReplicationRoleArn, Rules: [] }
  }]
}
```
The actual rule merge (replace `Rules[ID==conv(dest)]`, append if absent) is cleanest in a JSONata `Assign`/`Output` block (`$filter` + array concat), exactly like the EFS `CleanSourcePolicyJSONata` state uses `$filter` over statements. Then `putBucketReplication` with `{ ReplicationConfiguration: { Role, Rules } }`.

**Note on Resource keys:** Step Functions SDK integration parameters are **PascalCase** (`Bucket`, `ReplicationConfiguration`, `Role`, `Rules`) and the action in the Resource ARN is **camelCase** (`getBucketReplication`). [VERIFIED: cross-checked against EFS ASL which uses `arn:aws:states:::aws-sdk:efs:describeFileSystems` + PascalCase `FileSystemId`]

### Pattern 2: run_batch_replication — CreateJob with GeneratedManifest (D-06, D-07)

**Example:**
```jsonc
"CreateBatchJob": {
  "Type": "Task",
  "Resource": "arn:aws:states:::aws-sdk:s3control:createJob",
  "Parameters": {
    "AccountId.$": "$.SourceAccount.AccountId",
    "ConfirmationRequired": false,
    "Priority": 1,
    "ClientRequestToken.$": "States.UUID()",
    "RoleArn.$": "$.BatchReplicationRoleArn",
    "Operation": { "S3ReplicateObject": {} },
    "Report": {
      "Enabled": true,
      "Bucket.$": "$.ReportBucketArn",
      "Prefix": "batch-replication-report",
      "Format": "Report_CSV_20180820",
      "ReportScope": "AllTasks"
    },
    "ManifestGenerator": {
      "S3JobManifestGenerator": {
        "ExpectedBucketOwner.$": "$.SourceAccount.AccountId",
        "SourceBucket.$": "$.SourceBucketArn",
        "EnableManifestOutput": false,
        "Filter": {
          "EligibleForReplication": true,
          "ObjectReplicationStatuses": ["NONE", "FAILED"]
        }
      }
    }
  },
  "Credentials": { "RoleArn.$": "$.SourceAccount.RoleArn" },
  "ResultSelector": { "JobId.$": "$.JobId" },
  "ResultPath": "$.BatchJob",
  "Next": "PrepareOutput",
  "Catch": [{ "ErrorEquals": ["States.ALL"], "ResultPath": "$.Error", "Next": "BatchJobFailed" }]
}
```
[VERIFIED: docs.aws.amazon.com/AmazonS3/latest/userguide/s3-batch-replication-existing-config.html — CLI `create-job --operation '{"S3ReplicateObject":{}}'`; CreateJob API requires AccountId, ClientRequestToken, Operation, Priority, Report, RoleArn] [CITED: docs.aws.amazon.com/AmazonS3/latest/API/API_control_CreateJob.html]

Critical facts:
- `ClientRequestToken` is **REQUIRED** (idempotency token, 1-64 chars). Generate with `States.UUID()`. Re-running with the same token throws `IdempotencyException`.
- `Report` is **REQUIRED** (the `JobReport` type) even though task results are optional — set `Enabled` true/false. A report bucket is needed when `Enabled:true`. **Open Question O1** below.
- `Manifest` and `ManifestGenerator` are **mutually exclusive** (Union — exactly one). Use `ManifestGenerator.S3JobManifestGenerator` (D-07).
- `ConfirmationRequired:false` so the job runs without console confirmation.
- `SourceBucket` / `Report.Bucket` are **bucket ARNs** (`arn:aws:s3:::name`), not names.
- The job must be created in the **same region as the source bucket**. Since v1.2 is same-region only (eu-central-1), the SFN region == source region.
- Response is just `{ "JobId": "<id>" }` — chain into DescribeJob.

### Pattern 3: check_batch_replication — DescribeJob poll loop (D-08)

```jsonc
"WaitForJob": { "Type": "Wait", "Seconds": 30, "Next": "DescribeBatchJob" },
"DescribeBatchJob": {
  "Type": "Task",
  "Resource": "arn:aws:states:::aws-sdk:s3control:describeJob",
  "Parameters": { "AccountId.$": "$.SourceAccount.AccountId", "JobId.$": "$.BatchJob.JobId" },
  "Credentials": { "RoleArn.$": "$.SourceAccount.RoleArn" },
  "ResultPath": "$.JobStatus",
  "Next": "EvaluateJobStatus"
},
"EvaluateJobStatus": {
  "Type": "Choice",
  "Choices": [
    { "Variable": "$.JobStatus.Job.Status", "StringEquals": "Complete",  "Next": "BatchSucceeded" },
    { "Variable": "$.JobStatus.Job.Status", "StringEquals": "Failed",    "Next": "BatchJobFailed" },
    { "Variable": "$.JobStatus.Job.Status", "StringEquals": "Cancelled", "Next": "BatchJobFailed" }
  ],
  "Default": "WaitForJob"
}
```
`Job.Status` enum (full set): `New | Preparing | Suspended | Ready | Active | Pausing | Paused | Complete | Cancelling | Cancelled | Failing | Failed`. Terminal states are `Complete`, `Cancelled`, `Failed`. `ProgressSummary` exposes `NumberOfTasksSucceeded`/`NumberOfTasksFailed`/`TotalNumberOfTasks` for richer output. `FailureReasons[]` carries `FailureCode`/`FailureReason`. [VERIFIED: docs.aws.amazon.com/AmazonS3/latest/API/API_control_DescribeJob.html]

Note: a `Suspended`/`Ready` state occurs when `ConfirmationRequired:true` — avoided by setting it false in CreateJob.

### Pattern 4: delete_replication — symmetric teardown (D-04)

Same read-merge-write Map, but **filter OUT** the Rules whose IDs match the destinations being removed. After filtering: if `Rules` is empty -> `deleteBucketReplication` (`arn:aws:states:::aws-sdk:s3:deleteBucketReplication`, `Parameters:{Bucket}`); else `putBucketReplication` with remaining Rules. Mirror EFS `delete_replication`'s `CheckCleanedPolicyEmpty` -> `DeleteSourcePolicy` vs `PutCleanedSourcePolicy` branch. Catch `ReplicationConfigurationNotFoundError` -> a `NoReplicationExists` Pass/Succeed (idempotent teardown).

### Anti-Patterns to Avoid
- **Mutating the source bucket beyond replication config:** D-03 forbids it. Only `PutBucketReplication`/`DeleteBucketReplication` writes; `GetBucketVersioning` is read-only. Never call `PutBucketVersioning`.
- **`putBucketReplication` with only the new Rule:** would wipe all other spokes (whole-config replace). Always read-merge-write.
- **`MaxConcurrency` > 1 in setup Map:** concurrent read-merge-write to one config = lost-update race. Keep `MaxConcurrency:1` (D-01).
- **Omitting `ClientRequestToken` / re-using it on retry:** missing -> validation error; reused -> `IdempotencyException`. Use a fresh `States.UUID()` per execution.
- **Passing bucket *names* where ARNs are required:** `SourceBucket`, `Destination.Bucket`, `Report.Bucket` are ARNs.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Backfill existing objects | Lambda enumerating + copying objects | `s3control:createJob` + `S3ReplicateObject` + GeneratedManifest | S3-native, parallel, reuses the live replication config, no compute to maintain (D-06, REPL-06) |
| Manifest of objects to replicate | S3 Inventory setup or custom CSV builder | `S3JobManifestGenerator` with `Filter.EligibleForReplication` | No inventory precondition; S3 generates + filters at job creation (D-07) |
| Job status tracking | Custom polling Lambda / EventBridge wiring | `s3control:describeJob` in a Wait+Choice loop | Pure ASL, no Lambda (D-08, D-09) |
| Sync status read | Lambda computing diff | `getBucketReplication` (rules Enabled) + `describeJob` | Native states cover the v1.2 need (D-09) |
| Idempotent multi-spoke config | Maintaining per-pair configs | Single config, Rule-per-destination, read-merge-write | Matches the S3 data model (one config/bucket, D-01) |

**Key insight:** S3 already provides server-side primitives (Batch Replication, GeneratedManifest, job describe) for everything this module needs. A Lambda would only add operational surface and contradict the locked "native SDK first" stance — which is exactly why D-09 cancelled it.

## Common Pitfalls

### Pitfall 1: GetBucketReplication throws when no config exists
**What goes wrong:** First-destination setup fails because `GetBucketReplication` errors instead of returning empty.
**Why it happens:** S3 returns `ReplicationConfigurationNotFoundError` (HTTP 404, mapped by Step Functions to an `S3.ReplicationConfigurationNotFoundError`-style error name) when the bucket has no replication config.
**How to avoid:** `Catch` that error and route to a "seed empty config" Pass state producing `{ Role: <ReplicationRoleArn>, Rules: [] }`, then proceed to the merge. Mirrors EFS `CheckExistingReplication` Catch -> default path.
**Warning signs:** setup works on an already-configured bucket but fails on a fresh one.
[VERIFIED: docs.aws.amazon.com/AmazonS3/latest/API/API_GetBucketReplication.html + drdroid.io / cloudquery issue #265 confirm the not-found error]

### Pitfall 2: PutBucketReplication replaces the entire configuration
**What goes wrong:** Adding spoke B silently removes spoke A.
**Why it happens:** The API "creates a replication configuration or replaces an existing one" — it is not additive.
**How to avoid:** Always read-merge-write the full Rules array (Pattern 1). Never write a partial config.
[VERIFIED: docs.aws.amazon.com/AmazonS3/latest/API/API_PutBucketReplication.html]

### Pitfall 3: Filter + multi-rule requires Priority and DeleteMarkerReplication
**What goes wrong:** `MalformedXML`/validation error when a Rule has a `Filter` but no `Priority`/`Status`/`DeleteMarkerReplication`.
**Why it happens:** "When you add the Filter element ... you must also add: DeleteMarkerReplication, Status, and Priority." With N destination rules, each needs a distinct `Priority` and an `ID`.
**How to avoid:** Always emit `ID`, `Status:"Enabled"`, `Priority:<unique int>`, `Filter:{Prefix:""}` (or real filter), and `DeleteMarkerReplication:{Status: ...}` (default from D-05 options) per Rule. Make the merge assign a deterministic Priority (e.g. derived from destination index or a stored field).
[VERIFIED: docs.aws.amazon.com/AmazonS3/latest/API/API_PutBucketReplication.html]

### Pitfall 4: RTC requires Metrics
**What goes wrong:** Enabling `ReplicationTime` (RTC) without `Metrics` is rejected.
**Why it happens:** S3 RTC is coupled with replication metrics; the API example always pairs `ReplicationTime` with `Metrics` (both `Enabled`, `Minutes:15`).
**How to avoid:** In the D-05 options mapping, if `RTC.Status==Enabled` then force `Metrics.Status=Enabled` (and a default `EventThreshold.Minutes`). Validate in the merge logic.
[VERIFIED: docs.aws.amazon.com/AmazonS3/latest/API/API_PutBucketReplication.html — RTC sample request includes Metrics]

### Pitfall 5: Batch job created in wrong region / wrong account context
**What goes wrong:** CreateJob fails or job can't find the replication config.
**Why it happens:** The job must be initiated from the source bucket's region, and `AccountId`/`ExpectedBucketOwner` must be the source account.
**How to avoid:** Same-region only in v1.2; pass `AccountId = SourceAccount.AccountId` and `ExpectedBucketOwner = SourceAccount.AccountId`. The SFN runs in eu-central-1 (= source region).
[VERIFIED: docs.aws.amazon.com/AmazonS3/latest/userguide/s3-batch-replication-existing-config.html "You must initiate the job from the same AWS Region as the replication source bucket."]

### Pitfall 6: Confusing the two roles (orchestrator-assumed source role vs batch/replication role)
**What goes wrong:** PassRole denied, or batch job AccessDenied.
**Why it happens:** Two distinct roles: (a) the **source role** the SFN assumes imperatively — needs `s3:PutBucketReplication`/`Get*`, `s3control:CreateJob`/`DescribeJob`, `iam:PassRole`; (b) the **replication role** passed into the config / batch job — assumed by `s3.amazonaws.com` and `batchoperations.s3.amazonaws.com`, needs object read/write + `s3:InitiateReplication`.
**How to avoid:** Keep them separate in `source-account/main.tf` (see Security Domain). The source role's `iam:PassRole` must target the replication role ARN with `iam:PassedToService` listing **both** service principals (or two statements).

## Runtime State Inventory

Greenfield-ish for the module (new files), but the IAM change touches existing source-account state.

| Category | Items Found | Action Required |
|----------|-------------|------------------|
| Stored data | None — no datastore keys, collection names, or IDs involve a renamed string. New module only. | None |
| Live service config | None for the generic module. (Real S3 replication configs live on client buckets in the external stack — explicitly out of scope.) | None here |
| OS-registered state | None — no OS-level registrations. | None |
| Secrets/env vars | None — no new secrets or env var names. | None |
| Build artifacts | None — no compiled artifacts, no Lambda zip (D-09 removes the `archive_file` that `sync/` has). | None |

**Note (not a rename, but a stateful IAM addition):** adding the `s3_replication` role and `s3_access` policy to `modules/source-account/` is a **new resource creation** gated by `var.enable_s3` (default it to `false` to keep existing deployments byte-identical — like `enable_efs` but inverted default, since S3 is new). No existing resource is renamed or destroyed. Confirm `var.enable_s3` defaults preserve plan-noop for current consumers (ORCH-05 spirit).

## Code Examples

All verified shapes are inline in Architecture Patterns 1-4 above. Key Resource ARNs (all [CITED: docs.aws.amazon.com/step-functions/latest/dg/supported-services-awssdk.html]):

```
arn:aws:states:::aws-sdk:s3:getBucketVersioning
arn:aws:states:::aws-sdk:s3:getBucketReplication
arn:aws:states:::aws-sdk:s3:putBucketReplication
arn:aws:states:::aws-sdk:s3:deleteBucketReplication
arn:aws:states:::aws-sdk:s3control:createJob
arn:aws:states:::aws-sdk:s3control:describeJob
```

Terraform `main.tf` skeleton (mirror EFS, file()-based, no templatefile):
```hcl
locals {
  step_functions = {
    setup_cross_account_replication = "setup_cross_account_replication.asl.json"
    run_batch_replication           = "run_batch_replication.asl.json"
    check_batch_replication         = "check_batch_replication.asl.json"
    delete_replication              = "delete_replication.asl.json"
  }
  sfn_names = {
    for k, v in local.step_functions : k => (
      var.naming_convention == "pascal"
      ? "${var.prefix}-S3-${replace(title(replace(k, "_", " ")), " ", "")}"
      : "${var.prefix}-s3-${replace(k, "_", "-")}"
    )
  }
}

resource "aws_sfn_state_machine" "s3" {
  for_each   = local.step_functions
  name       = local.sfn_names[each.key]
  role_arn   = var.orchestrator_role_arn
  definition = file("${path.module}/${each.value}")
  logging_configuration {
    log_destination        = var.enable_logging ? "${aws_cloudwatch_log_group.sfn[0].arn}:*" : null
    include_execution_data = var.enable_logging
    level                  = var.enable_logging ? "ALL" : "OFF"
  }
  tracing_configuration { enabled = var.enable_xray_tracing }
  tags = merge(var.tags, { Module = "s3", Name = local.sfn_names[each.key] })
}
```
[VERIFIED: codebase `modules/step-functions/efs/main.tf` lines 53-75]

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| Lambda proxy for compare/sync (planned REPL-06) | Native `aws-sdk` SDK integration only | Phase 7 discussion (D-09) | No Lambda packaging in `s3/`; INFRA-04 drops Lambda unit tests; success criterion #4 drops the Lambda clause |
| S3 Inventory as Batch Operations manifest prerequisite | On-demand `S3JobManifestGenerator` | GA'd (manifest generation for batch ops) | No inventory setup needed before backfill (D-07) [CITED: aws.amazon.com/blogs/storage/accelerating-amazon-s3-batch-operations-at-scale-with-on-demand-manifest-generation] |

**Deprecated/outdated:** Older replication configs filtered only on `<Prefix>` directly under `<Rule>`. Current API uses the `<Filter>` element (with `And`/`Prefix`/`Tag`) and then mandates `Priority`/`Status`/`DeleteMarkerReplication`. Emit the modern Filter form.

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | Step Functions maps the GetBucketReplication not-found error to a catchable name like `S3.ReplicationConfigurationNotFoundError` (exact error-name string under `aws-sdk:s3` not verified against a live run) | Pattern 1 / Pitfall 1 | Catch `ErrorEquals` may need the generic `States.TaskFailed`/`S3.S3Exception` fallback; plan should include a broad Catch. LOW risk — broad Catch mitigates. |
| A2 | `s3control` SDK integration accepts `AccountId` as a top-level PascalCase parameter (mapped to `x-amz-account-id`) | Patterns 2-3 | If the integration expects a different key, CreateJob/DescribeJob fail at runtime; verifiable only in SFN execution. MEDIUM — AccountId is the documented SDK param name across AWS SDKs. |
| A3 | Default `var.enable_s3 = false` to keep existing deployments noop | Runtime State Inventory | If team wants parity with `enable_efs` (default true), flip it. LOW — a one-line default, confirm at plan. |
| A4 | Report bucket for batch job report is provided as an input (`ReportBucketArn`), or report disabled | Pattern 2 / Open Q O1 | If no report bucket available, set `Report.Enabled:false`. MEDIUM — generic module shouldn't own a bucket; see O1. |

## Open Questions

1. **Batch job completion-report bucket ownership.**
   - What we know: `CreateJob.Report` is a required field; a report needs a destination bucket (with `s3:PutObject` for the role). The generic module owns no bucket.
   - What's unclear: whether to (a) require a `ReportBucketArn` input, (b) set `Report.Enabled:false` (still a valid required `JobReport` with Enabled=false), or (c) reuse the source bucket.
   - Recommendation: expose optional `ReportBucketArn`; when absent, emit `Report:{Enabled:false}`. Flag for discuss/plan confirmation (relates to D-05 input surface).

2. **Rule-ID convention (D-02, plan discretion).**
   - What we know: each Rule needs a stable `ID` and unique `Priority` for the read-merge-write to be idempotent.
   - Recommendation: derive `ID` from destination identity (e.g. `repl-<DestAccountId>-<DestBucket>` truncated to S3's 255-char Rule-ID limit) and `Priority` from a per-destination input or destination index. Decide at plan.

3. **DeleteMarkerReplication default (D-05).**
   - What we know: must be present on every filtered Rule; values `Enabled`/`Disabled`.
   - Recommendation: default `Disabled` (safe — delete markers not propagated) unless the destination opts in. Confirm with team.

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| Terraform / OpenTofu | `terraform plan` success criterion | ✓ (CI `setup-terraform@v3`) | per `TF_VERSION` in CI | — |
| pytest | ASL validation (auto-discovery) | ✓ (`tests/`, CI step-functions.yml) | repo-pinned | — |
| AWS account / live deploy | runtime execution of SFN | n/a for Phase 7 | — | Phase 7 is module authoring + `plan` only; no apply |

No external tools beyond the existing repo toolchain are introduced. `terraform validate`/`fmt`/`tflint`/`checkov` and `pytest` already run in CI.

## Validation Architecture

### Test Framework
| Property | Value |
|----------|-------|
| Framework | pytest (repo `tests/`) |
| Config file | none dedicated; auto-discovery via `rglob` in `tests/test_asl_validation.py` and `tests/conftest.py` |
| Quick run command | `pytest tests/test_asl_validation.py -v --tb=short` |
| Full suite command | `pytest tests/ -v` (sfn_local tests auto-skip when SFN Local absent) |

### Phase Requirements -> Test Map
| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| REPL-01..04 | Each new `.asl.json` is valid JSON, has `StartAt` + `States`, valid state structure | unit (structural) | `pytest tests/test_asl_validation.py -v` | ✅ (auto-discovers new files via `rglob("*.asl.json")`) |
| REPL-01..06 | `terraform plan`/`validate` passes for the new module | terraform | `terraform validate` (CI) | ✅ CI `terraform.yml` |
| IAM-01/02 | source-account renders valid IAM with `enable_s3` | terraform | `terraform validate` + `terraform plan` | ✅ CI |
| REPL-02/03 | Batch SFN execution semantics (status transitions) | integration (SFN Local) | `pytest tests/test_stepfunctions_local.py -m sfn_local` | ⚠️ Phase 9 — SFN Local strips `Credentials`/JSONata; full exec test is Phase 9 spec scope |

### Sampling Rate
- **Per task commit:** `pytest tests/test_asl_validation.py -v --tb=short` (sub-second, no AWS creds)
- **Per wave merge:** `terraform fmt -check -recursive && terraform validate` + `pytest tests/ -v`
- **Phase gate:** full suite green before `/gsd:verify-work`

### Wave 0 Gaps
- None for ASL structural validation — `tests/test_asl_validation.py` auto-discovers the 4 new files; no test wiring needed [VERIFIED: `tests/test_asl_validation.py:18` `rglob`].
- Deeper execution tests (status-transition simulation) belong to **Phase 9** (INFRA-04 = ASL validation only; no Lambda tests). Do not add them in Phase 7.

## Security Domain

### Applicable ASVS Categories

| ASVS Category | Applies | Standard Control |
|---------------|---------|-----------------|
| V2 Authentication | no | n/a (machine-to-machine via IAM roles) |
| V3 Session Management | no | n/a |
| V4 Access Control | yes | Least-privilege IAM; imperative assume-role scoped to source account; `iam:PassRole` constrained by `iam:PassedToService` |
| V5 Input Validation | partial | ASL Choice guards validate versioning precondition (D-03); inputs are SFN execution payloads from the trusted orchestrator |
| V6 Cryptography | yes (delegated) | S3 SSE-KMS replication handled by S3/replication role + destination KMS policy (out of scope here); never hand-roll crypto |

### IAM Model — two roles (verified)

**(a) Source role** (assumed imperatively by orchestrator SFN; lives in `source-account`, the `aws_iam_role.source` already exists — add an `s3_access` policy gated by `enable_s3`):
```jsonc
// Sid: S3ReplicationManage
{ "Effect":"Allow",
  "Action":["s3:PutBucketReplication","s3:GetBucketReplication","s3:GetBucketVersioning","s3:DeleteBucketReplication"],
  "Resource":"arn:aws:s3:::*" },         // tighten to client bucket ARNs at wiring (Phase 8)
// Sid: S3BatchControl
{ "Effect":"Allow",
  "Action":["s3:CreateJob","s3:DescribeJob"],   // s3control actions use the s3: namespace
  "Resource":"*" },
// Sid: PassRoleToS3Replication  (mirror EFS PassRoleToEFSReplication)
{ "Effect":"Allow", "Action":["iam:PassRole"],
  "Resource":"arn:aws:iam::${account_id}:role/${prefix}-s3-replication-role",
  "Condition":{ "StringEquals":{ "iam:PassedToService":["s3.amazonaws.com","batchoperations.s3.amazonaws.com"] } } }
```
Note: S3 Batch Operations IAM actions are `s3:CreateJob` / `s3:DescribeJob` (the `s3control` API maps to the `s3:` IAM namespace). [VERIFIED: docs.aws.amazon.com/AmazonS3/latest/userguide/batch-ops-iam-role-policies.html — "the `s3:CreateJob` user permission is required ... must also have the `iam:PassRole` permission"]

**(b) Combined replication role** (`aws_iam_role.s3_replication`, gated `enable_s3`; mirror `aws_iam_role.efs_replication`) — trust BOTH service principals:
```jsonc
"AssumeRolePolicyDocument": {
  "Version":"2012-10-17",
  "Statement":[
    { "Effect":"Allow","Principal":{"Service":"s3.amazonaws.com"},"Action":"sts:AssumeRole" },
    { "Effect":"Allow","Principal":{"Service":"batchoperations.s3.amazonaws.com"},"Action":"sts:AssumeRole" }
  ]
}
```
Permissions policy (live replication + batch replicate-existing with generated manifest):
```jsonc
// Live replication (S3 service assumes this)
{ "Effect":"Allow",
  "Action":["s3:GetReplicationConfiguration","s3:ListBucket","s3:GetObjectVersionForReplication",
            "s3:GetObjectVersionAcl","s3:GetObjectVersionTagging","s3:GetObjectRetention","s3:GetObjectLegalHold"],
  "Resource":["arn:aws:s3:::<source>","arn:aws:s3:::<source>/*"] },
{ "Effect":"Allow",
  "Action":["s3:ReplicateObject","s3:ReplicateDelete","s3:ReplicateTags","s3:ObjectOwnerOverrideToBucketOwner"],
  "Resource":"arn:aws:s3:::<destination>/*" },
// Batch replicate-existing (batchoperations assumes this) — with S3 generated manifest
{ "Effect":"Allow","Action":["s3:InitiateReplication"],"Resource":"arn:aws:s3:::<source>/*" },
{ "Effect":"Allow","Action":["s3:GetReplicationConfiguration","s3:PutInventoryConfiguration"],"Resource":"arn:aws:s3:::<source>" },
{ "Effect":"Allow","Action":["s3:GetObject","s3:GetObjectVersion"],"Resource":"arn:aws:s3:::<manifest-bucket>/*" },
{ "Effect":"Allow","Action":["s3:PutObject"],"Resource":["arn:aws:s3:::<report-bucket>/*","arn:aws:s3:::<manifest-bucket>/*"] }
```
[VERIFIED: docs.aws.amazon.com/AmazonS3/latest/userguide/batch-ops-iam-role-policies.html — "Replicate existing objects: InitiateReplication with an S3 generated manifest" policy: `s3:InitiateReplication`, `s3:GetReplicationConfiguration`, `s3:PutInventoryConfiguration`, plus manifest/report `GetObject`/`PutObject`]

Note: for the **generic** module, resources stay as `*`/parameterized; concrete bucket/KMS ARNs and destination-side grants are client-specific and **out of scope** (Phase 8 wiring / external stack). Keep policies broad-but-correct at module level, tighten at instantiation.

### Known Threat Patterns

| Pattern | STRIDE | Standard Mitigation |
|---------|--------|---------------------|
| Over-broad `iam:PassRole` lets source role pass any role to S3 | Elevation of Privilege | Constrain `Resource` to the specific replication role ARN + `iam:PassedToService` condition (mirror EFS) |
| Source-bucket mutation via replication role | Tampering | Validate-only versioning (D-03); module never calls Put/Delete on source data, only the replication subresource |
| Lost-update wiping spokes | Tampering/DoS | `MaxConcurrency:1` Map + read-merge-write (D-01) |
| Cross-account confused-deputy on assume-role | Elevation of Privilege | Existing source-role trust uses `aws:PrincipalOrgID` condition (already in `source-account/main.tf`); reuse unchanged |
| Replaying CreateJob | Tampering | `ClientRequestToken` idempotency (fresh UUID per execution) |

## Sources

### Primary (HIGH confidence)
- Codebase: `modules/step-functions/efs/{setup_cross_account_replication,delete_replication}.asl.json`, `efs/main.tf|variables.tf|outputs.tf|versions.tf`, `modules/source-account/main.tf|variables.tf`, `modules/step-functions/sync/main.tf`, `tests/test_asl_validation.py`, `tests/conftest.py`, `.github/workflows/{terraform,step-functions}.yml` — patterns, IAM template, test discovery.
- docs.aws.amazon.com/AmazonS3/latest/API/API_PutBucketReplication.html — ReplicationConfiguration request shape, replace semantics, Filter/Priority requirement, RTC+Metrics pairing.
- docs.aws.amazon.com/AmazonS3/latest/API/API_GetBucketReplication.html — not-found error behavior.
- docs.aws.amazon.com/AmazonS3/latest/API/API_control_CreateJob.html — required params (AccountId, ClientRequestToken, Operation, Priority, Report, RoleArn), Manifest/ManifestGenerator mutual exclusivity, JobId response.
- docs.aws.amazon.com/AmazonS3/latest/API/API_control_DescribeJob.html — Job.Status enum, ProgressSummary, FailureReasons.
- docs.aws.amazon.com/AmazonS3/latest/userguide/s3-batch-replication-existing-config.html — CLI create-job for S3ReplicateObject + GeneratedManifest, same-region requirement.
- docs.aws.amazon.com/AmazonS3/latest/userguide/batch-ops-iam-role-policies.html — batch ops trust policy (`batchoperations.s3.amazonaws.com`), InitiateReplication-with-generated-manifest permission set, `s3:CreateJob`+`iam:PassRole`.

### Secondary (MEDIUM confidence)
- docs.aws.amazon.com/step-functions/latest/dg/supported-services-awssdk.html — `arn:aws:states:::aws-sdk:<service>:<action>` form; AccountId passed explicitly to s3control.
- aws.amazon.com/blogs/storage/accelerating-amazon-s3-batch-operations-at-scale-with-on-demand-manifest-generation — on-demand manifest generation (no inventory precondition).

### Tertiary (LOW confidence — flagged)
- drdroid.io / github cloudquery-aws issue #265 — corroborate `ReplicationConfigurationNotFoundError` name (exact Step Functions error-name mapping = Assumption A1).

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH — Terraform/ASL versions and module layout verified directly in codebase.
- Architecture (request/response shapes, IAM): HIGH — every shape verified against official AWS API reference pages.
- Pitfalls: HIGH — each derived from an authoritative API doc statement.
- Step Functions error-name mapping (A1) and s3control AccountId param key (A2): MEDIUM — documented in AWS SDKs but not verified in a live SFN execution; mitigated by broad Catch and standard SDK param naming.

**Research date:** 2026-06-17
**Valid until:** 2026-07-17 (stable AWS APIs; ~30 days)
