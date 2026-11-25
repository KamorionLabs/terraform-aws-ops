output "step_function_arns" {
  description = "Map of Step Function keys to ARNs"
  value       = { for k, v in aws_sfn_state_machine.cloudfront : k => v.arn }
}

output "step_function_names" {
  description = "Map of Step Function keys to names"
  value       = local.sfn_names
}

output "remove_aliases_arn" {
  description = "ARN of the remove_aliases Step Function"
  value       = aws_sfn_state_machine.cloudfront["remove_aliases"].arn
}

output "lambda_function_arn" {
  description = "ARN of the CloudFront alias manager Lambda"
  value       = aws_lambda_function.alias_manager.arn
}

output "lambda_function_name" {
  description = "Name of the CloudFront alias manager Lambda"
  value       = aws_lambda_function.alias_manager.function_name
}

output "lambda_role_arn" {
  description = "ARN of the Lambda execution role"
  value       = aws_iam_role.lambda.arn
}
