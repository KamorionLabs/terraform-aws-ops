---
phase: 6
slug: orchestrator-integration
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-03-17
---

# Phase 6 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest (existing) |
| **Config file** | tests/conftest.py |
| **Quick run command** | `python -m pytest tests/test_asl_validation.py -x -q` |
| **Full suite command** | `python -m pytest tests/ -x -q --ignore=tests/test_stepfunctions_local.py` |
| **Estimated runtime** | ~15 seconds |

---

## Sampling Rate

- **After every task commit:** Run `python -m pytest tests/test_asl_validation.py -x -q`
- **After every plan wave:** Run `python -m pytest tests/ -x -q --ignore=tests/test_stepfunctions_local.py`
- **Before `/gsd:verify-work`:** Full suite must be green
- **Max feedback latency:** 15 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|-----------|-------------------|-------------|--------|
| 06-01-01 | 01 | 1 | ORCH-01, ORCH-02, ORCH-03 | unit | `python -m pytest tests/test_asl_validation.py -k "refresh_orchestrator" -x` | Auto-discovery | ⬜ pending |
| 06-01-02 | 01 | 1 | ORCH-02 | smoke | `terraform fmt -check -recursive modules/step-functions/orchestrator/` | N/A | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

Existing infrastructure covers all phase requirements via auto-discovery. No new test files needed.

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| ConfigSync absent = no change in behavior | ORCH-01 | Requires live SFN execution | Run orchestrator without ConfigSync in input, verify identical behavior |
| ConfigSync.Enabled=true calls SyncConfigItems | ORCH-02 | Requires live SFN + cross-account | Run orchestrator with ConfigSync section, verify sync SFN is invoked |

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 15s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
