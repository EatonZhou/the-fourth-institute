import test from 'node:test';
import assert from 'node:assert/strict';
import {createWebSimulation, WEB_ADAPTERS} from '../src/architecture/web-mechanisms.model.js';
import {WEB_VIEWS} from '../src/architecture/web-mechanisms.views.js';
import {WEB_OBJECT_ENTRIES, getWebObjectModes, resolveWebObjectEntry} from '../src/architecture/web-mechanisms.entries.js';

// Contract fixture: independent of live research, SDK and source authorization.
function fixture() {
  return {batchKey:'fixed-test-batch',objects:[{id:'interface.conversation',mechanism_ids:['web.m.resident-cursors']}],evidence:[{id:'e'}],mechanism:{
    id:'web.m.resident-cursors',adapter:'portal.architecture.resident-cursors.v1',owner_id:'interface.conversation',mode:'simulated',evidence_ids:['e'],
    input_schema:{type:'object',properties:{state_cursor:{type:'integer'}},required:['state_cursor'],additionalProperties:false},
    state_fields:[{id:'conversation_cursor',type:'integer',initial:10},{id:'state_cursor',type:'integer',initial:10},{id:'snapshot_reads',type:'integer',initial:0},{id:'applied',type:'array',initial:[]}],
    participants:[{object_id:'interface.conversation'}],
    steps:[
      {id:'ack',from:'interface.conversation',to:'interface.conversation',state_changes:[{field:'state_cursor',op:'set',value:{input:'state_cursor'}}],next:['message','gap'],evidence_ids:['e']},
      {id:'message',from:'interface.conversation',to:'interface.conversation',state_changes:[{field:'conversation_cursor',op:'increment',value:1},{field:'applied',op:'append',value:'cursor11'}],next:[],evidence_ids:['e']},
      {id:'gap',from:'interface.conversation',to:'interface.conversation',state_changes:[{field:'snapshot_reads',op:'increment',value:1}],next:[],evidence_ids:['e']},
    ],
    cases:[{id:'continuous',inputs:{state_cursor:12},path:['ack','message'],evidence_ids:['e']},{id:'gap',inputs:{state_cursor:12},path:['ack','gap'],evidence_ids:['e']}],
  }};
}
test('early state receipt cannot skip conversation events; backward replay restores both cursors',()=>{
  const input=fixture(),sim=createWebSimulation(input);
  sim.advance(); assert.deepEqual(sim.getScene().state,{conversation_cursor:10,state_cursor:12,snapshot_reads:0,applied:[]});
  sim.advance();assert.equal(sim.getScene().state.conversation_cursor,11);assert.equal(sim.getScene().state.state_cursor,12);
  sim.previous();assert.equal(sim.getScene().state.conversation_cursor,10);
  assert.equal(input.mechanism.state_fields[1].initial,10);
});
test('gap retains known messages and requests a snapshot without faking later message content',()=>{
  const sim=createWebSimulation(fixture());sim.choose('gap');sim.advance();sim.advance();
  assert.equal(sim.getScene().state.conversation_cursor,10);assert.equal(sim.getScene().state.snapshot_reads,1);assert.deepEqual(sim.getScene().state.applied,[]);
});
test('restored state is replayed from fixed case; injected state cannot override facts and another batch is refused',()=>{
  const sim=createWebSimulation(fixture());sim.advance();const state=sim.getViewState();
  sim.reset();sim.restore({...state,state:{conversation_cursor:9000}});assert.equal(sim.getScene().state.conversation_cursor,10);
  assert.throws(()=>sim.restore({...state,batchKey:'new-batch'}),/invalid_restore/);
  assert.throws(()=>sim.restore({...state,position:99}),/invalid_restore/);
});
test('missing evidence or hidden participant is rejected before rendering',()=>{
  const data=fixture();data.evidence=[];assert.throws(()=>createWebSimulation(data),/evidence_unavailable/);
  const other=fixture();other.mechanism.participants.push({object_id:'hidden'});assert.throws(()=>createWebSimulation(other),/participant_unavailable/);
});
test('unknown operations, type-changing writes and invalid case jumps fail closed',()=>{
  const a=fixture();a.mechanism.steps[0].state_changes[0].op='delete';assert.throws(()=>createWebSimulation(a),/unsupported_change/);
  const b=fixture();b.mechanism.steps[0].state_changes[0].value='twelve';assert.throws(()=>createWebSimulation(b),/invalid_change_type/);
  const c=fixture();c.mechanism.steps[0].next=[];assert.throws(()=>createWebSimulation(c),/invalid_case_path/);
});
test('case conditions use declared state, never implicit input lookups',()=>{
  const a=fixture();a.mechanism.steps[1].when={field:'state_cursor',equals:13};assert.throws(()=>createWebSimulation(a),/case_guard_failed/);
  a.mechanism.steps[1].when={field:'unknown_input',equals:12};assert.throws(()=>createWebSimulation(a),/unknown_condition_field/);
});
test('unknown input types and unsupported adapters do not use a generic interpretation',()=>{
  const a=fixture();a.mechanism.cases[0].inputs.state_cursor='12';assert.throws(()=>createWebSimulation(a),/invalid_input/);
  const b=fixture();b.mechanism.adapter='portal.architecture.new.v1';assert.throws(()=>createWebSimulation(b),/unsupported_adapter/);
  assert.equal(WEB_ADAPTERS.some(x=>/projection|isa-discovery/.test(x)),false);
  assert.ok(WEB_ADAPTERS.includes('portal.architecture.isa-commit-replay.v1'));
  assert.ok(WEB_ADAPTERS.includes('portal.architecture.backup-receipt.v1'));
});

test('backup receipt and ISA replay show different durable records, not shared success states',()=>{
  const b=WEB_VIEWS['backup-receipt']({state:{triggered:true,accepted_effect:true,backup_success:false,status:'queued'},inputs:{available:true,active:false},prefix:[]});
  assert.match(b,/成功快照/);assert.match(b,/没有确认新的成功恢复点/);assert.match(b,/已排队/);
  const isa=WEB_VIEWS['isa-commit-replay']({state:{handler_runs:1,committed:true,projection:'pending',result:'projection_pending'},inputs:{},prefix:['enter','commit','pending']});
  assert.match(isa,/业务已提交/);assert.match(isa,/投影暂未完成/);assert.match(isa,/领域处理次数/);
});
test('domain outputs preserve different reader-visible causal structures and escape input text',()=>{
  const sim=createWebSimulation(fixture());const cursor=WEB_VIEWS['resident-cursors'](sim.getScene());assert.match(cursor,/已连续读取的消息/);assert.match(cursor,/已接受的运行状态/);
  const doc=WEB_VIEWS['document-edit']({state:{draft:'<img onerror=bad>',dirty:true,saving:false,save_id:1,error:''},prefix:[]});
  assert.match(doc,/web-draft/);assert.doesNotMatch(doc,/<img/);
  const stream=WEB_VIEWS['http-stream']({state:{last_cursor:'cursor-1',phase:'disconnected',authenticated:true,cancel_signalled:true,rollback_known:false},prefix:['request','authenticate','stream','cancel']});
  assert.match(stream,/连接中断/);assert.match(stream,/需要另读权威记录/);assert.doesNotMatch(stream,/业务已回滚/);
});

function entryFixture() {
  const data=fixture();
  // Minimal reference-validation contracts; these are not domain case examples.
  const mode=(key,title)=>({...structuredClone(data.mechanism),id:`web.m.${key}`,adapter:`portal.architecture.${key}.v1`,title});
  data.mechanisms=[mode('document-edit','保存与旧回执'),mode('document-navigation','打开与预览')];
  delete data.mechanism;
  data.objects.push({id:'interface.content.navigation',kind:'concept',child_ids:[],mechanism_ids:['web.m.document-edit','web.m.document-navigation']});
  return data;
}
test('explicit object entry ignores mechanism ordering and does not claim shared-adapter objects',()=>{
  const data=entryFixture(),route=resolveWebObjectEntry('interface.content.navigation',data);
  assert.equal(route.defaultMechanismId,'web.m.document-navigation');
  assert.deepEqual(route.route,{kind:'mechanism',objectId:'interface.content.navigation',mechanismId:'web.m.document-navigation'});
  data.mechanisms.reverse();assert.deepEqual(resolveWebObjectEntry('interface.content.navigation',data),route);
  data.objects.push({id:'unowned.http',kind:'protocol',child_ids:[],mechanism_ids:['web.m.document-navigation']});
  assert.equal(resolveWebObjectEntry('unowned.http',data),null);
  assert.equal(resolveWebObjectEntry('toString',data),null);
  assert.equal(WEB_OBJECT_ENTRIES['projection-family'],undefined);
});
test('entry mode availability uses exact subject, evidence and defaults without alternative promotion',()=>{
  const data=entryFixture();assert.equal(getWebObjectModes('interface.content.navigation',data).length,2);
  data.mechanisms.find(m=>m.id==='web.m.document-navigation').evidence_ids=['hidden'];
  assert.deepEqual(getWebObjectModes('interface.content.navigation',data).map(m=>m.mechanismId),['web.m.document-edit']);
  assert.equal(resolveWebObjectEntry('interface.content.navigation',data),null);
  data.objects.pop();assert.deepEqual(getWebObjectModes('interface.content.navigation',data),[]);
});
test('non-leaf default remains composition even when mechanism data is unavailable',()=>{
  const data=entryFixture();data.objects.push({id:'interface.content',kind:'module',child_ids:['interface.content.navigation'],mechanism_ids:['web.m.document-edit']});
  data.mechanisms=[];
  assert.deepEqual(resolveWebObjectEntry('interface.content',data).route,{kind:'composition',objectId:'interface.content',mechanismId:null});
});
test('protocol and module using the same mechanism keep their own route object identity',()=>{
  const data=entryFixture(),m={...data.mechanisms[0],id:'web.m.http-stream',adapter:'portal.architecture.http-stream.v1'};
  data.mechanisms=[m];
  for(const [id,kind] of [['http-interface','protocol'],['interface.shell.api-client','concept']])data.objects.push({id,kind,child_ids:[],mechanism_ids:[m.id]});
  assert.deepEqual(resolveWebObjectEntry('http-interface',data).route,{kind:'protocol',objectId:'http-interface',mechanismId:m.id});
  assert.deepEqual(resolveWebObjectEntry('interface.shell.api-client',data).route,{kind:'mechanism',objectId:'interface.shell.api-client',mechanismId:m.id});
});
test('remembered named mode is restored only for the exact object, batch and valid replay position',()=>{
  const data=entryFixture(),objectId='interface.content.navigation';
  const sim=createWebSimulation({...data,mechanism:data.mechanisms.find(m=>m.id==='web.m.document-edit')});
  sim.advance();const saved={...sim.getViewState(),objectId};
  const entry=resolveWebObjectEntry(objectId,data,saved);
  assert.equal(entry.route.mechanismId,'web.m.document-edit');assert.equal(entry.initialState.position,1);
  for(const invalid of [{...saved,objectId:'interface.content.document-session'},{...saved,batchKey:'other'},{...saved,position:1000}]) {
    const rejected=resolveWebObjectEntry(objectId,data,invalid);
    assert.equal(rejected.route.mechanismId,'web.m.document-navigation');assert.equal(rejected.initialState,null);
  }
  data.mechanisms=data.mechanisms.filter(m=>m.id!=='web.m.document-edit');
  assert.equal(resolveWebObjectEntry(objectId,data,saved).route.mechanismId,'web.m.document-navigation');
});
