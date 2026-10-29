const assert = require('node:assert/strict');
const fs = require('node:fs'), vm = require('node:vm');
const path = require('node:path');
const {JSDOM} = require('jsdom');
const sourcePath = process.argv[2] || path.join(__dirname, '../../../management_portal/static/portal_management_backend.js');
const source = fs.readFileSync(sourcePath, 'utf8').replaceAll('export ', '');
const flush = () => new Promise(resolve => setImmediate(resolve));
const scenario = process.argv[3] || 'register';
const registering = ['register', 'local-register', 'local-other'].includes(scenario);
const local = scenario.startsWith('local-');
// Registration picks the default model itself (the local login's, else sonnet);
// repair edits the catalog explicitly.
const model = scenario === 'register' ? 'sonnet' : 'opus-5.5';

(async () => {
  const dom = new JSDOM('<main></main>', {url: 'http://portal.invalid/'});
  const {window} = dom, calls = [];
  let accounts = registering ? [] : [{account_id: 'cc', provider: 'claude',
    kind: 'subscription_login', enabled: true, registration_version: 7, config: {models: []}}];
  window.PortalI18n = {t: text => text};
  window.PortalUI = {createDialogController: ({modal, onClose}) => ({
    open: () => modal.setAttribute('open', ''), close: onClose, destroy() {},
  })};
  window.PortalInstructionClient = {execute: async (name, args, options) => {
    calls.push({name, args: structuredClone(args), options});
    if (name === 'ListProviders') return {providers: [{provider: 'claude', kinds: ['subscription_login'],
      ...(local ? {local_login: {available: true, default_model: 'opus-5.5'}} : {})}]};
    if (name === 'ListProviderAccounts') return {accounts};
    if (name === 'ListBackendRoles') return {roles: [{role: 'default', account_id: 'another'}]};
    if (name === 'ListModelApis') return {apis: []};
    if (name === 'RegisterProviderAccount') {
      accounts.push({...args, enabled: true, registration_version: 1}); return {registration: accounts.at(-1)};
    }
    if (name === 'UpdateProviderAccountRegistration') {
      assert.equal(options.expectedVersion, 7);
      if (scenario === 'conflict') throw new Error('账号登记版本已改变');
      accounts[0] = {...accounts[0], ...args.changes, registration_version: 8}; return {registration: accounts[0]};
    }
    if (name === 'GetProviderAccountSetup') return {setup: {provider: 'claude', enabled: true,
      runtime_ready: true, preparation: {status: 'ready'}, login_state: 'logged_in', supports_tools: true,
      models: accounts[0].config.models || [], default_model: accounts[0].config.default_model || ''}};
    if (name === 'CheckProviderAccount') {
      const config = accounts[0].config;
      assert.equal(config.default_model, model);
      assert(config.models.some(row => row.id === config.default_model && row.enabled !== false));
      return {execution_id: 'check-fixture'};
    }
    if (name === 'GetProviderAccountCheck') return {check: {passed: true, model, status: 'completed'}};
    throw new Error(name);
  }};
  const context = vm.createContext({window, document: window.document, Node: window.Node, AbortController,
    setTimeout: () => 1, clearTimeout() {}, setInterval: () => 1, clearInterval() {}, URL, console});
  vm.runInContext(source, context);
  const panel = await context.mount(window.document.querySelector('main'), {me: {access: {permission_system: 'open'}}});
  const click = label => {
    const scope = window.document.querySelector('dialog') || window.document;
    const button = [...scope.querySelectorAll('button')].find(b => b.textContent === label && !b.hidden);
    assert(button && !button.disabled, `available action: ${label}`); button.click();
  };
  if (registering) click('接入订阅账号');
  else { click('接入与测试'); await flush(); click('编辑模型目录'); }
  const form = window.document.querySelector('dialog form');
  const submit = () => form.dispatchEvent(new window.Event('submit', {bubbles: true, cancelable: true}));
  const mutations = () => calls.filter(c => ['RegisterProviderAccount', 'UpdateProviderAccountRegistration'].includes(c.name));
  assert(form.elements.default_model, 'Claude registration/repair must collect the default model');
  if (registering) {
    form.elements.account_id.value = 'cc';
    // The default model is chosen automatically and the catalog is edited after onboarding.
    assert.equal(form.elements.default_model.readOnly, true);
    assert.equal(form.elements.default_model.value, model);
    assert(!form.elements.models, 'registration does not ask for a model catalog');
    if (local) {
      assert.equal(form.elements.reuse_local_login.checked, true);
      if (scenario === 'local-other') form.elements.reuse_local_login.checked = false;
    } else assert(!form.elements.reuse_local_login);
    submit(); await flush(); await flush();
  } else {
    assert(!form.elements.reuse_local_login);
    form.elements.default_model.value = model;
    for (const invalid of ['{}', '[{"id":"opus-5.5","enabled":false}]', '[{"id":"other"}]']) {
      form.elements.models.value = invalid; submit(); await flush();
      assert.equal(mutations().length, 0, 'invalid catalog must not be saved');
      assert(form.querySelector('[role=alert]').textContent);
    }
    form.elements.models.value = '[]'; submit(); await flush(); await flush();
  }
  if (scenario === 'conflict') {
    assert.match(form.querySelector('[role=alert]').textContent, /版本已改变/);
    assert.equal(accounts[0].registration_version, 7);
    assert.deepEqual(accounts[0].config, {models: []});
    assert(!calls.some(c => c.name === 'CheckProviderAccount'));
    panel.destroy(); dom.window.close();
    console.log(JSON.stringify({scenario, passed: true})); return;
  }
  const mutation = mutations().at(-1);
  assert(mutation);
  const config = mutation.args.config || mutation.args.changes.config;
  assert.equal(config.default_model, model);
  assert.deepEqual(config.models, [{id: model}]);
  if (local) assert.equal(config.reuse_local_login, scenario === 'local-register');
  assert.equal(mutations().length, 1);
  assert(window.document.querySelector('[data-backend-setup-status]'));
  assert(!calls.some(c => c.name === 'CheckProviderAccount'), 'saving config does not start a paid check');
  click('测试并完成'); await flush(); await flush();
  assert(calls.some(c => c.name === 'CheckProviderAccount' && c.args.model === model));
  assert(!calls.some(c => c.name === 'UpdateBackendRole'), 'unchecked default role is untouched');
  assert(!calls.some(c => ['DeleteProviderAccount','RestartSession'].includes(c.name)));
  panel.destroy(); dom.window.close();
  console.log(JSON.stringify({scenario, passed: true, config, calls: calls.map(c => c.name)}));
})().catch(error => { console.error(error); process.exitCode = 1; });
