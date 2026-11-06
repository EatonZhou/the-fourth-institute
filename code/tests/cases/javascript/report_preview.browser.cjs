'use strict';
// Native resource loading and shared Preview, with the production report view.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const http = require('node:http');
const path = require('node:path');
const {createHash} = require('node:crypto');
const { chromium } = require('../../../management_portal/test_framework/tau_runtime/node_modules/playwright');
const base = path.resolve(__dirname, '../../../management_portal/static');
const id = 'report_123456789abc';
const packageAddress='workspace_123456789abc/file/lesson/manifest.json';
const notesAddress='workspace_123456789abc/file/lesson/notes.md';
const notes='## 解释内容\n\n这是固定解释包的正文。';
const scrollOnly=process.argv.includes('--scroll-only');
const lifecycle=process.argv.find(value=>value.startsWith('--lifecycle='))?.split('=')[1];
const interactionAddress='workspace_123456789abc/file/lesson/interaction.html';
const interaction='<h2>图文起点</h2>'+Array.from({length:30},(_,i)=>`<p style="padding:24px">第${i+1}步：继续向下阅读长图文。</p>`).join('')+'<button id="end">图文结尾</button>';
const sha=value=>createHash('sha256').update(value).digest('hex');
const manifest=JSON.stringify({schema:'portal-explanation/v1',title:'汇报中的解释',revision:1,quality:'low',supplement:{address:notesAddress,sha256:sha(notes)},...(scrollOnly?{interaction:{address:interactionAddress,sha256:sha(interaction)}}:{})});
const snapshot = { schema: 'tfi.home-report.v1', report_id: id, revision: 3, latest_revision: 4,
  title: '本轮进展', goal: '阅读已保存的成果', quality: 'medium', format: 'interactive',
  target: { kind: 'project', id: 'fixture' }, version: 'a'.repeat(64), checked_at: '2026-11-05T00:00:00Z',
  sources: [], warnings: [], counts: { '已完成': 1 }, reasoning: { available: false }, exports: [],
  revised_content: true, markdown: '**已经修复下载问题。**\n\n[完整说明](/r/workspace_123456789abc/file/supplement.md)',
  rows: [{ id: 'fix', title: '附件下载已恢复', body: '可以下载原附件。', status: '已完成', sources: [], replies: [] }] };
const scripts = ['portal_text','portal_state','portal_i18n','portal_ui_primitives','portal_display_protocol','portal_preview_contract', 'portal_preview_runtime', 'portal_pdf_preview', 'portal_preview_renderers', 'portal_resource_chips'];
const html = `<!doctype html>${scrollOnly?'<link rel="stylesheet" href="/static/portal_workbench.css"><link rel="stylesheet" href="/static/portal_file_preview.css"><div class="portalFileView" style="height:640px"><span class="portalFileSaveStatus">只读</span><div class="portalFileDocument"><main id="preview"></main></div></div>':'<main id="preview" style="height:640px"></main>'}<script>
const template=(p,...v)=>p.reduce((s,t,i)=>s+t+(v[i]??''),'');
window.PortalI18n={t:v=>v,html:template,text:template};
window.PortalCompatibility={schemaMatches:(a,b)=>a===b};
window.PortalResourceRoutes={schema:'tfi.resource-portal-routes.v1',text_resource_types:['report'],navigation_resource_types:[],identity_parameters:[],routes:[]};
window.PortalAPI={raw:(url,options)=>fetch(url,options)};
</script>${scripts.map(name => `<script src="/static/${name}.js"></script>`).join('')}`;
(async () => {
  const requests = []; let denied = false, explanationMode=scrollOnly || Boolean(lifecycle);
  const server = http.createServer((req, res) => {
    const url = new URL(req.url, 'http://localhost');
    requests.push({ path: url.pathname, query: url.search, method: req.method });
    if (url.pathname.startsWith('/static/')) {
      const filename = path.join(base, path.basename(url.pathname));
      res.setHeader('Content-Type', (filename.endsWith('.css') ? 'text/css' : 'text/javascript') + '; charset=utf-8');
      return res.end(fs.readFileSync(filename));
    }
    if (url.pathname === '/api/resources/content' || url.pathname === '/api/home-report') {
      const address=url.searchParams.get('address');
      if(address===interactionAddress) {res.setHeader('Content-Type','text/html');return res.end(interaction);}
      if(address===packageAddress || address===notesAddress) {
        res.setHeader('Content-Type',address===packageAddress?'application/json':'text/markdown');
        return res.end(address===packageAddress?manifest:notes);
      }
      res.writeHead(denied ? 403 : 200, { 'Content-Type': 'application/json' });
      return res.end(JSON.stringify(denied ? { error: '权限已撤回' } : explanationMode ? {
        ...snapshot,content_kind:'explanation',explanation_target:{address:packageAddress,sha256:sha(manifest)},
        reference:{view:'report',target:{kind:'report',id,revision:3}}
      } : snapshot));
    }
    if(url.pathname==='/api/home-report/explanations') {
      res.setHeader('Content-Type','application/json');
      return res.end(JSON.stringify({reference:{view:'report',title:'汇报中的解释',target:{kind:'report',id,revision:3}}}));
    }
    if(url.pathname==='/api/home-explanation/media') {
      res.setHeader('Content-Type','application/json');return res.end('{"media":null}');
    }
    if (url.pathname === '/api/home-report/validate') {
      res.writeHead(denied ? 403 : 200, { 'Content-Type': 'application/json' });
      return res.end(JSON.stringify(denied ? { error: '权限已撤回' } : { readable: true, source_changed: false }));
    }
    res.setHeader('Content-Type', 'text/html; charset=utf-8');
    res.end(html);
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const browser = await chromium.launch({ headless: true });
  try {
    const page = await browser.newPage({ viewport: { width: 900, height: 800 } });
    page.setDefaultTimeout(7000);
    const errors=[];
    page.on('pageerror', error => { errors.push(error.message);console.error('browser:', error.message); });
    await page.goto(`http://127.0.0.1:${server.address().port}/`);
    if(lifecycle) {
      await require('./report_preview_lifecycle.cjs')({page,id,packageAddress,lifecycle});
      assert.deepEqual(errors,[],'no unhandled browser errors');
      assert.equal(requests.filter(r=>r.method!=='GET' && r.path!=='/api/home-report/validate').length,0,'opening and retrying never creates content');
      return;
    }
    const observed = await page.evaluate(async id => {
      const payload = await PortalResourceChips.loadPreview(id, { reference: {
        resource_type: 'report', content_kind: 'json', media_type: 'application/json', filename: 'report.json', title: '本轮进展'
      } });
      window.payload = payload;
      const handle = PortalPreview.render({ root: '#preview', document: payload });
      await handle.promise;
      return { renderer: handle.renderer, frames: document.querySelectorAll('iframe').length };
    }, id);
    assert.equal(observed.renderer, 'report');
    if(scrollOnly) {
      await page.waitForFunction(()=>document.querySelector('.homeExplanationInteraction')?.offsetHeight>2000);
      const demo=page.frameLocator('.homeExplanationInteraction').frameLocator('iframe');
      for(const width of [900,390]) {
        await demo.locator('#end').evaluate(el=>el.blur());
        await page.setViewportSize({width,height:800});
        await page.locator('.portalFileDocument').evaluate(el=>{el.scrollTop=0;});
        const explanation=page.locator('.homeExplanation');
        const offset=()=>explanation.evaluate(el=>el.scrollTop-el.getBoundingClientRect().top);
        const start=await offset();
        const surface=await page.locator('.homeExplanationInteraction').boundingBox();
        await page.mouse.move(surface.x+30,surface.y+20);
        await page.mouse.wheel(0,550);
        await page.waitForFunction(start=>{const el=document.querySelector('.homeExplanation');return el.scrollTop-el.getBoundingClientRect().top>start+300;},start);
        const down=await offset();
        await page.mouse.wheel(0,-300);
        await page.waitForFunction(down=>{const el=document.querySelector('.homeExplanation');return el.scrollTop-el.getBoundingClientRect().top<down-150;},down);
        assert.equal(await page.evaluate(()=>window.scrollY),0,'Preview scrolling must not move the page');
        await demo.locator('#end').focus();
        const end=await demo.locator('#end').boundingBox();
        assert.ok(end.y>=0 && end.y+end.height<=650,`keyboard reaches the end within the Preview viewport: ${JSON.stringify({width,end,viewport:await page.locator('.portalFileDocument').boundingBox(),scrollY:await page.evaluate(()=>scrollY)})}`);
        assert.equal(await demo.locator('html').evaluate(()=>scrollY),0,'illustration has no nested scroll viewport');
        assert.ok(await page.locator('.portalFileDocument').evaluate(el=>el.scrollWidth<=el.clientWidth+1),'narrow Preview has no horizontal overflow');
        if(process.env.SCREENSHOT_DIR) {
          fs.mkdirSync(process.env.SCREENSHOT_DIR,{recursive:true});
          await page.screenshot({path:path.join(process.env.SCREENSHOT_DIR,`report-scroll-${width}.png`)});
        }
      }
      assert.deepEqual(errors,[],'no browser errors');
      assert.equal(requests.filter(r=>r.method!=='GET' && r.path!=='/api/home-report/validate').length,0);
      console.log('report scroll: wheel over sandboxed content, keyboard end, bounded viewport, read-only passed');
      return;
    }
    assert.equal(observed.frames,0,'saved report renders in the Preview document, without a second page frame');
    const frame = page.locator('#preview');
    await frame.getByRole('heading', { name: '本轮进展' }).waitFor();
    await frame.locator('.hr-markdown strong').getByText('已经修复下载问题。').waitFor();
    assert.equal(await frame.getByRole('link', { name: '完整说明', exact: true }).getAttribute('href'), '/r/workspace_123456789abc/file/supplement.md');
    await frame.getByRole('button', { name: '全部 1', exact: true }).click();
    await frame.getByText('附件下载已恢复', { exact: true }).first().waitFor();
    assert.equal(requests.filter(r => r.method !== 'GET' && r.path !== '/api/home-report/validate').length, 0,
      'opening only reads and rechecks permission; it does not create or refresh a report');
    assert.ok(requests.some(r => r.path === '/api/home-report' && r.query.includes('revision=3')));
    denied = true;
    await page.evaluate(() => window.dispatchEvent(new Event('portal:permissions-changed')));
    await frame.getByText('权限已撤回', { exact: false }).first().waitFor();
    assert.equal(await frame.getByRole('heading', { name: '本轮进展' }).count(), 0);
    const checks = await page.evaluate(() => {
      const root = document.querySelector('#preview');
      const normal = PortalPreview.render({ root, document: { ...payload, source: 'workspace_link', resource_type: '', path: 'snapshot.json' } });
      const removed = !root.querySelector('.home-report');
      const file = PortalPreview.render({ root, document: { ...payload, resource_address: payload.address + '/revision/3/file/report.json' } });
      let mismatch = false;
      try { PortalPreview.render({ root, document: { ...payload, resource_address: payload.address + '/revision/2' } }); }
      catch (error) { mismatch = /版本/.test(error.message); }
      return { normal: normal.renderer, file: file.renderer, removed, mismatch };
    });
    assert.deepEqual(checks, { normal: 'text', file: 'text', removed: true, mismatch: true });
    denied=false; explanationMode=true;
    const canonical=await page.evaluate(async address=>{
      const document=await PortalResourceChips.loadPreview(address,{reference:{resource_type:'report',content_kind:'json',media_type:'application/json',filename:'manifest.json'}});
      window.explanationHandle=PortalPreview.render({root:'#preview',document});
      await explanationHandle.promise;
      return document.path;
    },packageAddress);
    assert.equal(canonical,id+'/revision/3','legacy package lookup reads the report identity without a write');
    await page.getByRole('heading',{name:'汇报中的解释',exact:true}).waitFor();
    await page.getByText('这是固定解释包的正文。',{exact:true}).waitFor();
    assert.equal(requests.filter(r=>r.method!=='GET' && r.path!=='/api/home-report/validate').length,0);
    denied=true;
    await page.evaluate(()=>window.dispatchEvent(new Event('portal:permissions-changed')));
    await page.getByText(/来源暂不可读，已撤去报告/).waitFor();
    assert.equal(await page.getByRole('heading',{name:'汇报中的解释',exact:true}).count(),0,
      'report permission withdrawal removes the delegated explanation even while its Workspace remains readable');
    console.log('report preview: fixed revision, interactive report, read-only reopen, permission withdrawal, disposal and JSON isolation passed');
  } finally {
    await browser.close();
    await new Promise(resolve => server.close(resolve));
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
