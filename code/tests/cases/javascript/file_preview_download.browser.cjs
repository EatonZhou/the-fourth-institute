'use strict';
// Exercise the actual FilePreview -> PanelPackage toolbar, not an anchor wired
// directly from the fixture. Only the authorized source/HTTP transport is mocked.
const assert = require('node:assert/strict');
const {execFileSync} = require('node:child_process');
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const {chromium} = require('../../../management_portal/test_framework/tau_runtime/node_modules/playwright');
const root = path.resolve(__dirname, '../../../management_portal');
const base = process.env.D066_TEST_BASE;
const baselineFilePreview = base ? execFileSync('git', ['show', `${base}:code/management_portal/static/portal_file_preview.js`], {cwd: root}) : null;
const groups = JSON.parse(fs.readFileSync(path.join(root, 'script_groups.json')));
const scripts = ['portal_state.js', 'portal_page_state.js', 'portal_text.js', 'portal_translations.js',
  'portal_i18n.js', 'portal_ui_primitives.js', 'portal_document_statistics.js',
  'portal_preview_contract.js', 'portal_preview_runtime.js', 'portal_pdf_preview.js', 'portal_preview_renderers.js',
  ...groups['portal-workspace-surface'].map(value => value.replace(/^\/static\//, '')),
  'portal_agent_preview_source.js', 'markdown.js', 'portal_file_preview.js'];
const files = [
  {name: '第一份 原稿.docx', type: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document', bytes: Buffer.from('PK synthetic Word original, not the preview PDF')},
  {name: '另一份说明.txt', type: 'text/plain', bytes: Buffer.from('Different file contents')},
];
const downloads = [];
const server = http.createServer((req, res) => {
  const url = new URL(req.url, 'http://fixture');
  if (url.pathname.startsWith('/file/')) {
    const index = Number(url.pathname.split('/').at(-1));
    const file = files[index];
    if (!file) { res.statusCode = 404; return res.end(); }
    if (url.searchParams.get('preview') === 'pdf') {
      res.setHeader('Content-Type', 'application/pdf');
      return res.end(fs.readFileSync(path.join(__dirname, '../fixtures/paper-synthetic-counts.pdf')));
    }
    res.setHeader('Content-Type', file.type);
    if (url.searchParams.get('download') === '1') {
      downloads.push(index);
      res.setHeader('Content-Disposition', "attachment; filename*=UTF-8''" + encodeURIComponent(file.name));
    }
    return res.end(file.bytes);
  }
  if (url.pathname === '/') {
    res.setHeader('Content-Type', 'text/html');
    return res.end('<!doctype html><meta charset="utf-8"><link rel="stylesheet" href="/static/portal_design_system.css"><style>#root{height:700px}</style>'
      + scripts.map(name => `<script src="/static/${name}"></script>`).join('') + '<div id="root"></div>');
  }
  const file = path.join(root, url.pathname);
  if (!file.startsWith(root + '/static/') || !fs.existsSync(file)) { res.statusCode = 404; return res.end(); }
  res.setHeader('Content-Type', file.endsWith('.css') ? 'text/css' : 'text/javascript');
  if (baselineFilePreview && url.pathname === '/static/portal_file_preview.js') return res.end(baselineFilePreview);
  res.end(fs.readFileSync(file));
});

(async () => {
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const browser = await chromium.launch({headless: true});
  try {
    const page = await browser.newPage();
    page.setDefaultTimeout(10000);
    const errors = []; page.on('pageerror', error => errors.push(error.message));
    const origin = `http://127.0.0.1:${server.address().port}`;
    await page.goto(origin);
    await page.evaluate(names => {
      window.PortalIdentity = {peek: () => ({username: 'isolated-download-fixture'})};
      window.PortalAPI = {get: async () => ({}), post: async () => ({})};
      window.PortalResidentService = {};
      window.PortalFileLinks = {describe: () => null};
      window.denied = false;
      window.PortalResourceChips = {
        resourceAddressFromPreviewRequest: request => request.resourceAddress,
        async loadPreview(address) {
          if (denied) throw Object.assign(new Error('Read denied'), {status: 403});
          const index = Number(address.at(-1));
          return {source: 'resource', resource_address: address, name: names[index], title: 'Display title, not a filename',
            kind: index === 0 ? 'office' : 'text', content: index === 0 ? '' : 'Different file contents',
            raw_url: `/file/${index}`, content_url: `/file/${index}`,
            preview_url: index === 0 ? `/file/${index}?preview=pdf` : '',
            download_url: `/file/${index}?download=1`, capabilities: {edit: false, save: false}};
        },
      };
      const panel = PortalWorkspaceSurface.panelPackages.preview.create({root: document.querySelector('#root'), explorer: false});
      const body = panel.surface.element('preview'); body.replaceChildren();
      window.entries = names.map(() => {
        const root = document.createElement('div'); body.append(root);
        let file;
        const update = () => {
          if (window.activeFile !== file) return;
          const toolbar = file.toolbar();
          panel.setPresentation({tabs: [], path: file.snapshot().payload?.resource_address || '',
            document: toolbar.document, download: toolbar.download, actions: toolbar.actions});
        };
        file = PortalFilePreview.create({root, onChange: () => queueMicrotask(update)});
        return {root, file, update};
      });
      window.selectFile = async index => {
        for (const entry of entries) if (entry.file === window.activeFile) entry.file.setPresented(false);
        const entry = entries[index]; window.activeFile = entry.file;
        entries.forEach(value => { value.root.hidden = value !== entry; });
        await entry.file.setPresented(true);
        await entry.file.show({address: `article_0123456789a${index}`}); entry.update();
      };
    }, files.map(file => file.name));
    if (process.argv.includes('--workspace-contract-only')) {
      const workspace = 'workspace_0123456789ab', address = `${workspace}/file/fixture-1`;
      const cases = await page.evaluate(async ({ workspace, address }) => {
        const results = [];
        for (const allowed of [true, false]) {
          const calls = [];
          window.PortalInstructionClient = { execute: async (name, args) => {
            calls.push([name, args]);
            if (name === 'ResolveResource') return { target: { target_kind: 'file', exists: true, address,
              file_reference: { workspace_id: workspace, relative_path: 'fixture-1' } } };
            return { allowed };
          } };
          const root = document.createElement('div');
          document.body.append(root);
          const preview = PortalFilePreview.create({ root });
          await preview.show({ address });
          const toolbar = preview.toolbar();
          results.push({ calls, editable: !toolbar.actions.code.disabled, download: toolbar.download?.url });
          preview.destroy();
          root.remove();
        }
        return results;
      }, { workspace, address });
      for (const [index, editable] of [true, false].entries()) {
        assert.deepEqual(cases[index], { calls: [
          ['ResolveResource', { address }],
          ['CheckInstructionPermission', { principal_id: 'isolated-download-fixture', instruction: 'UpdateWorkspaceFile', arguments: { address } }],
        ], editable, download: '/file/1?download=1' });
      }
      assert.deepEqual(errors, []);
      console.log('PASS FilePreview checks the named write instruction against the canonical workspace address');
      return;
    }
    const button = page.locator('.portalPreviewDownloadButton');
    for (const index of [0, 1, 0]) {
      await page.evaluate(index => selectFile(index), index);
      assert.equal(await button.getAttribute('href'), `/file/${index}?download=1`, 'toolbar must use the current original download URL, never raw or preview');
      assert.equal(await page.evaluate(() => activeFile.toolbar().download.filename), files[index].name);
      const pending = page.waitForEvent('download');
      await button.click();
      const download = await pending;
      assert.equal(download.suggestedFilename(), files[index].name);
      assert.deepEqual(fs.readFileSync(await download.path()), files[index].bytes);
      assert.equal(page.url(), origin + '/', 'download keeps the preview page');
    }
    await page.evaluate(async () => { window.denied = true; await activeFile.show({address: 'article_0123456789a0'}); });
    assert.equal(await button.getAttribute('href'), null, 'failed authorization removes the previous download target');
    assert.equal(await button.isVisible(), false);
    await page.evaluate(() => entries.forEach(entry => entry.file.destroy()));
    assert.deepEqual(downloads, [0, 1, 0]);
    assert.deepEqual(errors, []);
    console.log('PASS FilePreview toolbar original download, filename/bytes, switching/restoring and denied access');
  } finally { await browser.close(); server.closeAllConnections(); await new Promise(resolve => server.close(resolve)); }
})().catch(error => { console.error(error); process.exitCode = 1; });
