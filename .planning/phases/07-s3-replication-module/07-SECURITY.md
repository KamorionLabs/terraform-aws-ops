---
phase: 07
slug: s3-replication-module
status: verified
threats_open: 0
asvs_level: 1
created: 2026-06-22
---

# Phase 07 — Security

> Per-phase security contract: threat register, accepted risks, and audit trail.

---

## Trust Boundaries

| Boundary | Description | Data Crossing |
|----------|-------------|---------------|
| SFN execution → source account | SFN assumes `$.SourceAccount.RoleArn` to write the replication subresource / call s3control on a bucket owned by an external stack | Replication config, destination payload |
| SFN payload → S3 control plane | Destination list is an execution input from the trusted orchestrator (Phase 8) | Destination bucket/account list |
| source role → s3_replication role | `iam:PassRole` lets the source role hand the replication role to S3 services | Role ARN (scoped) |
| S3 / batch service → source data | The replication role is assumed by AWS services to read source / write destination objects | Object data (cross-account) |
| Terraform module → AWS account | Module declares Step Function resources executed under `var.orchestrator_role_arn` | IaC resource definitions |
| Test tooling → npm registry | CI-only JSONata validation pulls `jsonata` (devtooling, not deployed) | npm package |

---

## Threat Register

| Threat ID | Category | Component | Disposition | Mitigation | Status |
|-----------|----------|-----------|-------------|------------|--------|
| T-07-01-setup | Tampering | source bucket data | mitigate | Validate-only versioning — only `getBucketVersioning` reads, never `putBucketVersioning`; only replication subresource written (`putBucketReplication`) | closed |
| T-07-02 | Tampering/DoS | replication config (lost update wiping spokes) | mitigate | Single Get→merge→single Put atomique ; `$reduce`/`$filter` par ID déterministe préserve les Rules des autres spokes ; chemin d'erreur lecture (`States.ALL`) → `SetupFailed` (ne seed PAS de config vide) ; `AssertDistinctPriorities` | closed |
| T-07-03 | Elevation of Privilege | imperative assume-role chain | accept | Source role trust conditionnée par `aws:PrincipalOrgID` (inchangé) ; SFN passe seulement `Credentials.RoleArn` + payload destination | closed |
| T-07-04 | Tampering (replay) | replay of CreateJob | mitigate | `ClientRequestToken.$: States.UUID()` frais par exécution dans les deux states createJob | closed |
| T-07-05 | Information Disclosure | batch completion report | accept | `Report.Enabled:false` par défaut quand `$.ReportBucketArn` absent ; grants du report bucket = client-owned (hors scope) | closed |
| T-07-06 | Elevation of Privilege | createJob RoleArn passing | mitigate | `RoleArn.$` vient de l'input ; contrainte `iam:PassRole` appliquée côté source-account (cf. T-07-01-iam) | closed |
| T-07-01-iam | Elevation of Privilege | `iam:PassRole` scope | mitigate | Resource = ARN spécifique `${prefix}-s3-replication-role` (PAS de wildcard) + `iam:PassedToService` = exactement `s3.amazonaws.com` + `batchoperations.s3.amazonaws.com` | closed |
| T-07-07 | Elevation of Privilege | combined trust policy over-grant | mitigate | Trust de `s3_replication` admet uniquement les 2 service principals S3 via `sts:AssumeRole` ; aucun principal IAM / account-root | closed |
| T-07-08 | Tampering | new IAM disrupts existing deployments | mitigate | `var.enable_s3` default false ; les 3 ressources S3 count-gated → plan-noop pour les consommateurs existants | closed |
| T-07-09 | Information Disclosure | over-broad source-data read by replication role | accept | Resources `*`/paramétrées par design (module générique opensource) ; tightening bucket/KMS concret = wiring client Phase 8 (TODO explicites en code) | closed |
| T-07-10 | Tampering (compute surface) | introducing Lambda | mitigate | Aucun `aws_lambda_function`/`archive_file`/`lambda:invoke`/`templatefile` dans le module ; skeleton `file()`-only (D-09) | closed |
| T-07-11 | Repudiation | missing execution audit | mitigate | `enable_logging` default true ; `logging_configuration` (level ALL, include_execution_data) → `aws_cloudwatch_log_group.sfn` | closed |
| T-07-SC | Supply chain | dependency installs (déviation) | mitigate | Dép `jsonata@^2.0.0` ajoutée pour la validation CI (devtooling, NON déployé) : unique package MIT, lockfile committé (v3, hash integrity), 0 dépendance transitive, 0 script postinstall/preinstall, scope test/CI | closed |

*Status: open · closed*
*Disposition: mitigate (implementation required) · accept (documented risk) · transfer (third-party)*

---

## Accepted Risks Log

| Risk ID | Threat Ref | Rationale | Accepted By | Date |
|---------|------------|-----------|-------------|------|
| AR-07-01 | T-07-03 | Chaîne assume-role impérative : bucket source owned par stack externe → déclaratif impossible. Trust bornée par `aws:PrincipalOrgID`. | Jean (plan-time, ADR-008) | 2026-06-22 |
| AR-07-02 | T-07-05 | Report S3 Batch désactivé par défaut ; quand activé, le report bucket et ses grants sont client-owned (hors périmètre module générique). | Jean (plan-time) | 2026-06-22 |
| AR-07-03 | T-07-09 | Resources IAM `*`/paramétrées par design : module générique opensource. Le tightening concret (ARNs bucket/KMS) relève du wiring client Phase 8 (TODO WR-04 documentés en code). | Jean (plan-time) | 2026-06-22 |

*Accepted risks do not resurface in future audit runs.*

---

## Security Audit Trail

| Audit Date | Threats Total | Closed | Open | Run By |
|------------|---------------|--------|------|--------|
| 2026-06-22 | 13 | 13 | 0 | gsd-security-auditor (opus) |

---

## Sign-Off

- [x] All threats have a disposition (mitigate / accept / transfer)
- [x] Accepted risks documented in Accepted Risks Log
- [x] `threats_open: 0` confirmed
- [x] `status: verified` set in frontmatter

**Approval:** verified 2026-06-22

---

## Non-blocking reserve (informatif, hors register)

Le SUMMARY 07-03 notait que `terraform validate`/`fmt`/`plan` avaient été refusés dans le sandbox de l'exécuteur — gap de **vérification fonctionnelle**, pas de mitigation absente. Levé par l'orchestrateur : `tofu validate` = Success sur `modules/step-functions/s3/` et `modules/source-account/`, `tofu fmt -check` clean (2026-06-22). Reste à confirmer un `plan` avec `enable_s3` false puis true avant merge réel.
