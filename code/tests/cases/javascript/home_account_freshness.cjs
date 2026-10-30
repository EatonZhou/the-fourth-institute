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
for (const name of ['accountCreditsHtml', 'accountQuotaWindow', 'accountQuotaWindowIsStale']) {
  const fn = homeStatements().find(({node: n}) => n.type === 'FunctionDeclaration' && n.id.name === name);
  vm.runInContext(fn.source, context);
}
const render = account => {
  dom.window.document.querySelector('main').innerHTML = context.accountCreditsHtml(account);
  return dom.window.document.querySelector('[data-account-credits]');
};
try {
  const credits = {balance: 1234, observed_at: 1000, stale: false};
  const fresh = render({credits, checked_at: 9999});
  assert.match(fresh.title, /1970-01-01T00:16:40.000Z/);
  assert.equal(fresh.classList.contains('is-stale'), false);
  const failed = render({credits: {...credits, stale: true}, error: '读取超时'});
  assert.match(failed.title, /1970-01-01T00:16:40.000Z/);
  assert.match(failed.title, /数据已过期.*读取超时/);
  assert.match(failed.textContent, /1,234\*/);
  const unknown = render({credits: {balance: null}, checked_at: 9999});
  assert.match(unknown.title, /采样时间未知/);
  assert.match(unknown.textContent, /—/);
  assert.equal(render({}), null);
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
  console.log('Credits retain their own sampling identity, stale value and unknown-time display');
} finally { dom.window.close(); }
