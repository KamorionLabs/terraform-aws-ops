# External Integrations

**Analysis Date:** 2026-03-13

## APIs & External Services

**AWS Step Functions:**
- Service: AWS Step Functions State Machine execution
  - SDK/Client: boto3 (built into Lambda runtime)
  - Used by: Orchestrator to invoke child state machines for database, EFS, EKS, and utility operations
  - Pattern: Cross-account role assumption via Step Functions credentials parameter

**AWS Systems Manager (AWS SDK Integration):**
- Service: AWS-SDK integration for Step Functions
  - Used for: RDS operations (DescribeDBClusters), EC2 operations, parameter store access
  - Pattern: Direct AWS-SDK invocation from Step Functions using `arn:aws:states:::aws-sdk:*`
  - Cross-account: Supports STS AssumeRole credentials in Step Function state definitions

**Cross-Region AWS API Proxy:**
- Lambda: `lambdas/cross-region-rds-proxy/cross_region_rds_proxy.py`
  - Purpose: Assume role in source region, call AWS API, normalize response keys
  - Used for: RDS operations across regions and accounts

## Data Storage

**Databases:**
- RDS/Aurora MySQL
  - Connection: Cross-account RDS connections via PyMySQL layer
  - Client: `pymysql` (pure Python driver, custom layer at `lambdas/layers/pymysql/`)
  - Operations: Database restore, cluster management, SQL execution, parameter group configuration
  - Architecture: Source production database → Destination non-prod restoration

**DynamoDB:**
- Purpose: State storage and comparison between source/destination
  - Usage: Step Functions store extracted infrastructure states for later comparison
  - Format: Stores raw AWS API responses and comparison results
  - Pattern: States access DynamoDB via Step Functions task integration
  - Lifecycle: Results persist for comparison phases

**Secrets Manager:**
- Purpose: Database credentials and master password rotation
  - Operations: `enable_master_secret`, `rotate_secrets` state machines
  - Cross-account: Secrets stored in both source and destination accounts
  - Secret paths tracked: `/rubix/{instance}-{env}/{eks-cluster}/{db}` → `/digital/{env}/infra/databases/{rds-cluster}/{db}`

**AWS Systems Manager Parameter Store (SSM):**
- Purpose: Configuration parameters and application settings
  - State machines: `config-ssm-checker`, `config-ssm-compare`
  - Comparison logic: Extract, compare, report drift between source and destination
  - Cross-account: Supports cross-account parameter store access via assumed roles

**File Storage:**
- S3 (Lambda Code Bucket)
  - Bucket: Created by `modules/lambda-code` or provided via `lambda_code_bucket_name` variable
  - Pattern: Dynamic Lambda deployment - Step Functions downloads code from S3 for execution
  - Versioning: Enabled on Lambda code bucket
  - Encryption: AES256 server-side encryption
  - Access: Cross-account role ARNs granted access via bucket policy
  - Location: `modules/lambda-code/main.tf` defines bucket creation and access

- S3 (Database Operations)
  - Purpose: SQL script import/export, MySQL backup storage
  - Operations: `run_sql_from_s3`, MySQL dump/import via S3 integration
  - Lambda: `lambdas/cross-region-rds-proxy/` supports S3 integration operations

**EFS (Elastic File System):**
- Purpose: Shared filesystem for database refresh operations
- Operations: Replication, snapshot management, restore subpath handling
- Lambda: `lambdas/check-flag-file/check_flag_file.py`, `lambdas/get-efs-subpath/get_efs_subpath.py`
- State machines: `repl-efs-sync-checker`, EFS replication monitoring
- Cross-account: Source/destination EFS sync via restore snapshots

## Authentication & Identity

**Auth Provider:**
- Custom STS AssumeRole cross-account architecture
  - Implementation: IAM roles with STS trust relationships
  - Orchestrator role: `modules/iam/main.tf` - created in shared services account
  - Source roles: List provided via `source_role_arns` variable
  - Destination roles: List provided via `destination_role_arns` variable
  - Pattern: Step Functions assume source/destination roles for cross-account AWS API calls

**Service Principals:**
- AWS Step Functions (states.amazonaws.com)
  - Allows: Step Functions to execute state machines and assume cross-account roles
  - Defined in: `modules/iam/main.tf` line 27

**Cross-Account Access:**
- Role assumption: Step Functions in orchestrator account assume roles in source/destination
- Resource path: Orchestrator policy allows `sts:AssumeRole` on `source_role_arns` and `destination_role_arns`
- IAM role policy: `modules/iam/main.tf` - orchestrator_assume_roles policy

## Monitoring & Observability

**Error Tracking:**
- Step Functions built-in: Error handling via Catch blocks in state definitions
- Pattern: States.ALL errors captured with ResultPath for detailed error tracking
- Format: Error results stored in DynamoDB with timestamp and cause

**Logs:**
- CloudWatch Logs
  - Enabled: Via `enable_step_functions_logging` variable (default: true)
  - Retention: Configurable via `log_retention_days` (default: 30)
  - Log groups: Created per Step Function state machine
  - IAM policy: `modules/iam/main.tf` grants `logs:CreateLogDeliveryOptions`, `logs:CreateLogGroup`, `logs:CreateLogStream`, `logs:PutResourcePolicy`

**Tracing:**
- AWS X-Ray
  - Enabled: Via `enable_xray_tracing` variable (default: false)
  - Tracing: Step Functions trace segment creation for distributed tracing
  - Lambda tracing: Optional X-Ray SDK integration in Python functions

**Comparison & Validation:**
- Infrastructure state comparison stored in DynamoDB
- State machines generate detailed comparison reports
- Checker state machines: 20+ checkers for infrastructure validation (RDS, EKS, EFS, security groups, ALB, DNS, CloudFront, etc.)

## CI/CD & Deployment

**Hosting:**
- AWS (multi-account)
  - Orchestrator account: Central Step Functions and Lambda code
  - Source accounts: Production database clusters
  - Destination accounts: Non-production refresh targets

**CI Pipeline:**
- GitHub Actions (`.github/workflows/terraform.yml`)
  - Terraform 1.6.0 validation and formatting
  - TFLint static analysis
  - Trivy vulnerability scanning
  - Checkov infrastructure compliance checking
  - Triggers: On push to main or PR with `**.tf` or workflow changes
  - Module validation: `modules/iam` and `modules/step-functions/db` validated separately

**Step Functions Testing:**
- GitHub Actions (`.github/workflows/step-functions.yml`)
  - Step Functions state machine validation
  - Python pytest for Lambda unit/integration tests
  - boto3 mock testing for AWS service calls

**Docker & MySQL:**
- GitHub Actions (`.github/workflows/docker-mysql-s3.yml`)
  - MySQL container for integration testing
  - S3 export/import pipeline validation

## Environment Configuration

**Required env vars (Lambda execution):**
- `LOG_LEVEL`: Logging level for Lambda functions (default: INFO)
- Cross-account configuration passed via Step Function input parameters:
  - `CrossAccountRoleArn`: ARN to assume in source/destination account
  - `Project`: Project identifier
  - `Env`: Environment (stg, ppd, prd)
  - `DbClusterIdentifier`: RDS cluster identifier
  - `ClusterName`: EKS cluster name
  - `Namespace`: Kubernetes namespace

**Secrets location:**
- AWS Secrets Manager
  - Database credentials stored per environment/cluster path
  - Rotation via `rotate_secrets` state machine
- AWS SSM Parameter Store
  - Application configuration parameters
  - Accessed via cross-account assumed roles

**AWS Credentials:**
- Implicit via Lambda execution role
- Cross-account: Explicit via STS AssumeRole in Step Function state definitions
- No hardcoded credentials; all authentication via IAM

## Webhooks & Callbacks

**Incoming:**
- EventBridge/Step Functions execution triggers
- Pattern: Manual invocation or scheduled via EventBridge rules (not defined in this codebase)
- Entry: Orchestrator state machine receives input parameters and routes to DB/EFS/EKS modules

**Outgoing:**
- SNS notifications: Optional via IAM policy (referenced but not actively used in checked files)
- CloudWatch Events: Step Functions state machine completion events
- State output: Comparison results stored in DynamoDB for downstream consumption

**Parallel Execution Pattern:**
- Comparison orchestrator: `step-functions/compare/comparison-orchestrator.asl.json`
- Pattern: Parallel branches for source state collection, destination state collection, then comparison
- State machine routing: Orchestrator dispatches to specialized checkers/comparers:
  - Database: `net-alb-compare`, `k8s-services-compare`, `k8s-secrets-compare`, `config-ssm-compare`, etc.
  - Results: All comparisons written to DynamoDB for aggregation

---

*Integration audit: 2026-03-13*
