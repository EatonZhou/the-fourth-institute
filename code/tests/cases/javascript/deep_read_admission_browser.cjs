const assert=require('node:assert/strict');
const {chromium}=require('/opt/tfi/the-fourth-institute-dev/code/management_portal/test_framework/tau_runtime/node_modules/playwright');
(async()=>{const browser=await chromium.launch({headless:true});try{
 const page=await browser.newPage({viewport:{width:360,height:640}});
 await page.setContent('<button id="deepReadBtn">Deep Read</button><span id="deepReadStatusText" role="status" aria-live="polite"></span>');
 for(const file of ['paper_workspace_view.js','paper_reviews.js'])await page.addScriptTag({path:'management_portal/static/'+file});
 await page.evaluate(()=>{
 const state={selectedId:'p',mode:'reader',detail:{paper:{id:'p',has_pdf:true}}};window.testState=state;window.posts=0;
 const v=window.PortalPaperWorkspaceView;const deps=Object.fromEntries(v.dependencies.map(k=>[k,()=>{}]));Object.assign(deps,{state,$:id=>document.getElementById(id)});const view=v.createWorkspaceView(deps);
 const m=window.PortalPaperReviews;const d=Object.fromEntries(m.dependencies.map(k=>[k,()=>{}]));Object.assign(d,{state,updateDeepReadControls:view.updateDeepReadControls,paperApi:{request:async(url)=>{
  if(url.endsWith('/start')){window.posts++;return new Promise((r,j)=>window.finish={r,j});}
  return window.observed||{paper_id:'p',job_id:'one',status:'running',step:'extracting pdf text',progress:18};
 }}});window.api=m.createReviews(d);document.getElementById('deepReadBtn').onclick=()=>api.startDeepRead();
 });
 await page.click('#deepReadBtn');assert(await page.locator('#deepReadBtn').isDisabled());assert((await page.textContent('#deepReadStatusText')).includes('Submitting'));
 await page.evaluate(()=>api.startDeepRead());assert.equal(await page.evaluate(()=>posts),1);
 await page.evaluate(()=>finish.j(new Error('HTTP 504')));await page.waitForFunction(()=>testState.deepReadStatus.job_id==='one');
 assert((await page.textContent('#deepReadStatusText')).includes('Extracting PDF'));assert(await page.locator('#deepReadBtn').isDisabled());
 await page.evaluate(async()=>{window.observed={paper_id:'p',job_id:'one',status:'error',error:'Synthetic model failed'};await api.refreshDeepReadProjection()});
 assert.equal(await page.textContent('#deepReadBtn'),'Retry Deep Read');assert(!(await page.locator('#deepReadBtn').isDisabled()));assert((await page.textContent('#deepReadStatusText')).includes('Synthetic model failed'));
 await page.screenshot({path:'/tmp/deep-read-failure-retry-360.png'});
 await page.click('#deepReadBtn');assert.equal(await page.evaluate(()=>posts),2);assert(await page.locator('#deepReadBtn').isDisabled());
 await page.evaluate(()=>finish.r({paper_id:'p',job_id:'two',status:'queued',step:'queued'}));await page.waitForFunction(()=>testState.deepReadStatus.job_id==='two');
 console.log('PASS Chromium360 actual Reviews/Workspace controls: immediate disabled, duplicate guarded, 504->GET existing, visible failure+retry');
}finally{await browser.close()}})().catch(e=>{console.error(e);process.exitCode=1});
