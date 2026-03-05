# -----------------------------------------------------------------------------
# EKS Step Functions Module
# Cross-account Step Functions for EKS operations
# -----------------------------------------------------------------------------

locals {
  _suffix = var.eks_access_mode == "private" ? "_private" : ""

  step_functions = {
    # Storage Management
    manage_storage = "manage_storage${local._suffix}.asl.json"

    # Scaling - scale_nodegroup_asg uses aws-sdk only (not eks:call), no private variant needed
    scale_nodegroup_asg = "scale_nodegroup_asg.asl.json"
    scale_services      = "scale_services${local._suffix}.asl.json"

    # Verification
    verify_and_restart_services = "verify_and_restart_services${local._suffix}.asl.json"
  }

  # Naming: pascal = "EKS-ManageStorage", kebab = "eks-manage-storage"
  sfn_names = {
    for k, v in local.step_functions : k => (
      var.naming_convention == "pascal"
      ? "${var.prefix}-EKS-${replace(title(replace(k, "_", " ")), " ", "")}"
      : "${var.prefix}-eks-${replace(k, "_", "-")}"
    )
  }
}

# -----------------------------------------------------------------------------
# Step Functions Resources
# -----------------------------------------------------------------------------

resource "aws_sfn_state_machine" "eks" {
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
    Module = "eks"
    Name   = local.sfn_names[each.key]
  })
}

# -----------------------------------------------------------------------------
# CloudWatch Log Group
# -----------------------------------------------------------------------------

resource "aws_cloudwatch_log_group" "sfn" {
  count = var.enable_logging ? 1 : 0

  name              = "/aws/stepfunctions/${var.prefix}-eks"
  retention_in_days = var.log_retention_days

  tags = var.tags
}
