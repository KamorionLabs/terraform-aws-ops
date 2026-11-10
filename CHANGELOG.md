# Changelog

Notable changes to the modules of this repository. Versions are the git tags
consumers pin (`?ref=vX.Y.Z`). Earlier versions are described by their tag
messages (`git tag -n99 -l 'v1.*'`).

## v1.24.0 (proposed)

### Fixed

- **destination-account, source-account: Lambda log groups and their IAM grants
  were built from `resource_prefixes.log_group` prefixed again with
  `/aws/lambda/`.** A consumer passing the full path
  (`log_group = "/aws/lambda/fct-…"`) got log groups named
  `/aws/lambda//aws/lambda/fct-…-k8s-proxy` (and `…-run-sql`, …), and an IAM
  `CloudWatchLogs` statement on that same doubled path. AWS writes a function's
  logs to `/aws/lambda/<function name>` only, so the created groups stayed empty
  and the Lambdas had no right to create or write their real log group: their
  logs were lost.
  Log groups are now always `/aws/lambda/<function name>`, derived from
  `resource_prefixes.lambda`. The IAM grant covers
  `/aws/lambda/<resource_prefixes.lambda>-*` and
  `/aws/lambda/<dynamic_lambda_prefix>-*`, so helper Lambdas created by Step
  Functions (`<dynamic_lambda_prefix>-get-efs-subpath-*`,
  `…-check-flag-file-*`) can log too. The pattern ends in `-*` (no `:*`), so it
  matches the log group ARN with or without its trailing `:*`, and its streams.

### Deprecated

- `resource_prefixes.log_group` is ignored. It stays in the input type so
  existing callers keep validating; remove it from your configuration.

### Upgrade notes

- Consumers that never set `resource_prefixes.log_group`, or set it equal to
  `resource_prefixes.lambda`: no change in the plan.
- Consumers that set it to something else (typically `/aws/lambda/<prefix>`):
  each `aws_cloudwatch_log_group.k8s_proxy[0]` / `aws_cloudwatch_log_group.lambda[*]`
  is **replaced** (the doubled-path group is destroyed, the correct one created),
  and the `CloudWatchLogs` statement of the inline policies
  (`<iam_policy>-k8s-proxy-lambda`, `<iam_policy>-lambda-policy`,
  `<iam_policy>-lambda-execution-policy`) is updated in place.
  The destroyed groups only held what the Lambdas could write there: nothing.
  Check `storedBytes` before applying if in doubt:
  `aws logs describe-log-groups --log-group-name-prefix '/aws/lambda//aws/lambda/'`.
- If `/aws/lambda/<function name>` already exists in the account (a Lambda that
  could log through another policy), the create fails with
  `ResourceAlreadyExistsException`. Import it from the root module before
  applying, for example:

  ```hcl
  import {
    for_each = { for k, v in local.destination_accounts : k => v }
    to       = module.refresh_destination[each.key].aws_cloudwatch_log_group.k8s_proxy[0]
    id       = "/aws/lambda/fct-dig-${each.value.env_abbrev}-refresh-dest-k8s-proxy"
  }
  ```

  (`for_each` in `import` blocks needs OpenTofu >= 1.7 / Terraform >= 1.7.)

### Tests

- `tofu test` suites for both modules (mocked providers, plan only), run by the
  new `Test Modules` CI job.
