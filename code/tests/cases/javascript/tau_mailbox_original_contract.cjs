'use strict';
const assert=require('node:assert/strict'), fs=require('node:fs'), path=require('node:path'), http=require('node:http');
const code=process.env.PORTAL_CODE || path.resolve(__dirname,'../../..');
const draft=process.env.MAILBOX_DRAFT;
const modulePath=name=>draft ? path.join(draft,name+'.js') : path.join(code,'management_portal/test_framework',name+'.js');
const {mailboxInstructionReceipt,mailboxProjectionEventMetadata,observeMailboxChanges}=require(modulePath('tau_mailbox_original_contract'));
const {observeMailboxFrames}=require(modulePath('tau_mailbox_evidence'));
const {observeMailboxRequests}=require(modulePath('tau_mailbox_request_evidence'));
const {chromium}=require(path.join(code,'management_portal/test_framework/tau_runtime/node_modules/playwright'));
const target={residentId:'resident_123456789abc',entryKey:'resident:resident_123456789abc',documentId:'fixture-original-document'};
const create={instruction_id:'inst_fixture',status:'committed',replayed:false,result:{resident_id:target.residentId}};
// AS's exact absent-item/unset-key failure, before a target exists.
const receipt=mailboxInstructionReceipt(create);
assert.equal(receipt.resident_id,target.residentId);assert.equal(receipt.item,null);
assert.equal(mailboxInstructionReceipt({result:{}},{}).item,null);
for(const item of [null,[],{},'bad',{key:'other',read_revision:19}])assert.equal(mailboxInstructionReceipt({result:{item}},target).item,null);
assert.deepEqual(mailboxInstructionReceipt({result:{item:{key:target.entryKey,read_revision:19}}},target).item,{key:target.entryKey,read_revision:19});
const detail={projection_changes:{'agent-conversation-index':{current:['unread']},'resident-view-state':{[target.residentId]:['unread'],'resident_deadbeefdead':['unread']}},projection_details:{'agent-conversation-index':{current:{keys:[target.entryKey,'resident:resident_deadbeefdead']}}}};
const extracted=mailboxProjectionEventMetadata(detail,target);
assert.deepEqual(extracted.projection_changes.target_view.values,['unread']);
assert.deepEqual(extracted.index_keys.values,[target.entryKey]);assert.equal(extracted.index_keys.omitted_count,1);
assert.equal(extracted.read_revision.present,false);assert.ok(!JSON.stringify(extracted).includes('deadbeef'));
for(const v of [null,'unread',{},[]]){
 const x=mailboxProjectionEventMetadata({projection_changes:{'resident-view-state':{[target.residentId]:v}}},target).projection_changes.target_view;
 assert.equal(x.type,v===null?'null':Array.isArray(v)?'array':typeof v);assert.deepEqual(x.values,Array.isArray(v)?[]:null);
}
let browser,probe,changes,capture;const calls=[];
const scripts={
 '/static/portal_api.js':fs.readFileSync(path.join(code,'management_portal/static/portal_api.js'),'utf8'),
 '/static/portal_resident_service.js':fs.readFileSync(draft?path.join(draft,'portal_resident_service.js'):path.join(code,'management_portal/static/portal_resident_service.js'),'utf8'),
 '/static/original-fixture.js':`window.originalRead=async()=>{await Promise.resolve();return Promise.allSettled([PortalResidentService.projection({residentId:'${target.residentId}'}),PortalResidentService.projection({residentId:'${target.residentId}'})]);};`,
};
const server=http.createServer((req,res)=>{
 if(scripts[req.url]){res.writeHead(200,{'Content-Type':'text/javascript'});res.end(scripts[req.url]);return;}
 if(req.url.startsWith('/api/')){
  calls.push({method:req.method,url:req.url,id:req.headers['x-portal-request-id']});
  res.writeHead(404,{'Content-Type':'application/json'});res.end(JSON.stringify({error:{code:'operation.not_found',message:'PRIVATE_DO_NOT_RETAIN'}}));return;
 }
 res.writeHead(200,{'Content-Type':'text/html'});res.end(`<style>.portalAgentInboxPanel{display:none}</style><aside data-portal-agent-inbox><button data-agent-conversation-trigger aria-expanded="false"><span data-agent-conversation-count>9</span></button><section class="portalAgentInboxPanel"><div data-agent-conversation-key="${target.entryKey}" data-status="idle"></div></section></aside><script>window.PortalI18n={requestLanguage:()=>"en",message:v=>typeof v==='string'?v:'error',t:v=>v};window.PortalUIPrimitives={randomUUID:()=>crypto.randomUUID()};window.snapshot={entries:[],unreadCount:8,sections:{},ready:true};window.PortalConversationStore={snapshot:()=>window.snapshot};</script><script src="/static/portal_api.js"></script><script src="/static/portal_resident_service.js"></script><script src="/static/original-fixture.js"></script>`);
});
(async()=>{try{
 await new Promise(r=>server.listen(0,'127.0.0.1',r));const origin='http://127.0.0.1:'+server.address().port;
 browser=await chromium.launch({headless:true,args:['--no-sandbox']});const page=await browser.newPage();await page.goto(origin);
 const samples=[],events=[];
 probe=await observeMailboxFrames(page,{...target,onSample:r=>samples.push(r)});
 changes=await observeMailboxChanges(page,{...target,record:r=>events.push(r)});
 await page.evaluate(d=>window.dispatchEvent(new CustomEvent('portal:change',{detail:d})),detail);
 await page.waitForFunction(()=>true); // Node sinks are asynchronous; await a bounded observed predicate below.
 const until=async predicate=>{const until=Date.now()+5000;while(!predicate()){if(Date.now()>until)throw Error('fixture evidence missing');await new Promise(r=>setTimeout(r,20));}};
 await until(()=>samples.length>0&&events.length===1);
 assert.equal(samples[0].badge.value,'9');assert.equal(samples[0].store.unreadCount,8);assert.equal(samples[0].panel.visible,false);
 assert.equal(events[0].document_id,target.documentId);assert.deepEqual(events[0].projection_changes.target_view.values,['unread']);
 assert.equal(events[0].read_revision.present,false);
 await page.evaluate(()=>{document.querySelector('[data-agent-conversation-count]').textContent='7';window.snapshot.unreadCount=6;});
 await until(()=>samples.some(x=>x.badge.value==='7'));
 const latest=samples.find(x=>x.badge.value==='7');assert.equal(latest.store.unreadCount,6);assert.ok(latest.browser_ms>=samples[0].browser_ms);
 assert.equal(calls.length,0,'passive samplers issue no requests');
 const ids=new WeakMap();let n=0;
 capture=await observeMailboxRequests({page,origin,residentId:target.residentId,requestId:r=>{if(!ids.has(r))ids.set(r,'fixture-original-'+(++n));return ids.get(r);}});
 await page.evaluate(()=>originalRead());const evidence=await capture.stop();capture=null;
 assert.equal(calls.length,2);assert.equal(new Set(calls.map(x=>x.id)).size,2);
 assert.ok(calls.every(c=>c.method==='GET'&&c.url==='/api/agent-workbench/residents/projection?id='+target.residentId&&/^[0-9a-f-]{36}$/.test(c.id)));
 assert.equal(evidence.records.length,2);
 for(const r of evidence.records){assert.equal(r.initiator_association,'unique_original_request_signature');assert.equal(r.error_code,'operation.not_found');assert.ok(r.caller_stack.some(x=>x.source==='/static/portal_resident_service.js'));assert.ok(r.caller_stack.some(x=>x.source==='/static/original-fixture.js'));
  // Each read carries its own transport id, so its cause is the Service's own observation, not a stack or timing guess.
  assert.equal(r.native_source_association,'unique_original_transport');assert.equal(r.native_source.transport_id,r.transport_id);
  assert.equal(r.native_source.observation.consumer,'direct_service');assert.equal(r.trigger_reason,'original_native_observation');}
 assert.ok(!JSON.stringify(evidence).includes('PRIVATE'));
 await probe.stop();probe=null;await changes.stop();changes=null;
 await new Promise(r=>setTimeout(r,100));const stopped=samples.length;
 await page.evaluate(d=>window.dispatchEvent(new CustomEvent('portal:change',{detail:d})),detail);
 await new Promise(r=>setTimeout(r,100));assert.equal(samples.length,stopped);assert.equal(events.length,1);
 console.log(JSON.stringify({status:'passed',browser:'Chromium',isolated:true,business_acceptance:false,checks:['original create receipt with absent item/unset target','target scoped unread arrays and missing revisions','continuous actual DOM badge separate from store count','native Service simultaneous projection transport identities','original CDP stack identity','no observer requests/private content','stop and fixture cleanup']}));
}finally{await capture?.stop();await probe?.stop();await changes?.stop();await browser?.close();await new Promise(r=>server.close(r));}})().catch(e=>{console.error(e);process.exitCode=1;});
