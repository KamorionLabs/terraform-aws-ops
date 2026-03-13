---
phase: 2
slug: refactoring
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-03-13
---

# Phase 2 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest (existing) |
| **Config file** | none — uses default pytest discovery |
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
| 02-01-01 | 01 | 1 | REF-01 | unit | `python -m pytest tests/test_asl_validation.py -k "check_replication_sync" -x` | Existing (auto-discovery) | ⬜ pending |
| 02-01-02 | 01 | 1 | REF-02 | unit | `python -m pytest tests/test_asl_validation.py -k "setup_cross_account_replication" -x` | Existing (auto-discovery) | ⬜ pending |
| 02-01-03 | 01 | 1 | NEW-01 | unit | `python -m pytest tests/test_asl_validation.py -k "check_flag_file_sync" -x` | Auto-discovery | ⬜ pending |
| 02-01-04 | 01 | 1 | REF-05 | unit | `python -m pytest tests/test_interface_snapshots.py -x` | Wave 0 | ⬜ pending |
| 02-02-01 | 02 | 2 | REF-04 | unit | `python -m pytest tests/test_asl_validation.py -k "prepare_snapshot" -x` | Existing (auto-discovery) | ⬜ pending |
| 02-02-02 | 02 | 2 | NEW-02 | unit | `python -m pytest tests/test_asl_validation.py -k "ensure_snapshot_available" -x` | Auto-discovery | ⬜ pending |
| 02-03-01 | 03 | 3 | REF-03 | unit | `python -m pytest tests/test_asl_validation.py -k "refresh_orchestrator" -x` | Existing (auto-discovery) | ⬜ pending |
| 02-03-02 | 03 | 3 | NEW-03 | unit | `python -m pytest tests/test_asl_validation.py -k "cluster_switch_sequence" -x` | Auto-discovery | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [ ] `tests/test_interface_snapshots.py` — interface non-regression tests for REF-05
- [ ] `tests/snapshots/` — reference output schemas for all 4 refactored SFNs
- [ ] `tests/test_asl_validation.py::TestASLCatchSelfContained` — update `_get_sub_sfn_files()` to discover DB and orchestrator sub-SFN files (currently hardcoded to `efs/manage_*.asl.json`)
- [ ] State count validation test (optional) — assert refactored files have <= N states

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| `terraform plan` shows in-place update | REF-05 | Requires Terraform state + provider | Run `terraform plan` in deployment env, verify no destroy/recreate |
| Moved blocks resolve correctly | REF-01-04 | Requires Terraform state | Verify `terraform plan` shows 0 changes after state migration |

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 15s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
