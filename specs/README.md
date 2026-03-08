# Specs - Cahiers des charges unitaires

## Organisation

Chaque spec definit un check autonome qui peut etre implemente par un agent independant.

---

## Architecture des Checks

### Scope (Granularite)

Chaque check a un scope qui determine sa granularite :

| Scope | Description | Exemples |
|-------|-------------|----------|
| `INSTANCE` | Par instance commerciale | MI1, MI2, MI3, FR, BENE, INDUS |
| `COUNTRY` | Par pays/region | DNS, CloudFront (multi-instance) |
| `GLOBAL` | Partage entre tous | TGW, ADO Pipelines |

### Composants (Dual Architecture)

La plupart des checks suivent une architecture a deux composants :

```
┌─────────────────────────────────────────────────────────────────┐
│                        Check Complet                             │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  ┌──────────────────────┐    ┌──────────────────────┐          │
│  │  Composant 1         │    │  Composant 2         │          │
│  │  FETCH & STORE       │───▶│  COMPARE             │          │
│  │                      │    │                      │          │
│  │  • Recupere l'etat   │    │  • Lit Legacy depuis │          │
│  │  • Calcule status    │    │    DynamoDB          │          │
│  │  • Sauvegarde        │    │  • Lit NH depuis     │          │
│  │    DynamoDB          │    │    DynamoDB          │          │
│  │                      │    │  • Compare & detecte │          │
│  │  Step Function ou    │    │    differences       │          │
│  │  Lambda selon APIs   │    │  • Identifie expected│          │
│  └──────────────────────┘    │    vs unexpected     │          │
│                              │                      │          │
│                              │  Step Function       │          │
│                              │  (lecture DynamoDB)  │          │
│                              └──────────────────────┘          │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

**Note**: Certains checks n'ont pas de Compare (ex: ADO est specifique NH).

### Cross-Account Access

Pour les checks accedant a plusieurs comptes AWS :

```json
{
  "Type": "Task",
  "Resource": "arn:aws:states:::aws-sdk:sts:assumeRole",
  "Parameters": {
    "RoleArn": "arn:aws:iam::{ACCOUNT_ID}:role/ops-dashboard-read",
    "RoleSessionName": "ops-dashboard-{check-id}"
  },
  "ResultPath": "$.Credentials"
}
```

### Status et Outputs

#### Fetch & Store Status
| Status | Description |
|--------|-------------|
| `ok` | Tout est operationnel |
| `warning` | Degradation mineure |
| `critical` | Probleme bloquant |

#### Compare Status
| Status | Description |
|--------|-------------|
| `synced` | Legacy et NH identiques |
| `differs` | Differences detectees |

#### Difference Types
| Type | Description |
|------|-------------|
| `expected` | Difference attendue (ex: NH a plus de features) |
| `unexpected` | Difference potentiellement problematique |
| `only_legacy` | Element present uniquement sur Legacy |
| `only_nh` | Element present uniquement sur New Horizon |

---

## Nomenclature

| Prefixe | Domaine | Description |
|---------|---------|-------------|
| `infra-` | Infrastructure | EKS, RDS, EFS, EC2 |
| `k8s-` | Kubernetes | Pods, Services, Ingress, Secrets |
| `repl-` | Replication | DMS, EFS sync, RDS replicas |
| `net-` | Network | DNS, ALB, Security Groups, CloudFront, TGW |
| `cost-` | FinOps | Couts, anomalies |
| `repo-` | Repositories | Terraform, Helm, ArgoCD, Bitbucket |
| `cicd-` | CI/CD | ADO Pipelines, Jenkins, ArgoCD, Bitbucket |
| `config-` | Configuration | Secrets Manager, SSM Parameter Store |
| `val-` | Validation | Smoke tests, Health checks, Sync markers |
| `sec-` | Security | IAM, secrets rotation |

---

## Mapping Comptes AWS

### New Horizon Accounts

| Env | Account ID | AWS Profile | Cluster |
|-----|------------|-------------|---------|
| int | 492919832539 | digital-webshop-integration/AWSAdministratorAccess | nh-int-webshop |
| stg | 281127105461 | digital-webshop-staging/AWSAdministratorAccess | rubix-dig-stg-webshop |
| ppd | 287223952330 | digital-webshop-preprod/AWSAdministratorAccess | rubix-dig-ppd-webshop |
| prd | 366483377530 | digital-webshop-prod/AWSAdministratorAccess | rubix-dig-prd-webshop |

### Legacy Account

| Env | Account ID | Region | Cluster |
|-----|------------|--------|---------|
| DE (stg/ppd/prd) | 073290922796 | eu-central-1 | rubix-nonprod / rubix-prod |
| FR (stg/ppd/prd) | 073290922796 | eu-west-3 | rubix-nonprod-fr / rubix-prod-fr |

### Shared Accounts

| Account | Account ID | Usage |
|---------|------------|-------|
| Network | 588738610824 | TGW, Route53, VPN |
| Shared Services | 025922408720 | ECR, Tooling EKS |
| Management | 218807463257 | Organizations, SCPs |

---

## Liste des checks

### Infrastructure (P0 - Critique)
| ID | Spec | Scope | Status | Implementation |
|----|------|-------|--------|----------------|
| `infra-eks` | [infra-eks-cluster.md](./infra-eks-cluster.md) | INSTANCE | A faire | Step Function + Compare |
| `infra-rds` | [infra-rds-cluster.md](./infra-rds-cluster.md) | INSTANCE | A faire | Step Function + Compare |
| `infra-efs` | [infra-efs-filesystem.md](./infra-efs-filesystem.md) | INSTANCE | A faire | Step Function + Compare |

### Kubernetes (P0 - Critique)
| ID | Spec | Scope | Status | Implementation |
|----|------|-------|--------|----------------|
| `k8s-pods` | [k8s-pods-readiness.md](./k8s-pods-readiness.md) | INSTANCE | A faire | Step Function (eks:call) + Compare |
| `k8s-services` | [k8s-services-status.md](./k8s-services-status.md) | INSTANCE | A faire | Step Function (eks:call) + Compare |
| `k8s-ingress` | [k8s-ingress-status.md](./k8s-ingress-status.md) | INSTANCE | A faire | Step Function (eks:call) + Compare |
| `k8s-pvc` | [k8s-pvc-status.md](./k8s-pvc-status.md) | INSTANCE | A faire | Step Function (eks:call) + Compare |
| `k8s-secrets` | [k8s-secrets-sync.md](./k8s-secrets-sync.md) | INSTANCE | A faire | Lambda + Compare |
| `k8s-hpa` | [k8s-hpa-status.md](./k8s-hpa-status.md) | INSTANCE | A faire | Step Function (eks:call) + Compare |

### Replication (P0 - Critique pour migration)
| ID | Spec | Scope | Status | Implementation |
|----|------|-------|--------|----------------|
| `repl-dms` | [repl-dms-tasks.md](./repl-dms-tasks.md) | INSTANCE | A faire | Lambda (DMS Serverless) |
| `repl-efs` | [repl-efs-sync.md](./repl-efs-sync.md) | INSTANCE | A faire | Step Function (EFS native) |
| `repl-rds` | [repl-rds-replicas.md](./repl-rds-replicas.md) | INSTANCE | A faire | Step Function |

### Validation (P0 - Critique)
| ID | Spec | Scope | Status | Implementation |
|----|------|-------|--------|----------------|
| `val-url` | [val-url.md](./val-url.md) | INSTANCE | A faire | Lambda (HTTP tests) |
| `val-hybris` | [val-hybris.md](./val-hybris.md) | INSTANCE | A faire | Lambda (HAC API) |
| `val-sync` | [val-sync-markers.md](./val-sync-markers.md) | INSTANCE | A faire | Lambda (DB + EFS) |

### Network (P1 - Important)
| ID | Spec | Scope | Status | Implementation |
|----|------|-------|--------|----------------|
| `net-dns` | [net-dns-resolution.md](./net-dns-resolution.md) | COUNTRY | A faire | Lambda (cross-account) |
| `net-alb` | [net-alb-health.md](./net-alb-health.md) | INSTANCE | A faire | Step Function + Compare |
| `net-sg` | [net-security-groups.md](./net-security-groups.md) | INSTANCE | A faire | Step Function + Compare |
| `net-cf` | [net-cloudfront.md](./net-cloudfront.md) | COUNTRY | A faire | Step Function |
| `net-tgw` | [net-tgw.md](./net-tgw.md) | GLOBAL | A faire | Step Function (cross-account) |

### Repositories (P1 - Important pour migration)
| ID | Spec | Scope | Status | Implementation |
|----|------|-------|--------|----------------|
| `repo-tf-aws` | [repo-tf-aws.md](./repo-tf-aws.md) | GLOBAL | A faire | Lambda (ADO API) |
| `repo-helm` | [repo-helm.md](./repo-helm.md) | GLOBAL | A faire | Lambda (ADO API) |
| `repo-argocd` | [repo-argocd.md](./repo-argocd.md) | INSTANCE | A faire | Lambda (ADO API) |
| `repo-bb-hybris` | [repo-bb-hybris.md](./repo-bb-hybris.md) | INSTANCE | A faire | Lambda (Bitbucket API) |

### CI/CD (P1 - Important)
| ID | Spec | Scope | Status | Implementation |
|----|------|-------|--------|----------------|
| `cicd-ado` | [cicd-ado.md](./cicd-ado.md) | GLOBAL | A faire | Lambda (ADO API) |
| `cicd-jenkins` | [cicd-jenkins.md](./cicd-jenkins.md) | INSTANCE | A faire | Lambda + Compare |
| `cicd-argocd` | [cicd-argocd.md](./cicd-argocd.md) | INSTANCE | A faire | Lambda + Compare |
| `cicd-bb` | [cicd-bb.md](./cicd-bb.md) | GLOBAL | A faire | Lambda (Bitbucket API) |

### Configuration (P1 - Important)
| ID | Spec | Scope | Status | Implementation |
|----|------|-------|--------|----------------|
| `config-sm` | [config-secrets-manager.md](./config-secrets-manager.md) | INSTANCE | A faire | Step Function + Compare |
| `config-ssm` | [config-ssm.md](./config-ssm.md) | INSTANCE | A faire | Step Function + Compare |

### Cost / FinOps (P1 - Important post-migration)
| ID | Spec | Scope | Status | Implementation |
|----|------|-------|--------|----------------|
| `cost-daily` | [cost-daily-summary.md](./cost-daily-summary.md) | GLOBAL | A faire | Lambda (Cost Explorer) |
| `cost-anomaly` | [cost-anomaly-detection.md](./cost-anomaly-detection.md) | GLOBAL | A faire | Step Function |

### Security (P2 - Nice-to-have)
| ID | Spec | Scope | Status | Implementation |
|----|------|-------|--------|----------------|
| `sec-iam` | sec-iam-audit.md | GLOBAL | A creer | Lambda (IAM Access Analyzer) |
| `sec-secrets` | sec-secrets-rotation.md | INSTANCE | A creer | Step Function |

---

## Resume par implementation

### Step Functions Only (8 checks)
- `repl-efs`, `repl-rds`
- `net-alb`, `net-sg`, `net-cf`, `net-tgw`
- `cost-anomaly`
- `sec-secrets`

### Step Function + Compare (13 checks)
- `infra-eks`, `infra-rds`, `infra-efs`
- `k8s-pods`, `k8s-services`, `k8s-ingress`, `k8s-pvc`, `k8s-hpa`
- `config-sm`, `config-ssm`

### Lambdas avec APIs externes (12 checks)
- `k8s-secrets` (External Secrets API)
- `repl-dms` (DMS Serverless)
- `val-url`, `val-hybris`, `val-sync`
- `net-dns` (Route53 cross-account)
- `repo-tf-aws`, `repo-helm`, `repo-argocd`, `repo-bb-hybris`
- `cicd-ado`, `cicd-jenkins`, `cicd-argocd`, `cicd-bb`
- `cost-daily`
- `sec-iam`

---

## Secrets et Authentification

### AWS Secrets Manager
| Secret | Usage |
|--------|-------|
| `ops-dashboard/ado-pat` | Azure DevOps Personal Access Token |
| `ops-dashboard/jenkins-token` | Jenkins API Token + Username |
| `ops-dashboard/argocd-token` | ArgoCD API Token |
| `ops-dashboard/bitbucket-token` | Bitbucket App Password |
| `ops-dashboard/hybris-hac` | Hybris HAC credentials |

### Cross-Account IAM Roles
Chaque compte cible doit avoir un role `ops-dashboard-read` avec :
- Trust policy vers le compte ops-dashboard
- Permissions en lecture seule sur les ressources checkees

---

## DynamoDB Schema

### Primary Table
```
pk: domain#target     (ex: "mro#mi1-ppd-legacy", "webshop#prod-nh")
sk: check_type        (ex: "infra-eks", "k8s-pods", "cicd-argocd")

Attributes:
- payload (Map): Resultat du check
- status (String): ok | warning | critical
- updated_at (String): ISO8601 timestamp
- updated_by (String): source du check
- hash (String): SHA256 du payload pour change detection
```

### History Table (TTL 90 jours)
```
pk: domain#target#check_type
sk: timestamp (ISO8601)

Attributes:
- payload (Map): Snapshot historique
- status (String): Status au moment du snapshot
```

---

## Workflow d'implementation

1. **Selection** : Choisir une spec a implementer
2. **Agent** : Lancer un agent autonome avec la spec comme contexte
3. **Implementation** : L'agent cree les fichiers (SF ASL / Lambda / Terraform)
4. **Review** : Validation humaine du code genere
5. **Test** : Execution manuelle puis integration
6. **Deploy** : Terraform apply

## Commande agent

```bash
# Lancer un agent pour implementer une spec
claude --prompt "Implemente le check defini dans specs/[SPEC].md.
Cree les fichiers necessaires:
- step-functions/[check-id].asl.json (si SF)
- lambdas/[check-id]/[check_id].py (si Lambda)
- Mets a jour terraform/step-functions.tf ou lambdas.tf
Respecte les patterns existants dans le projet."
```

## Agents en parallele

Pour implementer plusieurs checks simultanement :

```bash
# Infrastructure P0
claude --prompt "Implemente infra-eks" &
claude --prompt "Implemente infra-rds" &
claude --prompt "Implemente infra-efs" &
wait

# Kubernetes P0
claude --prompt "Implemente k8s-pods" &
claude --prompt "Implemente k8s-services" &
...
```

---

## Differences Legacy vs New Horizon

### Differences attendues (expected)

| Aspect | Legacy | New Horizon | Raison |
|--------|--------|-------------|--------|
| Add-ons EKS | Geres manuellement | EKS managed | Migration vers managed |
| Secrets | Kubernetes Secrets | External Secrets + Infisical | Meilleure securite |
| Autoscaling | Cluster Autoscaler | Karpenter | Performance |
| GitOps | Helm direct | ArgoCD | Tracabilite |
| Values files | values.yaml generique | values-{instance}-{env}.yaml | Isolation |
| Ingress | ALB Ingress | AWS LB Controller | Standard |

### Differences a verifier (unexpected)

| Aspect | Attendu |
|--------|---------|
| Versions applicatives | Identiques Legacy/NH |
| Nombre de replicas | Identiques (ou NH >= Legacy) |
| Secrets | Memes cles/valeurs |
| DNS resolution | Memes records |
| Security groups | Memes regles entrantes |

---

## Notes importantes

- **EFS replication** : Utilise la replication native EFS, PAS DataSync.
- **DMS** : Utilise DMS Serverless (pas d'instances a gerer).
- **APIs externes** : Les checks repos/cicd utilisent des APIs (ADO, Jenkins, ArgoCD, Bitbucket) donc Lambda obligatoire.
- **ArgoCD values** : Critique de verifier la presence des values files pour l'instance/env.
- **Validation** : Les checks `val-*` sont critiques pour la migration, a executer avant/apres bascule.
- **Security** : Ne jamais logger ou stocker les valeurs des secrets dans DynamoDB.
- **Cross-account** : Toujours utiliser assume role, jamais de credentials hardcodes.
