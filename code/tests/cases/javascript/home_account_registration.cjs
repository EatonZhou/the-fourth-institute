const assert = require('node:assert/strict');
const fs = require('node:fs'), path = require('node:path'), vm = require('node:vm');
const {JSDOM} = require('jsdom');
const {homeStatements} = require('./home_modules.cjs');
const {PORTAL_MANAGEMENT_SCRIPTS} = require('./portal_management_scripts.cjs');
const dom = new JSDOM('<section id="home"></section><main></main>', {url: 'http://portal.invalid/', runScripts: 'outside-only'});
const w = dom.window, context = dom.getInternalVMContext();
const source = name => fs.readFileSync(path.resolve(__dirname, '../../../management_portal/static', name), 'utf8');
const tag = (parts, ...args) => parts.reduce((s, p, i) => s + p + (args[i] ?? ''), '');
const calls = [], notices = [];
let accounts = [], panel, release;
w.PortalI18n = {t: x => x, html: tag, text: tag};
w.PortalIdentity = {passwordRequirements: ''};
w.PortalUI = {toast: message => notices.push(message), createDialogController: ({modal, onClose}) => ({
  open: () => modal?.setAttribute('open', ''), close: () => onClose?.(), destroy() {},
})};
w.PortalInstructionClient = {execute: async (name, args) => {
  calls.push({name, args});
  switch (name) {
    case 'ListProviders': return {providers: [{provider: 'codex', kinds: ['subscription_login']}]};
    case 'ListProviderAccounts': return {accounts};
    case 'ListModelApis': return {apis: []};
    case 'ListBackendRoles': return {roles: []};
    case 'RegisterProviderAccount': accounts = [{...args, registration: {version: 1, enabled: true}}]; return {account: accounts[0]};
    case 'GetProviderAccountSetup': return {setup: {provider: 'codex', runtime_ready: true, enabled: true, login_state: 'logged_out'}};
    default: throw Error(name);
  }
}};
const me = {username: 'new-user', access: {permission_system: 'open', allows: {ListProviders: true, RegisterProviderAccount: true}}};
w.canRegisterAccount = true;
w.canSetScheduler = false;
w.currentCanRegisterAccount = () => w.canRegisterAccount;
w.currentCanSetScheduler = () => w.canSetScheduler;
w.accountRegistrationPending = false;
w.accountDetails = null;
w.esc = x => String(x ?? '').replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('"', '&quot;');
const names = new Set(['accountManagementActions', 'accountEmptyState', 'openAccountRegistration', 'syncAccountDetailsButtons']);
for (const {node: n, source: text} of homeStatements()) {
  if (n.type === 'FunctionDeclaration' && names.delete(n.id.name)) vm.runInContext(text, context);
}
assert.equal(names.size, 0);
const mountHome = data => {
  const populated = Boolean(data.accounts?.length);
  w.document.querySelector('#home').innerHTML = w.accountManagementActions(populated) + (populated ? '' : w.accountEmptyState(data));
  w.syncAccountDetailsButtons();
  return w.document.querySelector('[data-account-register]');
};
const flush = async () => { for (let i = 0; i < 10; i++) await new Promise(r => setImmediate(r)); };
(async () => {
  const backend = new vm.SourceTextModule(source('portal_management_backend.js'), {context});
  const enrollment = new vm.SyntheticModule(['mountEnrollment'], function () {
    this.setExport('mountEnrollment', () => ({isBusy: () => false, hasUnsavedChanges: () => false, destroy() {}}));
  }, {context});
  for (const mod of [backend, enrollment]) { await mod.link(() => { throw Error('Unexpected dependency'); }); await mod.evaluate(); }
  for (const file of PORTAL_MANAGEMENT_SCRIPTS) new vm.Script(source(file), {importModuleDynamically: async specifier => {
    if (specifier === '/static/portal_user_enrollment.js') return enrollment;
    assert.equal(specifier, '/static/portal_management_backend.js'); return backend;
  }}).runInContext(context);
  const navigation = [];
  w.PortalNav = {openPermissionManagement: async options => {
    navigation.push(options);
    await new Promise(r => { release = r; });
    panel = await w.PortalManagementPanel.mount(w.document.querySelector('main'), {...options, me});
  }};
  assert.equal(mountHome({status: 'loading'}), null);
  w.canRegisterAccount = false; assert.equal(mountHome({accounts: []}), null);
  w.canRegisterAccount = true;
  const button = mountHome({accounts: []});
  assert.match(w.document.querySelector('#home').textContent, /尚未添加 AI 账号/);
  button.click(); button.click();
  assert.equal(navigation.length, 1); assert(button.disabled);
  assert.equal(navigation[0].view, 'backend'); assert.equal(navigation[0].createAccount, true);
  release(); await flush();
  assert(!button.disabled);
  const form = w.document.querySelector('dialog[aria-label="新增执行账号"] form');
  assert(form, 'Home entry opens the existing account registration form through Management');
  assert(!form.elements.max_concurrency, 'initial settings are assigned without advanced fields');
  form.elements.account_id.value = 'first-ai';
  form.dispatchEvent(new w.Event('submit', {bubbles: true, cancelable: true})); await flush();
  const registered = calls.filter(c => c.name === 'RegisterProviderAccount');
  assert.equal(registered.length, 1);
  assert.equal(registered[0].args.account_id, 'first-ai');
  assert.equal(registered[0].args.provider, 'codex');
  assert.equal(registered[0].args.max_concurrency, 1);
  assert(w.document.querySelector('[data-backend-setup-status]'), 'Saving continues to the existing account setup flow');
  panel.destroy(); panel = null;
  w.PortalNav.openPermissionManagement = async () => { throw Error('navigation failed'); };
  const retry = mountHome({accounts_error: 'unavailable'});
  assert.match(w.document.querySelector('#home').textContent, /Limit data unavailable/);
  retry.click(); await flush(); assert(!retry.disabled); assert.equal(notices.at(-1), 'navigation failed');
  // Usage failures on existing accounts do not remove management or registration.
  const existing = {accounts: [{account_id: 'first-ai', status: 'failed'}], accounts_refresh_error: 'usage unavailable'};
  assert(mountHome(existing));
  const manage = w.document.querySelector('[data-account-manage]');
  assert(manage); manage.click(); await flush(); assert.equal(notices.at(-1), 'navigation failed');
  const opened = [];
  w.PortalNav.openPermissionManagement = async options => opened.push(options);
  manage.click(); await flush();
  assert.equal(opened[0].view, 'backend'); assert.equal(opened[0].accountId, '');
  w.document.querySelector('[data-account-register]').click(); await flush();
  assert.equal(opened[1].createAccount, true);
  w.canRegisterAccount = false; w.canSetScheduler = true;
  assert.equal(mountHome(existing), null); assert(w.document.querySelector('[data-account-manage]'));
  w.canSetScheduler = false; mountHome(existing);
  assert(!w.document.querySelector('[data-account-manage]'));
  const denied = await backend.namespace.mount(w.document.querySelector('main'), {me: {access: {allows: {UpdateProviderAccount: true}}}});
  assert.throws(() => denied.openAccountEditor(), /没有新增/);
  assert(!w.document.querySelector('dialog')); denied.destroy();
  console.log('Home empty-account entry, Management dialog, registration, permissions and retry passed');
})().finally(() => { panel?.destroy(); dom.window.close(); }).catch(error => { console.error(error); process.exitCode = 1; });
