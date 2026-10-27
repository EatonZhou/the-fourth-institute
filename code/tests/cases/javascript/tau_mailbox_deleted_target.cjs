const assert=require('node:assert/strict'),path=require('node:path');
const code=process.env.TAU_CODE_ROOT||path.resolve(__dirname,'../../..');
const {chromium}=require(path.join(code,'management_portal/test_framework/tau_runtime/node_modules/playwright'));
const {readMailboxDeletedTarget:read}=require(process.env.TAU_MAILBOX_DELETED_MODULE||path.join(code,'management_portal/test_framework/tau_mailbox_deleted_target'));
async function nativeStoreSampling(browser){
 const context=await browser.newContext(),p=await context.newPage(),requests=[],errors=[];
 const target={residentId:'resident_111111111111',entryKey:'resident:resident_111111111111'};
 await context.route('**/*',async route=>{
  const url=new URL(route.request().url());requests.push({method:route.request().method(),path:url.pathname});
  if(url.pathname==='/')return route.fulfill({contentType:'text/html',body:'<!doctype html><section hidden><button data-node-id="resident_111111111111">Retained hidden graph</button></section>'});
  if(url.pathname==='/api/agent-conversations')return route.fulfill({json:{entries:[],ready:true,read_revision:1,content_signature:'empty-isolated-index'}});
  errors.push('Unexpected fixture route '+url.pathname);return route.abort();
 });
 p.on('pageerror',e=>errors.push(e.message));
 try{
  await p.goto('http://mailbox-fixture.local/');
  await p.evaluate(()=>{
   window.PortalI18n={t:x=>x,message:x=>typeof x==='string'?x:x.message,requestLanguage:()=> 'en'};
   window.PortalIdentity={username:()=> 'fixture',userId:async()=> 'fixture'};
   window.PortalCompatibility={identifier:x=>'fixture:'+x};
   window.PortalEvents={isConnected:()=>true,connect(){},watchResident:()=>()=>{}};
  });
  for(const name of ['portal_state','portal_text','portal_ui_primitives','portal_api','portal_instruction_client','portal_resident_service','resident_protocol','portal_refresh_coordinator','portal_conversation_store'])
   await p.addScriptTag({path:path.join(code,'management_portal/static',name+'.js')});
  await p.evaluate(()=>PortalConversationStore.refresh());
  await p.waitForFunction(()=>{const s=PortalConversationStore.snapshot();return s.ready&&!s.loading&&!s.refreshing&&!s.syncing;});
  const ready=await read(p,target);assert.equal(ready.ready,true,JSON.stringify(ready));
  assert.equal(ready.samples[0].hidden_node_count,1);
  const deadlines=[];
  for(const stallAt of [1,2]){
   await p.evaluate(stallAt=>{
    const original={request:window.requestAnimationFrame,cancel:window.cancelAnimationFrame,store:window.PortalConversationStore};
    const held=new Map(),issued=[],cancelled=[];let calls=0,nextId=1000000;
    const fixture=window.deleteFrameFixture={original,held,issued,cancelled,reads:0};
    // Only instrument calls: every value still comes from the real Store.
    window.PortalConversationStore={...original.store,snapshot:()=>{fixture.reads++;return original.store.snapshot();}};
    window.requestAnimationFrame=callback=>{
     if(++calls<stallAt)return original.request.call(window,callback);
     const id=nextId++;held.set(id,callback);issued.push(callback);return id;
    };
    window.cancelAnimationFrame=id=>{if(held.has(id)){held.delete(id);cancelled.push(id);}else original.cancel.call(window,id);};
   },stallAt);
   let watchdog;
   const baseline=!!process.env.TAU_MAILBOX_DELETED_BASELINE;
   const pending=read(p,{...target,frameTimeoutMs:150});
   const outcome=await Promise.race([pending,new Promise(resolve=>{watchdog=setTimeout(()=>resolve({watchdog:true}),baseline?300:1500);})]);
   clearTimeout(watchdog);
   const cleanup=await p.evaluate(async()=>{
    const f=window.deleteFrameFixture,before={held:f.held.size,cancelled:f.cancelled.length,reads:f.reads};
    window.requestAnimationFrame=f.original.request;window.cancelAnimationFrame=f.original.cancel;
    for(const callback of f.issued)callback(performance.now());
    await new Promise(resolve=>f.original.request.call(window,()=>f.original.request.call(window,resolve)));
    const afterReads=f.reads;window.PortalConversationStore=f.original.store;
    return {...before,afterReads};
   });
   const released=await pending;
   if(baseline){
    assert.equal(outcome.watchdog,true);assert.equal(cleanup.reads,stallAt-1);
    assert.equal(cleanup.cancelled,0);assert.equal(cleanup.afterReads,2);assert.equal(released.ready,true);
    deadlines.push({stallAt,pending_until_external_release:true,original_samples:cleanup.reads});continue;
   }
   assert(!outcome.watchdog,'Deleted-target sampling must settle without a browser frame');
   assert.equal(outcome.ready,false);assert.equal(outcome.frames_completed,stallAt-1);
   assert.equal(outcome.error.code,'frame_timeout');assert.equal(outcome.error.phase,'animation_frames');
   assert.equal(outcome.samples.length,stallAt-1,'No sample before its original frame');
   assert.equal(outcome.frame_timeout_ms,150);
   assert.deepEqual(cleanup,{held:0,cancelled:1,reads:stallAt-1,afterReads:stallAt-1});deadlines.push(outcome);
  }
  if(!process.env.TAU_MAILBOX_DELETED_BASELINE){
   for(const frameTimeoutMs of [0,-1,NaN,Infinity,10001,'150'])await assert.rejects(read(p,{...target,frameTimeoutMs}),/deadline/);
   for(const failAt of [1,2]){
    await p.evaluate(failAt=>{
     const store=window.PortalConversationStore;let reads=0;
     window.restoreDeleteStore=()=>{window.PortalConversationStore=store;};
     window.PortalConversationStore={...store,snapshot:()=>{if(++reads===failAt)throw new Error('isolated native snapshot failure');return store.snapshot();}};
    },failAt);
    const failed=await read(p,target);await p.evaluate(()=>window.restoreDeleteStore());
    assert.equal(failed.ready,false);assert.equal(failed.samples.length,failAt-1);assert.equal(failed.frames_completed,failAt);
    assert.equal(failed.error.code,'sampling_error');assert.equal(failed.error.phase,'sample');
    assert.match(failed.error.stack,/isolated native snapshot failure/);
   }
   await p.evaluate(()=>{window.restoreDeleteFrames=window.requestAnimationFrame;window.requestAnimationFrame=()=>{throw new Error('isolated delete frame scheduler failure');};});
   const scheduler=await read(p,target);await p.evaluate(()=>{window.requestAnimationFrame=window.restoreDeleteFrames;});
   assert.equal(scheduler.ready,false);assert.deepEqual(scheduler.samples,[]);
   assert.equal(scheduler.error.phase,'animation_frames');assert.match(scheduler.error.stack,/isolated delete frame scheduler failure/);
   assert.equal(await p.locator('[data-node-id]').count(),1,'The helper never deletes retained hidden physical nodes');
  }
  assert(requests.every(r=>r.method==='GET'&&['/','/api/agent-conversations'].includes(r.path)));
  assert.deepEqual(errors,[]);
  return {baseline:!!process.env.TAU_MAILBOX_DELETED_BASELINE,ready,deadlines,fixture:'Actual PortalConversationStore and native protocol/service with local HTTP responses; retained graph DOM is a fixture',production_requests:0,caller_RTT_measured:false,formal_acceptance:false};
 }finally{await context.close();}
}
(async()=>{const browser=await chromium.launch({headless:true,args:['--no-sandbox']});try{
 if(process.env.TAU_MAILBOX_DELETED_BASELINE){console.log(JSON.stringify(await nativeStoreSampling(browser)));return;}
 const p=await browser.newPage();await p.setContent('<section hidden><button data-node-id="resident_111111111111">Retained hidden graph</button></section>');
 const target={residentId:'resident_111111111111',entryKey:'resident:resident_111111111111'};
 await p.evaluate(()=>{window.fixture={ready:true,loading:false,syncing:false,refreshing:false,error:'',entries:[]};window.PortalConversationStore={snapshot:()=>structuredClone(fixture)};});
 let v=await read(p,target);assert(v.ready);assert.equal(v.samples[0].hidden_node_count,1);
 await p.locator('section').evaluate(n=>n.hidden=false);assert.equal((await read(p,target)).ready,false);
 await p.locator('section').evaluate(n=>n.hidden=true);
 await p.evaluate(t=>fixture.entries=[{resident_id:t.residentId,key:t.entryKey,pinned:true}],target);assert.equal((await read(p,target)).ready,false);
 await p.evaluate(()=>{fixture.entries=[];fixture.refreshing=true;});assert.equal((await read(p,target)).ready,false);
 await p.evaluate(()=>{fixture.refreshing=false;fixture.entries=null;});assert.equal((await read(p,target)).ready,false);
 await p.evaluate(()=>{fixture.entries=[];document.body.insertAdjacentHTML('beforeend','<div role="alertdialog" aria-label="Delete Resident?">Delete</div>');});assert.equal((await read(p,target)).ready,false);
 await p.locator('[role=alertdialog]').evaluate(n=>n.remove());
 assert((await read(p,target)).ready);await assert.rejects(read(p,{...target,entryKey:'wrong'}));
 const native=await nativeStoreSampling(browser);
 console.log(JSON.stringify({passed:true,scope:'isolated Chromium helper, not formal case',checks:['hidden graph retained and reported','visible graph blocks','pinned target blocks','refresh blocks','missing entries blocks','open confirmation blocks','exact identity','native Store frame deadline and cancellation','original post-frame sampling order','no late sampling','snapshot and scheduler errors retained'],native}));
}finally{await browser.close();}})().catch(e=>{console.error(e);process.exitCode=1;});
