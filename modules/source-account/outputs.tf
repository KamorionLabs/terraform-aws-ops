# -----------------------------------------------------------------------------
# Outputs - Source Account Module
# -----------------------------------------------------------------------------

output "role_arn" {
  description = "ARN of the source account IAM role"
  value       = local.role_arn
}

output "role_name" {
  description = "Name of the source account IAM role"
  value       = local.role_name
}

output "role_created" {
  description = "Whether the IAM role was created by this module"
  value       = var.create_role
}

# -----------------------------------------------------------------------------
# Lambda Outputs
# -----------------------------------------------------------------------------

output "lambda_role_arn" {
  description = "ARN of the Lambda execution role (for dynamically created Lambdas)"
  value       = var.deploy_lambda_role ? aws_iam_role.lambda[0].arn : var.existing_lambda_role_arn
}

output "lambda_role_name" {
  description = "Name of the Lambda execution role"
  value       = var.deploy_lambda_role ? aws_iam_role.lambda[0].name : var.existing_lambda_role_name
}

output "lambda_security_group_id" {
  description = "ID of the Lambda security group (created by this module)"
  value       = var.deploy_lambda_role && var.create_lambda_security_group ? aws_security_group.lambda[0].id : null
}

output "lambda_security_group_ids" {
  description = "Security group IDs for Lambda VPC configuration (created or provided)"
  value       = var.deploy_lambda_role && var.create_lambda_security_group ? [aws_security_group.lambda[0].id] : var.lambda_security_group_ids
}

output "lambda_subnet_ids" {
  description = "Subnet IDs for Lambda VPC configuration"
  value       = var.lambda_subnet_ids
}

# -----------------------------------------------------------------------------
# EFS Replication Outputs
# -----------------------------------------------------------------------------

output "efs_replication_role_arn" {
  description = "ARN of the EFS replication role (for cross-account replication)"
  value       = var.enable_efs ? aws_iam_role.efs_replication[0].arn : null
}

output "efs_replication_role_name" {
  description = "Name of the EFS replication role"
  value       = var.enable_efs ? aws_iam_role.efs_replication[0].name : null
}
