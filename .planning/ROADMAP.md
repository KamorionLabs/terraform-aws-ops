# Roadmap: Step Functions Modularization

## Milestones

- ✅ **v1.0 Step Functions Modularization** — Phases 1-3 (shipped 2026-03-16)
- **v1.1 Secrets & Parameters Sync** — Phases 4-6 (in progress)

## Phases

<details>
<summary>v1.0 Step Functions Modularization (Phases 1-3) — SHIPPED 2026-03-16</summary>

- [x] Phase 1: Extraction (3/3 plans) — completed 2026-03-13
- [x] Phase 2: Refactoring (3/3 plans) — completed 2026-03-13
- [x] Phase 3: Consolidation (3/3 plans) — completed 2026-03-16

See: `.planning/milestones/v1.0-ROADMAP.md` for full details

</details>

### v1.1 Secrets & Parameters Sync

**Milestone Goal:** SFN generique pour copier/synchroniser des secrets SM et parametres SSM entre comptes AWS source et destination, avec transformations configurables.

- [ ] **Phase 4: Foundation** - Module Terraform, Lambda generique, et SFN SyncConfigItems skeleton avec Choice state SM/SSM
- [ ] **Phase 5: Sync Engine** - Flow complet fetch/transform/write avec path mapping, transformations, merge mode, et tests ASL
- [ ] **Phase 6: Orchestrator Integration** - Branchement ConfigSync dans refresh_orchestrator avec section input optionnelle

## Phase Details

### Phase 4: Foundation
**Goal**: L'infrastructure Terraform et le squelette SFN existent -- la Lambda generique est deployable et la SFN SyncConfigItems route correctement vers SM ou SSM via Choice state
**Depends on**: Phase 3 (v1.0 complete)
**Requirements**: SYNC-01, SYNC-08, INFRA-01
**Success Criteria** (what must be TRUE):
  1. Le module Terraform dans modules/step-functions/ deploie la SFN SyncConfigItems et la Lambda generique, avec ARN exporte en output
  2. La SFN contient un Choice state qui route vers le branch Secrets Manager ou SSM Parameter Store selon le type d'item en input
  3. La Lambda generique accepte un input structure (type, source, destination, credentials) et retourne un output structure -- sans logique metier Rubix-specifique hardcodee
  4. `terraform plan` passe sans erreur avec le nouveau module integre
**Plans**: 2 plans

Plans:
- [ ] 04-01-PLAN.md — Lambda stub sync_config_items + tests unitaires + ASL SyncConfigItems avec Choice state
- [ ] 04-02-PLAN.md — Module Terraform sync/ + wiring root main.tf et outputs.tf

### Phase 5: Sync Engine
**Goal**: Le flow complet fetch cross-account, transformation de valeurs, et ecriture destination fonctionne pour SM et SSM -- avec path mapping, merge mode, creation automatique, et recursive traversal
**Depends on**: Phase 4
**Requirements**: SYNC-02, SYNC-03, SYNC-04, SYNC-05, SYNC-06, SYNC-07, INFRA-02
**Success Criteria** (what must be TRUE):
  1. La Lambda fetch les secrets/parametres depuis le compte source via sts:AssumeRole cross-account, avec support multi-region
  2. Les chemins source sont renommes vers les chemins destination selon le path mapping configure dans l'input (ex: /rubix/bene-prod/* vers /digital/prd/*)
  3. Les valeurs JSON des secrets sont transformees selon les regles configurees (remplacement regex ou literal par cle) et le merge mode preserve les cles destination-only
  4. Les secrets/parametres sont crees cote destination si inexistants, mis a jour si la valeur differe, et le recursive traversal copie tous les parametres sous un path SSM donne
  5. Les tests ASL de validation passent pour la SFN SyncConfigItems (auto-decouverte via rglob existant)
**Plans**: TBD

Plans:
- [ ] 05-01: TBD
- [ ] 05-02: TBD

### Phase 6: Orchestrator Integration
**Goal**: L'orchestrateur de refresh appelle SyncConfigItems de maniere optionnelle via une section ConfigSync dans l'input JSON, a la phase d'execution configurable
**Depends on**: Phase 5
**Requirements**: ORCH-01, ORCH-02, ORCH-03
**Success Criteria** (what must be TRUE):
  1. Quand ConfigSync est absent ou Enabled=false dans l'input JSON, le refresh_orchestrator ignore completement la sync et son comportement est identique a avant
  2. Quand ConfigSync.Enabled=true, l'orchestrateur appelle SyncConfigItems via startExecution.sync:2 avec la configuration fournie
  3. La phase d'execution de la sync est configurable dans l'input (post-restore, pre-verify, etc.) -- pas hardcodee a un point fixe du flow
**Plans**: TBD

Plans:
- [ ] 06-01: TBD

## Progress

| Phase | Milestone | Plans Complete | Status | Completed |
|-------|-----------|----------------|--------|-----------|
| 1. Extraction | v1.0 | 3/3 | Complete | 2026-03-13 |
| 2. Refactoring | v1.0 | 3/3 | Complete | 2026-03-13 |
| 3. Consolidation | v1.0 | 3/3 | Complete | 2026-03-16 |
| 4. Foundation | v1.1 | 0/2 | Planning | - |
| 5. Sync Engine | v1.1 | 0/? | Not started | - |
| 6. Orchestrator Integration | v1.1 | 0/? | Not started | - |
