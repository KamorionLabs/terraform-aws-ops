# Phase 5: Sync Engine - Research

**Researched:** 2026-03-17
**Domain:** AWS Secrets Manager / SSM Parameter Store cross-account sync, Python Lambda, Step Functions Map error handling
**Confidence:** HIGH

## Summary

Phase 5 transforms the sync_config_items Lambda stub (Phase 4) into a fully functional sync engine. The Lambda must implement: cross-account fetch via STS AssumeRole, path mapping with glob wildcards and `{name}` placeholder expansion, JSON value transforms (replace/skip), merge mode for preserving destination-only keys, auto-creation of missing secrets/parameters, and recursive SSM traversal. The existing project has reusable patterns for cross-account access (`fetch_secrets.py`), path mapping (`compare_secrets_manager.py`), and glob matching (`compare_secrets_manager.py`).

A critical finding is the current ASL design: `ItemFailed` is a `Fail` state inside the Map iterator, which causes the entire Map state to fail on the first error. The CONTEXT.md requires "continue + rapport" semantics. The solution is two-fold: (1) the Lambda must handle ALL business errors internally and return `status: "error"` results, and (2) the ASL `ItemFailed` state should be changed from `Fail` to `Pass` (with `End: true`) so infrastructure-level Catch errors also produce a result instead of aborting the Map. The SFN PrepareOutput state then aggregates all results including errors.

**Primary recommendation:** Implement the Lambda as a set of well-separated internal functions (fetch, transform, write) with comprehensive error handling that NEVER raises -- all errors are caught and returned as structured `status: "error"` results. Modify the ASL `ItemFailed` to be a `Pass` state. Use `fnmatch.fnmatch` for glob matching (its `*` already matches `/` which is the desired behavior for path wildcards).

<user_constraints>

## User Constraints (from CONTEXT.md)

### Locked Decisions
- **MergeMode=true**: destination gagne pour les cles communes non couvertes par les Transforms. La source n'ecrase que les cles avec un Transform explicite. Les cles destination-only sont preservees.
- **MergeMode=false (ou absent)**: ecrasement total du secret destination par la valeur source (apres Transforms). Les cles destination-only disparaissent.
- **Secrets non-JSON** (valeur string simple): les Transforms (replace from/to) s'appliquent sur la valeur brute. En MergeMode=true : si le secret destination existe, garder la valeur destination ; si inexistant, copier la source (avec Transforms appliques).
- **Continue + rapport**: la SFN continue de sync les autres items quand un item echoue. Les erreurs sont collectees et retournees dans le resultat final avec status 'error' par item.
- **Output SFN**: Status global "complete" (tout ok), "partial" (certains echecs), "failed" (tous en erreur). Avec compteurs ItemsProcessed/ItemsSynced/ItemsFailed + Results[] detaille.
- **Glob avec `**` support**: support de patterns glob (*, **) pour matcher les secrets/params source. Pas juste un prefix filter simple.
- **`{name}` placeholder**: le {name} dans le path destination est la partie du path source apres le prefix avant le wildcard. Simple et intuitif.

### Claude's Discretion
- Implementation du glob matching (fnmatch Python, ou custom)
- Pattern STS AssumeRole (copier de fetch_secrets.py existant)
- Structure interne de la Lambda (fonctions helper, separation fetch/transform/write)
- Gestion du retry sur les API calls AWS (exponential backoff via botocore)
- Tests unitaires : scope et couverture

### Deferred Ideas (OUT OF SCOPE)
None -- discussion stayed within phase scope

</user_constraints>

<phase_requirements>

## Phase Requirements

| ID | Description | Research Support |
|----|-------------|-----------------|
| SYNC-02 | Fetch cross-account des valeurs source via IAM role assumption (sts:AssumeRole) avec support multi-region | Copy `get_cross_account_client()` from `fetch_secrets.py` -- exact pattern with STS DurationSeconds=900 |
| SYNC-03 | Path mapping configurable -- renommage des chemins source vers destination avec wildcards | Use `fnmatch.fnmatch` for glob matching + prefix extraction for `{name}` placeholder |
| SYNC-04 | Transformations de valeurs dans les secrets JSON -- remplacement de valeurs par cle (regex ou literal) | Apply transforms per-key on parsed JSON dict; for non-JSON, apply replace on raw string value |
| SYNC-05 | Creation automatique du secret/parametre cote destination si inexistant, mise a jour si existant | SM: `create_secret`/`put_secret_value`; SSM: `put_parameter(Overwrite=True)` with try/except flow |
| SYNC-06 | Merge mode -- preserver les cles destination-only lors de la mise a jour d'un secret JSON | Fetch existing destination value, merge dicts with source transforms winning, preserve dest-only keys |
| SYNC-07 | Recursive traversal pour SSM -- copier tous les parametres sous un path donne avec mapping | `get_parameters_by_path(Recursive=True)` + path mapping applied per returned parameter |
| INFRA-02 | Tests ASL de validation pour la nouvelle SFN (auto-decouverte via rglob existant) | ASL tests already auto-discover via `PROJECT_ROOT.rglob("*.asl.json")` -- ASL changes are covered automatically |

</phase_requirements>

## Standard Stack

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| boto3 | 1.42.67 (Lambda runtime) | AWS SDK -- SM, SSM, STS API calls | Standard AWS Lambda runtime, no install needed |
| botocore | 1.42.67 | ClientError handling, retry config | Comes with boto3, used for error types |
| fnmatch | stdlib | Glob pattern matching for path wildcards | Python stdlib, `*` matches `/` which is correct for secret paths |
| json | stdlib | Parse/serialize secret JSON values | Standard library |
| re | stdlib | Regex replace in transforms | Standard library |
| logging | stdlib | Structured logging with LOG_LEVEL | Project pattern |

### Supporting
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| pytest | 9.0.2 | Unit testing | Already installed, project standard |
| unittest.mock | stdlib | Mock boto3 clients in tests | Standard approach, no moto in project |

### Alternatives Considered
| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| fnmatch | Custom regex conversion | fnmatch is simpler and sufficient -- `*` matching `/` is exactly what we want for path wildcards |
| unittest.mock | moto | moto not installed in project; mock is lighter and sufficient for unit tests that verify Lambda logic |
| botocore retry | tenacity | botocore built-in retry config is simpler; SFN Retry already handles Lambda-level retries |

## Architecture Patterns

### Lambda Internal Structure
```
lambdas/sync-config-items/
└── sync_config_items.py     # Single file (project pattern: inline Lambda)
    ├── get_cross_account_client()     # STS AssumeRole (copy from fetch_secrets.py)
    ├── list_matching_secrets()        # SM: list_secrets + fnmatch filter
    ├── list_matching_parameters()     # SSM: get_parameters_by_path(Recursive=True)
    ├── apply_transforms()             # JSON key transforms + string replace
    ├── merge_values()                 # MergeMode logic for JSON secrets
    ├── sync_secret()                  # SM: fetch → transform → merge → write
    ├── sync_parameter()               # SSM: fetch → transform → write
    ├── resolve_wildcard_items()       # Expand wildcard SourcePath to concrete items
    ├── map_destination_path()         # {name} placeholder resolution
    └── lambda_handler()               # Entry point, error-safe wrapper
```

### Pattern 1: Error-Safe Lambda (Continue + Rapport)
**What:** Lambda catches ALL business exceptions and returns structured error results instead of raising.
**When to use:** Always -- the SFN Map must continue processing other items when one fails.
**Example:**
```python
# Source: CONTEXT.md decision + ASL analysis
def lambda_handler(event: dict, context: Any) -> dict:
    try:
        item = event.get("Item", {})
        item_type = item.get("Type", "unknown")

        if item_type == "SecretsManager":
            results = sync_secret(event)
        elif item_type == "SSMParameter":
            results = sync_parameter(event)
        else:
            return error_result(item, f"Unsupported type: {item_type}")

        return {
            "statusCode": 200,
            "result": results  # May be a single result or list (wildcard)
        }
    except Exception as e:
        logger.exception("Unhandled error syncing item")
        return {
            "statusCode": 200,  # 200 so SFN treats as success
            "result": {
                "status": "error",
                "source": item.get("SourcePath", ""),
                "destination": item.get("DestinationPath", ""),
                "type": item.get("Type", "unknown"),
                "message": str(e),
            }
        }
```

### Pattern 2: Wildcard Expansion Inside Lambda
**What:** When SourcePath contains `*` or `**`, the Lambda lists matching secrets/params and syncs each one, returning an aggregated result.
**When to use:** When Item.SourcePath contains glob wildcards.
**Example:**
```python
# Source: CONTEXT.md decision on glob matching
import fnmatch

def resolve_wildcard_items(source_path: str, dest_pattern: str,
                           source_client, item_type: str) -> list[tuple[str, str]]:
    """Expand wildcard SourcePath to concrete (source, destination) pairs."""
    if '*' not in source_path:
        return [(source_path, dest_pattern)]

    # Find prefix before first wildcard
    wildcard_idx = source_path.index('*')
    prefix = source_path[:wildcard_idx]

    if item_type == "SecretsManager":
        all_names = list_all_secret_names(source_client)
    else:
        all_names = list_all_parameter_names(source_client, prefix)

    pairs = []
    for name in all_names:
        if fnmatch.fnmatch(name, source_path):
            # Extract {name} = part after prefix
            name_part = name[len(prefix):]
            dest_path = dest_pattern.replace("{name}", name_part)
            pairs.append((name, dest_path))

    return pairs
```

### Pattern 3: Merge Mode for JSON Secrets
**What:** When MergeMode=true, fetch destination secret, merge with transformed source, preserve destination-only keys.
**When to use:** When Item.MergeMode is true and secrets are JSON.
**Example:**
```python
# Source: CONTEXT.md merge/conflict decisions
def merge_values(source_value: dict, destination_value: dict,
                 transforms: dict) -> dict:
    """Merge source into destination, preserving dest-only keys.

    - Keys with explicit Transform: use transformed source value
    - Common keys without Transform: keep destination value
    - Source-only keys: copy from source
    - Destination-only keys: preserve
    """
    result = dict(destination_value)  # Start with destination

    for key, value in source_value.items():
        if key in transforms:
            if transforms[key].get("skip"):
                continue  # Don't touch this key
            # Apply transform and use source value
            result[key] = apply_key_transform(value, transforms[key])
        elif key not in destination_value:
            # Source-only key: copy
            result[key] = value
        # else: common key without transform → keep destination (already in result)

    return result
```

### Pattern 4: STS AssumeRole (Copy from fetch_secrets.py)
**What:** Cross-account access via STS AssumeRole with 15-minute session.
**When to use:** For all cross-account API calls (source fetch + destination write).
**Example:**
```python
# Source: lambdas/fetch-secrets/fetch_secrets.py lines 38-76
def get_cross_account_client(
    service: str, role_arn: str, region: str,
    session_name: str = "SyncConfigItems",
) -> Any:
    sts_client = boto3.client("sts")
    try:
        assumed_role = sts_client.assume_role(
            RoleArn=role_arn,
            RoleSessionName=session_name,
            DurationSeconds=900,
        )
        credentials = assumed_role["Credentials"]
        return boto3.client(
            service,
            region_name=region,
            aws_access_key_id=credentials["AccessKeyId"],
            aws_secret_access_key=credentials["SecretAccessKey"],
            aws_session_token=credentials["SessionToken"],
        )
    except ClientError as e:
        logger.error(f"Failed to assume role {role_arn}: {e}")
        raise
```

### Anti-Patterns to Avoid
- **Raising exceptions for business errors:** The Lambda must NEVER raise for a sync failure (e.g., source secret not found, permission denied). Always return `status: "error"`. Only infrastructure-level failures (Lambda timeout, OOM) should cause the SFN Catch to fire.
- **Hardcoded client names:** SYNC-08 mandates no Rubix/Bene/Homebox references. The test `test_no_hardcoded_client_names` enforces this.
- **Concurrent PutSecretValue calls:** SM limits PutSecretValue to once every 10 minutes per secret. MaxConcurrency=1 in the ASL helps, but be aware of this limit.
- **Ignoring SSM parameter Type:** When creating SSM parameters, preserve the original Type (String/SecureString/StringList) from the source.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Glob matching | Custom regex builder | `fnmatch.fnmatch` (stdlib) | Handles `*`, `?`, `[seq]` patterns correctly; `*` matches `/` which is desired |
| Retry logic | Custom exponential backoff | botocore built-in + SFN Retry | SFN Retry handles Lambda.ServiceException/TooManyRequestsException with backoff already configured in ASL |
| Cross-account auth | Custom STS flow | Copy exact `get_cross_account_client()` from `fetch_secrets.py` | Battle-tested pattern already in production |
| JSON deep merge | Custom recursive merge | Simple dict merge (single-level JSON secrets) | Secret JSON values are flat key-value dicts, not deeply nested |

**Key insight:** The AWS APIs handle most of the complexity (pagination, recursive SSM traversal, secret versioning). The Lambda's job is orchestration logic: glue the APIs together with transform/merge/path-mapping logic.

## Common Pitfalls

### Pitfall 1: Map Fail State Aborts Entire Workflow
**What goes wrong:** Current ASL `ItemFailed` is a `Fail` state inside Map iterator. One item failure stops all remaining items.
**Why it happens:** `Fail` states inside Map iterators cause the Map state to fail, not just the iteration.
**How to avoid:** Change `ItemFailed` from `Fail` to `Pass` with `End: true` and include error info in the output. Additionally, ensure the Lambda catches ALL business errors internally.
**Warning signs:** First item error causes no subsequent items to process.

### Pitfall 2: SM ListSecrets Does Not Support Path Prefix Filtering
**What goes wrong:** Assuming `list_secrets(Filters=[{Key: 'name', Values: ['/prefix/*']}])` does glob matching. It does NOT -- the name filter does exact prefix match, not glob.
**Why it happens:** SM ListSecrets `Filters` key `name` does a `contains` match, not glob.
**How to avoid:** List ALL secrets via paginator, then filter client-side with `fnmatch`. For SSM, `get_parameters_by_path` with `Recursive=True` handles prefix listing natively.
**Warning signs:** Missing secrets in wildcard matches.

### Pitfall 3: SSM PutParameter Requires Type for New Parameters
**What goes wrong:** Calling `put_parameter(Name=..., Value=..., Overwrite=True)` without `Type` for a new parameter fails with `InvalidParameterException`.
**Why it happens:** `Type` is required for creation, optional for update (Overwrite=True preserves existing Type).
**How to avoid:** When creating new SSM parameters, always include `Type` from the source parameter. Use try/except flow: try `put_parameter(Overwrite=True)`, if `ParameterNotFound` then call `put_parameter` with explicit `Type`.
**Warning signs:** `InvalidParameterException` on new parameter creation.

### Pitfall 4: SM CreateSecret vs PutSecretValue
**What goes wrong:** Using `create_secret` when secret already exists throws `ResourceExistsException`. Using `put_secret_value` when secret doesn't exist throws `ResourceNotFoundException`.
**Why it happens:** SM has separate create and update APIs unlike SSM's unified `put_parameter`.
**How to avoid:** Strategy: try `put_secret_value` first, catch `ResourceNotFoundException`, then `create_secret`. This is the optimal path since most syncs are updates.
**Warning signs:** `ResourceExistsException` or `ResourceNotFoundException`.

### Pitfall 5: Non-JSON Secret Values with MergeMode
**What goes wrong:** Trying to parse a plain string secret as JSON for merge mode.
**Why it happens:** Not all secrets are JSON -- some are plain strings (API keys, tokens).
**How to avoid:** Try `json.loads()`, catch `JSONDecodeError`. For non-JSON secrets with MergeMode=true: keep destination value if it exists, otherwise copy source (with transforms applied). CONTEXT.md explicitly defines this behavior.
**Warning signs:** `JSONDecodeError` on plain string secrets.

### Pitfall 6: SM PutSecretValue Rate Limiting
**What goes wrong:** Calling PutSecretValue more than once every 10 minutes per secret creates excess versions.
**Why it happens:** SM creates a new version for every PutSecretValue call and only cleans up versions older than certain thresholds.
**How to avoid:** MaxConcurrency=1 in ASL helps serialize calls. For large batch syncs, consider comparing source/destination hashes before writing to skip unchanged secrets.
**Warning signs:** Secret version count growing, eventual version quota errors.

## Code Examples

### SM Fetch + Create/Update Pattern
```python
# Source: boto3 SM API analysis + fetch_secrets.py pattern
def write_secret(dest_client, name: str, value: str) -> str:
    """Write secret to destination. Returns 'created' or 'updated'."""
    try:
        dest_client.put_secret_value(SecretId=name, SecretString=value)
        return "updated"
    except dest_client.exceptions.ResourceNotFoundException:
        dest_client.create_secret(Name=name, SecretString=value)
        return "created"
```

### SSM Recursive Fetch Pattern
```python
# Source: boto3 SSM API GetParametersByPath signature
def fetch_parameters_by_path(ssm_client, path: str) -> list[dict]:
    """Fetch all SSM parameters under a path recursively."""
    params = []
    paginator = ssm_client.get_paginator("get_parameters_by_path")
    for page in paginator.paginate(
        Path=path,
        Recursive=True,
        WithDecryption=True,
    ):
        params.extend(page.get("Parameters", []))
    return params
```

### SSM Write with Type Preservation
```python
# Source: boto3 SSM PutParameter API analysis
def write_parameter(dest_client, name: str, value: str,
                    param_type: str = "String") -> str:
    """Write SSM parameter. Returns 'created' or 'updated'."""
    try:
        dest_client.put_parameter(
            Name=name,
            Value=value,
            Type=param_type,
            Overwrite=True,
        )
        # Check if it was create or update by checking if param existed
        return "synced"
    except dest_client.exceptions.ParameterNotFound:
        # Should not happen with Overwrite=True for existing params
        # But handle gracefully
        dest_client.put_parameter(
            Name=name,
            Value=value,
            Type=param_type,
        )
        return "created"
```

### Transform Application
```python
# Source: CONTEXT.md transform decisions
def apply_transforms(value: str, transforms: dict) -> str:
    """Apply transforms to a secret/parameter value.

    For JSON values: transforms apply per-key.
    For string values: transforms apply on the raw value.
    """
    try:
        data = json.loads(value)
        if isinstance(data, dict):
            return apply_json_transforms(data, transforms)
    except (json.JSONDecodeError, TypeError):
        pass

    # Non-JSON: apply string-level transforms
    return apply_string_transforms(value, transforms)


def apply_json_transforms(data: dict, transforms: dict) -> str:
    """Apply per-key transforms to a JSON secret."""
    result = dict(data)
    for key, transform in transforms.items():
        if transform.get("skip"):
            continue  # Key marked as skip -- don't include in sync
        if key in result and "replace" in transform:
            for replacement in transform["replace"]:
                val = str(result[key])
                result[key] = val.replace(
                    replacement["from"], replacement["to"]
                )
    return json.dumps(result)


def apply_string_transforms(value: str, transforms: dict) -> str:
    """Apply transforms on raw string value."""
    result = value
    for key, transform in transforms.items():
        if "replace" in transform:
            for replacement in transform["replace"]:
                result = result.replace(
                    replacement["from"], replacement["to"]
                )
    return result
```

### ASL ItemFailed Fix (Fail -> Pass)
```json
{
  "ItemFailed": {
    "Type": "Pass",
    "Parameters": {
      "statusCode": 200,
      "result": {
        "status": "error",
        "source.$": "$.Item.SourcePath",
        "destination.$": "$.Item.DestinationPath",
        "type.$": "$.Item.Type",
        "message.$": "$.Error"
      }
    },
    "End": true
  }
}
```

**Note:** The Catch block provides `$.Error` and `$.Cause` in the input to the fallback state. However, `$.Item` may not be accessible in the error path because Catch replaces the state input with error data. The actual implementation should use `ResultPath` on the Catch block to preserve the original input alongside the error. Alternative: since the Lambda handles all business errors, `ItemFailed` is only reached for infrastructure errors (Lambda crash/timeout) where we may not have the original paths. A simpler approach:

```json
"Catch": [
  {
    "ErrorEquals": ["States.ALL"],
    "ResultPath": "$.ErrorInfo",
    "Next": "ItemFailed"
  }
],

"ItemFailed": {
  "Type": "Pass",
  "Parameters": {
    "statusCode": 200,
    "result": {
      "status": "error",
      "source.$": "$.Item.SourcePath",
      "destination.$": "$.Item.DestinationPath",
      "type.$": "$.Item.Type",
      "message": "Lambda execution failed (infrastructure error)"
    }
  },
  "End": true
}
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| SM GetSecretValue 5K RPS | SM GetSecretValue 10K RPS | March 2025 | Higher throughput for listing/fetching |
| SM DescribeSecret lower limit | SM DescribeSecret 40K RPS | March 2025 | No throttling concern for batch describe |
| SSM default 40 TPS | SSM 1000+ TPS (configurable) | July 2023 | Adequate for sync operations |

**Deprecated/outdated:**
- None relevant to this phase.

## Open Questions

1. **MergeMode field location in Item schema**
   - What we know: CONTEXT.md defines MergeMode behavior clearly
   - What's unclear: Whether MergeMode is a per-Item field or a top-level ConfigSync field
   - Recommendation: Add as per-Item field `Item.MergeMode: true/false` (default false) since different items may need different merge behavior

2. **Wildcard result aggregation format**
   - What we know: Lambda returns one result per Map iteration; wildcards expand to multiple secrets
   - What's unclear: Whether result should be a single summary or array of sub-results
   - Recommendation: Return a single result with `"status": "synced"/"partial"/"error"`, `"items_synced": N`, `"items_failed": N`, and `"details": [...]` for wildcard items. For non-wildcard items, return the simple single-result format.

3. **Skip transform semantics**
   - What we know: `skip: true` means "ne pas toucher cette cle lors du sync"
   - What's unclear: In non-MergeMode, does skip mean "exclude from the synced value entirely" or "keep the source value as-is"?
   - Recommendation: `skip: true` means "exclude this key from the synced value" (remove it from the result). In MergeMode, the destination value is preserved for skipped keys.

## Validation Architecture

### Test Framework
| Property | Value |
|----------|-------|
| Framework | pytest 9.0.2 |
| Config file | none (uses pytest defaults + conftest.py markers) |
| Quick run command | `python3 -m pytest tests/test_sync_config_items.py -x -v` |
| Full suite command | `python3 -m pytest tests/ -x -v --ignore=tests/test_stepfunctions_local.py` |

### Phase Requirements -> Test Map
| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| SYNC-02 | Cross-account fetch via STS AssumeRole | unit (mock STS) | `python3 -m pytest tests/test_sync_config_items.py::TestCrossAccountFetch -x` | Needs new tests |
| SYNC-03 | Path mapping with glob wildcards + {name} | unit | `python3 -m pytest tests/test_sync_config_items.py::TestPathMapping -x` | Needs new tests |
| SYNC-04 | JSON value transforms (replace/skip) | unit | `python3 -m pytest tests/test_sync_config_items.py::TestTransforms -x` | Needs new tests |
| SYNC-05 | Auto-create dest secret/param if missing | unit (mock SM/SSM) | `python3 -m pytest tests/test_sync_config_items.py::TestAutoCreate -x` | Needs new tests |
| SYNC-06 | Merge mode preserves dest-only keys | unit | `python3 -m pytest tests/test_sync_config_items.py::TestMergeMode -x` | Needs new tests |
| SYNC-07 | Recursive SSM traversal | unit (mock SSM) | `python3 -m pytest tests/test_sync_config_items.py::TestSSMRecursive -x` | Needs new tests |
| INFRA-02 | ASL validation for sync SFN | unit | `python3 -m pytest tests/test_asl_validation.py -k sync -x` | Already covered (auto-discovery) |

### Sampling Rate
- **Per task commit:** `python3 -m pytest tests/test_sync_config_items.py -x -v`
- **Per wave merge:** `python3 -m pytest tests/ -x -v --ignore=tests/test_stepfunctions_local.py`
- **Phase gate:** Full suite green before `/gsd:verify-work`

### Wave 0 Gaps
- [ ] `tests/test_sync_config_items.py` -- needs complete rewrite: replace stub tests with real behavior tests covering SYNC-02 through SYNC-07
- [ ] No new test files needed -- extend existing `test_sync_config_items.py`
- [ ] No framework install needed -- pytest 9.0.2 + unittest.mock already available
- [ ] ASL validation (INFRA-02) already covered by auto-discovery in `test_asl_validation.py`

## Sources

### Primary (HIGH confidence)
- `lambdas/fetch-secrets/fetch_secrets.py` -- `get_cross_account_client()` pattern, lines 38-76
- `lambdas/compare-secrets-manager/compare_secrets_manager.py` -- path mapping, `match_pattern()`, lines 354-402
- `lambdas/sync-config-items/sync_config_items.py` -- stub with I/O contract
- `modules/step-functions/sync/sync_config_items.asl.json` -- ASL with Map/Choice pattern
- `tests/test_asl_validation.py` -- auto-discovery via `rglob("*.asl.json")`
- boto3 1.42.67 / botocore 1.42.67 API model introspection (SM + SSM operation shapes, error types)
- Python 3.12 `fnmatch` stdlib behavior verification (interactive tests)

### Secondary (MEDIUM confidence)
- [AWS Secrets Manager quotas](https://docs.aws.amazon.com/secretsmanager/latest/userguide/reference_limits.html) -- rate limits, PutSecretValue 10-minute guidance
- [AWS SM increases API limits (March 2025)](https://aws.amazon.com/about-aws/whats-new/2025/03/aws-secrets-manager-increases-api-requests-seconds/) -- 10K RPS GetSecretValue, 40K DescribeSecret
- [AWS SSM Parameter Store throughput](https://docs.aws.amazon.com/systems-manager/latest/userguide/parameter-store-throughput.html) -- 1000 TPS default, configurable
- [AWS SFN error handling docs](https://docs.aws.amazon.com/step-functions/latest/dg/concepts-error-handling.html) -- Fail state inside Map behavior, Catch semantics
- [AWS SFN Fail state docs](https://docs.aws.amazon.com/step-functions/latest/dg/state-fail.html) -- Fail caught by Catch block

### Tertiary (LOW confidence)
- None -- all findings verified with primary or secondary sources.

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH -- all stdlib + boto3 (Lambda runtime), verified API signatures via botocore models
- Architecture: HIGH -- patterns copied from existing project Lambdas, ASL behavior verified
- Pitfalls: HIGH -- API error shapes verified via botocore, SM rate limits from AWS docs, ASL Fail behavior confirmed

**Research date:** 2026-03-17
**Valid until:** 2026-04-17 (30 days -- stable AWS APIs, no breaking changes expected)
