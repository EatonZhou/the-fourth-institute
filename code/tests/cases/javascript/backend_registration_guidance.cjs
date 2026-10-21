const assert = require('node:assert/strict');
const fs = require('node:fs'), vm = require('node:vm');
const {JSDOM} = require('jsdom');
const dom = new JSDOM('<main></main>', {url: 'http://portal.invalid/'});
const {window} = dom, calls = [];
let failRegistration = true, apis = [], createFailure = null;
window.PortalI18n = {t: text => text};
window.PortalUI = {createDialogController: ({modal, onClose}) => ({
  open: () => modal.setAttribute('open', ''), close: onClose, destroy() {},
})};
window.PortalInstructionClient = {execute: async (name, args) => {
  calls.push({name, args: JSON.parse(JSON.stringify(args))});
  switch (name) {
    case 'ListProviders': return {providers: [
      {provider: 'codex', title: 'Codex', kinds: ['subscription_login']},
      {provider: 'provider_api', title: 'Model API', kinds: ['api_profile']}]};
    case 'ListProviderAccounts': return {accounts: []};
    case 'ListBackendRoles': return {roles: []};
    case 'ListModelApis': return {apis};
    case 'CreateModelApi': if (createFailure) throw createFailure; apis = [{...args}]; return {api: apis[0]};
    case 'RegisterProviderAccount': if (failRegistration) throw Error('registration unavailable'); return {};
    case 'GetProviderAccountSetup': return {setup: {provider: 'provider_api', enabled: true, runtime_ready: true,
      login_state: 'not_applicable', default_model: 'example-model', models: ['example-model']}};
    default: throw Error(name);
  }
}};
const context = vm.createContext({window, document: window.document, Node: window.Node, AbortController, setTimeout, clearTimeout, setInterval, clearInterval, URL});
vm.runInContext(fs.readFileSync('management_portal/static/portal_management_backend.js', 'utf8').replaceAll('export ', ''), context);
const flush = async () => {for (let n = 0; n < 6; n++) await new Promise(resolve => setImmediate(resolve));};
const click = text => {const b = [...window.document.querySelectorAll('button')].find(el => el.textContent === text); assert(b && !b.disabled, text); b.click();};
(async () => {
  const panel = await context.mount(window.document.querySelector('main'), {me: {access: {permission_system: 'open'}}});
  click('接入订阅账号');
  let form = window.document.querySelector('dialog form');
  assert.equal(form.elements.kind.value, 'subscription_login');
  assert.match(form.textContent, /浏览器.*授权/);
  assert(!form.querySelector('[name=secret]'));
  click('关闭'); click('新增模型 API');
  form = window.document.querySelector('dialog form');
  assert.match(form.textContent, /API Key/);
  assert.match(form.textContent, /连接名称/);
  assert(form.elements.api_id.closest('details'), 'internal connection name is optional advanced configuration');
  assert(form.elements.register_account.closest('details'), 'users do not need to understand account linkage');
  assert.deepEqual([...form.elements].filter(e => e.tagName === 'INPUT' && !e.closest('details')).map(e => e.name),
    ['base_url', 'secret', 'default_model']);
  assert(!form.elements.secret_file.closest('details').open);
  form.elements.api_id.value = 'Invalid.Name';
  assert(!form.elements.api_id.checkValidity(), 'names follow the registration contract');
  form.elements.api_id.value = 'example-api';
  form.elements.provider.value = 'example';
  form.elements.base_url.value = 'https://api.example.com/v1';
  form.elements.default_model.value = 'example-model';
  form.elements.secret.value = 'fixture-key';
  const submit = () => form.dispatchEvent(new window.Event('submit', {bubbles: true, cancelable: true}));
  submit(); await flush();
  assert.equal(form.elements.secret.value, '');
  assert.match(form.querySelector('[role=alert]').textContent, /API 已保存/);
  assert.equal(calls.filter(c => c.name === 'CreateModelApi').length, 1);
  failRegistration = false; submit(); await flush();
  assert.equal(calls.filter(c => c.name === 'CreateModelApi').length, 1, 'retry must reuse the saved API');
  const registration = calls.filter(c => c.name === 'RegisterProviderAccount').at(-1).args;
  assert.equal(registration.provider, 'provider_api');
  assert.equal(registration.config.api_id, 'example-api');
  assert(!('secret' in registration));
  assert(window.document.querySelector('[data-backend-setup-status]'));
  assert(!calls.some(c => ['CompleteChat', 'CheckProviderAccount', 'OpenProviderAccountLogin'].includes(c.name)), 'registration does not spend quota or authorize automatically');
  click('关闭'); click('新增模型 API');
  form = window.document.querySelector('dialog form');
  form.elements.api_id.value = 'api-only'; form.elements.base_url.value = 'https://api.example.com/v1';
  form.elements.default_model.value = 'example-model'; form.elements.register_account.checked = false;
  const before = calls.filter(c => c.name === 'RegisterProviderAccount').length;
  form.dispatchEvent(new window.Event('submit', {bubbles: true, cancelable: true})); await flush();
  assert.equal(calls.filter(c => c.name === 'RegisterProviderAccount').length, before);
  click('编辑'); form = window.document.querySelector('dialog form');
  assert.equal(form.elements.api_id.readOnly, true);
  assert.equal(form.elements.secret.value, '');
  assert(!form.elements.register_account, 'editing an API must not register another account');
  click('关闭'); click('新增模型 API');
  form = window.document.querySelector('dialog form');
  form.elements.base_url.value = 'https://api.example.com/v1';
  form.elements.default_model.value = 'example-model';
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
  assert.equal(calls.filter(c => c.name === 'RegisterProviderAccount').at(-1).args.config.api_id, generated.api_id);
  assert(window.document.querySelector('[data-backend-setup-status]'));
  panel.destroy();
  const readonly = await context.mount(window.document.querySelector('main'), {me: {access: {allows: {}}}});
  for (const title of ['接入订阅账号', '新增模型 API']) {
    assert([...window.document.querySelectorAll('button')].filter(b => b.textContent === title).every(b => b.disabled));
  }
  readonly.destroy(); dom.window.close(); console.log('Backend registration guidance and partial-failure retry passed');
})().catch(error => {console.error(error); dom.window.close(); process.exitCode = 1;});
