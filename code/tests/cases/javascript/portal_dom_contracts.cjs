'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { createHarness } = require('./workbench_visibility_state.cjs');

async function commandPriority(h) {
  const w = h.window, d = w.document;
  const input = d.createElement('input'); d.body.append(input); input.focus();
  const calls = [];
  const local = w.PortalCommandRegistry.createScope({ id: 'shortcut-page' });
  for (const binding of ['Ctrl+Shift+L', 'Meta+Shift+L']) {
    w.PortalCommandRegistry.root.register({ id: `global-${binding}`, keybindings: [binding], allowInInput: true, execute: () => calls.push('global') });
    local.register({ id: `local-${binding}`, keybindings: [binding], priority: 999999, allowInInput: true, execute: () => calls.push('local') });
  }
  local.register({ id: 'local-save', keybindings: ['Ctrl+S'], allowInInput: true, execute: () => calls.push('save') });
  d.addEventListener('keydown', event => {
    if (event.key.toLowerCase() !== 'l' || !(event.ctrlKey || event.metaKey)) return;
    calls.push('page-capture'); event.preventDefault(); event.stopImmediatePropagation();
  }, true);
  const send = async init => {
    const event = new w.KeyboardEvent('keydown', { bubbles: true, cancelable: true, composed: true, ...init });
    input.dispatchEvent(event); await Promise.resolve(); return event.defaultPrevented;
  };
  assert.equal(await send({ key: 'L', ctrlKey: true, shiftKey: true }), true);
  assert.equal(await send({ key: 'L', metaKey: true, shiftKey: true }), true);
  assert.equal(await send({ key: 's', ctrlKey: true }), true);
  for (const key of 'normal input') assert.equal(await send({ key }), false, 'ordinary typing is not swallowed');
  assert.deepEqual(calls, ['global', 'global', 'save'], 'root priority precedes local and page capture');
  local.dispose();
}

async function commandPolicy(h) {
  const w = h.window, d = w.document, root = w.PortalCommandRegistry.root, calls = [];
  const input = d.createElement('input'); d.body.append(input);
  const host = d.createElement('div'); d.body.append(host);
  const shadowInput = input.cloneNode(); host.attachShadow({ mode: 'open' }).append(shadowInput);
  root.register({ id: 'composition-check', keybindings: ['Ctrl+J'], allowInInput: true, execute: () => calls.push('composed') });
  root.register({ id: 'outside-inputs', keybindings: ['Ctrl+U'], execute: () => calls.push('outside') });
  root.register({ id: 'disabled', keybindings: ['Ctrl+I'], enabled: false, priority: 20, execute: () => calls.push('disabled') });
  root.register({ id: 'hidden', keybindings: ['Ctrl+I'], visible: false, priority: 30, execute: () => calls.push('hidden') });
  root.register({ id: 'available', keybindings: ['Ctrl+I'], execute: () => calls.push('available') });
  const send = async (target, key, extra = {}) => {
    const event = new w.KeyboardEvent('keydown', { key, ctrlKey: true, bubbles: true, composed: true, cancelable: true, ...extra });
    target.dispatchEvent(event); await Promise.resolve(); return event.defaultPrevented;
  };
  const prevented = [await send(input, 'j', { isComposing: true }), await send(input, 'j', { keyCode: 229 }),
    await send(input, 'u'), await send(shadowInput, 'u'), await send(d.body, 'u'), await send(d.body, 'i')];
  assert.deepEqual(prevented, [false, false, false, false, true, true], 'composition and input policy');
  assert.deepEqual(calls, ['outside', 'available'], 'only available command executes');
}

async function localConfirmation(h) {
  const w = h.window, d = w.document;
  w.eval(fs.readFileSync(path.join(__dirname, '../../../management_portal/static/portal_overlay.js'), 'utf8'));
  let submits = 0;
  d.addEventListener('submit', event => { submits += 1; event.preventDefault(); event.stopImmediatePropagation(); }, true);
  d.addEventListener('click', event => {
    if (event.target.closest('button[type="submit"]')) { event.preventDefault(); event.stopImmediatePropagation(); }
  }, true);
  const result = w.PortalUI.confirm({ title: 'Discard local draft' });
  const surface = d.querySelector('[data-portal-confirmation]');
  const button = d.querySelector('[data-portal-confirm-accept]');
  assert.equal(surface.hidden, false, 'confirmation opens');
  assert.equal(button.type, 'button', 'local confirmation is not a form submission');
  button.click();
  assert.equal(await result, true, 'blocked submit does not block local confirmation');
  assert.equal(surface.hidden, true, 'confirmation closes after decision');
  assert.equal(submits, 0, 'no form submit occurred');
}

async function dirtyConfirmation(h) {
  const w = h.window, d = w.document;
  w.eval(fs.readFileSync(path.join(__dirname, '../../../management_portal/static/portal_overlay.js'), 'utf8'));
  const modal = d.createElement('div'); modal.hidden = true;
  modal.innerHTML = '<textarea>changed</textarea>'; d.body.append(modal);
  const dialog = w.PortalUI.createDialogController({ modal, dirty: () => true, backdropClose: true, escapeClose: true });
  try {
    dialog.open();
    assert.equal(dialog.close(), false, 'dirty close is deferred');
    assert.equal(modal.hidden, false, 'dirty dialog remains open while deciding');
    assert.equal(d.querySelector('[data-portal-confirmation]').hidden, false, 'dirty guard uses shared confirmation');
    d.querySelector('[data-portal-confirm-accept]').click();
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(modal.hidden, true, 'accepted dirty close hides dialog');
  } finally { dialog.destroy(); }
}

const contracts = { 'command-priority': commandPriority, 'command-policy': commandPolicy,
  'local-confirmation': localConfirmation, 'dirty-confirmation': dirtyConfirmation };
async function run(name, options) {
  assert.ok(contracts[name], 'independent contract name required');
  const h = createHarness(options);
  try { await contracts[name](h); await h.settle(); }
  finally { await h.cleanup(); }
}
module.exports = { run };
if (require.main === module) run(process.argv[2]).then(() => console.log(`PASS ${process.argv[2]}`))
  .catch(error => { console.error(error); process.exitCode = 1; });
