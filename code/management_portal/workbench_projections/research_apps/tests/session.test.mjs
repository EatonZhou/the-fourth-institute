import test from 'node:test';
import assert from 'node:assert/strict';
import {createResearchSession} from '../src/session.js';
import {readableIndex} from '../src/index-access.js';
const deferred=()=>{let resolve;const promise=new Promise(r=>{resolve=r;});return {promise,resolve};};
function fixture(overrides={}) {
 const calls=[];
 const raw={resource:async(op,args)=>{calls.push([op,args]);return op==='resolve'?{readable:true}:{content:'record',metadata:{content_sha256:'a'.repeat(64)}};},
  cancelFeedback:async()=>calls.push(['cancel']),focus:async obj=>calls.push(['focus',obj]),
  feedback:async obj=>calls.push(['feedback',obj]),watch:async()=>()=>{},...overrides};
 return {calls,raw,session:createResearchSession(raw,{workbenchId:'workbench_d82dedd31208'})};
}
test('permission rejection and resolve/read revocation never yield cached data; service errors remain errors',async()=>{
 const {session,calls}=fixture({resource:async op=>op==='resolve'?{readable:false}:assert.fail('read after denied resolve')});
 await assert.rejects(session.read('experiment_111111111111'),{code:'permission_denied'});session.destroy();
 for(const code of ['permission_denied','file_not_found','resource_not_found','service_unavailable']) {
  const f=fixture({resource:async op=>{if(op==='resolve')return {readable:true};throw Object.assign(Error(code),{code});}});
  await assert.rejects(f.session.read('experiment_111111111111'),{code});f.session.destroy();
 }
 assert.equal(calls.filter(x=>x[0]==='feedback').length,0);
});
test('hidden / destroyed session rejects late reads, cancels confirmations and releases watch',async()=>{
 const d=deferred();let stop=0;
 const f=fixture({resource:async op=>op==='resolve'?{readable:true}:d.promise,watch:async()=>()=>{stop++;}});
 const pending=f.session.read('experiment_111111111111');await new Promise(r=>setImmediate(r));
 f.session.hide();d.resolve({content:'old'});await assert.rejects(pending,{code:'projection.stale'});
 assert.ok(stop>0);assert.ok(f.calls.some(x=>x[0]==='cancel'));f.session.destroy();
});
test('complete research source groups above the HTTP batch size reach the host',async()=>{
 const groups=[];
 const f=fixture({watch:async roots=>{groups.push(roots);return ()=>{};}});
 const refs=Array.from({length:137},(_,i)=>`experiment_${String(i).padStart(12,'0')}`);
 const readable=await f.session.readable(refs);
 await new Promise(r=>setImmediate(r));
 assert.equal(readable.size,refs.length);
 assert.deepEqual(new Set(groups.at(-1)),new Set(refs));
 await f.session.destroy();
});
test('overlapping cleanup shares work; release failure remains visible and retryable after destroy',async t=>{
 const failure=Error('unsubscribe unavailable'), gate=deferred();let calls=0;
 const log=t.mock.method(console,'error',()=>{});
 const f=fixture({watch:async()=>()=>{calls++;return calls===1?gate.promise:undefined;}});
 await f.session.resolve('experiment_111111111111');
 await new Promise(r=>setImmediate(r));
 const hidden=f.session.hide(),destroyed=f.session.destroy();
 await Promise.resolve();assert.equal(calls,1);
 gate.resolve(Promise.reject(failure));
 for(const result of await Promise.allSettled([hidden,destroyed])) {
  assert.equal(result.status,'rejected');assert.equal(result.reason,failure);
 }
 assert.equal(log.mock.calls.length,1);assert.equal(log.mock.calls[0].arguments[1],failure);
 await f.session.destroy();assert.equal(calls,2);
 await f.session.destroy();assert.equal(calls,2);
});
test('late subscriptions still record synchronous release failures and can be retried',async t=>{
 const gate=deferred(), failure=Error('late unsubscribe failed');let calls=0;
 const log=t.mock.method(console,'error',()=>{});
 const f=fixture({watch:()=>gate.promise});
 await f.session.resolve('experiment_111111111111');
 await new Promise(r=>setImmediate(r));
 await f.session.destroy();
 gate.resolve(()=>{if(++calls===1)throw failure;});
 await new Promise(r=>setImmediate(r));
 assert.equal(log.mock.calls.length,1);assert.equal(log.mock.calls[0].arguments[1],failure);
 await f.session.destroy();assert.equal(calls,2);
});
test('discussion preserves exact five fields and text; changed source cancels without feedback',async()=>{
 const f=fixture();
 await f.session.select({ref:'experiment_111111111111',kind:'research-measurement',title:'PI 0.5 × A100',scene:{hardware:'A100'}});
 assert.deepEqual(Object.keys(f.calls.find(x=>x[0]==='focus')[1]).sort(),['kind','observed_at','ref','source','status']);
 await f.session.discuss('保持我的原文\n与问题');
 assert.equal(f.calls.find(x=>x[0]==='feedback')[1].text,'保持我的原文\n与问题');
 f.raw.resource=async op=>op==='resolve'?{readable:true}:{content:'changed',metadata:{content_sha256:'b'.repeat(64)}};
 await assert.rejects(f.session.discuss('不能发送'),{code:'projection.stale'});
 assert.equal(f.calls.filter(x=>x[0]==='feedback').length,1);f.session.destroy();
});
test('late A selection cannot replace B or revive after close',async()=>{
 const late=deferred();const f=fixture({resource:async(op,{address})=>op==='resolve'?{readable:true}:address==='experiment_111111111111'?late.promise:{content:'B',metadata:{content_sha256:'b'}}});
 const a=f.session.select({ref:'experiment_111111111111',kind:'record',title:'A'});
 await new Promise(r=>setImmediate(r));await f.session.select({ref:'experiment_222222222222',kind:'record',title:'B'});
 late.resolve({content:'A'});await assert.rejects(a,{code:'projection.stale'});
 assert.deepEqual(f.calls.filter(x=>x[0]==='focus').map(x=>x[1].ref),['experiment_222222222222']);
 f.session.clearSelection();await assert.rejects(f.session.discuss('old'),{code:'projection.stale'});f.session.destroy();
});
test('index excludes denied measurements, branches, cached source and inventory before counting',async()=>{
 const good='experiment_111111111111',bad='experiment_222222222222';
 const index={sources:{[good]:{text:'known'},[bad]:{text:'secret'}},inventory:[{address:good},{address:bad}],
 cells:{pi05__a100:{measurements:[{source:{address:good}},{source:{address:bad}}],strategies:[{sources:[good]},{sources:[bad]}]}},
 normalized_reports:{[good]:{branches:[{id:'parent',source:bad},{id:'child',source:good,parent_id:'parent'}]}}};
 const result=await readableIndex(index,{readable:async()=>new Set([good])});
 assert.equal(result.inventory.length,1);assert.equal(result.cells.pi05__a100.measurements.length,1);
 assert.equal(result.sources[bad],undefined);assert.equal(result.normalized_reports[good].branches[0].parent_id,'parent');
 assert.equal(JSON.stringify(result).includes('secret'),false);
});
