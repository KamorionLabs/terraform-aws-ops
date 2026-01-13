# Spec: EFS Native Replication Status

## Identifiant
- **ID**: `repl-efs`
- **Domaine**: replication
- **Priorite**: P0 (critique pour migration)
- **Scope**: `INSTANCE` (MI1, MI2, MI3, FR, BENE, INDUS)

## Objectif
Verifier l'etat de la replication EFS native :
- Status de la replication entre source et destination
- Derniere synchronisation et temps de retard
- Comparaison des tailles source/destination
- Comparaison Legacy vs New Horizon

**Note importante**: Rubix utilise la **replication EFS native**, PAS DataSync.

## Architecture

### Composant 1 : Fetch & Store (Step Function)
Recupere l'etat de la replication EFS et stocke en DynamoDB.

- [x] **Step Function** (appels directs AWS SDK avec cross-account)

**Reference existante**: Step Function `check_replication_sync` dans `terraform-aws-refresh/modules/efs/`

### Composant 2 : Compare (Step Function)
Compare les replications Legacy vs New Horizon (optionnel).

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
  "SourceFileSystemId": "fs-xxxxxxxx",
  "DestinationRegion": "eu-central-1",
  "MaxSyncDelayMinutes": 60
}
```

### Compare
```json
{
  "Domain": "string",
  "Instance": "MI1",
  "Environment": "ppd",
  "LegacyStateKey": "mro#mi1-ppd-legacy|repl-efs",
  "NHStateKey": "mro#mi1-ppd-nh|repl-efs"
}
```

## Outputs (payload DynamoDB)

### State (Fetch & Store)
```json
{
  "status": "ok | warning | critical",
  "source": "legacy | nh",
  "summary": {
    "sourceFileSystemId": "fs-xxxxxxxx",
    "destinationFileSystemId": "fs-yyyyyyyy",
    "replicationState": "ENABLED",
    "lastSyncTime": "ISO8601",
    "timeSinceLastSyncMinutes": 15
  },
  "replication": {
    "sourceFileSystemArn": "arn:aws:elasticfilesystem:eu-west-1:xxx:file-system/fs-xxx",
    "sourceFileSystemRegion": "eu-west-1",
    "destinations": [
      {
        "fileSystemId": "fs-yyyyyyyy",
        "region": "eu-central-1",
        "status": "ENABLED",
        "lastReplicatedTimestamp": "ISO8601"
      }
    ]
  },
  "sourceFilesystem": {
    "fileSystemId": "fs-xxxxxxxx",
    "lifeCycleState": "available",
    "sizeInBytes": 1073741824,
    "numberOfMountTargets": 3
  },
  "destinationFilesystem": {
    "fileSystemId": "fs-yyyyyyyy",
    "lifeCycleState": "available",
    "sizeInBytes": 1073741824,
    "numberOfMountTargets": 3
  },
  "sizeComparison": {
    "sourceSizeGB": 1.0,
    "destinationSizeGB": 1.0,
    "differenceGB": 0,
    "differencePercent": 0
  },
  "healthy": true,
  "issues": [],
  "timestamp": "ISO8601"
}
```

### Comparison
```json
{
  "status": "synced | differs",
  "summary": {
    "replicationStatus": "synced | differs",
    "syncDelay": "synced | differs",
    "filesystemSizes": "synced | differs"
  },
  "replicationComparison": {
    "legacy": {
      "status": "ENABLED",
      "lastSyncMinutesAgo": 15,
      "sourceRegion": "eu-west-1",
      "destRegion": "eu-central-1"
    },
    "nh": {
      "status": "ENABLED",
      "lastSyncMinutesAgo": 10,
      "sourceRegion": "eu-west-1",
      "destRegion": "eu-central-1"
    }
  },
  "sizeComparison": {
    "legacy": {"sourceSizeGB": 100, "destSizeGB": 100},
    "nh": {"sourceSizeGB": 50, "destSizeGB": 50},
    "expected": true,
    "reason": "NH has less data during migration"
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
    "RoleSessionName": "ops-dashboard-efs-repl-check"
  },
  "ResultPath": "$.Credentials"
}
```

### API Calls
| Service | API Call | Resource ARN pattern |
|---------|----------|---------------------|
| EFS | `DescribeReplicationConfigurations` | `arn:aws:elasticfilesystem:*:*:file-system/*` |
| EFS | `DescribeFileSystems` | `arn:aws:elasticfilesystem:*:*:file-system/*` |
| EFS | `DescribeMountTargets` | `arn:aws:elasticfilesystem:*:*:file-system/*` |

## Logique metier

### Fetch & Store
1. Assume role cross-account si necessaire
2. Appeler `efs:DescribeReplicationConfigurations` pour le filesystem source
3. Verifier que la replication est ENABLED
4. Recuperer le timestamp de derniere replication
5. Appeler `efs:DescribeFileSystems` pour source ET destination
6. Comparer les tailles pour detecter des ecarts
7. Calculer le temps depuis derniere sync
8. Sauvegarder en DynamoDB

### Compare
1. Recuperer state Legacy depuis DynamoDB
2. Recuperer state NH depuis DynamoDB
3. Comparer les status de replication
4. Comparer les delais de synchronisation
5. Comparer les tailles relatives
6. Calculer le status global

## Conditions de succes (status: ok)
- [x] Replication status = ENABLED
- [x] Les deux filesystems en LifeCycleState = available
- [x] Temps depuis derniere sync < MaxSyncDelayMinutes
- [x] Mount targets disponibles des deux cotes
- [x] Difference de taille < 5%

## Conditions d'alerte (status: warning)
- [x] Temps depuis derniere sync entre 50-100% du MaxSyncDelayMinutes
- [x] Difference de taille entre 5-10%
- [x] Filesystem en etat "updating"
- [x] Replication en etat "PAUSING" ou "PAUSED"

## Conditions d'erreur (status: critical)
- [x] Replication status = ERROR ou DELETING
- [x] Filesystem en etat "error" ou "deleting"
- [x] Temps depuis derniere sync > MaxSyncDelayMinutes
- [x] Aucune configuration de replication trouvee
- [x] Difference de taille > 10%

## Dependances
- Prerequis: Replication EFS native configuree
- Services AWS: EFS
- Permissions IAM (dans le role cross-account):
  - `elasticfilesystem:DescribeReplicationConfigurations`
  - `elasticfilesystem:DescribeFileSystems`
  - `elasticfilesystem:DescribeMountTargets`

## Mapping Comptes AWS

| Instance | Env | Legacy Account | NH Account |
|----------|-----|----------------|------------|
| MI1/MI2/MI3 | stg | 073290922796 | 281127105461 |
| MI1/MI2/MI3 | ppd | 073290922796 | 287223952330 |
| MI1/MI2/MI3 | prd | 073290922796 | 366483377530 |
| FR | * | 073290922796 (eu-west-3) | selon env |

## Mapping Replications

| Instance | Source Region | Dest Region | Purpose |
|----------|---------------|-------------|---------|
| MI1 | eu-west-1 | eu-central-1 | DR |
| MI2 | eu-west-1 | eu-central-1 | DR |
| FR | eu-west-3 | eu-central-1 | DR |

## RPO et Seuils

| Metrique | Nominal | Warning | Critical |
|----------|---------|---------|----------|
| Sync delay | < 30 min | 30-60 min | > 60 min |
| Size diff | < 2% | 2-5% | > 5% |

## Reference Step Function existante

La Step Function `check_replication_sync` dans `terraform-aws-refresh/modules/efs/` peut etre reutilisee :
- Structure ASL quasi identique
- Adapter les outputs pour le schema DynamoDB du dashboard
- Ajouter la sauvegarde en DynamoDB

## Notes
- **IMPORTANT**: La replication EFS utilise la fonctionnalite native d'EFS, PAS DataSync
- La replication EFS est asynchrone avec un RPO typique de 15 minutes
- Le script `scripts/check-efs-replications.py` contient deja la logique de verification
- Verifier aussi que les mount targets sont dans les bonnes AZ
- La taille peut differer temporairement pendant la replication initiale
