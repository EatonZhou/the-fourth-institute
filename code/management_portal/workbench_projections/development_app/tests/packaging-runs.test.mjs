import test from 'node:test';
import assert from 'node:assert/strict';
import {newRunRequest, runPresentation, safeResultUrl} from '../src/packaging/release-runs.js';
import {releaseSession} from '../src/packaging/release-session.js';
import {releaseApi,releaseCandidates} from '../src/packaging/release-api.js';

test('candidate bridge resolves bound Lineage and preserves real project ownership',async()=>{
 const calls=[],sdk={context:async()=>({bindings:{packaging_system:{lineage_id:'lineage_ed619225d417'}}}),release:async(op,value)=>{calls.push(value);return {items:[{resource_id:'lineage_a',attributes:{project_ids:['project_a']}}]};}};
 const bridge=releaseCandidates(sdk);
 await bridge.hatg('GetLineage',{}, {binding:'packaging_system'});
 assert.deepEqual(calls[0].arguments,{lineage_id:'lineage_ed619225d417'});
 await assert.rejects(bridge.hatg('GetLineage',{}, {binding:'missing'}),/绑定/);
 const result=await bridge.hatg('SearchResources',{resource_type:'lineage'});
 assert.deepEqual(result.items[0].project,['project_a']);
});

test('service adapter uses server identities and the closed candidate operation',async()=>{
 const calls=[],sdk={release:async(operation,args)=>{
   calls.push({operation,args});
   if(operation==='configs.save')return {config:{config_id:'actual_config',revision:1,name:args.name,snapshot:args.snapshot}};
   if(operation==='runs.start')return {run:{run_id:'actual_run',state:'checking',mode:'full_test',supervisor_resident_id:'resident_e4f55ad12dae',result:{instance_url:'http://localhost:18100/',manifest:{schema:'tfi.distribution.v1'}},stages:[{stage:'installation',evidence:{status:'installed'}}]}};
   return {types:[]};
 }};
 const api=releaseApi(sdk),config=await api.saveConfig({name:'A',checklist:{source:'Dev'},config_id:'client-placeholder',request_key:'save-key'});
 assert.equal(config.config_id,'actual_config');assert.equal(calls[0].args.config_id,'client-placeholder');assert.deepEqual(config.checklist,{source:'Dev'});
 const run=await api.startRun({request_key:'run-key',config_id:config.config_id,checklist_snapshot:config.checklist,mode:'full_test',validate_tau:false,request_text:'original'});
 assert.equal(run.run_id,'actual_run');assert.equal(run.instance_url,'http://localhost:18100/');assert.equal(run.supervisor_resident_id,'resident_e4f55ad12dae');
 assert.deepEqual(calls[1].args,{request_key:'run-key',config_id:'actual_config',snapshot:{source:'Dev'},mode:'full_test',tau_validation:false,provenance:{request_text:'original'}});
 await releaseCandidates(sdk).hatg('ListResourceTypes',{});
 assert.deepEqual(calls[2],{operation:'candidates',args:{operation:'ListResourceTypes',arguments:{}}});
});

test('active runs have independent identities and immutable retry payloads',()=>{
  const config={config_id:'config_a',revision:3,checklist:{version:'same',notes:' original ',categories:{tests:[]}}};
  const prior={key:'packaging:wb:same',text:'old',message_id:'msg_old'};
  const original=structuredClone(prior);
  const a=newRunRequest({config,mode:'full_test',validateTau:true,requestText:'raw request'});
  const b=newRunRequest({config,mode:'full_test',validateTau:false,requestText:'raw request'});
  assert.notEqual(a.request_key,b.request_key);
  assert.equal(a.config_id,b.config_id);
  assert.equal(a.validate_tau,true);
  assert.deepEqual(a.checklist_snapshot.categories.tests,[]);
  config.checklist.notes='new notes';
  assert.equal(a.checklist_snapshot.notes,' original ');
  assert.deepEqual(JSON.parse(JSON.stringify(a)),a);
  assert.deepEqual(prior,original);
});

test('acceptance, installed and exit zero do not mean full test success',()=>{
  for(const state of ['queued','running','installed','needs_configuration','unknown']){
    assert.equal(runPresentation({mode:'full_test',state,exit_code:0}).success,false);
  }
  assert.equal(runPresentation({state:'succeeded',mode:'full_test',supervisor_resident_id:'resident_e4f55ad12dae'}).success,true);
  assert.equal(runPresentation({state:'failed',error:'install failed'}).success,false);
  assert.throws(()=>newRunRequest({config:{config_id:'a',revision:1,checklist:{}},mode:'arbitrary'}));
});

test('artifact URLs reject script, credentials and non-http schemes',()=>{
  for(const value of ['javascript:alert(1)','data:text/html,x','//evil.test','https://u:p@host/','file:///etc/passwd'])assert.equal(safeResultUrl(value),null);
  assert.equal(safeResultUrl('http://127.0.0.1:18100/'),'http://127.0.0.1:18100/');
  assert.equal(safeResultUrl('/resource/knowledge?item=decision_123'),'/resource/knowledge?item=decision_123');
});

test('configuration edits, save as, lost receipts and restored retries',async()=>{
  let state={},draft={version:'v1',notes:'original',feature_states:{harness_recording:{included:true,default_enabled:false},telepus:{included:false,default_enabled:null}}},lost=false,starts=0;
  const configs=new Map(),runs=new Map();
  const api={
    saveConfig:async request=>{
      const config_id=request.config_id||'server_config_'+configs.size;
      const previous=configs.get(config_id);
      if(previous&&JSON.stringify(previous.checklist)===JSON.stringify(request.checklist)&&previous.name===request.name)return structuredClone(previous);
      const result={config_id,name:request.name,checklist:structuredClone(request.checklist)};
      configs.set(result.config_id,result);return result;
    },
    config:async id=>{const config=structuredClone(configs.get(id));config.checklist=Object.fromEntries(Object.entries(config.checklist).reverse());return config;},
    startRun:async request=>{
      if(!runs.has(request.request_key)){starts++;runs.set(request.request_key,{...structuredClone(request),run_id:'server_'+starts,state:'queued'});}
      if(lost){lost=false;throw Error('lost receipt');}return structuredClone(runs.get(request.request_key));
    },run:async id=>runs.get(id),
  };
  const make=()=>releaseSession({api,readState:()=>state,writeState:async v=>{state=structuredClone(v);},collect:()=>draft,requestText:v=>JSON.stringify(v)});
  let session=make();const a=await session.save('A');draft.notes='edited';
  await session.save('A');assert.equal(state.config.config_id,a.config_id);assert.equal(state.config.revision,undefined);
  const b=await session.save('B',{copy:true});assert.notEqual(b.config_id,a.config_id);assert.deepEqual(b.checklist.feature_states,draft.feature_states);
  draft=await session.load(a.config_id);assert.equal(draft.notes,'edited');assert.deepEqual(draft.feature_states,b.checklist.feature_states);
  lost=true;await assert.rejects(session.start('full_test',true),/lost receipt/);
  const pending=structuredClone(state.pending_runs[0]);draft.notes='changed after timeout';
  session=make();const retry=await session.retry(pending.request_key);assert.equal(starts,1);
  assert.equal(retry.checklist_snapshot.notes,'edited');assert.equal(retry.validate_tau,true);assert.deepEqual(retry.checklist_snapshot.feature_states,b.checklist.feature_states);
  assert.deepEqual(state.pending_runs,[]);
  await assert.rejects(session.start('publish',false),/先保存/);
  await session.save('A');const next=await session.start('publish',false);
  assert.notEqual(next.run_id,retry.run_id);assert.equal(starts,2);
});

test('lost configuration save retries the creation identity; saves replace current content',async()=>{
  let state={},draft={notes:'unfinished'},lost=true;
  const configs=new Map(),requests=[],receipts=new Map();
  const api={saveConfig:async request=>{
    requests.push(structuredClone(request));
    if(receipts.has(request.request_key))return receipts.get(request.request_key);
    const config_id=request.config_id||'server_config_'+configs.size;
      const previous=configs.get(config_id);
    if(previous&&JSON.stringify(previous.checklist)===JSON.stringify(request.checklist))return previous;
    const result={...request,config_id,checklist:structuredClone(request.checklist)};configs.set(config_id,result);receipts.set(request.request_key,result);
    if(lost){lost=false;throw Error('receipt lost');}return result;
  }};
  const make=()=>releaseSession({api,readState:()=>state,writeState:async value=>{state=structuredClone(value);},collect:()=>draft,requestText:JSON.stringify});
  await assert.rejects(make().save('draft'),/receipt lost/);
  const result=await make().save('draft');
  assert.equal(configs.size,1);assert.deepEqual(requests[0],requests[1]);assert.equal(state.pending_config,null);
  configs.set(result.config_id,{...result,revision:3});draft.notes='local edit';
  const updated=await make().save('renamed');
  assert.equal(updated.checklist.notes,'local edit');
  assert.equal(configs.size,1);assert.equal(updated.config_id,result.config_id);
  assert.equal(state.config.revision,undefined);assert.equal(state.pending_config,null);
  assert.equal(requests.at(-1).expected_revision,undefined);
});

test('a pending save cannot update a different loaded configuration',async()=>{
  let state={config:{config_id:'A',revision:1}},lost=true;
  const requests=[];
  const api={
    config:async id=>({config_id:id,revision:1,name:'same',checklist:{notes:'same'}}),
    saveConfig:async request=>{requests.push(request);if(lost){lost=false;throw Error('lost receipt');}return {...request,revision:2};},
  };
  const session=releaseSession({api,readState:()=>state,writeState:async value=>{state=value;},collect:()=>({notes:'same'}),requestText:JSON.stringify});
  await assert.rejects(session.save('same'),/lost receipt/);
  await session.load('B');await session.save('same');
  assert.equal(requests[0].config_id,'A');assert.equal(requests[1].config_id,'B');
  assert.notEqual(requests[0].request_key,requests[1].request_key);
});

test('server frozen dependencies do not look like edits and remain on the run request',async()=>{
 let draft={version:'v54',notes:'same'},state={config:{config_id:'A',revision:2}},request;
 const frozen={schema:'tfi.release-resource-dependencies.v1',projections:[{id:'wb',revision:7}]};
 const api={config:async()=>({config_id:'A',revision:2,checklist:{...draft,resource_dependencies:frozen}}),startRun:async value=>{request=value;return {run_id:'controlled'};}};
 const session=releaseSession({api,readState:()=>state,writeState:async value=>{state=value;},collect:()=>draft,requestText:JSON.stringify});
 await session.start('publish',false);assert.deepEqual(request.checklist_snapshot.resource_dependencies,frozen);
 assert.equal(request.use_config_snapshot,true);
 const fixed=await api.config();api.config=async()=>fixed;draft={...draft,notes:'modified'};
 await assert.rejects(session.start('publish',false),/先保存/);
});

test('new config runs use server frozen snapshot; older pending transports remain unchanged',async()=>{
 const calls=[],api=releaseApi({release:async(operation,args)=>{calls.push(args);return {run:{run_id:'mock'}};}});
 const request={request_key:'key',config_id:'A',config_revision:2,checklist_snapshot:{resource_types:[]},mode:'publish'};
 await api.startRun({...request,use_config_snapshot:true});assert(!Object.hasOwn(calls[0],'snapshot'));assert.equal(calls[0].config_id,'A');
 await api.startRun(request);assert.deepEqual(calls[1].snapshot,request.checklist_snapshot);assert.equal(calls[1].provenance.config_revision,2);
});
