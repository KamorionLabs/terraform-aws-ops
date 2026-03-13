# Requirements: Step Functions Modularization

**Defined:** 2026-03-13
**Core Value:** Chaque pattern ASL duplique n'existe qu'une seule fois, dans une sous-SFN reutilisable testee independamment.

## v1 Requirements

Requirements pour la modularisation complete. Chaque requirement mappe a une phase du roadmap.

### Prerequisites

- [x] **PRE-01**: Fix CI GitHub Actions — stripper les champs Credentials dans conftest.py pour que SFN Local accepte les definitions cross-account
- [ ] **PRE-02**: Etablir le pattern templatefile() pour injection ARN des sous-SFN dans les modules Terraform appelants

### Extraction Sous-SFN

- [ ] **SUB-01**: Creer sous-SFN ManageLambdaLifecycle (~8 states) eliminant ~24 states dupliques dans check_replication_sync et get_subpath_and_store_in_ssm
- [ ] **SUB-02**: Creer sous-SFN ManageAccessPoint (~4 states) eliminant ~12 states dupliques dans check_replication_sync et get_subpath_and_store_in_ssm
- [ ] **SUB-03**: Creer sous-SFN ManageFileSystemPolicy (~6 states) eliminant ~18 states dupliques dans setup_cross_account_replication et delete_replication
- [ ] **SUB-04**: Contrats Input/Output explicites documentes (JSON schema) pour chaque sous-SFN
- [ ] **SUB-05**: Catch auto-contenu par sous-SFN — chaque sous-SFN gere ses erreurs sans dependre du parent
- [ ] **SUB-06**: Module Terraform dans modules/step-functions/ avec aws_sfn_state_machine resource pour chaque sous-SFN

### Refactoring Fichiers Complexes

- [ ] **REF-01**: Refactorer check_replication_sync de 72 a ~35 states en appelant ManageLambdaLifecycle x2, ManageAccessPoint x2, et en extrayant CheckFlagFileSync
- [ ] **REF-02**: Refactorer setup_cross_account_replication de 53 a ~30 states en appelant ManageFileSystemPolicy x2
- [ ] **REF-03**: Refactorer refresh_orchestrator de 51 a ~30 states en extrayant ClusterSwitchSequence et simplifiant les Choice states
- [ ] **REF-04**: Refactorer prepare_snapshot_for_restore de 39 a ~18 states en extrayant EnsureSnapshotAvailable reutilisable par restore_cluster
- [ ] **REF-05**: Interfaces externes (Input/Output) des SFN refactorees restent identiques pour les appelants existants

### Consolidation Public/Private

- [ ] **CON-01**: Consolider manage_storage et manage_storage_private en 1 fichier parametrise via Account.RoleArn optionnel
- [ ] **CON-02**: Consolider scale_services et scale_services_private en 1 fichier parametrise
- [ ] **CON-03**: Consolider verify_and_restart_services et verify_and_restart_services_private en 1 fichier parametrise
- [ ] **CON-04**: Consolider run_archive_job et run_archive_job_private en 1 fichier avec gestion Jobs K8s unifiee
- [ ] **CON-05**: Consolider run_mysqldump_on_eks et run_mysqldump_on_eks_private en 1 fichier
- [ ] **CON-06**: Consolider run_mysqlimport_on_eks et run_mysqlimport_on_eks_private en 1 fichier

### Tests & CI

- [ ] **TST-01**: Tests ASL de validation pour chaque nouvelle sous-SFN (auto-decouverte via rglob)
- [x] **TST-02**: Audit pre-extraction des references $$.Execution.Input dans chaque bloc candidat pour eviter le scope loss

## v2 Requirements

Deferred a un milestone futur.

### Qualite Avancee

- **QAL-01**: Generalisation du matrix CI pour auto-decouverte des modules ASL
- **QAL-02**: Fixtures de test specifiques par sous-SFN (inputs minimaux happy path)
- **QAL-03**: Integration ValidateStateMachineDefinition (boto3) pour validation semantique ASL
- **QAL-04**: Seuil d'extraction documente et enforce (>= 4 states OU duplique >= 2 fois)

## Out of Scope

| Feature | Reason |
|---------|--------|
| ASL templating (Jinja, jsonnet, cue) | Introduit un build step, casse la compatibilite AWS native — decision prise |
| Tests unitaires Lambda Python | Projet separe, surface de risque differente |
| Monitoring/alerting CloudWatch | Amelioration separee — ne pas multiplier le blast radius |
| Refactoring Lambda (monolithes 700+ lignes) | Dette technique separee |
| Rollback automatique depuis le parent | Impossible par design AWS SFN — documenter manuellement |
| Decouverte dynamique sous-SFN (SSM/tags) | Ajoute latence et complexite — ARN statiques via Terraform |
| Execution parallele sous-SFN | Complexite concurrence — revisiter si latence mesuree |

## Traceability

| Requirement | Phase | Status |
|-------------|-------|--------|
| PRE-01 | Phase 1 | Complete |
| PRE-02 | Phase 1 | Pending |
| SUB-01 | Phase 1 | Pending |
| SUB-02 | Phase 1 | Pending |
| SUB-03 | Phase 1 | Pending |
| SUB-04 | Phase 1 | Pending |
| SUB-05 | Phase 1 | Pending |
| SUB-06 | Phase 1 | Pending |
| TST-01 | Phase 1 | Pending |
| TST-02 | Phase 1 | Complete |
| REF-01 | Phase 2 | Pending |
| REF-02 | Phase 2 | Pending |
| REF-03 | Phase 2 | Pending |
| REF-04 | Phase 2 | Pending |
| REF-05 | Phase 2 | Pending |
| CON-01 | Phase 3 | Pending |
| CON-02 | Phase 3 | Pending |
| CON-03 | Phase 3 | Pending |
| CON-04 | Phase 3 | Pending |
| CON-05 | Phase 3 | Pending |
| CON-06 | Phase 3 | Pending |

**Coverage:**
- v1 requirements: 21 total
- Mapped to phases: 21
- Unmapped: 0 ✓

---
*Requirements defined: 2026-03-13*
*Last updated: 2026-03-13 after initial definition*
