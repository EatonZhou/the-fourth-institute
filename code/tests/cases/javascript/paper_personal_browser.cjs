const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const {chromium, firefox} = require('../../../management_portal/test_framework/tau_runtime/node_modules/playwright');
const root = path.resolve(__dirname, '../../..');
const html = `<!doctype html><meta charset="utf-8"><div id="folderTree">repository</div><div id="workspaceMain"></div>
<script src="/static/paper_personal.js"></script><script src="/static/paper_reader.js"></script>
<script>
window.state={principalId:'alice',favoriteIds:new Set(),favoritePending:new Set(),favoriteRevision:0,selectedId:'paper_000000000001',mode:'reader'};
window.calls=[];window.renders=[];
window.PortalI18n={t:x=>x,html:(s,...v)=>s.reduce((a,x,i)=>a+x+(v[i]??''),'')};
window.PortalPreview={mountZoom:()=>({dispose(){},setEnabled(){}})};
window.PortalPdfPreview={render:config=>{renders.push(config);return {root:config.root,promise:new Promise(()=>{}),cancel(){}}}};
const ids=['paper_000000000001','paper_000000000002'];
const api={request:async(url,options)=>{calls.push({url,options});
if(url.includes('recent-reading/open'))return {};
if(url.includes('/favorites'))return options.body;
if(url.includes('bibtex/export'))return {bibtex:'@misc{paper_000000000001,\\n title={中文},\\n}\\n',warnings:[{code:'missing',field:'year'}]};
if(url.includes('recent-reading'))return url.includes('cursor=next')?{papers:[{id:ids[1],title:'Second',favorite:false}],next_cursor:''}:{papers:[{id:ids[0],title:'First',favorite:false}],next_cursor:'next'};
return {papers:[{id:ids[0],title:'First'}]};}};
window.openPaper=id=>{state.selectedId=id;state.mode='reader';state.detail={paper:{id,title:'Fixture',pdf_url:'/fixture.pdf'}};reader.renderReaderMode();};
const deps=Object.fromEntries(PortalPaperReader.dependencies.map(k=>[k,()=> '']));
Object.assign(deps,{$:id=>document.getElementById(id),state,esc:String,md:String,detailSectionsLoaded:()=>true,alertError:()=>{}});
window.reader=PortalPaperReader.createReader(deps);
PortalPaperPersonal.mount({state,paperApi:api,loadDetail:async id=>{state.selectedId=id;},setMode:mode=>{state.mode=mode;}});
</script>`;
const server=http.createServer((req,res)=>{
  res.setHeader('Content-Type',req.url==='/'?'text/html':'text/javascript');
  if(req.url==='/')return res.end(html);
  const allowed=['/static/paper_personal.js','/static/paper_reader.js'];
  if(!allowed.includes(req.url)){res.statusCode=404;return res.end();}
  res.end(fs.readFileSync(path.join(root,'management_portal',req.url)));
});
(async()=>{
 await new Promise(r=>server.listen(0,'127.0.0.1',r));
 try { for(const [name,type] of [['chromium',chromium],['firefox',firefox]]) {
  const browser=await type.launch({headless:true});
  try {
   const page=await browser.newPage({acceptDownloads:true});
   await page.goto('http://127.0.0.1:'+server.address().port);
   await page.locator('[data-view=recent]').click();
   await page.locator('[data-row]').first().waitFor();
   assert.equal(await page.evaluate(()=>calls.filter(c=>c.url.includes('/open')).length),0);
   await page.evaluate(()=>window.rowBefore=document.querySelector('[data-row]'));
   await page.locator('[data-favorite]').click();
   await page.waitForFunction(()=>document.querySelector('[data-favorite]').getAttribute('aria-pressed')==='true');
   await page.locator('[data-favorite]').click();
   await page.waitForFunction(()=>document.querySelector('[data-favorite]').getAttribute('aria-pressed')==='false');
   assert.equal(await page.evaluate(()=>rowBefore===document.querySelector('[data-row]')),true);
   assert.equal(await page.evaluate(()=>state.favoriteRevision),4);
   assert.equal(await page.evaluate(()=>state.favoritePending.size),0);
   await page.locator('[data-more]').click();
   await page.waitForFunction(()=>document.querySelectorAll('[data-row]').length===2);
   assert.equal(await page.locator('[data-more]').isVisible(),false);
   await page.evaluate(()=>{openPaper('paper_000000000001');renders.at(-1).onReady();renders.at(-1).onReady();});
   assert.equal(await page.evaluate(()=>calls.filter(c=>c.url.includes('/open')).length),1);
   await page.evaluate(()=>{openPaper('paper_000000000001');renders.at(-1).onReady();});
   assert.equal(await page.evaluate(()=>calls.filter(c=>c.url.includes('/open')).length),2);
   await page.evaluate(()=>{openPaper('paper_000000000001');window.late=renders.at(-1);openPaper('paper_000000000002');late.onReady();});
   assert.equal(await page.evaluate(()=>calls.filter(c=>c.url.includes('/open')).length),2);
   // Current render never signals success: load failure must not record.
   await page.evaluate(()=>{reader.cancelPdfRendering();renders.at(-1).onReady();});
   assert.equal(await page.evaluate(()=>calls.filter(c=>c.url.includes('/open')).length),2);
   for(const selector of ['single','favorites']){
    const downloadPromise=page.waitForEvent('download');await page.locator('[data-export='+selector+']').click();
    const download=await downloadPromise;const text=fs.readFileSync(await download.path(),'utf8');
    assert.ok(text.startsWith('@misc{'));assert.ok(!text.includes('missing'));
   }
   assert.ok((await page.locator('[data-status]').textContent()).includes('missing'));
   await page.locator('[data-view=favorites]').click();
   await page.waitForFunction(()=>document.querySelector('[data-heading]').textContent==='我的收藏'&&document.querySelectorAll('[data-row]').length===1);
   await page.locator('[data-favorite]').click();
   await page.waitForFunction(()=>document.querySelectorAll('[data-row]').length===0);
   console.log(name+': personal paging/favorites/ready generations/exports passed');
  } finally {await browser.close();}
 }} finally {server.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
