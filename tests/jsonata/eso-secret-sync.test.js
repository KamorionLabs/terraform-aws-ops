#!/usr/bin/env node
'use strict';
/**
 * Semantic regression tests for the ExternalSecret pre-scale sync in ScaleServices.
 *
 * Reads the real ASL file, extracts the JSONata expressions that build the list of
 * ExternalSecrets to force-sync before scaling up and that decide whether one has
 * synced, and evaluates them with the reference jsonata engine. Locks in the fix for
 * pods starting before ESO has copied the new EFS sub-path or database credentials.
 */
const path = require('path');
const fs = require('fs');
const jsonata = require('jsonata');
const { REPO_ROOT } = require('./lib/extract');

const FILE = path.join(REPO_ROOT, 'modules', 'step-functions', 'eks', 'scale_services.asl.json');
const STATES = JSON.parse(fs.readFileSync(FILE, 'utf8')).States;
const MAP = STATES.SyncExternalSecretsMap;
const INNER = MAP.ItemProcessor.States;
const strip = (raw) => raw.trim().slice(2, -2).trim();

const RESOLVE = strip(STATES.ResolveSecretRefresh.Output);
const ITEM_SELECTOR = strip(MAP.ItemSelector);
const PUT_BODY = strip(INNER.EsForceSyncPublic.Arguments.RequestBody);
const [SYNCED, TIMED_OUT] = INNER.EsEvaluate.Choices.map((c) => strip(c.Condition));

const ev = (expr, vars) => jsonata(expr).evaluate({}, vars);
const resolve = async (input) => (await ev(RESOLVE, { states: { input } })).SecretRefreshResolved;

let pass = 0, fail = 0;
function check(name, cond, detail) {
  if (cond) { pass++; console.log('  ok   ' + name); }
  else { fail++; console.log('  FAIL ' + name + (detail ? '  <<< ' + detail : '')); }
}

const EKS = { Namespace: 'app-ns', AccessMode: 'private', ClusterName: 'cluster-a' };
const withRefresh = (sr) => Object.assign({}, EKS, { SecretRefresh: sr });

(async () => {
  console.log('RESOLVE (ResolveSecretRefresh)');
  let r = await resolve({ EKS: withRefresh({ ExternalSecrets: [{ Name: 'media-path' }, { Name: 'db-app-user', Namespace: 'db-ns' }] }), Replicas: 1 });
  check('R1 list resolved, namespace defaults to EKS.Namespace',
    Array.isArray(r.ExternalSecrets) && r.ExternalSecrets.length === 2 &&
    r.ExternalSecrets[0].Namespace === 'app-ns' && r.ExternalSecrets[1].Namespace === 'db-ns', JSON.stringify(r.ExternalSecrets));
  check('R1 scale up with a list -> PreScaleSync true, v1, 300s/10s -> 30 polls',
    r.PreScaleSync === true && r.ApiVersion === 'v1' && r.MaxPolls === 30, JSON.stringify(r));

  r = await resolve({ EKS: withRefresh({ ExternalSecrets: [{ Name: 'only-one' }] }), Replicas: 1 });
  check('R2 single entry stays an array (Map Items)', Array.isArray(r.ExternalSecrets) && r.ExternalSecrets.length === 1, JSON.stringify(r.ExternalSecrets));

  r = await resolve({ EKS: withRefresh({ ExternalSecrets: [{ Name: 'a' }] }), Replicas: 0 });
  check('R3 scale down -> PreScaleSync false', r.PreScaleSync === false, JSON.stringify(r));

  r = await resolve({ EKS, Replicas: 1, CleanupSecret: true });
  check('R4 no SecretRefresh -> empty list, step skipped, legacy delete mode kept',
    Array.isArray(r.ExternalSecrets) && r.ExternalSecrets.length === 0 && r.PreScaleSync === false && r.Mode === 'delete', JSON.stringify(r));

  r = await resolve({ EKS: withRefresh({ ExternalSecrets: [] }), Replicas: 1 });
  check('R5 empty list -> step skipped', r.PreScaleSync === false && r.ExternalSecrets.length === 0, JSON.stringify(r));

  r = await resolve({ EKS: withRefresh({ Mode: 'annotate', SecretName: 'media-path', ExternalSecrets: [{ Name: 'media-path' }, { Name: 'db' }] }), Replicas: 1 });
  check('R6 annotate SecretName included once (deduplicated)',
    r.ExternalSecrets.length === 2 && r.ExternalSecrets.filter((x) => x.Name === 'media-path').length === 1, JSON.stringify(r.ExternalSecrets));

  r = await resolve({ EKS: withRefresh({ Mode: 'annotate', SecretName: 'media-path' }), Replicas: 1 });
  check('R7 legacy annotate mode alone -> one ExternalSecret', r.PreScaleSync === true && r.ExternalSecrets.length === 1, JSON.stringify(r.ExternalSecrets));

  r = await resolve({ EKS: withRefresh({ ExternalSecrets: [{ Name: '' }, { Namespace: 'x' }, 'bad', { Name: 'ok' }] }), Replicas: 1 });
  check('R8 invalid entries ignored', r.ExternalSecrets.length === 1 && r.ExternalSecrets[0].Name === 'ok', JSON.stringify(r.ExternalSecrets));

  r = await resolve({ EKS: withRefresh({ ExternalSecrets: [{ Name: 'a' }], WaitTimeoutSeconds: 45, PollIntervalSeconds: 20, ApiVersion: 'v1beta1' }), Replicas: 1 });
  check('R9 overrides: ceil(45/20) = 3 polls, ApiVersion', r.MaxPolls === 3 && r.ApiVersion === 'v1beta1', JSON.stringify(r));

  r = await resolve({ EKS: withRefresh({ ExternalSecrets: [{ Name: 'a' }], WaitTimeoutSeconds: 0 }), Replicas: 1 });
  check('R10 WaitTimeoutSeconds 0 -> MaxPolls 0 (force-sync only)', r.MaxPolls === 0 && r.PreScaleSync === true, JSON.stringify(r));

  console.log('ITEM (SyncExternalSecretsMap.ItemSelector)');
  const mapInput = { EKS, SecretRefreshResolved: { ApiVersion: 'v1', PollIntervalSeconds: 10, MaxPolls: 30, WaitTimeoutSeconds: 300 },
    EksCluster: { Cluster: { Name: 'unused' } }, K8sProxyLambdaArn: 'proxy', EksRoleArn: null, DestinationAccount: { RoleArn: 'role' } };
  const item = await ev(ITEM_SELECTOR, { states: { input: mapInput, context: { Map: { Item: { Value: { Name: 'db', Namespace: 'db-ns' } } } } } });
  check('I1 path built from ApiVersion, namespace and name',
    item.Path === '/apis/external-secrets.io/v1/namespaces/db-ns/externalsecrets/db' && item.MaxPolls === 30 && item.RoleArn === 'role', JSON.stringify(item));

  console.log('EVALUATE (EsEvaluate)');
  const at = '2026-01-01T00:00:05.400Z';
  const ready = [{ type: 'Ready', status: 'True', reason: 'SecretSynced' }];
  const evalWith = (expr, vars) => ev(expr, Object.assign({ forceSyncAt: at, polls: 1, states: { input: { MaxPolls: 3 } } }, vars));
  check('E1 refreshed in the force-sync second + Ready -> synced',
    (await evalWith(SYNCED, { esStatus: { refreshTime: '2026-01-01T00:00:05Z', conditions: ready } })) === true);
  check('E2 refreshed one hour earlier -> not synced',
    (await evalWith(SYNCED, { esStatus: { refreshTime: '2025-12-31T23:00:05Z', conditions: ready } })) === false);
  check('E3 refreshed but not Ready -> not synced',
    (await evalWith(SYNCED, { esStatus: { refreshTime: '2026-01-01T00:01:00Z', conditions: [{ type: 'Ready', status: 'False' }] } })) === false);
  check('E4 poll error (status null) -> not synced, no evaluation error',
    (await evalWith(SYNCED, { esStatus: null })) === false);
  check('E5 polls exhausted -> timeout', (await evalWith(TIMED_OUT, { polls: 3 })) === true);
  check('E6 polls left -> keep waiting', (await evalWith(TIMED_OUT, { polls: 2 })) === false);

  console.log('FORCE-SYNC (public PUT body)');
  const es = { apiVersion: 'external-secrets.io/v1', kind: 'ExternalSecret', metadata: { name: 'es', resourceVersion: '7', annotations: { keep: 'me' } }, spec: { refreshInterval: '1h' }, status: { refreshTime: 'x' } };
  const body = await ev(PUT_BODY, { es, forceSyncAt: at });
  check('F1 PUT body sets force-sync, keeps other annotations and resourceVersion, drops status',
    body.metadata.annotations['force-sync'] === at && body.metadata.annotations.keep === 'me' && body.metadata.resourceVersion === '7' && body.status === undefined && body.spec.refreshInterval === '1h',
    JSON.stringify(body));
  const bodyNoAnn = await ev(PUT_BODY, { es: { metadata: { name: 'es' }, spec: {} }, forceSyncAt: at });
  check('F2 ExternalSecret without annotations gets force-sync', bodyNoAnn.metadata.annotations['force-sync'] === at, JSON.stringify(bodyNoAnn));

  console.log(`\nESO secret sync semantic regression: ${pass} passed, ${fail} failed`);
  process.exit(fail ? 1 : 0);
})();
