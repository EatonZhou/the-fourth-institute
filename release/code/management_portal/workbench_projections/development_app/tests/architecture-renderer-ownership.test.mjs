import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { gunzipSync } from 'node:zlib';
import { fileURLToPath } from 'node:url';
import { build } from 'esbuild';
import { resolveDomainEntry } from '../src/architecture/exploration-entry-routes.js';
import { createSchedulingSimulation } from '../src/architecture/scheduling-mechanisms.model.js';
import { createExecutionSimulation } from '../src/architecture/execution-workbench.model.js';

// Exercise the actual registry without a browser. CSS is omitted only in this
// Node import; package/worker loading is checked separately by the build audit.
const bundled = await build({
  entryPoints: [fileURLToPath(new URL('../src/architecture/exploration-renderers.js', import.meta.url))],
  bundle: true, write: false, format: 'esm', platform: 'node', loader: { '.css': 'empty' },
});
const registry = await import(`data:text/javascript;base64,${Buffer.from(bundled.outputFiles[0].contents).toString('base64')}`);
const model = JSON.parse(gunzipSync(readFileSync(new URL('./fixtures/frozen-exploration/model.json.gz', import.meta.url))));
const content = id => ({ ...model, batchKey: 'fixed-renderer-ownership', mechanism: model.mechanisms.find(m => m.id === id) });

test('Worker lease enters the declared scheduling owner; lifecycle remains an explicit distinct route', () => {
  const route = { kind: 'protocol', objectId: 'worker-lease', mechanismId: null };
  const resolved = resolveDomainEntry(model, route, registry.objectEntries);
  assert.equal(resolved.mechanismId, 'control.worker-protocol');
  const mechanism = content(resolved.mechanismId).mechanism;
  assert.equal(registry.mechanismFactory(mechanism), registry.mechanismIdFactories[mechanism.id]);
  assert.equal(typeof registry.objectWorkbenchFactory(model.objects.find(o => o.id === route.objectId), mechanism), 'function');
  const explicit = { ...route, mechanismId: 'execution.lifecycle' };
  assert.deepEqual(resolveDomainEntry(model, explicit, registry.objectEntries), explicit);
  assert.equal(registry.mechanismFactory(content(explicit.mechanismId).mechanism), registry.mechanismFactories['execution.lifecycle.v1']);
  assert.equal(registry.mechanismFactory({ id: 'invented-lease', adapter: 'worker-lease.v1' }), null);
});

test('the scheduling owner replays and restores its frozen protocol with exact batch identity', () => {
  const simulation = createSchedulingSimulation(content('control.worker-protocol'));
  simulation.advance();
  const state = simulation.getViewState(), scene = simulation.getScene();
  simulation.reset(); simulation.restore(state);
  assert.deepEqual(simulation.getScene(), scene);
  assert.throws(() => simulation.restore({ ...state, batchKey: 'other' }));
  assert.throws(() => simulation.restore({ ...state, mechanismId: 'execution.lifecycle' }));
});

test('completed-provider recovery retains reclaim and restart as distinct lifecycle steps', () => {
  const simulation = createExecutionSimulation(content('execution.lifecycle'));
  simulation.selectCase('recover_completed');
  const seen = [];
  while (simulation.getScene().next) {
    const scene = simulation.advance(); seen.push(scene.step_id);
    if (scene.step_id === 'completed-old-write') assert.equal(scene.state.rejected_old_write, true);
    if (scene.step_id === 'completed-claim') assert.equal(scene.state.execution_state, 'claimed');
    if (scene.step_id === 'completed-start') assert.equal(scene.state.execution_state, 'running');
  }
  assert.ok(seen.indexOf('completed-claim') >= 0);
  assert.ok(seen.indexOf('completed-start') > seen.indexOf('completed-claim'));
  assert.equal(simulation.getScene().explanation.provider_submissions, 1);
});
