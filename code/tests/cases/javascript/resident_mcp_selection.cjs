'use strict';
// Exercise the real capability form; isolate only the settings transport.
const assert = require('node:assert/strict');
const {test} = require('node:test');
const fs = require('node:fs');
const path = require('node:path');
const {JSDOM} = require('jsdom');
const root = path.resolve(__dirname, '../../../management_portal/static');
const plain = value => JSON.parse(JSON.stringify(value));
const flush = async () => {for (let i = 0; i < 8; i++) await new Promise(resolve => setTimeout(resolve, 0));};

async function form(t, mcp = []) {
  const dom = new JSDOM('<body></body>', {url: 'https://fixture.invalid', pretendToBeVisual: true, runScripts: 'outside-only'});
  const w = dom.window;
  w.CSS = {escape: s => s};
  w.matchMedia = () => ({matches: false, addEventListener() {}, removeEventListener() {}});
  for (const name of ['portal_text', 'portal_state', 'portal_translations', 'portal_i18n', 'portal_ui_primitives', 'portal_overlay', 'portal_ui', 'portal_model_api_settings', 'chat_workspace_runtime_settings']) {
    w.eval(fs.readFileSync(path.join(root, name + '.js'), 'utf8'));
  }
  const catalog = {models: ['m'], default_model: 'm', efforts_by_model: {}, service_tiers_by_model: {}, runtime_capabilities: {settings: ['model']}};
  const saves = [], errors = [], sessionChanges = [];
  w.PortalResidentService = {runtimeAccounts: async () => ({accounts: []}), runtimeCatalog: async () => catalog,
    observeSessionRestart: () => () => {}};
  w.PortalUI.toast = message => errors.push(String(message));
  const api = w.PortalChatWorkspaceRuntimeSettings.createRuntimeSettings({escapeHtml: w.PortalUIPrimitives.escapeHtml});
  const dialog = api.createSessionRestartDialog();
  t.after(async () => {dialog.destroy(); await flush(); dom.window.close();});
  dialog.open({resident_id: 'fixture', choose_runtime_settings: true,
    runtime_settings: {model: 'm', capability_config: {schema: 'tfi.resident-capabilities.v1', tools: [], mcp, skills: []}}, runtime_catalog: catalog,
    source: {provider: 'codex', account_id: 'two', has_session: true},
    providers: [{id: 'codex', label: 'Codex'}], accounts: {codex: [{id: 'two', account_id: 'codex_two', label: 'two'}]},
    onSave: async values => {saves.push(plain(values)); return {settings: values};}},
  async values => {sessionChanges.push(values); return {status: 'completed'};});
  await flush();
  const find = selector => w.document.querySelector(selector);
  find('[data-runtime-view="capabilities"]').click();
  return {find, saves, async save() {
    find('[data-capability-submit]').click(); await flush();
    assert.deepEqual(errors, []);
    assert.deepEqual(sessionChanges, [], 'capability save cannot initiate a session operation');
    return saves.at(-1)?.capability_config.mcp;
  }};
}

test('new MCP entry with blank tool names submits an explicit empty list', async t => {
  const ui = await form(t);
  ui.find('[data-capability-add="mcp"]').click();
  ui.find('[data-capability-ref="mcp"]').value = 'mcp_0123456789ab';
  assert.deepEqual(await ui.save(), [{ref: 'mcp_0123456789ab', enabled: true, required: false, tools: []}]);
  assert.equal(ui.find('[data-mcp-selection]'), null, 'no follow-all choice is offered');
});

for (const [label, extra] of [['omitted', {}], ['empty', {tools: []}]]) {
  test(`${label} stored MCP tools open as empty and stay empty on save`, async t => {
    const ui = await form(t, [{ref: 'mcp_0123456789ab', enabled: true, required: false, ...extra}]);
    const names = ui.find('[data-mcp-tools]');
    assert.equal(names.value, '');
    assert.equal(names.hidden, false, 'an omitted list must not hide an implicit all-tools mode');
    ui.find('[data-capability-required]').click();
    assert.deepEqual(await ui.save(), [{ref: 'mcp_0123456789ab', enabled: true, required: true, tools: []}]);
  });
}

test('MCP saves only named tools and clearing names deselects every tool', async t => {
  const ui = await form(t, [{ref: 'mcp_0123456789ab', enabled: true, required: false, tools: ['read', 'search']}]);
  assert.equal(ui.find('[data-mcp-tools]').value, 'read, search');
  ui.find('[data-mcp-tools]').value = ' read , lookup ';
  assert.deepEqual((await ui.save())[0].tools, ['read', 'lookup']);
  ui.find('[data-mcp-tools]').value = '';
  assert.deepEqual((await ui.save())[0].tools, []);
});
