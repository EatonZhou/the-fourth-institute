const assert = require('node:assert/strict');
const test = require('node:test');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const original = 'thinking_0123456789ab';

function harness() {
  const deleted = [], confirmations = [];
  let confirm;
  const state = {detailFileId: original, detailSelectedFileId: original,
    raw: {items: [{kb_id: original}]}, deletedKnowledgeIds: new Set(), indexPaging: {requestSeq: 0}, exactQuery: {seq: 0}};
  const context = vm.createContext({
    state, byId: () => ({title: 'Original', type: 'thinking'}), syncDetailButtons() {},
    window: {PortalI18n: {t: s => s}, PortalUI: {confirmAction: async (_action, title) => {
      confirmations.push(title);
      return new Promise(resolve => { confirm = resolve; });
    }}},
    knowledgeApi: {request: async (_url, options) => {
      const {kb_id} = JSON.parse(options.body);
      deleted.push(kb_id);
      return {deleted: true, kb_id};
    }},
    detailResourceGuest: {deleted: () => true},
    closeModal: () => { state.detailFileId = ''; }, renderAll() {},
    alertError: error => { throw error; },
  });
  vm.runInContext(fs.readFileSync(path.resolve(__dirname, '../../../management_portal/static/knowledge_board_detail.js'), 'utf8'), context);
  context.syncDetailButtons = () => {};
  context.closeModal = () => { state.detailFileId = ''; };
  return {context, state, deleted, confirmations, confirm: value => confirm(value)};
}

test('a delete confirmation cannot follow navigation to another resource or file', async () => {
  for (const change of [h => { h.state.detailFileId = h.state.detailSelectedFileId = 'thinking_fedcba987654'; },
    h => { h.state.detailSelectedFileId = original + '/notes.md'; }]) {
    const h = harness();
    const pending = h.context.deleteDetail();
    assert.equal(h.confirmations[0], 'Original');
    change(h);
    h.confirm(true);
    await pending;
    assert.deepEqual(h.deleted, []);
    assert.equal(h.state.deletedKnowledgeIds.size, 0);
  }
});

test('cancelled confirmation does not mutate; a fresh confirmed retry deletes the original once', async () => {
  const h = harness();
  const cancelled = h.context.deleteDetail();
  h.confirm(false);
  await cancelled;
  assert.deepEqual(h.deleted, []);
  const retry = h.context.deleteDetail();
  h.confirm(true);
  await retry;
  assert.deepEqual(h.deleted, [original]);
  assert(h.state.deletedKnowledgeIds.has(original));
  assert.equal(h.state.detailFileId, '');
});
