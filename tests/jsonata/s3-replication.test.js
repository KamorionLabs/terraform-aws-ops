#!/usr/bin/env node
'use strict';
/**
 * Semantic regression tests for the S3 replication read-merge-write logic.
 *
 * Reads the real ASL files, extracts the load-bearing JSONata expressions, and
 * evaluates them against representative inputs with the reference jsonata engine.
 * Locks in the fixes for:
 *   - CR-03 : stable per-destination priority (no collision on re-run)
 *   - CR-01 : merge preserves other spokes' rules
 *   - delete singleton-array : $filter returning one rule must stay an array
 *
 * These checks cannot run in `tofu validate` or the structural Python tests —
 * they require an actual JSONata evaluator. Full AWS-runtime confirmation is
 * still covered separately by the stepfunctions-local suite.
 */
const path = require('path');
const fs = require('fs');
const jsonata = require('jsonata');
const { REPO_ROOT } = require('./lib/extract');

const S3 = path.join(REPO_ROOT, 'modules', 'step-functions', 's3');

function exprOf(file, state, key) {
  const d = JSON.parse(fs.readFileSync(path.join(S3, file), 'utf8'));
  const raw = d.States[state].Assign[key].trim();
  return raw.slice(2, -2).trim(); // strip {% %}
}
const MERGE = exprOf('setup_cross_account_replication.asl.json', 'MergeAllRules', 'mergedRules');
const REMAINING = exprOf('delete_replication.asl.json', 'FilterRules', 'remainingRules');
const REMAINING_EMPTY = exprOf('delete_replication.asl.json', 'FilterRules', 'remainingEmpty');

const evalWith = (expr, statesInput) =>
  jsonata(expr).evaluate({}, { states: { input: statesInput } });

let pass = 0, fail = 0;
function check(name, cond, detail) {
  if (cond) { pass++; console.log('  ok   ' + name); }
  else { fail++; console.log('  FAIL ' + name + (detail ? '  <<< ' + detail : '')); }
}
const prio = (rules, id) => { const r = (rules || []).find(x => x.ID === id); return r ? r.Priority : null; };
const ID_A = 'repl-111111111111-dst-a-bucket';
const ID_B = 'repl-222222222222-dst-b-bucket';
const existingA = { ReplicationConfiguration: { Rules: [
  { ID: ID_A, Priority: 0, Status: 'Enabled', Destination: { Bucket: 'dst-a-bucket', Account: '111111111111' } },
] } };
const existingAB = { ReplicationConfiguration: { Rules: [
  { ID: ID_A, Priority: 0 }, { ID: ID_B, Priority: 1 },
] } };

(async () => {
  console.log('MERGE (setup / MergeAllRules)');
  let r = await evalWith(MERGE, { Destinations: [{ Bucket: 'dst-a-bucket', AccountId: '111111111111' }] });
  check('S1 first destination -> 1 rule @ priority 0', Array.isArray(r) && r.length === 1 && r[0].Priority === 0, JSON.stringify(r));

  r = await evalWith(MERGE, { Existing: existingA, Destinations: [{ Bucket: 'dst-b-bucket', AccountId: '222222222222' }] });
  check('S2 add destination -> A preserved @0, B @1 (max+1)',
    r.length === 2 && prio(r, ID_A) === 0 && prio(r, ID_B) === 1, JSON.stringify(r.map(x => ({ ID: x.ID, P: x.Priority }))));

  r = await evalWith(MERGE, { Existing: existingA, Destinations: [{ Bucket: 'dst-a-bucket', AccountId: '111111111111' }] });
  check('S3 re-run same destination -> no duplicate, priority stable @0 (CR-03)',
    r.length === 1 && prio(r, ID_A) === 0, JSON.stringify(r.map(x => ({ ID: x.ID, P: x.Priority }))));

  r = await evalWith(MERGE, { Destinations: [
    { Bucket: 'b1', AccountId: '111111111111' }, { Bucket: 'b2', AccountId: '222222222222' }, { Bucket: 'b3', AccountId: '333333333333' },
  ] });
  check('S4 three destinations -> distinct priorities', r.length === 3 && new Set(r.map(x => x.Priority)).size === 3, JSON.stringify(r.map(x => x.Priority)));

  r = await evalWith(MERGE, { Destinations: [{ Bucket: 'b1', AccountId: '111111111111', RTC: { Status: 'Enabled' } }] });
  check('S5 RTC enabled -> Metrics + ReplicationTime', !!r[0].Destination.Metrics && !!r[0].Destination.ReplicationTime, JSON.stringify(r[0].Destination));

  console.log('\nFILTER (delete / FilterRules)');
  let f = await evalWith(REMAINING, { Existing: existingAB, Destinations: [{ Bucket: 'dst-a-bucket', AccountId: '111111111111' }] });
  let empty = await evalWith(REMAINING_EMPTY, { Existing: existingAB, Destinations: [{ Bucket: 'dst-a-bucket', AccountId: '111111111111' }] });
  check('D1 remove 1 of 2 -> remainingRules is an ARRAY (singleton fix)', Array.isArray(f), JSON.stringify(f));
  check('D1 remaining is B only', Array.isArray(f) && f.length === 1 && f[0].ID === ID_B);
  check('D1 remainingEmpty = false', empty === false, String(empty));

  empty = await evalWith(REMAINING_EMPTY, { Existing: existingA, Destinations: [{ Bucket: 'dst-a-bucket', AccountId: '111111111111' }] });
  check('D2 remove last -> remainingEmpty = true (routes DeleteAllReplication)', empty === true, String(empty));

  f = await evalWith(REMAINING, { Existing: existingAB, Destinations: [{ Bucket: 'dst-c', AccountId: '999999999999' }] });
  check('D3 remove non-existent -> unchanged (2 rules)', Array.isArray(f) && f.length === 2, JSON.stringify(f));

  console.log(`\nS3 semantic regression: ${pass} passed, ${fail} failed`);
  process.exit(fail > 0 ? 1 : 0);
})().catch(e => { console.error('HARNESS ERROR:', e.message); process.exit(2); });
