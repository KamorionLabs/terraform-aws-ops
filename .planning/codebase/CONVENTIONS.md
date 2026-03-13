# Coding Conventions

**Analysis Date:** 2026-03-13

## Overview

This codebase combines **Terraform infrastructure code** with **Python Lambda functions** and **AWS Step Functions (ASL JSON definitions)**. Each subsystem has distinct conventions.

---

## Python Conventions (Lambda Functions)

### File & Directory Naming

**Files:**
- Snake case: `compare_secrets_manager.py`, `get_efs_subpath.py`, `cross_region_rds_proxy.py`
- Lambda handlers named `{module_name}.py` where handler is `{module_name}.lambda_handler`
- Organized by function: `lambdas/{function-name}/{handler_file}.py`

**Example:** `lambdas/compare-secrets-manager/compare_secrets_manager.py`

### Function Naming

- **snake_case** for all functions: `parse_dynamodb_item()`, `extract_payload()`, `map_source_to_destination_name()`
- **Entry point:** `lambda_handler(event: dict, context: Any) -> dict`
- **Helper functions:** `extract_payload()`, `deserialize_dynamo_value()`, `compare_single_secret()`
- **Configuration functions:** `get_transformation_config()`, `match_pattern()`

### Type Hints

**Required for:**
- Function parameters: `def extract_payload(state_result: dict) -> Optional[dict]:`
- Return types: All functions have explicit return type annotations
- Complex types: Use `Optional[dict]`, `list[str]`, `dict[str, Any]`, `Any`

**Location:** `lambdas/compare-secrets-manager/compare_secrets_manager.py:274-830` (full type hints on all public functions)

### Import Organization

**Order (from `compare_secrets_manager.py:20-25`):**
1. Standard library imports (`json`, `logging`, `os`, `re`)
2. Standard library type/date imports (`datetime`, `timezone`)
3. Type hints (`typing` module)
4. AWS SDK imports (when used)

```python
import json
import logging
import os
import re
from datetime import datetime, timezone
from typing import Any, Optional
```

### Docstrings

**Module Level:**
- Triple-quoted docstring at file start
- Describe purpose, inputs, outputs, environment variables
- Example: `lambdas/compare-secrets-manager/compare_secrets_manager.py:1-18`

```python
"""
Compare AWS Secrets Manager States (Source vs Destination)
============================================================
Compares AWS Secrets Manager states between Source and Destination environments.

This Lambda:
- Receives parsed states from DynamoDB
- Applies name mapping
- Produces structured comparison report

Environment Variables:
- LOG_LEVEL: Logging level (default: INFO)
"""
```

**Function Level:**
- Docstring describing what the function does
- Signature with input/output format when complex
- Example: `lambdas/compare-secrets-manager/compare_secrets_manager.py:354-366`

```python
def map_source_to_destination_name(
    source_name: str,
    instance: str,
    environment: str,
    custom_mapping: dict = None,
) -> str:
    """
    Map Source secret name to Destination naming convention.

    Supports two types of mappings:
    1. App secrets: /rubix/{instance}-{env}/app/* -> /digital/{env}/app/mro-{instance}/*
    2. DB secrets:  /rubix/{instance}-{env}/{eks-cluster}/* -> /digital/{env}/infra/databases/{rds-cluster}/*
    """
```

### Error Handling

**Pattern: Exception Chaining with Logging**

Each exception type has dedicated handling with logging and structured response:

```python
try:
    # operation
except FileNotFoundError as e:
    logger.error(f"EFS mount not accessible: {e}")
    return {
        "statusCode": 404,
        "body": json.dumps({"error": "EFS_NOT_FOUND", "message": str(e)})
    }
except PermissionError as e:
    logger.error(f"Permission denied accessing EFS: {e}")
    return {
        "statusCode": 403,
        "body": json.dumps({"error": "PERMISSION_DENIED", "message": str(e)})
    }
except Exception as e:
    logger.error(f"Unexpected error: {e}")
    return {
        "statusCode": 500,
        "body": json.dumps({"error": "INTERNAL_ERROR", "message": str(e)})
    }
```

**Location:** `lambdas/get-efs-subpath/get_efs_subpath.py:81-107`

### Logging

**Framework:** Python standard `logging` module

**Setup Pattern (`lambdas/compare-secrets-manager/compare_secrets_manager.py:27-28`):**
```python
logger = logging.getLogger(__name__)
logger.setLevel(os.environ.get("LOG_LEVEL", "INFO"))
```

**Usage:**
- `logger.info()` for informational messages
- `logger.error()` for errors
- `logger.warning()` for warnings
- Format: Use f-strings with context-rich messages

**Examples:**
```python
logger.info(f"Comparing {len(source_secrets)} source vs {len(destination_secrets)} destination secrets")
logger.error(f"No backup restore directory found with prefix '{prefix}'")
```

### Comments

**When to Comment:**
- Complex algorithms or business logic
- Non-obvious name mappings (e.g., path transformation rules)
- Step-by-step explanations in multi-step operations
- Inline comments for complex regex or data transformations

**Examples from code:**
```python
# Handle parallel result format
if "SourceState" in state_result:
    state_result = state_result["SourceState"]

# Try item/itemData first (from parallel branch result)
item = state_result.get("item") or state_result.get("itemData")
```

### Module Design

**Organization Pattern:**

1. **Constants at top** (`lambdas/compare-secrets-manager/compare_secrets_manager.py:31-178`):
   ```python
   DEFAULT_PATH_MAPPING = { ... }
   DEFAULT_EXPECTED_TRANSFORMATIONS = { ... }
   ```

2. **Helper functions** (small, focused utilities):
   ```python
   def parse_dynamodb_item(raw_state: dict) -> Optional[dict]: ...
   def extract_payload(state_result: dict) -> Optional[dict]: ...
   ```

3. **Core business logic functions** (larger, domain-specific):
   ```python
   def compare_secrets(...) -> dict: ...
   def identify_issues(...) -> list: ...
   ```

4. **Lambda handler at bottom** (`lambda_handler()`):
   - Orchestrates helper functions
   - Handles top-level errors
   - Returns structured response

**Design Principle:** Single responsibility - each function does one thing well

### Function Size

**Typical pattern:**
- Small helpers: 15-40 lines
- Medium functions: 40-100 lines
- Large functions: 100-200 lines (complex comparisons, orchestration)

**Location examples:**
- `parse_value()` nested helper: 20 lines (`compare_secrets_manager.py:283-302`)
- `extract_payload()`: 45 lines (`compare_secrets_manager.py:307-351`)
- `lambda_handler()`: 140 lines (`compare_secrets_manager.py:690-829`)

### Variable Naming

- **snake_case** consistently: `source_secrets`, `destination_payload`, `backup_dirs`
- **Descriptive:** `only_source_keys`, `expected_diff_keys` (not `keys1`, `diff`)
- **Boolean prefixes:** `has_terminal`, `is_valid`, `should_retry` (not just `status`)
- **Collection plurals:** `secrets`, `issues`, `listeners` (plural for collections)

### Default Parameters

**Use None as sentinel:**
```python
def get_transformation_config(secret_name: str, custom_config: dict = None) -> dict:
    config = custom_config or DEFAULT_EXPECTED_TRANSFORMATIONS
```

Not `= {}` (mutable default anti-pattern)

---

## Terraform Conventions

### File Structure

**Standard files per module (from `modules/lambda-code/`):**
- `main.tf` - Primary resources
- `variables.tf` - Input variables
- `outputs.tf` - Output values
- `versions.tf` - Provider version constraints

**Location:** `modules/{module-name}/main.tf`, `modules/{module-name}/variables.tf`, etc.

### Naming Patterns

**Variables:**
- snake_case: `prefix`, `tags`, `enable_logging`, `log_retention_days`
- Descriptive: `orchestrator_role_arn` (not `role`)

**Resources:**
- Kebab-case in name attributes: `lambda-code`, `refresh-orchestrator`
- Snake_case in Terraform resource IDs: `aws_s3_bucket`, `aws_sfn_state_machine`

**Locals:**
- Computed values in `locals {}` block for readability
- Example: `lambdas/modules/lambda-code/main.tf:10-43`
  ```hcl
  locals {
    account_id = data.aws_caller_identity.current.account_id
    region     = data.aws_region.current.id
    bucket_name = var.create_bucket ? (
      var.bucket_name != null ? var.bucket_name : "${var.prefix}-lambda-code-${local.account_id}"
    ) : var.existing_bucket_name
  }
  ```

### Comments & Section Organization

**Pattern:** Section headers with dashes for visual separation

From `main.tf:1-9`:
```hcl
# terraform-aws-refresh
# Cross-account database refresh orchestrator using AWS Step Functions
#
# This module deploys Step Functions for orchestrating database refresh
# operations across multiple AWS accounts (source production -> destination non-prod)

# -----------------------------------------------------------------------------
# Lambda Code Module - Packages and uploads Lambda code to S3
# -----------------------------------------------------------------------------
```

**Sections:**
- Feature comments above resource groups
- 80-char dashed dividers
- One blank line between sections

**Within resources:**
```hcl
# Note: tags passed separately to bucket only (S3 objects limited to 10 tags)
tags = var.tags
```

### Formatting

**Rules enforced by CI:**
- `terraform fmt` checks all `.tf` files
- Consistent indentation (2 spaces)
- Standardized attribute ordering

**From `.github/workflows/terraform.yml:31-32`:**
```yaml
- name: Terraform Format Check
  run: terraform fmt -check -recursive
```

### Variable Declarations

**Pattern with descriptions:**
```hcl
variable "prefix" {
  description = "Prefix for all resource names"
  type        = string
  default     = "refresh"
}
```

**Requirements:**
- `description` field required
- `type` field required (use `string`, `bool`, `number`, `list(string)`, `map(string)`, etc.)
- `default` optional (omit for required variables)

**From `variables.tf:5-9, 21-29`:**
```hcl
variable "source_role_arns" {
  description = "List of IAM role ARNs in source accounts that the orchestrator can assume"
  type        = list(string)
}
```

### Module References

**Pattern (from `main.tf:27-37`):**
```hcl
module "step_functions_db" {
  source = "./modules/step-functions/db"

  prefix                = var.prefix
  tags                  = var.tags
  orchestrator_role_arn = module.iam.orchestrator_role_arn

  enable_logging      = var.enable_step_functions_logging
  log_retention_days  = var.log_retention_days
  enable_xray_tracing = var.enable_xray_tracing
}
```

**Convention:**
- One blank line before module block
- Related variables grouped together
- Pass variables explicitly (no implicit forwarding)

### Output Descriptions

**All outputs must have descriptions (from `outputs.tf:5-8`):**
```hcl
output "orchestrator_role_arn" {
  description = "ARN of the orchestrator IAM role"
  value       = module.iam.orchestrator_role_arn
}
```

---

## AWS Step Functions (ASL JSON)

### File Naming

- Kebab-case: `restore_cluster.asl.json`, `delete_cluster.asl.json`
- Location: `modules/step-functions/{db,efs,eks,utils}/` or `step-functions/` directory

### Structure

**Required fields in all definitions:**
- `"Comment"` - English description
- `"StartAt"` - Name of starting state
- `"States"` - Object containing all state definitions

### State Naming

- PascalCase: `RestoreCluster`, `DeleteDatabase`, `CheckStatus`
- Descriptive action: `WaitForSnapshot`, `ListStateMachines`
- Generic: `Pass`, `Choice`, `Fail`, `Succeed` (built-in types)

### Organization

**From `modules/step-functions/db/main.tf:9-39`:**
- Group related operations: Core Operations, Instance Management, Snapshot Management, Secrets Management, S3 & SQL Operations
- Use descriptive keys in locals map
- Reference via for_each loop for resource generation

---

## Code Quality Standards

### Type Checking (Python)

**Tool:** `mypy` (in `requirements-dev.txt`)

**Applied to:** Lambda functions (though not enforced in CI yet)

### Formatting (Terraform)

**Tool:** `terraform fmt` (enforced in CI)

**Command:** `terraform fmt -check -recursive`

### Linting

**Terraform:**
- Tool: `tflint` (GitHub Actions)
- Command: `tflint --recursive --format=compact`
- Config: `.tflint.hcl` (if present)

**Security:**
- Trivy vulnerability scanner (config/IaC scanning)
- Checkov (IaC policy as code)

### Testing

**See TESTING.md for details**

---

## Key Principles

1. **Consistency over creativity** - Follow established patterns
2. **Readability first** - Clear variable/function names, proper formatting
3. **Documentation** - Docstrings, comments, section headers
4. **Type safety** - Use type hints in Python, explicit types in Terraform
5. **Error clarity** - Specific exceptions, context in logging
6. **DRY** - Don't repeat: use locals in Terraform, helper functions in Python
