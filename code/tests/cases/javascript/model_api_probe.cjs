const assert = require('node:assert/strict');
const fs = require('node:fs'), vm = require('node:vm');
const {JSDOM} = require('jsdom');
const dom = new JSDOM('<main></main>', {url: 'http://portal.invalid/'});
const {window} = dom, calls = [];
let probe = {};
const apis = [{api_id: 'gateway', base_url: 'https://gateway.invalid/v1', default_model: 'gpt-test',
  harness_protocols: ['openai_chat', 'google_genai'],
  protocols: {google_genai: {base_url: 'https://native.invalid/prefix'}}, version: 1}];
const accounts = [
  {account_id: 'codex_one', provider: 'codex', kind: 'subscription_login', route_id: 'one', enabled: true,
   config: {is_default: true}, version: 2, login_state: 'logged_in'},
  {account_id: 'gateway-codex', provider: 'codex', kind: 'api_profile', route_id: 'gateway-codex', enabled: true,
   config: {api_id: 'gateway'}, version: 3, login_state: 'not_applicable'}];
window.PortalI18n = {t: (text, values = {}) => text.replace(/\{(p\d+)\}/g, (_, key) => values[key])};
window.PortalUI = {createDialogController: ({modal, onClose}) => ({
  open: () => modal.setAttribute('open', ''), close: onClose, destroy() {},
})};
window.PortalInstructionClient = {execute: async (name, args, options = {}) => {
  calls.push({name, args: JSON.parse(JSON.stringify(args)), expectedVersion: options.expectedVersion});
  switch (name) {
    case 'ListProviders': return {providers: [
      {provider: 'codex', title: 'Codex', kinds: ['subscription_login', 'api_profile'], api_protocol: 'openai_responses',
       api_upstream_protocols: ['openai_chat', 'openai_responses', 'anthropic_messages']},
      {provider: 'claude', title: 'Claude Code', kinds: ['subscription_login', 'api_profile'], api_protocol: 'anthropic_messages',
       api_upstream_protocols: ['openai_chat', 'openai_responses', 'anthropic_messages']},
      {provider: 'opencode', title: 'OpenCode', kinds: ['service', 'api_profile'], api_protocol: 'openai_chat'},
      {provider: 'provider_api', title: 'Model API', kinds: ['api_profile'], api_protocol: ''}]};
    case 'ListProviderAccounts': return {accounts: accounts.map(({version, login_state, ...registration}) => ({
      account_id: registration.account_id, login_state, registration: {...registration, version}}))};
    case 'ListBackendRoles': return {roles: []};
    case 'ListModelApis': return {apis};
    case 'ProbeModelApi': return {model: args.default_model, results: probe};
    default: throw Error(name);
  }
}};
const context = vm.createContext({window, document: window.document, Node: window.Node, AbortController, setTimeout, clearTimeout, setInterval, clearInterval, URL});
vm.runInContext(fs.readFileSync('management_portal/static/portal_management_backend.js', 'utf8').replaceAll('export ', ''), context);
const flush = async () => {for (let n = 0; n < 8; n++) await new Promise(resolve => setImmediate(resolve));};
const buttons = (root, text) => [...root.querySelectorAll('button')].filter(el => el.textContent === text);
const click = (text, root = window.document) => {const [b] = buttons(root, text); assert(b && !b.disabled, text); b.click();};
// Runners whose protocol the API lacks stay listed but disabled.
const choices = select => [...select.options].filter(option => !option.disabled).map(option => option.value);
const last = name => calls.filter(c => c.name === name).at(-1);
const row = id => window.document.querySelector(`[data-backend-account="${id}"]`);
const status = id => row(id).children[2].textContent;
(async () => {
  const panel = await context.mount(window.document.querySelector('main'), {me: {access: {permission_system: 'open'}}});
  await flush();

  // A new API is probed with the form's values; the result ticks the protocols and the harnesses follow.
  click('新增模型 API');
  let form = window.document.querySelector('dialog form');
  assert.equal(form.elements.protocol_google_genai.checked, false);
  assert.equal(form.elements.google_base_url.value, '', 'never guess a native endpoint from a Chat URL');
  assert(form.elements.google_base_url.parentElement.hidden);
  form.elements.protocol_google_genai.click();
  assert(!form.elements.google_base_url.parentElement.hidden);
  assert(form.elements.google_base_url.required);
  form.elements.protocol_google_genai.click();
  form.elements.base_url.value = 'https://new.invalid/v1'; form.querySelector('[data-backend-model-id]').value = 'gpt-new';
  form.elements.secret.value = 'sk-draft';
  probe = {openai_chat: {status: 'supported', http_status: 200}, openai_responses: {status: 'supported', http_status: 200},
           anthropic_messages: {status: 'supported', http_status: 200, auth: 'x-api-key'}};
  click('检测接口协议', form); await flush();
  assert.deepEqual(last('ProbeModelApi').args, {base_url: 'https://new.invalid/v1', default_model: 'gpt-new', secret: 'sk-draft'});
  assert.equal(form.elements.protocol_openai_responses.checked, true);
  assert.equal(form.elements.protocol_anthropic_messages.checked, true);
  assert.equal(form.elements.anthropic_auth.value, 'x-api-key');
  assert(!form.elements.anthropic_base_url.parentElement.hidden);
  assert.deepEqual(choices(form.elements.harness), ['provider_api', 'codex', 'claude', 'opencode']);
  assert.deepEqual([...form.querySelectorAll('.backendProbe li')].map(li => li.dataset.status),
                   ['supported', 'supported', 'supported']);
  // A protocol the endpoint does not serve is unticked; an uncertain outcome leaves the box as it was.
  probe = {openai_chat: {status: 'supported'}, openai_responses: {status: 'unsupported', http_status: 404, message: 'Not Found'},
           anthropic_messages: {status: 'rejected', http_status: 400, message: 'unknown model'}};
  click('检测接口协议', form); await flush();
  assert.equal(form.elements.protocol_openai_responses.checked, false);
  assert.equal(form.elements.protocol_anthropic_messages.checked, true);
  assert.deepEqual(choices(form.elements.harness), ['provider_api', 'codex', 'claude', 'opencode']);
  assert.match(form.querySelector('[data-protocol="openai_responses"]').textContent, /不支持（HTTP 404 · Not Found）/);
  click('关闭');

  // Editing probes the saved API: its stored key is used unless a new one is typed.
  click('编辑'); form = window.document.querySelector('dialog form');
  assert.equal(form.elements.protocol_google_genai.checked, true);
  assert.equal(form.elements.google_base_url.value, 'https://native.invalid/prefix');
  click('检测接口协议', form); await flush();
  assert.deepEqual(last('ProbeModelApi').args, {api_id: 'gateway', base_url: 'https://gateway.invalid/v1', default_model: 'gpt-test'});
  click('关闭');

  // Provider registration defaults do not claim the site's default AI role.
  assert.equal(status('codex_one'), '已启用 · 已登录');
  assert.equal(buttons(window.document, '设为默认路线').length, 0);
  assert.equal(status('gateway-codex'), '已启用');

  panel.destroy(); dom.window.close(); console.log('Model API probe and converted harness choices passed');
})().catch(error => {console.error(error); dom.window.close(); process.exitCode = 1;});
