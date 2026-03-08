# Spec: Kubernetes Pods Readiness

## Identifiant
- **ID**: `k8s-pods`
- **Domaine**: kubernetes
- **Priorite**: P0 (critique)
- **Scope**: `INSTANCE` (MI1, MI2, MI3, FR, BENE, INDUS)

## Objectif
Verifier l'etat des pods dans un namespace :
- Status Running/Ready
- Restarts et crashloops
- Resource utilisation
- Comparaison Legacy vs New Horizon (memes pods deployes)

## Architecture

### Composant 1 : Fetch & Store (Step Function)
Recupere l'etat des pods et stocke en DynamoDB.

- [x] **Step Function** (appel direct eks:call avec cross-account)
- [x] **Lambda** ProcessPods (parsing complexe des pods)

### Composant 2 : Compare (Step Function)
Compare les pods Legacy vs New Horizon.

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
  "Namespace": "string - namespace K8s (hybris, argocd, etc.)",
  "LabelSelector": "string - optionnel (app=hybris)",
  "ExpectedPodCount": 3
}
```

### Compare
```json
{
  "Domain": "string",
  "Instance": "MI1",
  "Environment": "ppd",
  "LegacyStateKey": "mro#mi1-ppd-legacy|k8s-pods",
  "NHStateKey": "mro#mi1-ppd-nh|k8s-pods"
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
    "running": 8,
    "pending": 1,
    "failed": 1,
    "ready": 8,
    "notReady": 2,
    "expectedCount": 10,
    "actualCount": 10
  },
  "pods": [
    {
      "name": "hybris-0",
      "status": "Running",
      "ready": "2/2",
      "restarts": 0,
      "age": "5d",
      "node": "ip-10-0-1-100.ec2.internal",
      "conditions": {
        "Ready": true,
        "ContainersReady": true,
        "PodScheduled": true,
        "Initialized": true
      },
      "containers": [
        {
          "name": "hybris",
          "ready": true,
          "restartCount": 0,
          "state": "running",
          "image": "xxx.dkr.ecr.eu-central-1.amazonaws.com/hybris:v1.2.3",
          "lastTerminationReason": null
        }
      ],
      "resources": {
        "requests": {"cpu": "2", "memory": "8Gi"},
        "limits": {"cpu": "4", "memory": "16Gi"}
      }
    }
  ],
  "issues": [
    {
      "pod": "hybris-1",
      "issue": "CrashLoopBackOff",
      "message": "Back-off restarting failed container"
    }
  ],
  "healthy": true,
  "timestamp": "ISO8601"
}
```

### Comparison
```json
{
  "status": "synced | differs",
  "summary": {
    "podCount": "synced | differs",
    "podStatus": "synced | differs",
    "imageVersions": "synced | differs"
  },
  "podCountComparison": {
    "legacy": 3,
    "nh": 3,
    "status": "synced"
  },
  "podStatusComparison": {
    "legacy": {"running": 3, "ready": 3, "issues": 0},
    "nh": {"running": 3, "ready": 3, "issues": 0},
    "status": "synced"
  },
  "imageComparison": {
    "hybris": {
      "legacy": "xxx.dkr.ecr.eu-central-1.amazonaws.com/hybris:v1.2.2",
      "nh": "xxx.dkr.ecr.eu-central-1.amazonaws.com/hybris:v1.2.3",
      "expected": true,
      "reason": "NH has newer version"
    }
  },
  "resourcesComparison": {
    "legacy": {"totalCpuRequests": "6", "totalMemoryRequests": "24Gi"},
    "nh": {"totalCpuRequests": "6", "totalMemoryRequests": "24Gi"},
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
    "RoleSessionName": "ops-dashboard-pods-check"
  },
  "ResultPath": "$.Credentials"
}
```

### API Calls
| Service | API Call | Resource ARN pattern |
|---------|----------|---------------------|
| EKS | `DescribeCluster` | `arn:aws:eks:*:*:cluster/*` |
| EKS | `eks:call` (GET /api/v1/namespaces/{ns}/pods) | `arn:aws:eks:*:*:cluster/*` |

## Logique metier

### Fetch & Store
1. Assume role cross-account si necessaire
2. Appeler `eks:DescribeCluster` pour recuperer endpoint et certificat
3. Appeler `eks:call` pour lister les pods du namespace
4. Pour chaque pod:
   - Extraire status.phase (Running, Pending, Failed, Succeeded)
   - Verifier conditions (Ready, ContainersReady)
   - Compter les restarts de chaque container
   - Extraire les versions d'images
   - Identifier les problemes (CrashLoopBackOff, ImagePullBackOff)
5. Calculer le summary (total, running, ready, etc.)
6. Comparer avec ExpectedPodCount si fourni
7. Sauvegarder en DynamoDB

### Compare
1. Recuperer state Legacy depuis DynamoDB
2. Recuperer state NH depuis DynamoDB
3. Comparer le nombre de pods
4. Comparer les status (running, ready, issues)
5. Comparer les versions d'images
6. Comparer les resources allouees
7. Identifier les differences inattendues
8. Calculer le status global

## Conditions de succes (status: ok)
- [x] Tous les pods en status Running
- [x] Tous les pods en condition Ready=True
- [x] Aucun restart recent (< 3 dans les 24h)
- [x] Aucun pod en status Pending depuis > 5min
- [x] Nombre de pods = ExpectedPodCount (si defini)

## Conditions d'alerte (status: warning)
- [x] Pod en status Pending depuis < 5min
- [x] Pod avec restarts > 3 dans les 24h
- [x] Pod avec container en status Waiting (ImagePullBackOff)
- [x] Nombre de pods < ExpectedPodCount (scale down en cours)

## Conditions d'erreur (status: critical)
- [x] Pod en status Failed
- [x] Pod en CrashLoopBackOff
- [x] Pod en status Pending depuis > 5min
- [x] Ready condition False depuis > 2min
- [x] Aucun pod running dans le namespace

## Dependances
- Prerequis: `infra-eks` (cluster doit etre ACTIVE)
- Services AWS: EKS
- Permissions IAM (dans le role cross-account):
  - `eks:DescribeCluster`
  - `sts:AssumeRole` (depuis le compte ops-dashboard)
- Kubernetes RBAC: `get`, `list` sur pods dans le namespace

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

## Lambda ProcessPods (necessaire)

Le parsing des pods K8s necessite une Lambda car la logique est complexe :
- Calcul du summary
- Detection des issues
- Extraction des images/versions
- Formatage du payload

```python
def lambda_handler(event, context):
    pods = event.get('pods', [])
    expected_count = event.get('expected_pod_count')

    summary = {
        'total': 0, 'running': 0, 'pending': 0,
        'failed': 0, 'ready': 0, 'notReady': 0
    }
    processed_pods = []
    issues = []

    for pod in pods:
        summary['total'] += 1
        phase = pod['status']['phase']

        if phase == 'Running':
            summary['running'] += 1
        elif phase == 'Pending':
            summary['pending'] += 1
        elif phase == 'Failed':
            summary['failed'] += 1

        # Check readiness
        conditions = {c['type']: c['status'] == 'True'
                      for c in pod['status'].get('conditions', [])}
        if conditions.get('Ready', False):
            summary['ready'] += 1
        else:
            summary['notReady'] += 1

        # Detect issues
        for cs in pod['status'].get('containerStatuses', []):
            waiting = cs.get('state', {}).get('waiting', {})
            if waiting.get('reason') in ['CrashLoopBackOff', 'ImagePullBackOff']:
                issues.append({
                    'pod': pod['metadata']['name'],
                    'issue': waiting['reason'],
                    'message': waiting.get('message', '')
                })

        processed_pods.append({
            'name': pod['metadata']['name'],
            'status': phase,
            # ... autres champs
        })

    if expected_count:
        summary['expectedCount'] = expected_count
        summary['actualCount'] = summary['total']

    # Determine status
    status = 'ok'
    if summary['failed'] > 0 or len(issues) > 0:
        status = 'critical'
    elif summary['pending'] > 0 or summary['notReady'] > 0:
        status = 'warning'

    return {
        'status': status,
        'summary': summary,
        'pods': processed_pods,
        'issues': issues,
        'healthy': status == 'ok'
    }
```

## Notes
- `eks:call` necessite CertificateAuthority et Endpoint du cluster
- Le labelSelector peut filtrer les pods (ex: app=hybris)
- Pour les StatefulSets, verifier aussi l'ordre des pods (hybris-0, hybris-1, etc.)
- Les containers init ne sont pas comptes dans le ready count
- Les images peuvent differer entre Legacy et NH (versions differentes)
