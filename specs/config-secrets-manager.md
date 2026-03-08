# Spec: AWS Secrets Manager Comparison

## Identifiant
- **ID**: `config-sm`
- **Domaine**: config
- **Priorite**: P1 (important pour migration)
- **Scope**: `INSTANCE` (MI1, MI2, MI3, FR, BENE, INDUS)

## Objectif
Verifier et comparer les secrets AWS Secrets Manager entre Legacy et New Horizon :
- Existence des secrets requis
- Coherence des valeurs JSON (avec distinction des differences attendues vs drift)
- Verification des cles individuelles dans les secrets JSON
- Detection des secrets manquants ou inattendus

**Reference**: Pattern base sur `NewHorizon-IaC-Webshop/scripts/migrate-secrets/migrate_secrets.py`

## Architecture

### Composant 1 : Fetch & Store (Lambda)
Recupere les secrets et stocke en DynamoDB (sans les valeurs sensibles).

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
  "SecretPatterns": ["rubix/*", "hybris/*"],
  "MappingConfig": {
    "source_prefix": "rubix/mi1/preprod/",
    "dest_prefix": "rubix/ppd/mi1/",
    "transformations": {
      "hybris/db-credentials": {
        "keys": {
          "host": {
            "type": "replace",
            "pattern": "rubix-nonprod-aurora.cluster-xxx.eu-central-1.rds.amazonaws.com",
            "replacement": "rubix-dig-ppd-aurora.cluster-yyy.eu-central-1.rds.amazonaws.com"
          },
          "username": {"type": "keep"},
          "password": {"type": "ignore_diff"}
        }
      }
    },
    "expected_new_keys": {"hybris/db-credentials": ["connection_pool_size"]},
    "expected_removed_keys": {"hybris/db-credentials": ["legacy_flag"]}
  }
}
```

### Compare
```json
{
  "Domain": "string",
  "Instance": "MI1",
  "Environment": "ppd",
  "LegacyStateKey": "mro#mi1-ppd-legacy|config-sm",
  "NHStateKey": "mro#mi1-ppd-nh|config-sm"
}
```

## Outputs (payload DynamoDB)

### State (Fetch & Store)
```json
{
  "status": "ok | warning | critical",
  "source": "legacy | nh",
  "summary": {
    "totalSecrets": 35,
    "rotationEnabled": 10,
    "recentlyAccessed": 30
  },
  "secrets": {
    "rubix/mi1/preprod/hybris/db-credentials": {
      "arn": "arn:aws:secretsmanager:...",
      "createdDate": "ISO8601",
      "lastChangedDate": "ISO8601",
      "lastAccessedDate": "ISO8601",
      "rotationEnabled": true,
      "versionStages": ["AWSCURRENT"],
      "keysList": ["host", "port", "username", "password", "database"],
      "keysHash": "sha256:xxxx"
    }
  },
  "secretsList": ["liste des noms pour comparaison"],
  "timestamp": "ISO8601"
}
```

### Comparison
```json
{
  "status": "synced | differs | only_legacy | only_nh",
  "summary": {
    "totalSecrets": 35,
    "synced": 28,
    "differs_expected": 5,
    "differs_unexpected": 1,
    "only_legacy_expected": 0,
    "only_legacy_unexpected": 1,
    "only_nh_expected": 1,
    "only_nh_unexpected": 0
  },
  "details": {
    "synced": [
      {
        "legacySecret": "rubix/mi1/preprod/api/jwt-secret",
        "nhSecret": "rubix/ppd/mi1/api/jwt-secret",
        "keysCount": 2,
        "allKeysIdentical": true
      }
    ],
    "expected_differences": [
      {
        "legacySecret": "rubix/mi1/preprod/hybris/db-credentials",
        "nhSecret": "rubix/ppd/mi1/hybris/db-credentials",
        "sameKeys": ["port", "username", "database"],
        "expectedDiffKeys": ["host"],
        "expectedNewKeys": ["connection_pool_size"],
        "expectedRemovedKeys": ["legacy_flag"],
        "transformationApplied": "host: endpoint replacement"
      }
    ],
    "unexpected_differences": [
      {
        "legacySecret": "rubix/mi1/preprod/hybris/solr-credentials",
        "nhSecret": "rubix/ppd/mi1/hybris/solr-credentials",
        "unexpectedDiffKeys": ["api_key"],
        "reason": "drift - valeur modifiee sans transformation attendue"
      }
    ],
    "only_legacy_unexpected": [
      {
        "secret": "rubix/mi1/preprod/legacy/deprecated-service",
        "reason": "Secret a migrer ou a supprimer intentionnellement"
      }
    ],
    "only_nh_expected": [
      {
        "secret": "rubix/ppd/mi1/new-feature/credentials",
        "reason": "Nouveau service NH uniquement"
      }
    ]
  },
  "issues": [],
  "timestamp": "ISO8601"
}
```

## Appels AWS necessaires
| Service | API Call | Profil | Note |
|---------|----------|--------|------|
| Secrets Manager | `ListSecrets` | Legacy ou NH | Liste des secrets |
| Secrets Manager | `DescribeSecret` | Legacy ou NH | Metadata sans valeur |
| Secrets Manager | `GetSecretValue` | Legacy ou NH | Pour hash des cles JSON |
| DynamoDB | `GetItem`, `PutItem` | ops-dashboard | Stockage state |

## Logique metier

### Fetch & Store
1. Initialiser le client AWS avec le bon profil (Legacy ou NH)
2. Lister tous les secrets correspondant aux patterns
3. Pour chaque secret:
   - Recuperer les metadata (DescribeSecret)
   - Recuperer la valeur (GetSecretValue) pour extraire les cles JSON
   - **Ne PAS stocker les valeurs**, seulement:
     - Liste des cles (`keysList`)
     - Hash des cles triees (`keysHash`)
     - Hash de chaque valeur individuellement si besoin de comparaison fine
4. Stocker en DynamoDB

### Compare
1. Recuperer le state Legacy depuis DynamoDB
2. Recuperer le state NH depuis DynamoDB
3. Appliquer le mapping des noms de secrets
4. Pour chaque secret Legacy:
   - Trouver le secret NH correspondant
   - Comparer les keysList
   - Pour chaque cle, comparer le hash de la valeur
   - Appliquer les transformations attendues
   - Categoriser les differences
5. Identifier les secrets orphelins
6. Calculer le status global

## Conditions de succes
- [x] Aucune difference inattendue (`differs_unexpected = 0`)
- [x] Aucun secret manquant inattendu (`only_legacy_unexpected = 0`)
- [x] Tous les secrets requis presents
- [x] Les differences correspondent aux transformations configurees

## Conditions d'alerte
- [x] Secrets `only_legacy_unexpected` (a migrer)
- [x] Secrets `only_nh_unexpected` (a verifier)
- [x] Rotation disabled pour secrets critiques
- [x] Secret non accede depuis > 90 jours

## Conditions d'erreur
- [x] Differences inattendues detectees
- [x] Secrets critiques manquants
- [x] Rotation en echec
- [x] Secret inaccessible (permissions)

## Dependances
- Services AWS: Secrets Manager (multi-account)
- Permissions IAM:
  - Account Legacy: `secretsmanager:ListSecrets`, `secretsmanager:DescribeSecret`, `secretsmanager:GetSecretValue`
  - Account NH: memes permissions
  - **Important**: GetSecretValue necessaire pour hash des cles JSON

## Mapping Comptes AWS

| Instance | Env | Legacy Account | NH Account |
|----------|-----|----------------|------------|
| MI1/MI2/MI3 | stg | 073290922796 | 281127105461 |
| MI1/MI2/MI3 | ppd | 073290922796 | 287223952330 |
| MI1/MI2/MI3 | prd | 073290922796 | 366483377530 |
| FR | * | 073290922796 (eu-west-3) | selon env |

## Mapping des secrets (exemple MI1-ppd)

| Legacy Secret | NH Secret | Type |
|---------------|-----------|------|
| `rubix/mi1/preprod/hybris/db-credentials` | `rubix/ppd/mi1/hybris/db-credentials` | rename |
| `rubix/mi1/preprod/hybris/solr-credentials` | `rubix/ppd/mi1/hybris/solr-credentials` | rename |
| `rubix/mi1/preprod/api/jwt-secret` | `rubix/ppd/mi1/api/jwt-secret` | rename |

## Transformations attendues par type de secret

### Database credentials (`**/db-credentials`)
- `host`: endpoint RDS change (expected)
- `port`: identique (3306)
- `username`: identique
- `password`: ignore diff (gere par rotation)
- `database`: identique

### Solr credentials (`**/solr-credentials`)
- `host`: endpoint change (expected)
- `port`: identique
- `username`: identique
- `password`: ignore diff

### API secrets (`**/api/*`)
- Generalement identiques sauf si service reconfigure

## Securite

**CRITIQUE**: Les valeurs des secrets ne doivent JAMAIS etre:
- Loggees
- Stockees en DynamoDB
- Affichees dans le dashboard

Seuls les hash (SHA256) des valeurs sont stockes pour comparaison.

## Future: Sync Trigger (Phase 2)

```json
{
  "Action": "sync",
  "DryRun": true,
  "Instance": "MI1",
  "Environment": "ppd",
  "OnlyMissing": true,
  "ApplyTransformations": true,
  "SecretsToSync": ["optional list or all"]
}
```

## Notes
- Reutiliser la logique de `migrate_secrets.py --diff`
- Les cles JSON peuvent varier entre applications (Hybris, API, etc.)
- La config de mapping doit etre validee par l'equipe
- Prevoir un mecanisme de "whitelist" pour les differences acceptees
