# The source Lambda role must be granted /aws/lambda/<function name>, never a
# doubled "/aws/lambda//aws/lambda/..." path (resource_prefixes.log_group used
# to be prefixed again with /aws/lambda/).
#
# Run: tofu init -backend=false && tofu test   (in modules/source-account)

mock_provider "aws" {
  override_data {
    target = data.aws_caller_identity.current
    values = { account_id = "222222222222" }
  }
  override_data {
    target = data.aws_region.current
    values = { id = "eu-central-1", name = "eu-central-1" }
  }
}

run "log_group_input_holding_the_path_is_ignored" {
  command = plan

  variables {
    prefix = "refresh"
    resource_prefixes = {
      lambda    = "fct-dig-prd-refresh-source"
      log_group = "/aws/lambda/fct-dig-prd-refresh-source"
    }
    dynamic_lambda_prefix = "fct-dig-prd-refresh"
  }

  assert {
    condition = local.lambda_log_group_arns == [
      "arn:aws:logs:*:222222222222:log-group:/aws/lambda/fct-dig-prd-refresh-source-*",
      "arn:aws:logs:*:222222222222:log-group:/aws/lambda/fct-dig-prd-refresh-*",
    ]
    error_message = "unexpected IAM log grants: ${jsonencode(local.lambda_log_group_arns)}"
  }
}
