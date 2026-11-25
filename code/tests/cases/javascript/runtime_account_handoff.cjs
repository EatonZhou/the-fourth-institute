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
    for (const strategy of ['preserve', 'native', 'local']) {
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
      codex: [{id: 'codex_two', context_options: [{id: 'preserve', label: '完整上下文', enabled: true}]}],
      claude: [{id: 'claude_one', context_options: [{id: 'preserve', enabled: true, description: '继承完整有效上下文。'}]}]}};
  for (const inline of [false, true]) {
    const selection = api.createRuntimePanel({inline});
    w.document.body.append(selection.element);
    selection.open({...config, accounts: {...config.accounts, claude: [
      {id: 'claude_one', context_options: [
        {id: 'preserve', label: '完整上下文', enabled: true, description: '首条真实输入继承完整上下文'}]},
      {id: 'claude_two', context_options: []},
      {id: 'claude_three', context_options: [{id: 'preserve', label: '完整上下文', enabled: false}]}
    ]}}, () => {throw new Error('an incomplete selection must not submit');});
    await flush();
    const backend = selection.element.querySelector('[data-session-backend]');
    const account = selection.element.querySelector('[data-session-target]');
    const memory = selection.element.querySelector('[data-memory-method]');
    const status = selection.element.querySelector('[data-switch-context-status]');
    const submit = selection.element.querySelector('[data-session-restart-submit]');
    const change = async (element, value) => {
      element.value = value; element.dispatchEvent(new w.Event('change', {bubbles: true})); await flush();
    };
    const description = selection.element.querySelector('[data-switch-context-description]');
    // The only migration method needs no field or explanation, so the card
    // keeps one size before and after an account is chosen.
    const quiet = () => {
      assert.equal(memory.closest('label').hidden, true, 'a single handoff method is not shown as a choice');
      assert.equal(status.hidden, true); assert.equal(status.textContent, '');
      assert.equal(description.hidden, true); assert.equal(description.textContent, '');
    };
    await change(backend, 'claude');
    assert.equal(account.value, '');
    quiet();
    assert.equal(submit.disabled, true); submit.click();
    await change(account, JSON.stringify(['claude', 'claude_one']));
    quiet();
    assert.equal(memory.value, 'preserve', 'account migration uses full Fork context');
    assert.equal(submit.disabled, false);
    await change(account, '');
    quiet();
    for (const id of ['claude_two', 'claude_three']) {
      await change(account, JSON.stringify(['claude', id]));
      assert.equal(memory.closest('label').hidden, true, 'missing or unsupported capabilities must not show a blank control');
      assert.match(status.textContent, /交接能力暂不可用/);
      assert.equal(submit.disabled, true); submit.click();
    }
    await change(backend, 'codex');
    assert.equal(memory.closest('label').hidden, true);
    assert.equal(status.textContent, '', 'returning to the current account clears the handoff warning');
    selection.destroy();
  }
  for (const inline of [false, true]) {
    const parameterWrites = [], restartWrites = [];
    const parameters = api.createRuntimePanel({inline});
    w.document.body.append(parameters.element);
    parameters.open({...config, runtime_settings: {model: 'm', reasoning_effort: 'low'},
      runtime_catalog: {...catalog, models: ['m', 'm2'], efforts_by_model: {m: ['low', 'high'], m2: ['low', 'high']},
        runtime_capabilities: {settings: ['model', 'reasoning_effort']}},
      onSave: async values => {parameterWrites.push(values); return {settings: values};}}, async values => {restartWrites.push(values);});
    await flush();
    const memory = parameters.element.querySelector('[data-memory-method]');
    assert.equal(memory.closest('label').hidden, true);
    for (const [field, value] of [['model', 'm2'], ['reasoning_effort', 'high']]) {
      const input = parameters.element.querySelector(`[data-runtime-editor="current"] [data-runtime-setting="${field}"]`);
      input.value = value; input.dispatchEvent(new w.Event('change', {bubbles: true})); await flush();
    }
    assert.equal(memory.closest('label').hidden, true, 'parameter changes never ask for memory transfer');
    const submit = parameters.element.querySelector('[data-session-restart-submit]');
    assert.equal(submit.disabled, false); submit.click(); await flush();
    assert.equal(parameterWrites.length, 1);
    assert.equal(parameterWrites[0].model, 'm2');
    assert.equal(parameterWrites[0].reasoning_effort, 'high');
    assert.equal(restartWrites.length, 0, 'parameter saves keep the current session');
    assert.equal(parameterWrites[0].context_strategy, undefined);
    parameters.destroy();
  }
  toasts.length = 0;
  const panel = api.createRuntimePanel({onDone: () => closed++});
  w.document.body.append(panel.element);
  for (const deferred of [false, true]) {
    panel.open(config, async values => {
      requests++; assert.equal(values.context_strategy, 'preserve');
      assert.equal(values.active_execution_policy, 'wait'); assert.equal(Object.hasOwn(values, 'resume_failed_input'), false);
      assert.ok(values.idempotency_key);
      return deferred ? {...pending, status: 'accepted'} : pending;
    });
    await flush();
    const backend = panel.element.querySelector('[data-session-backend]');
    backend.value = 'claude'; backend.dispatchEvent(new w.Event('change', {bubbles: true})); await flush();
    assert.equal(panel.element.querySelector('[data-memory-method]').closest('label').hidden, true);
    assert.match(panel.element.querySelector('[data-runtime-save-hint]').textContent, /额度/);
    assert.doesNotMatch(panel.element.querySelector('[data-switch-context-description]').textContent, /新会话成功后才切换/,
      'manual configuration must not repeat the old immediate model handoff promise');
    const submit = panel.element.querySelector('[data-session-restart-submit]');
    assert.match(submit.textContent, /切换账号|Switch account/);
    submit.click(); submit.click(); await flush();
    if (deferred) {observe({restart: pending}); await flush(); observe({restart: pending}); await flush();}
    assert.equal(requests, deferred ? 2 : 1, 'double submit admits one write: ' + JSON.stringify(toasts));
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
