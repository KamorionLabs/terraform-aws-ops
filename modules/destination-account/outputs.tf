# -----------------------------------------------------------------------------
# Outputs - Destination Account Module
# -----------------------------------------------------------------------------

output "role_arn" {
  description = "ARN of the destination account IAM role"
  value       = local.role_arn
}

output "role_name" {
  description = "Name of the destination account IAM role"
  value       = local.role_name
}

output "role_created" {
  description = "Whether the IAM role was created by this module"
  value       = var.create_role
}

# -----------------------------------------------------------------------------
# Lambda Outputs
# -----------------------------------------------------------------------------

output "lambda_run_sql_arn" {
  description = "ARN of the run-sql Lambda function"
  value       = var.deploy_lambdas ? aws_lambda_function.functions["run-sql"].arn : null
}

output "lambda_run_sql_name" {
  description = "Name of the run-sql Lambda function"
  value       = var.deploy_lambdas ? aws_lambda_function.functions["run-sql"].function_name : null
}

output "lambda_get_efs_subpath_arn" {
  description = "ARN of the get-efs-subpath Lambda function"
  value       = var.deploy_lambdas && var.enable_efs ? aws_lambda_function.functions["get-efs-subpath"].arn : null
}

output "lambda_get_efs_subpath_name" {
  description = "Name of the get-efs-subpath Lambda function"
  value       = var.deploy_lambdas && var.enable_efs ? aws_lambda_function.functions["get-efs-subpath"].function_name : null
}

output "lambda_role_arn" {
  description = "ARN of the Lambda execution IAM role"
  value       = var.deploy_lambdas ? aws_iam_role.lambda[0].arn : var.existing_lambda_role_arn
}

output "lambda_role_name" {
  description = "Name of the Lambda execution role"
  value       = var.deploy_lambdas ? aws_iam_role.lambda[0].name : var.existing_lambda_role_name
}

output "lambda_security_group_id" {
  description = "ID of the Lambda security group (created by this module)"
  value       = var.deploy_lambdas && var.create_lambda_security_group ? aws_security_group.lambda[0].id : null
}

output "lambda_security_group_ids" {
  description = "Security group IDs for Lambda VPC configuration (created or provided)"
  value       = var.deploy_lambdas && var.create_lambda_security_group ? [aws_security_group.lambda[0].id] : var.lambda_security_group_ids
}

output "lambda_subnet_ids" {
  description = "Subnet IDs for Lambda VPC configuration"
  value       = var.lambda_subnet_ids
}

# -----------------------------------------------------------------------------
# Lambda Layer Outputs
# -----------------------------------------------------------------------------

output "pymysql_layer_arn" {
  description = "ARN of the PyMySQL Lambda layer"
  value       = var.deploy_lambdas ? aws_lambda_layer_version.layer["pymysql"].arn : null
}

# -----------------------------------------------------------------------------
# All Lambda Functions Map
# -----------------------------------------------------------------------------

output "lambda_functions" {
  description = "Map of all Lambda functions with their ARNs and names"
  value = var.deploy_lambdas ? {
    for k, v in aws_lambda_function.functions : k => {
      arn           = v.arn
      function_name = v.function_name
      invoke_arn    = v.invoke_arn
    }
  } : {}
}

# -----------------------------------------------------------------------------
# EKS Access Entry Outputs
# -----------------------------------------------------------------------------

output "eks_access_entry_arn" {
  description = "ARN of the EKS access entry"
  value       = var.create_eks_access_entry && var.eks_cluster_name != null ? aws_eks_access_entry.destination[0].access_entry_arn : null
}

output "eks_access_entry_created" {
  description = "Whether the EKS access entry was created"
  value       = var.create_eks_access_entry && var.eks_cluster_name != null
}

# -----------------------------------------------------------------------------
# EKS Pod Identity Outputs
# -----------------------------------------------------------------------------

output "eks_pod_identity_role_arn" {
  description = "ARN of the EKS Pod Identity IAM role for refresh jobs"
  value       = var.create_eks_pod_identity ? aws_iam_role.eks_pod_identity[0].arn : null
}

output "eks_pod_identity_role_name" {
  description = "Name of the EKS Pod Identity IAM role for refresh jobs"
  value       = var.create_eks_pod_identity ? aws_iam_role.eks_pod_identity[0].name : null
}
