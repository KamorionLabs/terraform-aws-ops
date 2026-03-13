# Domain Pitfalls

**Domain:** AWS Step Functions modularization — cross-account, sub-SFN extraction, public/private consolidation
**Researched:** 2026-03-13
**Confidence:** HIGH (verified against AWS official docs + direct codebase inspection)

---

## Critical Pitfalls

Mistakes that cause rewrites, silent failures, or production incidents.

---

### Pitfall 1: Variable Scope Loss When Extracting to Sub-SFN

**What goes wrong:** A state uses `$$.Execution.Input.*` or Assign variables from the parent to access original execution context. After extraction to a sub-SFN, those references resolve to the sub-execution's own input/context, not the parent's — silently returning wrong values or `null`.

**Why it happens:** `$$.Execution.Input` is the intrinsic reference to the *current* execution's input, not a global. Each `states:startExecution.sync:2` call is a completely independent execution with its own isolated scope. Parent Assign variables are not inherited.

**Concrete example in this codebase:** `check_replication_sync.asl.json` uses `$$.Execution.Input.SourceSubpathSSMParameter` in the `SetSubpathDefaults` Choice state (line 31) to detect optional fields. If this block is extracted to `ManageLambdaLifecycle`, the `$$.Execution.Input` reference points to the sub-SFN's input, which may not contain that field even if the parent did.

**Consequences:** Optional field detection silently fails, default path always taken, subpath SSM logic never applied. No error thrown — behavior diverges from the original silently.

**Prevention:**
- Before extracting any block, grep the entire block for `$$.Execution.Input` and Assign variable references
- Convert all `$$.Execution.Input.X` references to explicit input fields (pass them in the sub-SFN's Input)
- Document the full input contract of each sub-SFN before writing the ASL

**Detection (warning sign):** Any state in a candidate extraction block containing `$$.Execution.Input.*` or relying on Assign variables set upstream.

**Phase:** Phase 1 (sub-SFN extraction) — audit every candidate block before writing code.

---

### Pitfall 2: Credentials Field Silently Ignored in Sub-SFN Tests

**What goes wrong:** The CI pipeline uses `amazon/aws-stepfunctions-local` Docker image for structural validation. Step Functions Local explicitly does not use the Credentials field and cannot perform cross-account role assumption. Tests that validate state machine *creation* pass. Tests that validate *execution* silently skip the cross-account step or fail with a misleading error unrelated to the real problem.

**Why it happens:** Step Functions Local is documented by AWS as "unsupported, does not provide feature parity." The Credentials field is accepted at parse time but ignored at runtime.

**Concrete example in this codebase:** The existing `test_asl_validation.py` `TestASLCrossAccount` class validates that `Credentials` blocks have a `RoleArn` — a syntax check only. The `test_stepfunctions_local.py` `test_create_state_machine` tests only confirm the definition is accepted, not executed. Any new sub-SFN that uses `Credentials` (e.g., `ManageAccessPoint` with `Account.RoleArn`) will pass CI without ever testing the cross-account path.

**Consequences:** Broken cross-account behavior discovered only in production (real AWS). Regression risk after refactor of `check_replication_sync` which has 20+ states with `Credentials` blocks.

**Prevention:**
- Never rely on Step Functions Local for cross-account behavior validation
- For new sub-SFNs using `Credentials`, write structural tests only — document explicitly that execution path requires real AWS
- Use `TestState API` (AWS-side) for individual state validation when deploying to a test environment
- Add a comment in CI workflow explaining this gap: "Credentials field: structural check only, execution requires real AWS"

**Detection (warning sign):** New sub-SFN file contains `Credentials` field AND is added to the `test-sfn-local` CI job without a mock config file.

**Phase:** Phase 1 (new sub-SFNs) and Phase 2 (refactored files with Credentials) — affects every file with cross-account states.

---

### Pitfall 3: Broken CI Matrix After Adding New ASL Files

**What goes wrong:** The `step-functions.yml` CI workflow has a hardcoded matrix of filenames per module. New sub-SFN files (`manage_lambda_lifecycle.asl.json`, etc.) are not automatically picked up by the module validation jobs. The `validate-db-module` and `validate-efs-module` jobs skip them entirely. Only the pytest-based `validate-local` job picks them up (via `rglob`).

**Why it happens:** The matrix is manually curated. It also omits several existing files already: `run_mysqldump_on_eks_private`, `run_mysqlimport_on_eks_private`, `check_replication_sync`, `setup_cross_account_replication`, `delete_replication`, `get_subpath_and_store_in_ssm` are all absent from the matrix jobs.

**Consequences:** New sub-SFNs can have structural regressions invisible to per-module CI checks. The gap is masked because pytest covers JSON syntax — but the module-level jq-based checks (task count, credentials count) don't run on new files.

**Prevention:**
- When creating each new sub-SFN, immediately add it to the appropriate module matrix in `step-functions.yml`
- Add a CI step that detects ASL files not covered by the matrix (shell glob vs matrix diff)
- Prefer expanding the pytest coverage over duplicating matrix logic

**Detection (warning sign):** Adding a new `.asl.json` file without simultaneously updating `step-functions.yml`.

**Phase:** Phase 1 (new sub-SFNs), Phase 3 (consolidated public/private files).

---

### Pitfall 4: Output Envelope Wrapping with startExecution.sync:2

**What goes wrong:** When a parent SFN calls a sub-SFN using `states:startExecution.sync:2`, the result is wrapped in an envelope: `{"Output": "<actual_output>", "OutputDetails": {...}}`. Code that accesses `$.SomeField` directly (expecting the raw sub-SFN output) gets `null`; the real output is at `$.Output.SomeField` after a `ResultSelector`.

**Why it happens:** The `:2` suffix in `startExecution.sync:2` auto-parses the `Output` field from JSON string to object, but the outer envelope is still present. This is a frequent source of confusion when migrating inline states to sub-SFN calls.

**Concrete example risk:** After extracting `ManageLambdaLifecycle`, the parent `check_replication_sync` will call it and expect `$.FunctionArn` in the result. Without a `ResultSelector: {"FunctionArn.$": "$.Output.FunctionArn"}`, the value is inaccessible.

**Prevention:**
- Every sub-SFN call must include a `ResultSelector` that extracts from `$.Output.*`
- Define the output contract in the sub-SFN's documentation before writing the parent caller
- Pattern to follow (existing orchestrator does this correctly in `ValidateInputs`):
  ```json
  "ResultSelector": {
    "ValidationStatus.$": "$.Output.ValidationStatus",
    "Results.$": "$.Output.Results"
  }
  ```

**Detection (warning sign):** A new Task state calling a sub-SFN with `ResultPath` but without `ResultSelector`. A `Choice` state downstream that references `$.FieldName` directly after a sub-SFN call.

**Phase:** Phase 1 (every sub-SFN call), Phase 2 (every refactored caller).

---

### Pitfall 5: Execution Name Collision in Nested Calls

**What goes wrong:** When a parent passes `Name.$: "States.Format('{}-Something', $$.Execution.Name)"` for each sub-SFN call, and the same parent is retried or the same sub-SFN is called twice in the same execution (e.g., `ManageLambdaLifecycle` called twice in `check_replication_sync` — once for source Lambda, once for destination Lambda), the execution names collide. AWS returns `ExecutionAlreadyExists` (400) if the same name is used for a running execution.

**Why it happens:** AWS enforces execution name uniqueness per state machine for 90 days. The orchestrator already uses this pattern (`$$.Execution.Name}-ValidateInputs`). If two states call the same sub-SFN with the same derived name, only the first succeeds.

**Concrete example risk:** `check_replication_sync` calls `ManageLambdaLifecycle` twice (source + destination). If both use `States.Format('{}-ManageLambda', $$.Execution.Name)`, the second call fails with `ExecutionAlreadyExists`.

**Prevention:**
- When a sub-SFN is called multiple times within one execution, include a discriminator in the name: `States.Format('{}-ManageLambda-Source', $$.Execution.Name)` and `...-Destination`
- Document name patterns as part of each sub-SFN's input contract

**Detection (warning sign):** Two Task states in the same file both calling the same sub-SFN ARN with the same `Name.$` expression.

**Phase:** Phase 2 (refactoring files that call sub-SFNs multiple times, especially `check_replication_sync`).

---

### Pitfall 6: Terraform Circular Dependency — Sub-SFN ARN Not Available at Plan Time

**What goes wrong:** A new sub-SFN Terraform resource must be created before the parent SFN that references its ARN. If the new sub-SFN module is added to the same Terraform plan that also modifies the parent SFN using `templatefile()` with the sub-SFN ARN, Terraform may cycle or require a two-phase apply.

**Why it happens:** The orchestrator's `main.tf` already uses `templatefile()` to inject ARNs from other modules. Sub-SFNs added to an existing module create a new output that the orchestrator (or parent refactored SFN) needs — but that output only exists after `apply`. On first apply, if the sub-SFN and its caller are in the same plan, Terraform resolves the dependency correctly via `depends_on` or implicit references. The risk is when the sub-SFN ARN is embedded in an ASL JSON string rather than passed via `templatefile()`.

**Concrete example risk:** If `check_replication_sync.asl.json` hardcodes the `ManageLambdaLifecycle` ARN as a string literal (like `"arn:aws:states:us-east-1:123456789012:stateMachine:prefix-ManageLambdaLifecycle"`) instead of using a template variable, Terraform cannot track the dependency and the wrong ARN may be used across environments.

**Prevention:**
- Never hardcode sub-SFN ARNs in ASL files — always pass via `templatefile()` variables
- Add sub-SFN ARNs as outputs from their module and use module references as inputs to parent callers
- Example: the orchestrator module already follows this pattern with `var.efs_step_function_arns`
- Extend this pattern to all refactored files that call sub-SFNs

**Detection (warning sign):** An ASL file contains a hardcoded ARN string (grep: `"arn:aws:states:.*stateMachine:`). A new sub-SFN is referenced in an ASL file but is not listed in the calling module's `variables.tf`.

**Phase:** Phase 1 (Terraform module structure for new sub-SFNs), Phase 2 (refactored callers need ARN injection).

---

## Moderate Pitfalls

---

### Pitfall 7: Catch Blocks Not Adapted After Extraction

**What goes wrong:** When extracting a block of states from a monolith into a sub-SFN, the Catch blocks in the extracted states route to states that exist in the original monolith but do not exist in the sub-SFN. The sub-SFN JSON is syntactically valid but has broken `Next` references — the ASL validation tests catch this, but only if the sub-SFN is added to the test suite.

**Why it happens:** Inline states have Catch that routes to a centralized failure state (e.g., `"Next": "SetupFailed"`) in the parent. The extracted sub-SFN needs its own terminal failure state.

**Prevention:**
- Each sub-SFN must have its own `Fail` terminal state for error paths
- When copying a block, remap all `Catch.Next` references to the sub-SFN's local failure state
- The sub-SFN propagates failure by terminating with a `Fail` state — the parent Catches the child execution failure via `States.ExecutionFailed`

**Detection (warning sign):** Extracted sub-SFN has a Catch pointing to a state name that doesn't exist in the sub-SFN's `States` map. The pytest `test_next_references_valid` test catches this if the file is in scope.

**Phase:** Phase 1 (every sub-SFN extraction).

---

### Pitfall 8: Optional Credentials Field Causes JSONPath/JSONata Split Failure

**What goes wrong:** Phase 3 consolidates public/private pairs by making `Account.RoleArn` optional. A Choice state checks `IsPresent` to decide whether to include `Credentials`. However, in JSONata mode (`QueryLanguage: JSONata`), the `Credentials` field must either be present with a valid ARN or absent entirely — it cannot be set to `null` or an empty string. A state that conditionally passes `Credentials` based on an optional field requires two separate state definitions (one with Credentials, one without), not one state with conditional Credentials.

**Why it happens:** The `Credentials.RoleArn` field in ASL does not support conditional presence within a single state definition. JSONata expressions in `Credentials` are evaluated, but if the expression evaluates to null/undefined, Step Functions throws a runtime error rather than omitting the field.

**Concrete example risk:** The public/private consolidation plan is to add `Account.RoleArn` as optional. If implemented as a single Task state with `"RoleArn.$": "$.Account.RoleArn"` and `Account` is absent, the execution fails with `States.Runtime` error — not a clean skip.

**Prevention:**
- Use a Choice state before each cross-account Task: one branch with `Credentials` (when `Account.RoleArn` present), one without (same-account path)
- The 3-state pairs in the public/private consolidation (`manage_storage` = 93 byte diff) map cleanly to this pattern: 1 Choice + 2 Task variants = 3 states replacing 2 files
- For the more complex pairs (`run_mysqldump`, `run_mysqlimport`, `run_archive_job` with 5-6 state diffs), enumerate all states that differ and apply the same Choice-branching pattern

**Detection (warning sign):** A Task state with `"RoleArn.$": "$.Account.RoleArn"` (or similar) where `Account` is documented as optional in the input contract.

**Phase:** Phase 3 (public/private consolidation) — the core design decision of this phase.

---

### Pitfall 9: 256 KB Payload Limit with Complex EFS Context

**What goes wrong:** The `check_replication_sync` workflow accumulates state across 72 states, including EFS file system details, Lambda configs, SSM parameters, and flag-file sync results. After refactoring, the parent execution passes this accumulated context as Input to each sub-SFN call. If the Input exceeds 256 KB, the call fails with `States.DataLimitExceeded`.

**Why it happens:** AWS Step Functions has a hard 256 KB limit on state input/output/context payloads. The current monolith avoids this by keeping data in the local state. Modularization requires serializing all relevant context into the sub-SFN Input.

**Why it is lower risk in this codebase:** The current payloads are config objects (ARNs, IDs, strings) not large data blobs. The largest states pass Lambda configs, VPC configs, and file system IDs — likely well under 256 KB. However, `check_replication_sync` has a parallel cleanup block that aggregates results.

**Prevention:**
- Before extracting a block, estimate the Input payload size: serialize the subset of current state that the sub-SFN needs and measure it
- Pass only what the sub-SFN needs — not the full parent state
- For sub-SFNs like `CheckFlagFileSync`, the input is small (file system IDs, Lambda names, wait counters)

**Detection (warning sign):** A sub-SFN input includes large arrays or aggregated results from previous states.

**Phase:** Phase 2 (refactoring large files, especially `check_replication_sync`).

---

### Pitfall 10: IAM Role Missing Permissions for New Sub-SFN startExecution

**What goes wrong:** Each new sub-SFN requires the calling execution role to have `states:StartExecution` permission on its specific ARN, plus `states:DescribeExecution` and `states:StopExecution` for the `.sync:2` polling pattern. Adding a sub-SFN to Terraform without updating the IAM policy for the orchestrator role causes a silent `AccessDeniedException` at runtime.

**Why it happens:** The existing modules use a single `orchestrator_role_arn` for all SFN executions. The role's inline or managed policy must enumerate the ARNs (or use wildcards) of all callable sub-SFNs. New sub-SFNs added in Phase 1 are not automatically covered.

**Prevention:**
- Define IAM permissions for sub-SFN calls alongside the Terraform resource for each sub-SFN
- Use ARN wildcards scoped to prefix: `arn:aws:states:*:*:stateMachine:${var.prefix}-*` if the trust boundary is acceptable
- Alternatively, pass the sub-SFN ARN through a policy document that is updated as part of the same Terraform module

**Detection (warning sign):** Deploying a new sub-SFN Terraform resource without a corresponding `aws_iam_role_policy` or `aws_iam_policy_document` update for `states:StartExecution` on the new ARN.

**Phase:** Phase 1 (sub-SFN creation) — each new sub-SFN needs an IAM permission update.

---

## Minor Pitfalls

---

### Pitfall 11: Latency Accumulation in Deeply Nested or Repeated Sub-SFN Calls

**What goes wrong:** Each `startExecution.sync:2` call adds 2-3 seconds of overhead (start + polling). In `check_replication_sync`, calling `ManageLambdaLifecycle` twice adds ~4-6 seconds. Combined with 3 sub-SFN calls in a refactored file, the total adds 6-10 seconds to what was previously inline state transitions (milliseconds).

**Why it is minor:** The existing `check_replication_sync` already runs for 4+ hours (120 wait cycles × 120 seconds). An additional 10 seconds is negligible. For shorter flows, 10 seconds may be perceptible.

**Prevention:**
- Apply the extraction threshold rule from the project constraints: only extract if `>= 4 states` OR `duplicated >= 2 times`
- Do not extract single-purpose 2-3 state blocks unique to one file

**Phase:** Phase 1 design decisions.

---

### Pitfall 12: QueryLanguage Mismatch Between Parent and Sub-SFN

**What goes wrong:** The codebase uses both JSONPath (`Parameters.$`) and JSONata (`QueryLanguage: JSONata` + `Arguments`) within the same file and across files. When a block is extracted from a JSONata context into a new sub-SFN, the sub-SFN defaults to JSONPath unless `QueryLanguage: JSONata` is specified at state or machine level. JSONata-specific syntax (e.g., `{% $states.input.X %}`) fails silently or throws a parse error in JSONPath mode.

**Concrete example risk:** `setup_cross_account_replication.asl.json` mixes JSONata states (the proxy routing via `CheckProxyForGetSource`) with JSONPath states. Extracting the `ManageFileSystemPolicy` block — which uses JSONata for policy merging — into a sub-SFN without including `QueryLanguage: JSONata` on the relevant states causes parsing errors.

**Prevention:**
- Explicitly set `QueryLanguage` at the state level for all JSONata states in the sub-SFN
- Test the extracted file with the pytest JSON validation suite before deploying

**Phase:** Phase 1 (ManageFileSystemPolicy extraction uses JSONata for merge logic).

---

## Phase-Specific Warnings

| Phase Topic | Likely Pitfall | Mitigation |
|-------------|---------------|------------|
| Phase 1: ManageLambdaLifecycle extraction | `$$.Execution.Input` references in source block | Audit block for context refs before extraction |
| Phase 1: ManageAccessPoint extraction | Missing Catch remap to local Fail state | Use checklist: every Catch.Next must exist in sub-SFN |
| Phase 1: ManageFileSystemPolicy extraction | JSONata `QueryLanguage` not carried over | Set `QueryLanguage: JSONata` on all JSONata states |
| Phase 1: All new sub-SFNs | Not added to CI matrix | Update `step-functions.yml` matrix on same PR |
| Phase 1: All new sub-SFNs | IAM role missing StartExecution permission | Add IAM policy update to same Terraform PR |
| Phase 2: check_replication_sync refactor | Execution name collision (2x ManageLambdaLifecycle) | Use `-Source`/`-Destination` discriminator suffixes |
| Phase 2: check_replication_sync refactor | Output envelope not extracted in ResultSelector | Add `ResultSelector: {Field.$: $.Output.Field}` to all sub-SFN calls |
| Phase 2: All refactored callers | Sub-SFN ARN hardcoded in ASL | Use templatefile() variable injection |
| Phase 3: Public/private consolidation | Optional Credentials not conditional-branched | Add Choice state gating Credentials path |
| Phase 3: Public/private consolidation | Callers of old private SFN ARN not updated | Audit all callers in orchestrator before deleting old files |

---

## Sources

- [Accessing resources in other AWS accounts in Step Functions](https://docs.aws.amazon.com/step-functions/latest/dg/concepts-access-cross-acct-resources.html) — HIGH confidence (official docs)
- [Testing state machines with Step Functions Local (unsupported)](https://docs.aws.amazon.com/step-functions/latest/dg/sfn-local.html) — HIGH confidence (official docs)
- [Processing input and output in Step Functions](https://docs.aws.amazon.com/step-functions/latest/dg/concepts-input-output-filtering.html) — HIGH confidence (official docs)
- [Passing data between states with variables](https://docs.aws.amazon.com/step-functions/latest/dg/workflow-variables.html) — HIGH confidence (official docs)
- [Breaking down monolith workflows: Modularizing AWS Step Functions](https://aws.amazon.com/blogs/compute/breaking-down-monolith-workflows-modularizing-aws-step-functions-workflows/) — MEDIUM confidence (AWS blog, October 2025)
- [Step Functions service quotas](https://docs.aws.amazon.com/step-functions/latest/dg/service-quotas.html) — HIGH confidence (official docs)
- [Step Functions pitfall: Maximum number of history events (25000)](https://cloudonaut.io/step-functions-pitfall-maximum-number-of-history-events/) — MEDIUM confidence (community, verified against quota docs)
- [StartExecution API reference](https://docs.aws.amazon.com/step-functions/latest/apireference/API_StartExecution.html) — HIGH confidence (official docs, execution name uniqueness)
- Direct codebase inspection: `check_replication_sync.asl.json`, `setup_cross_account_replication.asl.json`, `refresh_orchestrator.asl.json`, `tests/test_asl_validation.py`, `.github/workflows/step-functions.yml` — HIGH confidence (source of truth)
