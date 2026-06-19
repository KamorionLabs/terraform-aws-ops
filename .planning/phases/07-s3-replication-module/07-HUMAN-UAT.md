---
status: partial
phase: 07-s3-replication-module
source: [07-VERIFICATION.md]
started: 2026-06-19T00:00:00Z
updated: 2026-06-19T00:00:00Z
---

## Current Test

[awaiting human testing — JSONata runtime semantics, deferred by design to Phase 9 stepfunctions-local]

## Tests

### 1. Sémantique MergeAllRules (setup_cross_account_replication)
expected: Le `$reduce` JSONata sur `Destinations[]` produit une `ReplicationConfiguration` correcte — priorités stables par destination (réutilise la Priority d'une règle existante pour le même ID, `max+1` pour une nouvelle destination), pas de collision au re-run, fusion préservant les règles des autres spokes. Vérifiable via exécution réelle (Phase 9 stepfunctions-local).
result: [pending]

### 2. Sémantique FilterRules (delete_replication)
expected: Le `$filter`/`$not` retire exactement la/les Rule(s) ciblée(s) et conserve les autres ; bascule correcte entre `deleteBucketReplication` (plus aucune rule) et `putBucketReplication` (rules restantes). Idempotent sur not-found. Vérifiable via exécution réelle (Phase 9 stepfunctions-local).
result: [pending]

### 3. Confirmation tofu validate
expected: `tofu validate` passe sur `modules/step-functions/s3/` et `modules/source-account/`.
result: passed — confirmé par l'orchestrateur le 2026-06-19 (`tofu validate` = Success sur les deux modules ; `tofu fmt -check` clean). Reste un warning déprécation pré-existant `data.aws_region.current.id` (hérité du pattern EFS, hors scope).

## Summary

total: 3
passed: 1
issues: 0
pending: 2
skipped: 0
blocked: 0

## Gaps
