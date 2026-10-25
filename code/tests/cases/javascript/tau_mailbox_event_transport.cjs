'use strict';
const assert = require('node:assert/strict');
const path = require('node:path');
const source = process.argv[2] || path.resolve(__dirname, '../../../management_portal/test_framework');
const { chromium } = require(path.join(__dirname, '../../../management_portal/test_framework/tau_runtime/node_modules/playwright'));
const patched = require(path.join(source, 'tau_mailbox_evidence'));
const { observeMailboxChanges } = require(path.join(source, 'tau_mailbox_original_contract'));
const target = {residentId:'resident_a123456789ab',entryKey:'resident:resident_a123456789ab'};
const event = {resident_id:target.residentId,event_type:'resident.input.state.changed',cursor:7,
 payload:{input_state:{message_id:'msg_fixture',state:'completed',updated_at:'2026-01-01T00:00:00Z',error:'PRIVATE'},body:'PRIVATE'}};
(async()=>{
 let browser;const observers=[];
 try {
  browser=await chromium.launch({headless:true,args:['--no-sandbox']});
  const pages=await Promise.all([browser.newPage(),browser.newPage()]);
  const errors=[],requests=[];
  for(const page of pages){page.on('pageerror',e=>errors.push(e.name));page.on('request',r=>requests.push(r.url()));await page.setContent('<title>isolated original-document fixture</title>');}
  const invoke=async(page,source,events,identity)=>page.evaluate(({source,events,identity})=>{
   try{return {ok:true,value:(0,eval)('('+source+')')(events,identity)}}catch(e){return {ok:false,name:e.name,message:e.message}}
  },{source,events,identity});
  for(const page of pages){
   const direct=await invoke(page,patched.mailboxResidentEventMetadata.toString(),[event],target);
   assert.equal(direct.ok,true);assert.equal(direct.value.target_events[0].input_state.fields.message_id.value,'msg_fixture');
   assert.equal((await invoke(page,patched.mailboxResidentEventMetadata.toString(),[event],{})).ok,false);
  }
  const rows=[[],[]];
  for(let i=0;i<pages.length;i++)observers.push(await observeMailboxChanges(pages[i],{...target,documentId:'fixture-document-'+i,record:r=>rows[i].push(r)}));
  assert.deepEqual(rows,[[],[]],'registration preflight must not invent events');
  const detail={snapshot_required:true,resident_changes:{[target.residentId]:['runtime','identity','deleted','PRIVATE']},resident_events:[event,{...event,resident_id:'resident_b123456789ab'}]};
  const raw=JSON.stringify(detail);
  for(const page of pages)await page.evaluate(d=>window.dispatchEvent(new CustomEvent('portal:change',{detail:d})),detail);
  const until=async p=>{const end=Date.now()+5000;while(!p()){if(Date.now()>end)throw Error('original fixture callback missing');await new Promise(r=>setTimeout(r,10));}};
  await until(()=>rows.every(r=>r.length===1));
  for(let i=0;i<rows.length;i++){
   const row=rows[i][0];assert.equal(row.document_id,'fixture-document-'+i);
   assert.deepEqual(row.resident_changes.values,['runtime','identity','deleted']);assert.equal(row.resident_changes.omitted_count,1);
   assert.equal(row.resident_events.target_events.length,1);assert.equal(row.resident_events.target_events[0].input_state.fields.message_id.value,'msg_fixture');
   assert.equal(row.resident_events.target_events[0].payload.fields.message_id.present,false);
   assert.equal(row.read_revision.present,false);assert.equal(row.kind,'portal_change');assert.deepEqual(row.snapshot_required,{present:true,type:'boolean',value:true});assert.ok(!JSON.stringify(row).includes('PRIVATE'));assert.ok(!JSON.stringify(row).includes('resident_b123'));
  }
  assert.equal(JSON.stringify(detail),raw);
  for(const page of pages)await page.evaluate(()=>window.dispatchEvent(new CustomEvent('portal:change',{detail:{resident_events:null}})));
  await until(()=>rows.every(r=>r.length===2));for(const r of rows){assert.equal(r[1].resident_events.type,'null');assert.equal(r[1].snapshot_required.present,false);}
  for(const page of pages)await page.evaluate(()=>window.dispatchEvent(new CustomEvent('portal:events-ready',{detail:{reconnected:true,private:'PRIVATE'}})));
  await until(()=>rows.every(r=>r.length===3));for(const r of rows){assert.equal(r[2].kind,'events_ready');assert.equal(r[2].reconnected,true);assert.ok(!JSON.stringify(r[2]).includes('PRIVATE'));}
  for(const observer of observers.splice(0))await observer.stop();
  for(const page of pages)await page.evaluate(d=>window.dispatchEvent(new CustomEvent('portal:change',{detail:d})),detail);
  for(const page of pages)await page.evaluate(()=>window.dispatchEvent(new CustomEvent('portal:events-ready',{detail:{reconnected:true}})));
  await new Promise(r=>setTimeout(r,50));assert.deepEqual(rows.map(r=>r.length),[3,3]);
  assert.deepEqual(errors,[]);assert.deepEqual(requests,[]);
  console.log(JSON.stringify({status:'passed',isolated:true,business_acceptance:false,checks:['local validation in two browser documents','packaged observer preflight before registration','original nested input identity','runtime/deleted scope preservation','snapshot and reconnect original metadata','missing/null preservation','other-target/private payload exclusion','passive no requests','detached listeners']}));
 }finally{for(const o of observers)await o.stop();await browser?.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
