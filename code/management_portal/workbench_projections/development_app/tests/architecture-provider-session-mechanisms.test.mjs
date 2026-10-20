import test from 'node:test';
import assert from 'node:assert/strict';
import {createProviderSessionSimulation, PROVIDER_SESSION_ADAPTERS, getProviderSessionWorkbenchEntry, getProviderSessionDelegatedModes, supportsProviderSessionMechanism} from '../src/architecture/provider-session-mechanisms.model.js';
import {readProviderSessionFixture} from './architecture-provider-session-mechanisms.fixture.mjs';
const {model, content, contract} = readProviderSessionFixture();
const input = id => structuredClone({...content, mechanism:model.mechanisms.find(m => m.id === id)});
const finish = (id, caseId) => {
  const sim = createProviderSessionSimulation(input(id)); assert(sim.chooseCase(caseId));
  while (sim.getScene().next) assert(sim.perform(sim.getScene().next.id)); return sim.getScene();
};
test('frozen pair supplies exactly four authored adapters; excluded domains are not claimed', () => {
  assert(contract.includes('state_changes'));
  assert.deepEqual(model.mechanisms.filter(supportsProviderSessionMechanism).map(m => m.id).sort(), Object.keys(PROVIDER_SESSION_ADAPTERS).sort());
  assert(!supportsProviderSessionMechanism({...model.mechanisms.find(m => m.id === 'provider.catalog-settings'), adapter:'other'}));
  for (const id of ['provider.native-socket', 'codex.native-turn', 'provider-capabilities.preflight', 'ps.mechanism.runtime-selection']) assert(!supportsProviderSessionMechanism(model.mechanisms.find(m => m.id === id)));
  for (const o of model.objects.filter(o => getProviderSessionWorkbenchEntry(o.id))) {
    const e = getProviderSessionWorkbenchEntry(o.id);
    assert.equal(e.kind === 'composition', o.child_ids.length > 0);
    if (o.child_ids.length) { assert.equal(e.defaultMechanismId, null); assert.equal(e.factory, null); }
    else assert(o.mechanism_ids.includes(e.defaultMechanismId));
    for (const id of e.modeMechanismIds) assert(o.mechanism_ids.includes(id));
  }
  assert.equal(getProviderSessionWorkbenchEntry('execution.providers.catalog').defaultMechanismId, 'provider.catalog-settings');
  assert.equal(getProviderSessionWorkbenchEntry('__proto__'), null);
});
for (const id of Object.keys(PROVIDER_SESSION_ADAPTERS)) {
  const m = input(id).mechanism;
  for (const scenario of m.cases) test(`${id}/${scenario.id}: every declared prefix replays changes and next edges`, () => {
    const sim = createProviderSessionSimulation(input(id)); sim.chooseCase(scenario.id);
    const expected = Object.fromEntries(m.state_fields.map(f => [f.id, structuredClone(f.initial)]));
    assert.deepEqual(sim.getScene().state, expected);
    for (const stepId of scenario.path) {
      const before = sim.getViewState(); assert(!sim.perform('undeclared')); assert.deepEqual(sim.getViewState(), before);
      const step = m.steps.find(s => s.id === stepId);
      if (step.when) assert.deepEqual(expected[step.when.field], step.when.equals);
      for (const c of step.state_changes) {
        const v = c.value?.input ? scenario.inputs[c.value.input] : c.value;
        if (c.op === 'set') expected[c.field] = v;
        else if (c.op === 'increment') expected[c.field] += v;
        else expected[c.field].push(v);
      }
      assert.equal(sim.getScene().next.id, stepId); assert(sim.perform(stepId));
      assert.deepEqual(sim.getScene().state, expected); assert(sim.getScene().evidence_ids.every(id => content.evidence.some(e => e.id === id)));
      const saved = sim.getViewState(); assert(sim.back()); assert(sim.perform(stepId)); assert.deepEqual(sim.getViewState(), saved);
    }
    assert.equal(sim.getScene().next, null);
    for (const [field, value] of Object.entries(scenario.outcome)) assert.deepEqual(sim.getScene().state[field], value);
    const saved = sim.getViewState();
    for (const bad of [{...saved,batchKey:'other'}, {...saved,mechanismId:'provider.native-socket'}, {...saved,cursor:100}, {...saved,caseId:'undeclared-case'}]) {
      assert(!sim.restoreViewState(bad)); assert.deepEqual(sim.getViewState(), saved);
    }
  });
}
test('CAS rejects stale replacement and stale restore without overwriting newer identity', () => {
  for (const id of ['race','restore']) { const s = finish('provider.binding-cas', id).state; assert.equal(s.current_ref,'newer'); assert.equal(s.error,'RuntimeError'); assert.equal(s.receipt_saved,false); }
  const rejected = finish('provider.binding-cas','accepted').state; assert.equal(rejected.current_ref,'old');
  const completed = finish('provider.binding-cas','complete').state; assert.equal(completed.current_ref,'old'); assert.equal(completed.injection_count_delta,1);
});
test('read preparation remains read only; synchronous result forwarding and interrupt ack have separate meanings', () => {
  for (const scenario of input('provider.session-resolve').mechanism.cases) {
    const s = finish('provider.session-resolve',scenario.id).state; assert.equal(s.binding_written,false); assert.equal(s.native_calls,0);
  }
  const ack = finish('provider.adapter-boundary','ack').state; assert.equal(ack.control_ack,true); assert.equal(ack.observed_stopped,'unknown');
  const failed = finish('provider.adapter-boundary','execute-error').state; assert.equal(failed.provider_calls,1); assert.equal(failed.result_forwarded,false); assert.equal(failed.provider_error_propagated,true);
  const okay = finish('provider.adapter-boundary','execute').state; assert.equal(okay.result_received,true); assert.equal(okay.result_forwarded,true); assert.equal(okay.observed_stopped,'unknown');
});
test('natural-language guards never become predicates; invalid machine conditions, writers, edges and sources fail closed', () => {
  const c = input('provider.catalog-settings'); for (const s of c.mechanism.steps) s.guard = 'deliberately unparseable human explanation';
  assert.doesNotThrow(() => createProviderSessionSimulation(c));
  const mutations = [
    c => { c.mechanism.steps[1].when = {field:'selection',equals:'unsupported_setting'}; },
    c => { c.mechanism.steps[1].when = {expression:'invented'}; },
    c => { c.mechanism.steps[1].next = ['missing']; },
    c => { c.mechanism.steps[1].state_changes[0].field = 'missing'; },
    c => { c.mechanism.steps[1].state_changes[0].writer_id = 'missing'; },
    c => { c.mechanism.evidence_ids = ['not-authorized']; },
    c => { c.mechanism.cases[0].inputs.selection = 'invented'; },
    c => { c.mechanism.cases[0].path = ['load', 'reject-setting']; },
  ];
  for (const mutate of mutations) { const c = input('provider.catalog-settings'); mutate(c); assert.throws(() => createProviderSessionSimulation(c), /provider_session:/); }
  const bindings = input('provider.binding-cas'); bindings.mechanism.steps.find(s => s.id === 'replace').state_changes[0].writer_id = bindings.mechanism.owner_id;
  assert.throws(() => createProviderSessionSimulation(bindings), /change_authority/);
});
test('catalog modes: explicit settings default and delegated preflight are independent of supplied ordering', () => {
  const c = input('provider.catalog-settings'); c.mechanisms.reverse(); c.objects.find(o => o.id === 'execution.providers.catalog').mechanism_ids.reverse();
  assert.equal(getProviderSessionWorkbenchEntry('execution.providers.catalog').defaultMechanismId,'provider.catalog-settings');
  const modes = getProviderSessionDelegatedModes(c,'execution.providers.catalog');
  assert.equal(modes.length,1); assert.deepEqual(modes[0].route,{kind:'mechanism',objectId:'execution.providers.catalog',mechanismId:'provider-capabilities.preflight'});
  c.evidence = []; assert.deepEqual(getProviderSessionDelegatedModes(c,'execution.providers.catalog'),[]);
  const c2 = input('provider.catalog-settings'); c2.mechanisms.push(c2.mechanisms.find(m => m.id === 'provider-capabilities.preflight'));
  assert.deepEqual(getProviderSessionDelegatedModes(c2,'execution.providers.catalog'),[]);
});
