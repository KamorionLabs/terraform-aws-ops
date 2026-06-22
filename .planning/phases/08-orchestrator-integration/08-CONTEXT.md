# Phase 8: Orchestrator Integration - Context

**Gathered:** 2026-06-22
**Status:** Ready for planning

<domain>
## Phase Boundary

Brancher la réplication S3 (module `s3/` livré en Phase 7) dans `refresh_orchestrator` de manière **optionnelle**, via un bloc input S3 (miroir du bloc EFS), avec **no-op strict** quand le bloc est absent ou `S3.Enabled=false`. Couvre ORCH-04 et ORCH-05.

**Hors scope :** modification des SFN S3 (contrat figé Phase 7), wiring client / valeurs Rubix concrètes, spec `repl-s3-sync.md` + tests (Phase 9), grants côté destination.

</domain>

<decisions>
## Implementation Decisions

### D1 — Point d'insertion dans le lifecycle
- **D-01:** La phase de réplication S3 est **tissée en `Parallel` (style EFS)**, pas en bolt-on séquentiel. Intention : le setup + backfill S3 **chevauchent** les phases de refresh (DB/EFS/EKS) déjà longues, pour que leur complétion `sync` n'ajoute quasi pas de temps mur.
- **D-02:** Le placement exact de la branche Parallel (dans quel `Parallel` existant, ou nouvelle branche) est **dérivé au plan** en s'alignant sur le pattern EFS (`Phase1WithReplication`) — pas figé ici.
- **D-03:** **Contrainte rétrocompat (critère #2, ORCH-05) :** la garde d'activation doit garantir un comportement **strictement identique à avant** quand S3 est absent/désactivé. Avec un weave Parallel, la garde `Choice` (style `CheckEFSReplicationMode`/`CheckEFSEnabled`) doit soit court-circuiter entièrement la branche S3, soit la réduire à un `Pass` no-op — à valider au plan (le bolt-on aurait été plus simple sur ce point, le weave demande une garde soignée).

### D2 — Périmètre de la phase S3
- **D-04:** `setup_cross_account_replication` est **toujours invoqué** quand `S3.Enabled=true`.
- **D-05:** Le **backfill** (`run_batch_replication` → `check_batch_replication`) est gardé par un **sous-toggle** `S3.Backfill.Enabled` (coûteux/long, pas toujours souhaité).
- **D-06:** **Pas de teardown** S3 dans l'orchestrateur (pas d'appel `delete_replication`). La réplication S3 est **persistante** (réplication live continue), contrairement à la réplication EFS qui est temporaire au refresh (`DeleteEFSReplication` au cutoff). `delete_replication` reste une SFN appelable hors orchestrateur.

### D3 — Invocation sync vs async
- **D-07:** Toute la séquence S3 (`setup` → `run_batch` → `check_batch`) est invoquée en **`startExecution.sync:2`** (conforme critère de succès #3). L'orchestrateur attend la complétion du backfill.
- **D-08:** Acceptable car : (a) le `ManifestGenerator` de `run_batch` filtre déjà `ObjectReplicationStatuses: ["NONE","FAILED"]` + `EligibleForReplication: true` → un **re-run** sur des buckets déjà synchronisés ne traite que le **delta** (objets jamais répliqués / en échec), donc rapide et sans recopie/double egress ; (b) le seul cas long = **premier backfill** d'un gros bucket existant, absorbé par le **weave Parallel** (D-01) qui le fait chevaucher le refresh. (Alternative écartée : timeout borné sur `check_batch`.)

### D4 — Structure du bloc input S3
- **D-09:** Bloc input **miroir EFS** : `S3: { Enabled, Source, Destinations[], Replication }` (+ sous-toggle `S3.Backfill.Enabled` de D-05). Cohérence d'API côté appelant avec le bloc EFS.
- **D-10:** Le **reshape** vers le contrat SFN figé Phase 7 (`SourceBucket` / `Destinations[]` avec `AccountId`/`RoleArn`/`Bucket`/`Region`/options + `ReplicationRoleArn` / `BatchReplicationRoleArn` / `SourceAccount`) se fait **dans l'orchestrateur** (états `Pass`/`Arguments` avant chaque `startExecution`). Le contrat des SFN S3 reste **inchangé** (validé + sécurisé en Phase 7).

### Claude's Discretion
- Nom exact des états/gardes ASL (suivre la convention EFS : `CheckS3ReplicationMode`/`CheckS3Enabled`, `Fail` states nommés).
- Forme exacte du reshape (un Pass global vs Arguments par Task).
- Câblage Terraform racine (`main.tf`) du bloc input S3 vers l'orchestrateur.

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Périmètre & exigences Phase 8
- `.planning/ROADMAP.md` §"Phase 8: Orchestrator Integration" — goal + 3 critères de succès
- `.planning/REQUIREMENTS.md` — ORCH-04 (bloc input S3 mirroir EFS) / ORCH-05 (no-op strict si absent/désactivé)
- `.planning/phases/07-s3-replication-module/07-CONTEXT.md` §D-11 — contrat d'input des SFN S3 dérivé du pattern EFS

### Pattern analog EFS (modèle direct du weave + garde)
- `modules/step-functions/orchestrator/refresh_orchestrator.asl.json` — états EFS à mirror : `CheckEFSReplicationMode`/`CheckEFSEnabled` (gardes Choice), `Phase1WithReplication` (Parallel weave), `SynchronizedCutoffAndRestore`, `CheckConfigSyncOption`→`ExecuteSyncConfigItems` (bolt-on v1.1, autre option de garde)
- `modules/step-functions/efs/setup_cross_account_replication.asl.json` — pattern `startExecution.sync:2` d'une sous-SFN de réplication depuis l'orchestrateur

### Contrats SFN S3 figés (Phase 7 — NE PAS modifier)
- `modules/step-functions/s3/setup_cross_account_replication.asl.json` — input attendu : `SourceBucket`, `Destinations[]`, `SourceAccount`, role
- `modules/step-functions/s3/run_batch_replication.asl.json` — input : `SourceBucketArn`, `BatchReplicationRoleArn`, `ReportBucketArn?`, `SourceAccount.AccountId` ; manifest filtré NONE/FAILED
- `modules/step-functions/s3/check_batch_replication.asl.json` — input : `JobId`, `SourceAccount`
- `modules/step-functions/s3/main.tf` / `variables.tf` / `outputs.tf` — outputs ARN des 4 SFN à câbler dans l'orchestrateur

### Spec analog (pour Phase 9, contexte)
- `specs/repl-efs-sync.md` — structure miroir pour `specs/repl-s3-sync.md` (Phase 9)

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `refresh_orchestrator.asl.json` (49 états) : deux patterns d'intégration optionnelle déjà en place — **EFS weave** (Parallel, choisi ici) et **ConfigSync bolt-on** (v1.1, `CheckConfigSyncOption`→`ExecuteSyncConfigItems`). Réutiliser la mécanique de garde Choice + `startExecution.sync:2`.
- Sortie des SFN S3 enveloppée sous `$.Output.*` (convention `.sync:2`) — prévoir un `ResultSelector`/`OutputPath` pour ré-extraire le `JobId` entre `run_batch` et `check_batch`.

### Established Patterns
- Gardes d'activation : `CheckEFSEnabled` style `Choice` sur `$.EFS` présent + `$.EFS.Enabled` → reproduire `CheckS3Enabled` sur `$.S3`/`$.S3.Enabled`.
- Désactivable côté IAM/Terraform : `var.enable_s3` (déjà livré Phase 7 dans `source-account/`).
- `Credentials.RoleArn.$` impératif déjà dans les SFN S3 — l'orchestrateur passe juste le payload + ARNs.

### Integration Points
- `refresh_orchestrator.asl.json` : nouvelle branche Parallel + garde Choice + reshape input.
- Terraform racine / module orchestrator : passer les ARNs des SFN S3 (outputs du module `s3/`) à l'orchestrateur ; bloc input S3 dans la définition de l'exécution.
- Dépendance d'ordre **intra-phase S3** : `setup` AVANT `run_batch` (S3 Batch Replication réutilise la config de réplication posée par setup).

</code_context>

<specifics>
## Specific Ideas

- Le re-run d'un backfill sur buckets déjà synchronisés doit rester rapide — garanti par le filtre manifest `NONE`/`FAILED` (vérifié dans `run_batch_replication.asl.json` au moment de la discussion). Le planner ne doit pas réintroduire un manifest non filtré.
- Garde rétrocompat = exigence dure : un run sans bloc S3 doit produire un diff de comportement **nul** (critère #2).

</specifics>

<deferred>
## Deferred Ideas

- `delete_replication` piloté par l'orchestrateur (teardown automatique) — écarté (D-06) ; reste appelable manuellement. Reconsidérer si un cas d'usage de décommission orchestrée émerge.
- Timeout borné sur `check_batch` (compromis sync/async) — écarté au profit du sync complet (D-07/D-08) ; à revisiter si un premier backfill dépasse la fenêtre acceptable malgré le weave Parallel.
- Backfill async fire-and-forget — écarté ; à reconsidérer en Phase 9 si la spec révèle un besoin de découplage.

None hors de ces points — discussion restée dans le périmètre de la phase.

</deferred>

---

*Phase: 8-orchestrator-integration*
*Context gathered: 2026-06-22*
