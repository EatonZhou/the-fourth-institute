const assert = require('node:assert/strict');
const fs = require('node:fs'), vm = require('node:vm');
const {JSDOM} = require('jsdom');
const dom = new JSDOM('<main></main>', {url: 'http://portal.invalid/'});
const {window} = dom;
const calls = [];
let missing = false, fail = false, roles = [];
window.PortalI18n = {t: value => value};
window.PortalUI = {createDialogController: ({modal, onClose}) => ({
  open: () => modal.setAttribute('open', ''), close: () => onClose(), destroy: () => {},
})};
window.PortalInstructionClient = {execute: async (name, args, options) => {
  calls.push({name, args, options});
  switch (name) {
    case 'ListProviders': return {providers: [{provider: 'claude', kinds: ['subscription_login']}]};
    case 'ListProviderAccounts': return {accounts: [{account_id: 'existing', provider: 'claude', kind: 'subscription_login',
      registration: {version: 1, enabled: true}, login_state: 'logged_in'}]};
    case 'ListModelApis': return {apis: []};
    case 'ListBackendRoles': return {roles};
    case 'GetProviderAccountSetup': return {setup: {provider: 'claude', enabled: true, runtime_ready: !missing,
      login_state: 'logged_in', supports_tools: true, models: [{id: 'model'}], default_model: 'model'}};
    case 'CheckProviderAccount': return {execution_id: 'execution_check'};
    case 'GetProviderAccountCheck': return {check: {execution_id: 'execution_check', status: 'completed', passed: !fail,
      model: 'model', message: fail ? '测试未通过' : '测试通过'}};
    case 'UpdateBackendRole':
      roles = [{role: 'default', account_id: args.account_id, version: (roles[0]?.version || 0) + 1}];
      return {role: roles[0]};
    case 'PrepareProviderRuntime': missing = false; return {preparation: {status: 'ready'}};
    default: throw new Error(name);
  }
}};
const context = vm.createContext({window, document: window.document, Node: window.Node, AbortController,
  setTimeout, clearTimeout, URL, console});
const source = fs.readFileSync('management_portal/static/portal_management_backend.js', 'utf8').replaceAll('export ', '');
vm.runInContext(source, context);
const flush = () => new Promise(resolve => setImmediate(resolve));
const click = text => {
  const button = [...window.document.querySelectorAll('button')].find(el => el.textContent === text);
  assert(button && !button.disabled, text); button.click();
};
(async () => {
  await context.mount(window.document.querySelector('main'), {me: {access: {permission_system: 'open'}}});
  click('接入与测试'); await flush();
  assert.equal(window.document.querySelector('input[name=model]').value, 'model');
  click('测试并完成'); await flush(); await flush();
  assert(calls.some(call => call.name === 'UpdateBackendRole' && call.args.verification_execution_id === 'execution_check'));
  assert(!calls.some(call => ['PrepareProviderRuntime', 'OpenProviderAccountLogin'].includes(call.name)));
  click('测试并完成'); await flush(); await flush();
  assert.deepEqual(calls.filter(call => call.name === 'UpdateBackendRole').map(call => call.options.expectedVersion), [0, 1]);
  click('关闭'); missing = true; fail = true; calls.length = 0;
  click('接入与测试'); await flush();
  assert([...window.document.querySelectorAll('button')].find(el => el.textContent === '测试并完成').disabled);
  click('准备运行环境'); await flush();
  click('测试并完成'); await flush(); await flush();
  assert(!calls.some(call => call.name === 'UpdateBackendRole'));
  assert.match(window.document.querySelector('[data-backend-setup-status]').textContent, /未通过/);
  click('关闭'); console.log('provider onboarding UI passed');
})().catch(error => {console.error(error); process.exitCode = 1;});
