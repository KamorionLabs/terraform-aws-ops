# Phase 4: Foundation - Context

**Gathered:** 2026-03-16
**Status:** Ready for planning

<domain>
## Phase Boundary

Creer le module Terraform sync/, la Lambda generique sync_config_items, et le squelette ASL SyncConfigItems avec Choice state SM/SSM. A la fin de cette phase, l'infrastructure est deployable et la SFN route correctement selon le type d'item, mais la logique fetch/transform/write est un stub.

</domain>

<decisions>
## Implementation Decisions

### Schema input ConfigSync
- Structure globale : `ConfigSync.Enabled`, `ConfigSync.SourceAccount`, `ConfigSync.DestinationAccount`, `ConfigSync.Items[]`
- Un seul SourceAccount + DestinationAccount par appel (pas par item)
- Wildcards supportes dans les paths source (`/rubix/bene-prod/app/*` matche tous les secrets sous ce path)
- La SFN liste les secrets/parametres matchants puis itere via Map state
- Destination path avec placeholder `{name}` pour le nom relatif du secret source
- Transforms : map cle → replace (`"host": {"replace": [{"from": "...", "to": "..."}]}`) + `"skip": true` pour ignorer certaines cles
- L'orchestrateur pre-remplit les Transforms avec les valeurs dynamiques (endpoints DB) avant d'appeler SyncConfigItems — Claude decide du mecanisme exact (pre-remplissage vs variables dynamiques)
- Mode standalone : meme format d'input, les from/to sont fournis explicitement

### Module Terraform
- Nouveau module `modules/step-functions/sync/` — self-contained comme audit/
- Structure : main.tf (SFN + IAM), variables.tf, outputs.tf, versions.tf, sync_config_items.asl.json
- Lambda deployee via le module lambda-code existant (S3), pas inline
- Lambda source dans `lambdas/sync-config-items/sync_config_items.py`
- Pas de layer necessaire — boto3 inclus dans le runtime Python Lambda
- Module wire dans root main.tf comme les autres modules step-functions

### Lambda scope
- Une seule Lambda `sync_config_items.py` fait fetch + transform + write en un seul appel par item
- La SFN orchestre : ValidateInput → CheckType (Choice SM/SSM) → ListItems → MapOverItems → SyncSingleItem (Lambda) → PrepareOutput
- Pas de logique metier Rubix-specifique hardcodee — tout est configurable via l'input
- La Lambda supporte les deux backends (SM et SSM) via le champ `Type` dans l'input de chaque item

### Claude's Discretion
- Nommage exact des states ASL dans le squelette SFN
- Structure IAM roles/policies pour cross-account SM/SSM access
- Schema exact des variables.tf du module sync
- Implementation du stub Lambda (structure input/output correcte, pas de logique fetch/write)
- Mecanisme de resolution dynamique des transforms (pre-remplissage par l'orchestrateur vs variables dans l'input)

</decisions>

<specifics>
## Specific Ideas

- Le module audit/ est la reference exacte pour la structure du nouveau module sync/ — meme pattern (SFN + Lambda + IAM + CloudWatch)
- La Lambda n'a pas de dependance externe (pas de layer, pas de pymysql) — juste boto3 natif
- Les Lambdas existantes fetch-secrets et compare-secrets-manager contiennent deja la logique de path mapping et de hash de valeurs — la Lambda sync peut s'en inspirer pour la partie fetch
- Le pattern cross-account STS est deja utilise partout dans le projet (Credentials dans les ASL, sts:AssumeRole dans les Lambdas)

</specifics>

<code_context>
## Existing Code Insights

### Reusable Assets
- `modules/step-functions/audit/main.tf` : pattern de reference pour nouveau module (SFN + Lambda + IAM inline)
- `modules/lambda-code/` : module existant pour deployer Lambdas via S3
- `lambdas/fetch-secrets/fetch_secrets.py` : logique fetch SM cross-account existante (reference pour la partie fetch)
- `lambdas/compare-secrets-manager/compare_secrets_manager.py` : logique path mapping existante (DEFAULT_PATH_MAPPING, map_source_to_destination_name)
- `tests/test_asl_validation.py` : auto-decouverte via rglob — couvrira le nouveau fichier ASL automatiquement

### Established Patterns
- Module step-functions : `local.step_functions` map → `for_each` → `aws_sfn_state_machine`
- Nommage : `{prefix}-sync-{name}` (kebab) ou `{prefix}-Sync-{Name}` (pascal) via `var.naming_convention`
- IAM : role Lambda + policy pour SM/SSM/STS access + role SFN (orchestrator_role_arn)
- CloudWatch : log group par module + log group Lambda
- `templatefile()` pour injection ARN Lambda dans l'ASL

### Integration Points
- `main.tf` (root) : ajouter `module "step_functions_sync"` avec memes variables que les autres modules
- `modules/lambda-code/main.tf` : ajouter le packaging de sync_config_items.py
- `outputs.tf` (root) : exporter l'ARN de SyncConfigItems pour l'orchestrateur (Phase 6)

</code_context>

<deferred>
## Deferred Ideas

None — discussion stayed within phase scope

</deferred>

---

*Phase: 04-foundation*
*Context gathered: 2026-03-16*
