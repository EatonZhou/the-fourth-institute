import test from 'node:test';
import assert from 'node:assert/strict';
import {CATALOG_ID,TUPLE_ID,catalogAdapters,catalogObjectEntries,catalogObjectModes,createProviderCatalogSimulation,getProviderCatalogModes,catalogFieldObserved} from '../src/architecture/provider-catalog.model.js';
import {readCatalogFixture} from './architecture-provider-catalog.fixture.mjs';
const {model,content}=readCatalogFixture();
const input=id=>structuredClone({...content,mechanism:model.mechanisms.find(m=>m.id===id)});
function finish(id,caseId){const sim=createProviderCatalogSimulation(input(id));assert(sim.chooseCase(caseId));while(sim.getScene().next)sim.perform(sim.getScene().next.id);return sim.getScene();}
test('exact two adapters and explicit same-object default/modes survive supplied reordering',()=>{
  assert.deepEqual(Object.keys(catalogAdapters),[CATALOG_ID,TUPLE_ID]);
  assert.equal(catalogObjectEntries['execution.providers.catalog'],CATALOG_ID);
  const c=input(CATALOG_ID);c.mechanisms.reverse();c.objects.find(o=>o.id==='execution.providers.catalog').mechanism_ids.reverse();
  assert.deepEqual(getProviderCatalogModes(c,'execution.providers.catalog').map(m=>m.id),catalogObjectModes['execution.providers.catalog']);
  assert.deepEqual(getProviderCatalogModes(c,'execution.providers.catalog').map(m=>m.local),[true,true,false,false]);
  c.evidence=[];assert.deepEqual(getProviderCatalogModes(c,'execution.providers.catalog'),[]);
  assert.equal(catalogObjectEntries['execution.providers'],undefined);assert.equal(catalogObjectEntries['provider-capabilities'],undefined);
});
for(const id of [CATALOG_ID,TUPLE_ID])for(const scenario of input(id).mechanism.cases)test(`${id}/${scenario.id}: every frozen prefix and outcome`,()=>{
  const c=input(id),sim=createProviderCatalogSimulation(c);sim.chooseCase(scenario.id);
  const state=Object.fromEntries(c.mechanism.state_fields.map(f=>[f.id,structuredClone(f.initial)]));
  assert.deepEqual(sim.getScene().state,state);
  for(const stepId of scenario.path){
    const step=c.mechanism.steps.find(s=>s.id===stepId);assert.equal(sim.getScene().next.id,stepId);
    for(const change of step.state_changes){const value=change.value?.input?scenario.inputs[change.value.input]:change.value;
      if(change.op==='set')state[change.field]=value;else if(change.op==='append')state[change.field].push(value);else state[change.field]+=value;}
    assert(sim.perform(stepId));assert.deepEqual(sim.getScene().state,state);
    assert.equal(sim.getScene().state.live_observation,'unavailable');assert.equal(sim.getScene().state.native_turn_calls,0);
    assert(sim.getScene().evidence_ids.every(id=>content.evidence.some(e=>e.id===id)));
  }
  for(const [field,value]of Object.entries(scenario.outcome))assert.deepEqual(sim.getScene().state[field],value);
  assert.equal(sim.perform(undefined),false);assert.equal(sim.perform('invented'),false);
  const saved=sim.getViewState();assert(sim.back());assert(sim.restoreViewState(saved));
  for(const bad of [{...saved,batchKey:'b'.repeat(64)},{...saved,caseId:'invented'},{...saved,cursor:999}]){assert.equal(sim.restoreViewState(bad),false);assert.deepEqual(sim.getViewState(),saved);}
});
test('registration dispatch and cache key are specific to account, not route',()=>{
  const registry=finish(CATALOG_ID,'registered_routes').state;
  assert.deepEqual(registry.adapter_calls.map(c=>c.provider),['demo.local']);assert.equal(registry.catalog_accepted,false);
  for(const id of ['selected_account','default_account'])assert.equal(finish(CATALOG_ID,id).state.adapter_calls[0].provider,'codex');
  assert.deepEqual(finish(CATALOG_ID,'missing_account').state.adapter_calls,[]);
  for(const id of ['cache_hit','route_change_cached']){const s=finish(CATALOG_ID,id).state;assert(s.cache_hit);assert.equal(s.request_count,0);assert.equal(s.cache_key,'demo.account.A');}
  assert.equal(finish(CATALOG_ID,'route_change_cached').state.route_id,'demo.route.changed');
  assert.equal(finish(CATALOG_ID,'cache_expired').state.request_count,2);assert.equal(finish(CATALOG_ID,'other_account').state.cache_key,'demo.account.B');
});
test('pages never become a catalog until accepted, including failure after twenty pages',()=>{
  const sim=createProviderCatalogSimulation(input(CATALOG_ID));sim.chooseCase('pages');
  while(sim.getScene().next?.id!=='pages.3')sim.perform(sim.getScene().next.id);
  assert.equal(sim.getScene().state.observed_model_ids.length,2);assert.equal(sim.getScene().state.hidden_count,1);
  assert.equal(catalogFieldObserved(sim.getScene(),'catalog_accepted'),false);assert.deepEqual(sim.getScene().state.catalog,{});
  sim.perform('pages.3');assert.equal(sim.getScene().state.catalog.default_model,'demo.alpha');
  for(const id of ['repeated_cursor','page_limit','empty','request_error']){const s=finish(CATALOG_ID,id).state;assert.equal(s.catalog_accepted,false);assert.deepEqual(s.catalog,{});assert(s.error);}
  assert.equal(finish(CATALOG_ID,'page_limit').state.request_count,20);
});
test('explicit settings reject; implicit settings reconcile; model modalities do not infer native capability',()=>{
  for(const id of ['model_explicit','effort_explicit','tier_explicit','effort_undeclared','empty_catalog']){const s=finish(TUPLE_ID,id).state;assert.equal(s.accepted,false);assert.deepEqual(s.resolved,{});assert.equal(catalogFieldObserved(finish(TUPLE_ID,id),'native_image_input'),false);}
  assert.deepEqual(finish(TUPLE_ID,'implicit_reconcile').state.resolved,{model:'demo.alpha',reasoning_effort:'high',service_tier:'fast'});
  assert.deepEqual(finish(TUPLE_ID,'implicit_no_effort').state.resolved,{model:'demo.beta',reasoning_effort:'',service_tier:''});
  const valid=finish(TUPLE_ID,'valid');assert.deepEqual(valid.state.selected_model_modalities,['text','image']);assert.equal(catalogFieldObserved(valid,'native_image_input'),false);
});
test('media derivation preserves independent capabilities and never claims ISA authorization',()=>{
  for(const id of ['media_supported','media_route_denied','media_unknown_route','media_disabled','media_transport','media_no_route_catalog']){
    const scene=finish(TUPLE_ID,id),s=scene.state;assert.equal(s.isa_authorization,'not_evaluated');assert.equal(catalogFieldObserved(scene,'accepted'),false);
    assert(s.effective_harness_capabilities.includes('portal_tools'));assert(s.effective_harness_capabilities.includes('active_turn_input'));
    assert.equal(s.native_image_input,['media_supported','media_no_route_catalog'].includes(id));
  }
  for(const id of ['media_disabled','media_transport','media_no_route_catalog'])assert.deepEqual(finish(TUPLE_ID,id).state.route_catalog_calls,[]);
});
test('unavailable evidence, forged adapter and invalid paths fail without interpreting prose guards',()=>{
  const valid=input(TUPLE_ID);for(const s of valid.mechanism.steps)s.guard='unparsed natural language';assert.doesNotThrow(()=>createProviderCatalogSimulation(valid));
  for(const mutate of [c=>c.evidence=[],c=>c.mechanism.adapter='provider.runtime-catalog.v1',c=>c.mechanism.cases[0].path=['unknown-step']]){
    const c=input(TUPLE_ID);mutate(c);assert.throws(()=>createProviderCatalogSimulation(c));
  }
});
