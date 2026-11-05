'use strict';
// Real Settings, shared runtime controller and existing switch card; no live accounts.
const assert = require('node:assert/strict'), fs = require('node:fs'), path = require('node:path');
const {chromium} = require('../../../management_portal/test_framework/tau_runtime/node_modules/playwright');
const {PORTAL_COMPONENTS_STYLES} = require('./portal_components_styles.cjs');
const root = path.resolve(__dirname, '../../../management_portal/static');
(async () => {
  const browser = await chromium.launch({headless: true, ...(process.env.PORTAL_CHROMIUM_EXECUTABLE ? {executablePath: process.env.PORTAL_CHROMIUM_EXECUTABLE} : {})});
  try {
    const page = await browser.newPage({viewport: {width: 1000, height: 800}}), errors = [];
    page.setDefaultTimeout(10000);
    page.on('pageerror', error => errors.push(error.message));
    await page.route('**/*', route => {
      const url = new URL(route.request().url());
      if (url.hostname !== 'quota.fixture.invalid') return route.abort();
      if (url.pathname.startsWith('/static/')) return route.fulfill({body: fs.readFileSync(path.join(root, url.pathname.slice(8))), contentType: url.pathname.endsWith('.css') ? 'text/css' : 'text/javascript'});
      return route.fulfill({contentType: 'text/html', body: '<!doctype html><html lang="zh-CN"><meta charset="utf-8"><body><span id="nodeMenuSessionId"></span><button id="nodeMenuCopySession"></button><div id="nodeMenuRuntime"></div><section id="chat"><div class="chatHeadText"><span id="status"></span></div><button id="refresh">Session</button><div id="composer"><button data-workspace-runtime-settings-open><span class="composerRuntimeLabel"></span></button></div></section></body></html>'});
    });
    await page.goto('http://quota.fixture.invalid');
    for (const file of [...PORTAL_COMPONENTS_STYLES, 'portal_design_system', 'portal_themes', 'portal_resident_menu']) await page.addStyleTag({url: '/static/' + file + '.css'});
    for (const file of ['portal_text', 'portal_state', 'portal_translations', 'portal_i18n', 'portal_ui_primitives', 'portal_overlay', 'portal_ui', 'portal_model_api_settings', 'chat_workspace_runtime_settings', 'portal_resident_runtime_controller', 'portal_resident_menu_runtime', 'chat_workspace_runtime_controls', 'chat_workspace_resident_workspace']) await page.addScriptTag({url: '/static/' + file + '.js'});
    await page.evaluate(() => {
      const catalog = {models: ['m'], default_model: 'm', efforts_by_model: {}, service_tiers_by_model: {}, runtime_capabilities: {settings: ['model']}};
      const contextOptions = [{id: 'preserve', enabled: true, description: '完整保留上下文'}];
      let settings = {model: 'm'}, revision = 'r0';
      window.saves = []; window.switches = []; window.toasts = []; window.canEdit = true; window.canSwitch = true; window.optionTargets = []; window.currentResident = "fixture";
      PortalUI.toast = message => toasts.push(message);
      const detail = () => ({resident: {backend: 'codex'}, family_facade: {}, execution_account: {account_id: 'one'}, chat_workspace: {actions: {runtime_settings: {}}}, allows: {UpdateResidentSettings: canEdit, RestartSession: canSwitch}});
      window.PortalResidentProjection = {load: async () => detail(), invalidate() {}};
      const read = () => ({settings: {...settings}, settings_revision: revision, quota_switch: {auto_switch_on_quota: settings.auto_switch_on_quota ?? false}});
      window.PortalResidentService = {
        projection: async () => detail(), runtimeSettings: async () => read(), runtimeCatalog: async () => catalog,
        runtimeAccounts: async () => ({accounts: []}), observeSessionRestart: () => () => {},
        updateRuntimeSettings: async (id, changes, key, expected) => {
          if (!canEdit) throw new Error('无权限');
          if (expected !== revision) throw new Error('stale revision');
          saves.push({id, changes, key, expected}); settings = {...settings, ...changes}; revision += '1'; return read();
        },
        sessionRestartOptions: async id => { optionTargets.push(id); if (window.holdOptions) await new Promise(resolve => window.releaseOptions = resolve); return ({resident_id: id, source: {provider: 'codex', account_id: 'one', has_session: true},
          providers: [{id: 'codex', label: 'Codex'}, {id: 'claude', label: 'Claude'}],
          accounts: {codex: [{id: 'one', label: 'Current', context_options: contextOptions}, {id: 'outside-default', label: 'Outside default ordering', context_options: contextOptions}], claude: [{id: 'claude-one', label: 'Claude One', context_options: contextOptions}]}}); },
        restartSession: async value => {switches.push(value); return {status: 'completed', restart_id: 'op1'};},
      };
      const api = PortalChatWorkspaceRuntimeSettings.createRuntimeSettings({escapeHtml: PortalUIPrimitives.escapeHtml});
      window.PortalChatWorkspace = api;
      const context = window.menuContext = {residentId: 'fixture', node: {backend: 'codex'}};
      const menu = PortalResidentMenuRuntime.createRuntime({$id: id => document.getElementById(id), esc: PortalUIPrimitives.escapeHtml, menuState: {nodeMenuContext: context}, nodeMenuNotice: text => toasts.push(text), renderFamilySettings() {}});
      window.reloadMenu = () => menu.loadNodeMenuRuntime();
      window.state = {contract: {actions: {session_restart: {instruction: 'RestartSession'}}, composer: {}}};
      window.controls = PortalChatWorkspaceRuntimeControls.createRuntimeControls({contractResidentId: () => currentResident, createSessionRestartProgress: api.createSessionRestartProgress,
        currentController: () => null, els: {composer: document.getElementById('composer'), status: document.getElementById('status'), refresh: document.getElementById('refresh')},
        options: {}, residentRuntimeStatusText: api.residentRuntimeStatusText, sessionRestartActive: api.sessionRestartActive, state});
      window.quota = value => {state.contract.composer.quota_switch = value; controls.followContractResident();};
      // Use the Resident workspace facade used by the real send acknowledgement.
      const workspaceTypes = PortalChatWorkspaceResidentWorkspace.createResidentWorkspace({
        createWorkspace: () => controls, escapeHtml: PortalUIPrimitives.escapeHtml,
        messageIdentity: item => item.message_id, messageRole: item => item.role, messageTitle: () => '',
        normalizeRefs: value => value || {}, normalizeResidentTarget: value => value,
        residentProtocolMessageToWorkspace: value => value, residentSessionScope: () => 'fixture',
        workspaceInstanceId: () => 'fixture',
      });
      window.residentWorkspace = new workspaceTypes.ResidentChatWorkspace({target: {resident_id: 'fixture'}});
      residentWorkspace.controller = controls;
      controls.bindRuntimeButton();
      return reloadMenu();
    });
    const toggle = page.locator('[data-menu-quota-switch]');
    assert.equal(await toggle.getAttribute('aria-checked'), 'false', 'unset preserves effective old policy');
    await toggle.click();
    await page.waitForFunction(() => saves.length === 1 && document.querySelector('[data-menu-quota-switch]').getAttribute('aria-checked') === 'true');
    assert.deepEqual(await page.evaluate(() => saves[0].changes), {auto_switch_on_quota: true});
    await page.evaluate(() => reloadMenu());
    assert.equal(await toggle.getAttribute('aria-checked'), 'true', 'readback retains setting');
    await toggle.click();
    await page.waitForFunction(() => saves.length === 2 && !document.querySelector('[data-menu-quota-switch]').disabled);
    assert.deepEqual(await page.evaluate(() => saves[1].changes), {auto_switch_on_quota: false});
    await page.evaluate(() => quota({state: 'disabled', reason: 'auto_switch_disabled', message: '额度不足，请手动选择'}));
    assert.equal(await page.locator('.portalSessionRestartOverlay').count(), 0, 'viewing existing history does not pop a card');
    await page.evaluate(() => residentWorkspace.quotaSubmitted('message1'));
    await page.locator('[data-session-target]').waitFor({state: 'visible'});
    assert.equal(await page.locator('[data-session-target] optgroup option').count(), 3, 'manual picker includes all accounts, even outside priority');
    await page.locator('[data-session-restart-close]').last().click();
    await page.evaluate(() => quota({state: 'disabled', reason: 'auto_switch_disabled'}));
    assert.equal(await page.locator('.portalSessionRestartOverlay:visible').count(), 0, 'same receipt cannot repeatedly reopen dismissed card');
    assert.equal(await page.evaluate(() => switches.length), 0, 'cancel never switches or resends');
    await page.evaluate(() => {quota({state: 'switching', reason: 'quota_used_up'}); residentWorkspace.quotaSubmitted('message2'); quota({state: 'unavailable', reason: 'quota_unknown'});});
    assert.equal(await page.locator('.portalSessionRestartOverlay:visible').count(), 0, 'unknown or switching never asks for manual choice');
    const exhausted = '当前没有默认可切换的ai后端, 请手动选择';
    await page.evaluate(message => quota({state: 'manual_required', reason: 'default_backends_unavailable', message}), exhausted);
    assert.equal(await page.locator('.portalChatQuotaNotice span').innerText(), exhausted);
    assert.equal(await page.evaluate(() => toasts.at(-1)), exhausted);
    await page.locator('.portalChatQuotaNotice button').click();
    await page.locator('[data-session-target]').selectOption(JSON.stringify(['codex', 'outside-default']));
    await page.waitForFunction(() => !document.querySelector('[data-session-restart-submit]').disabled);
    if (process.env.PORTAL_UI_EVIDENCE) await page.screenshot({path: process.env.PORTAL_UI_EVIDENCE, fullPage: true});
    await page.locator('[data-session-restart-submit]').click();
    await page.waitForFunction(() => switches.length === 1);
    const payload = await page.evaluate(() => switches[0]);
    assert.equal(payload.target_account, 'outside-default'); assert.equal(payload.context_strategy, 'preserve'); assert.equal(payload.resume_failed_input, false);
    await page.evaluate(() => {
      quota({state: 'unavailable', reason: 'quota_unknown'}); residentWorkspace.quotaSubmitted('message3');
      state.contract.request_progress = {input_states: [{message_id: 'message3', state: 'failed'}]};
      controls.followContractResident();
    });
    assert.equal(await page.locator('.portalSessionRestartOverlay:visible').count(), 0, 'a failed receipt alone never opens the picker');
    await page.evaluate(() => quota({state: 'disabled', reason: 'auto_switch_disabled'}));
    await page.locator('[data-session-target]').waitFor({state: 'visible'});
    await page.locator('[data-session-restart-close]').last().click();
    await page.evaluate(() => {
      quota({state: 'ready', reason: ''}); residentWorkspace.quotaSubmitted('message4');
      state.contract.request_progress = {input_states: [{message_id: 'message4', state: 'completed'}]};
      controls.followContractResident(); quota({state: 'disabled', reason: 'auto_switch_disabled'});
    });
    assert.equal(await page.locator('.portalSessionRestartOverlay:visible').count(), 0, 'completed work cannot trigger a later quota popup');
    // Family facades use exact target controls, never the facade's unused Session.
    await page.evaluate(() => {
      currentResident = 'facade'; state.contract.actions = {};
      window.target = (id, message = 'f1', extra = {}) => ({target_resident_id: id, target_title: id + ' name',
        target_role: 'member', target_role_label: '成员', can_manage: true, request_message_ids: [message],
        state: 'disabled', reason: 'auto_switch_disabled', ...extra});
      quota({targets: [target('worker'), target('router', 'f1', {target_role: 'router', target_role_label: 'Router'})]});
      controls.quotaSubmitted('f1');
    });
    assert.equal(await page.locator('.portalChatQuotaRow').count(), 2);
    assert.equal(await page.locator('.portalSessionRestartOverlay:visible').count(), 0, 'multiple blockers do not pick a member');
    assert.match(await page.locator('[data-quota-target="router"]').innerText(), /Router · router name/);
    if (process.env.PORTAL_FAMILY_UI_EVIDENCE) await page.screenshot({path: process.env.PORTAL_FAMILY_UI_EVIDENCE, fullPage: true});
    await page.locator('[data-quota-target="worker"] button').click();
    await page.locator('[data-session-target]').waitFor({state: 'visible'});
    assert.equal(await page.evaluate(() => optionTargets.at(-1)), 'worker');
    await page.locator('[data-session-target]').selectOption(JSON.stringify(['codex', 'outside-default']));
    await page.waitForFunction(() => !document.querySelector('[data-session-restart-submit]').disabled);
    await page.locator('[data-session-restart-submit]').click();
    await page.waitForFunction(() => switches.length === 2);
    assert.equal(await page.evaluate(() => switches.at(-1).resident_id), 'worker');
    await page.evaluate(() => {
      quota({targets: []}); controls.quotaSubmitted('f2');
      state.contract.request_progress = {input_states: [{message_id: 'f2', state: 'completed'}]};
      quota({targets: [target('steward', 'f2', {state: 'manual_required', reason: 'default_backends_unavailable'})]});
    });
    await page.locator('[data-session-target]').waitFor({state: 'visible'});
    assert.equal(await page.evaluate(() => optionTargets.at(-1)), 'steward', 'exhaustion opens unique target despite completed facade relay receipt');
    await page.locator('[data-session-restart-close]').last().click();
    await page.evaluate(() => controls.followContractResident());
    assert.equal(await page.locator('.portalSessionRestartOverlay:visible').count(), 0, 'same submission never reopens');
    await page.evaluate(() => {
      quota({targets: [target('worker', 'unrelated')]}); controls.quotaSubmitted('f3');
    });
    assert.equal(await page.locator('.portalSessionRestartOverlay:visible').count(), 0, 'unrelated request cannot auto-open');
    await page.evaluate(() => quota({targets: [target('worker', 'f3', {can_manage: false})]}));
    assert.equal(await page.locator('.portalChatQuotaRow button:visible').count(), 0, 'non-manager has no manual action');
    await page.evaluate(() => {canSwitch = false; quota({targets: [target('worker', 'unrelated')]});});
    await page.locator('.portalChatQuotaRow button').click();
    await page.waitForFunction(() => toasts.some(text => /权限/.test(text)));
    assert.equal(await page.locator('.portalSessionRestartOverlay:visible').count(), 0, 'actual target permission is rechecked by shared picker');
    await page.evaluate(() => {canSwitch = true; holdOptions = true;});
    await page.locator('.portalChatQuotaRow button').click();
    await page.waitForFunction(() => typeof releaseOptions === 'function');
    await page.evaluate(() => {quota({targets: []}); holdOptions = false; releaseOptions();});
    await page.waitForTimeout(50);
    assert.equal(await page.locator('.portalSessionRestartOverlay:visible').count(), 0, 'removed blocker cancels pending picker');
    await page.evaluate(() => {quota({targets: [target('worker', 'unrelated')]}); holdOptions = true; releaseOptions = null;});
    await page.locator('.portalChatQuotaRow button').click();
    await page.waitForFunction(() => typeof releaseOptions === 'function');
    await page.evaluate(() => {controls.releaseRuntimeTarget(); holdOptions = false; releaseOptions();});
    await page.waitForTimeout(50);
    assert.equal(await page.locator('.portalSessionRestartOverlay:visible').count(), 0, 'retargeting cancels pending target picker');
    assert.equal(await page.locator('.portalChatQuotaRow').count(), 0);
    await page.evaluate(() => {menuContext.node.family = {role: 'facade'}; return reloadMenu();});
    await page.waitForFunction(() => !document.querySelector('[data-menu-quota-switch]'));
    assert.match(await page.locator('#nodeMenuRuntime').innerText(), /Family 设置管理|Family settings/);
    await page.evaluate(() => {menuContext.node.family = {role: 'member'}; return reloadMenu();});
    await toggle.waitFor({state: 'visible'});
    await page.evaluate(() => {canEdit = false; PortalResidentRuntimeController.invalidate('fixture');});
    await page.waitForFunction(() => !document.querySelector('[data-menu-quota-switch]') || document.querySelector('[data-menu-quota-switch]').disabled);
    assert.deepEqual(errors, []);
    console.log('PASS Family exact targets/roles/multiple/read-only/permission/lifecycle/facade settings; quota Settings save/readback; off send opens existing all-account card; cancel/no duplicate; unknown/switching silent; exhausted exact copy; manual switch preserves context/no replay; permission disabled.');
  } finally {await browser.close();}
})().catch(error => {console.error(error); process.exitCode = 1;});
