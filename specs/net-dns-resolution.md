# Spec: DNS Resolution Check

## Identifiant
- **ID**: `net-dns`
- **Domaine**: network
- **Priorite**: P1 (important)
- **Scope**: `COUNTRY` (FR, DE, BENE)

## Objectif
Verifier la resolution DNS des endpoints critiques :
- Records Route53 par pays
- Resolution DNS active
- Propagation et coherence
- Comparaison Legacy vs New Horizon (memes records)

**Source des domaines** : Les domaines a checker sont definis dans les fichiers terraform.tfvars :
- `stacks/cloudfront/env/<env>/terraform.tfvars` → `managed_domains` (domaines publics CloudFront)
- `stacks/eks/env/<env>/terraform.tfvars` → `managed_api_domains` (APIs privees)

## Architecture

### Composant 1 : Fetch & Store (Lambda)
Recupere l'etat DNS et stocke en DynamoDB.

- [x] **Lambda** (resolution DNS active necessaire)

Note: Lambda necessaire car la resolution DNS active n'est pas possible en Step Function.

### Composant 2 : Compare (Step Function)
Compare les records DNS Legacy vs New Horizon.

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
  "HostedZoneId": "Z0123456789ABC",
  "TfvarsSource": {
    "repository": "NewHorizon-IaC-Webshop",
    "cloudfront": "stacks/cloudfront/env/ppd/terraform.tfvars",
    "eks": "stacks/eks/env/ppd/terraform.tfvars"
  }
}
```

### Compare
```json
{
  "Domain": "string",
  "Country": "DE",
  "Environment": "ppd",
  "LegacyStateKey": "mro#de-ppd-legacy|net-dns",
  "NHStateKey": "mro#de-ppd-nh|net-dns"
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

### managed_api_domains (APIs privees EKS)
Source: `stacks/eks/env/<env>/terraform.tfvars`

```hcl
managed_api_domains = {
  "mi1-api" = {
    domain   = "api.mi1.internal.rubix.com"
    type     = "private"
    service  = "hybris-api"
    instance = "MI1"
  }
  # ... autres APIs
}
```

### Extraction des domaines
La Lambda doit :
1. Lire les fichiers tfvars via Azure DevOps API
2. Parser les blocs HCL `managed_domains` et `managed_api_domains`
3. Extraire les domaines selon le pays/instance cible
4. Verifier la resolution DNS de chaque domaine

## Outputs (payload DynamoDB)

### State (Fetch & Store)
```json
{
  "status": "ok | warning | critical",
  "source": "legacy | nh",
  "country": "DE",
  "summary": {
    "total": 5,
    "resolved": 5,
    "failed": 0,
    "matchExpected": 5,
    "mismatch": 0,
    "migrating": 1,
    "migrated": 0
  },
  "hostedZone": {
    "id": "Z0123456789ABC",
    "name": "rubix.com.",
    "recordCount": 150,
    "privateZone": false,
    "region": "eu-central-1"
  },
  "managedDomains": [
    {
      "key": "de-mi1-fo",
      "hostname": "mi1.ppd.rubix.com",
      "type": "CNAME",
      "value": "d123.cloudfront.net",
      "ttl": 300,
      "resolved": true,
      "resolvedIPs": ["1.2.3.4", "5.6.7.8"],
      "responseTimeMs": 45,
      "matchExpected": true,
      "status": "active",
      "migrated": false,
      "instance": "MI1",
      "country": "DE"
    },
    {
      "key": "de-mi2-fo",
      "hostname": "mi2.ppd.rubix.com",
      "type": "CNAME",
      "value": "d456.cloudfront.net",
      "ttl": 300,
      "resolved": true,
      "resolvedIPs": ["10.0.1.1", "10.0.1.2"],
      "responseTimeMs": 52,
      "matchExpected": true,
      "status": "migrating",
      "migrated": false,
      "instance": "MI2",
      "country": "DE"
    }
  ],
  "apiDomains": [
    {
      "key": "mi1-api",
      "hostname": "api.mi1.internal.rubix.com",
      "type": "A",
      "value": "10.0.1.100",
      "ttl": 60,
      "resolved": true,
      "resolvedIPs": ["10.0.1.100"],
      "responseTimeMs": 12,
      "matchExpected": true,
      "service": "hybris-api",
      "instance": "MI1"
    }
  ],
  "tfvarsSource": {
    "cloudfrontPath": "stacks/cloudfront/env/ppd/terraform.tfvars",
    "eksPath": "stacks/eks/env/ppd/terraform.tfvars",
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
    "recordCount": "synced | differs",
    "recordValues": "synced | differs",
    "migrationStatus": "synced | differs"
  },
  "managedDomainsComparison": {
    "sameRecords": ["de-mi1-fo", "de-mi3-fo"],
    "differentValues": [
      {
        "key": "de-mi2-fo",
        "hostname": "mi2.ppd.rubix.com",
        "legacy": {"type": "A", "value": "1.2.3.4"},
        "nh": {"type": "CNAME", "value": "d456.cloudfront.net"},
        "expected": true,
        "reason": "NH uses CloudFront CDN"
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
  "issues": [],
  "timestamp": "ISO8601"
}
```

## Structure Terraform (NewHorizon-IaC-Webshop)

```
NewHorizon-IaC-Webshop/
└── stacks/
    ├── cloudfront/
    │   └── env/
    │       ├── int/terraform.tfvars
    │       ├── stg/terraform.tfvars
    │       ├── ppd/terraform.tfvars
    │       └── prod/terraform.tfvars
    └── eks/
        └── env/
            ├── int/terraform.tfvars
            ├── stg/terraform.tfvars
            ├── ppd/terraform.tfvars
            └── prod/terraform.tfvars
```

### Mapping Environnement vers tfvars

| Env | CloudFront tfvars | EKS tfvars |
|-----|-------------------|------------|
| stg | stacks/cloudfront/env/stg/terraform.tfvars | stacks/eks/env/stg/terraform.tfvars |
| ppd | stacks/cloudfront/env/ppd/terraform.tfvars | stacks/eks/env/ppd/terraform.tfvars |
| prd | stacks/cloudfront/env/prod/terraform.tfvars | stacks/eks/env/prod/terraform.tfvars |

## Appels necessaires

### Azure DevOps API (pour lire tfvars)
| API Call | Description |
|----------|-------------|
| `GET /{project}/_apis/git/repositories/{repo}/items?path={tfvars}` | Contenu terraform.tfvars |

### AWS API (cross-account)
| Service | API Call | Description |
|---------|----------|-------------|
| STS | `AssumeRole` | Assume role cross-account |
| Route53 | `GetHostedZone` | Details de la hosted zone |
| Route53 | `ListResourceRecordSets` | Liste des records |

### Resolution DNS
| Service | API Call | Description |
|---------|----------|-------------|
| Lambda | DNS resolution via dnspython | Resolution active |

## Logique metier

### Fetch & Store
1. Lire les fichiers terraform.tfvars via Azure DevOps API
2. Parser `managed_domains` et `managed_api_domains`
3. Filtrer par pays/instance selon le Target
4. Assume role cross-account si necessaire
5. Appeler `GetHostedZone` pour les details Route53
6. Pour chaque domaine extrait des tfvars :
   - Faire une resolution DNS active
   - Mesurer le temps de reponse
   - Verifier le statut de migration
7. Calculer le summary
8. Sauvegarder en DynamoDB

### Compare
1. Recuperer state Legacy depuis DynamoDB
2. Recuperer state NH depuis DynamoDB
3. Comparer les records par hostname :
   - Memes records presents ?
   - Memes valeurs (type, value) ?
   - Statut de migration coherent ?
4. Identifier les differences :
   - Attendues (A -> CNAME pour CloudFront)
   - Inattendues (drift)
5. Calculer le status global

## Conditions de succes (status: ok)
- [x] Tous les hostnames des tfvars resolvent correctement
- [x] Les valeurs correspondent aux attendus
- [x] Temps de reponse < 200ms
- [x] Hosted zone existe et est active
- [x] Statut migration coherent avec l'environnement

## Conditions d'alerte (status: warning)
- [x] TTL tres court (< 60s)
- [x] Temps de reponse > 200ms
- [x] Domaine en statut "migrating" depuis > 7 jours
- [x] Domaine sans valeur attendue definie

## Conditions d'erreur (status: critical)
- [x] Hostname ne resout pas
- [x] Valeur resolue != attendue
- [x] Hosted zone inexistante ou inaccessible
- [x] Temps de reponse > 500ms
- [x] terraform.tfvars inaccessible

## Dependances
- Prerequis: `net-cloudfront` (si CNAME vers CloudFront)
- Services externes: Azure DevOps API (tfvars), Route53
- Secrets: `ops-dashboard/ado-pat` pour Azure DevOps
- Permissions IAM (dans le role cross-account):
  - `route53:GetHostedZone`
  - `route53:ListResourceRecordSets`

## Mapping Comptes AWS

| Country | Env | Legacy Account | NH Account |
|---------|-----|----------------|------------|
| DE | stg | 073290922796 | 281127105461 |
| DE | ppd | 073290922796 | 287223952330 |
| DE | prd | 073290922796 | 366483377530 |
| FR | * | 073290922796 (eu-west-3) | selon env |
| BENE | * | 073290922796 | selon env |

Note: Route53 est global mais les hosted zones peuvent etre gerees dans differents comptes.

## Statuts de migration (managed_domains)

| Status | Description |
|--------|-------------|
| active | Domaine actif, pas encore migre |
| migrating | Migration en cours |
| migrated | Migration terminee |

## Notes
- **Source of truth** : Les domaines viennent des terraform.tfvars, pas d'une liste statique
- La resolution DNS active necessite une Lambda
- Route53 est global mais les records sont specifiques par pays
- Verifier aussi les health checks Route53 associes
- Les CNAMEs vers CloudFront sont la norme pour NH
- Legacy peut utiliser des A records directs vers ALB
- Le champ `status` dans managed_domains indique l'etat de migration
