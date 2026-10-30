'use strict';
// Production panel, renderer and styles; isolated native snapshots, no live input.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const { execFileSync } = require('node:child_process');
const { chatWorkspaceScripts } = require('./chat_workspace_modules.cjs');
const { portalWorkbenchScripts } = require('./portal_workbench_scripts.cjs');
const repo = path.resolve(__dirname, '../../..');
const { chromium } = require(path.join(repo, 'management_portal/test_framework/tau_runtime/node_modules/playwright'));
const modules = ['portal_state.js', 'portal_page_state.js', 'portal_text.js', 'portal_i18n.js',
  'portal_ui_primitives.js', 'portal_ui.js', 'portal_overlay.js', 'portal_resource_chips.js',
  'portal_image_editor.js', 'portal_editor_open_service.js', 'portal_command_registry.js',
  'portal_workbench_context.js', ...portalWorkbenchScripts(), 'portal_resident_service.js',
  'resident_protocol.js', ...chatWorkspaceScripts()];
const routes = execFileSync('python3', ['-c',
  'from management_portal.resources.portal import portal_route_script; print(portal_route_script().decode())'],
{ cwd: repo, encoding: 'utf8' });
(async () => {
  const server = http.createServer((req, res) => {
    const name = new URL(req.url, 'http://fixture').pathname.slice(1);
    let body, type = 'text/javascript';
    if (!name) {
      type = 'text/html';
      body = '<!doctype html><meta charset="utf-8"><link rel="stylesheet" href="/portal_components.css">' +
        '<style>body{margin:8px}#chat{width:100%;height:800px}</style><main id="chat"></main>' +
        '<script src="/routes.js"></script>' + modules.map(n => `<script src="/${n}"></script>`).join('');
    } else if (name === 'routes.js') body = routes;
    else if (modules.includes(name) || name === 'portal_components.css') {
      body = fs.readFileSync(path.join(repo, 'management_portal/static', name));
      if (name.endsWith('.css')) type = 'text/css';
    }
    res.writeHead(body === undefined ? 404 : 200, { 'Content-Type': type }); res.end(body ?? 'unexpected request');
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const origin = 'http://127.0.0.1:' + server.address().port;
  let browser;
  try {
    browser = await chromium.launch({ headless: true });
    const page = await browser.newPage({ viewport: { width: 390, height: 900 } });
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.route('**/*', route => route.request().url().startsWith(origin + '/') ? route.continue() : route.abort());
    await page.goto(origin);
    await page.evaluate(() => {
      window.PortalIdentity = { username: () => 'scope-fixture' };
      window.PortalAPI = { get: async url => {
        if (!url.includes('session-restart')) throw new Error('Unexpected API: ' + url);
        return { config: {}, restart: null };
      } };
      window.PortalInstructionClient = { execute: async instruction => {
        if (instruction === 'ListInputHistory') return { entries: [] };
        throw new Error('Unexpected instruction: ' + instruction);
      } };
      window.filePath = '/workspace/很长的文件夹/'.repeat(6) + '待审批.txt';
      window.originalDiff = '@@ -1 +1 @@\n-before\n+<script>window.injected=true</script>\n';
      window.scope = { thread_id: 't', turn_id: 'u', item_id: 'i', changes: [
        { path: filePath, kind: { type: 'update', move_path: '/workspace/新名称.txt' }, diff: originalDiff },
        { path: '/workspace/空文件.txt', kind: { type: 'add' }, diff: '' }] };
      window.makeSnapshot = (id, scopeValue) => ({ controls: { can_send: true }, queue: { items: [] }, messages: [{
        message_id: 'msg_' + id, role: 'agent', content_parts: [{ type: 'tool_call', tool_name: 'request_approval',
          request_id: id, status: 'pending', thread_id: 't', turn_id: 'u', item_id: 'i', is_blocking: true,
          interaction: { kind: 'file_change', method: 'item/fileChange/requestApproval',
            request: { threadId: 't', turnId: 'u', itemId: 'i', reason: null, grantRoot: null }, file_change: scopeValue } }] }] });
      window.snap = makeSnapshot('original', scope);
      window.panel = PortalChatWorkspace.panelPackage.create({ root: document.getElementById('chat'), id: 'approvalScope',
        autoLoad: false, target: { resident_id: 'resident_111111111111' }, onError: error => { throw error; } });
      panel.protocol = { snapshot: () => snap, destroy() {} };
      panel.residentProjection = { resident: { resident_id: 'resident_111111111111' },
        chat_workspace: { schema: 'portal.chat_workspace.v3', target: { kind: 'resident', id: 'resident_111111111111' },
          header: { title: 'Approval scope' }, messages: [], composer: { can_send: true } } };
      panel.applySnapshot(snap);
    });
    assert.equal(await page.locator('.nativeApprovalFile').count(), 2);
    assert.equal(await page.locator('.nativeApprovalFile pre').first().textContent(), await page.evaluate(() => originalDiff));
    assert.equal(await page.locator('.nativeApprovalFile pre').nth(1).textContent(), '');
    assert.equal(await page.locator('.nativeApprovalScopeMissing').count(), 0);
    assert.equal(await page.evaluate(() => window.injected), undefined);
    assert.equal(await page.locator('.nativeApprovalFilePath').first().textContent(),
      await page.evaluate(() => '修改 · ' + filePath + ' → /workspace/新名称.txt'));
    const bounds = await page.locator('.nativeApprovalFilePath').first().evaluate(el => ({
      width: el.getBoundingClientRect().width, scroll: el.scrollWidth, client: el.clientWidth,
      right: el.getBoundingClientRect().right, viewport: innerWidth }));
    assert.ok(bounds.width > 0 && bounds.scroll <= bounds.client + 1 && bounds.right <= bounds.viewport,
      JSON.stringify(bounds));
    await page.evaluate(() => panel.applySnapshot(JSON.parse(JSON.stringify(snap))));
    assert.equal(await page.locator('.nativeApprovalFile pre').first().textContent(), await page.evaluate(() => originalDiff));
    for (const missing of ['absent', 'wrong-thread', 'wrong-turn', 'wrong-item']) {
      await page.evaluate(missing => {
        const value = missing === 'absent' ? undefined : { ...scope, [
          { 'wrong-thread': 'thread_id', 'wrong-turn': 'turn_id', 'wrong-item': 'item_id' }[missing]]: 'other' };
        snap = makeSnapshot(missing, value); panel.applySnapshot(snap);
      }, missing);
      assert.equal(await page.locator('.nativeApprovalFile').count(), 0, missing);
      assert.equal(await page.locator('.nativeApprovalScopeMissing').count(), 1, missing);
    }
    assert.deepEqual(errors, []);
    console.log('PASS original file approval scope, literal multiline diff, narrow paths, refresh and exact identity');
  } finally {
    if (browser) await browser.close(); server.closeAllConnections(); await new Promise(resolve => server.close(resolve));
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
