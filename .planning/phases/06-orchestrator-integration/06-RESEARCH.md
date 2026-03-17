# Phase 6: Orchestrator Integration - Research

**Researched:** 2026-03-17
**Domain:** AWS Step Functions ASL orchestration, Terraform module wiring
**Confidence:** HIGH

## Summary

Phase 6 integrates the existing SyncConfigItems SFN (deployed in Phases 4+5) into the refresh_orchestrator ASL workflow. The orchestrator already has 42 states with well-established patterns for optional steps (CheckXOption Choice states), nested SFN calls (startExecution.sync:2), and error tolerance (Catch with continue). The work is entirely mechanical: insert 2 new ASL states following the existing pattern, add a Terraform variable for the sync SFN ARN, and wire the root module.

The SyncConfigItems SFN expects an input with `$.ConfigSync.Enabled`, `$.ConfigSync.Items`, `$.ConfigSync.SourceAccount`, and `$.ConfigSync.DestinationAccount`. The orchestrator already has `$.SourceAccount` and `$.DestinationAccount` in its state, so it only needs to forward `$.ConfigSync` from the caller's input and assemble the nested SFN input from these fields.

**Primary recommendation:** Follow the exact CheckXOption + ExecuteX pattern already used by CheckRotateSecretsOption/RotateDatabaseSecrets, inserting after RotateDatabaseSecrets and before Phase4PostSwitchEKS.

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions
- Position fixe : apres RotateDatabaseSecrets, avant Phase4PostSwitchEKS (CreateEKSStorage)
- Pas de champ ConfigSync.Phase -- la position n'est pas configurable
- Logique : la rotation a mis a jour les credentials DB, la sync copie les bonnes valeurs, les services EKS ne sont pas encore up
- Le ORCH-03 est satisfait par le fait que ConfigSync est optionnel (Enabled=true/false) -- la "configurabilite" c'est l'activation, pas la position
- L'orchestrateur assemble l'input depuis le contexte global : $.SourceAccount, $.DestinationAccount, $.ConfigSync.Items
- Le caller du refresh fournit ConfigSync.Items avec les Transforms (pas de duplication des comptes dans ConfigSync)
- Placeholders dynamiques : la Lambda sync resout les `${...}` dans les Transforms depuis un champ `Context` dans l'input
- L'orchestrateur passe un champ `Context` avec les valeurs connues (endpoints DB, cluster names) en plus des Items
- Le Context est assemble par l'orchestrateur depuis $.Database, $.EKS, etc. -- le caller ne fournit pas le Context, l'orchestrateur le construit
- Continue + log warning : un echec partiel de la sync ne bloque pas le refresh
- Le resultat SyncConfigItems est preserve dans le state (ResultPath) et inclus dans la notification SNS finale
- Le refresh continue normalement meme si Status='partial' ou 'failed'

### Claude's Discretion
- Noms exacts des states ASL (CheckConfigSyncOption, ExecuteSyncConfigItems, etc.)
- Structure exacte du champ Context assemble par l'orchestrateur
- ResultPath pour preserver le resultat sync sans ecraser le state global
- Format du sync result dans la notification SNS

### Deferred Ideas (OUT OF SCOPE)
- Position configurable de la sync dans le flow (ConfigSync.Phase) -- ajouter si le besoin emerge
- Support de la resolution de placeholders Context depuis SSM/Secrets Manager (pas juste depuis l'input) -- v2
</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|-----------------|
| ORCH-01 | Section ConfigSync optionnelle dans l'input JSON du refresh orchestrator -- si absente ou Enabled=false, la sync est ignoree | Pattern CheckXOption Choice state identique a CheckRotateSecretsOption, CheckArchiveJobOption; ConfigSync field must be preserved through MergePrepareResults Pass state |
| ORCH-02 | Orchestrateur appelle SyncConfigItems via startExecution.sync:2 quand ConfigSync.Enabled=true | Pattern Task state identique a RotateDatabaseSecrets; sync SFN ARN injected via templatefile variable; input assembled from global state fields |
| ORCH-03 | Phase d'execution configurable dans l'input (post-restore, pre-verify, etc.) | Satisfied by CONTEXT.md decision: configurability = Enabled true/false toggle, not position. Fixed position after RotateDatabaseSecrets |
</phase_requirements>

## Standard Stack

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| AWS Step Functions ASL | 2022-05-04 (States language) | State machine definition | Already used by all 42 existing states |
| Terraform (HCL) | >= 1.0 | Module variable wiring | Existing project infrastructure |
| templatefile() | Terraform built-in | ARN injection into ASL JSON | Already used by orchestrator main.tf |

### Supporting
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| pytest | >= 8.0 | ASL validation tests | Auto-discovery via rglob for new ASL states |
| json (Python stdlib) | 3.x | ASL structure validation | Used by test_asl_validation.py |

### Alternatives Considered
None -- this phase uses exclusively existing patterns and tools. No new dependencies required.

## Architecture Patterns

### Recommended Project Structure
No new files to create. Modifications only:
```
modules/step-functions/orchestrator/
  refresh_orchestrator.asl.json   # +2 states (CheckConfigSyncOption, ExecuteSyncConfigItems)
  main.tf                         # +1 templatefile variable (sync_step_function_arns)
  variables.tf                    # +1 variable (sync_step_function_arns)
main.tf                           # +1 line: pass sync ARNs to orchestrator module
```

### Pattern 1: Optional Step via Choice + Task (Established)
**What:** A Choice state checks if a feature is enabled, routes to a Task state or skips to the next step.
**When to use:** Every optional step in the orchestrator follows this exact pattern.
**Example (existing CheckRotateSecretsOption):**
```json
"CheckRotateSecretsOption": {
  "Type": "Choice",
  "Choices": [
    {
      "Variable": "$.Database.Options.RotateSecrets",
      "BooleanEquals": true,
      "Next": "RotateDatabaseSecrets"
    }
  ],
  "Default": "Phase4PostSwitchEKS"
},
"RotateDatabaseSecrets": {
  "Type": "Task",
  "Resource": "arn:aws:states:::states:startExecution.sync:2",
  "Parameters": {
    "StateMachineArn.$": "$.StepFunctions.Database.RotateSecrets",
    "Input": { ... },
    "Name.$": "States.Format('{}-RotateSecrets', $$.Execution.Name)"
  },
  "ResultPath": "$.RotateSecretsResult",
  "Next": "Phase4PostSwitchEKS",
  "Catch": [
    {
      "ErrorEquals": ["States.ALL"],
      "ResultPath": "$.RotateSecretsError",
      "Next": "Phase4PostSwitchEKS"
    }
  ]
}
```

**Key observations from existing pattern:**
1. Choice state uses single boolean check (not And with IsPresent) for `$.Database.Options.RotateSecrets` -- but other optional steps (CheckRunSqlScriptsOption, CheckArchiveJobOption) use the `And [IsPresent, BooleanEquals]` pattern. The ConfigSync check should use the And pattern since ConfigSync may not be present at all.
2. Task state uses `startExecution.sync:2` for synchronous nested SFN execution.
3. ResultPath stores result without clobbering global state (e.g., `$.RotateSecretsResult`).
4. Catch block continues to next step on error (resilient pattern).
5. Both the Task's `Next` and the Catch's `Next` point to the same downstream state.

### Pattern 2: Nested SFN Input Assembly (Established)
**What:** The orchestrator extracts fields from its global state and constructs a flat input for nested SFN calls.
**When to use:** Every nested SFN call in the orchestrator follows this pattern.
**Example:**
```json
"Input": {
  "SourceAccount.$": "$.SourceAccount",
  "DestinationAccount.$": "$.DestinationAccount",
  "ConfigSync.$": "$.ConfigSync"
}
```

### Pattern 3: MergePrepareResults State Preservation (Critical)
**What:** The MergePrepareResults Pass state explicitly lists every field to preserve. New top-level input fields (like `ConfigSync`) MUST be added here or they are lost.
**When to use:** Any time a new top-level field is added to the orchestrator input.
**Current state (line 146-166):**
```json
"MergePrepareResults": {
  "Type": "Pass",
  "Parameters": {
    "Database.$": "$.Database",
    "EFS.$": "$.EFS",
    "EKS.$": "$.EKS",
    "Tags.$": "$.Tags",
    "SourceAccount.$": "$.SourceAccount",
    "DestinationAccount.$": "$.DestinationAccount",
    "K8sProxyLambdaArn.$": "$.K8sProxyLambdaArn",
    "StepFunctions.$": "$.StepFunctions",
    "Notifications.$": "$.Notifications",
    "ResourceNames.$": "$.PrepareResult.ResourceNames",
    ...
  }
}
```
**ConfigSync MUST be added here:** `"ConfigSync.$": "$.ConfigSync"` -- otherwise the field is dropped after PrepareRefresh.

### Pattern 4: ARN Injection via templatefile (Established)
**What:** The orchestrator main.tf uses templatefile() to inject SFN ARNs into the ASL JSON. Flat ARN variables are used for direct ARN references in the ASL.
**Example (existing):**
```hcl
definition = templatefile("${path.module}/${each.value}", {
  db_step_functions    = var.db_step_function_arns
  efs_step_functions   = var.efs_step_function_arns
  eks_step_functions   = var.eks_step_function_arns
  utils_step_functions = var.utils_step_function_arns
  cluster_switch_sequence_arn = var.db_step_function_arns["cluster_switch_sequence"]
})
```
**For sync:** Add `sync_config_items_arn = var.sync_step_function_arns["sync_config_items"]` as a flat ARN variable (same pattern as `cluster_switch_sequence_arn`).

### Anti-Patterns to Avoid
- **Forgetting MergePrepareResults:** Adding ConfigSync to the input but not listing it in MergePrepareResults causes the field to silently disappear after the PrepareRefresh phase. This is the #1 pitfall for this phase.
- **Using map lookup in ASL JSON:** Don't use `${sync_step_functions["sync_config_items"]}` in ASL -- use a flat variable `${sync_config_items_arn}` injected via templatefile (matches existing `cluster_switch_sequence_arn` pattern).
- **Blocking on sync failure:** The sync result must NOT cause the refresh to fail. Use Catch with continue pattern.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Optional step routing | Custom Lambda for decision | ASL Choice state | Pure ASL, no compute cost, instant execution |
| Nested SFN invocation | Lambda wrapper calling SFN | startExecution.sync:2 | Built-in Step Functions integration, handles polling |
| Error tolerance | Try/catch in Lambda | ASL Catch with ResultPath | Preserves error context in state, continues flow |
| ARN injection | Hardcoded ARNs | templatefile() + variables | Module decoupling, reusable across environments |

## Common Pitfalls

### Pitfall 1: MergePrepareResults field omission
**What goes wrong:** ConfigSync field is present in initial input but silently dropped after MergePrepareResults because the Pass state uses Parameters (which is an allowlist).
**Why it happens:** The Pass state with Parameters acts as a whitelist -- only explicitly listed fields survive.
**How to avoid:** Add `"ConfigSync.$": "$.ConfigSync"` to the MergePrepareResults Parameters block.
**Warning signs:** CheckConfigSyncOption Choice state gets `null` for `$.ConfigSync.Enabled` and always takes the Default path.

### Pitfall 2: ConfigSync absent in input (IsPresent guard)
**What goes wrong:** If the caller doesn't include ConfigSync at all, checking `$.ConfigSync.Enabled` directly would cause a runtime error.
**Why it happens:** The orchestrator serves multiple use cases -- not all callers need config sync.
**How to avoid:** Use the `And [IsPresent, BooleanEquals]` pattern (same as CheckRunSqlScriptsOption, CheckArchiveJobOption):
```json
{
  "And": [
    { "Variable": "$.ConfigSync.Enabled", "IsPresent": true },
    { "Variable": "$.ConfigSync.Enabled", "BooleanEquals": true }
  ]
}
```
**Warning signs:** Execution fails with `States.Runtime` error when ConfigSync is not in input.

### Pitfall 3: SyncConfigItems input schema mismatch
**What goes wrong:** The nested SFN receives an input structure that doesn't match what it expects.
**Why it happens:** The SyncConfigItems SFN expects `$.ConfigSync.Enabled`, `$.ConfigSync.Items`, `$.ConfigSync.SourceAccount`, `$.ConfigSync.DestinationAccount`. The orchestrator must assemble this correctly from its separate `$.SourceAccount`/`$.DestinationAccount` fields.
**How to avoid:** Build the nested input explicitly in the Task Parameters:
```json
"Input": {
  "ConfigSync": {
    "Enabled": true,
    "Items.$": "$.ConfigSync.Items",
    "SourceAccount.$": "$.SourceAccount",
    "DestinationAccount.$": "$.DestinationAccount"
  }
}
```
**Warning signs:** SyncConfigItems skips (SyncSkipped) even though Enabled=true, because SourceAccount/DestinationAccount are missing from ConfigSync.

### Pitfall 4: Snapshot test regression
**What goes wrong:** The test_interface_snapshots.py test fails because the orchestrator ASL was modified.
**Why it happens:** The snapshot captures terminal output states. Adding new states mid-flow does NOT change terminal states (RefreshComplete/DryRunComplete), so the snapshot should remain valid.
**How to avoid:** Verify that no terminal states are modified. The snapshot only captures Succeed/Fail/End states.
**Warning signs:** This is actually a non-issue for this phase -- but verify by running tests after modification.

### Pitfall 5: IAM permissions for nested SFN execution
**What goes wrong:** Orchestrator role can't start the sync SFN execution.
**Why it happens:** Missing states:StartExecution permission.
**How to avoid:** Already covered -- the IAM role has wildcard permission for `${var.prefix}-*` state machines. The sync SFN is named `${prefix}-Sync-SyncConfigItems` which matches.
**Warning signs:** This is a non-issue -- existing IAM policy already covers it.

## Code Examples

### New ASL States to Insert (after RotateDatabaseSecrets, before Phase4PostSwitchEKS)

```json
"CheckConfigSyncOption": {
  "Type": "Choice",
  "Comment": "Optionally sync config items (secrets/parameters) to destination account",
  "Choices": [
    {
      "And": [
        {
          "Variable": "$.ConfigSync.Enabled",
          "IsPresent": true
        },
        {
          "Variable": "$.ConfigSync.Enabled",
          "BooleanEquals": true
        }
      ],
      "Next": "ExecuteSyncConfigItems"
    }
  ],
  "Default": "Phase4PostSwitchEKS"
},
"ExecuteSyncConfigItems": {
  "Type": "Task",
  "Comment": "Sync secrets and parameters from source to destination account",
  "Resource": "arn:aws:states:::states:startExecution.sync:2",
  "Parameters": {
    "StateMachineArn": "${sync_config_items_arn}",
    "Input": {
      "ConfigSync": {
        "Enabled": true,
        "Items.$": "$.ConfigSync.Items",
        "SourceAccount.$": "$.SourceAccount",
        "DestinationAccount.$": "$.DestinationAccount"
      }
    },
    "Name.$": "States.Format('{}-SyncConfigItems', $$.Execution.Name)"
  },
  "ResultSelector": {
    "Status.$": "$.Output.Status",
    "Results.$": "$.Output.Results"
  },
  "ResultPath": "$.SyncResult",
  "Next": "Phase4PostSwitchEKS",
  "Catch": [
    {
      "ErrorEquals": ["States.ALL"],
      "ResultPath": "$.SyncError",
      "Next": "Phase4PostSwitchEKS"
    }
  ]
}
```

### State Transition Changes

Current flow:
```
CheckRotateSecretsOption -> RotateDatabaseSecrets -> Phase4PostSwitchEKS
                        \-> Phase4PostSwitchEKS (default/skip)
```

New flow:
```
CheckRotateSecretsOption -> RotateDatabaseSecrets -> CheckConfigSyncOption -> ExecuteSyncConfigItems -> Phase4PostSwitchEKS
                        \                                                \-> Phase4PostSwitchEKS (default/skip)
                         \-> CheckConfigSyncOption (when RotateSecrets disabled)
```

Changes required:
1. `RotateDatabaseSecrets.Next`: `"Phase4PostSwitchEKS"` -> `"CheckConfigSyncOption"`
2. `RotateDatabaseSecrets.Catch[0].Next`: `"Phase4PostSwitchEKS"` -> `"CheckConfigSyncOption"`
3. `CheckRotateSecretsOption.Default`: `"Phase4PostSwitchEKS"` -> `"CheckConfigSyncOption"`

### Terraform Variable Addition (orchestrator/variables.tf)

```hcl
variable "sync_step_function_arns" {
  description = "Map of Sync Step Function ARNs"
  type        = map(string)
  default     = {}
}
```

### Terraform main.tf Changes (orchestrator/main.tf)

```hcl
definition = templatefile("${path.module}/${each.value}", {
  db_step_functions    = var.db_step_function_arns
  efs_step_functions   = var.efs_step_function_arns
  eks_step_functions   = var.eks_step_function_arns
  utils_step_functions = var.utils_step_function_arns
  cluster_switch_sequence_arn = var.db_step_function_arns["cluster_switch_sequence"]
  sync_config_items_arn       = var.sync_step_function_arns["sync_config_items"]
})
```

### Root main.tf Changes

```hcl
module "orchestrator" {
  ...
  sync_step_function_arns = module.step_functions_sync.step_function_arns
}
```

### MergePrepareResults Addition

Add this line to the existing Parameters block:
```json
"ConfigSync.$": "$.ConfigSync"
```

### Context Assembly (Claude's Discretion recommendation)

The CONTEXT.md mentions the orchestrator should build a `Context` field with DB endpoints, cluster names, etc. This can be done in the ExecuteSyncConfigItems Task Parameters if needed. However, the SyncConfigItems SFN currently does NOT use a Context field -- it resolves items directly. The Context field with placeholder resolution (`${...}`) is a future enhancement.

**Recommendation:** For Phase 6, pass ConfigSync.Items as-is from the caller. Do NOT add Context assembly yet -- it would require Lambda changes that are out of scope. If Context is needed, it belongs to a separate plan or a v2 enhancement.

**Rationale:** The sync Lambda (sync_config_items.py) does not currently accept or process a `Context` field. Adding Context assembly to the orchestrator without Lambda support would be dead code.

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| N/A (new feature) | Choice + Task pattern for optional steps | Established in orchestrator v1 | Follow existing pattern exactly |

**Deprecated/outdated:**
- None relevant -- this is extending an existing architecture with no technology changes.

## Open Questions

1. **Context field assembly**
   - What we know: CONTEXT.md mentions the orchestrator should build a Context field with DB endpoints for placeholder resolution in transforms.
   - What's unclear: The Lambda doesn't currently support `${...}` placeholder resolution from a Context field. This may need Lambda changes.
   - Recommendation: Defer to a follow-up task or v2. Phase 6 should focus on the core integration (ORCH-01, ORCH-02, ORCH-03) which is fully achievable without Context.

2. **SNS notification enrichment**
   - What we know: CONTEXT.md says sync result should be included in SNS notification.
   - What's unclear: The current SNS notification uses a simple `States.Format()` string. Including structured sync results would require changing the notification format.
   - Recommendation: The SyncResult is already preserved at `$.SyncResult` in the state. The Notify sub-SFN could access it, but the current notification format is a simple string. For Phase 6, preserving the result in state is sufficient. SNS enrichment can be done via a simple States.Format extension or deferred.

## Validation Architecture

### Test Framework
| Property | Value |
|----------|-------|
| Framework | pytest >= 8.0 |
| Config file | None (uses default discovery) |
| Quick run command | `python -m pytest tests/test_asl_validation.py -x -q` |
| Full suite command | `python -m pytest tests/ -x -q --ignore=tests/test_stepfunctions_local.py` |

### Phase Requirements -> Test Map
| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| ORCH-01 | ConfigSync absent/disabled -> sync skipped | unit (ASL structure) | `python -m pytest tests/test_asl_validation.py -x -q` | Yes (auto-discovery covers new states) |
| ORCH-02 | ExecuteSyncConfigItems uses startExecution.sync:2 | unit (ASL structure) | `python -m pytest tests/test_asl_validation.py -x -q` | Yes (TestASLTaskStates.test_task_has_resource) |
| ORCH-03 | ConfigSync optional (Enabled toggle) | unit (ASL structure) | `python -m pytest tests/test_asl_validation.py -x -q` | Yes (TestASLChoiceStates.test_choice_has_choices) |

### Sampling Rate
- **Per task commit:** `python -m pytest tests/test_asl_validation.py -x -q`
- **Per wave merge:** `python -m pytest tests/ -x -q --ignore=tests/test_stepfunctions_local.py`
- **Phase gate:** Full suite green before `/gsd:verify-work`

### Wave 0 Gaps
None -- existing test infrastructure (test_asl_validation.py with auto-discovery via rglob) automatically validates all ASL files including newly added states. The interface snapshot test (test_interface_snapshots.py) will verify terminal outputs remain unchanged.

## Sources

### Primary (HIGH confidence)
- `modules/step-functions/orchestrator/refresh_orchestrator.asl.json` - Full ASL analysis, 42 states, all transition patterns
- `modules/step-functions/orchestrator/main.tf` - templatefile pattern, ARN variable injection
- `modules/step-functions/orchestrator/variables.tf` - Existing variable structure for SFN ARN maps
- `modules/step-functions/sync/sync_config_items.asl.json` - Input schema (ConfigSync.Enabled, Items, SourceAccount, DestinationAccount)
- `modules/step-functions/sync/outputs.tf` - sync_config_items_arn output, step_function_arns map output
- `main.tf` (root) - Module wiring pattern for SFN ARNs
- `modules/iam/main.tf` - IAM permissions (wildcard `${prefix}-*` already covers sync SFN)
- `tests/test_asl_validation.py` - Auto-discovery validation for all ASL files
- `tests/test_interface_snapshots.py` - Terminal output snapshot for orchestrator

### Secondary (MEDIUM confidence)
- None needed -- all findings are from direct codebase analysis

### Tertiary (LOW confidence)
- None

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH - 100% reuse of existing tools, no new dependencies
- Architecture: HIGH - All 4 patterns directly observed in existing codebase with multiple examples
- Pitfalls: HIGH - Each pitfall identified from actual code analysis (MergePrepareResults whitelist behavior, IsPresent guard pattern, input schema mismatch)

**Research date:** 2026-03-17
**Valid until:** Indefinite (codebase patterns, not library versions)
