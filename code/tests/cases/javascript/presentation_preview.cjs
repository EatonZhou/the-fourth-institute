'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { JSDOM } = require('jsdom');
const dom = new JSDOM('<div id="preview"></div>', { url: 'https://portal.test/', runScripts: 'outside-only' });
const w = dom.window;
const template = (parts, ...args) => parts.reduce((s, part, i) => s + part + (args[i] ?? ''), '');
w.PortalI18n = { t: text => text, html: template, text: template };
w.PortalUIPrimitives = { escapeHtml: value => String(value).replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('"', '&quot;') };
const renderers = [];
w.PortalPreview = {
  registerRenderer: renderer => { renderers.push(renderer); return () => {}; },
  mountZoom: () => ({ setEnabled() {}, dispose() {} }),
};
let pdfOptions;
w.PortalPdfPreview = { render: options => { pdfOptions = options; return { promise: Promise.resolve({}), cancel() {} }; } };
for (const name of ['portal_preview_contract.js', 'portal_preview_renderers.js']) {
  w.eval(fs.readFileSync(path.resolve(__dirname, '../../../management_portal/static', name), 'utf8'));
}
const contract = w.PortalPreviewContract;
const cases = [
  { kind: 'binary', path: '/share/slides.PPTX', raw_url: '/api/agent-workbench/link-file-raw?path=%2Fshare%2Fslides.PPTX', download_url: '/api/agent-workbench/link-file-raw?path=%2Fshare%2Fslides.PPTX&download=1' },
  { kind: 'office', path: 'workspace_0123456789ab/file/slides.ppt', raw_url: '/api/resources/content?address=workspace_0123456789ab%2Ffile%2Fslides.ppt' },
  { kind: 'binary', filename: '幻灯片.pptx', raw_url: '/api/uploads/upload_0123456789abcdef01234567' },
  { content_type: 'application/vnd.ms-powerpoint', raw_url: '/api/resources/content?address=upload_0123456789ab' },
];
for (const input of cases) {
  const doc = contract.normalizeDocument(input);
  assert.equal(doc.contentKind, 'office');
  assert.match(doc.previewUrl, /preview=pdf/);
  assert.equal(doc.rawUrl, input.raw_url);
  assert.equal(doc.downloadUrl, input.download_url || '');
  assert.equal(contract.isTextDocument(doc), false);
  const renderer = renderers.find(item => item.accepts(doc) && item.id === 'pdf');
  assert.ok(renderer);
  renderer.render(w.document.getElementById('preview'), doc, {});
  assert.match(pdfOptions.url, /preview=pdf/);
  assert.match(pdfOptions.loadingMessage, /转换/);
  assert.match(pdfOptions.errorMessage, /下载/);
}
for (const input of [
  { path: '/slides.docx', raw_url: '/api/resources/content?address=doc' },
  { path: '/slides.pptx', raw_url: 'https://external.test/api/resources/content?address=ppt' },
  { path: '/slides.pptx', raw_url: '/unsupported-endpoint' },
  { path: '/slides.pptx', raw_url: 'http://[' },
]) assert.equal(contract.normalizeDocument(input).previewUrl, '');
assert.equal(contract.normalizeDocument({ kind: 'pdf', raw_url: '/slides.pdf' }).contentKind, 'pdf');
// Conversion failures stay in Preview and do not load the error URL in an iframe.
w.eval(fs.readFileSync(path.resolve(__dirname, '../../../management_portal/static/portal_pdf_preview.js'), 'utf8').replace('if (window.PortalPdfPreview) return;', ''));
(async () => {
  const root = w.document.getElementById('preview');
  const viewer = w.PortalPdfPreview.render({ root, url: '/failed.pptx?preview=pdf', errorMessage: 'Download original',
    ensurePdfJs: () => Promise.reject(new Error('conversion failed')) });
  await viewer.promise;
  assert.equal(root.dataset.pdfStatus, 'error');
  assert.equal(root.querySelector('iframe'), null);
  assert.equal(root.querySelector('[role="alert"]').textContent, 'Download original');
  viewer.cancel();
  dom.window.close();
  console.log('Presentation contract, renderer selection and failure UI passed');
})().catch(error => { console.error(error); process.exitCode = 1; });
