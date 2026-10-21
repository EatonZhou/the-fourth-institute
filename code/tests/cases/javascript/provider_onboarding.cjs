const assert = require('node:assert/strict');
const fs = require('node:fs'), vm = require('node:vm');
const {JSDOM} = require('jsdom');
const dom = new JSDOM('<main></main>', {url: 'http://portal.invalid/'});
const {window} = dom;
const calls = [];
let missing = false, fail = false, roles = [], finishCheck, holdCheck = false;
let queued = false, loginError = false, loginCancelled = false;
let holdDefault = false, finishDefault;
const timers = new Map(), intervals = new Map(); let timerId = 0;
const later = (fn, collection) => { collection.set(++timerId, fn); return timerId; };
const tick = () => { const [id, fn] = timers.entries().next().value; timers.delete(id); fn(); };
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
    case 'CheckProviderAccount':
      if (holdCheck) return new Promise(resolve => { finishCheck = resolve; });
      return {execution_id: 'execution_check'};
    case 'OpenProviderAccountLogin': return {session: {state: 'pending'}};
    case 'CancelProviderAccountLogin': return {};
    case 'GetProviderAccountLogin':
      if (loginError) throw new Error('暂时无法读取登录状态');
      return {session: {state: loginCancelled ? 'cancelled' : 'pending'}};
    case 'GetProviderAccountCheck': return {check: {execution_id: 'execution_check', status: queued ? 'queued' : 'completed', passed: !queued && !fail,
      model: 'model', message: queued ? '账号正在处理其他任务，测试排队中。' : fail ? '测试未通过' : '测试通过'}};
    case 'UpdateBackendRole':
      if (holdDefault) await new Promise(resolve => { finishDefault = resolve; });
      roles = [{role: 'default', account_id: args.account_id, version: (roles[0]?.version || 0) + 1}];
      return {role: roles[0]};
    case 'PrepareProviderRuntime': missing = false; return {preparation: {status: 'ready'}};
    default: throw new Error(name);
  }
}};
const context = vm.createContext({window, document: window.document, Node: window.Node, AbortController,
  setTimeout: fn => later(fn, timers), clearTimeout: id => timers.delete(id),
  setInterval: fn => later(fn, intervals), clearInterval: id => intervals.delete(id), URL, console});
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
  click('关闭');
  holdCheck = true; fail = false;
  click('接入与测试'); await flush();
  click('测试并完成'); await flush();
  const status = window.document.querySelector('[data-backend-setup-status]');
  assert.match(status.textContent, /正在启动本地后台/);
  assert.equal(status.getAttribute('aria-busy'), 'true');
  assert([...window.document.querySelectorAll('button')].find(el => el.textContent === '测试并完成').disabled);
  finishCheck({execution_id: 'execution_check'}); await flush(); await flush();
  assert.match(status.textContent, /测试通过/);
  assert.equal(status.getAttribute('aria-busy'), 'false');
  click('关闭');
  holdCheck = false; queued = true; calls.length = 0;
  click('接入与测试'); await flush();
  window.document.querySelector('input[name=use_default]').checked = true;
  click('测试并完成'); await flush();
  const queuedStatus = window.document.querySelector('[data-backend-setup-status]');
  assert.equal(queuedStatus.getAttribute('aria-busy'), 'true');
  for (const fn of intervals.values()) fn();
  assert.match(queuedStatus.textContent, /测试排队中.*已等待/);
  assert(!calls.some(call => call.name === 'UpdateBackendRole'));
  assert([...window.document.querySelectorAll('button')].find(el => el.textContent === '测试并完成').disabled);
  [...window.document.querySelectorAll('button')].find(el => el.textContent === '测试并完成').click();
  assert.equal(calls.filter(call => call.name === 'CheckProviderAccount').length, 1);
  queued = false; holdDefault = true; tick(); await flush();
  assert.equal(queuedStatus.getAttribute('aria-busy'), 'true');
  for (const fn of intervals.values()) fn();
  assert.match(queuedStatus.textContent, /正在设为默认 AI.*已等待/);
  finishDefault(); holdDefault = false; await flush(); await flush();
  assert.equal(queuedStatus.getAttribute('aria-busy'), 'false');
  assert.equal(intervals.size, 0);
  assert(calls.some(call => call.name === 'UpdateBackendRole'));
  click('关闭');
  // Closing during submission must not start a detached poll/default update.
  holdCheck = true; calls.length = 0;
  click('接入与测试'); await flush(); click('测试并完成'); await flush(); click('关闭');
  finishCheck({execution_id: 'execution_check'}); await flush(); await flush();
  assert(!calls.some(call => ['GetProviderAccountCheck', 'UpdateBackendRole'].includes(call.name)));
  assert.equal(intervals.size, 0);
  const login = context.openLogin('existing'); await flush();
  assert.match(window.document.querySelector('[data-backend-login-state]').textContent, /正在获取登录授权信息/);
  click('取消登录'); await flush();
  assert.match(window.document.querySelector('[data-backend-login-state]').textContent, /登录已取消/);
  login.close();
  const retryLogin = context.openLogin('existing'); await flush();
  loginError = true; tick(); await flush();
  for (const fn of intervals.values()) fn();
  assert.match(window.document.querySelector('[data-backend-login-state]').textContent, /暂时无法读取/);
  loginError = false; loginCancelled = true; tick(); await flush();
  assert.equal(timers.size, 0);
  assert.equal(intervals.size, 0);
  assert([...window.document.querySelectorAll('button')].find(el => el.textContent === '取消登录').disabled);
  retryLogin.close();
  console.log('provider onboarding UI passed');
})().catch(error => {console.error(error); process.exitCode = 1;});
