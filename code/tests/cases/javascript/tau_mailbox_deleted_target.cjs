const assert=require('node:assert/strict'),path=require('node:path');
const code=process.env.TAU_CODE_ROOT||path.resolve(__dirname,'../../..');
const {chromium}=require(path.join(code,'management_portal/test_framework/tau_runtime/node_modules/playwright'));
const {readMailboxDeletedTarget:read}=require(process.env.TAU_MAILBOX_DELETED_MODULE||path.join(code,'management_portal/test_framework/tau_mailbox_deleted_target'));
(async()=>{const browser=await chromium.launch({headless:true,args:['--no-sandbox']});try{
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
 console.log(JSON.stringify({passed:true,scope:'isolated Chromium helper, not formal case',checks:['hidden graph retained and reported','visible graph blocks','pinned target blocks','refresh blocks','missing entries blocks','open confirmation blocks','exact identity']}));
}finally{await browser.close();}})().catch(e=>{console.error(e);process.exitCode=1;});
