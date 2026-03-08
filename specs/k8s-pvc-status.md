# Spec: Kubernetes PVC Status

## Identifiant
- **ID**: `k8s-pvc`
- **Domaine**: kubernetes
- **Priorite**: P0 (critique)
- **Scope**: `INSTANCE` (MI1, MI2, MI3, FR, BENE, INDUS)

## Objectif
Verifier l'etat des PersistentVolumeClaims et PersistentVolumes :
- Binding status (PVC et PV)
- Capacite et utilisation
- Association avec les PV et backends (EFS, EBS)
- Comparaison Source vs Destination (memes PVCs et PVs)

## Architecture

### Composant 1 : Fetch & Store (Step Function v3)
Recupere l'etat des PVCs et PVs en parallele, stocke en DynamoDB.

- [x] **Step Function** (appel direct eks:call avec cross-account)
- [x] **PVC fetching** via `/api/v1/namespaces/{ns}/persistentvolumeclaims`
- [x] **PV fetching** via `/api/v1/persistentvolumes` (cluster-wide)

### Composant 2 : Compare (Step Function)
Compare les PVCs et PVs entre Source et Destination.

- [x] **Step Function** (lecture DynamoDB + comparaison)
- [x] **Lambda compare-pvc** (comparaison detaillee avec terminologie generique)

## Inputs

### Fetch & Store (k8s-pvc-status-checker)
```json
{
  "Project": "string - projet (mro-mi2)",
  "Env": "string - environnement cible (nh-ppd, legacy-ppd)",
  "ClusterName": "string - nom du cluster EKS",
  "Namespace": "string - namespace K8s (hybris, rubix-mro-mi2-preprod)",
  "CrossAccountRoleArn": "arn:aws:iam::{account}:role/ops-dashboard-read (optionnel)"
}
```

### Compare (k8s-pvc-compare)
```json
{
  "Project": "string - projet (mro-mi2)",
  "SourceEnv": "string - environnement source (legacy-ppd)",
  "DestinationEnv": "string - environnement destination (nh-ppd)"
}
```

Note: La Lambda compare-pvc utilise une terminologie generique (source/destination)
au lieu de legacy/nh pour permettre la reutilisation open-source.

## Outputs (payload DynamoDB)

### State (Fetch & Store)
```json
{
  "status": "ok | warning | critical",
  "source": "legacy | nh",
  "summary": {
    "total": 5,
    "bound": 5,
    "pending": 0,
    "lost": 0,
    "totalCapacity": "500Gi",
    "byStorageClass": {
      "gp3": 3,
      "efs-sc": 2
    }
  },
  "pvcs": [
    {
      "name": "data-hybris-0",
      "status": "Bound",
      "volume": "pvc-xxx-yyy-zzz",
      "capacity": "100Gi",
      "accessModes": ["ReadWriteOnce"],
      "storageClass": "gp3",
      "volumeMode": "Filesystem",
      "createdAt": "ISO8601",
      "boundAt": "ISO8601"
    },
    {
      "name": "media-hybris",
      "status": "Bound",
      "volume": "pvc-efs-xxx",
      "capacity": "5Ti",
      "accessModes": ["ReadWriteMany"],
      "storageClass": "efs-sc",
      "volumeMode": "Filesystem",
      "efsConfig": {
        "fileSystemId": "fs-xxxxxxxx",
        "accessPointId": "fsap-xxxxxxxx"
      }
    }
  ],
  "persistentVolumes": [
    {
      "name": "pvc-xxx-yyy-zzz",
      "capacity": "100Gi",
      "accessModes": ["ReadWriteOnce"],
      "reclaimPolicy": "Delete",
      "storageClass": "gp3",
      "status": "Bound",
      "claimRef": "hybris/data-hybris-0",
      "csiDriver": "ebs.csi.aws.com",
      "volumeHandle": "vol-xxxxxxxxx"
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
    "pvcCount": "synced | differs",
    "pvCount": "synced | differs",
    "storageClasses": "synced | differs",
    "totalCapacity": "synced | differs"
  },
  "pvcCountComparison": {
    "source": 5,
    "destination": 5,
    "status": "synced",
    "difference": 0
  },
  "pvCountComparison": {
    "source": 6,
    "destination": 6,
    "status": "synced",
    "difference": 0,
    "sourceByStatus": {"Bound": 5, "Released": 1},
    "destinationByStatus": {"Bound": 6}
  },
  "pvcsComparison": {
    "samePvcs": ["data-hybris-0", "data-hybris-1", "media-hybris"],
    "differentConfig": [
      {
        "pvc": "data-hybris-0",
        "differences": [
          {
            "field": "storageClass",
            "source": "gp2",
            "destination": "gp3",
            "expected": true,
            "reason": "Expected mapping from gp2 to gp3"
          }
        ],
        "allExpected": true
      }
    ],
    "onlySource": [],
    "onlyDestination": ["cache-redis-0"]
  },
  "pvsComparison": {
    "status": "compared",
    "samePvs": ["pvc-xxx-yyy"],
    "differentConfig": [],
    "onlySource": [],
    "onlyDestination": []
  },
  "capacityComparison": {
    "source": "250Gi",
    "destination": "500Gi",
    "expected": true,
    "reason": "Capacity differs between environments"
  },
  "storageClassComparison": {
    "source": {"gp2": 3, "efs-sc": 2},
    "destination": {"gp3": 3, "efs-sc": 2},
    "expected": true,
    "reason": "Storage classes match after expected mappings"
  },
  "issues": [],
  "sourceTimestamp": "ISO8601",
  "destinationTimestamp": "ISO8601",
  "timestamp": "ISO8601"
}
```

Note: Les mappings de storage class attendus (ex: gp2 -> gp3) sont configurables
via la variable d'environnement `EXPECTED_SC_MAPPINGS` de la Lambda compare-pvc.

## Appels AWS necessaires

### Cross-account
```json
{
  "Type": "Task",
  "Resource": "arn:aws:states:::aws-sdk:sts:assumeRole",
  "Parameters": {
    "RoleArn.$": "$.CrossAccountRoleArn",
    "RoleSessionName": "ops-dashboard-pvc-check"
  },
  "ResultPath": "$.Credentials"
}
```

### API Calls
| Service | API Call | Path |
|---------|----------|------|
| EKS | `DescribeCluster` | - |
| EKS | `eks:call` | GET /api/v1/namespaces/{ns}/persistentvolumeclaims |
| EKS | `eks:call` | GET /api/v1/persistentvolumes |

## Logique metier

### Fetch & Store
1. Assume role cross-account si necessaire
2. Appeler `eks:DescribeCluster` pour recuperer endpoint et certificat
3. Lister les PVCs du namespace via `eks:call`
4. Lister les PVs (cluster-wide) via `eks:call`
5. Pour chaque PVC:
   - Verifier status.phase = Bound
   - Extraire capacite, storageClass, accessModes
   - Correlate avec le PV associe
   - Pour EFS: extraire fileSystemId et accessPointId
6. Calculer le summary
7. Sauvegarder en DynamoDB

### Compare
1. Recuperer state Source depuis DynamoDB
2. Recuperer state Destination depuis DynamoDB
3. Comparer les PVCs par nom:
   - Memes PVCs presents ?
   - Memes capacites ?
   - Memes storageClasses ?
4. Comparer les PVs par nom:
   - Memes PVs presents ?
   - Memes configurations (capacity, reclaimPolicy, csiDriver) ?
   - Comparaison des configs EFS (fileSystemId)
5. Comparer la capacite totale
6. Identifier les differences attendues vs inattendues
7. Calculer le status global (synced/differs)

## Conditions de succes (status: ok)
- [x] Tous les PVCs en status Bound
- [x] Tous les PVs associes disponibles
- [x] StorageClass valide

## Conditions d'alerte (status: warning)
- [x] PVC en status Pending depuis < 5min
- [x] Capacite proche du maximum (> 80%)

## Conditions d'erreur (status: critical)
- [x] PVC en status Pending depuis > 5min
- [x] PVC en status Lost
- [x] PV associe manquant ou en erreur

## Dependances
- Prerequis: `infra-eks`, `infra-efs` (si EFS CSI)
- Services AWS: EKS
- Permissions IAM (dans le role cross-account):
  - `eks:DescribeCluster`
- Kubernetes RBAC: `get`, `list` sur persistentvolumeclaims, persistentvolumes

## Mapping Comptes AWS

Voir CLAUDE.md pour la liste complete des comptes et contextes.

## StorageClasses attendues

| StorageClass | Type | Source | Destination | Use Case |
|--------------|------|--------|-------------|----------|
| gp2 | EBS | Yes | No | Block storage (source) |
| gp3 | EBS | No | Yes | Block storage (destination) |
| efs-sc | EFS | Yes | Yes | Shared media storage |
| efs-static | EFS | No | Yes | Static provisioning |

Les mappings attendus (ex: gp2 -> gp3) sont configurables via `EXPECTED_SC_MAPPINGS`.

## PVCs attendus par application

| Application | PVC Pattern | StorageClass | Capacity |
|-------------|-------------|--------------|----------|
| hybris | data-hybris-{n} | gp3 | 100Gi |
| hybris | media-hybris | efs-sc | 5Ti |
| solr | data-solr-{n} | gp3 | 50Gi |
| redis | data-redis-{n} | gp3 | 10Gi |

## Notes
- Pour EFS, verifier que le PV pointe vers le bon FileSystemId
- Les PVCs des StatefulSets suivent le pattern `{pvc-name}-{statefulset-name}-{ordinal}`
- La migration gp2 -> gp3 est attendue entre Source et Destination
- Verifier les annotations specifiques EFS (efs.csi.aws.com/*)
- Les PVCs EFS en ReadWriteMany peuvent etre partages entre pods
- Les PVs sont recuperes au niveau cluster (pas namespace) depuis v3
- La Step Function utilise un Parallel state pour fetcher PVCs et PVs simultanement
