variable "prefix" {
  description = "Prefix for resource names"
  type        = string
}

variable "tags" {
  description = "Tags to apply to resources"
  type        = map(string)
  default     = {}
}

variable "orchestrator_role_arn" {
  description = "ARN of the IAM role for Step Functions execution"
  type        = string
}

variable "enable_logging" {
  description = "Enable CloudWatch logging for Step Functions"
  type        = bool
  default     = true
}

variable "log_retention_days" {
  description = "CloudWatch log retention in days"
  type        = number
  default     = 30
}

variable "enable_xray_tracing" {
  description = "Enable X-Ray tracing for Step Functions"
  type        = bool
  default     = false
}

variable "naming_convention" {
  description = "Naming convention for resources: 'pascal' (Orchestrator-RefreshOrchestrator) or 'kebab' (orchestrator-refresh-orchestrator)"
  type        = string
  default     = "pascal"

  validation {
    condition     = contains(["pascal", "kebab"], var.naming_convention)
    error_message = "naming_convention must be 'pascal' or 'kebab'"
  }
}

# -----------------------------------------------------------------------------
# Step Function ARNs from other modules
# -----------------------------------------------------------------------------

variable "db_step_function_arns" {
  description = "Map of database Step Function ARNs"
  type        = map(string)
  default     = {}
}

variable "efs_step_function_arns" {
  description = "Map of EFS Step Function ARNs"
  type        = map(string)
  default     = {}
}

variable "eks_step_function_arns" {
  description = "Map of EKS Step Function ARNs"
  type        = map(string)
  default     = {}
}

variable "utils_step_function_arns" {
  description = "Map of Utils Step Function ARNs"
  type        = map(string)
  default     = {}
}

variable "sync_step_function_arns" {
  description = "Map of Sync Step Function ARNs"
  type        = map(string)
  default     = {}
}
