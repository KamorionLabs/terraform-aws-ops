# Phase 5: Sync Engine - Context

**Gathered:** 2026-03-17
**Status:** Ready for planning

<domain>
## Phase Boundary

Implementer la logique reelle dans la Lambda sync_config_items : fetch cross-account, path mapping, value transforms, merge mode, creation auto, recursive traversal SSM. A la fin de cette phase, la SFN SyncConfigItems est fonctionnelle end-to-end pour les deux backends (SM et SSM).

</domain>

<decisions>
## Implementation Decisions

### Comportement merge/conflict
- **MergeMode=true** : destination gagne pour les cles communes non couvertes par les Transforms. La source n'ecrase que les cles avec un Transform explicite. Les cles destination-only sont preservees.
- **MergeMode=false (ou absent)** : ecrasement total du secret destination par la valeur source (apres Transforms). Les cles destination-only disparaissent.
- **Secrets non-JSON** (valeur string simple) : les Transforms (replace from/to) s'appliquent sur la valeur brute. En MergeMode=true : si le secret destination existe, garder la valeur destination ; si inexistant, copier la source (avec Transforms appliques).

### Gestion des erreurs
- **Continue + rapport** : la SFN continue de sync les autres items quand un item echoue. Les erreurs sont collectees et retournees dans le resultat final avec status 'error' par item.
- Output SFN : Status global "complete" (tout ok), "partial" (certains echecs), "failed" (tous en erreur). Avec compteurs ItemsProcessed/ItemsSynced/ItemsFailed + Results[] detaille.

### Listing/matching wildcards
- **Glob avec ** support** : support de patterns glob (*, **) pour matcher les secrets/params source. Pas juste un prefix filter simple.
- **{name} placeholder** : le {name} dans le path destination est la partie du path source apres le prefix avant le wildcard. Simple et intuitif.
- Exemple : source `/rubix/bene-prod/app/*`, match `/rubix/bene-prod/app/hybris/config-keys`, {name} = `hybris/config-keys`, destination `/digital/prd/app/mro-bene/hybris/config-keys`.

### Claude's Discretion
- Implementation du glob matching (fnmatch Python, ou custom)
- Pattern STS AssumeRole (copier de fetch_secrets.py existant)
- Structure interne de la Lambda (fonctions helper, separation fetch/transform/write)
- Gestion du retry sur les API calls AWS (exponential backoff via botocore)
- Tests unitaires : scope et couverture

</decisions>

<specifics>
## Specific Ideas

- La Lambda fetch_secrets.py contient deja `get_cross_account_client()` avec STS AssumeRole — copier ce pattern exactement pour la Lambda sync
- Les Transforms s'appliquent a la fois sur les secrets JSON (par cle) ET sur les valeurs string simples (replace sur la valeur brute)
- Le `skip: true` dans les Transforms signifie "ne pas toucher cette cle lors du sync" — utile pour les passwords geres par rotation
- Pour SSM recursive : `get_parameters_by_path(Recursive=True)` fait deja le travail cote API, le path mapping s'applique sur chaque parametre retourne

</specifics>

<code_context>
## Existing Code Insights

### Reusable Assets
- `lambdas/fetch-secrets/fetch_secrets.py` : `get_cross_account_client()` — pattern STS exact a copier
- `lambdas/compare-secrets-manager/compare_secrets_manager.py` : `map_source_to_destination_name()` — logique path mapping existante (reference)
- `lambdas/sync-config-items/sync_config_items.py` : stub Phase 4 avec contrat I/O correct
- `modules/step-functions/sync/sync_config_items.asl.json` : ASL avec Map iterator + CheckType Choice

### Established Patterns
- `get_cross_account_client(service, role_arn, region, session_name)` → boto3 client cross-account
- Logging : `logger = logging.getLogger(__name__)` avec `LOG_LEVEL` env var
- Error handling : try/except ClientError avec logging, structured response
- Type hints sur toutes les fonctions publiques

### Integration Points
- `modules/step-functions/sync/sync_config_items.asl.json` : la SFN passe l'input a la Lambda via Map state — le contrat I/O est fixe depuis Phase 4
- `tests/test_sync_config_items.py` : 6 tests existants a etendre avec les nouveaux comportements
- `modules/step-functions/sync/main.tf` : IAM policy Lambda (sts:AssumeRole) — deja configure

</code_context>

<deferred>
## Deferred Ideas

None — discussion stayed within phase scope

</deferred>

---

*Phase: 05-sync-engine*
*Context gathered: 2026-03-17*
