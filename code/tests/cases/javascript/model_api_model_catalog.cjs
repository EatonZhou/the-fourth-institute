const assert = require('node:assert/strict');
const fs = require('node:fs'), vm = require('node:vm');
const {JSDOM} = require('jsdom');
const dom = new JSDOM('<main></main>', {url: 'http://portal.invalid/'});
const {window} = dom, calls = [];
let apis = [];
window.PortalI18n = {t: (text, values = {}) => text.replace(/\{(p\d+)\}/g, (_, key) => values[key])};
window.PortalUI = {createDialogController: ({modal, onClose}) => ({
  open: () => modal.setAttribute('open', ''), close: onClose, destroy() {},
})};
window.PortalInstructionClient = {execute: async (name, args) => {
  calls.push({name, args: JSON.parse(JSON.stringify(args))});
  switch (name) {
    case 'ListProviders': return {providers: [{provider: 'provider_api', title: 'Model API', kinds: ['api_profile']}]};
    case 'ListProviderAccounts': return {accounts: []};
    case 'ListBackendRoles': return {roles: []};
    case 'ListModelApis': return {apis};
    case 'CreateModelApi': apis = [{...args, version: 1}]; return {api: apis[0]};
    case 'UpdateModelApi': apis = [{...apis[0], ...args.changes, version: apis[0].version + 1}]; return {api: apis[0], version: apis[0].version};
    default: throw Error(name);
  }
}};
const context = vm.createContext({window, document: window.document, Node: window.Node, AbortController, setTimeout, clearTimeout, URL});
vm.runInContext(fs.readFileSync('management_portal/static/portal_management_backend.js', 'utf8').replaceAll('export ', ''), context);
const flush = async () => {for (let n = 0; n < 6; n++) await new Promise(resolve => setImmediate(resolve));};
const click = (text, root = window.document) => {
  const b = [...root.querySelectorAll('button')].find(el => el.textContent === text);
  assert(b && !b.disabled && !b.closest('fieldset:disabled'), text); b.click();
};
const submit = form => form.dispatchEvent(new window.Event('submit', {bubbles: true, cancelable: true}));
const count = name => calls.filter(c => c.name === name).length;
const last = name => calls.filter(c => c.name === name).at(-1).args;
const rows = () => [...window.document.querySelectorAll('[data-backend-model-row]')];
const modelId = row => row.querySelector('[data-backend-model-id]');
const defaultChoice = row => row.querySelector('[name=model_default]');
const enabled = row => row.querySelector('input[name^=model_enabled_]');
const setModel = (row, value) => {const input = modelId(row); input.value = value; input.dispatchEvent(new window.Event('input'));};
const chooseEffort = (row, value) => {
  const input = [...row.querySelectorAll('.backendModelEfforts input')].find(input => input.value === value);
  assert(input, value); input.click();
};
const customEffort = row => row.querySelector('input[name^=model_effort_]');
const edit = () => {click('编辑'); return window.document.querySelector('dialog form');};
const refresh = async () => {click('刷新', window.document.querySelector('[data-backend-section=apis]')); await flush();};
(async () => {
  const panel = await context.mount(window.document.querySelector('main'), {me: {access: {permission_system: 'open'}}});
  click('新增模型 API');
  let form = window.document.querySelector('dialog form');
  assert(!form.querySelector('textarea'), 'models are edited with ordinary controls');
  assert(!form.querySelector('[data-backend-model-catalog]').closest('details'), 'models are a primary setting');
  assert.equal(rows().length, 1);
  assert(defaultChoice(rows()[0]).checked);
  assert(rows()[0].querySelector('button').disabled, 'at least one model row remains');
  form.elements.api_id.value = 'example-api'; form.elements.base_url.value = 'https://api.example.com/v1';
  form.elements.register_account.checked = false;
  submit(form); await flush();
  assert.match(form.querySelector('[role=alert]').textContent, /请填写每个模型的标识/);
  assert.equal(count('CreateModelApi'), 0);
  setModel(rows()[0], '  example-model  ');
  chooseEffort(rows()[0], 'low'); chooseEffort(rows()[0], 'high');
  customEffort(rows()[0]).value = 'ultra';
  customEffort(rows()[0]).dispatchEvent(new window.KeyboardEvent('keydown', {key: 'Enter', cancelable: true}));
  assert.equal(count('CreateModelApi'), 0, 'Enter adds a reasoning effort without submitting the API');
  assert.equal(customEffort(rows()[0]).value, '');
  click('添加模型');
  assert.equal(window.document.activeElement, modelId(rows()[1]), 'the new model is ready to type');
  setModel(rows()[1], 'example-model');
  submit(form); await flush();
  assert.match(form.querySelector('[role=alert]').textContent, /模型标识不能重复/);
  assert.equal(count('CreateModelApi'), 0);
  setModel(rows()[1], 'example-mini');
  defaultChoice(rows()[1]).click();
  assert(!defaultChoice(rows()[0]).checked);
  submit(form); await flush();
  const catalog = [{id: 'example-model', reasoning_efforts: ['low', 'high', 'ultra']}, {id: 'example-mini'}];
  assert.deepEqual(last('CreateModelApi').models, catalog);
  assert.equal(last('CreateModelApi').default_model, 'example-mini');

  form = edit();
  assert.deepEqual(rows().map(row => modelId(row).value), ['example-model', 'example-mini']);
  assert(defaultChoice(rows()[1]).checked);
  assert(rows()[0].querySelector('input[value=ultra]').checked, 'custom reasoning efforts survive editing');
  submit(form); await flush();
  assert(!('models' in last('UpdateModelApi').changes), 'an unchanged list is not resent');

  form = edit(); defaultChoice(rows()[0]).click();
  submit(form); await flush();
  assert.equal(last('UpdateModelApi').changes.default_model, 'example-model');
  assert(!('models' in last('UpdateModelApi').changes), 'changing the default does not rewrite model metadata');

  form = edit();
  click('移除模型', rows()[0]);
  assert.equal(rows().length, 1);
  assert(defaultChoice(rows()[0]).checked, 'removing the default selects the remaining enabled model');
  assert.equal(window.document.activeElement, modelId(rows()[0]));
  submit(form); await flush();
  assert.deepEqual(last('UpdateModelApi').changes.models, [{id: 'example-mini'}]);
  assert.equal(last('UpdateModelApi').changes.default_model, 'example-mini');

  // Preserve undeclared UI fields, disabled models and effort order during edits.
  apis[0].models = [
    {id: 'example-mini', enabled: true, reasoning_efforts: ['high', 'low', 'custom'],
     context_window: 128000, supported_parameters: ['reasoning_effort'], aliases: ['small'], extension: {vendor: true}},
    {id: 'archived', enabled: false, supported_efforts: ['medium']},
  ];
  await refresh(); form = edit();
  assert(!enabled(rows()[1]).checked);
  assert(defaultChoice(rows()[1]).disabled);
  submit(form); await flush();
  assert(!('models' in last('UpdateModelApi').changes), 'opening and saving preserves metadata without normalization');
  form = edit();
  const metadata = apis[0].models[0];
  setModel(rows()[0], 'renamed-mini');
  chooseEffort(rows()[0], 'high');
  customEffort(rows()[0]).value = 'max'; // Include typed values when saving directly.
  submit(form); await flush();
  assert.deepEqual(last('UpdateModelApi').changes.models, [
    {...metadata, id: 'renamed-mini', reasoning_efforts: ['low', 'custom', 'max']},
    {id: 'archived', enabled: false, supported_efforts: ['medium']},
  ]);
  assert.equal(last('UpdateModelApi').changes.default_model, 'renamed-mini', 'the default follows a renamed row');

  form = edit();
  enabled(rows()[1]).click(); defaultChoice(rows()[1]).click();
  enabled(rows()[1]).click();
  assert(defaultChoice(rows()[0]).checked, 'disabling the default selects an enabled model');
  enabled(rows()[0]).click();
  const updates = count('UpdateModelApi'); submit(form); await flush();
  assert.match(form.querySelector('[role=alert]').textContent, /至少启用一个模型/);
  assert.equal(count('UpdateModelApi'), updates);
  enabled(rows()[1]).click();
  assert(defaultChoice(rows()[1]).checked, 'enabling a model restores a valid default');
  submit(form); await flush();
  assert.equal(last('UpdateModelApi').changes.default_model, 'archived');

  // Old APIs that only saved default_model still appear as one editable model.
  apis[0] = {api_id: 'legacy', base_url: 'https://api.example.com/v1', default_model: 'legacy-model', version: 1};
  await refresh(); form = edit();
  assert.equal(rows().length, 1); assert.equal(modelId(rows()[0]).value, 'legacy-model');
  submit(form); await flush();
  assert(!('models' in last('UpdateModelApi').changes), 'credential-only edits keep a legacy catalog undeclared');
  form = edit(); click('添加模型'); setModel(rows()[1], '<img src=x onerror=alert(1)>');
  submit(form); await flush();
  assert.deepEqual(last('UpdateModelApi').changes.models, [{id: 'legacy-model'}, {id: '<img src=x onerror=alert(1)>'}]);
  form = edit(); assert.equal(form.querySelector('img'), null, 'model names are rendered as text');

  panel.destroy(); dom.window.close(); console.log('Model API model catalog editing passed');
})().catch(error => {console.error(error); dom.window.close(); process.exitCode = 1;});
