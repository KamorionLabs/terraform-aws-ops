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

### Active

- [ ] SFN generique pour synchroniser des secrets SM cross-account avec path mapping configurable
- [ ] SFN generique pour synchroniser des parametres SSM cross-account avec path mapping configurable
- [ ] Transformations de valeurs configurables dans les secrets JSON (remplacement endpoints, IPs, etc.)
- [ ] Creation automatique des secrets/parametres cote destination si inexistants
- [ ] Merge mode : preserver les cles destination-only lors de la sync
- [ ] Configuration via l'input JSON de l'orchestrateur (optionnel, activable par refresh)

### Out of Scope

- Tests unitaires des Lambda functions Python — projet separe, hors perimetre modularisation
- Ajout de monitoring/alerting — amelioration separee
- Refactor des Lambda functions (monolithes 700+ lignes) — dette technique separee
- Migration vers un templating ASL (Jinja, jsonnet) — trop de changement a la fois
- Rollback automatique depuis le parent — impossible par design AWS SFN
- Execution parallele sous-SFN — complexite concurrence, revisiter si latence mesuree

## Current Milestone: v1.1 Secrets & Parameters Sync

**Goal:** SFN generique pour copier/synchroniser des secrets SM et parametres SSM entre comptes AWS source et destination, avec transformations configurables.

**Target features:**
- Sync secrets Secrets Manager cross-account avec path mapping
- Sync parametres SSM cross-account avec path mapping
- Transformations de valeurs dans les secrets JSON
- Creation si inexistant, merge mode, key filtering
- Configuration via input JSON (optionnel dans le refresh orchestrator)

## Context

Shipped v1.0 — Step Functions Modularization complete.

- 44 fichiers ASL, 6 sous-SFN reutilisables
- Plus gros fichier : manage_storage.asl.json (31 states, unifie pub/priv)
- 0 paires public/private restantes (6 consolidees via EKS.AccessMode)
- 0 duplication de patterns (54 states dupliques elimines)
- 916+ tests ASL passent (auto-decouverte via rglob)
- Architecture Terraform : triple-map (EFS) et dual-map (DB) avec moved blocks
- Commutateur pub/priv : EKS.AccessMode dans l'input SFN (runtime, pas deploy-time)
- Lambdas compare-secrets-manager et compare-ssm existent deja (comparaison, pas sync)
- Patterns de mapping NBS→NH documentes (path mapping, value transformations, 6 instances)

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

---
*Last updated: 2026-03-16 after v1.1 milestone start*
