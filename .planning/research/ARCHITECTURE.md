# Architecture Patterns

**Domain:** AWS Step Functions modularisation — sub-state-machine extraction
**Researched:** 2026-03-13
**Confidence:** HIGH (derived from direct codebase analysis)

---

## Current Architecture (Baseline)

The existing system is a modular Step Functions orchestrator for cross-account infrastructure refresh. Understanding the current shape is the prerequisite for every modularisation decision.

### Terraform Module Hierarchy

```
main.tf (root)
├── module.iam                          → orchestrator IAM role
├── module.lambda_code                  → S3 bucket + packaged Lambda code
├── module.step_functions_db            → modules/step-functions/db/
├── module.step_functions_efs           → modules/step-functions/efs/
├── module.step_functions_eks           → modules/step-functions/eks/
├── module.step_functions_utils         → modules/step-functions/utils/
└── module.orchestrator                 → modules/step-functions/orchestrator/
    └── receives ARN maps from db/efs/eks/utils outputs
```

Each domain module (db, efs, eks, utils) follows an identical Terraform pattern:
- `local.step_functions` map: `{ key => "file.asl.json" }`
- Single `aws_sfn_state_machine.{domain}` resource using `for_each`
- Shared `orchestrator_role_arn` variable (one IAM role for all SFMs)
- `step_function_arns` output: `{ key => arn }` map

The orchestrator module is special: it receives the ARN maps from all domain modules via `templatefile()` and injects them into `refresh_orchestrator.asl.json` at deploy time.

### IAM Role Model

One IAM role (`{prefix}-orchestrator-role`) is shared by all state machines. The role policy grants:
- `states:StartExecution` / `DescribeExecution` / `StopExecution` on `arn:...:stateMachine:{prefix}-*`
- `events:PutTargets` / `PutRule` / `DescribeRule` (required for `.sync:2` callback)
- `sts:AssumeRole` on configured source/destination account role ARNs
- CloudWatch Logs write permissions

This wildcard on `{prefix}-*` means **any new sub-SFN created under the same prefix is automatically callable** without IAM changes. This is the key architectural enabler for Phase 1.

### ASL Invocation Patterns in Use

The existing codebase already uses both invocation patterns:

**Sync invocation** (`states:startExecution.sync:2`) — used today by the orchestrator:
```json
{
  "Type": "Task",
  "Resource": "arn:aws:states:::states:startExecution.sync:2",
  "Parameters": {
    "StateMachineArn.$": "$.StepFunctions.Utils.ValidateRefreshConfig",
    "Input": { "..." },
    "Name.$": "States.Format('{}-Op', $$.Execution.Name)"
  },
  "ResultSelector": { "Output.$": "$.Output" }
}
```

**SDK integration** (`aws-sdk:*`) — used for direct AWS API calls without Lambda.

Sub-SFN extraction will exclusively use `states:startExecution.sync:2`. The `.sync:2` variant is required (not `.sync`) because it correctly propagates the child execution Output into the parent `ResultSelector`. The older `.sync` variant wraps output differently and is not compatible with the current pattern.

---

## Target Architecture: Sub-State-Machines

### Component Boundaries

| Component | Responsibility | Communicates With | Location |
|-----------|---------------|-------------------|----------|
| `refresh_orchestrator` | 5-phase workflow coordinator | All domain SFMs via sync call | `orchestrator/` module |
| Domain SFMs (db, efs, eks, utils) | Domain-specific multi-step operations | Sub-SFMs via sync call, Lambdas, AWS SDK | Each domain module |
| **Sub-SFMs (new)** | Single reusable pattern (~4-18 states) | AWS SDK, Lambdas only — no further nesting | Shared `shared/` sub-module or within domain module |
| IAM module | Single orchestrator role | All SFMs share the same role | `modules/iam/` |
| Lambda functions | Specialised AWS/K8s operations | Called by domain SFMs and sub-SFMs | `lambdas/` |

### Nesting Depth

```
refresh_orchestrator (L0)
  └── domain SFM e.g. check_replication_sync (L1)
        └── sub-SFM e.g. ManageLambdaLifecycle (L2)
```

Maximum depth: **2 levels below orchestrator (L2)**. Do not nest sub-SFMs inside other sub-SFMs. The cost per extra level (+2-3s latency, extra transitions billed) compounds quickly and the added indirection produces no architectural benefit at this scale.

### Sub-SFM Location — Two Valid Options

**Option A: Flat within the domain module (recommended for Phase 1)**

```
modules/step-functions/efs/
├── main.tf                          (add sub_step_functions local map)
├── check_replication_sync.asl.json  (existing — refactored)
├── setup_cross_account_replication.asl.json
├── manage_lambda_lifecycle.asl.json (NEW sub-SFM)
├── manage_access_point.asl.json     (NEW sub-SFM)
└── manage_file_system_policy.asl.json (NEW sub-SFM)
```

The EFS module's `main.tf` would split `local.step_functions` into two maps: the existing public-facing ones and a new `local.sub_step_functions`. Both use the same `for_each` pattern. The sub-SFM ARNs are exported in the module's `outputs.tf` so the calling ASL files can reference them.

**Option B: Shared sub-module (for cross-domain sub-SFMs)**

```
modules/step-functions/shared/
├── main.tf
├── variables.tf
├── outputs.tf
└── manage_lambda_lifecycle.asl.json
```

Required only if the same sub-SFM is called by multiple domain modules (e.g., `ManageLambdaLifecycle` is called by both `efs/` and potentially `db/`). For Phase 1, all three target sub-SFMs are EFS-only, so Option A is sufficient.

Verdict: **Use Option A for Phase 1.** Introduce Option B only when a sub-SFM is consumed by more than one domain module.

---

## Data Flow: Parent → Child → Parent

### Input Contract

Sub-SFMs receive self-contained inputs. They cannot read the parent's `Assign` variables or `$$.Execution.Input`. Everything must be passed explicitly in `Parameters.Input`.

**Pattern used today (from orchestrator → utils):**
```json
"Parameters": {
  "StateMachineArn.$": "$.StepFunctions.Utils.ValidateRefreshConfig",
  "Input": {
    "Database.$": "$.Database",
    "EFS.$": "$.EFS",
    "SourceAccount.$": "$.SourceAccount"
  },
  "Name.$": "States.Format('{}-ValidateInputs', $$.Execution.Name)"
}
```

The `Name` field is mandatory for `.sync:2` to avoid execution name collisions when a sub-SFM is called in a loop or in parallel branches.

**For sub-SFMs (new pattern):**
```json
"Parameters": {
  "StateMachineArn": "<ARN injected by templatefile>",
  "Input": {
    "FileSystemId.$": "$.SourceFileSystemId",
    "Account.$": "$.SourceAccount"
  },
  "Name.$": "States.Format('{}-ManageSourceAccessPoint', $$.Execution.Name)"
}
```

### Output Contract

`states:startExecution.sync:2` returns:
```json
{
  "ExecutionArn": "...",
  "Input": "...",
  "Name": "...",
  "Output": "{ ... }",   ← child SFM's terminal state output (JSON string)
  "StartDate": "...",
  "StopDate": "...",
  "Status": "SUCCEEDED"
}
```

The `Output` field is a **JSON string**. The `ResultSelector` step must parse or extract from it:
```json
"ResultSelector": {
  "FunctionArn.$": "$.Output.FunctionArn",
  "Created.$":     "$.Output.Created"
}
```

Step Functions automatically deserialises `$.Output` when using `.sync:2` (the string is parsed into an object). Verify this during Phase 1 implementation — behaviour differs between SDK versions. If Output comes as a string, add `States.StringToJson($.Output)` in ResultSelector.

### ARN Injection — How Sub-SFMs Get Their ARNs

Today the orchestrator receives domain SFM ARNs via Terraform `templatefile()`. The same pattern must be applied for sub-SFMs within domain modules.

**Current orchestrator pattern:**
```hcl
# orchestrator/main.tf
definition = templatefile("${path.module}/refresh_orchestrator.asl.json", {
  db_step_functions    = var.db_step_function_arns
  efs_step_functions   = var.efs_step_function_arns
})
```

**New pattern for domain modules that call sub-SFMs:**
```hcl
# efs/main.tf
resource "aws_sfn_state_machine" "efs_domain" {
  for_each = local.step_functions   # top-level SFMs only

  definition = templatefile("${path.module}/${each.value}", {
    manage_lambda_lifecycle_arn    = aws_sfn_state_machine.efs_sub["manage_lambda_lifecycle"].arn
    manage_access_point_arn        = aws_sfn_state_machine.efs_sub["manage_access_point"].arn
    manage_file_system_policy_arn  = aws_sfn_state_machine.efs_sub["manage_file_system_policy"].arn
  })
}

resource "aws_sfn_state_machine" "efs_sub" {
  for_each   = local.sub_step_functions  # sub-SFMs
  definition = file("${path.module}/${each.value}")
}
```

The sub-SFMs themselves use `file()` (not `templatefile()`): they call only AWS SDK integrations or Lambda invocations, not other SFMs, so they need no ARN injection.

**Dependency order within Terraform:** `efs_sub` resources must be created before `efs_domain` because `efs_domain.definition` references `efs_sub.*.arn`. Terraform resolves this automatically via implicit resource dependency when the ARN is referenced directly (not via a `depends_on`).

---

## Patterns to Follow

### Pattern 1: Sub-SFM as Self-Contained Unit

**What:** Each sub-SFM is independently deployable and testable. Its input and output contracts are fully documented in the file header Comment field.

**When:** Any pattern extracted from a parent SFM.

**Principle:** A sub-SFM must succeed or fail cleanly. It must not leave partial state that the parent cannot detect. All Catch handlers inside the sub-SFM route to a `Fail` terminal state with a structured error Output.

**Example terminal error state:**
```json
"OperationFailed": {
  "Type": "Fail",
  "ErrorPath": "$.Error.Cause",
  "CausePath": "$.Error.Cause"
}
```

### Pattern 2: Minimal Input Principle

**What:** Pass only what the sub-SFM needs, not the entire parent context.

**Why:** Keeps input contracts stable and independent of parent refactors. Step Functions have a 256KB input/output limit per state — passing the full parent context risks hitting this limit in large orchestrations.

**How:** Explicitly map only required fields in `Parameters.Input`:
```json
"Input": {
  "FileSystemId.$": "$.SourceFileSystemId",
  "Account": {
    "RoleArn.$": "$.SourceAccount.RoleArn"
  }
}
```

### Pattern 3: ResultPath Isolation

**What:** Store sub-SFM output in a namespaced ResultPath to avoid clobbering parent state.

**When:** Every `states:startExecution.sync:2` call.

**Example:**
```json
"ResultPath": "$.SubResults.SourceLambda",
"ResultSelector": {
  "FunctionArn.$": "$.Output.FunctionArn"
}
```

### Pattern 4: Execution Name Uniqueness

**What:** Always set `Name` in sub-SFM invocation to avoid collision.

**When:** Always — particularly important when the same sub-SFM is called twice in the same parent (e.g., source then destination lambda lifecycle).

**Example:**
```json
"Name.$": "States.Format('{}-SrcLambda', $$.Execution.Name)"
"Name.$": "States.Format('{}-DstLambda', $$.Execution.Name)"
```

### Pattern 5: Cross-Account Sub-SFMs via Account.RoleArn

**What:** Sub-SFMs that operate on cross-account resources accept an optional `Account.RoleArn`. When present, all AWS SDK calls include a `Credentials` block. When absent, same-account calls are made directly.

**Why:** Enables public/private consolidation (Phase 3) using the same sub-SFM definition.

**Example:**
```json
"Credentials": {
  "RoleArn.$": "$.Account.RoleArn"
}
```

Include this only on Task states that make cross-account API calls. Pass states and Choice states never need it.

---

## Anti-Patterns to Avoid

### Anti-Pattern 1: Sub-SFM Calling Sub-SFM

**What:** A sub-SFM invoking another sub-SFM to decompose further.

**Why bad:** Creates 3-level nesting (L3). Latency compounds (+4-6s), IAM permissions become harder to audit, execution traces become difficult to read in the console, and test isolation degrades.

**Instead:** Flatten the logic into the sub-SFM itself. If it truly needs to grow, make it a domain SFM.

### Anti-Pattern 2: Shared Mutable State via SSM

**What:** Using SSM Parameter Store as a side channel between parent and sub-SFM to bypass the input/output contract.

**Why bad:** Introduces hidden coupling, makes testing harder, creates race conditions in parallel branches. The existing codebase already has one SSM-based anti-pattern in `check_replication_sync` (subpath SSM params) — do not extend it.

**Instead:** Pass all required data via `Parameters.Input`. If data is large, store it in S3 and pass the S3 reference.

### Anti-Pattern 3: Partial ARN Injection

**What:** Hardcoding partial ARNs in ASL using `States.Format` with account ID or region literals.

**Why bad:** Breaks in multi-region or multi-account deployments. The existing pattern already avoids this by injecting full ARNs via `templatefile()` — maintain this discipline for all new sub-SFM references.

**Instead:** Always inject full ARNs via `templatefile()` in `main.tf`.

### Anti-Pattern 4: Over-Extracting Small Blocs

**What:** Extracting 1-3 state sequences into sub-SFMs.

**Why bad:** The sub-SFM startup latency (+2-3s) and additional billing for transitions outweighs any DRY benefit for small blocs.

**Instead:** Apply the project's established threshold: extract only if the bloc has **>= 4 states** and/or is **duplicated >= 2 times**.

### Anti-Pattern 5: Changing Public Interface on Refactor

**What:** Renaming or restructuring the input/output fields of existing domain SFMs during Phase 2 refactor.

**Why bad:** External callers (the orchestrator, CI integration tests, manual invocations) depend on stable interfaces. A renamed field causes a silent data flow failure — the parent state machine proceeds but the value is undefined.

**Instead:** Preserve external input keys. Use `Parameters` remapping internally to adapt the parent's field names to sub-SFM input contracts when they differ.

---

## Terraform Layout: Build Order Implications

### Phase 1 — Sub-SFM creation (no structural change)

Only the EFS module changes. Domain and orchestrator modules are untouched.

```
modules/step-functions/efs/
├── main.tf              MODIFIED — add local.sub_step_functions + second for_each resource
├── outputs.tf           MODIFIED — export sub_step_function_arns
├── variables.tf         unchanged
├── check_replication_sync.asl.json         unchanged (consumer, refactored in Phase 2)
├── setup_cross_account_replication.asl.json unchanged
├── manage_lambda_lifecycle.asl.json        NEW
├── manage_access_point.asl.json            NEW
└── manage_file_system_policy.asl.json      NEW
```

Terraform apply order: `efs_sub` resources first (via implicit dependency). No changes to root `main.tf`.

### Phase 2 — Domain SFM refactor

Domain ASL files (`check_replication_sync`, `setup_cross_account_replication`, etc.) are refactored to replace inline duplicate blocks with `states:startExecution.sync:2` calls. Their Terraform resource definition (`aws_sfn_state_machine.efs`) switches from `file()` to `templatefile()` to inject sub-SFM ARNs.

```
modules/step-functions/efs/main.tf  MODIFIED — file() → templatefile() for affected SFMs
modules/step-functions/db/main.tf   MODIFIED — if EnsureSnapshotAvailable added
```

Root `main.tf` is untouched — the EFS module's external interface (outputs) does not change.

### Phase 3 — Public/private consolidation

The 6 public/private pairs in `eks/` and `utils/` are merged. The Terraform modules switch from a suffix-based file selection (`local._eks_suffix`) to a single parametrised file per operation. The `eks_access_mode` variable becomes the control point for the optional `Account.RoleArn` field passed at runtime, not for selecting different ASL files at deploy time.

```
modules/step-functions/eks/main.tf    MODIFIED — remove _suffix logic, use single ASL files
modules/step-functions/utils/main.tf  MODIFIED — same
modules/step-functions/db/main.tf     MODIFIED — same (mysqldump/mysqlimport)
```

---

## Component Communication Matrix

```
                         ┌─────────────┐
                         │  Orchestrator│  L0
                         └──────┬──────┘
              ┌──────────┬──────┼──────┬──────────┐
              ▼          ▼      ▼      ▼          ▼
           ┌─────┐   ┌─────┐ ┌─────┐ ┌─────┐  ┌──────┐
           │ DB  │   │ EFS │ │ EKS │ │Utils│  │Audit │  L1 Domain SFMs
           └──┬──┘   └──┬──┘ └─────┘ └─────┘  └──────┘
              │         │
         ┌────┘    ┌────┴──────────────┐
         ▼         ▼                   ▼
  EnsureSnapshot  ManageLambdaLifecycle  ManageAccessPoint   L2 Sub-SFMs (new)
                  ManageFileSystemPolicy
                  CheckFlagFileSync (future)
                  ClusterSwitchSequence (future)

  All SFMs → Lambda functions (same-account or cross-account via Credentials)
  All SFMs → AWS SDK integrations (same-account or cross-account via Credentials)
```

Arrows represent `states:startExecution.sync:2` invocation (sync, parent waits for child).

---

## Scalability Considerations

| Concern | Current | After modularisation |
|---------|---------|----------------------|
| Largest ASL file | 72 states (unmaintainable) | ~35 states (readable) |
| Duplicate state count | ~54 states | 0 |
| File count | 44 ASL + 0 sub-SFM | ~38 ASL + 6-8 sub-SFM |
| Latency per operation | Baseline | +2-3s per sub-SFM invocation |
| Test surface | 44 files (monolithic) | 44 + 8 files (each independently testable) |
| IAM changes | None needed | None needed (wildcard covers new names) |
| Terraform apply impact | N/A | Phase 1: EFS module only; Phase 2: +DB; Phase 3: EKS+Utils+DB |

---

## Key Architectural Invariants

These must not change regardless of how the modularisation proceeds:

1. **Single IAM role for all SFMs** — the `orchestrator_role_arn` variable is passed identically to every module. No per-sub-SFM role.
2. **ARN injection via templatefile** — never hardcode ARNs in ASL. Always inject via Terraform.
3. **Prefix-scoped naming** — all state machine names follow `{prefix}-{Domain}-{PascalCaseName}`. Sub-SFMs follow the same convention (e.g., `{prefix}-EFS-ManageLambdaLifecycle`).
4. **Stable external input contracts** — callers of domain SFMs (the orchestrator, external systems) must not need changes when domain SFMs are internally refactored.
5. **No public/private Terraform variable for sub-SFMs** — the `Account.RoleArn` optional field is the runtime switch, not a Terraform-level `eks_access_mode`-style variable.

---

## Sources

- Direct analysis of `modules/step-functions/efs/main.tf` — IAM role sharing model
- Direct analysis of `modules/step-functions/orchestrator/main.tf` — templatefile ARN injection pattern
- Direct analysis of `modules/step-functions/orchestrator/refresh_orchestrator.asl.json` — existing sync:2 invocation pattern
- Direct analysis of `modules/iam/main.tf` — wildcard IAM policy on `{prefix}-*`
- Direct analysis of `docs/modularization-plan.md` — sub-SFM input/output contracts, state counts
- Direct analysis of `.planning/codebase/ARCHITECTURE.md` — existing architectural context
- Confidence: HIGH (all claims derived from codebase inspection, not external sources)
