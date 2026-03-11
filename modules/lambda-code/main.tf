# -----------------------------------------------------------------------------
# Lambda Code Module
# Packages and uploads Lambda code to S3 for dynamic creation by Step Functions
# Supports creating a new bucket or using an existing one (create_bucket = false)
# -----------------------------------------------------------------------------

data "aws_caller_identity" "current" {}
data "aws_region" "current" {}

locals {
  account_id = data.aws_caller_identity.current.account_id
  region     = data.aws_region.current.id

  # S3 bucket name - existing bucket, custom name, or auto-generated
  bucket_name = var.create_bucket ? (
    var.bucket_name != null ? var.bucket_name : "${var.prefix}-lambda-code-${local.account_id}"
  ) : var.existing_bucket_name

  # Filter cross-account role ARNs - remove empty/null and validate IAM ARN format
  valid_role_arns = [
    for arn in var.cross_account_role_arns : arn
    if arn != null && arn != "" && can(regex("^arn:aws:iam::[0-9]+:role/", arn))
  ]

  # Lambda functions to package and upload to S3
  lambda_functions = {
    "check-flag-file" = {
      source_file = "${path.module}/../../lambdas/check-flag-file/check_flag_file.py"
      handler     = "check_flag_file.lambda_handler"
      description = "Manage replication flag file in EFS (write/check/delete)"
    }
    "get-efs-subpath" = {
      source_file = "${path.module}/../../lambdas/get-efs-subpath/get_efs_subpath.py"
      handler     = "get_efs_subpath.lambda_handler"
      description = "Find EFS restore subpath from AWS Backup"
    }
    "cross-region-rds-proxy" = {
      source_file = "${path.module}/../../lambdas/cross-region-rds-proxy/cross_region_rds_proxy.py"
      handler     = "cross_region_rds_proxy.lambda_handler"
      description = "Cross-region AWS API proxy for Step Functions (assumes role, calls API in source region, normalizes response keys)"
    }
  }
}

# -----------------------------------------------------------------------------
# S3 Bucket for Lambda Code (only when create_bucket = true)
# -----------------------------------------------------------------------------

resource "aws_s3_bucket" "lambda_code" {
  count  = var.create_bucket ? 1 : 0
  bucket = local.bucket_name

  tags = merge(var.tags, {
    Name    = local.bucket_name
    Purpose = "Lambda code storage for Step Functions dynamic deployment"
  })
}

resource "aws_s3_bucket_versioning" "lambda_code" {
  count  = var.create_bucket ? 1 : 0
  bucket = aws_s3_bucket.lambda_code[0].id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "lambda_code" {
  count  = var.create_bucket ? 1 : 0
  bucket = aws_s3_bucket.lambda_code[0].id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "lambda_code" {
  count  = var.create_bucket ? 1 : 0
  bucket = aws_s3_bucket.lambda_code[0].id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# -----------------------------------------------------------------------------
# S3 Bucket Policy for Cross-Account Access (only when create_bucket = true)
# -----------------------------------------------------------------------------

resource "aws_s3_bucket_policy" "lambda_code" {
  count  = var.create_bucket && length(local.valid_role_arns) > 0 ? 1 : 0
  bucket = aws_s3_bucket.lambda_code[0].id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "AllowCrossAccountGetObject"
        Effect = "Allow"
        Principal = {
          AWS = local.valid_role_arns
        }
        Action = [
          "s3:GetObject",
          "s3:GetObjectVersion"
        ]
        Resource = "${aws_s3_bucket.lambda_code[0].arn}/*"
      },
      {
        Sid    = "AllowCrossAccountListBucket"
        Effect = "Allow"
        Principal = {
          AWS = local.valid_role_arns
        }
        Action   = "s3:ListBucket"
        Resource = aws_s3_bucket.lambda_code[0].arn
      }
    ]
  })
}

# -----------------------------------------------------------------------------
# Package Lambda Code
# -----------------------------------------------------------------------------

data "archive_file" "lambda_functions" {
  for_each = local.lambda_functions

  type             = "zip"
  source_file      = each.value.source_file
  output_file_mode = "0666"
  output_path      = "${path.module}/../../lambdas/${each.key}.zip"
}

# -----------------------------------------------------------------------------
# Upload Lambda Code to S3
# -----------------------------------------------------------------------------

resource "aws_s3_object" "lambda_code" {
  for_each = data.archive_file.lambda_functions

  bucket = local.bucket_name
  key    = "lambdas/${each.key}.zip"
  source = each.value.output_path
  etag   = each.value.output_md5

  # S3 Object tags limited to 10 - completely disable provider default_tags
  override_provider {
    default_tags {
      tags = {}
    }
  }
}
