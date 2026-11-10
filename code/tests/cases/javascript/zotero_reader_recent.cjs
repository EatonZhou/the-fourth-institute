const { [process.argv[3]]:engine}=require('../../../management_portal/test_framework/tau_runtime/node_modules/playwright');const assert=require('node:assert/strict');
(async()=>{const b=await engine.launch({headless:true});try{const [url,,name,value]=process.argv.slice(2),ctx=await b.newContext();await ctx.addCookies([{name,value,url}]);const p=await ctx.newPage();await p.goto(url+'/integration');await p.waitForFunction(()=>ready);
await p.evaluate(()=>openPaper('paper_0123456789ab'));await p.waitForFunction(()=>posts===1);
assert.equal(await p.evaluate(()=>renders.length),0,'Paper must never start the public PDF preview');
await p.evaluate(()=>{window.originalFrame=document.querySelector('iframe');window.originalWindow=originalFrame.contentWindow;});
for(let i=0;i<3;i++){
 await p.locator('#paperFullscreenBtn').click();await p.waitForTimeout(40);
 await p.locator('.paperLayoutExit').click();await p.waitForTimeout(40);
 assert(await p.evaluate(()=>originalFrame===document.querySelector('iframe')&&originalWindow===originalFrame.contentWindow));
 assert.equal(await p.evaluate(()=>posts),1);
}
await p.evaluate(()=>openPaper('paper_0123456789ab'));await p.waitForTimeout(100);
assert(await p.evaluate(()=>originalFrame===document.querySelector('iframe')));assert.equal(await p.evaluate(()=>posts),1);
await p.locator('#paperFullscreenBtn').click();
const outer=p.frames()[1];await outer.locator('button.note').click();
await outer.frameLocator('iframe').first().locator('.page').first().click({position:{x:160,y:160}});
await outer.locator('[contenteditable=true]').last().fill('navigation barrier');
await p.route('**/paper/api/reader/annotations',r=>r.request().method()==='POST'?r.fulfill({status:503,contentType:'application/json',body:'{"error":"save unavailable"}'}):r.continue());
assert.equal(await p.evaluate(()=>detailOwner.loadDetail('paper_abcdef012345')),null);
assert.equal(await p.evaluate(()=>state.selectedId),'paper_0123456789ab');
assert(await p.evaluate(()=>originalFrame===document.querySelector('iframe')));
await p.unroute('**/paper/api/reader/annotations');
await p.evaluate(()=>detailOwner.loadDetail('paper_abcdef012345'));await p.waitForFunction(()=>posts===2);
assert(await p.evaluate(()=>!originalFrame.isConnected&&originalFrame!==document.querySelector('iframe')));
assert.equal(await p.evaluate(()=>reader.cancelPdfRendering()),true);assert.equal(await p.locator('iframe').count(),0);
await p.evaluate(()=>openPaper('paper_0123456789ab'));await p.waitForFunction(()=>posts===3);
console.log('PASS Paper embedded Zotero/fullscreen same iframe, no preview, recent once, same-paper render, release/reopen');
}finally{await b.close();}})().catch(e=>{console.error(e);process.exitCode=1;});
