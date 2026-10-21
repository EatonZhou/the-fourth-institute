const assert = require('node:assert/strict');
const fs = require('node:fs'), vm = require('node:vm');
const {JSDOM} = require('jsdom');
const dom = new JSDOM('<main></main>', {url: 'http://portal.invalid/'});
const {window} = dom, calls = [];
let apis = [];
window.PortalI18n = {t: text => text};
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
const click = text => {const b = [...window.document.querySelectorAll('button')].find(el => el.textContent === text); assert(b && !b.disabled, text); b.click();};
const submit = form => form.dispatchEvent(new window.Event('submit', {bubbles: true, cancelable: true}));
const count = name => calls.filter(c => c.name === name).length;
const catalog = [{id: 'example-model', reasoning_efforts: ['low', 'medium', 'high']}, {id: 'example-mini'}];
(async () => {
  const panel = await context.mount(window.document.querySelector('main'), {me: {access: {permission_system: 'open'}}});
  click('新增模型 API');
  let form = window.document.querySelector('dialog form');
  const models = form.elements.models;
  assert.equal(models.tagName, 'TEXTAREA');
  assert.equal(models.value, '');
  assert(models.closest('details'), 'the model list is an advanced setting');
  form.elements.api_id.value = 'example-api'; form.elements.base_url.value = 'https://api.example.com/v1';
  form.elements.default_model.value = 'example-model'; form.elements.register_account.checked = false;

  models.value = '{"id": "example-model"}';
  submit(form); await flush();
  assert.match(form.querySelector('[role=alert]').textContent, /JSON 数组/);
  models.value = JSON.stringify([{id: 'other-model'}]);
  submit(form); await flush();
  assert.match(form.querySelector('[role=alert]').textContent, /包含上面填写的模型名称/);
  assert.equal(count('CreateModelApi'), 0, 'an invalid model list is not sent');

  models.value = JSON.stringify(catalog);
  submit(form); await flush();
  assert.equal(count('CreateModelApi'), 1);
  assert.deepEqual(calls.find(c => c.name === 'CreateModelApi').args.models, catalog);

  click('编辑'); form = window.document.querySelector('dialog form');
  assert.deepEqual(JSON.parse(form.elements.models.value), catalog, 'editing shows the declared models');
  submit(form); await flush();
  assert(!('models' in calls.filter(c => c.name === 'UpdateModelApi').at(-1).args.changes), 'an unchanged list is not resent');

  click('编辑'); form = window.document.querySelector('dialog form');
  form.elements.default_model.value = 'missing-model';
  submit(form); await flush();
  assert.match(form.querySelector('[role=alert]').textContent, /包含上面填写的模型名称/);
  assert.equal(count('UpdateModelApi'), 1, 'a default model outside the declared list is refused');
  form.elements.default_model.value = 'example-model'; form.elements.models.value = '';
  submit(form); await flush();
  assert.deepEqual(calls.filter(c => c.name === 'UpdateModelApi').at(-1).args.changes.models, [], 'clearing removes the list');

  panel.destroy(); dom.window.close(); console.log('Model API model catalog editing passed');
})().catch(error => {console.error(error); dom.window.close(); process.exitCode = 1;});
