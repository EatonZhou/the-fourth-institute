const assert = require('node:assert/strict');
const test = require('node:test');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.resolve(__dirname, '../../../management_portal/static/knowledge_board.js'), 'utf8');
const start = source.indexOf('async function init() {');
const end = source.lastIndexOf('\ninit();');
assert.ok(start >= 0 && end > start);
function harness(item = '') {
  const events = [];
  let finishIndex;
  const index = new Promise(resolve => { finishIndex = resolve; });
  const context = vm.createContext({
    window: {PortalUI: {loadCurrentUser: async () => ({username: 'viewer'})}},
    document: {body: {classList: {remove: () => events.push('visible')}}},
    initDetailDocumentSession() {}, initDetailResourceGuest() {},
    portalUsername: '', semanticSearchReady: false,
    detailChatWorkspace: null, archiveChatWorkspace: null,
    applyKnowledgeUiState() {}, initialKnowledgeItemId: () => item,
    detailResourceGuest: {embedded: () => false},
    requestedSemanticSearchId: () => '', requestedKnowledgeMatrixFilter: () => ({}),
    state: {query: 'design'}, filter: {}, el: {q: {}}, syncKnowledgeView() {},
    initSemanticSearch: () => ({load: async () => {}}),
    loadIndex: () => index,
    detailHistory: {restoreReturnPosition: () => events.push('restore')},
    openDetail: async id => events.push('open:' + id), alertError: e => { throw e; },
  });
  vm.runInContext(source.slice(start, end), context);
  return {context, events, finishIndex};
}
test('cross-document collection return restores saved position only after list layout is ready', async () => {
  const h = harness();
  const pending = h.context.init();
  await new Promise(setImmediate);
  assert.deepEqual(h.events, []);
  h.finishIndex();
  await pending;
  assert.deepEqual(h.events, ['visible', 'restore']);
});
test('direct detail entry opens its resource without restoring collection position', async () => {
  const h = harness('decision_123456789abc');
  const pending = h.context.init();
  h.finishIndex();
  await pending;
  assert.deepEqual(h.events, ['visible', 'open:decision_123456789abc']);
});
