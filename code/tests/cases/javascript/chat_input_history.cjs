'use strict';
// The composer's Arrow history reads what the person typed from the server
// (ListInputHistory with resident_id or chatroom_id, sample_admin 10-16).
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { JSDOM } = require('jsdom');
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
  'portal_command_registry.js', 'portal_workbench_context.js', 'portal_workbench.js', 'resident_protocol.js', 'portal_chat_workspace.js']) {
  w.eval(fs.readFileSync(path.resolve(__dirname, '../../../management_portal/static', name), 'utf8'));
}
const settle = async () => { for (let turn = 0; turn < 5; turn += 1) await new Promise(resolve => setTimeout(resolve, 0)); };
const message = (id, body, extra = {}) => ({ id, message_id: id, role: 'user', author_id: 'sample_admin', content: body, ...extra });

async function arrowHistoryListsQueuedSentThenServerPages() {
  const loads = [];
  const errors = [];
  let firstPage = [message('in-2', 'second input'), message('in-1', 'first input')];
  let otherConversationDown = true;
  let scope = 'conversation-c';
  const workspace = w.PortalChatWorkspace.createWorkspace({
    root: w.document.getElementById('fixture'), id: 'chat', historyScope: () => scope, refs: false,
    onError: error => errors.push(error),
    onLoadInputHistory: async (pageScope, pageToken) => {
      loads.push([pageScope, pageToken]);
      if (pageScope === 'conversation-d') {
        if (otherConversationDown) throw new Error('server unavailable');
        return { messages: [message('d-1', 'other conversation')], next_page_token: '' };
      }
      if (pageToken === 'p2') return { messages: [message('in-0', 'zeroth input')], next_page_token: '' };
      return { messages: firstPage, next_page_token: 'p2' };
    },
    contract: {
      schema: 'portal.chat_workspace.v3', messages: [],
      composer: { can_send: true, queue: { items: [
        { message_id: 'q-theirs', author_id: 'CASE-A', content_parts: [{ type: 'text', text: 'their queued' }] },
        { message_id: 'q-mine', author_id: 'sample_admin', content_parts: [{ type: 'text', text: 'my queued' }] },
      ] } },
    },
  });
  const composer = w.document.querySelector('#fixture .composerInput');
  const steps = async (key, count = 1) => {
    for (let step = 0; step < count; step += 1) {
      composer.dispatchEvent(new w.KeyboardEvent('keydown', { key, bubbles: true, cancelable: true }));
      await settle();
    }
    return composer.textContent.trim();
  };
  const show = messages => { workspace.setMessages(messages, { history: { can_load_older: false } }); return settle(); };
  try {
    const shown = [message('in-1', 'first input'), { ...message('theirs', 'typed by CASE-A'), author_id: 'CASE-A' }, message('in-2', 'second input')];
    await show(shown);
    assert.deepEqual(loads, [['conversation-c', '']], 'the first page loads once the conversation shows');
    composer.focus();
    assert.equal(await steps('ArrowUp'), 'my queued', 'only this person\'s queued input, ahead of the server list');
    assert.equal(await steps('ArrowUp'), 'second input');
    assert.equal(await steps('ArrowUp'), 'first input', 'another member\'s message never enters the history');
    assert.equal(await steps('ArrowUp'), 'zeroth input', 'the end of the list reads the next page');
    assert.equal(await steps('ArrowUp'), 'zeroth input', 'the list ends where the server has no more');
    assert.deepEqual(loads, [['conversation-c', ''], ['conversation-c', 'p2']]);
    assert.equal(await steps('ArrowDown', 4), '', 'Down returns to the empty draft');

    // An input just sent comes first until the server lists it, then shows once.
    await workspace.submit(async () => ({}), { historyText: 'just sent', sentMessageId: () => 'in-3' });
    assert.equal(await steps('ArrowUp'), 'just sent');
    assert.equal(await steps('ArrowUp'), 'my queued');
    assert.equal(await steps('ArrowDown', 2), '');
    firstPage = [message('in-3', 'just sent'), ...firstPage];
    await show([...shown, message('in-3', 'just sent')]);
    assert.deepEqual(loads.at(-1), ['conversation-c', ''], 'a new input of this person reads the first page again');
    assert.equal(await steps('ArrowUp'), 'my queued');
    assert.equal(await steps('ArrowUp'), 'just sent');
    assert.equal(await steps('ArrowUp'), 'second input');
    assert.equal(await steps('ArrowDown', 3), '');

    // The same person sends from another browser: the first page loads again; a reply reads nothing.
    const loadCount = loads.length;
    firstPage = [message('in-4', 'from elsewhere'), ...firstPage];
    await show([...shown, message('in-3', 'just sent'), message('in-4', 'from elsewhere')]);
    assert.equal(loads.length, loadCount + 1);
    await show([...shown, message('in-3', 'just sent'), message('in-4', 'from elsewhere'), { ...message('reply', 'reply'), role: 'agent' }]);
    assert.equal(loads.length, loadCount + 1, 'a reply or another member\'s message reads nothing');

    // Another conversation has its own history; Up reads it again after a failed first read.
    scope = 'conversation-d';
    workspace.setComposer({ can_send: true, queue: { items: [] } });
    await show([]);
    assert.deepEqual(loads.at(-1), ['conversation-d', '']);
    assert.ok(errors.length > 0, 'the failed read is reported');
    otherConversationDown = false;
    composer.focus();
    assert.equal(await steps('ArrowUp'), 'other conversation');
  } finally {
    workspace.destroy();
  }
}

(async () => {
  try {
    await arrowHistoryListsQueuedSentThenServerPages();
    console.log('PASS the Arrow history lists queued and just-sent inputs, then the server pages, per conversation');
  } finally {
    await new Promise(resolve => setImmediate(resolve));
    for (const observer of observers) observer.disconnect();
    w.close();
  }
})().catch(error => {
  console.error(error);
  process.exitCode = 1;
});
