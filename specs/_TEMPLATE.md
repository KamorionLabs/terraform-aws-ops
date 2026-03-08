# Spec: [NOM_DU_CHECK]

## Identifiant
- **ID**: `check-xxx`
- **Domaine**: infrastructure | replication | kubernetes | network | cost | cicd | config | repo
- **Priorite**: P0 (critique) | P1 (important) | P2 (nice-to-have)
- **Scope**: `INSTANCE` (MI1, MI2, MI3, FR, BENE, INDUS) | `COUNTRY` (FR, DE) | `GLOBAL` (shared/management)

## Objectif
[Description claire de ce que le check doit verifier]

## Architecture

### Composant 1 : Fetch & Store
Recupere les donnees et stocke en DynamoDB.

- [ ] **Step Function** (appels directs AWS SDK)
- [ ] **Lambda** (logique complexe / API externe)

### Composant 2 : Compare (optionnel)
Compare Legacy vs New Horizon.

- [ ] **Step Function** (lecture DynamoDB + comparaison)
- [ ] **Lambda** (logique de diff complexe)

## Inputs

### Fetch & Store
```json
{
  "Domain": "string - domaine metier (mro, webshop, etc.)",
  "Target": "string - environnement cible (mi2-preprod, prod, etc.)",
  "Instance": "string - instance (MI1, MI2, MI3, FR, BENE, INDUS)",
  "Environment": "string - env (int, stg, ppd, prd)",
  "CrossAccountRoleArn": "arn:aws:iam::{account}:role/ops-dashboard-read-role",
  // ... autres inputs specifiques
}
```

### Compare (si applicable)
```json
{
  "Domain": "string",
  "Target": "string",
  "LegacyKey": "pk#sk pour recuperer state legacy",
  "NHKey": "pk#sk pour recuperer state new horizon"
}
```

## Outputs (payload DynamoDB)

### State (Fetch & Store)
```json
{
  "status": "ok | warning | critical | unknown",
  "source": "legacy | nh",
  "checks": {
    // details des verifications
  },
  "metrics": {
    // metriques optionnelles
  },
  "timestamp": "ISO8601"
}
```

### Comparison (si applicable)
```json
{
  "status": "synced | differs | only_legacy | only_nh",
  "summary": {
    "total": 10,
    "synced": 8,
    "differs_expected": 1,
    "differs_unexpected": 1
  },
  "differences": [
    {
      "key": "...",
      "legacy": "...",
      "nh": "...",
      "expected": true
    }
  ],
  "timestamp": "ISO8601"
}
```

## Appels AWS necessaires

### Cross-account (Step Function)
```json
{
  "Type": "Task",
  "Resource": "arn:aws:states:::aws-sdk:sts:assumeRole",
  "Parameters": {
    "RoleArn.$": "$.CrossAccountRoleArn",
    "RoleSessionName": "ops-dashboard-check"
  },
  "ResultPath": "$.Credentials"
}
```

### API Calls
| Service | API Call | Resource ARN pattern |
|---------|----------|---------------------|
| ... | ... | ... |

## Logique metier

### Fetch & Store
1. Assume role cross-account si necessaire
2. [Etape 2]
3. ...
4. Calculer status global
5. Sauvegarder en DynamoDB via Lambda save-state

### Compare (si applicable)
1. Lire state Legacy depuis DynamoDB
2. Lire state NH depuis DynamoDB
3. Comparer les payloads
4. Categoriser les differences (expected vs unexpected)
5. Sauvegarder le resultat de comparaison

## Conditions de succes
- [ ] Condition 1
- [ ] Condition 2

## Conditions d'alerte (warning)
- [ ] Condition 1

## Conditions d'erreur (critical)
- [ ] Condition 1

## Dependances
- Autres checks requis: [aucune | liste]
- Services AWS: [liste]
- Permissions IAM (dans le role cross-account):
  - `sts:AssumeRole` sur le role cible
  - [autres permissions]

## Mapping Comptes AWS

| Instance/Env | AWS Account | Role ARN |
|--------------|-------------|----------|
| MI1-stg | 281127105461 | arn:aws:iam::281127105461:role/ops-dashboard-read |
| MI1-ppd | 287223952330 | arn:aws:iam::287223952330:role/ops-dashboard-read |
| MI1-prd | 366483377530 | arn:aws:iam::366483377530:role/ops-dashboard-read |
| Legacy | 073290922796 | arn:aws:iam::073290922796:role/ops-dashboard-read |
| shared-services | 025922408720 | arn:aws:iam::025922408720:role/ops-dashboard-read |

## Notes
[Informations supplementaires, edge cases, etc.]
