# Technology Stack

**Analysis Date:** 2026-03-13

## Languages

**Primary:**
- Terraform 1.0+ - Infrastructure as Code for AWS resource orchestration
- Python 3.x - Lambda functions for business logic and comparisons
- JSON - Step Functions state machines (ASL format)

**Secondary:**
- YAML - GitHub Actions workflow configuration
- Shell/Bash - Build scripts and automation

## Runtime

**Environment:**
- AWS Lambda - Python runtime for function execution
- AWS Step Functions - Orchestration engine for state machines
- AWS services - Cross-account operations using STS AssumeRole

**Package Manager:**
- pip - Python dependency management
- Terraform providers - AWS provider >= 5.0

## Frameworks

**Core Orchestration:**
- AWS Step Functions State Machines (ASL JSON) - Workflow orchestration for cross-account database/infrastructure refresh operations
- AWS Systems Manager - State machine execution and monitoring

**Testing:**
- pytest >= 7.4.0 - Unit and integration testing framework for Python
- pytest-cov >= 4.1.0 - Code coverage reporting
- pytest-xdist >= 3.5.0 - Parallel test execution

**Build/Dev:**
- GitHub Actions - CI/CD pipeline for Terraform validation and security scanning
- Terraform fmt - Code formatting validation
- TFLint - Terraform linting and best practices
- Trivy - Vulnerability scanning for infrastructure code
- Checkov - Cloud infrastructure compliance scanning

## Key Dependencies

**Critical:**
- boto3 >= 1.34.0 - AWS SDK for Python Lambda functions and cross-account operations
- botocore >= 1.34.0 - Low-level AWS service API bindings
- pymysql >= 1.1.0, < 2.0.0 - MySQL/Aurora database connections (packaged in Lambda layer at `lambdas/layers/pymysql/`)

**Cryptography & Security:**
- cryptography - Asymmetric encryption for parameter handling
- PyNaCl (nacl) - Additional cryptographic operations

**Infrastructure:**
- hashicorp/aws >= 5.0 - Terraform AWS provider for resource management

**Database Connectivity:**
- pymysql (custom layer) - Pure Python MySQL client for Lambda execution environment without compiled dependencies
- SSL/TLS libraries - Built-in Python ssl module for secure database connections

**Code Quality:**
- ruff >= 0.1.0 - Fast Python linter
- mypy >= 1.7.0 - Static type checking

## Configuration

**Environment:**
- Terraform variables define environment configuration:
  - `prefix`: Resource naming prefix (default: "refresh")
  - `source_role_arns`: IAM roles in source accounts for cross-account access
  - `destination_role_arns`: IAM roles in destination accounts for cross-account access
  - `enable_step_functions_logging`: CloudWatch logging toggle
  - `log_retention_days`: CloudWatch log retention period (default: 30)
  - `enable_xray_tracing`: X-Ray tracing toggle for Step Functions

**Build:**
- `versions.tf` - Terraform version constraints and required providers
- `.github/workflows/terraform.yml` - CI pipeline for Terraform validation, linting, and security scanning
- `.github/workflows/step-functions.yml` - Step Functions testing pipeline
- `.github/workflows/docker-mysql-s3.yml` - MySQL to S3 export pipeline
- `requirements-dev.txt` - Development dependencies (testing, code quality)
- `trivy.yaml` - Trivy vulnerability scanner configuration
- Terraform modules in `modules/` - Reusable infrastructure components

## Platform Requirements

**Development:**
- Terraform CLI >= 1.6.0 (specified in CI/CD)
- Python 3.x runtime for Lambda development
- AWS credentials configured for cross-account access
- Git for version control

**Production:**
- AWS Accounts: Orchestrator account + source/destination accounts (cross-account architecture)
- AWS Regions: Configurable via Terraform (uses aws_region data source)
- IAM permissions: STS AssumeRole capability in both source and destination accounts
- CloudWatch: For Step Functions logging and Lambda execution logs
- S3: Lambda code storage bucket (created by `modules/lambda-code`)
- DynamoDB: State storage for comparison operations
- RDS/Aurora: Database resources for refresh operations
- EFS: Filesystem resources for data operations
- EKS: Kubernetes cluster integration
- Secrets Manager/SSM Parameter Store: Configuration and credential storage

---

*Stack analysis: 2026-03-13*
