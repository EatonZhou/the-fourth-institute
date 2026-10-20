import test from 'node:test';
import assert from 'node:assert/strict';
import {createArchitectureCandidateLoader} from '../src/architecture/exploration-entry.js';
import {mountDiscoveredArchitecture} from '../src/architecture/architecture-mount.js';

function selection() {
 const calls=[];let evidence={pinned:{address:'base-a'},snapshots:[{commit:'old'}],consumer_contract:{address:'consumer-a'},discoveryPin:{exploration:{status:'available'}}};
 let failure=null, denied=false;
 const loader=createArchitectureCandidateLoader({evidenceLoader:{manifest:async()=>evidence,access:async()=>({readable:!denied})},explorationLoader:{
  async loadDiscovered(value){calls.push(value);if(failure)throw failure;return {status:value.discoveryPin.exploration?.status||'not_declared',batchKey:value.pinned.address};},
  async revalidate(){if(denied)throw Error('revoked');}
 }});
 return {loader,calls,set:value=>{evidence=value;},fail:()=>{failure=Error('invalid model');},deny:()=>{denied=true;},get evidence(){return evidence;}};
}
test('default selection waits for the complete candidate and never falls back from invalid available',async()=>{
 const f=selection(),a=await f.loader.discover();assert.equal(a.kind,'exploration');assert.equal(f.calls.length,1);
 f.set({...f.evidence,pinned:{address:'base-b'}});const b=await f.loader.discover();assert.notEqual(a.key,b.key);
 f.fail();await assert.rejects(f.loader.discover(),/invalid model/);assert.equal(a.exploration.batchKey,'base-a');
 f.deny();await assert.rejects(f.loader.revalidate(a),/revoked/);
});
test('obsolete commit options cannot select history; missing and absent declarations stay distinct',async()=>{
 const f=selection();const h=await f.loader.prepare(f.evidence,{commit:'old'});
 assert.equal(h.kind,'exploration');assert.equal(h.exploration.status,'available');assert.equal(f.calls.length,1);assert.equal(h.commit,undefined);
 assert.equal((await f.loader.prepare(f.evidence,{commit:'unregistered'})).kind,'exploration');
 f.set({...f.evidence,discoveryPin:{exploration:{status:'missing'}}});assert.equal((await f.loader.discover()).exploration.status,'missing');
 f.set({...f.evidence,discoveryPin:{}});assert.equal((await f.loader.discover()).exploration.status,'not_declared');
});

// DOM-independent lifecycle double. Domain rendering is covered by its browser
// reports; this exercises the default mount's current candidate handoff.
function lifecycleRig(initialKind='exploration') {
 const nodes=[];
 const doc={createElement(){const n={style:{},children:[],isConnected:false,setAttribute(){},prepend(x){this.children.unshift(x);x.isConnected=true;},replaceChildren(...xs){for(const x of this.children)x.isConnected=false;this.children=xs;for(const x of xs)x.isConnected=true;},};nodes.push(n);return n;}};
 const root=doc.createElement();root.ownerDocument=doc;
 const make=(id,kind='exploration')=>({key:id,kind,evidence:{},exploration:{status:kind==='exploration'?'available':'not_declared',model:{objects:[]}}});
 let next=make('a',initialKind),deny=false;const records=[],reads=[];
 const candidateLoader={async discover(){reads.push('discover');if(typeof next==='function')return next();if(next instanceof Error)throw next;return next;},async prepare(){return next;},async revalidate(){reads.push('reauthorize');if(deny)throw Error('denied');}};
 const mount=async(element,context,options)=>{
  const record={context,options,element,hidden:false,destroyed:false,state:{mode:'current',route:{kind:'composition'}},accepts:[]};records.push(record);
  const api={hide(){record.hidden=true;},async show(){record.hidden=false;},destroy(){record.destroyed=true;},notify(text){record.notice=text;},async acceptCandidate(candidate){record.accepts.push(candidate.key);},getReadingState:()=>({ready:true}),};
  if(options.candidate)api.session={snapshot:()=>record.state,revalidate:candidateLoader.revalidate};record.api=api;return api;
 };
 return {root,records,reads,make,candidateLoader,mount,set:value=>{next=value;},deny:()=>{deny=true;},context:{sdk:{},resources:{}},get next(){return next;}};
}
test('current renderer changes destroy departed views; history handoff is unavailable',async()=>{
 const r=lifecycleRig(),api=await mountDiscoveredArchitecture(r.root,r.context,{mountLegacy:r.mount,mountExploration:r.mount,candidateLoader:r.candidateLoader});
 const first=r.records[0];assert.equal(first.context.architectureEntry.history,undefined);
 r.set(r.make('base','legacy'));await api.refresh();assert(first.destroyed);
 const base=r.records[1];assert.equal(base.options.commit,undefined);
 r.set(r.make('current'));await api.refresh();assert(base.destroyed);assert.equal(r.records.length,3);
 assert(!r.records[2].destroyed);api.destroy();assert(r.records[2].destroyed);
});
test('rejected discovery retains only a newly authorized old view; late responses cannot replace newer candidates',async()=>{
 const r=lifecycleRig(),api=await mountDiscoveredArchitecture(r.root,r.context,{mountLegacy:r.mount,mountExploration:r.mount,candidateLoader:r.candidateLoader});
 r.set(Error('bad candidate'));await api.refresh();assert(!r.records[0].hidden);assert.match(r.records[0].notice,/保留/);
 let finish;r.set(()=>new Promise(resolve=>{finish=resolve;}));const late=api.refresh();await Promise.resolve();
 r.set(r.make('new'));await api.refresh();finish(r.make('late'));await late;
 assert.deepEqual(r.records[0].accepts,['new']);
 r.deny();r.set(Error('bad and revoked'));await api.refresh();assert(r.records[0].hidden);assert.match(r.records[0].notice,/清空/);api.destroy();
});

test('unchanged discovery reauthorizes without accepting or showing a replacement candidate',async()=>{
 const r=lifecycleRig(),api=await mountDiscoveredArchitecture(r.root,r.context,{mountLegacy:r.mount,mountExploration:r.mount,candidateLoader:r.candidateLoader});
 const record=r.records[0]; let shows=0; record.api.show=async()=>{shows++;};
 const element=r.root.children[0],before=r.reads.length;
 await api.refresh(); await api.refresh();
 assert.equal(r.records.length,1); assert.equal(r.root.children[0],element);
 assert.deepEqual(record.accepts,[]); assert.equal(shows,0);
 assert(r.reads.slice(before).includes('reauthorize'));
 r.deny(); await api.refresh(); assert(record.hidden); assert.match(record.notice,/清空/);
 api.destroy();
});
