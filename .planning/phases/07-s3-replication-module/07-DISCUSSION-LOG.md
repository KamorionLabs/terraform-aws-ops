# Phase 7: S3 Replication Module - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in CONTEXT.md — this log preserves the alternatives considered.

**Date:** 2026-06-17
**Phase:** 07-s3-replication-module
**Areas discussed:** Modélisation fan-out, Idempotence / config existante, Backfill batch (manifest), Scope du Lambda compare

---

## Modélisation fan-out

| Option | Description | Selected |
|--------|-------------|----------|
| Full config (N rules en 1 PUT) | Setup reçoit Destinations[] complet, une ReplicationConfiguration N Rules, un seul PutBucketReplication. Atomique, source de vérité = input. | |
| Map + GET/merge/PUT par dest | Map (MaxConcurrency 1) itérant chaque dest : GetBucketReplication → merge la Rule → PutBucketReplication. Gestion indépendante des spokes, esprit EFS. | ✓ |
| Tu décides | — | |

**User's choice:** Map + GET/merge/PUT par destination
**Notes:** Keying d'une Rule à sa destination laissé en discrétion au plan.

---

## Idempotence / config existante

### Versioning

| Option | Description | Selected |
|--------|-------------|----------|
| Validate-only (fail si off) | GetBucketVersioning ; si != Enabled → Fail. Jamais de mutation du bucket source (owned externe). | ✓ |
| Enable si absent | PutBucketVersioning Enabled si off. Mute un bucket owned par un autre stack. | |
| Tu décides | — | |

**User's choice:** Validate-only (fail si off)

### Delete

| Option | Description | Selected |
|--------|-------------|----------|
| Symétrique (retire 1 spoke, ou tout) | GET → retire les Rules des dests ciblées → PUT (ou DeleteBucketReplication si vide). Miroir du setup. | ✓ |
| Full teardown only | DeleteBucketReplication, supprime toute la config d'un coup. | |
| Tu décides | — | |

**User's choice:** Symétrique

### Options de réplication

| Option | Description | Selected |
|--------|-------------|----------|
| Minimal (defaults) | Rule basique, pas de RTC ni metrics. | |
| Configurable via input | RTC / Metrics / StorageClass / DeleteMarkerReplication en input optionnel, defaults sûrs. | ✓ |
| Tu décides | — | |

**User's choice:** Configurable via input

---

## Backfill batch (manifest)

### Batch op

| Option | Description | Selected |
|--------|-------------|----------|
| S3 Batch Replication (S3ReplicateObject) | CreateJob réutilise la ReplicationConfiguration → backfill vers toutes les dests, 1 job, GeneratedManifest. | ✓ |
| S3PutObjectCopy par destination | 1 job par dest, copie manuelle, n'utilise pas la config de réplication. | |
| Tu décides | — | |

**User's choice:** S3 Batch Replication (S3ReplicateObject)

### Manifest

| Option | Description | Selected |
|--------|-------------|----------|
| GeneratedManifest | S3 génère le manifest (filtre objets non-répliqués). Aucune pré-condition. | ✓ |
| S3 Inventory report requis | Le client fournit un rapport S3 Inventory. Pré-condition lourde. | |
| Tu décides | — | |

**User's choice:** GeneratedManifest

---

## Scope du Lambda compare

| Option | Description | Selected |
|--------|-------------|----------|
| Métriques CloudWatch pending | Agrège OperationsPendingReplication/BytesPending par dest. | |
| Parse du rapport batch job | Lit le completion report du job S3 Batch. | |
| Statut config + job (léger) | Agrège GetBucketReplication + DescribeJob. | |
| (Free text) | "en vrai je pense qu'elle n'est pas nécessaire ici" → puis "on annule cette lambda tout court" | ✓ |

**User's choice:** Lambda **annulée** entièrement (pas déférée).
**Notes:** Le backfill est suivi par DescribeJob (SDK natif), le statut config par GetBucketReplication (SDK natif), et le compare objet est hors scope v1.2. Un Lambda n'apportait que de la symétrie cosmétique avec process-efs-replication, contre la décision verrouillée "privilégier les intégrations SDK natives". Impacte REPL-06, critère de succès #4 (Phase 7), INFRA-04 (Phase 9) — deltas flaggés dans CONTEXT.md.

---

## Contrat d'input des SFN (zone grise supplémentaire)

**User's choice:** Dérive au plan (discrétion) — schéma mirror EFS Source/Destination/Replication aligné sur le pattern existant.

## Claude's Discretion

- Contrat d'input exact des 4 SFN (mirror EFS).
- Convention d'ID de Rule pour le merge.
- Paramètres de polling de check_batch_replication.
- Structure Terraform du module (sans Lambda).

## Deferred Ideas

- Compare objet-par-objet source/dest (S3 Inventory) — besoin v2 distinct (S3REPL-DR-02), à reconcevoir hors du Lambda annulé.
- Monitoring/alerting réplication côté Dashborion — v2 (S3REPL-DR-01).
- Cross-region replication via proxy Lambda — v2 (S3REPL-DR-03).
