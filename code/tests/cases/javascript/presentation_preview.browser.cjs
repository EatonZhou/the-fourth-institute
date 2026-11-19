'use strict';
// A single Chromium instance renders the actual LibreOffice output with the shared Preview.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const http = require('node:http');
const path = require('node:path');
const { chromium } = require('../../../management_portal/test_framework/tau_runtime/node_modules/playwright');
const root = path.resolve(__dirname, '../../../management_portal/static');
const pdfPath = process.argv[2];
const expectedPages = Number(process.argv[3] || 2);
const firstText = process.argv[4] || '中文幻灯片';
const lastText = process.argv[5] || 'Second slide';
const article = process.argv.includes('--article');
let html = `<!doctype html><html><head><link rel="stylesheet" href="/static/portal_workbench.css"></head><body>
<a id="download">Download</a><div id="preview" style="height:650px;width:900px;overflow:auto"></div>
<script>
const template = (parts, ...args) => parts.reduce((s, part, i) => s + part + (args[i] ?? ''), '');
window.PortalI18n = { t: text => text, html: template, text: template };
window.PortalUIPrimitives = { escapeHtml: value => String(value).replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('"', '&quot;') };
</script>
${['portal_preview_contract', 'portal_preview_runtime', 'portal_pdf_preview', 'portal_preview_renderers'].map(name => '<script src="/static/' + name + '.js"></script>').join('')}
</body></html>`;
if (article) {
  const scripts = html.slice(html.indexOf('<script>'), html.lastIndexOf('</body>'));
  html = fs.readFileSync(path.join(root, '../article_workspace.html'), 'utf8')
    .replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi, '')
    .replace('</body>', scripts + '<script src="/static/article_workspace_documents.js"></script></body>');
}
// Stream large presentation PDFs with Range support, just like the content route.
// Sending an entire video-bearing PDF through Playwright's route bridge can
// overwhelm the test transport before the viewer gets a chance to request pages.
function serveFile(request, response, filename, contentType) {
  const size = fs.statSync(filename).size;
  const range = /^bytes=(\d+)-(\d*)$/.exec(request.headers.range || '');
  const start = range ? Number(range[1]) : 0;
  const end = range && range[2] ? Math.min(Number(range[2]), size - 1) : size - 1;
  if (start > end || start >= size) {
    response.writeHead(416, { 'Content-Range': `bytes */${size}` });
    return response.end();
  }
  const headers = { 'Content-Type': contentType, 'Content-Length': end - start + 1, 'Accept-Ranges': 'bytes' };
  if (range) headers['Content-Range'] = `bytes ${start}-${end}/${size}`;
  response.writeHead(range ? 206 : 200, headers);
  const stream = fs.createReadStream(filename, { start, end });
  response.on('close', () => stream.destroy());
  stream.pipe(response);
}
(async () => {
  let convertedRequests = 0;
  const server = http.createServer((request, response) => {
    const url = new URL(request.url, 'http://localhost');
    if (url.pathname.startsWith('/static/')) {
      const filename = path.join(root, url.pathname.slice(8));
      const type = filename.endsWith('.js') ? 'text/javascript' : filename.endsWith('.css') ? 'text/css' : 'application/octet-stream';
      return serveFile(request, response, filename, type);
    }
    if (url.pathname === '/api/articles/article_0123456789ab/file') {
      assert.equal(url.searchParams.get('meta'), '1', 'PPT opens through metadata, never the text reader');
      const filename = url.searchParams.get('path');
      response.writeHead(200, { 'Content-Type': 'application/json' });
      return response.end(JSON.stringify({ path: filename, binary: true, version: '1',
        rawUrl: '/api/articles/article_0123456789ab/raw?path=' + encodeURIComponent(filename),
        downloadUrl: '/api/articles/article_0123456789ab/raw?path=' + encodeURIComponent(filename) + '&download=1' }));
    }
    if (url.pathname === '/api/resources/content' || url.pathname === '/api/articles/article_0123456789ab/raw') {
      if (url.searchParams.get('download') === '1') {
        response.writeHead(200, { 'Content-Disposition': 'attachment; filename="slides.pptx"' });
        return response.end('original-pptx');
      }
      assert.equal(url.searchParams.get('preview'), 'pdf');
      convertedRequests += 1;
      return serveFile(request, response, pdfPath, 'application/pdf');
    }
    response.writeHead(200, { 'Content-Type': 'text/html' });
    response.end(html);
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const origin = `http://127.0.0.1:${server.address().port}/`;
  let browser;
  try {
    browser = await chromium.launch({ headless: true });
    const page = await browser.newPage({ viewport: { width: 1024, height: 850 } });
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.goto(origin);
    await page.evaluate(async article => {
      if (article) {
        const $ = id => window.document.getElementById(id);
        // Workbench normally assigns this pane its size; isolate that boundary
        // while retaining the production editor/Visual DOM and styles.
        const pane = $('articleEditorPane');
        window.document.body.replaceChildren(pane);
        Object.assign(pane.style, { width: '960px', height: '760px', display: 'flex', flexDirection: 'column' });
        // Real Article document lifecycle and Preview/PDF rendering; unrelated
        // editor decorations and navigation services are outside this fixture.
        window.PortalWorkspaceSurface = { renderDocumentTabs() {} };
        const module = window.PortalArticleWorkspaceDocuments;
        const state = { selectedId: 'article_0123456789ab', documents: new Map(), openTabs: [], openFile: '' };
        window.articleDocuments = module.createDocuments({
          ...Object.fromEntries(module.dependencies.map(key => [key, () => {}])), $, state,
          request: async url => { const response = await fetch(url); if (!response.ok) throw new Error(url); return response.json(); },
          editorValue: () => $('articleSourceEditor').value,
          showToast: message => { throw new Error(message); },
        });
        await articleDocuments.openArticleFile('notes/slides.pptx');
        return;
      }
      const document = { path: 'workspace_0123456789ab/file/slides.pptx', kind: 'binary',
        rawUrl: '/api/resources/content?address=workspace_0123456789ab%2Ffile%2Fslides.pptx',
        downloadUrl: '/api/resources/content?address=workspace_0123456789ab%2Ffile%2Fslides.pptx&download=1' };
      window.PortalPreview.configureDownloadLink(window.document.getElementById('download'), { url: document.downloadUrl, filename: 'slides.pptx' });
      window.previewHandle = window.PortalPreview.render({ root: '#preview', document });
      await window.previewHandle.promise;
    }, article);
    const previewRoot = article ? '#articleDocumentPreview' : '#preview';
    const downloadLink = article ? '#articleDownloadFile' : '#download';
    await page.waitForFunction(() => document.querySelector('[data-portal-pdf-viewer]')?.dataset.pdfStatus === 'done');
    assert.equal(await page.locator('.previewPdfPage').count(), expectedPages);
    await page.waitForFunction(({ root, text }) => document.querySelector(root).textContent.includes(text), { root: previewRoot, text: firstText });
    for (let index = 0; index < expectedPages; index += 1) {
      await page.locator('.previewPdfPage').nth(index).scrollIntoViewIfNeeded();
      await page.waitForFunction(index => document.querySelectorAll('.previewPdfPage')[index]?.dataset.pdfRenderState === 'done', index);
    }
    await page.waitForFunction(({ root, text }) => document.querySelector(root).textContent.includes(text), { root: previewRoot, text: lastText });
    assert.ok(await page.locator('canvas').count() >= expectedPages);
    await page.locator('[data-preview-zoom-in]').click();
    await page.waitForFunction(root => Number(document.querySelector(root).dataset.previewZoom) > 1, previewRoot);
    const downloadEvent = page.waitForEvent('download');
    await page.locator(downloadLink).click();
    const downloaded = await downloadEvent;
    assert.equal(downloaded.suggestedFilename(), 'slides.pptx');
    assert.equal(page.url(), origin);
    assert.ok(convertedRequests >= 1);
    if (article) {
      assert.equal(await page.locator('#articleCodeMode').isDisabled(), true);
      assert.equal(await page.locator('#articleVisualEditor').isVisible(), true);
      // Closing disposes the preview; reopening legacy PPT uses the same viewer.
      await page.evaluate(async () => { articleDocuments.clearEditor(); await articleDocuments.openArticleFile('notes/slides.ppt'); });
      await page.waitForFunction(() => document.querySelector('#articleDocumentPreview [data-portal-pdf-viewer]')?.dataset.pdfStatus === 'done');
      assert.match(await page.locator(downloadLink).getAttribute('href'), /slides.ppt&download=1$/);
    }
    assert.deepEqual(errors, []);
    console.log(`Chromium: ${expectedPages} presentation pages, Chinese text, scrolling, zoom and original download passed`);
  } finally {
    await browser?.close();
    server.closeAllConnections();
    await new Promise(resolve => server.close(resolve));
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
