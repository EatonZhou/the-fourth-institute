const assert = require('node:assert/strict');
const vm = require('node:vm');
const {JSDOM} = require('jsdom');
const {homeStatements} = require('./home_modules.cjs');
const dom = new JSDOM('<main></main>');
const tag = (parts, ...values) => parts.reduce((text, part, i) => text + part + (values[i] ?? ''), '');
const context = vm.createContext({
  window: {PortalI18n: {t: x => x, html: tag, text: tag}},
  esc: x => String(x ?? '').replaceAll('&', '&amp;').replaceAll('"', '&quot;').replaceAll('<', '&lt;'),
  formatDateTime: value => value ? new Date(value).toISOString() : '',
});
for (const name of ['accountQuotaWindow', 'accountQuotaWindowIsStale', 'accountQuotaWindows', 'accountQuotaBlockers',
  'rateLimitWindowLabel', 'accountSummaryQuotaWindows', 'accountQuotaWarning', 'accountLimitStatus']) {
  const fn = homeStatements().find(({node: n}) => n.type === 'FunctionDeclaration' && n.id.name === name);
  vm.runInContext(fn.source, context);
}
try {
  const quota = {id: 'Gemini/Five Hour', label: 'Gemini · Five Hour', remaining_percent: 73,
    observed_at: Date.now() / 1000, resets_at: Date.now() / 1000 + 3600, stale: false};
  const account = {account_id: 'antigravity_one'};
  dom.window.document.querySelector('main').innerHTML = context.accountQuotaWindow(account, quota);
  const meter = dom.window.document.querySelector('[role="meter"]');
  assert.equal(meter.getAttribute('aria-valuenow'), '73');
  assert.match(dom.window.document.querySelector('[data-account-quota-window]').title, /重置/);
  assert.match(dom.window.document.querySelector('main').textContent, /Gemini · Five Hour.*73%/s);
  dom.window.document.querySelector('main').innerHTML = context.accountQuotaWindow(account, {...quota, remaining_percent: null});
  assert.equal(dom.window.document.querySelector('[role="meter"]'), null);
  assert.match(dom.window.document.querySelector('main').textContent, /—/);
  dom.window.document.querySelector('main').innerHTML = context.accountQuotaWindow(account, {...quota, stale: true});
  assert.match(dom.window.document.querySelector('main').textContent, /73%\*/);
  assert.match(dom.window.document.querySelector('[data-account-quota-window]').title, /数据已过期/);
  const short = {...quota, quota_group: 'shared'};
  const weekly = {id: 'week', label: '每周', quota_group: 'shared', remaining_percent: 0};
  const render = (first, second) => {
    dom.window.document.querySelector('main').innerHTML = context.accountQuotaWindow(
      {...account, rate_limits: [first, second]}, first);
    return dom.window.document.querySelector('.agent-status-limit-meta').textContent;
  };
  assert.equal(render(short, weekly), '受限');
  for (const unknown of [null, undefined, '0', false, NaN]) {
    assert.equal(render(short, {...weekly, remaining_percent: unknown}), '73%', 'unknown is never exhaustion');
  }
  assert.equal(render({...short, quota_group: undefined}, {...weekly, quota_group: undefined}), '73%', 'legacy windows have no inferred dependency');
  assert.equal(render(short, {...weekly, quota_group: 'other'}), '73%', 'independent pools do not constrain each other');
  assert.equal(render({...weekly, remaining_percent: 85}, {...short, remaining_percent: 0}), '受限', 'short-window exhaustion also constrains the weekly window');
  for (const [value, label] of [[2.17, '2%'], [72.6, '73%'], [0, '0%'], [0.001, '<1%'], [0.9, '<1%'],
    [99.9, '>99%'], [100, '100%']]) {
    assert.equal(render({...short, remaining_percent: value}, {...weekly, remaining_percent: 50}), label);
    const node = dom.window.document.querySelector('[data-account-quota-window]');
    assert.ok(node.title.includes(`本窗口剩余 ${value}%`), 'tooltip preserves the sample precision');
    assert.equal(node.querySelector('[role="meter"]').getAttribute('aria-valuenow'), String(value));
  }
  assert.equal(render({...short, remaining_percent: 2.17, stale: true}, {...weekly, remaining_percent: 50}), '2%*');
  assert.match(dom.window.document.querySelector('[data-account-quota-window]').title, /2\.17%\*/);
  const now = Math.floor(Date.now() / 1000);
  const shortReset = now + 3600;
  const weeklyReset = now + 3 * 86400;
  for (const [provider, shortId, weeklyId, minutes] of [
    ['kimi', 'limit5h', 'limit7d', 10080],
    ['codex', 'primary', 'secondary', 10080],
    ['claude', 'five_hour', 'seven_day', undefined],
    ['antigravity', 'Gemini Models/Five Hour Limit Remaining', 'Gemini Models/Weekly Limit Remaining', undefined],
  ]) {
    const row = {provider, kind: 'subscription_login', rate_limits: [
      {id: shortId, remaining_percent: 100, window_duration_mins: 300, resets_at: shortReset},
      {id: weeklyId, remaining_percent: 2.17, window_duration_mins: minutes, resets_at: weeklyReset},
    ]};
    const weeklyLabel = `周刷新：${new Date(weeklyReset * 1000).toISOString()}`;
    assert.equal(context.accountLimitStatus(row), weeklyLabel,
      `${provider} shows the weekly reset even when the short window resets earlier`);
    const protectedRow = {...row, quota_reserve: {
      status: 'reserved', remaining_percent: 1.9, reserve_percent: 2,
      windows: [{window: weeklyId, remaining_percent: 1.9, resets_at: weeklyReset}],
    }};
    assert.equal(context.accountLimitStatus(protectedRow), weeklyLabel,
      'remaining quota shows the weekly reset even when the account is protected');
    assert.equal(context.accountLimitStatus({...protectedRow, error: 'failed'}), '用量刷新失败');
    const exhaustedRow = {...row, quota_reserve: {
      status: 'reserved', remaining_percent: 0, reserve_percent: 2,
      windows: [{window: weeklyId, remaining_percent: 0, resets_at: weeklyReset}],
    }};
    assert.equal(context.accountLimitStatus(exhaustedRow), `预计恢复 ${new Date(weeklyReset * 1000).toISOString()}`);
    exhaustedRow.quota_reserve.windows[0].resets_at = now - 1;
    assert.equal(context.accountLimitStatus(exhaustedRow), '等待额度恢复确认');
    assert.equal(context.accountLimitStatus({...row, refreshing: true}), '正在刷新用量…');
    assert.equal(context.accountLimitStatus({...row, error: 'failed'}), '用量刷新失败');
    assert.equal(context.accountLimitStatus({...row, rate_limits: row.rate_limits.slice(0, 1)}), '等待用量刷新',
      'a short-window reset must never be labelled as weekly');
    row.rate_limits[1].resets_at = now - 1;
    assert.equal(context.accountLimitStatus(row), '等待用量刷新', 'past resets do not imply a new weekly reset');
  }
  console.log('Subscription percentages preserve stale and unknown states');
} finally { dom.window.close(); }
