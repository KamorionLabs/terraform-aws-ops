# Milestones

## v1.0 Step Functions Modularization (Shipped: 2026-03-16)

**Phases completed:** 3 phases, 9 plans, 21 requirements
**Timeline:** 2026-03-13 -> 2026-03-16

**Key accomplishments:**
- 6 sous-SFN reutilisables creees (ManageLambdaLifecycle, ManageAccessPoint, ManageFileSystemPolicy, CheckFlagFileSync, EnsureSnapshotAvailable, ClusterSwitchSequence)
- 4 fichiers ASL complexes refactores : check_replication_sync 72->27 states, setup_cross_account 53->45, refresh_orchestrator 51->42, prepare_snapshot 39->33
- 6 paires public/private consolidees en fichiers uniques via EKS.AccessMode Choice state routing
- Tests de non-regression des interfaces (snapshots JSON) empechant les regressions silencieuses
- CI repare (strip_credentials, matrix EFS/DB) + 916 tests passent
- Architecture Terraform dual/triple-map avec moved blocks (zero destroy/recreate)

**Tech debt (from audit):**
- CI Execution.Input audit scope limite a efs/manage_*.asl.json
- CI validate-db-module matrix manque 3 fichiers Phase 2
- restore_cluster dans db_templated sans template vars
- State count targets depasses (~45/~42 vs ~30)

---

