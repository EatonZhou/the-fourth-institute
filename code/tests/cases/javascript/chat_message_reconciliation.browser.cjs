'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const runtime = path.resolve(__dirname, '../../../management_portal/test_framework/tau_runtime/node_modules/playwright');
const browsers = require(fs.existsSync(runtime) ? runtime : 'playwright');
const {chatWorkspaceScripts} = require('./chat_workspace_modules.cjs');
const {portalWorkbenchScripts} = require('./portal_workbench_scripts.cjs');
const {routeScript} = JSON.parse(fs.readFileSync(0, 'utf8'));
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
      await page.route('**/*', route => {
        const url = new URL(route.request().url());
        const file = path.resolve(__dirname, '../../../management_portal/static', path.basename(url.pathname));
        if (url.pathname.startsWith('/static/') && fs.existsSync(file)) {
          return route.fulfill({path: file});
        }
        if (route.request().resourceType() === 'document') {
          return route.fulfill({contentType: 'text/html', body: '<main id="fixture"></main>'});
        }
        return route.fulfill({status: 404, body: ''});
      });
      await page.goto('http://reconciliation.fixture.invalid/lineage');
      await page.evaluate(() => {
        window.PortalIdentity = {username: () => 'sample_admin'};
        // Exercise the existing calibration callback without a two-minute test.
        const interval = window.setInterval;
        window.setInterval = (fn, delay, ...args) => {
          if (delay === 120000) window.calibrate = fn;
          return interval(fn, delay, ...args);
        };
      });
      await page.addScriptTag({content: routeScript});
      for (const name of scripts) await page.addScriptTag({path: path.resolve(__dirname, '../../../management_portal/static', name)});
      await page.evaluate(async () => {
        const rid = 'resident_111111111111';
        window.calls = {snapshots: 0, events: [], sends: 0};
        window.version = 1;
        window.saved = [];
        window.saveReply = text => {
          version++;
          saved.push({message_id: `reply_${version}`, role: 'agent', author_id: rid,
            content_parts: [{type: 'text', text}]});
        };
        window.PortalAPI = {get: async () => ({config: {}, restart: null})};
        window.PortalInstructionClient = {execute: async () => ({entries: [], items: []})};
        window.PortalResidentService = {
          snapshot: async () => { calls.snapshots++; return {resident: {resident_id: rid}, cursor: version,
            messages: saved, input_states: [], activities: [], queue: {items: []},
            state: {work_state: 'idle'}, controls: {can_send: true}}; },
          loadStates: async () => ({states: [{resident_id: rid, cursor: version,
            state: {work_state: 'idle'}, controls: {can_send: true}, viewer_state: {unread: true}}]}),
          events: async (id, {afterCursor}) => {
            calls.events.push(afterCursor);
            return {resident_id: id, high_watermark: version,
              events: saved.map((message, index) => ({cursor: index + 2,
                event_type: 'conversation.message.appended', payload: {message}})).filter(event => event.cursor > afterCursor)};
          },
          inputHistory: async () => ({entries: []}),
          sendInput: async () => { calls.sends++; throw new Error('No user message should be sent'); },
        };
        window.panel = PortalChatWorkspace.panelPackage.create({root: document.getElementById('fixture'),
          id: 'reconciliation', autoLoad: false, target: {resident_id: rid}});
        panel.residentProjection = {resident: {resident_id: rid}, chat_workspace: {
          schema: 'portal.chat_workspace.v3', target: {kind: 'resident', id: rid}, composer: {can_send: true}}};
        panel.protocol = PortalResidentProtocol.acquire(rid, update => {
          if (update.snapshot) panel.applySnapshot(update.snapshot, update);
        }, {load: false});
        await panel.protocol.load();
        panel.workspace.setDraftText('Unsent draft remains');
        saveReply('Reply recovered on focus');
        // No portal:change is sent: this is a lost tail notification.
        window.dispatchEvent(new Event('focus'));
      });
      await page.getByText('Reply recovered on focus', {exact: true}).waitFor();
      assert.equal(await page.locator('.composerInput').innerText(), 'Unsent draft remains');
      await page.evaluate(() => { saveReply('Reply recovered by calibration'); calibrate(); });
      await page.getByText('Reply recovered by calibration', {exact: true}).waitFor();
      assert.deepEqual(await page.evaluate(() => calls), {snapshots: 1, events: [1, 2], sends: 0});
      assert.equal(await page.locator('.composerInput').innerText(), 'Unsent draft remains');
      assert.deepEqual(errors, []);
      await page.evaluate(() => panel.destroy());
      console.log(`${engine}: lost replies render on focus and periodic calibration; draft preserved; no input sent`);
    } finally { await browser.close(); }
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
