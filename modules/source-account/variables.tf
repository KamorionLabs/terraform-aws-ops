# -----------------------------------------------------------------------------
# Variables - Source Account Module
# -----------------------------------------------------------------------------

variable "prefix" {
  description = "Default prefix for resource names. Can be overridden per resource type using resource_prefixes."
  type        = string
  default     = "refresh"
}

variable "resource_prefixes" {
  description = "Custom prefixes per resource type. Falls back to var.prefix if not specified."
  type = object({
    iam_role       = optional(string)
    iam_policy     = optional(string)
    security_group = optional(string)
    lambda         = optional(string)
    log_group      = optional(string)
  })
  default = {}
}

variable "tags" {
  description = "Tags to apply to resources"
  type        = map(string)
  default     = {}
}

variable "orchestrator_role_arn" {
  description = "ARN of the orchestrator IAM role (from shared services account). Required if create_role is true."
  type        = string
  default     = null
}

variable "cross_region_proxy_role_arn" {
  description = "ARN of the cross-region proxy Lambda IAM role (from shared services account). Optional, for cross-region RDS operations."
  type        = string
  default     = null
}

variable "additional_trust_principal_arns" {
  description = "Additional IAM role ARNs that can assume this role (e.g., ops-dashboard Step Functions role)"
  type        = list(string)
  default     = []
}

variable "aws_organization_id" {
  description = "AWS Organization ID for additional trust policy condition (optional)"
  type        = string
  default     = null
}

# -----------------------------------------------------------------------------
# IAM Role Configuration
# -----------------------------------------------------------------------------

variable "create_role" {
  description = "Create IAM role for source account. Set to false to use existing_role_arn."
  type        = bool
  default     = true
}

variable "existing_role_arn" {
  description = "ARN of existing IAM role to use instead of creating one. Required if create_role is false."
  type        = string
  default     = null
}

variable "existing_role_name" {
  description = "Name of existing IAM role (for attaching policies). Required if create_role is false and attach_policies is true."
  type        = string
  default     = null
}

variable "attach_policies" {
  description = "Attach IAM policies to the role. Set to false if policies are managed externally."
  type        = bool
  default     = true
}

# -----------------------------------------------------------------------------
# Feature Toggles
# -----------------------------------------------------------------------------

variable "enable_efs" {
  description = "Enable EFS-related permissions (for backup copy)"
  type        = bool
  default     = true
}

# -----------------------------------------------------------------------------
# Resource ARNs
# -----------------------------------------------------------------------------

variable "kms_key_arns" {
  description = "List of KMS key ARNs for snapshot encryption"
  type        = list(string)
  default     = ["*"]
}

variable "lambda_code_bucket_arn" {
  description = "ARN of S3 bucket containing Lambda code (for dynamic Lambda creation by Step Functions)"
  type        = string
  default     = null
}

# -----------------------------------------------------------------------------
# Lambda Configuration (for EFS flag file check)
# -----------------------------------------------------------------------------

variable "deploy_lambda_role" {
  description = "Deploy Lambda execution role for dynamic Lambda creation by Step Functions (flag file check). Set to false to use existing_lambda_role_arn."
  type        = bool
  default     = false
}

variable "existing_lambda_role_arn" {
  description = "ARN of existing Lambda execution role. Required if deploy_lambda_role is false and Lambda functionality is needed."
  type        = string
  default     = null
}

variable "existing_lambda_role_name" {
  description = "Name of existing Lambda execution role (for reference). Optional."
  type        = string
  default     = null
}

variable "vpc_id" {
  description = "VPC ID for Lambda security group. Required if deploy_lambda_role and create_lambda_security_group are true."
  type        = string
  default     = null
}

variable "lambda_subnet_ids" {
  description = "Subnet IDs for Lambda VPC configuration (output only, for use in Step Function config)."
  type        = list(string)
  default     = []
}

variable "lambda_security_group_ids" {
  description = "Security group IDs for Lambda VPC configuration. Used if create_lambda_security_group is false."
  type        = list(string)
  default     = []
}

variable "create_lambda_security_group" {
  description = "Create security group for Lambda functions. Set to false to use lambda_security_group_ids."
  type        = bool
  default     = true
}

variable "efs_cidr_blocks" {
  description = "CIDR blocks for EFS access (NFS port 2049)"
  type        = list(string)
  default     = ["10.0.0.0/8"]
}

# -----------------------------------------------------------------------------
# Dynamic Lambda Configuration
# For Lambdas created dynamically by Step Functions (may use different prefixes)
# -----------------------------------------------------------------------------

variable "dynamic_lambda_prefix" {
  description = "Prefix for dynamic Lambdas created by Step Functions (defaults to var.prefix). Used in IAM policies alongside resource_prefixes.lambda."
  type        = string
  default     = null
}
