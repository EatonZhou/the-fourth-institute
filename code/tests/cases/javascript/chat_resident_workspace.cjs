'use strict';
// The Chat of one Resident: which session actions it offers (compact only when
// the backend can compact, reset context), selecting messages to forward with
// a request taken together with its reply, the forward dock, and the
// conversation search scoped to the Resident.
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
const settle = async () => { for (let turn = 0; turn < 8; turn += 1) await new Promise(resolve => setTimeout(resolve, 0)); };
const text = node => (node?.textContent || '').replace(/\s+/g, ' ').trim();
const plain = value => JSON.parse(JSON.stringify(value));
const toasts = [];
w.PortalUI.toast = (message, tone) => toasts.push([String(message), tone]);

const chat = w.PortalChatWorkspace;
const messages = w.PortalChatWorkspaceMessages;
const escapeHtml = value => String(value ?? '').replace(/[&<>"']/g, char => `&#${char.charCodeAt(0)};`);
const { ResidentChatWorkspace, residentSessionActions } = w.PortalChatWorkspaceResidentWorkspace.createResidentWorkspace({
  createWorkspace: chat.createWorkspace, escapeHtml,
  messageIdentity: messages.messageIdentity, messageRole: messages.messageRole, messageTitle: messages.messageTitle,
  normalizeRefs: chat.normalizeRefs,
  normalizeResidentTarget: value => Object.freeze({ resident_id: String(value?.resident_id || '').trim(), lineage_id: String(value?.lineage_id || '').trim() }),
  residentProtocolMessageToWorkspace: chat.residentProtocolMessageToWorkspace,
  residentSessionScope: chat.residentSessionScope,
  workspaceInstanceId: options => options.id || 'residentChat',
});

function sessionActions() {
  assert.deepEqual(plain(residentSessionActions({ active: false, runtimeCapabilities: { operations: ['compact'] }, resetContext: true })), {
    extensions: false, forward: false, compact: false, compactDisabled: false, resetContext: true, resetContextDisabled: false,
  }, 'a Chat without a Resident offers no session actions of its own');
  assert.equal(residentSessionActions({ runtimeCapabilities: { operations: ['fork'] } }).compact, false,
    'a backend that cannot compact has no compact button');
  assert.equal(residentSessionActions({}).compact, false);
  assert.deepEqual(plain(residentSessionActions({ runtimeCapabilities: { operations: ['fork', 'compact'] }, compactBusy: true, resetContext: true, resetContextDisabled: true })), {
    extensions: true, forward: true, compact: true, compactDisabled: true, resetContext: true, resetContextDisabled: true,
  });
  assert.equal(residentSessionActions({ runtimeCapabilities: { operations: ['compact'] } }).compactDisabled, false);
}

const conversation = [
  { id: 'h1', message_id: 'h1', role: 'user', content: 'First request', name: 'Ana', created_at: '2026-10-26T01:00:00Z' },
  { id: 'a1', message_id: 'a1', role: 'assistant', content: 'Working on it', name: 'Builder', created_at: '2026-10-26T01:01:00Z' },
  { id: 'p1', message_id: 'p1', role: 'system', content: 'Tool ran', created_at: '2026-10-26T01:02:00Z' },
  { id: 'a2', message_id: 'a2', role: 'assistant', content: 'Done with the first', name: 'Builder', created_at: '2026-10-26T01:03:00Z' },
  { id: 'h2', message_id: 'h2', role: 'user', content: 'Second request', name: 'Ana', created_at: '2026-10-26T01:04:00Z' },
  { id: 'a3', message_id: 'a3', role: 'assistant', content: 'Second answer', name: 'Builder', created_at: '2026-10-26T01:05:00Z' },
  { id: 'p2', message_id: 'p2', role: 'system', content: 'Context compacted', created_at: '2026-10-26T01:05:30Z' },
  { id: 'h3', message_id: 'h3', role: 'user', content: 'Unanswered request', name: 'Ana', created_at: '2026-10-26T01:06:00Z' },
  { id: 'pending', role: 'user', content: 'Still sending', name: 'Ana', pending: true, created_at: '2026-10-26T01:07:00Z' },
];

async function residentChat() {
  const root = w.document.getElementById('fixture');
  const forwarded = [];
  const compacted = [];
  const resets = [];
  const workspace = new ResidentChatWorkspace({
    root, id: 'chatResident', target: { resident_id: 'resident_sample', lineage_id: 'lineage_sample' },
    contract: { schema: 'portal.chat_workspace.v3', messages: [], composer: { can_send: true, queue: { items: [] } } },
    renderMessageBody: chat.residentMessageBody,
    onForward: (items, owner) => forwarded.push([plain(items), owner]),
    onCompact: owner => compacted.push(owner),
    onResetContext: owner => resets.push(owner),
    onMenu: () => {},
  }).mount();
  const dock = () => workspace.element('forwardDock');
  const row = key => root.querySelector(`.messageList > [data-message-key="${key}"]`);
  const check = key => root.querySelector(`[data-resident-forward-select-key="${key}"]`);
  try {
    assert.equal(workspace.element('compact').id, 'chatResidentCompact', 'the chrome ids follow the workspace id');
    assert.equal(workspace.element('compact').hidden, true, 'compact is hidden until the backend is known');
    workspace.setResidentActionVisibility(residentSessionActions({ runtimeCapabilities: { operations: ['compact'] }, resetContext: true }));
    assert.equal(workspace.element('compact').hidden, false);
    assert.equal(workspace.element('compact').disabled, false);
    workspace.element('compact').click();
    assert.deepEqual(compacted, [workspace], 'the compact button asks the owner to compact');
    workspace.setResidentActionVisibility(residentSessionActions({ runtimeCapabilities: { operations: ['compact'] }, compactBusy: true, resetContext: true, resetContextDisabled: true }));
    assert.equal(workspace.element('compact').disabled, true, 'compact waits while another action runs');
    assert.equal(workspace.element('resetContext').hidden, false);
    assert.equal(workspace.element('resetContext').disabled, true, 'reset waits while the Resident works');
    workspace.setResidentActionVisibility(residentSessionActions({ runtimeCapabilities: { operations: [] }, resetContext: true }));
    assert.equal(workspace.element('compact').hidden, true, 'another backend without compact hides it again');
    workspace.element('resetContext').click();
    assert.deepEqual(resets, [workspace]);

    workspace.setMessages(conversation, { history: { can_load_older: false } });
    await settle();
    assert.deepEqual(plain(workspace.forwardSelectionGroup('h1')), ['h1', 'a2'], 'a request goes with the last reply before the next request');
    assert.deepEqual(plain(workspace.forwardSelectionGroup('h2')), ['h2', 'a3'], 'a system message after the reply is not the reply');
    assert.deepEqual(plain(workspace.forwardSelectionGroup('a1')), ['a1'], 'a reply goes alone');
    assert.deepEqual(plain(workspace.forwardSelectionGroup('h3')), ['h3'], 'an unanswered request goes alone');
    assert.deepEqual(plain(workspace.forwardSelectionGroup('missing')), ['missing']);

    assert.equal(dock().classList.contains('open'), false);
    const forwardItem = row('h1').querySelector('[data-portal-message-action="forward"]');
    assert.ok(forwardItem, 'each sent message offers Forward');
    assert.equal(forwardItem.hidden, false);
    workspace.setResidentActionVisibility(residentSessionActions({ active: false }));
    assert.equal(forwardItem.hidden, true, 'losing Forward hides it in place');
    workspace.setResidentActionVisibility(residentSessionActions({}));
    assert.equal(forwardItem.hidden, false, 'and shows it again');
    assert.equal(row('pending').querySelector('[data-portal-message-action="forward"]'), null, 'a message still sending does not');
    forwardItem.click();
    await settle();
    assert.equal(dock().classList.contains('open'), true, 'Forward starts selecting');
    assert.deepEqual(plain(workspace.forwardSelectionState()), { active: true, count: 2 });
    assert.equal(text(workspace.element('forwardCount')), '已选择 2 条');
    assert.equal(workspace.element('forward').disabled, false);
    assert.equal(check('h1').checked, true);
    assert.equal(check('a2').checked, true, 'with the reply to the request');
    assert.equal(check('a1').checked, false);
    assert.ok(row('a2').classList.contains('forwardSelected'));
    assert.ok(row('a1').classList.contains('forwardSelectable'));
    assert.equal(row('a1').classList.contains('forwardSelected'), false);

    check('h1').checked = false;
    check('h1').dispatchEvent(new w.Event('change'));
    await settle();
    assert.equal(text(workspace.element('forwardCount')), '已选择 0 条', 'unchecking a request drops its reply too');
    assert.equal(workspace.element('forward').disabled, true);
    assert.equal(dock().classList.contains('open'), true, 'selection goes on with nothing chosen');
    check('h2').checked = true;
    check('h2').dispatchEvent(new w.Event('change'));
    await settle();
    check('a1').checked = true;
    check('a1').dispatchEvent(new w.Event('change'));
    await settle();
    assert.equal(text(workspace.element('forwardCount')), '已选择 3 条');

    workspace.element('forward').click();
    assert.equal(forwarded.length, 1);
    assert.equal(forwarded[0][1], workspace);
    assert.deepEqual(forwarded[0][0], [
      { resident_id: 'resident_sample', message_id: 'a1', role: 'agent', author: 'Builder', content: 'Working on it', created_at: '2026-10-26T01:01:00Z' },
      { resident_id: 'resident_sample', message_id: 'h2', role: 'human', author: 'Ana', content: 'Second request', created_at: '2026-10-26T01:04:00Z' },
      { resident_id: 'resident_sample', message_id: 'a3', role: 'agent', author: 'Builder', content: 'Second answer', created_at: '2026-10-26T01:05:00Z' },
    ], 'the selected messages go in conversation order with their IDs');

    workspace.element('forwardCancel').click();
    await settle();
    assert.deepEqual(plain(workspace.forwardSelectionState()), { active: false, count: 0 }, 'cancel ends the selection');
    assert.equal(dock().classList.contains('open'), false);
    assert.equal(check('h2'), null, 'and removes the check boxes');

    workspace.startForwardSelection('pending');
    assert.equal(workspace.forwardSelectionState().active, false, 'a message still sending cannot start a selection');
    workspace.startForwardSelection('h3');
    await settle();
    assert.deepEqual(plain(workspace.forwardSelectionState()), { active: true, count: 1 });
    workspace.setResidentActionVisibility(residentSessionActions({ active: false }));
    await settle();
    assert.deepEqual(plain(workspace.forwardSelectionState()), { active: false, count: 0 }, 'losing Forward ends a selection');
    assert.equal(row('h1').querySelector('[data-portal-message-action="forward"]').hidden, true, 'and hides Forward');
    workspace.startForwardSelection('h1');
    assert.equal(workspace.forwardSelectionState().active, false);
    workspace.setResidentActionVisibility(residentSessionActions({}));
    assert.equal(row('h1').querySelector('[data-portal-message-action="forward"]').hidden, false);

    const searches = [];
    w.PortalResidentService = {
      searchMessages: async (residentIds, query, paging) => {
        searches.push([plain(residentIds), query, plain(paging)]);
        return {
          results: [
            { resident_id: 'resident_sample', message: { message_id: 'h2', role: 'user', content_parts: [{ type: 'text', text: 'Second request' }] } },
            { resident_id: 'resident_other', message: { message_id: 'x1', role: 'user', content_parts: [{ type: 'text', text: 'Not this Chat' }] } },
          ],
          next_page_token: 'next',
        };
      },
    };
    const found = await workspace.extensionOptions().onSearchMessages('request', { pageToken: '', limit: 50 });
    assert.deepEqual(searches, [[['resident_sample'], 'request', { pageToken: '', limit: 50 }]], 'search asks for this Resident only');
    assert.deepEqual(found.messages.map(item => [item.id, item.content]), [['h2', 'Second request']], 'and keeps only its messages');
    assert.equal(found.next_page_token, 'next');
  } finally {
    workspace.destroy();
  }
}

(async () => {
  try {
    sessionActions();
    await residentChat();
    // Use the complete Resident panel and its real message menu, including the
    // ResidentChatWorkspace adapter between the panel and shared controller.
    const root = w.document.createElement('section');
    w.document.body.append(root);
    const failures = [];
    const onError = event => { failures.push(event.error?.message || event.message); event.preventDefault(); };
    w.addEventListener('error', onError);
    w.PortalResidentService.inputHistory = async () => ({entries: []});
    w.PortalAPI = {get: async () => ({config: {}, restart: null})};
    w.PortalInstructionClient = {execute: async () => ({entries: [], items: []})};
    const panel = chat.panelPackage.create({root, id: 'quotePanel', autoLoad: false,
      target: {resident_id: 'resident_111111111111'}, onError: error => failures.push(error.message)});
    const snapshot = {messages: [
      {message_id: 'reply_before', role: 'agent', content_parts: [{type: 'text', text: 'Earlier part'}]},
      {message_id: 'reply_selected', role: 'agent', origin: {sender: {id: 'resident_222222222222'}},
        content_parts: [{type: 'text', text: 'Selected reply'}]},
      {message_id: 'reply_after', role: 'agent', content_parts: [{type: 'text', text: 'Later part'}]},
    ], controls: {can_send: true}, queue: {items: []}};
    panel.protocol = {snapshot: () => snapshot, destroy() {}};
    panel.residentProjection = {resident: {resident_id: 'resident_111111111111'},
      chat_workspace: {schema: 'portal.chat_workspace.v3', target: {kind: 'resident', id: 'resident_111111111111'},
        header: {title: 'Quote regression'}, messages: [], composer: {can_send: true}}};
    try {
      panel.applySnapshot(snapshot);
      const menu = root.querySelector('[data-portal-message-menu="reply_selected"]');
      assert.ok(menu, 'a reply has its actual action menu');
      menu.querySelector('[data-portal-message-action="quote"]').click();
      await settle();
      assert.deepEqual(failures, [], 'the panel menu reports no adapter failure');
      assert.equal(panel.workspace.quote()?.message_id, 'reply_selected');
      assert.equal(panel.workspace.quote()?.author_id, 'resident_222222222222');
      assert.equal(panel.workspace.quote()?.text, 'Selected reply');
      const bar = root.querySelector('[data-composer-quote-host]');
      assert.equal(bar.hidden, false, 'clicking Quote visibly opens the composer quote bar');
      menu.querySelector('[data-portal-message-action="pin"]').click();
      root.querySelector('[data-composer-quote-cancel]').click();
      root.querySelector('[data-pinned-action="quote"]').click();
      assert.equal(panel.workspace.quote()?.message_id, 'reply_selected', 'header quote selects the same reply');
      assert.doesNotMatch(bar.textContent, /Earlier part|Later part/);
    } finally { panel.destroy(); w.removeEventListener('error', onError); }
  } finally {
    observers.forEach(observer => observer.disconnect());
    await settle();
    w.close();
  }
})().catch(error => {
  console.error(error);
  process.exitCode = 1;
});
