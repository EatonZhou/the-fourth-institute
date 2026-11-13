const {chromium}=require('../../../management_portal/test_framework/tau_runtime/node_modules/playwright');
const assert=require('node:assert/strict');
(async()=>{const b=await chromium.launch({headless:true});try{
const [url,name,value,mode]=process.argv.slice(2),ctx=await b.newContext();await ctx.addCookies([{name,value,url}]);const p=await ctx.newPage();p.on('pageerror',e=>console.error(e.stack));p.setDefaultTimeout(6000);
await p.goto(url+'/personal-fixture');
if(mode==='broken'){
 await p.waitForFunction(()=>document.querySelector('[data-status]').textContent.length>0);
 const text=await p.locator('[data-status]').innerText();assert(!text.includes('暂无记录'));assert.equal(await p.locator('[data-row]').count(),0);
}else{
 await p.locator('[data-row="paper_000000000004"]').waitFor().catch(async e=>{console.error(await p.locator('[data-status]').innerText());throw e;});assert.equal(await p.locator('[data-status]').innerText(),'');
 await p.locator('[data-view=favorites]').click();await p.locator('[data-row="paper_000000000004"]').waitFor();
 assert.equal(await p.locator('[data-row]').count(),1);assert.equal(await p.locator('[data-scope=personal]').getAttribute('aria-pressed'),'true');
}
}finally{await b.close();}})().catch(e=>{console.error(e);process.exitCode=1;});
