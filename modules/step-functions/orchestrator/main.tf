# -----------------------------------------------------------------------------
# Orchestrator Step Functions Module
# Main orchestrator for cross-account database/EFS refresh
# -----------------------------------------------------------------------------

locals {
  step_functions = {
    refresh_orchestrator = "refresh_orchestrator.asl.json"
  }

  # Naming: pascal = "Orchestrator-RefreshOrchestrator", kebab = "orchestrator-refresh-orchestrator"
  sfn_names = {
    for k, v in local.step_functions : k => (
      var.naming_convention == "pascal"
      ? "${var.prefix}-Orchestrator-${replace(title(replace(k, "_", " ")), " ", "")}"
      : "${var.prefix}-orchestrator-${replace(k, "_", "-")}"
    )
  }
}

# -----------------------------------------------------------------------------
# Step Functions Resources
# -----------------------------------------------------------------------------

resource "aws_sfn_state_machine" "orchestrator" {
  for_each = local.step_functions

  name     = local.sfn_names[each.key]
  role_arn = var.orchestrator_role_arn

  definition = templatefile("${path.module}/${each.value}", {
    # Step Function ARNs from other modules
    db_step_functions    = var.db_step_function_arns
    efs_step_functions   = var.efs_step_function_arns
    eks_step_functions   = var.eks_step_function_arns
    utils_step_functions = var.utils_step_function_arns

    # Flat ARN variables for sub-SFN calls in ASL (avoids map lookup syntax in JSON)
    cluster_switch_sequence_arn = var.db_step_function_arns["cluster_switch_sequence"]
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
    Module = "orchestrator"
    Name   = local.sfn_names[each.key]
  })
}

# -----------------------------------------------------------------------------
# CloudWatch Log Group
# -----------------------------------------------------------------------------

resource "aws_cloudwatch_log_group" "sfn" {
  count = var.enable_logging ? 1 : 0

  name              = "/aws/stepfunctions/${var.prefix}-orchestrator"
  retention_in_days = var.log_retention_days

  tags = var.tags
}
