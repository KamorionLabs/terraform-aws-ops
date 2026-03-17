# -----------------------------------------------------------------------------
# IAM Module for Cross-Account Refresh
# Creates IAM roles for orchestrator, source, and destination accounts
# -----------------------------------------------------------------------------

data "aws_caller_identity" "current" {}
data "aws_region" "current" {}

locals {
  orchestrator_account_id = data.aws_caller_identity.current.account_id
}

# -----------------------------------------------------------------------------
# Orchestrator Role (deployed in shared services account)
# This role is assumed by Step Functions to orchestrate cross-account operations
# -----------------------------------------------------------------------------

resource "aws_iam_role" "orchestrator" {
  name = "${var.prefix}-orchestrator-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Principal = {
          Service = "states.amazonaws.com"
        }
        Action = "sts:AssumeRole"
      }
    ]
  })

  tags = var.tags
}

resource "aws_iam_role_policy" "orchestrator_assume_roles" {
  name = "${var.prefix}-assume-cross-account-roles"
  role = aws_iam_role.orchestrator.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = "sts:AssumeRole"
        Resource = concat(var.source_role_arns, var.destination_role_arns)
      }
    ]
  })
}

resource "aws_iam_role_policy" "orchestrator_step_functions" {
  name = "${var.prefix}-step-functions"
  role = aws_iam_role.orchestrator.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "states:StartExecution",
          "states:DescribeExecution",
          "states:StopExecution"
        ]
        Resource = "arn:aws:states:${data.aws_region.current.id}:${local.orchestrator_account_id}:stateMachine:${var.prefix}-*"
      },
      {
        Effect   = "Allow"
        Action   = "events:PutTargets"
        Resource = "arn:aws:events:${data.aws_region.current.id}:${local.orchestrator_account_id}:rule/StepFunctionsGetEventsForStepFunctionsExecutionRule"
      },
      {
        Effect = "Allow"
        Action = [
          "events:PutTargets",
          "events:PutRule",
          "events:DescribeRule",
          "events:DeleteRule",
          "events:RemoveTargets"
        ]
        Resource = [
          "arn:aws:events:${data.aws_region.current.id}:${local.orchestrator_account_id}:rule/StepFunctionsGetEventsFor*",
          "arn:aws:events:${data.aws_region.current.id}:${local.orchestrator_account_id}:rule/StepFunctions*"
        ]
      }
    ]
  })
}

resource "aws_iam_role_policy" "orchestrator_lambda_invoke" {
  name = "${var.prefix}-lambda-invoke"
  role = aws_iam_role.orchestrator.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "InvokeLambdas"
        Effect = "Allow"
        Action = "lambda:InvokeFunction"
        Resource = "arn:aws:lambda:${data.aws_region.current.id}:${local.orchestrator_account_id}:function:${var.prefix}-*"
      }
    ]
  })
}

resource "aws_iam_role_policy" "orchestrator_logging" {
  name = "${var.prefix}-logging"
  role = aws_iam_role.orchestrator.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "logs:CreateLogDelivery",
          "logs:GetLogDelivery",
          "logs:UpdateLogDelivery",
          "logs:DeleteLogDelivery",
          "logs:ListLogDeliveries",
          "logs:PutLogEvents",
          "logs:PutResourcePolicy",
          "logs:DescribeResourcePolicies",
          "logs:DescribeLogGroups"
        ]
        Resource = "*"
      }
    ]
  })
}

