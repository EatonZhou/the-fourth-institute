const assert = require('node:assert/strict');
const fs = require('node:fs'), path = require('node:path'), vm = require('node:vm');
const {JSDOM} = require('jsdom');
const {babelParse} = require('./babel_parse.cjs');
const dom = new JSDOM('<main></main>');
const tag = (parts, ...values) => parts.reduce((text, part, i) => text + part + (values[i] ?? ''), '');
const context = vm.createContext({window: {PortalI18n: {t: x => x, html: tag, text: tag}},
  esc: x => String(x ?? '').replaceAll('&', '&amp;').replaceAll('"', '&quot;').replaceAll('<', '&lt;'),
  formatDateTime: x => x || ''});
const source = fs.readFileSync(path.resolve(__dirname, '../../../management_portal/static/home.js'), 'utf8');
const fn = babelParse(source, 'home.js').program.body.find(n => n.type === 'FunctionDeclaration' && n.id.name === 'accountBalanceHtml');
vm.runInContext(source.slice(fn.start, fn.end), context);
const render = balance => {
  const main = dom.window.document.querySelector('main');
  main.innerHTML = context.accountBalanceHtml({balance});
  return main;
};
try {
  for (const balance of [{status: 'not_supported'}, {status: 'unavailable', reason: 'unsupported_endpoint'}]) {
    const el = render(balance);
    assert.match(el.textContent, /此连接未提供余额查询/);
    assert.doesNotMatch(el.textContent, /余额不足|API 状态不可用|0 CNY/);
    assert.equal(el.querySelector('.agent-status-balance-coins'), null);
  }
  assert.match(render({reason: 'unauthorized'}).textContent, /余额查询未获授权/);
  assert.match(render({reason: 'invalid_response'}).textContent, /余额返回格式无法识别/);
  assert.match(render({status: 'unavailable', reason: 'unsupported_endpoint', display: '12 CNY', stale: true}).textContent, /12 CNY（上次记录）/);
  assert.match(render({status: 'ok', display: '0 CNY', amount: 0}).textContent, /0 CNY/);
  console.log('Billing capability, failed reads and actual zero remain distinct');
} finally { dom.window.close(); }
