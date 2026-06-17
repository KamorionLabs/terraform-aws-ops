# Requirements: S3 Cross-Account Replication

**Defined:** 2026-06-17
**Core Value:** SFN generique pour configurer et piloter la replication S3 cross-account (live + backfill batch) en miroir du pattern EFS, perimetre generique uniquement.

## v1.2 Requirements

Requirements pour la replication S3 cross-account. Chaque requirement mappe a une phase du roadmap. Perimetre **module generique uniquement** — le wiring client et les grants cote destination sont explicitement hors scope (voir Out of Scope).

### Replication SFN

- [ ] **REPL-01**: SFN `setup_cross_account_replication` dans `modules/step-functions/s3/` configure la replication via `s3:PutBucketReplication` sur le bucket source — assume-role **imperatif** au runtime (`Credentials.RoleArn.$`, pattern EFS) car le bucket source appartient a un stack externe et du Terraform declaratif entrerait en conflit
- [ ] **REPL-02**: SFN `run_batch_replication` declenche un backfill des objets existants via `s3control:CreateJob` (S3 Batch Operations) — la live replication ne forwarde que les nouvelles ecritures, le backfill couvre l'existant
- [ ] **REPL-03**: SFN `check_batch_replication` poll l'etat du job via `s3control:DescribeJob` jusqu'a completion (Active/Complete/Failed)
- [ ] **REPL-04**: SFN `delete_replication` supprime la configuration de replication du bucket source (teardown)
- [ ] **REPL-05**: Fan-out hub-and-spoke — une source replique vers N destinations, same-region (eu-central-1) ; chaque destination configurable independamment
- [ ] **REPL-06**: Privilegier les integrations SDK natives `aws-sdk:s3:*` / `aws-sdk:s3control:*` dans les ASL ; Lambda uniquement pour le compare sync-status (analogue `process-efs-replication`)

### Source-Account IAM

- [ ] **IAM-01**: Rôle de replication S3 optionnel dans `modules/source-account/` (analogue `efs-replication-role`, garde par une variable `enable_s3` ou equivalent), assumable par le service S3 / `batchoperations.s3.amazonaws.com`
- [ ] **IAM-02**: Permissions sur le rôle source assume par l'orchestrateur — `s3:PutBucketReplication`, `s3control:CreateJob`, `s3control:DescribeJob`, et `iam:PassRole` vers le rôle de replication S3 (condition `iam:PassedToService`)

### Integration Orchestrator

- [ ] **ORCH-04**: Bloc input S3 optionnel dans `refresh_orchestrator` (analogue bloc EFS) pilotant une phase de replication S3 optionnelle ; structure d'input mirroir de `EFS` (Source/Destination/Replication)
- [ ] **ORCH-05**: Quand le bloc S3 est absent ou desactive (`S3.Enabled=false`), l'orchestrateur ignore completement la replication S3 et son comportement est strictement identique a avant

### Infrastructure & Spec

- [ ] **INFRA-03**: `specs/repl-s3-sync.md` en miroir de `specs/repl-efs-sync.md` (objectif, architecture, inputs/outputs, appels AWS, logique metier, conditions de succes/alerte/erreur, mapping comptes)
- [ ] **INFRA-04**: Validation ASL pour les nouvelles SFN S3 (auto-decouverte via rglob existant) + tests unitaires pour le Lambda compare sync-status

## v2 Requirements

Deferred a un milestone futur.

- **S3REPL-DR-01**: Monitoring/alerting de l'etat de replication (lag, objets en echec) cote dashboard Dashborion
- **S3REPL-DR-02**: Compare source/destination post-replication (inventaire S3, comptage objets/tailles) — analogue compare EFS
- **S3REPL-DR-03**: Support cross-region (proxy lambda) si un besoin DR multi-region emerge

## Out of Scope

| Feature | Reason |
|---------|--------|
| Grants cote destination (bucket policy + KMS key policy autorisant le rôle de replication source) | Vivent dans le stack client `NewHorizon-IaC-Webshop`, hors perimetre du module generique |
| Wiring client (`NewHorizon-IaC-AWS-Refresh`, rôle dans `sharedservices/refresh`, inputs orchestrateur reels) | Specifique au client Rubix, hors perimetre opensource generique |
| Cascade des replicas (replication d'un replica vers un 3e bucket) | S3 live replication ne cascade pas par design ; le backfill batch couvre l'existant, pas une feature manquante |
| Cross-region replication | Same-region (eu-central-1) uniquement pour ce milestone ; cross-region deferre a v2 si besoin DR |
| Rollback automatique de la replication | `delete_replication` couvre le teardown ; pas de rollback transactionnel (limite AWS SFN) |

## Traceability

| Requirement | Phase | Status |
|-------------|-------|--------|
| REPL-01 | Phase 7 | Planning |
| REPL-02 | Phase 7 | Planning |
| REPL-03 | Phase 7 | Planning |
| REPL-04 | Phase 7 | Planning |
| REPL-05 | Phase 7 | Planning |
| REPL-06 | Phase 7 | Planning |
| IAM-01 | Phase 7 | Planning |
| IAM-02 | Phase 7 | Planning |
| ORCH-04 | Phase 8 | Planning |
| ORCH-05 | Phase 8 | Planning |
| INFRA-03 | Phase 9 | Planning |
| INFRA-04 | Phase 9 | Planning |

**Coverage:**
- v1.2 requirements: 12 total
- Mapped to phases: 12
- Unmapped: 0

---
*Requirements defined: 2026-06-17*
*Last updated: 2026-06-17 after roadmap creation*
