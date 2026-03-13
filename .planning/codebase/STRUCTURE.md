# Codebase Structure

## Top-Level Layout

```
terraform-aws-ops/
├── main.tf                    # Root module entry point
├── variables.tf               # Root input variables
├── outputs.tf                 # Root outputs
├── versions.tf                # Provider version constraints
├── modules/                   # Terraform sub-modules
├── lambdas/                   # Lambda function source code (Python)
├── step-functions/            # ASL state machine definitions
├── tests/                     # Pytest test suite
├── scripts/                   # Utility scripts
├── specs/                     # Specification documents
├── examples/                  # Usage examples (simple, complete)
├── docker/                    # Docker configurations (mysql-s3)
├── docs/                      # Documentation
├── .github/                   # GitHub Actions workflows
├── requirements-dev.txt       # Python dev dependencies
└── PROJECT_STATUS.md          # Project status tracking
```

## Modules (`modules/`)

Terraform sub-modules organized by functional domain:

| Module | Purpose |
|--------|---------|
| `modules/orchestrator/` | Main Step Functions orchestrator |
| `modules/core/` | Core infrastructure resources |
| `modules/checkers/` | Readiness checker state machines |
| `modules/compare/` | Comparison state machines |
| `modules/source-account/` | Source AWS account resources |
| `modules/destination-account/` | Destination AWS account resources |
| `modules/iam/` | IAM roles and policies |
| `modules/lambda-code/` | Lambda function packaging and deployment |
| `modules/step-functions/` | Step Functions sub-modules |

### Step Functions Sub-Modules (`modules/step-functions/`)

| Sub-Module | Purpose |
|------------|---------|
| `modules/step-functions/utils/` | Utility state machines (prepare, validate, cleanup, archive, tag) |
| `modules/step-functions/audit/` | Audit state machines |

## Lambda Functions (`lambdas/`)

Each Lambda has its own directory with a single Python file following the pattern `lambdas/<name>/<snake_case_name>.py`.

### Categories

**Comparators** (source vs destination comparison):
- `compare-alb/`, `compare-cloudfront/`, `compare-dns/`, `compare-ingress/`
- `compare-pods/`, `compare-pvc/`, `compare-rds/`, `compare-secrets/`
- `compare-secrets-manager/`, `compare-security-groups/`, `compare-services/`, `compare-ssm/`

**Processors** (data fetching and processing):
- `process-alb/`, `process-ec2-nodes/`, `process-efs-replication/`, `process-ingress/`
- `process-nodes/`, `process-pods/`, `process-pvc/`, `process-rds/`
- `process-secrets/`, `process-services/`, `process-tgw/`

**Utilities**:
- `fetch-cloudfront/`, `fetch-secrets/`, `fetch-ssm/`
- `resolve-dns/`, `save-state/`, `check-flag-file/`
- `filter-alb-by-tags/`, `get-efs-subpath/`
- `run-scripts-mysql/`, `cross-region-rds-proxy/`
- `k8s-proxy/`, `analyze-security-groups/`

**Checkers**:
- `infra-checker/`, `replication-checker/`, `app-component-checker/`
- `cloudtrail-audit/`

**Shared Code**:
- `lambdas/shared/python/state_manager.py` - State management utilities
- `lambdas/shared/python/base_checker.py` - Base checker class
- `lambdas/layers/pymysql/` - Vendored PyMySQL dependency layer

## Step Functions ASL (`step-functions/`)

ASL JSON definitions organized by category:

- `step-functions/checkers/` - 18 readiness checker state machines (`*.asl.json`)
- `step-functions/compare/` - 12 comparison state machines + comparison orchestrator

Additional ASL files in `modules/step-functions/utils/`:
- `validate_refresh_config.asl.json`
- `prepare_refresh.asl.json`
- `cleanup_and_stop.asl.json`
- `run_archive_job.asl.json` / `run_archive_job_private.asl.json`
- `tag_resources.asl.json`

## Tests (`tests/`)

- `tests/conftest.py` - Pytest fixtures (Step Functions Local client, state machine factory)
- `tests/test_asl_validation.py` - Parametrized ASL validation tests (JSON syntax, required fields, state transitions)
- `tests/test_stepfunctions_local.py` - Integration tests against Step Functions Local

## Scripts (`scripts/`)

- `scripts/validate_asl.py` - ASL validation script
- `scripts/generate_refresh_inputs.py` - Refresh input generation

## CI/CD (`.github/workflows/`)

- `docker-mysql-s3.yml` - Docker build workflow for MySQL S3 image
- `step-functions.yml` - Step Functions validation/testing workflow

## Examples

- `examples/simple/` - Minimal usage example
- `examples/complete/` - Full-featured usage example

## Naming Conventions

- **Directories**: kebab-case (`compare-alb`, `process-rds`)
- **Python files**: snake_case matching directory name (`compare_alb.py`)
- **Terraform files**: Standard naming (`main.tf`, `variables.tf`, `outputs.tf`, `versions.tf`)
- **ASL files**: kebab-case with `.asl.json` extension (`readiness-checker.asl.json`)
- **Modules**: kebab-case (`lambda-code`, `source-account`)
