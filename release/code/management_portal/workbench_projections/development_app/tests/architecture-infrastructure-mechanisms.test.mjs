import {readFileSync} from 'node:fs';
import test from 'node:test';
import assert from 'node:assert/strict';
import {createInfrastructureSimulation} from '../src/architecture/infrastructure-mechanisms.model.js';

const fixture = JSON.parse(readFileSync(new URL('./architecture-infrastructure-mechanisms.fixture.json', import.meta.url)));
const content = name => ({batchKey: fixture.batchKey, objects: fixture.objects, evidence: fixture.evidence,
  mechanism: structuredClone(fixture.mechanisms.find(m => m.id === `infra.mechanism.${name}`))});
const finish = (simulation, caseId) => { simulation.selectCase(caseId); while (!simulation.getScene().complete) simulation.advance(); return simulation.getScene(); };

test('owner-authored infrastructure cases preserve every declared final observation', () => {
  for (const mechanism of fixture.mechanisms) {
    const simulation = createInfrastructureSimulation(content(mechanism.id.slice('infra.mechanism.'.length)));
    for (const scenario of mechanism.cases) {
      const scene = finish(simulation, scenario.id);
      assert.deepEqual(scene.state, scenario.expected_state, `${mechanism.id}/${scenario.id}`);
      assert.deepEqual(scene.events, scenario.path);
    }
  }
});
test('RPC dispatch with a missing reply stays unknown and replay fails before dispatch', () => {
  const simulation = createInfrastructureSimulation(content('rpc-envelope'));
  assert.equal(finish(simulation, 'unknown').state.commit_state, 'unknown');
  assert.equal(finish(simulation, 'replay').state.dispatched, false);
  const denied = finish(simulation, 'denied').state;
  assert.equal(denied.signature_valid, true); assert.equal(denied.authorized, false);
});
test('local release completion and push outcome remain independent', () => {
  const simulation = createInfrastructureSimulation(content('release-publication'));
  const pushed = finish(simulation, 'push').state;
  assert.equal(pushed.publication_recorded, true); assert.equal(pushed.push_status, 'failed');
  const local = finish(simulation, 'local').state;
  assert.equal(local.push_status, 'ok'); assert.equal(local.publication_recorded, false);
});
test('notification overflow retains the correct recovery scope', () => {
  const simulation = createInfrastructureSimulation(content('subscriber-overflow'));
  assert.equal(finish(simulation, 'slow').state.snapshot_scope, 'single');
  assert.equal(finish(simulation, 'burst').state.snapshot_scope, 'all');
  const hub = createInfrastructureSimulation(content('hub-cursor'));
  assert.equal(finish(hub, 'gap').state.snapshot_required, true);
});
test('old request confirmation cannot erase a newer component request', () => {
  const s = finish(createInfrastructureSimulation(content('request-coalescing')), 'concurrent').state;
  assert.ok(s.revision > s.processing_revision); assert.equal(s.retained, true);
});
test('confirmed TAU calls resume without a new send; changed source rejects a new call', () => {
  const simulation = createInfrastructureSimulation(content('tau-resume'));
  assert.equal(finish(simulation, 'resume').state.new_send, false);
  const changed = finish(simulation, 'changed').state;
  assert.equal(changed.invocation, 'none'); assert.equal(changed.result, 'source_changed');
});
test('deployment exposes its authored selection before any step and cannot mutate it through a scene', () => {
  const simulation = createInfrastructureSimulation(content('deployment-handoff'));
  for (const [id, selection] of [['pair', 'resources+lineage'], ['worker', 'worker'], ['unsupported', 'worker'], ['web', 'web']]) {
    simulation.selectCase(id);
    const scene = simulation.getScene();
    assert.equal(scene.position, 0); assert.equal(scene.inputs.selection, selection);
    scene.inputs.selection = 'unrelated';
    assert.equal(simulation.getScene().inputs.selection, selection);
    simulation.advance(); assert.equal(simulation.getScene().inputs.selection, selection);
  }
});
test('state restoration replays trusted facts and rejects other batches or mechanisms', () => {
  const simulation = createInfrastructureSimulation(content('rpc-envelope'));
  finish(simulation, 'unknown'); const saved = simulation.getViewState();
  simulation.selectCase('normal'); simulation.restoreViewState({...saved, state: {commit_state: 'success'}});
  assert.equal(simulation.getScene().state.commit_state, 'unknown');
  assert.throws(() => simulation.restoreViewState({...saved, key: 'other'}), /state_batch_mismatch/);
  assert.throws(() => simulation.restoreViewState({...saved, position: 999}), /position_invalid/);
  const original = simulation.getViewState(); assert.deepEqual(original, saved);
});
test('unavailable evidence/endpoints, unknown operators and unsupported adapters fail before rendering', () => {
  for (const mutate of [
    c => c.evidence.splice(0), c => c.objects.splice(0),
    c => c.mechanism.steps[0].state_changes.push({field: 'signature_valid', op: 'eval', value: 'true'}),
    c => c.mechanism.adapter = 'portal.unrelated.anything.v1',
    c => c.mechanism.cases[0].inputs.unexpected = 'x',
  ]) { const c = structuredClone(content('rpc-envelope')); mutate(c); assert.throws(() => createInfrastructureSimulation(c)); }
});
