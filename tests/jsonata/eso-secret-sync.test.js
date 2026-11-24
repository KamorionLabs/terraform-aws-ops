#!/usr/bin/env node
'use strict';
/**
 * Semantic regression tests for the ExternalSecret pre-scale sync in ScaleServices.
 *
 * Reads the real ASL file, extracts the JSONata expressions that decide whether the
 * ExternalSecret must be synced before scaling up and whether it has synced, and
 * evaluates them against representative inputs with the reference jsonata engine.
 * Locks in the fix for pods starting before the Secret holds the new EFS sub-path.
 */
const path = require('path');
const fs = require('fs');
const jsonata = require('jsonata');
const { REPO_ROOT } = require('./lib/extract');

const FILE = path.join(REPO_ROOT, 'modules', 'step-functions', 'eks', 'scale_services.asl.json');
const STATES = JSON.parse(fs.readFileSync(FILE, 'utf8')).States;
const strip = (raw) => raw.trim().slice(2, -2).trim();

const RESOLVE = strip(STATES.ResolveSecretRefresh.Output);
const PRIVATE_OBSERVED = strip(STATES.SecretSyncPollPrivate.Assign.syncObserved);
const PUBLIC_OBSERVED = strip(STATES.SecretSyncPollPublic.Assign.syncObserved);
const PUBLIC_PUT_BODY = strip(STATES.SecretSyncForceSyncPublic.Arguments.RequestBody);
const [SYNCED_VALUE, SYNCED_REFRESH, TIMED_OUT] =
  STATES.SecretSyncEvaluate.Choices.map((c) => strip(c.Condition));

const ev = (expr, input, vars) => jsonata(expr).evaluate(input || {}, Object.assign({ states: { input } }, vars || {}));

let pass = 0, fail = 0;
function check(name, cond, detail) {
  if (cond) { pass++; console.log('  ok   ' + name); }
  else { fail++; console.log('  FAIL ' + name + (detail ? '  <<< ' + detail : '')); }
}

const b64 = (s) => Buffer.from(s, 'utf8').toString('base64');
const EKS = { Namespace: 'app-ns', AccessMode: 'private', ClusterName: 'cluster-a' };

(async () => {
  console.log('RESOLVE (ResolveSecretRefresh)');
  let r = (await ev(RESOLVE, { EKS: Object.assign({}, EKS, { SecretRefresh: { Mode: 'annotate', SecretName: 'media-path' } }), Replicas: 1 })).SecretRefreshResolved;
  check('R1 annotate + scale up -> PreScaleSync true', r.PreScaleSync === true, JSON.stringify(r));
  check('R1 defaults: ES name = SecretName, ns = EKS.Namespace, v1, 300s/10s -> 30 polls',
    r.ExternalSecretName === 'media-path' && r.Namespace === 'app-ns' && r.ApiVersion === 'v1' && r.MaxPolls === 30, JSON.stringify(r));
  check('R1 no SecretKey -> SecretKey null', r.SecretKey === null);

  r = (await ev(RESOLVE, { EKS: Object.assign({}, EKS, { SecretRefresh: { Mode: 'annotate', SecretName: 'media-path' } }), Replicas: 0 })).SecretRefreshResolved;
  check('R2 annotate + scale down -> PreScaleSync false', r.PreScaleSync === false, JSON.stringify(r));

  r = (await ev(RESOLVE, { EKS, Replicas: 1, CleanupSecret: true })).SecretRefreshResolved;
  check('R3 no SecretRefresh -> legacy delete mode, no pre-scale sync', r.Mode === 'delete' && r.PreScaleSync === false, JSON.stringify(r));

  r = (await ev(RESOLVE, {
    EKS: Object.assign({}, EKS, { SecretRefresh: { Mode: 'annotate', SecretName: 's', ExternalSecretName: 'es', Namespace: 'other', SecretKey: 'SUB_PATH', WaitTimeoutSeconds: 45, PollIntervalSeconds: 20 } }),
    Replicas: 1, ExpectedSecretValueSSMParameter: '/param/subpath',
  })).SecretRefreshResolved;
  check('R4 overrides honoured, ceil(45/20) = 3 polls',
    r.ExternalSecretName === 'es' && r.Namespace === 'other' && r.SecretKey === 'SUB_PATH' && r.MaxPolls === 3 && r.ExpectedValueSSMParameter === '/param/subpath', JSON.stringify(r));

  r = (await ev(RESOLVE, { EKS: Object.assign({}, EKS, { SecretRefresh: { Mode: 'annotate', SecretName: 's', WaitTimeoutSeconds: 0 } }), Replicas: 1 })).SecretRefreshResolved;
  check('R5 WaitTimeoutSeconds 0 -> MaxPolls 0 (wait disabled)', r.MaxPolls === 0, JSON.stringify(r));

  console.log('OBSERVE (poll Assign)');
  const cfg = { SecretKey: 'SUB_PATH', MaxPolls: 3 };
  let o = await ev(PRIVATE_OBSERVED, {}, { syncMode: 'SecretValue', syncCfg: cfg, states: { result: { Payload: { Value: 'restore_1/data' } } } });
  check('O1 private SecretValue keeps only the value', JSON.stringify(o) === '{"value":"restore_1/data"}', JSON.stringify(o));
  o = await ev(PUBLIC_OBSERVED, {}, { syncMode: 'SecretValue', syncCfg: cfg, states: { result: { ResponseBody: { data: { SUB_PATH: b64('restore_1/data'), OTHER: b64('do-not-keep') } } } } });
  check('O2 public SecretValue decodes the key and drops the others', JSON.stringify(o) === '{"value":"restore_1/data"}', JSON.stringify(o));
  o = await ev(PUBLIC_OBSERVED, {}, { syncMode: 'SecretValue', syncCfg: cfg, states: { result: { ResponseBody: { data: {} } } } });
  check('O3 public SecretValue, key missing -> value null', o && o.value === null, JSON.stringify(o));
  o = await ev(PUBLIC_OBSERVED, {}, { syncMode: 'RefreshTime', syncCfg: cfg, states: { result: { ResponseBody: { status: { refreshTime: '2026-01-01T00:00:05Z' }, spec: {} } } } });
  check('O4 RefreshTime keeps only status', o && o.status && o.status.refreshTime && !o.spec, JSON.stringify(o));

  console.log('EVALUATE (SecretSyncEvaluate)');
  const at = '2026-01-01T00:00:05.400Z';
  const ready = [{ type: 'Ready', status: 'True' }];
  const base = { syncCfg: cfg, syncForceSyncAt: at, syncExpected: 'restore_1/data', syncPolls: 1 };
  const evalChoice = (expr, vars) => ev(expr, {}, Object.assign({}, base, vars));

  check('E1 Secret holds the new value -> synced',
    (await evalChoice(SYNCED_VALUE, { syncMode: 'SecretValue', syncObserved: { value: 'restore_1/data' } })) === true);
  check('E2 Secret still holds the old value -> not synced',
    (await evalChoice(SYNCED_VALUE, { syncMode: 'SecretValue', syncObserved: { value: '' } })) === false);
  check('E3 poll error (observed null) -> not synced, no evaluation error',
    (await evalChoice(SYNCED_VALUE, { syncMode: 'SecretValue', syncObserved: null })) === false);
  check('E4 refreshTime same second as force-sync + Ready -> synced',
    (await evalChoice(SYNCED_REFRESH, { syncMode: 'RefreshTime', syncObserved: { status: { refreshTime: '2026-01-01T00:00:05Z', conditions: ready } } })) === true);
  check('E5 refreshTime one hour old -> not synced',
    (await evalChoice(SYNCED_REFRESH, { syncMode: 'RefreshTime', syncObserved: { status: { refreshTime: '2025-12-31T23:00:05Z', conditions: ready } } })) === false);
  check('E6 refreshed but not Ready -> not synced',
    (await evalChoice(SYNCED_REFRESH, { syncMode: 'RefreshTime', syncObserved: { status: { refreshTime: '2026-01-01T00:01:00Z', conditions: [{ type: 'Ready', status: 'False' }] } } })) === false);
  check('E7 SecretValue mode never matches the RefreshTime rule',
    (await evalChoice(SYNCED_REFRESH, { syncMode: 'SecretValue', syncObserved: { value: 'x' } })) === false);
  check('E8 polls exhausted -> timeout', (await evalChoice(TIMED_OUT, { syncPolls: 3 })) === true);
  check('E9 polls left -> keep waiting', (await evalChoice(TIMED_OUT, { syncPolls: 2 })) === false);

  console.log('FORCE-SYNC (public PUT body)');
  const es = { apiVersion: 'external-secrets.io/v1', kind: 'ExternalSecret', metadata: { name: 'es', resourceVersion: '7', annotations: { keep: 'me' } }, spec: { refreshInterval: '1h' }, status: { refreshTime: 'x' } };
  const body = await ev(PUBLIC_PUT_BODY, {}, { syncEs: es, syncForceSyncAt: at });
  check('F1 PUT body sets force-sync, keeps other annotations and resourceVersion, drops status',
    body.metadata.annotations['force-sync'] === at && body.metadata.annotations.keep === 'me' && body.metadata.resourceVersion === '7' && body.status === undefined && body.spec.refreshInterval === '1h',
    JSON.stringify(body));
  const bodyNoAnn = await ev(PUBLIC_PUT_BODY, {}, { syncEs: { metadata: { name: 'es' }, spec: {} }, syncForceSyncAt: at });
  check('F2 ExternalSecret without annotations gets force-sync', bodyNoAnn.metadata.annotations['force-sync'] === at, JSON.stringify(bodyNoAnn));

  console.log(`\nESO secret sync semantic regression: ${pass} passed, ${fail} failed`);
  process.exit(fail ? 1 : 0);
})();
