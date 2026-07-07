# -----------------------------------------------------------------------------
# S3 Step Functions Module
# Cross-account Step Functions for S3 replication operations (NO Lambda — D-09)
# -----------------------------------------------------------------------------

locals {
  # SFN definitions using file() — no template variables, no sub-SFN ARN injection
  step_functions = {
    setup_cross_account_replication = "setup_cross_account_replication.asl.json"
    run_batch_replication           = "run_batch_replication.asl.json"
    run_batch_copy                  = "run_batch_copy.asl.json"
    check_batch_replication         = "check_batch_replication.asl.json"
    delete_replication              = "delete_replication.asl.json"
  }

  # Naming: pascal = "S3-SetupCrossAccountReplication", kebab = "s3-setup-cross-account-replication"
  sfn_names = {
    for k, v in local.step_functions : k => (
      var.naming_convention == "pascal"
      ? "${var.prefix}-S3-${replace(title(replace(k, "_", " ")), " ", "")}"
      : "${var.prefix}-s3-${replace(k, "_", "-")}"
    )
  }
}

# -----------------------------------------------------------------------------
# Step Functions Resources — file() based (no template variables)
# -----------------------------------------------------------------------------

resource "aws_sfn_state_machine" "s3" {
  for_each = local.step_functions

  name     = local.sfn_names[each.key]
  role_arn = var.orchestrator_role_arn

  definition = file("${path.module}/${each.value}")

  logging_configuration {
    log_destination        = var.enable_logging ? "${aws_cloudwatch_log_group.sfn[0].arn}:*" : null
    include_execution_data = var.enable_logging
    level                  = var.enable_logging ? "ALL" : "OFF"
  }

  tracing_configuration {
    enabled = var.enable_xray_tracing
  }

  tags = merge(var.tags, {
    Module = "s3"
    Name   = local.sfn_names[each.key]
  })
}

# -----------------------------------------------------------------------------
# CloudWatch Log Group - Step Functions
# -----------------------------------------------------------------------------

resource "aws_cloudwatch_log_group" "sfn" {
  count = var.enable_logging ? 1 : 0

  name              = "/aws/stepfunctions/${var.prefix}-s3"
  retention_in_days = var.log_retention_days

  tags = var.tags
}
