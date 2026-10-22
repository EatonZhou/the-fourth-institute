const assert = require('node:assert/strict');
const fs = require('node:fs'), vm = require('node:vm');
const {JSDOM} = require('jsdom');
const dom = new JSDOM('<main></main>', {url: 'http://portal.invalid/'});
const {window} = dom, calls = [];
let apis = [];
let failRegistration = true;
const offered = protocols => ['openai_chat', ...Object.keys(protocols || {})];
window.PortalI18n = {t: (text, values = {}) => text.replace(/\{(p\d+)\}/g, (_, key) => values[key])};
window.PortalUI = {createDialogController: ({modal, onClose}) => ({
  open: () => modal.setAttribute('open', ''), close: onClose, destroy() {},
})};
window.PortalInstructionClient = {execute: async (name, args) => {
  calls.push({name, args: JSON.parse(JSON.stringify(args))});
  switch (name) {
    case 'ListProviders': return {providers: [
      {provider: 'codex', title: 'Codex', kinds: ['subscription_login', 'api_profile'], api_protocol: 'openai_responses'},
      {provider: 'claude', title: 'Claude Code', kinds: ['subscription_login', 'api_profile'], api_protocol: 'anthropic_messages'},
      {provider: 'opencode', title: 'OpenCode', kinds: ['service', 'api_profile'], api_protocol: 'openai_chat'},
      {provider: 'dsh', title: 'DeepSeek Harness', kinds: ['api_profile'], api_protocol: ''},
      {provider: 'provider_api', title: 'Model API', kinds: ['api_profile'], api_protocol: ''}]};
    case 'ListProviderAccounts': return {accounts: []};
    case 'ListBackendRoles': return {roles: []};
    case 'ListModelApis': return {apis};
    case 'CreateModelApi': apis = [{...args, harness_protocols: offered(args.protocols), version: 1}]; return {api: apis[0]};
    case 'UpdateModelApi': {
      const index = apis.findIndex(api => api.api_id === args.api_id);
      apis[index] = {...apis[index], ...args.changes, version: apis[index].version + 1};
      apis[index].harness_protocols = offered(apis[index].protocols);
      return {api: apis[index], version: apis[index].version};
    }
    case 'RegisterProviderAccount':
      if (failRegistration) throw Error('account name already registered');
      return {registration: {...args}};
    case 'GetProviderAccountSetup': return {setup: {provider: args.account_id, enabled: true, runtime_ready: true,
      login_state: 'not_applicable', default_model: 'gpt-test', models: ['gpt-test']}};
    default: throw Error(name);
  }
}};
const context = vm.createContext({window, document: window.document, Node: window.Node, AbortController, setTimeout, clearTimeout, setInterval, clearInterval, URL});
vm.runInContext(fs.readFileSync('management_portal/static/portal_management_backend.js', 'utf8').replaceAll('export ', ''), context);
const flush = async () => {for (let n = 0; n < 6; n++) await new Promise(resolve => setImmediate(resolve));};
const click = text => {const b = [...window.document.querySelectorAll('button')].find(el => el.textContent === text); assert(b && !b.disabled, text); b.click();};
const submit = form => form.dispatchEvent(new window.Event('submit', {bubbles: true, cancelable: true}));
const toggle = input => { input.checked = !input.checked; input.dispatchEvent(new window.Event('change')); };
const choices = select => [...select.options].map(option => option.value);
const last = name => calls.filter(c => c.name === name).at(-1).args;
(async () => {
  const panel = await context.mount(window.document.querySelector('main'), {me: {access: {permission_system: 'open'}}});
  click('新增模型 API');
  let form = window.document.querySelector('dialog form');
  form.elements.api_id.value = 'gateway'; form.elements.base_url.value = 'https://gateway.invalid/v1';
  form.elements.default_model.value = 'gpt-test';
  // Chat Completions alone admits direct calls and OpenCode.
  assert.deepEqual(choices(form.elements.harness), ['provider_api', 'opencode']);
  assert.equal(form.elements.harness.value, 'provider_api', 'direct calls stay the default');
  assert(form.elements.anthropic_base_url.parentElement.hidden);
  toggle(form.elements.protocol_openai_responses);
  assert.deepEqual(choices(form.elements.harness), ['provider_api', 'codex', 'opencode']);
  form.elements.harness.value = 'codex';
  submit(form); await flush();
  assert.deepEqual(last('CreateModelApi').protocols, {openai_responses: {}});
  assert(form.elements.api_id.closest('details').open, 'failed account registration exposes the retry controls');
  for (const name of ['protocol_openai_responses', 'protocol_anthropic_messages', 'anthropic_base_url', 'anthropic_auth']) {
    assert(form.elements[name].disabled, 'saved API protocols cannot change during account retry');
  }
  assert(!form.elements.account_id.disabled, 'account name remains editable for retry');
  failRegistration = false;
  submit(form); await flush();
  assert.equal(calls.filter(call => call.name === 'CreateModelApi').length, 1, 'retry reuses the saved API');
  assert.deepEqual(last('RegisterProviderAccount'), {account_id: 'gateway-codex', provider: 'codex', kind: 'api_profile',
    config: {api_id: 'gateway'}, max_concurrency: 1}, 'a harness account is named apart from the API');
  click('关闭');

  click('编辑'); form = window.document.querySelector('dialog form');
  assert.equal(form.elements.protocol_openai_responses.checked, true);
  assert.equal(form.elements.protocol_anthropic_messages.checked, false);
  assert(!form.elements.harness, 'editing an API registers no account');
  submit(form); await flush();
  assert(!('protocols' in last('UpdateModelApi').changes), 'unchanged protocols are not resent');
  // The page lists APIs again after the next save.
  apis.push({api_id: 'chat_only', base_url: 'https://chat.invalid/v1', default_model: 'm', harness_protocols: ['openai_chat'], version: 1});
  click('编辑'); form = window.document.querySelector('dialog form');
  toggle(form.elements.protocol_anthropic_messages);
  assert(!form.elements.anthropic_base_url.parentElement.hidden);
  form.elements.anthropic_auth.value = 'x-api-key';
  submit(form); await flush();
  assert.deepEqual(last('UpdateModelApi').changes.protocols,
    {openai_responses: {}, anthropic_messages: {auth: 'x-api-key'}});

  // A harness account lists only the APIs that speak its protocol.
  click('新增执行账号'); form = window.document.querySelector('dialog form');
  form.elements.provider.value = 'codex'; form.elements.provider.dispatchEvent(new window.Event('change'));
  form.elements.kind.value = 'api_profile'; form.elements.kind.dispatchEvent(new window.Event('change'));
  const listed = choices(form.elements.api_id).filter(Boolean);
  assert(listed.includes('gateway') && !listed.includes('chat_only'), listed.join());
  assert.match(form.textContent, /openai_responses/);
  form.elements.account_id.value = 'second-codex'; form.elements.api_id.value = 'gateway';
  form.elements.is_default.checked = true;
  submit(form); await flush();
  assert.deepEqual(last('RegisterProviderAccount').config, {api_id: 'gateway', is_default: true});

  panel.destroy(); dom.window.close(); console.log('API key harness choices passed');
})().catch(error => {console.error(error); dom.window.close(); process.exitCode = 1;});
