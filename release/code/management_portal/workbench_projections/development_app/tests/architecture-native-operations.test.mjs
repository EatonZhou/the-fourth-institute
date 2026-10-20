import test from 'node:test';
import assert from 'node:assert/strict';
import {createNativeSimulation, NATIVE_OPERATIONS, NATIVE_OBJECT_ENTRIES, supportsNativeWorkbench, getNativeWorkbenchEntry, createDomainModeSession} from '../src/architecture/native-workbench.model.js';
import {readNativeOperationsFixture} from './architecture-native-operations.fixture.mjs';
const enabled = !!process.env.NATIVE_OPERATIONS_FACTS;
const fixture = enabled ? readNativeOperationsFixture() : null;
const content = id => fixture.content(id);
function run(id, caseId, stop) {
  const sim = createNativeSimulation(content(id)); assert.ok(sim.chooseCase(caseId));
  while (sim.getScene().next) { const step = sim.getScene().next; assert.ok(sim.perform(step.id)); if (step.id === stop) break; }
  return sim;
}
test('native operations reject unknown adapter identities, including missing IDs', () => {
  for (const adapter of ['unknown.v1', 'constructor', '__proto__']) assert.equal(supportsNativeWorkbench({adapter, mode: 'simulated'}), false);
});
test('native operations: every frozen case follows only declared guards, transitions and authorities', {skip: !enabled}, () => {
  for (const {id} of Object.values(NATIVE_OPERATIONS)) {
    const data = content(id), before = structuredClone(data); assert.ok(supportsNativeWorkbench(data.mechanism));
    for (const c of data.mechanism.cases) {
      const sim = run(id, c.id), scene = sim.getScene();
      assert.deepEqual(scene.trace.map(t => t.id), c.path); assert.equal(scene.outcome, c.outcome); assert.equal(scene.next, null);
      assert.equal(sim.perform(c.path[0]), false);
      assert.ok(sim.restoreViewState({...sim.getViewState(), state: {outcome: 'injected'}})); assert.deepEqual(sim.getScene(), scene);
      sim.reset(); assert.equal(sim.getScene().cursor, 0); assert.equal(sim.getScene().state.outcome, 'pending');
    }
    assert.deepEqual(data, before);
    const bad = structuredClone(data); bad.mechanism.id = 'codex.native-turn'; assert.equal(supportsNativeWorkbench(bad.mechanism), false);
    const missing = structuredClone(data); missing.mechanism.state_fields.shift(); assert.throws(() => createNativeSimulation(missing), /native_field_missing/);
    const writer = structuredClone(data); writer.mechanism.steps.find(s => s.state_changes.length).state_changes[0].writer_id = 'absent';
    assert.throws(() => createNativeSimulation(writer), /change_authority/);
  }
});
test('fork: missing source recovery preserves source identity, never starts a turn, and requires a target ID', {skip: !enabled}, () => {
  const ok = run('codex.fork', 'source_missing_with_history').getScene().state;
  assert.equal(ok.source_thread_id, 'demo.parent'); assert.notEqual(ok.result_thread_id, ok.source_thread_id);
  assert.equal(ok.history_injected, true); assert.equal(ok.thread_create_count, 1); assert.equal(ok.turn_start_count, 0); assert.equal(ok.backend_result_returned, true);
  for (const c of ['source_missing_without_history', 'other_provider_error', 'missing_result_id']) {
    const s = run('codex.fork', c).getScene().state; assert.equal(s.backend_result_returned, false); assert.equal(s.turn_start_count, 0); assert.equal(s.thread_create_count, 0);
  }
});
test('compact: committed context still waits for writer drain; cancel and timeout do not interrupt remotely', {skip: !enabled}, () => {
  const mid = run('codex.compact', 'normal', 'compacted_event').getScene().state;
  assert.equal(mid.context_committed, true); assert.equal(mid.technical_turn_active, true); assert.equal(mid.backend_result_returned, false);
  const end = run('codex.compact', 'normal').getScene().state; assert.equal(end.technical_turn_active, false); assert.equal(end.canonical_recorded, true); assert.equal(end.backend_result_returned, true);
  for (const c of ['total_timeout', 'no_progress_timeout', 'cancel_wait', 'record_missing']) {
    const s = run('codex.compact', c).getScene().state; assert.equal(s.interrupt_sent, false); assert.equal(s.backend_result_returned, false); assert.equal(s.local_busy, false); assert.equal(s.total_deadline_reset, false);
  }
  assert.equal(run('codex.compact', 'already_compacting').getScene().state.compact_start_count, 0);
});
test('goal: same objective retains budget; turn completion, handover, pause and terminal notification differ', {skip: !enabled}, () => {
  const mid = run('codex.goal', 'same_active_objective', 'turn').getScene().state;
  assert.equal(mid.goal_status, 'active'); assert.equal(mid.turn_completed, true); assert.equal(mid.goal_set_count, 0); assert.equal(mid.provider_budget, 100);
  const detach = run('codex.goal', 'planned_handover').getScene().state; assert.equal(detach.goal_status, 'active'); assert.equal(detach.interrupt_requested, false); assert.equal(detach.goal_set_count, 0);
  const stop = run('codex.goal', 'user_stop').getScene().state; assert.equal(stop.goal_status, 'paused'); assert.equal(stop.interrupt_requested, true); assert.deepEqual(stop.goal_set_fields, ['threadId', 'status']);
  assert.equal(run('codex.goal', 'pause_request_failure').getScene().state.goal_status, 'active');
  const first = run('codex.goal', 'terminal_notification_first').getScene().state; assert.equal(first.goal_status, 'budgetLimited'); assert.equal(first.terminal_notification_preserved, true);
});
test('dynamic tools: ownership gates, result envelopes and in-flight drain preserve distinct failures', {skip: !enabled}, () => {
  for (const c of ['stale_subscriber', 'detaching_new_request']) { const s = run('codex.dynamic-tools', c).getScene().state; assert.equal(s.reply_count, 0); assert.equal(s.active_calls, 0); }
  assert.equal(run('codex.dynamic-tools', 'missing_dispatcher').getScene().state.rpc_error_code, -32601);
  const failure = run('codex.dynamic-tools', 'harness_failure').getScene().state; assert.equal(failure.tool_success, false); assert.equal(failure.reply_count, 1); assert.equal(failure.rpc_error_code, 0);
  assert.equal(run('codex.dynamic-tools', 'dispatcher_failure').getScene().state.rpc_error_code, -32000);
  assert.equal(run('codex.dynamic-tools', 'governance_failure').getScene().state.tool_success, true);
  const mid = run('codex.dynamic-tools', 'drain_before_detach', 'detach_requested').getScene().state; assert.equal(mid.active_calls, 1); assert.equal(mid.detached, false);
  const end = run('codex.dynamic-tools', 'drain_before_detach').getScene().state; assert.equal(end.reply_count, 1); assert.equal(end.active_calls, 0); assert.equal(end.detached, true);
});
test('native socket: route rejection, handover, continuation and compression remain separate', {skip: !enabled}, () => {
  assert.equal(run('provider.native-socket', 'route_changed').getScene().state.client_call_count, 0);
  const h = run('provider.native-socket', 'handover').getScene().state; assert.equal(h.input_delivery, 'kept_running'); assert.equal(h.result_returned, false); assert.equal(h.final_answer, false); assert.equal(h.usage_observed, true);
  const c = run('provider.native-socket', 'provider_error_continuation').getScene().state; assert.equal(c.input_delivery, 'continued'); assert.equal(c.client_call_count, 2); assert.equal(c.result_returned, true);
  const n = run('provider.native-socket', 'nothing_to_compact').getScene().state; assert.equal(n.nothing_to_compact, true); assert.equal(n.result_returned, true); assert.equal(n.canonical_recorded, false);
  assert.equal(run('provider.native-socket', 'release_active').getScene().state.result_returned, false);
});
test('native operation entry and modes: exact object defaults, authorized modes and atomic restoration', {skip: !enabled}, () => {
  for (const [id, e] of Object.entries(NATIVE_OBJECT_ENTRIES)) {
    const object = fixture.model.objects.find(o => o.id === id); assert.ok(object);
    assert.equal(e.kind === 'composition', object.child_ids.length > 0);
    for (const mode of e.modeMechanismIds) assert.ok(object.mechanism_ids.includes(mode), id + ':' + mode);
    if (e.kind === 'composition') assert.equal(e.defaultMechanismId, null); else assert.equal(e.defaultMechanismId, id.endsWith('recovery-budget') ? 'codex.recovery-budget' : 'codex.native-turn');
  }
  assert.equal(getNativeWorkbenchEntry('provider-native.unknown'), null);
  const c = content('codex.fork'), entry = getNativeWorkbenchEntry('provider-native');
  c.mechanisms.reverse(); const session = createDomainModeSession(c, entry.objectId, entry, createNativeSimulation);
  assert.deepEqual(session.options.map(o => o.id), entry.modeMechanismIds);
  session.simulation.chooseCase('source_missing_with_history'); session.simulation.perform(session.simulation.getScene().next.id);
  const view = {simulation: session.simulation.getViewState(), scrollTop: 120, sourcesOpen: true};
  session.select('codex.goal', view); const goal = {simulation: session.simulation.getViewState(), scrollTop: 0};
  const saved = session.snapshot(goal), restored = createDomainModeSession(c, entry.objectId, entry, createNativeSimulation);
  assert.deepEqual(restored.restore(saved, goal.simulation), goal); assert.deepEqual(restored.select('codex.fork', goal), view);
  const before = restored.simulation.getScene(); assert.equal(restored.restore({...saved, batchKey: 'old-batch'}, goal.simulation), null); assert.deepEqual(restored.simulation.getScene(), before);
  const revoked = {...c, mechanisms: c.mechanisms.filter(m => m.id !== 'codex.goal')};
  assert.equal(createDomainModeSession(revoked, entry.objectId, entry, createNativeSimulation).restore(saved, goal.simulation), null);
});
