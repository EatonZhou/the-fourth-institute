'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { JSDOM } = require('jsdom');
const { STATIC, chatWorkspaceScripts } = require('./chat_workspace_modules.cjs');
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
w.PortalIdentity = { username: () => 'viewer' };
w.eval(input.routeScript);
for (const name of ['portal_state.js', 'portal_page_state.js', 'portal_text.js', 'portal_ui_primitives.js', 'portal_ui.js',
  'portal_overlay.js', 'portal_preview_contract.js', 'portal_file_links.js', 'portal_resource_chips.js', 'portal_image_editor.js', 'portal_editor_open_service.js',
  'portal_command_registry.js', 'portal_workbench_context.js', ...portalWorkbenchScripts(input.staticRoot || STATIC), 'resident_protocol.js', ...chatWorkspaceScripts(input.staticRoot || STATIC)]) {
  w.eval(fs.readFileSync(path.resolve(input.staticRoot || STATIC, name), 'utf8'));
}
const workspace = w.PortalChatWorkspace.createWorkspace({ root: w.document.getElementById('fixture'), id: 'chat',
  historyScope: () => 'recipient-labels', refs: false, can_attach: false,
  renderMessageFooter: w.PortalChatWorkspace.residentMessageFooter });
(async () => {
  try {
    workspace.setMessages(w.PortalChatWorkspace.projectResidentConversation({}, { messages: input.messages }).messages);
    const rows = [...w.document.querySelectorAll('[data-message-recipients]')];
    assert.equal(rows.length, input.expected.length);
    rows.forEach((row, index) => {
      assert.equal(row.querySelector('.familyRelayStatus').textContent, input.expected[index]);
      assert.equal(row.dataset.messageRecipients, input.messages[index].recipients.map(p => p.id).join(','));
      assert.equal(row.querySelector('img'), null, 'display labels are escaped text');
    });
    if (input.senderTitles) {
      const titles = [...w.document.querySelectorAll('.messageItem .messageTitle')].map(node => node.textContent);
      assert.deepEqual(titles, input.senderTitles);
    }
    console.log('PASS recipient labels preserve machine identity and render escaped display text');
  } finally {
    await new Promise(resolve => setImmediate(resolve));
    workspace.destroy(); for (const observer of observers) observer.disconnect();
    await new Promise(resolve => setImmediate(resolve)); w.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
