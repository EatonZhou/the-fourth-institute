'use strict';
// A single Chromium instance renders the actual LibreOffice output with the shared Preview.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('../../../management_portal/test_framework/tau_runtime/node_modules/playwright');
const root = path.resolve(__dirname, '../../../management_portal/static');
const pdf = fs.readFileSync(process.argv[2]);
const html = `<!doctype html><html><head><link rel="stylesheet" href="/static/portal_workbench.css"></head><body>
<a id="download">Download</a><div id="preview" style="height:650px;width:900px;overflow:auto"></div>
<script>
const template = (parts, ...args) => parts.reduce((s, part, i) => s + part + (args[i] ?? ''), '');
window.PortalI18n = { t: text => text, html: template, text: template };
window.PortalUIPrimitives = { escapeHtml: value => String(value).replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('"', '&quot;') };
</script>
${['portal_preview_contract', 'portal_preview_runtime', 'portal_pdf_preview', 'portal_preview_renderers'].map(name => '<script src="/static/' + name + '.js"></script>').join('')}
</body></html>`;
(async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    const page = await browser.newPage({ viewport: { width: 1024, height: 850 } });
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    let convertedRequests = 0;
    await page.route('http://preview.test/**', async route => {
      const url = new URL(route.request().url());
      if (url.pathname.startsWith('/static/')) {
        const filename = path.join(root, url.pathname.slice(8));
        return route.fulfill({ path: filename });
      }
      if (url.pathname === '/api/resources/content') {
        if (url.searchParams.get('download') === '1') return route.fulfill({ body: Buffer.from('original-pptx'),
          headers: { 'content-disposition': 'attachment; filename="slides.pptx"' } });
        assert.equal(url.searchParams.get('preview'), 'pdf');
        convertedRequests += 1;
        return route.fulfill({ contentType: 'application/pdf', body: pdf });
      }
      return route.fulfill({ contentType: 'text/html', body: html });
    });
    await page.goto('http://preview.test/');
    await page.evaluate(async () => {
      const document = { path: 'workspace_0123456789ab/file/slides.pptx', kind: 'binary',
        rawUrl: '/api/resources/content?address=workspace_0123456789ab%2Ffile%2Fslides.pptx',
        downloadUrl: '/api/resources/content?address=workspace_0123456789ab%2Ffile%2Fslides.pptx&download=1' };
      window.PortalPreview.configureDownloadLink(window.document.getElementById('download'), { url: document.downloadUrl, filename: 'slides.pptx' });
      window.previewHandle = window.PortalPreview.render({ root: '#preview', document });
      await window.previewHandle.promise;
    });
    await page.waitForFunction(() => document.querySelector('[data-portal-pdf-viewer]')?.dataset.pdfStatus === 'done');
    assert.equal(await page.locator('.previewPdfPage').count(), 2);
    await page.waitForFunction(() => document.querySelector('#preview').textContent.includes('中文幻灯片'));
    await page.locator('.previewPdfPage').nth(1).scrollIntoViewIfNeeded();
    await page.waitForFunction(() => document.querySelector('#preview').textContent.includes('Second slide'));
    assert.ok(await page.locator('canvas').count() >= 2);
    await page.locator('[data-preview-zoom-in]').click();
    await page.waitForFunction(() => Number(document.querySelector('#preview').dataset.previewZoom) > 1);
    const downloadEvent = page.waitForEvent('download');
    await page.locator('#download').click();
    const downloaded = await downloadEvent;
    assert.equal(downloaded.suggestedFilename(), 'slides.pptx');
    assert.equal(page.url(), 'http://preview.test/');
    assert.ok(convertedRequests >= 1);
    assert.deepEqual(errors, []);
    console.log('Chromium: two presentation pages, Chinese text, scrolling, zoom and original download passed');
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
