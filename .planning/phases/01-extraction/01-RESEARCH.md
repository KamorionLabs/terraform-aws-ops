# Phase 1: Extraction - Research

**Researched:** 2026-03-13
**Domain:** AWS Step Functions ASL, Terraform for_each, pytest/SFN Local CI
**Confidence:** HIGH

---

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions

**Placement Terraform**
- Sous-SFN dans la meme map `local.step_functions` et meme resource `aws_sfn_state_machine.efs` (for_each existant)
- Pas de map separee ni de sous-module dedie
- Meme pattern de nommage que les SFN principales : `{prefix}-efs-{name}` (ex: `refresh-efs-manage-lambda-lifecycle`)
- Le wildcard IAM `{prefix}-*` couvre automatiquement les nouvelles sous-SFN

**Contrats Input/Output**
- Double documentation : champ `Comment` dans le JSON ASL pour le quick-ref + README.md dans `modules/step-functions/efs/` pour le detail
- README centralise pour toutes les sous-SFN du module EFS : schemas Input/Output, exemples d'appel, comportement Catch

**CI Fix**
- Strip Credentials dans `conftest.py` (helper qui nettoie les definitions avant enregistrement dans SFN Local)
- Mettre a jour le matrix GitHub Actions pour couvrir les modules EFS manquants
- Scope : fix complet (pas juste le minimal)

**Ordre d'extraction**
- Claude's Discretion : choisir l'ordre optimal pour les 3 sous-SFN en fonction des dependances et du risque

### Claude's Discretion

- Ordre interne des 3 extractions (ManageLambdaLifecycle, ManageAccessPoint, ManageFileSystemPolicy)
- Implementation du helper strip_credentials dans conftest.py
- Structure exacte du README.md des sous-SFN

### Deferred Ideas (OUT OF SCOPE)

None — discussion stayed within phase scope
</user_constraints>

---

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|-----------------|
| PRE-01 | Fix CI GitHub Actions — strip Credentials dans conftest.py pour SFN Local | CI yaml analyse, conftest.py lu, pattern Credentials identifie |
| PRE-02 | Etablir le pattern templatefile() pour injection ARN des sous-SFN | Pattern file() existant dans efs/main.tf ligne 43 — switch templatefile() scope Phase 2, pas Phase 1 |
| SUB-01 | Creer sous-SFN ManageLambdaLifecycle (~8 states) | check_replication_sync + get_subpath_and_store_in_ssm identifies comme sources |
| SUB-02 | Creer sous-SFN ManageAccessPoint (~4 states) | Memes fichiers sources que SUB-01 |
| SUB-03 | Creer sous-SFN ManageFileSystemPolicy (~6 states) | setup_cross_account_replication + delete_replication identifies comme sources |
| SUB-04 | Contrats I/O explicites documentes | Pattern Comment ASL + README.md confirme |
| SUB-05 | Catch auto-contenu par sous-SFN | Pattern Catch existant dans ASLs confirme — reproductible |
| SUB-06 | Module Terraform aws_sfn_state_machine pour chaque sous-SFN | for_each existant dans efs/main.tf — ajout cles dans local.step_functions suffit |
| TST-01 | Tests ASL pour chaque nouvelle sous-SFN | rglob auto-decouverte confirme dans test_asl_validation.py |
| TST-02 | Audit pre-extraction des references $$.Execution.Input | RESULTAT AUDIT : check_replication_sync lignes 31, 50-51 — critique a resoudre avant extraction |
</phase_requirements>

---

## Summary

La Phase 1 extrait trois blocs ASL recurrents en sous-SFN autonomes deployees via le module Terraform existant. L'infrastructure Terraform est deja prete : la map `local.step_functions` dans `efs/main.tf` accepte de nouvelles entrees sans aucune modification structurelle — il suffit d'ajouter les cles et de creer les fichiers ASL correspondants. Les ARNs sont automatiquement exportes via `outputs.tf` (map `step_function_arns`).

Le blocage CI est connu et precise : SFN Local refuse les definitions contenant des blocs `Credentials` (restriction du simulateur local). La solution est un helper `strip_credentials()` dans `conftest.py` qui nettoie la definition avant `create_state_machine`. Le matrix GitHub Actions ne couvre qu'un seul fichier EFS (`delete_filesystem`) — il faut etendre la liste aux 10 fichiers existants plus les 3 nouveaux sous-SFN.

**Audit $$.Execution.Input critique :** `check_replication_sync.asl.json` utilise `$$.Execution.Input` aux lignes 31, 50-51 (`SourceSubpathSSMParameter`, `DestinationSubpathSSMParameter`). Ces references ne seront pas disponibles dans le contexte d'une sous-SFN appelee via `.sync:2`. Elles doivent etre converties en parametres d'input explicites (`$.SourceSubpathSSMParameter`) AVANT d'extraire les blocs concernes. Les fichiers `get_subpath_and_store_in_ssm.asl.json` et `delete_replication.asl.json` n'ont pas de references `$$.Execution.Input`.

**Recommandation principale :** Ordre d'extraction — ManageFileSystemPolicy en premier (sources propres, pas de $$.Execution.Input), puis ManageAccessPoint, puis ManageLambdaLifecycle (necessite la resolution des refs $$.Execution.Input en premier).

---

## Standard Stack

### Core

| Composant | Version/Detail | Purpose | Statut dans le projet |
|-----------|---------------|---------|----------------------|
| `aws_sfn_state_machine` | AWS Provider ~5.x | Deploiement des SFN | Deja en place dans efs/main.tf |
| `file()` Terraform | built-in | Chargement ASL JSON | Utilise ligne 43 efs/main.tf |
| `templatefile()` Terraform | built-in | Injection ARN (Phase 2 uniquement) | Pattern dans orchestrator/main.tf |
| SFN Local (Docker) | `amazon/aws-stepfunctions-local:latest` | Tests CI sans credentials AWS | Deja configure dans step-functions.yml |
| pytest | requirements-dev.txt | Tests ASL | Deja en place, auto-decouverte via rglob |

### Pattern Terraform etabli (efs/main.tf)

```hcl
locals {
  step_functions = {
    # Existant
    delete_filesystem = "delete_filesystem.asl.json"
    # AJOUTER (Phase 1) :
    manage_lambda_lifecycle  = "manage_lambda_lifecycle.asl.json"
    manage_access_point      = "manage_access_point.asl.json"
    manage_filesystem_policy = "manage_filesystem_policy.asl.json"
  }
}
# La resource aws_sfn_state_machine.efs{for_each} se deploie sans changement
```

Les ARNs sont accessibles immediatement via :
```hcl
module.efs_sfn.step_function_arns["manage_lambda_lifecycle"]
module.efs_sfn.step_function_arns["manage_access_point"]
module.efs_sfn.step_function_arns["manage_filesystem_policy"]
```

---

## Architecture Patterns

### Structure de fichiers cible

```
modules/step-functions/efs/
├── main.tf                              # Ajouter 3 cles dans local.step_functions
├── outputs.tf                           # Inchange — exports ARN automatiques
├── variables.tf                         # Inchange — aucune nouvelle variable
├── README.md                            # CREER : contrats I/O des 3 sous-SFN
├── manage_lambda_lifecycle.asl.json     # CREER (SUB-01)
├── manage_access_point.asl.json         # CREER (SUB-02)
├── manage_filesystem_policy.asl.json    # CREER (SUB-03)
├── check_replication_sync.asl.json      # MODIFIER : resoudre $$.Execution.Input
├── [autres .asl.json existants]         # Inchanges
```

### Pattern ASL sous-SFN (structure type)

```json
{
  "Comment": "ManageLambdaLifecycle | Input: {LambdaConfig, SourceAccount} | Output: {LambdaArn, Status}",
  "StartAt": "FirstState",
  "States": {
    "FirstState": {
      "Type": "Task",
      "Resource": "arn:aws:states:::lambda:invoke",
      "Credentials": { "RoleArn.$": "$.SourceAccount.RoleArn" },
      "Parameters": { "FunctionName.$": "$.LambdaConfig.FunctionName" },
      "Catch": [
        {
          "ErrorEquals": ["States.ALL"],
          "ResultPath": "$.Error",
          "Next": "HandleError"
        }
      ],
      "Next": "NextState"
    },
    "HandleError": {
      "Type": "Fail",
      "Error": "ManageLambdaLifecycleFailed",
      "Cause": "See $.Error for details"
    }
  }
}
```

**Regle Catch auto-contenu (SUB-05) :** Chaque sous-SFN se termine par un state `Fail` propre avec un `Error` nomme (`ManageLambdaLifecycleFailed`, etc.). Le parent detecte l'echec via l'erreur nommee dans son propre Catch. Pas de propagation silencieuse.

### Pattern strip_credentials (PRE-01)

SFN Local refuse les blocs `Credentials` — ils sont cross-account et impliquent AssumeRole non supporte localement.

```python
# A ajouter dans conftest.py

def strip_credentials(definition: dict) -> dict:
    """Remove Credentials blocks from ASL definition for SFN Local compatibility."""
    import copy
    clean = copy.deepcopy(definition)
    for state_name, state_def in clean.get("States", {}).items():
        state_def.pop("Credentials", None)
    return clean


@pytest.fixture
def create_state_machine(sfn_client):
    """Factory fixture — strips Credentials before registration."""
    created_arns = []

    def _create(name: str, definition: dict, role_arn: str = "arn:aws:iam::123456789012:role/test-role"):
        clean_definition = strip_credentials(definition)  # FIX ICI
        # ... reste inchange
        response = sfn_client.create_state_machine(
            name=name,
            definition=json.dumps(clean_definition),
            roleArn=role_arn,
            type='STANDARD'
        )
        ...
    yield _create
```

### Pattern nommage kebab (decision locked)

```
{prefix}-efs-manage-lambda-lifecycle
{prefix}-efs-manage-access-point
{prefix}-efs-manage-filesystem-policy
```

La logique de nommage dans `efs/main.tf` convertit automatiquement les cles snake_case en kebab-case. Pas de code supplementaire.

### Anti-patterns a eviter

- **Ne pas creer de sous-module Terraform dedie** : decision locked — rester dans `local.step_functions` existant
- **Ne pas utiliser templatefile() pour les sous-SFN elles-memes** : elles n'appellent pas d'autres SFN, `file()` suffit. `templatefile()` est pour les appelants (Phase 2)
- **Ne pas laisser de references $$.Execution.Input dans les blocs extraits** : scope loss garanti — les sous-SFN n'ont pas acces au contexte d'execution du parent

---

## Don't Hand-Roll

| Probleme | Ne pas construire | Utiliser | Pourquoi |
|----------|------------------|----------|----------|
| Export ARN sous-SFN | Output Terraform custom | `step_function_arns[key]` existant dans outputs.tf | Deja en place, map automatique |
| Nommage SFN | Logique custom | `local.sfn_names` existant dans main.tf | Gere pascal et kebab |
| Test auto-decouverte | Scanner manuel | `rglob("*.asl.json")` dans test_asl_validation.py | Les nouveaux fichiers sont couverts automatiquement |
| Strip credentials test | Mock AWS | Fonction `strip_credentials()` dans conftest.py | SFN Local ne supporte pas AssumeRole |

---

## Common Pitfalls

### Pitfall 1 : $$.Execution.Input scope loss (CRITIQUE)

**Ce qui se passe :** Un state dans une sous-SFN reference `$$.Execution.Input.SomeField`. Quand la sous-SFN est appelee par un parent via `.sync:2`, `$$.Execution.Input` contient l'input de la sous-SFN (le payload passe par le parent), pas l'input de l'execution parente. La valeur attendue est introuvable.

**Pourquoi ca arrive :** `$$.Execution.Input` est relatif a l'execution courante. Dans une sous-SFN, l'execution courante EST la sous-SFN.

**Comment eviter :** Avant d'extraire un bloc, scanner tous les states pour `$$.Execution.Input`. Convertir en references `$` explicites : `$$.Execution.Input.SourceSubpathSSMParameter` → passer `SourceSubpathSSMParameter` comme champ d'input et referencer `$.SourceSubpathSSMParameter`.

**Fichiers concernes (audit Phase 1) :**
- `check_replication_sync.asl.json` lignes 31, 50-51 : `SourceSubpathSSMParameter`, `DestinationSubpathSSMParameter` — A RESOUDRE avant extraction des blocs ManageLambdaLifecycle et ManageAccessPoint
- `get_subpath_and_store_in_ssm.asl.json` : PROPRE (0 reference)
- `delete_replication.asl.json` : PROPRE (0 reference)
- `setup_cross_account_replication.asl.json` : non audite dans cette session — a verifier

### Pitfall 2 : CI matrix EFS incomplet

**Ce qui se passe :** `validate-efs-module` ne couvre qu'un seul fichier (`delete_filesystem`). Les nouveaux fichiers ASL passent `validate-local` mais ne sont pas valides individuellement en CI.

**Solution :** Ajouter dans le matrix tous les fichiers EFS existants (10 fichiers) + les 3 nouveaux sous-SFN (13 entrees total).

**Fichiers EFS actuels a ajouter au matrix :**
```
check_replication_sync, cleanup_efs_lambdas, create_filesystem,
delete_filesystem (existant), delete_replication,
get_subpath_and_store_in_ssm, restore_from_backup,
setup_cross_account_replication,
manage_lambda_lifecycle, manage_access_point, manage_filesystem_policy
```

### Pitfall 3 : IAM wildcard non verifie

**Ce qui se passe :** Les nouvelles sous-SFN `{prefix}-efs-manage-*` ne sont pas couvertes par la policy IAM si le wildcard est `{prefix}-efs-*` plutot que `{prefix}-*`.

**Solution :** Verifier la policy IAM du role `orchestrator_role_arn` AVANT le premier `terraform apply`. 5 minutes de verification preventive.

**Warning sign :** `AccessDeniedException: States.StartExecution` au premier appel d'une sous-SFN depuis un parent.

### Pitfall 4 : Credentials dans SFN Local

**Ce qui se passe :** `create_state_machine` dans conftest.py envoie la definition brute avec les blocs `Credentials`. SFN Local retourne une erreur de validation.

**Solution :** Appliquer `strip_credentials()` systematiquement dans le fixture `create_state_machine` avant tout enregistrement.

---

## Code Examples

### Ajout sous-SFN dans efs/main.tf

```hcl
# Source: efs/main.tf pattern existant — ajout de 3 cles
locals {
  step_functions = {
    # ... existant inchange ...
    delete_filesystem                = "delete_filesystem.asl.json"
    check_replication_sync           = "check_replication_sync.asl.json"
    setup_cross_account_replication  = "setup_cross_account_replication.asl.json"
    delete_replication               = "delete_replication.asl.json"
    get_subpath_and_store_in_ssm     = "get_subpath_and_store_in_ssm.asl.json"
    restore_from_backup              = "restore_from_backup.asl.json"
    create_filesystem                = "create_filesystem.asl.json"

    # Phase 1 : nouvelles sous-SFN
    manage_lambda_lifecycle          = "manage_lambda_lifecycle.asl.json"
    manage_access_point              = "manage_access_point.asl.json"
    manage_filesystem_policy         = "manage_filesystem_policy.asl.json"
  }
}
# resource aws_sfn_state_machine.efs — INCHANGE
```

### Extension matrix CI EFS

```yaml
# .github/workflows/step-functions.yml
validate-efs-module:
  strategy:
    matrix:
      step_function:
        - check_replication_sync
        - cleanup_efs_lambdas
        - create_filesystem
        - delete_filesystem
        - delete_replication
        - get_subpath_and_store_in_ssm
        - restore_from_backup
        - setup_cross_account_replication
        - manage_lambda_lifecycle
        - manage_access_point
        - manage_filesystem_policy
```

Note : le job actuel a `exit 0` si fichier non trouve. Pour les nouveaux fichiers, passer a `exit 1` une fois les ASL crees.

### Structure README.md sous-SFN (contrat I/O)

```markdown
## ManageLambdaLifecycle

**Fichier :** `manage_lambda_lifecycle.asl.json`
**Etats :** ~8

### Input
```json
{
  "LambdaConfig": {
    "FunctionName": "string",
    "SourceLambdaRoleArn": "string"
  },
  "SourceAccount": {
    "RoleArn": "string"
  }
}
```

### Output
```json
{
  "LambdaArn": "string",
  "Status": "string"
}
```

### Catch
Tous les echecs terminent sur state `Fail` avec `Error: "ManageLambdaLifecycleFailed"`.
Le parent detecte via `ErrorEquals: ["ManageLambdaLifecycleFailed"]`.
```

---

## Ordre d'extraction recommande (Claude's Discretion)

**Recommandation : ManageFileSystemPolicy → ManageAccessPoint → ManageLambdaLifecycle**

| Ordre | Sous-SFN | Sources | $$.Execution.Input | Risque |
|-------|----------|---------|-------------------|--------|
| 1 | ManageFileSystemPolicy | setup_cross_account_replication, delete_replication | 0 ref (propre) | Faible |
| 2 | ManageAccessPoint | check_replication_sync, get_subpath_and_store_in_ssm | 0 ref (propre) | Faible |
| 3 | ManageLambdaLifecycle | check_replication_sync | 3 refs a resoudre | Moyen — necessite modif check_replication_sync |

Extraire ManageFileSystemPolicy en premier valide le pattern (Terraform + CI + contrat I/O) sans toucher aux refs $$.Execution.Input. Les deux suivantes peuvent alors suivre le meme playbook.

---

## Validation Architecture

### Test Framework

| Property | Value |
|----------|-------|
| Framework | pytest (version dans requirements-dev.txt) |
| Config file | pytest.ini ou pyproject.toml (a verifier) |
| Quick run command | `pytest tests/test_asl_validation.py -v --tb=short` |
| Full suite command | `pytest tests/ -v --tb=short` |

### Phase Requirements → Test Map

| Req ID | Behavior | Test Type | Automated Command | Fichier existe ? |
|--------|----------|-----------|-------------------|-----------------|
| PRE-01 | strip_credentials ne plante pas sur ASL avec Credentials | unit | `pytest tests/conftest.py -v` (indirect via fixtures) | Partiel — fixture a modifier |
| PRE-02 | Pattern templatefile() — scope Phase 2, pas de test Phase 1 | N/A | N/A | Hors scope |
| SUB-01 | manage_lambda_lifecycle.asl.json JSON valide, StartAt/States presents | unit | `pytest tests/test_asl_validation.py -v -k manage_lambda` | ❌ Wave 0 (fichier ASL a creer) |
| SUB-02 | manage_access_point.asl.json structure valide | unit | `pytest tests/test_asl_validation.py -v -k manage_access` | ❌ Wave 0 |
| SUB-03 | manage_filesystem_policy.asl.json structure valide | unit | `pytest tests/test_asl_validation.py -v -k manage_filesystem` | ❌ Wave 0 |
| SUB-04 | Champ Comment present dans chaque sous-SFN | unit | `pytest tests/test_asl_validation.py -v` (ajouter TestASLComment) | ❌ Wave 0 — test a ajouter |
| SUB-05 | Chaque sous-SFN a un state Fail avec Error nomme | unit | `pytest tests/test_asl_validation.py -v -k catch` | ❌ Wave 0 — test a ajouter |
| SUB-06 | Terraform plan sans erreur avec 3 nouvelles cles | smoke | `terraform plan` (manuel) | Manuel uniquement |
| TST-01 | Auto-decouverte couvre les 3 nouveaux fichiers | unit | `pytest tests/test_asl_validation.py -v` | Auto via rglob |
| TST-02 | Audit $$.Execution.Input documente avant extraction | review | Scan grep (manuel pre-extraction) | Manuel — fait dans cette session |

### Sampling Rate

- **Par commit de tache :** `pytest tests/test_asl_validation.py -v --tb=short`
- **Par merge de wave :** `pytest tests/ -v --tb=short`
- **Phase gate :** Suite complete verte avant `/gsd:verify-work`

### Wave 0 Gaps

- [ ] `modules/step-functions/efs/manage_lambda_lifecycle.asl.json` — couvre SUB-01, TST-01
- [ ] `modules/step-functions/efs/manage_access_point.asl.json` — couvre SUB-02, TST-01
- [ ] `modules/step-functions/efs/manage_filesystem_policy.asl.json` — couvre SUB-03, TST-01
- [ ] Test `TestASLComment` dans `tests/test_asl_validation.py` — couvre SUB-04
- [ ] Test `TestASLCatchNamed` dans `tests/test_asl_validation.py` — couvre SUB-05
- [ ] Fonction `strip_credentials()` dans `tests/conftest.py` — couvre PRE-01

---

## Sources

### Primary (HIGH confidence)

- Code source direct : `modules/step-functions/efs/main.tf` — pattern for_each, file(), nommage
- Code source direct : `modules/step-functions/efs/outputs.tf` — export ARN existant
- Code source direct : `modules/step-functions/efs/variables.tf` — variables disponibles
- Code source direct : `tests/conftest.py` — fixtures existantes, absence de strip_credentials
- Code source direct : `tests/test_asl_validation.py` — auto-decouverte rglob confirmee
- Code source direct : `.github/workflows/step-functions.yml` — matrix EFS incomplet confirme (1 seule entree)
- Audit grep : `check_replication_sync.asl.json` lignes 31, 50-51 — $$.Execution.Input confirme

### Secondary (MEDIUM confidence)

- CONTEXT.md decisions : pattern strip_credentials, decision for_each, nommage kebab
- STATE.md blockers : wildcard IAM, comportement Output .sync:2

### Tertiary (LOW confidence)

- Comportement exact Output envelope `.sync:2` (string JSON vs objet) — a confirmer sur le premier deploy reel

---

## Metadata

**Confidence breakdown :**
- Standard stack : HIGH — code existant lu directement
- Architecture : HIGH — patterns extraits du code source confirme
- Pitfalls : HIGH (scope loss, CI matrix) / MEDIUM (IAM wildcard — a verifier)
- Ordre extraction : MEDIUM — raisonnement dependances, non valide experimentalement

**Research date :** 2026-03-13
**Valid until :** 2026-04-13 (stack stable, AWS SFN ASL specification rarement cassante)
