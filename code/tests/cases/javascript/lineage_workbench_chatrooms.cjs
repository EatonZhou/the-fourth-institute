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
  assert.match(cards()[0].textContent, /2 位成员/, 'a quiet room shows its members');
  assert.ok(cards()[0].querySelector('.chatroomPinnedMark'));

  cards()[1].querySelector('[data-chatroom-menu]').click();
  const pin = page.document.querySelector(`.chatroomCardMenu [data-chatroom-action="pin"][data-chatroom-id="${ROOMS[2].chatroom_id}"]`);
  assert.ok(pin, 'the menu offers to pin an unpinned room');
  pin.click();
  await page.until(() => titles()[0] === 'Room 3');
  assert.ok(pins.has(ROOMS[2].chatroom_id), 'the pin is saved as a UI association');
  assert.deepEqual(titles(), ['Room 3', 'Room 2', 'Room 1'], 'pinned rooms order by activity among themselves');
  assert.deepEqual(page.errors, []);
});
