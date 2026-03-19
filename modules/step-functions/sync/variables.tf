# -----------------------------------------------------------------------------
# Sync Module Variables
# -----------------------------------------------------------------------------

variable "prefix" {
  description = "Prefix for naming resources"
  type        = string
}

variable "orchestrator_role_arn" {
  description = "ARN of the IAM role for Step Functions execution"
  type        = string
}

variable "lambda_role_arn" {
  description = "ARN of an externally managed IAM role for the sync Lambda. When provided, the module skips creating its own role and uses this one instead."
  type        = string
  default     = null
}

variable "cross_account_role_arns" {
  description = "List of cross-account role ARNs that the Lambda can assume (only used when lambda_role_arn is null)"
  type        = list(string)
  default     = []
}

variable "naming_convention" {
  description = "Naming convention for Step Functions: 'pascal' (e.g., Sync-SyncConfigItems) or 'kebab' (e.g., sync-sync-config-items)"
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
  description = "Log level for Lambda function"
  type        = string
  default     = "INFO"
  validation {
    condition     = contains(["DEBUG", "INFO", "WARNING", "ERROR"], var.log_level)
    error_message = "log_level must be DEBUG, INFO, WARNING, or ERROR"
  }
}

variable "tags" {
  description = "Tags to apply to all resources"
  type        = map(string)
  default     = {}
}
