'use strict';
// One Chromium page, sequential cases (--workers=1); all transports are fixtures.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {execFileSync} = require('node:child_process');
const {chatWorkspaceScripts} = require('./chat_workspace_modules.cjs');
const {portalWorkbenchScripts} = require('./portal_workbench_scripts.cjs');
const root = path.resolve(__dirname, '../../..');
const {chromium} = require(path.join(root, 'management_portal/test_framework/tau_runtime/node_modules/playwright'));
const modules = ['portal_state.js', 'portal_page_state.js', 'portal_text.js', 'portal_i18n.js',
  'portal_ui_primitives.js', 'portal_ui.js', 'portal_overlay.js', 'portal_resource_chips.js',
  'portal_image_editor.js', 'portal_editor_open_service.js', 'portal_command_registry.js',
  'portal_workbench_context.js', ...portalWorkbenchScripts(), 'portal_resident_service.js',
  'resident_protocol.js', ...chatWorkspaceScripts(), 'article_workspace_chat.js',
  'project_common.js', 'project_map_layout.js', 'project_home.js'];

(async () => {
  const routeScript = execFileSync(process.env.TFI_TEST_PYTHON || 'python3', ['-c',
    'from management_portal.resources.portal import portal_route_script; print(portal_route_script().decode())'], {cwd: root, encoding: 'utf8'});
  const browser = await chromium.launch({headless: true});
  try {
    const page = await browser.newPage();
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.route('**/*', route => route.fulfill({contentType: 'text/html', body:
      '<!doctype html><span id="portalUsername">alice</span><main id="articleChatWorkspace"></main><main id="project"></main>'}));
    await page.goto('http://discussion.fixture.invalid');
    await page.addScriptTag({content: routeScript});
    for (const name of modules) await page.addScriptTag({path: path.join(root, 'management_portal/static', name)});
    await page.evaluate(async () => {
      window.PortalIdentity = {username: () => 'alice'};
      window.calls = []; window.sent = []; window.bindings = {}; window.revision = 1;
      window.projects = [{project_id: 'one', title: '项目一'}, {project_id: 'two', title: '项目二'}];
      window.projectContext = selected => ({projects, selected_project_id: selected || (projects.length === 1 ? projects[0].project_id : ''), selection_required: projects.length > 1 && !selected});
      window.snapshot = {messages: [], controls: {can_send: true}, queue: {items: []}};
      window.detail = id => ({resident: {resident_id: id}, chat_workspace: {
        schema: 'portal.chat_workspace.v3', target: {kind: 'resident', id}, messages: snapshot.messages,
        composer: {can_send: true}, header: {title: '资源讨论'}}});
      window.PortalInstructionClient = {execute: async (name, args) => {
        if (name === 'ListInputHistory') return {entries: []};
        if (name === 'GetProject') return {project: {resource_id: args.project_id, status: 'active'}, team: {lineages: []}, resource_counts: {}, recent_changes: []};
        if (name === 'GetResearchMap') return {directions: []};
        throw new Error('Unexpected instruction: ' + name);
      }};
      window.PortalAvatarEditor = {loadAssets: async () => {}};
      window.PortalAPI = {
        get: async url => {
          const query = new URL(url, location.origin); calls.push({get: url});
          if (query.pathname === '/projects/api/discussions') {
            if (window.holdList) await new Promise(resolve => { window.releaseList = resolve; });
            if (window.denyList) throw new Error('Forbidden project');
            const offset = Number(query.searchParams.get('offset'));
            return {items: window.emptyList ? [] : [{resource_id: 'article_a', resource_type: 'article', title: '原资源讨论', url: '/articles?article=article_a', resident_id: 'private', lineage_id: 'private'}], total: window.emptyList ? 0 : 51, offset, limit: 50};
          }
          const kb = query.searchParams.get('kb_id');
          const value = {project_context: projectContext(query.searchParams.get('project_id')), ...(bindings[kb] || {binding_state: 'unbound', chat_workspace: {
            schema: 'portal.chat_workspace.v3', target: {kind: 'knowledge_item', id: kb}, messages: [], composer: {can_send: true, history_scope: kb}}})};
          if (window.holdRead) await new Promise(resolve => { window.releaseRead = resolve; });
          return value;
        },
        post: async (url, body) => {
          calls.push({post: url, body});
          if (window.holdPrepare) await new Promise(resolve => { window.releasePrepare = resolve; });
          if (window.denyPrepare) throw Object.assign(new Error('Project revoked'), {status: 403});
          if (window.losePrepare) throw new TypeError('Load failed');
          const identity = bindings[body.kb_id] ||= {resident_id: 'resident_' + body.kb_id};
          return {...identity, project_context: projectContext(body.project_id), initial_context: {context_text: `summary ${revision} project ${body.project_id || ''}`}};
        },
      };
      window.PortalResidentProjection = {load: async id => detail(id), subscribe: () => () => {}};
      window.PortalResidentProtocol = {...PortalResidentProtocol,
        acquire: () => ({load: async () => snapshot, snapshot: () => snapshot, destroy() {}}),
        sendInput: async (body, options) => {
          await options.prepare(body, snapshot); sent.push(body); snapshot.messages.push(body.message);
          return {message_id: body.message.message_id};
        },
      };
      window.state = {selectedId: 'article_a'};
      const deps = Object.fromEntries(PortalArticleWorkspaceChat.dependencies.map(key => [key, () => null]));
      window.host = PortalArticleWorkspaceChat.createChat({...deps, state,
        $: id => document.getElementById(id), selectedArticle: () => ({title: 'Article', canonical_root: '/fixture'}),
        showToast: error => { throw new Error(String(error)); }});
      await host.loadArticleChat(); window.panel = state.chatPanel;
    });
    const selection = page.getByRole('combobox', {name: '讨论背景项目'});
    assert.equal(await selection.inputValue(), '', 'multiple projects never silently select the first');
    assert.equal(await page.evaluate(() => calls.filter(c => c.post).length), 0, 'opening is read only');
    assert.match(await page.evaluate(async () => {try {await panel.send({text: 'blocked'});} catch (e) {return e.message;}}), /请选择/);
    assert.equal(await page.evaluate(() => sent.length), 0);
    await selection.selectOption('two');
    await page.evaluate(() => host.loadArticleChat());
    assert.equal(await selection.inputValue(), 'two', 'same-resource activation retains the unbound choice');
    await page.evaluate(async () => {panel.workspace.setDraftText('first'); await panel.send(); revision = 2; panel.workspace.setDraftText('second'); await panel.send();});
    assert.deepEqual(await page.evaluate(() => calls.filter(c => c.post).map(c => [c.body.purpose, c.body.project_id])), [['send', 'two'], ['send', 'two']]);
    assert.deepEqual(await page.evaluate(() => sent.map(m => m.message.content_parts.find(p => p.type === 'context_reference').context.context_text.split('\n')[0])), ['summary 1 project two', 'summary 2 project two']);
    assert.equal(await page.evaluate(() => new Set(sent.map(m => m.message.message_id)).size), 2);
    await selection.selectOption('@none');
    await page.evaluate(() => panel.send({text: 'resource only'}));
    assert.equal(await page.evaluate(() => calls.filter(c => c.post).at(-1).body.project_id), '@none');
    await page.evaluate(() => {denyPrepare = true; panel.workspace.setDraftText('keep revoked draft');});
    assert.match(await page.evaluate(async () => {try {await panel.send();} catch (e) {return e.message;}}), /revoked/);
    assert.equal(await page.evaluate(() => panel.workspace.draftText()), 'keep revoked draft');
    assert.equal(await page.evaluate(() => sent.length), 3);
    await page.evaluate(async () => {denyPrepare = false; projects = [projects[0]]; await panel.reload();});
    assert.equal(await selection.inputValue(), 'one', 'refresh rechecks available projects');
    await page.evaluate(() => {losePrepare = true;});
    assert.match(await page.evaluate(async () => {try {await panel.send({text: 'lost'});} catch (e) {return e.message;}}), /Load failed/);
    assert.equal(await page.evaluate(() => sent.length), 3, 'lost preparation never sends with stale context');
    await page.evaluate(async () => {
      losePrepare = false; holdPrepare = true; window.pendingSend = panel.send({text: 'old resource'});
    });
    await page.waitForFunction(() => Boolean(window.releasePrepare));
    await page.evaluate(async () => {state.selectedId = 'article_b'; projects = [{project_id: 'new', title: '新项目'}]; await host.loadArticleChat(); holdPrepare = false; releasePrepare(); await pendingSend;});
    assert.equal(await selection.inputValue(), 'new');
    assert.equal(await page.evaluate(() => sent.length), 3, 'stale preparation cannot send');
    await page.evaluate(() => {holdRead = true; state.selectedId = 'article_old'; window.pendingRead = host.loadArticleChat();});
    await page.waitForFunction(() => Boolean(window.releaseRead));
    await page.evaluate(async () => {holdRead = false; state.selectedId = 'article_final'; projects = [{project_id: 'final', title: '最终项目'}]; await host.loadArticleChat(); releaseRead(); await pendingRead;});
    assert.equal(await selection.inputValue(), 'final', 'late read cannot restore a previous choice');
    await page.evaluate(async () => {
      projects = [{project_id: 'one'}, {project_id: 'two'}]; await panel.reload();
      await panel.prepareSurfaceResident(panel.options.resolveBeforeSettings);
    });
    assert.equal(await page.evaluate(() => calls.filter(c => c.post).at(-1).body.purpose), 'settings');
    assert.equal(await page.evaluate(() => sent.length), 3, 'settings needs no choice and sends nothing');

    await page.evaluate(async () => {window.home = PortalProjectHome.create({root: document.getElementById('project')}); await home.load('one');});
    const list = page.locator('#projectDiscussions');
    await list.getByRole('link', {name: '原资源讨论'}).waitFor();
    assert.equal(await list.getByRole('link').getAttribute('href'), '/articles?article=article_a');
    assert.doesNotMatch(await list.innerText(), /private/);
    await list.getByRole('button', {name: '下一页'}).click();
    await page.waitForFunction(() => calls.some(c => c.get?.includes('offset=50')));
    await list.getByRole('button', {name: '上一页'}).waitFor();
    await list.getByRole('button', {name: '上一页'}).click();
    await page.evaluate(() => {denyList = true;});
    await list.getByRole('button', {name: '刷新', exact: true}).click();
    await list.getByRole('alert').waitFor();
    await page.evaluate(() => {denyList = false; emptyList = true;});
    await list.getByRole('button', {name: '刷新', exact: true}).click();
    await list.getByText('还没有资源讨论。', {exact: false}).waitFor();
    await page.evaluate(() => {emptyList = false; holdList = true; void home.load('old');});
    await page.waitForFunction(() => Boolean(window.releaseList));
    await page.evaluate(async () => {holdList = false; emptyList = true; await home.load('final'); releaseList();});
    await list.getByText('还没有资源讨论。', {exact: false}).waitFor();
    assert.deepEqual(errors, []);
    await page.evaluate(() => panel.destroy());
    console.log('PASS resource discussion projects: selection, per-send context, settings, revocation, lost response, navigation races, private resource links, pagination, empty/error states (Chromium workers=1)');
  } finally { await browser.close(); }
})().catch(error => {console.error(error); process.exitCode = 1;});
