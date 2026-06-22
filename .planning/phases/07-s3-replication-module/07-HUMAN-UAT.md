---
status: partial
phase: 07-s3-replication-module
source: [07-VERIFICATION.md]
started: 2026-06-19T00:00:00Z
updated: 2026-06-19T00:00:00Z
---

## Current Test

[JSONata semantics validated locally via reference engine — 2 deploy-blocking bugs found & fixed. Full AWS-runtime confirmation remains for Phase 9 stepfunctions-local.]

## Tests

### 1. Sémantique MergeAllRules (setup_cross_account_replication)
expected: Le `$reduce` JSONata sur `Destinations[]` produit une `ReplicationConfiguration` correcte — priorités stables par destination (réutilise la Priority d'une règle existante pour le même ID, `max+1` pour une nouvelle destination), pas de collision au re-run, fusion préservant les règles des autres spokes.
result: validated-local — exécuté contre le moteur jsonata de référence (= AWS SFN), scénarios S1-S5 verts (1ère destination@P0, ajout B@max+1, re-run idempotent priorité stable, 3 destinations distinctes, RTC→Metrics+ReplicationTime). **Bug trouvé+corrigé** : corps de lambda `function(){ a;b;expr }` non-parseable → wrappé en `function(){ (a;b;expr) }` (commit fcb9664). Régression verrouillée en CI (`tests/jsonata/s3-replication.test.js`). Confirmation AWS réelle → Phase 9.

### 2. Sémantique FilterRules (delete_replication)
expected: Le `$filter`/`$not` retire exactement la/les Rule(s) ciblée(s) et conserve les autres ; bascule correcte entre `deleteBucketReplication` (plus aucune rule) et `putBucketReplication` (rules restantes). Idempotent sur not-found.
result: validated-local — scénarios D1-D3 verts. **2 bugs trouvés+corrigés** (commit 610ec66) : (a) même bug de lambda non-parseable ; (b) `$filter` retournant 1 seule règle renvoyait un objet, pas un tableau → `$append([], $filter(...))` garantit un tableau (sinon S3 rejette `Rules` quand il reste exactement 1 spoke). Régression verrouillée en CI. Confirmation AWS réelle → Phase 9.

### 3. Confirmation tofu validate
expected: `tofu validate` passe sur `modules/step-functions/s3/` et `modules/source-account/`.
result: passed — confirmé par l'orchestrateur le 2026-06-19 (`tofu validate` = Success sur les deux modules ; `tofu fmt -check` clean). Reste un warning déprécation pré-existant `data.aws_region.current.id` (hérité du pattern EFS, hors scope).

## Summary

total: 3
passed: 1
validated_local: 2
pending_aws_runtime: 2
issues: 0
skipped: 0
blocked: 0

## Gaps
