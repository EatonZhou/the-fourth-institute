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
let provider = 'claude', emptyCatalog = false, holdSetup = false, finishSetup, loginSession;
let kimiRegion, holdLoginPoll = false, finishLoginPoll, failLoginStart = false;
const kimiOptions = () => ({regions: [{id:'mainland-cn', label:'中国区'}, {id:'global', label:'国际区'}], region: kimiRegion || 'mainland-cn'});
const kimiResult = () => ({login_options: kimiOptions(), session: kimiRegion ? {state:'awaiting_user',
  verification_code: kimiRegion, verification_url: `https://www.kimi.${kimiRegion === 'global' ? 'ai' : 'com'}/code/authorize_device`} : null});
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
      if (holdSetup) await new Promise(resolve => { finishSetup = resolve; });
      return {setup: {provider, enabled: true, runtime_ready: !missing,
        preparation: {status: missing ? preparation : 'ready', phase: 'dependency_install', started_at: new Date().toISOString(), message: preparation === 'failed' ? '环境准备失败' : ''},
        login_state: loginState, supports_tools: true, models: emptyCatalog ? [] : [{id: 'model'}], default_model: emptyCatalog ? '' : 'model'}};
    case 'CheckProviderAccount':
      if (holdCheck) return new Promise(resolve => { finishCheck = resolve; });
      return {execution_id: 'execution_check'};
    case 'OpenProviderAccountLogin':
      if (args.account_id === 'kimi_sub') {
        if (failLoginStart) throw Error('temporary login failure');
        kimiRegion = args.region; return kimiResult();
      }
      return {session: {state: 'pending'}};
    case 'UpdateProviderAccountLogin':
      assert.equal(args.authorization_code, 'http://localhost:1234/?code=synthetic&state=test');
      return {session: {state: 'succeeded'}};
    case 'CancelProviderAccountLogin': return {};
    case 'GetProviderAccountLogin':
      if (args.account_id === 'kimi_sub') {
        if (holdLoginPoll) return new Promise(resolve => { finishLoginPoll = resolve; });
        return kimiResult();
      }
      if (loginError) throw new Error('暂时无法读取登录状态');
      if (loginSession) return {session: loginSession};
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
  assert.match(window.document.querySelector('[data-backend-preparation-detail]').textContent, /下载并安装运行依赖/);
  assert.match(window.document.querySelector('[data-backend-setup-stages]').textContent, /准备运行环境.*进行中/);
  assert([...window.document.querySelectorAll('button')].find(el => el.textContent === '测试并完成').disabled);
  assert.equal(calls.filter(call => call.name === 'PrepareProviderRuntime').length, 1);
  const preparingStatus = window.document.querySelector('[data-backend-setup-status]');
  const preparingText = preparingStatus.textContent;
  const modelOption = window.document.querySelector('datalist option');
  const draft = window.document.querySelector('input[name=model]');
  draft.value = 'custom-model'; draft.focus();
  holdSetup = true; tick(); await flush();
  assert.equal(preparingStatus.textContent, preparingText, 'background polling must not replace the current status with a loading screen');
  assert.equal(preparingStatus.getAttribute('aria-busy'), 'false');
  finishSetup(); holdSetup = false; await flush();
  assert.equal(window.document.querySelector('datalist option'), modelOption, 'unchanged model options keep their DOM identity');
  assert.equal(draft.value, 'custom-model'); assert.equal(window.document.activeElement, draft);
  draft.value = 'model';
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
  missing = false; loginState = 'logged_out'; provider = 'codex'; emptyCatalog = true; calls.length = 0;
  click('接入与测试'); await flush();
  assert.deepEqual(setupActions().map(el => el.textContent), ['登录', '测试并完成']);
  assert(setupActions().find(el => el.textContent === '测试并完成').disabled);
  assert(window.document.querySelector('input[name=model]').parentElement.hidden);
  assert(!window.document.querySelector('input[name=model]').required);
  assert.match(window.document.querySelector('[data-backend-login-hint]').textContent, /设备码/);
  setupActions().find(el => el.textContent === '登录').click(); await flush();
  assert(calls.some(call => call.name === 'OpenProviderAccountLogin'));
  assert(!calls.some(call => call.name === 'CheckProviderAccount'));
  loginSession = {state: 'awaiting_user', verification_code: 'TEST-CODE', verification_url: 'https://example.com/device'};
  tick(); await flush();
  const deviceCode = window.document.querySelector('[data-backend-login-code]');
  const codeNode = deviceCode.firstChild;
  const authLink = deviceCode.nextElementSibling;
  const mutations = [];
  const observer = new window.MutationObserver(records => mutations.push(...records));
  observer.observe(authLink, {attributes: true});
  tick(); await flush(); observer.disconnect();
  assert.equal(deviceCode.firstChild, codeNode, 'polling must not disrupt copying the device code');
  assert.equal(mutations.length, 0, 'the authorization link must not hide and reappear on every poll');
  loginState = 'logged_in'; loginSession = {state: 'succeeded'}; tick(); await flush();
  assert(!window.document.querySelector('input[name=model]').parentElement.hidden);
  assert.equal(window.document.querySelector('input[name=model]').value, '');
  fail = false; click('测试并完成'); await flush(); await flush();
  assert.equal(calls.filter(call => call.name === 'CheckProviderAccount').at(-1).args.model, '', 'the server may discover the account default after startup');
  assert.equal(window.document.querySelector('dialog'), null);
  provider = 'claude'; emptyCatalog = false; loginSession = null;
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
  loginCancelled = false;
  const googleLogin = context.openLogin('antigravity_one'); await flush();
  loginSession = {state: 'awaiting_user', requires_code_input: true, input_kind: 'callback_url',
    input_hint: '使用 Google 登录后，若浏览器无法打开 localhost，请粘贴地址栏中的完整回调 URL。',
    verification_url: 'https://accounts.google.com/o/oauth2/auth?state=test'};
  tick(); await flush();
  const callbackInput = window.document.querySelector('input[name=authorization_code]');
  assert.equal(callbackInput.type, 'password');
  assert.match(window.document.getElementById(callbackInput.getAttribute('aria-describedby')).textContent, /完整回调 URL/);
  assert.match(callbackInput.parentElement.textContent, /浏览器返回的完整地址/);
  callbackInput.value = 'http://localhost:1234/?code=synthetic&state=test';
  click('提交回调地址'); await flush();
  assert.equal(callbackInput.value, '');
  assert(!('authorization_code' in calls.find(c => c.name === 'UpdateProviderAccountLogin').args));
  assert.match(window.document.querySelector('[data-backend-login-state]').textContent, /登录成功/);
  googleLogin.close();
  calls.length = 0;
  const kimiLogin = context.openLogin('kimi_sub'); await flush();
  const regionSelect = window.document.querySelector('[name=login_region]');
  assert.equal(regionSelect.value, 'mainland-cn');
  assert.deepEqual([...regionSelect.options].map(o => o.textContent), ['中国区', '国际区']);
  assert(!calls.some(c => c.name === 'OpenProviderAccountLogin'), 'choose region before native login');
  assert.equal(timers.size, 0, 'no polling before login');
  click('获取登录授权'); await flush();
  const loginLink = window.document.querySelector('dialog a');
  assert.equal(new URL(loginLink.href).hostname, 'www.kimi.com');
  assert.equal(calls.find(c => c.name === 'OpenProviderAccountLogin').args.region, 'mainland-cn');
  holdLoginPoll = true; tick(); await flush();
  const oldResult = kimiResult();
  regionSelect.value = 'global'; regionSelect.dispatchEvent(new window.Event('change'));
  assert(loginLink.hidden && !loginLink.hasAttribute('href'), 'hide old authorization when region changes');
  click('获取登录授权'); await flush();
  finishLoginPoll(oldResult); holdLoginPoll = false; await flush();
  assert.equal(new URL(loginLink.href).hostname, 'www.kimi.ai', 'late poll cannot restore the previous region');
  assert.equal(window.document.querySelector('[data-backend-login-code]').textContent, 'global');
  kimiLogin.close();
  const reopened = context.openLogin('kimi_sub'); await flush();
  const reopenedRegion = window.document.querySelector('[name=login_region]');
  assert.equal(reopenedRegion.value, 'global', 'reopen shows the existing flow region');
  reopenedRegion.value = 'mainland-cn'; reopenedRegion.dispatchEvent(new window.Event('change'));
  failLoginStart = true; click('获取登录授权'); await flush();
  assert.match(window.document.querySelector('[data-backend-login-state]').textContent, /temporary login failure/);
  assert(!reopenedRegion.disabled, 'failure permits retry');
  failLoginStart = false; click('获取登录授权'); await flush();
  assert.equal(new URL(window.document.querySelector('dialog a').href).hostname, 'www.kimi.com');
  click('取消登录'); await flush();
  click('获取登录授权'); await flush();
  assert.equal(window.document.querySelector('[data-backend-login-code]').textContent, 'mainland-cn');
  reopened.close();
  assert.equal(timers.size, 0); assert.equal(intervals.size, 0);
  console.log('provider onboarding UI passed');
})().catch(error => {console.error(error); process.exitCode = 1;});
