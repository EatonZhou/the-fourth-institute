'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const runtime = path.resolve(__dirname, '../../../management_portal/test_framework/tau_runtime/node_modules/playwright');
const browsers = require(fs.existsSync(runtime) ? runtime : 'playwright');
const {chatWorkspaceScripts} = require('./chat_workspace_modules.cjs');
const {portalWorkbenchScripts} = require('./portal_workbench_scripts.cjs');
const data = JSON.parse(fs.readFileSync(0, 'utf8'));
const scripts = ['portal_state.js', 'portal_page_state.js', 'portal_text.js', 'portal_ui_primitives.js', 'portal_ui.js',
  'portal_overlay.js', 'portal_resource_chips.js', 'portal_image_editor.js', 'portal_editor_open_service.js',
  'portal_command_registry.js', 'portal_workbench_context.js', ...portalWorkbenchScripts(), 'resident_protocol.js', ...chatWorkspaceScripts()];

(async () => {
  for (const engine of ['chromium', 'webkit']) {
    const browser = await browsers[engine].launch({headless: true});
    try {
      const page = await browser.newPage();
      const errors = [];
      page.on('pageerror', error => errors.push(error.message));
      await page.route('**/*', route => route.fulfill({contentType: 'text/html', body: '<main id="fixture"></main>'}));
      await page.goto('http://history.fixture.invalid/lineage');
      await page.evaluate(() => { window.PortalIdentity = {username: () => 'sample_admin'}; });
      await page.addScriptTag({content: data.routeScript});
      for (const name of scripts) await page.addScriptTag({path: path.resolve(__dirname, '../../../management_portal/static', name)});
      await page.evaluate(async () => {
        window.historyCalls = [];
        window.PortalAPI = {get: async () => ({config: {}, restart: null})};
        window.PortalInstructionClient = {execute: async () => ({entries: [], items: []})};
        window.PortalResidentService = {inputHistory: async residentId => {
          historyCalls.push(residentId);
          return {messages: residentId === 'resident_333333333333' ? [{
            message_id: 'my_earlier_input', role: 'user', author_id: 'sample_admin',
            content_parts: [{type: 'text', text: 'Earlier personal input'}],
          }] : []};
        }};
        window.panel = PortalChatWorkspace.panelPackage.create({root: document.getElementById('fixture'),
          id: 'historyPanel', autoLoad: false, target: {resident_id: 'resident_111111111111'}});
        window.install = residentId => {
          panel.workspace.setTarget({resident_id: residentId});
          panel.applyDetail({resident: {resident_id: residentId}, chat_workspace: {
            schema: 'portal.chat_workspace.v3', target: {kind: 'resident', id: residentId},
            header: {title: 'History'}, messages: [], composer: {can_send: true, queue: {items: []}},
          }});
        };
        install('resident_111111111111');
        await new Promise(resolve => setTimeout(resolve, 20));
        historyCalls.length = 0;
        panel.workspace.setHistoryTarget({resident_id: 'resident_333333333333'});
        panel.targetRevision += 1;
        panel.workspace.setComposer({can_send: true, queue: {items: []}});
        await new Promise(resolve => setTimeout(resolve, 20));
      });
      assert.deepEqual(await page.evaluate(() => historyCalls), [], 'pending navigation must not read the previous Resident');
      await page.evaluate(() => { install('resident_333333333333'); panel.workspace.setDraftText(''); });
      const input = page.locator('.composerInput');
      await input.focus();
      await input.press('ArrowUp');
      await page.waitForFunction(() => panel.workspace.draftText() === 'Earlier personal input');
      await input.press('ArrowDown');
      assert.equal(await input.innerText(), '');
      assert.deepEqual(await page.evaluate(() => historyCalls), ['resident_333333333333']);
      assert.deepEqual(errors, []);
      await page.evaluate(() => panel.destroy());
      console.log(`${engine}: Resident switch, empty-draft ArrowUp and ArrowDown passed`);
    } finally { await browser.close(); }
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
