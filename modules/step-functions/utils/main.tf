# -----------------------------------------------------------------------------
# Utils Step Functions Module
# Cross-account Step Functions for utility operations
# -----------------------------------------------------------------------------

locals {
  step_functions = {
    # Tagging
    tag_resources = "tag_resources.asl.json"

    # Archive
    run_archive_job = "run_archive_job.asl.json"

    # Preparation & Cleanup
    prepare_refresh  = "prepare_refresh.asl.json"
    cleanup_and_stop = "cleanup_and_stop.asl.json"

    # Notifications
    notify = "notify.asl.json"

    # Validation
    validate_refresh_config = "validate_refresh_config.asl.json"
  }

  # Naming: pascal = "Utils-Notify", kebab = "utils-notify"
  sfn_names = {
    for k, v in local.step_functions : k => (
      var.naming_convention == "pascal"
      ? "${var.prefix}-Utils-${replace(title(replace(k, "_", " ")), " ", "")}"
      : "${var.prefix}-utils-${replace(k, "_", "-")}"
    )
  }
}

# -----------------------------------------------------------------------------
# Step Functions Resources
# -----------------------------------------------------------------------------

resource "aws_sfn_state_machine" "utils" {
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
    Module = "utils"
    Name   = local.sfn_names[each.key]
  })
}

# -----------------------------------------------------------------------------
# CloudWatch Log Group
# -----------------------------------------------------------------------------

resource "aws_cloudwatch_log_group" "sfn" {
  count = var.enable_logging ? 1 : 0

  name              = "/aws/stepfunctions/${var.prefix}-utils"
  retention_in_days = var.log_retention_days

  tags = var.tags
}
