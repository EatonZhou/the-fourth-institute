'use strict';
const assert = require('node:assert/strict'), fs = require('node:fs'), path = require('node:path');
const {JSDOM} = require('jsdom');
const w = new JSDOM('<body></body>', {url: 'https://fixture.invalid', runScripts: 'outside-only'}).window;
const root = path.resolve(__dirname, '../../../management_portal/static');
w.CSS = {escape: value => value};
w.matchMedia = () => ({matches: false, addEventListener() {}, removeEventListener() {}});
for (const name of ['portal_text', 'portal_state', 'portal_translations', 'portal_i18n', 'portal_ui_primitives', 'portal_overlay', 'portal_ui', 'portal_model_api_settings', 'chat_workspace_runtime_settings']) w.eval(fs.readFileSync(path.join(root, name + '.js'), 'utf8'));
const api = w.PortalChatWorkspaceRuntimeSettings.createRuntimeSettings({escapeHtml: w.PortalUIPrimitives.escapeHtml});
const flush = async () => {for (let i = 0; i < 8; i++) await new Promise(resolve => setTimeout(resolve, 0));};
const pending = {status: 'completed', phase: 'completed', operation: 'restart', restart_id: 'pending', configuration_only: true,
  context_result: 'pending_first_input', model_operation: false, context_strategy: 'compact',
  source: {provider: 'codex', account_id: 'codex_two'}, target: {provider: 'claude', account_id: 'claude_one'}};
const toasts = [];
w.PortalUI.toast = (message, tone) => toasts.push([message, tone]);
(async () => {
  for (const compact of [false, true]) {
    const progress = api.createSessionRestartProgress({compact, headerIcon: compact});
    w.document.body.append(progress.element);
    for (const strategy of ['compact', 'visible']) {
      progress.update({restart: {...pending, context_strategy: strategy}});
      assert.equal(progress.element.hidden, false, 'configuration completion must expose pending context');
      assert.match(progress.element.textContent, /账号配置已保存/);
      assert.match(progress.element.querySelector('[role="status"]').textContent, /codex_two → claude_one/);
      const description = compact ? progress.element.querySelector('button').title : progress.element.textContent;
      assert.match(description, /首条真实输入/); assert.match(description, /额度/);
      assert.equal(api.sessionRestartActive(pending), false, 'waiting for input is not an active migration');
    }
    for (const result of ['delivered', 'preserved']) {
      progress.update({restart: {...pending, context_result: result}});
      assert.equal(progress.element.hidden, true, 'only pending handoff keeps the completed receipt visible');
    }
    progress.update({restart: {...pending, status: 'failed', error: 'denied', previous_session_retained: true}});
    assert.match(compact ? progress.element.querySelector('button').title : progress.element.textContent,
      /原会话已保留|original session is kept/);
    progress.destroy();
  }
  const catalog = {models: ['m'], default_model: 'm', efforts_by_model: {}, service_tiers_by_model: {}, runtime_capabilities: {settings: ['model']}};
  let observe, requests = 0, closed = 0;
  w.PortalResidentService = {runtimeAccounts: async () => ({accounts: []}), runtimeCatalog: async () => catalog,
    observeSessionRestart: (_id, callback) => {observe = callback; return () => {};}};
  const config = {resident_id: 'fixture', runtime_settings: {model: 'm'}, runtime_catalog: catalog,
    source: pending.source, providers: [{id: 'codex'}, {id: 'claude'}], accounts: {
      codex: [{id: 'codex_two', context_options: [{id: 'preserve', enabled: true}]}],
      claude: [{id: 'claude_one', context_options: [{id: 'compact', enabled: true, description: '新会话成功后才切换，可能遗漏细节'}]}]}};
  const panel = api.createRuntimePanel({onDone: () => closed++});
  w.document.body.append(panel.element);
  for (const deferred of [false, true]) {
    panel.open(config, async values => {
      requests++; assert.equal(values.context_strategy, 'compact');
      assert.equal(values.active_execution_policy, 'wait'); assert.equal(values.resume_failed_input, false);
      assert.ok(values.idempotency_key);
      return deferred ? {...pending, status: 'accepted'} : pending;
    });
    await flush();
    const backend = panel.element.querySelector('[data-session-backend]');
    backend.value = 'claude'; backend.dispatchEvent(new w.Event('change', {bubbles: true})); await flush();
    assert.match(panel.element.querySelector('[data-switch-context-status]').textContent, /首条真实输入/);
    assert.match(panel.element.querySelector('[data-runtime-save-hint]').textContent, /额度/);
    assert.doesNotMatch(panel.element.querySelector('[data-switch-context-description]').textContent, /新会话成功后才切换/,
      'manual configuration must not repeat the old immediate model handoff promise');
    const submit = panel.element.querySelector('[data-session-restart-submit]');
    assert.equal(submit.textContent, '保存账号配置');
    submit.click(); submit.click(); await flush();
    if (deferred) {observe({restart: pending}); await flush(); observe({restart: pending}); await flush();}
    assert.equal(requests, deferred ? 2 : 1, 'double submit admits one write');
    assert.equal(closed, deferred ? 2 : 1, 'repeated receipt closes only once');
    assert.equal(toasts.length, deferred ? 2 : 1, 'one completion notice per submission');
    assert.match(toasts.at(-1)[0], /账号配置已保存.*首条真实输入/);
  }
  panel.destroy();
  let resolve;
  const beforeDestroy = toasts.length;
  const destroyed = api.createRuntimePanel({onDone: () => {throw new Error('destroyed panel completed');}});
  w.document.body.append(destroyed.element);
  destroyed.open(config, () => new Promise(done => {resolve = done;})); await flush();
  const backend = destroyed.element.querySelector('[data-session-backend]');
  backend.value = 'claude'; backend.dispatchEvent(new w.Event('change', {bubbles: true})); await flush();
  destroyed.element.querySelector('[data-session-restart-submit]').click(); await flush();
  destroyed.destroy(); resolve(pending); await flush();
  assert.equal(toasts.length, beforeDestroy, 'a destroyed renderer must not emit a completion notice');
  console.log('PASS pending context, preservation, failures, immediate/deferred receipts and duplicate guards');
})().catch(error => {console.error(error); process.exitCode = 1;}).finally(() => w.close());
