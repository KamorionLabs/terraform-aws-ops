# Project Research Summary

**Project:** Step Functions Modularization
**Domain:** AWS Step Functions ASL refactoring — sub-state-machine extraction and public/private consolidation
**Researched:** 2026-03-13
**Confidence:** HIGH

## Executive Summary

This project is an internal infrastructure refactoring of an existing, production AWS Step Functions orchestrator. The system manages cross-account database refresh workflows across EFS, EKS, and RDS domains via 44 ASL state machine files, several of which have grown to 51-72 states with significant duplication (~54 states duplicated across files). The established expert approach is to extract repeated state blocs into reusable sub-state-machines using native `states:startExecution.sync:2` — the same pattern the project already uses at the orchestrator layer. All required tooling (Terraform, pytest, boto3, SFN Local) is already in place; no new dependencies are needed.

The recommended execution order is sequential: first create the three shared sub-SFMs (ManageLambdaLifecycle, ManageAccessPoint, ManageFileSystemPolicy) that eliminate duplication, then fix the CI Credentials breakage that blocks validation, then refactor the complex domain files that consume those sub-SFMs, and finally consolidate the 6 public/private file pairs. This order respects hard dependencies — callers cannot be refactored until the sub-SFMs they call exist — and defers the highest-risk change (refresh_orchestrator) until patterns are proven on lower-criticality files.

The primary risks are behavioral rather than architectural. Variable scope loss (references to `$$.Execution.Input` that silently break after extraction), output envelope handling (forgetting `ResultSelector: {Field.$: $.Output.Field}` after `.sync:2` calls), and execution name collisions when the same sub-SFM is called twice in one execution are the top three failure modes. All are preventable with per-file audits before extraction and a consistent implementation checklist. The CI gap around cross-account Credentials validation is a known limitation of Step Functions Local that cannot be fully solved without real AWS execution in a staging environment.

## Key Findings

### Recommended Stack

The stack requires no new dependencies. All required tools are already installed and in use: Terraform AWS provider `~> 5.x`, Python 3.x, pytest, boto3, and the `amazon/aws-stepfunctions-local` Docker image. The only additions are: a `strip_credentials()` helper in `conftest.py` (fixes the broken CI test path for cross-account state machines), a new pytest class calling `ValidateStateMachineDefinition` via boto3 (catches JSONata expression errors that local tooling misses), and new Terraform `outputs.tf` entries for sub-SFM ARNs.

**Core technologies:**
- `states:startExecution.sync:2`: sub-SFM invocation — already established in 11+ files; `:2` variant is mandatory (parses Output as JSON, not string)
- `templatefile()` in Terraform: ARN injection into parent ASL at deploy time — already the orchestrator pattern; extend to domain modules
- `ValidateStateMachineDefinition` (boto3): authoritative ASL semantic validation — the only tool that validates JSONata expressions; requires OIDC credentials already present in CI
- `amazon/aws-stepfunctions-local`: structural smoke tests — strip `Credentials` fields before registration to unblock CI

**Explicit rejections:** Jinja2/jsonnet templating (breaks native AWS tooling), localstack (unreliable for `.sync:2` in community tier), moto for SFN (cannot execute JSONata), pytest-stepfunctions (Lambda-centric, not SFN-native).

### Expected Features

**Must have (table stakes):**
- ManageLambdaLifecycle sub-SFM — eliminates 24 duplicated states; largest single reduction
- ManageAccessPoint sub-SFM — eliminates 12 duplicated states; simplest pattern
- ManageFileSystemPolicy sub-SFM — eliminates 18 duplicated states; highest copy-paste risk (JSONata merge logic)
- Explicit Input/Output contracts per sub-SFM — sub-SFMs cannot access parent Assign variables; all context must flow via explicit Input/Output
- Self-contained error handling per sub-SFM — each sub-SFM must Catch its own failures and terminate cleanly; parent cannot roll back a completed sub-SFM
- Fix CI Credentials breakage — current CI fails for cross-account state machines; must be resolved before Phase 2 callers can be validated
- Terraform registration of all new sub-SFMs — IaC consistency; `aws_sfn_state_machine` resource per sub-SFM in the same module
- Backward-compatible parent interfaces — external callers (orchestrator, CI, manual invocations) must not need changes

**Should have (differentiators):**
- Refactor check_replication_sync (72 → ~35 states) — highest ROI once Phase 1 sub-SFMs exist; most complex file
- Refactor setup_cross_account_replication (53 → ~30 states) — directly uses ManageFileSystemPolicy x2
- Refactor prepare_snapshot_for_restore (39 → ~18 states) — EnsureSnapshotAvailable sub-SFM reusable by restore_cluster
- Public/private pair consolidation (6 pairs, 12 files → 6 files) — eliminates silent divergence between public and private variants
- Sub-SFM-specific test fixtures — minimal JSON inputs for happy-path testing via SFN Local

**Defer (after Phase 2 is stable):**
- Refactor refresh_orchestrator — business-critical; highest blast radius; defer until patterns proven
- CI matrix generalization (glob-based discovery) — valuable but not blocking; add when pain is felt
- CheckFlagFileSync sub-SFM — future extraction identified in plan, not required for initial modularization
- ClusterSwitchSequence sub-SFM — future; defer until refresh_orchestrator refactor

**Anti-features (do not build):**
- Lambda function refactoring — separate milestone, different risk surface
- Monitoring/alerting additions — add after modularization is stable
- Automatic rollback orchestration — architectural shift out of scope; document runbooks instead
- Parallel sub-SFM execution — revisit only if latency is a measured problem

### Architecture Approach

The target architecture adds a sub-SFM layer (L2) below the existing domain SFMs (L1) which already sit below the orchestrator (L0). Maximum nesting depth is L2 — sub-SFMs must not call other sub-SFMs. Each sub-SFM is self-contained, independently deployable, and uses only AWS SDK integrations or Lambda invocations. Phase 1 sub-SFMs live within the EFS domain module (Option A: flat within the module); a shared cross-domain module (Option B) is introduced only when a sub-SFM is consumed by more than one domain.

**Major components:**
1. `refresh_orchestrator` (L0) — 5-phase workflow coordinator; receives domain SFM ARNs via Terraform `templatefile()`; unchanged in Phases 1-2
2. Domain SFMs — EFS, DB, EKS, Utils (L1) — domain-specific multi-step operations; Phase 2 refactors these to call sub-SFMs instead of inline duplicate blocks
3. Sub-SFMs (L2, new) — single reusable patterns (4-18 states); Phase 1 creates ManageLambdaLifecycle, ManageAccessPoint, ManageFileSystemPolicy in the EFS module
4. IAM module — single `orchestrator_role_arn` shared by all SFMs; wildcard on `{prefix}-*` means new sub-SFMs are automatically callable without IAM changes
5. Terraform module structure — sub-SFMs registered via a separate `local.sub_step_functions` map and `efs_sub` `for_each` resource in the EFS module; ARNs exported via `outputs.tf` and injected into parent SFM definitions via `templatefile()`

**Key invariants that must not change:** single IAM role for all SFMs; ARN injection always via `templatefile()` (never hardcoded); prefix-scoped naming for all state machines; stable external input contracts on domain SFMs.

### Critical Pitfalls

1. **Variable scope loss on extraction** — `$$.Execution.Input` references in an extracted block silently resolve to the sub-SFN's own input, not the parent's. Audit every candidate block for `$$.Execution.Input` and Assign variable references before writing code. Convert all such references to explicit Input fields.

2. **Output envelope not extracted in ResultSelector** — `.sync:2` wraps child output in `{Output: {...}}`. Every sub-SFM call must include `"ResultSelector": {"Field.$": "$.Output.Field"}`. A downstream Choice state referencing `$.FieldName` directly after a sub-SFM call is a silent null-access.

3. **Execution name collision** — When the same sub-SFM is called twice in one execution (e.g., ManageLambdaLifecycle for source then destination), both calls with the same `States.Format('{}-ManageLambda', $$.Execution.Name)` pattern fail with `ExecutionAlreadyExists`. Use discriminator suffixes (`-Source`, `-Destination`).

4. **Credentials field ignored in CI, breaking cross-account validation** — Step Functions Local does not enforce `Credentials` blocks. New sub-SFMs with cross-account states pass CI structurally but are never actually tested cross-account until staging. Strip `Credentials` fields before SFN Local registration; document the gap explicitly in CI comments.

5. **Optional Credentials in Phase 3 requires Choice branching** — For public/private consolidation, `Account.RoleArn` cannot be conditionally included in a single Task state's `Credentials` block. If the field is absent and referenced via JSONata, the execution fails with `States.Runtime`. Use a Choice state to gate the Credentials path.

## Implications for Roadmap

Based on research, the dependency graph is clear and dictates a sequential 3-phase structure. Sub-SFMs must exist before callers can be refactored; CI must be green before refactored callers can be validated; the highest-criticality file (refresh_orchestrator) must be last.

### Phase 1: Sub-SFM Extraction — Create the Building Blocks

**Rationale:** Hard prerequisite for everything in Phase 2. No domain SFM can be refactored until the sub-SFMs it calls exist and are deployed. Also the lowest-risk phase — only the EFS module changes, and only by addition (new ASL files, new Terraform resource block). Orchestrator and other domain modules are untouched.

**Delivers:**
- `manage_lambda_lifecycle.asl.json` (new sub-SFM, ~8 states)
- `manage_access_point.asl.json` (new sub-SFM, ~4 states)
- `manage_file_system_policy.asl.json` (new sub-SFM, ~6 states)
- Updated `efs/main.tf` with `local.sub_step_functions` map and `aws_sfn_state_machine.efs_sub` resource
- Updated `efs/outputs.tf` exporting sub-SFM ARNs
- Fix for CI Credentials breakage (`strip_credentials()` in conftest, update SFN Local test fixture)
- New `ValidateStateMachineDefinition` pytest class for semantic ASL validation
- Updated `step-functions.yml` CI matrix to include new sub-SFM files

**Addresses:** ManageLambdaLifecycle, ManageAccessPoint, ManageFileSystemPolicy (table stakes), Fix CI Credentials breakage (table stakes), Terraform registration (table stakes)

**Avoids:** Pitfall 1 (audit `$$.Execution.Input` before extraction), Pitfall 2 (Credentials CI gap — strip before SFN Local), Pitfall 7 (remap Catch blocks to local Fail state), Pitfall 12 (set `QueryLanguage: JSONata` in ManageFileSystemPolicy)

### Phase 2: Domain SFM Refactor — Replace Duplication with Sub-SFM Calls

**Rationale:** Phase 1 sub-SFMs now exist; CI is green. Domain SFMs can be refactored in order of ROI, deferring the highest-risk file last. Each refactored file is validated by the full test suite before the next one starts.

**Delivers (in recommended order):**
1. `check_replication_sync` refactored: 72 → ~35 states; calls ManageLambdaLifecycle x2, ManageAccessPoint, ManageFileSystemPolicy
2. `setup_cross_account_replication` refactored: 53 → ~30 states; calls ManageFileSystemPolicy x2
3. `prepare_snapshot_for_restore` refactored: 39 → ~18 states; EnsureSnapshotAvailable sub-SFM extracted and usable by restore_cluster
4. Remaining domain files refactored per modularization plan

**Uses:** `templatefile()` ARN injection (extend pattern from orchestrator to domain modules), `states:startExecution.sync:2`, sub-SFM ARN outputs from Phase 1

**Implements:** L1 → L2 sub-SFM invocation layer; `file()` → `templatefile()` migration for affected domain SFMs in Terraform

**Avoids:** Pitfall 2 (ResultSelector on every sub-SFM call), Pitfall 5 (execution name discriminator suffixes for repeated sub-SFM calls), Pitfall 6 (no hardcoded ARNs — always templatefile), Pitfall 9 (minimal input contracts — don't pass full parent state)

### Phase 3: Public/Private Consolidation — Eliminate File Pairs

**Rationale:** Independent from Phases 1-2 (no hard dependencies on sub-SFMs); can run in parallel with Phase 2 if resourcing allows. Scheduled after Phase 2 because: (a) the Credentials Choice-branching pattern established in Phase 2 provides a proven template, (b) the blast radius is different (EKS + Utils + DB modules, not just EFS), and (c) it involves deleting files and updating callers — higher coordination overhead.

**Delivers:**
- 6 public/private ASL file pairs collapsed to 6 single parametrized files
- `Account.RoleArn` optional field as runtime switch (not Terraform-level variable)
- EKS, Utils, DB module Terraform updated (remove `_suffix` logic)
- All orchestrator references to old private ARNs updated

**Avoids:** Pitfall 8 (optional Credentials must use Choice branching, not conditional field), Pitfall 3 (update CI matrix for renamed/deleted files)

### Phase Ordering Rationale

- Phase 1 before Phase 2: hard dependency — callers cannot be refactored until sub-SFMs exist and are deployed
- CI fix in Phase 1: CI must be green before Phase 2 files are touched; a broken CI during refactor of production-critical files is unacceptable
- check_replication_sync before refresh_orchestrator: progressively increasing criticality; check_replication_sync is complex (72 states) but less business-critical than the orchestrator
- refresh_orchestrator deferred beyond Phase 2: highest blast radius; defer until sub-SFM patterns are proven across multiple less-critical files first
- Phase 3 parallel-optional: no hard dependency on Phase 2, but benefits from established Credentials Choice pattern

### Research Flags

Phases likely needing deeper research during planning:
- **Phase 2 — check_replication_sync refactor:** The SSM-based subpath anti-pattern (`$$.Execution.Input.SourceSubpathSSMParameter` in SetSubpathDefaults) requires careful analysis before extraction. The exact states that reference parent context need to be enumerated and the Input contract explicitly designed before writing any code.
- **Phase 2 — EnsureSnapshotAvailable extraction:** Cross-account and cross-region snapshot copy variants both need to call the same sub-SFM — the input contract must handle both cases cleanly. Research the snapshot ARN resolution logic before designing the interface.
- **Phase 3 — mysqldump/mysqlimport consolidation:** These pairs have 5-6 state differences (including K8s Job spec differences between public and private). The Choice-branching approach may require more states than the simpler `manage_storage` pairs. Detailed diff analysis needed before implementation.

Phases with standard patterns (skip research-phase):
- **Phase 1 — ManageAccessPoint and ManageLambdaLifecycle:** Well-understood patterns; direct extraction with known state counts; no cross-account complexity at the sub-SFM level
- **Phase 1 — CI fix (strip_credentials):** The fix is fully specified in STACK.md with working code; no additional research needed
- **Phase 3 — simple pairs (manage_storage, scale_services, verify_and_restart):** 93-byte diffs; straightforward Choice-branching; no research needed

## Confidence Assessment

| Area | Confidence | Notes |
|------|------------|-------|
| Stack | HIGH | All findings from direct codebase inspection and official AWS docs; no new tools needed; existing stack is well-established |
| Features | HIGH | Derived from direct code inspection (test files, CI pipeline, ASL files) and PROJECT.md; duplication counts verified against modularization-plan.md |
| Architecture | HIGH | All architectural claims verified against actual Terraform and ASL source files; IAM wildcard pattern confirmed in modules/iam/main.tf |
| Pitfalls | HIGH | Mix of official AWS docs (Credentials field behavior, 256KB limit, execution name uniqueness) and direct codebase inspection (CI gaps, existing SSM anti-pattern) |

**Overall confidence:** HIGH

### Gaps to Address

- **ValidateStateMachineDefinition integration:** The API is documented and supported in boto3 1.34+ but integration into the test suite requires authoring a new pytest class. The exact fixture structure (how to handle region/role for the API call in CI) needs to be confirmed during Phase 1 implementation. MEDIUM confidence on the integration detail.

- **Output envelope behavior for `.sync:2`:** ARCHITECTURE.md notes that automatic Output deserialization behavior "differs between SDK versions" and recommends verifying during Phase 1 implementation. If `$.Output` comes back as a JSON string rather than an object, all `ResultSelector` patterns need `States.StringToJson($.Output)`. Verify on first sub-SFM deployment before establishing the pattern for all subsequent callers.

- **IAM wildcard coverage for new sub-SFMs:** ARCHITECTURE.md identifies that the existing `{prefix}-*` wildcard should automatically cover new sub-SFMs. PITFALLS.md flags this as a moderate pitfall (Pitfall 10). Confirm the exact policy resource pattern in `modules/iam/main.tf` covers the sub-SFM ARN format before deploying Phase 1 — this is a 5-minute check that avoids a production incident.

- **check_replication_sync SSM anti-pattern scope:** The existing SSM side-channel in `check_replication_sync` is identified as an anti-pattern in ARCHITECTURE.md but its exact scope (which states depend on SSM reads vs writes) is not fully enumerated in the research. This must be mapped before Phase 2 refactor begins.

## Sources

### Primary (HIGH confidence)
- `.planning/PROJECT.md` — validated requirements, constraints, out-of-scope list
- `docs/modularization-plan.md` — state counts, duplication analysis, per-file refactor targets
- `modules/step-functions/efs/main.tf` — Terraform for_each pattern, IAM role sharing
- `modules/step-functions/orchestrator/main.tf` — templatefile ARN injection pattern
- `modules/step-functions/orchestrator/refresh_orchestrator.asl.json` — existing sync:2 invocation pattern
- `modules/iam/main.tf` — wildcard IAM policy on `{prefix}-*`
- `tests/test_asl_validation.py`, `tests/test_stepfunctions_local.py`, `tests/conftest.py` — existing test suite
- `.github/workflows/step-functions.yml` — CI pipeline structure and gaps
- AWS Step Functions Local docs: https://docs.aws.amazon.com/step-functions/latest/dg/sfn-local.html
- ValidateStateMachineDefinition API: https://docs.aws.amazon.com/step-functions/latest/apireference/API_ValidateStateMachineDefinition.html
- Step Functions service quotas: https://docs.aws.amazon.com/step-functions/latest/dg/service-quotas.html
- StartExecution API (execution name uniqueness): https://docs.aws.amazon.com/step-functions/latest/apireference/API_StartExecution.html
- Cross-account access in Step Functions: https://docs.aws.amazon.com/step-functions/latest/dg/concepts-access-cross-acct-resources.html

### Secondary (MEDIUM confidence)
- AWS blog "Breaking down monolith workflows: Modularizing AWS Step Functions" (October 2025) — general patterns consistent with codebase approach
- TestState API (November 2025 enhancement) — enhanced local unit testing with mock support; when mock specified, roleArn becomes optional
- terraform-aws-modules/step-functions v5.0.2 — reference for module output conventions

### Tertiary (LOW confidence)
- SFN Local startExecution.sync:2 unsupported (community issue #4132) — community-reported; consistent with official docs but not officially acknowledged in AWS docs
- Step Functions history event limit pitfall (cloudonaut.io) — MEDIUM confidence; verified against quota docs

---
*Research completed: 2026-03-13*
*Ready for roadmap: yes*
