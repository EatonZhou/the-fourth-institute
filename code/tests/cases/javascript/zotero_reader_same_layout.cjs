const engine=require('../../../management_portal/test_framework/tau_runtime/node_modules/playwright')[process.argv[3]];
const assert=require('node:assert/strict');
(async()=>{const browser=await engine.launch({headless:true});try{
const [url,,cookie,one]=process.argv.slice(2);const context=await browser.newContext();
await context.addCookies([{name:cookie,value:one,url}]);const page=await context.newPage();
await page.goto(url);await page.waitForFunction(()=>window.ready);await page.evaluate(()=>openDocument(true));
await page.waitForFunction(()=>signals.length===1);const outer=page.frames()[1];
await outer.locator('button.note').click();await outer.frameLocator('iframe').first().locator('.page').first().click({position:{x:160,y:160}});
await outer.locator('[contenteditable=true]').last().fill('same instance draft');
await page.evaluate(()=>{window.firstFrame=document.querySelector('iframe');window.firstWindow=firstFrame.contentWindow;});
for(let i=0;i<3;i++){
 await page.evaluate(()=>host.setFullscreen(true));await page.waitForTimeout(30);
 await page.evaluate(()=>host.setFullscreen(false));await page.waitForTimeout(30);
 assert(await page.evaluate(()=>firstFrame===document.querySelector('iframe')&&firstWindow===firstFrame.contentWindow));
 assert.equal(await page.evaluate(()=>signals.length),1);
 assert.equal(await outer.locator('[contenteditable=true]').last().innerText(),'same instance draft');
}
await page.evaluate(()=>host.setFullscreen(true));
await page.locator('.paperReadingToolbar button').focus();await page.keyboard.press('Escape');
assert(await page.locator('dialog').evaluate(e=>e.classList.contains('is-embedded')));
assert.equal(await page.evaluate(()=>signals.length),1);
const navigation=page.waitForEvent('dialog');
const leaving=page.goto(url+'?leave',{timeout:3000}).catch(()=>{});
await (await navigation).dismiss();await leaving;
assert(await page.evaluate(()=>firstFrame===document.querySelector('iframe')));
assert.equal(await outer.locator('[contenteditable=true]').last().innerText(),'same instance draft');
await page.route('**/paper/api/reader/annotations',r=>r.request().method()==='POST'?r.fulfill({status:503,contentType:'application/json',body:'{"error":"unavailable"}'}):r.continue());
await outer.locator('[contenteditable=true]').last().fill('edit after cancelled navigation');
assert.equal(await page.evaluate(()=>host.close()),false);assert.equal(await page.locator('iframe').count(),1);
assert(await page.evaluate(()=>firstWindow===document.querySelector('iframe').contentWindow));
await page.unroute('**/paper/api/reader/annotations');assert.equal(await page.evaluate(()=>host.close()),true);
assert.equal(await page.locator('iframe').count(),0);
console.log('PASS synthetic PDF same iframe/window/draft/ready across layouts; failed save retains; successful close releases');
}finally{await browser.close();}})().catch(e=>{console.error(e);process.exitCode=1;});
