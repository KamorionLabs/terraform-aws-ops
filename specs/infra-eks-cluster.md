# Spec: EKS Cluster Status

## Identifiant
- **ID**: `infra-eks`
- **Domaine**: infrastructure
- **Priorite**: P0 (critique)
- **Scope**: `INSTANCE` (MI1, MI2, MI3, FR, BENE, INDUS)

## Objectif
Verifier l'etat de sante d'un cluster EKS :
- Statut du control plane et version Kubernetes
- Configuration addons et leurs versions
- Endpoints et configuration reseau
- Comparaison Legacy vs New Horizon (versions, config)

## Architecture

### Composant 1 : Fetch & Store (Step Function)
Recupere l'etat du cluster EKS et stocke en DynamoDB.

- [x] **Step Function** (appels directs AWS SDK avec cross-account)

### Composant 2 : Compare (Step Function)
Compare les clusters Legacy vs New Horizon.

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
  "ClusterName": "string - nom du cluster EKS"
}
```

### Compare
```json
{
  "Domain": "string",
  "Instance": "MI1",
  "Environment": "ppd",
  "LegacyStateKey": "mro#mi1-ppd-legacy|infra-eks",
  "NHStateKey": "mro#mi1-ppd-nh|infra-eks"
}
```

## Outputs (payload DynamoDB)

### State (Fetch & Store)
```json
{
  "status": "ok | warning | critical",
  "source": "legacy | nh",
  "cluster": {
    "name": "string",
    "status": "ACTIVE | CREATING | DELETING | FAILED | UPDATING",
    "version": "1.29",
    "platformVersion": "eks.5",
    "endpoint": "https://xxx.eu-central-1.eks.amazonaws.com",
    "createdAt": "ISO8601",
    "arn": "arn:aws:eks:eu-central-1:xxx:cluster/xxx"
  },
  "networking": {
    "vpcId": "vpc-xxxxxxxx",
    "subnetIds": ["subnet-xxx", "subnet-yyy"],
    "securityGroupIds": ["sg-xxx"],
    "clusterSecurityGroupId": "sg-yyy",
    "serviceIpv4Cidr": "172.20.0.0/16",
    "ipFamily": "ipv4"
  },
  "logging": {
    "api": true,
    "audit": true,
    "authenticator": true,
    "controllerManager": true,
    "scheduler": true
  },
  "addons": [
    {
      "name": "vpc-cni",
      "version": "v1.15.4-eksbuild.1",
      "status": "ACTIVE",
      "configurationValues": "{}"
    },
    {
      "name": "coredns",
      "version": "v1.10.1-eksbuild.6",
      "status": "ACTIVE"
    },
    {
      "name": "kube-proxy",
      "version": "v1.29.0-eksbuild.1",
      "status": "ACTIVE"
    }
  ],
  "nodeGroups": {
    "count": 3,
    "types": ["managed", "karpenter"]
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
    "clusterVersion": "synced | differs",
    "addonsVersions": "synced | differs",
    "networking": "synced | differs"
  },
  "versionComparison": {
    "legacy": "1.28",
    "nh": "1.29",
    "expected": true,
    "reason": "NH uses newer K8s version"
  },
  "addonsComparison": {
    "sameVersion": ["coredns"],
    "differentVersion": [
      {
        "addon": "vpc-cni",
        "legacy": "v1.14.1-eksbuild.1",
        "nh": "v1.15.4-eksbuild.1",
        "expected": true,
        "reason": "NH has updated VPC CNI"
      }
    ],
    "onlyLegacy": [],
    "onlyNH": ["aws-ebs-csi-driver"]
  },
  "configComparison": {
    "networking": {
      "status": "differs",
      "legacy": {"vpcId": "vpc-legacy"},
      "nh": {"vpcId": "vpc-nh"},
      "expected": true,
      "reason": "Different VPCs per environment"
    }
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
    "RoleSessionName": "ops-dashboard-eks-check"
  },
  "ResultPath": "$.Credentials"
}
```

### API Calls
| Service | API Call | Resource ARN pattern |
|---------|----------|---------------------|
| EKS | `DescribeCluster` | `arn:aws:eks:*:*:cluster/*` |
| EKS | `ListAddons` | `arn:aws:eks:*:*:cluster/*` |
| EKS | `DescribeAddon` | `arn:aws:eks:*:*:addon/*/*/*` |
| EKS | `ListNodegroups` | `arn:aws:eks:*:*:cluster/*` |

## Logique metier

### Fetch & Store
1. Assume role cross-account si necessaire
2. Appeler `eks:DescribeCluster` avec le nom du cluster
3. Verifier le statut du cluster (doit etre ACTIVE)
4. Lister les addons avec `eks:ListAddons`
5. Pour chaque addon, verifier le statut avec `eks:DescribeAddon`
6. Lister les node groups avec `eks:ListNodegroups`
7. Agreger les resultats et determiner le statut global
8. Sauvegarder en DynamoDB

### Compare
1. Recuperer state Legacy depuis DynamoDB
2. Recuperer state NH depuis DynamoDB
3. Comparer les versions Kubernetes
4. Comparer les versions des addons
5. Comparer la configuration reseau (different attendu)
6. Identifier les differences inattendues
7. Calculer le status global

## Conditions de succes (status: ok)
- [x] Cluster status = ACTIVE
- [x] Tous les addons en status ACTIVE
- [x] Endpoint accessible
- [x] Logging actif pour tous les types

## Conditions d'alerte (status: warning)
- [x] Un addon en status UPDATING ou CREATING
- [x] Version Kubernetes deprecated mais encore supportee
- [x] Logging desactive pour certains types

## Conditions d'erreur (status: critical)
- [x] Cluster status != ACTIVE
- [x] Un addon en status FAILED ou DEGRADED
- [x] Version Kubernetes en fin de support
- [x] Endpoint inaccessible

## Dependances
- Services AWS: EKS
- Permissions IAM (dans le role cross-account):
  - `eks:DescribeCluster`
  - `eks:ListAddons`
  - `eks:DescribeAddon`
  - `eks:ListNodegroups`

## Mapping Comptes AWS

| Instance | Env | Legacy Account | NH Account |
|----------|-----|----------------|------------|
| MI1/MI2/MI3 | stg | 073290922796 | 281127105461 |
| MI1/MI2/MI3 | ppd | 073290922796 | 287223952330 |
| MI1/MI2/MI3 | prd | 073290922796 | 366483377530 |
| FR | * | 073290922796 (eu-west-3) | selon env |

## Mapping Clusters

| Instance | Env | Legacy Cluster | NH Cluster |
|----------|-----|----------------|------------|
| MI1 | ppd | rubix-nonprod | rubix-dig-ppd-webshop |
| MI2 | ppd | rubix-nonprod | rubix-dig-ppd-webshop |
| MI1 | prd | rubix-prod | rubix-dig-prd-webshop |
| FR | ppd | rubix-nonprod-fr | rubix-dig-ppd-webshop |

## Versions et Addons attendus

### K8s Versions
| Environment | Legacy | NH |
|-------------|--------|-----|
| stg | 1.27 | 1.29 |
| ppd | 1.27 | 1.29 |
| prd | 1.27 | 1.29 |

### Addons obligatoires
| Addon | Purpose | Required |
|-------|---------|----------|
| vpc-cni | Networking | Yes |
| coredns | DNS | Yes |
| kube-proxy | Networking | Yes |
| aws-ebs-csi-driver | Storage | NH only |

## Notes
- La verification de l'endpoint (healthz) necessite une Lambda car Step Functions ne peut pas faire de HTTP call direct
- Pour un check complet, combiner avec `k8s-pods` pour verifier les workloads
- NH et Legacy peuvent avoir des versions K8s differentes (expected)
- Les addons NH incluent souvent des versions plus recentes
