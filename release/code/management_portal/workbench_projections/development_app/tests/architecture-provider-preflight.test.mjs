import test from 'node:test';
import assert from 'node:assert/strict';
import {createProviderPreflightSimulation,supportsProviderPreflight,getProviderPreflightEntry,getProviderPreflightModes,PREFLIGHT_ID,PREFLIGHT_ADAPTER} from '../src/architecture/provider-preflight.model.js';
import {supportsProviderMechanisms,createProviderSimulation,getProviderDelegatedModes} from '../src/architecture/provider-mechanisms.model.js';
import {readProviderFeedbackFixture} from './architecture-provider-feedback.fixture.mjs';
const enabled=!!process.env.PROVIDER_FEEDBACK_FACTS;
const f=enabled?readProviderFeedbackFixture():null;
const content=(objectId='provider-capabilities',modes=false)=>f.content(PREFLIGHT_ID,objectId,modes);
function run(id){const s=createProviderPreflightSimulation(content());assert.ok(s.chooseCase(id));while(s.getScene().next)assert.ok(s.perform(s.getScene().next.id));return s;}
test('parent opaque paired batch token mounts and restores only by full-string identity', {skip:!enabled},()=>{
  const input=content(),pins=['base:fixed','consumer:fixed',input.batchKey,f.provenance.modelSha];
  const token=JSON.stringify(pins),sim=createProviderPreflightSimulation({...input,batchKey:token});
  sim.chooseCase('requirements_second_alternative');assert.ok(sim.perform(sim.getScene().next.id));
  const saved=sim.getViewState(),scene=sim.getScene();assert.equal(saved.batchKey,token);
  const restored=createProviderPreflightSimulation({...input,batchKey:token});assert.ok(restored.restoreViewState(saved));assert.deepEqual(restored.getScene(),scene);
  for(const other of [JSON.stringify(['base:changed',...pins.slice(1)]),JSON.stringify([...pins.slice(0,3),'model:changed']),token+' ']){
    const mismatch=createProviderPreflightSimulation({...input,batchKey:other}),before=mismatch.getScene();
    assert.equal(mismatch.restoreViewState(saved),false);assert.deepEqual(mismatch.getScene(),before);
  }
  const opaque='parent-token:not-json/not-a-sha';assert.equal(createProviderPreflightSimulation({...input,batchKey:opaque}).getViewState().batchKey,opaque);
  for(const invalid of ['',null,undefined,42])assert.throws(()=>createProviderPreflightSimulation({...input,batchKey:invalid}),/batch_missing/);
});
test('preflight has its own exact identity and factory, never disguises itself as negotiate',()=>{
  const m={id:PREFLIGHT_ID,adapter:PREFLIGHT_ADAPTER,mode:'simulated'};
  assert.equal(supportsProviderPreflight(m),true);assert.equal(supportsProviderMechanisms(m),false);
  assert.throws(()=>createProviderSimulation({mechanism:m}),/unsupported_adapter/);
  for(const wrong of [{...m,id:'provider-capabilities.negotiate'},{...m,adapter:'constructor'},{...m,mode:'live'},{}])assert.equal(supportsProviderPreflight(wrong),false);
});
test('preflight replays every declared path with typed state and exact evidence; snapshots cannot inject or cross batches', {skip:!enabled},()=>{
  const input=content(),copy=structuredClone(input);
  for(const c of input.mechanism.cases){
    const sim=run(c.id),scene=sim.getScene();assert.deepEqual(scene.trace.map(s=>s.id),c.path);assert.equal(scene.next,null);
    assert.deepEqual(scene.outcome,c.outcome);assert.equal(sim.perform(c.path[0]),false);
    assert.ok(sim.restoreViewState({...sim.getViewState(),state:{native_call_count:999}}));assert.deepEqual(sim.getScene(),scene);
    assert.equal(sim.restoreViewState({...sim.getViewState(),batchKey:'0'.repeat(64)}),false);assert.deepEqual(sim.getScene(),scene);
    sim.reset();assert.equal(sim.getScene().cursor,0);assert.equal(sim.getScene().state.native_call_count,0);
  }
  assert.deepEqual(input,copy);
  const field=structuredClone(input);field.mechanism.state_fields=field.mechanism.state_fields.filter(f=>f.id!=='alternative_checks');assert.throws(()=>createProviderPreflightSimulation(field),/domain_field_missing/);
  const writer=structuredClone(input);writer.mechanism.steps.find(s=>s.state_changes.length).state_changes[0].writer_id='absent';assert.throws(()=>createProviderPreflightSimulation(writer),/writer_unavailable|change_authority/);
});
test('preflight evaluates AND groups then OR, reports first missing alternative and never invokes a provider', {skip:!enabled},()=>{
  for(const [id,index] of [['requirements_first_alternative',0],['requirements_second_alternative',1]]){
    const s=run(id).getScene().state;assert.equal(s.feature_id,'inline_media_history');assert.deepEqual(s.requirement_alternatives,[['portal_tools'],['input_image_detail']]);
    assert.deepEqual(s.satisfied_alternative_indices,[index]);assert.equal(s.alternative_checks[index].satisfied,true);assert.equal(s.feature_status,'eligible');assert.equal(s.native_call_count,0);assert.equal(s.isa_call_count,0);
  }
  const missing=run('requirements_missing').getScene().state;assert.ok(missing.alternative_checks.every(a=>!a.satisfied));assert.equal(missing.unmet_requirement,'requires_portal_tools');assert.equal(missing.feature_status,'skipped');
  const compaction=run('requirements_compaction_missing').getScene().state;assert.equal(compaction.unmet_requirement,'requires_auto_compact_threshold');assert.equal(compaction.native_call_count,0);
  assert.ok(missing.requirement_catalog.every(r=>r.alternatives.every(group=>group.length===1)),'No invented multi-capability registered group');
});
test('preflight separates optional feature rejection, main operation, account gaps, schema fixtures and ISA', {skip:!enabled},()=>{
  const optional=run('optional').getScene().state;assert.equal(optional.feature_status,'skipped');assert.equal(optional.native_call_count,1);
  assert.equal(run('supported').getScene().state.native_call_count,1);
  for(const id of ['unsupported','setting']){const s=run(id).getScene().state;assert.equal(s.validation,'rejected');assert.equal(s.native_call_count,0);}
  const gap=run('catalog-gap').getScene().state;assert.equal(gap.account_observation.model_catalog_status,'not_captured');assert.equal(gap.validation,'blocked_missing_catalog');assert.equal(gap.native_call_count,0);
  for(const id of ['model','effort','tier']){const s=run(id).getScene().state;assert.equal(s.catalog_origin,'schema_fixture');assert.equal(s.validation,'rejected');assert.equal(s.native_call_count,0);}
  const p=run('permissions').getScene().state;assert.equal(p.native_permission_preset,'full-access');assert.equal(p.isa_authorization,'not_evaluated');assert.equal(p.isa_call_count,0);
});
test('preflight object entries preserve composition/catalog defaults and delegate exact same-object authorized modes', {skip:!enabled},()=>{
  for(const id of ['provider-capabilities','execution.providers.catalog','execution.providers']){
    const entry=getProviderPreflightEntry(id),object=f.model.objects.find(o=>o.id===id);
    for(const mode of getProviderPreflightModes(content(id,true),id))assert.ok(object.mechanism_ids.includes(mode.mechanismId));
    if(entry.defaultMechanismId)assert.ok(object.mechanism_ids.includes(entry.defaultMechanismId));
    assert.equal(entry.kind==='composition',object.child_ids.length>0);
  }
  assert.equal(getProviderPreflightEntry('provider-capabilities').defaultMechanismId,PREFLIGHT_ID);
  assert.equal(getProviderPreflightEntry('execution.providers.catalog').defaultMechanismId,'provider.catalog-settings');
  assert.equal(getProviderPreflightEntry('execution.providers').defaultMechanismId,null);
  assert.equal(getProviderPreflightEntry('provider-capabilities.unknown'),null);
  const c=content('provider-capabilities',true),modes=getProviderPreflightModes(c,'provider-capabilities');
  assert.deepEqual(modes.map(m=>m.mechanismId),['provider-capabilities.negotiate','execution.lifecycle']);
  for(const m of modes)assert.deepEqual(m.route,{kind:'protocol',objectId:'provider-capabilities',mechanismId:m.mechanismId});
  assert.deepEqual(getProviderPreflightModes({...c,mechanisms:[]},'provider-capabilities'),[]);
  assert.deepEqual(getProviderPreflightModes({...c,evidence:[]},'provider-capabilities'),[]);
  assert.ok(getProviderDelegatedModes(c,'provider-capabilities').some(m=>m.id===PREFLIGHT_ID));
  const catalog=getProviderPreflightModes(content('execution.providers.catalog',true),'execution.providers.catalog');assert.deepEqual(catalog.map(m=>m.mechanismId),['provider.catalog-settings']);
});
