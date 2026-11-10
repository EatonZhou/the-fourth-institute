// Complete upstream Viewer, real native editing UI, synthetic PDF, isolated account API.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const {chromium} = require('../../../management_portal/test_framework/tau_runtime/node_modules/playwright');
const root = path.resolve(__dirname, '../../..');
let items=[], failSave=false, failLoad=false, writes=0;
const html=`<!doctype html><meta charset="utf-8"><link rel="stylesheet" href="/static/paper.css">
<script src="/static/portal_api.js"></script><button id="open">全屏阅读</button>
<script type="module">
import {openReader} from '/static/paper_fullscreen_reader.js';
document.querySelector('#open').onclick=()=>window.reader=openReader({paper:{id:'paper_0123456789ab',title:'Fixture PDF',pdf_url:'/fixture.pdf'},onClose:p=>window.closedPosition=p});
</script>`;
const types={'.html':'text/html','.css':'text/css','.mjs':'text/javascript','.js':'text/javascript',
  '.json':'application/json','.pdf':'application/pdf','.svg':'image/svg+xml','.wasm':'application/wasm',
  '.ftl':'text/plain','.woff2':'font/woff2','.bcmap':'application/octet-stream'};
const server=http.createServer(async(req,res)=>{
  const url=new URL(req.url,'http://local');
  if(url.pathname==='/') {res.setHeader('Content-Type','text/html');return res.end(html);}
  if(url.pathname==='/paper/api/highlights') {
    res.setHeader('Content-Type','application/json');
    if(req.method==='POST') {
      let body='';for await(const chunk of req) body+=chunk;
      if(failSave) {res.statusCode=403;return res.end(JSON.stringify({error:'denied'}));}
      const data=JSON.parse(body);writes++;
      items=data.operation==='add'?[...items.filter(i=>i.id!==data.highlight.id),data.highlight]:items.filter(i=>i.id!==data.id);
    } else if(failLoad) {res.statusCode=403;return res.end(JSON.stringify({error:'denied'}));}
    return res.end(JSON.stringify({highlights:items}));
  }
  const file=url.pathname==='/fixture.pdf'?path.join(root,'tests/cases/fixtures/paper-synthetic-means.pdf'):path.join(root,'management_portal',url.pathname);
  if(!file.startsWith(root)||!fs.existsSync(file)||fs.statSync(file).isDirectory()) {res.statusCode=404;return res.end();}
  res.setHeader('Content-Type',types[path.extname(file)]||'application/octet-stream');res.end(fs.readFileSync(file));
});
const until=async predicate=>{for(let i=0;i<100;i++){if(await predicate())return;await new Promise(r=>setTimeout(r,100));}assert.fail('condition timed out');};
(async()=>{
  await new Promise(r=>server.listen(0,'127.0.0.1',r));
  const browser=await chromium.launch({headless:true});
  try {
    const page=await browser.newPage({viewport:{width:1440,height:960}}), errors=[];
    page.setDefaultTimeout(8000);
    page.on('pageerror',e=>errors.push(e.message));
    await page.goto(`http://127.0.0.1:${server.address().port}`);
    async function open() {
      await page.locator('#open').click();
      await page.waitForFunction(()=>/选中文字/.test(document.querySelector('[role=status]')?.textContent),null,{timeout:20000}).catch(async e=>{console.error({errors,status:await page.locator('[role=status]').textContent()});throw e;});
      const frame=page.frames().find(f=>f.url().includes('viewer.html'));
      await frame.locator('.textLayer span').first().waitFor();
      return frame;
    }
    async function select(frame) {
      const toggle=frame.locator('#editorHighlightButton');
      if(await frame.evaluate(()=>PDFViewerApplication.pdfViewer.annotationEditorMode===9)) await toggle.click();
      await frame.waitForFunction(()=>PDFViewerApplication.pdfViewer.annotationEditorMode===0);
      const span=frame.locator('.textLayer span').filter({hasText:/\S{5}/}).first();
      const box=await span.boundingBox();
      await page.mouse.move(box.x+2,box.y+box.height/2);await page.mouse.down();
      await page.mouse.move(box.x+box.width-2,box.y+box.height/2,{steps:10});await page.mouse.up();
      await frame.locator('.editToolbar .highlightButton').waitFor({state:'visible'}).catch(async e=>{
        console.error(await frame.evaluate(()=>({selection:getSelection()?.toString(),mode:PDFViewerApplication.pdfViewer.annotationEditorMode,
          layer:document.querySelector('.textLayer')?.className,buttons:[...document.querySelectorAll('.editToolbar')].map(n=>n.outerHTML.slice(0,300))})));throw e;});
    }
    async function mark(frame) {
      await select(frame);
      await frame.locator('.editToolbar .highlightButton').click();
      await frame.locator('.highlightEditor').first().waitFor();
    }
    async function remove(frame) {
      const editor=frame.locator('.highlightEditor').first();
      await editor.click({force:true});
      await frame.locator('.editToolbar .deleteButton').waitFor({state:'visible'});
      await frame.locator('.editToolbar .deleteButton').click();
    }
    let frame=await open();
    const bounds=await page.locator('dialog').boundingBox();assert.equal(bounds.width,1440);assert.equal(bounds.height,960);
    await mark(frame);await until(()=>items.length===1);
    const first=structuredClone(items[0]);assert.ok(first.native[0].quadPoints.length>=8);
    await frame.locator('#zoomInButton').click();
    await page.setViewportSize({width:1200,height:800});
    await until(()=>frame.locator('.highlightEditor').count());
    assert.deepEqual(items[0],first,'zoom/resize must not change stored coordinates');
    await page.getByRole('button',{name:'退出全屏',exact:true}).click();await page.locator('dialog').waitFor({state:'detached'});
    assert.equal((await page.evaluate(()=>window.closedPosition)).kind,'pdf-page');
    frame=await open();await frame.locator('.highlightEditor').first().waitFor().catch(async e=>{
      console.error(await frame.locator('.highlightEditor').evaluate(el=>({rect:el.getBoundingClientRect().toJSON(),style:el.getAttribute('style'),parent:el.parentElement.outerHTML.slice(0,800)})));throw e;});
    failSave=true;await remove(frame);
    await page.getByRole('button',{name:'重试保存'}).waitFor();assert.equal(items.length,1);
    await page.getByRole('button',{name:'退出全屏',exact:true}).click();assert.equal(await page.locator('dialog').count(),1);
    failSave=false;await page.getByRole('button',{name:'重试保存'}).click();await until(()=>items.length===0);
    // Native undo restores the same personal identity.
    await frame.locator('#editorUndoBarUndoButton').click();await until(()=>items.length===1);
    assert.equal(items[0].id,first.id);
    await page.getByRole('button',{name:'退出全屏',exact:true}).click();await page.locator('dialog').waitFor({state:'detached'});
    // Old records import as native editable marks without a write-on-read migration.
    items=[{id:first.id,text:first.text,rects:first.rects}];const before=writes;
    frame=await open();await frame.locator('.highlightEditor').first().waitFor();
    await new Promise(r=>setTimeout(r,700));assert.equal(writes,before);
    await remove(frame);await until(()=>items.length===0);
    failSave=true;await mark(frame);await page.getByRole('button',{name:'重试保存'}).waitFor();assert.equal(items.length,0);
    failSave=false;await page.getByRole('button',{name:'重试保存'}).click();await until(()=>items.length===1);
    await page.getByRole('button',{name:'退出全屏',exact:true}).click();await page.locator('dialog').waitFor({state:'detached'});
    failLoad=true;
    await page.locator('#open').click();
    await page.waitForFunction(()=>document.querySelector('[role=status]')?.textContent.includes('个人高亮载入失败'));
    assert.equal(await page.locator('iframe').evaluate(el=>el.inert),true,'no editing before account state loads');
    failLoad=false;await page.getByRole('button',{name:'重试保存'}).click();
    await page.waitForFunction(()=>document.querySelector('iframe')?.inert===false);
    await page.getByRole('button',{name:'退出全屏',exact:true}).click();await page.locator('dialog').waitFor({state:'detached'});
    assert.deepEqual(errors,[]);console.log('PASS full Viewer native selection/highlight/delete/undo, reload, legacy, zoom, save failures, exit anchor');
  } finally {await browser.close();server.close();}
})().catch(e=>{console.error(e);process.exitCode=1;server.close();});
