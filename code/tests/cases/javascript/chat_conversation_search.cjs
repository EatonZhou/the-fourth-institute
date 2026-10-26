'use strict';
// Searching a conversation from the Chat header: the reader's own messages
// listed without a query, a query matched in the loaded messages or sent to
// the server search page by page, earlier history loaded on request, a result
// moved to in the message list, and the search reset when the conversation
// changes.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { JSDOM } = require('jsdom');
const { chatWorkspaceScripts } = require('./chat_workspace_modules.cjs');
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const dom = new JSDOM('<section id="local"></section><section id="remote"></section>', {
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
w.Element.prototype.getBoundingClientRect = () => ({ left: 0, top: 0, right: 320, bottom: 120, width: 320, height: 120 });
w.eval(input.routeScript);
for (const name of ['portal_state.js', 'portal_page_state.js', 'portal_text.js', 'portal_ui_primitives.js', 'portal_ui.js',
  'portal_overlay.js', 'portal_resource_chips.js', 'portal_image_editor.js', 'portal_editor_open_service.js',
  'portal_command_registry.js', 'portal_workbench_context.js', 'portal_workbench.js', 'resident_protocol.js', ...chatWorkspaceScripts()]) {
  w.eval(fs.readFileSync(path.resolve(__dirname, '../../../management_portal/static', name), 'utf8'));
}
const settle = async () => { for (let turn = 0; turn < 8; turn += 1) await new Promise(resolve => setTimeout(resolve, 0)); };
const debounce = () => new Promise(resolve => setTimeout(resolve, 260));
const text = node => (node?.textContent || '').replace(/\s+/g, ' ').trim();
const deferred = () => {
  let resolve, reject;
  const promise = new Promise((done, fail) => { resolve = done; reject = fail; });
  return { promise, resolve, reject };
};
const toasts = [];
w.PortalUI.toast = (message, tone) => toasts.push([String(message), tone]);

const at = minute => `2026-10-26T01:${String(minute).padStart(2, '0')}:00Z`;
const human = (id, content, minute) => ({ id, role: 'user', content, name: 'Ana', created_at: at(minute) });
const agent = (id, content, minute) => ({ id, role: 'assistant', content, name: 'Builder', created_at: at(minute) });
const contract = { schema: 'portal.chat_workspace.v3', messages: [], composer: { can_send: true, queue: { items: [] } } };

function parts(root) {
  const panel = w.document.querySelector(`[data-search-owner="${root.id}"]`);
  return {
    trigger: root.querySelector('[data-workspace-history-search]'),
    panel,
    input: panel.querySelector('input'),
    status: panel.querySelector('[role="status"]'),
    more: panel.querySelector('.conversationSearchMore'),
    keys: () => [...panel.querySelectorAll('[data-search-message]')].map(button => button.dataset.searchMessage),
    result: key => panel.querySelector(`[data-search-message="${key}"]`),
  };
}
const type = (field, value) => {
  field.value = value;
  field.dispatchEvent(new w.Event('input', { bubbles: true }));
};
// Tells each Chat's panel apart once the popover has moved it out of the root.
const tagPanels = root => {
  for (const panel of w.document.querySelectorAll('.portalConversationSearch:not([data-search-owner])')) panel.dataset.searchOwner = root.id;
};

async function localSearch() {
  const root = w.document.getElementById('local');
  let scope = 'conversation-a';
  let older = 0;
  const long = `${'filler '.repeat(40)}the needle sits here ${'tail '.repeat(40)}`;
  const messages = [
    human('m1', 'Please fix the login bug in the form', 1),
    agent('m2', 'The login handler drops the token', 2),
    human('m3', '   ', 3),
    human('m4', 'Add tests for LOGIN later', 4),
    human('m5', 'A message from another Resident', 5),
    human('m6', long, 6),
  ];
  const workspace = w.PortalChatWorkspace.createWorkspace({
    root, id: 'chatLocal', historyScope: () => scope, contract,
    renderMessageBody: w.PortalChatWorkspace.residentMessageBody,
    formatTime: value => `at ${value.slice(11, 16)}`,
    messageClass: item => (item.id === 'm5' ? 'messageIncoming' : ''),
    onLoadOlder: async () => {
      older += 1;
      workspace.setMessages([human('m0', 'An earlier login question', 0), ...messages], { history: { can_load_older: false } });
    },
  });
  tagPanels(root);
  const ui = parts(root);
  try {
    workspace.setMessages(messages, { history: { can_load_older: true } });
    await settle();
    ui.trigger.click();
    await settle();
    assert.equal(ui.trigger.getAttribute('aria-expanded'), 'true', 'the header button opens the search');
    assert.deepEqual(ui.keys(), ['m6', 'm4', 'm1'], 'without a query the reader’s own messages are listed, newest first');
    assert.equal(text(ui.status), '对话片段 · 3');
    assert.equal(text(ui.result('m4').querySelector('.conversationSearchMeta')), 'Anaat 01:04');
    assert.equal(ui.more.hidden, false);
    assert.equal(text(ui.more), '加载更早片段');

    type(ui.input, 'login');
    assert.equal(text(ui.status), '没有匹配的片段', 'a new query clears the old results at once');
    await debounce();
    assert.deepEqual(ui.keys(), ['m4', 'm2', 'm1'], 'a query matches every loaded message, ignoring case');
    assert.equal(text(ui.status), '匹配 · 3');
    assert.deepEqual([...ui.result('m4').querySelectorAll('mark')].map(text), ['LOGIN']);
    assert.equal(ui.result('m1').querySelector('.conversationSearchExcerpt').innerHTML,
      'Please fix the <mark>login</mark> bug in the form');

    type(ui.input, 'needle');
    await debounce();
    const excerpt = text(ui.result('m6').querySelector('.conversationSearchExcerpt'));
    assert.match(excerpt, /^…/, 'a match deep in a long message starts the excerpt near it');
    assert.match(excerpt, /the needle sits here/);
    assert.ok(excerpt.length < long.trim().length);

    type(ui.input, 'login');
    await debounce();
    ui.result('m1').click();
    await settle();
    assert.equal(ui.trigger.getAttribute('aria-expanded'), 'false', 'choosing a result closes the search');
    assert.ok(root.querySelector('.messageList > [data-message-key="m1"]').classList.contains('messageQuoteTarget'),
      'and moves to the message');

    ui.trigger.click();
    await settle();
    assert.deepEqual(ui.keys(), ['m4', 'm2', 'm1'], 'the query is kept when the search opens again');
    ui.input.dispatchEvent(new w.KeyboardEvent('keydown', { key: 'ArrowDown', bubbles: true }));
    assert.equal(w.document.activeElement, ui.result('m4'), 'arrow keys move through the results');
    ui.input.focus();
    ui.input.dispatchEvent(new w.KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
    await settle();
    assert.ok(root.querySelector('.messageList > [data-message-key="m4"]').classList.contains('messageQuoteTarget'),
      'Enter opens the first result');

    ui.trigger.click();
    await settle();
    type(ui.input, '');
    await debounce();
    assert.equal(text(ui.more), '加载更早片段');
    ui.more.click();
    await settle();
    assert.equal(older, 1, 'the button loads earlier history');
    assert.deepEqual(ui.keys(), ['m6', 'm4', 'm1', 'm0'], 'and the list shows what it brought');
    assert.equal(ui.more.hidden, true, 'nothing earlier, no button');

    scope = 'conversation-b';
    type(ui.input, 'login');
    workspace.setMessages([human('n1', 'Another conversation', 1)], { history: { can_load_older: false } });
    await settle();
    assert.equal(ui.trigger.getAttribute('aria-expanded'), 'false', 'another conversation closes the search');
    assert.equal(ui.input.value, '', 'and clears the query');
    await debounce();
    ui.trigger.click();
    await settle();
    assert.deepEqual(ui.keys(), ['n1']);
  } finally {
    workspace.destroy();
  }
  assert.equal(ui.panel.isConnected, false, 'destroying the Chat removes the search');
}

async function serverSearch() {
  const root = w.document.getElementById('remote');
  const searches = [];
  const workspace = w.PortalChatWorkspace.createWorkspace({
    root, id: 'chatRemote', historyScope: () => 'conversation-remote', contract,
    renderMessageBody: w.PortalChatWorkspace.residentMessageBody,
    onSearchMessages: (query, options) => {
      const call = { query, options: { ...options }, reply: deferred() };
      searches.push(call);
      return call.reply.promise;
    },
  });
  tagPanels(root);
  const ui = parts(root);
  try {
    workspace.setMessages([human('s1', 'deploy the build', 1)], { history: { can_load_older: false } });
    await settle();
    ui.trigger.click();
    await settle();
    type(ui.input, 'dep');
    type(ui.input, 'deploy');
    assert.equal(text(ui.status), '正在查找…');
    await debounce();
    assert.deepEqual(searches.map(call => [call.query, call.options]), [['deploy', { pageToken: '', limit: 50 }]],
      'typing is sent once it pauses');
    searches[0].reply.resolve({ messages: [human('s1', 'deploy the build', 1), agent('s2', 'deploy failed', 2)], next_page_token: 'page-2' });
    await settle();
    assert.deepEqual(ui.keys(), ['s1', 's2'], 'server results keep the server order');
    assert.equal(text(ui.status), '匹配 · 2');
    assert.equal(ui.more.hidden, false);
    assert.equal(text(ui.more), '加载更多结果');

    ui.more.click();
    assert.deepEqual(searches[1].options, { pageToken: 'page-2', limit: 50 }, 'the next page continues from the token');
    assert.equal(ui.more.disabled, true, 'while the page loads');
    ui.more.click();
    assert.equal(searches.length, 2, 'a second click while loading sends nothing');
    searches[1].reply.resolve({ messages: [agent('s2', 'deploy failed', 2), human('s3', 'deploy again', 3)], next_page_token: '' });
    await settle();
    assert.deepEqual(ui.keys(), ['s1', 's2', 's3'], 'a result already listed is not repeated');
    assert.equal(ui.more.hidden, true, 'the last page has no button');

    type(ui.input, 'first');
    await debounce();
    type(ui.input, 'second');
    await debounce();
    searches[2].reply.resolve({ messages: [human('s4', 'first answer', 4)], next_page_token: 'stale-page' });
    await settle();
    searches[3].reply.resolve({ messages: [human('s5', 'second answer', 5)] });
    await settle();
    assert.deepEqual(ui.keys(), ['s5'], 'a reply for an older query is dropped');
    assert.equal(ui.more.hidden, true, 'and so is its next page');

    type(ui.input, 'broken');
    await debounce();
    searches[4].reply.resolve({ messages: [human('s8', 'broken build', 8)], next_page_token: 'page-b2' });
    await settle();
    ui.more.click();
    assert.deepEqual(searches[5].options, { pageToken: 'page-b2', limit: 50 });
    searches[5].reply.reject(new Error('search offline'));
    await settle();
    assert.equal(text(ui.status), '查找失败：search offline');
    assert.equal(text(ui.more), '重试');
    ui.more.click();
    assert.deepEqual([searches[6].query, searches[6].options], ['broken', { pageToken: '', limit: 50 }],
      'retry after a failed page starts the query again');
    searches[6].reply.resolve({ messages: [human('s9', 'broken link', 9)] });
    await settle();
    assert.deepEqual(ui.keys(), ['s9'], 'the retried query lists only its own results');
    ui.result('s9').click();
    await settle();
    assert.deepEqual(toasts.at(-1), ['这条消息已不在当前对话中。', 'warning'], 'a result outside the loaded conversation is reported');
  } finally {
    workspace.destroy();
  }
}

(async () => {
  try {
    await localSearch();
    await serverSearch();
  } finally {
    observers.forEach(observer => observer.disconnect());
    await settle();
    w.close();
  }
})().catch(error => {
  console.error(error);
  process.exitCode = 1;
});
