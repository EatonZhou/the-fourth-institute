const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const html = fs.readFileSync(path.resolve(__dirname, '../../../knowledge_board_lan/knowledge_board.html'), 'utf8');
const start = html.indexOf('      async function openDetail(fileId, options = {}) {');
const end = html.indexOf('      async function applyDetailHistoryState()', start);
assert(start >= 0 && end > start);

(async () => {
  const navigations = [];
  const knowledgePreview = new Error('normal knowledge preview');
  const context = vm.createContext({
    state: {},
    byId: id => ({kb_id: id, resource_type: id.startsWith('search_') ? 'search' : 'decision'}),
    detailDocumentSession: null,
    detailResourceGuest: {embedded: () => false},
    detailHistory: {open: () => {throw knowledgePreview;}},
    window: {location: {assign: url => navigations.push(url)}},
  });
  vm.runInContext(html.slice(start, end), context);
  assert.equal(await context.openDetail('search_8176d8f8cf7a'), true);
  assert.deepEqual(navigations, ['/r/search_8176d8f8cf7a']);
  await assert.rejects(context.openDetail('decision_f2a6a5dd4646'), error => error === knowledgePreview);
  context.detailDocumentSession = {guardDiscard: async () => false};
  assert.equal(await context.openDetail('search_8176d8f8cf7a'), false);
  assert.equal(navigations.length, 1, 'unsaved draft guard must precede navigation');
  console.log('Search uses canonical resource navigation; documents keep preview and draft protection.');
})().catch(error => {console.error(error); process.exitCode = 1;});
