'use strict';
// The way back through a Chat's conversation: the load-earlier button, the Fork
// card's opening compaction, quote blocks that go to the quoted message (loading
// earlier history until it is there), and the quote bar of the message being
// written, one per conversation. A destroyed Chat answers none of these clicks.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { JSDOM } = require('jsdom');
const { chatWorkspaceScripts } = require('./chat_workspace_modules.cjs');
const { portalWorkbenchScripts } = require('./portal_workbench_scripts.cjs');
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const dom = new JSDOM('<main id="fixtures"></main>', {
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
const toasts = [];
w.PortalUI.toast = (message, tone) => toasts.push([String(message), tone]);
// Long enough for the animation frames a reveal waits for.
const settle = () => new Promise(resolve => setTimeout(resolve, 60));
const take = list => list.splice(0);
const CONTRACT = { schema: 'portal.chat_workspace.v3', messages: [], composer: { can_send: true, queue: { items: [] } } };
const at = (() => { let minute = 0; return () => `2026-10-27T08:${String(minute++).padStart(2, '0')}:00Z`; })();
const human = (id, content = `${id} asks`, extra = {}) => ({ id, role: 'user', content, created_at: at(), ...extra });
const agent = (id, content = `${id} answers`, extra = {}) => ({ id, role: 'assistant', content, created_at: at(), ...extra });
const INHERITANCE = {
  source_resident_id: 'resident_aaaaaaaaaaaa', forked_from_at: '2026-10-27T07:00:00Z', fork_mode: 'context',
  opening_compaction: { created_at: '2026-10-27T06:00:00Z' }, message_count: 2, recorded: true,
};
const errors = [];
const workspaces = [];
const fresh = (name, extra = {}) => {
  const root = w.document.createElement('section');
  w.document.getElementById('fixtures').append(root);
  let scope = name;
  const workspace = w.PortalChatWorkspace.createWorkspace({
    root, id: name, historyScope: () => scope, draftOwner: 'viewer', draftSaveDelay: 600_000, contract: CONTRACT,
    onError: error => errors.push(error.message),
    ...extra,
  });
  workspaces.push(workspace);
  const field = root.querySelector('.composerInput');
  const one = selector => root.querySelector(selector);
  const row = key => root.querySelector(`.messageList > [data-message-key="${key}"]`);
  // Another conversation is shown in the same Chat.
  const show = (next, messages = [], renderOptions = {}) => {
    scope = next;
    workspace.setMessages(messages, renderOptions);
  };
  const destroy = () => {
    workspaces.splice(workspaces.indexOf(workspace), 1);
    workspace.destroy();
  };
  return { workspace, root, field, one, row, show, destroy };
};

(async () => {
  try {
    {
      // The load-earlier button asks the host once, and says so while it loads.
      const loads = [];
      const { workspace, one } = fresh('older', {
        onLoadOlder: view => new Promise((resolve, reject) => loads.push({ view, resolve, reject })),
      });
      workspace.setMessages([human('h2'), agent('a3')], { history: { can_load_older: true } });
      const loader = () => one('[data-workspace-load-older]');
      assert.equal(loader().textContent, '加载更早消息');
      loader().click();
      assert.equal(loads.length, 1);
      assert.equal(loads[0].view, workspace, 'the host is told which Chat');
      assert.equal(loader().disabled, true);
      assert.equal(loader().textContent, '正在加载…');
      loader().dispatchEvent(new w.MouseEvent('click', { bubbles: true }));
      assert.equal(loads.length, 1, 'a click while it loads asks nothing more');
      loads.shift().resolve();
      await settle();
      assert.equal(loader().disabled, false, 'once loaded it can be asked again');
      assert.equal(loader().textContent, '加载更早消息');
      loader().click();
      loads.shift().reject(new Error('offline'));
      await settle();
      assert.deepEqual(take(errors), ['offline'], 'a failed load goes to the host');
      assert.equal(loader().disabled, false);
      loader().click();
      workspace.setMessages([human('h0'), agent('a1'), human('h2'), agent('a3')], { history: { can_load_older: false } });
      loads.shift().resolve();
      await settle();
      assert.equal(loader(), null, 'with nothing earlier left there is no button');
    }
    {
      // Without a host error handler a failed load is a toast; without a loader the button does nothing.
      const { workspace, one } = fresh('older-toast', { onError: undefined, onLoadOlder: () => Promise.reject(new Error('offline')) });
      workspace.setMessages([human('h2')], { history: { can_load_older: true } });
      take(toasts);
      one('[data-workspace-load-older]').click();
      await settle();
      assert.deepEqual(take(toasts), [['offline', 'error']]);
      const plain = fresh('older-none');
      plain.workspace.setMessages([human('h2')], { history: { can_load_older: true } });
      plain.one('[data-workspace-load-older]').click();
      assert.equal(plain.one('[data-workspace-load-older]').disabled, false);
    }
    {
      // The Fork card reads its opening compaction once, when it is first opened.
      const reads = [];
      const { workspace, one, show } = fresh('fork', {
        onLoadInheritedContext: view => new Promise((resolve, reject) => reads.push({ view, resolve, reject })),
      });
      workspace.setMessages([human('h1'), agent('a2')], { inheritance: INHERITANCE });
      const summary = () => one('[data-chat-inheritance-summary]');
      const card = () => one('[data-message-key="__inheritance__"]');
      assert.equal(summary().getAttribute('aria-expanded'), 'false');
      summary().click();
      assert.equal(reads.length, 1);
      assert.equal(reads[0].view, workspace);
      assert.equal(summary().getAttribute('aria-expanded'), 'true');
      assert.match(card().textContent, /正在读取压缩摘要/);
      summary().click();
      assert.equal(summary().getAttribute('aria-expanded'), 'false', 'it closes while it reads');
      summary().click();
      assert.equal(reads.length, 1, 'opening it again does not read again');
      reads.shift().resolve({ opening_compaction: { items: [{ role: 'agent', text: 'the opening summary' }] } });
      await settle();
      assert.match(card().textContent, /the opening summary/);
      summary().click();
      summary().click();
      assert.equal(reads.length, 0, 'what was read is kept');
      assert.match(card().textContent, /the opening summary/);
      workspace.setMessages([human('h1'), agent('a2'), human('h3')], { inheritance: INHERITANCE });
      assert.equal(summary().getAttribute('aria-expanded'), 'true', 'it stays open when the list is drawn again');
      show('fork-other', [human('o1')], { inheritance: INHERITANCE });
      assert.equal(summary().getAttribute('aria-expanded'), 'false', 'another conversation has its own card');
      summary().click();
      show('fork-third', [human('t1')], { inheritance: INHERITANCE });
      summary().focus();
      reads.shift().resolve({ opening_compaction: { items: [{ role: 'agent', text: 'late summary' }] } });
      await settle();
      assert.equal(summary().getAttribute('aria-expanded'), 'false', 'a late answer stays with its own conversation');
      assert.doesNotMatch(card().textContent, /late summary/);
      assert.equal(w.document.activeElement, summary(), 'and the card shown keeps its focus');
    }
    {
      // A Fork card whose compaction cannot be read says why, and reading again is a click away.
      const reads = [];
      const { workspace, one } = fresh('fork-error', {
        onLoadInheritedContext: () => new Promise((resolve, reject) => reads.push({ resolve, reject })),
      });
      workspace.setMessages([human('h1')], { inheritance: INHERITANCE });
      const summary = () => one('[data-chat-inheritance-summary]');
      const card = () => one('[data-message-key="__inheritance__"]');
      summary().click();
      reads.shift().resolve({});
      await settle();
      assert.match(card().textContent, /读取压缩摘要失败：这次 Fork 已没有可读取的压缩。/);
      summary().click();
      summary().click();
      assert.equal(reads.length, 1, 'opening it again reads again');
      reads.shift().reject(new Error('source gone'));
      await settle();
      assert.match(card().textContent, /读取压缩摘要失败：source gone/);
    }
    {
      // The quote bar shows the quote of the conversation shown, and its cancel drops it.
      const { workspace, field, one, show } = fresh('quote');
      const bar = () => one('[data-composer-quote-host]');
      assert.equal(bar().hidden, true);
      field.blur();
      assert.equal(workspace.setQuote({ message_id: 'a1', author_id: 'resident_bbbbbbbbbbbb', text: 'the answer' }), true);
      assert.equal(bar().hidden, false);
      assert.match(bar().textContent, /the answer/);
      assert.equal(w.document.activeElement, field, 'writing goes on in the input');
      assert.deepEqual({ ...workspace.quote() }, { message_id: 'a1', author_id: 'resident_bbbbbbbbbbbb', text: 'the answer' });
      assert.equal(workspace.setQuote({ message_id: 'a2', text: ' ' }), false, 'a quote needs text');
      assert.equal(workspace.setQuote({ text: 'other' }), false, 'and the message it quotes');
      assert.equal(workspace.quote().message_id, 'a1');
      assert.equal(workspace.clearQuote({ messageId: 'a2' }), false, 'clearing for another message leaves it');
      assert.equal(bar().hidden, false);
      show('quote-other');
      assert.equal(bar().hidden, true, 'another conversation has its own quote');
      assert.equal(workspace.quote(), null);
      assert.equal(workspace.clearQuote(), false);
      show('quote');
      assert.equal(bar().hidden, false, 'and coming back shows this one again');
      assert.equal(workspace.clearQuote({ scope: 'quote-other' }), false);
      field.blur();
      one('[data-composer-quote-cancel]').click();
      assert.equal(bar().hidden, true, 'the cancel drops it');
      assert.equal(workspace.quote(), null);
      assert.equal(w.document.activeElement, field, 'and writing goes on');
      workspace.setQuote({ message_id: 'a1', text: 'the answer' });
      assert.equal(workspace.clearQuote({ messageId: 'a1' }), true);
      assert.equal(bar().hidden, true);
    }
    {
      // A quote block goes to the quoted message, loading earlier history until it is there.
      let loads = 0;
      const older = [human('h0'), agent('a1', 'first answer')];
      const recent = [human('h2', 'about that', { quote: { message_id: 'a1', author_id: 'resident_bbbbbbbbbbbb', text: 'first answer' } })];
      const { workspace, one, row } = fresh('jump', {
        onLoadOlder: view => {
          loads += 1;
          view.setMessages([...older, ...recent], { history: { can_load_older: false } });
        },
      });
      workspace.setMessages(recent, { history: { can_load_older: true } });
      one('[data-message-quote="a1"]').click();
      await settle();
      assert.equal(loads, 1);
      assert.ok(row('a1').classList.contains('messageQuoteTarget'), 'the quoted message is shown');
      one('[data-message-quote="a1"]').click();
      await settle();
      assert.equal(loads, 1, 'a message already there is not loaded again');
      assert.ok(row('a1').classList.contains('messageQuoteTarget'));
      take(toasts);
      assert.equal(await workspace.revealMessage(' '), false, 'there is nothing to find without an ID');
      assert.equal(loads, 1);
      assert.deepEqual(take(toasts), [], 'and nothing to say');
      workspace.setMessages([human('h3', 'about something gone', { quote: { message_id: 'gone', text: 'gone' } })]);
      one('[data-message-quote="gone"]').click();
      await settle();
      assert.deepEqual(take(toasts), [['这条消息已不在当前对话中。', 'warning']]);
      assert.equal(loads, 1, 'with nothing earlier left nothing is loaded');
      const failing = fresh('jump-error', { onLoadOlder: () => Promise.reject(new Error('offline')) });
      failing.workspace.setMessages(recent, { history: { can_load_older: true } });
      failing.one('[data-message-quote="a1"]').click();
      await settle();
      assert.deepEqual(take(errors), ['offline'], 'a failed load goes to the host');
      const quiet = fresh('jump-toast', { onError: undefined, onLoadOlder: () => Promise.reject(new Error('offline')) });
      quiet.workspace.setMessages(recent, { history: { can_load_older: true } });
      quiet.one('[data-message-quote="a1"]').click();
      await settle();
      assert.deepEqual(take(toasts), [['offline', 'error']], 'or, without a handler, to a toast');
    }
    {
      // Finding a message waits for a host that draws the earlier page a frame later,
      // stops when a load brings nothing earlier, and stops quietly when another conversation is shown.
      const older = [human('h0'), agent('a1', 'first answer')];
      const recent = [human('h2', 'about that')];
      const later = fresh('find-later', {
        onLoadOlder: view => {
          w.requestAnimationFrame(() => view.setMessages([...older, ...recent], { history: { can_load_older: false } }));
        },
      });
      later.workspace.setMessages(recent, { history: { can_load_older: true } });
      assert.equal(await later.workspace.revealMessage('a1'), true);
      assert.ok(later.row('a1').classList.contains('messageQuoteTarget'));
      let stuckLoads = 0;
      const stuck = fresh('find-stuck', { onLoadOlder: () => { stuckLoads += 1; } });
      stuck.workspace.setMessages(recent, { history: { can_load_older: true } });
      take(toasts);
      assert.equal(await stuck.workspace.revealMessage('a1'), false);
      assert.equal(stuckLoads, 1, 'a load that brings nothing earlier ends the search');
      assert.deepEqual(take(toasts), [['这条消息已不在当前对话中。', 'warning']]);
      const moved = fresh('find-moved', {
        onLoadOlder: () => { moved.show('find-elsewhere', [human('e1')], { history: { can_load_older: true } }); },
      });
      moved.workspace.setMessages(recent, { history: { can_load_older: true } });
      assert.equal(await moved.workspace.revealMessage('a1'), false);
      assert.deepEqual(take(toasts), [], 'another conversation shown ends it quietly');
    }
    {
      // A Chat destroyed in the middle of finding a message stops looking.
      const pending = [];
      const { workspace, destroy } = fresh('leaving', { onLoadOlder: () => new Promise(resolve => pending.push(resolve)) });
      workspace.setMessages([human('h2')], { history: { can_load_older: true } });
      take(toasts);
      const reveal = workspace.revealMessage('h0');
      await settle();
      destroy();
      pending.shift()();
      assert.equal(await reveal, false);
      assert.deepEqual(take(toasts), []);
    }
    {
      // A destroyed Chat answers none of these clicks.
      const calls = [];
      const paging = fresh('destroyed', { onLoadOlder: () => { calls.push('older'); } });
      paging.workspace.setMessages([human('h1', 'asks', { quote: { message_id: 'h0', text: 'earlier' } })],
        { history: { can_load_older: true } });
      paging.workspace.setQuote({ message_id: 'h1', text: 'asks' });
      paging.destroy();
      take(toasts);
      paging.one('[data-workspace-load-older]').click();
      paging.one('[data-message-quote="h0"]').click();
      paging.one('[data-composer-quote-cancel]').click();
      // The Fork card shows once nothing earlier is left to load.
      const forked = fresh('destroyed-fork', { onLoadInheritedContext: () => { calls.push('fork'); return {}; } });
      forked.workspace.setMessages([human('h1')], { inheritance: INHERITANCE });
      forked.destroy();
      forked.one('[data-chat-inheritance-summary]').click();
      await settle();
      assert.deepEqual(calls, [], 'the host is asked nothing');
      assert.deepEqual(take(toasts), []);
      assert.equal(forked.one('[data-chat-inheritance-summary]').getAttribute('aria-expanded'), 'false');
      assert.equal(paging.one('[data-composer-quote-host]').hidden, false, 'and its quote stays as it was');
    }
    {
      const pinned = fresh('pins', {pinMessages: true});
      const selected = agent('pin-answer', 'pin-answer selected segment', {
        message_id: 'pin-answer', origin: {sender: {id: 'resident_selected'}},
        family_reply: {request_message_id: 'pin-question'},
      });
      const before = agent('before', 'before segment', {message_id: 'before', family_reply: {request_message_id: 'pin-question'}});
      const after = agent('after', 'after segment', {message_id: 'after', family_reply: {request_message_id: 'pin-question'}});
      pinned.workspace.setMessages([human('pin-question'), before, selected, after]);
      const target = pinned.workspace.messageQuoteTarget('pin-answer');
      assert.equal(target.author_id, 'resident_selected');
      pinned.workspace.toggleMessagePin('pin-answer');
      assert.match(pinned.one('.workspacePinnedMessages').textContent, /pin-answer/);
      assert.doesNotMatch(pinned.one('.workspacePinnedMessages').textContent, /before segment|after segment/);
      pinned.one('[data-pinned-action="quote"]').click();
      assert.equal(JSON.stringify(pinned.workspace.quote()), JSON.stringify(target), 'pin and quote preserve the identical message ID, author and excerpt');
      pinned.show('other-pins');
      assert.equal(pinned.one('.workspacePinnedMessages').hidden, true);
      pinned.show('pins', [human('pin-question'), agent('pin-answer')]);
      assert.equal(pinned.one('.workspacePinnedMessages').hidden, false);
      assert.equal(pinned.one('[data-pinned-action="jump"]').title, '跳到置顶消息');
      pinned.destroy();
      const restored = fresh('pins', {pinMessages: true});
      restored.workspace.setMessages([agent('pin-answer')]);
      assert.equal(restored.one('.workspacePinnedMessages').hidden, false, 'pins survive remount');
      restored.one('[data-pinned-action="remove"]').click();
      assert.equal(restored.one('.workspacePinnedMessages').hidden, true);
      restored.workspace.setMessages([agent('pending', 'unfinished', {pending: true, message_id: 'pending'})]);
      restored.workspace.toggleMessagePin('pending');
      assert.equal(restored.one('.workspacePinnedMessages').hidden, true, 'an unquotable reply cannot be pinned');
      restored.destroy();
    }
    console.log('PASS the load-earlier button, Fork card, quote jumps, pins and quote bar');
  } finally {
    await settle();
    for (const workspace of workspaces) workspace.destroy();
    for (const observer of observers) observer.disconnect();
    w.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
