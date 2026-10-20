import test from 'node:test';
import assert from 'node:assert/strict';
import {createSchedulingSimulation, matchesSchedulingCondition, SCHEDULING_ADAPTER, SCHEDULING_MECHANISMS} from '../src/architecture/scheduling-mechanisms.model.js';

function content() {
  return {batchKey: 'fixed-pair-A', objects: [{id: 'owner'}, {id: 'worker'}], evidence: [{id: 'source'}], mechanism: {
    id: 'control.worker-protocol', adapter: SCHEDULING_ADAPTER, mode: 'simulated', owner_id: 'owner', participants: [{object_id: 'worker'}],
    state_model: {kind: 'declarative_transition_system', condition_scope: 'state'},
    input_schema: {type: 'object', properties: {changed: {type: 'boolean'}}, required: ['changed'], additionalProperties: false},
    state_fields: [{id: 'changed', type: 'boolean', initial: false}, {id: 'generation', type: 'integer', initial: 0, authority_id: 'owner'}, {id: 'rejected', type: 'boolean', initial: false}],
    steps: [
      {id: 'load', from: 'owner', to: 'owner', state_changes: [{field: 'changed', op: 'set', value: {input: 'changed'}}], next: ['claim'], evidence_ids: ['source']},
      {id: 'claim', from: 'worker', to: 'owner', state_changes: [{field: 'generation', op: 'increment', value: 1}], next: ['accept', 'reject'], evidence_ids: ['source']},
      {id: 'accept', from: 'worker', to: 'owner', when: {field: 'changed', equals: false}, state_changes: [], next: [], evidence_ids: ['source']},
      {id: 'reject', from: 'worker', to: 'owner', when: {field: 'changed', equals: true}, state_changes: [{field: 'rejected', op: 'set', value: true}], next: [], evidence_ids: ['source']},
    ],
    cases: [
      {id: 'current', inputs: {changed: false}, path: ['load', 'claim', 'accept'], evidence_ids: ['source']},
      {id: 'stale', inputs: {changed: true}, path: ['load', 'claim', 'reject'], evidence_ids: ['source']},
    ],
  }};
}

test('registry owns exactly eight named control mechanisms, not the shared adapter wholesale', () => {
  assert.deepEqual(SCHEDULING_MECHANISMS, ['control.admission', 'control.fairness', 'control.quota', 'control.worker-protocol', 'control.expiry', 'control.worker-slots', 'control.generation', 'control.handover']);
  const x = content(); x.mechanism.id = 'control.cancel'; assert.throws(() => createSchedulingSimulation(x), /unsupported mechanism/);
});
test('a stale qualification cannot reach the accepted branch', () => {
  const c = content(), sim = createSchedulingSimulation(c); sim.selectCase('stale');
  while (sim.getScene().next) sim.advance();
  assert.equal(sim.getScene().state.rejected, true); assert.equal(sim.getScene().state.generation, 1);
  c.mechanism.cases[1].path[2] = 'accept'; assert.throws(() => createSchedulingSimulation(c), /illegal case path/);
});
test('return reconstructs same-batch state and ignores injected state values', () => {
  const sim = createSchedulingSimulation(content()); sim.advance(); sim.advance(); const saved = sim.getViewState();
  sim.selectCase('stale'); sim.restore({...saved, state: {generation: 999, rejected: true}});
  assert.deepEqual(sim.getScene().state, {changed: false, generation: 1, rejected: false});
  for (const patch of [{batchKey: 'new-pair'}, {mechanismId: 'control.expiry'}, {count: 8}, {count: -1}, {count: .5}, {caseId: 'invented'}]) assert.throws(() => sim.restore({...saved, ...patch}), /incompatible saved view/);
});
test('back and case reset replay inputs rather than retaining previous outcome', () => {
  const sim = createSchedulingSimulation(content()); sim.selectCase('stale'); sim.advance(); sim.advance(); sim.advance();
  sim.back(); assert.equal(sim.getScene().state.rejected, false);
  sim.selectCase('current'); assert.equal(sim.getScene().step_count, 0); assert.equal(sim.getScene().state.generation, 0);
});
test('missing evidence or an unauthorized endpoint is rejected before any content is rendered', () => {
  for (const mutate of [c => { c.evidence = []; }, c => { c.objects.pop(); }, c => { c.mechanism.state_fields[1].authority_id = 'hidden'; }]) {
    const c = content(); mutate(c); assert.throws(() => createSchedulingSimulation(c), /missing/);
  }
});
test('input types, next edges, terminal paths and state operations are checked', () => {
  const mutations = [c => { c.mechanism.cases[0].inputs.changed = 'false'; }, c => { c.mechanism.steps[1].next = ['reject']; }, c => { c.mechanism.cases[0].path.pop(); }, c => { c.mechanism.steps[1].state_changes[0].op = 'eval'; }, c => { c.mechanism.steps[1].state_changes[0].value = '1'; }];
  for (const mutate of mutations) { const c = content(); mutate(c); assert.throws(() => createSchedulingSimulation(c)); }
});
test('nested all/any conditions are strict; unknown grammar never executes', () => {
  assert.equal(matchesSchedulingCondition({all: [{field: 'a', equals: true}, {any: [{field: 'b', equals: 1}, {field: 'b', equals: 2}]}]}, {a: true, b: 2}), true);
  assert.throws(() => matchesSchedulingCondition({any: [{field: 'a', equals: true}, {script: 'true'}]}, {a: true}), /unsupported condition/);
  assert.throws(() => matchesSchedulingCondition({field: 'hidden', equals: false}, {}), /unsupported condition/);
});
test('callers cannot mutate the pinned content or returned scene', () => {
  const c = content(), sim = createSchedulingSimulation(c); c.mechanism.steps[0].next = [];
  sim.getContent().mechanism.steps[0].next = []; sim.getScene().state.generation = 99;
  sim.advance(); assert.equal(sim.getScene().next.id, 'claim'); assert.equal(sim.getScene().state.generation, 0);
});

test('an authored intermediate stop requires a complete matching expected state', () => {
  const c = content(); c.mechanism.cases[0].path = ['load', 'claim'];
  assert.throws(() => createSchedulingSimulation(c), /incomplete case path/);
  c.mechanism.cases[0].expected_state = {changed: false, generation: 1, rejected: false};
  const sim = createSchedulingSimulation(c); sim.advance(); sim.advance();
  assert.equal(sim.getScene().complete, true); assert.equal(sim.getScene().next, null);
  assert.equal(sim.getScene().state.generation, 1);
  sim.back(); assert.equal(sim.getScene().complete, false);
  sim.restore({schema: 'scheduling-view.v1', batchKey: c.batchKey, mechanismId: c.mechanism.id, caseId: 'current', count: 2});
  assert.equal(sim.getScene().complete, true);
  for (const expected of [{changed: false, generation: 1}, {changed: false, generation: 2, rejected: false}, {changed: false, generation: 1, rejected: false, extra: true}, null]) {
    const bad = structuredClone(c); bad.mechanism.cases[0].expected_state = expected;
    assert.throws(() => createSchedulingSimulation(bad), /invalid expected state/);
  }
});

test('terminal cases with declared expected state must also match the replay', () => {
  const c = content(); c.mechanism.cases[0].expected_state = {changed: false, generation: 1, rejected: true};
  assert.throws(() => createSchedulingSimulation(c), /invalid expected state/);
});
