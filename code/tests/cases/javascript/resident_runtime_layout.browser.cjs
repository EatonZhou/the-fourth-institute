'use strict';
// Production dialog, overlay owner and CSS; provider mutations stay in this fixture.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {chromium} = require('../../../management_portal/test_framework/tau_runtime/node_modules/playwright');
const root = path.resolve(__dirname, '../../../management_portal/static');
(async () => {
  const browser = await chromium.launch({headless: true, ...(process.env.PORTAL_TEST_CHROMIUM ? {executablePath: process.env.PORTAL_TEST_CHROMIUM} : {})});
  try {
    const page = await browser.newPage({viewport: {width: 1200, height: 900}});
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.route('**/*', route => {
      const url = new URL(route.request().url());
      if (url.hostname !== 'runtime.fixture.invalid') return route.abort();
      if (url.pathname.startsWith('/static/')) return route.fulfill({body: fs.readFileSync(path.join(root, url.pathname.slice(8))), contentType: url.pathname.endsWith('.css') ? 'text/css' : 'text/javascript'});
      return route.fulfill({contentType: 'text/html; charset=utf-8', body: '<!doctype html><html lang="zh-CN" data-portal-theme="light"><meta charset="utf-8"><body><button id="background">背景操作</button></body></html>'});
    });
    await page.goto('http://runtime.fixture.invalid');
    for (const name of ['portal_components', 'portal_workbench', 'lineage_workbench', 'portal_design_system', 'portal_themes']) await page.addStyleTag({url: '/static/' + name + '.css'});
    for (const name of ['portal_text', 'portal_state', 'portal_translations', 'portal_i18n', 'portal_ui_primitives', 'portal_overlay', 'portal_ui', 'portal_detail_surface', 'portal_model_api_settings', 'chat_workspace_runtime_settings']) await page.addScriptTag({url: '/static/' + name + '.js'});
    await page.evaluate(() => {
      window.requests = []; window.saves = []; window.backgroundClicks = 0;
      document.getElementById('background').onclick = () => backgroundClicks++;
      const catalog = {models: ['m', 'm2'], default_model: 'm', efforts_by_model: {}, service_tiers_by_model: {}, runtime_capabilities: {settings: ['model']}};
      const choices = preserve => [{id: 'preserve', enabled: preserve, description: '完整保留上下文'}, {id: 'compact', enabled: true, description: '摘要交接可能遗漏细节和工具过程'}, {id: 'fresh', enabled: true}];
      window.PortalResidentService = {runtimeAccounts: async () => ({accounts: []}), runtimeCatalog: async () => catalog, observeSessionRestart: () => () => {}, refreshSessionRestart: async () => ({})};
      PortalUI.toast = message => {throw new Error(message);};
      const api = PortalChatWorkspaceRuntimeSettings.createRuntimeSettings({escapeHtml: PortalUIPrimitives.escapeHtml});
      window.openSettings = () => {
        window.dialog?.destroy();
        window.dialog = api.createSessionRestartDialog();
        dialog.open({resident_id: 'fixture', source: {provider: 'codex', account_id: 'one'}, runtime_settings: {model: 'm'}, runtime_catalog: catalog,
          application: {items: Array.from({length: 30}, (_, i) => ({path: 'capability_config.' + i, status: 'unknown', delivery: 'unknown'}))},
          providers: [{id: 'codex', label: 'Codex'}, {id: 'claude', label: 'Claude'}], accounts: {codex: [{id: 'one', context_options: choices(true)}, {id: 'two', context_options: choices(true)}], claude: [{id: 'claude_one', context_options: choices(false)}]},
          onSave: async values => {saves.push(values); return {settings: values};}}, async values => {requests.push(values); return {status: 'completed'};});
      };
      openSettings();
    });
    const tab = view => page.locator('[data-runtime-view="' + view + '"]').click();
    for (const width of [1200, 840, 390]) {
      await page.setViewportSize({width, height: 900});
      await tab('switch_account');
      const initial = await page.locator('.portalRuntimeSettingsSurface').boundingBox();
      for (const view of ['parameters', 'capabilities', 'restart', 'switch_account']) {
        await tab(view);
        assert.equal(await page.locator('[data-runtime-view="' + view + '"]').getAttribute('aria-pressed'), 'true');
        const box = await page.locator('.portalRuntimeSettingsSurface').boundingBox();
        assert.ok(Math.abs(initial.height - box.height) < 1 && Math.abs(initial.width - box.width) < 1 && Math.abs(initial.y - box.y) < 1, 'card dimensions and position must stay stable: ' + JSON.stringify({width, view, initial, box}));
        assert.ok(box.x >= 0 && box.x + box.width <= width && box.y >= 0 && box.y + box.height <= 900);
        const nav = page.locator('.portalRuntimePurposeNav');
        const visible = await nav.evaluate(el => {const b = el.getBoundingClientRect(); return el.contains(document.elementFromPoint(b.x + 20, b.y + 15));});
        assert.ok(visible, 'content must not cover the tabs');
        const close = await page.locator('[data-session-restart-close]').first().boundingBox();
        assert.ok(box.x + box.width - close.x - close.width < 24, 'close action stays at the right edge');
      }
      await tab('capabilities');
      await page.locator('[data-capability-application]').evaluate(el => {el.closest('details').open = true;});
      const body = page.locator('.portalSessionRestartOverlay [data-portal-detail-body]');
      await body.evaluate(el => {el.scrollTop = el.scrollHeight;});
      assert.ok(await body.evaluate(el => el.scrollTop > 0 && el.scrollWidth <= el.clientWidth + 1));
      await page.locator('[data-capability-submit]').click();
      await page.locator('[data-session-restart-close]').first().click();
      await page.locator('#background').click();
      assert.equal(await page.locator('body').evaluate(el => el.classList.contains('modalOpen')), false);
      await page.evaluate(() => openSettings());
    }
    assert.equal(await page.locator('[data-session-binding-snapshot]').count(), 0, 'no diagnostics in settings');
    await tab('parameters');
    await page.selectOption('[data-runtime-panel="parameters"] [data-runtime-setting="model"]', 'm2');
    await tab('capabilities'); await tab('parameters');
    assert.equal(await page.inputValue('[data-runtime-panel="parameters"] [data-runtime-setting="model"]'), 'm2');
    await page.click('[data-runtime-settings-submit]');
    await page.waitForFunction(() => saves.length === 1);
    assert.equal(await page.evaluate(() => requests.length), 0);
    await page.keyboard.press('Escape'); await page.locator('#background').click();
    await page.evaluate(() => openSettings());
    await page.setViewportSize({width: 1200, height: 900});
    await page.mouse.click(3, 3); await page.locator('#background').click();
    await page.evaluate(() => openSettings());
    await page.selectOption('[data-session-target]', JSON.stringify(['claude', 'claude_one']));
    await page.selectOption('[data-target-editor-host] [data-runtime-setting="model"]', 'm2');
    assert.match(await page.locator('[data-switch-context-description]').innerText(), /遗漏细节/);
    assert.equal(await page.locator('[data-session-restart-submit]').innerText(), '交接并切换');
    await page.locator('[data-session-restart-close]').last().click();
    assert.equal(await page.evaluate(() => requests.length), 0, 'cancel does not switch');
    await page.evaluate(() => openSettings());
    await page.selectOption('[data-session-target]', JSON.stringify(['claude', 'claude_one']));
    await page.selectOption('[data-target-editor-host] [data-runtime-setting="model"]', 'm2');
    await page.click('[data-session-restart-submit]');
    await page.waitForFunction(() => requests.length === 1);
    assert.equal(await page.evaluate(() => requests[0].context_strategy), 'compact');
    await page.evaluate(() => openSettings()); await tab('restart');
    await page.selectOption('[data-session-restart="strategy"]', 'fresh');
    await page.click('[data-session-restart-submit]');
    await page.waitForFunction(() => requests.length === 2);
    assert.equal(await page.evaluate(() => requests[1].context_strategy), 'fresh');
    assert.deepEqual(errors, []);
    if (process.env.RUNTIME_SETTINGS_SCREENSHOT) {
      await page.evaluate(() => openSettings()); await tab('capabilities');
      await page.screenshot({path: process.env.RUNTIME_SETTINGS_SCREENSHOT});
    }
    console.log('PASS runtime dialog: stable 1200/840/390px layout, scrolling, X/Escape/backdrop/cancel, background interaction, save, handoff and deliberate fresh restart');
  } finally {await browser.close();}
})().catch(error => {console.error(error); process.exitCode = 1;});
