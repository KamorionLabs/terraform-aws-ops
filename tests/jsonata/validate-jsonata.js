#!/usr/bin/env node
'use strict';
/**
 * Generic JSONata grammar gate for Step Functions ASL.
 *
 * Auto-discovers every `*.asl.json` under modules/, extracts each
 * `{% ... %}` expression, and compiles it with the reference `jsonata`
 * engine — the same grammar AWS Step Functions uses. Any expression that
 * fails to parse is a deploy blocker (rejected at CreateStateMachine /
 * runtime) that `tofu validate` and the Python structural tests do NOT catch,
 * because nothing else in the stack parses JSONata.
 *
 * Exit 0 = all expressions parse. Exit 1 = at least one parse failure.
 */
const path = require('path');
const fs = require('fs');
const jsonata = require('jsonata');
const { REPO_ROOT, findAslFiles, collectExprs } = require('./lib/extract');

const MODULES_DIR = path.join(REPO_ROOT, 'modules');

function main() {
  if (!fs.existsSync(MODULES_DIR)) {
    console.error(`modules/ not found at ${MODULES_DIR}`);
    process.exit(1);
  }
  const files = findAslFiles(MODULES_DIR);
  let total = 0;
  let withJsonata = 0;
  const failures = [];

  for (const file of files) {
    const rel = path.relative(REPO_ROOT, file);
    let data;
    try {
      data = JSON.parse(fs.readFileSync(file, 'utf8'));
    } catch (e) {
      failures.push({ file: rel, path: '(file)', error: `invalid JSON: ${e.message}`, snippet: '' });
      continue;
    }
    const exprs = collectExprs(data, '$', []);
    if (exprs.length) withJsonata++;
    for (const { path: p, expr } of exprs) {
      total++;
      try {
        jsonata(expr); // compile-only parse check
      } catch (e) {
        failures.push({ file: rel, path: p, error: e.message, snippet: expr.slice(0, 90) });
      }
    }
  }

  console.log(`ASL files scanned          : ${files.length}`);
  console.log(`Files using JSONata        : ${withJsonata}`);
  console.log(`JSONata expressions parsed : ${total}`);
  console.log(`Parse failures             : ${failures.length}`);

  if (failures.length) {
    console.log('\nFAILURES:');
    for (const f of failures) {
      console.log(`\n  ✗ ${f.file}`);
      console.log(`     path : ${f.path}`);
      console.log(`     error: ${f.error}`);
      if (f.snippet) console.log(`     expr : ${f.snippet}...`);
    }
    console.log('\nJSONata grammar gate: FAILED');
    process.exit(1);
  }
  console.log('\nJSONata grammar gate: PASSED');
}

main();
