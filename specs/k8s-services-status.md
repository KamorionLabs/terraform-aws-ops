# Spec: Kubernetes Services Status

## Identifiant
- **ID**: `k8s-services`
- **Domaine**: kubernetes
- **Priorite**: P0 (critique)
- **Scope**: `INSTANCE` (MI1, MI2, MI3, FR, BENE, INDUS)

## Objectif
Verifier l'etat des Services Kubernetes :
- Endpoints actifs et ready
- Load balancers et external IPs
- Connectivite interne
- Comparaison Legacy vs New Horizon (memes services exposes)

## Architecture

### Composant 1 : Fetch & Store (Step Function)
Recupere l'etat des services et stocke en DynamoDB.

- [x] **Step Function** (appel direct eks:call avec cross-account)
- [x] **Lambda** ProcessServices (correlation services/endpoints)

### Composant 2 : Compare (Step Function)
Compare les services Legacy vs New Horizon.

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
  "ServiceNames": ["string"]
}
```

### Compare
```json
{
  "Domain": "string",
  "Instance": "MI1",
  "Environment": "ppd",
  "LegacyStateKey": "mro#mi1-ppd-legacy|k8s-services",
  "NHStateKey": "mro#mi1-ppd-nh|k8s-services"
}
```

## Outputs (payload DynamoDB)

### State (Fetch & Store)
```json
{
  "status": "ok | warning | critical",
  "source": "legacy | nh",
  "summary": {
    "total": 5,
    "healthy": 4,
    "unhealthy": 1,
    "byType": {
      "ClusterIP": 3,
      "LoadBalancer": 1,
      "NodePort": 1
    }
  },
  "services": [
    {
      "name": "hybris",
      "type": "ClusterIP",
      "clusterIP": "10.100.0.100",
      "externalIP": null,
      "ports": [
        {"name": "http", "port": 9001, "targetPort": 9001, "protocol": "TCP"},
        {"name": "admin", "port": 9002, "targetPort": 9002, "protocol": "TCP"}
      ],
      "selector": {"app": "hybris"},
      "endpoints": {
        "ready": 3,
        "notReady": 0,
        "addresses": ["10.0.1.10", "10.0.1.11", "10.0.1.12"]
      },
      "healthy": true
    },
    {
      "name": "hybris-lb",
      "type": "LoadBalancer",
      "clusterIP": "10.100.0.101",
      "externalIP": "a]xxx.elb.eu-central-1.amazonaws.com",
      "ports": [{"port": 443, "targetPort": 9001, "protocol": "TCP"}],
      "loadBalancer": {
        "hostname": "xxx.elb.eu-central-1.amazonaws.com",
        "status": "Active"
      },
      "endpoints": {
        "ready": 3,
        "notReady": 0
      },
      "healthy": true
    }
  ],
  "issues": [],
  "healthy": true,
  "timestamp": "ISO8601"
}
```

### Comparison
```json
{
  "status": "synced | differs",
  "summary": {
    "serviceCount": "synced | differs",
    "serviceTypes": "synced | differs",
    "endpointHealth": "synced | differs"
  },
  "servicesComparison": {
    "sameServices": ["hybris", "solr", "apache"],
    "differentConfig": [
      {
        "service": "hybris-lb",
        "legacy": {"type": "NodePort", "port": 30001},
        "nh": {"type": "LoadBalancer", "port": 443},
        "expected": true,
        "reason": "NH uses ALB Ingress instead of NodePort"
      }
    ],
    "onlyLegacy": [],
    "onlyNH": ["redis"]
  },
  "endpointsComparison": {
    "legacy": {"totalReady": 9, "totalNotReady": 0},
    "nh": {"totalReady": 9, "totalNotReady": 0},
    "status": "synced"
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
    "RoleSessionName": "ops-dashboard-services-check"
  },
  "ResultPath": "$.Credentials"
}
```

### API Calls
| Service | API Call | Path |
|---------|----------|------|
| EKS | `DescribeCluster` | - |
| EKS | `eks:call` | GET /api/v1/namespaces/{ns}/services |
| EKS | `eks:call` | GET /api/v1/namespaces/{ns}/endpoints |

## Logique metier

### Fetch & Store
1. Assume role cross-account si necessaire
2. Appeler `eks:DescribeCluster` pour recuperer endpoint et certificat
3. Lister les services du namespace via `eks:call`
4. Lister les endpoints du namespace via `eks:call`
5. Pour chaque service:
   - Extraire type, ports, selector
   - Correlate avec les endpoints correspondants
   - Verifier que chaque service a au moins 1 endpoint ready
   - Pour les LoadBalancer, verifier l'external IP/hostname
6. Calculer le summary
7. Sauvegarder en DynamoDB

### Compare
1. Recuperer state Legacy depuis DynamoDB
2. Recuperer state NH depuis DynamoDB
3. Comparer les services par nom:
   - Memes services presents ?
   - Memes types (ClusterIP, LoadBalancer) ?
   - Memes ports exposes ?
4. Comparer la sante des endpoints
5. Identifier les differences attendues vs inattendues
6. Calculer le status global

## Conditions de succes (status: ok)
- [x] Tous les services ont au moins 1 endpoint ready
- [x] Les LoadBalancer ont une external IP/hostname assignee
- [x] Pas d'endpoints notReady

## Conditions d'alerte (status: warning)
- [x] Service avec endpoints notReady > 0
- [x] LoadBalancer en status Pending (< 5min)

## Conditions d'erreur (status: critical)
- [x] Service sans aucun endpoint ready
- [x] LoadBalancer sans external IP depuis > 5min
- [x] Service attendu manquant

## Dependances
- Prerequis: `infra-eks`, `k8s-pods`
- Services AWS: EKS
- Permissions IAM (dans le role cross-account):
  - `eks:DescribeCluster`
- Kubernetes RBAC: `get`, `list` sur services, endpoints

## Mapping Comptes AWS

| Instance | Env | Legacy Account | NH Account |
|----------|-----|----------------|------------|
| MI1/MI2/MI3 | stg | 073290922796 | 281127105461 |
| MI1/MI2/MI3 | ppd | 073290922796 | 287223952330 |
| MI1/MI2/MI3 | prd | 073290922796 | 366483377530 |
| FR | * | 073290922796 (eu-west-3) | selon env |

## Services attendus par namespace

| Namespace | Services | Critical |
|-----------|----------|----------|
| hybris | hybris, hybris-admin | Yes |
| hybris | solr, solr-leader | Yes |
| hybris | apache | Yes |
| hybris | redis | No |
| hybris | smui | No |

## Notes
- Necessite une Lambda pour le processing (correlation services/endpoints)
- Les services de type ExternalName n'ont pas d'endpoints
- Les LoadBalancer peuvent utiliser AWS ALB ou NLB selon les annotations
- Legacy utilise souvent NodePort, NH utilise LoadBalancer + Ingress
- Verifier les annotations AWS specifiques (service.beta.kubernetes.io/*)
