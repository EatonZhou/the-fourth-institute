'use strict';
// Native resource loading and shared Preview, with the production report view.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const http = require('node:http');
const path = require('node:path');
const { chromium } = require('../../../management_portal/test_framework/tau_runtime/node_modules/playwright');
const base = path.resolve(__dirname, '../../../management_portal/static');
const id = 'report_123456789abc';
const snapshot = { schema: 'tfi.home-report.v1', report_id: id, revision: 3, latest_revision: 4,
  title: '本轮进展', goal: '阅读已保存的成果', quality: 'medium', format: 'interactive',
  target: { kind: 'project', id: 'fixture' }, version: 'a'.repeat(64), checked_at: '2026-11-05T00:00:00Z',
  sources: [], warnings: [], counts: { '已完成': 1 }, reasoning: { available: false }, exports: [],
  revised_content: true, markdown: '**已经修复下载问题。**\n\n[完整说明](/r/workspace_123456789abc/file/supplement.md)',
  rows: [{ id: 'fix', title: '附件下载已恢复', body: '可以下载原附件。', status: '已完成', sources: [], replies: [] }] };
const scripts = ['portal_text','portal_state','portal_i18n','portal_ui_primitives','portal_display_protocol','portal_preview_contract', 'portal_preview_runtime', 'portal_pdf_preview', 'portal_preview_renderers', 'portal_resource_chips'];
const html = `<!doctype html><main id="preview" style="height:640px"></main><script>
const template=(p,...v)=>p.reduce((s,t,i)=>s+t+(v[i]??''),'');
window.PortalI18n={t:v=>v,html:template,text:template};
window.PortalCompatibility={schemaMatches:(a,b)=>a===b};
window.PortalResourceRoutes={schema:'tfi.resource-portal-routes.v1',text_resource_types:['report'],navigation_resource_types:[],identity_parameters:[],routes:[]};
window.PortalAPI={raw:(url,options)=>fetch(url,options)};
</script>${scripts.map(name => `<script src="/static/${name}.js"></script>`).join('')}`;
(async () => {
  const requests = []; let denied = false;
  const server = http.createServer((req, res) => {
    const url = new URL(req.url, 'http://localhost');
    requests.push({ path: url.pathname, query: url.search, method: req.method });
    if (url.pathname.startsWith('/static/')) {
      const filename = path.join(base, path.basename(url.pathname));
      res.setHeader('Content-Type', (filename.endsWith('.css') ? 'text/css' : 'text/javascript') + '; charset=utf-8');
      return res.end(fs.readFileSync(filename));
    }
    if (url.pathname === '/api/resources/content' || url.pathname === '/api/home-report') {
      res.writeHead(denied ? 403 : 200, { 'Content-Type': 'application/json' });
      return res.end(JSON.stringify(denied ? { error: '权限已撤回' } : snapshot));
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
    page.on('pageerror', error => console.error('browser:', error.message));
    await page.goto(`http://127.0.0.1:${server.address().port}/`);
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
    console.log('report preview: fixed revision, interactive report, read-only reopen, permission withdrawal, disposal and JSON isolation passed');
  } finally {
    await browser.close();
    await new Promise(resolve => server.close(resolve));
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
