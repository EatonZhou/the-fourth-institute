const engine=require('../../../management_portal/test_framework/tau_runtime/node_modules/playwright')[process.argv[3]];
const assert=require('node:assert/strict');
(async()=>{const browser=await engine.launch({headless:true});try{
const [url,,cookie,one,two]=process.argv.slice(2);const context=await browser.newContext();
await context.addCookies([{name:cookie,value:one,url}]);const page=await context.newPage();page.on('pageerror',e=>console.error('PAGE',e.message));page.on('response',r=>{if(r.status()>=400)console.error('HTTP',r.status(),r.url());});
const open=async()=>{await page.goto(url);await page.waitForFunction(()=>window.ready);await page.evaluate(()=>openDocument());await page.waitForFunction(()=>signals.length===1);assert(await page.evaluate(()=>!!signals[0].fingerprint));return page.frames()[1];};
let outer=await open();await outer.locator('button.note').click();await outer.frameLocator('iframe').first().locator('.page').first().click({position:{x:160,y:160}});await outer.locator('[contenteditable=true]').last().fill('formal fullscreen');
// A rejected save must retain the dialog and edit, then recover through retry close.
await page.route('**/paper/api/reader/annotations',r=>r.request().method()==='POST'?r.fulfill({status:503,contentType:'application/json',body:'{"error":"injected unavailable"}'}):r.continue());
await page.evaluate(()=>host.close());assert(await page.locator('dialog').count());assert(!await page.evaluate(()=>window.readerClosed===true));
await page.unroute('**/paper/api/reader/annotations');await page.evaluate(()=>host.close());assert.equal(await page.locator('dialog').count(),0);
outer=await open();await outer.getByText('formal fullscreen',{exact:true}).waitFor();
const csrf=await context.request.post(url+'/paper/api/reader/open',{headers:{Origin:'https://foreign.invalid','X-Reader-Principal':'reader_one'},data:{paper_id:'paper_0123456789ab'}});assert.equal(csrf.status(),403);
await context.addCookies([{name:cookie,value:two,url}]);
await outer.locator('button.note').click();await outer.frameLocator('iframe').first().locator('.page').first().click({position:{x:240,y:220}});await outer.locator('[contenteditable=true]').last().fill('must stay pending');await page.evaluate(()=>host.close());assert(await page.locator('dialog').count());
console.log('PASS formal fullscreen ready/save/reopen/failure barrier/cookie switch');
}finally{await browser.close();}})().catch(e=>{console.error(e);process.exitCode=1;});
