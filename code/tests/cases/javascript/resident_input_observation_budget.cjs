'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {JSDOM} = require('jsdom');
const drain = async () => { for(let i=0;i<5;i++) await new Promise(r=>setImmediate(r)); };
(async () => {
 const dom = new JSDOM('', {runScripts:'outside-only', url:'https://fixture.invalid',pretendToBeVisual:true});
 const w = dom.window; let interest=0, snapshots=0, reads=0, state='queued', gate, denied=false;
 w.PortalI18n={t:x=>x};
 w.PortalEvents={watchResident:()=>{interest++;return()=>interest--;}};
 w.PortalResidentService={
  inputState:async()=>{reads++; if(denied) throw Object.assign(new Error('denied'),{status:403}); const result={message_id:'outside-page',state}; if(gate) return new Promise(r=>gate=r); return result;},
  snapshot:async()=>{snapshots++;return {resident:{resident_id:'r'},cursor:10,messages:[],input_states:[],state:{},controls:{},queue:{items:[]}};}
 };
 w.eval(fs.readFileSync(path.resolve(__dirname,'../../../management_portal/static/resident_protocol.js'),'utf8'));
 const updates=[];const watch=w.PortalResidentProtocol.watchInputState('r','outside-page',v=>updates.push(v));
 await drain(); assert.equal(reads,1); assert.equal(snapshots,0);assert.equal(interest,1);
 const send=(cursor,type,payload={})=>w.dispatchEvent(new w.CustomEvent('portal:change',{detail:{resident_events:[{resident_id:'r',cursor,event_type:type,payload}]}}));
 // Group receipt names the group, not this member. Target read projects it.
 state='completed';send(1,'resident.input.state.changed',{input_state:{message_id:'group',state}});
 await drain();assert.equal(updates.at(-1).input_state.state,'completed');assert.equal(reads,2);
 send(1,'resident.input.state.changed',{});await drain();assert.equal(reads,2,'duplicate cursor ignored');
 state='failed';send(4,'unrelated');await drain();assert.equal(updates.at(-1).input_state.state,'failed','gap recovers target');
 w.dispatchEvent(new w.CustomEvent('portal:events-ready',{detail:{snapshot_required:true}}));await drain();assert.equal(reads,4);
 // Existing Chat and input observer share the exact same event interest.
 const chat=w.PortalResidentProtocol.acquire('r',()=>{}, {load:false});await chat.load();assert.equal(interest,1);
 chat.destroy();assert.equal(interest,1);assert.equal(snapshots,1);
 gate=true;const stale=watch.load();await drain();state='completed';send(5,'resident.input.state.changed',{input_state:{message_id:'outside-page',state}});
 const resolve=gate;gate=null;resolve({message_id:'outside-page',state:'queued'});await stale;await drain();
 assert.equal(updates.at(-1).input_state.state,'completed','late initial read cannot roll back newer event');
 denied=true;await watch.load();assert.equal(updates.at(-1).error.status,403,'explicit target denial is not idle');
 denied=false;state='failed';w.dispatchEvent(new w.Event('focus'));await drain();
 assert.equal(updates.at(-1).input_state.state,'failed','focus restores authoritative read after error');
 w.dispatchEvent(new w.Event('portal:identity-changed'));await drain();assert.equal(updates.at(-1).error.name,'AbortError');assert.equal(interest,0);
 watch.destroy();const before=reads;send(6,'resident.queue.changed');await drain();assert.equal(reads,before);
 console.log(JSON.stringify({cold_chat_reads:0,cold_input_reads:1,shared_streams:1,group_gap_reconnect_identity:true}));
 w.close();
})().catch(e=>{console.error(e);process.exitCode=1;});
