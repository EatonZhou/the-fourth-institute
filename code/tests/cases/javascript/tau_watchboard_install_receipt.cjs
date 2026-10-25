'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),os=require('node:os'),http=require('node:http');
const code=process.env.TAU_CODE_ROOT||path.resolve(__dirname,'../../..');
const {chromium}=require(path.join(code,'management_portal/test_framework/tau_runtime/node_modules/playwright'));
const {observeBrowser}=require(path.join(code,'management_portal/test_framework/tau_browser_observer'));
const {observeFiniteInstructionResponses}=require(path.join(code,'management_portal/test_framework/tau_finite_instruction_evidence'));
const {readWatchboardInstallationReceipt:read}=require(process.env.TAU_WATCHBOARD_ACTION_MODULE||path.join(code,'management_portal/test_framework/tau_watchboard_action_contract'));
(async()=>{
 let browser,context,capture,observer; const received=[],dir=fs.mkdtempSync(path.join(os.tmpdir(),'watchboard-original-install-'));
 const server=http.createServer((req,res)=>{if(req.method!=='POST'){res.end('<!doctype html><button>Install</button>');return}
  let raw='';req.on('data',c=>raw+=c);req.on('end',()=>{const b=JSON.parse(raw);received.push(b);let created=b.arguments.operations.map(o=>o.value.id);
   if(b.idempotency_key==='missing')created.pop();if(b.idempotency_key==='duplicate')created.push(created[0]);if(b.idempotency_key==='foreign')created.push('effect_other');
   const result={created_ids:created,updated_ids:[],deleted_ids:[]};if(b.idempotency_key==='absent')delete result.created_ids;
   res.writeHead(200,{'Content-Type':'application/json'});res.end(JSON.stringify({instruction_id:'inst_'+received.length,status:'committed',replayed:b.idempotency_key==='replay',result}));});});
 try{await new Promise(r=>server.listen(0,'127.0.0.1',r));const origin='http://127.0.0.1:'+server.address().port;
  browser=await chromium.launch({headless:true});context=await browser.newContext();capture=await observeBrowser(context,{evidenceDir:dir});const page=await context.newPage();await page.goto(origin);
  const residents=['resident_source','resident_fork'],events=[];
  observer=observeFiniteInstructionResponses(page,{origin,targets:residents.map(r=>({instruction:'ApplyLineageChanges',arguments:{lineage_id:'lineage_fixture',near_resident_id:r}})),requestId:r=>capture.requestId(r),record:r=>events.push(r)});
  const proofs=[];
  for(const [i,scenario] of ['install','reinstall','fork-install','missing','duplicate','foreign','absent','replay','owner'].entries()){
   const residentId=i===2?residents[1]:residents[0],ids=['event_'+i,'effect_'+i,'wire_'+i];
   const args={lineage_id:'lineage_fixture',near_resident_id:residentId,operations:[
    {op:'create',kind:'component',value:{id:ids[0],kind:'event_source',client_key:'watchboard.'+residentId+'.view',config:{private:'not retained'}}},
    {op:'create',kind:'component',value:{id:ids[1],kind:'effect',client_key:'watchboard.'+(scenario==='owner'?'resident_other':residentId)+'.command.effect',config:{private:'not retained'}}},
    {op:'create',kind:'wire',value:{id:ids[2],source:ids[0],target:ids[1]}}]};
   await page.evaluate(payload=>{document.querySelector('button').onclick=()=>{window.done=fetch('/api/instructions/execute',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)}).then(r=>r.json())}},{instruction:'ApplyLineageChanges',arguments:args,idempotency_key:scenario});
   const original=page.waitForResponse(r=>r.request().method()==='POST');await page.getByRole('button',{name:'Install',exact:true}).click();const response=await original;
   const receipt=await read(response,observer,{lineageId:'lineage_fixture',residentId});proofs.push(receipt);
   fs.writeFileSync(path.join(dir,scenario+'.json'),JSON.stringify(receipt)); // Preserve before readiness assertions.
   assert.equal(receipt.request_id,capture.requestId(response.request()));assert.equal(receipt.instruction_id,'inst_'+(i+1));assert.equal(receipt.ready,i<3);
   if(i<3){assert.deepEqual(receipt.created_ids,ids);assert.deepEqual(receipt.operations.map(o=>o.id),ids)}else assert(receipt.issues.length);
   assert(!JSON.stringify(receipt).includes('not retained'));await page.evaluate(()=>window.done);assert.equal(received.length,i+1,'helper never sends or retries');
  }
  assert.equal(events.filter(e=>e.phase==='request_observed').length,9);assert.equal(new Set(proofs.map(p=>p.request_id)).size,9);
  console.log(JSON.stringify({status:'passed',formal_acceptance:false,isolated_chromium:true,checks:['source/reinstall/fork exact original request IDs','operations and returned IDs saved before assertions','missing/extra/duplicate/absent IDs rejected','foreign owner and replay rejected','no config/body leakage','no helper requests or replay']}));
 }finally{if(observer)await observer.close();if(context)await context.close();if(capture){await capture.flush();capture.stop()}if(browser)await browser.close();server.closeAllConnections();await new Promise(r=>server.close(r));fs.rmSync(dir,{recursive:true,force:true})}
})().catch(e=>{console.error(e.stack);process.exitCode=1});
