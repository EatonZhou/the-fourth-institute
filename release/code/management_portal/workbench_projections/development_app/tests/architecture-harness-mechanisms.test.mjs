import test from 'node:test';
import assert from 'node:assert/strict';
import {HARNESS_ADAPTERS, HARNESS_OBJECT_ENTRIES, createHarnessSimulation, getHarnessWorkbenchEntry, supportsHarnessMechanisms} from '../src/architecture/harness-mechanisms.model.js';
import {contentFor, sourceHashes} from './architecture-harness-mechanisms.fixture.mjs';

test('pinned manifest/model/contract and four exact adapter identities', () => {
  assert.equal(sourceHashes.model.sha256, 'daf11b2d9e220ca7ef37fc058e798931c6d610b8c98dd65ca326097ef05cecd7');
  assert.equal(sourceHashes.pair.sha256, 'ba11d85123441bc9411207bac97fec92a8f0f3ac2429d896347cbcc90b062810');
  assert.deepEqual(Object.values(HARNESS_ADAPTERS), ['harness.plan-selection','harness.tool-attachment','harness.plugin-lifecycle','harness.observation']);
  for (const [adapter,id] of Object.entries(HARNESS_ADAPTERS)) {
    assert.equal(adapter, `${id}.v1`); assert(supportsHarnessMechanisms(contentFor(id).mechanism));
  }
  assert.equal(supportsHarnessMechanisms({...contentFor('harness.plan-selection').mechanism, id:'harness.plan-selection.v1'}), false);
  assert.equal(getHarnessWorkbenchEntry('worker.harness').kind, 'composition');
  assert.equal(getHarnessWorkbenchEntry('worker.harness').defaultMechanismId, null);
  assert.equal(getHarnessWorkbenchEntry('worker.runtime'), null);
  for (const [objectId,entry] of Object.entries(HARNESS_OBJECT_ENTRIES)) {
    if (objectId === 'worker.harness') continue;
    assert.equal(entry.modeMechanismIds.length, 1); assert.equal(entry.modeMechanismIds[0],entry.defaultMechanismId);
  }
});

for (const id of Object.values(HARNESS_ADAPTERS)) {
  const content = contentFor(id);
  for (const scenario of content.mechanism.cases) test(`${id} / ${scenario.id}: formal path, state and outcome`, () => {
    const sim = createHarnessSimulation(content); sim.chooseCase(scenario.id);
    assert.equal(sim.getScene().cursor, 0);
    for (const stepId of scenario.path) {
      assert.equal(sim.getScene().next.id, stepId); assert.equal(sim.advance(), true);
      const scene = sim.getScene(); assert.equal(scene.current.id, stepId);
      for (const change of scene.trace.at(-1).changes) assert.equal(scene.state[change.field], change.after);
    }
    const scene = sim.getScene(); assert.equal(scene.complete, true); assert.equal(sim.advance(), false);
    for (const [field,value] of Object.entries(scenario.outcome)) assert.deepEqual(scene.state[field],value);
    if (id === 'harness.observation') assert.equal(scene.state.scheduler_terminal_written, false);
    if (scenario.id === 'control') assert.equal(scene.state.error, 'ExecutionControlSignal');
    const saved = sim.getViewState(), restored = createHarnessSimulation(content);
    assert.equal(restored.restoreViewState(saved), true); assert.deepEqual(restored.getScene(),scene);
    assert.equal(restored.restoreViewState({...saved,batchKey:'other-batch'}), false); assert.deepEqual(restored.getScene(),scene);
    assert.equal(restored.restoreViewState({...saved,cursor:1000}), false);
    assert.equal(restored.restoreViewState({...saved,caseId:'invented'}), false);
    sim.back(); assert.equal(sim.getScene().cursor, scenario.path.length-1); sim.reset(); assert.equal(sim.getScene().cursor,0);
  });
}

test('ordinary plugin error continues; control and invalid batch retain separate exits', () => {
  const sim = createHarnessSimulation(contentFor('harness.plugin-lifecycle'));
  sim.chooseCase('fail-open'); sim.advance(); sim.advance();
  assert.equal(sim.getScene().state.plugin_status, 'failed_open'); assert.equal(sim.getScene().state.provider_called,false);
  sim.advance(); assert.equal(sim.getScene().state.provider_called,true);
  sim.chooseCase('control'); sim.advance(); sim.advance();
  assert.equal(sim.getScene().state.provider_called,false); assert.equal(sim.getScene().state.plugin_status,'pending');
  sim.chooseCase('batch-type'); sim.advance(); sim.advance();
  assert.equal(sim.getScene().state.batch_preserved,true); assert.equal(sim.getScene().state.provider_called,false);
});

test('formal model rejects malformed declarations, paths, authority and evidence before replay', () => {
  const mutations = [
    c => { c.mechanism.steps[0].state_changes[0].op='execute'; },
    c => { c.mechanism.steps[0].state_changes[0].field='unknown'; },
    c => { c.mechanism.steps[0].state_changes[0].writer_id='unavailable'; },
    c => { c.mechanism.steps[0].state_changes[0].value={input:'missing'}; },
    c => { c.mechanism.steps[1].when={field:'unknown',equals:true}; },
    c => { c.mechanism.steps[1].when={field:'plan_condition',execute:'anything'}; },
    c => { c.mechanism.steps[1].when={field:'plan_condition',equals:'impossible'}; },
    c => { c.mechanism.steps[1].relation_id='missing'; },
    c => { c.mechanism.steps[1].next=[]; },
    c => { c.mechanism.steps[0].next.push('missing'); },
    c => { c.mechanism.cases[0].path=['load','replay']; },
    c => { c.mechanism.cases[0].path=['load','select']; },
    c => { c.mechanism.cases[0].outcome.effective_plan='invented'; },
    c => { c.mechanism.cases[0].inputs.extra='invented'; },
    c => { c.mechanism.state_fields.shift(); },
    c => { c.mechanism.state_fields[1].authority_id='worker.runtime'; },
    c => { c.mechanism.state_model.condition_source='prose'; },
    c => { c.evidence=[]; },
    c => { c.evidence[0].sha256='0'.repeat(64); },
    c => { c.batchKey=''; },
  ];
  for (const mutate of mutations) { const c=contentFor('harness.plan-selection');mutate(c);assert.throws(()=>createHarnessSimulation(c)); }
});

test('natural guard remains explanatory and returned scenes cannot modify authoritative local replay', () => {
  const c=contentFor('harness.plan-selection'); for(const step of c.mechanism.steps)step.guard='throw new Error("do not evaluate prose")';
  const sim=createHarnessSimulation(c); sim.advance(); const scene=sim.getScene(); scene.state.effective_plan='tampered';
  assert.equal(sim.getScene().state.effective_plan,''); sim.advance();assert.equal(sim.getScene().state.effective_plan,'capability-filtered');
});
