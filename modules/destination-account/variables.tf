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
  description = "Create IAM role for destination account. Set to false to use existing_role_arn."
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
  description = "Enable EFS-related resources and permissions"
  type        = bool
  default     = true
}

variable "enable_eks" {
  description = "Enable EKS-related permissions"
  type        = bool
  default     = true
}

variable "deploy_lambdas" {
  description = "Deploy Lambda helper functions. Set to false to use existing_lambda_role_arn for dynamic Lambda creation."
  type        = bool
  default     = true
}

variable "existing_lambda_role_arn" {
  description = "ARN of existing Lambda execution role. Used when deploy_lambdas is false but dynamic Lambda creation is still needed."
  type        = string
  default     = null
}

variable "existing_lambda_role_name" {
  description = "Name of existing Lambda execution role (for reference). Optional."
  type        = string
  default     = null
}

# -----------------------------------------------------------------------------
# EKS Access Entry Configuration
# -----------------------------------------------------------------------------

variable "create_eks_access_entry" {
  description = "Create EKS Access Entry for the destination role"
  type        = bool
  default     = false
}

variable "eks_cluster_name" {
  description = "Name of the EKS cluster for access entry"
  type        = string
  default     = null
}

variable "eks_access_policy_arn" {
  description = "ARN of the EKS access policy to associate (e.g., arn:aws:eks::aws:cluster-access-policy/AmazonEKSClusterAdminPolicy)"
  type        = string
  default     = "arn:aws:eks::aws:cluster-access-policy/AmazonEKSClusterAdminPolicy"
}

variable "eks_access_scope_type" {
  description = "Scope type for EKS access (cluster or namespace)"
  type        = string
  default     = "cluster"

  validation {
    condition     = contains(["cluster", "namespace"], var.eks_access_scope_type)
    error_message = "eks_access_scope_type must be 'cluster' or 'namespace'."
  }
}

variable "eks_access_scope_namespaces" {
  description = "Namespaces for EKS access when scope_type is 'namespace'"
  type        = list(string)
  default     = []
}

variable "eks_access_policies" {
  description = <<-EOT
    List of EKS access policies to associate with the role.
    Each policy can have its own scope (cluster or namespace).
    If empty, falls back to eks_access_policy_arn/eks_access_scope_type/eks_access_scope_namespaces.
    Example:
      eks_access_policies = [
        {
          name       = "view"
          policy_arn = "arn:aws:eks::aws:cluster-access-policy/AmazonEKSViewPolicy"
          scope_type = "cluster"
          namespaces = []
        },
        {
          name       = "admin"
          policy_arn = "arn:aws:eks::aws:cluster-access-policy/AmazonEKSAdminPolicy"
          scope_type = "namespace"
          namespaces = ["rubix-mro-mi1-staging", "rubix-mro-mi2-staging"]
        }
      ]
  EOT
  type = list(object({
    name       = string
    policy_arn = string
    scope_type = string
    namespaces = list(string)
  }))
  default = []

  validation {
    condition = alltrue([
      for p in var.eks_access_policies : contains(["cluster", "namespace"], p.scope_type)
    ])
    error_message = "Each policy scope_type must be 'cluster' or 'namespace'."
  }
}

# -----------------------------------------------------------------------------
# Resource ARNs
# -----------------------------------------------------------------------------

variable "kms_key_arns" {
  description = "List of KMS key ARNs for encryption"
  type        = list(string)
  default     = ["*"]
}

variable "s3_bucket_arns" {
  description = "List of S3 bucket ARNs for SQL scripts and archives"
  type        = list(string)
  default     = ["arn:aws:s3:::*"]
}

variable "dynamodb_table_arn" {
  description = "ARN of DynamoDB table for notifications (optional)"
  type        = string
  default     = null
}

variable "sns_topic_arn" {
  description = "ARN of SNS topic for notifications (optional)"
  type        = string
  default     = null
}

# -----------------------------------------------------------------------------
# Lambda Configuration
# -----------------------------------------------------------------------------

variable "lambda_subnet_ids" {
  description = "Subnet IDs for Lambda VPC configuration"
  type        = list(string)
  default     = []
}

variable "lambda_security_group_ids" {
  description = "Security group IDs for Lambda VPC configuration"
  type        = list(string)
  default     = []
}

variable "lambda_log_level" {
  description = "Log level for Lambda functions"
  type        = string
  default     = "INFO"
}

variable "log_retention_days" {
  description = "CloudWatch log retention in days"
  type        = number
  default     = 30
}

# -----------------------------------------------------------------------------
# Lambda Package Paths (optional - override defaults)
# -----------------------------------------------------------------------------

variable "pymysql_layer_path" {
  description = "Path to PyMySQL layer zip file (optional, uses bundled if not provided)"
  type        = string
  default     = null
}

variable "run_sql_lambda_path" {
  description = "Path to run-sql Lambda zip file (optional, uses bundled if not provided)"
  type        = string
  default     = null
}

variable "get_efs_subpath_lambda_path" {
  description = "Path to get-efs-subpath Lambda zip file (optional, uses bundled if not provided)"
  type        = string
  default     = null
}

# -----------------------------------------------------------------------------
# EFS Configuration (for get-efs-subpath Lambda)
# -----------------------------------------------------------------------------

variable "efs_access_point_arn" {
  description = "ARN of EFS Access Point for Lambda to mount (required if enable_efs and deploy_lambdas)"
  type        = string
  default     = null
}

# -----------------------------------------------------------------------------
# Lambda Deployment Configuration
# -----------------------------------------------------------------------------

variable "vpc_id" {
  description = "VPC ID for Lambda security group"
  type        = string
  default     = null
}

variable "create_lambda_security_group" {
  description = "Create security group for Lambda functions"
  type        = bool
  default     = true
}

variable "database_cidr_blocks" {
  description = "CIDR blocks for database access (MySQL 3306)"
  type        = list(string)
  default     = ["10.0.0.0/8"]
}

variable "efs_cidr_blocks" {
  description = "CIDR blocks for EFS access (NFS 2049)"
  type        = list(string)
  default     = ["10.0.0.0/8"]
}

variable "lambda_code_bucket_arn" {
  description = "ARN of S3 bucket containing Lambda code (for dynamic Lambda creation by Step Functions)"
  type        = string
  default     = null
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

variable "ssm_parameter_prefixes" {
  description = "Additional SSM parameter path prefixes to allow (without leading slash)."
  type        = list(string)
  default     = []
}

# -----------------------------------------------------------------------------
# EKS Pod Identity Configuration
# For Kubernetes refresh jobs that need AWS access (e.g., S3 for SQL scripts)
# -----------------------------------------------------------------------------

variable "create_eks_pod_identity" {
  description = "Create EKS Pod Identity IAM role and associations for refresh jobs"
  type        = bool
  default     = false
}

variable "eks_pod_identity_associations" {
  description = "List of namespace/service_account pairs for Pod Identity associations"
  type = list(object({
    namespace       = string
    service_account = string
  }))
  default = []
}

variable "eks_pod_identity_s3_arns" {
  description = "S3 ARNs for Pod Identity role (refresh bucket read access)"
  type        = list(string)
  default     = []
}

# -----------------------------------------------------------------------------
# K8s Proxy Lambda Configuration
# For EKS clusters with private-only API endpoints
# -----------------------------------------------------------------------------

variable "enable_k8s_proxy" {
  description = "Deploy k8s-proxy Lambda for EKS private endpoint access. Uses the destination role as Lambda execution role."
  type        = bool
  default     = false
}

variable "k8s_proxy_lambda_path" {
  description = "Path to k8s-proxy Lambda source file (optional, uses bundled if not provided)"
  type        = string
  default     = null
}
