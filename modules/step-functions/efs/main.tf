# -----------------------------------------------------------------------------
# EFS Step Functions Module
# Cross-account Step Functions for EFS operations
# -----------------------------------------------------------------------------

locals {
  step_functions = {
    # Core Operations
    delete_filesystem = "delete_filesystem.asl.json"
    create_filesystem = "create_filesystem.asl.json"

    # Subpath Management
    get_subpath_and_store_in_ssm = "get_subpath_and_store_in_ssm.asl.json"

    # Backup & Replication
    restore_from_backup             = "restore_from_backup.asl.json"
    setup_cross_account_replication = "setup_cross_account_replication.asl.json"
    check_replication_sync          = "check_replication_sync.asl.json"
    delete_replication              = "delete_replication.asl.json"
  }

  # Naming: pascal = "EFS-RestoreFromBackup", kebab = "efs-restore-from-backup"
  sfn_names = {
    for k, v in local.step_functions : k => (
      var.naming_convention == "pascal"
      ? "${var.prefix}-EFS-${replace(title(replace(k, "_", " ")), " ", "")}"
      : "${var.prefix}-efs-${replace(k, "_", "-")}"
    )
  }
}

# -----------------------------------------------------------------------------
# Step Functions Resources
# -----------------------------------------------------------------------------

resource "aws_sfn_state_machine" "efs" {
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
    Module = "efs"
    Name   = local.sfn_names[each.key]
  })
}

# -----------------------------------------------------------------------------
# CloudWatch Log Group
# -----------------------------------------------------------------------------

resource "aws_cloudwatch_log_group" "sfn" {
  count = var.enable_logging ? 1 : 0

  name              = "/aws/stepfunctions/${var.prefix}-efs"
  retention_in_days = var.log_retention_days

  tags = var.tags
}
