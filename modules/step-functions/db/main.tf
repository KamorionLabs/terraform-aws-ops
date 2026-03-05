# -----------------------------------------------------------------------------
# Database Step Functions Module
# Cross-account Step Functions for RDS/Aurora operations
# -----------------------------------------------------------------------------

locals {
  _eks_suffix = var.eks_access_mode == "private" ? "_private" : ""

  step_functions = {
    # Core Operations
    restore_cluster           = "restore_cluster.asl.json"
    delete_cluster            = "delete_cluster.asl.json"
    rename_cluster            = "rename_cluster.asl.json"
    ensure_cluster_available  = "ensure_cluster_available.asl.json"
    ensure_cluster_not_exists = "ensure_cluster_not_exists.asl.json"
    stop_cluster              = "stop_cluster.asl.json"

    # Instance Management
    create_instance = "create_instance.asl.json"

    # Snapshot Management
    prepare_snapshot_for_restore = "prepare_snapshot_for_restore.asl.json"
    share_snapshot               = "share_snapshot.asl.json"
    create_manual_snapshot       = "create_manual_snapshot.asl.json"
    list_shared_snapshots        = "list_shared_snapshots.asl.json"

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

  # Naming: pascal = "DB-RestoreCluster", kebab = "db-restore-cluster"
  category = var.naming_convention == "pascal" ? "DB" : "db"
  sfn_names = {
    for k, v in local.step_functions : k => (
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
# CloudWatch Log Group
# -----------------------------------------------------------------------------

resource "aws_cloudwatch_log_group" "sfn" {
  count = var.enable_logging ? 1 : 0

  name              = "/aws/stepfunctions/${var.prefix}-db"
  retention_in_days = var.log_retention_days

  tags = var.tags
}
