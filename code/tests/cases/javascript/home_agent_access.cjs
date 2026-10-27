const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {JSDOM} = require('jsdom');
const {babelParse} = require('./babel_parse.cjs');

const root = path.resolve(__dirname, '../../../management_portal/static');
const source = fs.readFileSync(path.join(root, 'home.js'), 'utf8');
const ast = babelParse(source, 'home.js');
const functionNames = new Set([
  'esc', 'residentShortcutId', 'residentShortcutSummary', 'residentShortcutEntries',
  'residentNavigationUrl', 'residentShortcutUrl', 'agentAccessActivityLabel',
  'agentAccessLinkHtml', 'renderAgentAccess', 'loadAgentAccessSummaries', 'loadAgentAccess',
]);
const variableNames = new Set([
  'homeApi', 'agentAccessCatalog', 'personalAmaResidentId', 'agentAccessLoadSeq',
  'agentAccessSummaryById', 'agentAccessSummaryPending', 'activeAgentStatus',
]);
const ama = {resident_id: 'resident_ama', lineage_id: 'lineage_personal', title: 'Personal Agent'};
const recommender = {resident_id: 'resident_recommender', lineage_id: 'lineage_discovery', title: 'Discovery'};

async function check({shortcuts, associations, loading = false, fail = false} = {}) {
  const dom = new JSDOM('<section data-agent-access></section>', {url: 'http://portal.invalid/'});
  const requests = [];
  const writes = [];
  let refresh;
  let directoryReads = 0;
  const recent = [{key: 'resident:resident_recent', title: 'Recent Agent',
    url: '/lineage?resident=resident_recent&lineage=lineage_recent', status: 'queued'}];
  const api = {
    async get(url, options) {
      requests.push(url);
      assert.equal(options.cache, 'no-store');
      if (url === '/api/agent-workbench/residents') {
        directoryReads++;
        return {ready: !(loading && directoryReads === 1), residents: [
          {resident_id: 'resident_leader', lineage_id: 'lineage_shared', title: 'Leader'},
        ]};
      }
      if (url === '/api/agent-workbench/residents/shortcuts') {
        if (fail) throw new Error('shortcuts unavailable');
        return shortcuts || {residents: [ama, recommender], recommendation_resident: recommender};
      }
      throw new Error(`Unexpected read: ${url}`);
    },
    async post(url) {
      writes.push(url);
      assert.equal(url, '/api/agent-workbench/resident-discovery/contexts');
      return {residents: []};
    },
    createClient() { return api; },
  };
  const tag = (parts, ...values) => parts.reduce((html, part, i) => html + part + (values[i] ?? ''), '');
  const window = {
    PortalAPI: api, location: dom.window.location,
    addEventListener: dom.window.addEventListener.bind(dom.window),
    setTimeout: callback => setTimeout(callback, 0),
    PortalI18n: {t: value => value, markup: value => value, html: tag},
    PortalUIPrimitives: {escapeHtml: value => String(value ?? '').replaceAll('&', '&amp;')
      .replaceAll('"', '&quot;').replaceAll('<', '&lt;')},
    PortalIdentity: {listUIAssociations: async key => {
      assert.equal(key, 'lineage-workbench.personal-ama');
      return associations || {items: [{key: 'primary', value: {resident_id: ama.resident_id}}]};
    }},
    PortalConversationStore: {entries: () => recent, subscribe: callback => { refresh = callback; }},
  };
  const context = vm.createContext({window, document: dom.window.document, URL, URLSearchParams});
  vm.runInContext(fs.readFileSync(path.join(root, 'portal_resident_service.js'), 'utf8'), context);
  for (const node of ast.program.body) {
    if (node.type === 'FunctionDeclaration' && functionNames.has(node.id.name)
      || node.type === 'VariableDeclaration' && node.declarations.some(item => variableNames.has(item.id.name))
      || node.type === 'ExpressionStatement' && source.slice(node.start, node.end).startsWith('window.PortalConversationStore?.subscribe?.(')) {
      vm.runInContext(source.slice(node.start, node.end), context);
    }
  }
  await context.loadAgentAccess();
  await new Promise(resolve => setImmediate(resolve));
  const panel = dom.window.document.querySelector('[data-agent-access]');
  try {
    if (fail) {
      assert.match(panel.textContent, /Resident 入口读取失败：shortcuts unavailable/);
      assert.equal(panel.querySelector('a'), null);
    } else {
      const expected = shortcuts ? [] : [ama, recommender];
      const verifyLinks = () => {
        const links = [...panel.querySelectorAll('a.agent-access-command')];
        assert.equal(links.length, expected.length, 'Bound shortcut Residents must be clickable, even when absent from the directory');
        links.forEach((link, index) => {
          const url = new URL(link.href);
          assert.equal(url.pathname, '/lineage');
          assert.equal(url.searchParams.get('resident'), expected[index].resident_id);
          assert.equal(url.searchParams.get('lineage'), expected[index].lineage_id);
          assert.equal(link.tabIndex, 0);
        });
        assert.equal(panel.querySelector('a.agent-access-recent-item').getAttribute('href'), recent[0].url);
      };
      verifyLinks();
      recent[0].status = 'running';
      refresh();
      verifyLinks();
      assert.match(panel.querySelector('a.agent-access-recent-item').textContent, /运行中/);
      if (!expected.length) assert.equal((panel.textContent.match(/当前暂不可用/g) || []).length, 2);
      assert.equal(directoryReads, loading ? 2 : 1);
    }
    assert.equal(requests.filter(url => url.endsWith('/shortcuts')).length, 1);
    assert.ok(writes.every(url => url.endsWith('/resident-discovery/contexts')), 'Opening Home must never start a conversation');
  } finally {
    dom.window.close();
  }
}

(async () => {
  await check();
  await check({loading: true});
  await check({shortcuts: {residents: [], recommendation_resident: null}, associations: {items: []}});
  await check({fail: true});
  console.log('Home Agent Access: separate shortcuts, navigation targets, refresh, loading, unavailable and errors passed');
})().catch(error => { console.error(error); process.exitCode = 1; });
