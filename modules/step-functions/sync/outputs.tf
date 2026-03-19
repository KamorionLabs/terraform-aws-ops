# -----------------------------------------------------------------------------
# Sync Module Outputs
# -----------------------------------------------------------------------------

output "step_function_arns" {
  description = "Map of Step Function names to ARNs"
  value       = { for k, v in aws_sfn_state_machine.sync : k => v.arn }
}

output "step_function_names" {
  description = "Map of Step Function keys to names"
  value       = local.sfn_names
}

output "sync_config_items_arn" {
  description = "ARN of the sync_config_items Step Function"
  value       = aws_sfn_state_machine.sync["sync_config_items"].arn
}

output "lambda_function_arn" {
  description = "ARN of the Sync Config Items Lambda function"
  value       = aws_lambda_function.sync_config_items.arn
}

output "lambda_function_name" {
  description = "Name of the Sync Config Items Lambda function"
  value       = aws_lambda_function.sync_config_items.function_name
}

output "lambda_role_arn" {
  description = "ARN of the Lambda execution role (external or module-managed)"
  value       = local.lambda_role_arn
}
