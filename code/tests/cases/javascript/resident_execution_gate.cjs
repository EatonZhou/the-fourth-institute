'use strict';
const {JSDOM}=require('jsdom');
const fs=require('fs'),assert=require('node:assert/strict');
const root=process.env.SOURCE_ROOT||require('path').resolve(__dirname,'../../..');
(async()=>{
const dom=new JSDOM('',{url:'https://quota.fixture.invalid',runScripts:'outside-only',pretendToBeVisual:true}),w=dom.window;
const calls=[],updates=[];let gate={state:'closed',reason:'quota_used_up',used_up:true},cursor=5,canSend=true;
const snapshot=()=>({resident:{resident_id:'member'},cursor,state:{work_state:'idle',intake_state:'open',execution_gate:gate},controls:{can_send:canSend,can_stop:false,disabled_reason:canSend?'':'denied',send_hint:gate.state==='open'?'':'backend queue hint'},messages:[],input_states:[]});
w.PortalI18n={t:s=>s};w.PortalIdentity={load:async()=>{},username:()=> 'fixture'};
w.PortalEvents={watchResident:()=>()=>{}};
w.PortalResidentService={snapshot:async()=>{calls.push('snapshot');return snapshot()},loadStates:async ids=>{calls.push(['batch',ids]);return {states:ids.map(resident_id=>({resident_id,cursor,state:snapshot().state,controls:snapshot().controls,viewer_state:{unread:false}}))}},loadEvents:async()=>({events:[]})};
w.eval(fs.readFileSync(root+'/management_portal/static/resident_protocol.js','utf8'));
const p=w.PortalResidentProtocol;
let sub,states;
try{
assert.equal(p.stateView({intake_state:'open'}).execution_gate?.state,'unknown','missing gate must not promise open');
assert.equal(p.stateView({work_state:'queued',work_phase:'waiting_quota'}).work_phase,'waiting_quota','quota waiting must survive the shared state projection');
assert.equal(p.executionGateView({intake_state:'closed',execution_gate:{state:'open',override_enabled:true}}).reason,'manual');
assert.equal(p.executionGateView({execution_gate:{state:'open',override_enabled:true}}).text,'','effective override remains open');
for(const reason of ['quota_used_up','quota_reserved','quota_recovery_unconfirmed'])assert.match(p.executionGateView({execution_gate:{state:'closed',reason}}).text,/CLOSED/);
assert.match(p.executionGateView({execution_gate:{state:'unknown',reason:'quota_unknown'}}).text,/未确认/);
sub=p.acquire('member',u=>{if(u.snapshot)updates.push(u.snapshot)},{load:false});await sub.load();
states=p.acquireStates(['member'],()=>{},{load:false});await states.load();
function event(state){cursor++;w.dispatchEvent(new w.CustomEvent('portal:change',{detail:{resident_events:[{resident_id:'member',event_type:'resident.state.changed',cursor,payload:{state,controls:{can_send:true,can_stop:false,disabled_reason:'',send_hint:''}}}]}}));}
event({work_state:'queued',intake_state:'open'});
assert.equal(sub.snapshot().state.execution_gate.state,'closed','gate-less event preserves conversation gate');
assert.equal(p.stateSnapshot('member').state.execution_gate.state,'closed','gate-less event preserves graph state');
assert.equal(sub.snapshot().controls.send_hint,'backend queue hint');
// The quota read is newer even with an unchanged conversation cursor.
gate={state:'unknown',reason:'quota_recovery_unconfirmed'};await states.load({force:true});
assert.equal(sub.snapshot().state.execution_gate.state,'unknown','batch refresh reaches conversation');
gate={state:'open',override_enabled:true};await states.load({force:true});
assert.equal(sub.snapshot().state.execution_gate.state,'open');assert.equal(sub.snapshot().controls.send_hint,'');
assert.equal(sub.snapshot().controls.can_send,true);
gate={state:'closed',reason:'quota_reserved'};canSend=false;await states.load({force:true});assert.equal(sub.snapshot().controls.can_send,false);
// A minimal control receipt cannot erase protection or queue permissions.
w.PortalResidentService.cancelInput=async()=>({resident_id:'member',cursor,state:{intake_state:'open',work_state:'idle'},controls:{can_send:false,send_hint:''}});
await p.cancelInput({resident_id:'member'});assert.equal(sub.snapshot().state.execution_gate.state,'closed');
// A new quota read must survive an advanced conversation cursor without rolling work back.
event({intake_state:'open',work_state:'running'});const eventCursor=cursor;cursor--;gate={state:'unknown',reason:'quota_unknown'};await states.load({force:true});
assert.equal(sub.snapshot().state.execution_gate.state,'unknown');assert.equal(sub.snapshot().state.work_state,'running');cursor=eventCursor;
// Failed state reconciliation remains retryable; it does not clear known protection.
const originalLoad=w.PortalResidentService.loadStates;w.PortalResidentService.loadStates=async()=>{throw new TypeError('fixture offline')};
await assert.rejects(states.load({force:true}),/offline/);assert.equal(sub.snapshot().state.execution_gate.state,'unknown');w.PortalResidentService.loadStates=originalLoad;
// A Chat-only subscriber must also join the existing batched reconciliation.
states.destroy();states=null;gate={state:'open',override_enabled:true};w.dispatchEvent(new w.Event('focus'));await new Promise(r=>setTimeout(r,30));
assert.equal(sub.snapshot().state.execution_gate.state,'open');
assert(calls.filter(c=>Array.isArray(c)).length>=5);assert(updates.some(s=>s.state.execution_gate?.state==='unknown'));
console.log(JSON.stringify({ok:true,calls,checks:'normalizer, manual priority, override, missing-gate event/receipt, same-cursor batch, controls, Chat-only reconciliation'}));
}finally{states?.destroy();sub?.destroy();dom.window.close()}
})().catch(e=>{console.error(e);process.exitCode=1});
