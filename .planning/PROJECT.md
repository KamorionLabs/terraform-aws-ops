# Step Functions Modularization

## What This Is

Refactorisation des Step Functions ASL du projet terraform-aws-ops pour eliminer la duplication, reduire la complexite des gros fichiers, et consolider les paires public/private. Le projet couvre 44 fichiers ASL totalisant ~550 states, dont ~54 sont des duplications pures.

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

### Active

- [ ] Sous-SFN ManageLambdaLifecycle reutilisable (elimine ~24 states dupliques)
- [ ] Sous-SFN ManageAccessPoint reutilisable (elimine ~12 states dupliques)
- [ ] Sous-SFN ManageFileSystemPolicy reutilisable (elimine ~18 states dupliques)
- [ ] Refactor check_replication_sync (72 → ~35 states)
- [ ] Refactor setup_cross_account_replication (53 → ~30 states)
- [ ] Refactor refresh_orchestrator (51 → ~30 states)
- [ ] Refactor prepare_snapshot_for_restore (39 → ~18 states)
- [ ] Consolidation des 6 paires public/private en 6 fichiers uniques parametrises
- [ ] Tests ASL de validation pour chaque nouvelle sous-SFN
- [ ] Fix des tests CI GitHub Actions casses par les champs Credentials cross-account

### Out of Scope

- Tests unitaires des Lambda functions Python — projet separe, hors perimetre modularisation
- Ajout de monitoring/alerting — amelioration separee
- Refactor des Lambda functions (monolithes 700+ lignes) — dette technique separee
- Migration vers un templating ASL (Jinja, jsonnet) — trop de changement a la fois

## Context

- 44 fichiers ASL, ~550 states cumules
- Plus gros fichier : check_replication_sync.asl.json (72 states)
- 6 paires de fichiers public/private quasi-identiques (seule difference : lookup EKS initial)
- ~54 states de duplication identifiee sur 3 patterns principaux (Lambda lifecycle, Access Point, FS Policy)
- Tests ASL existants dans `tests/` (validation JSON, structure, transitions) — passent en local mais CI cassee avec champs Credentials
- Structure Terraform : modules dans `modules/step-functions/` avec sous-modules par domaine
- Plan de modularisation detaille dans `docs/modularization-plan.md`

## Constraints

- **Structure Terraform**: Nouvelles sous-SFN dans `modules/step-functions/` (structure existante)
- **Backward compatibility**: Les interfaces d'input/output des SFN existantes ne doivent pas changer pour les appelants externes
- **AWS Step Functions**: Sous-SFN via `states:startExecution.sync:2` — latence +2-3s par appel, transitions facturees
- **Passage de contexte**: Sous-SFN ne partagent pas les variables Assign du parent — tout via Input/Output
- **Rollback**: Chaque sous-SFN doit gerer ses propres erreurs (Catch) — le parent ne peut pas annuler

## Key Decisions

| Decision | Rationale | Outcome |
|----------|-----------|---------|
| Extraire en sous-SFN plutot que templating | Reutilisation native AWS, testable independamment, pas de build step supplementaire | — Pending |
| Phases sequentielles (1→2→3) | Phase 2 depend des sous-SFN de Phase 1 | — Pending |
| Consolider pub/priv via champ Account.RoleArn optionnel | Elimine la divergence entre variantes, une seule source de verite | — Pending |
| Seuil extraction : >= 4 states OU duplique >= 2 fois | Evite la sur-modularisation et le cout latence des appels sous-SFN | — Pending |

---
*Last updated: 2026-03-13 after initialization*
