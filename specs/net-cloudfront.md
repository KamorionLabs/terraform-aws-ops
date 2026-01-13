# Spec: CloudFront Distribution Check

## Identifiant
- **ID**: `net-cf`
- **Domaine**: network
- **Priorite**: P1 (important)
- **Scope**: `COUNTRY` (FR, DE, BENE)

## Objectif
Verifier l'etat des distributions CloudFront :
- Status et configuration
- Origins et behaviors
- Certificats SSL
- WAF association
- Comparaison Legacy vs New Horizon (memes distributions)

**Source des domaines** : Les distributions a checker sont definies dans les fichiers terraform.tfvars :
- `stacks/cloudfront/env/<env>/terraform.tfvars` → `managed_domains` (domaines publics CloudFront)

## Architecture

### Composant 1 : Fetch & Store (Step Function)
Recupere l'etat CloudFront et stocke en DynamoDB.

- [x] **Step Function** (appels directs AWS SDK)

### Composant 2 : Compare (Step Function)
Compare les distributions Legacy vs New Horizon.

- [x] **Step Function** (lecture DynamoDB + comparaison)

## Inputs

### Fetch & Store
```json
{
  "Domain": "string - domaine metier (mro, webshop)",
  "Target": "string - environnement cible (de-ppd-legacy, de-ppd-nh)",
  "Country": "FR | DE | BENE",
  "Environment": "stg | ppd | prd",
  "Source": "legacy | nh",
  "CrossAccountRoleArn": "arn:aws:iam::{account}:role/ops-dashboard-read",
  "TfvarsSource": {
    "repository": "NewHorizon-IaC-Webshop",
    "cloudfront": "stacks/cloudfront/env/ppd/terraform.tfvars"
  }
}
```

### Compare
```json
{
  "Domain": "string",
  "Country": "DE",
  "Environment": "ppd",
  "LegacyStateKey": "mro#de-ppd-legacy|net-cf",
  "NHStateKey": "mro#de-ppd-nh|net-cf"
}
```

## Source des domaines : terraform.tfvars

### managed_domains (CloudFront)
Source: `stacks/cloudfront/env/<env>/terraform.tfvars`

```hcl
managed_domains = {
  "de-mi1-fo" = {
    domain   = "mi1.stg.rubix.com"
    status   = "active"          # active | migrating | migrated
    migrated = false
    type     = "webshop"
    country  = "DE"
    instance = "MI1"
  }
  "de-mi2-fo" = {
    domain   = "mi2.stg.rubix.com"
    status   = "migrating"
    migrated = false
    type     = "webshop"
    country  = "DE"
    instance = "MI2"
  }
  "fr-fo" = {
    domain   = "www.stg.rubix.fr"
    status   = "active"
    migrated = false
    type     = "webshop"
    country  = "FR"
    instance = "FR"
  }
  # ... autres domaines
}
```

### Extraction des domaines
La Step Function doit :
1. Lire le fichier tfvars via Azure DevOps API (Lambda)
2. Parser le bloc HCL `managed_domains`
3. Extraire les domaines selon le pays cible
4. Chercher les distributions CloudFront correspondantes par alias

## Structure Terraform (NewHorizon-IaC-Webshop)

```
NewHorizon-IaC-Webshop/
└── stacks/
    └── cloudfront/
        └── env/
            ├── int/terraform.tfvars
            ├── stg/terraform.tfvars
            ├── ppd/terraform.tfvars
            └── prod/terraform.tfvars
```

### Mapping Environnement vers tfvars

| Env | CloudFront tfvars |
|-----|-------------------|
| stg | stacks/cloudfront/env/stg/terraform.tfvars |
| ppd | stacks/cloudfront/env/ppd/terraform.tfvars |
| prd | stacks/cloudfront/env/prod/terraform.tfvars |

## Outputs (payload DynamoDB)

### State (Fetch & Store)
```json
{
  "status": "ok | warning | critical",
  "source": "legacy | nh",
  "country": "DE",
  "summary": {
    "totalDistributions": 2,
    "deployed": 2,
    "enabled": 2,
    "withWaf": 2,
    "migrating": 1,
    "migrated": 0
  },
  "distributions": [
    {
      "key": "de-mi1-fo",
      "id": "E1234567890ABC",
      "arn": "arn:aws:cloudfront::xxx:distribution/E1234567890ABC",
      "domainName": "d111111abcdef8.cloudfront.net",
      "hostname": "mi1.ppd.rubix.com",
      "status": "Deployed",
      "enabled": true,
      "aliases": ["mi1.ppd.rubix.com", "www.mi1.rubix.com"],
      "priceClass": "PriceClass_100",
      "httpVersion": "http2and3",
      "origins": [
        {
          "id": "S3-webshop-assets",
          "domainName": "webshop-assets.s3.eu-central-1.amazonaws.com",
          "type": "S3",
          "originPath": ""
        },
        {
          "id": "ALB-webshop",
          "domainName": "k8s-xxx.eu-central-1.elb.amazonaws.com",
          "type": "ALB",
          "originPath": "",
          "protocol": "https-only"
        }
      ],
      "defaultCacheBehavior": {
        "targetOriginId": "ALB-webshop",
        "viewerProtocolPolicy": "redirect-to-https",
        "compress": true,
        "cachePolicyName": "Managed-CachingOptimized"
      },
      "waf": {
        "enabled": true,
        "webAclId": "arn:aws:wafv2:us-east-1:xxx:global/webacl/xxx"
      },
      "certificate": {
        "source": "acm",
        "arn": "arn:aws:acm:us-east-1:xxx:certificate/xxx",
        "minimumProtocolVersion": "TLSv1.2_2021",
        "expiresAt": "ISO8601"
      },
      "migrationStatus": "active",
      "migrated": false,
      "instance": "MI1",
      "country": "DE",
      "lastModifiedTime": "ISO8601"
    }
  ],
  "recentInvalidations": [
    {
      "distributionId": "E1234567890ABC",
      "id": "I1234567890ABC",
      "status": "Completed",
      "createTime": "ISO8601",
      "paths": ["/*"]
    }
  ],
  "tfvarsSource": {
    "cloudfrontPath": "stacks/cloudfront/env/ppd/terraform.tfvars",
    "lastCommit": "abc123"
  },
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
    "distributionCount": "synced | differs",
    "origins": "synced | differs",
    "wafConfig": "synced | differs",
    "cachePolicy": "synced | differs",
    "migrationStatus": "synced | differs"
  },
  "distributionsComparison": {
    "sameDistributions": [
      {
        "key": "de-mi1-fo",
        "hostname": "mi1.ppd.rubix.com",
        "status": "synced"
      }
    ],
    "differentConfig": [
      {
        "key": "de-mi2-fo",
        "hostname": "mi2.ppd.rubix.com",
        "legacy": {
          "priceClass": "PriceClass_All",
          "httpVersion": "http2",
          "originType": "ALB-direct",
          "waf": false
        },
        "nh": {
          "priceClass": "PriceClass_100",
          "httpVersion": "http2and3",
          "originType": "ALB-via-OriginShield",
          "waf": true
        },
        "expected": true,
        "reason": "NH optimized with Origin Shield, HTTP/3, and WAF"
      }
    ],
    "onlyLegacy": [],
    "onlyNH": []
  },
  "migrationStatus": {
    "migrating": ["de-mi2-fo"],
    "migrated": [],
    "pending": ["de-mi1-fo", "de-mi3-fo"]
  },
  "originsComparison": {
    "legacy": {"s3Origins": 1, "albOrigins": 1, "customOrigins": 0},
    "nh": {"s3Origins": 1, "albOrigins": 1, "customOrigins": 1},
    "expected": true,
    "reason": "NH added custom origin for API"
  },
  "wafComparison": {
    "legacy": {"enabled": false},
    "nh": {"enabled": true, "webAclName": "rubix-waf-global"},
    "expected": true,
    "reason": "NH has WAF protection"
  },
  "certificateComparison": {
    "legacy": {"expiresIn": 45, "protocolVersion": "TLSv1.2_2019"},
    "nh": {"expiresIn": 320, "protocolVersion": "TLSv1.2_2021"},
    "status": "differs",
    "expected": true,
    "reason": "NH uses newer certificate with updated TLS"
  },
  "issues": [],
  "timestamp": "ISO8601"
}
```

## Appels necessaires

### Azure DevOps API (pour lire tfvars)
| API Call | Description |
|----------|-------------|
| `GET /{project}/_apis/git/repositories/{repo}/items?path={tfvars}` | Contenu terraform.tfvars |

### AWS API (cross-account)
| Service | API Call | Description |
|---------|----------|-------------|
| STS | `AssumeRole` | Assume role cross-account |
| CloudFront | `ListDistributions` | Liste des distributions |
| CloudFront | `GetDistribution` | Details d'une distribution |
| CloudFront | `ListInvalidations` | Invalidations recentes |
| ACM | `DescribeCertificate` | Details certificat (us-east-1) |

## ASL Step Function (structure)
```json
{
  "StartAt": "FetchTfvars",
  "States": {
    "FetchTfvars": {
      "Type": "Task",
      "Resource": "${FetchTfvarsLambdaArn}",
      "Parameters": {
        "TfvarsSource.$": "$.TfvarsSource",
        "Country.$": "$.Country"
      },
      "ResultPath": "$.managedDomains",
      "Next": "AssumeRole"
    },
    "AssumeRole": {
      "Type": "Task",
      "Resource": "arn:aws:states:::aws-sdk:sts:assumeRole",
      "Parameters": {
        "RoleArn.$": "$.CrossAccountRoleArn",
        "RoleSessionName": "ops-dashboard-cloudfront-check"
      },
      "ResultPath": "$.Credentials",
      "Next": "ListDistributions"
    },
    "ListDistributions": {
      "Type": "Task",
      "Resource": "arn:aws:states:::aws-sdk:cloudfront:listDistributions",
      "Parameters": {},
      "ResultPath": "$.distributions",
      "Next": "FilterDistributions"
    },
    "FilterDistributions": {
      "Type": "Pass",
      "Comment": "Filter distributions by hostnames from managed_domains",
      "Next": "MapGetDistribution"
    },
    "MapGetDistribution": {
      "Type": "Map",
      "ItemsPath": "$.filteredDistributions",
      "ItemProcessor": {
        "StartAt": "GetDistribution",
        "States": {
          "GetDistribution": {
            "Type": "Task",
            "Resource": "arn:aws:states:::aws-sdk:cloudfront:getDistribution",
            "Parameters": {
              "Id.$": "$.Id"
            },
            "Next": "GetInvalidations"
          },
          "GetInvalidations": {
            "Type": "Task",
            "Resource": "arn:aws:states:::aws-sdk:cloudfront:listInvalidations",
            "Parameters": {
              "DistributionId.$": "$.Distribution.Id",
              "MaxItems": "10"
            },
            "End": true
          }
        }
      },
      "ResultPath": "$.distributionDetails",
      "Next": "SaveState"
    },
    "SaveState": {
      "Type": "Task",
      "Resource": "${SaveStateLambdaArn}",
      "End": true
    }
  }
}
```

## Logique metier

### Fetch & Store
1. Lire le fichier terraform.tfvars via Azure DevOps API (Lambda)
2. Parser `managed_domains` et filtrer par pays
3. Assume role cross-account
4. Lister les distributions CloudFront
5. Matcher les distributions avec les hostnames des managed_domains
6. Pour chaque distribution trouvee:
   - Recuperer les details complets
   - Verifier status (Deployed) et enabled
   - Extraire origins, behaviors, WAF, certificate
   - Lister les invalidations recentes
   - Associer le statut de migration depuis tfvars
7. Calculer le summary
8. Sauvegarder en DynamoDB

### Compare
1. Recuperer state Legacy depuis DynamoDB
2. Recuperer state NH depuis DynamoDB
3. Comparer les distributions par key/hostname:
   - Memes distributions presentes ?
   - Memes origins configurees ?
   - Memes behaviors ?
4. Comparer la configuration:
   - WAF enabled sur NH ?
   - Certificats valides ?
   - Protocol TLS a jour ?
5. Verifier le statut de migration:
   - Coherent avec l'environnement ?
   - Domaines en cours de migration identifies ?
6. Identifier les differences:
   - Attendues (Origin Shield, HTTP/3, WAF)
   - Inattendues (drift)
7. Calculer le status global

## Conditions de succes (status: ok)
- [x] Tous les hostnames des tfvars ont une distribution
- [x] Toutes les distributions en status Deployed
- [x] Toutes les distributions enabled
- [x] Certificats SSL valides et > 30 jours avant expiration
- [x] WAF attache (si requis)
- [x] Origins accessibles
- [x] Statut migration coherent avec l'environnement

## Conditions d'alerte (status: warning)
- [x] Distribution en status InProgress (deploiement en cours)
- [x] Certificat expirant dans < 30 jours
- [x] Invalidation en cours depuis > 10min
- [x] WAF non attache (si requis)
- [x] Domaine en statut "migrating" depuis > 7 jours

## Conditions d'erreur (status: critical)
- [x] Hostname des tfvars sans distribution correspondante
- [x] Distribution disabled
- [x] Distribution en status Error
- [x] Certificat expire ou invalide
- [x] Origin inaccessible
- [x] terraform.tfvars inaccessible

## Dependances
- Prerequis: `net-alb` (pour origins ALB)
- Services externes: Azure DevOps API (tfvars), CloudFront, ACM
- Secrets: `ops-dashboard/ado-pat` pour Azure DevOps
- Permissions IAM (dans le role cross-account):
  - `cloudfront:ListDistributions`
  - `cloudfront:GetDistribution`
  - `cloudfront:ListInvalidations`
  - `acm:DescribeCertificate` (us-east-1)

## Mapping Comptes AWS

| Country | Env | Legacy Account | NH Account |
|---------|-----|----------------|------------|
| DE | stg | 073290922796 | 281127105461 |
| DE | ppd | 073290922796 | 287223952330 |
| DE | prd | 073290922796 | 366483377530 |
| FR | * | 073290922796 (eu-west-3) | selon env |
| BENE | * | 073290922796 | selon env |

Note: CloudFront est global mais les distributions sont gerees par compte.

## Statuts de migration (managed_domains)

| Status | Description |
|--------|-------------|
| active | Domaine actif, pas encore migre |
| migrating | Migration en cours |
| migrated | Migration terminee |

## Differences attendues Legacy vs NH

| Element | Legacy | NH | Raison |
|---------|--------|-----|--------|
| HTTP Version | http2 | http2and3 | Performance HTTP/3 |
| Origin Shield | Non | Oui | Reduce origin load |
| WAF | Non | Oui | Security |
| Price Class | PriceClass_All | PriceClass_100 | Cost optimization |
| TLS Version | TLSv1.2_2019 | TLSv1.2_2021 | Security update |

## Notes
- **Source of truth** : Les domaines viennent des terraform.tfvars, pas d'une liste statique
- CloudFront est global, les certificats doivent etre dans us-east-1
- Verifier la coherence des aliases avec Route53 (net-dns)
- Les origins peuvent etre S3, ALB, ou custom
- L'invalidation peut prendre quelques minutes
- NH utilise Origin Shield pour reduire la charge sur les origins
- Le champ `status` dans managed_domains indique l'etat de migration
