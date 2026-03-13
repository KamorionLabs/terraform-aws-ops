# Feature Landscape

**Domain:** AWS Step Functions ASL modularization (internal infrastructure refactoring)
**Researched:** 2026-03-13
**Mode:** Ecosystem — what does a complete modularization project include?

---

## Table Stakes

Features without which the modularization is incomplete or unsafe to ship.

| Feature | Why Expected | Complexity | Notes |
|---------|--------------|------------|-------|
| Sub-SFN extraction (ManageLambdaLifecycle) | Eliminates 24 duplicated states — core of the refactor | Medium | ~8 states; called via `states:startExecution.sync:2`; needs Catch |
| Sub-SFN extraction (ManageAccessPoint) | Eliminates 12 duplicated states | Low | ~4 states; simplest pattern |
| Sub-SFN extraction (ManageFileSystemPolicy) | Eliminates 18 duplicated states; JSONata merge logic is the riskiest copy-paste | Medium | ~6 states; PolicyNotFound Catch required |
| Explicit Input/Output contracts per sub-SFN | Sub-SFN cannot access parent's Assign variables — all context via Input/Output | Medium | Each sub-SFN needs a documented, stable JSON schema for Input and Output |
| Self-contained error handling per sub-SFN | Parent cannot roll back a completed sub-SFN; each sub-SFN must Catch its own failures | Medium | Missing this means partial failures leave orphaned AWS resources |
| Public/private pair consolidation (6 pairs) | Eliminates 12 files diverging silently; `Account.RoleArn` optional field approach | Medium | Simple pairs (manage_storage, scale_services, verify_and_restart) are Low; archive/mysqldump/mysqlimport are Medium due to K8s Job differences |
| Terraform registration of new sub-SFN | A sub-SFN deployed outside Terraform breaks IaC consistency; `aws_sfn_state_machine` resource per sub-SFN | Low | Pattern already established in `modules/step-functions/*/main.tf` |
| Backward-compatible parent interface | External callers must not require changes when parents are refactored | Low | The outer Input/Output schema of each parent SFN must remain identical |
| ASL validation tests for each new sub-SFN | Existing pytest suite (`test_asl_validation.py`) must cover new files automatically | Low | Auto-discovery via `rglob("*.asl.json")` means new files are picked up for free; need to ensure they are valid |
| CI pipeline covering new sub-SFN | The GitHub Actions `step-functions.yml` matrix must include or auto-detect new modules | Low | Current EFS matrix is minimal; need to add new modules or generalize discovery |

---

## Differentiators

Features that go beyond basic extraction and meaningfully improve operational quality.

| Feature | Value Proposition | Complexity | Notes |
|---------|-------------------|------------|-------|
| Fix Credentials CI breakage | Current CI skips or fails on cross-account `Credentials` fields — this is a known blocker for any new sub-SFN testing | Medium | Root cause: `test_credentials_have_role_arn` passes locally but CI job matrix uses `jq` differently; investigate `RoleArn.$` vs `RoleArn` handling in jq filter |
| Refactor check_replication_sync (72 → ~35 states) | Most complex file; benefits from all 3 Phase-1 sub-SFN | High | Adds CheckFlagFileSync sub-SFN (~18 states); requires careful state graph rewrite |
| Refactor setup_cross_account_replication (53 → ~30 states) | Second-largest file; directly uses ManageFileSystemPolicy x2 | High | EFS destination creation block stays in-place; complex proxy routing preserved |
| Refactor refresh_orchestrator (51 → ~30 states) | Most business-critical file; ClusterSwitchSequence extraction | High | Low tolerance for regressions; must be done last |
| Refactor prepare_snapshot_for_restore (39 → ~18 states) | EnsureSnapshotAvailable sub-SFN reusable by restore_cluster too | Medium | Cross-account and cross-region snapshot copy variants must all call the same sub-SFN |
| State-machine-level test inputs (per sub-SFN) | Each sub-SFN should have a documented minimal JSON input that exercises the happy path via Step Functions Local | Medium | Currently only generic Pass/Choice/Fail tests exist; sub-SFN-specific fixtures needed in conftest.py |
| Extraction threshold enforcement | Document and enforce the ">= 4 states OR duplicated >= 2 times" rule as a comment/checklist | Low | Prevents over-modularization that adds latency cost with no duplication benefit |
| CI module matrix generalization | Replace per-module hardcoded matrix with glob-based discovery of `*.asl.json` files | Medium | Reduces maintenance burden; avoids new modules being silently excluded from CI |
| Sub-SFN ARN parameterization via Terraform outputs | Parent SFNs reference sub-SFN ARNs via `templatefile()` or SSM — not hardcoded | Medium | Pattern: `templatefile("${path.module}/parent.asl.json", { manage_lambda_lifecycle_arn = module.sub_sfn.arn })` |

---

## Anti-Features

Features to deliberately NOT build as part of this milestone.

| Anti-Feature | Why Avoid | What to Do Instead |
|--------------|-----------|-------------------|
| ASL templating engine (Jinja, jsonnet, cue) | Introduces a build step, breaks native AWS tooling compatibility, out of scope per PROJECT.md | Use native AWS sub-SFN calls (`states:startExecution.sync:2`) — already decided |
| Lambda function refactoring | Separate technical debt (700+ line monoliths) — different risk surface, different milestone | Track separately; do not mix with ASL modularization |
| Monitoring/alerting additions | Separate improvement — adding CloudWatch alarms or X-Ray analysis while refactoring ASL multiplies the blast radius | Add after modularization is stable |
| Lambda unit tests | Explicitly out of scope per PROJECT.md; separate project | Track in separate milestone |
| Automatic rollback orchestration from parent | AWS Step Functions cannot undo completed sub-SFN executions — designing a compensating transaction layer is a major architectural shift | Each sub-SFN handles its own Catch; document rollback runbooks manually |
| Dynamic sub-SFN discovery at runtime | Using SSM or tags to discover sub-SFN ARNs at runtime adds latency and complexity | Pass ARNs as static Input fields or via Terraform-rendered ASL |
| Parallel sub-SFN execution for independent patterns | Tempting for ManageLambdaLifecycle x2 in check_replication_sync, but adds concurrency complexity | Keep sequential for now; revisit only if latency becomes a measured problem |

---

## Feature Dependencies

```
ManageLambdaLifecycle (Phase 1.1)
ManageAccessPoint     (Phase 1.2)  ─── all three must exist before ──▶  Refactor check_replication_sync (Phase 2.1)
ManageFileSystemPolicy (Phase 1.3) ─────────────────────────────────▶  Refactor setup_cross_account_replication (Phase 2.2)

Fix CI Credentials breakage ───────────────────────────────────────▶  All Phase 2 refactors (CI must be green before refactoring callers)

EnsureSnapshotAvailable (Phase 2.4 sub-SFN) ───────────────────────▶  Refactor prepare_snapshot_for_restore (Phase 2.4 caller)
                                              └──────────────────────▶  restore_cluster (secondary caller)

Public/private consolidation (Phase 3) ─── independent, no hard deps ──▶  can run in parallel with Phase 2 if needed

Terraform ARN parameterization ────────────────────────────────────▶  Required before any sub-SFN is deployed (parent must know ARN)
```

---

## Existing Capabilities (Already Built — Do Not Rebuild)

Understanding what exists prevents wasted effort.

| Capability | Location | Status |
|------------|----------|--------|
| JSON syntax validation | `scripts/validate_asl.py`, `tests/test_asl_validation.py` | Working locally |
| Required fields check (StartAt, States) | `tests/test_asl_validation.py::TestASLRequiredFields` | Working locally |
| State reference integrity (Next, Default, Catch) | `tests/test_asl_validation.py::TestASLStateTransitions` | Working locally |
| Unreachable state detection | `scripts/validate_asl.py::_detect_unreachable_states` | Working locally |
| Hardcoded ARN detection | `scripts/validate_asl.py::_check_hardcoded_arns` | Working locally |
| Cross-account Credentials check | `tests/test_asl_validation.py::TestASLCrossAccount` | **CI broken** (Credentials field handling) |
| Step Functions Local execution tests | `tests/test_stepfunctions_local.py` | Working locally; CI broken for cross-account patterns |
| Terraform module structure (per domain) | `modules/step-functions/{db,efs,eks,utils,orchestrator,audit}/` | Working, deployed |
| CI pipeline (GitHub Actions) | `.github/workflows/step-functions.yml` | Partially broken (EFS matrix minimal, Credentials issue) |

---

## MVP Recommendation

Prioritize in this order to maximize duplication reduction per unit of risk:

1. **ManageFileSystemPolicy** — impacts the most active area (cross-account EFS policies), highest cognitive duplication risk (JSONata merge logic copy-pasted)
2. **ManageLambdaLifecycle** — largest single state reduction (24 states)
3. **ManageAccessPoint** — natural companion to ManageLambdaLifecycle; same files impacted
4. **Fix CI Credentials breakage** — must be green before touching the callers in Phase 2
5. **Refactor check_replication_sync** — benefits from all three Phase-1 sub-SFN; most complex file, highest ROI once prerequisites exist
6. **Public/private consolidation (simple pairs)** — low effort, high maintenance value

Defer until Phase 2+ is stable:
- **Refactor refresh_orchestrator** — business-critical; defer until patterns are proven with less critical files
- **CI matrix generalization** — useful but not blocking; do it when a new module is added and the pain is felt
- **Sub-SFN-specific test fixtures** — add incrementally as each sub-SFN is created

---

## Sources

- Project context: `.planning/PROJECT.md` — validated requirements, out-of-scope list, constraints (HIGH confidence — primary source)
- Modularization plan: `docs/modularization-plan.md` — state counts, duplication analysis, per-file refactor targets (HIGH confidence — primary source)
- Existing test suite: `tests/test_asl_validation.py`, `tests/test_stepfunctions_local.py`, `tests/conftest.py` (HIGH confidence — direct code inspection)
- CI pipeline: `.github/workflows/step-functions.yml` (HIGH confidence — direct code inspection)
- Terraform module pattern: `modules/step-functions/efs/main.tf` representative example (HIGH confidence — direct code inspection)
- AWS Step Functions pricing model (sub-SFN latency +2-3s, transition billing): PROJECT.md constraints section (MEDIUM confidence — cited from project authors; consistent with AWS documentation patterns)
