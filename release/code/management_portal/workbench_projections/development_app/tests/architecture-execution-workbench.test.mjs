import test from 'node:test';
import assert from 'node:assert/strict';
import {createExecutionSimulation} from '../src/architecture/execution-workbench.model.js';

// Adapter contract fixture, not Portal source evidence. The browser check loads
// the complete owner-frozen mechanism; these small cases isolate interpreter faults.
function fixture() {
  const states = {
    execution_state: ['string', 'new', ['new', 'queued', 'claimed', 'running', 'succeeded', 'cancelled']],
    provider_phase: ['string', 'not_called', ['not_called', 'accepted', 'completed', 'interrupted']],
    content_phase: ['string', 'empty', ['empty', 'captured']],
    binding_updated: ['boolean', false], cancel_requested: ['boolean', false], claim_valid: ['boolean', false],
    claim_generation: ['integer', 0], business_phase: ['string', 'waiting', ['waiting', 'applied']], rejected_old_write: ['boolean', false],
  };
  const shape = [
    ['enqueue', {execution_state: 'queued'}, ['claim', 'cancel-queued']],
    ['claim', {execution_state: 'claimed', claim_valid: true, claim_generation: 1}, ['start']],
    ['start', {execution_state: 'running'}, ['invoke']],
    ['invoke', {provider_phase: 'accepted'}, ['provider-done', 'cancel-active', 'lease-lost']],
    ['provider-done', {provider_phase: 'completed'}, ['capture']],
    ['capture', {content_phase: 'captured'}, ['binding']],
    ['binding', {binding_updated: true}, ['finish']],
    ['finish', {execution_state: 'succeeded', claim_valid: false}, ['apply']],
    ['apply', {business_phase: 'applied'}, []],
    ['cancel-queued', {execution_state: 'cancelled', cancel_requested: true}, []],
    ['cancel-active', {cancel_requested: true}, ['interrupt']],
    ['interrupt', {provider_phase: 'interrupted'}, ['finish-cancel']],
    ['finish-cancel', {execution_state: 'cancelled', claim_valid: false}, []],
    ['lease-lost', {execution_state: 'queued', claim_valid: false}, ['stale-write']],
    ['stale-write', {rejected_old_write: true}, ['reclaim']],
    ['reclaim', {execution_state: 'running', claim_generation: 2, claim_valid: true}, ['provider-done']],
  ];
  const normal = ['enqueue', 'claim', 'start', 'invoke', 'provider-done', 'capture', 'binding', 'finish', 'apply'];
  const paths = {
    normal, cancel_queued: ['enqueue', 'cancel-queued'],
    cancel_running: normal.slice(0, 4).concat(['cancel-active', 'interrupt', 'finish-cancel']),
    recover_accepted: normal.slice(0, 4).concat(['lease-lost', 'stale-write', 'reclaim'], normal.slice(4)),
  };
  return {
    batchKey: 'contract-fixture:one',
    mechanism: {
      id: 'execution.lifecycle', adapter: 'execution.lifecycle.v1', owner_id: 'execution', mode: 'simulated',
      title: 'Synthetic Execution adapter contract', question: 'Contract test only', state_model: 'Local contract fixture',
      participants: [{object_id: 'execution', role: 'fixture owner'}], evidence_ids: ['test-source'],
      input_schema: {type: 'object', properties: {variant: {enum: Object.keys(paths)}}, required: ['variant'], additionalProperties: false},
      state_fields: Object.entries(states).map(([id, [type, initial, values]]) => ({id, type, initial, ...(values ? {enum: values} : {})})),
      steps: shape.map(([id, delta, next]) => ({id, title: id, from: 'execution', to: 'runtime', trigger: 'fixture', guard: 'Fixture premise',
        state_changes: Object.entries(delta).map(([field, value]) => ({field, op: 'set', value})), next, evidence_ids: ['test-source']})),
      cases: Object.entries(paths).map(([id, path]) => ({id, title: id, inputs: {variant: id}, path, outcome: `${id} terminal`, evidence_ids: ['test-source']})),
    },
    objects: [{id: 'execution', title: 'Fixture execution'}, {id: 'runtime', title: 'Fixture runtime'}],
    evidence: [{id: 'test-source', address: 'fixture:contract', version: 1, sha256: 'a'.repeat(64), start_line: 1, end_line: 1,
      excerpt: 'Synthetic test only', frozen: {address: 'fixture:frozen', sha256: 'a'.repeat(64), size: 19}}],
  };
}
function until(simulation, stepId) {
  while (simulation.getScene().step_id !== stepId) simulation.advance();
  return simulation.getScene();
}

test('Provider completion, content, binding, Scheduler finish and business acknowledgement remain distinct', () => {
  const simulation = createExecutionSimulation(fixture());
  const provider = until(simulation, 'provider-done');
  assert.equal(provider.state.provider_phase, 'completed');
  assert.equal(provider.state.content_phase, 'empty');
  assert.equal(provider.state.execution_state, 'running');
  assert.equal(provider.state.business_phase, 'waiting');
  const content = simulation.advance('capture');
  assert.equal(content.state.binding_updated, false);
  const binding = simulation.advance('binding');
  assert.equal(binding.state.execution_state, 'running');
  const finish = simulation.advance('finish');
  assert.equal(finish.state.execution_state, 'succeeded');
  assert.equal(finish.state.business_phase, 'waiting');
  assert.equal(finish.state.claim_valid, false);
  assert.equal(simulation.advance('apply').state.business_phase, 'applied');
  assert.throws(() => simulation.advance(), /case_complete/);
});

test('cancellation paths preserve the distinction between request, interrupt and authoritative terminal', () => {
  const simulation = createExecutionSimulation(fixture());
  simulation.selectCase('cancel_queued');
  assert.equal(simulation.canEstablishRunning(), false);
  assert.throws(() => simulation.establishRunning(), /case_has_no_start/);
  const queued = until(simulation, 'cancel-queued');
  assert.equal(queued.explanation.provider_submissions, 0);
  assert.equal(queued.state.execution_state, 'cancelled');
  simulation.selectCase('cancel_running');
  const requested = until(simulation, 'cancel-active');
  assert.equal(requested.state.execution_state, 'running');
  assert.equal(requested.state.provider_phase, 'accepted');
  const interrupted = simulation.advance('interrupt');
  assert.equal(interrupted.state.execution_state, 'running');
  assert.equal(simulation.advance('finish-cancel').state.execution_state, 'cancelled');
});

test('recovery fences old writes, changes claim generation and never submits accepted input twice', () => {
  const simulation = createExecutionSimulation(fixture());
  simulation.selectCase('recover_accepted');
  assert.equal(until(simulation, 'lease-lost').state.claim_valid, false);
  assert.equal(simulation.advance('stale-write').state.rejected_old_write, true);
  const resumed = simulation.advance('reclaim');
  assert.equal(resumed.state.claim_generation, 2);
  assert.equal(resumed.state.claim_valid, true);
  assert.equal(resumed.state.provider_phase, 'accepted');
  const final = until(simulation, 'apply');
  assert.equal(final.explanation.provider_submissions, 1);
  assert.deepEqual(final.prefix.filter(id => id === 'invoke'), ['invoke']);
});

test('back replays prefixes; case changes and establishing start erase later outcomes', () => {
  const simulation = createExecutionSimulation(fixture());
  until(simulation, 'apply');
  const previous = simulation.back();
  assert.equal(previous.state.business_phase, 'waiting');
  assert.equal(previous.state.execution_state, 'succeeded');
  const start = simulation.establishRunning();
  assert.deepEqual(start.prefix, ['enqueue', 'claim', 'start']);
  assert.equal(start.state.provider_phase, 'not_called');
  assert.equal(start.explanation.provider_submissions, 0);
  simulation.selectCase('recover_accepted');
  assert.equal(simulation.getScene().state.claim_generation, 0);
  assert.throws(() => simulation.advance('reclaim'), /step_out_of_order/);
});

test('restore replays the exact case prefix and rejects another batch or tampered path without carrying old state', () => {
  const content = fixture(), simulation = createExecutionSimulation(content);
  simulation.selectCase('recover_accepted'); until(simulation, 'reclaim');
  const saved = simulation.getViewState(), restored = createExecutionSimulation(content);
  restored.restore({...saved, state: {execution_state: 'succeeded'}});
  assert.deepEqual(restored.getScene(), simulation.getScene());
  assert.throws(() => restored.restore({...saved, batchKey: 'contract-fixture:two'}), /restore_batch_mismatch/);
  assert.equal(restored.getScene().step_count, 0);
  assert.throws(() => restored.restore({...saved, prefix: ['enqueue', 'finish']}), /restore_prefix/);
  assert.equal(restored.getScene().state.execution_state, 'new');
  const external = simulation.getScene(); external.state.execution_state = 'cancelled';
  assert.equal(simulation.getScene().state.execution_state, 'running');
});

test('reject unavailable source/endpoint, unsupported adapter, malformed transitions, input and state changes', () => {
  const mutations = [
    d => { d.evidence = []; }, d => { d.objects.pop(); },
    d => { d.mechanism.adapter = 'other.v1'; }, d => { d.mechanism.cases[0].path.splice(1, 1); },
    d => { d.mechanism.cases[0].inputs.extra = 'hidden'; },
    d => { d.mechanism.steps[0].state_changes[0].value = 'unknown-state'; },
    d => { d.mechanism.steps[0].state_changes[0].op = 'eval'; },
    d => { d.mechanism.steps[0].state_changes[0].field = 'unknown'; },
  ];
  for (const mutate of mutations) { const d = fixture(); mutate(d); assert.throws(() => createExecutionSimulation(d), /execution_workbench:/); }
});

test('declarative guards are enforced; text is a premise, never executable code', () => {
  const content = fixture();
  content.mechanism.steps.find(step => step.id === 'start').when = {all: [{field: 'claim_valid', equals: true}, {field: 'claim_generation', gte: 1}]};
  const simulation = createExecutionSimulation(content);
  assert.equal(simulation.establishRunning().state.execution_state, 'running');
  content.mechanism.steps.find(step => step.id === 'start').when = {field: 'claim_valid', equals: false};
  assert.throws(() => createExecutionSimulation(content), /guard_blocked:start/);
  content.mechanism.steps.find(step => step.id === 'start').when = {field: 'claim_valid', eval: 'arbitrary'};
  assert.throws(() => createExecutionSimulation(content), /guard_operator/);
  content.mechanism.steps.find(step => step.id === 'start').when = false;
  assert.throws(() => createExecutionSimulation(content), /guard_invalid/);
});

// A small synthetic delta isolates writer/authority validation and result reuse;
// browser evidence separately exercises the full immutable owner case.
function recoveryFixture() {
  const content = fixture(), m = content.mechanism;
  for (const field of m.state_fields) {field.authority_id = 'execution'; field.state_scope = 'observation';}
  const add = (id, type, initial, values) => m.state_fields.push({id, type, initial,
    ...(values ? {enum: values} : {}), authority_id: 'execution', state_scope: 'observation'});
  add('checkpoint_phase', 'string', 'none', ['none', 'provider_completed']);
  add('provider_call_count', 'integer', 0); add('result_recovered', 'boolean', false);
  add('interrupt_requested', 'boolean', false);
  add('binding_observation', 'string', 'unknown', ['unknown', 'updated']);
  add('content_observation', 'string', 'unknown', ['unknown', 'captured']);
  m.state_fields.find(f => f.id === 'provider_phase').enum.push('unknown');
  m.state_fields.find(f => f.id === 'content_phase').enum.push('unknown');
  const step = id => m.steps.find(item => item.id === id);
  const change = (id, field, value, op = 'set') => step(id).state_changes.push({field, value, op, writer_id: 'runtime'});
  for (const item of m.steps) for (const delta of item.state_changes) delta.writer_id = 'runtime';
  change('invoke', 'provider_call_count', 1, 'increment');
  change('provider-done', 'checkpoint_phase', 'provider_completed');
  change('capture', 'content_observation', 'captured'); change('binding', 'binding_observation', 'updated');
  step('interrupt').state_changes[0].value = 'unknown'; change('interrupt', 'interrupt_requested', true);
  change('finish-cancel', 'content_phase', 'unknown');
  step('provider-done').next.push('completed-fault');
  for (const [id, delta, next] of [
    ['completed-fault', {execution_state: 'queued', claim_valid: false}, ['completed-claim']],
    ['completed-claim', {execution_state: 'running', claim_valid: true, claim_generation: 2}, ['recover-result']],
    ['recover-result', {result_recovered: true}, ['capture']],
  ]) m.steps.push({id, title: id, from: 'runtime', to: 'runtime', guard: 'Synthetic case premise',
    state_changes: Object.entries(delta).map(([field, value]) => ({field, value, op: 'set', writer_id: 'runtime'})),
    next, evidence_ids: ['test-source']});
  m.input_schema.properties.variant.enum.push('recover_completed');
  const normal = m.cases[0].path;
  m.cases.push({id: 'recover_completed', title: 'Synthetic completed recovery', inputs: {variant: 'recover_completed'},
    path: normal.slice(0, 5).concat(['completed-fault', 'completed-claim', 'recover-result'], normal.slice(5)),
    outcome: 'reused original result', evidence_ids: ['test-source']});
  return content;
}

test('completed checkpoint recovery skips a second provider call and back recomputes recovered state', () => {
  const simulation = createExecutionSimulation(recoveryFixture());
  simulation.selectCase('recover_completed');
  const fault = until(simulation, 'completed-fault');
  assert.equal(fault.state.checkpoint_phase, 'provider_completed');
  assert.equal(fault.state.content_phase, 'empty');
  assert.equal(fault.state.provider_call_count, 1);
  const restored = until(simulation, 'recover-result');
  assert.equal(restored.state.result_recovered, true);
  assert.equal(restored.explanation.provider_submissions, 1);
  assert.deepEqual(restored.changes[0], {field: 'result_recovered', before: false, after: true,
    writer_id: 'runtime', authority_id: 'execution', state_scope: 'observation'});
  assert.equal(simulation.back().state.result_recovered, false);
  assert.equal(until(simulation, 'apply').state.provider_call_count, 1);
  simulation.selectCase('normal');
  assert.equal(simulation.getScene().state.result_recovered, false);
  assert.equal(simulation.getScene().state.provider_call_count, 0);
});

test('cancelled observation remains unknown independently from authoritative cancellation', () => {
  const simulation = createExecutionSimulation(recoveryFixture());
  simulation.selectCase('cancel_running');
  const interrupt = until(simulation, 'interrupt');
  assert.equal(interrupt.state.interrupt_requested, true);
  assert.equal(interrupt.state.provider_phase, 'unknown');
  assert.equal(interrupt.state.execution_state, 'running');
  const terminal = simulation.advance('finish-cancel');
  assert.equal(terminal.state.execution_state, 'cancelled');
  assert.equal(terminal.state.content_phase, 'unknown');
  assert.equal(terminal.state.binding_observation, 'unknown');
});

test('authority and writer references must be authorized, but need not be identical', () => {
  assert.doesNotThrow(() => createExecutionSimulation(recoveryFixture()));
  const badAuthority = recoveryFixture(); badAuthority.mechanism.state_fields[0].authority_id = 'hidden';
  assert.throws(() => createExecutionSimulation(badAuthority), /authority_unavailable/);
  const badWriter = recoveryFixture(); badWriter.mechanism.steps[0].state_changes[0].writer_id = 'hidden';
  assert.throws(() => createExecutionSimulation(badWriter), /writer_unavailable/);
  delete badWriter.mechanism.steps[0].state_changes[0].writer_id;
  assert.throws(() => createExecutionSimulation(badWriter), /writer_unavailable/);
});
