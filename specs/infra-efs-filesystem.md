# Spec: EFS Filesystem Status

## Identifiant
- **ID**: `infra-efs`
- **Domaine**: infrastructure
- **Priorite**: P0 (critique)
- **Scope**: `INSTANCE` (MI1, MI2, MI3, FR, BENE, INDUS)

## Objectif
Verifier l'etat de sante d'un filesystem EFS :
- Statut et disponibilite du filesystem
- Mount targets par AZ
- Throughput mode et performance
- Comparaison Legacy vs New Horizon (configuration)

## Architecture

### Composant 1 : Fetch & Store (Step Function)
Recupere l'etat du filesystem EFS et stocke en DynamoDB.

- [x] **Step Function** (appels directs AWS SDK avec cross-account)

### Composant 2 : Compare (Step Function)
Compare les filesystems Legacy vs New Horizon.

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
  "FileSystemId": "fs-xxxxxxxx"
}
```

### Compare
```json
{
  "Domain": "string",
  "Instance": "MI1",
  "Environment": "ppd",
  "LegacyStateKey": "mro#mi1-ppd-legacy|infra-efs",
  "NHStateKey": "mro#mi1-ppd-nh|infra-efs"
}
```

## Outputs (payload DynamoDB)

### State (Fetch & Store)
```json
{
  "status": "ok | warning | critical",
  "source": "legacy | nh",
  "filesystem": {
    "id": "fs-xxxxxxxx",
    "name": "hybris-media-mi1",
    "lifeCycleState": "available",
    "numberOfMountTargets": 3,
    "sizeInBytes": {
      "value": 1073741824,
      "valueInIA": 0,
      "valueInStandard": 1073741824
    },
    "performanceMode": "generalPurpose",
    "throughputMode": "elastic",
    "provisionedThroughputInMibps": null,
    "encrypted": true,
    "kmsKeyId": "arn:aws:kms:...",
    "creationTime": "ISO8601"
  },
  "mountTargets": [
    {
      "id": "fsmt-xxxxxxxx",
      "lifeCycleState": "available",
      "subnetId": "subnet-xxxxxxxx",
      "availabilityZone": "eu-central-1a",
      "ipAddress": "10.0.1.100",
      "networkInterfaceId": "eni-xxxxxxxx"
    },
    {
      "id": "fsmt-yyyyyyyy",
      "lifeCycleState": "available",
      "subnetId": "subnet-yyyyyyyy",
      "availabilityZone": "eu-central-1b",
      "ipAddress": "10.0.2.100"
    }
  ],
  "accessPoints": [
    {
      "id": "fsap-xxxxxxxx",
      "name": "hybris-media",
      "lifeCycleState": "available",
      "rootDirectory": "/media",
      "posixUser": {"uid": 1000, "gid": 1000}
    }
  ],
  "metrics": {
    "clientConnections": 5,
    "burstCreditBalance": 2160000000000,
    "percentIOLimit": 0.5
  },
  "healthy": true,
  "timestamp": "ISO8601"
}
```

### Comparison
```json
{
  "status": "synced | differs",
  "summary": {
    "filesystemConfig": "synced | differs",
    "mountTargets": "synced | differs",
    "accessPoints": "synced | differs"
  },
  "configComparison": {
    "throughputMode": {
      "legacy": "bursting",
      "nh": "elastic",
      "expected": true,
      "reason": "NH uses elastic throughput"
    },
    "encrypted": {
      "legacy": true,
      "nh": true,
      "status": "synced"
    }
  },
  "mountTargetsComparison": {
    "legacyAZs": ["eu-central-1a", "eu-central-1b", "eu-central-1c"],
    "nhAZs": ["eu-central-1a", "eu-central-1b", "eu-central-1c"],
    "missingInNH": [],
    "missingInLegacy": []
  },
  "accessPointsComparison": {
    "sameAccessPoints": ["hybris-media"],
    "onlyLegacy": [],
    "onlyNH": ["hybris-imports"]
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
    "RoleSessionName": "ops-dashboard-efs-check"
  },
  "ResultPath": "$.Credentials"
}
```

### API Calls
| Service | API Call | Resource ARN pattern |
|---------|----------|---------------------|
| EFS | `DescribeFileSystems` | `arn:aws:elasticfilesystem:*:*:file-system/*` |
| EFS | `DescribeMountTargets` | `arn:aws:elasticfilesystem:*:*:file-system/*` |
| EFS | `DescribeAccessPoints` | `arn:aws:elasticfilesystem:*:*:access-point/*` |
| CloudWatch | `GetMetricData` | (optionnel) |

## Logique metier

### Fetch & Store
1. Assume role cross-account si necessaire
2. Appeler `efs:DescribeFileSystems` avec le FileSystemId
3. Verifier le lifeCycleState du filesystem
4. Appeler `efs:DescribeMountTargets` pour le filesystem
5. Verifier que tous les mount targets sont available
6. Appeler `efs:DescribeAccessPoints`
7. Optionnel: Recuperer metriques CloudWatch (burst credits, IO limit)
8. Sauvegarder en DynamoDB

### Compare
1. Recuperer state Legacy depuis DynamoDB
2. Recuperer state NH depuis DynamoDB
3. Comparer la configuration (throughput mode, encryption)
4. Comparer les mount targets par AZ
5. Comparer les access points
6. Identifier les differences inattendues
7. Calculer le status global

## Conditions de succes (status: ok)
- [x] Filesystem lifeCycleState = available
- [x] Tous les mount targets en lifeCycleState = available
- [x] Au moins 1 mount target par AZ cible
- [x] Burst credits > 50% (si mode bursting)

## Conditions d'alerte (status: warning)
- [x] Filesystem ou mount target en status creating/updating
- [x] Burst credits entre 20-50%
- [x] PercentIOLimit > 80%
- [x] Mount targets < nombre d'AZ attendues

## Conditions d'erreur (status: critical)
- [x] Filesystem lifeCycleState != available
- [x] Mount target en status deleted/deleting/error
- [x] Aucun mount target disponible
- [x] Burst credits < 20%
- [x] PercentIOLimit > 95%

## Dependances
- Services AWS: EFS, CloudWatch (optionnel)
- Permissions IAM (dans le role cross-account):
  - `elasticfilesystem:DescribeFileSystems`
  - `elasticfilesystem:DescribeMountTargets`
  - `elasticfilesystem:DescribeAccessPoints`
  - `cloudwatch:GetMetricData` (optionnel)

## Mapping Comptes AWS

| Instance | Env | Legacy Account | NH Account |
|----------|-----|----------------|------------|
| MI1/MI2/MI3 | stg | 073290922796 | 281127105461 |
| MI1/MI2/MI3 | ppd | 073290922796 | 287223952330 |
| MI1/MI2/MI3 | prd | 073290922796 | 366483377530 |
| FR | * | 073290922796 (eu-west-3) | selon env |

## Mapping Filesystems

| Instance | Env | Legacy EFS | NH EFS |
|----------|-----|------------|--------|
| MI1 | ppd | fs-legacy-mi1 | fs-nh-mi1 |
| MI2 | ppd | fs-legacy-mi2 | fs-nh-mi2 |
| FR | ppd | fs-legacy-fr | fs-nh-fr |

## Configuration attendue

| Parametre | Legacy | NH | Note |
|-----------|--------|-----|------|
| throughputMode | bursting | elastic | NH upgraded |
| encrypted | true | true | Must match |
| performanceMode | generalPurpose | generalPurpose | Must match |

## Notes
- Le check de replication EFS est separe (voir `repl-efs-sync.md`)
- Pour verifier l'accessibilite depuis les pods K8s, combiner avec `k8s-pvc`
- Les metriques CloudWatch EFS ont un delai de ~1 minute
- NH utilise souvent le mode elastic pour meilleure performance
