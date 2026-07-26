# -----------------------------------------------------------------------------
# Fast-clone cross-account — SOURCE side (ADR-018)
# Static RAM share of the source Aurora cluster(s) + KMS grant so a destination
# account can create a copy-on-write clone (RestoreDBClusterToPointInTime,
# RestoreType=copy-on-write) WITHOUT copying/sharing a snapshot.
#
# Provisioned in IaC (NOT created dynamically by the refresh SFN): the refresh
# role needs no RAM permissions, and there is no per-run RAM churn. Gate with
# fast_clone_ram_share.enabled=false when the share is managed outside the
# refresh (ops / Organization RAM policy / the cluster module) — the refresh
# then just assumes the share exists.
# -----------------------------------------------------------------------------

variable "fast_clone_ram_share" {
  description = <<-EOT
    Cross-account fast-clone (ADR-018). Shares the source Aurora cluster(s) via
    AWS RAM to the destination account(s) and grants the destination role(s) use
    of the source CMK(s), so the destination can create a copy-on-write clone.
    Set enabled=false when the RAM share is managed outside the refresh.
    - allow_external_principals: false assumes source+destination are in the same
      AWS Organization (RAM sharing enabled -> auto-accepted). true for cross-org
      (destination must accept the share).
  EOT
  type = object({
    enabled                   = bool
    destination_account_ids   = optional(list(string), [])
    source_cluster_arns       = optional(list(string), [])
    kms_key_arns              = optional(list(string), [])
    destination_role_arns     = optional(list(string), [])
    allow_external_principals = optional(bool, false)
  })
  default = {
    enabled = false
  }
}

locals {
  fast_clone_enabled = var.fast_clone_ram_share.enabled

  # One KMS grant per (source CMK, destination role) pair.
  fast_clone_kms_grants = local.fast_clone_enabled ? {
    for pair in setproduct(var.fast_clone_ram_share.kms_key_arns, var.fast_clone_ram_share.destination_role_arns) :
    "${pair[0]}__${pair[1]}" => { key_arn = pair[0], role_arn = pair[1] }
  } : {}
}

resource "aws_ram_resource_share" "fast_clone" {
  count                     = local.fast_clone_enabled ? 1 : 0
  name                      = "${local.prefixes.iam_role}-fast-clone"
  allow_external_principals = var.fast_clone_ram_share.allow_external_principals
  tags                      = var.tags
}

# The source Aurora cluster(s) to expose for cloning.
resource "aws_ram_resource_association" "fast_clone" {
  for_each           = local.fast_clone_enabled ? toset(var.fast_clone_ram_share.source_cluster_arns) : toset([])
  resource_arn       = each.value
  resource_share_arn = aws_ram_resource_share.fast_clone[0].arn
}

# The destination account(s) allowed to clone.
resource "aws_ram_principal_association" "fast_clone" {
  for_each           = local.fast_clone_enabled ? toset(var.fast_clone_ram_share.destination_account_ids) : toset([])
  principal          = each.value
  resource_share_arn = aws_ram_resource_share.fast_clone[0].arn
}

# Grant each destination role use of the source Aurora CMK(s), so the
# cross-account copy-on-write clone can decrypt the shared (encrypted) cluster.
# Mirrors the CMK sharing done for the backup_restore / snapshot restore paths.
resource "aws_kms_grant" "fast_clone" {
  for_each          = local.fast_clone_kms_grants
  name              = "${local.prefixes.iam_role}-fastclone-${substr(md5(each.key), 0, 8)}"
  key_id            = each.value.key_arn
  grantee_principal = each.value.role_arn
  operations = [
    "Decrypt",
    "GenerateDataKey",
    "GenerateDataKeyWithoutPlaintext",
    "ReEncryptFrom",
    "ReEncryptTo",
    "DescribeKey",
    "CreateGrant",
  ]
}

output "fast_clone_resource_share_arn" {
  description = "ARN of the RAM resource share for cross-account fast-clone (null when disabled)."
  value       = local.fast_clone_enabled ? aws_ram_resource_share.fast_clone[0].arn : null
}
