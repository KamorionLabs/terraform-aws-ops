# ASL JSONata checks

JavaScript checks that validate the JSONata (`{% ... %}`) expressions embedded in
Step Functions ASL files against **the reference `jsonata` engine** — the same
grammar AWS Step Functions uses at `CreateStateMachine` / runtime.

## Why these exist (and why Python/Terraform can't replace them)

`tofu validate`, `scripts/validate_asl.py`, and `tests/test_asl_validation.py`
validate JSON shape, ASL structure, and SDK action names — **none of them parse
JSONata**. A JSONata expression can be structurally valid JSON and still be
rejected by AWS at deploy time. This was not hypothetical: Phase 7 shipped three
expressions whose lambda bodies used multi-statement `function(){ a; b; expr }`
syntax (invalid — requires `function(){ ( a; b; expr ) }`), plus a `$filter`
that returned a bare object instead of an array when exactly one rule remained.
All passed every other gate; only a JSONata parser/evaluator caught them.

## Checks

- **`validate-jsonata.js`** (`npm run test:parse`) — generic grammar gate.
  Auto-discovers every `modules/**/*.asl.json`, extracts each `{% ... %}`
  expression, and compiles it. Any parse failure fails the build. Covers the
  whole repo, not just S3.
- **`s3-replication.test.js`** (`npm run test:semantics`) — semantic regression
  tests for the S3 read-merge-write logic: stable per-destination priority on
  re-run (CR-03), preservation of other spokes' rules (CR-01), and the
  delete singleton-array coercion. Evaluates the real expressions against
  representative inputs.

Full AWS-runtime behaviour is covered separately by the stepfunctions-local
suite (`tests/test_stepfunctions_local.py`).

## Run locally

```bash
cd tests/jsonata
npm install
npm test
```

CI runs `npm ci && npm test` in the `validate-local` job of
`.github/workflows/step-functions.yml`.
