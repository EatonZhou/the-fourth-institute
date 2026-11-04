const assert = require('node:assert/strict');
const fs = require('node:fs'), vm = require('node:vm');
const {JSDOM} = require('jsdom');
const {window} = new JSDOM('<main></main>', {url: 'http://portal.invalid/'});
const calls = [];
let scanFails = false;
let deferScan = false, finishScan;
window.PortalI18n = {t: text => text};
window.PortalUI = {createDialogController: ({modal, onClose}) => ({open: () => modal.setAttribute('open', ''), close: onClose, destroy() {}})};
const execute = async (name, args) => {
  calls.push({name, args});
  switch (name) {
    case 'ListProviders': return {providers: ['codex', 'claude'].map(provider => ({provider, kinds: ['subscription_login'], account_directory_prefix: `/host/accounts/${provider}/`}))};
    case 'ListProviderAccounts': return {accounts: []};
    case 'ListBackendRoles': return {roles: []};
    case 'ListModelApis': return {apis: []};
    case 'RegisterProviderAccount': return {};
    case 'DiscoverProviderAccountDirectories':
      if (scanFails) throw Error('fixture scan unavailable');
      if (deferScan) return new Promise(resolve => { finishScan = resolve; });
      return {candidates: [{path: `/existing/${args.provider}`, provider: args.provider, evidence: 'fixture marker', login_state: 'unverified'}], warnings: [], truncated: false};
    case 'BrowseProviderAccountDirectories': {
      const path = args.path || '/host';
      if (path === '/unreadable') throw Error('fixture directory unavailable');
      return {path, parent: '/host', directories: path === '/host' ? [{name: 'team', path: '/host/team'}] : [], roots: [{name: 'Host root', path: '/host'}], truncated: false};
    }
    case 'GetProviderAccountSetup': return {setup: {provider: 'codex', runtime_ready: true, login_state: 'logged_out', models: []}};
    default: throw Error(name);
  }
};
window.PortalInstructionClient = {execute};
window.PortalAPI = {requestResult: async url => {
  const params = new URL(url, 'http://portal.invalid').searchParams, action = params.get('action');
  const payload = await execute(action === 'browse' ? 'BrowseProviderAccountDirectories' : 'DiscoverProviderAccountDirectories',
    action === 'browse' ? (params.has('path') ? {path: params.get('path')} : {}) : {provider: params.get('provider')});
  return {response: {ok: true}, payload};
}};
const context = vm.createContext({window, document: window.document, Node: window.Node, AbortController, setTimeout, clearTimeout, setInterval, clearInterval, URL, URLSearchParams});
vm.runInContext(fs.readFileSync('management_portal/static/portal_management_backend.js', 'utf8').replaceAll('export ', ''), context);
const flush = async () => {for (let i = 0; i < 8; i++) await new Promise(resolve => setImmediate(resolve));};
(async () => {
 for (const reuseExisting of [false, true]) {
  calls.length = 0; scanFails = false;
  const panel = await context.mount(window.document.querySelector('main'), {me: {access: {permission_system: 'open'}}});
  await flush();
  [...window.document.querySelectorAll('button')].find(b => b.textContent === '接入订阅帐号').click();
  const form = window.document.querySelector('dialog form');
  const change = (name, value, event = 'input') => {form.elements[name].value = value; form.elements[name].dispatchEvent(new window.Event(event));};
  assert(!form.elements.account_dir.required);
  assert.equal(form.elements.account_dir.value, '/host/accounts/codex/new-account');
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
  if (reuseExisting) {
    const toggle = checked => {form.elements.reuse_existing.checked = checked; form.elements.reuse_existing.dispatchEvent(new window.Event('change'));};
    toggle(true); await flush();
    assert(form.elements.account_dir.required);
    assert.equal(form.elements.account_dir.value, '', 'candidates must not silently choose an account');
    const pick = text => {const el = [...window.document.querySelectorAll('dialog button')].find(b => b.textContent === text); assert(el, text); el.click();};
    pick('/existing/codex'); assert.equal(form.elements.account_dir.value, '/existing/codex');
    change('account_dir', '/manual/kept');
    pick('重新扫描'); await flush();
    assert.equal(form.elements.account_dir.value, '/manual/kept');
    scanFails = true; pick('重新扫描'); await flush();
    assert.match(form.textContent, /扫描失败/);
    assert.equal(form.elements.account_dir.value, '/manual/kept');
    pick('浏览'); await flush();
    const picker = window.document.querySelector('dialog[aria-label="选择文件夹"]');
    assert(picker);
    [...picker.querySelectorAll('button')].find(b => b.textContent === '主目录').click(); await flush();
    [...picker.querySelectorAll('[role=option]')].find(b => b.textContent === 'team').dispatchEvent(new window.MouseEvent('dblclick')); await flush();
    assert.equal(picker.querySelector('[name=browse_path]').value, '/host/team');
    picker.querySelector('[aria-label="上一级"]').click(); await flush();
    assert.equal(picker.querySelector('[name=browse_path]').value, '/host');
    const address = picker.querySelector('[name=browse_path]');
    picker.querySelector('[aria-label="编辑路径"]').click();
    address.value = '/unreadable'; address.dispatchEvent(new window.Event('input'));
    assert([...picker.querySelectorAll('button')].find(b => b.textContent === '选择文件夹').disabled);
    picker.querySelector('[aria-label="打开目录"]').click(); await flush();
    assert.match(picker.textContent, /fixture directory unavailable/);
    [...picker.querySelectorAll('button')].find(b => b.textContent === '主目录').click(); await flush();
    [...picker.querySelectorAll('button')].find(b => b.textContent === '选择文件夹').click(); await flush();
    assert.equal(form.elements.account_dir.value, '/host');
    pick('浏览'); await flush();
    const cancelledPicker = window.document.querySelector('dialog[aria-label="选择文件夹"]');
    [...cancelledPicker.querySelectorAll('button')].find(b => b.textContent === '关闭').click();
    assert.equal(form.elements.account_dir.value, '/host', 'cancel does not select another directory');
    scanFails = false; deferScan = true; pick('重新扫描'); await flush();
    toggle(false); assert.equal(form.elements.account_dir.value, '/host/accounts/codex/renamed');
    finishScan({candidates: [{path: '/late-result', evidence: 'stale'}]}); deferScan = false; await flush();
    assert(!form.textContent.includes('/late-result'), 'late scan results cannot enter another mode');
    toggle(true); await flush(); assert.equal(form.elements.account_dir.value, '/host');
  }
  form.dispatchEvent(new window.Event('submit', {bubbles: true, cancelable: true}));
  await flush();
  assert.equal(calls.find(c => c.name === 'RegisterProviderAccount').args.config.account_dir, reuseExisting ? '/host' : '/host/accounts/codex/renamed');
  assert.equal(calls.find(c => c.name === 'RegisterProviderAccount').args.reuse_existing, reuseExisting ? true : undefined);
  assert.equal(calls.find(c => c.name === 'GetProviderAccountSetup').args.account_id, 'renamed');
  assert(window.document.querySelector('[data-backend-setup-status]'), 'registration continues into assisted sign-in setup');
  assert(!calls.some(c => c.name === 'CheckProviderAccount'), 'no model call before sign-in');
  panel.destroy();
 }
 console.log('Subscription directory unit checks passed');
})().catch(error => {console.error(error); process.exitCode = 1;});
