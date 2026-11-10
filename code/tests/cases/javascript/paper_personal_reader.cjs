const engine=require('../../../management_portal/test_framework/tau_runtime/node_modules/playwright')[process.argv[3]],assert=require('node:assert/strict');
(async()=>{const b=await engine.launch({headless:true});try{
const [url,,name,value]=process.argv.slice(2),ctx=await b.newContext();await ctx.addCookies([{name,value,url}]);const p=await ctx.newPage();
await p.route('**/paper/api/library?*',r=>r.fulfill({json:{papers:[]}}));
await p.route('**/paper/api/recent-reading?*',r=>r.fulfill({json:{papers:[],next_cursor:null}}));
await p.goto(url+'/integration');await p.waitForFunction(()=>ready);
assert.equal(await p.locator('[data-view=recent]').getAttribute('aria-pressed'),'true');
try {await p.getByText('暂无记录',{exact:true}).waitFor({timeout:3000});} catch(e){console.error(await p.locator('.paperPersonal').innerText());throw e;}
await p.evaluate(()=>openPaper('paper_0123456789ab'));await p.waitForFunction(()=>posts===1);
await p.evaluate(()=>{window.firstReader=document.querySelector('iframe');window.firstReaderWindow=firstReader.contentWindow;});
await p.locator('[data-scope=repository]').click();assert.equal(await p.locator('[data-personal-tabs]').isVisible(),false);
await p.locator('[data-scope=personal]').click();await p.locator('[data-view=favorites]').click();
await p.locator('[data-scope=repository]').click();await p.locator('[data-scope=personal]').click();assert.equal(await p.locator('[data-view=favorites]').getAttribute('aria-pressed'),'true');
await p.locator('[data-view=recent]').click();
assert(await p.evaluate(()=>firstReader===document.querySelector('iframe')&&firstReaderWindow===firstReader.contentWindow));
assert.equal(await p.evaluate(()=>posts),1);assert.equal(await p.getByLabel('翻译模式').isVisible(),false);
console.log('PASS personal recent empty default, repository/favorites switch retains embedded Reader and recent dedup');
}finally{await b.close();}})().catch(e=>{console.error(e);process.exitCode=1;});
