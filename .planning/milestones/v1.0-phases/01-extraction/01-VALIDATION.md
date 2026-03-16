---
phase: 1
slug: extraction
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-03-13
---

# Phase 1 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest (version dans requirements-dev.txt) |
| **Config file** | pytest.ini ou pyproject.toml (a verifier) |
| **Quick run command** | `pytest tests/test_asl_validation.py -v --tb=short` |
| **Full suite command** | `pytest tests/ -v --tb=short` |
| **Estimated runtime** | ~10 seconds |

---

## Sampling Rate

- **After every task commit:** Run `pytest tests/test_asl_validation.py -v --tb=short`
- **After every plan wave:** Run `pytest tests/ -v --tb=short`
- **Before `/gsd:verify-work`:** Full suite must be green
- **Max feedback latency:** 15 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|-----------|-------------------|-------------|--------|
| 1-01-01 | 01 | 0 | PRE-01 | unit | `pytest tests/conftest.py -v` | Partiel | ⬜ pending |
| 1-01-02 | 01 | 1 | SUB-01 | unit | `pytest tests/test_asl_validation.py -v -k manage_lambda` | ❌ W0 | ⬜ pending |
| 1-01-03 | 01 | 1 | SUB-02 | unit | `pytest tests/test_asl_validation.py -v -k manage_access` | ❌ W0 | ⬜ pending |
| 1-01-04 | 01 | 1 | SUB-03 | unit | `pytest tests/test_asl_validation.py -v -k manage_filesystem` | ❌ W0 | ⬜ pending |
| 1-01-05 | 01 | 1 | SUB-04 | unit | `pytest tests/test_asl_validation.py -v -k comment` | ❌ W0 | ⬜ pending |
| 1-01-06 | 01 | 1 | SUB-05 | unit | `pytest tests/test_asl_validation.py -v -k catch` | ❌ W0 | ⬜ pending |
| 1-01-07 | 01 | 1 | SUB-06 | smoke | `terraform plan` | Manuel | ⬜ pending |
| 1-01-08 | 01 | 1 | TST-01 | unit | `pytest tests/test_asl_validation.py -v` | Auto via rglob | ⬜ pending |
| 1-01-09 | 01 | 0 | TST-02 | review | `grep -rn '\\$\\$.Execution.Input' modules/step-functions/efs/` | Manuel | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [ ] `modules/step-functions/efs/manage_lambda_lifecycle.asl.json` — stubs for SUB-01, TST-01
- [ ] `modules/step-functions/efs/manage_access_point.asl.json` — stubs for SUB-02, TST-01
- [ ] `modules/step-functions/efs/manage_filesystem_policy.asl.json` — stubs for SUB-03, TST-01
- [ ] Test `TestASLComment` dans `tests/test_asl_validation.py` — couvre SUB-04
- [ ] Test `TestASLCatchNamed` dans `tests/test_asl_validation.py` — couvre SUB-05
- [ ] Fonction `strip_credentials()` dans `tests/conftest.py` — couvre PRE-01

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| Terraform plan sans erreur avec 3 nouvelles cles | SUB-06 | Necessite acces AWS provider | `terraform plan` dans modules/step-functions/efs/ |
| Audit $$.Execution.Input documente | TST-02 | Analyse pre-extraction one-shot | `grep -rn '$$.Execution.Input' modules/step-functions/efs/` |

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 15s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
