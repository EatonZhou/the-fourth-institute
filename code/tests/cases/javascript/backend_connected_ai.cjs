const assert = require('node:assert/strict');
const fs = require('node:fs'), vm = require('node:vm');
const {JSDOM} = require('jsdom');
const dom = new JSDOM('<main></main>', {url: 'http://portal.invalid/'});
const {window} = dom, calls = [];
window.Element.prototype.scrollIntoView = function () {};
let apisReady;
const apiGate = new Promise(resolve => { apisReady = resolve; });
const api = {api_id: 'gateway', default_model: 'example-model', base_url: 'https://gateway.invalid/v1', enabled: true,
  models: [{id: 'example-model', reasoning_efforts: ['low', 'medium', 'high']}, {id: 'example-mini'}, {id: 'retired', enabled: false}]};
const apis = [api, {...api, api_id: 'other-gateway', enabled: false}];
let accounts = [
  ...['codex', 'codex', 'kimi', 'opencode', 'provider_api'].map((provider, index) => ({
    account_id: `gateway-${provider}-${index}`, provider, kind: 'api_profile', route_id: `route-${index}`,
    enabled: index !== 2, registration_version: index + 1, config: {api_id: 'gateway'},
  })),
  {account_id: 'other-codex', provider: 'codex', kind: 'api_profile', enabled: true, registration_version: 1,
    config: {api_id: 'other-gateway'}},
  {account_id: 'subscription', provider: 'codex', kind: 'subscription_login', enabled: true,
    registration_version: 1, login_state: 'logged_in'},
  {account_id: 'unbound', provider: 'provider_api', kind: 'api_profile', enabled: true, registration_version: 1},
];
window.PortalI18n = {t: (text, values = {}) => text.replace(/\{(p\d+)\}/g, (_, key) => values[key])};
window.PortalUI = {confirm: async () => true, createDialogController: ({modal, onClose}) => ({
  open: () => modal.setAttribute('open', ''), close: onClose, destroy() {},
})};
window.PortalInstructionClient = {execute: async (name, args) => {
  calls.push({name, args: JSON.parse(JSON.stringify(args))});
  switch (name) {
    case 'ListProviders': return {providers: [
      {provider: 'codex', title: 'Codex', kinds: ['subscription_login', 'api_profile'], default_route_id: 'route-0'},
      {provider: 'kimi', title: 'Kimi Code', kinds: ['api_profile']},
      {provider: 'opencode', title: 'OpenCode', kinds: ['api_profile']},
      {provider: 'provider_api', title: 'Model API', kinds: ['api_profile']},
    ]};
    case 'ListProviderAccounts': return {accounts};
    case 'ListModelApis': await apiGate; return {apis};
    case 'ListBackendRoles': return {roles: []};
    case 'DeleteProviderAccount': accounts = accounts.filter(account => account.account_id !== args.account_id); return {};
    case 'GetProviderAccountSetup': return {setup: {provider: 'codex', enabled: true, runtime_ready: true,
      login_state: 'not_applicable', default_model: 'example-model', models: ['example-model']}};
    default: throw Error(name);
  }
}};
const context = vm.createContext({window, document: window.document, Node: window.Node, AbortController, setTimeout, clearTimeout, setInterval, clearInterval, URL});
vm.runInContext(fs.readFileSync('management_portal/static/portal_management_backend.js', 'utf8').replaceAll('export ', ''), context);
const flush = async () => {for (let n = 0; n < 8; n++) await new Promise(resolve => setImmediate(resolve));};
const section = () => window.document.querySelector('[data-backend-section=accounts]');
const group = id => [...section().querySelectorAll('[data-backend-api-connection]')].find(el => el.dataset.backendApiConnection === id);
const row = id => [...section().querySelectorAll('[data-backend-account]')].find(el => el.dataset.backendAccount === id);
const click = (root, text) => {const control = [...root.querySelectorAll('button')].find(el => el.textContent === text); assert(control && !control.disabled, text); control.click();};
(async () => {
  let panel;
  try {
    panel = await context.mount(window.document.querySelector('main'), {me: {access: {permission_system: 'open'}}, accountId: 'gateway-codex-1'});
    await flush();
    assert(group('gateway').querySelector('details').open, 'a requested account opens its connection before focusing');
    assert.equal(window.document.activeElement, row('gateway-codex-1'));
    apisReady(); await flush();
    assert.equal(section().querySelectorAll('[data-backend-api-connection]').length, 2, 'same endpoint/model do not merge distinct API identities');
    let shared = group('gateway');
    assert.equal(shared.querySelector('h3').textContent, 'example-model');
    assert.equal(shared.textContent.split('example-model').length - 1, 1, 'the default model appears once for all five accounts');
    assert.equal(shared.textContent.split(api.base_url).length - 1, 1, 'the endpoint appears once per connection');
    assert.match(shared.textContent, /其他模型：example-mini/);
    assert(!shared.textContent.includes('retired'));
    assert(!shared.textContent.includes('high'), 'reasoning efforts do not create rows');
    assert.equal(shared.querySelectorAll('[data-backend-account]').length, 5);
    assert.equal(shared.querySelectorAll('header button').length, 1, 'each connection has one edit action');
    assert(shared.querySelector('details').open, 'late API metadata keeps the account group expanded');
    assert.match(shared.querySelector('summary').textContent, /（5）/);
    assert.equal(row('gateway-codex-0').children[0].textContent, 'Codex');
    assert.equal(row('gateway-codex-0').children[1].textContent, 'gateway-codex-0');
    assert.equal(row('gateway-codex-1').children[1].textContent, 'gateway-codex-1', 'two accounts for one runner remain distinguishable');
    assert.equal(row('gateway-kimi-2').children[0].textContent, 'Kimi Code');
    assert.match(row('gateway-kimi-2').children[2].textContent, /已停用/);
    assert.match(row('gateway-codex-0').children[2].textContent, /已启用/);
    assert.equal(section().querySelectorAll('[data-backend-account-selection]').length, 1, 'default selection belongs to one policy control, not route badges');
    assert.match(group('other-gateway').querySelector('header').textContent, /已停用/);
    assert(!row('subscription').closest('[data-backend-api-connection]'));
    assert(!row('unbound').closest('[data-backend-api-connection]'));
    assert(!calls.some(call => /Create|Register|Update|Delete|Check|Prepare|CompleteChat/.test(call.name)), 'rendering has no account or model side effects');

    click(section(), '刷新'); await flush();
    assert(group('gateway').querySelector('details').open, 'refresh preserves expansion');
    click(group('gateway').querySelector('header'), '编辑连接');
    assert.equal(window.document.querySelector('dialog [name=api_id]').value, 'gateway');
    click(window.document.querySelector('dialog'), '关闭');
    click(row('gateway-codex-1'), '设置与测试'); await flush();
    assert.equal(calls.filter(call => call.name === 'GetProviderAccountSetup').at(-1).args.account_id, 'gateway-codex-1');
    assert(!calls.some(call => call.name === 'CheckProviderAccount'), 'opening setup waits for an explicit connection test');
    click(window.document.querySelector('dialog'), '关闭');
    click(row('gateway-codex-1'), '删除'); await flush();
    assert.equal(calls.filter(call => call.name === 'DeleteProviderAccount').at(-1).args.account_id, 'gateway-codex-1');
    assert.equal(group('gateway').querySelectorAll('[data-backend-account]').length, 4);
    assert(group('gateway').querySelector('details').open);
    assert(row('gateway-codex-0'), 'deleting one account keeps its sibling routes');
    assert.equal(apis.length, 2, 'deleting an account leaves the connection intact');
    console.log('Connected AI groups shared API routes and preserves account controls');
  } finally {panel?.destroy(); dom.window.close();}
})().catch(error => {console.error(error); process.exitCode = 1;});
