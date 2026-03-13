# Phase 1: Extraction - Context

**Gathered:** 2026-03-13
**Status:** Ready for planning

<domain>
## Phase Boundary

Creer les trois sous-SFN reutilisables (ManageLambdaLifecycle, ManageAccessPoint, ManageFileSystemPolicy), reparer le CI GitHub Actions casse par les champs Credentials, et etablir le pattern templatefile() pour l'injection ARN. Toutes les sous-SFN sont dans le domaine EFS.

</domain>

<decisions>
## Implementation Decisions

### Placement Terraform
- Sous-SFN dans la meme map `local.step_functions` et meme resource `aws_sfn_state_machine.efs` (for_each existant)
- Pas de map separee ni de sous-module dedie
- Meme pattern de nommage que les SFN principales : `{prefix}-efs-{name}` (ex: `refresh-efs-manage-lambda-lifecycle`)
- Le wildcard IAM `{prefix}-*` couvre automatiquement les nouvelles sous-SFN

### Contrats Input/Output
- Double documentation : champ `Comment` dans le JSON ASL pour le quick-ref + README.md dans `modules/step-functions/efs/` pour le detail
- README centralise pour toutes les sous-SFN du module EFS : schemas Input/Output, exemples d'appel, comportement Catch

### CI Fix
- Strip Credentials dans `conftest.py` (helper qui nettoie les definitions avant enregistrement dans SFN Local)
- Mettre a jour le matrix GitHub Actions pour couvrir les modules EFS manquants
- Scope : fix complet (pas juste le minimal)

### Ordre d'extraction
- Claude's Discretion : choisir l'ordre optimal pour les 3 sous-SFN en fonction des dependances et du risque

### Claude's Discretion
- Ordre interne des 3 extractions (ManageLambdaLifecycle, ManageAccessPoint, ManageFileSystemPolicy)
- Implementation du helper strip_credentials dans conftest.py
- Structure exacte du README.md des sous-SFN

</decisions>

<specifics>
## Specific Ideas

- Le module EFS utilise `file()` sur ligne 43 de `efs/main.tf` — les sous-SFN utilisent aussi `file()` puisqu'elles n'appellent pas d'autres SFN. Le switch vers `templatefile()` concerne uniquement les appelants (Phase 2).
- L'audit `$$.Execution.Input` (TST-02) doit scanner les blocs candidats AVANT extraction pour eviter le scope loss — pitfall critique identifie par la recherche.
- Le champ `Comment` ASL est visible dans la console AWS Step Functions — utile pour le debug en production.

</specifics>

<code_context>
## Existing Code Insights

### Reusable Assets
- `modules/step-functions/efs/main.tf` : pattern `for_each` + `file()` reutilisable directement pour les sous-SFN
- `modules/step-functions/orchestrator/main.tf` : pattern `templatefile()` avec injection ARN — reference pour Phase 2
- `tests/conftest.py` : fixtures `sfn_client`, `create_state_machine` — base pour le fix Credentials
- `tests/test_asl_validation.py` : auto-decouverte via `rglob("*.asl.json")` — couvre les nouvelles sous-SFN automatiquement

### Established Patterns
- Map `local.step_functions` → `for_each` → `aws_sfn_state_machine` (tous les modules step-functions)
- Nommage pascal/kebab configurable via `var.naming_convention`
- CloudWatch log group par module, X-Ray optionnel
- Catch → ResultPath → failure handler dans les ASL existants

### Integration Points
- `modules/step-functions/efs/outputs.tf` : exporter les ARN des nouvelles sous-SFN
- `modules/step-functions/efs/variables.tf` : aucune nouvelle variable necessaire (meme role, memes tags)
- `.github/workflows/step-functions.yml` : ajouter les modules EFS manquants au matrix

</code_context>

<deferred>
## Deferred Ideas

None — discussion stayed within phase scope

</deferred>

---

*Phase: 01-extraction*
*Context gathered: 2026-03-13*
