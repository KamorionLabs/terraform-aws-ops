# -----------------------------------------------------------------------------
# IAM Outputs
# -----------------------------------------------------------------------------

output "orchestrator_role_arn" {
  description = "ARN of the orchestrator IAM role"
  value       = module.iam.orchestrator_role_arn
}

output "orchestrator_role_name" {
  description = "Name of the orchestrator IAM role"
  value       = module.iam.orchestrator_role_name
}

# -----------------------------------------------------------------------------
# Step Functions - Database Outputs
# -----------------------------------------------------------------------------

output "step_functions_db" {
  description = "Map of database Step Functions ARNs"
  value       = module.step_functions_db.step_function_arns
}

# -----------------------------------------------------------------------------
# Step Functions - EFS Outputs
# -----------------------------------------------------------------------------

output "step_functions_efs" {
  description = "Map of EFS Step Functions ARNs"
  value       = module.step_functions_efs.step_function_arns
}

# -----------------------------------------------------------------------------
# Step Functions - EKS Outputs
# -----------------------------------------------------------------------------

output "step_functions_eks" {
  description = "Map of EKS Step Functions ARNs"
  value       = module.step_functions_eks.step_function_arns
}

# -----------------------------------------------------------------------------
# Step Functions - Utils Outputs
# -----------------------------------------------------------------------------

output "step_functions_utils" {
  description = "Map of Utils Step Functions ARNs"
  value       = module.step_functions_utils.step_function_arns
}

# -----------------------------------------------------------------------------
# Step Functions - Sync Outputs
# -----------------------------------------------------------------------------

output "step_functions_sync" {
  description = "Map of Sync Step Functions ARNs"
  value       = module.step_functions_sync.step_function_arns
}

# -----------------------------------------------------------------------------
# Orchestrator Outputs
# -----------------------------------------------------------------------------

output "orchestrator_arn" {
  description = "ARN of the main orchestrator Step Function"
  value       = module.orchestrator.orchestrator_arn
}

output "orchestrator_name" {
  description = "Name of the main orchestrator Step Function"
  value       = module.orchestrator.orchestrator_name
}

# -----------------------------------------------------------------------------
# All Step Functions (consolidated)
# -----------------------------------------------------------------------------

output "all_step_function_arns" {
  description = "Consolidated map of all Step Function ARNs by module"
  value = {
    db           = module.step_functions_db.step_function_arns
    efs          = module.step_functions_efs.step_function_arns
    eks          = module.step_functions_eks.step_function_arns
    utils        = module.step_functions_utils.step_function_arns
    sync         = module.step_functions_sync.step_function_arns
    orchestrator = module.orchestrator.step_function_arns
  }
}

# -----------------------------------------------------------------------------
# Lambda Code Outputs
# -----------------------------------------------------------------------------

output "lambda_code_bucket_name" {
  description = "Name of the S3 bucket containing Lambda code"
  value       = var.deploy_lambda_code ? module.lambda_code[0].bucket_name : null
}

output "lambda_code_bucket_arn" {
  description = "ARN of the S3 bucket containing Lambda code"
  value       = var.deploy_lambda_code ? module.lambda_code[0].bucket_arn : null
}

output "lambda_config" {
  description = "Lambda configuration for Step Functions input (S3 bucket and keys)"
  value = var.deploy_lambda_code ? {
    check_flag_file = module.lambda_code[0].check_flag_file_config
    get_efs_subpath = module.lambda_code[0].get_efs_subpath_config
  } : null
}
