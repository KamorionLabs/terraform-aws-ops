# -----------------------------------------------------------------------------
# Lambda Functions for Destination Account
# -----------------------------------------------------------------------------

locals {
  python_version      = "3.11"
  python_version_long = "python${local.python_version}"
  lambdas_path        = "${path.module}/../../lambdas"

  lambda_layers = var.deploy_lambdas ? ["pymysql"] : []

  # Base lambda functions (always deployed if deploy_lambdas is true)
  lambda_functions_base = var.deploy_lambdas ? {
    "run-sql" = {
      path             = "${local.lambdas_path}/run-scripts-mysql/run_sql.py"
      handler          = "run_sql.lambda_handler"
      timeout          = 900
      memory_size      = 320
      layers           = ["pymysql"]
      description      = "Execute SQL scripts from S3 on MySQL/Aurora databases"
      efs_access_point = false
    }
  } : {}

  # EFS lambda functions (only deployed if enable_efs is true)
  lambda_functions_efs = var.deploy_lambdas && var.enable_efs ? {
    "get-efs-subpath" = {
      path             = "${local.lambdas_path}/get-efs-subpath/get_efs_subpath.py"
      handler          = "get_efs_subpath.lambda_handler"
      timeout          = 60
      memory_size      = 128
      layers           = []
      description      = "Find the restore backup directory in EFS"
      efs_access_point = true
    }
    "check-flag-file" = {
      path             = "${local.lambdas_path}/check-flag-file/check_flag_file.py"
      handler          = "check_flag_file.lambda_handler"
      timeout          = 60
      memory_size      = 128
      layers           = []
      description      = "Check replication flag file in EFS (write/check/delete)"
      efs_access_point = true
    }
  } : {}

  # Merged lambda functions
  lambda_functions_filtered = merge(local.lambda_functions_base, local.lambda_functions_efs)

  lambda_functions_layers = {
    for k, v in local.lambda_functions_filtered : k => [
      for layer in v.layers : aws_lambda_layer_version.layer[layer].arn
    ]
  }
}

# -----------------------------------------------------------------------------
# Lambda Layers - pip install
# -----------------------------------------------------------------------------

resource "null_resource" "pip_install" {
  for_each = toset(local.lambda_layers)

  triggers = {
    requirements_hash = sha256(file("${local.lambdas_path}/layers/${each.key}/requirements.txt"))
  }

  provisioner "local-exec" {
    command = <<-EOT
      python3 -m pip --isolated \
        install -r ${local.lambdas_path}/layers/${each.key}/requirements.txt \
        --platform manylinux2014_aarch64 \
        --implementation cp \
        --python-version ${local.python_version} \
        --only-binary=:all: --upgrade \
        -t ${local.lambdas_path}/layers/${each.key}/python
    EOT
  }
}

data "archive_file" "lambda_layers" {
  for_each    = null_resource.pip_install
  type        = "zip"
  source_dir  = "${local.lambdas_path}/layers/${each.key}/"
  output_path = "${local.lambdas_path}/layers/${each.key}.zip"
}

resource "aws_lambda_layer_version" "layer" {
  for_each = data.archive_file.lambda_layers

  layer_name               = "${local.prefixes.lambda}-${each.key}"
  filename                 = each.value.output_path
  source_code_hash         = each.value.output_base64sha256
  compatible_runtimes      = [local.python_version_long]
  compatible_architectures = ["arm64"]
}

# -----------------------------------------------------------------------------
# Lambda Functions - Archive
# -----------------------------------------------------------------------------

data "archive_file" "lambda_functions" {
  for_each = local.lambda_functions_filtered

  type             = "zip"
  source_file      = each.value.path
  output_file_mode = "0666"
  output_path      = "${local.lambdas_path}/${each.key}.zip"
}

# -----------------------------------------------------------------------------
# Lambda Functions - Deployment (direct upload, no S3)
# -----------------------------------------------------------------------------

resource "aws_lambda_function" "functions" {
  for_each = data.archive_file.lambda_functions

  function_name    = "${local.prefixes.lambda}-${each.key}"
  description      = lookup(local.lambda_functions_filtered[each.key], "description", "")
  role             = aws_iam_role.lambda[0].arn
  filename         = each.value.output_path
  source_code_hash = each.value.output_base64sha256
  handler          = local.lambda_functions_filtered[each.key].handler
  runtime          = local.python_version_long
  architectures    = ["arm64"]
  layers           = lookup(local.lambda_functions_layers, each.key, [])
  timeout          = lookup(local.lambda_functions_filtered[each.key], "timeout", 300)
  memory_size      = lookup(local.lambda_functions_filtered[each.key], "memory_size", 320)

  vpc_config {
    security_group_ids = var.create_lambda_security_group ? [aws_security_group.lambda[0].id] : var.lambda_security_group_ids
    subnet_ids         = var.lambda_subnet_ids
  }

  dynamic "file_system_config" {
    for_each = lookup(local.lambda_functions_filtered[each.key], "efs_access_point", false) && var.efs_access_point_arn != null ? [1] : []
    content {
      arn              = var.efs_access_point_arn
      local_mount_path = "/mnt/efs"
    }
  }

  environment {
    variables = {
      LOG_LEVEL = var.lambda_log_level
    }
  }

  tags = var.tags

  depends_on = [
    aws_iam_role_policy.lambda[0],
    aws_cloudwatch_log_group.lambda
  ]
}

# -----------------------------------------------------------------------------
# Lambda IAM Role
# -----------------------------------------------------------------------------

resource "aws_iam_role" "lambda" {
  count = var.deploy_lambdas ? 1 : 0

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

resource "aws_iam_role_policy" "lambda" {
  count = var.deploy_lambdas ? 1 : 0

  name = "${local.prefixes.iam_policy}-lambda-policy"
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
        Sid    = "RDSDescribe"
        Effect = "Allow"
        Action = [
          "rds:DescribeDBClusters",
          "rds:DescribeDBInstances"
        ]
        Resource = "*"
      },
      {
        Sid    = "SecretsManager"
        Effect = "Allow"
        Action = [
          "secretsmanager:GetSecretValue"
        ]
        Resource = "arn:aws:secretsmanager:*:${local.account_id}:secret:*"
      },
      {
        Sid    = "S3Access"
        Effect = "Allow"
        Action = [
          "s3:GetObject"
        ]
        Resource = var.s3_bucket_arns
      },
      {
        Sid    = "EFSAccess"
        Effect = "Allow"
        Action = [
          "elasticfilesystem:ClientMount",
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
  count = (var.deploy_lambdas || var.enable_k8s_proxy) && var.create_lambda_security_group ? 1 : 0

  name        = "${local.prefixes.security_group}-dest-lambda-sg"
  description = "Security group for Lambda functions"
  vpc_id      = var.vpc_id

  lifecycle {
    create_before_destroy = true
  }

  tags = merge(var.tags, {
    Name = "${local.prefixes.security_group}-dest-lambda-sg"
  })
}

resource "aws_security_group_rule" "lambda_https_egress" {
  count = (var.deploy_lambdas || var.enable_k8s_proxy) && var.create_lambda_security_group ? 1 : 0

  security_group_id = aws_security_group.lambda[0].id
  type              = "egress"
  protocol          = "tcp"
  from_port         = 443
  to_port           = 443
  cidr_blocks       = ["0.0.0.0/0"]
  description       = "Allow HTTPS outbound for AWS APIs"
}

resource "aws_security_group_rule" "lambda_mysql_egress" {
  count = (var.deploy_lambdas || var.enable_k8s_proxy) && var.create_lambda_security_group ? 1 : 0

  security_group_id = aws_security_group.lambda[0].id
  type              = "egress"
  protocol          = "tcp"
  from_port         = 3306
  to_port           = 3306
  cidr_blocks       = var.database_cidr_blocks
  description       = "Allow MySQL outbound to database"
}

resource "aws_security_group_rule" "lambda_nfs_egress" {
  count = (var.deploy_lambdas || var.enable_k8s_proxy) && var.create_lambda_security_group && var.enable_efs ? 1 : 0

  security_group_id = aws_security_group.lambda[0].id
  type              = "egress"
  protocol          = "tcp"
  from_port         = 2049
  to_port           = 2049
  cidr_blocks       = var.efs_cidr_blocks
  description       = "Allow NFS outbound to EFS"
}

# -----------------------------------------------------------------------------
# CloudWatch Log Groups
# -----------------------------------------------------------------------------

resource "aws_cloudwatch_log_group" "lambda" {
  for_each = local.lambda_functions_filtered

  name              = "/aws/lambda/${local.prefixes.log_group}-${each.key}"
  retention_in_days = var.log_retention_days

  tags = var.tags
}

# -----------------------------------------------------------------------------
# K8s Proxy Lambda - uses destination role (not lambda role)
# Deployed in VPC to access EKS private endpoints
# -----------------------------------------------------------------------------

data "archive_file" "k8s_proxy" {
  count = var.enable_k8s_proxy ? 1 : 0

  type             = "zip"
  source_file      = coalesce(var.k8s_proxy_lambda_path, "${local.lambdas_path}/k8s-proxy/k8s_proxy.py")
  output_file_mode = "0666"
  output_path      = "${local.lambdas_path}/k8s-proxy.zip"
}

resource "aws_cloudwatch_log_group" "k8s_proxy" {
  count = var.enable_k8s_proxy ? 1 : 0

  name              = "/aws/lambda/${local.prefixes.log_group}-k8s-proxy"
  retention_in_days = var.log_retention_days

  tags = var.tags
}

resource "aws_lambda_function" "k8s_proxy" {
  count = var.enable_k8s_proxy ? 1 : 0

  function_name    = "${local.prefixes.lambda}-k8s-proxy"
  description      = "K8s API proxy for EKS private endpoint access"
  role             = local.role_arn # Uses destination role!
  filename         = data.archive_file.k8s_proxy[0].output_path
  source_code_hash = data.archive_file.k8s_proxy[0].output_base64sha256
  handler          = "k8s_proxy.lambda_handler"
  runtime          = local.python_version_long
  architectures    = ["arm64"]
  timeout          = 300
  memory_size      = 256

  vpc_config {
    security_group_ids = var.create_lambda_security_group ? [aws_security_group.lambda[0].id] : var.lambda_security_group_ids
    subnet_ids         = var.lambda_subnet_ids
  }

  environment {
    variables = {
      LOG_LEVEL = var.lambda_log_level
    }
  }

  tags = var.tags

  depends_on = [
    aws_iam_role_policy.k8s_proxy_lambda,
    aws_cloudwatch_log_group.k8s_proxy
  ]
}
