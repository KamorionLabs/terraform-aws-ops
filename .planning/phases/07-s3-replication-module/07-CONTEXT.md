# Phase 7: S3 Replication Module - Context

**Gathered:** 2026-06-17
**Status:** Ready for planning

<domain>
## Phase Boundary

Le module Terraform `modules/step-functions/s3/` déploie 4 SFN (`setup_cross_account_replication`, `run_batch_replication`, `check_batch_replication`, `delete_replication`) opérant la réplication S3 cross-account en assume-role **impératif** (`Credentials.RoleArn.$`), et `modules/source-account/` expose un rôle de réplication S3 **optionnel** + perms source. `terraform plan` doit passer.

Périmètre **générique uniquement** : grants côté destination (bucket/KMS policy) et wiring client = hors scope. Le branchement orchestrateur = Phase 8. La spec + tests = Phase 9.

**Changement de périmètre acté pendant la discussion :** le module `s3/` ne contient **AUCUN Lambda**. Le "compare sync-status" est entièrement assuré par des états SDK natifs ASL — voir D-09.

</domain>

<decisions>
## Implementation Decisions

### Fan-out (1 source → N destinations)
- **D-01:** S3 n'a qu'**une** `ReplicationConfiguration` par bucket source (N `Rules`, une par destination) — contrairement à EFS (une config par paire). `setup` utilise donc un **Map state (`MaxConcurrency: 1`)** itérant les destinations : `GetBucketReplication` → merge/replace la Rule de ce destinataire → `PutBucketReplication`. Permet d'ajouter/gérer un spoke indépendamment sans toucher aux autres rules existantes. Idempotent, dans l'esprit du check-existant EFS.
- **D-02:** Le keying d'une `Rule` à sa destination (convention d'ID de Rule pour le merge) est laissé en discrétion au plan.

### Versioning du bucket source
- **D-03:** **Validate-only.** `GetBucketVersioning` ; si != `Enabled` → `Fail` state explicite. On ne mute **jamais** le bucket source (owned par un stack externe — raison d'être de l'assume-role impératif). Le versioning est une pré-condition que le stack client doit satisfaire.

### Teardown
- **D-04:** `delete_replication` est **symétrique** au setup : reçoit `Destinations[]`, `GetBucketReplication` → retire les Rules de ces destinations → `PutBucketReplication` (ou `DeleteBucketReplication` s'il ne reste plus aucune rule). Permet de teardown un spoke sans casser les autres.

### Options de réplication
- **D-05:** Les options S3 (RTC / Replication Time Control, Metrics, StorageClass, DeleteMarkerReplication) sont exposées en **champs d'input optionnels** par destination, avec defaults sûrs. Surface d'input plus large mais flexible.

### Backfill (objets existants)
- **D-06:** `run_batch_replication` utilise **S3 Batch Replication** : `s3control:CreateJob` avec `Operation = S3ReplicateObject`, qui **réutilise la `ReplicationConfiguration`** déjà posée par `setup` → backfille vers toutes les destinations configurées en **1 seul job**. Mode AWS natif pour "répliquer l'existant".
- **D-07:** Manifest = **`GeneratedManifest`** (S3 génère le manifest à la création du job, filtre d'éligibilité sur les objets non-répliqués). Aucune pré-condition S3 Inventory côté client.
- **D-08:** `check_batch_replication` poll `s3control:DescribeJob` (SDK natif) jusqu'à completion (Active/Complete/Failed).

### Compare sync-status — ANNULÉ (pas de Lambda)
- **D-09:** **Aucun Lambda** dans ce module. Le sync-status est lu via états SDK natifs uniquement : `GetBucketReplication` (rules `Enabled`) + `s3control:DescribeJob` (statut backfill). Le compare objet-par-objet (inventaire/comptage) demandait du compute mais est hors scope v1.2 ; un Lambda n'aurait apporté que de la symétrie cosmétique avec `process-efs-replication`, ce qui va contre la décision verrouillée "privilégier les intégrations SDK natives". **Annulé, pas déféré.**

### IAM source-account
- **D-10:** Rôle de réplication S3 **optionnel** (garde par variable type `enable_s3`, analogue `enable_efs`), **combiné** : assumable par `s3.amazonaws.com` (live replication) **et** `batchoperations.s3.amazonaws.com` (S3 Batch Replication). Perms sur le rôle source assumé par l'orchestrateur : `s3:PutBucketReplication`, `s3:GetBucketReplication`, `s3:GetBucketVersioning`, `s3control:CreateJob`, `s3control:DescribeJob`, `iam:PassRole` vers le rôle de réplication S3 (condition `iam:PassedToService`). (Locked par IAM-01/IAM-02 ; liste exacte affinée au plan.)

### Claude's Discretion
- **D-11:** Contrat d'input exact des 4 SFN (mirror EFS `Source`/`Destination`/`Replication` : `SourceBucket` + `Destinations[]` avec `AccountId`/`RoleArn`/`Bucket`/`Region`/options-réplication + `ReplicationRoleArn`) — dérivé au plan en s'alignant sur le pattern EFS.
- Convention d'ID de Rule pour le merge (D-02).
- Paramètres de polling de `check_batch_replication` (intervalle, timeout, backoff).
- Structure Terraform du module (plus simple que `sync/` puisqu'il n'y a pas de Lambda à packager) — suit le pattern des modules `step-functions/` existants.

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Pattern de référence EFS (modèle direct)
- `modules/step-functions/efs/setup_cross_account_replication.asl.json` — pattern assume-role impératif (`Credentials.RoleArn.$`), check de config existante, gardes Choice, structure des policies de réplication
- `modules/step-functions/efs/delete_replication.asl.json` — pattern de teardown à mirror pour `delete_replication`
- `modules/step-functions/efs/main.tf` / `variables.tf` / `outputs.tf` — structure Terraform d'un module step-functions par domaine

### Structure module Terraform (analog récent)
- `modules/step-functions/sync/` — analog le plus récent (v1.1) pour la structure d'un nouveau module step-functions. **NB :** `sync/` package un Lambda inline (`archive_file`) ; le module `s3/` n'en a pas (D-09) → structure plus légère.

### IAM source-account
- `modules/source-account/main.tf` — rôle `efs_replication` (gardé par `var.enable_efs`), pattern `PassRoleToEFSReplication` (condition `iam:PassedToService`), policy `efs_access` à mirror pour S3

### Spec (cible Phase 9, à connaître dès Phase 7 pour cohérence)
- `specs/repl-efs-sync.md` — structure miroir pour `specs/repl-s3-sync.md` (Phase 9)

### Requirements / Roadmap
- `.planning/REQUIREMENTS.md` — REPL-01..06, IAM-01/02 (Phase 7)
- `.planning/ROADMAP.md` §Phase 7 — Goal + Success Criteria
- `.planning/PROJECT.md` — décisions v1.2 verrouillées + topologie Rubix (wiring ultérieur, hors scope)

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `efs/setup_cross_account_replication.asl.json` : pattern `Credentials.RoleArn.$` + Choice gardes + `Catch` → `Fail` states nommés. Réutiliser la mécanique d'assume-role impératif et de gestion d'erreur.
- `efs/delete_replication.asl.json` : modèle de teardown.
- `source-account/main.tf` : bloc `aws_iam_role.efs_replication` + `aws_iam_role_policy.efs_access` (Sid `PassRoleToEFSReplication`) — gabarit direct pour le rôle/perms S3.

### Established Patterns
- Module step-functions par domaine : `main.tf` (templatefile/file des ASL + `aws_sfn_state_machine`), `variables.tf`, `outputs.tf` (ARN exportés), `versions.tf`.
- Garde optionnelle côté IAM : `count = ... && var.enable_efs ? 1 : 0` → reproduire avec `var.enable_s3`.
- Intégrations SDK natives ASL : `arn:aws:states:::aws-sdk:s3:*` et `arn:aws:states:::aws-sdk:s3control:*` (analogue `aws-sdk:efs:*`).
- Auto-découverte des tests ASL via rglob (pas de wiring manuel des nouveaux fichiers).

### Integration Points
- `modules/step-functions/s3/` (NOUVEAU) : 4 fichiers `*.asl.json` + `main.tf`/`variables.tf`/`outputs.tf`/`versions.tf`.
- `modules/source-account/` : ajouter rôle `s3_replication` + policy `s3_access` gardés par `var.enable_s3` (+ la variable dans `variables.tf`).
- Le branchement dans `refresh_orchestrator` et le root `main.tf` = **Phase 8** (hors scope ici).

</code_context>

<specifics>
## Specific Ideas

- S3 = **une seule** `ReplicationConfiguration` par bucket source (N Rules) — c'est LA différence structurante vs EFS qui pilote le choix Map+merge (D-01).
- Bucket source **owned par un stack externe** → assume-role impératif + jamais de mutation (validate-only versioning, D-03).
- S3 Batch Replication (`S3ReplicateObject`) **réutilise la config de réplication** existante : `setup` doit tourner avant `run_batch` (dépendance d'ordre dans l'orchestrateur Phase 8).

</specifics>

<deferred>
## Deferred Ideas

- **Compare objet-par-objet source/destination** (inventaire S3, comptage objets/tailles) — reste un besoin v2 distinct (S3REPL-DR-02), mais **PAS** sous forme du Lambda annulé en D-09 ; à reconcevoir sur ses propres termes (S3 Inventory natif probable) si le besoin émerge.
- Monitoring/alerting de l'état de réplication (lag, objets en échec) côté Dashborion — v2 (S3REPL-DR-01).
- Cross-region replication via proxy Lambda — v2 (S3REPL-DR-03).

</deferred>

---

## ⚠ Requirement deltas to propagate (flagged for planner + PROJECT/REQUIREMENTS update)

La décision D-09 (annulation du Lambda compare) modifie trois éléments verrouillés :

| Élément | Avant | Après (acté en discussion) |
|---|---|---|
| **REPL-06** | "Lambda uniquement pour le compare sync-status" | "SDK natif partout ; **pas de Lambda** en v1.2" |
| **Critère succès #4 (Phase 7)** | "...un Lambda générique assure le compare sync-status" | Retirer la clause Lambda ; ne garder que le fan-out hub-and-spoke |
| **INFRA-04 (Phase 9)** | "validation ASL + **tests unitaires Lambda** compare" | Retirer les tests Lambda ; ne garder que la validation ASL |

---

*Phase: 07-s3-replication-module*
*Context gathered: 2026-06-17*
