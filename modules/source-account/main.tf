# -----------------------------------------------------------------------------
# Source Account Module
# Deploys IAM role in source (production) accounts for cross-account access
# -----------------------------------------------------------------------------

data "aws_caller_identity" "current" {}
data "aws_region" "current" {}

locals {
  account_id = data.aws_caller_identity.current.account_id
  region     = data.aws_region.current.id

  # Resource prefixes - fall back to var.prefix if not specified
  prefixes = {
    iam_role       = coalesce(var.resource_prefixes.iam_role, var.prefix)
    iam_policy     = coalesce(var.resource_prefixes.iam_policy, var.prefix)
    security_group = coalesce(var.resource_prefixes.security_group, var.prefix)
    lambda         = coalesce(var.resource_prefixes.lambda, var.prefix)
    log_group      = coalesce(var.resource_prefixes.log_group, var.prefix)
  }

  # Dynamic lambda prefix for Step Function created Lambdas
  # If dynamic_lambda_prefix is set and different from prefixes.lambda, include both patterns
  dynamic_lambda_prefix = coalesce(var.dynamic_lambda_prefix, var.prefix)
  lambda_resource_arns = distinct(concat(
    ["arn:aws:lambda:*:${local.account_id}:function:${local.prefixes.lambda}-*"],
    local.dynamic_lambda_prefix != local.prefixes.lambda ? ["arn:aws:lambda:*:${local.account_id}:function:${local.dynamic_lambda_prefix}-*"] : []
  ))

  # Use existing role or created role
  role_arn  = var.create_role ? aws_iam_role.source[0].arn : var.existing_role_arn
  role_name = var.create_role ? aws_iam_role.source[0].name : var.existing_role_name
  role_id   = var.create_role ? aws_iam_role.source[0].id : var.existing_role_name

  # Determine if we should attach policies
  # Use variables known at plan time to avoid "count depends on resource attributes" error
  should_attach_policies = var.attach_policies && (var.create_role || var.existing_role_name != null)
}

# -----------------------------------------------------------------------------
# IAM Role - Assumable by Orchestrator and additional principals (optional)
# -----------------------------------------------------------------------------

locals {
  # Combine orchestrator role with additional trust principals
  all_trust_principals = compact(concat(
    var.orchestrator_role_arn != null ? [var.orchestrator_role_arn] : [],
    var.additional_trust_principal_arns
  ))
}

resource "aws_iam_role" "source" {
  count = var.create_role ? 1 : 0

  name = "${local.prefixes.iam_role}-source-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      merge(
        {
          Effect = "Allow"
          Principal = {
            AWS = local.all_trust_principals
          }
          Action = "sts:AssumeRole"
        },
        var.aws_organization_id != null ? {
          Condition = {
            StringEquals = {
              "aws:PrincipalOrgID" = var.aws_organization_id
            }
          }
        } : {}
      )
    ]
  })

  tags = var.tags
}

# -----------------------------------------------------------------------------
# IAM Policy - RDS Read & Snapshot
# -----------------------------------------------------------------------------

resource "aws_iam_role_policy" "rds_access" {
  count = local.should_attach_policies ? 1 : 0

  name = "${local.prefixes.iam_policy}-rds-access"
  role = local.role_id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "RDSRead"
        Effect = "Allow"
        Action = [
          "rds:DescribeDBClusters",
          "rds:DescribeDBClusterSnapshots",
          "rds:DescribeDBInstances",
          "rds:ListTagsForResource"
        ]
        Resource = "*"
      },
      {
        Sid    = "RDSSnapshot"
        Effect = "Allow"
        Action = [
          "rds:CreateDBClusterSnapshot",
          "rds:CopyDBClusterSnapshot",
          "rds:AddTagsToResource"
        ]
        Resource = [
          "arn:aws:rds:*:${local.account_id}:cluster:*",
          "arn:aws:rds:*:${local.account_id}:cluster-snapshot:*"
        ]
      },
      {
        Sid    = "RDSSnapshotSharing"
        Effect = "Allow"
        Action = [
          "rds:ModifyDBClusterSnapshotAttribute"
        ]
        Resource = "arn:aws:rds:*:${local.account_id}:cluster-snapshot:*"
      }
    ]
  })
}

# -----------------------------------------------------------------------------
# IAM Policy - KMS for Snapshot Encryption
# -----------------------------------------------------------------------------

resource "aws_iam_role_policy" "kms_access" {
  count = local.should_attach_policies ? 1 : 0

  name = "${local.prefixes.iam_policy}-kms-access"
  role = local.role_id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "KMSAccess"
        Effect = "Allow"
        Action = [
          "kms:DescribeKey",
          "kms:CreateGrant",
          "kms:Decrypt",
          "kms:Encrypt",
          "kms:GenerateDataKey*",
          "kms:ReEncrypt*"
        ]
        Resource = var.kms_key_arns
      }
    ]
  })
}

# -----------------------------------------------------------------------------
# IAM Policy - EFS for Backup
# -----------------------------------------------------------------------------

resource "aws_iam_role_policy" "efs_access" {
  count = local.should_attach_policies && var.enable_efs ? 1 : 0

  name = "${local.prefixes.iam_policy}-efs-access"
  role = local.role_id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "EFSRead"
        Effect = "Allow"
        Action = [
          "elasticfilesystem:DescribeFileSystems",
          "elasticfilesystem:DescribeMountTargets",
          "elasticfilesystem:DescribeAccessPoints",
          "elasticfilesystem:DescribeBackupPolicy",
          "elasticfilesystem:DescribeReplicationConfigurations",
          "elasticfilesystem:DescribeFileSystemPolicy"
        ]
        Resource = "*"
      },
      {
        Sid    = "EFSReplication"
        Effect = "Allow"
        Action = [
          "elasticfilesystem:CreateReplicationConfiguration",
          "elasticfilesystem:DeleteReplicationConfiguration",
          "elasticfilesystem:PutFileSystemPolicy"
        ]
        Resource = "arn:aws:elasticfilesystem:*:${local.account_id}:file-system/*"
      },
      {
        Sid    = "PassRoleToEFSReplication"
        Effect = "Allow"
        Action = [
          "iam:PassRole"
        ]
        Resource = "arn:aws:iam::${local.account_id}:role/${local.prefixes.iam_role}-efs-replication-role"
        Condition = {
          StringEquals = {
            "iam:PassedToService" = "elasticfilesystem.amazonaws.com"
          }
        }
      },
      {
        Sid    = "EFSAccessPointManage"
        Effect = "Allow"
        Action = [
          "elasticfilesystem:CreateAccessPoint",
          "elasticfilesystem:DeleteAccessPoint",
          "elasticfilesystem:TagResource"
        ]
        Resource = "*"
      },
      {
        Sid    = "CloudWatchMetrics"
        Effect = "Allow"
        Action = [
          "cloudwatch:GetMetricStatistics",
          "cloudwatch:GetMetricData"
        ]
        Resource = "*"
      },
      {
        Sid    = "BackupRead"
        Effect = "Allow"
        Action = [
          "backup:DescribeBackupVault",
          "backup:ListRecoveryPointsByBackupVault",
          "backup:ListRecoveryPointsByResource",
          "backup:DescribeRecoveryPoint",
          "backup:GetRecoveryPointRestoreMetadata"
        ]
        Resource = "*"
      },
      {
        Sid    = "BackupCopy"
        Effect = "Allow"
        Action = [
          "backup:CopyIntoBackupVault",
          "backup:StartCopyJob",
          "backup:DescribeCopyJob"
        ]
        Resource = "*"
      }
    ]
  })
}

# -----------------------------------------------------------------------------
# IAM Policy - Secrets Manager (read-only)
# -----------------------------------------------------------------------------

resource "aws_iam_role_policy" "secrets_access" {
  count = local.should_attach_policies ? 1 : 0

  name = "${local.prefixes.iam_policy}-secrets-access"
  role = local.role_id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "SecretsManagerRead"
        Effect = "Allow"
        Action = [
          "secretsmanager:GetSecretValue",
          "secretsmanager:DescribeSecret"
        ]
        Resource = "arn:aws:secretsmanager:*:${local.account_id}:secret:*"
      },
      {
        Sid      = "SecretsManagerList"
        Effect   = "Allow"
        Action   = ["secretsmanager:ListSecrets"]
        Resource = "*"
      }
    ]
  })
}

# -----------------------------------------------------------------------------
# IAM Policy - Tagging
# -----------------------------------------------------------------------------

resource "aws_iam_role_policy" "tagging_access" {
  count = local.should_attach_policies ? 1 : 0

  name = "${local.prefixes.iam_policy}-tagging-access"
  role = local.role_id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "Tagging"
        Effect = "Allow"
        Action = [
          "tag:GetResources"
        ]
        Resource = "*"
      }
    ]
  })
}

# -----------------------------------------------------------------------------
# IAM Policy - Lambda Dynamic Creation (for EFS flag file check)
# -----------------------------------------------------------------------------

resource "aws_iam_role_policy" "lambda_access" {
  count = local.should_attach_policies && var.enable_efs ? 1 : 0

  name = "${local.prefixes.iam_policy}-lambda-access"
  role = local.role_id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "LambdaInvoke"
        Effect = "Allow"
        Action = [
          "lambda:InvokeFunction"
        ]
        Resource = local.lambda_resource_arns
      },
      {
        Sid    = "LambdaManage"
        Effect = "Allow"
        Action = [
          "lambda:GetFunction",
          "lambda:GetFunctionConfiguration",
          "lambda:UpdateFunctionConfiguration",
          "lambda:UpdateFunctionCode",
          "lambda:CreateFunction",
          "lambda:DeleteFunction",
          "lambda:TagResource"
        ]
        Resource = local.lambda_resource_arns
      },
      {
        Sid    = "LambdaPassRole"
        Effect = "Allow"
        Action = [
          "iam:PassRole"
        ]
        Resource = [
          "arn:aws:iam::${local.account_id}:role/${local.prefixes.iam_role}-*"
        ]
        Condition = {
          StringEquals = {
            "iam:PassedToService" = "lambda.amazonaws.com"
          }
        }
      },
      {
        Sid    = "S3GetLambdaCode"
        Effect = "Allow"
        Action = [
          "s3:GetObject",
          "s3:GetObjectVersion"
        ]
        Resource = var.lambda_code_bucket_arn != null ? [
          "${var.lambda_code_bucket_arn}/*"
          ] : [
          "arn:aws:s3:::${local.prefixes.lambda}-lambda-code-*/*"
        ]
      },
      {
        Sid    = "EC2DescribeForVpcLambda"
        Effect = "Allow"
        Action = [
          "ec2:DescribeSecurityGroups",
          "ec2:DescribeSubnets",
          "ec2:DescribeVpcs"
        ]
        Resource = "*"
      }
    ]
  })
}

# -----------------------------------------------------------------------------
# Lambda Execution Role (for dynamically created Lambdas)
# -----------------------------------------------------------------------------

resource "aws_iam_role" "lambda" {
  count = var.deploy_lambda_role ? 1 : 0

  name = "${local.prefixes.iam_role}-lambda-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Principal = {
          Service = "lambda.amazonaws.com"
        }
        Action = "sts:AssumeRole"
      }
    ]
  })

  tags = var.tags
}

resource "aws_iam_role_policy" "lambda_execution" {
  count = var.deploy_lambda_role ? 1 : 0

  name = "${local.prefixes.iam_policy}-lambda-execution-policy"
  role = aws_iam_role.lambda[0].id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "CloudWatchLogs"
        Effect = "Allow"
        Action = [
          "logs:CreateLogGroup",
          "logs:CreateLogStream",
          "logs:PutLogEvents"
        ]
        Resource = "arn:aws:logs:*:${local.account_id}:log-group:/aws/lambda/${local.prefixes.log_group}-*:*"
      },
      {
        Sid    = "VPCAccess"
        Effect = "Allow"
        Action = [
          "ec2:CreateNetworkInterface",
          "ec2:DescribeNetworkInterfaces",
          "ec2:DeleteNetworkInterface",
          "ec2:AssignPrivateIpAddresses",
          "ec2:UnassignPrivateIpAddresses"
        ]
        Resource = "*"
      },
      {
        Sid    = "EFSAccess"
        Effect = "Allow"
        Action = [
          "elasticfilesystem:ClientMount",
          "elasticfilesystem:ClientWrite",
          "elasticfilesystem:ClientRead",
          "elasticfilesystem:DescribeFileSystems",
          "elasticfilesystem:DescribeAccessPoints",
          "elasticfilesystem:DescribeMountTargets"
        ]
        Resource = "*"
      }
    ]
  })
}

# -----------------------------------------------------------------------------
# Lambda Security Group
# -----------------------------------------------------------------------------

resource "aws_security_group" "lambda" {
  count = var.deploy_lambda_role && var.create_lambda_security_group ? 1 : 0

  name        = "${local.prefixes.security_group}-source-lambda-sg"
  description = "Security group for Lambda functions (flag file check)"
  vpc_id      = var.vpc_id

  lifecycle {
    create_before_destroy = true
  }

  tags = merge(var.tags, {
    Name = "${local.prefixes.security_group}-source-lambda-sg"
  })
}

resource "aws_security_group_rule" "lambda_https_egress" {
  count = var.deploy_lambda_role && var.create_lambda_security_group ? 1 : 0

  security_group_id = aws_security_group.lambda[0].id
  type              = "egress"
  protocol          = "tcp"
  from_port         = 443
  to_port           = 443
  cidr_blocks       = ["0.0.0.0/0"]
  description       = "Allow HTTPS outbound for AWS APIs"
}

resource "aws_security_group_rule" "lambda_nfs_egress" {
  count = var.deploy_lambda_role && var.create_lambda_security_group && var.enable_efs ? 1 : 0

  security_group_id = aws_security_group.lambda[0].id
  type              = "egress"
  protocol          = "tcp"
  from_port         = 2049
  to_port           = 2049
  cidr_blocks       = var.efs_cidr_blocks
  description       = "Allow NFS outbound to EFS"
}

# -----------------------------------------------------------------------------
# IAM Policy - SSM Parameter Store (for EFS subpath configuration)
# -----------------------------------------------------------------------------

resource "aws_iam_role_policy" "ssm_access" {
  count = local.should_attach_policies && var.enable_efs ? 1 : 0

  name = "${local.prefixes.iam_policy}-ssm-access"
  role = local.role_id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "SSMGetParameter"
        Effect = "Allow"
        Action = [
          "ssm:GetParameter",
          "ssm:GetParameters",
          "ssm:GetParametersByPath"
        ]
        Resource = "arn:aws:ssm:*:${local.account_id}:parameter/*"
      }
    ]
  })
}

# -----------------------------------------------------------------------------
# EFS Replication Role - Dedicated role for cross-account EFS replication
# This role is assumed by the EFS service to perform replication operations
# -----------------------------------------------------------------------------

# -----------------------------------------------------------------------------
# AWS Backup Role - Dedicated role for cross-account EFS backup copy
# This role is assumed by the AWS Backup service to perform copy operations
# -----------------------------------------------------------------------------

resource "aws_iam_role" "backup_efs" {
  count = var.enable_efs ? 1 : 0

  name = "${local.prefixes.iam_role}-backup-efs-copy-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect    = "Allow"
        Principal = { Service = "backup.amazonaws.com" }
        Action    = "sts:AssumeRole"
      }
    ]
  })

  tags = var.tags
}

resource "aws_iam_role_policy" "backup_efs" {
  count = var.enable_efs ? 1 : 0

  name = "${local.prefixes.iam_policy}-backup-efs-copy"
  role = aws_iam_role.backup_efs[0].id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "BackupCopyOperations"
        Effect = "Allow"
        Action = [
          "backup:CopyIntoBackupVault",
          "backup:DescribeCopyJob",
          "backup:GetRecoveryPointRestoreMetadata"
        ]
        Resource = "*"
      },
      {
        Sid    = "BackupVaultRead"
        Effect = "Allow"
        Action = [
          "backup:DescribeBackupVault",
          "backup:ListRecoveryPointsByBackupVault",
          "backup:ListRecoveryPointsByResource"
        ]
        Resource = "*"
      },
      {
        Sid    = "KMSForBackup"
        Effect = "Allow"
        Action = [
          "kms:Decrypt",
          "kms:GenerateDataKey",
          "kms:DescribeKey",
          "kms:CreateGrant"
        ]
        Resource = var.kms_key_arns
      }
    ]
  })
}

resource "aws_iam_role" "efs_replication" {
  count = var.enable_efs ? 1 : 0

  name = "${local.prefixes.iam_role}-efs-replication-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "AllowAssumeByEFSService"
        Effect = "Allow"
        Principal = {
          Service = "elasticfilesystem.amazonaws.com"
        }
        Action = "sts:AssumeRole"
      }
    ]
  })

  tags = var.tags
}

resource "aws_iam_role_policy" "efs_replication" {
  count = var.enable_efs ? 1 : 0

  name = "${local.prefixes.iam_policy}-efs-replication"
  role = aws_iam_role.efs_replication[0].id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "EFSReplicationRead"
        Effect = "Allow"
        Action = [
          "elasticfilesystem:ReplicationRead",
          "elasticfilesystem:DescribeFileSystems"
        ]
        Resource = "arn:aws:elasticfilesystem:*:${local.account_id}:file-system/*"
      },
      {
        Sid    = "EFSReplicationWriteDestination"
        Effect = "Allow"
        Action = [
          "elasticfilesystem:DescribeFileSystems",
          "elasticfilesystem:CreateReplicationConfiguration",
          "elasticfilesystem:DescribeReplicationConfigurations",
          "elasticfilesystem:DeleteReplicationConfiguration",
          "elasticfilesystem:ReplicationWrite"
        ]
        Resource = "*"
      }
    ]
  })
}
