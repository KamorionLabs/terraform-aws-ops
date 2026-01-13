# Spec: Kubernetes Ingress Rules Status

## Identifiant
- **ID**: `k8s-ingress`
- **Domaine**: kubernetes
- **Priorite**: P0 (critique)
- **Scope**: `INSTANCE` (MI1, MI2, MI3, FR, BENE, INDUS)

## Objectif
Verifier l'etat des Ingress et leurs regles :
- **Ingress Front** : Traffic public (storefront)
- **Ingress BO** : Traffic backoffice (HAC, HMC)
- **Ingress Private** : Traffic interne (APIs internes)
- **NLB SFTP** : Service LoadBalancer pour SFTP (pas Ingress)

## Architecture

### Composant 1 : Fetch & Store (Step Function)
Recupere les Ingress et Services LoadBalancer, stocke en DynamoDB.

- [x] **Step Function** (appel direct eks:call)

### Composant 2 : Compare (Step Function)
Compare Legacy vs New Horizon (rules, hosts, paths).

- [x] **Step Function** (lecture DynamoDB + comparaison)

## Inputs

### Fetch & Store
```json
{
  "Domain": "string",
  "Target": "string (ex: mi1-ppd-legacy, mi1-ppd-nh)",
  "Instance": "MI1 | MI2 | MI3 | FR | BENE | INDUS",
  "Environment": "stg | ppd | prd",
  "Source": "legacy | nh",
  "ClusterName": "string",
  "Namespace": "hybris",
  "IngressTypes": ["front", "bo", "private", "sftp"]
}
```

### Compare
```json
{
  "Domain": "string",
  "Instance": "MI1",
  "Environment": "ppd",
  "LegacyStateKey": "mro#mi1-ppd-legacy|k8s-ingress",
  "NHStateKey": "mro#mi1-ppd-nh|k8s-ingress"
}
```

## Outputs (payload DynamoDB)

### State (Fetch & Store)
```json
{
  "status": "ok | warning | critical",
  "source": "legacy | nh",
  "summary": {
    "totalIngresses": 3,
    "healthy": 3,
    "withTLS": 3,
    "sftpService": true
  },
  "ingresses": {
    "front": {
      "name": "hybris-ingress-front",
      "class": "alb",
      "loadBalancer": {
        "hostname": "k8s-hybris-front-xxx.eu-central-1.elb.amazonaws.com",
        "scheme": "internet-facing"
      },
      "rules": [
        {
          "host": "webshop-mi1.rubix.com",
          "paths": [
            {"path": "/", "backend": "hybris-front:9001", "pathType": "Prefix"},
            {"path": "/medias", "backend": "hybris-front:9001", "pathType": "Prefix"}
          ]
        },
        {
          "host": "www.rubix-mi1.com",
          "paths": [
            {"path": "/", "backend": "hybris-front:9001", "pathType": "Prefix"}
          ]
        }
      ],
      "tls": [
        {"hosts": ["webshop-mi1.rubix.com", "www.rubix-mi1.com"], "secretName": "tls-front"}
      ],
      "annotations": {
        "alb.ingress.kubernetes.io/scheme": "internet-facing",
        "alb.ingress.kubernetes.io/target-type": "ip",
        "alb.ingress.kubernetes.io/certificate-arn": "arn:aws:acm:..."
      },
      "healthy": true
    },
    "bo": {
      "name": "hybris-ingress-bo",
      "class": "alb",
      "loadBalancer": {
        "hostname": "k8s-hybris-bo-xxx.eu-central-1.elb.amazonaws.com",
        "scheme": "internal"
      },
      "rules": [
        {
          "host": "hac-mi1.rubix.internal",
          "paths": [
            {"path": "/hac", "backend": "hybris-bo:9001", "pathType": "Prefix"},
            {"path": "/hmc", "backend": "hybris-bo:9001", "pathType": "Prefix"},
            {"path": "/backoffice", "backend": "hybris-bo:9001", "pathType": "Prefix"}
          ]
        }
      ],
      "tls": [
        {"hosts": ["hac-mi1.rubix.internal"], "secretName": "tls-bo"}
      ],
      "annotations": {
        "alb.ingress.kubernetes.io/scheme": "internal",
        "alb.ingress.kubernetes.io/target-type": "ip"
      },
      "healthy": true
    },
    "private": {
      "name": "hybris-ingress-private",
      "class": "alb",
      "loadBalancer": {
        "hostname": "k8s-hybris-private-xxx.eu-central-1.elb.amazonaws.com",
        "scheme": "internal"
      },
      "rules": [
        {
          "host": "api-mi1.rubix.internal",
          "paths": [
            {"path": "/rest", "backend": "hybris-api:9001", "pathType": "Prefix"},
            {"path": "/occ", "backend": "hybris-api:9001", "pathType": "Prefix"}
          ]
        }
      ],
      "healthy": true
    }
  },
  "sftpService": {
    "name": "hybris-sftp",
    "type": "LoadBalancer",
    "loadBalancer": {
      "hostname": "xxx.elb.eu-central-1.amazonaws.com",
      "type": "nlb"
    },
    "ports": [
      {"port": 22, "targetPort": 22, "protocol": "TCP"}
    ],
    "annotations": {
      "service.beta.kubernetes.io/aws-load-balancer-type": "nlb",
      "service.beta.kubernetes.io/aws-load-balancer-scheme": "internal"
    },
    "healthy": true
  },
  "allRules": [
    {
      "type": "front",
      "host": "webshop-mi1.rubix.com",
      "path": "/",
      "backend": "hybris-front:9001"
    }
  ],
  "timestamp": "ISO8601"
}
```

### Comparison
```json
{
  "status": "synced | differs",
  "summary": {
    "front": "synced | differs | only_legacy | only_nh",
    "bo": "synced | differs",
    "private": "synced | differs",
    "sftp": "synced | differs"
  },
  "rulesComparison": {
    "front": {
      "status": "synced | differs",
      "sameRules": [
        {"host": "webshop-mi1.rubix.com", "path": "/"}
      ],
      "differentRules": [
        {
          "host": "www.rubix-mi1.com",
          "legacy": {"path": "/", "backend": "hybris:9001"},
          "nh": {"path": "/", "backend": "hybris-front:9001"},
          "expected": true,
          "reason": "NH uses separate front service"
        }
      ],
      "onlyLegacy": [],
      "onlyNH": [
        {
          "host": "webshop-mi1.rubix.com",
          "path": "/api/v2",
          "reason": "New OCC v2 endpoint"
        }
      ]
    },
    "bo": {
      "status": "synced",
      "sameRules": ["all"],
      "differentRules": [],
      "onlyLegacy": [],
      "onlyNH": []
    }
  },
  "hostsComparison": {
    "legacy": ["webshop-mi1.rubix.com", "www.rubix-mi1.com", "hac-mi1.rubix.internal"],
    "nh": ["webshop-mi1.rubix.com", "www.rubix-mi1.com", "hac-mi1.rubix.internal", "api-mi1.rubix.internal"],
    "onlyLegacy": [],
    "onlyNH": ["api-mi1.rubix.internal"]
  },
  "issues": [],
  "timestamp": "ISO8601"
}
```

## Appels AWS/K8s necessaires

### EKS Call (Ingresses)
| Service | API Call | Path |
|---------|----------|------|
| EKS | `eks:call` | GET /apis/networking.k8s.io/v1/namespaces/{ns}/ingresses |

### EKS Call (Services LoadBalancer pour SFTP)
| Service | API Call | Path |
|---------|----------|------|
| EKS | `eks:call` | GET /api/v1/namespaces/{ns}/services |

## Logique metier

### Fetch & Store
1. Lister les ingresses du namespace
2. Categoriser par type (front, bo, private) selon le nom ou les annotations
3. Extraire les rules (host + path + backend)
4. Verifier que chaque ingress a une adresse loadBalancer
5. Lister les services de type LoadBalancer (pour SFTP/NLB)
6. Verifier TLS si configure
7. Sauvegarder en DynamoDB

### Compare
1. Recuperer state Legacy depuis DynamoDB
2. Recuperer state NH depuis DynamoDB
3. Pour chaque type d'ingress:
   - Comparer les hosts
   - Comparer les paths et backends
   - Categoriser les differences
4. Verifier le service SFTP
5. Calculer le status global

## Structure des Ingress Rubix

### Front Ingress
- **Purpose**: Traffic public (storefront)
- **Scheme**: internet-facing
- **Hosts**: domaines publics (webshop-mi1.rubix.com, www.rubix-mi1.com)
- **Paths**: `/`, `/medias`, `/static`
- **Backend**: hybris-front service

### BO Ingress (Backoffice)
- **Purpose**: Traffic backoffice interne
- **Scheme**: internal
- **Hosts**: domaines internes (hac-mi1.rubix.internal)
- **Paths**: `/hac`, `/hmc`, `/backoffice`, `/admin`
- **Backend**: hybris-bo service

### Private Ingress
- **Purpose**: APIs internes
- **Scheme**: internal
- **Hosts**: domaines internes (api-mi1.rubix.internal)
- **Paths**: `/rest`, `/occ`, `/api`
- **Backend**: hybris-api service

### SFTP Service (NLB)
- **Purpose**: Transfert de fichiers
- **Type**: Service LoadBalancer (NLB)
- **Scheme**: internal
- **Port**: 22
- **Backend**: hybris-sftp pod

## Conditions de succes
- [x] Tous les ingresses ont une adresse loadBalancer
- [x] Tous les backends references existent et ont des endpoints
- [x] Rules identiques entre Legacy et NH (hors differences attendues)
- [x] Service SFTP accessible

## Conditions d'alerte
- [x] Ingress sans TLS sur un host public
- [x] Annotations ALB manquantes ou incorrectes
- [x] Differences de rules non attendues
- [x] Service SFTP avec IP pending

## Conditions d'erreur
- [x] Ingress sans adresse depuis > 5min
- [x] Backend service inexistant
- [x] Host manquant dans NH vs Legacy
- [x] SFTP service manquant

## Dependances
- Autres checks requis: `k8s-services`
- Permissions K8s: `get`, `list` sur ingresses et services

## Mapping Comptes/Clusters

| Instance | Env | Legacy Cluster | NH Cluster |
|----------|-----|----------------|------------|
| MI1 | ppd | rubix-nonprod | rubix-dig-ppd-webshop |
| MI2 | ppd | rubix-nonprod | rubix-dig-ppd-webshop |
| FR | ppd | rubix-nonprod-fr | rubix-dig-ppd-webshop |

## Notes
- Verifier les annotations specifiques ALB (scheme, target-type, certificate-arn)
- Le secret TLS doit exister dans le namespace
- Le NLB pour SFTP est different des ALB pour les ingresses
- En NH, les services sont plus separes (front, bo, api) qu'en legacy
- Les hosts internes utilisent `.rubix.internal` vs `.rubix-nonprod.internal`
