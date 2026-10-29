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
      window.calls = []; window.toasts = []; window.failNext = false; window.denyRead = false;
      PortalUI.toast = message => toasts.push(message);
      let policy = {muted_member_ids: [], solo_member_id: ''};
      const members = [0, 1].map(i => ({resident_id: `member_${i}`, title: `成员 ${i} · 架构与实现`, role: 'member'}));
      const family = {family_id: 'family_fixture', title: 'Family', lineage_id: 'fixture', members,
        nodes: [{resident_id: 'router', role: 'router', title: 'Router'}, ...members]};
      window.facadeDetail = () => ({resident_id: 'facade', resident: {resident_id: 'facade', family: {role: 'facade'}},
        family_facade: {...family, chat_preferences: {...policy}}});
      window.memberDetail = (id = 'member_0', role = 'member') => ({resident: {resident_id: id, lineage_id: 'fixture', title: '成员 · 架构与实现', family: {family_id: 'family_fixture', role}}});
      window.watchers = new Set();
      window.PortalResidentProjection = {
        subscribe: (id, listener) => { assertFacade(id); watchers.add(listener); queueMicrotask(() => listener({detail: facadeDetail()})); return () => watchers.delete(listener); },
        invalidate: id => { assertFacade(id); watchers.forEach(listener => listener({detail: facadeDetail()})); },
      };
      const assertFacade = id => { if (id !== 'facade') throw new Error('Wrong preference target: ' + id); };
      window.PortalInstructionClient = {execute: async (name, args) => {
        calls.push([name, args]);
        if (name === 'GetLineage') return {lineage: {function_blocks: [{id: 'family_fixture', family: {facade: 'facade', members: members.map(m => m.resident_id)}}]}};
        assertFacade(args.resident_id);
        if (name === 'GetFamilyChatPreferences' && denyRead) throw new Error('Not visible');
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
      window.PortalResidentService = {projection: async () => facadeDetail()};
      window.PortalResidentRuntimeController = {bind: () => ({
        state: () => ({}), load: async () => ({detail: {resident: {backend: 'codex'}}, settings: {}}), close() {}, destroy() {},
      })};
      window.PortalResidentProtocol = {...PortalResidentProtocol,
        loadSnapshot: async (id, opts) => { calls.push(['snapshot', id, opts]); assertFacade(id); },
        acquire: () => ({load: async () => ({state: {work_state: 'idle'}, messages: []}), destroy() {}}),
      };
      window.panel = PortalChatWorkspace.panelPackage.create({root: document.getElementById('chat'), id: 'chat', title: 'Chat Workspace · 架构与实现', autoLoad: false, target: {resident_id: 'member_0'}});
      panel.updateFamilyStatus(memberDetail());
    });
    const own = page.locator('[data-family-chat-member]');
    const button = (id, mode, parent = own) => parent.locator(`[data-member="${id}"][data-family-mix="${mode}"]`);
    const pressed = async (id, mode, active, parent = own) => {
      await button(id, mode, parent).locator(`xpath=self::*[@aria-pressed='${active}'][not(@disabled)]`).waitFor();
    };
    await pressed('member_0', 'mute', false);
    assert.equal(await page.locator('[data-family-chat-members]').count(), 0, 'no facade-wide member bar');
    assert.equal(await own.locator('button').count(), 2, 'only this member has controls');
    assert.equal(await own.evaluate(node => node.previousElementSibling.classList.contains('paneTitle')), true, 'buttons immediately follow the Chat title');
    await button('member_0', 'mute').click(); await pressed('member_0', 'mute', true);
    await button('member_0', 'solo').click(); await pressed('member_0', 'solo', true);
    await pressed('member_0', 'mute', false);
    await button('member_0', 'solo').click(); await pressed('member_0', 'mute', true);

    // The original Menu/status panel remains available and shares preferences.
    await page.evaluate(async () => {
      const anchor = document.createElement('button'); anchor.id = 'family-anchor'; document.body.append(anchor);
      window.menu = await PortalChatWorkspace.openFamilyStatus('facade', {anchor});
    });
    const popover = page.locator('.familyStatusPanel');
    await pressed('member_0', 'mute', true, popover);
    await button('member_0', 'mute', popover).click(); await pressed('member_0', 'mute', false);
    await button('member_1', 'solo', popover).click(); await pressed('member_1', 'solo', true, popover);
    await page.evaluate(() => menu.close());
    await button('member_0', 'solo').click(); await pressed('member_0', 'solo', true);
    await button('member_0', 'solo').click(); await pressed('member_0', 'solo', false);
    await page.evaluate(() => { failNext = true; });
    await button('member_0', 'mute').click();
    await page.waitForFunction(() => toasts.includes('Save failed'));
    await pressed('member_0', 'mute', false);
    assert.equal(await page.evaluate(() => document.activeElement.dataset.familyMix), 'mute');
    await button('member_0', 'mute').click(); await pressed('member_0', 'mute', true);

    for (const role of ['facade', 'router', 'steward', 'independent']) {
      await page.evaluate(role => panel.updateFamilyStatus(role === 'facade' ? facadeDetail() : memberDetail('other', role)), role);
      assert.equal(await own.isVisible(), false, role + ' must not have title M/S');
      assert.equal(await page.evaluate(() => watchers.size), 0, 'switching Chat releases the parent subscription');
    }
    // A slow lookup must not put member buttons back on a facade after switching.
    await page.evaluate(() => { panel.updateFamilyStatus(memberDetail()); panel.updateFamilyStatus(facadeDetail()); });
    await page.waitForTimeout(30);
    assert.equal(await own.isVisible(), false);
    assert.equal(await page.evaluate(() => watchers.size), 0);
    await page.evaluate(() => panel.updateFamilyStatus(memberDetail()));
    await pressed('member_0', 'mute', true);
    // A denied read clears the controls instead of applying someone else's state.
    await page.evaluate(() => { denyRead = true; PortalResidentProjection.invalidate('facade'); });
    await page.waitForFunction(() => toasts.includes('Not visible'));
    assert.equal(await own.isVisible(), false);
    await page.evaluate(() => { denyRead = false; PortalResidentProjection.invalidate('facade'); });
    await pressed('member_0', 'mute', true);

    for (const width of [1200, 390]) {
      await page.setViewportSize({width, height: 900});
      const metrics = await own.evaluate(node => {
        const rect = node.getBoundingClientRect();
        return {width: rect.width, height: rect.height, right: rect.right, viewport: innerWidth};
      });
      assert.ok(metrics.height <= 32 && metrics.right <= metrics.viewport, JSON.stringify(metrics));
      await button('member_0', 'solo').click(); await pressed('member_0', 'solo', true);
      await button('member_0', 'solo').click(); await pressed('member_0', 'mute', true);
      if (process.env.FAMILY_CHAT_SCREENSHOTS) await page.screenshot({path: path.join(process.env.FAMILY_CHAT_SCREENSHOTS, `member-chat-${width}.png`)});
      console.log(JSON.stringify({width, ...metrics}));
    }
    await page.evaluate(() => panel.updateFamilyStatus(facadeDetail()));
    if (process.env.FAMILY_CHAT_SCREENSHOTS) await page.screenshot({path: path.join(process.env.FAMILY_CHAT_SCREENSHOTS, 'facade-no-controls.png')});
    assert.equal(new URL(page.url()).pathname, '/', 'M/S never navigates');
    assert.deepEqual(errors, []);
    const updates = await page.evaluate(() => calls.filter(call => call[0] === 'UpdateFamilyChatPreferences'));
    assert.ok(updates.every(([, args]) => args.resident_id === 'facade' && !args.actor && !args.username));
    await page.evaluate(() => { menu.destroy(); panel.destroy(); });
    console.log('PASS: member Chat only, parent Family preferences, Menu sync, failure/retry, permissions, cleanup, narrow layout');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
