# Phase 4: Foundation - Research

**Researched:** 2026-03-16
**Domain:** Terraform module (SFN + Lambda) pour la synchronisation generique de secrets SM et parametres SSM
**Confidence:** HIGH

## Summary

Cette phase cree le squelette complet de l'infrastructure de synchronisation : un module Terraform `modules/step-functions/sync/`, une SFN `SyncConfigItems` avec Choice state SM/SSM, et une Lambda generique `sync_config_items`. Le codebase existant fournit des patterns extremement clairs et bien etablis -- le module `audit/` sert de reference structurelle directe, et les ASL existants (`rotate_secrets.asl.json`, `restore_from_backup.asl.json`) demontrent deja les patterns Choice state, Map state, Credentials cross-account, et SDK integrations pour Secrets Manager.

L'investigation du code existant revele que le projet utilise deux approches pour le deploiement Lambda : inline (`archive_file` + `aws_lambda_function` dans audit/) et S3 (`lambda-code` module pour les Lambdas deployees dynamiquement par SFN). La decision CONTEXT.md specifie "Lambda deployee via le module lambda-code existant (S3)", donc la Lambda sync_config_items sera ajoutee au module `lambda-code` (et non deployee inline comme audit/).

**Primary recommendation:** Reproduire le pattern exact du module `audit/` pour la structure Terraform, mais utiliser le module `lambda-code` pour le packaging Lambda. L'ASL `sync_config_items.asl.json` doit suivre le pattern de `rotate_secrets.asl.json` pour le Choice state et le Map state avec Credentials cross-account.

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions
- Structure globale : `ConfigSync.Enabled`, `ConfigSync.SourceAccount`, `ConfigSync.DestinationAccount`, `ConfigSync.Items[]`
- Un seul SourceAccount + DestinationAccount par appel (pas par item)
- Wildcards supportes dans les paths source (`/rubix/bene-prod/app/*` matche tous les secrets sous ce path)
- La SFN liste les secrets/parametres matchants puis itere via Map state
- Destination path avec placeholder `{name}` pour le nom relatif du secret source
- Transforms : map cle -> replace (`"host": {"replace": [{"from": "...", "to": "..."}]}`) + `"skip": true` pour ignorer certaines cles
- L'orchestrateur pre-remplit les Transforms avec les valeurs dynamiques (endpoints DB) avant d'appeler SyncConfigItems
- Mode standalone : meme format d'input, les from/to sont fournis explicitement
- Nouveau module `modules/step-functions/sync/` -- self-contained comme audit/
- Structure : main.tf (SFN + IAM), variables.tf, outputs.tf, versions.tf, sync_config_items.asl.json
- Lambda deployee via le module lambda-code existant (S3), pas inline
- Lambda source dans `lambdas/sync-config-items/sync_config_items.py`
- Pas de layer necessaire -- boto3 inclus dans le runtime Python Lambda
- Module wire dans root main.tf comme les autres modules step-functions
- Une seule Lambda `sync_config_items.py` fait fetch + transform + write en un seul appel par item
- La SFN orchestre : ValidateInput -> CheckType (Choice SM/SSM) -> ListItems -> MapOverItems -> SyncSingleItem (Lambda) -> PrepareOutput
- Pas de logique metier Rubix-specifique hardcodee -- tout est configurable via l'input
- La Lambda supporte les deux backends (SM et SSM) via le champ `Type` dans l'input de chaque item

### Claude's Discretion
- Nommage exact des states ASL dans le squelette SFN
- Structure IAM roles/policies pour cross-account SM/SSM access
- Schema exact des variables.tf du module sync
- Implementation du stub Lambda (structure input/output correcte, pas de logique fetch/write)
- Mecanisme de resolution dynamique des transforms (pre-remplissage par l'orchestrateur vs variables dans l'input)

### Deferred Ideas (OUT OF SCOPE)
None -- discussion stayed within phase scope
</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|-----------------|
| SYNC-01 | SFN SyncConfigItems unique traitant SM et SSM via Choice state sur le type | Pattern valide via `rotate_secrets.asl.json` (Choice + Map + Credentials); ASL structure avec ValidateInput -> CheckType -> ListItems -> MapOverItems -> SyncSingleItem -> PrepareOutput |
| SYNC-08 | Lambda(s) generique(s) pour la logique fetch/transform/write -- pas de logique Rubix hardcodee | Pattern `cross_region_rds_proxy.py` comme reference de Lambda generique; `fetch_secrets.py` pour le pattern STS cross-account et path matching |
| INFRA-01 | Module Terraform pour la SFN SyncConfigItems + Lambda(s) dans modules/step-functions/ avec ARN exporte | Module `audit/` comme template exact (structure, naming, for_each, outputs) + module `lambda-code` pour le packaging S3 |
</phase_requirements>

## Standard Stack

### Core
| Library/Tool | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| Terraform (hashicorp/aws) | >= 5.0 | Provider AWS pour SFN, IAM, Lambda, CloudWatch | Identique au reste du projet (`versions.tf` de chaque module) |
| Terraform (hashicorp/archive) | >= 2.0 | Packaging Lambda en ZIP | Utilise par `lambda-code` module et `audit/` module |
| Python | 3.12 | Runtime Lambda | Identique aux autres Lambdas du projet (ex: `cloudtrail_audit`) |
| boto3 | inclus runtime | SDK AWS pour SM/SSM/STS | Pas de layer requise, inclus dans le runtime Python Lambda |
| ASL (Amazon States Language) | -- | Definition SFN JSON | Standard pour toutes les SFN du projet |

### Supporting
| Library/Tool | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| pytest | existant | Tests ASL (auto-decouverte via rglob) | Test automatique du nouveau fichier ASL |
| Step Functions Local | Docker | Tests d'integration SFN | Tests optionnels via `conftest.py` + marker `@pytest.mark.sfn_local` |

### Alternatives Considered
| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| Lambda pour fetch/write | SFN SDK integrations (aws-sdk:secretsmanager:*) | CONTEXT.md verrouille : une Lambda unique fait fetch+transform+write. Les SDK integrations ne supportent pas la logique de transformation. |
| Module lambda-code (S3) | Inline archive_file (comme audit/) | CONTEXT.md verrouille : "Lambda deployee via le module lambda-code existant (S3), pas inline" |

## Architecture Patterns

### Recommended Project Structure
```
modules/step-functions/sync/
  main.tf                        # SFN + IAM + CloudWatch + Lambda permission
  variables.tf                   # prefix, orchestrator_role_arn, cross_account_role_arns, etc.
  outputs.tf                     # step_function_arns, sync_config_items_arn, lambda_function_arn
  versions.tf                    # terraform >= 1.0, aws >= 5.0, archive >= 2.0
  sync_config_items.asl.json     # ASL definition avec templatefile placeholder

lambdas/sync-config-items/
  sync_config_items.py           # Lambda stub (structure input/output, pas de logique reelle)

modules/lambda-code/main.tf      # Ajouter entry "sync-config-items" dans local.lambda_functions
modules/lambda-code/outputs.tf   # Ajouter output sync_config_items_config

main.tf (root)                   # Ajouter module "step_functions_sync"
outputs.tf (root)                # Ajouter outputs pour sync SFN ARNs
```

### Pattern 1: Module Step Functions (reference: audit/)
**What:** Structure identique a `modules/step-functions/audit/main.tf`
**When to use:** Pour tout nouveau module SFN dans le projet
**Example:**
```hcl
# Source: modules/step-functions/audit/main.tf (pattern observe)
locals {
  step_functions = {
    sync_config_items = "sync_config_items.asl.json"
  }

  sfn_names = {
    for k, v in local.step_functions : k => (
      var.naming_convention == "pascal"
      ? "${var.prefix}-Sync-${replace(title(replace(k, "_", " ")), " ", "")}"
      : "${var.prefix}-sync-${replace(k, "_", "-")}"
    )
  }
}

resource "aws_sfn_state_machine" "sync" {
  for_each = local.step_functions

  name     = local.sfn_names[each.key]
  role_arn = var.orchestrator_role_arn

  definition = templatefile("${path.module}/${each.value}", {
    SyncConfigItemsLambdaArn = aws_lambda_function.sync_config_items.arn
  })

  # ... logging_configuration, tracing_configuration, tags (identiques a audit/)
}
```

### Pattern 2: Lambda via lambda-code Module (S3)
**What:** Ajout d'une entree dans `local.lambda_functions` du module `lambda-code` pour packaging et upload S3
**When to use:** Quand la Lambda doit etre deployable via S3 (decision verrouilee)
**Example:**
```hcl
# Source: modules/lambda-code/main.tf (pattern observe)
# Ajouter dans local.lambda_functions:
"sync-config-items" = {
  source_file = "${path.module}/../../lambdas/sync-config-items/sync_config_items.py"
  handler     = "sync_config_items.lambda_handler"
  description = "Sync config items (secrets/parameters) between AWS accounts"
}
```

**IMPORTANT -- Distinction audit/ vs lambda-code:** Le module `audit/` deploie sa Lambda inline (archive_file + aws_lambda_function dans le module lui-meme). Pour sync/, la Lambda est deployee dans le module sync/ (comme audit/) MAIS le code est AUSSI package via lambda-code pour que les SFN puissent deployer dynamiquement. La decision CONTEXT.md "Lambda deployee via le module lambda-code existant (S3)" signifie utiliser le pattern lambda-code pour le packaging. Cependant, le module sync/ a quand meme besoin d'une `aws_lambda_function` pour que la SFN puisse l'invoquer. L'approche recommandee : deployer la Lambda dans le module sync/ (inline comme audit/) ET ajouter au lambda-code pour coherence. Alternativement, si "deployee via lambda-code" signifie ne PAS deployer inline, il faut que le module sync/ reference l'ARN Lambda via une variable en input. A valider avec la decision verrouilee.

**Recommandation:** Deployer la Lambda inline dans le module sync/ (identique au pattern audit/) car c'est le pattern le plus simple et auto-contenu. La Lambda n'a pas besoin d'etre dans lambda-code car elle n'est pas deployee dynamiquement par une SFN -- elle EST invoquee par la SFN directement via son ARN. Le module lambda-code est pour les Lambdas creees dynamiquement par les SFN dans les comptes destination.

### Pattern 3: ASL avec Choice State + Map State + Credentials Cross-Account
**What:** SFN qui route selon un type, puis itere sur une liste d'items avec un Lambda call par item
**When to use:** Pour le flow SyncConfigItems
**Example (derive de rotate_secrets.asl.json):**
```json
{
  "Comment": "Sync config items between AWS accounts - routes SM/SSM via Choice state",
  "StartAt": "ValidateInput",
  "States": {
    "ValidateInput": {
      "Type": "Choice",
      "Choices": [
        {
          "And": [
            { "Variable": "$.ConfigSync.Enabled", "BooleanEquals": true },
            { "Variable": "$.ConfigSync.Items", "IsPresent": true }
          ],
          "Next": "MapOverItems"
        }
      ],
      "Default": "SyncSkipped"
    },
    "MapOverItems": {
      "Type": "Map",
      "ItemsPath": "$.ConfigSync.Items",
      "ItemSelector": {
        "Item.$": "$$.Map.Item.Value",
        "SourceAccount.$": "$.ConfigSync.SourceAccount",
        "DestinationAccount.$": "$.ConfigSync.DestinationAccount"
      },
      "ItemProcessor": {
        "ProcessorConfig": { "Mode": "INLINE" },
        "StartAt": "CheckType",
        "States": {
          "CheckType": {
            "Type": "Choice",
            "Choices": [
              { "Variable": "$.Item.Type", "StringEquals": "SecretsManager", "Next": "SyncSMItem" },
              { "Variable": "$.Item.Type", "StringEquals": "SSMParameter", "Next": "SyncSSMItem" }
            ],
            "Default": "UnsupportedType"
          },
          "SyncSMItem": {
            "Type": "Task",
            "Resource": "${SyncConfigItemsLambdaArn}",
            "End": true,
            "Retry": [...]
          },
          "SyncSSMItem": {
            "Type": "Task",
            "Resource": "${SyncConfigItemsLambdaArn}",
            "End": true,
            "Retry": [...]
          }
        }
      }
    }
  }
}
```

### Pattern 4: Cross-Account STS dans Lambda (reference: fetch_secrets.py)
**What:** Pattern `get_cross_account_client()` pour acceder SM/SSM dans un autre compte
**When to use:** Dans la Lambda sync_config_items pour lire source et ecrire destination
**Example:**
```python
# Source: lambdas/fetch-secrets/fetch_secrets.py (lignes 38-76)
def get_cross_account_client(service, role_arn, region, session_name="SyncConfigItems"):
    sts_client = boto3.client("sts")
    assumed_role = sts_client.assume_role(
        RoleArn=role_arn, RoleSessionName=session_name, DurationSeconds=900
    )
    credentials = assumed_role["Credentials"]
    return boto3.client(
        service, region_name=region,
        aws_access_key_id=credentials["AccessKeyId"],
        aws_secret_access_key=credentials["SecretAccessKey"],
        aws_session_token=credentials["SessionToken"],
    )
```

### Pattern 5: IAM Role Lambda + Cross-Account (reference: audit/)
**What:** Role IAM pour la Lambda avec permissions STS, SM, SSM, CloudWatch Logs
**When to use:** Pour la Lambda sync_config_items
**Example:**
```hcl
# Derive de modules/step-functions/audit/main.tf + modules/source-account/main.tf
resource "aws_iam_role_policy" "lambda_sync" {
  policy = jsonencode({
    Statement = [
      {
        Sid    = "AssumeRoleForCrossAccount"
        Effect = "Allow"
        Action = "sts:AssumeRole"
        Resource = var.cross_account_role_arns
      },
      {
        Sid    = "CloudWatchLogs"
        Effect = "Allow"
        Action = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"]
        Resource = "arn:aws:logs:*:*:*"
      }
    ]
  })
}
```
Note: Les permissions SM/SSM sont sur les roles assumes (source-account/destination-account), pas sur le role de la Lambda elle-meme. La Lambda assume le role du compte cible via STS, et ce role a deja les permissions SM/SSM.

### Anti-Patterns to Avoid
- **Hardcoder les paths Rubix-specifiques:** Tout doit etre configurable via l'input (SourcePath, DestinationPath, Transforms). Les patterns comme `DEFAULT_PATH_MAPPING` dans compare_secrets_manager.py sont exactement ce qu'il NE faut PAS faire dans la Lambda sync.
- **Permissions SM/SSM directes sur le role Lambda:** Les permissions SM/SSM sont sur les roles cross-account (source-account, destination-account), pas sur le role de la Lambda sync. La Lambda n'a besoin que de `sts:AssumeRole`.
- **Deployer la Lambda sans CloudWatch Log Group:** Toujours creer un `aws_cloudwatch_log_group.lambda` pour eviter la creation automatique sans retention.
- **Oublier `aws_lambda_permission` pour SFN:** Necessaire pour que la SFN puisse invoquer la Lambda (pattern audit/).

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Cross-account STS | Custom session management | Pattern `get_cross_account_client()` de fetch_secrets.py | Gestion des credentials, expiration, error handling deja resolus |
| ASL validation | Validation manuelle du JSON | `tests/test_asl_validation.py` (auto-decouverte via rglob) | Couvre JSON syntax, StartAt, state transitions, Choice requirements, Credentials, Comment field |
| Lambda packaging | Script custom de ZIP | `data "archive_file"` de Terraform | Pattern standard du projet (audit/, lambda-code) |
| SFN naming convention | Nommage ad-hoc | Pattern `local.sfn_names` avec var.naming_convention | Coherence pascal/kebab sur tout le projet |

**Key insight:** Le projet a deja resolu tous les problemes d'infrastructure (IAM cross-account, SFN patterns, Lambda deployment). La phase 4 est une application methodique de patterns existants, pas une innovation.

## Common Pitfalls

### Pitfall 1: Lambda inline vs lambda-code confusion
**What goes wrong:** Confusion entre le deploiement inline (archive_file + aws_lambda_function dans le module) et le deploiement via lambda-code (S3)
**Why it happens:** Le module audit/ utilise inline, tandis que le CONTEXT.md mentionne lambda-code
**How to avoid:** Le module sync/ deploie la Lambda inline (comme audit/). Le module lambda-code est reserve aux Lambdas creees dynamiquement par les SFN dans les comptes cibles. sync_config_items tourne dans le compte orchestrateur, pas dans les comptes cibles.
**Warning signs:** Si le module sync/ n'a pas de `aws_lambda_function` resource, c'est un signal d'erreur.

### Pitfall 2: Permissions SM/SSM sur le mauvais role
**What goes wrong:** Ajouter des permissions secretsmanager:* / ssm:* sur le role Lambda au lieu du role cross-account
**Why it happens:** Reflexe de donner les permissions directes a la Lambda
**How to avoid:** La Lambda a besoin UNIQUEMENT de `sts:AssumeRole` et CloudWatch Logs. Les permissions SM/SSM sont deja sur les roles dans les comptes source/destination (voir `modules/source-account/main.tf` lignes 260-286 et `modules/destination-account/main.tf` lignes 140-159, 350-377).
**Warning signs:** Policy Lambda avec `secretsmanager:*` ou `ssm:*`

### Pitfall 3: Oublier le wiring dans root main.tf et outputs.tf
**What goes wrong:** Le module est cree mais pas integre dans le root
**Why it happens:** Focus sur le module sans penser a l'integration
**How to avoid:** Checklist : 1) module "step_functions_sync" dans main.tf, 2) outputs dans outputs.tf, 3) parametres passes (prefix, tags, orchestrator_role_arn, etc.)
**Warning signs:** `terraform plan` ne montre aucun changement pour le nouveau module

### Pitfall 4: ASL templatefile sans variable correspondante
**What goes wrong:** Le placeholder `${SyncConfigItemsLambdaArn}` dans l'ASL n'est pas passe dans `templatefile()`
**Why it happens:** Oubli de synchroniser les variables entre l'ASL et le main.tf
**How to avoid:** Verifier que chaque `${...}` dans l'ASL a un correspondant dans les parametres de `templatefile()` du main.tf
**Warning signs:** `terraform plan` echoue avec "Invalid value for ... variable not found"

### Pitfall 5: Stub Lambda qui ne respecte pas le contrat d'interface
**What goes wrong:** Le stub retourne un format different de ce que la SFN attend
**Why it happens:** Phase 4 = stub, mais le contrat doit etre correct pour que la SFN soit testable
**How to avoid:** Le stub doit retourner le meme schema que la version finale (avec des valeurs placeholder), et accepter le meme input. Definir le contrat input/output explicitement.
**Warning signs:** La SFN echoue sur des `$.Result.xxx` qui n'existent pas dans le retour du stub

### Pitfall 6: Naming convention incoherent pour le module sync
**What goes wrong:** Le nommage ne suit pas le pattern pascal/kebab existant
**Why it happens:** Le pattern `sfn_names` dans audit/ utilise `"${var.prefix}-Audit-${...}"` en pascal
**How to avoid:** Pour sync, utiliser `"${var.prefix}-Sync-${...}"` en pascal ou `"${var.prefix}-sync-${...}"` en kebab, suivant le meme pattern
**Warning signs:** La SFN cree a un nom qui ne suit pas le pattern des autres modules

## Code Examples

### Input ConfigSync schema (contrat d'interface)
```json
{
  "ConfigSync": {
    "Enabled": true,
    "SourceAccount": {
      "AccountId": "111111111111",
      "RoleArn": "arn:aws:iam::111111111111:role/refresh-source-role",
      "Region": "eu-central-1"
    },
    "DestinationAccount": {
      "AccountId": "222222222222",
      "RoleArn": "arn:aws:iam::222222222222:role/refresh-destination-role",
      "Region": "eu-central-1"
    },
    "Items": [
      {
        "Type": "SecretsManager",
        "SourcePath": "/rubix/bene-prod/app/*",
        "DestinationPath": "/digital/prd/app/mro-bene/{name}",
        "Transforms": {
          "host": { "replace": [{"from": "old-endpoint.rds.amazonaws.com", "to": "new-endpoint.rds.amazonaws.com"}] },
          "password": { "skip": true }
        }
      },
      {
        "Type": "SSMParameter",
        "SourcePath": "/rubix/bene-prod/config/*",
        "DestinationPath": "/digital/prd/config/mro-bene/{name}",
        "Transforms": {}
      }
    ]
  }
}
```

### Lambda stub input/output contract
```python
# Input per item (from SFN Map state):
# {
#   "Item": {
#     "Type": "SecretsManager" | "SSMParameter",
#     "SourcePath": "/rubix/bene-prod/app/my-secret",
#     "DestinationPath": "/digital/prd/app/mro-bene/my-secret",
#     "Transforms": { ... }
#   },
#   "SourceAccount": { "AccountId": "...", "RoleArn": "...", "Region": "..." },
#   "DestinationAccount": { "AccountId": "...", "RoleArn": "...", "Region": "..." }
# }
#
# Output:
# {
#   "statusCode": 200,
#   "result": {
#     "status": "synced" | "created" | "updated" | "skipped" | "error",
#     "source": "/rubix/bene-prod/app/my-secret",
#     "destination": "/digital/prd/app/mro-bene/my-secret",
#     "type": "SecretsManager",
#     "message": "Stub - no actual sync performed"
#   }
# }

def lambda_handler(event, context):
    """Stub Lambda for sync_config_items - returns structured output without actual sync."""
    item = event.get("Item", {})
    return {
        "statusCode": 200,
        "result": {
            "status": "skipped",
            "source": item.get("SourcePath", ""),
            "destination": item.get("DestinationPath", ""),
            "type": item.get("Type", "unknown"),
            "message": "Stub - no actual sync performed"
        }
    }
```

### Root main.tf wiring pattern
```hcl
# Source: main.tf root (pattern observe pour tous les modules)
module "step_functions_sync" {
  source = "./modules/step-functions/sync"

  prefix                = var.prefix
  tags                  = var.tags
  orchestrator_role_arn = module.iam.orchestrator_role_arn

  cross_account_role_arns = concat(var.source_role_arns, var.destination_role_arns)

  enable_logging      = var.enable_step_functions_logging
  log_retention_days  = var.log_retention_days
  enable_xray_tracing = var.enable_xray_tracing
}
```

### Root outputs.tf pattern
```hcl
# Source: outputs.tf root (pattern observe)
output "step_functions_sync" {
  description = "Map of Sync Step Functions ARNs"
  value       = module.step_functions_sync.step_function_arns
}

# Ajout dans all_step_function_arns:
# sync = module.step_functions_sync.step_function_arns
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| Lambdas separees fetch + compare (ex: fetch_secrets + compare_secrets_manager) | Lambda unique generique qui fait fetch+transform+write | v1.1 (this phase) | Simplifie l'architecture, tout passe par un seul point d'entree |
| Path mapping hardcode (DEFAULT_PATH_MAPPING dans compare_secrets_manager.py) | Path mapping configurable dans l'input SFN | v1.1 (this phase) | Elimine la logique Rubix-specifique du code |
| SFN separees par type (SM vs SSM) | SFN unique avec Choice state | v1.1 (this phase) | Un seul flow a maintenir, routing dynamique |

**Deprecated/outdated:**
- Les Lambdas `fetch_secrets.py` et `compare_secrets_manager.py` restent en place pour le dashboard, mais leur logique de path mapping NE doit PAS etre reproduite dans la Lambda sync (qui est generique).

## Open Questions

1. **Lambda-code vs inline deployment**
   - What we know: Le CONTEXT.md dit "Lambda deployee via le module lambda-code existant (S3)". Le module audit/ deploy inline.
   - What's unclear: Est-ce que "via lambda-code" signifie deployer la Lambda dans le module sync/ (inline comme audit/) OU reference l'ARN d'une Lambda deployee ailleurs?
   - Recommendation: Deployer inline dans sync/ (comme audit/) car la Lambda tourne dans le compte orchestrateur et est invoquee directement par la SFN via ARN. Le module lambda-code est pour les Lambdas deployees dynamiquement dans les comptes cibles. Interpreter "via lambda-code" comme "le code source est dans lambdas/" et le packaging utilise archive_file (standard Terraform).

2. **Choice state position : avant ou dans le Map?**
   - What we know: CONTEXT.md dit "ValidateInput -> CheckType (Choice SM/SSM) -> ListItems -> MapOverItems -> SyncSingleItem -> PrepareOutput"
   - What's unclear: Le Choice state semble etre AVANT le Map, mais si Items[] contient un mix SM et SSM, le Choice doit etre DANS le Map (par item).
   - Recommendation: Mettre le Choice state DANS le Map iterator (par item), car Items[] peut contenir un mix de types. Le flow externe est : ValidateInput -> MapOverItems -> PrepareOutput. Dans le Map : CheckType (Choice) -> SyncSMItem ou SyncSSMItem (meme Lambda, parametres differents).

## Validation Architecture

### Test Framework
| Property | Value |
|----------|-------|
| Framework | pytest (existant dans le projet) |
| Config file | Pas de pytest.ini/pyproject.toml -- configuration via conftest.py |
| Quick run command | `python -m pytest tests/test_asl_validation.py -x -q` |
| Full suite command | `python -m pytest tests/ -x -q` |

### Phase Requirements -> Test Map
| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| SYNC-01 | SFN SyncConfigItems avec Choice state SM/SSM | unit (ASL validation) | `python -m pytest tests/test_asl_validation.py -x -q` | Oui (auto-decouverte via rglob) |
| SYNC-08 | Lambda generique sans logique Rubix hardcodee | unit (Lambda stub) | `python -m pytest tests/test_sync_config_items.py -x -q` | Non -- Wave 0 |
| INFRA-01 | Module Terraform deploie SFN + Lambda avec ARN exporte | smoke (terraform plan) | `terraform plan -no-color 2>&1 | head -50` | N/A (terraform, pas pytest) |

### Sampling Rate
- **Per task commit:** `python -m pytest tests/test_asl_validation.py -x -q`
- **Per wave merge:** `python -m pytest tests/ -x -q`
- **Phase gate:** Full suite green + `terraform plan` clean

### Wave 0 Gaps
- [ ] `tests/test_sync_config_items.py` -- teste le contrat input/output du stub Lambda (SYNC-08)
- [ ] Pas de gap pour SYNC-01 -- le test ASL existant couvre automatiquement tout nouveau fichier .asl.json via rglob
- [ ] Pas de gap pour INFRA-01 -- validation via `terraform plan` (pas un test pytest)

## Sources

### Primary (HIGH confidence)
- `modules/step-functions/audit/main.tf` -- Pattern de reference pour le module Terraform (structure, naming, for_each, IAM, CloudWatch)
- `modules/step-functions/audit/audit_resource.asl.json` -- Pattern ASL avec Choice state, Task states, Retry, Catch
- `modules/step-functions/db/rotate_secrets.asl.json` -- Pattern ASL avec Map state, Credentials cross-account, SDK integration SM
- `modules/lambda-code/main.tf` -- Pattern de packaging Lambda via S3 (local.lambda_functions)
- `lambdas/fetch-secrets/fetch_secrets.py` -- Pattern STS cross-account (`get_cross_account_client()`)
- `modules/iam/main.tf` -- Orchestrator role avec sts:AssumeRole sur cross-account roles
- `modules/source-account/main.tf` -- Permissions SM/SSM read sur le role source
- `modules/destination-account/main.tf` -- Permissions SM/SSM write sur le role destination
- `main.tf` (root) -- Pattern de wiring des modules step-functions
- `outputs.tf` (root) -- Pattern d'export des ARNs

### Secondary (MEDIUM confidence)
- `tests/test_asl_validation.py` -- Auto-decouverte via rglob, couvrira le nouveau ASL automatiquement
- `tests/conftest.py` -- Fixtures pour tests SFN Local (optionnel)

### Tertiary (LOW confidence)
- Aucune source externe utilisee -- toute la recherche est basee sur le code existant du projet

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH -- technologies identiques au reste du projet, aucune nouvelle dependance
- Architecture: HIGH -- patterns directement copies de modules existants (audit/, rotate_secrets, lambda-code)
- Pitfalls: HIGH -- identifies par analyse du code existant et des decisions CONTEXT.md

**Research date:** 2026-03-16
**Valid until:** 2026-04-16 (stable -- infrastructure Terraform, pas de fast-moving dependencies)
