import test from 'node:test';
import assert from 'node:assert/strict';
import { createResources } from '../src/shared/resources.js';

const tick = () => new Promise(resolve => setImmediate(resolve));
const response = (operation,args) => operation.endsWith('_many') ? {results:args.addresses.map(address=>({address,ok:true,result:operation.startsWith('resolve')?{readable:true}:{content:'body'}}))} : operation==='resolve'?{readable:true}:{content:'body'};
test('unfinished identical requests share transport but retain independent anchors and fresh permissions', async () => {
  const calls=[]; let release, denied=false;
  const resources=createResources({resource:async(operation,args)=>{
    calls.push([operation,args.address]); await new Promise(r=>release=r);
    if(denied)throw Object.assign(new Error('revoked'),{code:'permission_denied'});
    return {content:'{"value":1}'};
  }});
  const a=resources.read('fixture:a#L1'), b=resources.read('fixture:a#L2','json');
  await tick(); assert.equal(calls.length,1);release();
  assert.equal((await a).ref,'fixture:a#L1');assert.deepEqual((await b).data,{value:1});
  denied=true;const c=resources.read('fixture:a');await tick();release();
  assert.equal((await c).readable,false);assert.equal(calls.length,2);
});

test('one region bounds all read/resolve concurrency and drains to idle', async () => {
  let active=0,peak=0,total=0;
  const resources=createResources({resource:async(operation,args)=>{
    total++;peak=Math.max(peak,++active);await tick();active--;
    return response(operation,args);
  }});
  await Promise.all(Array.from({length:500},(_,i)=>i%2?resources.resolve('fixture:'+i):resources.read('fixture:'+i)));
  assert.equal(total,4);assert.equal(peak,4);assert.equal(active,0);
});

test('page disposal rejects queued requests without sending them and ignores late active responses', async () => {
  const controller=new AbortController(), releases=[];let total=0;
  const resources=createResources({resource:async(operation,args)=>{
    total++;await new Promise(r=>releases.push(r));return response(operation,args);
  }},{signal:controller.signal});
  const results=Promise.allSettled(Array.from({length:700},(_,i)=>resources.resolve('fixture:'+i)));
  await tick();assert.equal(total,4);controller.abort();releases.forEach(r=>r());
  for(const r of await results){assert.equal(r.status,'rejected');assert.equal(r.reason.name,'AbortError');}
  await assert.rejects(resources.read('new'),{name:'AbortError'});assert.equal(total,4);
});

test('failed shared requests are evicted and a retry reaches the server', async () => {
  let calls=0;const resources=createResources({resource:async()=>{
    calls++;await tick();if(calls===1)throw new Error('temporary');return {readable:true};
  }});
  const results=await Promise.allSettled([resources.resolve('a'),resources.resolve('a')]);
  assert(results.every(r=>r.status==='rejected'));assert.equal(calls,1);
  assert.equal((await resources.resolve('a')).readable,true);assert.equal(calls,2);
});

test('batch item failures retain denial semantics and never expose malformed or reordered responses', async () => {
  const resources=createResources({resource:async(_operation,{addresses})=>({results:addresses.map(address=>address==='private'
    ? {address,ok:false,status:403,error:{code:'permission_denied',error:'denied'}}
    : {address,ok:true,result:{content:'ok'}})})});
  const [good,bad]=await Promise.all([resources.read('public'),resources.read('private')]);
  assert.equal(good.data,'ok');assert.equal(bad.readable,false);
  const malformed=createResources({resource:async(_operation,{addresses})=>({results:addresses.reverse().map(address=>({address,ok:true,result:{readable:true}}))})});
  const results=await Promise.allSettled([malformed.resolve('a'),malformed.resolve('b')]);
  assert(results.every(r=>r.status==='rejected'&&r.reason.code==='projection.resource_batch_response'));
});

test('batch byte exhaustion retries only the affected item once; unsupported batch never fans out', async () => {
  const calls=[];const resources=createResources({resource:async(operation,args)=>{
    calls.push([operation,args]);
    if(operation==='read_many')return {results:args.addresses.map(address=>address==='large'
      ? {address,ok:false,status:413,error:{code:'projection.resource_size',error:'remaining budget'}}
      : {address,ok:true,result:{content:'small'}})};
    return {content:'large body'};
  }});
  const [small,large]=await Promise.all([resources.read('small'),resources.read('large')]);
  assert.equal(small.data,'small');assert.equal(large.data,'large body');
  assert.deepEqual(calls.map(c=>c[0]),['read_many','read']);assert.equal(calls[1][1].address,'large');
  let attempts=0;
  const unsupported=createResources({resource:async()=>{attempts++;throw Object.assign(Error('not available'),{code:'projection.resource_operation'});}});
  assert((await Promise.allSettled([unsupported.resolve('a'),unsupported.resolve('b')])).every(r=>r.status==='rejected'));
  assert.equal(attempts,1);
});
