'use strict';
// Use the real Article layout, event handler, Chat panel and composer.
// Only transport is isolated; no real Resident or message is created.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const { chatWorkspaceScripts } = require('./chat_workspace_modules.cjs');
const { portalWorkbenchScripts } = require('./portal_workbench_scripts.cjs');
const repo = path.resolve(__dirname, '../../..');
const staticRoot = path.join(repo, 'management_portal/static');
const playwright = require(path.join(repo, 'management_portal/test_framework/tau_runtime/node_modules/playwright'));
const routes = JSON.parse(fs.readFileSync(0, 'utf8')).routeScript;
const modules = ['portal_state.js', 'portal_page_state.js', 'portal_text.js', 'portal_i18n.js',
  'portal_ui_primitives.js', 'portal_ui.js', 'portal_overlay.js', 'portal_resource_chips.js',
  'portal_image_editor.js', 'portal_editor_open_service.js', 'portal_command_registry.js',
  'portal_workbench_context.js', ...portalWorkbenchScripts(), 'portal_resident_service.js',
  'portal_resident_runtime_controller.js', 'resident_protocol.js', ...chatWorkspaceScripts(),
  'article_workspace_monaco.js', 'article_workspace_layout.js', 'article_workspace_chat.js', 'article_workspace_events.js'];
const html = fs.readFileSync(path.join(repo, 'management_portal/article_workspace.html'), 'utf8')
  .replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi, '').replace(/<link\b[^>]*>/gi, '')
  .replace('</body>', '<script src="/routes.js"></script>' + modules.map(n => `<script src="/static/${n}"></script>`).join('') + '</body>');

(async () => {
  const server = http.createServer((req, res) => {
    const url = new URL(req.url, 'http://fixture');
    if (url.pathname === '/') { res.setHeader('Content-Type', 'text/html; charset=utf-8'); return res.end(html); }
    if (url.pathname === '/routes.js') { res.setHeader('Content-Type', 'text/javascript'); return res.end(routes); }
    const rel = url.pathname.slice('/static/'.length);
    if (!url.pathname.startsWith('/static/') || (!modules.includes(rel) && !rel.startsWith('vendor/monaco/')) || rel.includes('..')) {
      res.writeHead(404); return res.end();
    }
    try {
      res.setHeader('Content-Type', rel.endsWith('.css') ? 'text/css' : 'text/javascript');
      res.end(fs.readFileSync(path.join(staticRoot, rel)));
    } catch { res.writeHead(404); res.end(); }
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  let browser;
  try {
    browser = await playwright[process.env.PORTAL_TEST_BROWSER || 'chromium'].launch({ headless: true });
    const page = await browser.newPage();
    page.setDefaultTimeout(6000);
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    const origin = `http://127.0.0.1:${server.address().port}`;
    await page.route('**/*', route => route.request().url().startsWith(origin + '/') ? route.continue() : route.abort());
    await page.goto(origin);
    await page.evaluate(async () => {
      const $ = id => document.getElementById(id);
      window.notices = []; window.writes = [];
      window.bound = false;
      window.PortalIdentity = { username: () => 'article-context-fixture' };
      window.PortalInstructionClient = { execute: async () => ({ entries: [] }) };
      window.PortalAPI = {
        get: async url => {
          const id = new URL(url, location.origin).searchParams.get('kb_id');
          if (!id) throw new Error('Unexpected read: ' + url);
          if (bound) return { resident_id: 'resident_article_fixture' };
          return { binding_state: 'unbound', chat_workspace: { schema: 'portal.chat_workspace.v3',
            target: { kind: 'knowledge_item', id }, header: { title: 'Article discussion' }, messages: [],
            composer: { can_send: true, history_scope: 'article:' + id } } };
        },
        post: async (...args) => { writes.push(args); throw new Error('Context must not send'); },
      };
      const snapshot = { messages: [], controls: { can_send: true }, queue: { items: [] } };
      window.PortalResidentProjection = { load: async id => ({
        resident: { resident_id: id, backend: 'codex' },
        chat_workspace: { schema: 'portal.chat_workspace.v3', messages: [],
          target: { kind: 'resident', id }, header: { title: 'Article discussion' },
          composer: { can_send: true } },
      }), subscribe: () => () => {} };
      window.PortalResidentProtocol = { ...PortalResidentProtocol,
        acquire: () => ({ load: async () => snapshot, snapshot: () => snapshot, destroy() {} }),
      };
      window.state = { selectedId: 'article_fixture', openFile: 'main.tex', editorPreferences: { tabSize: 2 } };
      const deps = module => Object.fromEntries(module.dependencies.map(key => [key, () => {}]));
      const editor = PortalArticleWorkspaceMonaco.createMonaco({ ...deps(PortalArticleWorkspaceMonaco), $, state });
      let host;
      const layout = PortalArticleWorkspaceLayout.createLayout({ ...deps(PortalArticleWorkspaceLayout), $, state,
        loadArticleChat: (...args) => host.loadArticleChat(...args), showToast: text => notices.push(String(text)),
      });
      host = PortalArticleWorkspaceChat.createChat({ ...deps(PortalArticleWorkspaceChat), $, state,
        selectedArticle: () => ({ title: 'Fixture', canonical_root: '/fixture' }), articleProjects: () => [],
        activeDocument: () => ({ revision: 'r1' }), editorPosition: editor.editorPosition,
        editorSelectionContext: editor.editorSelectionContext, languageForPath: () => 'latex',
        setSideView: layout.setSideView, showToast: text => notices.push(String(text)),
      });
      window.host = host;
      PortalArticleWorkspaceEvents.createEvents({ ...deps(PortalArticleWorkspaceEvents), $, state,
        bringCurrentContextToChat: host.bringCurrentContextToChat, showToast: text => notices.push(String(text)),
      });
      await layout.setSideView('chat');
      state.chatController.setDraftText('Keep my question');
      $('articleSendContext').disabled = false;
      $('articleSourceEditor').disabled = false;
      $('articleSourceEditor').style.display = 'block';
      $('articleSourceEditor').value = 'first\nselected words\nlast';
      $('articleSourceEditor').setSelectionRange(6, 20);
      $('articleSourceEditor').focus();
    });
    await page.keyboard.press('Control+Alt+Enter');
    await page.waitForFunction(() => !PortalCommandRegistry.root.list().find(c => c.id === 'article.contextToChat').busy);
    const result = await page.evaluate(() => ({
      attachments: state.chatController.attachments(), payload: state.chatController.payload(),
      tokens: state.chatController.els.input.querySelectorAll('[data-portal-composer-attachment-token]').length, notices,
    }));
    assert.deepEqual(errors, [], 'page errors');
    assert.deepEqual(result.notices, [], 'action errors');
    assert.equal(result.attachments.length, 1, JSON.stringify(result));
    assert.equal(result.tokens, 1, 'reference is visible inside the real composer');
    assert.equal(JSON.parse(result.attachments[0].reference_text).file.selection.text, 'selected words');
    assert.match(result.payload.text, /Keep my question/, 'existing text survives');

    // Already-bound Article and real Monaco, matching the normal editor.
    await page.evaluate(async () => { bound = true; await host.loadArticleChat({ force: true }); state.chatController.setDraftText(''); });
    await page.addScriptTag({ url: origin + '/static/vendor/monaco/vs/loader.js' });
    await page.evaluate(() => new Promise(resolve => {
      require.config({ paths: { vs: '/static/vendor/monaco/vs' } });
      require(['vs/editor/editor.main'], () => {
        const root = document.getElementById('articleMonacoEditor');
        root.style.cssText = 'display:block;width:600px;height:200px';
        state.editor = monaco.editor.create(root, { value: 'alpha\nMonaco selection\nomega', language: 'plaintext', automaticLayout: true });
        state.monacoReady = true;
        state.editor.setSelection(new monaco.Selection(2, 1, 2, 17)); state.editor.focus();
        resolve();
      });
    }));
    await page.keyboard.press('Meta+Alt+Enter');
    await page.waitForFunction(() => state.chatController.attachments().length === 1);
    const boundResult = await page.evaluate(() => ({
      reference: state.chatController.attachments()[0],
      tokens: state.chatController.els.input.querySelectorAll('[data-portal-composer-attachment-token]').length,
    }));
    assert.equal(JSON.parse(boundResult.reference.reference_text).file.selection.text, 'Monaco selection');
    assert.equal(boundResult.tokens, 1);
    assert.deepEqual(await page.evaluate(() => writes), []);
    console.log('PASS: shortcut inserts a visible reference into the real Article Chat composer');
  } finally {
    await browser?.close();
    await new Promise(resolve => server.close(resolve));
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
