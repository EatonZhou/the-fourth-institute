'use strict';
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const { chromium } = require('../../../management_portal/test_framework/tau_runtime/node_modules/playwright');
const { chatWorkspaceScripts } = require('./chat_workspace_modules.cjs');
const { portalWorkbenchScripts } = require('./portal_workbench_scripts.cjs');
const data = JSON.parse(fs.readFileSync(0, 'utf8'));
(async () => {
  const browser = await chromium.launch({headless: true});
  try {
    const page = await browser.newPage();
    const errors = [];
    page.on('pageerror', e => errors.push(e.message));
    await page.setContent('<main id="fixture"></main>');
    await page.evaluate(() => { window.PortalIdentity = { username: () => 'CASE-A' }; });
    await page.addScriptTag({content: data.routeScript});
    for (const name of ['portal_state.js', 'portal_page_state.js', 'portal_text.js', 'portal_ui_primitives.js', 'portal_ui.js',
      'portal_overlay.js', 'portal_resource_chips.js', 'portal_image_editor.js', 'portal_editor_open_service.js',
      'portal_command_registry.js', 'portal_workbench_context.js', ...portalWorkbenchScripts(), 'resident_protocol.js', ...chatWorkspaceScripts()]) {
      await page.addScriptTag({path: path.resolve(__dirname, '../../../management_portal/static', name)});
    }
    await page.evaluate(historyPage => {
      window.workspace = window.PortalChatWorkspace.createWorkspace({
        root: document.getElementById('fixture'), id: 'browser-history', refs: false,
        historyScope: () => 'facade',
        onLoadInputHistory: async () => ({...historyPage, messages: historyPage.messages.map(window.PortalChatWorkspace.residentProtocolMessageToWorkspace)}),
        contract: {schema: 'portal.chat_workspace.v3', messages: [], composer: {can_send: true}},
      });
      workspace.setDraftText('未发送的草稿');
    }, data.page);
    const input = page.locator('.composerInput');
    await input.focus();
    await input.press('Home');
    await input.press('ArrowUp');
    await page.waitForFunction(() => document.querySelector('.composerInput').textContent.trim() === '请修复这个bug');
    assert.equal(await input.innerText(), '请修复这个bug');
    await input.press('ArrowDown');
    assert.equal(await input.innerText(), '未发送的草稿');
    // Repeat from an empty draft without a local sent cache.
    await page.evaluate(() => workspace.setDraftText(''));
    await input.focus(); await input.press('ArrowUp');
    await page.waitForFunction(() => document.querySelector('.composerInput').textContent.trim() === '请修复这个bug');
    assert.deepEqual(errors, []);
    console.log('Chromium ArrowUp recalled user request; ArrowDown restored draft; no assistant reply recalled');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
