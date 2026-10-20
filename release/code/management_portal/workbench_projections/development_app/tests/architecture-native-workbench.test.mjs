import test from 'node:test';
import assert from 'node:assert/strict';
import {writeFileSync, readFileSync} from 'node:fs';
import {createHash} from 'node:crypto';
import {createNativeSimulation, supportsNativeWorkbench, NATIVE_ADAPTER, BUDGET_ADAPTER, NATIVE_OBJECT_ENTRIES, getNativeWorkbenchEntry, createDomainModeSession} from '../src/architecture/native-workbench.model.js';

// Synthetic consumer contract, not a production fact or the old 32f3 scene.
function fixture() {
  const evidence = [{id: 'test-source', address: 'test://native-contract', sha256: '0'.repeat(64), start_line: 1, end_line: 4,
    excerpt: 'Synthetic validation data; no production authority.', claim: '合同验证用来源', frozen: {address: 'test://native-frozen', sha256: '0'.repeat(64), size: 48}}];
  const objects = ['adapter', 'provider', 'provider-native', 'worker.runtime'].map(id => ({id, title: ({adapter: 'Codex Adapter', provider: 'Codex App Server', 'provider-native': 'Provider 原生协议', 'worker.runtime': 'Runtime'})[id]}));
  const initials = {account_id: 'account-test', thread_id: '', thread_loaded: false, connection: 'disconnected', accepted_turn_id: '',
    checkpoint_turn_id: '', provider_turn_status: 'none', observed_turn_status: 'unknown', observed_turn_id: '', turn_start_count: 0, control_target_turn_id: '', control_result: 'none',
    thread_resume_count: 0, native_control_count: 0, control_error: '', control_rejection_reason: '', attached: false, interrupt_requested: false,
    input_identity: 'input-test', lookup_result: 'none', last_other_turn_id: '', failure: ''};
  const authority = id => ['thread_id','provider_turn_status'].includes(id) ? 'provider' : id === 'checkpoint_turn_id' ? 'worker.runtime' : 'adapter';
  const state_fields = Object.entries(initials).map(([id, initial]) => ({id, label: id, type: initial === null ? ['string', 'null'] : typeof initial === 'number' ? 'integer' : typeof initial, initial, authority_id: authority(id), state_scope: ['thread_id','provider_turn_status','checkpoint_turn_id'].includes(id) ? 'authoritative' : 'observation'}));
  const set = (field, value) => ({field, op: 'set', value});
  const steps = [
    {id: 'connect', title: '连接原账号', from: 'adapter', to: 'provider', state_changes: [set('connection', 'ready')]},
    {id: 'load', title: '加载原 thread', from: 'adapter', to: 'provider', state_changes: [set('thread_id', 'thread-test'), set('thread_loaded', true)], when: {field: 'connection', equals: 'ready'}},
    {id: 'start', title: '提交并取得 turn ID', from: 'adapter', to: 'provider', state_changes: [{field: 'turn_start_count', op: 'increment', value: 1}, set('accepted_turn_id', 'turn-test'), set('checkpoint_turn_id', 'turn-test'), set('provider_turn_status', 'in_progress'), set('observed_turn_status', 'in_progress')], when: {field: 'thread_loaded', equals: true}},
    {id: 'fail-before', title: '接受前请求失败', from: 'provider', to: 'adapter', state_changes: [{field: 'turn_start_count', op: 'increment', value: 1}, set('connection', 'disconnected')]},
    {id: 'disconnect', title: '切断连接', from: 'provider', to: 'adapter', state_changes: [set('connection', 'disconnected')]},
    {id: 'native-complete', title: '原轮次在服务端完成', from: 'provider', to: 'provider', state_changes: [set('provider_turn_status', 'completed')]},
    {id: 'reconnect', title: '重连原账号', from: 'adapter', to: 'provider', state_changes: [set('connection', 'ready')]},
    {id: 'observe', title: '读取原 turn 的结果', from: 'adapter', to: 'provider', state_changes: [set('observed_turn_status', 'completed')], when: {all: [{field: 'connection', equals: 'ready'}, {field: 'checkpoint_turn_id', equals: 'turn-test'}]}},
    {id: 'stale', title: '以旧 turn 发控制', from: 'adapter', to: 'provider', state_changes: [set('control_target_turn_id', 'turn-old'), set('control_result', 'rejected')]},
  ].map(step => ({...step, state_changes: step.state_changes.map(change => ({...change, writer_id: authority(change.field)})), trigger: step.title, guard: '验证情境声明的前提', observable: ({disconnect: '连接已断；恢复锚点和原轮次保持。', 'native-complete': '原生轮次已完成；断线客户端还不知道。', observe: '精确读取原轮次，未再次提交输入。', stale: '控制目标与当前 turn 不符，拒绝旧控制。', 'fail-before': '未取得接受 ID，没有可恢复 turn。'})[step.id] || step.title, evidence_ids: ['test-source'], next: []}));
  const paths = [
    ['normal', '正常原生返回', ['connect', 'load', 'start', 'native-complete', 'observe']],
    ['preaccept', '接受前失败', ['connect', 'load', 'fail-before']],
    ['recover', '已接受后断线与恢复', ['connect', 'load', 'start', 'disconnect', 'native-complete', 'reconnect', 'observe']],
    ['stale', '过期控制隔离', ['connect', 'load', 'start', 'stale', 'native-complete', 'observe']],
  ];
  for (const [, , path] of paths) path.forEach((id, i) => {const next = path[i + 1], step = steps.find(s => s.id === id); if (next && !step.next.includes(next)) step.next.push(next);});
  return {batchKey: '1'.repeat(64), objects, evidence, mechanism: {
    id: 'test.native-turn', adapter: NATIVE_ADAPTER, owner_id: 'provider-native', mode: 'simulated', question: '相同账号、thread 和接受 ID，怎样在连接中断后找回原轮次？',
    participants: [{object_id: 'adapter', role: 'adapter'}, {object_id: 'provider', role: 'provider'}, {object_id:'worker.runtime', role:'checkpoint'}],
    state_fields, steps, input_schema: {type: 'object', additionalProperties: false}, evidence_ids: ['test-source'],
    invariants: ['输入被接受后不重新提交。', '原生完成不替代 Runtime 和 Scheduler 的收尾。'], entry_conditions: ['独立本地试验；所有账号和 ID 均为合同测试值。'],
    cases: paths.map(([id, title, path]) => ({id, title, path, inputs: {}, outcome: `${title}情境结束`, evidence_ids: ['test-source']})),
  }};
}
if (process.env.NATIVE_CONTRACT_FIXTURE) writeFileSync(process.env.NATIVE_CONTRACT_FIXTURE, JSON.stringify(fixture(), null, 2));
function until(sim, id) {for (let i = 0; i < 30 && sim.getScene().current?.id !== id; i++) {const next = sim.getScene().next; assert.ok(next); assert.ok(sim.perform(next.id));} assert.equal(sim.getScene().current?.id, id);}

test('accepted identity survives disconnect; reconnect follows same turn without resubmission', () => {
  const sim = createNativeSimulation(fixture()); sim.chooseCase('recover'); until(sim, 'disconnect');
  let s = sim.getScene().state; assert.equal(s.connection, 'disconnected'); assert.equal(s.accepted_turn_id, s.checkpoint_turn_id); assert.equal(s.turn_start_count, 1);
  until(sim, 'native-complete'); s = sim.getScene().state; assert.equal(s.provider_turn_status, 'completed'); assert.equal(s.observed_turn_status, 'in_progress');
  until(sim, 'observe'); s = sim.getScene().state; assert.equal(s.account_id, 'account-test'); assert.equal(s.thread_id, 'thread-test'); assert.equal(s.observed_turn_status, 'completed'); assert.equal(s.turn_start_count, 1);
  assert.equal('execution_state' in s, false);
});
test('acceptance failure provides no invented recovery anchor', () => {
  const sim = createNativeSimulation(fixture()); sim.chooseCase('preaccept'); until(sim, 'fail-before'); const scene = sim.getScene();
  assert.equal(scene.state.accepted_turn_id, ''); assert.equal(scene.state.checkpoint_turn_id, ''); assert.equal(scene.state.provider_turn_status, 'none'); assert.equal(scene.next, null); assert.equal(sim.perform('reconnect'), false);
});
test('stale control cannot alter current turn; wrong actions cannot bypass case path', () => {
  const sim = createNativeSimulation(fixture()); sim.chooseCase('stale'); assert.equal(sim.perform('start'), false); until(sim, 'start'); const before = sim.getScene().state;
  until(sim, 'stale'); const after = sim.getScene().state;
  assert.equal(after.control_result, 'rejected'); assert.notEqual(after.control_target_turn_id, after.accepted_turn_id); assert.equal(after.provider_turn_status, before.provider_turn_status); assert.equal(after.accepted_turn_id, before.accepted_turn_id);
});
test('back and state restoration replay declared prefix, not caller-supplied outcomes', () => {
  const sim = createNativeSimulation(fixture()); sim.chooseCase('recover'); until(sim, 'observe'); const saved = sim.getViewState(); saved.state = {turn_start_count: 999};
  sim.back(); assert.equal(sim.getScene().state.observed_turn_status, 'in_progress'); assert.ok(sim.restoreViewState(saved)); assert.equal(sim.getScene().state.turn_start_count, 1);
  const copy = sim.getScene(); copy.state.turn_start_count = 999; assert.equal(sim.getScene().state.turn_start_count, 1);
  assert.equal(sim.restoreViewState({...saved, batchKey: 'other-batch'}), false); assert.equal(sim.restoreViewState({...saved, cursor: 999}), false);
});
test('other adapters and missing authorized evidence fail before rendering', () => {
  const data = fixture(); data.mechanism.adapter = 'execution.lifecycle.v1'; assert.equal(supportsNativeWorkbench(data.mechanism), false); assert.throws(() => createNativeSimulation(data), /unsupported_adapter/);
  const denied = fixture(); denied.evidence = []; assert.throws(() => createNativeSimulation(denied), /evidence_unavailable/);
  const endpoints = fixture(); endpoints.objects = endpoints.objects.filter(o => o.id !== 'provider'); assert.throws(() => createNativeSimulation(endpoints), /participant_unavailable/);
  const fields = fixture(); fields.mechanism.state_fields = fields.mechanism.state_fields.filter(f => f.id !== 'accepted_turn_id'); assert.throws(() => createNativeSimulation(fields), /native_field_missing/);
});
test('malformed paths, guards, and state operations are rejected atomically', () => {
  const badPath = fixture(); badPath.mechanism.cases[0].path = ['connect', 'observe']; assert.throws(() => createNativeSimulation(badPath), /case_transition/);
  const badGuard = fixture(); badGuard.mechanism.steps[0].when = {field: 'missing', equals: true}; assert.throws(() => createNativeSimulation(badGuard), /guard_field/);
  const badChange = fixture(); badChange.mechanism.steps[0].state_changes[0].op = 'execute'; assert.throws(() => createNativeSimulation(badChange), /change_operator/);
  const wrongType = fixture(); wrongType.mechanism.steps[0].state_changes[0].value = false; assert.throws(() => createNativeSimulation(wrongType), /value_type/);
});
test('new case resets local turn; fixed content stays immutable', () => {
  const data = fixture(), original = structuredClone(data), sim = createNativeSimulation(data); until(sim, 'observe'); sim.chooseCase('preaccept');
  assert.equal(sim.getScene().state.accepted_turn_id, ''); assert.equal(sim.getScene().state.turn_start_count, 0); assert.deepEqual(data, original);
});

test('writers cannot impersonate a different state authority', () => {
  const data=fixture(); data.mechanism.steps[0].state_changes[0].writer_id='provider';
  assert.throws(()=>createNativeSimulation(data), /change_authority/);
  const denied=fixture(); denied.mechanism.state_fields[0].authority_id='unreadable';
  assert.throws(()=>createNativeSimulation(denied), /state_authority_unavailable/);
});

test('frozen native mechanism: all declared paths, observation and control semantics', {skip: !process.env.NATIVE_FACTS}, () => {
  const capture=JSON.parse(readFileSync(process.env.NATIVE_FACTS,'utf8')), facts=capture.facts;
  const hash=text=>createHash('sha256').update(text).digest('hex');
  const delivery=JSON.parse(facts[capture.entry.address].text);
  assert.equal(hash(facts[capture.entry.address].text),capture.entry.sha256);
  const descriptor=delivery.exploration.manifest, manifest=JSON.parse(facts[descriptor.address].text);
  assert.equal(hash(facts[descriptor.address].text),descriptor.sha256);
  const modelKey=Object.keys(facts).find(key=>key.includes('/model-116d3a72'));
  assert.ok(modelKey);
  const text=facts[modelKey].text, model=JSON.parse(text);
  assert.equal(hash(text),'116d3a72b0e185fd54e6465452a2b0c6606a708a2ffb4947004fe843d8fc7eb0');
  const mechanism=model.mechanisms.find(m=>m.id==='codex.native-turn');
  const content={mechanism,objects:model.objects,evidence:model.evidence,batchKey:descriptor.sha256};
  const sim=createNativeSimulation(content);
  for(const scenario of mechanism.cases) {sim.chooseCase(scenario.id);until(sim,scenario.path.at(-1));assert.equal(sim.getScene().next,null);}
  sim.chooseCase('recover_accepted');until(sim,'disconnect');
  assert.equal(sim.getScene().state.observed_turn_status,'unknown');
  until(sim,'provider-complete');assert.equal(sim.getScene().state.provider_turn_status,'completed');
  assert.equal(sim.getScene().state.observed_turn_status,'unknown');
  until(sim,'observe-complete');assert.equal(sim.getScene().state.turn_start_count,1);
  sim.chooseCase('stale_control');until(sim,'reject-control');
  assert.equal(sim.getScene().state.native_control_count,0);assert.equal(sim.getScene().state.control_error,'ValueError');
  sim.chooseCase('interrupt');until(sim,'interrupt-ack');assert.equal(sim.getScene().state.provider_turn_status,'in_progress');
  until(sim,'interrupted');assert.equal(sim.getScene().state.observed_turn_status,'interrupted');
  sim.chooseCase('interrupt_uncertain');until(sim,'control-uncertain');assert.equal(sim.getScene().state.observed_turn_status,'unknown');
  sim.chooseCase('foreign_input');until(sim,'foreign-input');assert.equal(sim.getScene().state.turn_start_count,0);
  assert.equal(sim.getScene().state.accepted_turn_id,'');
  if(process.env.NATIVE_FROZEN_CONTENT)writeFileSync(process.env.NATIVE_FROZEN_CONTENT,JSON.stringify(content));
});

function incrementContent(id) {
  const capture = JSON.parse(readFileSync(process.env.NATIVE_INCREMENT_FACTS, 'utf8'));
  const batchKey = 'ec379604ae9355d9426fd22456675a1118d5f58e2fe8a3a72b08a16f0bb68c73';
  const modelSha = '9105515ec8785625eec38571299947d6a877504af9ac3f17721d12a48a0103b8';
  const read = (prefix, hash) => {
    const key = Object.keys(capture.facts).find(key => key.endsWith(`/${prefix}-${hash}.json`));
    assert.ok(key); const text = capture.facts[key].text;
    assert.equal(createHash('sha256').update(text).digest('hex'), hash); return JSON.parse(text);
  };
  read('manifest', batchKey);
  const model = read('model', modelSha), mechanism = model.mechanisms.find(m => m.id === id);
  assert.ok(mechanism); return {mechanism, mechanisms: model.mechanisms, objects: model.objects, evidence: model.evidence, batchKey};
}
test('Entry: historical native batch preserves composition and exposes only its declared modes', {skip: !process.env.NATIVE_INCREMENT_FACTS}, () => {
  const c = incrementContent('codex.native-turn');
  for (const id of Object.keys(NATIVE_OBJECT_ENTRIES)) {
    const entry = getNativeWorkbenchEntry(id), object = c.objects.find(item => item.id === id);
    assert.ok(object); assert.equal(entry.kind === 'composition', object.child_ids.length > 0);
    const primary = c.mechanisms.find(m => m.id === entry.primaryMechanismId);
    if (primary && object.mechanism_ids.includes(primary.id)) {
      const modes = createDomainModeSession({...c, mechanism: primary}, id, entry, createNativeSimulation);
      for (const mode of modes.options) assert.ok(object.mechanism_ids.includes(mode.id));
    }
    if (object.child_ids.length) { assert.equal(entry.defaultMechanismId, null); assert.equal(entry.factory, null); }
    else assert.equal(entry.factory, 'createNativeWorkbench');
  }
  const protocol = createDomainModeSession(c, 'provider-native', getNativeWorkbenchEntry('provider-native'), createNativeSimulation);
  assert.deepEqual(protocol.options.map(mode => mode.id), ['codex.native-turn']);
  assert.equal(getNativeWorkbenchEntry('execution.providers.codex.unknown'), null);
  assert.equal(getNativeWorkbenchEntry('execution.providers.codex.recovery-budget').defaultMechanismId, 'codex.recovery-budget');
});
test('Modes: native turn and independent budget retain separate state across returning to same explicit view', {skip: !process.env.NATIVE_INCREMENT_FACTS}, () => {
  const c = incrementContent('codex.native-turn'), entry = getNativeWorkbenchEntry('execution.providers.codex');
  const session = createDomainModeSession(c, entry.objectId, entry, createNativeSimulation);
  assert.deepEqual(session.options.map(item => item.id), ['codex.native-turn', 'codex.recovery-budget']);
  session.simulation.chooseCase('connect_failure_before_accept'); const first = session.simulation.getScene().next;
  session.simulation.perform(first.id); const turn = {simulation: session.simulation.getViewState(), scrollTop: 100, focusKey: 'mode:'};
  session.select('codex.recovery-budget', turn); session.simulation.chooseCase('exhausted');
  session.simulation.perform(session.simulation.getScene().next.id); const budget = {simulation: session.simulation.getViewState(), scrollTop: 200, focusKey: 'mode:'};
  const saved = session.snapshot(budget), restored = createDomainModeSession(c, entry.objectId, entry, createNativeSimulation);
  assert.deepEqual(restored.restore(saved, budget.simulation), budget);
  assert.equal(restored.simulation.mechanism.adapter, BUDGET_ADAPTER);
  assert.deepEqual(restored.select('codex.native-turn', budget), turn);
  assert.equal(restored.simulation.getScene().state.turn_start_count, 0);
  assert.deepEqual(createDomainModeSession(c, 'provider-native', getNativeWorkbenchEntry('provider-native'), createNativeSimulation).options.map(item => item.id), ['codex.native-turn']);
});
function finish(sim, caseId) {
  assert.equal(sim.chooseCase(caseId), true);
  for (let limit = 0; sim.getScene().next && limit < 40; limit++) assert.ok(sim.perform(sim.getScene().next.id));
  assert.equal(sim.getScene().next, null); return sim.getScene().state;
}
test('fixed increment: budget preserves charges across turn, claim, handover and probes; progress must be new', {skip: !process.env.NATIVE_INCREMENT_FACTS}, () => {
  const content = incrementContent('codex.recovery-budget'), original = structuredClone(content), sim = createNativeSimulation(content);
  assert.equal(content.mechanism.adapter, BUDGET_ADAPTER); assert.ok(supportsNativeWorkbench(content.mechanism));
  const expected = {exhausted: [3, 1, 'provider_recovery_exhausted'], progress: [0, 1, ''], replayed_item: [1, 1, ''],
    no_progress_events: [1, 1, ''], new_turn: [1, 1, ''], new_claim: [1, 2, ''], planned_handover: [1, 2, ''],
    one_disconnect: [1, 2, ''], invalid_checkpoint: [3, 1, 'provider_recovery_exhausted']};
  assert.deepEqual(content.mechanism.cases.map(c => c.id).sort(), Object.keys(expected).sort());
  for (const [id, [attempts, claims, error]] of Object.entries(expected)) {
    const s = finish(sim, id); assert.equal(s.attempts, attempts, id); assert.equal(s.checkpoint_attempts, attempts, id);
    assert.equal(s.claim_count, claims, id); assert.equal(s.error, error, id);
  }
  let state = finish(sim, 'one_disconnect'); assert.equal(state.reconnect_probes, 3); assert.equal(state.deferred, true); assert.equal(state.turn_id, 'turn-demo');
  state = finish(sim, 'new_turn'); assert.equal(state.turn_id, 'turn-next'); assert.deepEqual(state.seen_item_ids, []);
  state = finish(sim, 'replayed_item'); assert.equal(state.replenished, false); assert.deepEqual(state.seen_item_ids, ['item-demo']);
  sim.chooseCase('exhausted'); until(sim, 'normal'); assert.equal(sim.getScene().state.attempts, 0);
  assert.deepEqual(content, original);
});
test('fixed budget restore rejects other mechanisms/batches and unavailable authority; only declared prefixes run', {skip: !process.env.NATIVE_INCREMENT_FACTS}, () => {
  const content = incrementContent('codex.recovery-budget'), sim = createNativeSimulation(content);
  finish(sim, 'one_disconnect'); const saved = sim.getViewState(); sim.reset();
  assert.ok(sim.restoreViewState({...saved, state: {attempts: 0}})); assert.equal(sim.getScene().state.attempts, 1);
  assert.equal(sim.restoreViewState({...saved, mechanismId: 'codex.native-turn'}), false);
  assert.equal(sim.restoreViewState({...saved, batchKey: 'older-batch'}), false);
  sim.chooseCase('exhausted'); assert.equal(sim.perform('progress'), false);
  const missing = structuredClone(content); missing.mechanism.state_fields = missing.mechanism.state_fields.filter(f => f.id !== 'attempts');
  assert.throws(() => createNativeSimulation(missing), /native_field_missing:attempts/);
  const denied = structuredClone(content); denied.objects = denied.objects.filter(o => o.id !== 'execution.scheduling');
  assert.throws(() => createNativeSimulation(denied), /participant_unavailable/);
  const forged = structuredClone(content); forged.mechanism.steps.find(s => s.id === 'retry-one').state_changes[0].writer_id = 'execution.scheduling';
  assert.throws(() => createNativeSimulation(forged), /change_authority/);
});
test('fixed increment: pre-initialize connection failure has no submitted or accepted turn', {skip: !process.env.NATIVE_INCREMENT_FACTS}, () => {
  const sim = createNativeSimulation(incrementContent('codex.native-turn'));
  const s = finish(sim, 'connect_failure_before_accept');
  assert.equal(s.turn_start_count, 0); assert.equal(s.accepted_turn_id, ''); assert.equal(s.checkpoint_turn_id, '');
  assert.equal(s.failure, 'CodexAppServerUnavailable'); assert.equal(s.thread_loaded, false);
  assert.equal(sim.perform('start'), false);
});
