# Lambda log groups must be /aws/lambda/<function name>: AWS writes there, and
# nowhere else, unless the function sets a custom LoggingConfig (it does not).
# Regression: resource_prefixes.log_group = "/aws/lambda/<prefix>" used to yield
# "/aws/lambda//aws/lambda/<prefix>-k8s-proxy" and an IAM grant on that path,
# so the Lambdas could neither create nor write their real log group.
#
# Run: tofu init -backend=false && tofu test   (in modules/destination-account)

mock_provider "aws" {
  override_data {
    target = data.aws_caller_identity.current
    values = { account_id = "111111111111" }
  }
  override_data {
    target = data.aws_region.current
    values = { id = "eu-central-1", name = "eu-central-1" }
  }
  # Mocked ARNs must still parse as ARNs (the provider validates them at plan).
  mock_resource "aws_iam_role" {
    defaults = { arn = "arn:aws:iam::111111111111:role/mock" }
  }
  mock_resource "aws_lambda_layer_version" {
    defaults = { arn = "arn:aws:lambda:eu-central-1:111111111111:layer:mock:1" }
  }
}
mock_provider "archive" {}
mock_provider "null" {}

variables {
  prefix           = "refresh"
  enable_k8s_proxy = true
  deploy_lambdas   = true
  enable_efs       = true
  vpc_id           = "vpc-12345678"
  lambda_subnet_ids = [
    "subnet-12345678",
  ]
}

run "log_group_input_holding_the_path_is_ignored" {
  command = plan

  variables {
    resource_prefixes = {
      iam_role   = "iam-dig-stg-refresh"
      iam_policy = "pol-dig-stg-refresh"
      lambda     = "fct-dig-stg-refresh-dest"
      # The value the Rubix refresh stack passes (v1.23.0 and earlier).
      log_group = "/aws/lambda/fct-dig-stg-refresh-dest"
    }
    dynamic_lambda_prefix = "fct-dig-stg-refresh"
  }

  assert {
    condition     = aws_cloudwatch_log_group.k8s_proxy[0].name == "/aws/lambda/${aws_lambda_function.k8s_proxy[0].function_name}"
    error_message = "k8s-proxy log group must be /aws/lambda/<function name>, got ${aws_cloudwatch_log_group.k8s_proxy[0].name}"
  }

  assert {
    condition     = aws_cloudwatch_log_group.k8s_proxy[0].name == "/aws/lambda/fct-dig-stg-refresh-dest-k8s-proxy"
    error_message = "unexpected k8s-proxy log group name ${aws_cloudwatch_log_group.k8s_proxy[0].name}"
  }

  assert {
    condition = alltrue([
      for k, lg in aws_cloudwatch_log_group.lambda :
      lg.name == "/aws/lambda/${aws_lambda_function.functions[k].function_name}"
    ])
    error_message = "helper Lambda log groups must be /aws/lambda/<function name>"
  }

  assert {
    condition = alltrue([
      for arn in local.lambda_log_group_arns : !strcontains(arn, "/aws/lambda//")
    ])
    error_message = "IAM log grants must not contain a doubled /aws/lambda/ path"
  }

  # Helper Lambdas created by Step Functions use dynamic_lambda_prefix
  # (fct-dig-stg-refresh-get-efs-subpath-*): the grant must cover them too.
  assert {
    condition = (
      contains(local.lambda_log_group_arns, "arn:aws:logs:*:111111111111:log-group:/aws/lambda/fct-dig-stg-refresh-dest-*") &&
      contains(local.lambda_log_group_arns, "arn:aws:logs:*:111111111111:log-group:/aws/lambda/fct-dig-stg-refresh-*")
    )
    error_message = "IAM log grants must cover the lambda and dynamic Lambda prefixes: ${jsonencode(local.lambda_log_group_arns)}"
  }
}

run "defaults_are_unchanged" {
  command = plan

  assert {
    condition     = aws_cloudwatch_log_group.k8s_proxy[0].name == "/aws/lambda/refresh-k8s-proxy"
    error_message = "default k8s-proxy log group changed: ${aws_cloudwatch_log_group.k8s_proxy[0].name}"
  }

  assert {
    condition     = local.lambda_log_group_arns == ["arn:aws:logs:*:111111111111:log-group:/aws/lambda/refresh-*"]
    error_message = "default IAM log grant changed: ${jsonencode(local.lambda_log_group_arns)}"
  }
}
