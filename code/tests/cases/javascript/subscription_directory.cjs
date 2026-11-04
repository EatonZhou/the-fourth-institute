const assert = require('node:assert/strict');
const fs = require('node:fs'), vm = require('node:vm');
const {JSDOM} = require('jsdom');
const {window} = new JSDOM('<main></main>', {url: 'http://portal.invalid/'});
const calls = [];
window.PortalI18n = {t: text => text};
window.PortalUI = {createDialogController: ({modal, onClose}) => ({open: () => modal.setAttribute('open', ''), close: onClose, destroy() {}})};
window.PortalInstructionClient = {execute: async (name, args) => {
  calls.push({name, args});
  switch (name) {
    case 'ListProviders': return {providers: ['codex', 'claude'].map(provider => ({provider, kinds: ['subscription_login'], account_directory_prefix: `/host/accounts/${provider}/`}))};
    case 'ListProviderAccounts': return {accounts: []};
    case 'ListBackendRoles': return {roles: []};
    case 'ListModelApis': return {apis: []};
    case 'RegisterProviderAccount': return {};
    case 'GetProviderAccountSetup': return {setup: {provider: 'codex', runtime_ready: true, login_state: 'logged_out', models: []}};
    default: throw Error(name);
  }
}};
const context = vm.createContext({window, document: window.document, Node: window.Node, AbortController, setTimeout, clearTimeout, setInterval, clearInterval, URL});
vm.runInContext(fs.readFileSync('management_portal/static/portal_management_backend.js', 'utf8').replaceAll('export ', ''), context);
const flush = async () => {for (let i = 0; i < 8; i++) await new Promise(resolve => setImmediate(resolve));};
(async () => {
  const panel = await context.mount(window.document.querySelector('main'), {me: {access: {permission_system: 'open'}}});
  await flush();
  [...window.document.querySelectorAll('button')].find(b => b.textContent === '接入订阅帐号').click();
  const form = window.document.querySelector('dialog form');
  const change = (name, value, event = 'input') => {form.elements[name].value = value; form.elements[name].dispatchEvent(new window.Event(event));};
  assert(!form.elements.account_dir.required);
  change('account_id', 'new-account');
  assert.equal(form.elements.account_dir.value, '/host/accounts/codex/new-account');
  change('account_dir', '/existing/login');
  change('account_id', 'renamed');
  assert.equal(form.elements.account_dir.value, '/existing/login');
  change('provider', 'claude', 'change');
  assert.equal(form.elements.account_dir.value, '/host/accounts/claude/renamed');
  change('provider', 'codex', 'change');
  assert.equal(form.elements.account_dir.value, '/existing/login');
  change('account_dir', '');
  assert.equal(form.elements.account_dir.value, '/host/accounts/codex/renamed');
  form.dispatchEvent(new window.Event('submit', {bubbles: true, cancelable: true}));
  await flush();
  assert.equal(calls.find(c => c.name === 'RegisterProviderAccount').args.config.account_dir, '/host/accounts/codex/renamed');
  assert.equal(calls.find(c => c.name === 'GetProviderAccountSetup').args.account_id, 'renamed');
  assert(window.document.querySelector('[data-backend-setup-status]'), 'registration continues into assisted sign-in setup');
  assert(!calls.some(c => c.name === 'CheckProviderAccount'), 'no model call before sign-in');
  panel.destroy();
})().catch(error => {console.error(error); process.exitCode = 1;});
