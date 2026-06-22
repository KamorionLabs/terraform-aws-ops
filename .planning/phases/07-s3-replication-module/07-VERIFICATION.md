---
phase: 07-s3-replication-module
verified: 2026-06-19T12:00:00Z
status: human_needed
score: 10/10 must-haves verified
overrides_applied: 0
human_verification:
  - test: "Valider la sémantique JSONata du merge $reduce dans MergeAllRules"
    expected: "L'expression JSONata produit correctement un tableau de Rules fusionnées avec priorités distinctes et stables, la Rule ciblée remplacée et toutes les autres Rules préservées"
    why_human: "Les tests pytest valident la structure ASL (JSON valide, StartAt, States, Credentials, etc.) mais pas l'évaluation JSONata runtime. Le $reduce / $filter / $exists imbriqués dans MergeAllRules ne peuvent être vérifiés que par exécution avec stepfunctions-local (Phase 9) ou un test d'intégration réel."
  - test: "Valider la sémantique JSONata du FilterRules dans delete_replication"
    expected: "Le $filter sur les IDs à supprimer retire exactement les Rules ciblées et préserve les autres ; remainingEmpty = true lorsque toutes les Rules sont supprimées"
    why_human: "Même raison que ci-dessus : expressions JSONata non exécutées par pytest (structurel uniquement)."
  - test: "Confirmer tofu validate / tofu fmt -check sur les deux modules"
    expected: "tofu validate et tofu fmt -check passent sans erreur pour modules/step-functions/s3/ et modules/source-account/"
    why_human: "La vérification_context indique que ces commandes ont passé, mais l'exécuteur (07-04-SUMMARY) note que le sandbox a refusé tofu. Confirmé par l'orchestrateur selon le contexte de vérification, mais une exécution directe serait une preuve formelle supplémentaire."
---

# Phase 7: S3 Replication Module — Rapport de Vérification

**Phase Goal:** Le module Terraform `modules/step-functions/s3/` déploie 4 SFN (setup/run_batch/check_batch/delete) opérant la réplication S3 cross-account avec assume-role impératif (Credentials.RoleArn par Task), et `modules/source-account/` expose un rôle de réplication S3 optionnel (var.enable_s3, default false) + perms source (PutBucketReplication, s3control CreateJob/DescribeJob, iam:PassRole scopé) ; `terraform validate` passe. Contrainte dure D-09 : AUCUN Lambda (intégrations SDK natives uniquement).
**Verified:** 2026-06-19T12:00:00Z
**Status:** human_needed
**Re-verification:** Non — vérification initiale

---

## Goal Achievement

### Observable Truths

| # | Truth | Status | Evidence |
|---|-------|--------|----------|
| 1 | setup ASL gate versioning (validate-only) avant tout write, Fail explicite si non Enabled | ✓ VERIFIED | `GetSourceVersioning` Task (getBucketVersioning), Choice sur `$.Versioning.Status` StringEquals "Enabled", Fail `SourceVersioningNotEnabled`, jamais `putBucketVersioning` |
| 2 | setup ASL lit la config existante, fusionne toute la liste Destinations[] en un seul pass, écrit l'intégralité des Rules en un seul put (single atomic read-merge-write) | ✓ VERIFIED | `ReadExistingConfig` (getBucketReplication) → `MergeAllRules` (JSONata Pass avec $reduce sur Destinations[]) → `WriteConfig` (putBucketReplication unique). Pas de Map, pas de per-destination write-back. |
| 3 | setup ASL supporte le fan-out hub-and-spoke (N destinations) via le $reduce sur Destinations[] | ✓ VERIFIED | `MergeAllRules` itère `$states.input.Destinations` via `$reduce` ; toutes les destinations sont traitées en mémoire avant un seul `PutBucketReplication` |
| 4 | delete ASL retire uniquement les Rules des destinations ciblées, repasse DeleteBucketReplication si aucune rule ne reste | ✓ VERIFIED | `FilterRules` (JSONata: $filter exclus les IDs ciblés), `CheckRemainingEmpty` Choice → `DeleteAllReplication` (deleteBucketReplication) ou `PutRemainingConfig` (putBucketReplication) |
| 5 | Chaque Task cross-account assume le rôle source via Credentials.RoleArn.$ | ✓ VERIFIED | Toutes les Tasks SDK (getBucketVersioning, getBucketReplication, putBucketReplication, deleteBucketReplication, createJob, describeJob) portent `"Credentials": {"RoleArn.$": "$.SourceAccount.RoleArn"}` ou `"RoleArn": "{% $sourceRoleArn %}"` (JSONata équivalent) |
| 6 | run_batch ASL crée un job S3 Batch (S3ReplicateObject) réutilisant la config live, avec GeneratedManifest, ClientRequestToken frais et ConfirmationRequired:false | ✓ VERIFIED | `CreateBatchJobWithReport`/`CreateBatchJobNoReport` : `s3control:createJob`, `S3ReplicateObject: {}`, `S3JobManifestGenerator` avec filtre NONE/FAILED, `ClientRequestToken.$: "States.UUID()"`, `ConfirmationRequired: false` |
| 7 | check_batch ASL poll describeJob en boucle Wait+Task+Choice, terminal sur Complete/Failed/Cancelled, avec Retry et TimeoutSeconds | ✓ VERIFIED | Boucle `WaitForJob` → `DescribeBatchJob` → `EvaluateJobStatus` → (Default) `WaitForJob`. `Retry` sur `S3Control.S3ControlException`/`States.TaskFailed`. `TimeoutSeconds: 86400` au top-level. |
| 8 | var.enable_s3 (default false) gate tous les nouveaux resources IAM S3 | ✓ VERIFIED | `variables.tf` : `variable "enable_s3"` type bool, `default = false`. `main.tf` : `count = local.should_attach_policies && var.enable_s3 ? 1 : 0` pour `s3_access`, `count = var.enable_s3 ? 1 : 0` pour `s3_replication` role et policy |
| 9 | Rôle s3_replication assumable par s3.amazonaws.com ET batchoperations.s3.amazonaws.com | ✓ VERIFIED | Trust policy : deux Statements — `AllowAssumeByS3Service` (s3.amazonaws.com) et `AllowAssumeByS3BatchOperations` (batchoperations.s3.amazonaws.com) |
| 10 | iam:PassRole scopé au rôle s3_replication ARN exact via condition iam:PassedToService (pas de wildcard) | ✓ VERIFIED | Sid `PassRoleToS3Replication` : Resource `arn:aws:iam::${local.account_id}:role/${local.prefixes.iam_role}-s3-replication-role`, Condition `iam:PassedToService = ["s3.amazonaws.com","batchoperations.s3.amazonaws.com"]` |

**Score:** 10/10 truths verified

---

### Note sur SC-4 ROADMAP vs implémentation

Le ROADMAP SC-4 décrit "Map + GetBucketReplication/merge/PutBucketReplication par destination" mais le code implémente un single-pass sans Map (`MergeAllRules` via `$reduce` JSONata). Cette déviation est **intentionnelle et supérieure** : elle corrige WR-01 (code review) qui signalait qu'un Map per-destination laissait la config en état partiellement modifié en cas d'erreur mid-loop. L'outcome SC-4 — "le fan-out hub-and-spoke est supporté" — est satisfait : `$reduce` traite toutes les destinations en un seul pass atomique. La description textuelle "Map par destination" dans le ROADMAP était la conception initiale, remplacée par la meilleure solution après code review.

---

### Required Artifacts

| Artifact | Expected | Status | Details |
|----------|----------|--------|---------|
| `modules/step-functions/s3/setup_cross_account_replication.asl.json` | Setup SFN via read-merge-write, versioning gate | ✓ VERIFIED | 13 states, StartAt GetSourceVersioning, putBucketReplication, Credentials sur toutes les Tasks S3 |
| `modules/step-functions/s3/delete_replication.asl.json` | Teardown symétrique, idempotent | ✓ VERIFIED | 8 states, getBucketReplication + filter + put/delete branching |
| `modules/step-functions/s3/run_batch_replication.asl.json` | Batch job dispatch avec S3ReplicateObject | ✓ VERIFIED | s3control:createJob, GeneratedManifest, States.UUID(), précondition getBucketReplication (WR-05) |
| `modules/step-functions/s3/check_batch_replication.asl.json` | Poll describeJob en boucle jusqu'à completion | ✓ VERIFIED | s3control:describeJob, Wait+Choice loop, TimeoutSeconds:86400, Retry transient errors |
| `modules/step-functions/s3/main.tf` | for_each aws_sfn_state_machine.s3 sur 4 ASL + log group | ✓ VERIFIED | `for_each = local.step_functions`, `file("${path.module}/${each.value}")`, `role_arn = var.orchestrator_role_arn`, `aws_cloudwatch_log_group.sfn` |
| `modules/step-functions/s3/variables.tf` | 7 variables standard (pas de Lambda vars) | ✓ VERIFIED | prefix, tags, orchestrator_role_arn, enable_logging, log_retention_days, enable_xray_tracing, naming_convention — aucune variable Lambda |
| `modules/step-functions/s3/outputs.tf` | step_function_arns / _names / log_group_arn | ✓ VERIFIED | 3 outputs sur `aws_sfn_state_machine.s3` |
| `modules/step-functions/s3/versions.tf` | required_version >= 1.0, aws >= 5.0 | ✓ VERIFIED | Conforme |
| `modules/source-account/variables.tf` | enable_s3 (bool, default false) | ✓ VERIFIED | `variable "enable_s3"` type bool, default false, ligne 91 |
| `modules/source-account/main.tf` | s3_access policy + s3_replication role + s3_replication policy | ✓ VERIFIED | 3 resources enable_s3-gatées, PassRoleToS3Replication scopé, trust policy duale |
| `modules/source-account/outputs.tf` | s3_replication_role_arn / _name | ✓ VERIFIED | Lignes 67-75, gated `var.enable_s3 ? ... : null` |

---

### Key Link Verification

| From | To | Via | Status | Details |
|------|----|-----|--------|---------|
| `setup_cross_account_replication.asl.json` | Source bucket replication subresource | `aws-sdk:s3:putBucketReplication` avec Credentials.RoleArn | ✓ WIRED | Ligne 135 : `arn:aws:states:::aws-sdk:s3:putBucketReplication`, Credentials `{% $sourceRoleArn %}` |
| `delete_replication.asl.json` | Source bucket replication subresource | `aws-sdk:s3:(delete|put)BucketReplication` | ✓ WIRED | Lignes 59 + 78 : deleteBucketReplication et putBucketReplication, tous avec Credentials |
| `run_batch_replication.asl.json` | S3 Batch Operations control plane | `aws-sdk:s3control:createJob` avec S3ReplicateObject | ✓ WIRED | Lignes 62 + 110, AccountId.$, Credentials.RoleArn.$ |
| `check_batch_replication.asl.json` | S3 Batch Operations control plane | `aws-sdk:s3control:describeJob` polling sur Job.Status | ✓ WIRED | Ligne 15, AccountId.$, JobId.$ = $.BatchJob.JobId |
| Source role `s3_access` policy | `s3_replication` role | `iam:PassRole` avec `iam:PassedToService` condition | ✓ WIRED | Resource ARN exact du rôle, condition listant les 2 services S3 |
| `main.tf aws_sfn_state_machine.s3` | 4 fichiers `*.asl.json` | `file("${path.module}/${each.value}")` | ✓ WIRED | `for_each = local.step_functions` avec les 4 clés pointant les 4 filenames |
| `main.tf` | `var.orchestrator_role_arn` | `role_arn = var.orchestrator_role_arn` | ✓ WIRED | Ligne 33 |

---

### Data-Flow Trace (Level 4)

Non applicable pour ce type de phase (module Terraform + ASL JSON statique, pas de composant qui rende des données dynamiques au sens frontend). Les ASL sont évaluées au runtime SFN, non par le module Terraform. La vérification de flux de données runtime est déléguée à Phase 9 (stepfunctions-local).

---

### Behavioral Spot-Checks

| Behavior | Command | Result | Status |
|----------|---------|--------|--------|
| Pas de lambda:invoke dans aucun ASL | `grep -l "lambda:invoke" modules/step-functions/s3/*.asl.json` | Aucun match | ✓ PASS |
| No templatefile/moved/archive_file/lambda dans main.tf | `grep "lambda\|archive_file\|templatefile\|moved {" main.tf` | Aucun match | ✓ PASS |
| for_each + file() + role_arn présents dans main.tf | `grep "for_each\|file.*each.value\|role_arn = var"` | Présents | ✓ PASS |
| PassRole Resource non-wildcard | Lecture main.tf ligne 711 | `arn:aws:iam::${local.account_id}:role/...` | ✓ PASS |
| tofu validate + pytest 978 passed | Confirmé par verification_context | Deux modules validés, 978 tests pytest green | ✓ PASS (attesté) |

---

### Probe Execution

Aucun probe script conventionnel détecté dans `scripts/*/tests/probe-*.sh` pour cette phase. La validation formelle est `tofu validate` (attestée par verification_context) et `pytest tests/test_asl_validation.py` (978 passed selon verification_context).

---

### Requirements Coverage

| Requirement | Plans | Description | Status | Evidence |
|-------------|-------|-------------|--------|----------|
| REPL-01 | 07-01, 07-04 | SFN `setup_cross_account_replication` via `s3:PutBucketReplication` + assume-role impératif | ✓ SATISFIED | putBucketReplication + Credentials.RoleArn sur toutes les Tasks S3 |
| REPL-02 | 07-02 | SFN `run_batch_replication` via `s3control:CreateJob` (S3ReplicateObject) | ✓ SATISFIED | s3control:createJob avec S3ReplicateObject + GeneratedManifest |
| REPL-03 | 07-02 | SFN `check_batch_replication` poll via `s3control:DescribeJob` jusqu'à completion | ✓ SATISFIED | describeJob en boucle Wait+Choice, terminal sur Complete/Failed/Cancelled |
| REPL-04 | 07-01 | SFN `delete_replication` retire la config de réplication | ✓ SATISFIED | getBucketReplication → filter → deleteBucketReplication ou putBucketReplication (remainder) |
| REPL-05 | 07-01, 07-02 | Fan-out hub-and-spoke : 1 source → N destinations | ✓ SATISFIED | setup: $reduce sur Destinations[] dans MergeAllRules ; run_batch: 1 job couvre toutes les destinations |
| REPL-06 | 07-02, 07-04 | Aucun Lambda ; intégrations SDK natives uniquement | ✓ SATISFIED | Aucun `lambda:invoke`, aucun `aws_lambda_function`/`archive_file` dans aucun fichier |
| IAM-01 | 07-03 | Rôle s3_replication optionnel gardé par enable_s3, assumable par les 2 services S3 | ✓ SATISFIED | `aws_iam_role.s3_replication` count=`var.enable_s3 ? 1 : 0`, trust policy duale |
| IAM-02 | 07-03 | Perms source : PutBucketReplication, CreateJob, DescribeJob, iam:PassRole scopé | ✓ SATISFIED | `s3_access` policy : S3ReplicationManage + S3BatchControl + PassRoleToS3Replication (non-wildcard) |

**Orphaned requirements (not claimed by Phase 7 plans) :** ORCH-04, ORCH-05 → Phase 8 ; INFRA-03, INFRA-04 → Phase 9. Conformes à la traceability REQUIREMENTS.md.

---

### Anti-Patterns Found

| File | Line | Pattern | Severity | Impact |
|------|------|---------|----------|--------|
| `modules/source-account/main.tf` | 698 | `TODO(Phase 8 / WR-04)` | ℹ️ Info | Marqueur de suivi formel référençant Phase 8 — non bloquant (phase de wiring future documentée) |
| `modules/source-account/main.tf` | 809 | `TODO(Phase 8 / WR-04)` | ℹ️ Info | Idem — tightening des ARNs destination au wiring client Phase 8 |

Aucun marqueur `TBD`, `FIXME` ou `XXX` non référencé. Les `TODO` présents référencent tous "Phase 8 / WR-04" — travail de suivi formel auditables. Règle de blocage : non déclenchée.

---

### Human Verification Required

#### 1. Sémantique JSONata MergeAllRules (setup)

**Test:** Exécuter `setup_cross_account_replication` avec stepfunctions-local (Phase 9) sur un bucket ayant déjà 2 rules, en ajoutant une 3e destination, puis relancer avec la même destination (idempotence).

**Expected:** (1) La 3e Rule est ajoutée avec une priorité unique. (2) Un deuxième run avec la même destination remplace la Rule existante sans changer sa priorité. (3) Les 2 autres Rules sont préservées intactes. Le guard `AssertDistinctPriorities` ne se déclenche pas.

**Why human:** Les tests pytest sont structurels (JSON valide, types, NextState, Credentials). L'évaluation des expressions `$reduce` / `$filter` / `$exists` / `$max` JSONata ne peut être vérifiée qu'à l'exécution. Le correctif CR-03 (priorité stable) et WR-01 (single-pass atomique) sont syntaxiquement corrects mais leur comportement runtime dépend de la sémantique JSONata réelle d'AWS Step Functions.

#### 2. Sémantique JSONata FilterRules (delete)

**Test:** Exécuter `delete_replication` avec stepfunctions-local sur une config à 3 Rules en supprimant 2 destinations ciblées.

**Expected:** Les 2 Rules ciblées sont retirées, la 3e est préservée dans `PutRemainingConfig`. Un deuxième run avec les mêmes destinations produit `remainingEmpty = true` et déclenche `DeleteAllReplication` (idempotence).

**Why human:** Même raison : sémantique JSONata de `$map` / `$filter` / `$not` non testable statiquement.

#### 3. Confirmation formelle de tofu validate

**Test:** Exécuter `tofu validate` et `tofu fmt -check` sur `modules/step-functions/s3/` et `modules/source-account/`.

**Expected:** Exit code 0 pour les deux commandes sur les deux modules.

**Why human:** Le verification_context atteste que ces commandes ont passé, mais l'exécuteur 07-04 a noté que le sandbox refusait `tofu`. Confirmation directe souhaitée si non encore faite dans l'environnement de CI.

---

### Gaps Summary

Aucun gap bloquant. Les 10 truths sont vérifiées dans le code. Les 8 requirements Phase 7 (REPL-01..06, IAM-01, IAM-02) ont des artifacts substantiels et câblés. La contrainte D-09 (aucun Lambda) est respectée dans tous les fichiers.

Les seuls items ouverts sont des vérifications de comportement runtime JSONata (déférées à Phase 9 stepfunctions-local par conception) et la confirmation formelle de tofu validate — tous trois relevant de la vérification humaine, non de blocages de code.

---

## Deferred Items

| # | Item | Addressed In | Evidence |
|---|------|-------------|----------|
| 1 | Intégration orchestrateur (bloc input S3 optionnel, guard no-op) | Phase 8 | ROADMAP SC Phase 8 : "Un bloc input S3 optionnel... pilote une phase de replication S3 dans l'orchestrateur" |
| 2 | spec repl-s3-sync.md + validation ASL formelle | Phase 9 | ROADMAP SC Phase 9 : "specs/repl-s3-sync.md existe... La validation ASL passe pour les nouvelles SFN S3" |
| 3 | Tests d'exécution JSONata runtime (sémantique merge/filter) | Phase 9 | ROADMAP Phase 9 : "stepfunctions-local" — INFRA-04 |

---

_Verified: 2026-06-19T12:00:00Z_
_Verifier: Claude (gsd-verifier)_
