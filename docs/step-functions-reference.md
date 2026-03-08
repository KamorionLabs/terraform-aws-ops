# Step Functions Reference

Documentation complete des Step Functions du projet Ops Dashboard.

---

## Vue d'ensemble

| Step Function | Categorie | Deploye | Teste |
|---------------|-----------|---------|-------|
| **K8s Checkers** | | | |
| k8s-nodes-checker | k8s | Oui | Oui |
| k8s-pods-readiness-checker | k8s | Oui | Oui |
| k8s-services-checker | k8s | Oui | Oui |
| k8s-pvc-status-checker | k8s | Oui | Oui |
| k8s-ingress-status-checker | k8s | Oui | Oui |
| k8s-secrets-sync-checker | k8s | Oui | Oui |
| **K8s Compare** | | | |
| k8s-pods-compare | k8s | Non | Non |
| k8s-services-compare | k8s | Non | Non |
| k8s-pvc-compare | k8s | Non | Non |
| k8s-ingress-compare | k8s | Non | Non |
| k8s-secrets-compare | k8s | Non | Non |
| **Network Checkers** | | | |
| net-tgw-checker | net | Oui | Non |
| net-dns-checker | net | Oui | Oui |
| net-alb-checker | net | Oui | Oui |
| net-cloudfront-checker | net | Oui | Oui |
| net-sg-checker | net | Oui | Oui |
| **Network Compare** | | | |
| net-dns-compare | net | Non | Non |
| net-alb-compare | net | Non | Non |
| net-cloudfront-compare | net | Oui | Oui |
| net-sg-compare | net | Non | Non |
| **Config** | | | |
| config-ssm-checker | config | Oui | Non |
| config-ssm-compare | config | Oui | Non |
| config-sm-checker | config | Oui | Oui |
| config-sm-compare | config | Oui | Oui |
| **Infra** | | | |
| infra-rds-checker | infra | Oui | Oui |
| infra-rds-compare | infra | Oui | Oui |
| readiness-checker | infra | Non | Non |
| repl-efs-sync-checker | repl | Non | Non |
| **Application** | | | |
| app-component-health-checker | app | Oui | Oui (NH + Legacy) |

---

## Principes d'architecture

### Step Functions first

Privilegier les appels directs AWS SDK :

```json
// Appel direct aws-sdk
{
  "Type": "Task",
  "Resource": "arn:aws:states:::aws-sdk:rds:describeDBClusters",
  "Parameters": {
    "DbClusterIdentifier.$": "$.RdsClusterIdentifier"
  }
}

// Appel direct eks:call pour K8s API
{
  "Type": "Task",
  "Resource": "arn:aws:states:::eks:call",
  "Parameters": {
    "ClusterName.$": "$.ClusterName",
    "Method": "GET",
    "Path": "/api/v1/namespaces/hybris/pods"
  }
}
```

**Avantages** :
- Moins de code a maintenir
- Pas de cold start Lambda
- Retry/error handling natif
- Cout reduit

### Terminologie Source/Destination

Les Step Functions de comparaison utilisent une terminologie generique :

- **Source** : Environnement de reference (peut etre Legacy, ou tout autre env)
- **Destination** : Environnement cible a comparer
- **SourceStateKey** : Cle DynamoDB format `project#env|check_type`
- **DestinationStateKey** : Cle DynamoDB format `project#env|check_type`

---

## Infrastructure Checkers

### readiness-checker

Verifie l'etat des composants infrastructure (EKS, RDS, EFS, Pods).

```json
{
  "Project": "mro-mi2",
  "Env": "nh-ppd",
  "Instance": "MI2",
  "Environment": "ppd",
  "EksClusterName": "rubix-dig-ppd-webshop",
  "RdsClusterIdentifier": "rubix-dig-ppd-aurora",
  "EfsFileSystemId": "fs-xxxxxxxx",
  "Namespace": "hybris"
}
```

**Format de sortie** : `project/env/category: "infra"/check_type: "readiness"`

---

### infra-rds-checker

Verifie l'etat des clusters RDS/Aurora incluant les parameter groups.

```json
{
  "Project": "mro-mi2",
  "Env": "nh-ppd",
  "Instance": "MI2",
  "Environment": "ppd",
  "DbClusterIdentifier": "rds-dig-ppd-mro-mi2",
  "CrossAccountRoleArn": "arn:aws:iam::287223952330:role/iam-dig-ppd-dashboard-read"
}
```

**Exemple Legacy** :
```json
{
  "Project": "mro-mi2",
  "Env": "legacy-stg",
  "Instance": "MI2",
  "Environment": "staging",
  "DbClusterIdentifier": "mi2-staging-eks-cluster",
  "CrossAccountRoleArn": "arn:aws:iam::073290922796:role/rubix-refresh-source-role"
}
```

**Format de sortie** : `project/env/category: "infra"/check_type: "rds"`

**Parametres** :
| Parametre | Requis | Description |
|-----------|--------|-------------|
| Project | Oui | Projet Dashborion |
| Env | Oui | Environnement avec prefixe |
| Instance | Oui | Instance webshop |
| Environment | Oui | Environnement court |
| DbClusterIdentifier | Oui | Identifiant du cluster RDS/Aurora |
| CrossAccountRoleArn | Non | Role cross-account |

**Donnees collectees** :
- Cluster status, engine, version, endpoints
- Cluster parameter group (parametres user-modified uniquement)
- Instance parameter group (parametres user-modified uniquement)
- Instance status, class, AZ, performance insights
- Backup configuration (retention, windows)
- Storage configuration (type, encryption)

**Seuils de status** :
| Status | Conditions |
|--------|------------|
| ok | Cluster available, writer present, deletion protection enabled |
| warning | Backup retention < 7 days, deletion protection disabled, parameter pending-reboot |
| critical | Cluster failed/failing-over, no writer instance |

**Roles cross-account** :
| Env | Account | Role |
|-----|---------|------|
| nh-stg | 281127105461 | iam-dig-stg-dashboard-read |
| nh-ppd | 287223952330 | iam-dig-ppd-dashboard-read |
| nh-prd | 366483377530 | iam-dig-prd-dashboard-read |
| legacy | 073290922796 | rubix-refresh-source-role |

---

### infra-rds-compare

Compare les etats RDS/Aurora entre Source et Destination.

```json
{
  "Project": "mro-mi2",
  "SourceEnv": "legacy-stg",
  "DestinationEnv": "nh-stg",
  "SourceStateKey": "mro-mi2#legacy-stg|infra:rds",
  "DestinationStateKey": "mro-mi2#nh-stg|infra:rds",
  "ExpectedDifferences": {
    "clusterConfig": ["storageEncrypted"],
    "clusterParameters": ["binlog_format"]
  }
}
```

**Format de sortie** : `project/env/category: "infra"/check_type: "rds"`
**DynamoDB** : `pk={Project}#comparison:{SourceEnv}:{DestinationEnv}`

**Parametres** :
| Parametre | Requis | Description |
|-----------|--------|-------------|
| Project | Oui | Projet Dashborion |
| SourceEnv | Oui | Environnement source |
| DestinationEnv | Oui | Environnement destination |
| SourceStateKey | Oui | Cle DynamoDB source (format: project#env\|check_type) |
| DestinationStateKey | Oui | Cle DynamoDB destination |
| ExpectedDifferences | Non | Differences attendues (default: {}) |

**ExpectedDifferences structure** :
```json
{
  "clusterConfig": ["storageEncrypted", "deletionProtection"],
  "clusterParameters": ["binlog_format"],
  "instanceParameters": []
}
```

**Comparaisons effectuees** :
- **clusterConfig** : engine, engineVersion, multiAZ, storageEncrypted, deletionProtection, engineMode, iamDatabaseAuthenticationEnabled
- **clusterParameters** : Parametres user-modified du cluster parameter group
- **instanceParameters** : Parametres user-modified de l'instance parameter group

**Categories de resultats** :
| Categorie | Description |
|-----------|-------------|
| synced | Valeurs identiques |
| expectedDifferences | Differences declarees dans ExpectedDifferences |
| unexpectedDifferences | Differences non declarees |
| onlySource | Parametre present uniquement en source |
| onlyDestination | Parametre present uniquement en destination |

**Seuils de status** :
| Status | Conditions |
|--------|------------|
| synced | Toutes les differences sont attendues ou synced |
| differs | Au moins une difference inattendue |

---

### repl-efs-sync-checker

Verifie l'etat de replication EFS cross-region.

```json
{
  "Project": "mro-mi1",
  "Env": "legacy-ppd",
  "Instance": "MI1",
  "Environment": "ppd",
  "SourceFileSystemId": "fs-xxxxxxxx",
  "DestinationRegion": "eu-central-1",
  "MaxSyncDelayMinutes": 60,
  "CrossAccountRoleArn": "arn:aws:iam::073290922796:role/ops-dashboard-read"
}
```

**Format de sortie** : `project/env/category: "repl"/check_type: "efs-sync"`

**Seuils de status** :
| Status | Conditions |
|--------|------------|
| ok | replication ENABLED, sync delay < 30 min, size diff < 2% |
| warning | sync delay 30-60 min, size diff 2-5%, replication PAUSING/PAUSED |
| critical | replication ERROR/DELETING, sync delay > 60 min, size diff > 5% |

---

## Kubernetes Checkers

### k8s-nodes-checker

Verifie l'etat des nodes Kubernetes et optionnellement enrichit avec les donnees EC2.

```json
{
  "ClusterName": "k8s-dig-stg-webshop",
  "CrossAccountRoleArn": "arn:aws:iam::281127105461:role/iam-dig-stg-dashboard-read",
  "IncludeEC2": true
}
```

**Format de sortie** : `_cluster#clusterName/category: "k8s"/check_type: "nodes"`

**Parametres** :
| Parametre | Requis | Description |
|-----------|--------|-------------|
| ClusterName | Oui | Nom du cluster EKS |
| CrossAccountRoleArn | Non | Role pour acces cross-account |
| IncludeEC2 | Non | Active l'enrichissement EC2 (default: false) |

**Enrichissement EC2** (si `IncludeEC2: true`) :
- Correlation nodes K8s ↔ instances EC2 via `providerID`
- Etat des instances (`running`, `stopped`, etc.)
- Status checks EC2 (`system_status`, `instance_status`)

**Permissions requises pour EC2** :
- `ec2:DescribeInstances`
- `ec2:DescribeInstanceStatus`

**Ressources collectees** :
- Nodes K8s (`/api/v1/nodes`)
- EC2 Instances (filtre `tag:kubernetes.io/cluster/{cluster}`)
- EC2 Instance Status

**Seuils de status** :
| Status | Conditions |
|--------|------------|
| ok | Tous nodes Ready, EC2 running, status checks OK |
| warning | Nodes cordones, PID pressure, EC2 non trouve |
| critical | Nodes NotReady, Memory/Disk pressure, EC2 not running, status check failed |

**Issues detectees** :
| Issue | Severite | Description |
|-------|----------|-------------|
| NodeNotReady | critical | Node en etat NotReady |
| MemoryPressure | critical | Node avec memory pressure |
| DiskPressure | critical | Node avec disk pressure |
| PIDPressure | warning | Node avec PID pressure |
| Unschedulable | warning | Node cordone |
| EC2NotFound | warning | Instance EC2 non trouvee pour le node |
| EC2NotRunning | critical | Instance EC2 non running |
| EC2StatusCheckFailed | critical | Status check EC2 failed |

---

### k8s-ingress-status-checker

```json
{
  "Project": "mro-mi2",
  "Env": "nh-stg",
  "Instance": "MI2",
  "Environment": "stg",
  "ClusterName": "k8s-dig-stg-webshop",
  "Namespace": "rubix-mro-mi2-staging",
  "CrossAccountRoleArn": "arn:aws:iam::281127105461:role/iam-dig-stg-refresh-destination-role",
  "EnableTargetGroupBindings": false
}
```

**Format de sortie** : `project/env/category: "k8s"/check_type: "ingress"`

**Parametres** :
| Parametre | Requis | Description |
|-----------|--------|-------------|
| EnableTargetGroupBindings | Non | Active collecte TargetGroupBindings (default: false) |
| CrossAccountRoleArn | Non | Role pour acces cross-account |

**Ressources collectees** :
- Ingresses (`networking.k8s.io/v1`)
- Services (filtre LoadBalancer pour SFTP)
- TargetGroupBindings (`elbv2.k8s.aws/v1beta1`) - si active

**Seuils de status** :
| Status | Conditions |
|--------|------------|
| ok | Tous ingresses healthy |
| warning | Ingresses sans TLS, annotations manquantes |
| critical | Ingresses not synced, services manquants |

---

### k8s-secrets-sync-checker

```json
{
  "Project": "mro-mi2",
  "Env": "legacy-stg",
  "Instance": "MI2",
  "Environment": "stg",
  "SecretProviderType": "csi",
  "ClusterName": "rubix-nonprod",
  "Namespace": "mi2-staging",
  "CrossAccountRoleArn": "arn:aws:iam::073290922796:role/step_function_eks_nonprod"
}
```

**Format de sortie** : `project/env/category: "k8s"/check_type: "secrets"`

**SecretProviderType** :
| Type | labelSelector |
|------|---------------|
| csi | `secrets-store.csi.k8s.io/managed=true` |
| external-secrets | `reconcile.external-secrets.io/managed=true` |

---

### k8s-secrets-compare

```json
{
  "Project": "mro-mi2",
  "Env": "nh-stg",
  "Instance": "MI2",
  "Environment": "stg",
  "SourceStateKey": "mro-mi2#legacy-stg|k8s-secrets",
  "DestinationStateKey": "mro-mi2#nh-stg|k8s-secrets"
}
```

**Format de sortie** : `project/env/category: "k8s"/check_type: "secrets-compare"`

**Format StateKeys** : `project#env|check_type`

**Seuils de status** :
| Status | Conditions |
|--------|------------|
| synced | Meme nombre de secrets, memes providers |
| differs | Differences de count, sync status, ou secrets manquants |

---

## Network Checkers

### net-tgw-checker

```json
{
  "Project": "network",
  "Env": "global",
  "Instance": "TGW",
  "Environment": "shared",
  "TransitGatewayId": "tgw-xxxxxxxxxxxxxxxxx",
  "CrossAccountRoleArn": "arn:aws:iam::588738610824:role/ops-dashboard-read",
  "ExpectedAttachments": [
    {"name": "rubix-dig-stg-webshop", "accountId": "281127105461", "type": "vpc"},
    {"name": "rubix-dig-ppd-webshop", "accountId": "287223952330", "type": "vpc"},
    {"name": "rubix-dig-prd-webshop", "accountId": "366483377530", "type": "vpc"}
  ]
}
```

**Format de sortie** : `project/env/category: "net"/check_type: "tgw"`

**Seuils de status** :
| Status | Conditions |
|--------|------------|
| ok | TGW available, tous attachments presents, pas de blackhole |
| warning | Attachments en etat transitoire, blackhole detectes |
| critical | TGW non available, attachments manquants/rejected/failed |

---

### net-dns-checker

Architecture modulaire : `fetch-ado-file` → `prepare-dns-domains` → `resolve-dns` → `process-dns`

**Mode tfvars** :
```json
{
  "Project": "mro-mi2",
  "Env": "nh-ppd",
  "Environment": "ppd",
  "Source": "nh",
  "TfvarsSource": {
    "cloudfront": "stacks/cloudfront/env/ppd/terraform.tfvars",
    "eks": "stacks/eks/env/ppd/terraform.tfvars",
    "repository": "NewHorizon-IaC-Webshop",
    "project": "NewHorizon-IaC",
    "branch": "master"
  },
  "HostedZoneId": "Z0958769182PISZL4XPF8",
  "CrossAccountRoleArn": "arn:aws:iam::287223952330:role/iam-dig-ppd-dashboard-read"
}
```

**Mode filtrage par cles** :
```json
{
  "Project": "mro-mi2",
  "Env": "nh-ppd",
  "TfvarsSource": { ... },
  "DomainKeys": ["mi2-webshop-bo", "mi2-api"]
}
```

**Mode test direct** :
```json
{
  "Project": "mro-mi2",
  "Env": "dns-test",
  "TestDomains": [
    "mi2-api.preprod.rubix-digital.net",
    {"hostname": "mi2-bo.preprod.rubix-digital.net", "key": "mi2-bo", "country": "DE"}
  ]
}
```

**Parametres** :
| Parametre | Requis | Description |
|-----------|--------|-------------|
| Project | Oui | Projet Dashborion |
| Env | Oui | Environnement |
| TfvarsSource ou TestDomains | Oui | Au moins un requis |
| Country | Non | Filtre par pays |
| HostedZoneId | Non | Zone Route53 pour enrichissement |
| CrossAccountRoleArn | Non | Role cross-account |
| DomainKeys | Non | Liste de cles pour filtrer tfvars |

**TfvarsSource** :
| Champ | Description |
|-------|-------------|
| cloudfront | Chemin tfvars CloudFront (managed_domains) |
| eks | Chemin tfvars EKS (managed_api_domains) - optionnel |
| repository | Repo Azure DevOps |
| project | Projet Azure DevOps |
| branch | Branche (default: master) |

**Enrichissement Route53** :

Transformation hostname : `fr-webshop-bo.preprod.rubix.com` → `fr-webshop-bo.preprod.rubix-digital.net`

Champs enrichis :
- `nhHostname` : Hostname NH transforme
- `route53` : Details record (type, value, ttl, isAlias)
- `matchExpected` : true si resolution = record Route53

**Summary avec Route53** :
```json
{
  "total": 131,
  "resolved": 131,
  "route53Found": 131,
  "route53NotFound": 0,
  "matchExpected": 131,
  "mismatch": 0
}
```

**API Domains** :
- Pattern hostname : `{country}-api.{env}.rubix-digital.net`
- Mapping env : `stg`→`staging`, `ppd`→`preprod`, `prd`→`prod`

---

### net-dns-compare

```json
{
  "Project": "mro-mi2",
  "Env": "nh-stg",
  "Instance": "MI2",
  "Environment": "stg",
  "SourceStateKey": "mro-mi2#legacy-stg|dns-resolution",
  "DestinationStateKey": "mro-mi2#nh-stg|dns-resolution"
}
```

**Format de sortie** : `project/env/category: "net"/check_type: "dns-compare"`

**Seuils de status** :
| Status | Conditions |
|--------|------------|
| ok | All domains resolved, matching avec differences attendues |
| warning | Minor differences, missing records |
| critical | DNS failures, significant mismatches |

---

### net-alb-checker

**Par tags** :
```json
{
  "Project": "mro-mi2",
  "Env": "nh-ppd",
  "Instance": "MI2",
  "Environment": "ppd",
  "LoadBalancerTags": {
    "kubernetes.io/cluster/k8s-dig-ppd-webshop": "owned"
  },
  "CrossAccountRoleArn": "arn:aws:iam::287223952330:role/iam-dig-ppd-dashboard-read"
}
```

**Par ARN** :
```json
{
  "Project": "mro-mi2",
  "Env": "nh-ppd",
  "Instance": "MI2",
  "Environment": "ppd",
  "LoadBalancerArns": [
    "arn:aws:elasticloadbalancing:eu-central-1:287223952330:loadbalancer/app/k8s-hybris-xxxxx/yyyyy"
  ],
  "CrossAccountRoleArn": "arn:aws:iam::287223952330:role/iam-dig-ppd-dashboard-read"
}
```

**Parametres** :
| Parametre | Requis | Description |
|-----------|--------|-------------|
| Project | Oui | Projet Dashborion |
| Env | Oui | Environnement |
| Instance | Oui | Instance webshop |
| Environment | Oui | Environnement (stg, ppd, prd) |
| LoadBalancerArns ou LoadBalancerTags | Oui | Au moins un requis |
| CrossAccountRoleArn | Non | Role cross-account |

**Donnees collectees** :
- Load Balancers (state, scheme, type, security groups, AZs)
- Listeners (protocols, ports, SSL policies)
- Target Groups (health check config)
- Target Health (healthy, unhealthy, draining)

**Seuils de status** :
| Status | Conditions |
|--------|------------|
| ok | Tous ALBs actifs, tous targets healthy |
| warning | Targets draining, SSL policies anciennes |
| critical | ALBs non actifs, targets unhealthy |

---

### net-sg-checker

```json
{
  "Project": "mro-mi2",
  "Env": "nh-ppd",
  "Instance": "MI2",
  "Environment": "ppd",
  "VpcId": "vpc-0123456789abcdef0",
  "Filters": [
    {
      "Name": "tag:kubernetes.io/cluster/k8s-dig-ppd-webshop",
      "Values": ["owned", "shared"]
    }
  ],
  "CrossAccountRoleArn": "arn:aws:iam::287223952330:role/iam-dig-ppd-dashboard-read"
}
```

**Parametres** :
| Parametre | Requis | Description |
|-----------|--------|-------------|
| Project | Oui | Projet Dashborion |
| Env | Oui | Environnement |
| Instance | Oui | Instance webshop |
| Environment | Oui | Environnement |
| VpcId | Oui | ID VPC pour filtrer ENIs |
| Filters | Oui | Filtres EC2 DescribeSecurityGroups |
| CrossAccountRoleArn | Non | Role cross-account |

**Analyse effectuee** :
- Ports sensibles exposes (SSH, RDP, DB)
- Regles trop permissives (0.0.0.0/0)
- Security groups non utilises
- Descriptions manquantes

**Seuils de status** :
| Status | Conditions |
|--------|------------|
| ok | Tous SGs compliant |
| warning | CIDRs larges, descriptions manquantes, SGs non utilises |
| critical | Ports sensibles exposes, regles all-traffic |

---

### net-cloudfront-checker

**Mode tfvars (NH)** :
```json
{
  "Project": "mro-mi2",
  "Env": "nh-ppd",
  "Instance": "MI2",
  "Environment": "ppd",
  "TfvarsSource": {
    "repository": "NewHorizon-IaC-Webshop",
    "path": "stacks/cloudfront/env/ppd/terraform.tfvars",
    "project": "NewHorizon-IaC",
    "branch": "master"
  },
  "CrossAccountRoleArn": "arn:aws:iam::287223952330:role/iam-dig-ppd-dashboard-read"
}
```

**Mode discovery (Legacy)** :
```json
{
  "Project": "mro-mi2",
  "Env": "legacy-ppd",
  "Instance": "MI2",
  "Environment": "ppd",
  "DiscoveryMode": true,
  "CrossAccountRoleArn": "arn:aws:iam::073290922796:role/dashboard-read"
}
```

**Format de sortie** : `project/env/category: "net"/check_type: "cloudfront"`

**Parametres** :
| Parametre | Requis | Description |
|-----------|--------|-------------|
| Project | Oui | Projet Dashborion |
| Env | Oui | Environnement avec prefixe |
| TfvarsSource ou DiscoveryMode | Oui | Au moins un requis |
| Instance | Non | Instance webshop |
| Environment | Non | Environnement court (requis en discovery) |
| CrossAccountRoleArn | Non | Role cross-account |
| Country | Non | Filtre par pays (mode tfvars) |

**Mode discovery - Parsing aliases** :

Extraction automatique :
- **environment** : stg (.staging.), ppd (.preprod.), prd (.prod.)
- **country** : Prefixe du domaine (fr, uk, de, es...)
- **type** : webshop, webshop-cache, punchout, pim, upgrade...
- **isNH** : true si alias contient `-nh`
- **isBO** : true si alias contient `-bo`

**Summary discovery** :
```json
{
  "totalDistributions": 72,
  "deployed": 72,
  "enabled": 72,
  "withWaf": 45,
  "totalAliases": 144,
  "typeBreakdown": {"webshop": 41, "punchout": 60, "pim": 3},
  "countryBreakdown": {"FR": 7, "DE": 3, "UK": 3}
}
```

**Seuils de status** :
| Status | Conditions |
|--------|------------|
| ok | Toutes distributions deployed, enabled, avec WAF |
| warning | Sans WAF, certificats proches expiration |
| critical | Non deployed, non enabled, domaines non trouves |

**Roles cross-account** :
| Env | Account | Role |
|-----|---------|------|
| ppd-nh | 287223952330 | iam-dig-ppd-dashboard-read |
| prd-nh | 366483377530 | iam-dig-prd-dashboard-read |
| legacy | 073290922796 | dashboard-read |

---

### net-cloudfront-compare

```json
{
  "Project": "mro-mi2",
  "Env": "nh-stg",
  "Instance": "MI2",
  "Environment": "stg",
  "SourceStateKey": "mro-mi2#legacy-stg|net:cloudfront",
  "DestinationStateKey": "mro-mi2#nh-stg|net:cloudfront"
}
```

**Format de sortie** : `project/env/category: "net"/check_type: "cloudfront-compare"`

**Prerequis** : Executer checkers sur les deux environnements d'abord.

**Matching distributions** :
- Mode tfvars : champ `key` (ex: `fr-webshop`)
- Mode discovery : extraction du premier alias
- Suffixes `-nh` retires pour matching

**Comparaisons** :
- HTTP version
- Price class
- WAF
- TLS version
- Origin Shield
- Origin count

**Categories resultats** :
| Categorie | Description |
|-----------|-------------|
| sameDistributions | Config identique |
| differentConfig | Meme distribution avec differences |
| onlySource | Uniquement en source (non migrees) |
| onlyDestination | Uniquement en destination (nouvelles) |

---

## Config Checkers

### config-sm-checker

```json
{
  "Project": "mro-mi2",
  "Env": "legacy-ppd",
  "Instance": "MI2",
  "Environment": "ppd",
  "CrossAccountRoleArn": "arn:aws:iam::073290922796:role/rubix-refresh-source-role",
  "SecretPatterns": ["/rubix/mi2-preprod/*"]
}
```

**Exemple NH** :
```json
{
  "Project": "mro-mi2",
  "Env": "nh-ppd",
  "Instance": "MI2",
  "Environment": "ppd",
  "CrossAccountRoleArn": "arn:aws:iam::287223952330:role/iam-dig-ppd-refresh-source-role",
  "SecretPatterns": ["/digital/ppd/app/mro-mi2/*", "/digital/ppd/infra/*"]
}
```

**Parametres** :
| Parametre | Description |
|-----------|-------------|
| Project | Projet Dashborion |
| Env | Environnement avec prefixe |
| Instance | Instance webshop |
| Environment | Environnement court |
| CrossAccountRoleArn | Role IAM cross-account |
| SecretPatterns | Patterns glob pour filtrer secrets |

**DynamoDB** : `pk={Project}#{Env}`, `sk=check:config:sm:current`

**Patterns par environnement** (aligne migrate-secrets/config.yaml) :

| Instance | Env | Source (Legacy) | Destination (NH) |
|----------|-----|-----------------|------------------|
| MI1 | stg | `/rubix/mi1-staging/*` | `/digital/stg/app/mro-eu/*`, `/digital/stg/infra/*` |
| MI1 | ppd | `/rubix/mi1-preprod/*` | `/digital/ppd/app/mro-mi1/*`, `/digital/ppd/infra/*` |
| MI2 | ppd | `/rubix/mi2-preprod/*` | `/digital/ppd/app/mro-mi2/*`, `/digital/ppd/infra/*` |
| FR | ppd | `/rubix/fr-preprod/*` | `/digital/ppd/app/mro-fr/*`, `/digital/ppd/infra/*` |

**Securite** : Seuls les hashes SHA256 sont persistes (pas les valeurs).

---

### config-sm-compare

```json
{
  "Project": "mro-mi2",
  "SourceEnv": "legacy-ppd",
  "DestinationEnv": "nh-ppd",
  "Instance": "MI2",
  "Environment": "ppd"
}
```

**DynamoDB** : `pk={Project}#comparison:{SourceEnv}:{DestinationEnv}`, `sk=check:config:sm:current`

**Mapping de chemins** :

Secrets applicatifs :
```
Source:      /rubix/{instance}-{env}/app/{app}/{secret}
Destination: /digital/{env}/app/mro-{instance}/{app}/{secret}

Exemple:
  /rubix/mi2-preprod/app/hybris/config-keys
  -> /digital/ppd/app/mro-mi2/hybris/config-keys
```

Secrets de base de donnees :
```
Source:      /rubix/{instance}-{env}/{instance}-{env}-eks-cluster/{db}
Destination: /digital/{env}/infra/databases/rds-dig-{env}-mro-{instance}/{db}

Exemple:
  /rubix/mi2-preprod/mi2-preprod-eks-cluster/prod_uk
  -> /digital/ppd/infra/databases/rds-dig-ppd-mro-mi2/prod_uk
```

**Transformations attendues** :

hybris/config-keys :
- `expected_diff_keys` : datalake.search.servers.configuration, datalake.exports.servers.*
- `expected_new_keys` : mail.from, ftp3.server, ftpdatalake.server...
- `expected_removed_keys` : sb2.token.configuration

Secrets DB :
- `expected_diff_keys` : host, host_ro, endpoint, dbClusterIdentifier, jdbcUrl...
- `ignore_diff_keys` : password

**Categories de resultats** :

| Categorie | Description | Severite |
|-----------|-------------|----------|
| synced | Identiques | OK |
| differs_expected | Differences attendues | OK |
| differs_unexpected | Differences non configurees | Critical |
| only_source_expected | Source sans dest (attendu) | OK |
| only_source_unexpected | Secret non migre | Warning |
| only_destination_expected | Dest sans source (attendu) | OK |
| only_destination_unexpected | Secret NH specifique | Info |

**Seuils de status** :
| Status | Conditions |
|--------|------------|
| synced | Tous synced ou differs_expected |
| differs | Au moins un unexpected |
| critical | differs_unexpected ou erreur acces |

---

## Application Checkers

### app-component-health-checker

Health checker generique pour les composants applicatifs Rubix. Utilise une architecture plugin pour supporter des checks specifiques par composant.

**Exemple NH staging (teste)** :
```json
{
  "Project": "mro-mi2",
  "Env": "nh-stg",
  "Component": "smui",
  "ClusterName": "k8s-dig-stg-webshop",
  "Namespace": "rubix-mro-mi2-staging",
  "CrossAccountRoleArn": "arn:aws:iam::281127105461:role/iam-dig-stg-dashboard-read"
}
```

**Exemple Legacy staging SMUI (teste)** :
```json
{
  "Project": "mro-mi2",
  "Env": "legacy-stg",
  "Component": "smui",
  "ClusterName": "rubix-nonprod",
  "Namespace": "mi2-staging",
  "CrossAccountRoleArn": "arn:aws:iam::073290922796:role/step_function_eks_nonprod"
}
```

**Exemple Legacy staging Apache (teste)** :
```json
{
  "Project": "mro-mi2",
  "Env": "legacy-stg",
  "Component": "apache",
  "ClusterName": "rubix-nonprod",
  "Namespace": "mi2-staging",
  "LabelSelector": "app=apache",
  "CrossAccountRoleArn": "arn:aws:iam::073290922796:role/step_function_eks_nonprod"
}
```

**Exemple Legacy staging Solr (leader + follower)** :
```json
{
  "Project": "common",
  "Env": "legacy-stg",
  "Component": "solr",
  "ClusterName": "rubix-nonprod",
  "Namespace": "common-staging",
  "LabelSelector": "app=solr",
  "FetchLogs": true,
  "CrossAccountRoleArn": "arn:aws:iam::073290922796:role/step_function_eks_nonprod"
}
```

> **Notes** :
> - Sur les clusters legacy, le label Apache est souvent `app=apache` au lieu de `app.kubernetes.io/name=apache`.
> - Pour Solr, utiliser `app=solr` pour recuperer leader ET follower, ou `app.kubernetes.io/name=solr-leader` / `solr-follower` pour un seul type.
> - La Step Function detecte automatiquement le container name (`solrleader` ou `solrfollower`) selon le nom du pod.

**Composants supportes** : `smui` (deploye), `apache` (deploye), `solr` (deploye), `hybris` (planned)

**Parametres** :
| Parametre | Requis | Description |
|-----------|--------|-------------|
| Project | Oui | Projet Dashborion (ex: mro-mi2, mro-fr) |
| Env | Oui | Environnement (ex: nh-stg, legacy-ppd) |
| Component | Oui | Composant a verifier (smui, hybris...) |
| ClusterName | Oui | Nom cluster EKS |
| Namespace | Oui | Namespace K8s |
| CrossAccountRoleArn | Non | Role cross-account (requis si cluster hors shared-services) |
| LabelSelector | Non | Selecteur label (default: app.kubernetes.io/name={component}) |
| ComponentConfig | Non | Config specifique composant |

**Roles cross-account par environnement** :
| Env | Account | Role | Cluster |
|-----|---------|------|---------|
| nh-stg | 281127105461 | iam-dig-stg-dashboard-read | k8s-dig-stg-webshop |
| nh-ppd | 287223952330 | iam-dig-ppd-dashboard-read | k8s-dig-ppd-webshop |
| nh-prd | 366483377530 | iam-dig-prd-dashboard-read | k8s-dig-prd-webshop |
| legacy-stg/ppd (DE) | 073290922796 | step_function_eks_nonprod | rubix-nonprod |
| legacy-prd (DE) | 073290922796 | step_function_eks_prod | rubix-prod |
| legacy-* (FR) | 073290922796 | step_function_eks_nonprod-fr / step_function_eks_prod-fr | rubix-nonprod-fr / rubix-prod-fr |

**DynamoDB** : `pk={Project}#{Env}`, `sk=check:app:{component}:current`

**SMUI Plugin Checks** :

| Check | Description | Pattern detecte |
|-------|-------------|-----------------|
| pod_status | Pod running et ready | K8s pod status |
| container_restarts | Restarts < threshold (5) | K8s container status |
| image_pull | Images pull OK | `ImagePullBackOff`, `ErrImagePull` (absence) |
| jdbc_connection | HikariPool demarrage OK | `HikariPool-mysql - Start completed` |
| jdbc_url_valid | jdbcUrl non null | Absence de `jdbcUrl, null` |
| database_access | Pas d'Access denied | Absence de `Access denied for user` |
| application_startup | Application demarree | `Application started` ou `Listening for HTTP` |
| application_errors | Comptage erreurs recentes | `ERROR`, `Exception` |
| hikari_pool_health | Pool connexions sain | Absence de `Connection timeout` |

**Apache Plugin Checks** :

| Check | Description | Pattern detecte |
|-------|-------------|-----------------|
| pod_status | Pod running et ready | K8s pod status |
| container_restarts | Restarts < threshold (5) | K8s container status |
| image_pull | Images pull OK | `ImagePullBackOff`, `ErrImagePull` (absence) |
| media_config | Variables env media presentes | `SHARED_DATA_MEDIA_EFS_ID`, `SHARED_DATA_MEDIA_SUB_PATH` |
| media_accessibility | Pas de 404 sur /medias/, /_ui/, /assets/ | `GET /medias/.* 404` |
| http_errors | Comptage erreurs 5xx | `" 500 "`, `" 502 "`, `" 503 "` |
| upstream_health | Connectivite backend | `AH0111*`, `upstream timed out`, `Connection refused` |
| config_errors | Erreurs config Apache/SSL | `AH00526`, `SSL`, `Permission denied` |

**Solr Plugin Checks** :

| Check | Description | Pattern detecte |
|-------|-------------|-----------------|
| pod_status | Pods running et ready | K8s pod status |
| container_restarts | Restarts < threshold (5) | K8s container status |
| image_pull | Images pull OK | `ImagePullBackOff`, `ErrImagePull` (absence) |
| solr_started | Solr demarre | `SolrCore.*registered`, `Server.*started` |
| replication_status | Sync leader/follower | Analyse versions IndexFetcher |
| core_status | Cores charges | `SolrCore.*registered`, absence `CoreInitializationException` |
| query_errors | Erreurs requetes | `SolrException`, `Update failed`, `Query timeout` |
| memory_issues | Problemes memoire | `OutOfMemoryError`, `GC overhead` |
| general_errors | Comptage erreurs | `ERROR`, `SEVERE`, `Exception` |

> **Analyse de replication** : Le plugin Solr parse les logs IndexFetcher pour extraire les versions leader/follower et detecter les decalages. Exemple de resultats :
> ```json
> "version_analysis": {
>   "checks_performed": 21,
>   "has_mismatch": true,
>   "mismatch_count": 4,
>   "mismatches": [{"leader_generation": 0, "follower_generation": 601, ...}]
> }
> ```

**Seuils de status** :
| Status | Conditions |
|--------|------------|
| ok | Tous checks passent, composant sain |
| warning | Issues mineures (erreurs dans logs, message startup non trouve) |
| critical | JDBC null, access denied, pod not ready |

**Exemple de sortie** :
```json
{
  "component": "smui",
  "status": "warning",
  "healthy": false,
  "summary": {
    "pods_total": 1,
    "pods_ready": 1,
    "checks_total": 8,
    "checks_ok": 6,
    "checks_warning": 2,
    "checks_critical": 0
  },
  "checks": [
    {"name": "pod_status", "status": "ok", "message": "All 1 pod(s) running and ready"},
    {"name": "application_errors", "status": "warning", "message": "Found 1 error(s) in logs"}
  ],
  "issues": [
    {"severity": "warning", "check": "application_errors", "message": "Found 1 error(s) in logs"}
  ]
}
```

**Architecture Lambda** :
- `app_component_checker.py` : Point d'entree avec plugin registry
- `plugins/base_plugin.py` : Classe abstraite avec checks communs
- `plugins/smui_plugin.py` : Plugin SMUI avec patterns specifiques

---

## Voir aussi

- [Lambdas Reference](lambdas-reference.md) - Documentation des Lambdas
- [Refactoring Plan CloudFront & DNS](refactoring-plan-cloudfront-dns.md) - Architecture modulaire
- [Architecture Overview](migration-ARCHITECTURE.md) - Vue d'ensemble architecture
