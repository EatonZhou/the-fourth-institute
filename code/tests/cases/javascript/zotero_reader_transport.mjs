import assert from 'node:assert/strict';
import {createPersistence} from '../../../management_portal/paper_reader_app/zotero_prototype/persistence.js';
const scopes = actorKey => ({actorKey,paper_id:'paper_fixture',attachment_id:'attachment_fixture',attachment_version:'v1'});
const server=new Map(), requests=[];
let sequence=0, dropNext=false, failNext=false;
const transport=actorKey=>({actorKey,
  async read(document,context){const state=get(document,context);return {...documentResponse(document,state),annotations:[...state.records.values()]};},
  async commit(body,context){requests.push(structuredClone({body,context}));const state=get(body,context),raw=JSON.stringify(body);
    if(failNext){failNext=false;throw Error('network before commit');}
    if(state.receipts.has(body.operation_id)){const old=state.receipts.get(body.operation_id);assert.equal(raw,old.raw,'operation ID must have identical body');return old.response;}
    if(body.expected_revision!==String(state.revision))throw Object.assign(Error('conflict'),{status:409});
    for(const id of body.deletes)state.records.delete(id);for(const value of body.upserts)state.records.set(value.id,value);state.revision++;
    const response={...documentResponse(body,state),acknowledged_operation_id:body.operation_id};state.receipts.set(body.operation_id,{raw,response});
    if(dropNext){dropNext=false;throw Error('response lost after commit');}return response;
  }});
function get(document,context){const key=JSON.stringify([context.actorKey,document.paper_id,document.attachment_id,document.attachment_version]);if(!server.has(key))server.set(key,{records:new Map(),receipts:new Map(),revision:0});return server.get(key);}
function documentResponse(d,s){return {document:{paper_id:d.paper_id,attachment_id:d.attachment_id,attachment_version:d.attachment_version},revision:String(s.revision)};}
const session=(scope=scopes('a'),t=transport(scope.actorKey))=>createPersistence({scope,transport:t,operationId:()=>`op-${++sequence}`});
const a=session();await a.load();dropNext=true;const initial=[{id:'A',comment:'first'}];await assert.rejects(a.save(initial),/response lost/);const lost=requests.at(-1).body;await a.save(initial);assert.deepEqual(requests.at(-1).body,lost);assert.equal(a.snapshot().revision,'1');
dropNext=true;await assert.rejects(a.save([{id:'A',comment:'second'}]));const previous=requests.at(-1).body;await a.save([{id:'A',comment:'newer'}]);assert.deepEqual(requests.at(-2).body,previous);assert.notEqual(requests.at(-1).body.operation_id,previous.operation_id);assert.equal(requests.at(-1).body.expected_revision,'2');assert.equal(get(scopes('a'),scopes('a')).records.get('A').comment,'newer');
// Delete commits, save fails before commit; upstream retries both callbacks.
await a.remove(['A']);failNext=true;await assert.rejects(a.save([{id:'B',comment:'pending'}]));await a.remove(['A']);await a.save([{id:'B',comment:'merged newer'}]);assert.equal(get(scopes('a'),scopes('a')).records.has('A'),false);assert.equal(get(scopes('a'),scopes('a')).records.get('B').comment,'merged newer');
const b=session();await b.load();await a.save([{id:'C'}]);await assert.rejects(b.save([{id:'B',comment:'conflicting'}]),/conflict/);let count=requests.length;await assert.rejects(b.remove(['C']),/conflict/);assert.equal(requests.length,count);assert(b.snapshot().pending.upserts.length);assert.equal(get(scopes('a'),scopes('a')).records.get('B').comment,'merged newer');
for(const scope of [scopes('b'),{...scopes('a'),attachment_version:'v2'},{...scopes('a'),attachment_id:'other'}]){const isolated=session(scope);assert.equal((await isolated.load()).annotations.length,0);await isolated.save([{id:'B',comment:'isolated'}]);}
// Response from an old session is allowed to commit to its old namespace only.
let release,started;
const start=new Promise(resolve=>started=resolve),original=transport('a');
const delayed={actorKey:'a',read:original.read,async commit(body,context){const response=await original.commit(body,context);started();await new Promise(resolve=>release=resolve);return response;}};
const old=session({...scopes('a'),attachment_version:'old'},delayed);await old.load();const inflight=old.save([{id:'LATE'}]);await start;old.dispose();const fresh=session({...scopes('b'),attachment_version:'new'});await fresh.load();release();await assert.rejects(inflight,error=>error.code==='stale');assert.equal(old.snapshot().revision,'0');assert(old.snapshot().pending);assert.equal(fresh.snapshot().revision,'0');assert.equal((await session({...scopes('b'),attachment_version:'new'}).load()).annotations.length,0);
// Mutable caller objects cannot change queued intent or scope.
const mutable=scopes('mutable'),m=session(mutable);mutable.attachment_version='different';await m.load();const values=[{id:'M',comment:'original'}],saving=m.save(values);values[0].comment='mutated';await saving;assert.equal(get(scopes('mutable'),scopes('mutable')).records.get('M').comment,'original');
assert.throws(()=>session(scopes('x'),transport('y')),/principal/);
// A queued callback must not start after disposal, nor may a late read install state.
let resolveRead;const readGate=new Promise(resolve=>resolveRead=resolve);
const lateRead=session(scopes('read'),{actorKey:'read',read:async document=>{await readGate;return {document,revision:'9',annotations:[]};},commit:async()=>{throw Error('must not call');}});
const loading=lateRead.load();await Promise.resolve();const queued=lateRead.save([{id:'QUEUED'}]);lateRead.dispose();resolveRead();await assert.rejects(loading,e=>e.code==='stale');await assert.rejects(queued,e=>e.code==='stale');assert.equal(lateRead.snapshot().revision,undefined);
// Rapid callback invocation remains one network operation at a time.
const order=[],orderedBase=transport('ordered');let busy=0;
const ordered=session(scopes('ordered'),{actorKey:'ordered',read:orderedBase.read,async commit(body,context){assert.equal(busy++,0);order.push(body.deletes.length?'delete':'save');await new Promise(resolve=>setImmediate(resolve));const result=await orderedBase.commit(body,context);busy--;return result;}});
await ordered.load();await Promise.all([ordered.remove(['A']),ordered.save([{id:'Z'}])]);assert.deepEqual(order,['delete','save']);
console.log('PASS transport: exact replay, changed intent settles old first/new ID, partial success, conflict pause, ordered callbacks, actor/attachment/version isolation, stale response, immutable input');
