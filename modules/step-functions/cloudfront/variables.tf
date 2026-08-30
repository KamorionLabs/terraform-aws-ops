variable "prefix" {
  description = "Prefix for naming resources"
  type        = string
}

variable "orchestrator_role_arn" {
  description = "ARN of the IAM role for Step Functions execution (must allow invoking the Lambda)"
  type        = string
}

variable "cross_account_role_arns" {
  description = "List of cross-account role ARNs that the Lambda is allowed to assume. Leave empty for same-account only."
  type        = list(string)
  default     = []
}

variable "naming_convention" {
  description = "Naming convention for Step Functions: 'pascal' (CloudFront-RemoveAliases) or 'kebab' (cloudfront-remove-aliases)"
  type        = string
  default     = "kebab"
  validation {
    condition     = contains(["pascal", "kebab"], var.naming_convention)
    error_message = "naming_convention must be 'pascal' or 'kebab'"
  }
}

variable "enable_logging" {
  description = "Enable CloudWatch logging for Step Functions"
  type        = bool
  default     = true
}

variable "enable_xray_tracing" {
  description = "Enable X-Ray tracing for Step Functions"
  type        = bool
  default     = false
}

variable "log_retention_days" {
  description = "CloudWatch log retention in days"
  type        = number
  default     = 30
}

variable "log_level" {
  description = "Log level for the Lambda function"
  type        = string
  default     = "INFO"
  validation {
    condition     = contains(["DEBUG", "INFO", "WARNING", "ERROR"], var.log_level)
    error_message = "log_level must be DEBUG, INFO, WARNING, or ERROR"
  }
}

variable "lambda_timeout" {
  description = "Lambda timeout in seconds (CloudFront updates can be slow)"
  type        = number
  default     = 120
}

variable "tags" {
  description = "Tags to apply to all resources"
  type        = map(string)
  default     = {}
}
