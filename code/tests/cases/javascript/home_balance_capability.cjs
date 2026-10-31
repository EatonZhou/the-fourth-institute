const assert = require('node:assert/strict');
const vm = require('node:vm');
const {JSDOM} = require('jsdom');
const {homeStatements} = require('./home_modules.cjs');
const dom = new JSDOM('<main></main>');
const tag = (parts, ...values) => parts.reduce((text, part, i) => text + part + (values[i] ?? ''), '');
const context = vm.createContext({window: {PortalI18n: {t: x => x, html: tag, text: tag}},
  esc: x => String(x ?? '').replaceAll('&', '&amp;').replaceAll('"', '&quot;').replaceAll('<', '&lt;'),
  formatDateTime: x => x || ''});
const fn = homeStatements().find(({node: n}) => n.type === 'FunctionDeclaration' && n.id.name === 'accountBalanceHtml');
vm.runInContext(fn.source, context);
const render = balance => {
  const main = dom.window.document.querySelector('main');
  main.innerHTML = context.accountBalanceHtml({balance});
  return main;
};
try {
  for (const balance of [{status: 'not_supported'}, {status: 'unavailable', reason: 'unsupported_endpoint'}]) {
    const el = render(balance);
    assert.match(el.querySelector('[data-account-balance]').title, /此连接未提供余额查询/);
    assert.equal(el.textContent, '—');
    assert.doesNotMatch(el.textContent, /余额不足|API 状态不可用|0 CNY/);
    assert.equal(el.querySelector('.agent-status-balance-coins'), null);
  }
  assert.match(render({reason: 'unauthorized'}).firstChild.title, /余额查询未获授权/);
  assert.match(render({reason: 'invalid_response'}).firstChild.title, /余额返回格式无法识别/);
  assert.match(render({status: 'unavailable', reason: 'unsupported_endpoint', display: '12 CNY', stale: true}).textContent, /12 CNY\*/);
  assert.match(render({status: 'ok', display: '0 CNY', amount: 0}).textContent, /0 CNY/);
  const cny = amount => ({status:'ok',display:`${amount} CNY`,currency:'CNY',comparison_amount:amount,comparison_currency:'CNY'});
  for (const [amount,count] of [[0,0],[-10,0],[4,0],[5,1],[10,1],[80.38,8],[125,13],[100000,24]]) {
    const el=render(cny(amount));
    assert.equal(el.querySelectorAll('.agent-status-balance-coins i').length,count);
    assert.equal(el.querySelector('.agent-status-balance-value').textContent,`${amount} CNY`);
  }
  assert.equal(render(cny(100000)).querySelector('.agent-status-coins-more').textContent,'+9976');
  const dollars={...cny(70),display:'10 USD',currency:'USD',exchange_rate_date:'2026-10-31'};
  assert.equal(render(dollars).querySelectorAll('.agent-status-balance-coins i').length,7);
  assert.match(render(dollars).querySelector('[data-account-balance]').title,/70.00 CNY.*2026-10-31/);
  assert.equal(render({...dollars,comparison_amount:undefined}).querySelector('.agent-status-balance-coins'),null);
  assert.equal(render({...cny(50),metric_kind:'availability'}).querySelector('.agent-status-balance-coins'),null);
  for(const stale of [{stale:true,status:'unavailable'}, {comparison_stale:true}]) {
    assert(render({...cny(50),...stale}).querySelector('.agent-status-balance-visual.is-stale'));
  }
  console.log('Coins use rounded CNY / 10; missing balances and FX are not fabricated, stale values stay marked');
} finally { dom.window.close(); }
