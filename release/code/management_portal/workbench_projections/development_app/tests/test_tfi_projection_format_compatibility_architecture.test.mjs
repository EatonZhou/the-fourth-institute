import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { gunzipSync } from 'node:zlib';
import { createHash } from 'node:crypto';
import { fixture } from './test_tfi_projection_format_compatibility_architecture_fixtures.mjs';
import { createEvidenceLoader } from '../src/architecture/data.js';
import { DISCOVERY_SCHEMA, supplementEntryMatches, verificationMatches } from '../src/architecture/discovery.js';
import { validateExplorationModel } from '../src/architecture/exploration-model.js';
import { validateMap, evolutionBinding, directionSelection, MAP_REF, EVOLUTION_REF, MANIFEST_REF } from '../src/architecture/model.js';
import { selectExplorationDirection } from '../src/architecture/exploration-directions.js';
import { readContent, CONTENT_SCHEMA } from '../src/architecture/instruction-workbench.model.js';
import { createExecutionSimulation } from '../src/architecture/execution-workbench.model.js';
import { createResourceSimulation } from '../src/architecture/resource-mechanisms.model.js';
import { createLineageSimulation } from '../src/architecture/lineage-mechanisms.model.js';

const tag = (name, prefix = 'tfi', version = 1) => `${prefix}.${name}.v${version}`;
const next = value => value.replace(/^tfi\./, 'archive.');
const badTags = value => [next(value).replace(/\.v\d+$/, '.v999'), next(value) + ' ', ' ' + next(value), next(value).toUpperCase(), 'other.' + value.split('.').slice(1).join('.'), null, {}];
const hash = text => createHash('sha256').update(text).digest('hex');
const source = name => JSON.parse(gunzipSync(readFileSync(new URL(`./fixtures/frozen-exploration/${name}.json.gz`, import.meta.url))));
const model = source('model'), lineage = source('lineage-model');
const content = (m, id) => ({...structuredClone(m), mechanism: structuredClone(m.mechanisms.find(x => x.id === id)), batchKey: 'test:fixed-batch'});
const outcome = async (run, rejects) => rejects ? assert.rejects(async () => run()) : run();

// Documentation admission no longer uses historical schema certificates.
// Rendering formats require exact canonical tags.

test('model and instruction workbench preserve accepted input tags and strict fields', async()=>{
  const catalog=JSON.parse(readFileSync(new URL('./fixtures/instruction-catalog.json',import.meta.url),'utf8'));
  for(const [original,run,expected] of [[model,v=>{const r=validateExplorationModel(v);if(!r.valid)throw Error(JSON.stringify(r.errors));return v;},model.schema],
    [catalog,readContent,catalog.schema],[{schema:tag('portal-architecture'),modules:[],protocols:[]},validateMap,tag('portal-architecture')]]) {
    for(const actual of [expected,next(expected),...badTags(expected)]) {
      const value=structuredClone(original);value.schema=actual;const before=JSON.stringify(value);
      const result=await outcome(()=>run(value),actual!==expected);
      if(result)assert.deepEqual(result.schema,actual);assert.equal(JSON.stringify(value),before);
    }
  }
  const broken=structuredClone(catalog);broken.catalog.instructions[0].owner='wrong';assert.throws(()=>readContent(broken));
  const wrong=structuredClone(model);wrong.revision=2;assert.equal(validateExplorationModel(wrong).valid,false);
});

test('state restoration accepts only the exact version; batch, adapter and replay remain strict',async()=>{
  const c=content(model,'execution.lifecycle'), expected=tag('execution-workbench-state');
  const sim=createExecutionSimulation(c), saved=sim.getViewState();
  assert.equal(saved.schema,expected);
  for(const actual of [expected,next(expected),...badTags(expected)]) {
    const value={...saved,schema:actual}, before=JSON.stringify(value);
    await outcome(()=>sim.restore(value),actual!==expected);
    assert.equal(JSON.stringify(value),before);assert.equal(sim.getViewState().schema,expected);
  }
  for(const field of ['batchKey','mechanismId'])assert.throws(()=>sim.restore({...saved,[field]:'wrong'}));
  assert.throws(()=>sim.restore({...saved,prefix:['not-a-real-step']}));
  assert.throws(()=>sim.restore({...saved,adapter:'tfi.execution.lifecycle.v1'}));
});

test('resource and packet teaching graphs read canonical schemas and reject different adapter identities',async()=>{
  for(const [m,id,field,expected,run] of [
    [model,'resources.mechanism.storage.workspace.atomic-files','state_model.schema',tag('architecture.simulation-state'),createResourceSimulation],
    [model,'resources.mechanism.resources.permissions.coverage','state_model.teaching_graph_schema',tag('permission-teaching-graph'),createResourceSimulation],
    [lineage,'mechanism.lineage.packet-operators','teaching_graph.schema',tag('lineage-packet-teaching-graph'),createLineageSimulation]]) {
    for(const actual of [expected,next(expected),...badTags(expected)]) {
      const c=content(m,id),[a,b]=field.split('.');c.mechanism[a][b]=actual;const before=JSON.stringify(c);
      await outcome(()=>run(c),actual!==expected);assert.equal(JSON.stringify(c),before);
    }
    const c=content(m,id);c.mechanism.adapter=next(c.mechanism.adapter)+'-changed';assert.throws(()=>run(c));
  }
});

test('historical resource adapters are rejected without mutating their identity',()=>{
  const c=content(model,'resources.mechanism.storage.workspace.atomic-files');
  c.mechanism.adapter=c.mechanism.adapter.replace(/^tfi\./,'archive.');
  const before=JSON.stringify(c);
  assert.throws(()=>createResourceSimulation(c),/resource_adapter_unsupported/);
  assert.equal(JSON.stringify(c),before);
});

test('unversioned or historical evolution formats never inherit v1 binding semantics',()=>{
  const data = {evolutionAvailable:true, modelVersion:1, mapSchema:'tfi.portal-architecture.v1'};
  assert.equal(evolutionBinding({...data,evolutionSchema:'tfi.portal-evolution.v1'}).valid,true);
  for (const evolutionSchema of [undefined, null, 'archive.portal-evolution.v1', 'archive.portal-evolution.v2', 'tfi.portal-evolution.v3', 'tfi.portal-evolution.v1\n']) {
    assert.equal(evolutionBinding({...data,evolutionSchema}).valid,false);
  }
  assert.equal(evolutionBinding({...data,mapSchema:'archive.portal-architecture.v1',evolutionSchema:'tfi.portal-evolution.v1'}).valid,false);
});

test('evolution tags trigger versioned semantics while binding schema remains raw equality',()=>{
  for(const prefix of ['tfi']) {
    const data={mode:'current',evolutionAvailable:true,evolutionSchema:tag('portal-evolution',prefix,2),modelVersion:1,
      mapSchema:tag('portal-architecture',prefix),mapHash:'a'.repeat(64),moduleMap:{ref:MAP_REF,schema:tag('portal-architecture',prefix),model_version:1,revision:'a'.repeat(64)}};
    assert.equal(evolutionBinding(data).valid,true);
    data.moduleMap.schema=tag('portal-architecture',prefix==='tfi'?'archive':'tfi');
    assert.equal(evolutionBinding(data).valid,false);
    const direction={id:'test',modules:['root'],plans:[]};
    const d={...data,moduleMap:null,evolutionRef:EVOLUTION_REF,directions:[direction],plans:[{id:'p',directions:['test'],modules:['root']}],
      modules:[{id:'root'}],evolutionAnchors:{directions:{test:1}}};
    const semantic={ref:EVOLUTION_REF,source:EVOLUTION_REF,kind:'direction:test',status:'proposed',observed_at:'test'};
    assert.equal(directionSelection(d,semantic).data.plans.length,0);
    const evolution={schema:tag('portal-evolution',prefix,2),directions:[direction],module_map:{revision:'a'.repeat(64),model_version:2}};
    const bundle={mode:'current',batchKey:'test',binding:{module_map:{sha256:'a'.repeat(64)}},model:{objects:[{id:'root',kind:'module'}]}};
    assert.equal(!!selectExplorationDirection(bundle,evolution,semantic),true);
    evolution.schema=tag('portal-evolution',prefix,3);assert.equal(selectExplorationDirection(bundle,evolution,semantic),null);
  }
});

test('frozen component supplement and verification retain exact descriptor/revision bindings',()=>{
  const entry=JSON.parse(readFileSync(new URL('./fixtures/architecture-component-relations-v2-manifest.json',import.meta.url)));
  const body=JSON.parse(readFileSync(new URL('./fixtures/architecture-component-relations-v2-body.json',import.meta.url)));
  for(const target of ['entry','body'])for(const actual of [tag(target==='entry'?'architecture-component-relations-manifest':'architecture-component-relations'),
    next(target==='entry'?entry.schema:body.schema),...badTags(target==='entry'?entry.schema:body.schema)]) {
    const e=structuredClone(entry),b=structuredClone(body);(target==='entry'?e:b).schema=actual;
    const expected=tag(target==='entry'?'architecture-component-relations-manifest':'architecture-component-relations');
    assert.equal(supplementEntryMatches(e,b),actual===expected);
  }
  const changed=structuredClone(entry);changed.map.size++;assert.equal(supplementEntryMatches(changed,body),false);
  const d={address:'test:base',sha256:'a'.repeat(64),size:1},evidence={pinned:d,current_commit:'b'.repeat(40)};
  const expected=tag('architecture-component-verification');
  for(const actual of [expected,next(expected),...badTags(expected)]) {
    const report={schema:actual,revision:1,base_manifest:d,commit:evidence.current_commit,template_manifest:d,status:'missing',reason_code:'test',subject:'test',action:'test'};
    assert.equal(verificationMatches(report,evidence),actual===expected);
    report.commit='c'.repeat(40);assert.equal(verificationMatches(report,evidence),false);
  }
});

test('current evidence loader recognizes tfi evolution v2 without normalizing map binding',async()=>{
  for(const prefix of ['tfi'])for(const mismatch of [false,true]) {
    const f=fixture(), map={schema:tag('portal-architecture',prefix),model_version:1,modules:[{id:'root'}],protocols:[]};
    const text=JSON.stringify(map);f.texts.set(MAP_REF,text);
    const evolution={schema:tag('portal-evolution',prefix,2),module_map:{ref:MAP_REF,schema:tag('portal-architecture',mismatch?(prefix==='tfi'?'archive':'tfi'):prefix),model_version:1,revision:hash(text)},
      plans:[{id:'plan:test',modules:['root']}]};f.texts.set(EVOLUTION_REF,JSON.stringify(evolution));
    const before=[...f.texts],result=await createEvidenceLoader({resources:f.resources}).current({snapshots:[]});
    assert.equal(result.mapSchema,map.schema);assert.equal(result.moduleMap.schema,evolution.module_map.schema);
    assert.equal(result.evolutionMappingValid,!mismatch);assert.deepEqual(result.plans[0].modules,mismatch?[]:['root']);
    assert.deepEqual([...f.texts],before);assert.deepEqual(f.writes,[]);
  }
});
