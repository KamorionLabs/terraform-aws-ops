# Step Functions Modularization

## What This Is

Orchestrateur de refresh cross-account AWS utilisant Step Functions, avec des sous-SFN reutilisables, des fichiers ASL consolides (public/private unifies), et un CI complet. Le projet couvre 44 fichiers ASL modulaires avec 6 sous-SFN reutilisables et 0 duplication de patterns.

## Core Value

Chaque pattern ASL duplique n'existe qu'une seule fois, dans une sous-SFN reutilisable testee independamment.

## Requirements

### Validated

- ✓ Orchestrateur de refresh 5 phases (validate → prepare → backup → restore → verify) — existing
- ✓ Operations cross-account via IAM role assumption — existing
- ✓ Modules Step Functions par domaine (DB, EFS, EKS, Utils) — existing
- ✓ Lambda functions pour operations specialisees (~45 fonctions) — existing
- ✓ Tests ASL de validation (JSON syntax, required fields, state transitions) — existing
- ✓ CI/CD GitHub Actions (Terraform validation, security scanning) — existing
- ✓ Checkers de readiness et comparateurs source/destination — existing
- ✓ Sous-SFN ManageLambdaLifecycle reutilisable (elimine ~24 states dupliques) — v1.0
- ✓ Sous-SFN ManageAccessPoint reutilisable (elimine ~12 states dupliques) — v1.0
- ✓ Sous-SFN ManageFileSystemPolicy reutilisable (elimine ~18 states dupliques) — v1.0
- ✓ Sous-SFN CheckFlagFileSync pour verification flag files EFS — v1.0
- ✓ Sous-SFN EnsureSnapshotAvailable pour attente snapshots RDS — v1.0
- ✓ Sous-SFN ClusterSwitchSequence pour sequence rename/delete/tag clusters — v1.0
- ✓ Refactor check_replication_sync (72 → 27 states) — v1.0
- ✓ Refactor setup_cross_account_replication (53 → 45 states) — v1.0
- ✓ Refactor refresh_orchestrator (51 → 42 states) — v1.0
- ✓ Refactor prepare_snapshot_for_restore (39 → 33 states) — v1.0
- ✓ Consolidation des 6 paires public/private en 6 fichiers uniques via EKS.AccessMode — v1.0
- ✓ Tests ASL de validation pour chaque nouvelle sous-SFN — v1.0
- ✓ Tests de non-regression des interfaces (snapshots JSON) — v1.0
- ✓ Fix CI GitHub Actions (strip_credentials, matrix EFS/DB) — v1.0
- ✓ SFN SyncConfigItems unique (SM + SSM via Choice state, fetch/transform/write) — v1.1
- ✓ Fetch cross-account multi-region via sts:AssumeRole + path mapping configurable — v1.1
- ✓ Transformations de valeurs dans les secrets JSON (regex/literal par cle) — v1.1
- ✓ Creation si inexistant + merge mode (preserve cles destination-only) + recursive SSM — v1.1
- ✓ Lambda generique fetch/transform/write (pas de logique Rubix hardcodee) — v1.1
- ✓ Integration orchestrateur via section ConfigSync optionnelle, phase configurable — v1.1

### Active

- [ ] SFN generique pour configurer la replication S3 cross-account (s3:PutBucketReplication, assume-role imperatif)
- [ ] SFN pour backfill des objets existants via S3 Batch Operations (s3control:CreateJob/DescribeJob)
- [ ] SFN delete_replication pour teardown de la config de replication
- [ ] Fan-out hub-and-spoke 1 source -> N destinations (same-region)
- [ ] Role de replication S3 optionnel + perms source dans modules/source-account/
- [ ] Integration orchestrateur via bloc input S3 optionnel (analogue EFS)
- [ ] Spec repl-s3-sync.md + validation ASL + tests unitaires

### Out of Scope

- Tests unitaires des Lambda functions Python — projet separe, hors perimetre modularisation
- Ajout de monitoring/alerting — amelioration separee
- Refactor des Lambda functions (monolithes 700+ lignes) — dette technique separee
- Migration vers un templating ASL (Jinja, jsonnet) — trop de changement a la fois
- Rollback automatique depuis le parent — impossible par design AWS SFN
- Execution parallele sous-SFN — complexite concurrence, revisiter si latence mesuree

## Current Milestone: v1.2 S3 Cross-Account Replication

**Goal:** SFN generique pour configurer et piloter la replication S3 cross-account (live + backfill batch), en miroir du pattern EFS — module s3/, IAM source-account, integration orchestrateur optionnelle, spec + tests. Perimetre generique uniquement (wiring client hors scope).

**Target features:**
- SFN setup_cross_account_replication (s3:PutBucketReplication, assume-role imperatif — bucket source owned par stack externe)
- SFN run_batch_replication + check_batch_replication (backfill via S3 Batch Operations)
- SFN delete_replication (teardown)
- Fan-out hub-and-spoke 1 source -> N destinations, same-region (eu-central-1)
- Role de replication S3 optionnel + perms source (analogue EFS) dans modules/source-account/
- Bloc input S3 optionnel pilotant une phase optionnelle dans refresh_orchestrator
- specs/repl-s3-sync.md + validation ASL + tests unitaires Lambda compare sync-status

## Context

Shipped v1.0 (Modularization) + v1.1 (Secrets & Parameters Sync — audit passed 13/13).

- 44+ fichiers ASL, 6 sous-SFN reutilisables + SFN SyncConfigItems (SM/SSM)
- 916+ tests ASL passent (auto-decouverte via rglob)
- Architecture Terraform : triple-map (EFS) et dual-map (DB) avec moved blocks
- Pattern EFS de reference pour v1.2 : assume-role imperatif Credentials.RoleArn.$ (efs/setup_cross_account_replication.asl.json), role de replication optionnel + perms source (modules/source-account/, gardes par var.enable_efs), bloc input EFS optionnel + phase conditionnelle (orchestrator CheckEFSReplicationMode), spec specs/repl-efs-sync.md
- Module sync/ (v1.1) = analog le plus recent pour la structure Terraform d'un nouveau module step-functions (main.tf inline archive_file Lambda, outputs ARN)
- S3 live replication ne cascade pas les replicas : backfill des objets existants = S3 Batch Operations job
- Rubix target topology (wiring client ulterieur, hors scope ici) : s3-dig-prd-pim-media (366483377530) -> s3-dig-ppd-pim-media (287223952330) + s3-dig-stg-pim-media (281127105461)

## Constraints

- **Structure Terraform**: Sous-SFN dans `modules/step-functions/` (structure existante)
- **Backward compatibility**: Interfaces d'input/output des SFN inchangees pour les appelants externes
- **AWS Step Functions**: Sous-SFN via `states:startExecution.sync:2` — latence +2-3s par appel
- **Passage de contexte**: Sous-SFN ne partagent pas les variables Assign du parent — tout via Input/Output
- **Rollback**: Chaque sous-SFN gere ses propres erreurs (Catch) — le parent ne peut pas annuler

## Key Decisions

| Decision | Rationale | Outcome |
|----------|-----------|---------|
| Extraire en sous-SFN plutot que templating | Reutilisation native AWS, testable independamment, pas de build step supplementaire | ✓ Good — 6 sous-SFN deployees, testees, reutilisees |
| Phases sequentielles (1→2→3) | Phase 2 depend des sous-SFN de Phase 1 | ✓ Good — execution fluide, pas de blocages |
| Consolider pub/priv via EKS.AccessMode (pas Account.RoleArn) | Pub/priv = exposition API K8s, pas cross-account. Choice state route selon le mode | ✓ Good — corrige le commutateur original |
| Seuil extraction : >= 4 states OU duplique >= 2 fois | Evite la sur-modularisation et le cout latence des appels sous-SFN | ✓ Good — pas de sur-extraction |
| Dual-map Terraform (file + templatefile) avec moved blocks | Zero impact sur les SFN existantes, migration declarative | ✓ Good — pas de destroy/recreate |
| Three-tier Terraform pour EFS (avoid circular ARN refs) | efs → efs_sub_templated → efs_templated evite les references circulaires | ✓ Good — pattern propre |
| $$.Execution.Input materialise via States.ArrayGetItem | Elimine SSM intermediary states, data inline dans InitializeState | ✓ Good — 3 refs resolues proprement |
| Tests interface snapshots dans tests/snapshots/ | Non-regression automatisee des contrats Input/Output (REF-05) | ✓ Good — empeche les regressions silencieuses |

## Evolution

This document evolves at phase transitions and milestone boundaries.

**After each phase transition** (via `/gsd-transition`):
1. Requirements invalidated? → Move to Out of Scope with reason
2. Requirements validated? → Move to Validated with phase reference
3. New requirements emerged? → Add to Active
4. Decisions to log? → Add to Key Decisions
5. "What This Is" still accurate? → Update if drifted

**After each milestone** (via `/gsd:complete-milestone`):
1. Full review of all sections
2. Core Value check — still the right priority?
3. Audit Out of Scope — reasons still valid?
4. Update Context with current state

---
*Last updated: 2026-06-17 after v1.2 milestone start*
