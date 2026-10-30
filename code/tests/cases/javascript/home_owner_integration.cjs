// Real isolated HTTP/cache owner and the production Home renderer, without a browser.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const {JSDOM} = require('jsdom');
const {homeStatements} = require('./home_modules.cjs');

async function main() {
  const input = JSON.parse(fs.readFileSync(0, 'utf8'));
  const origin = new URL(input.origin);
  assert.ok(['127.0.0.1', 'localhost', '[::1]'].includes(origin.hostname), 'isolated owner only');
  const api = async (endpoint, data) => {
    const response = await fetch(new URL(endpoint, origin), {
      method: data === undefined ? 'GET' : 'POST',
      headers: {Cookie: `db_session=${input.cookie}`, Origin: origin.origin, 'Content-Type': 'application/json'},
      body: data === undefined ? undefined : JSON.stringify(data),
      signal: AbortSignal.timeout(5000),
    });
    const body = await response.json();
    assert.equal(response.status, 200, `${endpoint}: ${JSON.stringify(body)}`);
    return body;
  };
  const control = data => api('/__isolated__/control', data);
  const observation = () => api('/__isolated__/observations');
  const until = async predicate => {
    const deadline = Date.now() + 5000;
    while (Date.now() < deadline) {
      const result = await observation();
      if (predicate(result)) return result;
      await new Promise(resolve => setTimeout(resolve, 20));
    }
    throw new Error('isolated collector did not reach the expected state');
  };
  const dom = new JSDOM('<section data-home-component="agent-status">' +
    ['agent-accounts', 'system-resources', 'scheduled-triggered', 'usage-attribution']
      .map(id => `<div data-home-component="${id}"></div>`).join('') + '</section>');
  // Unrelated panels/localization are ports. The warning, busy state and button
  // markup come directly from renderAgentStatus; no duplicate refresh algorithm.
  const tag = (parts, ...values) => parts.reduce((result, part, index) => result + part + (values[index] ?? ''), '');
  const context = vm.createContext({
    document: dom.window.document,
    window: {PortalI18n: {t: value => value, html: tag}},
    agentStatusData: {}, diskStatusData: {}, gpuStatusData: {}, scheduledTriggeredStatusData: {},
    activeAgentStatus: {}, agentStatusSummaryLoaded: true, agentStatusRunboardHtml: '',
    canRegisterAccount: false, canSetScheduler: false, accountRegistrationPending: false,
    currentScheduledTriggeredStatus: () => context.scheduledTriggeredStatusData,
    currentCanRegisterAccount: () => context.canRegisterAccount, currentCanSetScheduler: () => context.canSetScheduler,
    esc: value => {const node = dom.window.document.createElement('span'); node.textContent = value; return node.innerHTML;},
    executionAccounts: () => [], agentStatusRunboard: () => '', focusedResidentIcon: () => null,
    gpuResourceHtml: () => '', diskResourceHtml: () => '', gpuDetailsHtml: () => '', diskDetailsHtml: () => '',
    renderScheduledTriggeredStatus() {}, restoreResidentIconFocus() {}, syncAccountDetailsButtons() {},
    refreshAccountQuotaWindows() {},
  });
  const wanted = new Set(['ensureAgentStatusComponentHosts', 'accountManagementActions', 'accountEmptyState', 'renderAgentStatus']);
  for (const {node, source} of homeStatements(input.static)) {
    if (node.type === 'FunctionDeclaration' && wanted.delete(node.id.name)) {
      vm.runInContext(source, context);
    }
  }
  assert.equal(wanted.size, 0, 'production rendering functions must exist');
  const panel = () => dom.window.document.querySelector('[data-home-component="agent-accounts"]');
  const button = () => dom.window.document.querySelector('[data-agent-status-refresh]');
  try {
    await control({clock: 1100});
    await api('/api/agent-status/refresh', {});
    await until(result => !result.refresh_in_progress);
    const skipped = await api('/api/agent-status');
    assert.equal(skipped.refresh_retry_after_seconds, 200);
    context.renderAgentStatus(skipped);
    assert.match(panel().textContent, /余额和账号资料刷新有间隔限制，请在 4 分钟后重试/);
    assert.equal(button().disabled, false);

    await control({clock: 1301, hold: true});
    await api('/api/agent-status/refresh', {});
    const pending = await until(result => result.collector_entered);
    assert.equal(pending.refresh_in_progress, true);
    assert.equal(pending.rpc_calls.length, 1);
    const beforeRelease = await api('/api/agent-status');
    context.renderAgentStatus(beforeRelease);
    assert.equal(button().disabled, true);
    assert.equal(button().getAttribute('aria-busy'), 'true');
    assert.doesNotMatch(panel().textContent, /余额和账号资料刷新有间隔限制/);

    await control({release: true});
    await until(result => result.rpc_calls.length === 2 && !result.refresh_in_progress);
    context.renderAgentStatus(await api('/api/agent-status'));
    assert.doesNotMatch(panel().textContent, /余额和账号资料刷新有间隔限制/);
    assert.equal(button().disabled, false);
    process.stdout.write(JSON.stringify({
      same_window_notice_matches_response: true, window_reopened_notice_cleared: true,
      skipped_response_seconds: skipped.refresh_retry_after_seconds,
      response_while_collector_held: {status: beforeRelease.status, refreshing: beforeRelease.refreshing, retry_after_seconds: beforeRelease.refresh_retry_after_seconds},
      accepted_refresh_completed_before_collector_release: pending.trace.some(event => event.event === 'http_end' && event.method === 'POST' && event.path === '/api/agent-status/refresh'),
      controlled_collector_was_running: pending.refresh_in_progress,
      rendering: 'production renderAgentStatus in Node DOM', browser_launched: false,
    }));
  } finally {
    dom.window.close();
  }
}

main().catch(error => { console.error(error.stack); process.exitCode = 1; });
