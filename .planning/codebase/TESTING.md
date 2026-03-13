# Testing

## Framework

- **pytest** as the test runner
- **boto3** for AWS SDK interactions (Step Functions Local)
- Dependencies listed in `requirements-dev.txt`

## Test Structure

```
tests/
├── conftest.py                    # Session fixtures, markers, SFN Local setup
├── test_asl_validation.py         # Unit tests - ASL structure validation
└── test_stepfunctions_local.py    # Integration tests - SFN Local execution
```

## Test Categories

### ASL Validation Tests (`test_asl_validation.py`)

Unit tests that run **without AWS credentials**. All ASL files are discovered via `rglob("*.asl.json")` and tested through parametrization.

**Test classes:**
- `TestASLJsonSyntax` - Valid JSON parsing
- `TestASLRequiredFields` - `StartAt` and `States` fields exist, `StartAt` references valid state
- `TestASLStateTypes` - All states have valid Type (Task, Pass, Choice, Wait, Succeed, Fail, Parallel, Map)
- `TestASLStateTransitions` - Next/Default/Catch references point to existing states, terminal states exist, non-terminal states have transitions
- `TestASLTaskStates` - Task states have Resource field
- `TestASLChoiceStates` - Choice states have non-empty Choices array
- `TestASLCrossAccount` - States with Credentials have RoleArn

**Pattern:** Each test class uses `@pytest.mark.parametrize` over all discovered ASL files, generating one test per file per assertion.

### Integration Tests (`test_stepfunctions_local.py`)

Tests that require **Step Functions Local** running on `http://localhost:8083` (configurable via `SFN_LOCAL_ENDPOINT` env var).

- Marked with `@pytest.mark.sfn_local`
- Auto-skipped if Step Functions Local is not available
- Uses factory fixtures for state machine creation/cleanup

## Fixtures (`conftest.py`)

| Fixture | Scope | Purpose |
|---------|-------|---------|
| `sfn_client` | session | boto3 Step Functions client connected to local endpoint |
| `sfn_local_available` | session | Bool check if SFN Local is reachable |
| `create_state_machine` | function | Factory fixture - creates state machines with auto-cleanup |
| `sample_pass_definition` | function | Simple Pass state machine definition |
| `sample_choice_definition` | function | Choice branching state machine definition |

## Custom Markers

- `sfn_local` - Marks tests requiring Step Functions Local; auto-skipped if unavailable

## CI/CD Integration

GitHub Actions workflow (`.github/workflows/step-functions.yml`):
- Runs ASL validation tests
- Optionally runs SFN Local integration tests with Docker

## Test Coverage Gaps

- **Lambda functions**: No unit tests for any of the ~45 Lambda functions
- **Terraform modules**: No `terraform validate` or `terraform plan` tests in CI
- **End-to-end**: No integration tests for the full refresh workflow
- **Only ASL files** are currently tested (structure validation + basic SFN Local execution)

## Running Tests

```bash
# ASL validation only (no AWS needed)
pytest tests/test_asl_validation.py -v

# All tests (requires SFN Local on :8083)
pytest tests/ -v

# Skip SFN Local tests
pytest tests/ -v -m "not sfn_local"
```
