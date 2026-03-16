# Phase 3: Consolidation - Context

**Gathered:** 2026-03-16
**Status:** Ready for planning

<domain>
## Phase Boundary

Fusionner les 6 paires de fichiers ASL public/private en 6 fichiers uniques parametrises. Le commutateur est EKS.AccessMode (public/private) dans l'input SFN, pas la variable Terraform. Supprimer les 6 fichiers _private, la logique _suffix Terraform, et la variable eks_access_mode. L'orchestrateur ne change pas (il passe deja toutes les infos necessaires).

</domain>

<decisions>
## Implementation Decisions

### Commutateur pub/priv
- Le commutateur est `EKS.AccessMode` dans l'input SFN, pas `Account.RoleArn`
- Public/private est une notion d'exposition de l'API Kubernetes (endpoint public vs prive), PAS une notion de cross-account — ca peut etre cross ou mono-account dans les deux cas
- Les deux versions (pub et priv) utilisent deja `DestinationAccount.RoleArn` dans les Credentials pour les appels cross-account
- Le ROADMAP mentionne `Account.RoleArn` comme commutateur mais l'analyse du codebase montre que ce n'est pas le bon discriminant — c'est l'accessibilite de l'API K8s qui compte

### Consolidation des 4 paires simples
- manage_storage, scale_services, verify_and_restart_services, run_mysqldump_on_eks
- Seule difference : un state `GetEksClusterInfo` (eks:describeCluster) au debut de la version publique
- Consolidation : Choice state au debut route selon `EKS.AccessMode`
  - "public" -> GetEksClusterInfo -> flow commun
  - "private" -> flow commun directement (les infos cluster sont deja dans l'input)

### Consolidation des 2 paires complexes
- run_archive_job et run_mysqlimport_on_eks
- Difference structurelle : la version publique utilise `eks:runJob.sync` (integration native SFN), la privee utilise un cycle Lambda (create/wait/check/delete job) car l'endpoint prive ne supporte pas l'integration native
- Consolidation : Choice state apres la preparation route vers le flow natif ou le flow Lambda selon `EKS.AccessMode`
- Les deux chemins convergent vers PrepareOutput

### Decoupage en plans
- 3 plans groupes par module, dans cet ordre :
  1. **Plan 03-01 : Module EKS** — Consolider manage_storage + scale_services + verify_and_restart_services, supprimer 3 fichiers _private, supprimer logique _suffix dans eks/main.tf
  2. **Plan 03-02 : Module DB** — Consolider run_mysqldump_on_eks + run_mysqlimport_on_eks (complexe), supprimer 2 fichiers _private, supprimer logique _eks_suffix dans db/main.tf
  3. **Plan 03-03 : Module Utils + cleanup** — Consolider run_archive_job (complexe), supprimer 1 fichier _private, supprimer logique _eks_suffix dans utils/main.tf, supprimer variable eks_access_mode du root module, mise a jour CI matrix
- Ordre : EKS d'abord (3 paires simples, valide le pattern), DB ensuite (1 simple + 1 complexe), Utils en dernier avec cleanup global

### Impact orchestrator
- Zero modification dans l'orchestrateur — il passe deja DestinationAccount + infos EKS dans l'input de chaque sous-SFN
- Seul changement cote appelant : la map StepFunctions n'a plus les cles _private (un seul ARN par operation)
- Claude doit verifier que l'orchestrateur passe bien les infos EksCluster/K8sProxy dans l'input de TOUTES les sous-SFN concernees (edge case a auditer)

### Variable Terraform eks_access_mode
- Supprimer dans le dernier plan (03-03) apres que tous les modules aient retire la logique _suffix
- Le mode public/private est desormais determine au runtime via l'input SFN, pas au deploy-time via Terraform

### Claude's Discretion
- Commutateur exact dans le Choice state ($.EKS.AccessMode ou presence/absence de $.EksCluster — analyser ce qui est le plus robuste)
- Noms internes des states dans les fichiers consolides
- Gestion des Credentials dans les states communs
- Structure exacte du CI matrix update
- Ordre des taches dans chaque plan

</decisions>

<specifics>
## Specific Ideas

- Pour les 4 paires simples, le pattern est identique : Choice state au debut qui decide si GetEksClusterInfo est necessaire. Le reste du flow est identique entre pub et priv.
- Pour les 2 paires complexes (run_archive_job, run_mysqlimport), les flows divergent apres la preparation : `eks:runJob.sync` (natif) vs cycle Lambda (create/wait/check/delete). Le fichier consolide contient les deux chemins avec un Choice state.
- La distinction pub/priv n'est PAS cross-account vs mono-account. C'est "API K8s accessible publiquement" vs "API K8s en endpoint prive necessitant un proxy Lambda".
- L'orchestrateur ne reference jamais les ARN _private directement — le choix se faisait au deploy-time via Terraform `eks_access_mode`. Apres consolidation, le choix se fait au runtime via l'input SFN.

</specifics>

<code_context>
## Existing Code Insights

### Reusable Assets
- `modules/step-functions/eks/main.tf` : logique `_suffix` a supprimer, pattern for_each existant
- `modules/step-functions/db/main.tf` : logique `_eks_suffix` a supprimer, dual-map pattern de Phase 2
- `modules/step-functions/utils/main.tf` : logique `_eks_suffix` a supprimer
- `tests/test_interface_snapshots.py` : framework de tests de non-regression des interfaces (cree en Phase 2)
- `tests/snapshots/` : repertoire pour les snapshots de reference

### Established Patterns
- Choice state pour routage conditionnel (utilise partout dans les ASL existants)
- Dual-map Terraform avec moved blocks (etabli en Phase 2)
- `var.eks_access_mode == "private" ? "_private" : ""` dans 3 modules (EKS, DB, Utils)
- Auto-decouverte des fichiers ASL dans les tests (rglob)

### Integration Points
- `modules/step-functions/eks/main.tf` : retirer _suffix, supprimer entries _private de la map
- `modules/step-functions/db/main.tf` : retirer _eks_suffix, supprimer entries _private
- `modules/step-functions/utils/main.tf` : retirer _eks_suffix, supprimer entry _private
- `variables.tf` (root) : supprimer variable eks_access_mode
- `.github/workflows/step-functions.yml` : mettre a jour CI matrix (fichiers supprimes)

### Current State Counts
| Paire | Module | Pub | Priv | Type |
|-------|--------|-----|------|------|
| manage_storage | EKS | 18 | 17 | Simple (GetEksClusterInfo) |
| scale_services | EKS | 9 | 8 | Simple |
| verify_and_restart | EKS | 6 | 5 | Simple |
| run_mysqldump | DB | 10 | 9 | Simple |
| run_archive_job | Utils | 9 | 14 | Complexe (job management) |
| run_mysqlimport | DB | 10 | 16 | Complexe (job management) |

</code_context>

<deferred>
## Deferred Ideas

None — discussion stayed within phase scope

</deferred>

---

*Phase: 03-consolidation*
*Context gathered: 2026-03-16*
