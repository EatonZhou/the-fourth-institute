const assert = require('node:assert/strict');
const test = require('node:test');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { JSDOM } = require('jsdom');

const root = path.resolve(__dirname, '../../..');
const source = name => fs.readFileSync(path.join(root, 'management_portal/static', name), 'utf8');
const extract = (text, start, end) => {
  const from = text.indexOf(start);
  const to = text.indexOf(end, from);
  assert.ok(from >= 0 && to > from);
  return text.slice(from, to);
};
function harness() {
  const dom = new JSDOM(fs.readFileSync(path.join(root, 'knowledge_board_lan/knowledge_board.html'), 'utf8'));
  const { window } = dom;
  window.PortalI18n = { t: value => value, text: (parts, ...values) => String.raw({raw: parts}, ...values) };
  const navigations = [];
  const context = vm.createContext({
    window, document: window.document,
    state: {}, el: Object.fromEntries(['mFavorite', 'mResourceIdentity', 'modal'].map(id => [id, window.document.getElementById(id)])),
    knowledgeBoardShell: { formatDateTime: () => '2026-11-08' },
    openDetail: id => navigations.push(id),
    disposeDetailPreview() {}, resetDetailGraph() {}, resetDetailChat() {},
    detailDocumentSession: null, detailResourceGuest: null, detailHistory: { close() {} },
  });
  const render = source('knowledge_board_render.js');
  const detail = source('knowledge_board_detail.js');
  vm.runInContext(extract(render, 'function createKnowledgeCard(', 'function getProjectColumnCount(')
    + extract(detail, 'function renderKnowledgeResourceIdentity(', 'function initialKnowledgeItemId(')
    + extract(detail, 'function finalizeDetailClose(', 'function syncDetailButtons('), context);
  return { dom, context, navigations };
}

test('Knowledge cards declare real kb_id before the asynchronous favorites module loads', () => {
  const { dom, context, navigations } = harness();
  try {
    assert.equal(context.window.PortalResourceFavorites, undefined);
    const id = 'concept_123456789abc';
    const card = context.createKnowledgeCard({kb_id: id, id: 'wrong-id', title: '<b>Title</b>'});
    dom.window.document.body.appendChild(card);
    assert.equal(card.querySelector('[data-resource-favorite]').dataset.resourceFavorite, id);
    assert.equal(card.querySelector('.cardTitle').textContent, '<b>Title</b>');
    assert.equal(card.querySelector('.cardTitle b'), null);
    card.click();
    assert.deepEqual(navigations, [id]);
    card.remove();
    assert.equal(card.querySelector('[data-resource-favorite]').isConnected, false);
    assert.equal(context.createKnowledgeCard({id: 'wrong-id'}).querySelector('[data-resource-favorite]'), null);
  } finally { dom.window.close(); }
});

test('detail favorites replace the old observed container on switching and remove it on closing', () => {
  const { dom, context } = harness();
  try {
    const host = context.el.mFavorite;
    assert.equal(host.closest('.knowledgeDetailActions') !== null, true);
    context.renderKnowledgeResourceIdentity({kb_id: 'concept_123456789abc', id: 'wrong-id'});
    const previous = host.firstElementChild;
    assert.equal(previous.dataset.resourceFavorite, 'concept_123456789abc');
    context.renderKnowledgeResourceIdentity({kb_id: 'insight_abcdef123456'});
    assert.equal(previous.isConnected, false);
    assert.equal(host.querySelectorAll('[data-resource-favorite]').length, 1);
    assert.equal(host.firstElementChild.dataset.resourceFavorite, 'insight_abcdef123456');
    context.finalizeDetailClose();
    assert.equal(host.childElementCount, 0);
    // Direct detail entry uses its actual requested resource address before index loading.
    context.renderKnowledgeResourceIdentity(null, 'plan_123456abcdef');
    assert.equal(host.firstElementChild.dataset.resourceFavorite, 'plan_123456abcdef');
  } finally { dom.window.close(); }
});
