const { [process.argv[3]]:engine}=require('../../../management_portal/test_framework/tau_runtime/node_modules/playwright');const assert=require('node:assert/strict');
(async()=>{const b=await engine.launch({headless:true});try{const [url,,name,value]=process.argv.slice(2),ctx=await b.newContext();await ctx.addCookies([{name,value,url}]);const p=await ctx.newPage();await p.goto(url+'/integration');await p.waitForFunction(()=>ready);
await p.evaluate(()=>openPaper('paper_0123456789ab'));assert.equal(await p.evaluate(()=>posts),0);assert(await p.locator('#paperFullscreenBtn').count(),await p.locator('#workspaceMain').innerHTML());
// Failed embedded load has no ready and must not record. Real fullscreen provides ready.
await p.route('**/paper/api/reader/open',r=>r.fulfill({status:503,contentType:'application/json',body:'{"error":"open failed"}'}));await p.locator('#paperFullscreenBtn').click();await p.locator('[role=status]').filter({hasText:'open failed'}).waitFor();assert.equal(await p.evaluate(()=>posts),0);await p.getByText('退出全屏',{exact:true}).click();await p.waitForFunction(()=>!document.querySelector('dialog'));await p.unroute('**/paper/api/reader/open');await p.evaluate(()=>openPaper('paper_0123456789ab'));
await p.locator('#paperFullscreenBtn').click();await p.waitForFunction(()=>posts===1);
await p.evaluate(()=>renders.at(-1).onReady());assert.equal(await p.evaluate(()=>posts),1);
await p.getByText('退出全屏',{exact:true}).click();await p.waitForFunction(()=>!document.querySelector('dialog'));await p.evaluate(()=>{renders.at(-1).onReady();});assert.equal(await p.evaluate(()=>posts),1);
await p.evaluate(async()=>{await fetch('/paper/api/recent-reading/open',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({paper_id:'paper_000000000002',expected_principal:'reader_one'})});openPaper('paper_0123456789ab');renders.at(-1).onReady();});await p.waitForFunction(()=>posts===2);
await p.locator('[data-view=recent]').click();await p.waitForFunction(()=>document.querySelectorAll('[data-row]').length===2);assert.equal(await p.locator('[data-row]').first().getAttribute('data-row'),'paper_0123456789ab');
console.log('PASS page fullscreen real ready -> recent PG -> personal dedup, failed embedded no record, exit reopen updates');
}finally{await b.close();}})().catch(e=>{console.error(e);process.exitCode=1;});
