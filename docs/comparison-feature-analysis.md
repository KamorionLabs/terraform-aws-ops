# Rapport : Feature de Comparaison d'Environnement

**Date** : 2026-01-12
**Status** : Analyse complete

## Vue d'ensemble

La feature de comparaison permet de comparer l'etat de deux environnements (typiquement Legacy vs New Horizon) pour identifier les differences avant/pendant une migration.

---

## Etat actuel du deploiement

### Step Functions Compare deployees (12)

| Step Function | Lambda associee | Deploye TF | Teste |
|---------------|-----------------|------------|-------|
| `config-sm-compare` | compare-secrets-manager | Oui | Oui |
| `config-ssm-compare` | compare-ssm | Oui | Non |
| `net-cloudfront-compare` | compare-cloudfront | Oui | Oui |
| `net-dns-compare` | compare-dns | Oui | Non |
| `net-alb-compare` | compare-alb | Oui | Non |
| `net-sg-compare` | compare-security-groups | Oui | Non |
| `infra-rds-compare` | compare-rds | Oui | Oui |
| `k8s-pods-compare` | compare-pods | Oui | Non |
| `k8s-services-compare` | compare-services | Oui | Non |
| `k8s-pvc-compare` | compare-pvc | Oui | Non |
| `k8s-ingress-compare` | compare-ingress | Oui | Non |
| `k8s-secrets-compare` | compare-secrets | Oui | Non |

### Orchestrator

`comparison-orchestrator` deploye mais **incomplet**.

---

## Problemes identifies

### 1. Orchestrator gravement incomplet

L'orchestrateur actuel (`comparison-orchestrator.asl.json`) ne coordonne que **5 types K8s** :
- pods, services, ingress, pvc, secrets

**Manquent completement** :
- Config : SM (Secrets Manager), SSM
- Network : DNS, CloudFront, ALB, SG
- Infra : RDS

C'est-a-dire que 7 des 12 comparateurs ne sont pas orchestres.

### 2. Parametres manquants dans l'orchestrator

L'orchestrateur appelle les checkers avec seulement `Project` et `Env` :

```json
"Input": {
  "Project.$": "$.Project",
  "Env.$": "$.SourceEnv"
}
```

Mais les checkers K8s necessitent :
- `ClusterName` : Nom du cluster EKS
- `Namespace` : Namespace K8s
- `CrossAccountRoleArn` : Role IAM cross-account
- `SecretProviderType` : Pour secrets-sync-checker (csi/external-secrets)

**L'orchestrateur echouera systematiquement** car il ne passe pas ces parametres obligatoires.

### 3. Interfaces incoherentes entre Step Functions

| Step Function | Parametres input |
|---------------|------------------|
| `config-sm-compare` | Project, SourceEnv, DestinationEnv, SourceStateKey, DestinationStateKey |
| `net-cloudfront-compare` | Project, **Env, Instance, Environment**, SourceStateKey, DestinationStateKey |
| `k8s-*-compare` | Project, SourceEnv, DestinationEnv, SourceStateKey, DestinationStateKey |

`net-cloudfront-compare` utilise `Env` (singulier) au lieu de `SourceEnv/DestinationEnv`.

### 4. Schema DynamoDB incoherent

Format des cles DynamoDB :
- Checkers : `pk={Project}#{Env}`, `sk=check:{category}:{type}:current`
- Comparisons : `pk={Project}#comparison:{SourceEnv}:{DestinationEnv}`, `sk=check:{category}:{type}:current`

Mais les StateKeys attendus par les compare utilisent le format `project#env|check_type` qui est different.

### 5. Documentation obsolete

La doc `step-functions-reference.md` indique que les K8s compare ne sont "pas deployes", mais le Terraform les deploie bien.

---

## Analyse de la Lambda compare-secrets-manager

La Lambda `compare-secrets-manager` est la plus mature. Elle implemente :

**Points positifs** :
- Mapping de chemins Legacy -> NH
- Gestion des transformations attendues
- Categorisation fine : synced, differs_expected, differs_unexpected, only_source, only_destination
- Securite : travaille avec des hashes, jamais les valeurs

**Probleme d'interface** :
La Lambda attend `instance` et `environment` mais la Step Function ne les passe pas.

---

## Probleme architectural majeur

### Absence de registre de configuration

Il n'existe pas de **registre centralise** qui associe un `Project + Env` aux parametres necessaires :

```
mro-mi2#nh-stg -> {
  ClusterName: "k8s-dig-stg-webshop",
  Namespace: "rubix-mro-mi2-staging",
  CrossAccountRoleArn: "arn:aws:iam::281127105461:role/...",
  Instance: "MI2",
  Environment: "stg",
  ...
}
```

Sans ce registre, l'orchestrateur ne peut pas fonctionner de maniere autonome.

---

## Resume

| Aspect | Etat | Criticite |
|--------|------|-----------|
| Deploiement Terraform | OK | - |
| Lambdas Compare | Fonctionnelles | - |
| Step Functions Compare individuelles | Testables manuellement | Medium |
| Orchestrator | **Non fonctionnel** | **Critique** |
| Registre de configuration | **Absent** | **Critique** |
| Documentation | Obsolete | Medium |

**Conclusion** : La feature de comparaison a les briques individuelles (Lambdas, Step Functions) mais l'orchestration est cassee faute de registre de configuration et de passage de parametres.
