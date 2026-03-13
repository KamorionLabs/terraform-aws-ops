# Roadmap: Step Functions Modularization

## Overview

Ce projet elimine la duplication dans les 44 fichiers ASL de l'orchestrateur de refresh en trois phases sequentielles. La Phase 1 cree les trois sous-SFN reutilisables (les briques de base) et repare le CI casse. La Phase 2 refactore les fichiers de domaine complexes pour consommer ces sous-SFN. La Phase 3 consolide les 6 paires public/private en fichiers uniques parametrises. A l'issue, chaque pattern ASL duplique n'existe qu'une seule fois.

## Phases

**Phase Numbering:**
- Integer phases (1, 2, 3): Planned milestone work
- Decimal phases (2.1, 2.2): Urgent insertions (marked with INSERTED)

Decimal phases appear between their surrounding integers in numeric order.

- [ ] **Phase 1: Extraction** - Creer les trois sous-SFN reutilisables et reparer le CI
- [ ] **Phase 2: Refactoring** - Remplacer la duplication inline par des appels aux sous-SFN
- [ ] **Phase 3: Consolidation** - Fusionner les 6 paires public/private en fichiers parametrises

## Phase Details

### Phase 1: Extraction
**Goal**: Les trois sous-SFN (ManageLambdaLifecycle, ManageAccessPoint, ManageFileSystemPolicy) sont deployees via Terraform, le CI passe en vert, et le pattern d'integration est valide pour servir de base aux refactorings suivants.
**Depends on**: Nothing (first phase)
**Requirements**: PRE-01, PRE-02, SUB-01, SUB-02, SUB-03, SUB-04, SUB-05, SUB-06, TST-01, TST-02
**Success Criteria** (what must be TRUE):
  1. Les trois fichiers ASL (manage_lambda_lifecycle, manage_access_point, manage_file_system_policy) existent dans modules/step-functions/efs/ avec leurs contrats Input/Output documentes et leurs Catch auto-contenus
  2. Les trois sous-SFN sont enregistrees via aws_sfn_state_machine dans efs/main.tf et leurs ARNs sont exportes dans efs/outputs.tf
  3. Le CI GitHub Actions passe pour tous les fichiers cross-account (strip_credentials dans conftest.py elimine les echecs SFN Local)
  4. Tous les blocs candidats ont ete audites pour les references $$.Execution.Input avant extraction (TST-02) et le pattern templatefile() est etabli pour l'injection ARN
  5. Les tests ASL (validation JSON + ValidateStateMachineDefinition) couvrent les trois nouvelles sous-SFN
**Plans**: TBD

### Phase 2: Refactoring
**Goal**: Les quatre fichiers de domaine complexes (check_replication_sync, setup_cross_account_replication, refresh_orchestrator, prepare_snapshot_for_restore) ont remplace leurs blocs dupliques inline par des appels aux sous-SFN de Phase 1, avec des interfaces externes inchangees.
**Depends on**: Phase 1
**Requirements**: REF-01, REF-02, REF-03, REF-04, REF-05
**Success Criteria** (what must be TRUE):
  1. check_replication_sync est reduit de 72 a ~35 states et appelle ManageLambdaLifecycle x2 et ManageAccessPoint via states:startExecution.sync:2 avec ResultSelector corrects
  2. setup_cross_account_replication est reduit de 53 a ~30 states et appelle ManageFileSystemPolicy x2 avec discriminateurs de nom d'execution
  3. prepare_snapshot_for_restore est reduit de 39 a ~18 states via extraction EnsureSnapshotAvailable
  4. refresh_orchestrator est reduit de 51 a ~30 states avec ClusterSwitchSequence extrait
  5. Toutes les interfaces Input/Output des SFN refactorees sont identiques pour les appelants existants (orchestrateur, CI, invocations manuelles)
**Plans**: TBD

### Phase 3: Consolidation
**Goal**: Les 6 paires de fichiers public/private sont remplacees par 6 fichiers uniques parametrises via Account.RoleArn optionnel, eliminant la divergence silencieuse entre variantes.
**Depends on**: Phase 2
**Requirements**: CON-01, CON-02, CON-03, CON-04, CON-05, CON-06
**Success Criteria** (what must be TRUE):
  1. Les 6 paires de fichiers ASL (manage_storage, scale_services, verify_and_restart_services, run_archive_job, run_mysqldump_on_eks, run_mysqlimport_on_eks) sont consolidees en 6 fichiers uniques avec un Choice state gatant le chemin Credentials
  2. Le champ Account.RoleArn est le seul commutateur public/private — absent signifie execution locale, present signifie cross-account via Credentials
  3. Les modules Terraform EKS, Utils et DB sont mis a jour (logique _suffix supprimee) et le CI matrix est mis a jour pour les fichiers renommes/supprimes
  4. L'orchestrateur de refresh ne reference plus les ARNs *_private (les anciens fichiers sont supprimes)
**Plans**: TBD

## Progress

**Execution Order:**
Phases execute in numeric order: 1 → 2 → 3

| Phase | Plans Complete | Status | Completed |
|-------|----------------|--------|-----------|
| 1. Extraction | 0/TBD | Not started | - |
| 2. Refactoring | 0/TBD | Not started | - |
| 3. Consolidation | 0/TBD | Not started | - |
