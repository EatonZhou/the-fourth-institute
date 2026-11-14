'use strict';
const assert = require('node:assert/strict'), fs = require('node:fs'), path = require('node:path'), vm = require('node:vm');
const {JSDOM} = require('jsdom');
const {babelParse} = require('./babel_parse.cjs');
const source = fs.readFileSync(process.env.HOME_REFRESH_SOURCE || path.resolve(__dirname, '../../../management_portal/static/home.js'), 'utf8');
const declarations = babelParse(source, 'home.js').program.body;
const tick = () => new Promise(resolve => setImmediate(resolve));
const metrics = [];
async function scenario(visible, failure = false) {
  const dom = new JSDOM('<section data-home-component="agent-status">' + ['agent-accounts', 'system-resources', 'scheduled-triggered', 'usage-attribution'].map(id => `<div data-home-component="${id}"></div>`).join('') + '</section>');
  const document = dom.window.document, calls = [], completed = [];
  let release;
  const accounts = new Promise(resolve => { release = resolve; });
  const refresh = name => ({start() {}, refresh: async ({force}) => { assert.equal(force, true); calls.push(name); await accounts; completed.push(name); }});
  const tag = (parts, ...values) => parts.reduce((s, part, index) => s + part + (values[index] ?? ''), '');
  const c = vm.createContext({document, window: {PortalI18n: {t: x => x, html: tag}},
    agentStatusData: {}, diskStatusData: {}, gpuStatusData: {}, activeAgentStatus: {}, agentStatusSummaryLoaded: true, agentStatusRunboardHtml: '',
    currentScheduledTriggeredStatus: () => ({}), executionAccounts: () => [], agentStatusRunboard: () => '', focusedResidentIcon: () => null,
    accountManagementActions: () => '', accountEmptyState: () => '', esc: String,
    gpuResourceHtml: () => '', diskResourceHtml: () => '', gpuDetailsHtml: () => '', diskDetailsHtml: () => '',
    renderScheduledTriggeredStatus() {}, restoreResidentIconFocus() {}, syncAccountDetailsButtons() {}, refreshAccountQuotaWindows() {},
    homeController: {isComponentVisible: id => visible.includes(id)},
    ensureAgentStatusRefresh: () => refresh('accounts'), ensureActiveAgentStatusRefresh: () => refresh('active'),
  });
  for (const name of ['loadGpuStatus', 'loadDiskStatus', 'loadScheduledTriggeredStatus']) c[name] = async () => {
    calls.push(name); completed.push(name);
    if (failure) throw new Error('isolated read failure');
  };
  for (const name of ['ensureAgentStatusComponentHosts', 'renderAgentStatus', 'loadAgentStatus']) {
    const node = declarations.find(n => n.type === 'FunctionDeclaration' && n.id.name === name);
    assert.ok(node, name); vm.runInContext(source.slice(node.start, node.end), c);
  }
  try {
    c.renderAgentStatus({});
    const button = document.querySelector('[data-agent-status-refresh]'); button.click();
    await tick();
    const beforeRelease = [...calls], completedBeforeRelease = [...completed];
    release(); await tick(); await tick();
    const expected = [...(visible.includes('agent-accounts') ? ['accounts', 'active'] : []),
      ...(visible.includes('system-resources') ? ['loadGpuStatus', 'loadDiskStatus'] : []),
      ...(visible.includes('scheduled-triggered') ? ['loadScheduledTriggeredStatus'] : [])];
    metrics.push({visible, failure, started_before_accounts_release: beforeRelease, completed_before_accounts_release: completedBeforeRelease, final_calls: calls});
    assert.deepEqual(calls, expected, 'each visible owner is read once; hidden owners are not read');
    assert.deepEqual(beforeRelease, expected, 'independent reads must start before the account read completes');
    if (visible.includes('agent-accounts')) {
      assert.equal(button.disabled, true, 'real account loader still marks refresh busy');
      assert.equal(button.getAttribute('aria-busy'), 'true');
    }
  } finally { release(); dom.window.close(); }
}
(async () => {
  await scenario(['agent-accounts', 'system-resources', 'scheduled-triggered']);
  await scenario(['agent-accounts']);
  await scenario(['system-resources', 'scheduled-triggered'], true);
})().catch(error => { console.error(error); process.exitCode = 1; }).finally(() => {
  if (process.env.HOME_REFRESH_METRICS) fs.writeFileSync(process.env.HOME_REFRESH_METRICS, JSON.stringify(metrics, null, 2));
  console.log(JSON.stringify(metrics));
});
