---
phase: 3
slug: consolidation
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-03-16
---

# Phase 3 — Validation Strategy

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
- **Before `/gsd:verify-work`:** Full suite must be green
- **Max feedback latency:** 15 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|-----------|-------------------|-------------|--------|
| 03-01-01 | 01 | 1 | CON-01 | unit | `python -m pytest tests/test_asl_validation.py -k "manage_storage" -x` | Auto-discovery | ⬜ pending |
| 03-01-02 | 01 | 1 | CON-02 | unit | `python -m pytest tests/test_asl_validation.py -k "scale_services" -x` | Auto-discovery | ⬜ pending |
| 03-01-03 | 01 | 1 | CON-03 | unit | `python -m pytest tests/test_asl_validation.py -k "verify_and_restart" -x` | Auto-discovery | ⬜ pending |
| 03-02-01 | 02 | 2 | CON-05 | unit | `python -m pytest tests/test_asl_validation.py -k "run_mysqldump" -x` | Auto-discovery | ⬜ pending |
| 03-02-02 | 02 | 2 | CON-06 | unit | `python -m pytest tests/test_asl_validation.py -k "run_mysqlimport" -x` | Auto-discovery | ⬜ pending |
| 03-03-01 | 03 | 3 | CON-04 | unit | `python -m pytest tests/test_asl_validation.py -k "run_archive_job" -x` | Auto-discovery | ⬜ pending |
| 03-03-02 | 03 | 3 | ALL | smoke | `test ! -f modules/step-functions/eks/manage_storage_private.asl.json && test ! -f modules/step-functions/eks/scale_services_private.asl.json` | N/A | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

Existing infrastructure covers all phase requirements via auto-discovery (rglob on `*.asl.json`). Tests automatically pick up new consolidated files and stop testing deleted `_private` files.

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| `terraform plan` shows no destroy/recreate for consolidated SFNs | ALL | Requires Terraform state + provider | Run `terraform plan` in deployment env |
| Deleted `_private` files no longer in Terraform state | ALL | Requires Terraform state | Verify `terraform plan` shows clean state |
| EKS.AccessMode routing works at runtime | ALL | Requires live AWS environment | Execute orchestrator with both public and private inputs |

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 15s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
