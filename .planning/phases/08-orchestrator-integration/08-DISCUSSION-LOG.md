# Phase 8: Orchestrator Integration - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in CONTEXT.md — this log preserves the alternatives considered.

**Date:** 2026-06-22
**Phase:** 8-orchestrator-integration
**Areas discussed:** Point d'insertion lifecycle, Périmètre de la phase S3, Sync vs async backfill, Structure du bloc input S3

---

## Point d'insertion lifecycle

| Option | Description | Selected |
|--------|-------------|----------|
| Bolt-on après le refresh | Choice garde style ConfigSync → phase S3 indépendante en fin de flux | |
| Bolt-on avant le refresh | setup S3 tôt pour capturer les écritures du refresh | |
| Tissé comme EFS (Parallel) | Intégré aux Parallel phases ; S3 chevauche le refresh | ✓ |

**User's choice:** Tissé comme EFS (Parallel)
**Notes:** Cohérent avec le choix « tout sync » (Q3 opt.2) : en Parallel, l'attente du backfill chevauche le refresh DB/EFS déjà long → impact temps mur quasi nul. Contrainte rétrocompat (no-op strict) demande une garde Choice soignée autour de la branche.

---

## Périmètre de la phase S3

| Option | Description | Selected |
|--------|-------------|----------|
| setup + backfill optionnel, pas de teardown | backfill sous-toggle S3.Backfill.Enabled ; réplication persiste | ✓ |
| setup + backfill toujours ensemble | enchaîne systématiquement | |
| setup seul | backfill hors orchestrateur | |

**User's choice:** setup + backfill optionnel (sous-toggle), pas de teardown
**Notes:** Réplication S3 persistante (≠ EFS temporaire). `delete_replication` reste appelable hors orchestrateur.

---

## Sync vs async backfill

| Option | Description | Selected |
|--------|-------------|----------|
| setup sync, backfill fire-and-forget | l'orchestrateur continue sans attendre check_batch | |
| Tout en sync:2 | attend la complétion du backfill (conforme critère #3) | ✓ |
| Sync avec timeout borné sur check_batch | compromis | |

**User's choice:** Tout en sync:2 (option 2) — après clarification
**Notes:** L'utilisateur a demandé ce qui se passe au re-run sur buckets déjà synchronisés. Réponse : le `ManifestGenerator` filtre `ObjectReplicationStatuses: ["NONE","FAILED"]` + `EligibleForReplication: true` → re-run = delta seulement, rapide, pas de recopie. Seul le premier backfill est long, absorbé par le weave Parallel. Décision sync confirmée sur cette base.

---

## Structure du bloc input S3

| Option | Description | Selected |
|--------|-------------|----------|
| Mirror EFS + reshape dans l'orchestrateur | bloc miroir EFS, reshape vers contrat SFN figé Phase 7 | ✓ |
| Passthrough direct du contrat SFN | bloc déjà au format SFN | |
| Mirror EFS strict, SFN adaptent | casserait le contrat Phase 7 | |

**User's choice:** Mirror EFS + reshape dans l'orchestrateur
**Notes:** Cohérence d'API appelant avec EFS ; contrat SFN S3 (validé + sécurisé Phase 7) inchangé.

## Claude's Discretion

- Noms d'états/gardes ASL (convention EFS), forme exacte du reshape (Pass global vs Arguments par Task), câblage Terraform racine du bloc input.

## Deferred Ideas

- Teardown S3 orchestré (`delete_replication`) — écarté, reste manuel.
- Timeout borné sur `check_batch` — écarté au profit du sync complet.
- Backfill async fire-and-forget — à reconsidérer en Phase 9 si besoin de découplage.
