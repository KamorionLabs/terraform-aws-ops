# Phase 2: Refactoring - Context

**Gathered:** 2026-03-13
**Status:** Ready for planning

<domain>
## Phase Boundary

Remplacer la duplication inline par des appels aux sous-SFN dans les 4 fichiers de domaine complexes (check_replication_sync, setup_cross_account_replication, refresh_orchestrator, prepare_snapshot_for_restore + restore_cluster). Creer 3 nouvelles sous-SFN necessaires a ces refactorings. Les interfaces externes (Input/Output) doivent rester identiques pour tous les appelants existants.

</domain>

<decisions>
## Implementation Decisions

### Nouvelles sous-SFN
- 3 nouvelles sous-SFN a creer dans cette phase :
  - **CheckFlagFileSync** dans le module EFS — verifie les flag files EFS, sous-SFN reutilisable (meme pattern que Phase 1)
  - **ClusterSwitchSequence** dans le module DB — sequence rename old -> wait -> delete/tag old -> rename new -> wait -> verify. Waits inclus, autonome en un seul appel
  - **EnsureSnapshotAvailable** dans le module DB — verifie l'existence du snapshot RDS + wait until available. Pas de creation (geree en amont par l'orchestrateur)
- Toutes suivent le meme pattern que Phase 1 : meme map `local.step_functions`, `for_each`, `file()`, `Comment` ASL + README, wildcard IAM `{prefix}-*`

### Scope EnsureSnapshotAvailable
- Phase 2 cree EnsureSnapshotAvailable ET l'integre dans prepare_snapshot_for_restore ET restore_cluster
- Les deux fichiers appelants sont refactores dans le meme plan (module DB)

### Decoupage en plans
- 3 plans groupes par module, dans cet ordre :
  1. **Plan 02-01 : Module EFS** — Creer CheckFlagFileSync + refactorer check_replication_sync (ManageLambdaLifecycle x2, ManageAccessPoint x2, CheckFlagFileSync) + refactorer setup_cross_account_replication (ManageFileSystemPolicy x2) + migration templatefile() pour ces fichiers + tests
  2. **Plan 02-02 : Module DB** — Creer EnsureSnapshotAvailable + refactorer prepare_snapshot_for_restore + refactorer restore_cluster + migration templatefile() pour ces fichiers + tests
  3. **Plan 02-03 : Module Orchestrator** — Creer ClusterSwitchSequence + refactorer refresh_orchestrator + tests
- Ordre : EFS d'abord (plus gros gain, 72->35 states), puis DB, puis Orchestrator
- Audit explicite des refs `$$.Execution.Input` et SSM comme premiere tache du plan EFS (check_replication_sync a 3 refs + 2 SSM)

### Migration file() -> templatefile()
- Migrer uniquement les fichiers refactores vers `templatefile()` — les autres gardent `file()`
- Deux maps separees dans chaque module : `local.step_functions` (file(), inchangee) + `local.step_functions_templated` (templatefile(), nouvelle)
- Deux resources `aws_sfn_state_machine` separees (une par map) pour zero impact sur les SFN existantes
- Utiliser des `moved` blocks declaratifs pour le changement d'adresse Terraform (pas de `terraform state mv` manuel)

### Verification post-refactoring
- Tests structurels ASL automatises (CI) : JSON valide, states atteignables, Catch/Next valides, SFN Local ValidateStateMachine
- Review manuelle des paths critiques (happy path, error path) old vs new
- `terraform plan` pour confirmer in-place update (pas de destroy/recreate)
- Test de non-regression des interfaces (REF-05) : snapshots JSON de reference dans `tests/snapshots/` comparant les schemas Input/Output avant/apres refactoring
- Deploy dev hors scope des plans (validation reelle post-merge)

### Claude's Discretion
- Scope exact de chaque sous-SFN (nombre de states, noms internes)
- Strategie de passage de contexte pour les refs `$$.Execution.Input` dans check_replication_sync
- Nommage interne des states dans les fichiers refactores
- Implementation du test de non-regression des interfaces
- Structure exacte des moved blocks

</decisions>

<specifics>
## Specific Ideas

- Le pattern orchestrator `templatefile()` avec injection ARN (var.db_step_function_arns, var.efs_step_function_arns) est la reference pour l'implementation dans les modules EFS et DB
- L'audit `$$.Execution.Input` dans check_replication_sync est un blocker identifie dans STATE.md — doit etre traite en premiere tache avant tout refactoring
- EnsureSnapshotAvailable ne fait que wait — la creation du snapshot est geree en amont dans le flow orchestrateur (phase "prepare")
- ClusterSwitchSequence doit etre autonome : le parent fait un seul appel, la sous-SFN gere toute la sequence y compris les waits entre operations

</specifics>

<code_context>
## Existing Code Insights

### Reusable Assets
- `modules/step-functions/efs/manage_*.asl.json` : 3 sous-SFN Phase 1 (ManageLambdaLifecycle, ManageAccessPoint, ManageFileSystemPolicy) — consommees directement par les refactorings EFS
- `modules/step-functions/orchestrator/main.tf` : pattern `templatefile()` avec injection ARN via variables — reference pour la migration EFS/DB
- `tests/test_asl_validation.py` : auto-decouverte via `rglob("*.asl.json")` — couvre les nouvelles sous-SFN automatiquement
- `tests/conftest.py` : fixture `strip_credentials` operationnelle depuis Phase 1

### Established Patterns
- Map `local.step_functions` -> `for_each` -> `aws_sfn_state_machine` (tous les modules)
- `file()` pour les sous-SFN (pas de variables a injecter)
- `templatefile()` pour les appelants (injection ARN des sous-SFN)
- `states:startExecution.sync:2` pour appels synchrones de sous-SFN (refresh_orchestrator en fait deja 22)
- Catch auto-contenu par sous-SFN (pattern Phase 1)

### Integration Points
- `modules/step-functions/efs/main.tf` : ajouter map `step_functions_templated` + resource `efs_templated` + moved blocks
- `modules/step-functions/db/main.tf` : meme pattern — map + resource + moved blocks
- `modules/step-functions/efs/outputs.tf` : pas de changement d'ARN necessaire (moved block gere la transition)
- `modules/step-functions/db/outputs.tf` : exporter ARN de EnsureSnapshotAvailable pour orchestrator si necessaire
- `tests/snapshots/` : nouveau repertoire pour les fichiers de reference des interfaces

</code_context>

<deferred>
## Deferred Ideas

None — discussion stayed within phase scope

</deferred>

---

*Phase: 02-refactoring*
*Context gathered: 2026-03-13*
