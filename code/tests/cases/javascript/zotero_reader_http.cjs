const engine=require('../../../management_portal/test_framework/tau_runtime/node_modules/playwright')[process.argv[3]||'chromium'];
const assert=require('node:assert/strict');
(async()=>{const browser=await engine.launch({headless:true});try{const context=await browser.newContext();const url=process.argv[2];await context.addCookies([{name:'session',value:'a',url}]);const page=await context.newPage();const errors=[];page.on('pageerror',e=>errors.push(e.message));
const open=async()=>{
  await page.goto(url);
  await page.waitForFunction(()=>window.ready&&document.querySelector('iframe').contentWindow.createReader);
  await page.evaluate(()=>{
    const start=performance.now();
    document.querySelector('iframe').contentDocument.addEventListener('webviewerloaded',event=>{
      const app=event.detail.source.PDFViewerApplication;
      app.initializedPromise.then(()=>app.eventBus.on('pagerendered',event=>{
        if (event.pageNumber!==1 || event.error || window.firstPaint) return;
        const requests=performance.getEntriesByType('resource').filter(r=>r.name.includes('/paper/api/reader/pdf?'));
        window.firstPaint={ms:Math.round(performance.now()-start),bytes:requests.reduce((n,r)=>n+r.encodedBodySize,0),
          requests:requests.map(r=>({status:r.responseStatus,bytes:r.encodedBodySize}))};
      }));
    });
    return window.openDocument();
  });
  const outer=page.frames()[1];
  await outer.frameLocator('iframe').first().locator('.textLayer span').first().waitFor();
  await page.waitForFunction(()=>window.firstPaint);
  return outer;
};
let outer=await open();
const paint=await page.evaluate(()=>window.firstPaint);
console.log('PDF first page',JSON.stringify(paint));
assert(paint.bytes>0 && paint.bytes<512*1024);
assert(paint.requests.every(request=>request.status===206));
const pdfUrl=await page.evaluate(()=>'/paper/api/reader/pdf?'+new URLSearchParams(window.transport.document));
const rangeHeaders={Range:'bytes=0-65535','X-Reader-Principal':'a'};
const range=await context.request.get(url+pdfUrl,{headers:rangeHeaders});
assert.equal(range.status(),206);
console.log('Initial Content-Range',range.headers()['content-range']);
assert.match(range.headers()['content-range'],/^bytes 0-65535\/\d+$/);
assert.equal((await range.body()).length,65536);
assert.equal(range.headers()['cache-control'],'private, no-store');
const invalid=await context.request.get(url+pdfUrl,{headers:{...rangeHeaders,Range:'bytes=999999999-'}});
assert.equal(invalid.status(),416);
await page.evaluate(()=>window.host.reader.navigate({pageIndex:11}));
await outer.frameLocator('iframe').first().getByText('Range test page 12',{exact:true}).waitFor();
assert(await page.evaluate(()=>performance.getEntriesByType('resource').filter(r=>r.name.includes('/paper/api/reader/pdf?')).length)>paint.requests.length);
await page.evaluate(()=>window.host.reader.navigate({pageIndex:0}));
await outer.locator('button.note').click();await outer.frameLocator('iframe').first().locator('.page').first().click({position:{x:160,y:160}});await outer.locator('[contenteditable=true]').last().fill('HTTP to PostgreSQL');await page.evaluate(()=>window.host.close());
// An annotation read held by the server must not prevent the first byte-range request.
const pdfStarted=page.waitForRequest(r=>r.url().includes('/paper/api/reader/pdf?') && r.headers().range==='bytes=0-65535');
await page.route('**/paper/api/reader/annotations?*',async route=>{await pdfStarted;await route.continue();},{times:1});
outer=await open();await outer.locator('[data-sidebar-annotation-id]').first().waitFor();assert(await outer.getByText('HTTP to PostgreSQL',{exact:true}).count());
await page.evaluate(async()=>{window.oldPersistence=window.transport.persistence;await fetch('/test/replace');const {openHttpDocument}=await import('/http_transport.js');await openHttpDocument({paperId:'paper_fixture',principal:'a'});});
const conflict=await page.evaluate(async()=>{try{await window.oldPersistence.remove(['old-id']);return false;}catch(e){return e.status===409;}});assert(conflict);
const oldRange=await context.request.get(url+pdfUrl,{headers:rangeHeaders});
assert.equal(oldRange.status(),206);
assert.deepEqual(await oldRange.body(),await range.body());
const deniedUrl=new URL(url+pdfUrl);
deniedUrl.searchParams.set('attachment_version','missing');
assert.equal((await context.request.get(deniedUrl.href,{headers:rangeHeaders})).status(),404);
await context.request.get(url+'/test/revoke');
assert.equal((await context.request.get(url+pdfUrl,{headers:rangeHeaders})).status(),403);
await page.evaluate(()=>{void window.host.reader.navigate({pageIndex:11});});
await page.waitForFunction(()=>window.readerError==='Paper access denied');
await context.addCookies([{name:'session',value:'b',url}]);const switched=await page.evaluate(async()=>{const response=await fetch('/paper/api/reader/open',{method:'POST',headers:{'Content-Type':'application/json','X-Reader-Principal':'a'},body:JSON.stringify({paper_id:'paper_fixture'})});return response.status;});assert.equal(switched,409);assert.deepEqual(errors,[]);console.log('PASS authenticated handler + native Reader + PG save/reopen, active version conflict, principal switch');
assert.equal((await context.request.get(url+pdfUrl,{headers:rangeHeaders})).status(),409);
await context.clearCookies();
assert.equal((await context.request.get(url+pdfUrl,{headers:rangeHeaders})).status(),401);
}finally{await browser.close();}})().catch(e=>{console.error(e);process.exitCode=1;});
