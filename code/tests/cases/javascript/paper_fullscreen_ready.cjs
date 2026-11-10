const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),http=require('node:http');
const {chromium,firefox}=require('../../../management_portal/test_framework/tau_runtime/node_modules/playwright');
const root=path.resolve(__dirname,'../../..');
const html=`<!doctype html><meta charset="utf-8"><link rel="stylesheet" href="/static/paper.css"><div id="folderTree"></div><div id="workspaceMain"></div>
<script src="/static/paper_personal.js"></script><script src="/static/paper_reader.js"></script>
<script>
window.state={principalId:'alice',username:'alice',selectedId:'paper_000000000001',mode:'reader',favoriteIds:new Set()};window.writes=[];window.embedded=[];
window.PortalAPI={request:async()=>({highlights:[]})};
window.PortalI18n={t:x=>x,html:(s,...v)=>s.reduce((a,x,i)=>a+x+(v[i]??''),'')};
window.PortalPreview={mountZoom:()=>({dispose(){},setEnabled(){}})};
window.PortalPdfPreview={render:config=>{embedded.push(config);return {root:config.root,promise:new Promise(()=>{}),cancel(){}}}};
const deps=Object.fromEntries(PortalPaperReader.dependencies.map(k=>[k,()=> '']));
Object.assign(deps,{$:id=>document.getElementById(id),state,esc:String,md:String,detailSectionsLoaded:()=>true,alertError:()=>{}});
window.reader=PortalPaperReader.createReader(deps);
window.prepare=(url='/fixture.pdf')=>{state.detail={paper:{id:state.selectedId,title:'Synthetic',pdf_url:url}};reader.renderReaderMode();};
PortalPaperPersonal.mount({state,paperApi:{request:async(url,options)=>{if(url.endsWith('/open'))writes.push({actor:state.username,...options.body});return {papers:[],warnings:[]}}},loadDetail:async()=>{},setMode:()=>{}});
prepare();
</script>`;
const types={'.html':'text/html','.js':'text/javascript','.mjs':'text/javascript','.css':'text/css','.pdf':'application/pdf','.ftl':'text/plain','.wasm':'application/wasm'};
let held=[];
const server=http.createServer((req,res)=>{
 const url=new URL(req.url,'http://local');
 if(url.pathname==='/'){res.setHeader('Content-Type','text/html');return res.end(html);}
 if(url.pathname==='/slow.pdf'){held.push(res);server.emit('held');return;}
 if(url.pathname==='/bad.pdf'){res.statusCode=404;return res.end();}
 const file=url.pathname==='/fixture.pdf'?path.join(root,'tests/cases/fixtures/paper-synthetic-means.pdf'):path.join(root,'management_portal',url.pathname);
 if(!file.startsWith(root)||!fs.existsSync(file)||!fs.statSync(file).isFile()){res.statusCode=404;return res.end();}
 res.setHeader('Content-Type',types[path.extname(file)]||'application/octet-stream');res.end(fs.readFileSync(file));
});
(async()=>{
 await new Promise(r=>server.listen(0,'127.0.0.1',r));
 try{for(const [name,type] of [['chromium',chromium],['firefox',firefox]]){
  const browser=await type.launch({headless:true});
  try{
   const page=await browser.newPage();page.setDefaultTimeout(20000);
   page.on('pageerror',error=>console.error(name,error.message));
   await page.goto('http://127.0.0.1:'+server.address().port);
   async function open(){await page.locator('#paperFullscreenBtn').click();}
   async function rendered(){const frame=page.frameLocator('iframe');try{await frame.locator('.page canvas').first().waitFor();await frame.locator('.textLayer span').first().waitFor();}catch(error){console.error(await page.locator('iframe').evaluate(f=>({url:f.src,text:f.contentDocument.body.innerText.slice(-700),app:!!f.contentWindow.PDFViewerApplication,pdf:!!f.contentWindow.PDFViewerApplication?.pdfDocument})));throw error;}}
   async function close(){await page.getByRole('button',{name:'退出全屏',exact:true}).click();await page.locator('dialog').waitFor({state:'detached'});}
   // Embedded document has not signalled readiness. Fullscreen must record from a real PDF render.
   await open();await rendered();await page.waitForFunction(()=>writes.length===1);
   assert.equal(await page.evaluate(()=>writes[0].paper_id), 'paper_000000000001');
   await close();await open();await rendered();await page.waitForFunction(()=>writes.length===2);await close();
   // An already-ready embedded reader and its fullscreen transition share one acknowledgement.
   await page.evaluate(()=>{prepare();embedded.at(-1).onReady();});
   await page.waitForFunction(()=>writes.length===3);await open();await rendered();
   assert.equal(await page.evaluate(()=>writes.length),3);await close();
   // Failed PDF has no successful render signal.
   await page.evaluate(()=>prepare('/bad.pdf'));const failure=page.waitForEvent('pageerror');await open();
   await failure;await close();
   assert.equal(await page.evaluate(()=>writes.length),3);
   // Old iframe is closed before a delayed response; it cannot record later.
   await page.evaluate(()=>prepare('/slow.pdf'));let received=new Promise(r=>server.once('held',r));await open();await received;
   await page.waitForFunction(()=>!!document.querySelector('iframe')?.contentWindow?.PDFViewerApplication);
   await close();
   for(const response of held.splice(0)){if(!response.destroyed){response.setHeader('Content-Type','application/pdf');response.end(fs.readFileSync(path.join(root,'tests/cases/fixtures/paper-synthetic-means.pdf')));}}
   // Account change during a real delayed render is rejected by the host session guard.
   await page.evaluate(()=>{prepare('/slow.pdf');state.username='alice';});received=new Promise(r=>server.once('held',r));await open();await received;
   await page.evaluate(()=>state.username='bob';state.principalId='bob');
   for(const response of held.splice(0)){if(!response.destroyed){response.setHeader('Content-Type','application/pdf');response.end(fs.readFileSync(path.join(root,'tests/cases/fixtures/paper-synthetic-means.pdf')));}}
   await rendered();
   assert.equal(await page.evaluate(()=>writes.length),3);await close();
   console.log(name+': real PDF fullscreen ready, dedup, reopen, failed/closed/account guards passed');
  }finally{await browser.close();}
 }}finally{for(const response of held.splice(0))response.destroy();server.close();}
})().catch(error=>{console.error(error);process.exitCode=1;server.close();});
