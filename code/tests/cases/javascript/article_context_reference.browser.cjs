'use strict';
// Use the real Article layout, event handler, Chat panel and composer.
// Only transport is isolated; no real Resident or message is created.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const { chatWorkspaceScripts } = require('./chat_workspace_modules.cjs');
const { portalWorkbenchScripts } = require('./portal_workbench_scripts.cjs');
const { portalWorkspaceSurfaceScripts } = require('./portal_workspace_surface_scripts.cjs');
const repo = path.resolve(__dirname, '../../..');
const staticRoot = path.join(repo, 'management_portal/static');
const playwright = require(path.join(repo, 'management_portal/test_framework/tau_runtime/node_modules/playwright'));
const routes = JSON.parse(fs.readFileSync(0, 'utf8')).routeScript;
const modules = ['portal_state.js', 'portal_page_state.js', 'portal_text.js', 'portal_i18n.js',
  'portal_ui_primitives.js', 'portal_ui.js', 'portal_overlay.js', 'portal_resource_chips.js',
  'portal_image_editor.js', 'portal_editor_open_service.js', 'portal_command_registry.js', 'portal_code_editor.js',
  'portal_workbench_context.js', ...portalWorkbenchScripts(), ...portalWorkspaceSurfaceScripts(), 'portal_resident_service.js',
  'portal_resident_runtime_controller.js', 'resident_protocol.js', ...chatWorkspaceScripts(),
  'article_workspace_monaco.js', 'article_workspace_pdf.js', 'article_workspace_tools.js', 'article_workspace_layout.js', 'article_workspace_chat.js', 'article_workspace_events.js'];
const html = fs.readFileSync(path.join(repo, 'management_portal/article_workspace.html'), 'utf8')
  .replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi, '')
  .replace('</body>', '<script src="/routes.js"></script>' + modules.map(n => `<script src="/static/${n}"></script>`).join('') + '</body>');

(async () => {
  const server = http.createServer((req, res) => {
    const url = new URL(req.url, 'http://fixture');
    if (url.pathname === '/') { res.setHeader('Content-Type', 'text/html; charset=utf-8'); return res.end(html); }
    if (url.pathname === '/routes.js') { res.setHeader('Content-Type', 'text/javascript'); return res.end(routes); }
    const rel = url.pathname.slice('/static/'.length);
    if (!url.pathname.startsWith('/static/') || (!modules.includes(rel) && !rel.startsWith('vendor/monaco/') && !rel.endsWith('.css')) || rel.includes('..')) {
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
    const page = await browser.newPage({ viewport: { width: 1920, height: 1080 } });
    page.setDefaultTimeout(6000);
    const errors = [];
    page.on('console', message => { if(message.type()==='error') console.error(message.text()); });
    page.on('pageerror', error => errors.push(error.message));
    const origin = `http://127.0.0.1:${server.address().port}`;
    await page.route('**/*', route => route.request().url().startsWith(origin + '/') ? route.continue() : route.abort());
    await page.goto(origin);
    const setup = async ({ prepareContext = false, boundChat = false, sideView } = {}) => {
      const $ = id => document.getElementById(id);
      window.notices = []; window.writes = [];
      window.bound = boundChat;
      window.PortalPreview = {createFrameCoalescer: () => ({cancel() {}, schedule() {}})};
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
      window.state = { selectedId: 'article_fixture', openFile: 'main.tex', editorPreferences: { tabSize: 2, fontSize:14 }, monacoDisposables: [] };
      const deps = module => Object.fromEntries(module.dependencies.map(key => [key, () => {}]));
      const doc = {revision:'r1', source:'', editable:true, textDocument:true};
      state.documents = new Map([['main.tex',doc]]);
      let editor;
      const editorOptions = { ...deps(PortalArticleWorkspaceMonaco),
        getSession: () => ({articleId:state.selectedId,path:state.openFile,documents:state.documents,preferences:state.editorPreferences}),
        activeDocument: () => doc, handleEditorPairing: () => false, syncEditorScroll() {},
        captureActiveDocument: () => { if(editor) doc.source = editor.editorValue(); },
        activateDocument: () => { doc.model = monaco.editor.createModel(doc.source,'latex'); editor.getEditor().setModel(doc.model); },
        bringCurrentFileToChat: () => host.bringCurrentFileToChat(),
        onSelectionCommitted: () => host.bringSelectionToChat(),
      };
      const outputOptions = { ...deps(PortalArticleWorkspacePdf),
        getContext: () => ({articleId:state.selectedId,path:state.openFile,settings:{},view:'pdf'}),scheduleReadingPosition() {} };
      let host;
      const layout = window.layout = PortalArticleWorkspaceLayout.createLayout({ ...deps(PortalArticleWorkspaceLayout), $, state,
        panelOptions: () => ({editor:editorOptions,output:outputOptions}),
        normalizedEditorPreferences: () => ({tabSize:2,fontSize:14}),
        articleLayoutStore: PortalWorkbench.layout.createStore({ storageKey: 'portal.articleWorkspace.layout' }),
        loadArticleChat: (...args) => host.loadArticleChat(...args), showToast: text => notices.push(String(text)),
      });
      host = PortalArticleWorkspaceChat.createChat({ ...deps(PortalArticleWorkspaceChat), $, state,
        selectedArticle: () => ({ title: 'Fixture', canonical_root: '/fixture' }), articleProjects: () => [],
        outputView: () => layout.currentArticleWorkbench().getPanel('output'),
        activeDocument: () => doc, editorPosition: () => editor.editorPosition(),
        editorSelectionContext: (...args) => editor.editorSelectionContext(...args), editorValue: () => editor.editorValue(), languageForPath: () => 'latex',
        setSideView: layout.setSideView, showToast: text => notices.push(String(text)),
      });
      window.host = host;
      layout.initArticleWorkbench();
      editor = window.editorOwner = layout.currentArticleWorkbench().getPanel('editor');
      PortalArticleWorkspaceEvents.createEvents({ ...deps(PortalArticleWorkspaceEvents), $, state,
        articleGitRefresh: { stop() {} }, articleContentRefresh: { stop() {} },
        bringCurrentFileToChat: host.bringCurrentFileToChat, bringSelectionToChat: host.bringSelectionToChat, showToast: text => notices.push(String(text)),
        setSideView: layout.setSideView, currentArticleWorkbench: layout.currentArticleWorkbench,
        setToolVisible: layout.setToolVisible,
        canToggleArticleSidebar: layout.canToggleArticleSidebar, toggleArticleSidebar: layout.toggleArticleSidebar,
      });
      await layout.restoreLayout({ sideView });
      if (!prepareContext) return;
      await layout.setSideView('chat');
      state.chatController.setDraftText('Keep my question');
      $('articleSendContext').disabled = false;
      $('articleSourceEditor').disabled = false;
      $('articleSourceEditor').style.display = 'block';
      $('articleSourceEditor').value = 'first\nselected words\nlast';
      $('articleSourceEditor').setSelectionRange(6, 20);
      $('articleSourceEditor').focus();
    };
    await page.evaluate(setup, { prepareContext: true });
    // The sidebar command also works in the plain-text fallback editor.
    const sidebarLayout = await page.evaluate(() => layout.currentArticleWorkbench().getLayout());
    const sidebarWidth = await page.locator('[data-article-panel="chat"]').evaluate(el => el.getBoundingClientRect().width);
    const fallbackDraft = await page.locator('#articleSourceEditor').inputValue();
    await page.keyboard.press('Control+b');
    await page.waitForFunction(() => layout.currentArticleWorkbench().isPanelCollapsed('chat'));
    assert.equal(await page.locator('[data-article-panel="chat"]').isVisible(), false);
    await page.keyboard.press('Meta+b');
    await page.waitForFunction(() => !layout.currentArticleWorkbench().isPanelCollapsed('chat'));
    assert.equal(await page.locator('#articleSourceEditor').inputValue(), fallbackDraft);
    assert.equal(await page.evaluate(() => document.activeElement.id), 'articleSourceEditor');
    assert.deepEqual(await page.evaluate(() => layout.currentArticleWorkbench().getLayout()), sidebarLayout);
    assert.ok(Math.abs(await page.locator('[data-article-panel="chat"]').evaluate(el => el.getBoundingClientRect().width) - sidebarWidth) < 2);
    await page.evaluate(() => state.chatController.focusComposer());
    await page.keyboard.press('Control+b');
    assert.equal(await page.evaluate(() => layout.currentArticleWorkbench().isPanelCollapsed('chat')), false, 'chat retains B formatting');
    await page.locator('#articleResourceQuery').focus();
    await page.keyboard.press('Control+b');
    assert.equal(await page.evaluate(() => layout.currentArticleWorkbench().isPanelCollapsed('chat')), false, 'search retains its shortcut');
    await page.locator('#articleSourceEditor').focus();
    await page.keyboard.press('Control+Alt+Enter');
    await page.waitForFunction(() => !PortalCommandRegistry.root.list().find(c => c.id === 'article.fileToChat').busy);
    const result = await page.evaluate(() => ({
      attachments: state.chatController.attachments(), payload: state.chatController.payload(),
      tokens: state.chatController.els.input.querySelectorAll('[data-portal-composer-attachment-token]').length, notices,
    }));
    assert.deepEqual(errors, [], 'page errors');
    assert.deepEqual(result.notices, [], 'action errors');
    assert.equal(result.attachments.length, 1, JSON.stringify(result));
    assert.equal(result.tokens, 1, 'reference is visible inside the real composer');
    assert.equal(JSON.parse(result.attachments[0].reference_text).file.content, 'first\nselected words\nlast');
    assert.equal(JSON.parse(result.attachments[0].reference_text).file.selection, null);
    assert.doesNotMatch(result.payload.text, /#L1/);
    assert.match(result.payload.text, /Keep my question/, 'existing text survives');

    // Already-bound Article and real Monaco, matching the normal editor.
    await page.evaluate(async () => { bound = true; await host.loadArticleChat({ force: true }); state.chatController.setDraftText(''); });
    await page.addScriptTag({ url: origin + '/static/vendor/monaco/vs/loader.js' });
    await page.evaluate(async () => {
      require.config({ paths: { vs: '/static/vendor/monaco/vs' } });
      window.PortalCodeEditor = { ...window.PortalCodeEditor,
        loadMonaco: () => new Promise(resolve => require(['vs/editor/editor.main'],resolve)) };
      document.getElementById('articleMonacoEditor').style.cssText = 'display:block;width:600px;height:200px';
      document.getElementById('articleSourceEditor').value = 'alpha\nMonaco selection\nomega';
      if (!await editorOwner.initializeMonacoEditor()) throw Error('Monaco did not initialize');
      state.editor = editorOwner.getEditor();
      state.editor.setSelection(new monaco.Selection(2,1,2,17)); state.editor.focus();
    });
    const monacoBeforeToggle = await page.evaluate(() => ({ value: state.editor.getValue(), selection: state.editor.getSelection() }));
    await page.keyboard.press('Control+b');
    await page.waitForFunction(() => layout.currentArticleWorkbench().isPanelCollapsed('chat'));
    await page.keyboard.press('Control+b');
    await page.waitForFunction(() => !layout.currentArticleWorkbench().isPanelCollapsed('chat'));
    assert.deepEqual(await page.evaluate(() => ({ value: state.editor.getValue(), selection: state.editor.getSelection() })), monacoBeforeToggle);
    assert.equal(await page.evaluate(() => state.editor.hasTextFocus()), true, 'Monaco keeps its caret after sidebar toggle');
    await page.keyboard.press('Meta+Alt+Enter');
    await page.waitForFunction(() => state.chatController.attachments().length === 1);
    const boundResult = await page.evaluate(() => ({
      reference: state.chatController.attachments()[0],
      tokens: state.chatController.els.input.querySelectorAll('[data-portal-composer-attachment-token]').length,
    }));
    assert.equal(JSON.parse(boundResult.reference.reference_text).file.content, 'alpha\nMonaco selection\nomega');
    assert.equal(boundResult.tokens, 1);
    assert.deepEqual(await page.evaluate(() => writes), []);

    // Mouse references use the same gesture owner as Repo. They replace only
    // the previous automatic attachment, leave the draft and editor focus alone.
    await page.evaluate(() => {
      state.chatController.setDraftText('Keep automatic context', state.chatController.attachments());
      state.editor.setPosition({ lineNumber: 3, column: 1 });
      state.editor.focus();
    });
    const dragSelection = async (start, end) => {
      const points = await page.evaluate(([start, end]) => {
        const box = state.editor.getDomNode().getBoundingClientRect();
        return [start, end].map(column => {
          const p = state.editor.getScrolledVisiblePosition({lineNumber: 2, column});
          return {x: box.left + p.left + 1, y: box.top + p.top + p.height / 2};
        });
      }, [start, end]);
      const target = await page.evaluate(p => document.elementFromPoint(p.x, p.y)?.closest('.monaco-editor') !== null, points[0]);
      assert.equal(target, true, 'mouse drag targets the visible Monaco editor');
      await page.mouse.move(points[0].x, points[0].y); await page.mouse.down();
      await page.mouse.move(points[1].x, points[1].y, {steps: 6});
      await page.mouse.up();
    };
    await dragSelection(1, 7);
    await page.waitForFunction(() => state.chatController.attachments().length === 2).catch(async error => {
      console.error(await page.evaluate(() => ({notices, selection: state.editor.getSelection(),
        references: state.chatController.attachments().map(a => JSON.parse(a.reference_text).file),
        focus: document.activeElement?.className})));
      throw error;
    });
    assert.equal(await page.evaluate(() => JSON.parse(state.chatController.attachments().at(-1).reference_text).file.selection.text), 'Monaco');
    assert.equal(await page.evaluate(() => state.editor.hasTextFocus()), true);
    await dragSelection(8, 17);
    await page.waitForFunction(() => JSON.parse(state.chatController.attachments().at(-1).reference_text).file.selection.text === 'selection');
    assert.equal(await page.evaluate(() => state.chatController.attachments().length), 2);
    assert.equal(await page.evaluate(() => state.editor.hasTextFocus()), true);
    assert.match(await page.evaluate(() => state.chatController.payload().text), /Keep automatic context/);
    await page.evaluate(() => state.editor.setSelection(new monaco.Selection(1, 1, 1, 3)));
    await page.waitForTimeout(50);
    assert.equal(await page.evaluate(() => JSON.parse(state.chatController.attachments().at(-1).reference_text).file.selection.text), 'selection', 'programmatic selection adds no reference');

    // Use the real Workbench tab drag/drop with the real Chat composer and
    // Monaco model. Moving a tool must not mount a second conversation.
    const stackFor = id => page.locator(`[data-portal-workbench-panel="${id}"]`).locator('xpath=ancestor::*[@data-portal-workbench-stack][1]');
    const dragTool = async (id, target, zone) => {
      const tab = page.locator(`[data-portal-workbench-tab="${id}"]`);
      await tab.focus(); // Shared Workbench reveals its tab strip on focus.
      const destination = stackFor(target);
      const box = await destination.boundingBox();
      assert.ok(box, `${target} stack has geometry`);
      await tab.dragTo(destination, {
        targetPosition: { x: box.width * (zone === 'right' ? .9 : .5), y: box.height * (zone === 'bottom' ? .9 : .5) },
      });
    };
    await page.evaluate(() => {
      layout.currentArticleWorkbench().setLayoutLocked(false);
      state.chatController.setDraftText('Draft survives every move');
      window.originalChat = state.chatController;
      window.originalPayload = state.chatController.payload();
      window.originalModel = state.editor.getModel();
      window.originalSelection = state.editor.getSelection();
      document.getElementById('articleGlobalSearchInput').value = 'keep search';
    });
    await dragTool('chat', 'output', 'right');
    await page.evaluate(() => layout.setSideView('files'));
    assert.equal(await page.locator('[data-article-panel="files"]').isVisible(), true);
    assert.equal(await page.locator('#articleChatWorkspace').isVisible(), true, 'detached Chat remains visible next to Files');
    const detachedLayout = await page.evaluate(() => layout.currentArticleWorkbench().getLayout());
    await page.evaluate(() => layout.setSideView('chat'));
    assert.deepEqual(await page.evaluate(() => layout.currentArticleWorkbench().getLayout()), detachedLayout, 'explicit navigation focuses Chat without relocating it');
    await page.locator('[data-article-view="git"]').click();
    assert.deepEqual(await page.evaluate(() => [...state.visibleTools].sort()), ['chat', 'git'], 'Git and Chat have independent visibility');
    assert.deepEqual(await page.evaluate(() => ({
      chat: originalChat === state.chatController, model: originalModel === state.editor.getModel(),
      selection: state.editor.getSelection().equalsSelection(originalSelection),
      search: document.getElementById('articleGlobalSearchInput').value,
      draft: JSON.stringify(state.chatController.payload()) === JSON.stringify(originalPayload),
    })), { chat: true, model: true, selection: true, search: 'keep search', draft: true });
    const savedDetachedLayout = await page.evaluate(() => layout.currentArticleWorkbench().getLayout());
    if (process.env.ARTICLE_TOOLS_ARTIFACT_DIR) {
      fs.mkdirSync(process.env.ARTICLE_TOOLS_ARTIFACT_DIR, {recursive: true});
      await page.screenshot({path: path.join(process.env.ARTICLE_TOOLS_ARTIFACT_DIR, 'detached-chat.png')});
    }
    await page.reload(); await page.evaluate(setup, { boundChat: true });
    assert.deepEqual(await page.evaluate(() => [...state.visibleTools].sort()), ['chat', 'git'], 'reload restores the detached group and each active tab');
    assert.deepEqual(await page.evaluate(() => layout.currentArticleWorkbench().getLayout()), savedDetachedLayout, 'reload retains split geometry');
    // Re-establish editor state after the intentional reload, then test moves.
    await page.evaluate(() => {
      state.chatController.setDraftText('Draft survives every move');
      window.originalChat = state.chatController;
      window.chatRoot = document.getElementById('articleChatWorkspace');
    });
    await dragTool('chat', 'output', 'bottom');
    assert.equal(await page.locator('#articleChatWorkspace').isVisible(), true);
    // A detached Chat stays visible when the left tool group folds away.
    await page.evaluate(() => { const source = document.getElementById('articleSourceEditor'); source.disabled = false; source.value = 'Unsaved source'; source.focus(); });
    const leftActive = await page.evaluate(() => layout.currentArticleWorkbench().getLayout().children[0].activePanelId);
    const sourceBeforeToggle = await page.locator('#articleSourceEditor').inputValue();
    const layoutBeforeToggle = await page.evaluate(() => layout.currentArticleWorkbench().getLayout());
    await page.keyboard.press('Control+b');
    await page.waitForFunction(id => layout.currentArticleWorkbench().isPanelCollapsed(id), leftActive);
    assert.equal(await page.locator('#articleChatWorkspace').isVisible(), true, 'independent right-side Chat is not a sidebar');
    assert.match(await page.evaluate(() => state.chatController.payload().text), /Draft survives every move/);
    await page.keyboard.press('Control+b');
    await page.waitForFunction(id => !layout.currentArticleWorkbench().isPanelCollapsed(id), leftActive);
    assert.deepEqual(await page.evaluate(() => layout.currentArticleWorkbench().getLayout()), layoutBeforeToggle);
    assert.equal(await page.locator('#articleSourceEditor').inputValue(), sourceBeforeToggle);
    assert.equal(await page.evaluate(() => document.activeElement.id), 'articleSourceEditor');
    await page.evaluate(() => layout.currentArticleWorkbench().setLayoutLocked(true));
    await page.keyboard.press('Control+b');
    assert.equal(await page.evaluate(id => layout.currentArticleWorkbench().isPanelCollapsed(id), leftActive), false, 'locked layout is respected');
    await page.evaluate(() => { layout.currentArticleWorkbench().setLayoutLocked(false); document.getElementById('articleWorkspaceGrid').hidden = true; });
    await page.keyboard.press('Control+b');
    assert.equal(await page.evaluate(id => layout.currentArticleWorkbench().isPanelCollapsed(id), leftActive), false, 'invisible workspace does not claim the shortcut');
    await page.evaluate(() => { document.getElementById('articleWorkspaceGrid').hidden = false; });
    await dragTool('chat', 'git', 'center');
    assert.equal(await page.evaluate(() => originalChat === state.chatController && chatRoot === document.getElementById('articleChatWorkspace')), true);
    assert.match(await page.evaluate(() => state.chatController.payload().text), /Draft survives every move/);
    await page.locator('[data-article-view="files"]').click();
    assert.equal(await page.locator('#articleChatWorkspace').isVisible(), false, 'Chat becomes a sibling tab again');
    await page.locator('[data-article-view="chat"]').click();
    assert.match(await page.evaluate(() => state.chatController.payload().text), /Draft survives every move/);

    await page.locator('[data-article-view="chat"]').focus();
    await page.keyboard.press('Control+b');
    await page.waitForFunction(() => layout.currentArticleWorkbench().isPanelCollapsed('chat'));
    await page.reload();
    await page.evaluate(setup, { boundChat: true });
    assert.equal(await page.evaluate(() => layout.currentArticleWorkbench().isPanelCollapsed('chat')), true, 'reload retains folded sidebar');
    await page.locator('[data-article-view="chat"]').focus();
    await page.keyboard.press('Control+b');
    await page.waitForFunction(() => !layout.currentArticleWorkbench().isPanelCollapsed('chat'));
    assert.equal(await page.locator('#articleChatWorkspace').isVisible(), true, 'same tool restored after reload');
    // Recreate the page with the same browser storage, as a user reload does.
    await page.reload();
    await page.evaluate(setup, { boundChat: true });
    assert.equal(await page.evaluate(() => state.sideView), 'chat');
    assert.equal(await page.locator('[data-article-panel="chat"]').evaluate(panel => panel.classList.contains('active')), true);
    assert.equal(await page.evaluate(() => !!state.chatController), true, 'restored Chat loads the existing Resident');
    await page.evaluate(() => layout.setToolVisible(false, 'chat'));
    await page.reload();
    await page.evaluate(setup, { boundChat: true });
    assert.equal(await page.evaluate(() => layout.currentArticleWorkbench().isPanelVisible('chat')), false, 'restore keeps a hidden tool hidden');
    await page.evaluate(() => layout.setSideView('files'));
    await page.reload();
    await page.evaluate(setup);
    assert.equal(await page.evaluate(() => state.sideView), 'files', 'switching away from Chat is also remembered');
    assert.equal(await page.evaluate(() => layout.currentArticleWorkbench().getPanel('files').isVisible()), true);
    await page.evaluate(() => layout.setToolVisible(false, 'files'));
    await page.reload();
    await page.evaluate(setup, { boundChat: true, sideView: 'chat' });
    assert.equal(await page.evaluate(() => state.sideView), 'chat', 'an explicit Chat link overrides the saved view');
    assert.equal(await page.evaluate(() => layout.currentArticleWorkbench().getPanel('chat').isVisible()), true, 'explicit Chat opens its panel');
    await page.evaluate(() => PortalState.storage.writeJson('portal.articleWorkspace.layout', { sideView: 'obsolete', fileTreeHeight: 180 }));
    await page.reload();
    await page.evaluate(setup);
    assert.equal(await page.evaluate(() => state.sideView), 'files', 'unknown saved views fall back to the file tree');
    assert.equal(await page.evaluate(() => PortalState.storage.readJson('portal.articleWorkspace.layout').fileTreeHeight), 180, 'view persistence preserves other layout settings');

    // Migrate a user's pre-split workbench and named preset without resetting
    // source/PDF sizes or the selected navigation tool.
    await page.evaluate(() => {
      const {split, stack} = PortalWorkbench.model;
      const old = layout.currentArticleWorkbench().getState();
      old.panelIds = ['navigation', 'editor', 'output'];
      old.layout = split('horizontal', [stack(['navigation']), stack(['editor']), stack(['output'])], [320, 840, 600]);
      old.hidden = []; old.collapsed = [];
      old.savedWorkspaces = {oldProof: {name: 'Old proof', layout: split('horizontal', [stack(['editor']), stack(['output'])]), hidden: ['navigation']}};
      layout.currentArticleWorkbench().destroy(); // Stop unload persistence before seeding an old version.
      PortalWorkbench.storage.createUserStore({kind: 'workbench', id: 'article-workspace', user: 'article-context-fixture'}).replace(old);
      PortalState.storage.writeJson('portal.articleWorkspace.layout', {sideView: 'search'});
    });
    await page.reload(); await page.evaluate(setup);
    assert.deepEqual(await page.evaluate(() => layout.currentArticleWorkbench().getLayout().weights), [320, 840, 600]);
    assert.deepEqual(await page.evaluate(() => [...state.visibleTools]), ['search']);
    assert.deepEqual(await page.evaluate(() => layout.currentArticleWorkbench().getState().savedWorkspaces.oldProof.hidden),
      ['files', 'favorites', 'search', 'git', 'chat', 'settings', 'library']);
    assert.equal(await page.locator('[data-portal-workbench-tab="navigation"]').count(), 0);

    // The left column may hold multiple groups. Ctrl+B controls the column,
    // including the active tool in each group, while a right-hand Chat stays put.
    await page.evaluate(async () => {
      const wb = layout.currentArticleWorkbench(), {split, stack} = PortalWorkbench.model;
      wb.applyWorkspace({layout: split('horizontal', [
        split('vertical', [stack(['files', 'favorites', 'library'], 'files'), stack(['search', 'git', 'settings'], 'git')], [420, 360]),
        stack(['editor']), split('vertical', [stack(['output']), stack(['chat'])]),
      ], [320, 840, 600])});
      await layout.setSideView('chat');
      state.chatController.setDraftText('Independent chat draft');
      document.getElementById('articleGitMessage').value = 'Keep my commit message';
      window.sidebarRoots = ['files', 'git'].map(id => wb.getPanel(id).root);
    });
    const columnLayout = await page.evaluate(() => layout.currentArticleWorkbench().getLayout());
    const columnWidth = await page.locator('[data-article-panel="files"]').evaluate(el => el.getBoundingClientRect().width);
    assert.deepEqual(await page.evaluate(() => [...state.visibleTools].sort()), ['chat', 'files', 'git']);
    await page.locator('[data-article-view="files"]').focus();
    await page.keyboard.press('Control+b');
    assert.deepEqual(await page.evaluate(() => [...state.visibleTools]), ['chat'], 'both left groups retract together');
    assert.match(await page.evaluate(() => state.chatController.payload().text), /Independent chat draft/);
    await page.keyboard.press('Meta+b');
    assert.deepEqual(await page.evaluate(() => [...state.visibleTools].sort()), ['chat', 'files', 'git']);
    assert.equal(await page.locator('#articleGitMessage').inputValue(), 'Keep my commit message');
    assert.equal(await page.evaluate(() => sidebarRoots.every((root, index) => root === layout.currentArticleWorkbench().getPanel(['files', 'git'][index]).root)), true);
    assert.deepEqual(await page.evaluate(() => layout.currentArticleWorkbench().getLayout()), columnLayout);
    assert.ok(Math.abs(await page.locator('[data-article-panel="files"]').evaluate(el => el.getBoundingClientRect().width) - columnWidth) < 2);
    await page.locator('[data-article-view="search"]').click();
    await page.locator('#articleGlobalSearchInput').fill('keep this search');
    await page.locator('[data-article-view="search"]').focus();
    await page.keyboard.press('Control+b');
    assert.deepEqual(await page.evaluate(() => [...state.visibleTools]), ['chat']);
    await page.keyboard.press('Control+b');
    assert.deepEqual(await page.evaluate(() => [...state.visibleTools].sort()), ['chat', 'files', 'search'], 'each group restores its active tool');
    assert.equal(await page.locator('#articleGlobalSearchInput').inputValue(), 'keep this search');
    await page.keyboard.press('Control+b');
    await page.reload(); await page.evaluate(setup, {boundChat: true});
    assert.deepEqual(await page.evaluate(() => [...state.visibleTools]), ['chat'], 'every collapsed left group persists across reload');
    await page.locator('[data-article-view="search"]').focus();
    await page.keyboard.press('Meta+b');
    assert.deepEqual(await page.evaluate(() => [...state.visibleTools].sort()), ['chat', 'files', 'search']);
    await page.evaluate(() => {
      const {split, stack} = PortalWorkbench.model;
      layout.currentArticleWorkbench().applyWorkspace({layout: split('horizontal', [stack(['editor', 'git'], 'git'), stack(['files']), stack(['output'])]),
        hidden: ['favorites', 'search', 'settings', 'chat', 'library']});
    });
    await page.locator('[data-article-view="git"]').focus();
    await page.keyboard.press('Control+b');
    assert.equal(await page.evaluate(() => layout.canToggleArticleSidebar()), false, 'content groups and right-side tools are not the left sidebar');
    assert.deepEqual(await page.evaluate(() => [...state.visibleTools].sort()), ['files', 'git']);
    assert.deepEqual(errors, [], 'no page errors after reloads');
    assert.deepEqual(await page.evaluate(() => writes), [], 'restoring Chat sends no message');
    console.log('PASS: Article tools detach right/bottom, regroup and persist with real Chat drafts, references and independent visibility');
  } finally {
    await browser?.close();
    await new Promise(resolve => server.close(resolve));
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
