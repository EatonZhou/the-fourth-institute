'use strict';
const assert = require('node:assert/strict'), fs = require('node:fs'), vm = require('node:vm'), path = require('node:path');
const root = path.resolve(__dirname, '../../../management_portal/static');
const {restartParameters} = JSON.parse(fs.readFileSync(0, 'utf8'));
const events = {}, window = {addEventListener: (name, fn) => {events[name] = fn;}, PortalI18n: {t: x => x}, PortalUIPrimitives: {randomUUID: () => 'request-key'}};
vm.runInNewContext(fs.readFileSync(path.join(root, 'portal_resident_service.js'), 'utf8'), {window, URLSearchParams, setTimeout, clearTimeout});
const mutations = [];
const service = window.PortalResidentService.createClient({api: {get: async () => ({})}, execute: async (name, args, controls) => {
  if (name === 'RestartSession') {
    for (const [key, value] of Object.entries(args)) {
      const parameter = restartParameters.find(p => p.name === key);
      assert.ok(parameter, `RestartSession does not declare ${key}`);
      if (parameter.enum) assert.ok(parameter.enum.includes(value), `RestartSession rejects ${key}=${value}`);
    }
  }
  mutations.push(JSON.parse(JSON.stringify({name, args, controls}))); return {status: 'completed', restart_id: 'op1'};
}});
(async () => {
  await service.updateRuntimeSettings('r1', {model: 'm2'}, 'save-key', 'sha256:old');
  assert.deepEqual(mutations[0], {name: 'UpdateResidentSettings', args: {resident_id: 'r1', changes: {model: 'm2'}, expected_revision: 'sha256:old'}, controls: {idempotencyKey: 'save-key'}});
  await service.restartSession({resident_id: 'r1', operation: 'switch_account', target_provider: 'codex', target_account: 'three', context_strategy: 'preserve', active_execution_policy: 'wait', resume_failed_input: false, idempotency_key: 'switch-key', credits_consent: {account_id: 'codex_three', confirmed: true}});
  assert.deepEqual(mutations[1], {name: 'RestartSession', args: {resident_id: 'r1', provider: 'codex', account_id: 'three'}, controls: {idempotencyKey: 'switch-key'}});
  await service.restartSession({resident_id: 'r1', target_provider: 'codex', account_selection: 'auto',
    model: 'codex-model', idempotency_key: 'auto-key'});
  assert.equal(mutations[2].args.account_selection, 'auto');
  assert.equal(mutations[2].args.model, 'codex-model');
  assert.equal(Object.hasOwn(mutations[2].args, 'account_id'), false, 'auto selection must not pin the catalog preview account');
  assert.equal(mutations[2].controls.idempotencyKey, 'auto-key');
  vm.runInNewContext(fs.readFileSync(path.join(root, 'portal_resident_runtime_controller.js'), 'utf8'), {window});
  let revision = 'r1', config, writes = [], settings = {model: 'm'};
  const controller = window.PortalResidentRuntimeController.create({service: {
    projection: async () => ({resident: {backend: 'codex'}, execution_account: {account_id: 'two'}, allows: {RestartSession: true, UpdateResidentSettings: true}, chat_workspace: {actions: {runtime_settings: {}}}}),
    runtimeSettings: async () => ({settings, runtime_settings_access: 'available', runtime_settings_resident_id: 'r1', runtime_settings_revision: revision, application: {items: [{status: 'unknown'}]}}),
    runtimeCatalog: async () => ({models: ['m', 'm2']}), observeSessionRestart: () => () => {}, sessionRestartOptions: async () => ({source: {provider: 'codex', account_id: 'two'}}),
    updateRuntimeSettings: async (_id, changes, key, expected) => {
      writes.push({changes, key, expected}); if (expected !== revision) throw Object.assign(new Error('conflict'), {status: 409});
      settings = {...settings, ...changes}; revision = 'r3'; return {settings, runtime_settings_access: 'available', runtime_settings_resident_id: 'r1', runtime_settings_revision: revision};
    },
  }, dialogs: {restart: () => ({open: value => {config = value;}, close() {}, destroy() {}})}});
  const handle = controller.bind('r1'); await handle.openSettings();
  assert.equal(config.initial_view, 'parameters'); assert.equal(config.application.items[0].status, 'unknown');
  revision = 'r2'; events['portal:change']({detail: {resident_changes: {r1: ['runtime']}}}); await handle.load();
  await assert.rejects(config.onSave({model: 'm2'}), /conflict/);
  assert.equal(writes[0].expected, 'r1', 'a background refresh must not authorize overwriting newer settings with an old draft');
  handle.close(); await handle.openSettings(); await config.onSave({model: 'm2'});
  assert.equal(writes[1].expected, 'r2');
  await config.onSave({capability_config: {schema: 'tfi.resident-capabilities.v1', tools: [], mcp: [], skills: []}});
  assert.equal(writes[2].expected, 'r3', 'successful saves advance the edit revision');
  handle.destroy(); console.log('PASS real service ISA operation/CAS payload; stale-draft conflict; successful-save revision');
})().catch(error => {console.error(error); process.exitCode = 1;});
