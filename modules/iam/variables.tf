variable "prefix" {
  description = "Prefix for resource names"
  type        = string
}

variable "tags" {
  description = "Tags to apply to resources"
  type        = map(string)
  default     = {}
}

# -----------------------------------------------------------------------------
# Cross-Account Role ARNs
# -----------------------------------------------------------------------------

variable "source_role_arns" {
  description = "List of IAM role ARNs in source accounts that the orchestrator can assume"
  type        = list(string)
}

variable "destination_role_arns" {
  description = "List of IAM role ARNs in destination accounts that the orchestrator can assume"
  type        = list(string)
}

variable "additional_assume_role_arns" {
  description = "Additional IAM role ARNs the orchestrator can assume (e.g. EKS-specific roles for legacy clusters)"
  type        = list(string)
  default     = []
}
