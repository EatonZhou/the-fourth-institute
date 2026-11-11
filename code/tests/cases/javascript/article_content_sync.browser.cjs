'use strict';
// Real Article document/event owners, refresh coordinator and Monaco editor.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const root = path.resolve(__dirname, '../../..');
const staticRoot = path.join(root, 'management_portal/static');
const { chromium } = require(path.join(root, 'management_portal/test_framework/tau_runtime/node_modules/playwright'));
const modules = ['portal_command_registry.js', 'portal_refresh_coordinator.js', 'article_workspace_documents.js',
  'article_workspace_events.js', 'vendor/monaco/vs/loader.js'];
const entry = fs.readFileSync(path.join(staticRoot, 'article_workspace.js'), 'utf8');
const start = entry.indexOf('  const articleContentRefresh =');
const end = entry.indexOf('  const {\n    searchProject,', start);
assert.ok(start > 0 && end > start);
const syncSource = entry.slice(start, end) + '\nwindow.sync = { articleContentRefresh, scheduleArticleContentRefresh };';
const html = fs.readFileSync(path.join(root, 'management_portal/article_workspace.html'), 'utf8')
  .replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi, '').replace(/<link\b[^>]*>/gi, '')
  .replace('</body>', modules.map(name => `<script src="/static/${name}"></script>`).join('') + '</body>');

(async () => {
  const server = http.createServer((req, res) => {
    const url = new URL(req.url, 'http://fixture');
    if (url.pathname === '/') { res.setHeader('Content-Type', 'text/html; charset=utf-8'); return res.end(html); }
    const rel = url.pathname.slice('/static/'.length);
    if (!url.pathname.startsWith('/static/') || rel.includes('..') || (!modules.includes(rel) && !rel.startsWith('vendor/monaco/'))) {
      res.writeHead(404); return res.end();
    }
    res.setHeader('Content-Type', rel.endsWith('.css') ? 'text/css' : 'text/javascript');
    res.end(fs.readFileSync(path.join(staticRoot, rel)));
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  let browser;
  try {
    browser = await chromium.launch({ headless: true });
    const page = await browser.newPage();
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.goto(`http://127.0.0.1:${server.address().port}`);
    await page.evaluate(async () => {
      require.config({ paths: { vs: '/static/vendor/monaco/vs' } });
      await new Promise(resolve => require(['vs/editor/editor.main'], resolve));
      const $ = id => document.getElementById(id);
      const text = (parts, ...values) => parts.reduce((s, part, i) => s + part + (values[i] ?? ''), '');
      window.PortalI18n = { t: s => s, text };
      window.PortalWorkspaceSurface = { renderDocumentTabs() {} };
      window.PortalPreviewContract = { isTextDocument: () => true };
      window.PortalPreview = { normalizeDocument: p => ({ ...p, contentKind: 'text', capabilities: {} }), configureDownloadLink() {}, dispose() {} };
      window.PortalDocumentSession = { create: () => ({ present() {}, updateDraft() {}, destroy() {} }) };
      window.state = { selectedId: 'article_fixture', documents: new Map(), filesByPath: new Map(), openTabs: [],
        openFile: '', source: '', savedSource: '', editorPreferences: { tabSize: 2 }, monacoReady: true };
      const host = $('articleMonacoEditor'); host.style.cssText = 'height:300px;width:800px';
      state.editor = monaco.editor.create(host, { value: '', language: 'plaintext' });
      window.remote = { content: 'line one\nline two', version: '1', revision: 'r1' };
      window.notices = []; window.reads = 0; window.fail = false; window.hold = false;
      const deps = Object.fromEntries(PortalArticleWorkspaceDocuments.dependencies.map(key => [key, () => {}]));
      window.docs = PortalArticleWorkspaceDocuments.createDocuments({ ...deps, $, state,
        editorValue: () => state.editor.getValue(), monacoApi: () => monaco,
        articleModelUri: p => monaco.Uri.parse('file:///' + p), languageForPath: () => 'plaintext',
        showToast: message => notices.push(message),
        request: async url => {
          reads++;
          if (fail) throw new Error('temporary network failure');
          const snapshot = { ...remote, size: remote.content.length };
          if (hold) { hold = false; await new Promise(resolve => { window.releaseRead = resolve; }); }
          return url.includes('meta=1') ? { version: snapshot.version, size: snapshot.size } : snapshot;
        },
      });
      window.unregisteringArticles = new Set();
      window.loadFiles = async context => {
        if (!context.isCurrent()) return false;
        state.filesByPath = new Map([['main.tex', {}]]); return false;
      };
      window.refreshArticleDocuments = docs.refreshArticleDocuments;
      window.loadSemanticIndex = async () => {};
      await docs.openArticleFile('main.tex');
      state.editor.setPosition({ lineNumber: 2, column: 3 });
    });
    await page.addScriptTag({ content: syncSource });
    await page.evaluate(() => {
      const deps = Object.fromEntries(PortalArticleWorkspaceEvents.dependencies.map(key => [key, () => {}]));
      PortalArticleWorkspaceEvents.createEvents({ ...deps, $: id => document.getElementById(id), state, ...sync });
      sync.scheduleArticleContentRefresh(0);
      window.changeEvent = () => window.dispatchEvent(new CustomEvent('portal:change', { detail: {
        resource_projection_changes: { article: ['article_fixture'] },
      } }));
    });
    await page.waitForFunction(() => reads >= 3);
    await page.evaluate(() => { window.originalModel = state.editor.getModel(); remote = { content: 'new one\nnew two', version: '2', revision: 'r2' }; changeEvent(); });
    await page.waitForFunction(() => state.editor.getValue() === 'new one\nnew two');
    assert.deepEqual(await page.evaluate(() => ({ ...state.editor.getPosition() })), { lineNumber: 2, column: 3 });
    await page.evaluate(() => { window.cleanModel = state.editor.getModel(); changeEvent(); });
    await page.waitForTimeout(250);
    assert.equal(await page.evaluate(() => cleanModel === state.editor.getModel()), true, 'unchanged content preserves Monaco model');

    await page.evaluate(() => {
      state.editor.executeEdits('user', [{ range: new monaco.Range(1, 1, 1, 1), text: 'draft ' }]);
      remote = { content: 'remote while editing', version: '3', revision: 'r3' }; changeEvent();
    });
    await page.waitForFunction(() => notices.length === 1);
    assert.equal(await page.evaluate(() => state.editor.getValue()), 'draft new one\nnew two');
    assert.equal(await page.evaluate(() => cleanModel === state.editor.getModel()), true, 'draft and undo history preserved');
    await page.evaluate(() => { state.editor.trigger('keyboard', 'undo'); changeEvent(); });
    await page.waitForFunction(() => state.editor.getValue() === 'remote while editing');

    // Direct filesystem changes have no Portal event; the periodic check catches them.
    await page.evaluate(() => { remote = { content: 'poll update', version: '4', revision: 'r4' }; });
    await page.waitForFunction(() => state.editor.getValue() === 'poll update', { timeout: 8000 });
    await page.evaluate(() => { fail = true; changeEvent(); });
    await page.waitForTimeout(250);
    await page.evaluate(() => { fail = false; remote = { content: 'focus update', version: '5', revision: 'r5' }; window.dispatchEvent(new Event('focus')); });
    await page.waitForFunction(() => state.editor.getValue() === 'focus update');
    await page.evaluate(() => { hold = true; remote.version = '6'; changeEvent(); });
    await page.waitForFunction(() => !!window.releaseRead);
    await page.evaluate(() => { state.selectedId = 'article_other'; sync.scheduleArticleContentRefresh(0); releaseRead(); });
    await page.waitForTimeout(250);
    assert.equal(await page.evaluate(() => state.editor.getValue()), 'focus update', 'old response cannot change another Article');
    await page.evaluate(() => sync.articleContentRefresh.destroy());
    assert.deepEqual(errors, []);
    console.log('PASS: events, polling, focus recovery, Monaco cursor/model, draft/undo and stale response protection');
  } finally {
    await browser?.close();
    await new Promise(resolve => server.close(resolve));
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
