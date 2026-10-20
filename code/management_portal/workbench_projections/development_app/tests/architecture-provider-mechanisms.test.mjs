import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync, writeFileSync} from 'node:fs';
import {createHash} from 'node:crypto';
import {createProviderSimulation, supportsProviderMechanisms, PROVIDER_ADAPTERS, PROVIDER_OBJECT_ENTRIES, getProviderWorkbenchEntry, createDomainModeSession, getProviderDelegatedModes} from '../src/architecture/provider-mechanisms.model.js';
const sha = text => createHash('sha256').update(text).digest('hex');
const fixturePath = process.env.PROVIDER_FACTS;
const fixture = fixturePath ? JSON.parse(readFileSync(fixturePath, 'utf8')) : null;
const textOf = descriptor => { const text = fixture.facts[descriptor.address].text; assert.equal(sha(text), descriptor.sha256); assert.equal(Buffer.byteLength(text), descriptor.size); return JSON.parse(text); };
let model, batchKey, contents;
if (fixture) {
  textOf(fixture.entry); const manifest = textOf(fixture.manifest);
  const approved={'3f565481c7878fcc8c5d0876b0b31f9e4d3b305d4955c25d5a77a5dc926a0313':['226ade465c813a0494d5819b70a7448483c311c171c1125416e3b3a5e56bec10',15],
    'ec379604ae9355d9426fd22456675a1118d5f58e2fe8a3a72b08a16f0bb68c73':['9105515ec8785625eec38571299947d6a877504af9ac3f17721d12a48a0103b8',18]};
  assert.ok(approved[fixture.manifest.sha256]);const [modelHash,count]=approved[fixture.manifest.sha256];
  const entry = Object.entries(fixture.facts).find(([address]) => address.endsWith(`model-${modelHash}.json`));
  assert.ok(entry); assert.equal(sha(entry[1].text), modelHash);
  assert.ok(JSON.stringify(manifest).includes(modelHash));
  model = JSON.parse(entry[1].text); batchKey = fixture.manifest.sha256;
  contents = model.mechanisms.filter(supportsProviderMechanisms).map(mechanism => ({mechanism, objects: model.objects, evidence: model.evidence, batchKey}));
  assert.equal(contents.length,count);
  if (process.env.PROVIDER_CONTENT) writeFileSync(process.env.PROVIDER_CONTENT, JSON.stringify(contents.map(c=>{
    const m=c.mechanism,objects=new Set([m.owner_id,...m.participants.map(p=>p.object_id),...m.state_fields.map(f=>f.authority_id)]);
    const evidence=new Set([...m.evidence_ids,...m.steps.flatMap(s=>s.evidence_ids),...m.cases.flatMap(s=>s.evidence_ids)]);
    return {...c,objects:c.objects.filter(o=>objects.has(o.id)),evidence:c.evidence.filter(e=>evidence.has(e.id))};
  }), null, 2));
}
const formal = (name, fn) => test(name, {skip: !fixturePath && 'Set PROVIDER_FACTS to the exact frozen domains-expanded fixture'}, fn);
const content = id => structuredClone(contents.find(c => c.mechanism.id === id));
const run = (id, caseId, stop) => { const sim = createProviderSimulation(content(id)); assert.equal(sim.chooseCase(caseId),true,`Unknown declared case: ${id}/${caseId}`); let scene; while ((scene = sim.getScene()).next && (!stop || scene.current?.id !== stop)) assert.equal(sim.perform(scene.next.id), true); return sim; };
formal('Entry: exact authored defaults match fixed object membership and preserve nonleaf composition', () => {
  for (const id of Object.keys(PROVIDER_OBJECT_ENTRIES)) {
    const entry = getProviderWorkbenchEntry(id), object = model.objects.find(item => item.id === id);
    assert.ok(object, id); assert.equal(entry.kind === 'composition', object.child_ids.length > 0, id);
    if (entry.kind === 'composition') { assert.equal(entry.defaultMechanismId, null); assert.equal(entry.factory, null); }
    else { assert.ok(object.mechanism_ids.includes(entry.defaultMechanismId)); assert.equal(entry.factory, 'createProviderMechanisms'); }
    for (const mode of [...entry.modeMechanismIds, ...entry.delegatedModes.map(item => item.mechanismId)]) assert.ok(object.mechanism_ids.includes(mode), `${id}/${mode}`);
  }
  assert.equal(getProviderWorkbenchEntry('model-api').defaultMechanismId, 'ps.mechanism.api-chat-lifecycle');
  assert.equal(getProviderWorkbenchEntry('model-services.typed-decisions').defaultMechanismId, 'ps.mechanism.api-jev-lifecycle');
  assert.equal(getProviderWorkbenchEntry('provider-capabilities').defaultMechanismId, 'provider-capabilities.negotiate');
  assert.equal(getProviderWorkbenchEntry('model-api.unknown'), null); assert.equal(getProviderWorkbenchEntry('__proto__'), null);
  const copy = getProviderWorkbenchEntry('model-api'); copy.modeMechanismIds.reverse();
  assert.equal(getProviderWorkbenchEntry('model-api').modeMechanismIds[0], 'ps.mechanism.api-chat-lifecycle');
});
formal('Modes: only same-object supplied valid mechanisms are offered; array order does not choose entry', () => {
  const c = {...content('ps.mechanism.api-chat-lifecycle'), mechanisms: [...model.mechanisms].reverse()};
  const entry = getProviderWorkbenchEntry('model-services.chat-completion');
  const session = createDomainModeSession(c, entry.objectId, entry, createProviderSimulation);
  assert.deepEqual(session.options.map(item => item.id), entry.modeMechanismIds);
  assert.equal(session.simulation.mechanism.id, entry.defaultMechanismId);
  assert.equal(session.select('ps.mechanism.relay-connection', {}), null);
  const absent = {...c, mechanisms: []};
  assert.equal(createDomainModeSession(absent, entry.objectId, entry, createProviderSimulation).options.length, 1);
  const invalid = structuredClone(c); invalid.mechanisms.find(m => m.id === 'ps.mechanism.chat-request').evidence_ids.push('not-authorized');
  assert.equal(createDomainModeSession(invalid, entry.objectId, entry, createProviderSimulation).options.length, 1);
  assert.throws(() => createDomainModeSession({...c, evidence: []}, entry.objectId, entry, createProviderSimulation));
});
formal('Modes: per-mode prefixes and viewport snapshots restore atomically within one subject and batch', () => {
  const c = {...content('ps.mechanism.api-chat-lifecycle'), mechanisms: model.mechanisms}, entry = getProviderWorkbenchEntry('model-api');
  const session = createDomainModeSession(c, entry.objectId, entry, createProviderSimulation);
  const local = scrollTop => ({simulation: session.simulation.getViewState(), scrollTop, focusId: 'mode', sourcesOpen: false});
  session.simulation.perform(session.simulation.getScene().next.id); const chat = local(123);
  session.select('ps.mechanism.api-jev-lifecycle', chat); session.simulation.perform(session.simulation.getScene().next.id);
  session.simulation.perform(session.simulation.getScene().next.id); const jev = local(234), saved = session.snapshot(jev);
  assert.deepEqual(session.select('ps.mechanism.api-chat-lifecycle', jev), chat);
  assert.equal(session.simulation.getScene().cursor, chat.simulation.cursor);
  assert.equal(session.restore({...saved, objectId: 'model-services.chat-completion'}), null);
  assert.equal(session.restore({...saved, batchKey: '0'.repeat(64)}), null);
  const corrupt = structuredClone(saved); corrupt.views[0].state.simulation.cursor = 999;
  const before = session.simulation.getScene(); assert.equal(session.restore(corrupt), null); assert.deepEqual(session.simulation.getScene(), before);
  assert.equal(session.restore(saved, {...jev.simulation, cursor: 999}), null);
  assert.deepEqual(session.restore(saved, jev.simulation), jev);
  assert.deepEqual(session.select('ps.mechanism.api-chat-lifecycle', jev), chat);
});
formal('Modes: lifecycle delegated from capability uses exact parent route and supplied evidence', () => {
  const c = {...content('provider-capabilities.negotiate'), mechanisms: model.mechanisms};
  assert.deepEqual(getProviderDelegatedModes(c, 'provider-capabilities').map(item => item.route), [{kind: 'protocol', objectId: 'provider-capabilities', mechanismId: 'execution.lifecycle'}]);
  assert.deepEqual(getProviderDelegatedModes({...c, mechanisms: []}, 'provider-capabilities'), []);
  assert.deepEqual(getProviderDelegatedModes({...c, evidence: []}, 'provider-capabilities'), []);
  assert.deepEqual(getProviderDelegatedModes(c, 'model-api'), []);
});
test('Provider adapters are explicit and do not substitute native/unknown presentations', () => {
  assert.equal(supportsProviderMechanisms({adapter: 'codex.native-turn.v1', mode: 'simulated'}), false);
  assert.equal(supportsProviderMechanisms({adapter: 'made-up', presentation: 'permission_matrix', mode: 'simulated'}), false);
  assert.equal(supportsProviderMechanisms({adapter: 'provider-capabilities.negotiate.v1', mode: 'live'}), false);
});
formal('All exact fixed mechanisms replay their declared typed paths and return reversibly', () => {
  for (const c of contents) { const sim = createProviderSimulation(c); for (const item of c.mechanism.cases) {
    assert.equal(sim.chooseCase(item.id), true); for (const id of item.path) assert.equal(sim.perform(id), true);
    const end = sim.getScene(); assert.equal(end.next, null); assert.equal(sim.back(), true); assert.equal(sim.perform(item.path.at(-1)), true); assert.deepEqual(sim.getScene(), end);
  } }
});
formal('Increment: exact Chat/Jev and Relay lifecycle cases retain waiting and connection semantics',()=>{
  const added=contents.filter(c=>['apiLifecycle','relayLifecycle'].includes(PROVIDER_ADAPTERS[c.mechanism.adapter].view));assert.equal(added.length,3);
  for(const c of added){for(const item of c.mechanism.cases){const sim=run(c.mechanism.id,item.id);assert.equal(sim.getScene().next,null);}}
  for(const id of ['ps.mechanism.api-chat-lifecycle','ps.mechanism.api-jev-lifecycle']){
    const s=run(id,'late_response').getScene().state;assert.equal(s.result_accepted,false);assert.equal(s.late_result_disposition,'dropped');assert.equal(s.upstream_interrupt_sent,false);assert.equal(s.remote_compute_state,'unknown');
    const exhausted=run(id,'retry_exhausted').getScene().state;assert.equal(exhausted.attempt_count,2);assert.equal(exhausted.attempts_remaining,0);
  }
  const bad=run('ps.mechanism.relay-connection','bad_payload').getScene().state,broken=run('ps.mechanism.relay-connection','bad_json').getScene().state;
  assert.equal(bad.response_status,400);assert.equal(bad.can_read_next_frame,true);assert.equal(bad.slot_held,true);
  assert.equal(broken.response_status,400);assert.equal(broken.can_read_next_frame,false);assert.equal(broken.slot_held,false);
  assert.equal(run('ps.mechanism.relay-connection','same_id_twice').getScene().state.execution_submit_count,2);
});
formal('Native launch increment: five distinct providers preserve routing, credentials and process boundaries',()=>{
  const added=contents.filter(c=>['dispatch','credentials','kimi','dsh','bridge'].includes(PROVIDER_ADAPTERS[c.mechanism.adapter].view));
  assert.equal(added.length,5);let cases=0;
  for(const c of added)for(const item of c.mechanism.cases){
    const sim=run(c.mechanism.id,item.id),end=sim.getScene();assert.equal(end.next,null);cases++;
    const saved=sim.getViewState();sim.back();assert.equal(sim.restoreViewState(saved),true);assert.deepEqual(sim.getScene(),end);
  }
  assert.equal(cases,20);
  const state=(id,c)=>run('ps.mechanism.'+id,c).getScene().state;
  assert.equal(state('runtime-selection','api').destination,'ChatCompletionService');assert.equal(state('runtime-selection','api').native_session,false);
  assert.equal(state('runtime-selection','invalid').destination,'unsupported_provider');
  assert.equal(state('claude-credentials','mix').credential_source,'');assert.equal(state('claude-credentials','wrong-dir').configuration,'rejected_directory');
  assert.equal(state('kimi-launch','normal').database_env_removed,true);assert.equal(state('kimi-launch','external-host').database_env_removed,false);
  assert.equal(state('kimi-launch','missing').stage,'missing_runtime');
  assert.equal(state('dsh-start','reuse').process_started,false);assert.equal(state('dsh-start','reuse').launch_lock,false);
  assert.equal(state('dsh-start','timeout').process_started,true);assert.equal(state('dsh-start','timeout').status,'startup_timed_out');
  assert.equal(state('opencode-bridge','paid').child_key_env,false);assert.equal(state('opencode-bridge','paid').key_source,'bound_registry_file');
  assert.equal(state('opencode-bridge','binding').status,'route_rejected');assert.equal(state('opencode-bridge','server').event_transport,'');
});
formal('HTTP cancellation keeps remote unknown; pre-cancel sends none; retry is explicit', () => {
  const id='ps.mechanism.chat-request'; const cancelled=run(id,'cancel').getScene().state;
  assert.equal(cancelled.request_count,1); assert.equal(cancelled.remote_state,'unknown'); assert.equal(cancelled.status,'cancelled_wait');
  assert.equal(run(id,'pre-cancel').getScene().state.request_count,0); assert.equal(run(id,'retry').getScene().state.request_count,2); assert.equal(run(id,'exhausted').getScene().state.request_count,1);
});
formal('Relay read deadline/connection slots, routing refusal and execution wait are distinct', () => {
  let s=run('ps.mechanism.relay-gate','timeout','timeout').getScene().state; assert.equal(s.slot_held,true); assert.equal(s.connection,'closed'); assert.equal(s.response_status,0);
  assert.equal(run('ps.mechanism.relay-gate','timeout').getScene().state.slot_held,false);
  s=run('ps.mechanism.relay-routing','model').getScene().state; assert.equal(s.status,403); assert.equal(s.api_selection,'');
  s=run('ps.mechanism.relay-roundtrip','wait').getScene().state; assert.equal(s.submitted,true); assert.equal(s.response_status,502); assert.equal(s.remote_stopped,'unknown');
});
formal('Capability operation success does not imply plugin feature or Provider invocation', () => {
  const s=run('provider-capabilities.negotiate','feature').getScene().state;
  assert.equal(s.operation_allowed,true); assert.equal(s.feature_enabled,false); assert.equal(s.reason,'requires_portal_tools'); assert.equal(Object.hasOwn(s,'native_call_count'),false);
});
formal('Synchronous CompleteChat and typed answers preserve their own failure boundaries', () => {
  const invalid=run('ps.mechanism.complete-chat','schema').getScene().state; assert.equal(invalid.http_sent,false); assert.equal(invalid.queue_submitted,false);
  const answer=run('ps.mechanism.complete-chat','answer').getScene().state; assert.equal(answer.http_sent,true); assert.equal(answer.retryable,true); assert.equal(answer.structured_available,false);
  const typed=run('ps.mechanism.typed-answer','missing-question').getScene().state; assert.equal(typed.request_sent,true); assert.equal(typed.answers_accepted,false);
});
formal('Same-ID account reuse and explicit native endpoint routes never synthesize fallback', () => {
  const account=run('ps.mechanism.api-account','shared').getScene().state; assert.equal(account.extra_account_created,false); assert.equal(account.account_source,'existing_native_account');
  const endpoint=run('ps.mechanism.native-endpoints','invalid').getScene().state; assert.notEqual(endpoint.status,'route_selected'); assert.equal(endpoint.service,'');
});
formal('Saved state replays only a declared prefix in the same immutable batch', () => {
  const sim=run('ps.mechanism.chat-request','cancel'),saved=sim.getViewState(),next=createProviderSimulation(content('ps.mechanism.chat-request'));
  assert.equal(next.restoreViewState({...saved,state:{remote_state:'stopped'}}),true); assert.equal(next.getScene().state.remote_state,'unknown');
  assert.equal(next.restoreViewState({...saved,batchKey:'e'.repeat(64)}),false); assert.equal(next.restoreViewState({...saved,cursor:999}),false); assert.equal(next.perform('invented'),false);
});
formal('Missing permissions/evidence/endpoints and malformed paths reject before presentation', () => {
  const original=content('ps.mechanism.chat-request');
  for (const mutate of [c=>{c.evidence=[];},c=>{c.objects=c.objects.filter(o=>o.id!==c.mechanism.participants[0].object_id);},c=>{c.mechanism.cases[0].path.reverse();},c=>{c.mechanism.steps[0].state_changes[0].field='unknown';},c=>{c.mechanism.steps[0].when={field:'not_state',equals:true};}]) {
    const bad=structuredClone(original); mutate(bad); assert.throws(()=>createProviderSimulation(bad),/provider_mechanisms:/);
  }
});
