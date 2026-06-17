---
phase: 07
slug: s3-replication-module
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-06-17
---

# Phase 07 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest (repo `tests/`) |
| **Config file** | none dedicated — auto-discovery via `rglob` in `tests/test_asl_validation.py` + `tests/conftest.py` |
| **Quick run command** | `pytest tests/test_asl_validation.py -v --tb=short` |
| **Full suite command** | `pytest tests/ -v` (sfn_local tests auto-skip when SFN Local absent) |
| **Estimated runtime** | ~1–2 seconds (structural ASL validation, no AWS creds) |

---

## Sampling Rate

- **After every task commit:** Run `pytest tests/test_asl_validation.py -v --tb=short`
- **After every plan wave:** Run `terraform fmt -check -recursive && terraform validate` + `pytest tests/ -v`
- **Before `/gsd:verify-work`:** Full suite must be green
- **Max feedback latency:** ~2 seconds (structural); terraform validate adds ~10–30s per wave

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Threat Ref | Secure Behavior | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|------------|-----------------|-----------|-------------------|-------------|--------|
| 07-01-* | 01 | 1 | REPL-01, REPL-03, REPL-04, REPL-05 | — | ASL valid JSON, StartAt+States present, imperative `Credentials.RoleArn.$` on SDK tasks | unit (structural) | `pytest tests/test_asl_validation.py -v` | ✅ (rglob auto-discovers) | ⬜ pending |
| 07-01-* | 01 | 1 | REPL-02 | — | run/check batch ASL: `s3control:createJob` + `describeJob` polling structure valid | unit (structural) | `pytest tests/test_asl_validation.py -v` | ✅ (rglob auto-discovers) | ⬜ pending |
| 07-02-* | 02 | 1 | REPL-01..06 | — | `terraform validate` passes for `modules/step-functions/s3/` | terraform | `terraform validate` (CI `terraform.yml`) | ✅ CI | ⬜ pending |
| 07-03-* | 03 | 2 | IAM-01, IAM-02 | T-07-01 (PassRole scope) | source-account renders valid IAM gated by `enable_s3`; `iam:PassRole` scoped via `iam:PassedToService` | terraform | `terraform validate` + `terraform plan` | ✅ CI | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- None for ASL structural validation — `tests/test_asl_validation.py` auto-discovers the 4 new `*.asl.json` files via `rglob("*.asl.json")` (verified at `tests/test_asl_validation.py:18`). No test wiring needed.
- Deeper execution tests (status-transition simulation, SFN Local) belong to **Phase 9** (INFRA-04 = ASL validation only; no Lambda tests per D-09). Do **not** add them in Phase 7.

*Existing infrastructure covers all Phase 7 structural + terraform requirements.*

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| Live S3 cross-account replication + Batch backfill end-to-end | REPL-02, REPL-03 | Requires two real AWS accounts, source bucket owned by external stack, assume-role chain; SFN Local strips `Credentials`/JSONata so full exec cannot be simulated in CI | Deferred to Phase 9 spec + real-account integration; Phase 7 verifies only structure + `terraform plan` |

---

## Validation Sign-Off

- [ ] All tasks have automated verify (structural ASL + terraform) or are explicitly manual-only/Phase-9-deferred
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references (none — existing infra covers)
- [ ] No watch-mode flags
- [ ] Feedback latency < 30s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
