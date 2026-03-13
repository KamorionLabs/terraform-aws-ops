# Technology Stack

**Project:** Step Functions Modularization
**Researched:** 2026-03-13
**Scope:** Sub-state-machine patterns, ASL composition, testing for nested + cross-account SFN

---

## Recommended Stack

### ASL Composition (Sub-State-Machine Pattern)

| Technology | Version | Purpose | Why |
|------------|---------|---------|-----|
| Native `states:startExecution.sync:2` | AWS-managed | Call sub-SFN, wait for result, return parsed JSON | Already used throughout the project (11+ files); `sync:2` returns `Output` as parsed JSON instead of a JSON string — eliminates an extra parse step |
| Terraform `templatefile()` | Terraform built-in | Inject sub-SFN ARNs at deploy time | Already the orchestrator pattern. New sub-SFN modules expose ARN outputs; parent modules consume via `templatefile()` variables |
| Runtime ARN passing via Input | ASL `StateMachineArn.$` | Pass sub-SFN ARN from parent input at execution time | Already the EFS/DB pattern. Works when ARN is known only at runtime (e.g., per-account selection). Keep for dynamic dispatch; use templatefile for static wiring |

**Decision point for new sub-SFN modules:** Use `templatefile()` injection when the caller is a fixed Terraform-deployed SFN. Use `StateMachineArn.$` input when the caller is itself called by the orchestrator and ARN comes from `$.StepFunctions.*` map. Maintain consistency with the existing convention in the relevant module.

**Confidence:** HIGH — pattern already established and working in codebase.

---

### Terraform Module Structure

| Technology | Version | Purpose | Why |
|------------|---------|---------|-----|
| AWS Terraform Provider | `~> 5.x` (existing) | `aws_sfn_state_machine` resource | Existing, no change required |
| Terraform module per sub-SFN domain | N/A (internal convention) | `modules/step-functions/utils/` for shared sub-SFN | Follows existing structure; shared sub-SFN go in `utils/` (ManageLambdaLifecycle, ManageAccessPoint, ManageFileSystemPolicy), domain-specific sub-SFN stay in their domain module (EnsureSnapshotAvailable → `db/`) |

**Output pattern for new sub-SFN modules:**
```hcl
output "state_machine_arn" {
  value = aws_sfn_state_machine.this.arn
}
```
Parent module consumes:
```hcl
definition = templatefile("${path.module}/my_sfn.asl.json", {
  manage_lambda_lifecycle_arn = var.utils_step_function_arns["manage_lambda_lifecycle"]
})
```

**Confidence:** HIGH — consistent with existing orchestrator pattern.

---

### Testing Framework

| Tool | Version | Purpose | Why |
|------|---------|---------|-----|
| `pytest` | `>=7.4.0` (existing) | Test runner | Already installed, no change |
| `boto3` | `>=1.34.0` (existing) | SFN client for validation and TestState | Already installed |
| `pytest-cov` | `>=4.1.0` (existing) | Coverage | Already installed |
| `pytest-xdist` | `>=3.5.0` (existing) | Parallel test execution | Already installed |
| AWS `ValidateStateMachineDefinition` API | boto3 built-in | Online ASL syntax validation | AWS's own validator catches errors that JSON schema cannot (e.g., invalid JSONata expressions, bad Reference Paths). Requires AWS creds but CI already has them via OIDC |

**What NOT to add:**
- `moto` for Step Functions — moto's SFN executor has limited ASL support and does not execute JSONata. Not suitable for validating the complex expressions this project uses.
- `pytest-stepfunctions` — wraps SFN Local with Lambda mocking. Only useful for end-to-end execution tests; adds operational complexity for marginal gain over structural validation.
- `localstack` — Pro tier required for reliable nested SFN + Credentials support. Community edition has known limitations with `startExecution.sync:2` (GitHub issue #4132). Cost and complexity not justified.

**Confidence:** HIGH for existing tooling. MEDIUM for ValidateStateMachineDefinition integration (requires AWS credentials in CI, already present but needs wiring).

---

### CI Testing Strategy for Nested + Cross-Account SFN

This is the core difficulty: `startExecution.sync:2` is **not supported** in the official `amazon/aws-stepfunctions-local` Docker image. The `Credentials` field (RoleArn) is also **not enforced** by SFN Local — it accepts any role ARN syntactically but does not perform IAM evaluation.

| Layer | Tool | What It Tests | AWS Creds Required |
|-------|------|--------------|-------------------|
| Structural validation | `test_asl_validation.py` (existing pytest) | JSON syntax, StartAt, state transitions, terminal state, Credentials shape | No |
| ASL semantic validation | `boto3` `ValidateStateMachineDefinition` | JSONata expressions, Reference Paths, Resource ARN format, ASL spec compliance | Yes (OIDC in CI) |
| Sub-SFN creation smoke test | `amazon/aws-stepfunctions-local` Docker (existing) | Each new sub-SFN ASL can be registered without errors | No |
| Integration (manual/staging) | Real AWS execution in staging account | End-to-end nested execution, cross-account role assumption | Yes |

**Credentials field in tests — concrete fix for the broken CI:** The existing `test_stepfunctions_local.py` fails when registering state machines that contain `Credentials` fields because SFN Local does not support the `Credentials` block syntax in `create_state_machine`. The fix is to **strip `Credentials` fields before submitting to SFN Local** in the test fixture, since the structural tests for Credentials shape are already covered separately in `TestASLCrossAccount`. This is the minimal change needed and does not require a new tool.

```python
def strip_credentials(definition: dict) -> dict:
    """Remove Credentials blocks for SFN Local compatibility."""
    import copy
    d = copy.deepcopy(definition)
    for state in d.get("States", {}).values():
        state.pop("Credentials", None)
    return d
```

**Confidence:** HIGH — confirmed from official SFN Local documentation (role-arn not used, feature parity not guaranteed) and existing project structure.

---

### ASL Validation Tools

| Tool | Version | Purpose | Why |
|------|---------|---------|-----|
| `asl-validator` (npm) | `4.0.0` | Optional: JSON-schema-based ASL lint in IDE | Useful for developer DX (VSCode extension, pre-commit). NOT recommended for CI — the Python-native approach with boto3 covers the same ground with fewer moving parts |
| AWS `ValidateStateMachineDefinition` API | boto3 built-in | Authoritative semantic validation | The only tool that catches JSONata syntax errors. Can be called from pytest in the CI job that already has AWS credentials. Returns structured diagnostics (errors + warnings) |

**Confidence for ValidateStateMachineDefinition:** MEDIUM — API is documented and in boto3 1.34+, but integration into the existing test suite requires authoring a new test class. The API is cloud-only (no local emulation support as of March 2026).

---

## Stack Summary (What Changes vs What Stays)

### No changes required

- Terraform AWS provider version
- Python 3.x / pytest / boto3 / pytest-cov / pytest-xdist
- `templatefile()` pattern for orchestrator
- `file()` pattern for domain modules that don't need ARN injection
- `startExecution.sync:2` as the sub-SFN invocation resource
- `amazon/aws-stepfunctions-local` Docker for smoke tests

### Additions

- `ValidateStateMachineDefinition` boto3 calls in a new pytest class (requires AWS creds already present in CI)
- Helper function in `conftest.py` to strip `Credentials` fields before SFN Local registration
- New Terraform outputs for each sub-SFN module to expose ARNs
- New `variables.tf` entries in parent modules to accept sub-SFN ARNs as inputs

### Explicit rejections

| Option | Reason |
|--------|--------|
| Jinja2 / jsonnet for ASL templating | Explicitly out of scope per PROJECT.md — adds a build step, breaks IDE support |
| `localstack` | `startExecution.sync:2` unreliable in community tier; Pro tier cost not justified |
| `moto` for SFN | Cannot execute JSONata, partial ASL support — false confidence |
| `pytest-stepfunctions` | Lambda mocking not needed here; nested SFN perf issues documented |
| `aws-sam-cli` local | Targets Lambda-centric workflows; adds complexity for SFN-only testing |

---

## Installation

No new packages to install. The existing `requirements-dev.txt` already covers all required libraries.

```bash
# Already installed (no change needed)
pip install -r requirements-dev.txt
# pytest>=7.4.0, boto3>=1.34.0, botocore>=1.34.0
```

For the optional `asl-validator` IDE integration (developer convenience only, not CI):
```bash
npm install -g asl-validator  # optional, for local pre-commit hooks
```

---

## Sources

- AWS Step Functions Local limitations (official docs): https://docs.aws.amazon.com/step-functions/latest/dg/sfn-local.html
  — Confirms role-arn is not enforced, no feature parity, TestState API recommended alternative
- ValidateStateMachineDefinition API: https://docs.aws.amazon.com/step-functions/latest/apireference/API_ValidateStateMachineDefinition.html
  — Authoritative ASL semantic validator, CI/pre-commit integration documented
- TestState API (November 2025 enhancement): https://aws.amazon.com/about-aws/whats-new/2025/11/aws-step-functions-local-testing-teststate-api/
  — Enhanced local unit testing; when mock is specified, roleArn is optional (bypasses Credentials issue)
- TestState API docs (mock + Credentials behavior): https://docs.aws.amazon.com/step-functions/latest/dg/test-state-isolation.html
  — "When you specify a mock, specifying the role becomes optional"
- Nested SFN startExecution.sync:2 docs: https://docs.aws.amazon.com/step-functions/latest/dg/connect-stepfunctions.html
- Terraform + Step Functions best practices: https://docs.aws.amazon.com/step-functions/latest/dg/terraform-sfn.html
- terraform-aws-modules/step-functions (v5.0.2): https://registry.terraform.io/modules/terraform-aws-modules/step-functions/aws/latest
- SFN Local startExecution.sync:2 unsupported (community issue): https://github.com/serverless-operations/serverless-step-functions/issues/398
- boto3 current version (1.42.x): https://boto3.amazonaws.com/v1/documentation/api/latest/reference/services/stepfunctions.html
- asl-validator npm (v4.0.0): https://www.npmjs.com/package/asl-validator
