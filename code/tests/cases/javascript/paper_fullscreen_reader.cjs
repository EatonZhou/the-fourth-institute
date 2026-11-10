// One isolated browser: real React/PDF.js, synthetic PDF and an in-memory API.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const {chromium} = require('../../../management_portal/test_framework/tau_runtime/node_modules/playwright');
const root = path.resolve(__dirname, '../../..');
let items = [], failSave = false;
const html = `<!doctype html><meta charset="utf-8"><link rel="stylesheet" href="/static/paper.css"><div id="workspaceMain" style="height:800px"></div>
<script>
window.PortalUIPrimitives = {escapeHtml: s => String(s).replaceAll('&','&amp;').replaceAll('<','&lt;')};
const template=(s,...v)=>s.reduce((a,t,i)=>a+t+(v[i]??''),'');
window.PortalI18n={t:s=>s,html:template,text:template,message:s=>s,requestLanguage:()=> 'zh-CN'};
</script><script src="/static/portal_api.js"></script><script src="/static/portal_pdf_preview.js"></script><script src="/static/paper_reader.js"></script>
<script>
window.PortalPreview={mountZoom:()=>({dispose(){},setEnabled(){}})};
const deps=Object.fromEntries(window.PortalPaperReader.dependencies.map(k=>[k,()=>{}]));
Object.assign(deps,{$:id=>document.getElementById(id),esc:s=>String(s),md:s=>String(s),detailSectionsLoaded:()=>true,
alertError:e=>{throw e;},state:{detail:{paper:{id:'paper_0123456789ab',title:'Fixture PDF',pdf_url:'/fixture.pdf'},
analysis:{kind:'summary',body:'中文短摘要正文',provenance:{resident_id:'resident_0123456789ab',input_tokens:109542,output_tokens:8205,total_tokens:117747,model:'historic-model'}}}}});
window.reader=window.PortalPaperReader.createReader(deps);window.reader.renderReaderMode();
</script>`;
const server = http.createServer(async (req, res) => {
  const url = new URL(req.url, 'http://local');
  if (url.pathname === '/') { res.setHeader('Content-Type','text/html'); return res.end(html); }
  if (url.pathname === '/paper/api/highlights') {
    res.setHeader('Content-Type','application/json');
    if (req.method === 'POST') {
      let body=''; for await (const chunk of req) body += chunk;
      if (failSave) { res.statusCode=403; return res.end(JSON.stringify({error:'denied'})); }
      const data=JSON.parse(body);
      items=data.operation==='add' ? [...items.filter(i=>i.id!==data.highlight.id),data.highlight] : items.filter(i=>i.id!==data.id);
    }
    return res.end(JSON.stringify({highlights:items}));
  }
  const target = url.pathname === '/fixture.pdf' ? path.join(root,'tests/cases/fixtures/paper-synthetic-means.pdf') : path.join(root,'management_portal',url.pathname);
  if (!target.startsWith(root) || !fs.existsSync(target)) { res.statusCode=404; return res.end(); }
  res.setHeader('Content-Type',target.endsWith('.pdf')?'application/pdf':target.endsWith('.css')?'text/css':'text/javascript');
  res.end(fs.readFileSync(target));
});
(async()=>{
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  const browser=await chromium.launch({headless:true});
  try {
    const page=await browser.newPage({viewport:{width:1440,height:960}});
    const errors=[]; page.on('pageerror',e=>errors.push(e.message));
    await page.goto(`http://127.0.0.1:${server.address().port}`);
    await page.getByText('分析 v0 · 短摘要', {exact:true}).waitFor();
    assert.match(await page.locator('#analysisReaderContent').textContent(), /中文短摘要正文/);
    assert.match(await page.locator('#analysisReaderContent').textContent(), /输入 109.5K · 输出 8.2K · 共 117.7K tokens/);
    assert.equal(await page.locator('#analysisReaderContent span[title]').getAttribute('title'),'输入 109542 · 输出 8205 · 共 117747 tokens');
    assert.equal(await page.getByRole('link',{name:'生成 Agent：resident_0123456789ab',exact:true}).getAttribute('href'),'/lineage?resident=resident_0123456789ab');
    assert.equal(await page.locator('dialog').count(),0);
    await page.getByRole('button',{name:'全屏阅读',exact:true}).click();
    await page.waitForFunction(()=>document.querySelector('[data-pdf-text-layer-state="done"]') && document.querySelector('[role=status]').textContent.includes('选中文字'), null, {timeout:10000}).catch(async error => {
      console.error({errors, status:await page.locator('[role=status]').textContent(), pdfStatus:await page.locator('.paperReadingPdf').getAttribute('data-pdf-status')});
      throw error;
    });
    const bounds=await page.locator('dialog').boundingBox();
    assert.equal(bounds.width,1440); assert.equal(bounds.height,960);
    async function selectText(){
      await page.evaluate(()=>{
        const text=[...document.querySelectorAll('.paperReadingPdf .textLayer span')].find(n=>n.textContent.trim().length>5);
        const range=document.createRange();range.selectNodeContents(text);
        const selection=window.getSelection();selection.removeAllRanges();selection.addRange(range);
        text.dispatchEvent(new MouseEvent('mouseup',{bubbles:true}));
      });
    }
    const text=page.locator('.paperReadingPdf .textLayer span').filter({hasText:/\S{6}/}).first();
    const textBox=await text.boundingBox();
    await page.mouse.move(textBox.x+2,textBox.y+textBox.height/2);
    await page.mouse.down();
    await page.mouse.move(textBox.x+textBox.width-2,textBox.y+textBox.height/2,{steps:8});
    assert.equal(await page.getByRole('toolbar',{name:'高亮操作'}).count(),0);
    await page.mouse.up();
    const actions=page.getByRole('toolbar',{name:'高亮操作'});
    await actions.waitFor();
    const popup=await actions.boundingBox();
    assert.ok(popup.y > textBox.y && popup.y < textBox.y+textBox.height+30,'actions are beside the selection');
    await actions.getByRole('button',{name:'高亮',exact:true}).click();
    await page.waitForFunction(()=>document.querySelector('[role=status]').textContent==='已保存');
    assert.equal(items.length,1);
    assert.equal(await actions.count(),0);
    async function clickHighlight(){
      const mark=page.locator('.paperHighlightLayer span').first();
      const b=await mark.boundingBox();
      await page.mouse.click(b.x+b.width/2,b.y+b.height/2);
      await actions.getByRole('button',{name:'删除高亮',exact:true}).waitFor();
    }
    await clickHighlight();
    await page.keyboard.press('Escape');
    assert.equal(await actions.count(),0); assert.equal(await page.locator('dialog').count(),1);
    const position=await page.locator('.paperHighlightLayer span').first().getAttribute('style');
    await page.getByRole('button',{name:'放大',exact:true}).click();
    await page.waitForFunction(()=>document.querySelector('[data-pdf-redraw-count]')?.dataset.pdfRedrawCount>'0');
    assert.equal(await page.locator('.paperHighlightLayer span').first().getAttribute('style'),position);
    await page.setViewportSize({width:1000,height:700});
    await page.waitForTimeout(150);
    assert.equal(await page.locator('.paperHighlightLayer span').first().getAttribute('style'),position);
    await page.getByRole('button',{name:'适应宽度',exact:true}).click();
    await page.waitForFunction(()=>document.querySelector('.paperReadingPdf .pdfJsPage').getBoundingClientRect().width < 1000);
    await page.locator('.paperReadingPdf [data-pdf-page="2"]').scrollIntoViewIfNeeded();
    await page.keyboard.press('Escape'); assert.equal(await page.locator('dialog').count(),0);
    await page.waitForFunction(()=>document.getElementById('paperPdfViewer')?.scrollTop > 100);
    await page.reload(); await page.getByRole('button',{name:'全屏阅读',exact:true}).click();
    await page.waitForFunction(()=>document.querySelector('.paperHighlightLayer span'));
    await clickHighlight();
    assert.equal(await page.locator('.paperReadingHighlights').isVisible(),false);
    failSave=true; await actions.getByRole('button',{name:'删除高亮',exact:true}).click();
    await page.waitForFunction(()=>document.querySelector('[role=status]').textContent.includes('保存失败'));
    assert.equal(items.length,1); assert.equal(await page.locator('.paperHighlightLayer span').count()>0,true);
    failSave=false; await actions.getByRole('button',{name:'删除高亮',exact:true}).click();
    await page.waitForFunction(()=>document.querySelector('[role=status]').textContent==='已保存');
    assert.equal(items.length,0); assert.equal(await page.locator('.paperHighlightLayer span').count(),0);
    // Keyboard selections use the same popover; clicking elsewhere dismisses it.
    await selectText(); await actions.waitFor();
    await page.keyboard.press('Escape');
    assert.equal(await actions.count(),0); assert.equal(await page.locator('dialog').count(),1);
    await selectText(); await actions.waitFor();
    await page.locator('.paperReadingStatus').click();
    assert.equal(await actions.count(),0);
    // An add failure must not paint an unsaved mark; the same action can retry.
    await selectText(); await actions.waitFor(); failSave=true;
    await actions.getByRole('button',{name:'高亮',exact:true}).click();
    await page.waitForFunction(()=>document.querySelector('[role=status]').textContent.includes('保存失败'));
    assert.equal(items.length,0); assert.equal(await page.locator('.paperHighlightLayer span').count(),0);
    failSave=false; await actions.getByRole('button',{name:'高亮',exact:true}).click();
    await page.waitForFunction(()=>document.querySelector('[role=status]').textContent==='已保存');
    await page.getByRole('button',{name:'我的高亮',exact:true}).click();
    await page.locator('.paperReadingHighlights').getByRole('button',{name:'删除高亮',exact:true}).click();
    await page.waitForFunction(()=>document.querySelector('[role=status]').textContent==='已保存' && !document.querySelector('.paperHighlightLayer span'));
    assert.equal(items.length,0);
    await page.getByRole('button',{name:'退出全屏',exact:true}).click();
    assert.equal(await page.locator('dialog').count(),0);
    assert.deepEqual(errors,[]);
    console.log('PASS: real drag selection, nearby highlight/delete, Escape/outside dismissal, zoom/resize, reload, failed add/delete retry, sidebar, fullscreen cleanup');
  } finally {await browser.close(); server.close();}
})().catch(error=>{console.error(error);server.close();process.exitCode=1;});
