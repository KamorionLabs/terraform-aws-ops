# -----------------------------------------------------------------------------
# Fast-clone cross-account — DESTINATION side (ADR-018)
# The destination role already has rds:* (covers RestoreDBClusterToPointInTime
# on the cross-account source cluster ARN). Two things may still be needed:
#   1. KMS use of the SOURCE Aurora CMK(s) — only when var.kms_key_arns is
#      scoped (not "*") and doesn't already cover them.
#   2. Accepting the RAM share — only for cross-org shares; in-org shares
#      (RAM sharing enabled in the Organization) are auto-accepted.
# Both are gated and off by default.
# -----------------------------------------------------------------------------

variable "fast_clone" {
  description = <<-EOT
    Cross-account fast-clone (ADR-018), destination side.
    - source_kms_key_arns: source Aurora CMK(s) the destination role must use to
      decrypt the shared cluster during the copy-on-write clone. Only needed when
      var.kms_key_arns is scoped (i.e. not ["*"]).
    - resource_share_arn: RAM share ARN to accept. Only for cross-org shares;
      leave null for in-org shares (auto-accepted).
  EOT
  type = object({
    enabled             = optional(bool, false)
    source_kms_key_arns = optional(list(string), [])
    resource_share_arn  = optional(string, null)
  })
  default = {}
}

# Extra KMS permissions on the SOURCE Aurora CMK(s) for the cross-account clone.
resource "aws_iam_role_policy" "fast_clone_kms" {
  count = local.should_attach_policies && var.fast_clone.enabled && length(var.fast_clone.source_kms_key_arns) > 0 ? 1 : 0

  name = "${local.prefixes.iam_policy}-fast-clone-kms"
  role = local.role_id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "FastCloneSourceCmk"
        Effect = "Allow"
        Action = [
          "kms:Decrypt",
          "kms:GenerateDataKey*",
          "kms:ReEncrypt*",
          "kms:DescribeKey",
          "kms:CreateGrant",
        ]
        Resource = var.fast_clone.source_kms_key_arns
      }
    ]
  })
}

# Accept the RAM share for cross-org sharing (in-org shares auto-accept).
resource "aws_ram_resource_share_accepter" "fast_clone" {
  count     = var.fast_clone.enabled && var.fast_clone.resource_share_arn != null ? 1 : 0
  share_arn = var.fast_clone.resource_share_arn
}
