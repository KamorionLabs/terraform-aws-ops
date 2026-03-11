# -----------------------------------------------------------------------------
# Outputs - Lambda Code Module
# -----------------------------------------------------------------------------

output "bucket_name" {
  description = "Name of the S3 bucket containing Lambda code"
  value       = local.bucket_name
}

output "bucket_arn" {
  description = "ARN of the S3 bucket containing Lambda code (only when create_bucket = true)"
  value       = var.create_bucket ? aws_s3_bucket.lambda_code[0].arn : null
}

output "lambda_code_s3_keys" {
  description = "Map of Lambda function names to their S3 keys"
  value = {
    for k, v in aws_s3_object.lambda_code : k => v.key
  }
}

# Pre-formatted config for Step Functions input
output "check_flag_file_config" {
  description = "Lambda configuration for check-flag-file"
  value = {
    CodeS3Bucket = local.bucket_name
    CodeS3Key    = aws_s3_object.lambda_code["check-flag-file"].key
  }
}

output "get_efs_subpath_config" {
  description = "Lambda configuration for get-efs-subpath"
  value = {
    CodeS3Bucket = local.bucket_name
    CodeS3Key    = aws_s3_object.lambda_code["get-efs-subpath"].key
  }
}

output "cross_region_rds_proxy_config" {
  description = "Lambda configuration for cross-region-rds-proxy"
  value = {
    CodeS3Bucket = local.bucket_name
    CodeS3Key    = aws_s3_object.lambda_code["cross-region-rds-proxy"].key
  }
}
