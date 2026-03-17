# terraform-aws-refresh
# Cross-account database refresh orchestrator using AWS Step Functions
#
# This module deploys Step Functions for orchestrating database refresh
# operations across multiple AWS accounts (source production -> destination non-prod)

# -----------------------------------------------------------------------------
# Lambda Code Module - Packages and uploads Lambda code to S3
# -----------------------------------------------------------------------------
module "lambda_code" {
  count  = var.deploy_lambda_code ? 1 : 0
  source = "./modules/lambda-code"

  prefix      = var.prefix
  bucket_name = var.lambda_code_bucket_name
  # Note: tags passed separately to bucket only (S3 objects limited to 10 tags)
  tags = var.tags

  # Cross-account access for Step Functions to deploy Lambdas dynamically
  # Note: roles must exist before bucket policy can be applied
  cross_account_role_arns = concat(var.source_role_arns, var.destination_role_arns)
}

# -----------------------------------------------------------------------------
# Step Functions - Database Module
# -----------------------------------------------------------------------------
module "step_functions_db" {
  source = "./modules/step-functions/db"

  prefix                = var.prefix
  tags                  = var.tags
  orchestrator_role_arn = module.iam.orchestrator_role_arn

  enable_logging      = var.enable_step_functions_logging
  log_retention_days  = var.log_retention_days
  enable_xray_tracing = var.enable_xray_tracing
}

# -----------------------------------------------------------------------------
# Step Functions - EFS Module
# -----------------------------------------------------------------------------
module "step_functions_efs" {
  source = "./modules/step-functions/efs"

  prefix                = var.prefix
  tags                  = var.tags
  orchestrator_role_arn = module.iam.orchestrator_role_arn

  enable_logging      = var.enable_step_functions_logging
  log_retention_days  = var.log_retention_days
  enable_xray_tracing = var.enable_xray_tracing
}

# -----------------------------------------------------------------------------
# Step Functions - EKS Module
# -----------------------------------------------------------------------------
module "step_functions_eks" {
  source = "./modules/step-functions/eks"

  prefix                = var.prefix
  tags                  = var.tags
  orchestrator_role_arn = module.iam.orchestrator_role_arn

  enable_logging      = var.enable_step_functions_logging
  log_retention_days  = var.log_retention_days
  enable_xray_tracing = var.enable_xray_tracing
}

# -----------------------------------------------------------------------------
# Step Functions - Utils Module
# -----------------------------------------------------------------------------
module "step_functions_utils" {
  source = "./modules/step-functions/utils"

  prefix                = var.prefix
  tags                  = var.tags
  orchestrator_role_arn = module.iam.orchestrator_role_arn

  enable_logging      = var.enable_step_functions_logging
  log_retention_days  = var.log_retention_days
  enable_xray_tracing = var.enable_xray_tracing
}

# -----------------------------------------------------------------------------
# Step Functions - Sync Module
# -----------------------------------------------------------------------------
module "step_functions_sync" {
  source = "./modules/step-functions/sync"

  prefix                = var.prefix
  tags                  = var.tags
  orchestrator_role_arn = module.iam.orchestrator_role_arn

  cross_account_role_arns = concat(var.source_role_arns, var.destination_role_arns)

  enable_logging      = var.enable_step_functions_logging
  log_retention_days  = var.log_retention_days
  enable_xray_tracing = var.enable_xray_tracing
}

# -----------------------------------------------------------------------------
# IAM Roles for Cross-Account Access
# -----------------------------------------------------------------------------
module "iam" {
  source = "./modules/iam"

  prefix = var.prefix
  tags   = var.tags

  source_role_arns      = var.source_role_arns
  destination_role_arns = var.destination_role_arns
}

# -----------------------------------------------------------------------------
# Orchestrator
# -----------------------------------------------------------------------------
module "orchestrator" {
  source = "./modules/step-functions/orchestrator"

  prefix                = var.prefix
  tags                  = var.tags
  orchestrator_role_arn = module.iam.orchestrator_role_arn

  enable_logging      = var.enable_step_functions_logging
  log_retention_days  = var.log_retention_days
  enable_xray_tracing = var.enable_xray_tracing

  # Pass Step Function ARNs from other modules
  db_step_function_arns    = module.step_functions_db.step_function_arns
  efs_step_function_arns   = module.step_functions_efs.step_function_arns
  eks_step_function_arns   = module.step_functions_eks.step_function_arns
  utils_step_function_arns = module.step_functions_utils.step_function_arns
  sync_step_function_arns  = module.step_functions_sync.step_function_arns
}
