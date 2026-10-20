import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {createAgentDataSimulation,AGENT_DATA_ADAPTERS} from '../src/architecture/agent-data-mechanisms.model.js';
import {AGENT_DATA_VIEWS} from '../src/architecture/agent-data-mechanisms.views.js';
const fixture=JSON.parse(readFileSync(new URL('./architecture-agent-data-mechanisms.fixture.json',import.meta.url)));
const content=id=>({mechanism:structuredClone(fixture.mechanisms.find(m=>m.id===`agent-data.${id}`)),objects:structuredClone(fixture.objects),evidence:structuredClone(fixture.evidence),batchKey:fixture.provenance.model.sha256});
for(const m of fixture.mechanisms) test(`${m.id}: frozen cases, exact outcomes, reversible state and batch restoration`,()=>{
  const c=content(m.id.slice(11)),sim=createAgentDataSimulation(c);
  for(const scenario of m.cases){sim.choose(scenario.id);const before=sim.getScene();const snapshots=[sim.getScene().state];while(!sim.getScene().complete){sim.advance();snapshots.push(sim.getScene().state);}
    for(const [key,value] of Object.entries(scenario.outcome))assert.deepEqual(sim.getScene().state[key],value,scenario.id+':'+key);
    const saved=sim.getViewState(),state=sim.getScene().state;sim.reset();sim.restore(saved);assert.deepEqual(sim.getScene().state,state);
    while(sim.getScene().position){sim.previous();assert.deepEqual(sim.getScene().state,snapshots[sim.getScene().position]);}
    assert.deepEqual(sim.getScene().state,before.state);
    assert.throws(()=>sim.restore({...saved,batchKey:'another-batch'}),/invalid_restore/);
  }
});
test('receipts remain after failure; accepted projection does not update stable binding',()=>{
  const receipt=createAgentDataSimulation(content('execution-receipt'));while(!receipt.getScene().complete)receipt.advance();assert.equal(receipt.getScene().state.accepted,true);assert.equal(receipt.getScene().state.execution_status,'failed');
  const accepted=createAgentDataSimulation(content('acceptance-projection'));accepted.advance();assert.equal(accepted.getScene().state.session_linked,true);assert.equal(accepted.getScene().state.receipt,false);accepted.advance();assert.equal(accepted.getScene().state.receipt,true);assert.equal(accepted.getScene().state.stable_binding_updated,false);
});
test('fork, unchanged inherited content and query windows retain their separate state',()=>{
  for(const id of ['trace-revision','actual-input','read-views']){const sim=createAgentDataSimulation(content(id));while(!sim.getScene().complete)sim.advance();const s=sim.getScene().state;if(id==='trace-revision'){assert.deepEqual(s.fork,['s1','s2']);assert.deepEqual(s.current,['s3']);}if(id==='actual-input')assert.equal(s.inherited_unchanged,true);if(id==='read-views'){assert.equal(s.chat_includes_notice,true);assert.equal(s.model_includes_notice,false);}}
});
test('missing sources, false writer and corrupt paths are rejected before any scene',()=>{
  let c=content('execution-receipt');c.evidence=[];assert.throws(()=>createAgentDataSimulation(c),/evidence_unavailable/);
  c=content('execution-receipt');c.mechanism.steps.find(s=>s.id==='failed').state_changes[0].writer_id=c.mechanism.owner_id;assert.throws(()=>createAgentDataSimulation(c),/wrong_authority/);
  c=content('segment-identity');c.mechanism.steps.find(s=>s.id==='new-payload').when.equals='same';assert.throws(()=>createAgentDataSimulation(c),/guard_failed/);
  c=content('trace-revision');c.mechanism.cases[0].path=['load','fork-stable'];assert.throws(()=>createAgentDataSimulation(c),/disconnected_path/);
  c=content('completed-content');c.mechanism.cases[0].outcome.segments=5;assert.throws(()=>createAgentDataSimulation(c),/outcome_mismatch/);
});
test('an adapter does not authorize another domain; restored positions and inputs are bounded',()=>{
  const c=content('actual-input');c.mechanism.adapter='execution.lifecycle.v1';assert.throws(()=>createAgentDataSimulation(c),/unsupported_mechanism/);
  const sim=createAgentDataSimulation(content('segment-identity'));assert.throws(()=>sim.restore({...sim.getViewState(),position:50}),/invalid_restore/);assert.throws(()=>sim.choose('fabricated'),/unknown_case/);
  const invalid=content('segment-identity');invalid.mechanism.cases[0].inputs.identity='invented';assert.throws(()=>createAgentDataSimulation(invalid),/invalid_input/);
});
test('each domain has a distinct diagram and fallback discrepancy remains visible',()=>{
  assert.equal(AGENT_DATA_ADAPTERS.length,7);assert.equal(new Set(Object.values(AGENT_DATA_VIEWS)).size,7);
  const sim=createAgentDataSimulation(content('completed-content'));sim.choose('fallback');sim.advance();const html=AGENT_DATA_VIEWS['completed-content'](sim.getScene());assert.match(html,/规范描述仍待事实 owner 对齐/);assert.match(html,/完成返回值回填/);
});
