# Phase 3: Consolidation - Research

**Researched:** 2026-03-16
**Domain:** AWS Step Functions ASL consolidation, Terraform module refactoring
**Confidence:** HIGH

## Summary

Phase 3 merges 6 pairs of public/private ASL files into 6 unified files using a runtime Choice state to branch between `eks:call` (public) and `lambda:invoke` via K8sProxy (private). The commutateur is `$.EKS.AccessMode` passed in the SFN execution input, replacing the compile-time `var.eks_access_mode` Terraform variable.

Analysis of all 12 ASL files reveals two distinct consolidation patterns. **Simple pairs** (manage_storage, scale_services, verify_and_restart_services, run_mysqldump_on_eks) differ only in the K8s interaction method: public uses `arn:aws:states:::eks:call` with cluster info from `GetEksClusterInfo`, private uses `arn:aws:states:::lambda:invoke` with `$.K8sProxyLambdaArn`. **Complex pairs** (run_archive_job, run_mysqlimport_on_eks) additionally differ in job lifecycle: public uses `eks:runJob.sync` (synchronous native integration), private uses a create/wait/check/delete Lambda cycle.

The Terraform changes are mechanical: remove the `_suffix`/`_eks_suffix` local in 3 modules, remove the `eks_access_mode` variable from 3 modules' `variables.tf`, and remove it from the root `main.tf` module calls (currently not passed -- the variable defaults to `"public"`). The CI matrix needs no changes for file names since the matrix entries already use the base names (e.g., `run_mysqldump_on_eks`) not the `_private` variants.

**Primary recommendation:** Use `$.EKS.AccessMode` as the Choice state variable (value `"public"` or `"private"`). This mirrors the existing Terraform variable semantics, is explicit, and avoids fragile presence-based detection.

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions
- Le commutateur est `EKS.AccessMode` dans l'input SFN, pas `Account.RoleArn`
- Public/private est une notion d'exposition de l'API Kubernetes (endpoint public vs prive), PAS une notion de cross-account
- Les deux versions (pub et priv) utilisent deja `DestinationAccount.RoleArn` dans les Credentials pour les appels cross-account
- Consolidation des 4 paires simples : Choice state au debut route selon `EKS.AccessMode` ("public" -> GetEksClusterInfo -> flow commun, "private" -> flow commun directement)
- Consolidation des 2 paires complexes : Choice state apres la preparation route vers le flow natif (eks:runJob.sync) ou le flow Lambda (create/wait/check/delete) selon `EKS.AccessMode`
- Decoupage en 3 plans groupes par module : 03-01 EKS (3 simples), 03-02 DB (1 simple + 1 complexe), 03-03 Utils + cleanup (1 complexe + var suppression + CI)
- Zero modification dans l'orchestrateur -- il passe deja DestinationAccount + infos EKS dans l'input de chaque sous-SFN
- Variable eks_access_mode supprimee dans le dernier plan (03-03)

### Claude's Discretion
- Commutateur exact dans le Choice state ($.EKS.AccessMode ou presence/absence de $.EksCluster -- analyser ce qui est le plus robuste)
- Noms internes des states dans les fichiers consolides
- Gestion des Credentials dans les states communs
- Structure exacte du CI matrix update
- Ordre des taches dans chaque plan

### Deferred Ideas (OUT OF SCOPE)
None -- discussion stayed within phase scope
</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|-----------------|
| CON-01 | Consolider manage_storage pub/priv en 1 fichier | Simple pattern: Choice on EKS.AccessMode, branch to GetEksClusterInfo or skip, then common flow with dual K8s interaction states |
| CON-02 | Consolider scale_services pub/priv en 1 fichier | Simple pattern: same as CON-01, Map iterator needs dual PatchService variants |
| CON-03 | Consolider verify_and_restart en 1 fichier | Simple pattern: same as CON-01, Map iterator needs dual GetService/RestartService variants |
| CON-04 | Consolider run_archive_job pub/priv en 1 fichier | Complex pattern: shared prep states, Choice after prep branches to RunKubernetesJob (eks:runJob.sync) or CreateArchiveJob/Wait/Check/Delete Lambda cycle |
| CON-05 | Consolider run_mysqldump_on_eks pub/priv en 1 fichier | Semi-simple: shared prep, Map iterator branches to RunEksJobForTable (eks:runJob.sync) or CreateDumpJob/Wait/Check/Delete Lambda cycle |
| CON-06 | Consolider run_mysqlimport_on_eks pub/priv en 1 fichier | Complex pattern: shared prep, Choice branches to RunEksJobForImport (eks:runJob.sync) or CreateImportJob/Wait/Check/Delete Lambda cycle |
</phase_requirements>

## Architecture Patterns

### Pattern 1: Simple Pair Consolidation (4 pairs)

**What:** For manage_storage, scale_services, verify_and_restart_services, run_mysqldump_on_eks -- the only difference between public and private is the K8s API interaction method.

**Public path:**
1. `GetEksClusterInfo` (eks:describeCluster) -> stores `$.EksCluster`
2. K8s operations via `arn:aws:states:::eks:call` using `$.EksCluster.Cluster.Name/CertificateAuthority/Endpoint`

**Private path:**
1. No GetEksClusterInfo (cluster info not needed for Lambda proxy)
2. K8s operations via `arn:aws:states:::lambda:invoke` using `$.K8sProxyLambdaArn` + `$.EksClusterName`

**Consolidated structure:**
```
StartAt: CheckAccessMode
States:
  CheckAccessMode:
    Type: Choice
    Choices:
      - Variable: $.EKS.AccessMode
        StringEquals: "public"
        Next: GetEksClusterInfo
    Default: <first common/private state>

  GetEksClusterInfo:
    Type: Task
    Resource: eks:describeCluster
    Next: <first common state>

  # For each K8s operation, two variants:
  <OperationName>Public:
    Resource: eks:call
    Parameters: { ClusterName.$, CertificateAuthority.$, Endpoint.$ }
  <OperationName>Private:
    Resource: lambda:invoke
    Parameters: { FunctionName.$: $.K8sProxyLambdaArn, Payload: {...} }
```

**Critical detail -- dual states vs single state:** Each K8s interaction (DeletePvc, CreateSc, PatchService, etc.) needs TWO state variants because the Resource, Parameters, Retry, and Catch differ fundamentally between `eks:call` and `lambda:invoke`. A single state cannot represent both. The Choice state must route to the correct variant at each interaction point, OR the flow must fork early into two parallel branches that converge at the output.

**Recommended approach for simple pairs:** Fork early (after GetEksClusterInfo for public, immediately for private) into two complete branches that converge at PrepareOutput. This avoids a Choice state before every single K8s operation and keeps the ASL readable. The "fork early" approach is what the CONTEXT.md describes.

### Pattern 2: Complex Pair Consolidation (2 pairs)

**What:** For run_archive_job and run_mysqlimport_on_eks -- the preparation phase is identical (EnsureClusterAvailable, GetClusterInfo, EnsureNodegroupCapacity, GetDbSecretValue, etc.), but the job execution phase differs fundamentally.

**Public path:**
- Single `eks:runJob.sync` call (synchronous, SFN manages the K8s job lifecycle)

**Private path:**
- Multi-state cycle: CreateJob -> WaitForJob -> CheckJobStatus -> (loop back or) DeleteJob
- Uses `lambda:invoke` with K8sProxy for each step

**Consolidated structure:**
```
StartAt: <first shared prep state>
States:
  # ... all shared preparation states ...

  CheckAccessModeForJob:
    Type: Choice
    Choices:
      - Variable: $.EKS.AccessMode
        StringEquals: "public"
        Next: GetEksClusterInfo  (for public, needs cluster info)
    Default: <CreateJob via Lambda>

  # Public branch: GetEksClusterInfo -> RunKubernetesJob (eks:runJob.sync)
  # Private branch: CreateJob -> WaitForJob -> CheckStatus -> DeleteJob cycle

  # Both converge at PrepareOutput
```

### Pattern 3: Hybrid Pair (run_mysqldump_on_eks -- CON-05)

**What:** run_mysqldump_on_eks is categorized as "simple" by CONTEXT.md state counts (10 pub / 9 priv), but the Map iterator contains the job execution logic that differs. The public version uses `eks:runJob.sync` inside the Map, the private uses the create/wait/check/delete cycle inside the Map.

**Key insight:** The outer flow (EnsureClusterAvailable -> GetClusterInfo -> EnsureNodegroupCapacity -> GetEksClusterInfo/skip -> GetDbSecretValue -> FormatSecretString -> DumpTablesMap) is shared. The divergence is INSIDE the Map's ItemProcessor.

**Public Map flow:** Single state `RunEksJobForTable` (eks:runJob.sync)
**Private Map flow:** CreateDumpJob -> WaitForDumpJob -> CheckDumpJobStatus -> DumpJobStatusChoice -> DeleteDumpJob/DeleteDumpJobOnError cycle

**Consolidated approach:** The Map ItemSelector prepares data for both modes. Inside the ItemProcessor, a Choice state routes to the public (single state) or private (multi-state cycle) path. Both paths converge.

### Commutateur Recommendation (Claude's Discretion)

**Recommended: `$.EKS.AccessMode` with explicit string values `"public"` / `"private"`**

Rationale:
1. **Explicit over implicit:** Testing for presence/absence of `$.EksCluster` is fragile -- a future caller might forget to set it, silently falling into the wrong branch.
2. **Mirrors existing semantics:** The Terraform `var.eks_access_mode` already uses `"public"` / `"private"` strings.
3. **Self-documenting:** Anyone reading the ASL sees `CheckAccessMode` with clear `"public"`/`"private"` values.
4. **Orchestrator already passes `$.EKS` object:** Adding `AccessMode` to the EKS input object is trivial from the caller side.

**However:** The user's locked decision says "Zero modification dans l'orchestrateur". The orchestrator currently does NOT pass `EKS.AccessMode`. Two options:
1. The EKS.AccessMode comes from the execution input (passed by the caller who invokes the orchestrator) -- the orchestrator just passes `$.EKS` through unchanged. This requires the caller to include `AccessMode` in the EKS input config.
2. The K8sProxyLambdaArn presence could be the implicit commutateur -- but this is fragile (K8sProxyLambdaArn is passed in BOTH public and private modes today).

**Verdict:** `$.EKS.AccessMode` is the right commutateur. No orchestrator ASL changes needed -- `$.EKS` is passed through as-is. The AccessMode value must be added to the execution input JSON by the caller (external to this codebase).

### State Naming Convention (Claude's Discretion)

For dual-variant states, use the pattern: `<BaseName>` for public, `<BaseName>Private` for private.

Examples:
- `DeletePvc` (public, uses eks:call) / `DeletePvcPrivate` (private, uses lambda:invoke)
- `RunKubernetesJob` (public, eks:runJob.sync) / `CreateArchiveJob` (private, lambda createJob)

For the complex pairs where the private flow has entirely different state names (CreateJob, WaitForJob, etc.), keep the existing private state names as-is and add the public states.

### Credentials Handling (Claude's Discretion)

**Finding:** Both public and private variants already use `"Credentials": { "RoleArn.$": "$.DestinationAccount.RoleArn" }` on ALL Task states. This does not change in consolidation. The Credentials block is identical in both paths.

The only difference is:
- Public `eks:call` uses Credentials for cross-account EKS API access
- Private `lambda:invoke` uses Credentials to invoke the K8sProxy Lambda in the destination account

Both use `$.DestinationAccount.RoleArn`. No special handling needed.

### Terraform Module Changes

**EKS module (Plan 03-01):**
- `main.tf`: Remove `_suffix` local, remove `${local._suffix}` from 3 map entries (manage_storage, scale_services, verify_and_restart_services)
- `variables.tf`: Remove `eks_access_mode` variable (lines 46-55)
- Delete 3 files: `manage_storage_private.asl.json`, `scale_services_private.asl.json`, `verify_and_restart_services_private.asl.json`

**DB module (Plan 03-02):**
- `main.tf`: Remove `_eks_suffix` local, remove `${local._eks_suffix}` from 2 map entries (run_mysqldump_on_eks, run_mysqlimport_on_eks)
- `variables.tf`: Remove `eks_access_mode` variable
- Delete 2 files: `run_mysqldump_on_eks_private.asl.json`, `run_mysqlimport_on_eks_private.asl.json`

**Utils module (Plan 03-03):**
- `main.tf`: Remove `_eks_suffix` local, remove `${local._eks_suffix}` from 1 map entry (run_archive_job)
- `variables.tf`: Remove `eks_access_mode` variable
- Delete 1 file: `run_archive_job_private.asl.json`

**Root module (Plan 03-03):**
- `main.tf`: No changes needed -- `eks_access_mode` is NOT currently passed to any submodule (all 3 use the default `"public"`)
- `variables.tf`: No changes needed -- `eks_access_mode` is NOT defined at root level

**Key insight:** The root module never passes `eks_access_mode`. All 3 submodules default to `"public"`, meaning only the public ASL files are used today. After consolidation, the choice moves to runtime, and the same unified file serves both modes.

### CI Matrix Changes (Claude's Discretion)

**Current CI (`.github/workflows/step-functions.yml`):**
- `validate-db-module` matrix lists `run_mysqldump_on_eks` and `run_mysqlimport_on_eks` -- these reference base filenames, NOT the `_private` variants. No change needed.
- There are NO matrix entries for EKS or Utils modules in the CI (only DB and EFS have dedicated matrix validation jobs).
- The `validate-local` job runs `find . -name "*.asl.json"` which auto-discovers all files. Deleting the 6 `_private` files means they simply won't be found. No change needed.
- The `test-sfn-local` job uses auto-discovery (rglob). No change needed.

**Verdict:** The CI matrix needs NO modifications for Phase 3. The only cosmetic improvement would be adding EKS and Utils matrix jobs, but that's out of scope (QAL-01 deferred).

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| State machine diffing | Manual visual comparison | `jq` structural comparison in tests | 12 files with subtle differences are error-prone to compare visually |
| Terraform state migration | `terraform state mv` commands | `moved` blocks in HCL | moved blocks are declarative, repeatable, and version-controlled. However, for this phase no moved blocks are needed since the Terraform map keys don't change (key is still `manage_storage`, the value changes from `manage_storage_private.asl.json` to `manage_storage.asl.json`) |

## Common Pitfalls

### Pitfall 1: Map Iterator State Divergence
**What goes wrong:** The Map iterator (used in scale_services, verify_and_restart, run_mysqldump) selects data using `ItemSelector` that differs between public and private. Public passes `ClusterName/CertificateAuthority/Endpoint` from `$.EksCluster.Cluster`, private passes `K8sProxyLambdaArn/EksClusterName`.
**Why it happens:** The ItemSelector runs BEFORE the Map's internal states, so both sets of data must be included in the consolidated ItemSelector.
**How to avoid:** Include BOTH public fields (`EksCluster.*` when present) and private fields (`K8sProxyLambdaArn`, `EksClusterName`) in the ItemSelector. The Choice state inside the Map's ItemProcessor decides which to use.
**Warning signs:** "Variable path not found" errors during execution.

### Pitfall 2: Error Handling Divergence
**What goes wrong:** Public `eks:call` catches `EKS.404` errors, private `lambda:invoke` catches `States.TaskFailed` or `Lambda.ServiceException`. The consolidated file must preserve both error handling patterns.
**Why it happens:** Different AWS service integrations throw different error codes.
**How to avoid:** Each variant state (public/private) keeps its own Retry and Catch blocks unchanged from the original file.
**Warning signs:** Unhandled errors causing the entire SFN to fail instead of graceful degradation.

### Pitfall 3: ResultSelector Differences
**What goes wrong:** Private lambda:invoke states often have `ResultSelector` to extract `$.Payload.ResponseBody` and `$.Payload.StatusCode`, while public eks:call states return the response directly in `$.ResponseBody`.
**Why it happens:** Lambda invoke wraps the response in a Payload envelope.
**How to avoid:** Keep ResultSelector on private variant states, omit on public variant states.
**Warning signs:** Downstream states failing to find expected fields in the state data.

### Pitfall 4: run_mysqldump Public Has GetEksClusterInfo, Private Does Not
**What goes wrong:** The public `run_mysqldump_on_eks` has a `GetEksClusterInfo` state between `EnsureNodegroupCapacity` and `GetDbSecretValue`, while the private version goes directly from `EnsureNodegroupCapacity` to `GetDbSecretValue`.
**Why it happens:** Public needs cluster info for `eks:runJob.sync`, private uses K8sProxyLambdaArn which gets cluster info internally.
**How to avoid:** In the consolidated version, insert a Choice state between EnsureNodegroupCapacity and GetDbSecretValue that routes public to GetEksClusterInfo, private skips to GetDbSecretValue.
**Warning signs:** Missing `$.EksCluster` data in the public RunEksJobForTable state.

### Pitfall 5: run_mysqlimport_on_eks Private Has Extra States
**What goes wrong:** The private `run_mysqlimport` has `CheckSkipDeletion` (DebugKeepJob support) and `ImportJobFailed` states that the public version doesn't have.
**Why it happens:** The private version implemented additional debug features.
**How to avoid:** Include the extra states in the consolidated file. They are only reachable from the private flow path, so they don't affect public execution.
**Warning signs:** None (additive, not conflicting).

### Pitfall 6: Terraform Plan Shows Destroy+Create Instead of Update
**What goes wrong:** When the ASL file name changes in the Terraform map, the for_each key stays the same but the `file()` call references a different file. This is an in-place update, NOT a destroy+create. But if the key name in the map were to change, it would be a destroy+create.
**Why it happens:** The Terraform map keys are `manage_storage`, `scale_services`, etc. -- these don't change. Only the value (filename) changes from `manage_storage_private.asl.json` to `manage_storage.asl.json`.
**How to avoid:** Verify that map keys in locals don't change. Only the file path value changes.
**Warning signs:** `terraform plan` showing `- aws_sfn_state_machine` (destroy) instead of `~ aws_sfn_state_machine` (update).

## Code Examples

### Simple Pair Consolidation Pattern (manage_storage)

```json
{
  "Comment": "Creates or Deletes EKS storage resources. Supports both public (eks:call) and private (k8s-proxy Lambda) access modes.",
  "StartAt": "CheckAccessMode",
  "States": {
    "CheckAccessMode": {
      "Type": "Choice",
      "Choices": [
        {
          "Variable": "$.EKS.AccessMode",
          "StringEquals": "public",
          "Next": "GetEksClusterInfo"
        }
      ],
      "Default": "ChooseAction"
    },
    "GetEksClusterInfo": {
      "Type": "Task",
      "Resource": "arn:aws:states:::aws-sdk:eks:describeCluster",
      "Parameters": { "Name.$": "$.EksClusterName" },
      "Credentials": { "RoleArn.$": "$.DestinationAccount.RoleArn" },
      "ResultPath": "$.EksCluster",
      "Next": "ChooseAction",
      "Catch": [{ "ErrorEquals": ["States.ALL"], "ResultPath": "$.Error", "Next": "ProcessFailed" }]
    },
    "ChooseAction": {
      "Type": "Choice",
      "Comment": "Shared routing -- both access modes converge here",
      "Choices": [
        { "Variable": "$.Action", "StringEquals": "Create", "Next": "GetEfsIdFromSsm" },
        { "Variable": "$.Action", "StringEquals": "Delete", "Next": "CheckAccessModeForDelete" }
      ],
      "Default": "ProcessFailed"
    },
    "CheckAccessModeForDelete": {
      "Type": "Choice",
      "Choices": [
        { "Variable": "$.EKS.AccessMode", "StringEquals": "public", "Next": "DeletePvc" }
      ],
      "Default": "DeletePvcPrivate"
    }
  }
}
```

**Note:** The above is a conceptual excerpt. The full file will contain both public (`DeletePvc` using `eks:call`) and private (`DeletePvcPrivate` using `lambda:invoke`) variants for each K8s operation.

### Alternative: Two-Branch Fork Pattern

Rather than multiplying Choice states before each operation, fork once into two complete branches:

```json
{
  "StartAt": "CheckAccessMode",
  "States": {
    "CheckAccessMode": {
      "Type": "Choice",
      "Choices": [
        { "Variable": "$.EKS.AccessMode", "StringEquals": "public", "Next": "GetEksClusterInfo" }
      ],
      "Default": "ChooseActionPrivate"
    },
    "GetEksClusterInfo": { "...": "...", "Next": "ChooseAction" },

    "ChooseAction": { "...public branch with eks:call states..." },
    "ChooseActionPrivate": { "...private branch with lambda:invoke states..." },

    "PrepareDeleteOutput": { "...shared..." },
    "PrepareCreateOutput": { "...shared..." },
    "ProcessFailed": { "...shared..." },
    "ProcessSucceeded": { "...shared..." }
  }
}
```

**Trade-off:** More state duplication (shared logic like ChooseAction appears twice) but simpler flow with fewer Choice states. For simple pairs where the branches are nearly identical in structure, this is acceptable. For complex pairs where the branches diverge significantly, this is the only practical approach.

### Complex Pair Consolidation Pattern (run_archive_job)

```json
{
  "Comment": "Run archive job. Supports public (eks:runJob.sync) and private (k8s-proxy Lambda) modes.",
  "StartAt": "GetClusterInfo",
  "States": {
    "GetClusterInfo": { "...shared..." },
    "EnsureNodegroupCapacity": { "...shared..." },
    "GetDatabaseCredentials": { "...shared..." },
    "ParseSecretString": { "...shared...", "Next": "CheckAccessModeForJob" },

    "CheckAccessModeForJob": {
      "Type": "Choice",
      "Choices": [
        { "Variable": "$.EKS.AccessMode", "StringEquals": "public", "Next": "GetEksClusterInfo" }
      ],
      "Default": "CreateArchiveJob"
    },

    "GetEksClusterInfo": { "...public only...", "Next": "RunKubernetesJob" },
    "RunKubernetesJob": { "Resource": "eks:runJob.sync", "Next": "PrepareOutput" },

    "CreateArchiveJob": { "...private...", "Next": "WaitForArchiveJob" },
    "WaitForArchiveJob": { "...private...", "Next": "CheckArchiveJobStatus" },
    "CheckArchiveJobStatus": { "...private...", "Next": "ArchiveJobStatusChoice" },
    "ArchiveJobStatusChoice": { "...private...", "Default": "WaitForArchiveJob" },
    "DeleteArchiveJob": { "...private...", "Next": "PrepareOutput" },
    "DeleteArchiveJobOnError": { "...private...", "Next": "ArchiveJobFailed" },
    "ArchiveJobFailed": { "Type": "Fail" },

    "PrepareOutput": { "...shared..." },
    "ArchiveFailed": { "Type": "Fail" },
    "ArchiveSucceeded": { "Type": "Succeed" }
  }
}
```

### Terraform Module After Consolidation (EKS example)

```hcl
locals {
  step_functions = {
    manage_storage              = "manage_storage.asl.json"
    scale_nodegroup_asg         = "scale_nodegroup_asg.asl.json"
    scale_services              = "scale_services.asl.json"
    verify_and_restart_services = "verify_and_restart_services.asl.json"
  }
  # ... naming unchanged ...
}
```

No `_suffix`, no conditional file selection. Direct filename reference.

## Detailed Diff Analysis Per Pair

### manage_storage (18 pub / 17 priv states)
- **Public-only state:** `GetEksClusterInfo` (1 state)
- **Shared states:** ChooseAction, all Wait states, PrepareDeleteOutput, GetEfsIdFromSsm, PrepareCreateOutput, ProcessFailed, ProcessSucceeded (9 states)
- **Dual-variant states (need both public + private versions):** DeletePvc, DeletePv, DeleteSc, CreateSc, CreatePv, CreatePvc (6 states x 2 = 12)
- **Estimated consolidated state count:** 1 (CheckAccessMode) + 1 (GetEksClusterInfo) + 6 (shared) + 12 (6 dual) + 2 (terminal) = ~22 states

### scale_services (9 pub / 8 priv states)
- **Public-only state:** `GetEksClusterInfo`
- **Shared states:** WaitAfterScaling, CheckCleanupSecret, PrepareOutput, ScaleFailed, ScaleSucceeded
- **Inside Map - dual states:** PatchService, GetSecret, DeleteSecret
- **Map state itself:** ScaleServicesMap (ItemSelector must include both sets of fields)
- **Special:** ServiceNotFoundSkip, WaitForScale are shared inside Map
- **Estimated consolidated state count:** ~12-14 states (outer) with Map internals

### verify_and_restart_services (6 pub / 5 priv states)
- **Public-only state:** `GetEksClusterInfo`
- **Shared states:** GetExpectedSubPath, PrepareOutput, VerificationFailed, VerificationSucceeded
- **Inside Map - dual states:** GetService, RestartService
- **Inside Map - shared states:** ExtractCurrentSubPath, DecideIfRestartNeeded, SubPathCorrect, ServiceNotFound, RestartFailed, WaitForRestart, ServiceRestarted
- **Special:** ExtractCurrentSubPath uses JSONata and references different field paths for public ($.ServiceDetails.ResponseBody) vs private ($.ServiceDetails.ResponseBody -- same after ResultSelector normalization)
- **Estimated consolidated state count:** ~8 states (outer) with Map internals

### run_mysqldump_on_eks (10 pub / 9 priv states)
- **Shared states:** EnsureClusterAvailable, GetClusterInfo, EnsureNodegroupCapacity, GetDbSecretValue, FormatSecretString, PrepareOutput, DumpFailed, DumpSucceeded
- **Public-only:** GetEksClusterInfo (between EnsureNodegroupCapacity and GetDbSecretValue)
- **Inside Map:** RunEksJobForTable (public) vs CreateDumpJob/WaitForDumpJob/CheckDumpJobStatus/DumpJobStatusChoice/DeleteDumpJob/DeleteDumpJobOnError/DumpJobFailed cycle (private)
- **Estimated consolidated state count:** ~12 states (outer) with 2 Map variant paths

### run_archive_job (9 pub / 14 priv states)
- **Shared states:** GetClusterInfo, EnsureNodegroupCapacity, GetDatabaseCredentials, ParseSecretString, PrepareOutput, ArchiveFailed, ArchiveSucceeded (7 states)
- **Public-only:** GetEksClusterInfo, RunKubernetesJob (2 states)
- **Private-only:** CreateArchiveJob, WaitForArchiveJob, CheckArchiveJobStatus, ArchiveJobStatusChoice, DeleteArchiveJob, DeleteArchiveJobOnError, ArchiveJobFailed (7 states)
- **Estimated consolidated state count:** 1 (CheckAccessMode) + 7 (shared) + 2 (public) + 7 (private) = 17 states

### run_mysqlimport_on_eks (10 pub / 16 priv states)
- **Shared states:** EnsureClusterAvailable, GetClusterInfo, EnsureNodegroupCapacity, GetDbSecretValue, FormatSecretString, PrepareOutput, ImportFailed, ImportSucceeded (8 states)
- **Public-only:** GetEksClusterInfo, RunEksJobForImport (2 states)
- **Private-only:** CreateImportJob, WaitForImportJob, CheckImportJobStatus, ImportJobStatusChoice, CheckSkipDeletion, DeleteImportJob, DeleteImportJobOnError, ImportJobFailed (8 states)
- **Estimated consolidated state count:** 1 (CheckAccessMode) + 8 (shared) + 2 (public) + 8 (private) = 19 states

## Validation Architecture

### Test Framework
| Property | Value |
|----------|-------|
| Framework | pytest (from requirements-dev.txt) |
| Config file | tests/conftest.py |
| Quick run command | `pytest tests/test_asl_validation.py -v --tb=short -x` |
| Full suite command | `pytest tests/ -v --tb=short` |

### Phase Requirements -> Test Map
| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| CON-01 | manage_storage consolidated file is valid ASL | unit | `pytest tests/test_asl_validation.py -v --tb=short -x` | Yes (auto-discovery) |
| CON-02 | scale_services consolidated file is valid ASL | unit | `pytest tests/test_asl_validation.py -v --tb=short -x` | Yes (auto-discovery) |
| CON-03 | verify_and_restart consolidated file is valid ASL | unit | `pytest tests/test_asl_validation.py -v --tb=short -x` | Yes (auto-discovery) |
| CON-04 | run_archive_job consolidated file is valid ASL | unit | `pytest tests/test_asl_validation.py -v --tb=short -x` | Yes (auto-discovery) |
| CON-05 | run_mysqldump_on_eks consolidated file is valid ASL | unit | `pytest tests/test_asl_validation.py -v --tb=short -x` | Yes (auto-discovery) |
| CON-06 | run_mysqlimport_on_eks consolidated file is valid ASL | unit | `pytest tests/test_asl_validation.py -v --tb=short -x` | Yes (auto-discovery) |
| ALL | SFN Local can load consolidated files | integration | `pytest tests/test_stepfunctions_local.py -v --tb=short -m sfn_local` | Yes (auto-discovery) |
| ALL | Deleted _private files no longer present | smoke | `test ! -f modules/step-functions/eks/manage_storage_private.asl.json` | Manual check |

### Sampling Rate
- **Per task commit:** `pytest tests/test_asl_validation.py -v --tb=short -x`
- **Per wave merge:** `pytest tests/ -v --tb=short` (full suite, requires Docker for SFN Local)
- **Phase gate:** Full suite green before `/gsd:verify-work`

### Wave 0 Gaps
None -- existing test infrastructure covers all phase requirements via auto-discovery (rglob on `*.asl.json`). The tests will automatically pick up new consolidated files and stop testing deleted `_private` files.

## Open Questions

1. **EKS.AccessMode in execution input**
   - What we know: The orchestrator passes `$.EKS` through unchanged. The sub-SFNs will now check `$.EKS.AccessMode`.
   - What's unclear: Who sets `EKS.AccessMode` in the execution input? It must come from the caller (external system or manual invocation).
   - Recommendation: This is outside the codebase scope. Document in a comment in the ASL that `EKS.AccessMode` must be `"public"` or `"private"` in the execution input. Default behavior (if absent) should be "public" since that's the current default.

2. **Default value when EKS.AccessMode is absent**
   - What we know: The Choice state `Default` branch handles the case where the variable doesn't match any choice.
   - What's unclear: Should missing `EKS.AccessMode` default to "public" (current default behavior) or fail?
   - Recommendation: Default to the public path (to match current `var.eks_access_mode` default of `"public"`). Use `Default` on the Choice state pointing to `GetEksClusterInfo` rather than requiring an explicit `"public"` value. BUT: this makes the Choice inverted (Default = public, explicit = private). Better: Use `IsPresent` check or just require explicit values. Given the user decision, the current Choice structure with `Default` -> private flow is correct as stated in CONTEXT.md.

## Sources

### Primary (HIGH confidence)
- Direct codebase analysis of all 12 ASL files, 3 Terraform modules, CI workflow, and test framework
- `modules/step-functions/eks/` -- manage_storage, scale_services, verify_and_restart_services (pub + priv)
- `modules/step-functions/db/` -- run_mysqldump_on_eks, run_mysqlimport_on_eks (pub + priv)
- `modules/step-functions/utils/` -- run_archive_job (pub + priv)
- `modules/step-functions/orchestrator/refresh_orchestrator.asl.json` -- orchestrator input passing patterns
- Terraform modules: eks/main.tf, db/main.tf, utils/main.tf, root main.tf
- CI: `.github/workflows/step-functions.yml`
- Tests: `tests/test_asl_validation.py`, `tests/test_stepfunctions_local.py`, `tests/conftest.py`

### Secondary (MEDIUM confidence)
- AWS Step Functions ASL specification (Choice state, Credentials, eks:call vs lambda:invoke) -- based on training data, verified against existing working ASL in codebase

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH -- codebase is fully analyzed, patterns are clear from existing working code
- Architecture: HIGH -- consolidation patterns derived from actual diff analysis of 12 files
- Pitfalls: HIGH -- pitfalls identified from real differences found in code analysis

**Research date:** 2026-03-16
**Valid until:** 2026-04-16 (stable -- ASL spec doesn't change frequently)
