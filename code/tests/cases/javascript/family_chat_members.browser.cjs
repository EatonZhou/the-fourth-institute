'use strict';
// Real Resident Chat and Family popover; isolated preferences, no live writes.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('../../../management_portal/test_framework/tau_runtime/node_modules/playwright');
const { chatWorkspaceScripts } = require('./chat_workspace_modules.cjs');
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const root = path.resolve(__dirname, '../../../management_portal/static');
(async () => {
  const browser = await chromium.launch({headless: true});
  try {
    const page = await browser.newPage({viewport: {width: 1200, height: 900}});
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.route('**/*', route => {
      const url = new URL(route.request().url());
      if (url.hostname !== 'family.fixture.invalid') return route.abort();
      if (url.pathname.startsWith('/static/')) return route.fulfill({
        body: fs.readFileSync(path.join(root, url.pathname.slice(8))),
        contentType: url.pathname.endsWith('.css') ? 'text/css' : 'text/javascript',
      });
      return route.fulfill({contentType: 'text/html', body: '<!doctype html><html lang="zh-CN"><meta charset="utf-8"><body><section id="chat" style="height:90vh;min-width:0"></section></body></html>'});
    });
    await page.goto('http://family.fixture.invalid');
    for (const name of ['portal_components', 'portal_workbench', 'portal_design_system', 'portal_themes']) {
      await page.addStyleTag({url: `/static/${name}.css`});
    }
    await page.addScriptTag({content: input.routeScript});
    for (const name of ['portal_state.js', 'portal_page_state.js', 'portal_text.js', 'portal_ui_primitives.js', 'portal_ui.js',
      'portal_overlay.js', 'portal_resource_chips.js', 'portal_image_editor.js', 'portal_editor_open_service.js',
      'portal_command_registry.js', 'portal_workbench_context.js', 'portal_workbench.js', 'resident_protocol.js', ...chatWorkspaceScripts()]) {
      await page.addScriptTag({url: `/static/${name}`});
    }
    await page.evaluate(() => {
      window.PortalIdentity = {username: () => 'viewer'};
      window.calls = []; window.toasts = []; window.failNext = false;
      PortalUI.toast = message => toasts.push(message);
      let policy = {muted_member_ids: [], solo_member_id: ''};
      const members = Array.from({length: 8}, (_, i) => ({resident_id: `member_${i}`, title: `成员 ${i} · 架构与实现开发团队`, role: 'member'}));
      const family = {family_id: 'family_fixture', title: 'Family', lineage_id: 'fixture', members,
        nodes: [{resident_id: 'router', role: 'router', title: 'Router'}, ...members]};
      window.detail = () => ({resident_id: 'facade', family_facade: {...family, chat_preferences: {...policy}}});
      window.PortalInstructionClient = {execute: async (name, args) => {
        calls.push([name, args]);
        if (name === 'UpdateFamilyChatPreferences') {
          if (failNext) { failNext = false; throw new Error('Save failed'); }
          if (args.control === 'mute') {
            policy.muted_member_ids = policy.muted_member_ids.filter(id => id !== args.member_id);
            if (args.enabled) policy.muted_member_ids.push(args.member_id);
            if (args.enabled && policy.solo_member_id === args.member_id) policy.solo_member_id = '';
          } else policy.solo_member_id = args.enabled ? args.member_id : '';
        }
        return {...policy};
      }};
      window.PortalAPI = {get: async () => ({accounts: []})};
      window.PortalResidentRuntimeController = {bind: () => ({
        state: () => ({}), load: async () => ({detail: {resident: {backend: 'codex'}}, settings: {}}), close() {}, destroy() {},
      })};
      window.PortalResidentProtocol = {...PortalResidentProtocol,
        loadSnapshot: async (id, opts) => { calls.push(['snapshot', id, opts]); panel.updateFamilyStatus(detail()); },
        acquire: () => ({load: async () => ({state: {work_state: 'idle'}, messages: []}), destroy() {}}),
      };
      window.panel = PortalChatWorkspace.panelPackage.create({root: document.getElementById('chat'), id: 'chat', autoLoad: false, target: {resident_id: 'facade'}});
      panel.updateFamilyStatus(detail());
    });
    const bar = page.locator('[data-family-chat-members]');
    const button = (id, mode, parent = bar) => parent.locator(`[data-member="${id}"][data-family-mix="${mode}"]`);
    const pressed = async (id, mode, active, parent = bar) => {
      await button(id, mode, parent).locator(`xpath=self::*[@aria-pressed='${active}'][not(@disabled)]`).waitFor();
    };
    assert.equal(await bar.locator('.familyMemberIdentity').count(), 8);
    assert.equal(await button('router', 'mute').count(), 0);
    assert.match(await bar.locator('a').first().getAttribute('href'), /resident=member_0/);
    await button('member_0', 'mute').click(); await pressed('member_0', 'mute', true);
    await button('member_1', 'solo').click(); await pressed('member_1', 'solo', true);
    await pressed('member_0', 'mute', false);
    await button('member_2', 'solo').click(); await pressed('member_2', 'solo', true);
    await pressed('member_1', 'solo', false);
    await button('member_2', 'solo').click(); await pressed('member_0', 'mute', true);
    assert.equal(await bar.locator('.familyMemberIdentity').count(), 8, 'hidden conversations never hide the unmute controls');

    // The existing Chat popover and the new inline controls share persisted state.
    await page.evaluate(() => {
      const anchor = document.createElement('button'); anchor.id = 'family-anchor'; document.body.append(anchor);
      panel.openFamilyStatus(anchor);
    });
    const popover = page.locator('.familyStatusPanel');
    await pressed('member_0', 'mute', true, popover);
    await button('member_0', 'mute', popover).click(); await pressed('member_0', 'mute', false);
    await page.evaluate(() => panel.familyStatusView.close());
    await page.evaluate(() => { failNext = true; });
    await button('member_1', 'mute').click();
    await page.waitForFunction(() => toasts.includes('Save failed'));
    await pressed('member_1', 'mute', false);
    assert.equal(await page.evaluate(() => document.activeElement.dataset.familyMix), 'mute');
    await button('member_1', 'mute').click(); await pressed('member_1', 'mute', true);

    // Rebinding a Chat drops the old controls, then reloads this user's choice.
    await page.evaluate(() => panel.updateFamilyStatus({resident_id: 'ordinary'}));
    assert.equal(await bar.isVisible(), false);
    await page.evaluate(() => panel.updateFamilyStatus(detail()));
    await pressed('member_1', 'mute', true);
    for (const width of [1200, 390]) {
      await page.setViewportSize({width, height: 900});
      const metrics = await bar.evaluate(node => {
        const rect = node.getBoundingClientRect();
        return {width: rect.width, height: rect.height, right: rect.right, viewport: innerWidth, overflow: node.scrollWidth > node.clientWidth + 1};
      });
      assert.ok(metrics.height <= 94 && metrics.right <= metrics.viewport && !metrics.overflow, JSON.stringify(metrics));
      await button('member_7', 'solo').click(); await pressed('member_7', 'solo', true);
      await button('member_7', 'solo').click(); await pressed('member_1', 'mute', true);
      if (process.env.FAMILY_CHAT_SCREENSHOTS) await page.screenshot({path: path.join(process.env.FAMILY_CHAT_SCREENSHOTS, `chat-${width}.png`)});
      console.log(JSON.stringify({width, ...metrics}));
    }
    assert.equal(new URL(page.url()).pathname, '/', 'M/S never navigates to the member Chat');
    assert.deepEqual(errors, []);
    const updates = await page.evaluate(() => calls.filter(call => call[0] === 'UpdateFamilyChatPreferences'));
    assert.ok(updates.every(([, args]) => args.resident_id === 'facade' && !args.actor && !args.username));
    await page.evaluate(() => panel.destroy());
    console.log('PASS: inline Chat M/S, popover sync, failure/retry, restore, narrow layout');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
