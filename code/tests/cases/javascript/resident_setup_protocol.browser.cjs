'use strict';
const assert = require('node:assert/strict'), fs = require('node:fs'), path = require('node:path');
const {chromium} = require('../../../management_portal/test_framework/tau_runtime/node_modules/playwright');
const root = path.resolve(__dirname, '../../../management_portal/static');
(async () => {
  const browser = await chromium.launch({headless: true});
  try {
    const page = await browser.newPage({viewport: {width: 1280, height: 900}}), errors = [];
    page.setDefaultTimeout(10000);
    page.on('pageerror', error => errors.push(error.message));
    await page.route('**/*', route => {
      const url = new URL(route.request().url());
      if (url.hostname !== 'resident.fixture.invalid') return route.abort();
      if (url.pathname.startsWith('/static/')) return route.fulfill({body: fs.readFileSync(path.join(root, url.pathname.slice(8))), contentType: url.pathname.endsWith('.css') ? 'text/css' : 'text/javascript'});
      return route.fulfill({contentType: 'text/html', body: '<!doctype html><html lang="zh-CN"><meta charset="utf-8"><body></body></html>'});
    });
    await page.goto('http://resident.fixture.invalid');
    for (const file of ['portal_components', 'portal_design_system', 'portal_themes', 'portal_resident_menu']) await page.addStyleTag({url: '/static/' + file + '.css'});
    for (const file of ['portal_text', 'portal_state', 'portal_translations', 'portal_i18n', 'portal_ui_primitives', 'portal_overlay', 'portal_ui', 'portal_model_api_settings', 'chat_workspace_runtime_settings']) await page.addScriptTag({url: '/static/' + file + '.js'});
    await page.evaluate(() => {
      const catalog = {models: ['m', 'm2'], default_model: 'm', efforts_by_model: {}, service_tiers_by_model: {}, runtime_capabilities: {settings: ['model', 'permissions'], permission_presets: ['full-access']}};
      window.calls = []; window.saves = [];
      window.PortalResidentService = {runtimeAccounts: async () => ({accounts: []}), runtimeCatalog: async () => catalog, observeSessionRestart: (_id, callback) => {window.notify = callback; return () => {};}};
      window.runtime = PortalChatWorkspaceRuntimeSettings.createRuntimeSettings({escapeHtml: PortalUIPrimitives.escapeHtml});
      window.dialog = runtime.createSessionRestartDialog();
      window.openDialog = () => dialog.open({resident_id: 'fixture', source: {provider: 'codex', account_id: 'two', has_session: true}, runtime_settings: {model: 'm'}, runtime_catalog: catalog,
        providers: [{id: 'codex', label: 'Codex'}], accounts: {codex: [{id: 'two', account_id: 'codex_two', label: 'two'}, {id: 'three', account_id: 'codex_three', label: 'three'}]},
        application: {items: [{path: 'capability_config', status: 'unknown', delivery: 'unknown'}]},
        onSave: async values => {saves.push(values); return {settings: values};}}, async values => {calls.push(values); return {status: 'completed', restart_id: 'done'};}, async () => ({}));
      openDialog();
    });
    assert.equal(await page.locator('[data-session-restart-submit]').isDisabled(), true);
    assert.equal(await page.locator('[data-runtime-settings-submit]').isVisible(), false);
    assert.equal(await page.locator('[data-session-restart="strategy"]').isVisible(), false);
    assert.equal(await page.locator('[data-session-reload]').isVisible(), false);
    await page.locator('[data-session-target]').selectOption(JSON.stringify(['codex', 'three']));
    await page.locator('[data-session-restart-close]').last().click();
    assert.equal(await page.evaluate(() => calls.length), 0, 'closing cancels the draft without switching');
    await page.evaluate(() => openDialog());
    await page.locator('[data-runtime-view="parameters"]').click();
    await page.locator('[data-runtime-panel="parameters"] [data-runtime-setting="model"]').selectOption('m2');
    await page.locator('[data-session-binding-snapshot] summary').click();
    await page.locator('[data-session-reload]').click();
    assert.equal(await page.locator('[data-runtime-panel="parameters"] [data-runtime-setting="model"]').inputValue(), 'm2');
    await page.locator('[data-runtime-settings-submit]').click();
    assert.equal(await page.evaluate(() => saves[0].model), 'm2');
    await page.locator('[data-runtime-view="capabilities"]').click();
    assert.match(await page.locator('[data-capability-application]').innerText(), /当前观察：未知.*交付：未知/);
    await page.locator('[data-runtime-view="switch_account"]').click();
    await page.locator('[data-session-target]').selectOption(JSON.stringify(['codex', 'three']));
    for (const width of [1280, 390]) {
      await page.setViewportSize({width, height: 900});
      await page.locator('[data-session-restart-submit]').scrollIntoViewIfNeeded();
      const size = await page.locator('[role="dialog"]').evaluate(el => ({width: el.getBoundingClientRect().width, scroll: el.scrollWidth, client: el.clientWidth}));
      assert(size.width <= width && size.scroll <= size.client + 1, 'dialog must fit without horizontal overflow');
    }
    if (process.env.PORTAL_UI_EVIDENCE) await page.screenshot({path: process.env.PORTAL_UI_EVIDENCE, fullPage: true});
    await page.locator('[data-session-restart-submit]').click();
    const payload = await page.evaluate(() => calls[0]);
    assert.equal(payload.operation, 'switch_account'); assert.equal(payload.context_strategy, 'preserve'); assert.equal(payload.resume_failed_input, false);
    assert.equal(await page.locator('.portalSessionRestartOverlay').isVisible(), false);
    assert.deepEqual(errors, []);
    console.log('PASS browser: isolated real dialog; cancel/save/switch; unknown delivery; draft refresh; 1280/390px; one Chromium page');
  } finally {await browser.close();}
})().catch(error => {console.error(error); process.exitCode = 1;});
