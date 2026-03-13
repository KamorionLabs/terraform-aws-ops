# -----------------------------------------------------------------------------
# EFS Step Functions Module
# Cross-account Step Functions for EFS operations
# -----------------------------------------------------------------------------

locals {
  # SFN definitions using file() — no template variables needed
  step_functions = {
    # Core Operations
    delete_filesystem = "delete_filesystem.asl.json"
    create_filesystem = "create_filesystem.asl.json"

    # Subpath Management
    get_subpath_and_store_in_ssm = "get_subpath_and_store_in_ssm.asl.json"

    # Backup & Replication (non-templated)
    restore_from_backup = "restore_from_backup.asl.json"
    delete_replication  = "delete_replication.asl.json"

    # Sub-SFNs — Phase 1 Extraction (reusable building blocks, no ARN injection)
    manage_filesystem_policy = "manage_filesystem_policy.asl.json"
    manage_access_point      = "manage_access_point.asl.json"
    manage_lambda_lifecycle  = "manage_lambda_lifecycle.asl.json"
  }

  # Phase 2 sub-SFN using templatefile() — calls Phase 1 sub-SFNs
  step_functions_sub = {
    check_flag_file_sync = "check_flag_file_sync.asl.json"
  }

  # Phase 2 refactored callers using templatefile() — call both Phase 1 and Phase 2 sub-SFNs
  step_functions_templated = {
    check_replication_sync          = "check_replication_sync.asl.json"
    setup_cross_account_replication = "setup_cross_account_replication.asl.json"
  }

  # Naming: pascal = "EFS-RestoreFromBackup", kebab = "efs-restore-from-backup"
  # Covers ALL maps for consistent naming
  sfn_names = {
    for k, v in merge(local.step_functions, local.step_functions_sub, local.step_functions_templated) : k => (
      var.naming_convention == "pascal"
      ? "${var.prefix}-EFS-${replace(title(replace(k, "_", " ")), " ", "")}"
      : "${var.prefix}-efs-${replace(k, "_", "-")}"
    )
  }
}

# -----------------------------------------------------------------------------
# Step Functions Resources — file() based (no template variables)
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
# Step Functions Resources — Phase 2 sub-SFNs (call Phase 1 sub-SFNs)
# -----------------------------------------------------------------------------

resource "aws_sfn_state_machine" "efs_sub_templated" {
  for_each = local.step_functions_sub

  name     = local.sfn_names[each.key]
  role_arn = var.orchestrator_role_arn

  definition = templatefile("${path.module}/${each.value}", {
    manage_lambda_lifecycle_arn = aws_sfn_state_machine.efs["manage_lambda_lifecycle"].arn
    manage_access_point_arn     = aws_sfn_state_machine.efs["manage_access_point"].arn
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
    Module = "efs"
    Name   = local.sfn_names[each.key]
  })
}

# -----------------------------------------------------------------------------
# Step Functions Resources — Phase 2 refactored callers (call sub-SFNs)
# -----------------------------------------------------------------------------

resource "aws_sfn_state_machine" "efs_templated" {
  for_each = local.step_functions_templated

  name     = local.sfn_names[each.key]
  role_arn = var.orchestrator_role_arn

  definition = templatefile("${path.module}/${each.value}", {
    manage_lambda_lifecycle_arn  = aws_sfn_state_machine.efs["manage_lambda_lifecycle"].arn
    manage_access_point_arn      = aws_sfn_state_machine.efs["manage_access_point"].arn
    manage_filesystem_policy_arn = aws_sfn_state_machine.efs["manage_filesystem_policy"].arn
    check_flag_file_sync_arn     = aws_sfn_state_machine.efs_sub_templated["check_flag_file_sync"].arn
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
    Module = "efs"
    Name   = local.sfn_names[each.key]
  })
}

# -----------------------------------------------------------------------------
# Moved blocks — zero-downtime migration from efs to efs_templated
# -----------------------------------------------------------------------------

moved {
  from = aws_sfn_state_machine.efs["check_replication_sync"]
  to   = aws_sfn_state_machine.efs_templated["check_replication_sync"]
}

moved {
  from = aws_sfn_state_machine.efs["setup_cross_account_replication"]
  to   = aws_sfn_state_machine.efs_templated["setup_cross_account_replication"]
}

# -----------------------------------------------------------------------------
# CloudWatch Log Group - Step Functions
# -----------------------------------------------------------------------------

resource "aws_cloudwatch_log_group" "sfn" {
  count = var.enable_logging ? 1 : 0

  name              = "/aws/stepfunctions/${var.prefix}-efs"
  retention_in_days = var.log_retention_days

  tags = var.tags
}
