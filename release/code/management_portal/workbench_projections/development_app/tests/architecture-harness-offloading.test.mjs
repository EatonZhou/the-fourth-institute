import test from 'node:test';
import assert from 'node:assert/strict';
import {createHarnessOffloadingSimulation,HARNESS_OFFLOADING_ID,HARNESS_OFFLOADING_ADAPTER,HARNESS_PIPELINE_MODE_IDS} from '../src/architecture/harness-offloading.model.js';
import {contentFor,sourceHashes} from './architecture-harness-offloading.fixture.mjs';
const content=contentFor();
test('new pinned offloading batch and same pipeline modes are explicit',()=>{
  assert.equal(HARNESS_OFFLOADING_ADAPTER,`${HARNESS_OFFLOADING_ID}.v1`);
  assert.deepEqual(HARNESS_PIPELINE_MODE_IDS,['harness.plugin-lifecycle','harness.tool-output-offloading']);
  assert.equal(sourceHashes.manifest.sha256, '8d1968564339972de4de5bb323096d9d9762c0a79367586e0a1e6001bf6aa926');
  assert.equal(sourceHashes.model.sha256, '8fc5b0a7252879de2e3e65bce69836750ea1560c1d60a397d3c175d9abe8f70e');
});
for(const c of content.mechanism.cases)test(`offloading ${c.id}: formal path and outcome`,()=>{
  const sim=createHarnessOffloadingSimulation(content);sim.chooseCase(c.id);
  assert.deepEqual(sim.getScene().observed_fields,[]);assert.equal(sim.recall(0,20),null);
  for(const id of c.path){assert.equal(sim.getScene().next.id,id);sim.advance();assert.equal(sim.getScene().current.id,id);}
  const scene=sim.getScene();for(const [key,value]of Object.entries(c.outcome))assert.deepEqual(scene.state[key],value);
  assert.equal(scene.complete,true);assert.equal(sim.advance(),false);
  if(scene.state.replaced){assert.equal(scene.state.artifact_retained,true);assert.equal(scene.state.original_retrievable,true);}
  const saved=sim.getViewState(),restored=createHarnessOffloadingSimulation(content);assert(restored.restoreViewState(saved));assert.deepEqual(restored.getScene(),scene);
  assert.equal(restored.restoreViewState({...saved,batchKey:'different'}),false);assert.equal(restored.restoreViewState({...saved,cursor:999}),false);
  assert.deepEqual(restored.getScene(),scene);sim.back();assert.equal(sim.getScene().cursor,c.path.length-1);
});
function complete(id){const sim=createHarnessOffloadingSimulation(content);sim.chooseCase(id);while(sim.getScene().next)sim.advance();return sim;}
test('UTF-8 bytes, effective threshold and character preview budgets are distinct',()=>{
  const utf=complete('utf8_threshold').getScene().state;assert.equal(utf.original_bytes,65538);assert.equal(utf.original_chars,21846);assert.equal(utf.preview_budget,2000);
  assert.equal(complete('threshold_equal').getScene().state.replaced,true);assert.equal(complete('threshold_below').getScene().state.replaced,false);
  const noSaving=complete('no_saving').getScene().state;assert.equal(noSaving.trigger_bytes,4096);assert.equal(noSaving.preview_budget,16000);
  assert.equal(noSaving.artifact_retained,true);assert.equal(noSaving.replaced,false);assert.equal(noSaving.provider_visible_version,'full-v1');
});
test('storage interruption, escaped ordinary error and outer control remain different',()=>{
  const storage=complete('storage_interrupted').getScene().state,ordinary=complete('ordinary_plugin_error').getScene().state,control=complete('control_signal').getScene().state;
  assert.equal(storage.plugin_status,'skipped');assert.equal(storage.put_attempts,1);assert.equal(storage.propagated_error,'');assert.equal(storage.reason,'artifact_retention_unavailable');
  assert.equal(ordinary.plugin_status,'failed_open');assert.equal(ordinary.propagated_error,'');assert.equal(control.plugin_status,'interrupted');assert.equal(control.propagated_error,'ExecutionControlSignal');
  assert.equal(complete('interrupted').getScene().state.propagated_error,'InterruptedError');
});
test('exact synthetic recall only after retention, including retained-but-not-replaced case',()=>{
  const sim=createHarnessOffloadingSimulation(content);sim.advance();sim.advance();assert.equal(sim.recall(0,20),null);sim.advance();
  assert.equal(sim.getScene().state.replaced,false);assert.equal(sim.recall(20,120).text,'x'.repeat(120));
  assert.equal(complete('utf8_threshold').recall(21000,5).text,'汉'.repeat(5));assert.equal(complete('no_saving').recall(4090,20).text,'x'.repeat(6));
  assert.equal(sim.recall(-1,20),null);assert.equal(sim.recall(0,2001),null);assert.equal(complete('storage_error').recall(0,20),null);
});
test('malformed fields, paths, conditions, sources and fixtures reject before rendering; prose never runs',()=>{
  for(const mutate of [
    c=>c.mechanism.steps[0].state_changes[0].op='execute',
    c=>c.mechanism.steps[0].state_changes[0].field='unknown',
    c=>c.mechanism.steps[0].state_changes[0].writer_id='missing',
    c=>c.mechanism.steps[0].state_changes[0].value={input:'missing'},
    c=>c.mechanism.steps[1].when={field:'missing',equals:true},
    c=>c.mechanism.steps[1].next=[],
    c=>c.mechanism.steps[2].relation_id='missing',
    c=>c.mechanism.cases[0].outcome.replaced=false,
    c=>c.mechanism.state_fields.shift(),
    c=>c.evidence=[],
    c=>c.mechanism.teaching_fixtures[0].content_repeat=1,
  ]){const c=contentFor();mutate(c);assert.throws(()=>createHarnessOffloadingSimulation(c));}
  const c=contentFor();for(const s of c.mechanism.steps)s.guard='throw new Error("prose must not run")';assert.doesNotThrow(()=>createHarnessOffloadingSimulation(c));
});
