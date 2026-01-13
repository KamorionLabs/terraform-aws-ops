# Plan de Refactoring - CloudFront & DNS Checkers

**Date**: 2026-01-12
**Objectif**: Modulariser et rendre réutilisable l'architecture des checkers réseau
**Statut**: ✅ Implémentation terminée

---

## Statut d'implémentation

| Phase | Description | Statut |
|-------|-------------|--------|
| Phase 1 | Lambda partagée `fetch-ado-file` | ✅ Terminé |
| Phase 2 | CloudFront refactoring | ✅ Terminé |
| Phase 3 | DNS refactoring | ✅ Terminé |
| Phase 4 | Cleanup et déploiement | ✅ Terminé (2026-01-12) |

### Tests validés (2026-01-12)

| Step Function | Mode | Résultat |
|---------------|------|----------|
| `net-dns-checker` | TestDomains | ✅ 3 domaines résolus |
| `net-dns-checker` | TfvarsSource | ✅ 131 domaines, 131 Route53 OK |
| `net-cloudfront-checker` | TfvarsSource | ✅ 24 distributions, WAF OK |

### Fichiers créés/modifiés

| Fichier | Action | Status |
|---------|--------|--------|
| `lambdas/fetch-ado-file/fetch_ado_file.py` | Créé | ✅ |
| `lambdas/fetch-cloudfront/fetch_cloudfront.py` | Simplifié + payload réduit | ✅ |
| `lambdas/process-cloudfront/process_cloudfront.py` | Refactoré | ✅ |
| `lambdas/resolve-dns/resolve_dns.py` | Créé | ✅ |
| `lambdas/prepare-dns-domains/prepare_dns_domains.py` | Créé | ✅ |
| `lambdas/process-dns/process_dns.py` | Créé | ✅ |
| `step-functions/net-cloudfront-checker.asl.json` | Mis à jour | ✅ |
| `step-functions/net-dns-checker.asl.json` | Mis à jour | ✅ |
| `terraform/lambdas.tf` | 4 nouvelles Lambdas | ✅ |
| `terraform/variables.tf` | Variable ado_default_project | ✅ |
| `terraform/step-functions.tf` | ARNs réels | ✅ |

---

## Problèmes actuels

### Duplication de code

| Fonction | fetch-tfvars | dns-checker | fetch-cloudfront |
|----------|--------------|-------------|------------------|
| AzureDevOpsClient | Oui | Oui (dupliqué) | Non |
| parse_hcl_map | Oui | Oui (dupliqué) | Non |
| parse_hcl_attributes | Oui | Oui (dupliqué) | Non |
| filter_domains_by_country | Oui | Oui (dupliqué) | Non |
| get_ado_pat | Oui | Oui (dupliqué) | Non |

### Lambdas monolithiques

- **dns-checker**: 855 lignes, fait TOUT (ADO, parsing, résolution, Route53, status)
- **fetch-cloudfront**: Récupère + transforme (format non-standard)
- **process-cloudfront**: Re-transforme, formats incompatibles

### Couplage fort avec Rubix

- Organisation ADO hardcodée
- Patterns tfvars spécifiques
- Difficile à réutiliser pour d'autres clients

---

## Architecture cible

### Vue d'ensemble

```
┌─────────────────────────────────────────────────────────────┐
│  fetch-ado-file (Lambda GÉNÉRIQUE - PARTAGÉE)               │
│  ├── Récupère n'importe quel fichier depuis ADO             │
│  ├── Parsing optionnel: HCL, JSON, YAML                     │
│  ├── Extraction de block HCL (managed_domains, etc.)        │
│  └── Configurable: organization, project, repo, branch      │
└─────────────────────────────────────────────────────────────┘

                    │
       ┌────────────┴────────────┐
       ▼                         ▼
┌──────────────────┐    ┌──────────────────┐
│ CloudFront       │    │ DNS              │
│ Checker          │    │ Checker          │
└──────────────────┘    └──────────────────┘
```

---

## CloudFront Checker

### Step Function: net-cloudfront-checker

```
┌─────────────────────────────────────────────────────────────┐
│  ValidateInput                                              │
│  └── Vérifie Project, Env, (TfvarsSource OU DiscoveryMode) │
└─────────────────────────────────────────────────────────────┘
                    │
                    ▼
┌─────────────────────────────────────────────────────────────┐
│  [Optionnel] FetchAdoFile                                   │
│  └── Si TfvarsSource fourni                                 │
│      Input: repository, path, branch, blockName             │
│      Output: managed_domains parsé                          │
└─────────────────────────────────────────────────────────────┘
                    │
                    ▼
┌─────────────────────────────────────────────────────────────┐
│  FetchCloudFront (Lambda)                                   │
│  └── Récupère distributions AWS                             │
│      Input: CrossAccountRoleArn                             │
│      Output: format AWS NATIF                               │
│        - distributions[] avec Id, ARN, Aliases, Status...   │
│        - tags par distribution                              │
│      NE FAIT PAS: transformation de format                  │
└─────────────────────────────────────────────────────────────┘
                    │
                    ▼
┌─────────────────────────────────────────────────────────────┐
│  ProcessCloudFront (Lambda)                                 │
│  └── TOUTES les transformations                             │
│      Input:                                                 │
│        - Distributions (AWS natif)                          │
│        - EnrichmentData (optionnel, depuis fetch-ado-file)  │
│        - Filters: ProjectTag, Country, Environment          │
│      Output: payload DynamoDB                               │
│        - Filtrage par tag Project                           │
│        - Matching avec tfvars (si dispo)                    │
│        - Parsing aliases (country, type, is_nh)             │
│        - Calcul status, issues, summary                     │
└─────────────────────────────────────────────────────────────┘
                    │
                    ▼
┌─────────────────────────────────────────────────────────────┐
│  SaveState                                                  │
└─────────────────────────────────────────────────────────────┘
```

### Lambdas CloudFront

| Lambda | Responsabilité |
|--------|----------------|
| `fetch-cloudfront` | ListDistributions + ListTagsForResource, format AWS natif |
| `process-cloudfront` | Filtrage, transformation, enrichissement, status |

---

## DNS Checker

### Step Function: net-dns-checker

```
┌─────────────────────────────────────────────────────────────┐
│  ValidateInput                                              │
│  └── Vérifie Project, Env, (TfvarsSource OU TestDomains)   │
└─────────────────────────────────────────────────────────────┘
                    │
                    ▼
┌─────────────────────────────────────────────────────────────┐
│  [Optionnel] FetchAdoFile                                   │
│  └── Si TfvarsSource fourni                                 │
│      Input: paths cloudfront + eks                          │
│      Output: managed_domains + managed_api_domains          │
└─────────────────────────────────────────────────────────────┘
                    │
                    ▼
┌─────────────────────────────────────────────────────────────┐
│  PrepareDomainList (Pass state)                             │
│  └── Construit liste de domaines à résoudre                 │
│      - Depuis tfvars parsé                                  │
│      - Ou depuis TestDomains direct                         │
└─────────────────────────────────────────────────────────────┘
                    │
                    ▼
┌─────────────────────────────────────────────────────────────┐
│  ResolveDns (Lambda)                                        │
│  └── Résolution DNS uniquement                              │
│      Input: [{hostname, key, metadata}]                     │
│      Output: [{hostname, resolved, ips, responseTimeMs}]    │
│      NE FAIT PAS: ADO, parsing HCL, Route53, status         │
└─────────────────────────────────────────────────────────────┘
                    │
                    ▼
┌─────────────────────────────────────────────────────────────┐
│  [Optionnel] FetchRoute53Records                            │
│  └── Si HostedZoneId fourni                                 │
│      Peut être: aws-sdk direct OU Lambda                    │
│      Input: hostnames + HostedZoneId + CrossAccountRoleArn  │
│      Output: records Route53 par hostname                   │
└─────────────────────────────────────────────────────────────┘
                    │
                    ▼
┌─────────────────────────────────────────────────────────────┐
│  ProcessDns (Lambda)                                        │
│  └── Agrégation et calcul status                            │
│      Input:                                                 │
│        - Résolutions DNS                                    │
│        - Records Route53 (optionnel)                        │
│        - Config originale (country, status, etc.)           │
│      Output: payload DynamoDB                               │
│        - Validation résolution vs Route53                   │
│        - Calcul issues, summary, status                     │
└─────────────────────────────────────────────────────────────┘
                    │
                    ▼
┌─────────────────────────────────────────────────────────────┐
│  SaveState                                                  │
└─────────────────────────────────────────────────────────────┘
```

### Lambdas DNS

| Lambda | Responsabilité |
|--------|----------------|
| `prepare-dns-domains` | Transformation tfvars → liste domaines, filtrage, construction hostnames API |
| `resolve-dns` | Résolution DNS pure (socket), mesure temps de réponse |
| `process-dns` | Validation Route53, calcul status, formatage payload |

---

## Lambda partagée: fetch-ado-file

### Interface

```python
# Input
{
    "Organization": "rubix-group",      # optionnel, default depuis env
    "Project": "NewHorizon-IaC",        # optionnel, default depuis env
    "Repository": "NewHorizon-IaC-Webshop",  # requis
    "Path": "stacks/cloudfront/env/ppd/terraform.tfvars",  # requis
    "Branch": "master",                 # optionnel, default: master
    "Parse": "hcl",                     # optionnel: hcl, json, yaml, none
    "BlockName": "managed_domains"      # optionnel, pour extraction HCL
}

# Output
{
    "statusCode": 200,
    "content": "...",                   # contenu brut si Parse=none
    "parsed": {...},                   # contenu parsé si Parse!=none
    "block": {...},                    # block extrait si BlockName fourni
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

### Fonctionnalités

1. **Récupération fichier** - Via Azure DevOps Git API
2. **Parsing HCL** - terraform.tfvars, extraction de blocks
3. **Parsing JSON** - fichiers JSON standard
4. **Parsing YAML** - fichiers YAML/YML
5. **Mode brut** - Retourne le contenu sans parsing

### Configuration

| Variable d'environnement | Description | Default |
|--------------------------|-------------|---------|
| `ADO_PAT_SECRET_NAME` | Secret Manager pour PAT | `ops-dashboard/ado-pat` |
| `ADO_ORGANIZATION` | Organisation Azure DevOps | `rubix-group` |
| `ADO_DEFAULT_PROJECT` | Projet par défaut | `NewHorizon-IaC` |

---

## Actions à réaliser

### Phase 1: Lambda partagée

| # | Action | Fichier | Status |
|---|--------|---------|--------|
| 1 | Créer `fetch-ado-file` | `lambdas/fetch-ado-file/fetch_ado_file.py` | ✅ |
| 2 | Tests unitaires | `lambdas/fetch-ado-file/test_fetch_ado_file.py` | ⏳ |
| 3 | Terraform Lambda | `terraform/lambdas.tf` | ⏳ |

### Phase 2: CloudFront

| # | Action | Fichier | Status |
|---|--------|---------|--------|
| 4 | Simplifier `fetch-cloudfront` | `lambdas/fetch-cloudfront/fetch_cloudfront.py` | ✅ |
| 5 | Refactorer `process-cloudfront` | `lambdas/process-cloudfront/process_cloudfront.py` | ✅ |
| 6 | Mettre à jour Step Function | `step-functions/net-cloudfront-checker.asl.json` | ✅ |

### Phase 3: DNS

| # | Action | Fichier | Status |
|---|--------|---------|--------|
| 7 | Créer `prepare-dns-domains` | `lambdas/prepare-dns-domains/prepare_dns_domains.py` | ✅ |
| 8 | Créer `resolve-dns` | `lambdas/resolve-dns/resolve_dns.py` | ✅ |
| 9 | Créer `process-dns` | `lambdas/process-dns/process_dns.py` | ✅ |
| 10 | Mettre à jour Step Function | `step-functions/net-dns-checker.asl.json` | ✅ |
| 11 | Supprimer `dns-checker` | `lambdas/dns-checker/` (après migration) | ⏳ |

### Phase 4: Cleanup et déploiement

| # | Action | Fichier | Status |
|---|--------|---------|--------|
| 12 | Terraform 4 nouvelles Lambdas | `terraform/lambdas.tf` | ✅ |
| 13 | Variable ado_default_project | `terraform/variables.tf` | ✅ |
| 14 | ARNs réels dans Step Functions | `terraform/step-functions.tf` | ✅ |
| 15 | Déployer via tofu apply | - | ✅ |
| 16 | Tester net-dns-checker (TestDomains) | - | ✅ |
| 17 | Tester net-dns-checker (TfvarsSource) | - | ✅ |
| 18 | Tester net-cloudfront-checker | - | ✅ |
| 19 | Fix datetime serialization | `fetch_cloudfront.py` | ✅ |
| 20 | Fix payload size limit | `fetch_cloudfront.py` (extract_essential_fields) | ✅ |

### Optionnel (post-migration)

| # | Action | Fichier | Status |
|---|--------|---------|--------|
| - | Supprimer `fetch-tfvars` | `lambdas/fetch-tfvars/` (remplacé par fetch-ado-file) | ⏳ |
| - | Supprimer `dns-checker` | `lambdas/dns-checker/` (remplacé par architecture modulaire) | ⏳ |

---

## Réutilisabilité pour autres clients

### Configuration par client

```python
# Exemple: autre client que Rubix
{
    "Organization": "autre-client",
    "Project": "IaC",
    "Repository": "terraform-infra",
    "Path": "config/domains.json",
    "Parse": "json"
}
```

### Points d'extension

1. **fetch-ado-file**: Organisation et projet configurables
2. **process-cloudfront**: Enrichissement optionnel (pas obligatoire)
3. **process-dns**: Fonctionne avec ou sans tfvars

---

## Métriques attendues

| Métrique | Avant | Après |
|----------|-------|-------|
| Lignes dupliquées (ADO client) | ~150 x 2 | 0 |
| Lignes dupliquées (HCL parser) | ~100 x 2 | 0 |
| Lambdas monolithiques | 2 (dns-checker, fetch-cloudfront) | 0 |
| Réutilisabilité autres clients | Non | Oui |

---

## Risques et mitigations

| Risque | Mitigation |
|--------|------------|
| Régression fonctionnelle | Tests avant/après avec mêmes inputs |
| Performances (plus d'appels Lambda) | Lambdas légères, cold start minimal |
| Complexité Step Function | Documentation claire, logs détaillés |
