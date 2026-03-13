output "step_function_arns" {
  description = "Map of Step Function names to ARNs"
  value = merge(
    { for k, v in aws_sfn_state_machine.db : k => v.arn },
    { for k, v in aws_sfn_state_machine.db_templated : k => v.arn }
  )
}

output "step_function_names" {
  description = "Map of Step Function keys to actual names"
  value = merge(
    { for k, v in aws_sfn_state_machine.db : k => v.name },
    { for k, v in aws_sfn_state_machine.db_templated : k => v.name }
  )
}

output "log_group_arn" {
  description = "ARN of the CloudWatch log group"
  value       = var.enable_logging ? aws_cloudwatch_log_group.sfn[0].arn : null
}
