# Spec: ALB Health Check

## Identifiant
- **ID**: `net-alb`
- **Domaine**: network
- **Priorite**: P1 (important)
- **Scope**: `INSTANCE` (MI1, MI2, MI3, FR, BENE, INDUS)

## Objectif
Verifier l'etat des Application Load Balancers :
- Status et configuration
- Target groups et healthy targets
- Listeners et rules
- Comparaison Legacy vs New Horizon (memes ALB)

## Architecture

### Composant 1 : Fetch & Store (Step Function)
Recupere l'etat ALB et stocke en DynamoDB.

- [x] **Step Function** (appels directs AWS SDK)

### Composant 2 : Compare (Step Function)
Compare les ALB Legacy vs New Horizon.

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
  "LoadBalancerArns": ["arn:aws:elasticloadbalancing:eu-central-1:xxx:loadbalancer/app/k8s-hybris/xxx"],
  "LoadBalancerTags": {"kubernetes.io/cluster/rubix-dig-ppd-webshop": "owned"}
}
```

### Compare
```json
{
  "Domain": "string",
  "Instance": "MI1",
  "Environment": "ppd",
  "LegacyStateKey": "mro#mi1-ppd-legacy|net-alb",
  "NHStateKey": "mro#mi1-ppd-nh|net-alb"
}
```

## Outputs (payload DynamoDB)

### State (Fetch & Store)
```json
{
  "status": "ok | warning | critical",
  "source": "legacy | nh",
  "summary": {
    "total": 2,
    "active": 2,
    "healthy": 2,
    "totalTargets": 6,
    "healthyTargets": 6
  },
  "loadBalancers": [
    {
      "arn": "arn:aws:elasticloadbalancing:eu-central-1:xxx:loadbalancer/app/k8s-hybris/xxx",
      "name": "k8s-hybris-alb",
      "state": "active",
      "type": "application",
      "scheme": "internet-facing",
      "ipAddressType": "ipv4",
      "dnsName": "k8s-xxx.eu-central-1.elb.amazonaws.com",
      "vpcId": "vpc-xxx",
      "securityGroups": ["sg-xxx", "sg-yyy"],
      "availabilityZones": ["eu-central-1a", "eu-central-1b", "eu-central-1c"],
      "listeners": [
        {
          "arn": "arn:aws:elasticloadbalancing:...:listener/...",
          "protocol": "HTTPS",
          "port": 443,
          "sslPolicy": "ELBSecurityPolicy-TLS13-1-2-2021-06",
          "defaultAction": "forward",
          "targetGroupArn": "arn:aws:elasticloadbalancing:...:targetgroup/k8s-hybris-tg/xxx"
        }
      ],
      "targetGroups": [
        {
          "arn": "arn:aws:elasticloadbalancing:...:targetgroup/k8s-hybris-tg/xxx",
          "name": "k8s-hybris-tg",
          "protocol": "HTTP",
          "port": 9001,
          "targetType": "ip",
          "healthCheck": {
            "path": "/health",
            "protocol": "HTTP",
            "intervalSeconds": 30,
            "timeoutSeconds": 5,
            "healthyThresholdCount": 2,
            "unhealthyThresholdCount": 3
          },
          "healthyCount": 3,
          "unhealthyCount": 0,
          "targets": [
            {"id": "10.0.1.10", "port": 9001, "health": "healthy", "az": "eu-central-1a"},
            {"id": "10.0.1.11", "port": 9001, "health": "healthy", "az": "eu-central-1b"},
            {"id": "10.0.1.12", "port": 9001, "health": "healthy", "az": "eu-central-1c"}
          ]
        }
      ],
      "tags": {
        "kubernetes.io/cluster/rubix-dig-ppd-webshop": "owned",
        "kubernetes.io/service-name": "hybris/hybris-lb"
      }
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
    "albCount": "synced | differs",
    "targetHealth": "synced | differs",
    "listenerConfig": "synced | differs"
  },
  "albComparison": {
    "sameALBs": ["hybris-alb", "apache-alb"],
    "differentConfig": [
      {
        "alb": "hybris-alb",
        "legacy": {
          "scheme": "internal",
          "sslPolicy": "ELBSecurityPolicy-2016-08",
          "targetType": "instance"
        },
        "nh": {
          "scheme": "internet-facing",
          "sslPolicy": "ELBSecurityPolicy-TLS13-1-2-2021-06",
          "targetType": "ip"
        },
        "expected": true,
        "reason": "NH uses modern TLS and IP target type for EKS"
      }
    ],
    "onlyLegacy": [],
    "onlyNH": ["api-alb"]
  },
  "targetHealthComparison": {
    "legacy": {"total": 6, "healthy": 6, "unhealthy": 0},
    "nh": {"total": 6, "healthy": 6, "unhealthy": 0},
    "status": "synced"
  },
  "securityComparison": {
    "legacy": {"sslPolicy": "ELBSecurityPolicy-2016-08"},
    "nh": {"sslPolicy": "ELBSecurityPolicy-TLS13-1-2-2021-06"},
    "expected": true,
    "reason": "NH uses TLS 1.3"
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
    "RoleSessionName": "ops-dashboard-alb-check"
  },
  "ResultPath": "$.Credentials"
}
```

### API Calls
| Service | API Call | Description |
|---------|----------|-------------|
| ELBv2 | `DescribeLoadBalancers` | Liste des ALB |
| ELBv2 | `DescribeListeners` | Listeners par ALB |
| ELBv2 | `DescribeTargetGroups` | Target groups par ALB |
| ELBv2 | `DescribeTargetHealth` | Sante des targets |
| ELBv2 | `DescribeTags` | Tags des ALB |

## ASL Step Function (structure)
```json
{
  "StartAt": "AssumeRole",
  "States": {
    "AssumeRole": {
      "Type": "Task",
      "Resource": "arn:aws:states:::aws-sdk:sts:assumeRole",
      "Parameters": {
        "RoleArn.$": "$.CrossAccountRoleArn",
        "RoleSessionName": "ops-dashboard-alb-check"
      },
      "ResultPath": "$.Credentials",
      "Next": "DescribeLoadBalancers"
    },
    "DescribeLoadBalancers": {
      "Type": "Task",
      "Resource": "arn:aws:states:::aws-sdk:elasticloadbalancingv2:describeLoadBalancers",
      "Parameters": {
        "LoadBalancerArns.$": "$.LoadBalancerArns"
      },
      "ResultPath": "$.loadBalancers",
      "Next": "MapGetDetails"
    },
    "MapGetDetails": {
      "Type": "Map",
      "ItemsPath": "$.loadBalancers.LoadBalancers",
      "ItemProcessor": {
        "StartAt": "DescribeListeners",
        "States": {
          "DescribeListeners": {
            "Type": "Task",
            "Resource": "arn:aws:states:::aws-sdk:elasticloadbalancingv2:describeListeners",
            "Parameters": {
              "LoadBalancerArn.$": "$.LoadBalancerArn"
            },
            "ResultPath": "$.listeners",
            "Next": "DescribeTargetGroups"
          },
          "DescribeTargetGroups": {
            "Type": "Task",
            "Resource": "arn:aws:states:::aws-sdk:elasticloadbalancingv2:describeTargetGroups",
            "Parameters": {
              "LoadBalancerArn.$": "$.LoadBalancerArn"
            },
            "ResultPath": "$.targetGroups",
            "Next": "MapDescribeTargetHealth"
          },
          "MapDescribeTargetHealth": {
            "Type": "Map",
            "ItemsPath": "$.targetGroups.TargetGroups",
            "ItemProcessor": {
              "StartAt": "DescribeTargetHealth",
              "States": {
                "DescribeTargetHealth": {
                  "Type": "Task",
                  "Resource": "arn:aws:states:::aws-sdk:elasticloadbalancingv2:describeTargetHealth",
                  "Parameters": {
                    "TargetGroupArn.$": "$.TargetGroupArn"
                  },
                  "End": true
                }
              }
            },
            "ResultPath": "$.targetHealth",
            "End": true
          }
        }
      },
      "ResultPath": "$.albDetails",
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
1. Assume role cross-account si necessaire
2. Decrire les load balancers (par ARN ou tags)
3. Pour chaque ALB:
   - Lister les listeners
   - Lister les target groups
   - Pour chaque TG, verifier la sante des targets
   - Extraire les security groups et AZs
4. Calculer le summary
5. Sauvegarder en DynamoDB

### Compare
1. Recuperer state Legacy depuis DynamoDB
2. Recuperer state NH depuis DynamoDB
3. Comparer les ALB par nom/service:
   - Memes ALB presents ?
   - Meme configuration (scheme, SSL policy) ?
4. Comparer la sante des targets:
   - Meme nombre de targets ?
   - Meme ratio healthy/unhealthy ?
5. Identifier les differences:
   - Attendues (TLS 1.3, IP target type)
   - Inattendues (drift)
6. Calculer le status global

## Conditions de succes (status: ok)
- [x] Tous les ALB en state active
- [x] Tous les targets healthy
- [x] Au moins 1 target healthy par TG
- [x] SSL policy a jour (TLS 1.2+)

## Conditions d'alerte (status: warning)
- [x] ALB en state provisioning
- [x] Target en state draining
- [x] < 50% des targets healthy
- [x] SSL policy obsolete

## Conditions d'erreur (status: critical)
- [x] ALB en state failed
- [x] Aucun target healthy dans un TG
- [x] Tous les targets unhealthy
- [x] ALB attendu manquant

## Dependances
- Prerequis: `infra-eks`, `k8s-services`
- Services AWS: ELBv2
- Permissions IAM (dans le role cross-account):
  - `elasticloadbalancing:DescribeLoadBalancers`
  - `elasticloadbalancing:DescribeListeners`
  - `elasticloadbalancing:DescribeTargetGroups`
  - `elasticloadbalancing:DescribeTargetHealth`
  - `elasticloadbalancing:DescribeTags`

## Mapping Comptes AWS

| Instance | Env | Legacy Account | NH Account |
|----------|-----|----------------|------------|
| MI1/MI2/MI3 | stg | 073290922796 | 281127105461 |
| MI1/MI2/MI3 | ppd | 073290922796 | 287223952330 |
| MI1/MI2/MI3 | prd | 073290922796 | 366483377530 |
| FR | * | 073290922796 (eu-west-3) | selon env |

## ALB attendus par instance

| Instance | ALB Name Pattern | Services |
|----------|-----------------|----------|
| MI1 | k8s-hybris-* | hybris, apache |
| MI2 | k8s-hybris-* | hybris, apache |
| MI3 | k8s-hybris-* | hybris, apache |
| FR | k8s-hybris-* | hybris, apache |

## Differences attendues Legacy vs NH

| Element | Legacy | NH | Raison |
|---------|--------|-----|--------|
| SSL Policy | ELBSecurityPolicy-2016-08 | ELBSecurityPolicy-TLS13-1-2-2021-06 | TLS 1.3 support |
| Target Type | instance | ip | EKS pod networking |
| Scheme | internal | internet-facing | CloudFront origin |
| Tags | custom | kubernetes.io/* | AWS LB Controller |

## Notes
- Les ALB geres par le AWS Load Balancer Controller ont des tags specifiques
- Verifier aussi les rules des listeners pour le routing
- Les security groups des ALB doivent permettre le trafic CloudFront
- Legacy utilise souvent des ALB internes, NH les expose via CloudFront
