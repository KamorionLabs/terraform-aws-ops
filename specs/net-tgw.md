# Spec: Transit Gateway Check

## Identifiant
- **ID**: `net-tgw`
- **Domaine**: network
- **Priorite**: P2 (nice-to-have)
- **Scope**: `GLOBAL` (shared across all instances)

## Objectif
Verifier l'etat du Transit Gateway :
- Status et attachments
- Route tables et propagations
- Connectivite inter-VPC
- Note: TGW est dans le compte network, partage avec tous les comptes

## Architecture

### Composant 1 : Fetch & Store (Step Function)
Recupere l'etat du TGW et stocke en DynamoDB.

- [x] **Step Function** (appels directs AWS SDK)

Note: Pas de composant Compare car le TGW est global et identique pour Legacy et NH.

## Inputs

### Fetch & Store
```json
{
  "Domain": "network",
  "Target": "global",
  "CrossAccountRoleArn": "arn:aws:iam::588738610824:role/ops-dashboard-read",
  "TransitGatewayId": "tgw-xxxxxxxx",
  "ExpectedAttachments": [
    {"name": "rubix-dig-stg-webshop", "accountId": "281127105461", "type": "vpc"},
    {"name": "rubix-dig-ppd-webshop", "accountId": "287223952330", "type": "vpc"},
    {"name": "rubix-dig-prd-webshop", "accountId": "366483377530", "type": "vpc"},
    {"name": "legacy-nonprod", "accountId": "073290922796", "type": "vpc"},
    {"name": "corporate-vpn", "type": "vpn"}
  ]
}
```

## Outputs (payload DynamoDB)

### State (Fetch & Store)
```json
{
  "status": "ok | warning | critical",
  "summary": {
    "transitGatewayId": "tgw-xxxxxxxx",
    "state": "available",
    "totalAttachments": 8,
    "availableAttachments": 8,
    "routeTables": 3,
    "byType": {
      "vpc": 6,
      "vpn": 1,
      "directConnect": 1
    }
  },
  "transitGateway": {
    "id": "tgw-xxxxxxxx",
    "arn": "arn:aws:ec2:eu-central-1:588738610824:transit-gateway/tgw-xxx",
    "state": "available",
    "ownerId": "588738610824",
    "options": {
      "amazonSideAsn": 64512,
      "autoAcceptSharedAttachments": "disable",
      "defaultRouteTableAssociation": "enable",
      "defaultRouteTablePropagation": "enable",
      "vpnEcmpSupport": "enable",
      "dnsSupport": "enable",
      "multicastSupport": "disable"
    },
    "tags": {
      "Name": "rubix-nh-tgw",
      "environment": "shared"
    }
  },
  "attachments": [
    {
      "id": "tgw-attach-xxx",
      "type": "vpc",
      "state": "available",
      "resourceId": "vpc-xxx",
      "resourceOwnerId": "366483377530",
      "name": "rubix-dig-prd-webshop",
      "association": {
        "transitGatewayRouteTableId": "tgw-rtb-xxx",
        "state": "associated"
      },
      "propagation": {
        "transitGatewayRouteTableId": "tgw-rtb-xxx",
        "state": "enabled"
      }
    },
    {
      "id": "tgw-attach-yyy",
      "type": "vpn",
      "state": "available",
      "resourceId": "vpn-xxx",
      "name": "corporate-vpn",
      "association": {
        "transitGatewayRouteTableId": "tgw-rtb-yyy",
        "state": "associated"
      }
    }
  ],
  "routeTables": [
    {
      "id": "tgw-rtb-xxx",
      "name": "workloads-rt",
      "state": "available",
      "defaultAssociationRouteTable": true,
      "defaultPropagationRouteTable": true,
      "routes": [
        {
          "destinationCidrBlock": "10.0.0.0/8",
          "type": "propagated",
          "state": "active",
          "attachmentId": "tgw-attach-xxx"
        },
        {
          "destinationCidrBlock": "192.168.0.0/16",
          "type": "static",
          "state": "active",
          "attachmentId": "tgw-attach-yyy"
        }
      ],
      "associations": 6,
      "propagations": 6
    }
  ],
  "expectedAttachments": {
    "found": [
      {"name": "rubix-dig-stg-webshop", "status": "available"},
      {"name": "rubix-dig-ppd-webshop", "status": "available"},
      {"name": "rubix-dig-prd-webshop", "status": "available"},
      {"name": "legacy-nonprod", "status": "available"},
      {"name": "corporate-vpn", "status": "available"}
    ],
    "missing": []
  },
  "connectivityMatrix": {
    "stg-to-ppd": "reachable",
    "ppd-to-prd": "reachable",
    "prd-to-corporate": "reachable"
  },
  "issues": [],
  "healthy": true,
  "timestamp": "ISO8601"
}
```

## Appels AWS necessaires

### Cross-account (vers compte network)
```json
{
  "Type": "Task",
  "Resource": "arn:aws:states:::aws-sdk:sts:assumeRole",
  "Parameters": {
    "RoleArn": "arn:aws:iam::588738610824:role/ops-dashboard-read",
    "RoleSessionName": "ops-dashboard-tgw-check"
  },
  "ResultPath": "$.Credentials"
}
```

### API Calls
| Service | API Call | Description |
|---------|----------|-------------|
| EC2 | `DescribeTransitGateways` | Details du TGW |
| EC2 | `DescribeTransitGatewayAttachments` | Attachments |
| EC2 | `DescribeTransitGatewayRouteTables` | Route tables |
| EC2 | `SearchTransitGatewayRoutes` | Routes dans une table |
| EC2 | `GetTransitGatewayRouteTableAssociations` | Associations |
| EC2 | `GetTransitGatewayRouteTablePropagations` | Propagations |

## ASL Step Function (structure)
```json
{
  "StartAt": "AssumeRole",
  "States": {
    "AssumeRole": {
      "Type": "Task",
      "Resource": "arn:aws:states:::aws-sdk:sts:assumeRole",
      "Parameters": {
        "RoleArn": "arn:aws:iam::588738610824:role/ops-dashboard-read",
        "RoleSessionName": "ops-dashboard-tgw-check"
      },
      "ResultPath": "$.Credentials",
      "Next": "DescribeTransitGateway"
    },
    "DescribeTransitGateway": {
      "Type": "Task",
      "Resource": "arn:aws:states:::aws-sdk:ec2:describeTransitGateways",
      "Parameters": {
        "TransitGatewayIds.$": "States.Array($.TransitGatewayId)"
      },
      "ResultPath": "$.tgw",
      "Next": "DescribeAttachments"
    },
    "DescribeAttachments": {
      "Type": "Task",
      "Resource": "arn:aws:states:::aws-sdk:ec2:describeTransitGatewayAttachments",
      "Parameters": {
        "Filters": [
          {
            "Name": "transit-gateway-id",
            "Values.$": "States.Array($.TransitGatewayId)"
          }
        ]
      },
      "ResultPath": "$.attachments",
      "Next": "DescribeRouteTables"
    },
    "DescribeRouteTables": {
      "Type": "Task",
      "Resource": "arn:aws:states:::aws-sdk:ec2:describeTransitGatewayRouteTables",
      "Parameters": {
        "Filters": [
          {
            "Name": "transit-gateway-id",
            "Values.$": "States.Array($.TransitGatewayId)"
          }
        ]
      },
      "ResultPath": "$.routeTables",
      "Next": "MapSearchRoutes"
    },
    "MapSearchRoutes": {
      "Type": "Map",
      "ItemsPath": "$.routeTables.TransitGatewayRouteTables",
      "ItemProcessor": {
        "StartAt": "SearchRoutes",
        "States": {
          "SearchRoutes": {
            "Type": "Task",
            "Resource": "arn:aws:states:::aws-sdk:ec2:searchTransitGatewayRoutes",
            "Parameters": {
              "TransitGatewayRouteTableId.$": "$.TransitGatewayRouteTableId",
              "Filters": [
                {
                  "Name": "state",
                  "Values": ["active", "blackhole"]
                }
              ]
            },
            "End": true
          }
        }
      },
      "ResultPath": "$.routes",
      "Next": "CheckExpectedAttachments"
    },
    "CheckExpectedAttachments": {
      "Type": "Task",
      "Resource": "${CheckAttachmentsLambdaArn}",
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
1. Assume role dans le compte network (588738610824)
2. Recuperer les details du Transit Gateway
3. Lister tous les attachments
4. Pour chaque attachment:
   - Verifier le status (available)
   - Verifier les associations et propagations
5. Lister les route tables et routes
6. Verifier que les attachments attendus sont presents
7. Verifier la connectivite (pas de blackholes)
8. Sauvegarder en DynamoDB

## Conditions de succes (status: ok)
- [x] Transit Gateway en state available
- [x] Tous les attachments attendus en state available
- [x] Associations et propagations actives
- [x] Pas de routes blackhole

## Conditions d'alerte (status: warning)
- [x] Attachment en state modifying ou pending
- [x] Route en state blackhole temporaire
- [x] Attachment non utilise (pas d'association)

## Conditions d'erreur (status: critical)
- [x] Transit Gateway non available
- [x] Attachment attendu manquant ou failed
- [x] Attachment en state rejected ou deleted
- [x] Route blackhole persistante vers destination critique

## Dependances
- Services AWS: EC2 (Transit Gateway)
- Permissions IAM (dans le compte network 588738610824):
  - `ec2:DescribeTransitGateways`
  - `ec2:DescribeTransitGatewayAttachments`
  - `ec2:DescribeTransitGatewayRouteTables`
  - `ec2:SearchTransitGatewayRoutes`
  - `ec2:GetTransitGatewayRouteTableAssociations`
  - `ec2:GetTransitGatewayRouteTablePropagations`

## Mapping Comptes AWS

Le TGW est dans le compte network et partage avec tous les comptes :

| Account | Account ID | VPC Name | Attachment Expected |
|---------|------------|----------|---------------------|
| Network | 588738610824 | TGW Owner | N/A |
| Shared Services | 025922408720 | tooling-vpc | Yes |
| Legacy | 073290922796 | legacy-nonprod | Yes |
| NH Staging | 281127105461 | rubix-dig-stg-webshop | Yes |
| NH PreProd | 287223952330 | rubix-dig-ppd-webshop | Yes |
| NH Production | 366483377530 | rubix-dig-prd-webshop | Yes |

## Attachments attendus

| Attachment Name | Type | Account | CIDR |
|-----------------|------|---------|------|
| rubix-dig-stg-webshop | vpc | 281127105461 | 10.1.0.0/16 |
| rubix-dig-ppd-webshop | vpc | 287223952330 | 10.2.0.0/16 |
| rubix-dig-prd-webshop | vpc | 366483377530 | 10.3.0.0/16 |
| legacy-nonprod | vpc | 073290922796 | 10.10.0.0/16 |
| legacy-prod | vpc | 073290922796 | 10.20.0.0/16 |
| corporate-vpn | vpn | - | 192.168.0.0/16 |
| directconnect | dxgw | - | On-premise |

## Route Tables

| Route Table | Purpose | Associations |
|-------------|---------|--------------|
| workloads-rt | VPC workloads | All VPC attachments |
| corporate-rt | VPN/DX traffic | VPN, DirectConnect |
| inspection-rt | Security inspection | Optional |

## Notes
- Le TGW est dans le compte network (588738610824) - cross-account obligatoire
- Verifier les RAM shares si multi-compte
- Les VPCs doivent avoir les routes vers le TGW dans leurs route tables
- Critique pour la connectivite on-premise et inter-VPC
- Pas de comparaison Legacy/NH car le TGW est partage
