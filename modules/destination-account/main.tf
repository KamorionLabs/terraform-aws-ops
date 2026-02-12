# -----------------------------------------------------------------------------
# Destination Account Module
# Deploys IAM role and Lambda helpers in destination (non-prod) accounts
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

  ssm_parameter_prefixes = distinct(compact(concat(
    [local.prefixes.lambda],
    var.ssm_parameter_prefixes
  )))
  ssm_parameter_arns = [
    for prefix in local.ssm_parameter_prefixes :
    "arn:aws:ssm:*:${local.account_id}:parameter/${prefix}/*"
  ]

  # Use existing role or created role
  role_arn  = var.create_role ? aws_iam_role.destination[0].arn : var.existing_role_arn
  role_name = var.create_role ? aws_iam_role.destination[0].name : var.existing_role_name
  role_id   = var.create_role ? aws_iam_role.destination[0].id : var.existing_role_name

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

resource "aws_iam_role" "destination" {
  count = var.create_role ? 1 : 0

  name = "${local.prefixes.iam_role}-destination-role"

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
# IAM Policy - RDS Full Access
# -----------------------------------------------------------------------------

resource "aws_iam_role_policy" "rds_access" {
  count = local.should_attach_policies ? 1 : 0

  name = "${local.prefixes.iam_policy}-rds-access"
  role = local.role_id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "RDSFullAccess"
        Effect = "Allow"
        Action = [
          "rds:*"
        ]
        Resource = "*"
      },
      {
        Sid    = "RDSPassRole"
        Effect = "Allow"
        Action = "iam:PassRole"
        Resource = "arn:aws:iam::${local.account_id}:role/*"
        Condition = {
          StringEquals = {
            "iam:PassedToService" = "rds.amazonaws.com"
          }
        }
      }
    ]
  })
}

# -----------------------------------------------------------------------------
# IAM Policy - Secrets Manager
# -----------------------------------------------------------------------------

resource "aws_iam_role_policy" "secrets_access" {
  count = local.should_attach_policies ? 1 : 0

  name = "${local.prefixes.iam_policy}-secrets-access"
  role = local.role_id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "SecretsManagerFullAccess"
        Effect = "Allow"
        Action = [
          "secretsmanager:*"
        ]
        Resource = "*"
      }
    ]
  })
}

# -----------------------------------------------------------------------------
# IAM Policy - EFS
# -----------------------------------------------------------------------------

resource "aws_iam_role_policy" "efs_access" {
  count = local.should_attach_policies && var.enable_efs ? 1 : 0

  name = "${local.prefixes.iam_policy}-efs-access"
  role = local.role_id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "EFSFullAccess"
        Effect = "Allow"
        Action = [
          "elasticfilesystem:*"
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
        Sid    = "BackupAccess"
        Effect = "Allow"
        Action = [
          "backup:StartRestoreJob",
          "backup:DescribeRestoreJob",
          "backup:GetRecoveryPointRestoreMetadata",
          "backup:ListRecoveryPointsByResource"
        ]
        Resource = "*"
      },
      {
        Sid    = "BackupPassRole"
        Effect = "Allow"
        Action = [
          "iam:PassRole"
        ]
        Resource = "arn:aws:iam::${local.account_id}:role/*"
        Condition = {
          StringEquals = {
            "iam:PassedToService" = "backup.amazonaws.com"
          }
        }
      }
    ]
  })
}

# -----------------------------------------------------------------------------
# IAM Policy - EKS
# -----------------------------------------------------------------------------

resource "aws_iam_role_policy" "eks_access" {
  count = local.should_attach_policies && var.enable_eks ? 1 : 0

  name = "${local.prefixes.iam_policy}-eks-access"
  role = local.role_id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "EKSDescribe"
        Effect = "Allow"
        Action = [
          "eks:DescribeCluster",
          "eks:ListClusters",
          "eks:DescribeNodegroup",
          "eks:ListNodegroups"
        ]
        Resource = "*"
      },
      {
        Sid    = "EKSRunJob"
        Effect = "Allow"
        Action = [
          "eks:AccessKubernetesApi"
        ]
        Resource = "arn:aws:eks:*:${local.account_id}:cluster/*"
      },
      {
        Sid    = "AutoScaling"
        Effect = "Allow"
        Action = [
          "autoscaling:DescribeAutoScalingGroups",
          "autoscaling:UpdateAutoScalingGroup",
          "autoscaling:SetDesiredCapacity"
        ]
        Resource = "*"
      }
    ]
  })
}

# -----------------------------------------------------------------------------
# IAM Policy - Lambda Invoke
# -----------------------------------------------------------------------------

resource "aws_iam_role_policy" "lambda_access" {
  count = local.should_attach_policies ? 1 : 0

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
# IAM Policy - SSM Parameters
# -----------------------------------------------------------------------------

resource "aws_iam_role_policy" "ssm_access" {
  count = local.should_attach_policies ? 1 : 0

  name = "${local.prefixes.iam_policy}-ssm-access"
  role = local.role_id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "SSMParameters"
        Effect = "Allow"
        Action = [
          "ssm:GetParameter",
          "ssm:PutParameter",
          "ssm:DeleteParameter"
        ]
        Resource = local.ssm_parameter_arns
      },
      {
        Sid    = "SSMReadAllParameters"
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
# IAM Policy - S3 Access
# -----------------------------------------------------------------------------

resource "aws_iam_role_policy" "s3_access" {
  count = local.should_attach_policies ? 1 : 0

  name = "${local.prefixes.iam_policy}-s3-access"
  role = local.role_id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "S3Access"
        Effect = "Allow"
        Action = [
          "s3:GetObject",
          "s3:ListBucket",
          "s3:PutObject"
        ]
        Resource = var.s3_bucket_arns
      }
    ]
  })
}

# -----------------------------------------------------------------------------
# IAM Policy - KMS
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
          "kms:Decrypt",
          "kms:Encrypt",
          "kms:GenerateDataKey*",
          "kms:DescribeKey",
          "kms:CreateGrant"
        ]
        Resource = var.kms_key_arns
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
          "tag:TagResources",
          "tag:UntagResources",
          "tag:GetResources"
        ]
        Resource = "*"
      }
    ]
  })
}

# -----------------------------------------------------------------------------
# IAM Policy - DynamoDB (for notifications)
# -----------------------------------------------------------------------------

resource "aws_iam_role_policy" "dynamodb_access" {
  count = local.should_attach_policies && var.dynamodb_table_arn != null ? 1 : 0

  name = "${local.prefixes.iam_policy}-dynamodb-access"
  role = local.role_id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "DynamoDBAccess"
        Effect = "Allow"
        Action = [
          "dynamodb:PutItem",
          "dynamodb:UpdateItem",
          "dynamodb:GetItem"
        ]
        Resource = var.dynamodb_table_arn
      }
    ]
  })
}

# -----------------------------------------------------------------------------
# IAM Policy - SNS (for notifications)
# -----------------------------------------------------------------------------

resource "aws_iam_role_policy" "sns_access" {
  count = local.should_attach_policies && var.sns_topic_arn != null ? 1 : 0

  name = "${local.prefixes.iam_policy}-sns-access"
  role = local.role_id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "SNSPublish"
        Effect = "Allow"
        Action = [
          "sns:Publish"
        ]
        Resource = var.sns_topic_arn
      }
    ]
  })
}

# -----------------------------------------------------------------------------
# EKS Access Entry - Grants Kubernetes API access to the destination role
# -----------------------------------------------------------------------------

resource "aws_eks_access_entry" "destination" {
  count = var.create_eks_access_entry && var.eks_cluster_name != null ? 1 : 0

  cluster_name  = var.eks_cluster_name
  principal_arn = local.role_arn
  type          = "STANDARD"

  tags = var.tags
}

# Build effective policies list - use new variable if provided, else fallback to legacy variables
locals {
  eks_effective_policies = length(var.eks_access_policies) > 0 ? var.eks_access_policies : [
    {
      name       = "default"
      policy_arn = var.eks_access_policy_arn
      scope_type = var.eks_access_scope_type
      namespaces = var.eks_access_scope_namespaces
    }
  ]

  eks_policies_map = var.create_eks_access_entry && var.eks_cluster_name != null ? {
    for policy in local.eks_effective_policies : policy.name => policy
  } : {}
}

resource "aws_eks_access_policy_association" "destination" {
  for_each = local.eks_policies_map

  cluster_name  = var.eks_cluster_name
  principal_arn = local.role_arn
  policy_arn    = each.value.policy_arn

  access_scope {
    type       = each.value.scope_type
    namespaces = each.value.scope_type == "namespace" ? each.value.namespaces : null
  }

  depends_on = [aws_eks_access_entry.destination]
}
