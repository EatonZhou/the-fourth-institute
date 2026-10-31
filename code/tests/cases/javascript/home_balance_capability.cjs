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
  console.log('Billing capability, failed reads and actual zero remain distinct');
} finally { dom.window.close(); }
