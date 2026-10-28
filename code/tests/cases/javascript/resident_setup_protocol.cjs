'use strict';
// Real runtime dialog with isolated provider reads and mutation boundary.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {JSDOM} = require('jsdom');
const root = path.resolve(__dirname, '../../../management_portal/static');
const dom = new JSDOM('<body></body>', {url: 'https://fixture.invalid', pretendToBeVisual: true, runScripts: 'outside-only'});
const w = dom.window;
w.CSS = {escape: s => s};
w.matchMedia = () => ({matches: false, addEventListener() {}, removeEventListener() {}});
for (const name of ['portal_text', 'portal_state', 'portal_translations', 'portal_i18n', 'portal_ui_primitives', 'portal_overlay', 'portal_ui', 'portal_model_api_settings', 'chat_workspace_runtime_settings']) {
  w.eval(fs.readFileSync(path.join(root, name + '.js'), 'utf8'));
}
const catalog = {models: ['m', 'm2'], default_model: 'm', efforts_by_model: {}, service_tiers_by_model: {}, runtime_capabilities: {settings: ['model', 'permissions'], permission_presets: ['full-access']}};
const changes = [], saves = [], errors = [];
let observe;
w.PortalResidentService = {runtimeAccounts: async () => ({accounts: []}), runtimeCatalog: async () => catalog,
  observeSessionRestart: (_id, fn) => {observe = fn; return () => {};}, refreshSessionRestart: async () => ({}),
  retrySessionRestart: async () => ({status: 'accepted', restart_id: 'same'})};
w.PortalUI.toast = message => errors.push(String(message));
const api = w.PortalChatWorkspaceRuntimeSettings.createRuntimeSettings({escapeHtml: w.PortalUIPrimitives.escapeHtml});
const contextOptions = enabled => [
  {id: 'preserve', label: '完整保留', enabled, description: enabled ? '不压缩上下文' : '这两个 Provider 无法完整保留上下文'},
  {id: 'compact', label: '精简交接', enabled: true, description: '可能遗漏细节和工具过程'},
  {id: 'visible', label: '仅可见对话', enabled: true, description: '不含完整工具过程'},
  {id: 'fresh', label: '空白开始', enabled: true, description: '不携带当前上下文'},
];
const config = {resident_id: 'fixture', choose_runtime_settings: true, runtime_settings: {model: 'm'}, runtime_catalog: catalog,
  source: {provider: 'codex', account_id: 'two', has_session: true},
  providers: [{id: 'codex', label: 'Codex'}, {id: 'claude', label: 'Claude'}],
  accounts: {codex: [{id: 'two', account_id: 'codex_two', label: 'two', context_options: contextOptions(true)}, {id: 'three', account_id: 'codex_three', label: 'three', context_options: contextOptions(true)}], claude: [{id: 'one', account_id: 'claude_one', label: 'one', context_options: contextOptions(false)}]},
  onSave: async values => {saves.push(values); return {settings: values};}};
const flush = async () => {for (let i = 0; i < 8; i++) await new Promise(resolve => setTimeout(resolve, 0));};
const click = selector => w.document.querySelector(selector).click();
const select = (selector, value) => {const el = w.document.querySelector(selector); el.value = value; el.dispatchEvent(new w.Event('change', {bubbles: true}));};
const plain = value => JSON.parse(JSON.stringify(value));
(async () => {
  const dialog = api.createSessionRestartDialog();
  dialog.open(config, async values => {changes.push(plain(values)); return {status: 'accepted', restart_id: 'op1'};}, async () => ({settings: {model: 'm'}}));
  await flush();
  const submit = w.document.querySelector('[data-session-restart-submit]');
  assert.equal(submit.disabled, true, 'same account must not initiate a restart');
  assert.equal(w.document.querySelector('[data-session-restart="resume-failed"]'), null, 'switch must not retry a different failed input');
  click('[data-runtime-view="parameters"]');
  select('[data-runtime-panel="parameters"] [data-runtime-setting="model"]', 'm2');
  click('[data-session-reload]'); await flush();
  assert.equal(w.document.querySelector('[data-runtime-panel="parameters"] [data-runtime-setting="model"]').value, 'm2', 'refresh must keep the draft');
  click('[data-runtime-settings-submit]'); await flush();
  assert.equal(saves.length, 1); assert.equal(saves[0].model, 'm2'); assert.equal(changes.length, 0);
  click('[data-runtime-view="switch_account"]');
  select('[data-session-target]', JSON.stringify(['codex', 'three'])); await flush();
  click('[data-session-restart-submit]'); await flush();
  assert.equal(changes.length, 1);
  assert.equal(changes[0].operation, 'switch_account'); assert.equal(changes[0].context_strategy, 'preserve');
  assert.equal(changes[0].active_execution_policy, 'wait'); assert.equal(changes[0].resume_failed_input, false);
  assert.equal(changes[0].target_account, 'three'); assert.equal(changes[0].model, undefined, 'same-provider switch must not overwrite model with a default');
  observe({restart: {restart_id: 'op1', status: 'completed', operation: 'switch_account'}}); await flush();
  dialog.destroy();
  const capabilities = api.createSessionRestartDialog();
  capabilities.open({...config, application: {items: [{path: 'capability_config', status: 'unknown', delivery: 'unknown'}]}}, async () => {throw new Error('saving capabilities cannot start a session');}); await flush();
  click('[data-runtime-view="capabilities"]');
  assert.match(w.document.querySelector('[data-capability-application]').textContent, /当前观察：未知.*交付：未知/);
  click('[data-capability-add="tools"]');
  w.document.querySelector('[data-capability-ref="tools"]').value = 'portal:hatg_instruction';
  select('.portalCapabilityRow select', 'disabled');
  click('[data-capability-submit]'); await flush();
  assert.deepEqual(plain(saves[1]), {capability_config: {schema: 'tfi.resident-capabilities.v1', tools: [{ref: 'portal:hatg_instruction', enabled: false, required: false}], mcp: [], skills: []}});
  assert.equal(changes.length, 1);
  capabilities.destroy();
  const again = api.createSessionRestartDialog(); again.open(config, async values => {changes.push(plain(values)); return {status: 'completed'};}); await flush();
  click('[data-runtime-view="restart"]');
  assert.equal(w.document.querySelector('[data-session-restart="strategy"]').value, 'preserve');
  select('[data-session-restart="strategy"]', 'fresh');
  click('[data-session-restart-submit]'); await flush();
  assert.equal(changes[1].operation, 'restart'); assert.equal(changes[1].context_strategy, 'fresh'); assert.equal(changes[1].target_account, 'two');
  again.destroy();
  const cancellations = [];
  const pending = api.createSessionRestartDialog();
  pending.open({...config, source: {...config.source, account_id: 'codex_two'}}, async values => {changes.push(plain(values)); return {status: 'accepted', restart_id: 'cancel-me'};}, null,
    async values => {cancellations.push(plain(values)); return {status: 'cancelling', restart_id: values.restart_id};}); await flush();
  assert.equal(w.document.querySelector('[data-session-restart-submit]').disabled, true, 'canonical account and display alias are the same account');
  select('[data-session-target]', JSON.stringify(['codex', 'three'])); await flush();
  click('[data-session-restart-submit]'); click('[data-session-restart-submit]'); await flush();
  assert.equal(changes.length, 3, 'double submit admits only one request');
  click('[data-session-restart-cancel]'); await flush();
  assert.deepEqual(cancellations, [{restart_id: 'cancel-me'}]);
  observe({restart: {status: 'cancelled', restart_id: 'cancel-me'}}); await flush(); pending.destroy();
  const retry = api.createSessionRestartDialog();
  retry.open({...config, restart: {status: 'confirming', operation: 'switch_account'}}, async () => {throw new Error('must use the existing pending request');}); await flush();
  click('[data-session-restart-submit]'); await flush();
  assert.equal(w.document.querySelector('[data-session-restart-submit]').disabled, true, 'retry follows the existing receipt and returns to tracking');
  retry.destroy();
  assert.deepEqual(errors, []);
  const cross = api.createSessionRestartDialog();
  cross.open(config, async values => {changes.push(plain(values)); return {status: 'completed'};}); await flush();
  select('[data-session-target]', JSON.stringify(['claude', 'one'])); await flush();
  assert.equal(w.document.querySelector('[data-switch-context]'), null, 'handoff does not require choosing an implementation strategy');
  assert.match(w.document.querySelector('[data-switch-context-status]').textContent, /无法完整保留/);
  assert.equal(changes.length, 3, 'selecting an account does not submit');
  const defaultModel = w.document.querySelector('[data-target-editor-host] [data-runtime-setting="model"]');
  assert.equal(defaultModel.selectedOptions[0].textContent, 'm (default)');
  assert.equal(defaultModel.value, '', 'the default option uses an inheritance value in the editor');
  click('[data-session-reload]'); await flush();
  assert.match(w.document.querySelector('[data-switch-context-description]').textContent, /遗漏细节/);
  assert.equal(w.document.querySelector('[data-session-restart-submit]').textContent, '交接并切换');
  click('[data-session-restart-submit]'); await flush();
  assert.equal(changes.length, 4, 'the displayed catalog default is a valid choice for a cross-provider switch');
  assert.equal(changes[3].model, 'm'); assert.equal(changes[3].target_provider, 'claude'); assert.equal(changes[3].context_strategy, 'compact');
  assert.equal(changes[3].operation, 'restart');
  cross.destroy();
  const handoff = api.createSessionRestartDialog();
  handoff.open(config, async values => {changes.push(plain(values)); return {status: 'accepted', restart_id: 'compact'};}); await flush();
  select('[data-session-target]', JSON.stringify(['claude', 'one'])); await flush();
  select('[data-session-target]', JSON.stringify(['codex', 'three'])); await flush();
  assert.equal(w.document.querySelector('[data-session-restart-submit]').textContent, '切换账号');
  assert.equal(w.document.querySelector('[data-switch-context-description]').textContent, '', 'preservation must not inherit the summary warning');
  select('[data-session-target]', JSON.stringify(['claude', 'one'])); await flush();
  select('[data-target-editor-host] [data-runtime-setting="model"]', 'm2');
  click('[data-session-restart-submit]'); await flush();
  assert.equal(changes.at(-1).model, 'm2', 'an explicit target model still overrides the catalog default');
  observe({restart: {status: 'accepted', restart_id: 'compact', operation: 'restart', context_strategy: 'compact', target: {provider: 'claude', account_id: 'claude_one'}}}); await flush();
  assert.equal(w.document.querySelector('[data-runtime-panel="switch_account"]').hidden, false);
  assert.equal(w.document.querySelector('[data-session-target]').disabled, true);
  handoff.destroy();
  const beforeCancel = changes.length;
  const cancelChoice = api.createSessionRestartDialog();
  cancelChoice.open(config, async values => {changes.push(values);}); await flush();
  select('[data-session-target]', JSON.stringify(['claude', 'one'])); await flush();
  click('[data-session-restart-close]'); await flush();
  assert.equal(changes.length, beforeCancel, 'closing the dialog never changes the session');
  cancelChoice.destroy();
  for (const available of [[], [{id: 'fresh', enabled: true}], [{id: 'visible', enabled: true}, {id: 'fresh', enabled: true}]]) {
    const unsupported = api.createSessionRestartDialog();
    unsupported.open({...config, accounts: {...config.accounts, claude: [{id: 'one', context_options: available}]}}, async values => {changes.push(values);}); await flush();
    select('[data-session-target]', JSON.stringify(['claude', 'one'])); await flush();
    assert.equal(w.document.querySelector('[data-session-restart-submit]').disabled, true, 'never automatically fall back to visible-only or blank context');
    click('[data-session-restart-submit]'); await flush();
    assert.equal(changes.length, beforeCancel);
    unsupported.destroy();
  }
  assert.deepEqual(errors, []);
  console.log('PASS preferred preservation, disclosed summary handoff, no blank fallback, cancellation, progress and settings drafts');
  const defaults = api.createSessionRestartDialog();
  defaults.open(config, async () => {throw new Error('parameter edits must not switch the session');}); await flush();
  click('[data-runtime-view="parameters"]');
  select('[data-runtime-panel="parameters"] [data-runtime-setting="model"]', '');
  const beforeDefaultSave = saves.length;
  click('[data-runtime-settings-submit]'); await flush();
  assert.equal(saves.length, beforeDefaultSave + 1);
  assert.equal(saves.at(-1).model, '', 'ordinary parameter edits must retain follow-default semantics');
  defaults.destroy();
  assert.deepEqual(errors, []);
  const beforeInvalidDefault = changes.length;
  for (const invalidDefault of ['', 'unavailable-model']) {
    catalog.default_model = invalidDefault;
    const invalid = api.createSessionRestartDialog();
    try {
      invalid.open(config, async values => {changes.push(values);}); await flush();
      select('[data-session-target]', JSON.stringify(['claude', 'one'])); await flush();
      click('[data-session-restart-submit]'); await flush();
      assert.equal(changes.length, beforeInvalidDefault, 'an absent or unavailable default must not start a switch');
      assert.match(errors.pop(), /默认模型未出现在此账号的 Provider 能力列表/);
    } finally {
      invalid.destroy();
      catalog.default_model = 'm';
    }
  }
  assert.deepEqual(errors, []);
  console.log('PASS target default resolution, explicit model override, default inheritance and invalid catalog rejection');
})().catch(error => {console.error(error); process.exitCode = 1;}).finally(async () => {await flush(); dom.window.close();});
