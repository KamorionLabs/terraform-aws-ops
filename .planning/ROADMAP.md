# Roadmap: Step Functions Modularization

## Milestones

- ✅ **v1.0 Step Functions Modularization** — Phases 1-3 (shipped 2026-03-16)
- ✅ **v1.1 Secrets & Parameters Sync** — Phases 4-6 (complete 2026-03-17, audit passed 13/13)
- **v1.2 S3 Cross-Account Replication** — Phases 7-9 (in progress)

## Phases

<details>
<summary>v1.0 Step Functions Modularization (Phases 1-3) — SHIPPED 2026-03-16</summary>

- [x] Phase 1: Extraction (3/3 plans) — completed 2026-03-13
- [x] Phase 2: Refactoring (3/3 plans) — completed 2026-03-13
- [x] Phase 3: Consolidation (3/3 plans) — completed 2026-03-16

See: `.planning/milestones/v1.0-ROADMAP.md` for full details

</details>

<details>
<summary>v1.1 Secrets & Parameters Sync (Phases 4-6) — COMPLETE 2026-03-17 (audit passed 13/13)</summary>

- [x] Phase 4: Foundation (2/2 plans) — Module Terraform, Lambda generique, SFN SyncConfigItems skeleton avec Choice state SM/SSM
- [x] Phase 5: Sync Engine (2/2 plans) — Flow fetch/transform/write avec path mapping, transformations, merge mode, tests ASL
- [x] Phase 6: Orchestrator Integration (1/1 plan) — Branchement ConfigSync dans refresh_orchestrator (section input optionnelle)

See: `.planning/milestones/v1.1-REQUIREMENTS.md` and `.planning/v1.1-MILESTONE-AUDIT.md`

</details>

### v1.2 S3 Cross-Account Replication

**Milestone Goal:** SFN generique pour configurer et piloter la replication S3 cross-account (live + backfill batch), en miroir du pattern EFS — module s3/, IAM source-account, integration orchestrateur optionnelle, spec + tests. Perimetre generique uniquement (wiring client hors scope).

- [x] **Phase 7: S3 Replication Module** - Module `modules/step-functions/s3/` (4 ops SFN, **aucun Lambda** — sync-status SDK natif) + rôle/perms S3 optionnels dans `modules/source-account/` (completed 2026-06-19)
- [x] **Phase 8: Orchestrator Integration** - Bloc input S3 optionnel pilotant une phase de replication optionnelle dans `refresh_orchestrator` (analogue EFS) (completed 2026-06-23)
- [x] **Phase 9: Spec & Tests** - `specs/repl-s3-sync.md` en miroir de `repl-efs-sync.md` + validation ASL (pas de tests Lambda — module sans Lambda) (completed 2026-07-01)

## Phase Details

### Phase 7: S3 Replication Module
**Goal**: Le module Terraform `modules/step-functions/s3/` deploie 4 SFN (setup/run_batch/check_batch/delete) operant la replication S3 cross-account avec assume-role imperatif, et `modules/source-account/` expose un rôle de replication S3 optionnel + perms source ; `terraform plan` passe
**Depends on**: Phase 6 (v1.1 complete)
**Requirements**: REPL-01, REPL-02, REPL-03, REPL-04, REPL-05, REPL-06, IAM-01, IAM-02
**Success Criteria** (what must be TRUE):
  1. La SFN `setup_cross_account_replication` appelle `aws-sdk:s3:putBucketReplication` sur le bucket source via assume-role imperatif (`Credentials.RoleArn.$`) — aucune ressource Terraform declarative ne gere la config de replication du bucket source
  2. Les SFN `run_batch_replication` (`s3control:createJob`) et `check_batch_replication` (`s3control:describeJob` en polling) backfillent les objets existants et suivent l'etat du job jusqu'a completion
  3. La SFN `delete_replication` retire la configuration de replication du bucket source
  4. Le fan-out hub-and-spoke (1 source -> N destinations, same-region) est supporte via la structure d'input (S3 = une ReplicationConfiguration a N Rules ; Map + GetBucketReplication/merge/PutBucketReplication par destination) ; le sync-status est lu via etats SDK natifs (`GetBucketReplication` + `s3control:describeJob`), sans Lambda
  5. `modules/source-account/` deploie un rôle de replication S3 optionnel (garde par variable) + perms source (`s3:PutBucketReplication`, `s3control:CreateJob/DescribeJob`, `iam:PassRole`) ; `terraform plan` passe sans erreur
**Plans**: 4 plans
  - [x] 07-01-PLAN.md — ASL setup + delete (read-merge-write replication config, validate-only versioning, Map fan-out) [REPL-01, REPL-04, REPL-05]
  - [x] 07-02-PLAN.md — ASL run_batch + check_batch (s3control createJob/describeJob, GeneratedManifest, poll loop) [REPL-02, REPL-03, REPL-05, REPL-06]
  - [x] 07-03-PLAN.md — source-account IAM (enable_s3 toggle, combined replication role, scoped PassRole) [IAM-01, IAM-02]
  - [x] 07-04-PLAN.md — s3 module Terraform skeleton (file()-based 4-SFN module, no Lambda, terraform validate) [REPL-01..06]

### Phase 8: Orchestrator Integration
**Goal**: `refresh_orchestrator` appelle la replication S3 de maniere optionnelle via un bloc input S3 (analogue EFS), avec activation configurable et no-op quand absent/desactive
**Depends on**: Phase 7
**Requirements**: ORCH-04, ORCH-05
**Success Criteria** (what must be TRUE):
  1. Un bloc input S3 optionnel (structure mirroir du bloc EFS : Source/Destination/Replication) pilote une phase de replication S3 dans l'orchestrateur
  2. Quand le bloc S3 est absent ou `S3.Enabled=false`, l'orchestrateur ignore completement la replication S3 et son comportement est strictement identique a avant (Choice state de garde)
  3. Quand active, l'orchestrateur invoque les SFN S3 (setup -> run_batch -> check_batch) via startExecution.sync:2 avec la configuration fournie
**Plans**: 1 plan
  - [x] 08-01-PLAN.md — Tisser la branche S3 self-guarded (CheckS3Enabled) dans Phase1DataRefresh + reshape vers contrats figes + module step_functions_s3 racine + regeneration snapshot [ORCH-04, ORCH-05]

### Phase 9: Spec & Tests
**Goal**: La documentation spec et la couverture de tests existent — `specs/repl-s3-sync.md` miroite `repl-efs-sync.md`, et la validation ASL couvre les nouvelles SFN (pas de tests Lambda — le module S3 ne contient aucun Lambda)
**Depends on**: Phase 8
**Requirements**: INFRA-03, INFRA-04
**Success Criteria** (what must be TRUE):
  1. `specs/repl-s3-sync.md` existe et miroite la structure de `repl-efs-sync.md` (objectif, architecture, inputs/outputs, appels AWS, logique metier, conditions succes/alerte/erreur, mapping comptes)
  2. La validation ASL passe pour les nouvelles SFN S3 (auto-decouverte via rglob existant)
**Plans**: 1 plan (estimation)

## Progress

| Phase | Milestone | Plans Complete | Status | Completed |
|-------|-----------|----------------|--------|-----------|
| 1. Extraction | v1.0 | 3/3 | Complete | 2026-03-13 |
| 2. Refactoring | v1.0 | 3/3 | Complete | 2026-03-13 |
| 3. Consolidation | v1.0 | 3/3 | Complete | 2026-03-16 |
| 4. Foundation | v1.1 | 2/2 | Complete | 2026-03-17 |
| 5. Sync Engine | v1.1 | 2/2 | Complete | 2026-03-17 |
| 6. Orchestrator Integration | v1.1 | 1/1 | Complete | 2026-03-17 |
| 7. S3 Replication Module | v1.2 | 4/4 | Complete   | 2026-06-19 |
| 8. Orchestrator Integration | v1.2 | 1/1 | Complete   | 2026-06-23 |
| 9. Spec & Tests | v1.2 | 1/1 | Complete   | 2026-07-01 |
