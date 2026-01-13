# EFS Replications Rubix - Documentation

> Derniere mise a jour : 2025-12-16

## Vue d'ensemble

Les replications EFS permettent de synchroniser les donnees entre les comptes **Legacy** (iph) et les nouveaux comptes **NewHorizon** (digital-webshop-*).

- **Technologie** : AWS EFS Replication (cross-account, cross-region)
- **Stockage destination** : Standard (pas de IA sur la destination par defaut)
- **RPO cible** : 15 minutes (apres sync initial)

---

## Inventaire des Replications

### MI2 Preprod

| Type | Source (iph) | Destination (ppd) | Region Source | Region Dest |
|------|--------------|-------------------|---------------|-------------|
| **Media** | `fs-0962a94e75305fb51` | `fs-0d613470978607736` | eu-central-1 | eu-central-1 |
| **Share** | `fs-01547ccf6fd91b3f0` | `fs-08d49e52b06f57b01` | eu-central-1 | eu-central-1 |

### MI2 Prod

| Type | Source (iph) | Destination (prd) | Region Source | Region Dest |
|------|--------------|-------------------|---------------|-------------|
| **Share** | `fs-00aca39ef1d1b5d9b` | `fs-0a1790b3f6feab75e` | eu-central-1 | eu-central-1 |

### FR Preprod

| Type | Source (iph) | Destination (ppd) | Region Source | Region Dest |
|------|--------------|-------------------|---------------|-------------|
| **Media** | `fs-07ffd36d037c214e1` | `fs-022f367d2f5c66d09` | eu-west-3 | eu-central-1 |
| **Share** | `fs-091c0fce6901a32ad` | `fs-0e40fb7d80ee74a11` | eu-west-3 | eu-central-1 |

### MI2 Staging

| Type | Source (iph) | Destination (stg) | Region Source | Region Dest |
|------|--------------|-------------------|---------------|-------------|
| **Media** | `fs-0ac799fd6e05359cc` | `fs-08b2ab5b21747221c` | eu-central-1 | eu-central-1 |
| **Share** | `fs-01eb055305bdebcec` | `fs-0f1b860ad9be1367c` | eu-central-1 | eu-central-1 |

### FR Prod (REPLICATION SUPPRIMEE)

| Type | Source (iph) | Destination (prd) | Region Source | Region Dest | Status |
|------|--------------|-------------------|---------------|-------------|--------|
| **Media** | `fs-02119f0fbca256090` | `fs-0bf18ac3a2c183c5a` | eu-west-3 | eu-central-1 | **A RECREER** |

> **Note**: La replication etait en etat ERROR irreversible et a ete supprimee. L'EFS destination existe mais est vide.

---

## Comptes AWS Impliques

| Environnement | Account ID | Profile AWS |
|---------------|------------|-------------|
| Legacy (source) | 073290922796 | `iph` |
| Staging NH | 281127105461 | `digital-webshop-staging/AWSAdministratorAccess` |
| Preprod NH | 287223952330 | `digital-webshop-preprod/AWSAdministratorAccess` |
| Prod NH | 366483377530 | `digital-webshop-prod/AWSAdministratorAccess` |

---

## Etat des Replications (2025-12-16 18:41)

### Progression du Sync Initial

| Env | EFS | Taille Source | Taille Dest | Progression | Status |
|-----|-----|---------------|-------------|-------------|--------|
| MI2 PPD | media | 525.38 GiB | 56.56 GiB | **10.7%** | ENABLED |
| MI2 PPD | share | 127.96 GiB | 114.81 GiB | **89.7%** | ENABLED |
| MI2 PRD | share | 563.39 GiB | 192.71 GiB | **34.2%** | ENABLED |
| MI2 STG | media | 422.62 GiB | 48.75 GiB | **11.5%** | ENABLED |
| MI2 STG | share | 787.30 GiB | 356.53 GiB | **45.2%** | ENABLED |
| FR PPD | media | 453.26 GiB | 59.82 GiB | **13.1%** | ENABLED |
| FR PPD | share | 51.91 GiB | 49.07 GiB | **94.5%** | ENABLED |
| FR PRD | media | 959.58 GiB | 0 GiB | **0%** | A RECREER |

### Etats de Replication Possibles

| Etat | Description |
|------|-------------|
| `ENABLED` | Replication active et fonctionnelle |
| `ENABLING` | Configuration en cours |
| `PAUSED` | Replication en pause (probleme d'autorisation, compte, KMS) |
| `ERROR` | Etat irrecuperable - doit etre supprimee et recreee |
| `DELETING` | Suppression en cours |

---

## Monitoring

### Metriques CloudWatch Disponibles

| Metrique | Description | Disponibilite |
|----------|-------------|---------------|
| `TimeSinceLastSync` | Temps depuis le dernier sync reussi | **Uniquement apres sync initial** |
| `StorageBytes` | Taille du filesystem | Toujours disponible |

**Important** : Il n'existe **pas de metrique** pour suivre la progression du sync initial. La seule methode est de comparer les tailles source/destination.

### Commandes de Verification

#### Status des replications (compte source)

```bash
# EU-CENTRAL-1 (MI2)
AWS_PROFILE="iph" aws efs describe-replication-configurations \
  --region eu-central-1 \
  --query 'Replications[*].{Source:SourceFileSystemId,Dest:Destinations[0].FileSystemId,Status:Destinations[0].Status,LastSync:Destinations[0].LastReplicatedTimestamp}' \
  --output table

# EU-WEST-3 (FR)
AWS_PROFILE="iph" aws efs describe-replication-configurations \
  --region eu-west-3 \
  --query 'Replications[*].{Source:SourceFileSystemId,Dest:Destinations[0].FileSystemId,Status:Destinations[0].Status,LastSync:Destinations[0].LastReplicatedTimestamp}' \
  --output table
```

#### Taille d'un EFS

```bash
# Source (legacy)
AWS_PROFILE="iph" aws efs describe-file-systems \
  --file-system-id <fs-id> \
  --region <region> \
  --query 'FileSystems[0].SizeInBytes' \
  --output json

# Destination (NH)
AWS_PROFILE="digital-webshop-preprod/AWSAdministratorAccess" aws efs describe-file-systems \
  --file-system-id <fs-id> \
  --region eu-central-1 \
  --query 'FileSystems[0].SizeInBytes' \
  --output json
```

#### Metrique TimeSinceLastSync (apres sync initial)

```bash
AWS_PROFILE="iph" aws cloudwatch get-metric-statistics \
  --namespace AWS/EFS \
  --metric-name TimeSinceLastSync \
  --dimensions Name=FileSystemId,Value=<source-fs-id> Name=DestinationFileSystemId,Value=<dest-fs-id> \
  --start-time "$(date -u -v-60M +%Y-%m-%dT%H:%M:%SZ)" \
  --end-time "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
  --period 300 \
  --statistics Average \
  --region <source-region> \
  --output table
```

---

## Calcul des Tailles EFS

AWS EFS utilise 3 classes de stockage :
- **Standard** : Acces frequent
- **Infrequent Access (IA)** : Acces peu frequent (fichiers < 128 KiB arrondis a 128 KiB pour la facturation)
- **Archive** : Archivage long terme

**Formule** :
```
Total = ValueInStandard + ValueInIA + ValueInArchive
```

La taille "Total" (Value) est bien la taille reelle sans arrondi.

---

## Problemes Connus

### FR PROD - Replication Supprimee

La replication FR PROD media etait en etat `ERROR` et a ete supprimee :
- Source: `fs-02119f0fbca256090` (eu-west-3) - 960 GiB
- Destination: `fs-0bf18ac3a2c183c5a` (eu-central-1) - EFS existe mais vide

**Action requise** : Recreer la configuration de replication.

### Sync Initial Long pour Media EFS

Les EFS media contiennent beaucoup de petits fichiers (images produits), ce qui ralentit significativement le sync initial. Les EFS share (plus gros fichiers, moins nombreux) se synchronisent plus rapidement.

---

## Historique

| Date | Action |
|------|--------|
| 2025-12-16 | Redemarrage des replications MI2 STG, MI2 PPD, FR PPD |
| 2025-12-16 | Detection FR PROD en ERROR |
| 2025-12-16 | Suppression replication FR PROD (etait en ERROR) |
| 2025-12-16 | Correction mapping EFS dans script monitoring |
| 2025-12-16 | Ajout MI2 PRD share a la liste des replications |

---

## References

- [AWS EFS Replication Documentation](https://docs.aws.amazon.com/efs/latest/ug/efs-replication.html)
- [Monitoring Replication Status](https://docs.aws.amazon.com/efs/latest/ug/monitoring-replication-status.html)
- [CloudWatch Metrics for EFS](https://docs.aws.amazon.com/efs/latest/ug/efs-metrics.html)
