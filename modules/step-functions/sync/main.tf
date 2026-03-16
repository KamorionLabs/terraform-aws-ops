# -----------------------------------------------------------------------------
# Sync Step Functions Module
# Step Functions for syncing config items (secrets/parameters) between AWS accounts
# -----------------------------------------------------------------------------

locals {
  step_functions = {
    sync_config_items = "sync_config_items.asl.json"
  }

  # Naming: pascal = "Sync-SyncConfigItems", kebab = "sync-sync-config-items"
  sfn_names = {
    for k, v in local.step_functions : k => (
      var.naming_convention == "pascal"
      ? "${var.prefix}-Sync-${replace(title(replace(k, "_", " ")), " ", "")}"
      : "${var.prefix}-sync-${replace(k, "_", "-")}"
    )
  }
}

# -----------------------------------------------------------------------------
# Lambda Function for Sync Config Items
# -----------------------------------------------------------------------------

data "archive_file" "sync_config_items" {
  type        = "zip"
  source_file = "${path.module}/../../../lambdas/sync-config-items/sync_config_items.py"
  output_path = "${path.module}/../../../lambdas/sync-config-items.zip"
}

resource "aws_lambda_function" "sync_config_items" {
  function_name = "${var.prefix}-sync-config-items"
  description   = "Sync config items (secrets/parameters) between AWS accounts"

  filename         = data.archive_file.sync_config_items.output_path
  source_code_hash = data.archive_file.sync_config_items.output_base64sha256

  handler = "sync_config_items.lambda_handler"
  runtime = "python3.12"
  timeout = 300
  role    = aws_iam_role.lambda_sync_config_items.arn

  environment {
    variables = {
      LOG_LEVEL = var.log_level
    }
  }

  tags = merge(var.tags, {
    Module = "sync"
    Name   = "${var.prefix}-sync-config-items"
  })
}

# -----------------------------------------------------------------------------
# IAM Role for Lambda
# -----------------------------------------------------------------------------

resource "aws_iam_role" "lambda_sync_config_items" {
  name = "${var.prefix}-lambda-sync-config-items"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action = "sts:AssumeRole"
        Effect = "Allow"
        Principal = {
          Service = "lambda.amazonaws.com"
        }
      }
    ]
  })

  tags = var.tags
}

resource "aws_iam_role_policy" "lambda_sync_config_items" {
  name = "${var.prefix}-lambda-sync-config-items"
  role = aws_iam_role.lambda_sync_config_items.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "AssumeRoleForCrossAccount"
        Effect   = "Allow"
        Action   = "sts:AssumeRole"
        Resource = var.cross_account_role_arns
      },
      {
        Sid    = "CloudWatchLogs"
        Effect = "Allow"
        Action = [
          "logs:CreateLogGroup",
          "logs:CreateLogStream",
          "logs:PutLogEvents"
        ]
        Resource = "arn:aws:logs:*:*:*"
      }
    ]
  })
}

# -----------------------------------------------------------------------------
# Step Functions Resources
# -----------------------------------------------------------------------------

resource "aws_sfn_state_machine" "sync" {
  for_each = local.step_functions

  name     = local.sfn_names[each.key]
  role_arn = var.orchestrator_role_arn

  definition = templatefile("${path.module}/${each.value}", {
    SyncConfigItemsLambdaArn = aws_lambda_function.sync_config_items.arn
  })

  logging_configuration {
    log_destination        = var.enable_logging ? "${aws_cloudwatch_log_group.sfn[0].arn}:*" : null
    include_execution_data = var.enable_logging
    level                  = var.enable_logging ? "ALL" : "OFF"
  }

  tracing_configuration {
    enabled = var.enable_xray_tracing
  }

  tags = merge(var.tags, {
    Module = "sync"
    Name   = local.sfn_names[each.key]
  })
}

# Grant Step Function permission to invoke Lambda
resource "aws_lambda_permission" "sfn_invoke" {
  statement_id  = "AllowStepFunctionsInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.sync_config_items.function_name
  principal     = "states.amazonaws.com"
  source_arn    = aws_sfn_state_machine.sync["sync_config_items"].arn
}

# -----------------------------------------------------------------------------
# CloudWatch Log Group
# -----------------------------------------------------------------------------

resource "aws_cloudwatch_log_group" "sfn" {
  count = var.enable_logging ? 1 : 0

  name              = "/aws/stepfunctions/${var.prefix}-sync"
  retention_in_days = var.log_retention_days

  tags = var.tags
}

resource "aws_cloudwatch_log_group" "lambda" {
  name              = "/aws/lambda/${aws_lambda_function.sync_config_items.function_name}"
  retention_in_days = var.log_retention_days

  tags = var.tags
}
