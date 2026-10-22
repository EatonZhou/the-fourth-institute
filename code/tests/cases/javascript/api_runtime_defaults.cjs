const assert = require('node:assert/strict'), fs = require('node:fs'), vm = require('node:vm');
const {JSDOM} = require('jsdom');
const catalog = JSON.parse(fs.readFileSync(0, 'utf8'));
const dom = new JSDOM('<body></body>'), {window} = dom, errors = [], saved = [];
const template = (parts, ...values) => parts.reduce((text, part, index) => text + part + (values[index] ?? ''), '');
window.PortalI18n = {t: text => text, html: template, text: template};
window.PortalUI = {
  toast: text => errors.push(text), errorMessage: (context, error) => `${context}: ${error.message}`,
  createDialogController: ({modal}) => ({open() {modal.hidden = false;}, close() {modal.hidden = true;}, destroy() {}}),
};
const context = vm.createContext({window, document: window.document, Option: window.Option,
  escapeHtml: text => String(text).replaceAll('&', '&amp;').replaceAll('"', '&quot;').replaceAll('<', '&lt;'),
  showResidentBackendNotices() {}, console});
vm.runInContext(fs.readFileSync('management_portal/static/portal_model_api_settings.js', 'utf8'), context);
const source = fs.readFileSync('management_portal/static/portal_chat_workspace.js', 'utf8');
vm.runInContext(source.slice(source.indexOf('  function createRuntimeSettingsDialog('),
  source.indexOf('  const sessionRestartActive =')), context);
const flush = async () => {for (let i = 0; i < 4; i++) await new Promise(resolve => setImmediate(resolve));};
(async () => {
  const dialog = context.createRuntimeSettingsDialog();
  for (const selection of ['', 'deepseek-v4-flash']) {
    dialog.open({model: 'gpt-6-luna'}, value => saved.push(value), {catalog});
    const model = window.document.querySelector('[data-runtime-setting=model]');
    assert.equal(model.value, 'gpt-6-luna', 'saved values remain visible until explicitly changed');
    assert.equal(model.options[0].textContent, 'deepseek-v4-flash (default)');
    model.value = selection; model.dispatchEvent(new window.Event('change'));
    window.document.querySelector('form').dispatchEvent(new window.Event('submit', {cancelable: true}));
    await flush();
    assert.deepEqual(errors, [], 'the offered default must pass the real form validation');
    assert.equal(saved.at(-1).model, selection);
  }
  assert.equal(saved.length, 2);
  dialog.destroy(); dom.window.close();
})().catch(error => {console.error(error); dom.window.close(); process.exitCode = 1;});
