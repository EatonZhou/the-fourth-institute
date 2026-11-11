'use strict';
// Production Article host, Chat panel and runtime controller; isolated transport.
// One Chromium. No live Resident, message, settings or model task is created.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const { chatWorkspaceScripts } = require('./chat_workspace_modules.cjs');
const { portalWorkbenchScripts } = require('./portal_workbench_scripts.cjs');
const repo = path.resolve(__dirname, '../../..');
const { chromium } = require(path.join(repo, 'management_portal/test_framework/tau_runtime/node_modules/playwright'));
const modules = ['portal_state.js', 'portal_page_state.js', 'portal_text.js', 'portal_i18n.js',
  'portal_ui_primitives.js', 'portal_ui.js', 'portal_overlay.js', 'portal_resource_chips.js',
  'portal_image_editor.js', 'portal_editor_open_service.js', 'portal_command_registry.js',
  'portal_workbench_context.js', ...portalWorkbenchScripts(), 'portal_resident_service.js',
  'portal_resident_runtime_controller.js', 'resident_protocol.js', ...chatWorkspaceScripts(), 'article_workspace_chat.js'];
const routes = JSON.parse(fs.readFileSync(0, 'utf8')).routeScript;

(async () => {
  const server = http.createServer((req, res) => {
    const name = new URL(req.url, 'http://fixture').pathname.slice(1);
    let body, type = 'text/javascript';
    if (!name) {
      type = 'text/html';
      body = '<!doctype html><meta charset="utf-8"><span id="portalUsername">alice</span><main id="articleChatWorkspace"></main>' +
        '<script src="/routes.js"></script>' + modules.map(n => `<script src="/${n}"></script>`).join('');
    } else if (name === 'routes.js') body = routes;
    else if (modules.includes(name)) body = fs.readFileSync(path.join(repo, 'management_portal/static', name));
    res.writeHead(body === undefined ? 404 : 200, { 'Content-Type': type }); res.end(body ?? 'unexpected request');
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  let browser;
  try {
    browser = await chromium.launch({ headless: true });
    const page = await browser.newPage();
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    const origin = `http://127.0.0.1:${server.address().port}`;
    await page.route('**/*', route => route.request().url().startsWith(origin + '/') ? route.continue() : route.abort());
    await page.goto(origin);
    await page.evaluate(async () => {
      window.PortalIdentity = { username: () => 'alice' };
      window.calls = []; window.menus = []; window.sent = []; window.notices = [];
      window.bindings = {}; window.model = 'fixture-model'; window.failPrepare = false; window.holdPrepare = false;
      window.snapshot = { messages: [], controls: { can_send: true }, queue: { items: [] } };
      window.detail = id => ({ resident: { resident_id: id, backend: 'codex' },
        backend_capabilities: { runtime_capabilities: { settings: ['model'] } },
        allows: { UpdateResidentSettings: true }, runtime_settings: { model },
        chat_workspace: { schema: 'portal.chat_workspace.v3', messages: [],
          target: { kind: 'resident', id }, header: { title: 'Article discussion' },
          capabilities: ['runtime_settings'], actions: { runtime_settings: {} },
          composer: { can_send: true, runtime_backend: 'codex', runtime_settings: { model } } } });
      window.PortalUI.toast = (message, tone) => notices.push({ message: String(message), tone });
      window.PortalNav = { openResidentMenu: async options => menus.push(options.residentId) };
      window.PortalInstructionClient = { execute: async instruction => {
        if (instruction === 'ListInputHistory') return { entries: [] };
        throw new Error('Unexpected instruction: ' + instruction);
      } };
      window.PortalAPI = {
        get: async url => {
          const kb = new URL(url, location.origin).searchParams.get('kb_id');
          if (!kb) throw new Error('Unexpected read: ' + url);
          calls.push({ read: kb });
          return bindings[kb] || { binding_state: 'unbound', initial_context: { context_text: kb },
            chat_workspace: { schema: 'portal.chat_workspace.v3', target: { kind: 'knowledge_item', id: kb },
              header: { title: 'Article discussion' }, messages: [],
              composer: { can_send: true, history_scope: 'article:' + kb, meta: '模型：尚未创建对话，可先点击设置选择' } } };
        },
        post: async (url, body) => {
          if (url !== '/knowledge/api/item-chat/prepare') throw new Error('Unexpected write: ' + url);
          calls.push({ prepare: body });
          if (holdPrepare) await new Promise(resolve => { window.releasePrepare = resolve; });
          if (failPrepare) throw new Error('Settings preparation failed');
          return bindings[body.kb_id] ||= { resident_id: 'resident_' + body.kb_id, initial_context: { context_text: body.kb_id } };
        },
      };
      window.PortalResidentProjection = { load: async id => detail(id), subscribe: () => () => {} };
      window.PortalResidentRuntimeController = PortalResidentRuntimeController.create({ service: {
        projection: async ({ residentId }) => detail(residentId),
        runtimeSettings: async () => ({ settings: { model } }),
        runtimeCatalog: async () => ({ runtime_capabilities: { settings: ['model'] } }),
        observeSessionRestart: () => () => {}, sessionRestartOptions: async () => ({}),
      } });
      window.PortalResidentProtocol = { ...PortalResidentProtocol,
        acquire: () => ({ load: async () => snapshot, snapshot: () => snapshot, destroy() {} }),
        sendInput: async (body, options) => {
          await options.prepare(body, snapshot); sent.push(body);
          return { message_id: body.message.message_id };
        },
      };
      window.state = { selectedId: 'article_a' };
      const deps = Object.fromEntries(PortalArticleWorkspaceChat.dependencies.map(key => [key, () => null]));
      window.host = PortalArticleWorkspaceChat.createChat({ ...deps, state,
        $: id => document.getElementById(id), selectedArticle: () => ({ title: 'Article', canonical_root: '/fixture' }),
        showToast: PortalUI.toast,
      });
      await host.loadArticleChat();
      window.panel = state.chatPanel;
    });
    const menu = () => page.locator('#articleWorkspaceChatMenu');
    assert.equal(await page.evaluate(() => calls.filter(c => c.prepare).length), 0, 'reading must not create a Resident');
    assert.match(await page.locator('#articleChatWorkspace').innerText(), /模型：尚未创建对话/);
    await page.evaluate(() => {
      panel.workspace.setDraftText('unsent draft', [{ name: 'note.txt', mime: 'text/plain', data_url: 'data:text/plain;base64,WA==' }]);
      panel.workspace.setRefs({ skills: ['shared/plan'] }); holdPrepare = true;
      window.expectedDraft = panel.workspace.draftText();
    });
    await menu().click(); await menu().click();
    assert.equal(await page.evaluate(() => calls.filter(c => c.prepare).length), 1, 'double click prepares once');
    await page.evaluate(() => { holdPrepare = false; releasePrepare(); });
    await page.waitForFunction(() => menus.length === 1 || notices.length > 0);
    assert.deepEqual(await page.evaluate(() => notices), [], 'settings preparation succeeds');
    assert.deepEqual(await page.evaluate(() => menus), ['resident_article_a']);
    assert.deepEqual(await page.evaluate(() => calls.find(c => c.prepare).prepare), { kb_id: 'article_a', purpose: 'settings' });
    assert.equal(await page.evaluate(() => sent.length), 0, 'opening settings sends no message');
    assert.equal(await page.evaluate(() => panel.workspace.draftText()), await page.evaluate(() => expectedDraft));
    assert.equal(await page.evaluate(() => panel.workspace.attachments()[0].name), 'note.txt');
    assert.deepEqual(await page.evaluate(() => [...panel.workspace.refs().skills]), ['shared/plan']);
    await page.waitForFunction(() => document.querySelector('.composerRuntimeLabel')?.textContent === 'fixture-model');
    await menu().click();
    assert.equal(await page.evaluate(() => calls.filter(c => c.prepare).length), 1, 'bound settings reuses Resident');
    await page.evaluate(async () => { state.selectedId = 'article_b'; await host.loadArticleChat(); failPrepare = true; panel.workspace.setDraftText('keep on error'); });
    await menu().click();
    await page.waitForFunction(() => notices.some(n => n.message.includes('Settings preparation failed')));
    assert.equal(await page.evaluate(() => panel.workspace.draftText()), 'keep on error');
    await page.evaluate(() => { failPrepare = false; holdPrepare = true; });
    await menu().click();
    await page.waitForFunction(() => panel.preparingResident);
    await page.evaluate(async () => { state.selectedId = 'article_c'; await host.loadArticleChat(); panel.workspace.setDraftText('other article draft'); holdPrepare = false; releasePrepare(); });
    await page.waitForFunction(() => !panel.preparingResident);
    assert.equal(await page.evaluate(() => menus.length), 2, 'late settings cannot open the previous Article');
    assert.equal(await page.evaluate(() => panel.workspace.draftText()), 'other article draft');
    await menu().click();
    await page.waitForFunction(() => menus.at(-1) === 'resident_article_c');
    assert.equal(await page.evaluate(() => panel.workspace.draftText()), 'other article draft');
    await page.evaluate(async () => { state.selectedId = 'article_d'; await host.loadArticleChat(); panel.workspace.setDraftText('first real question'); await panel.send(); });
    assert.equal(await page.evaluate(() => sent.length), 1, 'first send still delivers exactly once');
    assert.equal(await page.evaluate(() => sent[0].resident_id), 'resident_article_d');
    assert.match(await page.evaluate(() => sent[0].message.content_parts.find(p => p.type === 'context_reference').context.context_text), /Article: article_d/);
    assert.equal(await page.evaluate(() => panel.workspace.draftText()), '', 'successful send clears migrated draft');
    assert.deepEqual(errors, []);
    console.log('PASS: Article settings before first send, model label, draft migration, failure, double click, stale navigation and first-send context');
  } finally {
    await browser?.close();
    await new Promise(resolve => server.close(resolve));
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
