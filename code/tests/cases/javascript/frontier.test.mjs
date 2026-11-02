import {readFileSync} from 'node:fs';
import assert from 'node:assert/strict';
import {test} from 'node:test';
const source=readFileSync(new URL('../../../management_portal/static/frontier_model.js',import.meta.url),'utf8');
const {validateItem,progressOf,isHistory,saveDocument,deliver}=await import('data:text/javascript;base64,'+Buffer.from(source).toString('base64'));
const item={id:'one',title:'问题',line:'路线',progress:'形成了假设',reason:'可以被反驳',next:'检查反例',attention:'optional',sources:['thinking_0123456789ab']};
test('intervention and execution stay independent; completed input leaves the frontier',()=>{
  assert.equal(progressOf(item), '尚未投递');
  assert.equal(isHistory({...item,attention:'decision'},{state:'completed'}),true);
  assert.equal(progressOf({...item,dispatch:{}},{state:'failed'}),'执行失败');
  assert.equal(progressOf({...item,dispatch:{}}), '待核实');
});
test('updates retain identity and reject duplicate goals, malformed sources and self-links',()=>{
  assert.equal(validateItem(item,[item]),item);
  assert.throws(()=>validateItem({...item,id:'two'},[item]),/已有/);
  assert.throws(()=>validateItem({...item,sources:['javascript:alert(1)']}),/资源地址/);
  assert.throws(()=>validateItem({...item,parent:'one'},[item]),/自身/);
});
test('concurrent update cannot overwrite a newer decision',async()=>{
  await assert.rejects(saveDocument(async(_name,args)=>{assert.equal(args.expected_value,null);return {matched:false};},'lineage_0123456789ab',null,{items:[item]}),/其他人/);
});
test('receipt loss recovers the same immutable message and idempotency key',async()=>{
  const sent=[];const row={...item,dispatch:{owner:'resident_0123456789ab',message_id:'msg_fixture',text:'检查反例'}};
  const fake=async(name,args,opts)=>{sent.push({name,args,opts});if(sent.length===1)throw Error('lost receipt');return {accepted:true};};
  await assert.rejects(deliver(fake,row)); await deliver(fake,row);
  assert.deepEqual(sent[0],sent[1]);assert.equal(sent[1].opts.idempotencyKey,row.dispatch.message_id);
});
