# -----------------------------------------------------------------------------
# Database Step Functions Module
# Cross-account Step Functions for RDS/Aurora operations
# -----------------------------------------------------------------------------

locals {
  _eks_suffix = var.eks_access_mode == "private" ? "_private" : ""

  # SFN definitions using file() — no template variables needed
  step_functions = {
    # Core Operations
    delete_cluster            = "delete_cluster.asl.json"
    rename_cluster            = "rename_cluster.asl.json"
    ensure_cluster_available  = "ensure_cluster_available.asl.json"
    ensure_cluster_not_exists = "ensure_cluster_not_exists.asl.json"
    stop_cluster              = "stop_cluster.asl.json"

    # Instance Management
    create_instance = "create_instance.asl.json"

    # Snapshot Management
    share_snapshot         = "share_snapshot.asl.json"
    create_manual_snapshot = "create_manual_snapshot.asl.json"
    list_shared_snapshots  = "list_shared_snapshots.asl.json"

    # Sub-SFN — Phase 2 Extraction (reusable, no ARN injection)
    ensure_snapshot_available = "ensure_snapshot_available.asl.json"
    cluster_switch_sequence   = "cluster_switch_sequence.asl.json"

    # Secrets Management
    enable_master_secret = "enable_master_secret.asl.json"
    rotate_secrets       = "rotate_secrets.asl.json"

    # S3 & SQL Operations
    configure_s3_integration = "configure_s3_integration.asl.json"
    run_sql_lambda           = "run_sql_lambda.asl.json"
    run_sql_from_s3          = "run_sql_from_s3.asl.json"

    # EKS Integration (private variant)
    run_mysqldump_on_eks   = "run_mysqldump_on_eks${local._eks_suffix}.asl.json"
    run_mysqlimport_on_eks = "run_mysqlimport_on_eks${local._eks_suffix}.asl.json"
  }

  # Refactored entries using templatefile() for ARN injection
  step_functions_templated = {
    prepare_snapshot_for_restore = "prepare_snapshot_for_restore.asl.json"
    restore_cluster              = "restore_cluster.asl.json"
  }

  # Naming: pascal = "DB-RestoreCluster", kebab = "db-restore-cluster"
  # Covers ALL maps for consistent naming
  category = var.naming_convention == "pascal" ? "DB" : "db"
  sfn_names = {
    for k, v in merge(local.step_functions, local.step_functions_templated) : k => (
      var.naming_convention == "pascal"
      ? "${var.prefix}-DB-${replace(title(replace(k, "_", " ")), " ", "")}"
      : "${var.prefix}-db-${replace(k, "_", "-")}"
    )
  }
}

# -----------------------------------------------------------------------------
# Step Functions Resources
# -----------------------------------------------------------------------------

resource "aws_sfn_state_machine" "db" {
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
    Module = "database"
    Name   = local.sfn_names[each.key]
  })
}

# -----------------------------------------------------------------------------
# Step Functions Resources — Refactored callers (templatefile with ARN injection)
# -----------------------------------------------------------------------------

resource "aws_sfn_state_machine" "db_templated" {
  for_each = local.step_functions_templated

  name     = local.sfn_names[each.key]
  role_arn = var.orchestrator_role_arn

  definition = templatefile("${path.module}/${each.value}", {
    ensure_snapshot_available_arn = aws_sfn_state_machine.db["ensure_snapshot_available"].arn
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
    Module = "database"
    Name   = local.sfn_names[each.key]
  })
}

# -----------------------------------------------------------------------------
# Moved blocks — zero-downtime migration from db to db_templated
# -----------------------------------------------------------------------------

moved {
  from = aws_sfn_state_machine.db["prepare_snapshot_for_restore"]
  to   = aws_sfn_state_machine.db_templated["prepare_snapshot_for_restore"]
}

moved {
  from = aws_sfn_state_machine.db["restore_cluster"]
  to   = aws_sfn_state_machine.db_templated["restore_cluster"]
}

# -----------------------------------------------------------------------------
# CloudWatch Log Group
# -----------------------------------------------------------------------------

resource "aws_cloudwatch_log_group" "sfn" {
  count = var.enable_logging ? 1 : 0

  name              = "/aws/stepfunctions/${var.prefix}-db"
  retention_in_days = var.log_retention_days

  tags = var.tags
}
