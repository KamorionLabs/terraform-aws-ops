# Spec: RDS Cluster Status

## Identifiant
- **ID**: `infra-rds`
- **Domaine**: infrastructure
- **Priorite**: P1
- **Scope**: `INSTANCE` (tout cluster Aurora/RDS)

## Objectif
Verifier l'etat de sante d'un cluster Aurora/RDS :
- Statut du cluster et des instances
- **Parameter Groups** (cluster et instance) - verification des configurations
- Stockage, connexions, et metriques de performance
- Comparaison Source vs Destination avec differences attendues configurables

## Architecture

### Composant 1 : infra-rds-checker (Step Function)
Recupere les infos RDS et stocke en DynamoDB.

- **Step Function** avec appels SDK directs (cross-account)
- Pas de Lambda necessaire

### Composant 2 : infra-rds-compare (Step Function + Lambda)
Compare Source vs Destination avec gestion des differences attendues.

- **Step Function** pour orchestration et lecture DynamoDB
- **Lambda** `compare-rds` pour logique de comparaison

## Inputs

### infra-rds-checker
```json
{
  "Project": "string - projet Dashborion (ex: mro-mi2)",
  "Env": "string - environnement avec prefixe (ex: nh-ppd, legacy-stg)",
  "Instance": "string - instance optionnelle (MI1, MI2, FR...)",
  "Environment": "string - environnement court (stg, ppd, prd)",
  "CrossAccountRoleArn": "arn:aws:iam::{account}:role/ops-dashboard-read",
  "DbClusterIdentifier": "string - identifiant du cluster Aurora/RDS"
}
```

### infra-rds-compare
```json
{
  "Project": "string - projet Dashborion",
  "SourceEnv": "string - environnement source (ex: legacy-ppd)",
  "DestinationEnv": "string - environnement destination (ex: nh-ppd)",
  "SourceStateKey": "project#env|infra:rds - cle DynamoDB source",
  "DestinationStateKey": "project#env|infra:rds - cle DynamoDB destination",
  "ExpectedDifferences": {
    "clusterParameters": {
      "max_connections": {"reason": "NH scaled up", "severity": "info"},
      "binlog_format": {"reason": "NH uses ROW format", "severity": "info"}
    },
    "instanceParameters": {},
    "clusterConfig": {
      "engineVersion": {"reason": "NH uses Aurora MySQL 3.x", "severity": "info"},
      "dbClusterInstanceClass": {"reason": "NH uses Graviton", "severity": "info"}
    }
  }
}
```

## DynamoDB Keys

### Checker
- **pk**: `{Project}#{Env}` (ex: `mro-mi2#nh-ppd`)
- **sk**: `check:infra:rds:current`

### Compare
- **pk**: `{Project}#comparison:{SourceEnv}:{DestinationEnv}` (ex: `mro-mi2#comparison:legacy-ppd:nh-ppd`)
- **sk**: `check:infra:rds:current`

## Outputs

### State (infra-rds-checker)
```json
{
  "status": "ok | warning | critical",
  "cluster": {
    "identifier": "string",
    "status": "available | backing-up | creating | deleting | failed | maintenance | modifying",
    "engine": "aurora-mysql | aurora-postgresql",
    "engineVersion": "8.0.mysql_aurora.3.04.0",
    "endpoint": "string - writer endpoint",
    "readerEndpoint": "string",
    "port": 3306,
    "multiAZ": true,
    "storageEncrypted": true,
    "deletionProtection": true
  },
  "parameterGroups": {
    "cluster": {
      "name": "rubix-aurora-cluster-params",
      "family": "aurora-mysql8.0",
      "parameters": {
        "character_set_server": "utf8mb4",
        "max_connections": "1000",
        "slow_query_log": "1"
      }
    },
    "instance": {
      "name": "rubix-aurora-instance-params",
      "family": "aurora-mysql8.0",
      "parameters": {
        "performance_schema": "1",
        "max_allowed_packet": "67108864"
      }
    }
  },
  "instances": [
    {
      "identifier": "string",
      "status": "available",
      "instanceClass": "db.r6g.large",
      "availabilityZone": "eu-central-1a",
      "isWriter": true,
      "performanceInsightsEnabled": true,
      "dbParameterGroupName": "rubix-aurora-instance-params",
      "dbParameterGroupStatus": "in-sync | pending-reboot"
    }
  ],
  "storage": {
    "allocatedStorage": 100,
    "storageType": "aurora"
  },
  "backup": {
    "backupRetentionPeriod": 7,
    "preferredBackupWindow": "02:00-03:00"
  },
  "timestamp": "ISO8601"
}
```

### Comparison (infra-rds-compare)
```json
{
  "status": "synced | differs | synced_with_expected_diffs | critical",
  "summary": {
    "clusterConfig": "synced | differs",
    "clusterParameters": "synced | differs",
    "instanceParameters": "synced | differs",
    "instanceCount": "synced | differs"
  },
  "clusterConfigComparison": {
    "synced": ["storageEncrypted", "deletionProtection"],
    "expectedDifferences": [
      {
        "parameter": "engineVersion",
        "source": "5.7.mysql_aurora.2.11.3",
        "destination": "8.0.mysql_aurora.3.04.0",
        "reason": "NH uses Aurora MySQL 3.x",
        "severity": "info"
      }
    ],
    "unexpectedDifferences": []
  },
  "parameterGroupComparison": {
    "cluster": {
      "status": "synced | differs",
      "synced": ["character_set_server", "collation_server"],
      "expectedDifferences": [
        {
          "parameter": "max_connections",
          "source": "500",
          "destination": "1000",
          "reason": "NH scaled up",
          "severity": "info"
        }
      ],
      "unexpectedDifferences": [],
      "onlySource": [],
      "onlyDestination": ["binlog_format"]
    },
    "instance": {
      "status": "synced | differs",
      "synced": ["performance_schema"],
      "expectedDifferences": [],
      "unexpectedDifferences": [],
      "onlySource": [],
      "onlyDestination": []
    }
  },
  "issues": [],
  "timestamp": "ISO8601"
}
```

## Appels AWS (infra-rds-checker)

### Cross-account
```json
{
  "Type": "Task",
  "Resource": "arn:aws:states:::aws-sdk:sts:assumeRole",
  "Parameters": {
    "RoleArn.$": "$.CrossAccountRoleArn",
    "RoleSessionName": "ops-dashboard-rds-check"
  }
}
```

### API Calls avec credentials
```json
{
  "Type": "Task",
  "Resource": "arn:aws:states:::aws-sdk:rds:describeDBClusters",
  "Parameters": {
    "DbClusterIdentifier.$": "$.DbClusterIdentifier"
  },
  "Credentials": {
    "RoleArn.$": "$.CrossAccountRoleArn"
  }
}
```

### Liste des appels
| Service | API Call | Description |
|---------|----------|-------------|
| RDS | `DescribeDBClusters` | Info cluster |
| RDS | `DescribeDBInstances` | Info instances membres |
| RDS | `DescribeDBClusterParameters` | Parametres cluster (source=user) |
| RDS | `DescribeDBParameters` | Parametres instance (source=user) |

## Logique metier

### infra-rds-checker
1. Valider les inputs requis
2. Appeler `rds:DescribeDBClusters` avec cross-account credentials
3. Pour chaque membre du cluster, appeler `rds:DescribeDBInstances`
4. Recuperer le Cluster Parameter Group :
   - `DescribeDBClusterParameters` avec filtre `Source=user` (parametres modifies)
5. Recuperer l'Instance Parameter Group (du premier writer) :
   - `DescribeDBParameters` avec filtre `Source=user`
6. Calculer le status (ok/warning/critical)
7. Sauvegarder en DynamoDB via save-state Lambda

### infra-rds-compare
1. Valider les inputs requis
2. Fetch Source state depuis DynamoDB (parallel)
3. Fetch Destination state depuis DynamoDB (parallel)
4. Si donnees manquantes, retourner status "pending"
5. Appeler Lambda compare-rds avec les deux states
6. Sauvegarder le resultat en DynamoDB

### Lambda compare-rds
1. Parser les states DynamoDB
2. Comparer la config cluster :
   - engineVersion, instanceClass, multiAZ, etc.
   - Categoriser : synced, expectedDiff, unexpectedDiff
3. Comparer les parameter groups cluster :
   - Parametres identiques
   - Differences attendues (depuis ExpectedDifferences)
   - Differences inattendues
   - Parametres seulement source/destination
4. Comparer les parameter groups instance (idem)
5. Generer le rapport avec issues

## Conditions de succes (status: ok)
- Cluster status = available
- Toutes les instances en status available
- Au moins une instance writer active
- Parameter groups in-sync (pas pending-reboot)

## Conditions d'alerte (status: warning)
- Cluster ou instance en status backing-up, maintenance, modifying
- Parameter group status = pending-reboot
- Backup retention < 7 jours
- Differences inattendues dans parameter groups (compare)

## Conditions d'erreur (status: critical)
- Cluster status = failed
- Aucune instance writer disponible
- Instance en status failed
- Deletion protection desactive en prod

## Configuration des differences attendues

### Par defaut (DEFAULT_EXPECTED_DIFFERENCES)
```python
DEFAULT_EXPECTED_DIFFERENCES = {
    "clusterParameters": {
        # Parametres qui different typiquement entre envs
        "max_connections": {
            "reason": "Scaled based on instance size",
            "severity": "info"
        },
        "binlog_format": {
            "reason": "May differ based on replication needs",
            "severity": "info"
        }
    },
    "instanceParameters": {
        "max_allowed_packet": {
            "reason": "May be increased for large queries",
            "severity": "info"
        }
    },
    "clusterConfig": {
        "engineVersion": {
            "reason": "Version upgrade expected",
            "severity": "info"
        },
        "dbClusterInstanceClass": {
            "reason": "Instance class may differ",
            "severity": "info"
        }
    }
}
```

### Override par input
L'input `ExpectedDifferences` peut overrider ou completer la config par defaut.

## Permissions IAM (cross-account role)
```json
{
  "Effect": "Allow",
  "Action": [
    "rds:DescribeDBClusters",
    "rds:DescribeDBInstances",
    "rds:DescribeDBClusterParameterGroups",
    "rds:DescribeDBClusterParameters",
    "rds:DescribeDBParameterGroups",
    "rds:DescribeDBParameters"
  ],
  "Resource": "*"
}
```

## Exemples d'execution

### infra-rds-checker (NH ppd)
```json
{
  "Project": "mro-mi2",
  "Env": "nh-ppd",
  "Instance": "MI2",
  "Environment": "ppd",
  "DbClusterIdentifier": "rubix-dig-ppd-aurora-mi2",
  "CrossAccountRoleArn": "arn:aws:iam::287223952330:role/iam-dig-ppd-dashboard-read"
}
```

### infra-rds-checker (Legacy ppd)
```json
{
  "Project": "mro-mi2",
  "Env": "legacy-ppd",
  "Instance": "MI2",
  "Environment": "ppd",
  "DbClusterIdentifier": "rubix-nonprod-aurora-mi2",
  "CrossAccountRoleArn": "arn:aws:iam::073290922796:role/dashboard-read"
}
```

### infra-rds-compare
```json
{
  "Project": "mro-mi2",
  "SourceEnv": "legacy-ppd",
  "DestinationEnv": "nh-ppd",
  "SourceStateKey": "mro-mi2#legacy-ppd|infra:rds",
  "DestinationStateKey": "mro-mi2#nh-ppd|infra:rds",
  "ExpectedDifferences": {
    "clusterConfig": {
      "engineVersion": {"reason": "NH uses Aurora MySQL 3.x", "severity": "info"}
    }
  }
}
```

## Notes
- Le checker ne teste pas la connectivite SQL (necessite Lambda avec driver)
- Aurora Serverless v2 a des metriques differentes (ACU au lieu de CPU)
- Les parameter groups sont compares uniquement sur les parametres modifies (source=user)
- Les parametres avec valeurs dynamiques ({DBInstanceClassMemory*3/4}) sont compares comme strings
