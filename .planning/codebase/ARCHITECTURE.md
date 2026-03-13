# Architecture

**Analysis Date:** 2026-03-13

## Pattern Overview

**Overall:** Modular AWS Step Functions orchestrator for cross-account infrastructure refresh (database, EFS, EKS)

**Key Characteristics:**
- Step Functions as the primary orchestration engine (AWS-native serverless)
- Cross-account operation via IAM role assumption
- Modular design: separate Step Functions for each operational domain (DB, EFS, EKS, Utils)
- Lambda functions for cross-region/cross-account API proxying and specialized operations
- Configuration-driven flow with S3-based input loading and validation
- Parallel execution of independent resource checks and operations

## Layers

**Orchestration Layer:**
- Purpose: Coordinates the entire 5-phase refresh workflow (validate → prepare → backup → restore → verify)
- Location: `modules/step-functions/orchestrator/`
- Contains: refresh_orchestrator state machine, CloudWatch logging, X-Ray tracing
- Depends on: IAM roles, nested Step Functions (DB, EFS, EKS, Utils), SNS topics
- Used by: External systems via Step Functions invocation

**Operational Modules:**
- **Database Layer:** `modules/step-functions/db/` - RDS/Aurora operations (restore, snapshot management, SQL execution)
- **EFS Layer:** `modules/step-functions/efs/` - Filesystem operations (replication, backup restore, subpath management)
- **EKS Layer:** `modules/step-functions/eks/` - Kubernetes resource management (pod readiness, secret sync, configuration propagation)
- **Utilities Layer:** `modules/step-functions/utils/` - Cross-cutting concerns (validation, readiness checks, state management)

Each module:
- Exports multiple state machines (5-15 per module)
- Defines logging configuration (CloudWatch, optional X-Ray)
- Uses templatefile or file() for state machine definitions
- Inherits orchestrator role from IAM module

**Execution Layer:**
- Purpose: Custom Python Lambda functions for operations unsupported by Step Functions SDK integrations
- Location: `lambdas/` (64 functions organized by domain)
- Contains: Database utilities, Kubernetes proxies, AWS API helpers, health checkers, comparison operations
- Depends on: boto3 (AWS SDK), pymysql (for direct DB operations), kubeconfig/kubectl (for K8s)
- Used by: Step Functions via Task resources (invoke synchronously or asynchronously)

**Infrastructure Provisioning:**
- Purpose: IaC through Terraform modules
- Location: `modules/` (8 domain modules + root)
- Contains: IAM roles, Step Functions definitions, S3 buckets, CloudWatch log groups
- Depends on: AWS provider, cross-account role ARNs provided at deployment
- Used by: Applied via terraform apply to create/update AWS resources

**Code Storage Layer:**
- Purpose: S3 bucket for Lambda function code (enables dynamic deployment by Step Functions)
- Location: `modules/lambda-code/`
- Contains: Packaged Python Lambda functions (check-flag-file, get-efs-subpath, cross-region-rds-proxy)
- Depends on: boto3, botocore
- Used by: Step Functions tasks that invoke Lambda functions dynamically

## Data Flow

**Main Orchestration Flow (refresh_orchestrator):**

1. **Input Loading** → LoadConfiguration (Choice) → If ConfigS3Path present, load config from S3 else use direct input
2. **Validation** → Call utils:ValidateRefreshConfig (parallel validation of Database, EFS, EKS, SourceAccount, DestinationAccount)
3. **Dry-Run Check** → If DryRun=true, return validation report and exit; else continue
4. **Preparation Phase** → Execute domain-specific prepare steps (snapshot preparation, backups, replication setup)
5. **Refresh Phase** → Execute restore/apply operations in destination account
6. **Verification Phase** → Run readiness checks (database availability, EKS cluster health, pod readiness, EFS sync status)
7. **Notification** → Send SNS notification with results (success or failure branch)
8. **Return State** → Output full execution report with all check results

**Cross-Account Operation Pattern:**

```
[Orchestrator Account]
  ↓ (assumes role via sts:AssumeRole)
[Source Account] ← Extract data, create snapshots, validate resources
  ↓
[Destination Account] ← Restore data, apply configurations, verify health
```

**Lambda Invocation Pattern:**

```
Step Functions Task (Resource: lambda:invoke)
  ↓ Parameters loaded from event
  ↓ (optionally assumes cross-account role via boto3 STS)
Lambda Handler Executes
  ↓ Calls AWS API or Kubernetes API
  ↓
Returns Result (normalized response back to Step Functions)
```

**State Management:**

- **Configuration Loading:** Input event can reference S3 path (ConfigS3Bucket/ConfigS3Path) to load full refresh config
- **Validation Results:** Captured in ValidationResult object; used in Choice states to route flow
- **Execution State:** Full orchestrator input preserved throughout execution via ResultPath and OutputPath
- **Nested Execution State:** Nested Step Functions invocations via states:startExecution.sync:2 return Output containing results
- **Check Results:** Parallel branches accumulate results which are merged in terminal states

## Key Abstractions

**Step Function Module:**
- Purpose: Encapsulates a domain-specific set of state machines (DB, EFS, EKS, Utils, Orchestrator)
- Examples: `modules/step-functions/db/`, `modules/step-functions/efs/`
- Pattern: Each module defines multiple state machines in local.step_functions map, applies consistent naming/logging, exports ARNs for composition

**Lambda Function:**
- Purpose: Performs specialized AWS API calls, computation, or Kubernetes operations
- Examples: `lambdas/cross-region-rds-proxy/` (assumes role + calls RDS in target region), `lambdas/check-flag-file/` (EFS flag management)
- Pattern: Single Python file with lambda_handler(event, context), uses boto3/kubeconfig for external calls

**Readiness Checker Pattern:**
- Purpose: Validates infrastructure health before/after operations
- Examples: `step-functions/checkers/readiness-checker.asl.json`, `step-functions/checkers/infra-rds-checker.asl.json`
- Pattern: Parallel branches calling AWS SDK integrations (eks:describeCluster, rds:describeDBClusters) + Lambda for complex checks; results merged

**Comparator Pattern:**
- Purpose: Validates source and destination match (configuration, secrets, resources)
- Examples: `step-functions/compare/compare-rds-snapshots.asl.json`, `step-functions/compare/compare-k8s-secrets.asl.json`
- Pattern: Fetch source data → Fetch destination data → Compare → Report differences

## Entry Points

**Main Orchestrator:**
- Location: `modules/step-functions/orchestrator/refresh_orchestrator.asl.json`
- Triggers: External invocation (API gateway, CLI, scheduled EventBridge rule)
- Responsibilities:
  - Accept refresh configuration (Database, EFS, EKS, SourceAccount, DestinationAccount, StepFunctions ARN map)
  - Validate all inputs and resources
  - Orchestrate 5-phase workflow (prepare → backup → restore → verify → notify)
  - Send SNS notifications on success/failure
  - Return execution report

**Terraform Root Module:**
- Location: `main.tf`, `variables.tf`, `versions.tf`
- Triggers: terraform apply
- Responsibilities:
  - Compose all sub-modules (lambda-code, step-functions-db/efs/eks/utils, iam, orchestrator)
  - Pass cross-account role ARNs from source/destination accounts
  - Configure logging and tracing settings globally
  - Export consolidated outputs (all Step Function ARNs)

**Lambda Code Module:**
- Location: `modules/lambda-code/main.tf`
- Triggers: terraform apply with deploy_lambda_code=true
- Responsibilities:
  - Create S3 bucket (or use existing)
  - Package 3 core Lambda functions (check-flag-file, get-efs-subpath, cross-region-rds-proxy)
  - Apply bucket policy to allow cross-account roles to read code
  - Export S3 keys for Step Functions to reference

## Error Handling

**Strategy:** Fail-fast with detailed error context

**Patterns:**

1. **Validation Errors:** ValidateRefreshConfig step fails early with descriptive messages if required resources missing or invalid
2. **Catch Blocks in Step Functions:** Each Task has Catch for States.ALL, captures error in ResultPath, routes to failure handler
3. **Lambda Errors:** Python exceptions logged to CloudWatch; error message returned in response.errorMessage for Step Functions Catch
4. **Cross-Account Role Assumption Failures:** STS.AssumeRole failures caught by Lambda try/except or Task Catch; propagate as execution failure
5. **Nested Execution Failures:** states:startExecution.sync:2 returns error if nested SFN fails; parent Catch routes to notification
6. **Notification Path:** CheckNotifyEnabled_Failure routes failures through SNS notification before final failure state
7. **Logging:** All errors logged to CloudWatch log groups (one per Step Function module) with execution name + step context

## Cross-Cutting Concerns

**Logging:**
- CloudWatch log groups per module (`/aws/stepfunctions/{prefix}-{module}`)
- Step Functions logging configuration: include_execution_data=true, level=ALL
- Lambda logging: Python logging module configured with LOG_LEVEL env var (default INFO)
- Log retention: Configurable via log_retention_days variable (default 30 days)

**Validation:**
- Input validation at orchestrator entry via ValidateRefreshConfig step function
- Checks: Database configuration present, EFS Name required when EFS enabled, EKS cluster accessible, cross-account roles exist
- Field validation in nested steps (e.g., UpdateSourcePolicy/UpdateDestinationPolicy flags in EFS replication)

**Authentication:**
- Cross-account access via IAM role assumption (orchestrator role assumes source/destination roles)
- STS session name: sfn-{module}-{operation} for audit trail
- Role assumption duration: 3600 seconds (1 hour) for most operations
- Kubeconfig for EKS: fetched by Lambda via cross-region-rds-proxy pattern (assume role, fetch in source region)

**Observability:**
- X-Ray tracing: Optional (enable_xray_tracing variable), disabled by default
- Step Functions console: Full execution history with visual flow
- Execution names: States.Format('{}-{Operation}', $$.Execution.Name) for clarity
- SNS notifications: Execution status (success/failure) sent to configured topics
