'use strict';
// Real menu shell and Family table, with isolated member data. No live writes.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('../../../management_portal/test_framework/tau_runtime/node_modules/playwright');
const { RESIDENT_MENU_SCRIPTS } = require('./resident_menu_scripts.cjs');
const { PORTAL_COMPONENTS_STYLES } = require('./portal_components_styles.cjs');
const root = path.resolve(__dirname, '../../../management_portal/static');
(async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.route('**/*', route => {
      const url = new URL(route.request().url());
      if (url.hostname !== 'family.fixture.invalid') return route.abort();
      if (url.pathname.startsWith('/static/')) return route.fulfill({
        body: fs.readFileSync(path.join(root, url.pathname.slice(8))),
        contentType: url.pathname.endsWith('.css') ? 'text/css' : 'text/javascript',
      });
      return route.fulfill({ contentType: 'text/html', body: '<!doctype html><html lang="zh-CN" data-portal-theme="light"><meta charset="utf-8"><body></body></html>' });
    });
    await page.goto('http://family.fixture.invalid');
    for (const name of [...PORTAL_COMPONENTS_STYLES, 'portal_workbench', 'lineage_workbench', 'portal_design_system', 'portal_themes', 'portal_resident_menu']) {
      await page.addStyleTag({ url: '/static/' + name + '.css' });
    }
    for (const name of ['portal_text', 'portal_state', 'portal_translations', 'portal_i18n', 'portal_ui_primitives',
      'portal_overlay', 'portal_ui', 'portal_detail_surface', 'portal_society_service', ...RESIDENT_MENU_SCRIPTS,
      'resident_protocol', 'chat_workspace_family_status']) {
      await page.addScriptTag({ url: '/static/' + name + '.js' });
    }
    await page.evaluate(async () => {
      // Open the production menu shell through its local group adapter, then
      // populate its existing Family slot without requesting business objects.
      window.menu = await PortalResidentMenu.open({ lineage: { lineage_id: 'fixture', nodes: [] },
        group: { active: 'fixture', tabs: [{ key: 'fixture', label: 'Family' }], render() {} } });
      document.getElementById('nodeMenuGroupPanel').hidden = true;
      document.getElementById('nodeDisplayPanel').hidden = false;
      document.getElementById('nodeSettingsForm').hidden = true;
      document.querySelector('.nodeSettingsResidentTabs').hidden = false;
      document.getElementById('nodeMenuGroupTabs').hidden = true;
      const container = document.getElementById('nodeMenuFamilySettings');
      container.hidden = false;
      container.innerHTML = '<p>在这里查看和调整 Family 内部成员的工作状态、Backend、账号与接单设置。</p><div data-members></div><button id="after-members">管理成员</button>';
      const nodes = Array.from({ length: 12 }, (_, i) => ({ resident_id: 'fixture_' + i, role: 'member', title: 'ArchExplorer 开发团队 · 成员 ' + i }));
      let chatPolicy = {muted_member_ids: [], solo_member_id: ''};
      window.PortalInstructionClient = {execute: async (name, args) => {
        if (name === 'UpdateFamilyChatPreferences') {
          if (args.control === 'mute') chatPolicy.muted_member_ids = args.enabled ? [args.member_id] : [];
          else chatPolicy.solo_member_id = args.enabled ? args.member_id : '';
        }
        return {...chatPolicy};
      }};
      window.PortalAPI = { get: async () => ({ accounts: [] }) };
      window.PortalResidentRuntimeController = { bind: (_id, handlers) => {
        window.refreshMember = () => handlers.onState({});
        return ({
        state: () => ({ canEdit: true }), destroy() {},
        load: async () => ({ detail: { resident: { backend: 'codex' } }, settings: { model: 'gpt-model-long-name', reasoning_effort: 'high' } }),
      }); } };
      window.PortalResidentProtocol = { ...PortalResidentProtocol, loadSnapshot: async () => ({}), acquire: () => ({
        destroy() {}, load: async () => ({ state: { work_state: 'idle', execution_gate: { state: 'open' } }, messages: [] }),
      }) };
      const status = PortalChatWorkspaceFamilyStatus.createFamilyStatus({
        escapeHtml: PortalUIPrimitives.escapeHtml, footerTime: x => x, sessionRestartActive: () => false,
      });
      window.familyView = status.createFamilyStatusView('fixture', null, null,
        () => ({ title: 'Family', lineage_id: 'fixture', nodes }), container.querySelector('[data-members]'));
      familyView.open();
    });
    await page.waitForFunction(() => document.querySelectorAll('[data-family-status-row]').length === 12);
    const mute = page.locator('[data-family-status-row="fixture_0"] [data-family-mix="mute"]');
    await mute.click();
    await page.waitForFunction(() => document.querySelector('[data-member="fixture_0"][data-family-mix="mute"]').getAttribute('aria-pressed') === 'true');
    await page.locator('[data-member="fixture_1"][data-family-mix="solo"]').click();
    await page.waitForFunction(() => document.querySelector('[data-member="fixture_1"][data-family-mix="solo"]').getAttribute('aria-pressed') === 'true');
    await page.locator('[data-member="fixture_1"][data-family-mix="solo"]').click();
    await page.waitForFunction(() => document.querySelector('[data-member="fixture_0"][data-family-mix="mute"]').getAttribute('aria-pressed') === 'true');
    for (const width of [1440, 840, 600, 390]) {
      await page.setViewportSize({ width, height: 900 });
      const metrics = await page.evaluate(async () => {
        const body = document.querySelector('.nodeSettingsBody');
        const nav = document.querySelector('.nodeSettingsTabs');
        let scroller = document.querySelector('.familyStatusBody');
        body.scrollLeft = 0;
        const navLeft = nav.getBoundingClientRect().left;
        scroller.scrollLeft = 10000;
        const tableScrollBefore = scroller.scrollLeft;
        refreshMember();
        scroller = document.querySelector('.familyStatusBody');
        body.scrollLeft = 10000;
        await new Promise(resolve => requestAnimationFrame(resolve));
        const r = scroller.getBoundingClientRect(), n = nav.getBoundingClientRect();
        const hit = document.elementFromPoint(n.left + n.width / 2, n.top + 10);
        body.scrollTop = 10000;
        const action = document.getElementById('after-members').getBoundingClientRect();
        return { outerScroll: body.scrollLeft, tableScroll: scroller.scrollLeft,
          scrollPreserved: scroller.scrollLeft === tableScrollBefore,
          navStable: Math.abs(n.left - navLeft) < 1, navUncovered: nav.contains(hit),
          inContent: r.left >= n.right && r.right <= body.getBoundingClientRect().right,
          bottomReachable: action.bottom <= body.getBoundingClientRect().bottom,
          height: scroller.clientHeight, contentHeight: scroller.scrollHeight };
      });
      console.log(JSON.stringify({ width, ...metrics }));
      assert.equal(metrics.outerScroll, 0, 'horizontal scrolling must not move the menu rail');
      assert.ok(metrics.scrollPreserved, 'live updates must preserve the table reading position');
      assert.ok(metrics.tableScroll > 0 && metrics.navStable && metrics.navUncovered && metrics.inContent,
        'the wide member table must scroll only to the right of the divider');
      assert.ok(metrics.bottomReachable && metrics.height >= metrics.contentHeight - 1,
        'Settings retains one vertical scrollbar and its bottom actions stay reachable');
    }
    assert.deepEqual(errors, []);
    if (process.env.FAMILY_SETTINGS_SCREENSHOT) {
      await page.setViewportSize({ width: 840, height: 900 });
      await page.evaluate(() => {
        document.querySelector('.familyStatusBody').scrollLeft = 0;
        document.querySelector('.nodeSettingsBody').scrollTop = 0;
      });
      await page.screenshot({ path: process.env.FAMILY_SETTINGS_SCREENSHOT });
    }
    await page.evaluate(() => { familyView.destroy(); menu.destroy(); });
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
