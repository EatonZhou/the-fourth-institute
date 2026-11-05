'use strict';
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
w.PortalIdentity = { username: () => 'viewer' };
w.eval(input.routeScript);
for (const name of ['portal_state.js', 'portal_page_state.js', 'portal_text.js', 'portal_ui_primitives.js', 'portal_ui.js',
  'portal_overlay.js', 'portal_resource_chips.js', 'portal_image_editor.js', 'portal_editor_open_service.js',
  'portal_command_registry.js', 'portal_workbench_context.js', ...portalWorkbenchScripts(), 'resident_protocol.js', ...chatWorkspaceScripts()]) {
  w.eval(fs.readFileSync(path.resolve(__dirname, '../../../management_portal/static', name), 'utf8'));
}
const request = id => ({ message_id: id, role: 'user', content_parts: [{ type: 'text', text: id }] });
const reply = (id, requestId, text = (id + '\n').repeat(80)) => ({ message_id: id, role: 'agent', created_at: '2026-10-15T15:30:56.353Z',
  content_parts: [{ type: 'text', text }], family_reply: { request_message_id: requestId, resident_id: 'resident_111111111111' },
});
const shared = reply('shared-reply', 'request-a');
const snapshot = { family_facade: {}, messages: [request('request-a'), shared,
  request('request-b'), { ...shared, family_reply: { ...shared.family_reply, request_message_ids: ['request-b'] } }, request('request-c'), reply('new-reply', 'request-c')] };
const workspace = w.PortalChatWorkspace.createWorkspace({ root: w.document.getElementById('fixture'), id: 'chat',
  historyScope: () => 'instances', refs: false, can_attach: false,
  renderAfterMessage: (item, _index, _items, context) => item.family_status
    ? w.PortalChatWorkspace.requestProgressHtml(item.family_status, context) : '',
});
const root = w.document.getElementById('chatMessages');
const render = force => workspace.setMessages(w.PortalChatWorkspace.projectResidentConversation({}, snapshot).messages, { force });
const assertOrder = () => {
  const rows = [...root.querySelectorAll('.messageList > article.messageItem:not([data-message-after-of])')];
  assert.deepEqual(rows.map(row => row.dataset.messageKey), [...new Set(snapshot.messages.map(message => message.message_id))],
    'DOM order exactly matches the authoritative projection; no stranded reply at the bottom');
  assert.deepEqual(rows.filter(row => row.dataset.messageKey === 'shared-reply').map(row => row.dataset.workGroupId), ['request-a']);
  const projected = w.PortalChatWorkspace.projectResidentConversation({}, snapshot).messages;
  assert.deepEqual(Array.from(projected.find(item => item.id === 'shared-reply').family_reply.request_message_ids), ['request-a', 'request-b']);
};
(async () => {
  try {
    render(); assertOrder();
    render(true); assertOrder();
    const copies = [...root.querySelectorAll('article[data-message-key="shared-reply"]')];
    assert.equal(copies.length, 1);
    const toggle = copies[0].querySelector('[data-message-fold-toggle]');
    toggle.click();
    const expanded = toggle.getAttribute('aria-expanded');
    render(true); assertOrder();
    assert.equal(root.querySelector('[data-message-key="shared-reply"] [data-message-fold-toggle]').getAttribute('aria-expanded'), expanded,
      'the single durable reply retains its folding choice');
    snapshot.messages.splice(5, 0, reply('progress-c', 'request-c'));
    render(); assertOrder();
    snapshot.messages[3] = { ...snapshot.messages[3], content_parts: [{ type: 'text', text: 'Updated second projection' }] };
    render(); assertOrder();
    assert.match(root.querySelector('[data-work-group-id="request-a"] .messageContent').textContent, /Updated second projection/);
    snapshot.messages[0].relay = { state: 'running', message_id: 'worker-a', resident_id: 'resident_111111111111', title: 'Worker A' };
    snapshot.messages[2].relay = { state: 'running', message_id: 'worker-b', grouped_into_message_id: 'worker-a', resident_id: 'resident_111111111111', title: 'Worker A' };
    render(); assertOrder();
    assert.equal(root.querySelectorAll('.familyRequestStatus').length, 1, 'one actual execution gets one progress row');
    assert.ok(root.querySelector('[data-family-request="request-a"]'));
    render(true); assertOrder();
    snapshot.messages.unshift(request('older-request'), reply('older-reply', 'older-request'));
    render(); assertOrder();
    root.querySelector('[data-portal-work-group-toggle="request-a"]').click(); assertOrder();
    assert.ok(root.querySelector('[data-work-group-id="request-a"]').classList.contains('is-work-collapsed'));
    assert.ok(!root.querySelector('[data-work-group-id="request-c"]').classList.contains('is-work-collapsed'));
    console.log('PASS unique Family replies retain associations, order, progress and independent folding');
  } finally {
    await new Promise(resolve => setImmediate(resolve));
    workspace.destroy();
    for (const observer of observers) observer.disconnect();
    await new Promise(resolve => setImmediate(resolve));
    w.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
