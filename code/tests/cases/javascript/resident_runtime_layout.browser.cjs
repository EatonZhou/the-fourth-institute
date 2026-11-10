'use strict';
// Production runtime panel, overlay owner and CSS; provider mutations stay in this fixture.
// The panel shows the account and the parameters on one page, in a dialog and
// in place inside a host form (the Resident menu).
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {chromium} = require('../../../management_portal/test_framework/tau_runtime/node_modules/playwright');
const {PORTAL_COMPONENTS_STYLES} = require('./portal_components_styles.cjs');
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
      return route.fulfill({contentType: 'text/html; charset=utf-8', body: '<!doctype html><html lang="zh-CN" data-portal-theme="light"><meta charset="utf-8"><body><button id="background">背景操作</button><form id="hostForm" style="width:520px;margin:16px"><div id="inlineHost"></div></form></body></html>'});
    });
    await page.goto('http://runtime.fixture.invalid');
    for (const name of [...PORTAL_COMPONENTS_STYLES, 'portal_workbench', 'lineage_workbench', 'portal_design_system', 'portal_themes', 'portal_resident_menu']) await page.addStyleTag({url: '/static/' + name + '.css'});
    for (const name of ['portal_text', 'portal_state', 'portal_translations', 'portal_i18n', 'portal_ui_primitives', 'portal_overlay', 'portal_ui', 'portal_detail_surface', 'portal_model_api_settings', 'chat_workspace_runtime_settings']) await page.addScriptTag({url: '/static/' + name + '.js'});
    await page.evaluate(() => {
      window.requests = []; window.saves = []; window.backgroundClicks = 0; window.hostSubmits = 0;
      document.getElementById('background').onclick = () => backgroundClicks++;
      document.getElementById('hostForm').addEventListener('submit', event => {event.preventDefault(); hostSubmits++;});
      const catalog = {models: ['m', 'm2'], default_model: 'm', efforts_by_model: {m: ['low', 'high'], m2: ['low', 'high']}, service_tiers_by_model: {}, runtime_capabilities: {settings: ['model', 'reasoning_effort', 'permissions', 'collaboration_mode'], permission_presets: ['full-access']}};
      const choices = preserve => [{id: 'preserve', enabled: preserve, description: '完整保留上下文'}, {id: 'compact', enabled: true, description: '摘要交接可能遗漏细节和工具过程'}, {id: 'fresh', enabled: true}];
      window.PortalResidentService = {runtimeAccounts: async () => ({accounts: []}), runtimeCatalog: async () => catalog, observeSessionRestart: () => () => {}, refreshSessionRestart: async () => ({})};
      PortalUI.toast = message => {throw new Error(message);};
      const api = PortalChatWorkspaceRuntimeSettings.createRuntimeSettings({escapeHtml: PortalUIPrimitives.escapeHtml});
      window.initializationRequests = [];
      PortalResidentService.retryResidentInitialization = async (rid, values) => {
        initializationRequests.push({rid, ...values}); return {status: 'running'};
      };
      window.config = extra => ({resident_id: 'fixture', source: {provider: 'codex', account_id: 'one'}, runtime_settings: {model: 'm'}, runtime_catalog: catalog,
        credits_confirmation: {allowed: true, scope: 'target_session'},
        providers: [{id: 'codex', label: 'Codex'}, {id: 'claude', label: 'Claude'}], accounts: {codex: [{id: 'one', context_options: choices(true)}, {id: 'two', context_options: choices(true)}], claude: [{id: 'claude_one', context_options: choices(false)}]},
        onSave: async values => {saves.push(values); return {settings: values};}, ...extra});
      window.openSettings = (extra = {}) => {
        window.dialog?.destroy();
        window.dialog = api.createSessionRestartDialog();
        dialog.open(config(extra), async values => {requests.push(values); return {status: 'completed'};});
      };
      window.inlineDone = 0;
      window.mountInline = () => {
        window.inline?.destroy();
        window.inline = api.createRuntimePanel({inline: true, onDone: () => inlineDone++});
        document.getElementById('inlineHost').replaceChildren(inline.element);
        inline.open(config(), async values => {requests.push(values); return {status: 'completed'};});
      };
      openSettings();
    });
    const surface = page.locator('.portalSessionRestartOverlay .portalRuntimeSettingsSurface');
    assert.equal(await page.locator('[data-runtime-view]').count(), 0, 'no tabs');
    for (const width of [1200, 840, 390]) {
      await page.setViewportSize({width, height: 900});
      const box = await surface.boundingBox();
      assert.ok(box.x >= 0 && box.x + box.width <= width && box.y >= 0 && box.y + box.height <= 900, 'dialog fits: ' + JSON.stringify({width, box}));
      const body = page.locator('.portalSessionRestartOverlay [data-portal-detail-body]');
      assert.ok(await body.evaluate(el => el.scrollWidth <= el.clientWidth + 1), 'no horizontal overflow');
      const close = await page.locator('[data-session-restart-close]').boundingBox();
      assert.ok(box.x + box.width - close.x - close.width < 24, 'close action stays at the right edge');
      assert.ok(await page.locator('[data-runtime-section="account"]').isVisible());
      assert.ok(await page.locator('[data-runtime-editor="current"]').isVisible(), 'account and parameters are on the same page');
    }
    await page.setViewportSize({width: 1200, height: 900});
    if (process.env.RUNTIME_SETTINGS_SCREENSHOT) await surface.screenshot({path: process.env.RUNTIME_SETTINGS_SCREENSHOT});
    // X, Escape and the backdrop close without changing anything.
    await page.locator('[data-session-restart-close]').click();
    await page.locator('#background').click();
    await page.evaluate(() => openSettings()); await page.keyboard.press('Escape'); await page.locator('#background').click();
    await page.evaluate(() => openSettings()); await page.mouse.click(3, 3); await page.locator('#background').click();
    assert.equal(await page.evaluate(() => backgroundClicks), 3);
    assert.equal(await page.locator('body').evaluate(el => el.classList.contains('modalOpen')), false);
    await page.evaluate(() => openSettings());
    assert.equal(await page.locator('[data-session-restart-submit]').isDisabled(), true, 'nothing changed');
    await page.selectOption('[data-runtime-editor="current"] [data-runtime-setting="model"]', 'm2');
    assert.match(await page.locator('[data-runtime-save-hint]').innerText(), /下一轮|next turn/);
    await page.click('[data-session-restart-submit]');
    await page.waitForFunction(() => saves.length === 1);
    assert.equal(await page.evaluate(() => requests.length), 0, 'saving parameters keeps the Session');
    assert.equal(await page.locator('.portalSessionRestartOverlay').isVisible(), false, 'a saved change closes the dialog');
    await page.evaluate(() => openSettings());
    await page.selectOption('[data-session-backend]', 'claude');
    await page.selectOption('[data-session-target]', JSON.stringify(['claude', 'claude_one']));
    await page.waitForFunction(() => document.querySelector('[data-target-editor-host] [data-runtime-setting="model"]')?.selectedOptions[0]?.textContent === 'm (default)');
    assert.equal(await page.locator('[data-runtime-editor="current"]').isVisible(), false, 'a cross-Provider switch shows the target parameters');
    assert.match(await page.locator('[data-switch-context-description]').innerText(), /遗漏细节/);
    assert.equal(await page.locator('[data-session-restart-submit]').innerText(), await page.evaluate(() => PortalI18n.t('交接并切换')));
    await page.locator('[data-runtime-panel-reset]').click();
    assert.equal(await page.evaluate(() => requests.length), 0, 'cancel does not switch');
    await page.evaluate(() => openSettings());
    await page.selectOption('[data-session-backend]', 'claude');
    await page.selectOption('[data-session-target]', JSON.stringify(['claude', 'claude_one']));
    await page.waitForFunction(() => document.querySelector('[data-target-editor-host] [data-runtime-setting="model"]')?.selectedOptions[0]?.textContent === 'm (default)');
    await page.click('[data-session-restart-submit]');
    await page.waitForFunction(() => requests.length === 1);
    assert.equal(await page.evaluate(() => requests[0].model), 'm', 'switch submits the model displayed by the default option');
    assert.equal(await page.evaluate(() => requests[0].context_strategy), 'compact');
    // A same-Provider switch to a paid account asks for an explicit credits confirmation.
    await page.evaluate(() => openSettings());
    await page.selectOption('[data-session-target]', JSON.stringify(['codex', 'two']));
    await page.check('[data-session-credits-consent]');
    await page.click('[data-session-restart-submit]');
    await page.waitForFunction(() => requests.length === 2);
    assert.equal(await page.evaluate(() => requests[1].context_strategy), 'preserve');
    assert.deepEqual(await page.evaluate(() => requests[1].credits_consent), {account_id: 'two', confirmed: true});
    // A failed or running initialization continues from the same panel.
    await page.evaluate(() => openSettings({initialization: {initialization_id: 'original-init', status: 'running'}}));
    assert.equal(await page.locator('[data-session-restart-submit]').innerText(), await page.evaluate(() => PortalI18n.t('继续当前初始化')));
    assert.equal(await page.locator('[data-runtime-section="account"]').isVisible(), false);
    assert.equal(await page.locator('[data-session-credits-consent]').isChecked(), false);
    await page.check('[data-session-credits-consent]');
    await page.click('[data-session-restart-submit]');
    await page.waitForFunction(() => initializationRequests.length === 1);
    const initialization = await page.evaluate(() => initializationRequests[0]);
    assert.equal(initialization.expected_initialization_id, 'original-init');
    assert.deepEqual(initialization.credits_consent, {account_id: 'one', confirmed: true});
    assert.equal(await page.evaluate(() => requests.length), 2, 'initialization must not start a Session replacement');
    await page.evaluate(() => openSettings({initialization: {initialization_id: 'next-init', status: 'running'}}));
    assert.equal(await page.locator('[data-session-credits-consent]').isChecked(), false, 'a later attempt cannot inherit confirmation');
    await page.locator('[data-session-restart-close]').click();
    await page.evaluate(() => openSettings({initialization: {initialization_id: 'next-init', status: 'running'}, credits_confirmation: {allowed: false}}));
    assert.equal(await page.locator('[data-session-credits-consent]').isVisible(), false, 'only humans can confirm');
    await page.locator('[data-session-restart-close]').click();
    // In place, inside the host's own form: the same panel, and its buttons never submit the host.
    await page.evaluate(() => mountInline());
    const inlinePanel = page.locator('#inlineHost .portalRuntimePanel.is-inline');
    assert.equal(await inlinePanel.evaluate(el => el.tagName), 'DIV');
    assert.ok(await inlinePanel.locator('[data-runtime-section="account"]').isVisible());
    assert.equal(await inlinePanel.locator('[data-runtime-panel-reset]').isDisabled(), true, 'nothing to revert');
    if (process.env.RUNTIME_SETTINGS_INLINE_SCREENSHOT) await inlinePanel.screenshot({path: process.env.RUNTIME_SETTINGS_INLINE_SCREENSHOT});
    await inlinePanel.locator('[data-runtime-editor="current"] [data-runtime-setting="model"]').selectOption('m2');
    await inlinePanel.locator('[data-runtime-panel-reset]').click();
    assert.equal(await inlinePanel.locator('[data-runtime-editor="current"] [data-runtime-setting="model"]').inputValue(), 'm', 'revert restores the values in effect');
    await inlinePanel.locator('[data-runtime-editor="current"] [data-runtime-setting="model"]').selectOption('m2');
    await inlinePanel.locator('[data-session-restart-submit]').click();
    await page.waitForFunction(() => saves.length === 2 && inlineDone === 1);
    assert.equal(await page.evaluate(() => hostSubmits), 0, 'the panel does not submit the host form');
    assert.deepEqual(errors, []);
    console.log('PASS runtime panel: one page in a dialog and in place, 1200/840/390px, X/Escape/backdrop/cancel, save, handoff, credits and initialization');
  } finally {await browser.close();}
})().catch(error => {console.error(error); process.exitCode = 1;});
