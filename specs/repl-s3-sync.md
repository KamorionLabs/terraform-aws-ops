# Spec: S3 Cross-Account Replication

## Identifiant
- **ID**: `repl-s3`
- **Domaine**: replication
- **Priorite**: P0 (migration / DR cross-compte)
- **Scope**: `GENERIC` (perimetre generique uniquement — le wiring client est hors scope)

## Objectif
Configurer et piloter la **replication S3 cross-account** sur un bucket source possede par
une stack externe, en miroir du pattern de replication EFS :
- Mise en place de la replication **live** (objets futurs) vers une ou plusieurs destinations
- **Backfill** des objets existants via un job S3 Batch Operations (`S3ReplicateObject`)
- Suivi de l'avancement du job de backfill jusqu'a completion
- Teardown symetrique d'une ou plusieurs destinations

**Note importante**: module **sans aucun Lambda** (contrainte dure D-09 / REPL-06) — uniquement
des integrations AWS SDK natives Step Functions. Chaque Task S3 assume le role du compte source
de maniere imperative via `Credentials.RoleArn.$` (jamais de role par defaut de la SFN sur le bucket).

## Architecture

Quatre Step Functions, toutes generiques (aucune ressource client embarquee) :

### Composant 1 : Setup (Step Function `setup_cross_account_replication`)
Met en place la replication live. **Single atomic read-merge-write** sur l'unique
`ReplicationConfiguration` du bucket (WR-01) : `GetBucketReplication` une fois, fusion de
**toutes** les `Destinations[]` en un seul pass JSONata, `PutBucketReplication` une fois.
- Chaque spoke est keye par un ID deterministe `repl-<DestAccountId>-<DestBucketBasename>` —
  les Rules des autres spokes sont preservees a la re-execution.
- Priorite stable par destination (CR-03) : reuse de la priorite existante, nouvelles destinations
  en `max(priorites)+1` (pas de collision au re-run).
- **Ne mute jamais** le versioning : il est valide (`GetBucketVersioning`) et echoue explicitement
  si non `Enabled`.

### Composant 2 : Run Batch (Step Function `run_batch_replication`)
Dispatch **un seul** job S3 Batch Operations (`S3ReplicateObject`) pour backfiller les objets
existants, en reutilisant la `ReplicationConfiguration` live posee par le setup (replique vers
**toutes** les destinations en un job, REPL-05).
- Precondition `GetBucketReplication` (WR-05) → fail-fast `NoReplicationConfigForBatch` si le setup
  n'a pas tourne.
- `S3JobManifestGenerator` (pas de precondition S3 Inventory) filtrant les objets en statut de
  replication `NONE`/`FAILED`.
- `ClientRequestToken` frais via `States.UUID()`, `ConfirmationRequired:false` (idempotence, T-07-04).
- Report emis **uniquement** si `ReportBucketArn` fourni (O1 : le module generique ne possede pas
  de bucket de report).

### Composant 3 : Check Batch (Step Function `check_batch_replication`)
Polle le job S3 Batch jusqu'a completion via `s3control:describeJob` dans une boucle Wait+Task+Choice,
consommant le `JobId` produit par `run_batch_replication`.
- Retry sur erreurs `describeJob` transitoires (WR-02) avant le Catch.
- `TimeoutSeconds` au niveau machine (WR-03) borne la boucle (un job `Suspended`/`Paused` echoue en
  `States.Timeout` plutot que de tourner jusqu'a la limite du service).

### Composant 4 : Delete (Step Function `delete_replication`)
Teardown symetrique (D-04) : lit la `ReplicationConfiguration`, filtre **hors** les Rules des
destinations ciblees (meme convention d'ID que le setup), puis re-`PutBucketReplication` des Rules
restantes, ou `DeleteBucketReplication` si plus aucune ne reste. Idempotent sur not-found.

**Contrat d'ordre** : `setup_cross_account_replication` DOIT tourner avant `run_batch_replication`
(impose par l'orchestrateur en Phase 8).

## Inputs

### Setup / Delete
```json
{
  "SourceBucket": "string - nom du bucket source",
  "SourceBucketArn": "arn:aws:s3:::<bucket>",
  "ReplicationRoleArn": "arn:aws:iam::<sourceAccount>:role/<replication-role> (role de replication live S3)",
  "SourceAccount": {
    "AccountId": "string - compte proprietaire du bucket source",
    "RoleArn": "arn:aws:iam::<sourceAccount>:role/<assume-role> (assume imperatif par chaque Task S3)"
  },
  "Destinations": [
    {
      "Bucket": "arn:aws:s3:::<dest-bucket> (ARN destination)",
      "DestAccountId": "string - compte destination",
      "Priority": 1,
      "StorageClass": "STANDARD (optionnel)"
    }
  ]
}
```

### Run Batch
```json
{
  "SourceBucket": "string",
  "SourceBucketArn": "arn:aws:s3:::<bucket>",
  "BatchReplicationRoleArn": "arn:aws:iam::<sourceAccount>:role/<batch-role>",
  "SourceAccount": { "AccountId": "string", "RoleArn": "arn:aws:iam::<sourceAccount>:role/<assume-role>" },
  "ReportBucketArn": "arn:aws:s3:::<report-bucket> (optionnel — si absent, Report.Enabled:false)"
}
```

### Check Batch
```json
{
  "SourceAccount": { "AccountId": "string", "RoleArn": "arn:aws:iam::<sourceAccount>:role/<assume-role>" },
  "BatchJob": { "JobId": "string - JobId produit par run_batch_replication" }
}
```

## Outputs

### Setup
```json
{
  "status": "Completed",
  "ModuleName": "setup_cross_account_replication",
  "ReplicationConfiguration": { "Role": "<replication-role-arn>", "Rules": ["..."] }
}
```

### Run Batch
```json
{ "status": "Completed", "ModuleName": "run_batch_replication", "JobId": "string" }
```

### Check Batch
```json
{ "status": "Completed | Failed", "ModuleName": "check_batch_replication", "JobStatus": "Complete | Failed | Cancelled" }
```

### Delete
```json
{ "status": "Completed", "ModuleName": "delete_replication", "RemainingRules": 0 }
```

## Appels AWS necessaires

### Cross-account (imperatif par Task)
```json
{
  "Credentials": { "RoleArn.$": "$.SourceAccount.RoleArn" }
}
```

### API Calls
| Service | API Call | SFN | Resource ARN pattern |
|---------|----------|-----|----------------------|
| S3 | `GetBucketVersioning` | setup | `arn:aws:s3:::*` |
| S3 | `GetBucketReplication` | setup / delete / run_batch | `arn:aws:s3:::*` |
| S3 | `PutBucketReplication` | setup / delete | `arn:aws:s3:::*` |
| S3 | `DeleteBucketReplication` | delete | `arn:aws:s3:::*` |
| S3 Control | `CreateJob` (`S3ReplicateObject`) | run_batch | `arn:aws:s3:<region>:<srcAccount>:job/*` |
| S3 Control | `DescribeJob` | check_batch | `arn:aws:s3:<region>:<srcAccount>:job/*` |

## Logique metier

### Setup
1. `GetBucketVersioning` (validate-only) → Fail `SourceVersioningNotEnabled` si != `Enabled`
2. `GetBucketReplication` (lit l'unique config existante)
3. Fusion JSONata de toutes les `Destinations[]` en Rules (ID deterministe, priorite stable)
4. `PutBucketReplication` unique (remplace toute la config)

### Run Batch
1. `GetBucketReplication` precondition → Fail `NoReplicationConfigForBatch` si absente
2. Choice sur `ReportBucketArn` (report on/off)
3. `s3control:CreateJob` (`S3ReplicateObject`, `S3JobManifestGenerator` filtre `NONE`/`FAILED`,
   `ClientRequestToken=States.UUID()`, `ConfirmationRequired:false`, `Priority:1`)

### Check Batch
1. Wait → `s3control:DescribeJob` (Retry transitoire)
2. Choice sur `Job.Status` : `Complete` → Succeed ; `Failed`/`Cancelled` → Fail ; sinon loop
3. `TimeoutSeconds` borne la boucle

### Delete
1. `GetBucketReplication`
2. Filtre JSONata excluant les IDs des destinations ciblees
3. Choice : Rules restantes → `PutBucketReplication` ; aucune → `DeleteBucketReplication`

## Conditions de succes (status: Completed)
- [x] Setup : versioning source `Enabled`, `PutBucketReplication` accepte, Rules mergees sans collision d'ID/priorite
- [x] Run Batch : `ReplicationConfiguration` live presente, `CreateJob` renvoie un `JobId`
- [x] Check Batch : `Job.Status = Complete`
- [x] Delete : config re-ecrite (Rules restantes) ou supprimee (aucune restante), idempotent sur not-found

## Conditions d'alerte (status: warning)
- [x] Check Batch : job en etat non-terminal prolonge (`Suspended`/`Paused`) — boucle de poll
- [x] Run Batch : aucune destination filtrable (`NONE`/`FAILED`) → job vide (no-op)

## Conditions d'erreur (status: critical / Fail)
- [x] Setup : versioning source != `Enabled` (`SourceVersioningNotEnabled`)
- [x] Run Batch : pas de `ReplicationConfiguration` live (`NoReplicationConfigForBatch`)
- [x] Check Batch : `Job.Status = Failed`/`Cancelled`, ou `States.Timeout` (boucle bornee WR-03)
- [x] AccessDenied sur assume-role source ou sur un appel S3/S3 Control

## Dependances
- Prerequis : versioning **Enabled** sur le bucket source ; bucket(s) destination existant(s)
- Services AWS : S3, S3 Control (S3 Batch Operations)
- Permissions IAM (cote `modules/source-account/`, optionnelles via `var.enable_s3`, default false) :
  - `s3:PutBucketReplication`, `s3:GetBucketReplication`, `s3:GetBucketVersioning`
  - `s3control:CreateJob`, `s3control:DescribeJob`
  - `iam:PassRole` scope sur le role de replication S3
  - Role de replication S3 cross-account (assumable, `s3_replication`)
- **Same-region uniquement** (Pitfall 5) : `AccountId`/`ExpectedBucketOwner` = compte source ;
  la SFN tourne dans la region source ; `SourceBucket` et `Report.Bucket` sont des **ARN**, pas des noms.

## Mapping Comptes AWS
Generique — le mapping concret (source/destinations par instance/env) est fourni a l'execution via
les inputs `SourceAccount` / `Destinations[]`. Aucun compte n'est code en dur dans le module
(perimetre generique, wiring client hors scope).

## Notes
- **IMPORTANT** : module **sans Lambda** — `S3ReplicateObject` + `S3JobManifestGenerator` couvrent
  le backfill sans S3 Inventory ni code custom.
- La replication live S3 est asynchrone ; le backfill des objets pre-existants passe **obligatoirement**
  par un job S3 Batch (`S3ReplicateObject`) — la replication live ne retraite pas l'existant.
- Convention d'ID de Rule `repl-<DestAccountId>-<DestBucketBasename>` : c'est elle qui rend
  setup/delete composables et idempotents sur un bucket multi-destinations.
- Tests : `tests/jsonata/s3-replication.test.js` (regressions semantiques setup/delete) +
  `tests/jsonata/validate-jsonata.js` (grammaire JSONata, gate CI).
- Reference miroir : `specs/repl-efs-sync.md` (replication EFS native — meme structure de spec).
