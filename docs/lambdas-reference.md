# Lambdas Reference

Documentation complete des Lambda functions du projet Ops Dashboard.

---

## Vue d'ensemble

| Lambda | LOC | Type | Description |
|--------|-----|------|-------------|
| **Core** | | | |
| save-state | 92 | Persistance | Sauvegarde DynamoDB avec change detection |
| infra-checker | 232 | Standalone | Check EKS/RDS/EFS |
| replication-checker | 227 | Standalone | Check DMS |
| argocd-checker | 513 | Standalone | Check ArgoCD (NH only) |
| **Process (transformation)** | | | |
| process-efs-replication | 321 | Transform | Traitement EFS replication |
| process-tgw | 381 | Transform | Traitement Transit Gateway |
| process-cloudfront | 540 | Transform | Traitement CloudFront (refactoré) |
| process-dns | 290 | Transform | Traitement DNS + Route53 (nouveau) |
| process-nodes | 494 | Transform | Traitement K8s Nodes + EC2 enrichment |
| process-pods | 428 | Transform | Traitement K8s Pods |
| process-services | 390 | Transform | Traitement K8s Services |
| process-ingress | 504 | Transform | Traitement K8s Ingress |
| process-pvc | 486 | Transform | Traitement K8s PVC |
| process-secrets | 427 | Transform | Traitement K8s Secrets |
| process-alb | 403 | Transform | Traitement ALB |
| process-rds | 306 | Transform | Traitement RDS/Aurora cluster |
| **Fetch (collecte)** | | | |
| fetch-ado-file | 379 | Fetch | Fichiers Azure DevOps generique (nouveau) |
| fetch-ssm | 325 | Fetch | SSM Parameters |
| fetch-secrets | 332 | Fetch | Secrets Manager |
| fetch-cloudfront | 149 | Fetch | CloudFront distributions (simplifie) |
| **DNS** | | | |
| prepare-dns-domains | 145 | Transform | Prepare liste domaines (nouveau) |
| resolve-dns | 145 | DNS | Resolution DNS pure (nouveau) |
| dns-checker | 855 | Legacy | Resolution DNS (deprecie) |
| **Compare** | | | |
| compare-ssm | 519 | Compare | Compare SSM |
| compare-secrets-manager | 702 | Compare | Compare SecretsManager |
| compare-secrets | 437 | Compare | Compare K8s Secrets |
| compare-dns | 546 | Compare | Compare DNS |
| compare-pods | 410 | Compare | Compare K8s Pods |
| compare-ingress | 478 | Compare | Compare K8s Ingress |
| compare-services | 414 | Compare | Compare K8s Services |
| compare-pvc | 407 | Compare | Compare K8s PVC |
| compare-security-groups | 501 | Compare | Compare Security Groups |
| compare-alb | 560 | Compare | Compare ALB |
| compare-cloudfront | 578 | Compare | Compare CloudFront |
| compare-rds | 350 | Compare | Compare RDS/Aurora cluster |
| **Network** | | | |
| analyze-security-groups | 471 | Analysis | Analyse Security Groups |
| **Application** | | | |
| app-component-checker | 350 | Plugin | Check SMUI/Hybris/Solr/Apache |

**Total** : ~14,000 lignes de code Python

---

## Principes d'architecture

### Lambdas uniquement si necessaire

Creer une Lambda seulement pour :
- **Logique metier complexe** : aggregation, calculs, transformations
- **APIs externes** : services non-AWS, APIs tierces (Azure DevOps)
- **Persistance** : ecriture DynamoDB avec change detection
- **Code reutilisable** : checkers generiques

### Change detection

Ne sauvegarder en DynamoDB que si le payload a change :

```python
# state_manager.py
new_hash = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
if new_hash == current_hash:
    return {"changed": False, "reason": "no_change"}
```

### Separation des responsabilites

Architecture modulaire :
- **fetch-*** : Collecte de donnees (AWS, ADO, etc.)
- **prepare-*** : Preparation/transformation avant traitement
- **process-*** : Logique metier, calculs, status
- **compare-*** : Comparaison Source vs Destination

---

## Lambdas Core

### save-state

Sauvegarde l'etat en DynamoDB avec change detection.

**Input** :
```json
{
  "project": "mro-mi2",
  "env": "nh-ppd",
  "category": "net",
  "check_type": "dns",
  "payload": {...},
  "updated_by": "step-function:net-dns-checker"
}
```

**Output** :
```json
{
  "statusCode": 200,
  "changed": true,
  "pk": "mro-mi2#nh-ppd",
  "sk": "check:net:dns:current"
}
```

---

### infra-checker

Check standalone pour EKS, RDS, EFS.

**Input** :
```json
{
  "target": "mi2-preprod",
  "domain": "mro",
  "config": {
    "region": "eu-central-1",
    "eks_cluster_name": "rubix-dig-ppd-webshop",
    "rds_cluster_identifier": "rubix-dig-ppd-aurora",
    "efs_file_system_id": "fs-xxxxxxxx"
  }
}
```

---

### replication-checker

Check DMS tasks.

**Input** :
```json
{
  "target": "mi2-preprod",
  "domain": "mro",
  "config": {
    "region": "eu-central-1",
    "dms_tasks": ["arn:aws:dms:eu-central-1:xxx:task:xxx"],
    "max_lag_seconds": 300
  }
}
```

---

### argocd-checker

Check ArgoCD applications (NH only).

**Input** :
```json
{
  "target": "mi1-stg-nh",
  "domain": "cicd",
  "config": {
    "ArgocdUrl": "https://aws-argocd.rubix.com",
    "ClusterFilter": "k8s-dig-stg-webshop",
    "Instance": "MI1",
    "Environment": "stg",
    "region": "eu-central-1"
  }
}
```

**ClusterFilter format** : `k8s-dig-{env}-webshop` (stg, ppd, prd)

**Composants verifies** : apache, haproxy, sftp, shared, smtp

---

## Lambdas Fetch

### fetch-ado-file

Lambda generique pour recuperer des fichiers depuis Azure DevOps.

**Capabilities** :
- Recuperation fichier via Azure DevOps Git API
- Parsing HCL (terraform.tfvars)
- Parsing JSON
- Parsing YAML
- Mode brut (sans parsing)

**Input** :
```json
{
  "Organization": "rubix-group",
  "Project": "NewHorizon-IaC",
  "Repository": "NewHorizon-IaC-Webshop",
  "Path": "stacks/cloudfront/env/ppd/terraform.tfvars",
  "Branch": "master",
  "Parse": "hcl",
  "BlockName": "managed_domains"
}
```

**Parametres** :
| Parametre | Requis | Default | Description |
|-----------|--------|---------|-------------|
| Organization | Non | env ADO_ORGANIZATION | Organisation Azure DevOps |
| Project | Non | env ADO_DEFAULT_PROJECT | Projet Azure DevOps |
| Repository | Oui | - | Nom du repository |
| Path | Oui | - | Chemin du fichier |
| Branch | Non | master | Branche |
| Parse | Non | none | Type parsing (hcl, json, yaml, none) |
| BlockName | Non | - | Block HCL a extraire |

**Output** :
```json
{
  "statusCode": 200,
  "content": "...",
  "parsed": {...},
  "block": {...},
  "metadata": {
    "organization": "rubix-group",
    "project": "NewHorizon-IaC",
    "repository": "NewHorizon-IaC-Webshop",
    "path": "stacks/cloudfront/env/ppd/terraform.tfvars",
    "branch": "master",
    "size": 1234
  }
}
```

**Variables d'environnement** :
| Variable | Default | Description |
|----------|---------|-------------|
| ADO_PAT_SECRET_NAME | ops-dashboard/ado-pat | Secret Manager pour PAT |
| ADO_ORGANIZATION | rubix-group | Organisation Azure DevOps |
| ADO_DEFAULT_PROJECT | NewHorizon-IaC | Projet par defaut |

---

### fetch-cloudfront

Recupere les distributions CloudFront (format AWS natif).

**Input** :
```json
{
  "CrossAccountRoleArn": "arn:aws:iam::287223952330:role/iam-dig-ppd-dashboard-read"
}
```

**Output** :
```json
{
  "statusCode": 200,
  "distributions": [
    {
      "Id": "E1234567890",
      "ARN": "arn:aws:cloudfront::...",
      "Status": "Deployed",
      "Enabled": true,
      "Aliases": ["fr-webshop.preprod.rubix.com"],
      "Tags": {...}
    }
  ]
}
```

---

### fetch-ssm

Recupere les parametres SSM.

**Input** :
```json
{
  "CrossAccountRoleArn": "arn:aws:iam::287223952330:role/...",
  "Path": "/digital/ppd/",
  "Recursive": true
}
```

---

### fetch-secrets

Recupere les secrets Secrets Manager.

**Input** :
```json
{
  "CrossAccountRoleArn": "arn:aws:iam::287223952330:role/...",
  "SecretPatterns": ["/digital/ppd/app/mro-mi2/*"]
}
```

---

## Lambdas DNS

### prepare-dns-domains

Transforme les tfvars managed_domains/managed_api_domains en liste pour resolution.

**Input** :
```json
{
  "Project": "mro-mi2",
  "Env": "nh-ppd",
  "Instance": "MI2",
  "Environment": "ppd",
  "Country": "DE",
  "DomainKeys": ["fr-webshop"],
  "ManagedDomains": {
    "fr-webshop": {
      "domain": "fr-webshop.preprod.rubix.com",
      "country": "fr",
      "status": "active",
      "migrated": true
    }
  },
  "ManagedApiDomains": {
    "fr-api": {
      "country": "fr",
      "status": "pre-migration"
    }
  }
}
```

**Output** :
```json
{
  "domains": [
    {
      "hostname": "fr-webshop.preprod.rubix.com",
      "key": "fr-webshop",
      "metadata": {
        "domain": "fr-webshop.preprod.rubix.com",
        "country": "fr",
        "status": "active",
        "migrated": true,
        "type": "managed_domain"
      }
    }
  ],
  "count": 10,
  "summary": {
    "managedDomains": 8,
    "apiDomains": 2,
    "filtered": true
  }
}
```

**API Domains** : Hostname construit automatiquement `{country}-api.{env}.rubix-digital.net`

**Environment mapping** :
| Input | Output |
|-------|--------|
| stg | staging |
| ppd | preprod |
| prd | prod |

---

### resolve-dns

Resolution DNS pure (socket).

**Input** :
```json
{
  "Domains": [
    {
      "hostname": "fr-webshop.preprod.rubix.com",
      "key": "fr-webshop",
      "metadata": {...}
    },
    "simple-hostname.com"
  ],
  "RecordType": "A"
}
```

**Output** :
```json
{
  "statusCode": 200,
  "resolutions": [
    {
      "hostname": "fr-webshop.preprod.rubix.com",
      "key": "fr-webshop",
      "resolved": true,
      "resolvedIPs": ["1.2.3.4"],
      "responseTimeMs": 12.5,
      "metadata": {...}
    }
  ],
  "summary": {
    "total": 10,
    "resolved": 9,
    "failed": 1,
    "avgResponseTimeMs": 15.3
  }
}
```

---

### dns-checker (DEPRECIE)

Lambda monolithique remplacee par prepare-dns-domains + resolve-dns + process-dns.

**A supprimer apres migration complete.**

---

## Lambdas Process

### process-dns

Traitement DNS : Route53 queries, validation, calcul status.

**Input** :
```json
{
  "Project": "mro-mi2",
  "Env": "nh-ppd",
  "Instance": "MI2",
  "Environment": "ppd",
  "Country": "DE",
  "Source": "nh",
  "Resolutions": [
    {
      "key": "fr-webshop",
      "hostname": "fr-webshop.preprod.rubix.com",
      "resolved": true,
      "resolvedIPs": ["1.2.3.4"],
      "responseTimeMs": 12.5,
      "metadata": {...}
    }
  ],
  "HostedZoneId": "Z0123456789ABC",
  "CrossAccountRoleArn": "arn:aws:iam::...",
  "TfvarsSource": {...}
}
```

**Output** :
```json
{
  "statusCode": 200,
  "payload": {
    "status": "ok",
    "source": "nh",
    "country": "DE",
    "environment": "ppd",
    "summary": {
      "total": 131,
      "resolved": 131,
      "failed": 0,
      "route53Found": 131,
      "route53NotFound": 0,
      "matchExpected": 131,
      "mismatch": 0
    },
    "hostedZone": {...},
    "domains": [...],
    "issues": [],
    "healthy": true,
    "timestamp": "2026-01-12T10:30:00Z"
  }
}
```

**Thresholds** :
| Seuil | Valeur |
|-------|--------|
| RESPONSE_TIME_WARNING | 200ms |
| RESPONSE_TIME_CRITICAL | 500ms |

**Transformation hostname** :
```
fr-webshop-bo.preprod.rubix.com -> fr-webshop-bo.preprod.rubix-digital.net
```

---

### process-cloudfront

Traitement CloudFront : filtrage, transformation, enrichissement tfvars.

**Input** :
```json
{
  "Project": "mro-mi2",
  "Env": "nh-ppd",
  "Instance": "MI2",
  "Environment": "ppd",
  "Distributions": [...],
  "EnrichmentData": {...},
  "Filters": {
    "ProjectTag": "mro-mi2",
    "Country": "FR"
  }
}
```

**Output** :
```json
{
  "statusCode": 200,
  "payload": {
    "status": "ok",
    "distributions": [...],
    "summary": {
      "totalDistributions": 23,
      "deployed": 23,
      "enabled": 23,
      "withWaf": 20
    },
    "issues": [],
    "healthy": true
  }
}
```

---

### process-efs-replication

Traitement EFS replication status.

**Input** :
```json
{
  "SourceFileSystemId": "fs-xxxxxxxx",
  "ReplicationStatus": {...},
  "MaxSyncDelayMinutes": 60
}
```

**Seuils de status** :
| Status | Conditions |
|--------|------------|
| ok | ENABLED, sync < 30min, diff < 2% |
| warning | sync 30-60min, diff 2-5%, PAUSING/PAUSED |
| critical | ERROR/DELETING, sync > 60min, diff > 5% |

---

### process-tgw

Traitement Transit Gateway.

**Input** :
```json
{
  "TransitGateway": {...},
  "Attachments": [...],
  "RouteTables": [...],
  "ExpectedAttachments": [...]
}
```

---

### process-nodes

Traitement K8s Nodes avec enrichissement EC2 optionnel.

**Input** :
```json
{
  "nodes": [...],
  "cluster_name": "k8s-dig-stg-webshop",
  "cluster_version": "1.34",
  "ec2_data": {
    "EC2Data": {"Reservations": [...]},
    "EC2StatusData": {"InstanceStatuses": [...]}
  }
}
```

**Fonctionnalites** :
- Parse nodes K8s (conditions, capacity, allocatable)
- Extraction instance ID depuis `providerID`
- Correlation EC2 ↔ K8s nodes (si `ec2_data` fourni)
- Detection issues : NotReady, Memory/Disk/PID Pressure, Cordoned
- Detection issues EC2 : NotFound, NotRunning, StatusCheckFailed

**Output** :
```json
{
  "status": "ok|warning|critical",
  "summary": {
    "total": 8,
    "ready": 8,
    "notReady": 0,
    "cordoned": 0,
    "byNodegroup": {...},
    "byInstanceType": {...},
    "byZone": {...},
    "ec2": {
      "instances_found": 8,
      "instances_running": 8,
      "status_checks_ok": 8
    }
  },
  "nodes": [...],
  "issues": [...],
  "ec2_enriched": true
}
```

---

### process-pods

Traitement K8s Pods status.

---

### process-services

Traitement K8s Services status.

---

### process-ingress

Traitement K8s Ingress status.

---

### process-pvc

Traitement K8s PVC status.

---

### process-secrets

Traitement K8s Secrets status.

**SecretProviderType** :
| Type | labelSelector |
|------|---------------|
| csi | secrets-store.csi.k8s.io/managed=true |
| external-secrets | reconcile.external-secrets.io/managed=true |

---

### process-alb

Traitement ALB status.

---

### process-rds

Traitement des donnees RDS/Aurora cluster depuis les resultats Step Function.

**Input** :
```json
{
  "Input": {
    "Project": "mro-mi2",
    "Env": "nh-ppd",
    "Instance": "MI2",
    "Environment": "ppd",
    "DbClusterIdentifier": "rds-dig-ppd-mro-mi2"
  },
  "Timestamp": "2026-01-12T10:30:00Z",
  "Cluster": { "...raw DescribeDBClusters result..." },
  "ParameterGroupResults": [
    {"type": "cluster", "parameters": [...]},
    {"type": "instance", "parameters": [...], "instanceInfo": {...}}
  ]
}
```

**Output** :
```json
{
  "project": "mro-mi2",
  "env": "nh-ppd",
  "category": "infra",
  "check_type": "rds",
  "payload": {
    "status": "ok",
    "healthy": true,
    "cluster": {
      "identifier": "rds-dig-ppd-mro-mi2",
      "status": "available",
      "engine": "aurora-mysql",
      "engineVersion": "8.0.mysql_aurora.3.04.0"
    },
    "parameterGroups": {
      "cluster": {"name": "...", "parameters": {...}, "parameterCount": 5},
      "instance": {"name": "...", "parameters": {...}, "parameterCount": 3}
    },
    "instances": [...],
    "summary": {
      "instanceCount": 2,
      "writerCount": 1,
      "readerCount": 1,
      "clusterParameterCount": 5,
      "instanceParameterCount": 3
    },
    "issues": []
  },
  "updated_by": "step-function:infra-rds-checker"
}
```

**Filtrage parametres** : Seuls les parametres avec `Source=user` sont inclus.

**Checks effectues** :
- Cluster status (available, failed, etc.)
- Writer instance presente
- Deletion protection activee
- Backup retention >= 7 jours
- Parameter group status (pending-reboot)

---

## Lambdas Compare

Les Lambdas compare utilisent la terminologie Source/Destination.

### compare-secrets-manager

Compare secrets entre Source et Destination avec mapping de chemins.

**Input** :
```json
{
  "SourceState": {...},
  "DestinationState": {...},
  "Instance": "MI2",
  "Environment": "ppd"
}
```

**Path Mapping** :
```
/rubix/mi2-preprod/app/hybris/config-keys
-> /digital/ppd/app/mro-mi2/hybris/config-keys

/rubix/mi2-preprod/mi2-preprod-eks-cluster/prod_uk
-> /digital/ppd/infra/databases/rds-dig-ppd-mro-mi2/prod_uk
```

**Categories** :
| Categorie | Description |
|-----------|-------------|
| synced | Identiques |
| differs_expected | Differences attendues |
| differs_unexpected | Differences non configurees |
| only_source_unexpected | Secret non migre |
| only_destination_unexpected | Secret NH specifique |

---

### compare-cloudfront

Compare distributions CloudFront.

**Matching** :
- Mode tfvars : champ `key`
- Mode discovery : extraction du premier alias
- Suffixes `-nh` retires pour matching

---

### compare-dns

Compare etats DNS resolution.

---

### compare-ssm

Compare parametres SSM.

---

### compare-secrets

Compare K8s Secrets.

---

### compare-pods / compare-services / compare-pvc / compare-ingress

Compare ressources K8s entre Source et Destination.

---

### compare-security-groups / compare-alb

Compare ressources network.

---

### compare-rds

Compare etats RDS/Aurora cluster entre Source et Destination.

**Input** :
```json
{
  "project": "mro-mi2",
  "sourceEnv": "legacy-stg",
  "destinationEnv": "nh-stg",
  "source_state": {"hasData": true, "itemData": {...}},
  "destination_state": {"hasData": true, "itemData": {...}},
  "ExpectedDifferences": {
    "clusterConfig": ["storageEncrypted"],
    "clusterParameters": ["binlog_format"]
  }
}
```

**Output** :
```json
{
  "status": "differs",
  "sourceEnv": "legacy-stg",
  "destinationEnv": "nh-stg",
  "project": "mro-mi2",
  "clusterConfigComparison": {
    "status": "differs",
    "synced": ["engine", "engineVersion", "multiAZ"],
    "expectedDifferences": [],
    "unexpectedDifferences": [
      {"parameter": "storageEncrypted", "source": "False", "destination": "True"}
    ]
  },
  "parameterGroupComparison": {
    "cluster": {
      "status": "differs",
      "synced": [],
      "onlyDestinationExpected": [{"parameter": "binlog_format", "value": "ROW"}],
      "onlyDestinationUnexpected": [{"parameter": "character_set_database", "value": "utf8mb4"}]
    },
    "instance": {"status": "synced", "synced": [], ...}
  },
  "summary": {
    "sourceFound": true,
    "destinationFound": true,
    "clusterConfig": "differs",
    "clusterParameters": "differs",
    "instanceParameters": "synced",
    "totalExpectedDiffs": 1,
    "totalUnexpectedDiffs": 3
  },
  "issues": [...]
}
```

**Comparaisons** :
- **clusterConfig** : engine, engineVersion, multiAZ, storageEncrypted, deletionProtection, engineMode, iamDatabaseAuthenticationEnabled
- **clusterParameters** : Parametres user-modified
- **instanceParameters** : Parametres user-modified

**Expected differences** : Parametres declares comme attendus ne sont pas comptabilises comme problemes.

---

## Lambda Application

### app-component-checker

Health checker generique pour composants applicatifs Rubix avec architecture plugin extensible.

**Localisation** : `lambdas/app-component-checker/`

**Structure** :
```
app-component-checker/
├── app_component_checker.py    # Point d'entree Lambda, plugin registry
└── plugins/
    ├── __init__.py
    ├── base_plugin.py          # Classe abstraite BasePlugin
    ├── smui_plugin.py          # Plugin SMUI
    ├── apache_plugin.py        # Plugin Apache
    └── solr_plugin.py          # Plugin Solr (leader/follower)
```

**Composants supportes** :
| Composant | Plugin | Status |
|-----------|--------|--------|
| smui | smui_plugin.py | Deploye, teste |
| apache | apache_plugin.py | Deploye, teste |
| solr | solr_plugin.py | Deploye, teste |
| hybris | hybris_plugin.py | Planned |

**Interface Lambda** :
```python
def lambda_handler(event: dict, context) -> dict:
    """
    Args:
        event: {
            "Project": "mro-mi2",
            "Env": "nh-stg",
            "Component": "smui",
            "ClusterName": "k8s-dig-stg-webshop",
            "Namespace": "rubix-mro-mi2-staging",
            "Pods": [...],     # Pod data from eks:call
            "Logs": [...]      # Logs from Step Function Map
        }
    Returns:
        {"statusCode": 200, "body": "{...}"}
    """
```

**Architecture Plugin** :

Chaque plugin herite de `BasePlugin` et implemente :
- `run_all_checks(pods, logs)` : Execute tous les checks
- `get_checks()` : Liste des checks disponibles

Les checks communs (dans BasePlugin) :
- `check_pod_status()` : Verifie pods Running/Ready
- `check_container_restarts()` : Compte les restarts
- `check_image_pull()` : Detecte ImagePullBackOff, ErrImagePull (image inexistante ou nettoyee par ECR lifecycle)

**SMUI Plugin Checks** :

| Check | Description | Pattern detecte |
|-------|-------------|-----------------|
| pod_status | Pod running et ready | K8s pod status |
| container_restarts | Restarts < 5 | K8s container status |
| image_pull | Images pull OK | `ImagePullBackOff`, `ErrImagePull` (absence) |
| jdbc_connection | HikariPool demarrage | `HikariPool-mysql - Start completed` |
| jdbc_url_valid | jdbcUrl non null | Absence de `jdbcUrl, null` |
| database_access | Acces DB OK | Absence de `Access denied for user` |
| application_startup | App demarree | `Application started`, `Listening for HTTP` |
| application_errors | Comptage erreurs | `ERROR`, `Exception` |
| hikari_pool_health | Pool connexions sain | Absence de `Connection timeout` |

**Apache Plugin Checks** :

| Check | Description | Pattern detecte |
|-------|-------------|-----------------|
| pod_status | Pod running et ready | K8s pod status |
| container_restarts | Restarts < 5 | K8s container status |
| image_pull | Images pull OK | `ImagePullBackOff`, `ErrImagePull` (absence) |
| media_config | Variables d'env media presentes | `SHARED_DATA_MEDIA_EFS_ID`, `SHARED_DATA_MEDIA_SUB_PATH` |
| media_accessibility | Pas de 404 sur /medias/, /_ui/, /assets/ | `GET /medias/.* 404` |
| http_errors | Comptage erreurs 5xx | `" 500 "`, `" 502 "`, `" 503 "` |
| upstream_health | Connectivite backend | `AH0111*`, `upstream timed out`, `Connection refused` |
| config_errors | Erreurs config Apache/SSL | `AH00526`, `SSL`, `Permission denied` |

> **Note**: Le label selector par defaut pour Apache est `app.kubernetes.io/name=apache`, mais sur les clusters legacy c'est souvent `app=apache`. Utiliser le parametre `LabelSelector` pour overrider.

**Solr Plugin Checks** :

| Check | Description | Pattern detecte |
|-------|-------------|-----------------|
| pod_status | Pods running et ready | K8s pod status |
| container_restarts | Restarts < 5 | K8s container status |
| image_pull | Images pull OK | `ImagePullBackOff`, `ErrImagePull` (absence) |
| solr_started | Solr demarre | `SolrCore.*registered`, `Server.*started` |
| replication_status | Sync leader/follower | `Follower in sync with leader`, analyse versions |
| core_status | Cores charges | `SolrCore.*registered`, absence de `CoreInitializationException` |
| query_errors | Erreurs requetes | `SolrException`, `Update failed`, `Query timeout` |
| memory_issues | Problemes memoire | `OutOfMemoryError`, `GC overhead limit exceeded` |
| general_errors | Comptage erreurs | `ERROR`, `SEVERE`, `Exception` |

> **Note**: La Step Function detecte automatiquement le container name :
> - Pods contenant "leader" dans le nom → container `solrleader`
> - Pods contenant "follower" dans le nom → container `solrfollower`
>
> **Analyse de replication avancee** : Le plugin parse les logs IndexFetcher pour comparer les versions leader/follower et detecter les decalages de replication.

**Ajout d'un nouveau plugin** :

1. Creer `plugins/{component}_plugin.py`
2. Heriter de `BasePlugin`
3. Implementer `get_checks()` retournant la liste des methodes de check
4. Ajouter l'import dans `app_component_checker.py`

```python
# plugins/hybris_plugin.py
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from app_component_checker import register_plugin
from plugins.base_plugin import BasePlugin, CheckResult

@register_plugin("hybris")
class HybrisPlugin(BasePlugin):
    def get_checks(self) -> list:
        return [
            self.check_pod_status,         # herite de BasePlugin
            self.check_container_restarts, # herite de BasePlugin
            self.check_startup_complete,
            self.check_cronjobs,
        ]

    def check_startup_complete(self) -> CheckResult:
        # Utilise self.pods et self.logs
        return CheckResult(name="startup_complete", status="ok", message="...")
```

```python
# app_component_checker.py - ajouter l'import
from plugins import hybris_plugin  # noqa: E402
```

---

## Lambda Network

### analyze-security-groups

Analyse security groups pour compliance.

**Analyses** :
- Ports sensibles exposes (SSH, RDP, DB)
- Regles 0.0.0.0/0
- Security groups non utilises
- Descriptions manquantes

---

## Lambdas a supprimer

| Lambda | Remplacee par |
|--------|---------------|
| dns-checker | prepare-dns-domains + resolve-dns + process-dns |
| fetch-tfvars | fetch-ado-file |

---

## Voir aussi

- [Step Functions Reference](step-functions-reference.md) - Documentation des Step Functions
- [Refactoring Plan CloudFront & DNS](refactoring-plan-cloudfront-dns.md) - Architecture modulaire
