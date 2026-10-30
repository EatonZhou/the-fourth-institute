'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chatWorkspaceScripts } = require('./chat_workspace_modules.cjs');
const { portalWorkbenchScripts } = require('./portal_workbench_scripts.cjs');
const source = path.resolve(__dirname, '../../..');
const { chromium } = require(path.join(source, 'management_portal/test_framework/tau_runtime/node_modules/playwright'));
const { routeScript } = JSON.parse(fs.readFileSync(0, 'utf8'));
const residentId = 'resident_111111111111', otherId = 'resident_222222222222';

(async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    const page = await browser.newPage({ viewport: { width: 1600, height: 1000 } });
    const errors = [], requests = [];
    page.on('pageerror', e => errors.push(e.message));
    page.on('request', r => requests.push(r.url()));
    await page.route('**/*', route => route.request().url() === 'https://chat-ready.fixture.invalid/'
      ? route.fulfill({ contentType: 'text/html', body: '<!doctype html><main id="a" role="region" style="width:700px;height:850px;display:inline-block"></main><main id="b" role="region" style="width:700px;height:850px;display:inline-block"></main>' }) : route.abort());
    await page.goto('https://chat-ready.fixture.invalid/');
    await page.evaluate(() => {
      window.PortalIdentity = { username: () => 'fixture' };
      window.PortalEvents = { isConnected: () => true, connect() {} };
      window.PortalAPI = { get: async () => ({ config: {}, restart: null }) };
      window.PortalInstructionClient = { execute: async name => {
        if (name === 'ListInputHistory') return { entries: [] };
        throw new Error('Unexpected instruction: ' + name);
      } };
    });
    await page.addScriptTag({ content: routeScript });
    for (const name of ['portal_state.js', 'portal_page_state.js', 'portal_text.js', 'portal_ui_primitives.js', 'portal_ui.js', 'portal_overlay.js', 'portal_preview_contract.js', 'portal_resource_chips.js', 'portal_image_editor.js', 'portal_editor_open_service.js', 'portal_command_registry.js', 'portal_workbench_context.js', ...portalWorkbenchScripts(), 'portal_resident_service.js', 'resident_protocol.js', ...chatWorkspaceScripts()]) {
      await page.addScriptTag({ content: fs.readFileSync(path.join(source, 'management_portal/static', name), 'utf8') });
    }
    await page.addStyleTag({ content: fs.readFileSync(path.join(source, 'management_portal/static/portal_components.css'), 'utf8') });
    await page.evaluate(({ residentId, otherId }) => {
      window.panels = {};
      for (const [id, target] of [['a', residentId], ['b', otherId]]) {
        const panel = PortalChatWorkspace.panelPackage.create({ root: document.getElementById(id), id, autoLoad: false, target: { resident_id: target } });
        const messages = [
          { message_id: 'msg_' + id, role: 'agent', author_id: target, content_parts: [{ type: 'text', text: residentId }] },
          { message_id: 'msg_loading_' + id, role: 'user', author_id: 'fixture',
            content_parts: [{ type: 'text', text: 'Loading history text, not a live loading indicator.' }] },
        ];
        const snapshot = { messages, controls: { can_send: true }, queue: { items: [] } };
        panel.protocol = { snapshot: () => snapshot, destroy() {} };
        panel.residentProjection = { resident: { resident_id: target }, chat_workspace: {
          schema: 'portal.chat_workspace.v3', target: { kind: 'resident', id: target },
          header: { title: 'Chat Workspace - ' + id }, messages: [], composer: { can_send: true },
        } };
        panel.applySnapshot(snapshot);
        panel.workspace.setTitle('Chat Workspace - ' + id);
        panel.workspace.setSubtitle('', [{ identity: 'resident', text: target, copy_value: target, copy_label: 'resident_id' }]);
        panels[id] = panel;
      }
    }, { residentId, otherId });
    const a = page.locator('#a'), b = page.locator('#b');
    const oldRoot = page.getByRole('region', { name: /^Chat Workspace/ }).filter({ has: page.getByText(residentId, { exact: true }) });
    assert.equal(await oldRoot.count(), 2, 'message text causes the archived root locator to include another Resident');
    const oldIdentityCount = await a.getByText(residentId, { exact: true }).count();
    assert.ok(oldIdentityCount > 1, 'native header and message content/author both match');
    assert.equal(await a.locator('.chatHead [data-workspace-resident-id]').count(), 1);
    await assert.rejects(a.getByText(residentId, { exact: true }).waitFor({ state: 'visible', timeout: 100 }), /strict mode violation/);
    const oldNotLoading = await a.evaluate(root => ![...root.querySelectorAll('*')].some(e => e.getClientRects().length
      && !e.children.length && /^(加载中|正在读取|正在加载|正在载入|Reading\b|Loading\b)/i.test(e.textContent.trim())));
    assert.equal(oldNotLoading, false, 'archived loading predicate misreads message body as workspace loading');
    if (process.argv.includes('--probe')) {
      console.log('Reproduced archived helper ambiguity and false loading with native Chat messages; historical AM cause remains unknown.');
      return;
    }
    const { readResidentChatReady } = require(path.join(source, 'management_portal/test_framework/tau_chat_ready_evidence.js'));
    const baselineRequests = requests.length;
    const sample = () => readResidentChatReady(a, { residentId });
    const first = await sample();
    assert.equal(first.ready, true);
    assert.equal(first.candidates[0].headers[0].resident_id, residentId);
    assert.equal((await readResidentChatReady(oldRoot, { residentId })).ready, false, 'ambiguous roots are never silently narrowed');
    assert.equal((await readResidentChatReady(b, { residentId })).ready, false, 'message ID text cannot prove host identity');
    await page.evaluate(() => panels.a.workspace.setLoading('Native loading'));
    let state = await sample();
    assert.equal(state.ready, false);
    assert.equal(state.candidates[0].loading.active, true);
    assert.equal(state.candidates[0].checks.composer, false);
    await page.evaluate(() => panels.a.workspace.clearLoading());
    assert.equal((await sample()).ready, true);
    await page.evaluate(() => panels.a.workspace.setComposer({ can_edit: false, can_send: false }));
    state = await sample();
    assert.equal(state.ready, false);
    assert.equal(state.candidates[0].inputs[0].editable, false);
    await page.evaluate(() => panels.a.workspace.setComposer({ can_edit: true, can_send: true }));
    assert.equal((await sample()).ready, true);
    await a.evaluate(e => { e.hidden = true; });
    assert.equal((await sample()).ready, false);
    await a.evaluate(e => { e.hidden = false; });
    assert.equal((await readResidentChatReady(page.locator('#missing'), { residentId })).region_count, 0);
    assert.equal((await readResidentChatReady(a.locator('.chatWorkspaceBody'), { residentId })).ready, false, 'partial message body is not a Chat root');
    await a.locator('[data-workspace-resident-id]').evaluate(e => e.after(e.cloneNode(true)));
    state = await sample();
    assert.equal(state.ready, false);
    assert.equal(state.candidates[0].headers.length, 2, 'physical header duplicates retained');
    assert.equal(requests.length, baselineRequests, 'readonly samples send no request');
    await page.evaluate(() => Object.values(panels).forEach(panel => panel.destroy()));
    assert.deepEqual(errors, []);
    console.log('Native Chat ready evidence: header-only identity, native loading, editable composer and failed candidate snapshots verified.');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
