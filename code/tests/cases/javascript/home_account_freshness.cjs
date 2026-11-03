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
for (const name of ['accountQuotaWindow', 'accountQuotaWindowIsStale', 'accountQuotaWindows', 'accountQuotaBlockers']) {
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
  console.log('Subscription percentages preserve stale and unknown states');
} finally { dom.window.close(); }
