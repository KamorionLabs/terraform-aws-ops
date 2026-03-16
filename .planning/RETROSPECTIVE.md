# Project Retrospective

*A living document updated after each milestone. Lessons feed forward into future planning.*

## Milestone: v1.0 — Step Functions Modularization

**Shipped:** 2026-03-16
**Phases:** 3 | **Plans:** 9

### What Was Built
- 6 sous-SFN reutilisables (ManageLambdaLifecycle, ManageAccessPoint, ManageFileSystemPolicy, CheckFlagFileSync, EnsureSnapshotAvailable, ClusterSwitchSequence)
- 4 fichiers ASL complexes refactores avec reduction significative de states (72->27, 53->45, 51->42, 39->33)
- 6 paires public/private consolidees en fichiers uniques via EKS.AccessMode Choice state routing
- Framework de tests interface snapshots (non-regression des contrats I/O)
- Architecture Terraform dual/triple-map avec moved blocks

### What Worked
- Approche sequentielle par phases (extraction -> refactoring -> consolidation) : chaque phase construisait sur les fondations de la precedente
- Groupement par module dans les plans (EFS, DB, Orchestrator/Utils) : donne une vue coherente et permet de valider module par module
- Pattern dual-map Terraform avec moved blocks : zero destroy/recreate, migration declarative
- Tests auto-decouverte (rglob) : couvrent automatiquement les nouveaux fichiers sans modification du CI

### What Was Inefficient
- State count targets trop agressifs pour certains fichiers (~30 vise vs ~42-45 realise) : aurait du etre detecte plus tot dans la discussion
- Le commutateur pub/priv etait mal identifie au debut (Account.RoleArn vs EKS.AccessMode) : necessitait une analyse approfondie du codebase en Phase 3
- CI matrix DB incomplete apres Phase 2 (3 fichiers manquants) : dette technique cosmique mais evitable

### Patterns Established
- CheckAccessMode Choice state comme pattern de consolidation pub/priv (reutilisable pour d'autres paires futures)
- InitPrivateDefaults stub pour les ASL avec Map states qui necessitent des paths EksCluster
- Three-tier Terraform resource architecture quand des references ARN circulaires existent
- $$.Execution.Input materialization via States.ArrayGetItem default-value pattern
- Interface snapshot tests dans tests/snapshots/ pour non-regression automatisee

### Key Lessons
1. Analyser le codebase AVANT de verrouiller les targets dans le roadmap — les estimations de reduction de states etaient trop optimistes
2. Le commutateur pub/priv n'est pas toujours ce qu'on croit — la distinction est souvent plus subtile que "cross-account vs mono-account"
3. Les patterns etablis dans les premieres phases (dual-map, moved blocks, CheckAccessMode) accelerent significativement les phases suivantes

### Cost Observations
- Model mix: ~70% opus (execution), ~30% sonnet (verification, plan-checking)
- Total execution time: ~45 minutes pour 9 plans
- Notable: Phase 3 plans plus rapides (3-6min) que Phase 2 (5-13min) grace aux patterns stabilises

---

## Cross-Milestone Trends

### Process Evolution

| Milestone | Phases | Plans | Key Change |
|-----------|--------|-------|------------|
| v1.0 | 3 | 9 | Etabli le workflow discuss -> plan -> execute -> verify |

### Cumulative Quality

| Milestone | Tests | Coverage | Sub-SFNs Created |
|-----------|-------|----------|-----------------|
| v1.0 | 916+ | ASL structure + interface snapshots | 6 |

### Top Lessons (Verified Across Milestones)

1. Les patterns etablis tot paient exponentiellement dans les phases suivantes
2. Analyser le codebase reel avant de fixer des targets quantitatifs
