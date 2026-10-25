const assert = require('node:assert/strict'), fs = require('node:fs'), path = require('node:path');
const {JSDOM} = require('jsdom');
const {STATIC, chatWorkspaceScripts} = require('./chat_workspace_modules.cjs');
const catalog = JSON.parse(fs.readFileSync(0, 'utf8'));
const dom = new JSDOM('<body></body>', {url: 'https://portal.fixture.invalid/lineage', runScripts: 'outside-only'}), {window} = dom, errors = [], saved = [];
const template = (parts, ...values) => parts.reduce((text, part, index) => text + part + (values[index] ?? ''), '');
window.PortalI18n = {t: text => text, html: template, text: template};
window.PortalUI = {
  toast: text => errors.push(text), errorMessage: (context, error) => `${context}: ${error.message}`,
  createDialogController: ({modal}) => ({open() {modal.hidden = false;}, close() {modal.hidden = true;}, destroy() {}}),
};
window.PortalResourceChips = {};
const observers = new Set(), NativeMutationObserver = window.MutationObserver;
window.MutationObserver = class extends NativeMutationObserver {
  constructor(callback) {super(callback); observers.add(this);}
};
const close = () => {for (const observer of observers) observer.disconnect(); dom.window.close();};
// The real UI primitives, model API settings and chat workspace scripts, as a page loads them.
for (const file of ['portal_ui_primitives.js', 'portal_model_api_settings.js', ...chatWorkspaceScripts()]) {
  window.eval(fs.readFileSync(path.join(STATIC, file), 'utf8') + `\n//# sourceURL=${file}`);
}
const flush = async () => {for (let i = 0; i < 4; i++) await new Promise(resolve => setImmediate(resolve));};
(async () => {
  const dialog = window.PortalChatWorkspace.createRuntimeSettingsDialog();
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
  dialog.destroy(); close();
})().catch(error => {console.error(error); close(); process.exitCode = 1;});
