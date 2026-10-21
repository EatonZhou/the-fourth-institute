'use strict';
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict'),test=require('node:test'),path=require('node:path');
const source=fs.readFileSync(path.resolve(__dirname,'../../../management_portal/static/portal_resident_runtime_controller.js'),'utf8');
function fixture(){
 const calls=[],observers=new Map(),gates={};let fail=false;
 const window={addEventListener(){},PortalI18n:{t:s=>s},PortalUIPrimitives:{randomUUID:()=> 'key'}};
 vm.runInNewContext(source,{window,console});
 const service={projection:async({residentId})=>{calls.push(['projection',residentId]);if(gates[residentId])await gates[residentId];if(fail)throw new Error('offline');return {resident:{resident_id:residentId,backend:'codex'},allows:{RestartSession:true},chat_workspace:{actions:{runtime_settings:{}}}}},runtimeSettings:async id=>{calls.push(['settings',id]);return {settings:{model:'m'}}},runtimeCatalog:async()=>({models:['m']}),observeSessionRestart:(id,callback)=>{observers.set(id,callback);return()=>observers.delete(id)}};
 return {ctrl:window.PortalResidentRuntimeController.create({service}),calls,observers,gates,setFail:v=>fail=v};
}
test('new subscriber receives current state; cache-hit load replays only to its caller',async()=>{
 const f=fixture(),aStates=[],a=f.ctrl.bind('a',{onState:s=>aStates.push(s)});
 assert.equal(aStates.length,1,'subscribe delivers initial state without starting IO');assert.equal(f.calls.length,0);await a.load();
 const before=aStates.length,bStates=[],b=f.ctrl.bind('a',{onState:s=>bStates.push(s)});
 assert.equal(bStates.length,1);assert.equal(bStates[0].settings.model,'m');assert.equal(bStates[0].loading,false);
 const reads=f.calls.length;bStates.length=0;const value=await b.load();
 assert.equal(bStates.length,1,'explicit cached load repaints the calling view');assert.equal(bStates[0].settings.model,value.settings.model);assert.equal(aStates.length,before,'cache replay does not repaint unrelated subscribers');assert.equal(f.calls.length,reads);b.destroy();a.destroy();
});
test('inflight subscribers share loading and completion; destroyed targets stay isolated',async()=>{
 const f=fixture();let release;f.gates.a=new Promise(r=>release=r);const a=f.ctrl.bind('a'),one=a.load(),seen=[];
 const b=f.ctrl.bind('a',{onState:s=>seen.push(s)}),two=b.load();assert.equal(seen.at(-1).loading,true);
 let disposed=0;const stale=f.ctrl.bind('a',{onState:()=>disposed++});const old=stale.load();stale.destroy();const count=disposed;
 const other=[],c=f.ctrl.bind('b',{onState:s=>other.push(s)});await c.load();release();await Promise.all([one,two,old]);
 assert.equal(f.calls.filter(x=>x[0]==='settings'&&x[1]==='a').length,1);assert.equal(seen.at(-1).loading,false);assert.equal(seen.at(-1).settings.model,'m');assert.equal(disposed,count);assert(other.every(s=>s.residentId==='b'));
 const reads=f.calls.length;await stale.load();assert.equal(f.calls.length,reads,'disposed load must not initiate IO');a.destroy();b.destroy();c.destroy();assert.equal(f.observers.size,0);
});
test('failure is delivered to new subscribers and a retry recovers shared state',async()=>{
 const f=fixture(),a=f.ctrl.bind('a');f.setFail(true);await assert.rejects(a.load(),/offline/);
 const seen=[],b=f.ctrl.bind('a',{onState:s=>seen.push(s)});assert.equal(seen.at(-1).error,'offline');assert.equal(seen.at(-1).loading,false);
 f.setFail(false);await b.load();assert.equal(seen.at(-1).error,'');assert.equal(seen.at(-1).settings.model,'m');a.destroy();b.destroy();
});
