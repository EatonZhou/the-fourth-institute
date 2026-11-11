'use strict';
// The Chatrooms side view on the real lineage page: the list orders pinned
// Chatrooms first and the rest by latest activity, and pinning from a card's
// menu saves the pin and reorders the list.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const { startWorkbenchPage } = require('./lineage_workbench_modules.cjs');

const NAMESPACE = 'lineage-workbench.chatrooms';
const room = (digit, extra = {}) => ({
  chatroom_id: `chatroom_${digit.repeat(12)}`, title: `Room ${digit}`, members: ['fixture', 'alice'],
  permissions: {}, ...extra,
});
const said = (preview, at) => ({ last_message: { preview, author_id: 'alice', created_at: at } });
const ROOMS = [room('1', said('hi', '2026-10-20T00:00:00Z')), room('2'), room('3', said('later', '2026-10-21T00:00:00Z'))];

test('Chatroom list and message links reopen a live Chat after closing the active extra tab', async t => {
  const target = ROOMS[0];
  const message = { message_id: 'msg_fixture', ordinal: 1, author_id: 'alice', role: 'user',
    content_parts: [{ type: 'text', text: 'Chatroom message remains accessible' }] };
  const page = startWorkbenchPage({
    answers: {
      '/api/me': { username: 'fixture' }, '/api/users': [],
      '/api/agent-workbench/residents': { ready: true, residents: [], lineages: [], principal_hierarchy: { projects: [], user_residents: [] } },
    },
    instructions: {
      ListChatrooms: () => ({ chatrooms: ROOMS }),
      GetChatroom: () => ({ chatroom: target }),
      ListChatroomMessages: () => ({ messages: [message] }),
    },
  });
  t.after(page.close);
  page.window.performance.getEntriesByType = () => [];
  await page.until(() => page.parts.PortalLineageWorkbenchChatrooms?.dependencies.currentAgentChatPanel());
  const { dependencies, exports: rooms } = page.parts.PortalLineageWorkbenchChatrooms;
  const workbench = dependencies.currentLineageWorkbench();
  const closeExtraChat = async () => {
    await workbench.openResource({ type: 'chat', mode: 'new-tab', resource: { kind: 'chat', title: 'Extra Chat' } });
    const extra = dependencies.currentAgentChatPanel();
    assert.notEqual(extra.instanceId(), 'chat');
    // Move the room onto the soon-to-be-closed tab, exercising its cached host too.
    await rooms.selectChatroom(target.chatroom_id);
    await workbench.closePanel(extra.instanceId());
    assert.equal(workbench.getPanel(extra.instanceId()), null);
  };
  await closeExtraChat();
  await rooms.loadChatrooms();
  page.document.querySelector(`[data-chatroom-select="${target.chatroom_id}"]`).click();
  await page.until(() => page.document.querySelector('.lineageChatroomHost .messageList [data-message-key="msg_fixture"]'));
  assert.equal(workbench.getPanel(dependencies.currentAgentChatPanel().instanceId()), dependencies.currentAgentChatPanel());
  assert.match(page.document.querySelector('.lineageChatroomHost').textContent, /Chatroom message remains accessible/);
  assert.equal(workbench.getPanelsByType('chat').length, 1, 'reuse the surviving Chat');
  await closeExtraChat();
  const detail = { entry: { surface: 'lineage', url: `/lineage?chatroom=${target.chatroom_id}&message=msg_fixture` } };
  page.window.dispatchEvent(new page.window.CustomEvent('portal:agent-conversation-open', { detail }));
  await page.until(() => page.document.querySelector('.lineageChatroomHost .messageQuoteTarget'));
  assert.equal(detail.handled, true);
  assert.equal(page.document.querySelector('.messageQuoteTarget').dataset.messageKey, 'msg_fixture');
  assert.equal(new URL(page.window.location.href).searchParams.get('message'), 'msg_fixture');
  assert.deepEqual(page.errors, []);
});

test('Chatroom cold links and in-page Mailbox navigation preserve the exact message target', async t => {
  const calls = [];
  const id = ROOMS[0].chatroom_id;
  const page = startWorkbenchPage({
    url: `https://portal.fixture.invalid/lineage?chatroom=${id}&message=msg_new`,
    answers: {
      '/api/me': { username: 'fixture' },
      '/api/users': [],
      '/api/agent-workbench/residents': { ready: true, residents: [], lineages: [], principal_hierarchy: { projects: [], user_residents: [] } },
    },
    instructions: { ListChatrooms: () => ({ chatrooms: ROOMS }) },
  });
  t.after(page.close);
  page.window.performance.getEntriesByType = () => [];
  page.window.PortalChatroomView = { mount: () => ({
    setChatroom: async (id, options) => { calls.push({ id, ...options }); return ROOMS.find(r => r.chatroom_id === id); },
    destroy() {},
  }) };
  await page.until(() => calls.length > 0);
  assert.deepEqual(calls[0], { id, messageId: 'msg_new' });
  const detail = { entry: { surface: 'lineage', url: `/lineage?chatroom=${id}&message=msg_next` } };
  page.window.dispatchEvent(new page.window.CustomEvent('portal:agent-conversation-open', { detail }));
  await page.until(() => calls.length === 2);
  assert.equal(detail.handled, true);
  assert.deepEqual(calls[1], { id, messageId: 'msg_next' });
  assert.equal(new URL(page.window.location.href).searchParams.get('message'), 'msg_next');
  await page.parts.PortalLineageWorkbenchChatrooms.exports.selectChatroom(id);
  assert.deepEqual(calls[2], { id, messageId: '' });
  assert.equal(new URL(page.window.location.href).searchParams.has('message'), false, 'room-only navigation clears a stale message target');
});

test('quoting authors adds named mentions to the real composer without duplicate or self pings', async t => {
  const page = startWorkbenchPage({ answers: { '/api/me': { username: 'fixture' } } });
  const root = page.document.createElement('div');
  page.document.body.append(root);
  const resident = 'resident_0123456789ab';
  const view = page.window.PortalChatroomView.mount({ root, me: 'fixture',
    authorName: id => ({ alice: 'Alice Example', [resident]: 'Assistant' }[id] || id) });
  t.after(async () => { view.destroy(); await page.close(); });
  view.room = room('1', { permissions: { can_post: true } });
  view.chatroomId = view.room.chatroom_id;
  view.apply();
  const composer = view.controller;
  const quote = author => view.messageAction('quote', { message_id: 'msg_original', author_id: author,
    role: author === resident ? 'agent' : 'user', content_parts: [{ type: 'text', text: '方案' }] });
  for (const [draft, expected] of [
    ['我的回复', '@alice 我的回复'],
    ['@ALICE 我的回复', '@ALICE 我的回复'],
    ['@alice2', '@alice @alice2'],
    ['contact@alice.com', '@alice contact@alice.com'],
    ['`@alice`', '@alice `@alice`'],
    ['> @alice', '@alice > @alice'],
    ['[@alice](https://example.test)', '@alice [@alice](https://example.test)'],
  ]) {
    composer.setDraftText(draft);
    await quote('alice');
    assert.equal(composer.draftText(), expected);
  }
  composer.setDraftText('我的回复');
  await quote('alice');
  assert.equal(root.querySelector('.composerRichInput [data-mention-value]')?.textContent, '@Alice Example');
  await quote('alice');
  assert.equal(composer.draftText(), '@alice 我的回复', 'quoting again does not duplicate a named chip');
  for (const author of ['FIXTURE', 'former-member', 'portal']) {
    composer.setDraftText('保留草稿');
    await quote(author);
    assert.equal(composer.draftText(), '保留草稿');
  }
  composer.setDraftText('继续');
  await quote(resident);
  await quote(resident);
  assert.equal(composer.draftText(), `@${resident} 继续`, 'Resident quote behavior remains, with named-chip deduplication');
});

test('Chatrooms list pinned rooms first and pin from the card menu', async t => {
  const pins = new Map([[ROOMS[1].chatroom_id, '2026-10-01T00:00:00Z']]);
  const page = startWorkbenchPage({
    answers: {
      '/api/me': { username: 'fixture' },
      '/api/agent-workbench/residents': { ready: true, residents: [], lineages: [], principal_hierarchy: { projects: [], user_residents: [] } },
      '/api/ui-state/associations': at => ({ items: at.searchParams.get('namespace') !== NAMESPACE ? []
        : [...pins].map(([key, pinnedAt]) => ({ key, value: { chatroom_id: key, pinned_at: pinnedAt } })) }),
      '/api/ui-state/associations/put': (_at, body) => {
        assert.equal(body.namespace, NAMESPACE);
        assert.equal(body.value.chatroom_id, body.key);
        pins.set(body.key, body.value.pinned_at);
        return { ok: true };
      },
    },
    instructions: {
      ListChatrooms: () => ({ chatrooms: ROOMS }),
      ListUsers: () => ({ users: [{ username: 'alice', display_name: 'Alice' }] }),
    },
  });
  t.after(page.close);
  const cards = () => [...page.document.querySelectorAll('#chatroomList .chatroomCardWrap')];
  const titles = () => cards().map(card => card.querySelector('.agentTitle').textContent);
  await page.until(() => page.document.querySelector('[data-activity-view="chatrooms"]'));
  page.document.querySelector('[data-activity-view="chatrooms"]').click();
  await page.until(() => cards().length === 3);
  assert.deepEqual(titles(), ['Room 2', 'Room 3', 'Room 1'], 'pinned first, then the latest activity');
  assert.match(cards()[1].textContent, /Alice：later/, 'a card shows who said the latest message');
  assert.match(cards()[0].textContent, /2 (位成员|members)/, 'a quiet room shows its members, in the page language');
  assert.equal(page.document.querySelectorAll('#chatroomList .portalNavSectionBody').length, 1, 'one list, no pinned section');
  assert.equal(page.document.querySelector('#chatroomList .portalNavSectionHead'), null, 'the list has no section names');
  assert.deepEqual(cards().map(card => !!card.querySelector('.chatroomCard.pinned .chatroomPinnedMark')), [true, false, false],
    'like the Mailbox, a pinned room carries a pin mark');
  assert.ok(cards()[0].querySelector('.chatroomCard.portalNavRow'), 'a room is a shared navigation row');
  assert.ok(cards()[1].querySelector('.chatroomAvatars [data-avatar-kind="user"][data-avatar-id="alice"]'),
    'a room shows its other members, not the viewer');

  const search = page.document.getElementById('chatroomSearchInput');
  search.value = 'alice';
  search.dispatchEvent(new page.window.Event('input'));
  assert.equal(cards().length, 3, 'the search matches members');
  search.value = 'later';
  search.dispatchEvent(new page.window.Event('input'));
  assert.deepEqual(titles(), ['Room 3'], 'the search matches the latest message');
  search.value = 'nothing-like-this';
  search.dispatchEvent(new page.window.Event('input'));
  assert.equal(cards().length, 0);
  assert.match(page.document.getElementById('chatroomList').textContent, /nothing-like-this/);
  search.value = '';
  search.dispatchEvent(new page.window.Event('input'));
  await page.until(() => cards().length === 3);

  cards()[1].querySelector('[data-chatroom-menu]').click();
  const pin = page.document.querySelector(`.chatroomCardMenu [data-chatroom-action="pin"][data-chatroom-id="${ROOMS[2].chatroom_id}"]`);
  assert.ok(pin, 'the menu offers to pin an unpinned room');
  pin.click();
  await page.until(() => titles()[0] === 'Room 3');
  assert.ok(pins.has(ROOMS[2].chatroom_id), 'the pin is saved as a UI association');
  assert.deepEqual(titles(), ['Room 3', 'Room 2', 'Room 1'], 'pinned rooms order by activity among themselves');
  assert.deepEqual(page.errors, []);
});
