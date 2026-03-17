---
phase: 5
slug: sync-engine
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-03-17
---

# Phase 5 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest (existing) |
| **Config file** | tests/conftest.py |
| **Quick run command** | `python -m pytest tests/test_sync_config_items.py -x -q` |
| **Full suite command** | `python -m pytest tests/ -x -q --ignore=tests/test_stepfunctions_local.py` |
| **Estimated runtime** | ~15 seconds |

---

## Sampling Rate

- **After every task commit:** Run `python -m pytest tests/test_sync_config_items.py -x -q`
- **After every plan wave:** Run `python -m pytest tests/ -x -q --ignore=tests/test_stepfunctions_local.py`
- **Before `/gsd:verify-work`:** Full suite must be green
- **Max feedback latency:** 15 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|-----------|-------------------|-------------|--------|
| 05-01-01 | 01 | 1 | SYNC-02 | unit | `python -m pytest tests/test_sync_config_items.py -k "cross_account" -x` | Needs new tests | ⬜ pending |
| 05-01-02 | 01 | 1 | SYNC-03 | unit | `python -m pytest tests/test_sync_config_items.py -k "path_mapping" -x` | Needs new tests | ⬜ pending |
| 05-01-03 | 01 | 1 | SYNC-04 | unit | `python -m pytest tests/test_sync_config_items.py -k "transform" -x` | Needs new tests | ⬜ pending |
| 05-01-04 | 01 | 1 | SYNC-05, SYNC-06 | unit | `python -m pytest tests/test_sync_config_items.py -k "create or merge" -x` | Needs new tests | ⬜ pending |
| 05-01-05 | 01 | 1 | SYNC-07 | unit | `python -m pytest tests/test_sync_config_items.py -k "recursive" -x` | Needs new tests | ⬜ pending |
| 05-01-06 | 01 | 1 | INFRA-02 | unit | `python -m pytest tests/test_asl_validation.py -k "sync" -x` | Auto-discovery | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [ ] `tests/test_sync_config_items.py` — rewrite stub tests with real behavior tests covering SYNC-02 through SYNC-07 (mock boto3 clients)

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| Real cross-account SM/SSM sync | SYNC-02 | Requires live AWS accounts with IAM roles | Run SFN manually against test secrets in dev accounts |

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 15s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
