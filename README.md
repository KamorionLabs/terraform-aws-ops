# terraform-aws-ops

Terraform modules for cross-account AWS infrastructure operations using Step Functions. Designed for automating database refresh, EFS replication, and EKS orchestration across AWS accounts.

## Features

- **Cross-account orchestration** via Step Functions with AssumeRole
- **5-phase refresh pipeline**: DB restore → EFS replication → EKS jobs → Cleanup → Notify
- **40+ Step Functions** for granular operations (snapshot, restore, dump, import, scale, etc.)
- **Public & private EKS modes**: Direct API or Lambda proxy for private endpoints
- **Synchronized cutoff**: EFS replication sync + DB snapshot in coordinated sequence
- **Docker images** for MySQL dump/import with S3 integration ([mysql-s3](docker/mysql-s3/))
- **Multi-account IAM**: Orchestrator, source, and destination roles with least-privilege
- **CloudTrail audit**: Automatic audit trail of refresh operations

## Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                         Shared Services Account                              │
│  ┌─────────────────────────────────────────────────────────────────────┐    │
│  │                    Orchestrator Step Function                        │    │
│  │  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐           │    │
│  │  │  DB (20) │  │ EFS (8)  │  │ EKS (7)  │  │ Utils (7)│           │    │
│  │  └──────────┘  └──────────┘  └──────────┘  └──────────┘           │    │
│  └─────────────────────────────────────────────────────────────────────┘    │
│                                    │                                         │
│                          AssumeRole (cross-account)                          │
└────────────────────────────────────┼────────────────────────────────────────┘
                                     │
          ┌──────────────────────────┼──────────────────────────┐
          ▼                          ▼                          ▼
┌─────────────────────┐  ┌─────────────────────┐  ┌─────────────────────┐
│   Source Account    │  │ Destination Account │  │ Destination Account │
│   (Production)      │  │   (Staging)         │  │   (Dev)             │
│                     │  │                     │  │                     │
│  • RDS Snapshots    │  │  • Restore Cluster  │  │  • Restore Cluster  │
│  • EFS Backups      │  │  • Lambda Proxies   │  │  • Lambda Proxies   │
│  • Secrets (read)   │  │  • EKS Jobs         │  │  • EKS Jobs         │
└─────────────────────┘  └─────────────────────┘  └─────────────────────┘
```

## Repository Structure

```
terraform-aws-ops/
├── modules/
│   ├── step-functions/
│   │   ├── db/              # 20 SFNs - Aurora/RDS operations
│   │   ├── efs/             # 8 SFNs  - EFS backup & replication
│   │   ├── eks/             # 7 SFNs  - Kubernetes orchestration
│   │   ├── utils/           # 7 SFNs  - Notifications, cleanup, tagging
│   │   ├── orchestrator/    # 1 SFN   - 5-phase refresh pipeline
│   │   └── audit/           # 1 SFN   - CloudTrail audit
│   ├── iam/                 # Cross-account IAM roles
│   ├── lambda-code/         # Lambda S3 packaging & upload
│   ├── source-account/      # Production account resources
│   └── destination-account/ # Non-prod account resources
├── lambdas/                 # 45+ Lambda implementations
├── docker/
│   └── mysql-s3/            # MySQL dump/import Docker images
├── scripts/                 # Input generation & validation
├── specs/                   # Detailed specifications (21 specs)
├── docs/                    # Reference documentation
├── tests/                   # ASL validation & unit tests
├── main.tf                  # Root module
├── variables.tf
├── outputs.tf
└── versions.tf
```

## Step Functions

### Database (`modules/step-functions/db`) — 20 Step Functions

| Operation | Step Function | Description |
|-----------|--------------|-------------|
| **Restore** | `restore_cluster` | Restore Aurora cluster from snapshot |
| | `prepare_snapshot_for_restore` | Share + wait for snapshot availability |
| | `ensure_cluster_not_exists` | Ensure target cluster is deleted before restore |
| | `ensure_cluster_available` | Wait for cluster availability |
| **Snapshot** | `create_manual_snapshot` | Create manual cluster snapshot |
| | `share_snapshot` | Share snapshot to destination account |
| | `list_shared_snapshots` | List snapshots shared from source |
| **Instance** | `create_instance` | Create RDS instance in a cluster |
| | `rename_cluster` | Rename cluster (blue/green swap) |
| | `stop_cluster` | Stop Aurora cluster |
| | `delete_cluster` | Delete cluster and all instances |
| **Data** | `run_mysqldump_on_eks` | Dump via K8s job (public API) |
| | `run_mysqldump_on_eks_private` | Dump via Lambda proxy (private endpoint) |
| | `run_mysqlimport_on_eks` | Import via K8s job (public API) |
| | `run_mysqlimport_on_eks_private` | Import via Lambda proxy (private endpoint) |
| | `run_sql_from_s3` | Execute SQL scripts from S3 |
| | `run_sql_lambda` | Execute SQL via Lambda |
| **Config** | `enable_master_secret` | Enable Secrets Manager for master credentials |
| | `rotate_secrets` | Rotate database secrets |
| | `configure_s3_integration` | Configure Aurora S3 integration |

### EFS (`modules/step-functions/efs`) — 8 Step Functions

| Step Function | Description |
|--------------|-------------|
| `restore_from_backup` | Restore EFS from AWS Backup vault |
| `create_filesystem` | Create new EFS filesystem with mount targets |
| `delete_filesystem` | Delete EFS filesystem and mount targets |
| `setup_cross_account_replication` | Setup cross-account EFS replication |
| `delete_replication` | Delete EFS replication configuration |
| `check_replication_sync` | Check replication sync status |
| `get_subpath_and_store_in_ssm` | Find backup restore directory, store in SSM |
| `cleanup_efs_lambdas` | Cleanup EFS-related Lambda resources |

### EKS (`modules/step-functions/eks`) — 7 Step Functions

| Step Function | Description |
|--------------|-------------|
| `manage_storage` | Manage EFS PV/PVC for EKS (public API) |
| `manage_storage_private` | Same via Lambda proxy (private endpoint) |
| `scale_services` | Scale deployments/statefulsets (public API) |
| `scale_services_private` | Same via Lambda proxy |
| `scale_nodegroup_asg` | Scale EKS nodegroup ASG min/max/desired |
| `verify_and_restart_services` | Verify pod health and restart if needed (public) |
| `verify_and_restart_services_private` | Same via Lambda proxy |

### Utils (`modules/step-functions/utils`) — 7 Step Functions

| Step Function | Description |
|--------------|-------------|
| `prepare_refresh` | Build execution context from config |
| `validate_refresh_config` | Validate refresh configuration |
| `cleanup_and_stop` | Cleanup temp resources, stop cluster if requested |
| `tag_resources` | Tag AWS resources post-refresh |
| `run_archive_job` | Run archive/backup K8s job (public API) |
| `run_archive_job_private` | Same via Lambda proxy |
| `notify` | Send notifications (SNS/DynamoDB) |

### Orchestrator (`modules/step-functions/orchestrator`) — 1 Step Function

5-phase refresh pipeline coordinating all other Step Functions:

1. **Phase 1 — Database**: Snapshot → share → restore → create instance → enable secrets → mysqldump → mysqlimport
2. **Phase 2 — EFS**: Cross-account replication or backup restore → mount target setup
3. **Phase 3 — EKS**: Scale nodegroups → manage storage → scale services → verify health
4. **Phase 4 — Cleanup**: Tag resources → stop/delete temp clusters → cleanup
5. **Phase 5 — Notify**: Send completion notifications

Supports **synchronized cutoff mode**: EFS replication sync → parallel {manual snapshot + delete replication} → DB restore, ensuring DB and EFS are consistent.

### Audit (`modules/step-functions/audit`) — 1 Step Function

| Step Function | Description |
|--------------|-------------|
| `audit_resource` | CloudTrail-based resource change audit trail |

## Deployment Model

Each AWS account has its own Terraform deployment:

| Account | Module | Description |
|---------|--------|-------------|
| Shared Services | Root module | Orchestrator + all Step Functions |
| Production | `modules/source-account` | IAM role for snapshot/backup read access |
| Staging/Dev | `modules/destination-account` | IAM role + Lambda helpers + K8s proxy |

**Deployment order**: Source & destination accounts first, then shared services (needs role ARNs).

## Usage

### Source Account (Production)

```hcl
module "refresh_source" {
  source = "git::https://github.com/KamorionLabs/terraform-aws-ops.git//modules/source-account?ref=v1.22.0"

  prefix                = "myapp-refresh"
  orchestrator_role_arn = "arn:aws:iam::000000000000:role/myapp-refresh-orchestrator"

  kms_key_arns = ["arn:aws:kms:eu-central-1:111111111111:key/xxx"]

  tags = { Project = "database-refresh", Environment = "production" }
}
```

### Destination Account (Staging, Dev)

```hcl
module "refresh_destination" {
  source = "git::https://github.com/KamorionLabs/terraform-aws-ops.git//modules/destination-account?ref=v1.22.0"

  prefix                = "myapp-refresh"
  orchestrator_role_arn = "arn:aws:iam::000000000000:role/myapp-refresh-orchestrator"

  deploy_lambdas = true
  vpc_id         = "vpc-xxx"
  subnet_ids     = ["subnet-xxx", "subnet-yyy"]

  enable_efs           = true
  efs_access_point_arn = "arn:aws:elasticfilesystem:eu-central-1:222222222222:access-point/fsap-xxx"

  create_eks_access_entry = true
  eks_cluster_name        = "my-cluster"

  tags = { Project = "database-refresh", Environment = "staging" }
}
```

### Shared Services (Orchestrator)

```hcl
module "refresh" {
  source = "git::https://github.com/KamorionLabs/terraform-aws-ops.git?ref=v1.22.0"

  prefix = "myapp-refresh"

  source_role_arns = [
    "arn:aws:iam::111111111111:role/myapp-refresh-source-role"
  ]
  destination_role_arns = [
    "arn:aws:iam::222222222222:role/myapp-refresh-destination-role"
  ]

  tags = { Project = "database-refresh", Environment = "shared-services" }
}
```

## Docker Images

The [`mysql-s3`](docker/mysql-s3/) image provides MySQL dump and import with S3 sync, used by the `run_mysqldump_on_eks` and `run_mysqlimport_on_eks` Step Functions.

| Image | Tag | Base | Client |
|-------|-----|------|--------|
| `kamorion/mysql-s3` | `latest`, `mariadb` | Alpine 3.23 | MariaDB |
| `kamorion/mysql-s3` | `mysql8` | Oracle Linux 9 | MySQL 8.4 LTS |
| `ghcr.io/kamorionlabs/mysql-s3` | Same tags | Same | Same |

Multi-arch: `linux/amd64`, `linux/arm64`. See [docker/mysql-s3/README.md](docker/mysql-s3/README.md) for full documentation.

## Lambda Functions

45+ Lambda functions organized by category:

| Category | Examples | Purpose |
|----------|----------|---------|
| **Database** | `run-sql`, `run-scripts-mysql` | SQL execution on Aurora |
| **EFS** | `check-flag-file`, `get-efs-subpath` | EFS validation & path resolution |
| **K8s Proxy** | `k8s-proxy` | Private EKS endpoint access |
| **Fetchers** | `fetch-cloudfront`, `fetch-ssm`, `fetch-secrets` | Read config from source |
| **Checkers** | `app-component-checker`, `infra-checker` | Pre/post-refresh validation |
| **Comparators** | `compare-rds`, `compare-pods`, `compare-dns`, ... | Before/after state comparison |
| **Processors** | `process-alb`, `process-efs-replication`, ... | Data transformation |

See [docs/lambdas-reference.md](docs/lambdas-reference.md) for details.

## CI/CD

| Workflow | Trigger | Purpose |
|----------|---------|---------|
| [`docker-mysql-s3.yml`](.github/workflows/docker-mysql-s3.yml) | `docker/mysql-s3/**` | Build multi-arch Docker images → GHCR + Docker Hub + Trivy scan |
| [`terraform.yml`](.github/workflows/terraform.yml) | `**.tf` | Terraform fmt, validate, TFLint, Trivy, Checkov |
| [`step-functions.yml`](.github/workflows/step-functions.yml) | ASL files | Step Functions ASL syntax validation |

## Inputs

| Name | Description | Type | Default | Required |
|------|-------------|------|---------|:--------:|
| `prefix` | Prefix for all resource names | `string` | `"refresh"` | no |
| `tags` | Tags to apply to all resources | `map(string)` | `{}` | no |
| `source_role_arns` | IAM role ARNs in source accounts | `list(string)` | - | yes |
| `destination_role_arns` | IAM role ARNs in destination accounts | `list(string)` | - | yes |
| `deploy_lambda_code` | Deploy Lambda code to S3 | `bool` | `true` | no |
| `lambda_code_bucket_name` | S3 bucket for Lambda code | `string` | `""` | no |
| `enable_step_functions_logging` | Enable CloudWatch logging | `bool` | `true` | no |
| `log_retention_days` | CloudWatch log retention | `number` | `30` | no |
| `enable_xray_tracing` | Enable X-Ray tracing | `bool` | `false` | no |

## Outputs

| Name | Description |
|------|-------------|
| `orchestrator_role_arn` | Orchestrator IAM role ARN |
| `orchestrator_role_name` | Orchestrator IAM role name |
| `orchestrator_arn` | Main orchestrator Step Function ARN |
| `orchestrator_name` | Main orchestrator Step Function name |
| `step_functions_db` | Map of database Step Function ARNs |
| `step_functions_efs` | Map of EFS Step Function ARNs |
| `step_functions_eks` | Map of EKS Step Function ARNs |
| `step_functions_utils` | Map of utils Step Function ARNs |
| `all_step_function_arns` | Consolidated map of all Step Function ARNs |

## Requirements

| Name | Version |
|------|---------|
| Terraform / OpenTofu | >= 1.0 |
| AWS Provider | >= 5.0 |

## License

Apache 2.0

## Authors

[KamorionLabs](https://github.com/KamorionLabs)
