'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {test} = require('node:test');
const {babelParse, traverse} = require('./babel_parse.cjs');
const parts = {};
for (const file of ['portal_resident_service', 'lineage_workbench']) {
  const source = fs.readFileSync(path.resolve(__dirname, '../../../management_portal/static', file + '.js'), 'utf8');
  traverse(babelParse(source), {
    ObjectMethod(p) { if (p.node.key?.name === 'updateWatchboard') parts.service = source.slice(p.node.start, p.node.end); },
    FunctionDeclaration(p) { if (p.node.id?.name === 'updateCurrentResidentWatchboard') parts.ui = source.slice(p.node.start, p.node.end); },
  });
}
function fixture({refreshFails = false, code = 'instruction.conflict'} = {}) {
  const conflict = Object.assign(Error('revision changed'), {code, instruction_id: 'original-failed-write'});
  const lineage = {lineage_id: 'lineage', revision: 12};
  const calls = [], updates = [], notices = [], details = [];
  const state = {watchboardHiddenResidents: new Set()};
  let selection = 'resident';
  const context = vm.createContext({
    state, required: value => value, residentPath: value => value,
    keyed: () => ({idempotencyKey: 'original-key'}),
    get: async () => ({watchboard: {installation: {lineage_id: 'lineage', revision: 11, files: [],
      remove: [{op: 'delete', kind: 'component', id: 'original-component'}]}}}),
    execute: async (instruction, args, controls) => {
      calls.push({instruction, args, controls});
      if (instruction === 'ApplyLineageChanges') throw conflict;
      assert.equal(instruction, 'GetLineage');
      if (refreshFails) throw Error('read unavailable');
      return {lineage};
    },
    client: {watchboard: async () => { throw Error('must not report a committed update'); }},
    selectedResidentId: () => selection,
    lineageWorkbench: {getPanel: () => ({applyLineageMutation: async (...args) => updates.push(args)})},
    agentChatPanel: {applyDetail: detail => {state.detail = detail; details.push(detail);}},
    renderChatExtensionMenu() {}, writeLineageUiState() {}, closeChatExtensionMenu() {},
    watchboardController: {render() {}}, loadFolder: async () => {}, refreshResidentSidebarIfDue: async () => {},
    window: {
      PortalUI: {confirm: async () => true, toast: (...args) => notices.push(args)},
      PortalI18n: {t: value => value, text: (chunks, ...values) => String.raw({raw: chunks}, ...values)},
      PortalResidentProjection: {invalidate() {}, load: async () => ({resident_id: selection, installed: true})},
    },
  });
  vm.runInContext('var residentService = ({' + parts.service + '});' + parts.ui, context);
  return {context, conflict, lineage, calls, updates, notices, details, state, setSelection: id => {selection = id;}};
}
test('conflict preserves the original failure and only refreshes the definition', async () => {
  const f = fixture();
  await assert.rejects(f.context.residentService.updateWatchboard('remove', {resident_id: 'resident'}), error => {
    assert.equal(error, f.conflict);
    assert.equal(error.watchboardConflict.lineage, f.lineage);
    return true;
  });
  assert.deepEqual(f.calls.map(c => c.instruction), ['ApplyLineageChanges', 'GetLineage']);
  assert.equal(f.calls[0].controls.expectedVersion, 11);
  assert.equal(f.calls[0].args.operations[0].id, 'original-component');
});
test('UI redraws the fresh graph and Chat without replaying or moving objects', async () => {
  const f = fixture();
  await f.context.updateCurrentResidentWatchboard('remove', 'resident');
  assert.equal(f.calls.filter(c => c.instruction === 'ApplyLineageChanges').length, 1);
  assert.equal(f.updates.length, 1);
  assert.deepEqual(f.updates[0], [f.lineage], 'no separation option or layout mutation');
  assert.equal(f.details[0].installed, true);
  assert.match(f.notices[0][0], /未提交.*检查最新状态/);
  assert.equal(f.notices[0][1], 'error');
  assert.equal(f.state.watchboardMutationInFlight, false);
});
test('refresh failure never replaces the original conflict or claims fresh graph', async () => {
  const f = fixture({refreshFails: true});
  await f.context.updateCurrentResidentWatchboard('remove', 'resident');
  assert.equal(f.conflict.instruction_id, 'original-failed-write');
  assert.equal(f.conflict.watchboardConflict.refreshError.message, 'read unavailable');
  assert.equal(f.updates.length, 0);
  assert.match(f.notices[0][0], /最新状态读取失败/);
  assert.equal(f.calls.filter(c => c.instruction === 'ApplyLineageChanges').length, 1);
});
test('other errors are not classified as conflicts or retried', async () => {
  const f = fixture({code: 'permission.denied'});
  await f.context.updateCurrentResidentWatchboard('remove', 'resident');
  assert.deepEqual(f.calls.map(c => c.instruction), ['ApplyLineageChanges']);
  assert.equal(f.updates.length, 0);
  assert.equal(f.details.length, 0);
  assert.equal(f.conflict.watchboardConflict, undefined);
});
