# Spec: Kubernetes Secrets Synchronization

## Identifiant
- **ID**: `k8s-secrets`
- **Domaine**: kubernetes
- **Priorite**: P0 (critique)
- **Scope**: `INSTANCE` (MI1, MI2, MI3, FR, BENE, INDUS)

## Objectif
Verifier la synchronisation des secrets via External Secrets Operator :
- Status sync des ExternalSecrets
- Derniere mise a jour et delai
- Erreurs de synchronisation
- Comparaison Legacy vs New Horizon (memes secrets synchronises)

## Architecture

### Composant 1 : Fetch & Store (Lambda)
Recupere l'etat des ExternalSecrets et stocke en DynamoDB.

- [x] **Lambda** (parsing CRD ExternalSecret complexe)

Note: Lambda necessaire car les CRDs External Secrets ont une structure complexe.

### Composant 2 : Compare (Step Function)
Compare les secrets Legacy vs New Horizon.

- [x] **Step Function** (lecture DynamoDB + comparaison)

## Inputs

### Fetch & Store
```json
{
  "Domain": "string - domaine metier (mro, webshop)",
  "Target": "string - environnement cible (mi1-ppd-legacy, mi1-ppd-nh)",
  "Instance": "MI1 | MI2 | MI3 | FR | BENE | INDUS",
  "Environment": "stg | ppd | prd",
  "Source": "legacy | nh",
  "CrossAccountRoleArn": "arn:aws:iam::{account}:role/ops-dashboard-read",
  "ClusterName": "string - nom du cluster EKS",
  "Namespace": "string - namespace K8s (hybris)",
  "ExpectedSecrets": ["hybris-secrets", "db-credentials", "api-keys"]
}
```

### Compare
```json
{
  "Domain": "string",
  "Instance": "MI1",
  "Environment": "ppd",
  "LegacyStateKey": "mro#mi1-ppd-legacy|k8s-secrets",
  "NHStateKey": "mro#mi1-ppd-nh|k8s-secrets"
}
```

## Outputs (payload DynamoDB)

### State (Fetch & Store)
```json
{
  "status": "ok | warning | critical",
  "source": "legacy | nh",
  "summary": {
    "total": 10,
    "synced": 9,
    "failed": 1,
    "stale": 0,
    "byProvider": {
      "aws-secrets-manager": 8,
      "aws-parameter-store": 2
    }
  },
  "externalSecrets": [
    {
      "name": "hybris-secrets",
      "namespace": "hybris",
      "secretStore": "aws-secrets-manager",
      "secretStoreKind": "ClusterSecretStore",
      "status": "SecretSynced",
      "lastSyncTime": "ISO8601",
      "refreshInterval": "1h",
      "targetSecret": "hybris-secrets",
      "conditions": {
        "Ready": true,
        "message": "Secret was synced"
      },
      "dataKeys": ["DB_PASSWORD", "API_KEY", "ENCRYPTION_KEY"],
      "sourceRef": {
        "kind": "SecretStore",
        "name": "aws-secrets-manager"
      }
    }
  ],
  "secretStores": [
    {
      "name": "aws-secrets-manager",
      "kind": "ClusterSecretStore",
      "status": "Valid",
      "provider": "aws",
      "region": "eu-central-1",
      "conditions": {
        "Ready": true
      }
    }
  ],
  "issues": [
    {
      "name": "db-credentials",
      "type": "ExternalSecret",
      "error": "secret not found in AWS Secrets Manager",
      "lastAttempt": "ISO8601"
    }
  ],
  "healthy": false,
  "timestamp": "ISO8601"
}
```

### Comparison
```json
{
  "status": "synced | differs",
  "summary": {
    "secretCount": "synced | differs",
    "syncStatus": "synced | differs",
    "providers": "synced | differs"
  },
  "secretsComparison": {
    "sameSecrets": ["hybris-secrets", "api-keys"],
    "differentConfig": [
      {
        "secret": "db-credentials",
        "legacy": {"provider": "vault", "path": "/secret/db"},
        "nh": {"provider": "aws-secrets-manager", "path": "hybris/db-credentials"},
        "expected": true,
        "reason": "NH migrated from Vault to AWS Secrets Manager"
      }
    ],
    "onlyLegacy": [],
    "onlyNH": ["nh-specific-secret"]
  },
  "syncStatusComparison": {
    "legacy": {"synced": 9, "failed": 0, "stale": 1},
    "nh": {"synced": 10, "failed": 0, "stale": 0},
    "status": "synced"
  },
  "storeComparison": {
    "legacy": ["vault-store"],
    "nh": ["aws-secrets-manager", "aws-parameter-store"],
    "expected": true,
    "reason": "NH uses native AWS secret stores"
  },
  "issues": [],
  "timestamp": "ISO8601"
}
```

## Appels AWS necessaires

### Cross-account
```json
{
  "Type": "Task",
  "Resource": "arn:aws:states:::aws-sdk:sts:assumeRole",
  "Parameters": {
    "RoleArn.$": "$.CrossAccountRoleArn",
    "RoleSessionName": "ops-dashboard-secrets-check"
  },
  "ResultPath": "$.Credentials"
}
```

### API Calls
| Service | API Call | Path |
|---------|----------|------|
| EKS | `DescribeCluster` | - |
| EKS | `eks:call` | GET /apis/external-secrets.io/v1beta1/namespaces/{ns}/externalsecrets |
| EKS | `eks:call` | GET /apis/external-secrets.io/v1beta1/secretstores |
| EKS | `eks:call` | GET /apis/external-secrets.io/v1beta1/clustersecretstores |

## Logique metier

### Fetch & Store
1. Assume role cross-account si necessaire
2. Appeler `eks:DescribeCluster` pour recuperer endpoint et certificat
3. Lister les ExternalSecrets du namespace
4. Lister les SecretStores et ClusterSecretStores
5. Pour chaque ExternalSecret:
   - Verifier status.conditions[Ready] = True
   - Verifier lastSyncTime < refreshInterval
   - Extraire les cles de donnees synchronisees
   - Identifier les erreurs
6. Pour chaque SecretStore:
   - Verifier status = Valid
   - Identifier le provider (AWS SM, SSM, Vault)
7. Comparer avec ExpectedSecrets si fourni
8. Sauvegarder en DynamoDB

### Compare
1. Recuperer state Legacy depuis DynamoDB
2. Recuperer state NH depuis DynamoDB
3. Comparer les ExternalSecrets par nom:
   - Memes secrets presents ?
   - Memes providers utilises ?
4. Comparer les status de sync
5. Identifier les changements de provider (Vault -> AWS SM)
6. Calculer le status global

## Conditions de succes (status: ok)
- [x] Tous les ExternalSecrets en status SecretSynced
- [x] Tous les SecretStores en status Valid
- [x] lastSyncTime recent (< 2x refreshInterval)
- [x] Tous les ExpectedSecrets presents et synced

## Conditions d'alerte (status: warning)
- [x] ExternalSecret avec lastSyncTime > refreshInterval
- [x] SecretStore en status Degraded
- [x] Secret manquant dans ExpectedSecrets

## Conditions d'erreur (status: critical)
- [x] ExternalSecret avec condition Ready=False
- [x] SecretStore en status Invalid
- [x] Secret source introuvable dans le provider
- [x] Erreur de permission sur le provider

## Dependances
- Prerequis: `infra-eks`, External Secrets Operator installe
- Services AWS: EKS
- Permissions IAM (dans le role cross-account):
  - `eks:DescribeCluster`
- Kubernetes RBAC: `get`, `list` sur externalsecrets, secretstores, clustersecretstores

## Mapping Comptes AWS

| Instance | Env | Legacy Account | NH Account |
|----------|-----|----------------|------------|
| MI1/MI2/MI3 | stg | 073290922796 | 281127105461 |
| MI1/MI2/MI3 | ppd | 073290922796 | 287223952330 |
| MI1/MI2/MI3 | prd | 073290922796 | 366483377530 |
| FR | * | 073290922796 (eu-west-3) | selon env |

## SecretStores attendus

| Store | Kind | Provider | Legacy | NH |
|-------|------|----------|--------|-----|
| vault-store | ClusterSecretStore | HashiCorp Vault | Yes | No |
| aws-secrets-manager | ClusterSecretStore | AWS SM | No | Yes |
| aws-parameter-store | ClusterSecretStore | AWS SSM | No | Yes |

## ExternalSecrets attendus par application

| Application | Secret Name | Provider | Keys |
|-------------|-------------|----------|------|
| hybris | hybris-secrets | AWS SM | DB_*, API_*, ENCRYPTION_* |
| hybris | hybris-db-credentials | AWS SM | username, password, host |
| apache | apache-tls | AWS SM | tls.crt, tls.key |
| solr | solr-credentials | AWS SM | admin_password |

## Lambda (necessaire)

Le parsing des CRDs External Secrets est complexe :

```python
def lambda_handler(event, context):
    external_secrets = event.get('externalSecrets', [])
    secret_stores = event.get('secretStores', [])
    expected_secrets = event.get('expectedSecrets', [])

    summary = {
        'total': 0, 'synced': 0, 'failed': 0, 'stale': 0,
        'byProvider': {}
    }
    processed_secrets = []
    issues = []

    for es in external_secrets:
        summary['total'] += 1
        name = es['metadata']['name']

        # Check conditions
        conditions = {c['type']: c for c in es.get('status', {}).get('conditions', [])}
        ready = conditions.get('Ready', {})

        if ready.get('status') == 'True':
            summary['synced'] += 1
            status = 'SecretSynced'
        else:
            summary['failed'] += 1
            status = 'Failed'
            issues.append({
                'name': name,
                'type': 'ExternalSecret',
                'error': ready.get('message', 'Unknown error')
            })

        # Extract store info
        store_ref = es['spec'].get('secretStoreRef', {})
        store_name = store_ref.get('name', 'unknown')

        # Count by provider
        summary['byProvider'][store_name] = summary['byProvider'].get(store_name, 0) + 1

        processed_secrets.append({
            'name': name,
            'secretStore': store_name,
            'status': status,
            'lastSyncTime': es.get('status', {}).get('syncedResourceVersion'),
            'refreshInterval': es['spec'].get('refreshInterval', '1h'),
            'conditions': {'Ready': ready.get('status') == 'True'}
        })

    # Check expected secrets
    found_secrets = {s['name'] for s in processed_secrets}
    for expected in expected_secrets:
        if expected not in found_secrets:
            issues.append({
                'name': expected,
                'type': 'ExpectedSecret',
                'error': 'Secret not found'
            })

    # Determine status
    status = 'ok'
    if summary['failed'] > 0 or len(issues) > 0:
        status = 'critical'
    elif summary['stale'] > 0:
        status = 'warning'

    return {
        'status': status,
        'summary': summary,
        'externalSecrets': processed_secrets,
        'secretStores': process_stores(secret_stores),
        'issues': issues,
        'healthy': status == 'ok'
    }
```

## Notes
- External Secrets utilise des CRDs custom (external-secrets.io/v1beta1)
- Verifier aussi les SecretStores au niveau cluster
- Les erreurs de sync peuvent venir du provider (AWS Secrets Manager, Vault, etc.)
- La migration Vault -> AWS SM est attendue entre Legacy et NH
- Les refreshInterval peuvent varier selon la criticite du secret
