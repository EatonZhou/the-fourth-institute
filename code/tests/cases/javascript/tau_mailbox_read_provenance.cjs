'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),http=require('node:http');
const code=process.env.TAU_CODE_ROOT||path.resolve(__dirname,'../../..');
const draft=process.env.TAU_READ_DRAFT||null;
const {chromium}=require(path.join(code,'management_portal/test_framework/tau_runtime/node_modules/playwright'));
const {observeMailboxRequests}=require(draft?path.join(draft,'tau_mailbox_request_evidence.js'):path.join(code,'management_portal/test_framework/tau_mailbox_request_evidence.js'));
const target='resident_111111111111',foreign='resident_222222222222';
async function scenario(browser,patched){
 let status=200,missing=false,hold=false,pendingResponse,requests=[],observations=[],errors=[];
 const server=http.createServer((req,res)=>{
  const url=new URL(req.url,'http://localhost');
  if(!url.pathname.startsWith('/api/')){res.writeHead(200,{'content-type':'text/html'});res.end('<title>Isolated native read provenance</title>');return;}
  let raw='';req.on('data',x=>raw+=x);req.on('end',()=>{
   const body=raw?JSON.parse(raw):{},rid=url.searchParams.get('id')||body.arguments?.resident_id;
   requests.push({path:url.pathname,instruction:body.instruction||null,rid,ids:body.arguments?.resident_ids||null,transport:req.headers['x-portal-request-id']});
   const finish=()=>{
    let result;
    if(body.instruction==='ListResidentStates')result={states:missing?[]:(body.arguments.resident_ids||[]).map(id=>({resident_id:id,state:{status:'idle'},viewer_state:{unread:false},cursor:1}))};
    else if(body.instruction==='DeleteResident')result={resident_id:rid};
    else result={resident_id:rid,resident:{resident_id:rid},provider_session:{id:'bound'},private_result:'PRIVATE_BODY'};
    const error=!body.instruction&&status!==200;
    res.writeHead(error?status:200,{'content-type':'application/json'});
    res.end(JSON.stringify(error?{code:'operation.not_found',error:'PRIVATE_ERROR'}:body.instruction?{status:'committed',result}:result));
   };
   if(hold&&!body.instruction){pendingResponse=finish;return;}finish();
  });
 });
 await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
 const origin=`http://127.0.0.1:${server.address().port}`,context=await browser.newContext();
 let observer;
 const page=await context.newPage(),otherPage=await context.newPage();
 for(const p of [page,otherPage]){
  p.on('pageerror',e=>errors.push(e.message));await p.goto(origin);
  await p.evaluate(()=>{
   window.PortalI18n={t:x=>x,message:x=>typeof x==='string'?x:x.message,requestLanguage:()=> 'en'};
   window.PortalUIPrimitives={randomUUID:()=>crypto.randomUUID()};
   window.PortalEvents={watchResident:()=>()=>{}};
  });
  for(const n of ['portal_api.js','portal_instruction_client.js','portal_resident_service.js','resident_projection.js','resident_protocol.js']){
   const source=patched&&draft&&fs.existsSync(path.join(draft,n))?path.join(draft,n):path.join(code,'management_portal/static',n);
   await p.addScriptTag({path:source});
  }
 }
 const ids=new WeakMap();let sequence=0;
 const requestId=r=>{if(!ids.has(r))ids.set(r,`request-${++sequence}`);return ids.get(r);};
 try{
  // A read before registration remains outside original evidence.
  await page.evaluate(id=>PortalResidentService.projection({residentId:id}),target);
  observer=await observeMailboxRequests({page,origin,residentId:target,requestId});
  const afterRegistration=requests.length;
  await page.evaluate(id=>PortalResidentProjection.load(id),target);
  await page.evaluate(id=>{window.updates=[];window.stopProjection=PortalResidentProjection.subscribe(id,v=>updates.push(v.error?{error:v.error.code}:{ok:true}));},target);
  await page.waitForFunction(()=>updates.length===1);
  assert.equal(requests.length,afterRegistration+1,'cache subscription must not perform another GET');
  const notify=async detail=>{
   const count=await page.evaluate(()=>updates.length);
   await page.evaluate(detail=>window.dispatchEvent(new CustomEvent('portal:change',{detail})),detail);
   await page.waitForFunction(n=>updates.length>n,count);
  };
  await notify({resident_changes:{[target]:['identity']}});
  await notify({snapshot_required:true});
  let count=await page.evaluate(()=>updates.length);
  await page.evaluate(()=>window.dispatchEvent(new CustomEvent('portal:events-ready',{detail:{reconnected:true}})));
  await page.waitForFunction(n=>updates.length>n,count);
  count=await page.evaluate(()=>updates.length);
  await page.evaluate(id=>{
   window.dispatchEvent(new CustomEvent('portal:change',{detail:{resident_changes:{[id]:['identity']}}}));
   window.dispatchEvent(new CustomEvent('portal:change',{detail:{resident_changes:{[id]:['session_restart']}}}));
  },target);
  await page.waitForFunction(n=>updates.length>n,count);
  // Native state consumer observes a true empty result, never a synthetic tombstone.
  missing=true;
  const stateResult=await page.evaluate(async id=>{
   const handle=PortalResidentProtocol.acquireStates([id],()=>{},{load:false,consumer:'inbox'});
   try{await handle.load({force:true});return 'unexpected-success';}catch(e){return e.message;}finally{handle.destroy();}
  },target);
  assert.match(stateResult,/Resident State Snapshot missing/);missing=false;
  await page.evaluate(ids=>PortalResidentService.loadStates(ids),[target,foreign]);
  status=404;
  const failure=await page.evaluate(id=>PortalResidentService.projection({residentId:id}).then(()=>null,e=>e.code),target);
  assert.equal(failure,'operation.not_found');status=200;
  await page.evaluate(id=>PortalResidentService.projection({residentId:id}),foreign);
  // Independent B has its own lifecycle and transports; A must not collect B.
  await otherPage.evaluate(id=>PortalResidentService.projection({residentId:id}),target);
  await page.evaluate(()=>stopProjection());
  hold=true;await page.evaluate(id=>{window.late=PortalResidentService.projection({residentId:id}).then(()=>({ok:true}),e=>({code:e.code}));},target);
  const deadline=Date.now()+5000;while(!pendingResponse&&Date.now()<deadline)await new Promise(r=>setTimeout(r,5));
  assert(pendingResponse,'original held request');
  await page.evaluate(id=>window.dispatchEvent(new CustomEvent('portal:change',{detail:{resident_changes:{[id]:['deleted','identity']}}})),target);
  pendingResponse();hold=false;assert.equal((await page.evaluate(()=>late)).code,'resident.deleted');
  const beforeBlocked=requests.length;
  assert.equal(await page.evaluate(id=>PortalResidentService.projection({residentId:id}).then(()=>null,e=>e.code),target),'resident.deleted');
  assert.equal(requests.length,beforeBlocked,'retired read remains blocked before HTTP');
  observations=await observer.stop();
  const native=observations.native_reads;
  if(patched){
   const reads=observations.records.filter(x=>x.selected_resident&&x.path.endsWith('/projection'));
   assert.equal(reads.length,7,'only original registered A requests');
   assert(reads.every(x=>x.native_source_association==='unique_original_transport'));
   assert(reads.every(x=>x.native_source.transport_id===x.transport_id));
   const triggers=reads.map(x=>x.native_source.observation.triggers);
   assert.deepEqual(triggers.slice(0,5),[['explicit_load'],['identity'],['snapshot_required'],['reconnected'],['identity','session_restart']]);
   assert(native.some(x=>x.phase==='state_batch_resolved'&&x.target_present===false&&x.states_array===true));
   const stateReads=observations.records.filter(x=>x.submitted_instruction==='ListResidentStates');
   assert.equal(stateReads.length,2);assert(stateReads.every(x=>x.selected_resident&&x.native_source_association==='unique_original_transport'));
   for(const row of stateReads)assert(native.some(x=>x.batch_id===row.native_source.batch_id&&x.phase==='state_batch_resolved'));
   const blocked=native.find(x=>x.phase==='read_blocked');assert(blocked);assert.equal(blocked.transport_id,null);assert.equal(blocked.lifecycle.deleted,true);
   assert(native.some(x=>x.phase==='remote_deleted'&&x.lifecycle.revision>0));
   assert(!JSON.stringify(native).includes(foreign));assert(!JSON.stringify(observations).includes('PRIVATE_'));
   const beforeStop=native.length;
   await page.evaluate(id=>PortalResidentService.loadStates([id]),target);
   assert.equal(observer.snapshot().native_reads.length,beforeStop,'stopped observers detach');
   requests.pop();
  }else assert.equal(native.length,0,'baseline provides no fabricated provenance');
  assert.deepEqual(errors,[]);
  return {patched,requests:requests.map(({transport,...x})=>x),stateResult,failure,recorded_requests:observations.records.length,
    native_rows:native.length,scope:'isolated HTTP and native browser clients; not AY business acceptance or historical cause'};
 }finally{try{await observer?.stop();}finally{await context.close();await new Promise(resolve=>server.close(resolve));}}
}
(async()=>{const browser=await chromium.launch({headless:true,args:['--no-sandbox']});try{
 const baseline=draft?await scenario(browser,false):null,patched=await scenario(browser,true);
 if(baseline){
  assert.deepEqual(patched.requests,baseline.requests,'telemetry preserves native HTTP behavior');
  assert.equal(patched.stateResult,baseline.stateResult);assert.equal(patched.failure,baseline.failure);
 }
 console.log(JSON.stringify({status:'passed',formal_acceptance:false,isolated_chromium:true,
   requests_unchanged:baseline?patched.requests.length:null,original_transport_associations:patched.recorded_requests,
   native_rows:patched.native_rows,checks:['original trigger and coalescing','state absence to exact transport','retirement and late response','no original action replay','own target and document only','no prior read backfill','detached listeners','error and request behavior preserved']}));
}finally{await browser.close();}})().catch(e=>{console.error(e);process.exitCode=1;});
