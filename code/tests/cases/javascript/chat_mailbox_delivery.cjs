'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { JSDOM } = require('jsdom');
const { STATIC, chatWorkspaceScripts } = require('./chat_workspace_modules.cjs');
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
  'portal_command_registry.js', 'portal_workbench_context.js', 'portal_workbench.js', 'resident_protocol.js', ...chatWorkspaceScripts(input.staticRoot || STATIC)]) {
  w.eval(fs.readFileSync(path.resolve(input.staticRoot || STATIC, name), 'utf8'));
}
const workspace = w.PortalChatWorkspace.createWorkspace({ root: w.document.getElementById('fixture'), id: 'chat',
  historyScope: () => 'mailbox-delivery', refs: false, can_attach: false,
  renderMessageBody: w.PortalChatWorkspace.residentMessageBody,
  renderMessageFooter: w.PortalChatWorkspace.residentMessageFooter });
(async () => {
  try {
    for (const message of input.delivered) {
      // Consume the server's actual delta/page body before any snapshot refresh.
      workspace.setMessages(w.PortalChatWorkspace.projectResidentConversation({}, { messages: [input.plain, message] }).messages);
      const group = w.document.querySelector('[data-mailbox-group]');
      assert.ok(group, 'the delivered group renders immediately');
      const members = [...group.querySelectorAll('.mailboxMember')];
      assert.deepEqual(members.map(row => row.dataset.mailboxMember), input.expected.map(member => member.message_id));
      members.forEach((row, index) => {
        const member = input.expected[index];
        const text = member.content_parts.filter(part => part.type === 'text').map(part => part.text).join('\n');
        assert.equal(row.querySelector('.mailboxMemberText').textContent.trim(), text.trim());
        assert.ok(row.querySelector('.mailboxMemberMeta').textContent.includes(member.origin?.sender?.id || member.author_id));
        assert.match(row.querySelector('.mailboxMemberMeta').textContent, / · /, 'original time is visible');
      });
      assert.ok(w.document.body.textContent.includes(input.plain.content_parts[0].text));
      assert.ok(!w.document.body.textContent.includes('无正文。'));
    }
    console.log('PASS delivered Mailbox group renders original bodies, senders and times without refresh');
  } finally {
    await new Promise(resolve => setImmediate(resolve));
    workspace.destroy(); for (const observer of observers) observer.disconnect();
    await new Promise(resolve => setImmediate(resolve)); w.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
