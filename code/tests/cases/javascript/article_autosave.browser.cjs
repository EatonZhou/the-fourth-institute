'use strict';
// Real Article input callbacks, document sessions and Monaco; isolated files.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const { STATIC, portalWorkspaceSurfaceScripts } = require('./portal_workspace_surface_scripts.cjs');
const { chromium } = require(path.join(STATIC, '../test_framework/tau_runtime/node_modules/playwright'));
const modules = ['portal_state.js', 'portal_text.js', 'portal_i18n.js', 'portal_ui_primitives.js', 'portal_ui.js',
  'portal_command_registry.js', ...portalWorkspaceSurfaceScripts(), 'article_workspace_documents.js',
  'article_workspace_syntax.js', 'article_workspace_monaco.js', 'article_workspace_events.js'];
const html = fs.readFileSync(path.join(STATIC, '../article_workspace.html'), 'utf8')
  .replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi, '').replace(/<link\b[^>]*>/gi, '')
  .replace('</body>', modules.map(n => `<script src="/static/${n}"></script>`).join('') + '</body>');

(async () => {
  let disk = 'old', writes = 0, builds = 0, browser;
  const server = http.createServer(async (req, res) => {
    const url = new URL(req.url, 'http://fixture');
    if (url.pathname === '/') { res.setHeader('Content-Type', 'text/html'); return res.end(html); }
    if (url.pathname === '/api/articles/A/file') {
      if (req.method === 'POST') {
        const chunks = []; for await (const chunk of req) chunks.push(chunk);
        disk = JSON.parse(Buffer.concat(chunks).toString()).content; writes++;
      }
      res.setHeader('Content-Type', 'application/json'); return res.end(JSON.stringify({ path: 'main.tex', content: disk }));
    }
    if (url.pathname === '/api/articles/A/compile') { builds++; return res.end('{}'); }
    const rel = url.pathname.slice('/static/'.length);
    if (!url.pathname.startsWith('/static/') || rel.includes('..') || (!modules.includes(rel) && !rel.startsWith('vendor/monaco/'))) {
      res.writeHead(404); return res.end();
    }
    res.setHeader('Content-Type', rel.endsWith('.css') ? 'text/css' : 'text/javascript');
    try { res.end(fs.readFileSync(path.join(STATIC, rel))); } catch { res.writeHead(404); res.end(); }
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  try {
    browser = await chromium.launch({ headless: true });
    const page = await browser.newPage(); page.setDefaultTimeout(10000);
    const errors = []; page.on('pageerror', error => errors.push(error.message));
    const origin = `http://127.0.0.1:${server.address().port}`;
    await page.route('**/*', route => route.request().url().startsWith(origin + '/') ? route.continue() : route.abort());
    await page.goto(origin);
    await page.evaluate(() => {
      const $ = id => document.getElementById(id);
      const deps = module => Object.fromEntries(module.dependencies.map(key => [key, () => {}]));
      window.state = { selectedId: 'A', openFile: 'main.tex', openTabs: ['main.tex'], source: 'old', savedSource: 'old',
        documents: new Map(), editorPreferences: { tabSize: 2, fontSize: 14 }, monacoDisposables: [], settings: { auto_compile: false } };
      const session = PortalDocumentSession.create({ continuousEdit: true, canEdit: () => true });
      session.present({ path: 'main.tex', content: 'old' });
      state.documents.set('main.tex', { path: 'main.tex', textDocument: true, editable: true, source: 'old', savedSource: 'old', session });
      let syntax, monacoOwner;
      window.documentsOwner = PortalArticleWorkspaceDocuments.createDocuments({ ...deps(PortalArticleWorkspaceDocuments), $, state,
        editorValue: () => state.monacoReady ? state.editor.getValue() : $('articleSourceEditor').value,
        updateDirtyState: () => syntax.updateDirtyState(),
        request: async (url, options) => (await fetch(url, options)).json(),
        compileArticle: () => fetch('/api/articles/A/compile', { method: 'POST' }),
        detailResourceGuest: { changed() {} }, loadSemanticIndex: async () => {},
      });
      syntax = PortalArticleWorkspaceSyntax.createSyntax({ ...deps(PortalArticleWorkspaceSyntax), $, state,
        activeDocument: documentsOwner.activeDocument, renderDocumentTabs: documentsOwner.renderDocumentTabs,
      });
      const shared = { $, state, activeDocument: documentsOwner.activeDocument, captureActiveDocument: documentsOwner.captureActiveDocument,
        saveOpenFile: documentsOwner.saveOpenFile, scheduleAutoSave: documentsOwner.scheduleAutoSave,
        updateDirtyState: syntax.updateDirtyState, hasDirtyDocuments: documentsOwner.hasDirtyDocuments };
      PortalArticleWorkspaceEvents.createEvents({ ...deps(PortalArticleWorkspaceEvents), ...shared,
        articleGitRefresh: { stop() {} }, articleContentRefresh: { stop() {} },
      });
      monacoOwner = window.monacoOwner = PortalArticleWorkspaceMonaco.createMonaco({ ...deps(PortalArticleWorkspaceMonaco), ...shared,
        activateDocument: () => {
          const doc = documentsOwner.activeDocument();
          doc.model = monaco.editor.createModel(doc.source, 'latex');
          state.editor.setModel(doc.model);
        },
      });
      $('articleSourceEditor').disabled = false; $('articleSourceEditor').value = 'old';
      $('articleSourceEditor').style.cssText = 'display:block;width:600px;height:150px';
      syntax.updateDirtyState();
    });
    await page.fill('#articleSourceEditor', 'textarea draft');
    await page.waitForFunction(() => document.getElementById('articleEditorState').textContent === '已自动保存');
    assert.equal(disk, 'textarea draft'); assert.equal(writes, 1); assert.equal(builds, 0);
    await Promise.all([page.waitForResponse(r => r.url().endsWith('/compile')), page.click('#articleSaveFile')]);
    assert.equal(builds, 1);
    await page.addScriptTag({ url: origin + '/static/vendor/monaco/vs/loader.js' });
    await page.evaluate(async () => {
      require.config({ paths: { vs: '/static/vendor/monaco/vs' } });
      window.PortalCodeEditor = { loadMonaco: () => new Promise(resolve => require(['vs/editor/editor.main'], resolve)) };
      document.getElementById('articleMonacoEditor').style.cssText = 'display:block;width:600px;height:300px';
      if (!await monacoOwner.initializeMonacoEditor()) throw new Error('Monaco did not initialize');
      state.editor.focus();
    });
    await page.keyboard.press('Control+End'); await page.keyboard.type(' + Monaco draft');
    await page.waitForFunction(() => state.savedSource.endsWith(' + Monaco draft'));
    assert.equal(disk, 'textarea draft + Monaco draft'); assert.equal(writes, 2); assert.equal(builds, 1);
    await Promise.all([page.waitForResponse(r => r.url().endsWith('/compile')), page.keyboard.press('Control+s')]);
    assert.equal(builds, 2);
    assert.deepEqual(errors, []);
    console.log('PASS: textarea and Monaco idle edits persist without a build; manual save button and Ctrl+S compile');
  } finally { await browser?.close(); await new Promise(resolve => server.close(resolve)); }
})().catch(error => { console.error(error); process.exitCode = 1; });
