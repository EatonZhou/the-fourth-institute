const assert = require('node:assert/strict');
const fs = require('node:fs'), vm = require('node:vm');
const {JSDOM} = require('jsdom');
const dom = new JSDOM('<main></main>', {url: 'http://portal.invalid/'});
const {window} = dom, calls = [];
let failRoutes = true, apis = [], accounts = [], createFailure = null, checkFails = true;
let roles = [{role: 'default', account_id: null, version: 1}];
const provision = api => {
  let account = accounts.find(a => a.account_id === api.api_id);
  if (!account) {
    account = {account_id: api.api_id, provider: 'provider_api', kind: 'api_profile',
      registration: {version: 1, enabled: true, config: {api_id: api.api_id}}};
    accounts.push(account);
  }
  return {api, accounts: [account]};
};
window.PortalI18n = {t: text => text};
window.PortalUI = {createDialogController: ({modal, onClose}) => ({
  open: () => modal.setAttribute('open', ''), close: onClose, destroy() {},
})};
window.PortalInstructionClient = {execute: async (name, args, options = {}) => {
  assert(args && typeof args === 'object' && !Array.isArray(args), `${name} arguments must be an object`);
  calls.push({name, args: JSON.parse(JSON.stringify(args)), expectedVersion: options.expectedVersion});
  switch (name) {
    case 'ListProviders': return {providers: [
      {provider: 'codex', title: 'Codex', kinds: ['subscription_login']},
      {provider: 'claude', title: 'Claude Code', kinds: ['subscription_login']},
      {provider: 'provider_api', title: 'Model API', kinds: ['api_profile']}]};
    case 'ListProviderAccounts': return {accounts};
    case 'ListBackendRoles': return {roles};
    case 'ListModelApis': return {apis};
    case 'CreateModelApi': {
      if (createFailure) throw createFailure;
      apis.push({...args});
      if (failRoutes) throw Object.assign(new Error('route preparation failed'), {details: {reason: 'model_api.routes_incomplete', api_id: args.api_id}});
      return provision(apis.at(-1));
    }
    case 'UpdateModelApi': {
      const api = apis.find(a => a.api_id === args.api_id); Object.assign(api, args.changes); return provision(api);
    }
    case 'RegisterProviderAccount': accounts.push({...args, registration: {version: 1, enabled: true, config: args.config}}); return {};
    case 'UpdateBackendRole': roles = [{role: 'default', account_id: args.account_id, model: args.model, version: 2}]; return {role: roles[0]};
    case 'GetProviderAccountSetup': return {setup: {provider: 'provider_api', enabled: true, runtime_ready: true,
      login_state: 'not_applicable', default_model: 'example-model', models: ['example-model']}};
    case 'CheckProviderAccount': return {execution_id: 'execution_api_check'};
    case 'GetProviderAccountCheck': return {check: {status: 'completed', passed: !checkFails, model: 'example-model', message: checkFails ? 'HTTP 401' : '测试通过', next_step: '请更换 API Key'}};
    default: throw Error(name);
  }
}};
const context = vm.createContext({window, document: window.document, Node: window.Node, AbortController, setTimeout, clearTimeout, setInterval, clearInterval, URL});
vm.runInContext(fs.readFileSync('management_portal/static/portal_management_backend.js', 'utf8').replaceAll('export ', ''), context);
const flush = async () => {for (let n = 0; n < 6; n++) await new Promise(resolve => setImmediate(resolve));};
const click = text => {const b = [...window.document.querySelectorAll('button')].find(el => el.textContent === text); assert(b && !b.disabled, text); b.click();};
(async () => {
  const panel = await context.mount(window.document.querySelector('main'), {me: {access: {permission_system: 'open'}}});
  const introduction = window.document.querySelector('.backendIntroduction');
  assert.equal(introduction.querySelector('h2').textContent, '添加 AI 后端');
  assert.deepEqual([...introduction.querySelectorAll('button')].map(b => b.textContent), ['接入订阅帐号', '新增 API Key']);
  assert(!introduction.querySelector('p, .backendChoice'), 'the introduction is a compact action row');
  await flush();
  assert(window.document.querySelector('[data-backend-default-missing]'), 'missing defaults are visible beside connected accounts');
  click('接入订阅帐号');
  let form = window.document.querySelector('dialog form');
  assert.equal(form.elements.kind.value, 'subscription_login');
  assert.match(form.textContent, /添加可用的 Agent 后端/);
  assert(!form.querySelector('details'));
  assert(!form.querySelector('[name=secret]'));
  click('关闭'); click('新增执行账号');
  form = window.document.querySelector('dialog form');
  form.elements.account_id.value = 'example-api';
  form.elements.provider.value = 'provider_api';
  form.elements.provider.dispatchEvent(new window.Event('change'));
  assert(form.elements.kind.parentElement.hidden, 'a single access method does not need a dropdown');
  assert(!form.elements.api_id, 'an empty API registry must offer creation rather than an empty select');
  assert(form.querySelector('[type=submit]').disabled);
  click('新建模型 API 并接入');
  form = window.document.querySelector('dialog form');
  assert.match(form.textContent, /API Key/);
  assert(!form.elements.account_id, 'API creation uses server-provisioned identities');
  assert.match(form.textContent, /连接名称/);
  assert(form.elements.api_id.closest('details'), 'internal connection name is optional advanced configuration');
  assert(form.elements.register_account.closest('details'), 'users do not need to understand account linkage');
  assert.deepEqual([...form.elements].filter(e => e.tagName === 'INPUT' && !e.closest('details')).map(e => e.name),
    ['base_url', 'secret', 'model_default', 'model_id_1']);
  assert(!form.elements.secret_file.closest('details').open);
  form.elements.api_id.value = 'Invalid.Name';
  assert(!form.elements.api_id.checkValidity(), 'names follow the registration contract');
  form.elements.api_id.value = 'example-api';
  form.elements.provider.value = 'example';
  form.elements.base_url.value = 'https://api.example.com/v1';
  form.querySelector('[data-backend-model-id]').value = 'example-model';
  form.elements.secret.value = 'fixture-key';
  const submit = () => form.dispatchEvent(new window.Event('submit', {bubbles: true, cancelable: true}));
  submit(); await flush();
  assert.equal(form.elements.secret.value, '');
  assert.match(form.querySelector('[role=alert]').textContent, /接口已保存.*重试保存/);
  assert.equal(calls.filter(c => c.name === 'CreateModelApi').length, 1);
  failRoutes = false; submit(); await flush();
  assert.equal(calls.filter(c => c.name === 'CreateModelApi').length, 1, 'retry must reuse the saved API');
  assert(!calls.some(c => c.name === 'RegisterProviderAccount'), 'route retry creates no duplicate account');
  assert(window.document.querySelector('[data-backend-setup-status]'));
  assert(!calls.some(c => ['CompleteChat', 'CheckProviderAccount', 'OpenProviderAccountLogin'].includes(c.name)), 'registration does not spend quota or authorize automatically');
  click('测试并完成'); await flush();
  assert.match(window.document.querySelector('[data-backend-setup-status]').textContent, /401/);
  const beforeRepair = calls.filter(c => ['CreateModelApi', 'RegisterProviderAccount'].includes(c.name)).length;
  const repairButton = [...window.document.querySelectorAll('dialog button')].find(b => b.textContent === '编辑连接');
  assert(repairButton && !repairButton.hidden && !repairButton.disabled); repairButton.click();
  form = window.document.querySelector('dialog form');
  assert.equal(form.elements.api_id.value, 'example-api');
  assert.equal(form.elements.secret.value, '');
  form.elements.secret.value = 'replacement-fixture-key'; submit(); await flush();
  assert.equal(calls.filter(c => c.name === 'UpdateModelApi').at(-1).args.changes.secret, 'replacement-fixture-key');
  assert.equal(calls.filter(c => ['CreateModelApi', 'RegisterProviderAccount'].includes(c.name)).length, beforeRepair, 'repair edits the same connection without duplicate accounts');
  assert(window.document.querySelector('[data-backend-setup-status]'), 'saving an existing connection returns to its test flow');
  assert(!calls.some(c => c.name === 'UpdateBackendRole'), 'failed checks cannot enable the default AI');
  checkFails = false;
  click('测试并完成'); await flush(); assert(!window.document.querySelector('dialog'));
  const defaultUpdate = calls.find(c => c.name === 'UpdateBackendRole');
  assert.deepEqual(defaultUpdate.args, {backend_role: 'default', account_id: 'example-api', model: 'example-model', verification_execution_id: 'execution_api_check'});
  assert.equal(defaultUpdate.expectedVersion, 1);
  assert(!window.document.querySelector('[data-backend-default-missing]'));
  assert.match(window.document.querySelector('[data-backend-account="example-api"]').textContent, /默认 AI/);
  const tableEdit = [...window.document.querySelectorAll('[data-backend-api-connection] > header button')].find(b => b.textContent === '编辑连接');
  assert(tableEdit && !tableEdit.disabled); tableEdit.click();
  form = window.document.querySelector('dialog form'); assert.equal(form.elements.api_id.value, 'example-api');
  click('关闭'); click('新增模型 API');
  form = window.document.querySelector('dialog form');
  form.elements.api_id.value = 'api-only'; form.elements.base_url.value = 'https://api.example.com/v1';
  form.querySelector('[data-backend-model-id]').value = 'example-model'; form.elements.register_account.checked = false;
  const before = calls.filter(c => c.name === 'RegisterProviderAccount').length;
  form.dispatchEvent(new window.Event('submit', {bubbles: true, cancelable: true})); await flush();
  assert.equal(calls.filter(c => c.name === 'RegisterProviderAccount').length, before);
  const savedOnly = window.document.querySelector('[data-backend-account="api-only"]');
  assert(savedOnly, 'saving without continuing setup still refreshes the server-provisioned accounts');
  const makeDefault = [...savedOnly.querySelectorAll('button')].find(b => b.textContent === '设为默认 AI');
  assert(makeDefault && !makeDefault.disabled);
  const defaultUpdates = calls.filter(c => c.name === 'UpdateBackendRole').length;
  makeDefault.click(); await flush();
  assert(window.document.querySelector('[name=use_default]').checked, 'the action selects this account even when another default exists');
  assert.equal(calls.filter(c => c.name === 'UpdateBackendRole').length, defaultUpdates, 'opening default setup does not skip the real check');
  click('关闭');
  click('编辑'); form = window.document.querySelector('dialog form');
  assert.equal(form.elements.api_id.readOnly, true);
  assert.equal(form.elements.secret.value, '');
  assert(!form.elements.register_account, 'editing an API must not register another account');
  click('关闭'); click('新增模型 API');
  form = window.document.querySelector('dialog form');
  form.elements.base_url.value = 'https://api.example.com/v1';
  form.querySelector('[data-backend-model-id]').value = 'example-model';
  for (const [error, expected] of [
    [{code: 'instruction.conflict'}, /名称.*已存在/],
    [{code: 'permission.denied'}, /权限/],
    [{code: 'instruction.failed', details: {reason: 'model_api.secret_write_failed'}}, /服务器.*保存密钥/],
    [new TypeError('fetch failed fixture-key'), /网络/],
    [{code: 'instruction.failed'}, /保存.*失败/],
  ]) {
    createFailure = Object.assign(new Error('fixture-key must not appear'), error);
    if (error instanceof TypeError) createFailure = error;
    form.elements.secret.value = 'fixture-key';
    submit(); await flush();
    const message = form.querySelector('[role=alert]').textContent;
    assert.match(message, expected);
    assert(!message.includes('fixture-key'));
  }
  createFailure = null;
  form.elements.api_id.value = ''; // users need only the three provider-supplied values
  form.elements.secret.value = 'fixture-key';
  submit(); await flush();
  const generated = calls.filter(c => c.name === 'CreateModelApi').at(-1).args;
  assert.match(generated.api_id, /^api-[a-z0-9-]+$/);
  assert.equal(calls.filter(c => c.name === 'GetProviderAccountSetup').at(-1).args.account_id, generated.api_id);
  assert(window.document.querySelector('[data-backend-setup-status]'));
  click('关闭'); click('接入订阅帐号');
  form = window.document.querySelector('dialog form');
  form.elements.provider.value = 'claude';
  form.elements.provider.dispatchEvent(new window.Event('change'));
  assert.equal(form.elements.default_model.value, 'sonnet');
  assert(form.elements.default_model.readOnly);
  assert(!form.querySelector('details'));
  form.elements.account_id.value = 'native-claude';
  submit(); await flush();
  const native = calls.filter(c => c.name === 'RegisterProviderAccount').at(-1).args;
  assert.equal(native.config.default_model, 'sonnet');
  assert.deepEqual(native.config.models, [{id: 'sonnet'}]);
  assert.equal(native.max_concurrency, 1);
  const rowActions = [...window.document.querySelectorAll('[data-backend-account] button')].map(b => b.textContent);
  for (const removed of ['接入与测试', '停用', '启用', '编辑初始并发']) assert(!rowActions.includes(removed));
  assert(rowActions.includes('登录') && rowActions.includes('删除'));
  panel.destroy();
  const readonly = await context.mount(window.document.querySelector('main'), {me: {access: {allows: {}}}});
  for (const title of ['接入订阅帐号', '新增 API Key', '新增模型 API']) {
    assert([...window.document.querySelectorAll('button')].filter(b => b.textContent === title).every(b => b.disabled));
  }
  readonly.destroy(); dom.window.close(); console.log('Backend registration guidance and partial-failure retry passed');
})().catch(error => {console.error(error); dom.window.close(); process.exitCode = 1;});
