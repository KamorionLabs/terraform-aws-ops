---
phase: 4
slug: foundation
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-03-16
---

# Phase 4 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest (existing) |
| **Config file** | tests/conftest.py |
| **Quick run command** | `python -m pytest tests/test_asl_validation.py -x -q` |
| **Full suite command** | `python -m pytest tests/ -x -q` |
| **Estimated runtime** | ~15 seconds |

---

## Sampling Rate

- **After every task commit:** Run `python -m pytest tests/test_asl_validation.py -x -q`
- **After every plan wave:** Run `python -m pytest tests/ -x -q`
- **Before `/gsd:verify-work`:** Full suite must be green + `terraform plan` clean
- **Max feedback latency:** 15 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|-----------|-------------------|-------------|--------|
| 04-01-01 | 01 | 1 | SYNC-08 | unit | `python -m pytest tests/test_sync_config_items.py -x -q` | Wave 0 | ⬜ pending |
| 04-01-02 | 01 | 1 | SYNC-01 | unit | `python -m pytest tests/test_asl_validation.py -k "sync_config_items" -x` | Auto-discovery | ⬜ pending |
| 04-01-03 | 01 | 1 | INFRA-01 | smoke | `terraform fmt -check -recursive modules/step-functions/sync/` | N/A | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [ ] `tests/test_sync_config_items.py` — tests du contrat input/output du stub Lambda (SYNC-08)

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| `terraform plan` passe avec nouveau module | INFRA-01 | Requires Terraform state + provider | Run `terraform plan` in deployment env |

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 15s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
