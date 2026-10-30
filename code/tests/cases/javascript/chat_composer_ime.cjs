'use strict';
// While an input method is composing (Chinese, Japanese and so on), Ctrl/⌘ +
// Enter, the Send button and the Arrow history leave the composer alone; once
// composition ends the same keys send and recall as usual.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { JSDOM } = require('jsdom');
const { chatWorkspaceScripts } = require('./chat_workspace_modules.cjs');
const { portalWorkbenchScripts } = require('./portal_workbench_scripts.cjs');
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const dom = new JSDOM('<section id="fixture"></section>', {
  url: 'https://chat.fixture.invalid/lineage', pretendToBeVisual: true, runScripts: 'outside-only',
});
const w = dom.window;
const observers = new Set();
const NativeMutationObserver = w.MutationObserver;
w.MutationObserver = class extends NativeMutationObserver {
  constructor(callback) { super(callback); observers.add(this); }
};
w.CSS = { escape: value => String(value).replace(/[^a-zA-Z0-9_-]/g, char => `\\${char}`) };
w.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} };
w.matchMedia = query => Object.assign(new w.EventTarget(), { media: query, matches: false });
w.PortalIdentity = { username: () => 'sample_admin' };
w.eval(input.routeScript);
for (const name of ['portal_state.js', 'portal_page_state.js', 'portal_text.js', 'portal_ui_primitives.js', 'portal_ui.js',
  'portal_overlay.js', 'portal_resource_chips.js', 'portal_image_editor.js', 'portal_editor_open_service.js',
  'portal_command_registry.js', 'portal_workbench_context.js', ...portalWorkbenchScripts(), 'resident_protocol.js', ...chatWorkspaceScripts()]) {
  w.eval(fs.readFileSync(path.resolve(__dirname, '../../../management_portal/static', name), 'utf8'));
}
const settle = async () => { for (let turn = 0; turn < 5; turn += 1) await new Promise(resolve => setTimeout(resolve, 0)); };

(async () => {
  const sent = [];
  const workspace = w.PortalChatWorkspace.createWorkspace({
    root: w.document.getElementById('fixture'), id: 'chat', historyScope: () => 'conversation-ime', refs: false,
    onSend: payload => sent.push(payload.text),
    onLoadInputHistory: async () => ({
      messages: [{ id: 'in-1', message_id: 'in-1', role: 'user', author_id: 'sample_admin', content: 'earlier input' }],
      next_page_token: '',
    }),
    contract: { schema: 'portal.chat_workspace.v3', messages: [], composer: { can_send: true, queue: { items: [] } } },
  });
  const composer = w.document.querySelector('#fixture .composerInput');
  const key = async (init) => {
    composer.dispatchEvent(new w.KeyboardEvent('keydown', { bubbles: true, cancelable: true, ...init }));
    await settle();
  };
  const type = async text => {
    composer.textContent = text;
    composer.dispatchEvent(new w.Event('input', { bubbles: true }));
    await settle();
  };
  try {
    workspace.setMessages([], { history: { can_load_older: false } });
    await settle();
    composer.focus();
    await type('你好');

    await key({ key: 'Enter', ctrlKey: true, isComposing: true });
    await key({ key: 'Enter', metaKey: true, keyCode: 229 });
    assert.deepEqual(sent, [], 'a key event that belongs to the input method never sends');

    composer.dispatchEvent(new w.CompositionEvent('compositionstart', { bubbles: true, data: 'ni' }));
    await settle();
    await key({ key: 'Enter', ctrlKey: true });
    assert.deepEqual(sent, [], 'Ctrl+Enter between compositionstart and compositionend never sends');
    workspace.els.send.click();
    await settle();
    assert.deepEqual(sent, [], 'the Send button waits for composition to end');
    await key({ key: 'ArrowUp' });
    assert.equal(composer.textContent.trim(), '你好', 'ArrowUp while composing moves inside the candidate, not to history');

    composer.dispatchEvent(new w.CompositionEvent('compositionend', { bubbles: true, data: '你好' }));
    await settle();
    await key({ key: 'Enter', ctrlKey: true });
    assert.deepEqual(sent, ['你好'], 'after composition ends Ctrl+Enter sends once');
    await type('');
    await key({ key: 'ArrowUp' });
    assert.equal(composer.textContent.trim(), 'earlier input', 'after composition ends ArrowUp recalls the history');
    console.log('PASS the composer leaves Send, Ctrl+Enter and the Arrow history to the input method while it composes');
  } finally {
    workspace.destroy();
    await new Promise(resolve => setImmediate(resolve));
    for (const observer of observers) observer.disconnect();
    w.close();
  }
})().catch(error => {
  console.error(error);
  process.exitCode = 1;
});
