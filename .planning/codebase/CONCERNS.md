# Codebase Concerns

## Technical Debt

### HIGH Priority

#### No Test Coverage for Lambda Functions
- ~45+ Lambda functions with zero unit test coverage
- ~23,126 lines of untested Python code across `lambdas/` directory
- No test framework setup, no mocking patterns established
- **Risk**: Regressions go undetected; refactoring is risky without safety net

#### ASL Duplication Across Step Functions
- ~54 states of ASL duplication across 44 Step Functions JSON files
- 6 pairs of nearly-identical public/private Step Functions implementations
- No templating or generation mechanism for shared state machine logic
- **Risk**: Divergence between paired implementations; maintenance burden grows linearly

### MEDIUM Priority

#### Overly Broad IAM Permissions
- Multiple IAM policies using `Resource = "*"` patterns
- Violates principle of least privilege
- **Risk**: Blast radius of compromised credentials is unnecessarily large

#### Missing `sensitive = true` on Terraform Outputs
- Outputs containing ARNs and potentially sensitive identifiers not marked as sensitive
- **Risk**: Values displayed in CLI output and state files without redaction

#### Large Monolithic Lambda Functions
- Several Lambda functions exceed 700+ lines each
- No internal modularization or separation of concerns
- **Risk**: Difficult to maintain, test, and reason about

#### No Exponential Backoff in EFS Replication Polling
- Polling loops for EFS replication status use fixed intervals
- **Risk**: API throttling under load; inefficient resource usage

#### No Monitoring/Alerting for Refresh Operations
- Step Functions executions run without CloudWatch alarms or SNS notifications
- **Risk**: Failed refreshes go unnoticed until manual inspection

#### No Rollback Mechanism for Failed Refreshes
- If a refresh operation fails partway through, no automated rollback
- **Risk**: Environment left in inconsistent state requiring manual intervention

## Security Concerns

### Debug Statements in Vendored Dependencies
- `print()` statements found in vendored PyMySQL layer (`lambdas/layers/pymysql/`)
- **Risk**: Sensitive data (connection strings, queries) could leak to CloudWatch logs

### Cross-Account Role Assumptions Without Resource Constraints
- IAM role assumption policies lack resource-level constraints
- **Risk**: Overly permissive cross-account access

### Lambda Build Platform Hardcoded
- ARM64 architecture hardcoded without validation
- **Risk**: Build/runtime mismatch if deployment targets change

## Fragile Areas

### Complex State Machines
- EFS replication state machines contain up to 72 states
- High cyclomatic complexity makes changes error-prone
- No pre-commit ASL validation tooling
- **Risk**: Subtle bugs in state transitions; difficult to review changes

### No Integration Tests
- No end-to-end testing of refresh workflows
- Individual components untested in combination
- **Risk**: Integration failures only discovered in production

## Recommendations

1. **Immediate**: Add `sensitive = true` to outputs containing ARNs/secrets
2. **Short-term**: Establish Lambda testing framework with pytest + moto
3. **Medium-term**: Template Step Functions ASL to eliminate duplication
4. **Medium-term**: Implement monitoring/alerting for refresh operations
5. **Long-term**: Add integration test suite for core workflows
