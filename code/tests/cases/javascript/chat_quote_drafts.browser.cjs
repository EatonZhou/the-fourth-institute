'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const { chatWorkspaceScripts } = require('./chat_workspace_modules.cjs');
const { portalWorkbenchScripts } = require('./portal_workbench_scripts.cjs');
const root = path.resolve(__dirname, '../../..');
const { chromium } = require(path.join(root, 'management_portal/test_framework/tau_runtime/node_modules/playwright'));
const { routeScript } = JSON.parse(fs.readFileSync(0, 'utf8'));
const scripts = ['portal_state.js', 'portal_page_state.js', 'portal_text.js', 'portal_i18n.js',
  'portal_ui_primitives.js', 'portal_ui.js', 'portal_overlay.js', 'portal_resource_chips.js',
  'portal_image_editor.js', 'portal_editor_open_service.js', 'portal_command_registry.js',
  'portal_workbench_context.js', ...portalWorkbenchScripts(), 'resident_protocol.js', ...chatWorkspaceScripts()];
const server = http.createServer((request, response) => {
  const name = new URL(request.url, 'http://fixture').pathname.slice(1);
  let body, type = 'text/javascript';
  if (!name) {
    type = 'text/html';
    body = '<!doctype html><html><meta charset="utf-8"><main id="chat"></main><script src="/routes.js"></script>'
      + scripts.map(name => `<script src="/${name}"></script>`).join('');
  } else if (name === 'routes.js') body = routeScript;
  else if (scripts.includes(name)) body = fs.readFileSync(path.join(root, 'management_portal/static', name));
  response.writeHead(body === undefined ? 404 : 200, { 'Content-Type': type });
  response.end(body ?? 'not found');
});
(async () => {
  let browser;
  try {
    await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
    browser = await chromium.launch({ headless: true });
    const context = await browser.newContext();
    const errors = [];
    const page = await context.newPage();
    page.on('pageerror', error => errors.push(error.message));
    const url = `http://127.0.0.1:${server.address().port}`;
    const mount = async (owner = 'viewer') => page.evaluate(owner => {
      window.PortalIdentity = { username: () => owner };
      window.workspace = PortalChatWorkspace.createWorkspace({
        root: document.getElementById('chat'), id: 'quote-reload', draftOwner: owner,
        draftNamespace: 'chat', historyScope: () => 'resident:quote-reload',
        contract: { schema: 'portal.chat_workspace.v3', messages: [], composer: { can_send: true, queue: { items: [] } } },
      });
    }, owner);
    const reload = async owner => { await page.reload(); await mount(owner); };
    await page.goto(url);
    await mount();
    await page.evaluate(() => workspace.setQuote({ message_id: 'message-quoted', author_id: 'resident_aaaaaaaaaaaa', text: '关于 C / RTL 接入，保留这条引用。' }));
    await page.locator('.composerInput').fill('尚未发送的回复');
    // No explicit save or destroy: real navigation must flush the edit.
    await page.goto('about:blank');
    await page.goto(url);
    await mount();
    assert.equal(await page.locator('.composerInput').innerText(), '尚未发送的回复');
    assert.equal(await page.locator('.composerQuoteText').innerText(), '关于 C / RTL 接入，保留这条引用。');
    assert.equal(await page.evaluate(() => workspace.quote().message_id), 'message-quoted');
    await reload('another-viewer');
    assert.equal(await page.locator('.composerQuote').count(), 0, 'another user does not see the quote');
    await reload('viewer');
    await page.locator('[data-composer-quote-cancel]').click();
    await reload('viewer');
    assert.equal(await page.locator('.composerQuote').count(), 0, 'cancel remains cancelled across full reload');
    assert.equal(await page.locator('.composerInput').innerText(), '尚未发送的回复');
    await page.locator('.composerInput').fill('');
    await page.evaluate(() => workspace.setQuote({ message_id: 'only-quote', text: 'Only a quote, no draft text' }));
    await reload('viewer');
    assert.equal(await page.evaluate(() => workspace.quote().message_id), 'only-quote', 'a quote-only composition survives reload');
    assert.equal(await page.locator('.composerInput').innerText(), '');
    assert.deepEqual(errors, [], 'no startup or quote restoration errors');
    await context.close();
    console.log('PASS quoted drafts survive navigation and full reload, remain private to their owner, and retain cancellation');
  } finally {
    if (browser) await browser.close();
    await new Promise(resolve => server.close(resolve));
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
