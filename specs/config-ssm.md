# Spec: SSM Parameter Store Comparison

## Identifiant
- **ID**: `config-ssm`
- **Domaine**: config
- **Priorite**: P1 (important pour migration)
- **Scope**: `INSTANCE` (MI1, MI2, MI3, FR, BENE, INDUS)

## Objectif
Verifier et comparer les parametres SSM Parameter Store entre Legacy et New Horizon :
- Existence des parametres requis
- Coherence des valeurs (avec distinction des differences attendues vs drift)
- Detection des parametres manquants ou inattendus

**Reference**: Pattern base sur `NewHorizon-IaC-Webshop/scripts/migrate-secrets/migrate_secrets.py`

## Architecture

### Composant 1 : Fetch & Store (Lambda)
Recupere les parametres SSM et stocke en DynamoDB.

- [x] **Lambda** (multi-account AWS, logique de mapping)

### Composant 2 : Compare (Lambda)
Compare Legacy vs New Horizon avec detection des differences attendues.

- [x] **Lambda** (logique de diff complexe avec transformations)

## Inputs

### Fetch & Store
```json
{
  "Domain": "string",
  "Target": "string (ex: mi1-ppd-legacy, mi1-ppd-nh)",
  "Instance": "MI1 | MI2 | MI3 | FR | BENE | INDUS",
  "Environment": "stg | ppd | prd",
  "Source": "legacy | nh",
  "AwsProfile": "iph | digital-webshop-preprod/AWSAdministratorAccess",
  "Region": "eu-central-1 | eu-west-3",
  "ParameterPaths": [
    "/rubix/{instance}/{env}/",
    "/hybris/{instance}/{env}/"
  ],
  "MappingConfig": {
    "source_prefix": "/rubix/mi1/preprod/",
    "dest_prefix": "/rubix/ppd/mi1/",
    "transformations": {
      "rds/endpoint": {
        "type": "replace",
        "pattern": "rubix-nonprod-aurora",
        "replacement": "rubix-dig-ppd-aurora"
      },
      "eks/cluster-name": {
        "type": "replace",
        "pattern": "rubix-nonprod",
        "replacement": "rubix-dig-ppd-webshop"
      }
    },
    "expected_new_keys": ["eks/oidc-provider-arn", "irsa/role-arn"],
    "expected_removed_keys": ["legacy/deprecated-setting"]
  }
}
```

### Compare
```json
{
  "Domain": "string",
  "Instance": "MI1",
  "Environment": "ppd",
  "LegacyStateKey": "mro#mi1-ppd-legacy|config-ssm",
  "NHStateKey": "mro#mi1-ppd-nh|config-ssm"
}
```

## Outputs (payload DynamoDB)

### State (Fetch & Store)
```json
{
  "status": "ok | warning | critical",
  "source": "legacy | nh",
  "summary": {
    "totalParameters": 85,
    "stringParams": 60,
    "secureStringParams": 25,
    "byPath": {
      "/rubix/mi1/preprod/": 45,
      "/hybris/mi1/preprod/": 40
    }
  },
  "parameters": {
    "/rubix/mi1/preprod/eks/cluster-name": {
      "type": "String",
      "version": 3,
      "lastModified": "ISO8601",
      "valueHash": "sha256:xxxx"
    }
  },
  "parametersList": ["liste des noms pour comparaison"],
  "timestamp": "ISO8601"
}
```

### Comparison
```json
{
  "status": "synced | differs | only_legacy | only_nh",
  "summary": {
    "total": 85,
    "synced": 70,
    "differs_expected": 10,
    "differs_unexpected": 2,
    "only_legacy_expected": 1,
    "only_legacy_unexpected": 1,
    "only_nh_expected": 2,
    "only_nh_unexpected": 0
  },
  "details": {
    "synced": ["param1", "param2"],
    "expected_differences": [
      {
        "legacyKey": "/rubix/mi1/preprod/eks/cluster-name",
        "nhKey": "/rubix/ppd/mi1/eks/cluster-name",
        "legacyValueHash": "sha256:xxx",
        "nhValueHash": "sha256:yyy",
        "transformation": "replace: rubix-nonprod -> rubix-dig-ppd-webshop"
      }
    ],
    "unexpected_differences": [
      {
        "legacyKey": "/rubix/mi1/preprod/api/timeout",
        "nhKey": "/rubix/ppd/mi1/api/timeout",
        "legacyValueHash": "sha256:xxx",
        "nhValueHash": "sha256:yyy",
        "reason": "drift - valeurs differentes sans transformation attendue"
      }
    ],
    "only_legacy_expected": ["liste des cles supprimees intentionnellement"],
    "only_legacy_unexpected": ["liste des cles manquantes a migrer"],
    "only_nh_expected": ["liste des nouvelles cles attendues"],
    "only_nh_unexpected": ["liste des cles inattendues"]
  },
  "issues": [],
  "timestamp": "ISO8601"
}
```

## Appels AWS necessaires
| Service | API Call | Profil |
|---------|----------|--------|
| SSM | `GetParametersByPath` | Legacy ou NH selon source |
| SSM | `GetParameters` | Legacy ou NH selon source |
| DynamoDB | `GetItem`, `PutItem` | ops-dashboard account |

## Logique metier

### Fetch & Store
1. Initialiser le client AWS avec le bon profil (Legacy ou NH)
2. Pour chaque path, recuperer les parametres recursively
3. Ne PAS decrypter les SecureString (hash de la valeur chiffree)
4. Stocker la liste des parametres avec metadata en DynamoDB
5. Calculer le status (erreur si path inaccessible)

### Compare
1. Recuperer le state Legacy depuis DynamoDB
2. Recuperer le state NH depuis DynamoDB
3. Appliquer le mapping des noms de parametres (prefix transformation)
4. Pour chaque parametre Legacy:
   - Trouver le parametre NH correspondant
   - Comparer les valueHash
   - Si different, verifier si la transformation est attendue
5. Identifier les parametres orphelins (only_legacy, only_nh)
6. Categoriser en expected vs unexpected
7. Calculer le status global:
   - `synced` si aucune difference inattendue
   - `differs` si au moins une difference inattendue

## Conditions de succes
- [x] Aucune difference inattendue
- [x] Tous les parametres requis presents dans NH
- [x] Differences attendues correspondent aux transformations configurees

## Conditions d'alerte
- [x] Parametres `only_legacy_unexpected` (a migrer)
- [x] Parametres `only_nh_unexpected` (a verifier)
- [x] Parametre non modifie depuis > 180 jours

## Conditions d'erreur
- [x] Differences inattendues (`differs_unexpected > 0`)
- [x] Parametres critiques manquants
- [x] Path inaccessible (permissions)

## Dependances
- Services AWS: SSM Parameter Store (multi-account)
- Permissions IAM:
  - Account Legacy (073290922796): `ssm:GetParametersByPath`, `ssm:GetParameters`
  - Account NH (selon env): `ssm:GetParametersByPath`, `ssm:GetParameters`
  - **Note**: `WithDecryption: false` pour eviter d'exposer les valeurs

## Mapping Comptes AWS

| Instance | Env | Legacy Account | Legacy Profile | NH Account | NH Profile |
|----------|-----|----------------|----------------|------------|------------|
| MI1/MI2/MI3 | stg | 073290922796 | iph | 281127105461 | digital-webshop-staging/... |
| MI1/MI2/MI3 | ppd | 073290922796 | iph | 287223952330 | digital-webshop-preprod/... |
| MI1/MI2/MI3 | prd | 073290922796 | iph | 366483377530 | digital-webshop-prod/... |
| FR | stg | 073290922796 | iph (eu-west-3) | 281127105461 | digital-webshop-staging/... |

## Mapping des paths (exemple MI1-ppd)

| Legacy Path | NH Path | Transformation |
|-------------|---------|---------------|
| `/rubix/mi1/preprod/` | `/rubix/ppd/mi1/` | prefix renaming |
| `/hybris/mi1/preprod/` | `/hybris/ppd/mi1/` | prefix renaming |

## Transformations attendues

Les differences suivantes sont ATTENDUES et ne doivent PAS etre signalees comme drift:

1. **Endpoints RDS**: `rubix-nonprod-aurora` -> `rubix-dig-ppd-aurora`
2. **Clusters EKS**: `rubix-nonprod` -> `rubix-dig-ppd-webshop`
3. **Hostnames**: `*.rubix-nonprod.internal` -> `*.rubix-dig-ppd.internal`
4. **ARNs**: account ID change (073290922796 -> target account)

## Future: Sync Trigger (Phase 2)

Une fois la comparaison fiable, ajouter la possibilite de declencher une synchronisation:

```json
{
  "Action": "sync",
  "DryRun": true,
  "Instance": "MI1",
  "Environment": "ppd",
  "OnlyMissing": true,
  "ApplyTransformations": true
}
```

## Notes
- Ne JAMAIS logger ou stocker les valeurs des secrets
- Le hash permet de comparer sans exposer les donnees
- La config de mapping doit etre maintenue par instance/env
- Reutiliser la logique de `migrate_secrets.py --diff`
