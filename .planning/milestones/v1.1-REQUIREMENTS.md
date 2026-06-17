# Requirements: Secrets & Parameters Sync

**Defined:** 2026-03-16
**Core Value:** Synchronisation generique de secrets SM et parametres SSM entre comptes AWS source et destination, avec transformations configurables.

## v1.1 Requirements

Requirements pour la synchronisation secrets/parametres. Chaque requirement mappe a une phase du roadmap.

### Sync Engine

- [x] **SYNC-01**: SFN SyncConfigItems unique traitant Secrets Manager et SSM Parameter Store via Choice state sur le type — un seul flow (fetch → transform → write) avec backend-specific API calls
- [x] **SYNC-02**: Fetch cross-account des valeurs source via IAM role assumption (sts:AssumeRole) avec support multi-region
- [x] **SYNC-03**: Path mapping configurable — renommage des chemins source vers destination (ex: /rubix/bene-prod/app/* → /digital/prd/app/mro-bene/*)
- [x] **SYNC-04**: Transformations de valeurs dans les secrets JSON — remplacement de valeurs par cle (regex ou literal) avec regles configurables dans l'input
- [x] **SYNC-05**: Creation automatique du secret/parametre cote destination si inexistant, mise a jour si existant avec valeur differente
- [x] **SYNC-06**: Merge mode — preserver les cles destination-only lors de la mise a jour d'un secret JSON (pas d'ecrasement des cles ajoutees cote destination)
- [x] **SYNC-07**: Recursive traversal pour SSM — copier tous les parametres sous un path donne avec mapping de path applique a chacun
- [x] **SYNC-08**: Lambda(s) generique(s) pour la logique fetch/transform/write — pas de logique metier Rubix-specifique hardcodee

### Integration Orchestrator

- [x] **ORCH-01**: Section ConfigSync optionnelle dans l'input JSON du refresh orchestrator — si absente ou Enabled=false, la sync est ignoree
- [x] **ORCH-02**: Orchestrateur appelle SyncConfigItems via startExecution.sync:2 quand ConfigSync.Enabled=true
- [x] **ORCH-03**: Phase d'execution configurable dans l'input (post-restore, pre-verify, etc.)

### Infrastructure

- [x] **INFRA-01**: Module Terraform pour la SFN SyncConfigItems + Lambda(s) dans modules/step-functions/ avec ARN exporte
- [x] **INFRA-02**: Tests ASL de validation pour la nouvelle SFN (auto-decouverte via rglob existant)

## v2 Requirements

Deferred a un milestone futur.

### Qualite Avancee

- **QAL-01**: Dry-run mode — simuler la sync sans ecrire, retourner un rapport de ce qui changerait
- **QAL-02**: Rollback capability — sauvegarder l'etat destination avant sync pour restauration
- **QAL-03**: Diff report post-sync — comparer source et destination apres la sync pour confirmer l'alignement
- **QAL-04**: Key filtering — inclure/exclure des cles specifiques dans les secrets JSON

## Out of Scope

| Feature | Reason |
|---------|--------|
| Sync bidirectionnel (destination → source) | Risque de corruption des secrets prod, one-way seulement |
| Rotation des secrets | Gere par AWS Secrets Manager nativement |
| Sync de secrets K8s / ExternalSecrets | Domaine EKS different, couvert par k8s-secrets-sync-checker dans le dashboard |
| Planification / scheduling | EventBridge existant gere le scheduling, pas dans la SFN |
| Notification SNS de la sync | Gere par l'orchestrateur parent (pattern existant) |

## Traceability

| Requirement | Phase | Status |
|-------------|-------|--------|
| SYNC-01 | Phase 4 | Complete |
| SYNC-02 | Phase 5 | Complete |
| SYNC-03 | Phase 5 | Complete |
| SYNC-04 | Phase 5 | Complete |
| SYNC-05 | Phase 5 | Complete |
| SYNC-06 | Phase 5 | Complete |
| SYNC-07 | Phase 5 | Complete |
| SYNC-08 | Phase 4 | Complete |
| ORCH-01 | Phase 6 | Complete |
| ORCH-02 | Phase 6 | Complete |
| ORCH-03 | Phase 6 | Complete |
| INFRA-01 | Phase 4 | Complete |
| INFRA-02 | Phase 5 | Complete |

**Coverage:**
- v1.1 requirements: 13 total
- Mapped to phases: 13
- Unmapped: 0

---
*Requirements defined: 2026-03-16*
*Last updated: 2026-03-16 after roadmap creation*
