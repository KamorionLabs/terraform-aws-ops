# -----------------------------------------------------------------------------
# CloudFront Step Functions Module
# Slim SFN + Lambda to remove aliases from CloudFront distributions,
# optionally cross-account via AssumeRole.
# -----------------------------------------------------------------------------

locals {
  step_functions = {
    remove_aliases = "remove_aliases.asl.json"
  }

  sfn_names = {
    for k, v in local.step_functions : k => (
      var.naming_convention == "pascal"
      ? "${var.prefix}-CloudFront-${replace(title(replace(k, "_", " ")), " ", "")}"
      : "${var.prefix}-cloudfront-${replace(k, "_", "-")}"
    )
  }

  lambda_name = "${var.prefix}-cloudfront-alias-manager"
}

# -----------------------------------------------------------------------------
# Lambda
# -----------------------------------------------------------------------------

data "archive_file" "alias_manager" {
  type        = "zip"
  source_file = "${path.module}/../../../lambdas/cloudfront-alias-manager/cloudfront_alias_manager.py"
  output_path = "${path.module}/../../../lambdas/cloudfront-alias-manager.zip"
}

resource "aws_lambda_function" "alias_manager" {
  function_name = local.lambda_name
  description   = "Find and remove aliases on CloudFront distributions (same- or cross-account)"

  filename         = data.archive_file.alias_manager.output_path
  source_code_hash = data.archive_file.alias_manager.output_base64sha256

  handler = "cloudfront_alias_manager.lambda_handler"
  runtime = "python3.12"
  timeout = var.lambda_timeout
  role    = aws_iam_role.lambda.arn

  environment {
    variables = {
      LOG_LEVEL = var.log_level
    }
  }

  tags = merge(var.tags, {
    Module = "cloudfront"
    Name   = local.lambda_name
  })
}

# -----------------------------------------------------------------------------
# Lambda IAM
# -----------------------------------------------------------------------------

resource "aws_iam_role" "lambda" {
  name = "${var.prefix}-lambda-cloudfront-alias-manager"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Action    = "sts:AssumeRole"
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
    }]
  })

  tags = var.tags
}

resource "aws_iam_role_policy" "lambda" {
  name = "${var.prefix}-lambda-cloudfront-alias-manager"
  role = aws_iam_role.lambda.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = concat(
      [
        {
          Sid    = "CloudFrontSameAccount"
          Effect = "Allow"
          Action = [
            "cloudfront:ListDistributions",
            "cloudfront:GetDistribution",
            "cloudfront:GetDistributionConfig",
            "cloudfront:UpdateDistribution",
          ]
          Resource = "*"
        },
        {
          Sid      = "CloudWatchLogs"
          Effect   = "Allow"
          Action   = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"]
          Resource = "arn:aws:logs:*:*:*"
        },
      ],
      length(var.cross_account_role_arns) > 0 ? [{
        Sid      = "AssumeCrossAccountRoles"
        Effect   = "Allow"
        Action   = "sts:AssumeRole"
        Resource = var.cross_account_role_arns
      }] : []
    )
  })
}

resource "aws_cloudwatch_log_group" "lambda" {
  name              = "/aws/lambda/${aws_lambda_function.alias_manager.function_name}"
  retention_in_days = var.log_retention_days
  tags              = var.tags
}

# -----------------------------------------------------------------------------
# Step Function
# -----------------------------------------------------------------------------

resource "aws_sfn_state_machine" "cloudfront" {
  for_each = local.step_functions

  name     = local.sfn_names[each.key]
  role_arn = var.orchestrator_role_arn

  definition = templatefile("${path.module}/${each.value}", {
    CloudFrontAliasManagerLambdaArn = aws_lambda_function.alias_manager.arn
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
    Module = "cloudfront"
    Name   = local.sfn_names[each.key]
  })
}

resource "aws_lambda_permission" "sfn_invoke" {
  statement_id  = "AllowStepFunctionsInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.alias_manager.function_name
  principal     = "states.amazonaws.com"
  source_arn    = aws_sfn_state_machine.cloudfront["remove_aliases"].arn
}

resource "aws_cloudwatch_log_group" "sfn" {
  count             = var.enable_logging ? 1 : 0
  name              = "/aws/vendedlogs/states/${var.prefix}-cloudfront"
  retention_in_days = var.log_retention_days
  tags              = var.tags
}
