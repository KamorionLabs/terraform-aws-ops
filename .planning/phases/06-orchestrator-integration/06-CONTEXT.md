# Phase 6: Orchestrator Integration - Context

**Gathered:** 2026-03-17
**Status:** Ready for planning

<domain>
## Phase Boundary

Brancher SyncConfigItems dans refresh_orchestrator de maniere optionnelle. Ajouter un CheckConfigSyncOption Choice state apres RotateDatabaseSecrets, assembler l'input depuis le contexte global, et preserver le resultat pour la notification finale. La SFN SyncConfigItems est deja fonctionnelle (Phase 4+5).

</domain>

<decisions>
## Implementation Decisions

### Position dans le flow
- Position fixe : apres RotateDatabaseSecrets, avant Phase4PostSwitchEKS (CreateEKSStorage)
- Pas de champ ConfigSync.Phase — la position n'est pas configurable
- Logique : la rotation a mis a jour les credentials DB, la sync copie les bonnes valeurs, les services EKS ne sont pas encore up
- Le ORCH-03 est satisfait par le fait que ConfigSync est optionnel (Enabled=true/false) — la "configurabilite" c'est l'activation, pas la position

### Construction de l'input SyncConfigItems
- L'orchestrateur assemble l'input depuis le contexte global : $.SourceAccount, $.DestinationAccount, $.ConfigSync.Items
- Le caller du refresh fournit ConfigSync.Items avec les Transforms (pas de duplication des comptes dans ConfigSync)
- **Placeholders dynamiques** : la Lambda sync resout les `${...}` dans les Transforms depuis un champ `Context` dans l'input
- L'orchestrateur passe un champ `Context` avec les valeurs connues (endpoints DB, cluster names) en plus des Items
- Le Context est assemble par l'orchestrateur depuis $.Database, $.EKS, etc. — le caller ne fournit pas le Context, l'orchestrateur le construit

### Gestion du resultat
- **Continue + log warning** : un echec partiel de la sync ne bloque pas le refresh
- Le resultat SyncConfigItems est preserve dans le state (ResultPath) et inclus dans la notification SNS finale
- Le refresh continue normalement meme si Status='partial' ou 'failed'

### Claude's Discretion
- Noms exacts des states ASL (CheckConfigSyncOption, ExecuteSyncConfigItems, etc.)
- Structure exacte du champ Context assemble par l'orchestrateur
- ResultPath pour preserver le resultat sync sans ecraser le state global
- Format du sync result dans la notification SNS

</decisions>

<specifics>
## Specific Ideas

- Le pattern existant pour les etapes optionnelles est : CheckXOption (Choice) → si present, ExecuteX (Task) → suite du flow. Voir CheckRotateSecretsOption, CheckArchiveJobOption, CheckRunSqlScriptsOption — tous suivent ce pattern.
- L'orchestrateur utilise deja `templatefile()` avec injection d'ARN — le SyncConfigItems ARN sera injecte de la meme maniere via `var.sync_step_function_arns` ou un equivalent.
- Le resultat de sync doit etre dans un ResultPath qui ne clobber pas le state global (ex: `$.SyncResult`) — meme pattern que les autres Task results.

</specifics>

<code_context>
## Existing Code Insights

### Reusable Assets
- `modules/step-functions/orchestrator/refresh_orchestrator.asl.json` : 42 states, pattern CheckXOption Choice state pour etapes optionnelles
- `modules/step-functions/orchestrator/main.tf` : templatefile() avec db/efs/eks/utils ARN maps
- `modules/step-functions/sync/outputs.tf` : ARN de SyncConfigItems exportee

### Established Patterns
- Choice state optionnel : `$.ConfigSync.Enabled == true` → ExecuteSyncConfigItems, Default → skip
- Task state avec startExecution.sync:2 : tous les appels nested SFN suivent ce pattern
- ResultPath : `$.SyncResult` pour preserver sans ecraser
- Credentials cross-account dans l'Input de la nested SFN

### Integration Points
- `modules/step-functions/orchestrator/main.tf` : ajouter `sync_step_function_arns` dans les templatefile vars
- `modules/step-functions/orchestrator/variables.tf` : ajouter variable pour les ARN sync
- `main.tf` (root) : passer `module.step_functions_sync.step_function_arns` au module orchestrator
- `refresh_orchestrator.asl.json` : inserer CheckConfigSyncOption + ExecuteSyncConfigItems apres RotateDatabaseSecrets

</code_context>

<deferred>
## Deferred Ideas

- Position configurable de la sync dans le flow (ConfigSync.Phase) — ajouter si le besoin emerge
- Support de la resolution de placeholders Context depuis SSM/Secrets Manager (pas juste depuis l'input) — v2

</deferred>

---

*Phase: 06-orchestrator-integration*
*Context gathered: 2026-03-17*
