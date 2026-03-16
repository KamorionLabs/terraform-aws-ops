# Phase 2: Refactoring - Research

**Researched:** 2026-03-13
**Domain:** AWS Step Functions ASL refactoring -- replacing inline duplication with sub-SFN calls + Terraform `templatefile()` migration
**Confidence:** HIGH

## Summary

Phase 2 transforms 4 complex ASL files (check_replication_sync, setup_cross_account_replication, prepare_snapshot_for_restore, refresh_orchestrator) plus restore_cluster by replacing their duplicated inline patterns with calls to Phase 1 sub-SFNs (ManageLambdaLifecycle, ManageAccessPoint, ManageFileSystemPolicy) and 3 new sub-SFNs created in this phase (CheckFlagFileSync, EnsureSnapshotAvailable, ClusterSwitchSequence). The refactored files must switch from `file()` to `templatefile()` in Terraform to inject sub-SFN ARNs, while the sub-SFNs themselves remain with `file()`.

The critical challenge is maintaining identical external interfaces (Input/Output schemas) while restructuring internal state graphs. Three `$$.Execution.Input` references in check_replication_sync (lines 31, 50, 51) create a scope boundary issue since sub-SFNs cannot access the parent's execution input. This must be resolved by materializing these values into the state data before the refactored flow begins.

**Primary recommendation:** Execute plans in module order (EFS first, then DB, then Orchestrator) with an explicit `$$.Execution.Input` audit as the first task of Plan 02-01, and use `moved` blocks for zero-downtime Terraform address migration.

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions
- 3 nouvelles sous-SFN a creer dans cette phase :
  - **CheckFlagFileSync** dans le module EFS -- verifie les flag files EFS, sous-SFN reutilisable (meme pattern que Phase 1)
  - **ClusterSwitchSequence** dans le module DB -- sequence rename old -> wait -> delete/tag old -> rename new -> wait -> verify. Waits inclus, autonome en un seul appel
  - **EnsureSnapshotAvailable** dans le module DB -- verifie l'existence du snapshot RDS + wait until available. Pas de creation (geree en amont par l'orchestrateur)
- Toutes suivent le meme pattern que Phase 1 : meme map `local.step_functions`, `for_each`, `file()`, `Comment` ASL + README, wildcard IAM `{prefix}-*`
- Phase 2 cree EnsureSnapshotAvailable ET l'integre dans prepare_snapshot_for_restore ET restore_cluster
- Les deux fichiers appelants sont refactores dans le meme plan (module DB)
- 3 plans groupes par module, dans cet ordre :
  1. **Plan 02-01 : Module EFS** -- Creer CheckFlagFileSync + refactorer check_replication_sync (ManageLambdaLifecycle x2, ManageAccessPoint x2, CheckFlagFileSync) + refactorer setup_cross_account_replication (ManageFileSystemPolicy x2) + migration templatefile() pour ces fichiers + tests
  2. **Plan 02-02 : Module DB** -- Creer EnsureSnapshotAvailable + refactorer prepare_snapshot_for_restore + refactorer restore_cluster + migration templatefile() pour ces fichiers + tests
  3. **Plan 02-03 : Module Orchestrator** -- Creer ClusterSwitchSequence + refactorer refresh_orchestrator + tests
- Ordre : EFS d'abord (plus gros gain, 72->35 states), puis DB, puis Orchestrator
- Audit explicite des refs `$$.Execution.Input` et SSM comme premiere tache du plan EFS (check_replication_sync a 3 refs + 2 SSM)
- Migrer uniquement les fichiers refactores vers `templatefile()` -- les autres gardent `file()`
- Deux maps separees dans chaque module : `local.step_functions` (file(), inchangee) + `local.step_functions_templated` (templatefile(), nouvelle)
- Deux resources `aws_sfn_state_machine` separees (une par map) pour zero impact sur les SFN existantes
- Utiliser des `moved` blocks declaratifs pour le changement d'adresse Terraform (pas de `terraform state mv` manuel)
- Tests structurels ASL automatises (CI) : JSON valide, states atteignables, Catch/Next valides, SFN Local ValidateStateMachine
- Review manuelle des paths critiques (happy path, error path) old vs new
- `terraform plan` pour confirmer in-place update (pas de destroy/recreate)
- Test de non-regression des interfaces (REF-05) : snapshots JSON de reference dans `tests/snapshots/` comparant les schemas Input/Output avant/apres refactoring
- Deploy dev hors scope des plans (validation reelle post-merge)

### Claude's Discretion
- Scope exact de chaque sous-SFN (nombre de states, noms internes)
- Strategie de passage de contexte pour les refs `$$.Execution.Input` dans check_replication_sync
- Nommage interne des states dans les fichiers refactores
- Implementation du test de non-regression des interfaces
- Structure exacte des moved blocks

### Deferred Ideas (OUT OF SCOPE)
None -- discussion stayed within phase scope
</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|-----------------|
| REF-01 | Refactorer check_replication_sync de 72 a ~35 states en appelant ManageLambdaLifecycle x2, ManageAccessPoint x2, et en extrayant CheckFlagFileSync | $$.Execution.Input audit completed, state extraction mapping documented, CheckFlagFileSync scope defined (~21 states) |
| REF-02 | Refactorer setup_cross_account_replication de 53 a ~30 states en appelant ManageFileSystemPolicy x2 | Policy management states mapped (16 removable), cross-region proxy states identified |
| REF-03 | Refactorer refresh_orchestrator de 51 a ~30 states en extrayant ClusterSwitchSequence et simplifiant les Choice states | Cluster switch sequence mapped (10 states), total ~42 after extraction -- target ~30 requires additional Choice simplification |
| REF-04 | Refactorer prepare_snapshot_for_restore de 39 a ~18 states en extrayant EnsureSnapshotAvailable reutilisable par restore_cluster | Wait/verify loops identified in both copy and manual snapshot paths, restore_cluster already calls PrepareSnapshot as sub-SFN |
| REF-05 | Interfaces externes (Input/Output) des SFN refactorees restent identiques pour les appelants existants | Orchestrator call patterns documented, Input schemas analyzed, snapshot test approach defined |
</phase_requirements>

## Standard Stack

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| Terraform `moved` blocks | >= 1.1 | Declarative resource address migration | Avoids `terraform state mv`, works in plan/apply, git-tracked |
| Terraform `templatefile()` | built-in | Inject sub-SFN ARNs into caller ASL files | Already used by orchestrator module -- reference pattern |
| Terraform `file()` | built-in | Load sub-SFN definitions (no variable injection needed) | Already used by all sub-SFNs in Phase 1 |
| AWS Step Functions ASL | JSONPath + JSONata | State machine definition language | Project uses both query languages |

### Supporting
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| pytest | existing | ASL structural validation | Auto-discovery via `rglob("*.asl.json")` covers new files |
| SFN Local (Docker) | existing | ValidateStateMachineDefinition | CI pipeline for semantic validation |
| jq / python json | existing | Snapshot comparison for interface tests | REF-05 non-regression tests |

## Architecture Patterns

### Recommended Project Structure
```
modules/step-functions/
  efs/
    main.tf                                    # Add step_functions_templated map + new resource + moved blocks
    check_replication_sync.asl.json            # Refactored: templatefile() with ARN vars
    setup_cross_account_replication.asl.json   # Refactored: templatefile() with ARN vars
    check_flag_file_sync.asl.json              # NEW sub-SFN (file())
    manage_*.asl.json                          # Phase 1 sub-SFNs (unchanged)
    outputs.tf                                 # Merge both resource maps into single output
    README.md                                  # Add CheckFlagFileSync docs
  db/
    main.tf                                    # Add step_functions_templated map + new resource + moved blocks
    prepare_snapshot_for_restore.asl.json      # Refactored: templatefile() with ARN vars
    restore_cluster.asl.json                   # Refactored: templatefile() with ARN vars
    ensure_snapshot_available.asl.json         # NEW sub-SFN (file())
    outputs.tf                                 # Merge both resource maps, export new sub-SFN ARN
    README.md                                  # Add EnsureSnapshotAvailable docs
  orchestrator/
    main.tf                                    # Already uses templatefile(), add new ARN vars
    refresh_orchestrator.asl.json              # Refactored: add ClusterSwitchSequence ARN var
    variables.tf                               # Add db_step_function_arns if ClusterSwitchSequence ARN needed
tests/
  snapshots/                                   # NEW: Interface reference snapshots for REF-05
  test_asl_validation.py                       # Unchanged (auto-discovers new files)
```

### Pattern 1: Dual Resource Map for `file()` / `templatefile()` Coexistence

**What:** Two separate `local` maps and two separate `aws_sfn_state_machine` resources in the same module, with `moved` blocks to migrate refactored entries.

**When to use:** Every module that has files migrating from `file()` to `templatefile()` in this phase.

**Example (EFS module):**
```hcl
locals {
  # Unchanged entries stay with file() -- zero impact on existing SFNs
  step_functions = {
    delete_filesystem              = "delete_filesystem.asl.json"
    create_filesystem              = "create_filesystem.asl.json"
    get_subpath_and_store_in_ssm   = "get_subpath_and_store_in_ssm.asl.json"
    restore_from_backup            = "restore_from_backup.asl.json"
    delete_replication             = "delete_replication.asl.json"
    cleanup_efs_lambdas            = "cleanup_efs_lambdas.asl.json"
    # Sub-SFNs (no injection needed)
    manage_filesystem_policy = "manage_filesystem_policy.asl.json"
    manage_access_point      = "manage_access_point.asl.json"
    manage_lambda_lifecycle  = "manage_lambda_lifecycle.asl.json"
    check_flag_file_sync     = "check_flag_file_sync.asl.json"  # NEW Phase 2
  }

  # Refactored entries use templatefile() for ARN injection
  step_functions_templated = {
    check_replication_sync          = "check_replication_sync.asl.json"
    setup_cross_account_replication = "setup_cross_account_replication.asl.json"
  }
}

# Existing resource -- unchanged entries
resource "aws_sfn_state_machine" "efs" {
  for_each   = local.step_functions
  name       = local.sfn_names[each.key]
  role_arn   = var.orchestrator_role_arn
  definition = file("${path.module}/${each.value}")
  # ... logging, tracing, tags
}

# New resource -- refactored entries with ARN injection
resource "aws_sfn_state_machine" "efs_templated" {
  for_each   = local.step_functions_templated
  name       = local.sfn_names[each.key]
  role_arn   = var.orchestrator_role_arn
  definition = templatefile("${path.module}/${each.value}", {
    manage_lambda_lifecycle_arn = aws_sfn_state_machine.efs["manage_lambda_lifecycle"].arn
    manage_access_point_arn    = aws_sfn_state_machine.efs["manage_access_point"].arn
    manage_filesystem_policy_arn = aws_sfn_state_machine.efs["manage_filesystem_policy"].arn
    check_flag_file_sync_arn   = aws_sfn_state_machine.efs["check_flag_file_sync"].arn
  })
  # ... same logging, tracing, tags config
}

# Moved blocks: declarative migration from old to new resource address
moved {
  from = aws_sfn_state_machine.efs["check_replication_sync"]
  to   = aws_sfn_state_machine.efs_templated["check_replication_sync"]
}
moved {
  from = aws_sfn_state_machine.efs["setup_cross_account_replication"]
  to   = aws_sfn_state_machine.efs_templated["setup_cross_account_replication"]
}
```

**Critical:** The `sfn_names` map must include keys from BOTH maps for name generation to work correctly. Merge both maps when building the names:
```hcl
sfn_names = {
  for k, v in merge(local.step_functions, local.step_functions_templated) : k => (
    var.naming_convention == "pascal"
    ? "${var.prefix}-EFS-${replace(title(replace(k, "_", " ")), " ", "")}"
    : "${var.prefix}-efs-${replace(k, "_", "-")}"
  )
}
```

### Pattern 2: Sub-SFN Call via `states:startExecution.sync:2` with `templatefile()` ARN

**What:** Replace inline state blocks with a single Task state calling the sub-SFN.

**When to use:** Every refactoring point where inline states are replaced.

**Example (calling ManageLambdaLifecycle from check_replication_sync):**
```json
{
  "EnsureSourceLambdaReady": {
    "Type": "Task",
    "Comment": "Create or update source Lambda via ManageLambdaLifecycle sub-SFN",
    "Resource": "arn:aws:states:::states:startExecution.sync:2",
    "Parameters": {
      "StateMachineArn": "${manage_lambda_lifecycle_arn}",
      "Input": {
        "LambdaConfig": {
          "FunctionName.$": "$.SourceLambdaName",
          "Runtime": "python3.11",
          "Handler": "check_flag_file.lambda_handler",
          "Role.$": "$.LambdaConfig.SourceRole",
          "Code.$": "$.LambdaConfig.Code",
          "Timeout": 60,
          "MemorySize": 128,
          "Architectures": ["arm64"],
          "VpcConfig.$": "$.LambdaConfig.SourceVpcConfig",
          "Environment.$": "$.LambdaConfig.SourceEnvironment",
          "Tags.$": "$.LambdaConfig.Tags",
          "ForceUpdateCode.$": "$.LambdaConfig.ForceUpdateCode"
        },
        "Account.$": "$.SourceAccount"
      },
      "Name.$": "States.Format('{}-SrcLambda', $$.Execution.Name)"
    },
    "ResultSelector": {
      "FunctionName.$": "$.Output.FunctionName",
      "FunctionArn.$": "$.Output.FunctionArn",
      "Status.$": "$.Output.Status"
    },
    "ResultPath": "$.SourceLambdaResult",
    "Next": "EnsureSourceAccessPoint",
    "Catch": [
      {
        "ErrorEquals": ["States.ALL"],
        "ResultPath": "$.Error",
        "Next": "CheckFailed"
      }
    ]
  }
}
```

**Key detail:** The `.sync:2` integration wraps the sub-SFN output in `$.Output`. Use `ResultSelector` to extract the fields needed by downstream states.

### Pattern 3: `$$.Execution.Input` Context Materialization

**What:** Before the refactored flow reaches sub-SFN calls, materialize `$$.Execution.Input` references into the state data so sub-SFNs receive them as regular input fields.

**When to use:** check_replication_sync -- 3 references to `$$.Execution.Input` (lines 31, 50, 51) for SSM parameter names.

**Resolution strategy:** The existing `InitializeState` Pass state already runs first. Expand it (or add a follow-up Pass state) to copy the SSM parameter names from `$$.Execution.Input` into the state data:
```json
{
  "InitializeState": {
    "Type": "Pass",
    "Parameters": {
      "SourceFileSystemId.$": "$.SourceFileSystemId",
      "...": "...",
      "SourceSubpathSSMParameter.$": "States.ArrayGetItem(States.Array($$.Execution.Input.SourceSubpathSSMParameter, ''), 0)",
      "DestinationSubpathSSMParameter.$": "States.ArrayGetItem(States.Array($$.Execution.Input.DestinationSubpathSSMParameter, ''), 0)"
    },
    "Next": "..."
  }
}
```

This eliminates all `$$.Execution.Input` references from the rest of the flow, making the SSM parameter names available via regular `$.SourceSubpathSSMParameter` / `$.DestinationSubpathSSMParameter` references.

### Pattern 4: Output Map Merge for Module Outputs

**What:** Module outputs must include SFN ARNs from both the `file()` and `templatefile()` resources.

**When to use:** EFS and DB module `outputs.tf`.

**Example:**
```hcl
output "step_function_arns" {
  description = "Map of Step Function names to ARNs"
  value = merge(
    { for k, v in aws_sfn_state_machine.efs : k => v.arn },
    { for k, v in aws_sfn_state_machine.efs_templated : k => v.arn }
  )
}
```

### Anti-Patterns to Avoid

- **Breaking the output map:** If the output changes shape (keys disappear or rename), callers break. The `merge()` ensures backward compatibility.
- **Mixing `file()` and `templatefile()` in the same resource:** The `for_each` would need conditional logic per entry. Two separate resources is cleaner.
- **Passing `$$.Execution.Input` to sub-SFNs:** Sub-SFNs have their own execution context. `$$.Execution.Input` refers to the SUB-SFN's input, not the parent's. Must materialize parent context before the call.
- **Using `terraform state mv` instead of `moved` blocks:** State moves are imperative, not tracked in git, and error-prone in team workflows.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Lambda create-or-update | Inline 6-state check/create/update | ManageLambdaLifecycle sub-SFN | Handles ResourceConflictException, ForceUpdateCode, wait-for-ready |
| Access point create-and-wait | Inline 4-state create/wait/check loop | ManageAccessPoint sub-SFN | Handles lifecycle polling, idempotent via ClientToken |
| FS policy merge/add/remove | Inline 6-9 state parse/merge/put | ManageFileSystemPolicy sub-SFN | Handles empty policy, dedup by Sid, delete-entire-policy edge case |
| Terraform resource address migration | `terraform state mv` commands | `moved {}` blocks | Declarative, git-tracked, works across team members |
| Interface regression detection | Manual comparison | Automated snapshot tests | Catches silent Output schema drift |

**Key insight:** Each inline pattern that Phase 1 extracted into a sub-SFN had 4-9 states of complexity with subtle edge cases (race conditions, empty policies, lifecycle polling). Re-implementing them inline would reintroduce the exact bugs Phase 1 fixed.

## Common Pitfalls

### Pitfall 1: `$$.Execution.Input` Scope Loss
**What goes wrong:** After extracting states into a sub-SFN, references to `$$.Execution.Input` now point to the sub-SFN's own input, not the parent's. SSM parameter names become undefined.
**Why it happens:** `$$.Execution.Input` is scoped to the current state machine execution, not the parent.
**How to avoid:** Audit ALL `$$.Execution.Input` references BEFORE refactoring. Materialize them into state data in the InitializeState or a dedicated Pass state.
**Warning signs:** check_replication_sync has 3 references (lines 31, 50, 51). The audit found: `SourceSubpathSSMParameter` (1 isPresent check + 1 value read) and `DestinationSubpathSSMParameter` (1 value read with fallback).

### Pitfall 2: `.sync:2` Output Envelope
**What goes wrong:** The `states:startExecution.sync:2` integration wraps the sub-SFN's output in `$.Output`. Code that expects the result directly at `$.FieldName` gets `null`.
**Why it happens:** `.sync:2` returns `{Output: <sub-SFN-output>, ...execution metadata...}`. Without `ResultSelector`, the full envelope leaks into the state.
**How to avoid:** Always use `ResultSelector` to extract `$.Output.*` fields, or use a follow-up Pass state to reshape.
**Warning signs:** Downstream states referencing `$.SomeField` instead of `$.CallResult.Output.SomeField`.

### Pitfall 3: Terraform Destroy/Recreate Instead of Update
**What goes wrong:** `terraform plan` shows a destroy + create instead of in-place update, causing the SFN ARN to change and breaking all callers.
**Why it happens:** Moving an entry from one `for_each` resource to another without a `moved` block. Terraform sees it as a new resource.
**How to avoid:** Always pair map migration with `moved` blocks. Run `terraform plan` to verify "1 to update, 0 to destroy" before applying.
**Warning signs:** Plan output showing `# aws_sfn_state_machine.efs["check_replication_sync"] will be destroyed` and `# aws_sfn_state_machine.efs_templated["check_replication_sync"] will be created`.

### Pitfall 4: Execution Name Collision
**What goes wrong:** Sub-SFN execution names must be unique within an account. If two sub-SFN calls use the same `Name` format, the second call fails with ExecutionAlreadyExists.
**Why it happens:** check_replication_sync calls ManageLambdaLifecycle twice (source + destination). If both use `States.Format('{}-ManageLambda', $$.Execution.Name)`, the second fails.
**How to avoid:** Use discriminators in the execution name: `States.Format('{}-SrcLambda', $$.Execution.Name)` and `States.Format('{}-DstLambda', $$.Execution.Name)`.
**Warning signs:** `ExecutionAlreadyExists` error on the second sub-SFN call.

### Pitfall 5: Interface Drift in Output Schema
**What goes wrong:** Refactored SFN produces a slightly different Output structure (missing field, renamed key, different nesting). Callers break silently or get null values.
**Why it happens:** While restructuring states, the final Pass state that formats output gets modified inadvertently.
**How to avoid:** Create snapshot reference files of the current Output schemas BEFORE refactoring. Run automated comparison AFTER.
**Warning signs:** Orchestrator receiving `null` for fields it previously read correctly.

### Pitfall 6: Cross-Region Proxy State Inconsistency
**What goes wrong:** setup_cross_account_replication has paired states for direct SDK vs Lambda proxy (cross-region). When replacing policy management with ManageFileSystemPolicy, the proxy routing for the policy GET/PUT is lost.
**Why it happens:** ManageFileSystemPolicy uses `Credentials.RoleArn` (direct SDK), not Lambda proxy. Cross-region policy management may require the proxy.
**How to avoid:** Verify that ManageFileSystemPolicy's direct SDK calls work cross-region via IAM role assumption alone (which they should -- EFS API is regional but accessible via assumed role in the correct region). The proxy pattern in setup_cross_account_replication is for source EFS in a different region -- policy management uses `$.SourceAccount.RoleArn` which already handles this.
**Warning signs:** Policy update fails with AccessDenied when source EFS is in a different region.

## Code Examples

### Example 1: Moved Block Structure

```hcl
# In modules/step-functions/efs/main.tf
# Place moved blocks AFTER both resource definitions

moved {
  from = aws_sfn_state_machine.efs["check_replication_sync"]
  to   = aws_sfn_state_machine.efs_templated["check_replication_sync"]
}

moved {
  from = aws_sfn_state_machine.efs["setup_cross_account_replication"]
  to   = aws_sfn_state_machine.efs_templated["setup_cross_account_replication"]
}
```

### Example 2: CheckFlagFileSync Sub-SFN Input/Output Contract

```json
{
  "Comment": "CheckFlagFileSync | Input: {SourceFileSystemId, DestinationFileSystemId, SourceAccount: {RoleArn}, DestinationAccount: {RoleArn}, LambdaConfig: {Source..., Destination...}, SourceSubpath, DestinationSubpath, FlagId} | Output: {SyncVerified, FlagId, SourceTime, DestinationTime, Status}",
  "StartAt": "EnsureSourceLambda",
  "States": {
    "EnsureSourceLambda": {
      "Type": "Task",
      "Resource": "arn:aws:states:::states:startExecution.sync:2",
      "Comment": "..."
    }
  }
}
```

**Estimated ~21 states** covering:
1. Ensure source Lambda (call ManageLambdaLifecycle) -- but this is a Task state calling ANOTHER sub-SFN
2. Create source access point (call ManageAccessPoint)
3. Bind source AP to Lambda (updateFunctionConfiguration)
4. Ensure destination Lambda (call ManageLambdaLifecycle)
5. Create destination access point (call ManageAccessPoint)
6. Bind destination AP to Lambda (updateFunctionConfiguration)
7. Write flag file on source
8. Wait for propagation
9. Check flag on destination (poll loop)
10. Cleanup access points
11. Prepare output

**Important consideration:** CheckFlagFileSync would itself call ManageLambdaLifecycle and ManageAccessPoint as sub-sub-SFNs. This creates 3-level nesting (orchestrator -> check_replication_sync -> CheckFlagFileSync -> ManageLambdaLifecycle). This is architecturally valid in AWS Step Functions but adds latency (~2-3s per sub-SFN call). Total added latency: ~8-12s for 4 sub-sub-SFN calls within CheckFlagFileSync.

**Alternative:** CheckFlagFileSync could be a "flat" sub-SFN that includes the Lambda lifecycle and AP management inline (not calling sub-sub-SFNs), keeping the nesting to 2 levels. This trades code duplication for lower latency. Recommendation: accept the 3-level nesting since the flag sync operation takes minutes anyway (120 wait cycles of 120s each max), so 10s of sub-SFN overhead is negligible.

### Example 3: EnsureSnapshotAvailable Sub-SFN

```json
{
  "Comment": "EnsureSnapshotAvailable | Input: {SnapshotIdentifier, Account: {RoleArn}, ProxyLambdaArn?, SourceRegion?} | Output: {SnapshotIdentifier, SnapshotArn, Status, EngineVersion}",
  "StartAt": "CheckProxyForDescribe",
  "States": {
    "CheckProxyForDescribe": {
      "Type": "Choice",
      "Comment": "Route to proxy for cross-region snapshot check"
    },
    "DescribeSnapshotDirect": {
      "Type": "Task",
      "Resource": "arn:aws:states:::aws-sdk:rds:describeDBClusterSnapshots"
    },
    "DescribeSnapshotViaProxy": {
      "Type": "Task",
      "Resource": "arn:aws:states:::lambda:invoke"
    },
    "IsAvailable": {
      "Type": "Choice"
    },
    "WaitSnapshot": {
      "Type": "Wait",
      "Seconds": 30
    },
    "SnapshotAvailable": {
      "Type": "Pass",
      "Comment": "Output"
    },
    "SnapshotFailed": {
      "Type": "Fail"
    }
  }
}
```

**Estimated ~7-8 states** (small, focused sub-SFN).

### Example 4: ClusterSwitchSequence Sub-SFN

Extracts from refresh_orchestrator states: Phase3ClusterSwitch through RenameNewCluster (10 states).

**Input:**
```json
{
  "TargetClusterIdentifier": "my-cluster",
  "TmpClusterIdentifier": "my-cluster-restore",
  "OldClusterIdentifier": "my-cluster-old",
  "OldInstanceIdentifierPrefix": "my-cluster-old",
  "TargetInstanceIdentifierPrefix": "my-cluster",
  "Options": {
    "RenameOldCluster": true,
    "DeleteOldCluster": false
  },
  "StepFunctions": {
    "Database": {
      "RenameCluster": "arn:...",
      "DeleteCluster": "arn:...",
      "EnsureClusterNotExists": "arn:...",
      "EnsureClusterAvailable": "arn:..."
    },
    "Utils": {
      "TagResources": "arn:..."
    }
  },
  "DestinationAccount": { "RoleArn": "arn:..." }
}
```

**Estimated ~10-12 states** (the exact current count, moved into a self-contained sub-SFN).

### Example 5: Interface Snapshot Test

```python
# tests/test_interface_snapshots.py
import json
from pathlib import Path

SNAPSHOTS_DIR = Path(__file__).parent / "snapshots"

def extract_output_schema(asl_path: Path) -> dict:
    """Extract the output schema from terminal Pass states (Succeed preceded by output-formatting Pass)."""
    with open(asl_path) as f:
        data = json.load(f)

    # Find Pass states that lead to Succeed states
    states = data["States"]
    output_states = {}
    for name, state in states.items():
        if state.get("Type") == "Pass" and state.get("Next") in states:
            next_state = states[state["Next"]]
            if next_state.get("Type") == "Succeed":
                # Extract the Parameters keys as the output schema
                params = state.get("Parameters", state.get("Output", {}))
                output_states[name] = sorted(params.keys())
    return output_states

def test_check_replication_sync_interface():
    """Output schema must match pre-refactoring snapshot."""
    snapshot_path = SNAPSHOTS_DIR / "check_replication_sync_outputs.json"
    asl_path = Path("modules/step-functions/efs/check_replication_sync.asl.json")

    current = extract_output_schema(asl_path)

    if not snapshot_path.exists():
        # First run: create snapshot
        snapshot_path.parent.mkdir(parents=True, exist_ok=True)
        with open(snapshot_path, "w") as f:
            json.dump(current, f, indent=2, sort_keys=True)
        return

    with open(snapshot_path) as f:
        expected = json.load(f)

    assert current == expected, f"Output schema changed! Update snapshot if intentional."
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| Inline Lambda lifecycle (6 states) | ManageLambdaLifecycle sub-SFN (1 call) | Phase 1 (2026-03) | Eliminates ~24 duplicated states |
| Inline access point management (4 states) | ManageAccessPoint sub-SFN (1 call) | Phase 1 (2026-03) | Eliminates ~12 duplicated states |
| Inline policy merge (6-9 states) | ManageFileSystemPolicy sub-SFN (1 call) | Phase 1 (2026-03) | Eliminates ~18 duplicated states |
| All ASL files loaded with `file()` | Callers use `templatefile()`, sub-SFNs use `file()` | Phase 2 (this phase) | Enables ARN injection for sub-SFN calls |
| Single `for_each` map per module | Dual maps (`step_functions` + `step_functions_templated`) | Phase 2 (this phase) | Zero-impact migration, no SFN recreation |

## Open Questions

1. **setup_cross_account_replication target ~30 states**
   - What we know: Removing 16 policy states + adding 2 sub-SFN calls gives ~41 states. Current target is ~30.
   - What's unclear: Whether additional proxy/cross-region simplification is expected to reach ~30, or if the "~" prefix gives sufficient flexibility.
   - Recommendation: Accept ~39-41 as the realistic result for ManageFileSystemPolicy x2 extraction only. Document the delta and the reason (cross-region proxy routing stays inline -- it's not duplicated, it's specific to this SFN). The user specified "~30" with tilde, indicating approximation.

2. **refresh_orchestrator target ~30 states**
   - What we know: Extracting ClusterSwitchSequence (10 states) + adding 1 call gives ~42. Target is ~30.
   - What's unclear: The success criteria mentions "simplifiant les Choice states" -- this suggests merging some Choice chains. But specific simplifications were not detailed.
   - Recommendation: Extract ClusterSwitchSequence and assess if CheckRotateSecretsOption can be absorbed into the switch sequence or if other Phase 2/3/4 marker Pass states can be removed. Realistic target: ~38-42 after extraction only.

3. **CheckFlagFileSync nesting depth**
   - What we know: CheckFlagFileSync would call ManageLambdaLifecycle x2 and ManageAccessPoint x2 as sub-sub-SFNs (3-level nesting).
   - What's unclear: Whether the added latency (~10s) is acceptable vs. inlining the Lambda/AP management.
   - Recommendation: Accept 3-level nesting. The flag sync operation has ~4h max runtime (120 cycles * 120s). 10s overhead is negligible. Consistency with Phase 1 patterns outweighs micro-optimization.

4. **EnsureSnapshotAvailable scope for restore_cluster**
   - What we know: restore_cluster currently calls PrepareSnapshot sub-SFN. EnsureSnapshotAvailable is simpler (wait only).
   - What's unclear: How restore_cluster benefits from EnsureSnapshotAvailable if it already delegates to PrepareSnapshot.
   - Recommendation: EnsureSnapshotAvailable may be used by restore_cluster for its WaitClusterRestore/CheckClusterStatus loop (ensuring the cluster, not snapshot, is available). Or prepare_snapshot_for_restore calls it internally for its wait loops. Clarify during Plan 02-02.

## Validation Architecture

### Test Framework
| Property | Value |
|----------|-------|
| Framework | pytest (existing) |
| Config file | none -- uses default pytest discovery |
| Quick run command | `python -m pytest tests/test_asl_validation.py -x -q` |
| Full suite command | `python -m pytest tests/ -x -q` |

### Phase Requirements -> Test Map
| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| REF-01 | check_replication_sync refactored to ~35 states | unit | `python -m pytest tests/test_asl_validation.py -k "check_replication_sync" -x` | Existing (auto-discovery) |
| REF-02 | setup_cross_account_replication refactored to ~30 states | unit | `python -m pytest tests/test_asl_validation.py -k "setup_cross_account_replication" -x` | Existing (auto-discovery) |
| REF-03 | refresh_orchestrator refactored | unit | `python -m pytest tests/test_asl_validation.py -k "refresh_orchestrator" -x` | Existing (auto-discovery) |
| REF-04 | prepare_snapshot_for_restore + restore_cluster refactored | unit | `python -m pytest tests/test_asl_validation.py -k "prepare_snapshot" -x` | Existing (auto-discovery) |
| REF-05 | Interfaces unchanged | unit | `python -m pytest tests/test_interface_snapshots.py -x` | Wave 0 |
| NEW-01 | CheckFlagFileSync valid ASL | unit | `python -m pytest tests/test_asl_validation.py -k "check_flag_file_sync" -x` | Auto-discovery |
| NEW-02 | EnsureSnapshotAvailable valid ASL | unit | `python -m pytest tests/test_asl_validation.py -k "ensure_snapshot_available" -x` | Auto-discovery |
| NEW-03 | ClusterSwitchSequence valid ASL | unit | `python -m pytest tests/test_asl_validation.py -k "cluster_switch_sequence" -x` | Auto-discovery |
| NEW-04 | New sub-SFNs have named Fail errors | unit | `python -m pytest tests/test_asl_validation.py::TestASLCatchSelfContained -x` | Needs update (currently only checks `efs/manage_*.asl.json`) |

### Sampling Rate
- **Per task commit:** `python -m pytest tests/test_asl_validation.py -x -q`
- **Per wave merge:** `python -m pytest tests/ -x -q`
- **Phase gate:** Full suite green before `/gsd:verify-work`

### Wave 0 Gaps
- [ ] `tests/test_interface_snapshots.py` -- covers REF-05 (interface non-regression)
- [ ] `tests/snapshots/` directory -- reference output schemas for all 4 refactored SFNs
- [ ] `tests/test_asl_validation.py::TestASLCatchSelfContained` -- update `_get_sub_sfn_files()` to also discover DB and orchestrator sub-SFN files (currently hardcoded to `efs/manage_*.asl.json`)
- [ ] State count validation test (optional) -- assert that refactored files have <= N states

## Sources

### Primary (HIGH confidence)
- Codebase analysis: Direct reading of all 4 ASL files to refactor + 3 Phase 1 sub-SFNs + orchestrator module + Terraform configs
- State counts verified programmatically: check_replication_sync=72, setup_cross_account_replication=53, refresh_orchestrator=51, prepare_snapshot_for_restore=39, restore_cluster=26
- `$$.Execution.Input` references audited: 3 occurrences in check_replication_sync (lines 31, 50, 51), all for SSM parameter names
- Phase 1 patterns verified: `local.step_functions` map, `for_each`, `file()`, Comment ASL, README, named Fail errors

### Secondary (MEDIUM confidence)
- Terraform `moved` blocks: Part of core Terraform since v1.1, well-documented in official docs
- `templatefile()` pattern: Already in production use in orchestrator module (`modules/step-functions/orchestrator/main.tf`)
- State reduction estimates: Calculated from actual state graphs, with ~10% uncertainty on complex files

### Tertiary (LOW confidence)
- State count targets (~30 for setup_cross_account_replication and refresh_orchestrator): These appear aggressive based on analysis. The "~" prefix provides flexibility but actual counts may be 38-42 rather than 30.

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH -- all tools and patterns already in production in this project
- Architecture: HIGH -- dual-map pattern directly extends existing orchestrator module pattern
- Pitfalls: HIGH -- $$.Execution.Input audit completed with exact line numbers, .sync:2 envelope documented from Phase 1 experience
- State reduction estimates: MEDIUM -- calculations verified but complex state dependencies may require additional states not accounted for

**Research date:** 2026-03-13
**Valid until:** 2026-04-13 (stable -- ASL spec and Terraform patterns don't change rapidly)
