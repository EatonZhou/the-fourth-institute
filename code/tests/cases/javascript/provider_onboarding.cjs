const assert = require('node:assert/strict');
const fs = require('node:fs'), vm = require('node:vm');
const {JSDOM} = require('jsdom');
const dom = new JSDOM('<main></main>', {url: 'http://portal.invalid/'});
const {window} = dom;
const calls = [];
let missing = false, fail = false, roles = [], finishCheck, holdCheck = false;
let queued = false, loginError = false, loginCancelled = false;
let holdDefault = false, finishDefault;
let preparation = 'missing', prepareResult = 'ready', loginState = 'logged_in', setupError = false;
let holdPrepare = false, finishPrepare;
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
    case 'GetProviderAccountSetup':
      if (setupError) throw new Error('暂时无法读取接入状态');
      return {setup: {provider: 'claude', enabled: true, runtime_ready: !missing,
        preparation: {status: missing ? preparation : 'ready', message: preparation === 'failed' ? '环境准备失败' : ''},
        login_state: loginState, supports_tools: true, models: [{id: 'model'}], default_model: 'model'}};
    case 'CheckProviderAccount':
      if (holdCheck) return new Promise(resolve => { finishCheck = resolve; });
      return {execution_id: 'execution_check'};
    case 'OpenProviderAccountLogin': return {session: {state: 'pending'}};
    case 'CancelProviderAccountLogin': return {};
    case 'GetProviderAccountLogin':
      if (loginError) throw new Error('暂时无法读取登录状态');
      return {session: {state: loginCancelled ? 'cancelled' : 'pending'}};
    case 'GetProviderAccountCheck': return {check: {execution_id: 'execution_check', status: queued ? 'queued' : 'completed', passed: !queued && !fail,
      model: 'model', message: queued ? '账号正在处理其他任务，测试排队中。' : fail ? '测试未通过：接口拒绝了身份认证。' : '测试通过',
      next_step: fail ? '请在模型 API 中更换 API Key，然后重新测试。' : '',
      diagnostic: fail ? {reason: 'authentication', http_status: 401} : null,
      log: fail ? 'execution_check\nHTTP 401: [REDACTED]\n<script>not markup</script>' : ''}};
    case 'UpdateBackendRole':
      if (holdDefault) await new Promise(resolve => { finishDefault = resolve; });
      roles = [{role: 'default', account_id: args.account_id, version: (roles[0]?.version || 0) + 1}];
      return {role: roles[0]};
    case 'PrepareProviderRuntime':
      if (holdPrepare) return new Promise(resolve => { finishPrepare = resolve; });
      preparation = prepareResult; missing = prepareResult !== 'ready';
      return {preparation: {status: prepareResult}};
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
  const setupActions = () => [...window.document.querySelectorAll('dialog .backendActions button')].filter(el => !el.hidden);
  assert.deepEqual(setupActions().map(el => el.textContent), ['测试并完成']);
  assert([...window.document.querySelectorAll('button')].find(el => el.textContent === '显示详细日志').hidden);
  click('测试并完成'); await flush(); await flush();
  assert(calls.some(call => call.name === 'UpdateBackendRole' && call.args.verification_execution_id === 'execution_check'));
  assert(!calls.some(call => ['PrepareProviderRuntime', 'OpenProviderAccountLogin'].includes(call.name)));
  assert.equal(window.document.querySelector('dialog'), null, 'a passing check completes the dialog');
  click('接入与测试'); await flush();
  window.document.querySelector('input[name=use_default]').checked = true;
  click('测试并完成'); await flush(); await flush();
  assert.deepEqual(calls.filter(call => call.name === 'UpdateBackendRole').map(call => call.options.expectedVersion), [0, 1]);
  missing = true; fail = true; prepareResult = 'preparing'; calls.length = 0;
  click('接入与测试'); await flush();
  assert([...window.document.querySelectorAll('button')].find(el => el.textContent === '测试并完成').disabled);
  assert.equal(calls.filter(call => call.name === 'PrepareProviderRuntime').length, 1);
  tick(); await flush();
  assert.equal(calls.filter(call => call.name === 'PrepareProviderRuntime').length, 1, 'polling must not reinstall');
  missing = false; tick(); await flush();
  click('测试并完成'); await flush(); await flush();
  assert(!calls.some(call => call.name === 'UpdateBackendRole'));
  assert.match(window.document.querySelector('[data-backend-setup-status]').textContent, /未通过/);
  assert.match(window.document.querySelector('[data-backend-check-next-step]').textContent, /更换 API Key/);
  const checkDetail = window.document.querySelector('[data-backend-check-detail]');
  assert.match(checkDetail.textContent, /execution_check/);
  assert.match(checkDetail.textContent, /401/);
  assert.match(checkDetail.textContent, /authentication/);
  assert.equal(window.document.querySelector('input[name=model]').value, 'model');
  const log = window.document.querySelector('[data-backend-check-log]');
  assert(log.hidden);
  click('显示详细日志');
  assert(!log.hidden && log.readOnly);
  assert.match(log.value, /HTTP 401/);
  assert.match(log.value, /<script>not markup<\/script>/);
  assert.equal(window.document.querySelector('dialog script'), null);
  assert.equal([...window.document.querySelectorAll('button')].find(el => el.textContent === '收起详细日志').getAttribute('aria-expanded'), 'true');
  click('收起详细日志'); assert(log.hidden);
  // A new attempt must not present a stale failure log while polling.
  queued = true; click('测试并完成'); await flush();
  assert(log.hidden && !log.value);
  assert([...window.document.querySelectorAll('button')].find(el => el.textContent === '显示详细日志').hidden);
  queued = false; tick(); await flush(); await flush();
  click('关闭');
  // Failures expose recovery only when needed, rather than permanent manual steps.
  missing = true; preparation = 'missing'; prepareResult = 'failed'; calls.length = 0;
  click('接入与测试'); await flush();
  assert.match(window.document.querySelector('[data-backend-setup-status]').textContent, /准备失败/);
  assert.equal(timers.size, 0, 'failed preparation must not retry in a loop');
  click('重试'); await flush();
  assert.equal(calls.filter(call => call.name === 'PrepareProviderRuntime').length, 2);
  prepareResult = 'ready'; click('重试'); await flush();
  assert.deepEqual(setupActions().map(el => el.textContent), ['测试并完成']);
  click('关闭');
  setupError = true; click('接入与测试'); await flush();
  assert.match(window.document.querySelector('[data-backend-setup-status]').textContent, /无法读取/);
  setupError = false; click('重试'); await flush(); click('关闭');
  missing = true; preparation = 'missing'; prepareResult = 'busy'; calls.length = 0;
  click('接入与测试'); await flush();
  assert.equal(timers.size, 1, 'runtime update locks are retried automatically');
  prepareResult = 'ready'; tick(); await flush();
  assert.equal(calls.filter(call => call.name === 'PrepareProviderRuntime').length, 2);
  assert.deepEqual(setupActions().map(el => el.textContent), ['测试并完成']);
  click('关闭');
  // Observe an existing preparation, without submitting another one, and stop polling on close.
  missing = true; preparation = 'pending'; calls.length = 0;
  click('接入与测试'); await flush();
  assert(!calls.some(call => call.name === 'PrepareProviderRuntime'));
  assert.equal(timers.size, 1); click('关闭'); assert.equal(timers.size, 0);
  preparation = 'missing'; holdPrepare = true; calls.length = 0;
  click('接入与测试'); await flush(); click('关闭');
  finishPrepare({preparation: {status: 'preparing'}}); await flush();
  assert.equal(calls.filter(call => call.name === 'GetProviderAccountSetup').length, 1);
  assert.equal(timers.size, 0); assert.equal(intervals.size, 0);
  holdPrepare = false;
  missing = false; loginState = 'logged_out';
  click('接入与测试'); await flush();
  assert.deepEqual(setupActions().map(el => el.textContent), ['登录', '测试并完成']);
  assert(setupActions().find(el => el.textContent === '测试并完成').disabled);
  click('关闭'); loginState = 'logged_in';
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
  assert.equal(window.document.querySelector('dialog'), null);
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
  assert.equal(window.document.querySelector('dialog'), null);
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
