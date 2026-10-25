'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const code=process.env.TAU_CODE_ROOT||path.resolve(__dirname,'../../..');
const draft=process.env.TAU_REMOTE_DELETE_DRAFT_ROOT||code;
const {chromium}=require(path.join(code,'management_portal/test_framework/tau_runtime/node_modules/playwright'));
const rid='resident_111111111111',other='resident_222222222222';
async function run(browser,patched){
 const context=await browser.newContext(), A=await context.newPage(), B=await context.newPage();
 const reads=[],failures=[],errors=[]; let deleted=false,held=null,hold=false;
 await context.route('**/*',async route=>{
  const request=route.request(),u=new URL(request.url());
  if(u.pathname==='/'){await route.fulfill({contentType:'text/html',body:'<!doctype html><title>Isolated remote deletion</title>'});return;}
  let args={};if(request.method()==='POST')args=request.postDataJSON();
  const name=args.name||'projection',id=args.args?.resident_id||u.searchParams.get('id');
  if(name==='DeleteResident'){deleted=true;await route.fulfill({json:{resident_id:id}});return;}
  reads.push({page:request.frame().page()===A?'A':'B',name,id,deleted,path:u.pathname,transport_id:request.headers()['x-portal-request-id']});
  if(hold&&id===other){held=route;return;}
  if(deleted&&id===rid){failures.push({name,id});await route.fulfill({status:404,json:{code:name==='projection'?'operation.not_found':'instruction.not_found'}});return;}
  await route.fulfill({json:name==='GetConversation'?{resident:{resident_id:id},cursor:1,messages:[],input_states:[],state:{status:'idle'}}:{resident_id:id,resident:{resident_id:id},allows:{RestartSession:false},chat_workspace:{actions:{runtime_settings:false}}}});
 });
 for(const p of [A,B]){
  p.on('pageerror',e=>errors.push(e.message));await p.goto('http://localhost/');
  await p.evaluate(()=>{
   window.PortalI18n={t:x=>x};window.PortalUIPrimitives={randomUUID:()=>crypto.randomUUID()};
   const read=async(url,options)=>{const r=await fetch(url,options),body=await r.json();if(!r.ok)throw Object.assign(new Error(body.code),body,{status:r.status});return body};
   window.PortalAPI={get:(url,options)=>read(url,options)};
   window.PortalInstructionClient={execute:(name,args)=>read('/api/instructions/execute',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name,args})})};
   window.PortalEvents={watchResident:()=>()=>{}};window.updates=[];
  });
  for(const file of ['portal_resident_service.js','resident_projection.js','resident_protocol.js','portal_resident_runtime_controller.js']){
   await p.addScriptTag({path:path.join(patched&&file==='portal_resident_service.js'?draft:code,'management_portal/static',file)});
  }
 }
 try{
  await A.evaluate(async rid=>{
   window.conversation=PortalResidentProtocol.acquire(rid,x=>updates.push({consumer:'conversation',type:x.type,code:x.error?.code}),{load:false});
   window.runtime=PortalResidentRuntimeController.bind(rid,{onState:x=>updates.push({consumer:'runtime',error:x.error})});
   await Promise.all([conversation.load(),runtime.load(),PortalResidentProjection.load(rid)]);
   window.stopProjection=PortalResidentProjection.subscribe(rid,x=>updates.push({consumer:'projection',code:x.error?.code}));
  },rid);
  const initial=reads.length;
  await B.evaluate(rid=>PortalResidentService.remove({resident_id:rid,lineage_id:'lineage_test'}),rid);
  await A.evaluate(({rid,patched})=>window.dispatchEvent(new CustomEvent('portal:change',{detail:{resident_changes:{[rid]:patched?['deleted','identity']:['identity']}}})),{rid,patched});
  await A.waitForFunction(()=>updates.some(x=>x.consumer==='conversation'&&x.type==='error')&&updates.some(x=>x.consumer==='projection'&&x.code)&&updates.some(x=>x.consumer==='runtime'&&x.error));
  if(patched){assert.equal(failures.length,0);assert.equal(reads.length,initial);}
  else{assert.deepEqual(failures.map(x=>x.name).sort(),['GetConversation','projection','projection']);}
  // A normal identity change must still refresh a live target.
  await A.evaluate(other=>PortalResidentProjection.load(other),other);
  const beforeOther=reads.length;
  await A.evaluate(other=>{window.stopOther=PortalResidentProjection.subscribe(other,()=>{});window.dispatchEvent(new CustomEvent('portal:change',{detail:{resident_changes:{[other]:['identity']}}}));},other);
  await A.waitForFunction(other=>!!PortalResidentProjection.peek(other),other);
  assert.equal(reads.length,beforeOther+1);
  if(patched){
   await A.evaluate(other=>Promise.all([PortalResidentService.navigationTarget(other),PortalResidentService.navigationTarget(other)]),other);
   const navigation=reads.filter(x=>x.path.endsWith('/navigation-target'));assert.equal(navigation.length,2);assert(navigation.every(x=>x.transport_id));assert.notEqual(navigation[0].transport_id,navigation[1].transport_id);
   // A response started before the tombstone must not repopulate a retired target.
   hold=true;await A.evaluate(other=>{window.late=PortalResidentService.projection({residentId:other}).then(()=>({ok:true}),e=>({code:e.code}));},other);
   while(!held)await new Promise(r=>setTimeout(r,5));
   await A.evaluate(other=>window.dispatchEvent(new CustomEvent('portal:change',{detail:{resident_changes:{[other]:['deleted','identity']}}})),other);
   await held.fulfill({json:{resident_id:other}});assert.equal((await A.evaluate(()=>late)).code,'resident.deleted');
  }
  assert.deepEqual(errors,[]);
  return {patched,original_consumers:['runtime','conversation','projection'],initial_reads:initial,post_delete_http_404s:failures.length,unrelated_identity_refresh:true,late_response_guard:patched,document_count:2,scope:'isolated browser; transport and notifications are fixtures, not AU evidence'};
 }finally{await context.close();}
}
(async()=>{const browser=await chromium.launch({headless:true,args:['--no-sandbox']});try{for(const patched of (process.env.TAU_COMPARE_BASELINE?[false,true]:[true]))console.log(JSON.stringify(await run(browser,patched)));}finally{await browser.close();}})().catch(e=>{console.error(e);process.exitCode=1;});
