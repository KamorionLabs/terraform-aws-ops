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
  description = "Naming convention for resources: 'pascal' (DB-RestoreCluster) or 'kebab' (db-restore-cluster)"
  type        = string
  default     = "pascal"

  validation {
    condition     = contains(["pascal", "kebab"], var.naming_convention)
    error_message = "naming_convention must be 'pascal' or 'kebab'"
  }
}
