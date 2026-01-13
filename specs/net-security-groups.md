# Spec: Security Groups Audit

## Identifiant
- **ID**: `net-sg`
- **Domaine**: network
- **Priorite**: P2 (nice-to-have)
- **Scope**: `INSTANCE` (MI1, MI2, MI3, FR, BENE, INDUS)

## Objectif
Auditer les security groups :
- Regles ouvertes et conformite
- References entre SG
- Ports sensibles exposes
- Comparaison Legacy vs New Horizon (memes SG)

## Architecture

### Composant 1 : Fetch & Store (Step Function)
Recupere l'etat des SG et stocke en DynamoDB.

- [x] **Step Function** (appels directs AWS SDK)
- [x] **Lambda** AnalyzeSecurityGroups (analyse de conformite)

### Composant 2 : Compare (Step Function)
Compare les SG Legacy vs New Horizon.

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
  "VpcId": "vpc-xxx",
  "SecurityGroupIds": ["sg-xxx"],
  "SecurityGroupTags": {"kubernetes.io/cluster/rubix-dig-ppd-webshop": "owned"}
}
```

### Compare
```json
{
  "Domain": "string",
  "Instance": "MI1",
  "Environment": "ppd",
  "LegacyStateKey": "mro#mi1-ppd-legacy|net-sg",
  "NHStateKey": "mro#mi1-ppd-nh|net-sg"
}
```

## Outputs (payload DynamoDB)

### State (Fetch & Store)
```json
{
  "status": "ok | warning | critical",
  "source": "legacy | nh",
  "summary": {
    "total": 15,
    "compliant": 12,
    "warnings": 2,
    "violations": 1,
    "unused": 0
  },
  "securityGroups": [
    {
      "id": "sg-xxx",
      "name": "eks-cluster-sg",
      "vpcId": "vpc-xxx",
      "description": "EKS cluster security group",
      "ingressRules": [
        {
          "protocol": "tcp",
          "fromPort": 443,
          "toPort": 443,
          "source": "sg-yyy",
          "sourceType": "securityGroup",
          "description": "HTTPS from ALB"
        },
        {
          "protocol": "tcp",
          "fromPort": 443,
          "toPort": 443,
          "source": "10.0.0.0/8",
          "sourceType": "cidr",
          "description": "HTTPS from VPC"
        }
      ],
      "egressRules": [
        {
          "protocol": "-1",
          "fromPort": 0,
          "toPort": 0,
          "destination": "0.0.0.0/0",
          "description": "Allow all outbound"
        }
      ],
      "usedBy": {
        "enis": ["eni-xxx", "eni-yyy"],
        "instances": [],
        "lambdas": [],
        "rds": [],
        "count": 2
      },
      "findings": [],
      "compliant": true,
      "tags": {
        "kubernetes.io/cluster/rubix-dig-ppd-webshop": "owned"
      }
    }
  ],
  "findings": [
    {
      "sgId": "sg-yyy",
      "sgName": "legacy-db-sg",
      "severity": "HIGH",
      "rule": "SSH (22) open to 0.0.0.0/0",
      "recommendation": "Restrict to bastion or VPN CIDR"
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
    "sgCount": "synced | differs",
    "compliance": "synced | differs",
    "rulesCount": "synced | differs"
  },
  "sgComparison": {
    "sameSGs": ["eks-cluster-sg", "eks-node-sg"],
    "differentConfig": [
      {
        "sg": "db-access-sg",
        "legacy": {
          "ingressRules": 5,
          "openTo": ["0.0.0.0/0"],
          "findings": ["SSH open to internet"]
        },
        "nh": {
          "ingressRules": 3,
          "openTo": ["10.0.0.0/8"],
          "findings": []
        },
        "expected": true,
        "reason": "NH has stricter security rules"
      }
    ],
    "onlyLegacy": ["legacy-bastion-sg"],
    "onlyNH": ["eks-fargate-profile-sg"]
  },
  "complianceComparison": {
    "legacy": {"compliant": 10, "violations": 3, "warnings": 2},
    "nh": {"compliant": 14, "violations": 0, "warnings": 1},
    "expected": true,
    "reason": "NH follows security best practices"
  },
  "rulesComparison": {
    "legacy": {"totalIngress": 45, "totalEgress": 15},
    "nh": {"totalIngress": 35, "totalEgress": 15},
    "expected": true,
    "reason": "NH has consolidated rules"
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
    "RoleSessionName": "ops-dashboard-sg-check"
  },
  "ResultPath": "$.Credentials"
}
```

### API Calls
| Service | API Call | Description |
|---------|----------|-------------|
| EC2 | `DescribeSecurityGroups` | Liste des SG |
| EC2 | `DescribeSecurityGroupRules` | Regles des SG |
| EC2 | `DescribeNetworkInterfaces` | ENIs utilisant les SG |

## Logique metier

### Fetch & Store
1. Assume role cross-account si necessaire
2. Lister les security groups (par VPC ou tags)
3. Pour chaque SG:
   - Extraire les regles ingress/egress
   - Verifier la conformite (ports sensibles)
   - Identifier les sources/destinations ouvertes
   - Lister les ressources utilisant ce SG
4. Generer les findings de securite
5. Calculer le summary
6. Sauvegarder en DynamoDB

### Compare
1. Recuperer state Legacy depuis DynamoDB
2. Recuperer state NH depuis DynamoDB
3. Comparer les SG par nom/fonction:
   - Memes SG presents ?
   - Memes regles ?
4. Comparer la conformite:
   - Moins de violations sur NH ?
   - Regles plus strictes ?
5. Identifier les differences:
   - Attendues (securite renforcee)
   - Inattendues (drift)
6. Calculer le status global

## Conditions de succes (status: ok)
- [x] Aucune regle SSH/RDP ouverte a 0.0.0.0/0
- [x] Ports DB non exposes publiquement
- [x] Tous les SG ont des descriptions
- [x] Pas de SG non utilises

## Conditions d'alerte (status: warning)
- [x] SG non utilise depuis longtemps
- [x] Regle sans description
- [x] Trop de regles dans un SG (> 50)
- [x] CIDR trop large (ex: /8 ouvert)

## Conditions d'erreur (status: critical)
- [x] SSH (22) ouvert a 0.0.0.0/0
- [x] RDP (3389) ouvert a 0.0.0.0/0
- [x] Ports DB (3306, 5432, 27017) ouverts publiquement
- [x] Ports de management (8080, 9000) ouverts publiquement

## Dependances
- Prerequis: `infra-eks` (VPC cree)
- Services AWS: EC2
- Permissions IAM (dans le role cross-account):
  - `ec2:DescribeSecurityGroups`
  - `ec2:DescribeSecurityGroupRules`
  - `ec2:DescribeNetworkInterfaces`

## Mapping Comptes AWS

| Instance | Env | Legacy Account | NH Account |
|----------|-----|----------------|------------|
| MI1/MI2/MI3 | stg | 073290922796 | 281127105461 |
| MI1/MI2/MI3 | ppd | 073290922796 | 287223952330 |
| MI1/MI2/MI3 | prd | 073290922796 | 366483377530 |
| FR | * | 073290922796 (eu-west-3) | selon env |

## Security Groups attendus

| SG Name Pattern | Usage | Legacy | NH |
|-----------------|-------|--------|-----|
| eks-cluster-sg | EKS control plane | Yes | Yes |
| eks-node-sg | EKS worker nodes | Yes | Yes |
| rds-sg | Aurora database | Yes | Yes |
| efs-sg | EFS mount targets | Yes | Yes |
| alb-sg | Load balancers | Yes | Yes |
| bastion-sg | Bastion hosts | Yes | No |

## Ports sensibles a surveiller

| Port | Service | Allowed Sources |
|------|---------|-----------------|
| 22 | SSH | Bastion SG only |
| 3389 | RDP | Never |
| 3306 | MySQL | App SG only |
| 5432 | PostgreSQL | App SG only |
| 27017 | MongoDB | App SG only |
| 6379 | Redis | App SG only |
| 9001 | Hybris | ALB SG only |

## Lambda AnalyzeSecurityGroups

L'analyse de conformite necessite une Lambda pour la logique complexe :

```python
SENSITIVE_PORTS = {
    22: 'SSH',
    3389: 'RDP',
    3306: 'MySQL',
    5432: 'PostgreSQL',
    27017: 'MongoDB',
    6379: 'Redis',
    9200: 'Elasticsearch'
}

def lambda_handler(event, context):
    security_groups = event.get('securityGroups', [])

    findings = []
    summary = {
        'total': len(security_groups),
        'compliant': 0,
        'warnings': 0,
        'violations': 0
    }

    for sg in security_groups:
        sg_findings = []

        for rule in sg.get('IpPermissions', []):
            from_port = rule.get('FromPort', 0)
            to_port = rule.get('ToPort', 65535)

            # Check if open to internet
            for ip_range in rule.get('IpRanges', []):
                cidr = ip_range.get('CidrIp', '')

                if cidr == '0.0.0.0/0':
                    # Check sensitive ports
                    for port, service in SENSITIVE_PORTS.items():
                        if from_port <= port <= to_port:
                            findings.append({
                                'sgId': sg['GroupId'],
                                'sgName': sg['GroupName'],
                                'severity': 'HIGH',
                                'rule': f'{service} ({port}) open to 0.0.0.0/0',
                                'recommendation': f'Restrict {service} to specific IPs or SGs'
                            })
                            summary['violations'] += 1

        if not sg_findings:
            summary['compliant'] += 1

    return {
        'findings': findings,
        'summary': summary
    }
```

## Notes
- Integrer avec AWS Security Hub pour des checks plus complets
- Verifier aussi les VPC Flow Logs si activees
- NH devrait avoir des regles plus strictes que Legacy
- Les SG EKS sont geres par le AWS Load Balancer Controller
