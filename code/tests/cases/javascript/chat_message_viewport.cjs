'use strict';
// The message list's viewport: which scrolls are the reader's, following the
// tail, and keeping the reading place while Workbench Panels change its size.
// JSDOM has no layout, so the list gets a small one: a 300px viewport and rows
// stacked by their heights. Animation frames run only when the test flushes them.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { JSDOM } = require('jsdom');
const { chatWorkspaceScripts } = require('./chat_workspace_modules.cjs');
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
let frames = new Map();
let nextFrame = 1;
w.requestAnimationFrame = callback => { const id = nextFrame++; frames.set(id, callback); return id; };
w.cancelAnimationFrame = id => { frames.delete(id); };
const flushFrame = () => {
  const due = [...frames.values()];
  frames = new Map();
  for (const callback of due) callback(0);
};
w.eval(input.routeScript);
for (const name of ['portal_state.js', 'portal_page_state.js', 'portal_text.js', 'portal_ui_primitives.js', 'portal_ui.js',
  'portal_overlay.js', 'portal_resource_chips.js', 'portal_image_editor.js', 'portal_editor_open_service.js',
  'portal_command_registry.js', 'portal_workbench_context.js', 'portal_workbench.js', 'resident_protocol.js', ...chatWorkspaceScripts()]) {
  w.eval(fs.readFileSync(path.resolve(__dirname, '../../../management_portal/static', name), 'utf8'));
}
const conversation = (prefix, count = 10) => Array.from({ length: count }, (_, index) => ({
  id: `${prefix}${index}`, role: index % 2 ? 'assistant' : 'user', content: `${prefix} message ${index}`,
  created_at: `2026-10-26T08:${String(index).padStart(2, '0')}:00Z`,
}));

(async () => {
  const fixture = w.document.getElementById('fixture');
  let scope = 'conversation-a';
  const workspace = w.PortalChatWorkspace.createWorkspace({
    root: fixture, id: 'chat', historyScope: () => scope, refs: false, can_attach: false,
  });
  const view = workspace.els.messages;
  const VIEWPORT = 300;
  const heights = new Map();
  let rowHeight = 100;
  const rows = () => [...view.querySelectorAll('.messageList > [data-message-key]')];
  const heightOf = row => (row.dataset.messageKey === '__scroll_anchor__' ? 0 : heights.get(row.dataset.messageKey) ?? rowHeight);
  const contentHeight = () => rows().reduce((sum, row) => sum + heightOf(row), 0);
  const maxScroll = () => Math.max(0, contentHeight() - VIEWPORT);
  let scrollTop = 0;
  Object.defineProperties(view, {
    clientHeight: { configurable: true, get: () => VIEWPORT },
    scrollHeight: { configurable: true, get: () => Math.max(VIEWPORT, contentHeight()) },
    scrollTop: {
      configurable: true,
      get: () => Math.min(scrollTop, maxScroll()),
      set: value => { scrollTop = Math.max(0, Math.min(Number(value) || 0, maxScroll())); },
    },
    getClientRects: { configurable: true, value: () => [{ top: 0, height: VIEWPORT }] },
  });
  const box = (top, height) => ({ top, bottom: top + height, height, left: 0, right: 400, width: 400, x: 0, y: top });
  const nativeBox = w.Element.prototype.getBoundingClientRect;
  w.Element.prototype.getBoundingClientRect = function getBoundingClientRect() {
    if (this === view) return box(0, VIEWPORT);
    if (this.parentElement?.classList.contains('messageList') && this.dataset.messageKey && view.contains(this)) {
      let top = 0;
      for (const row of rows()) {
        if (row === this) return box(top - view.scrollTop, heightOf(row));
        top += heightOf(row);
      }
    }
    return nativeBox.call(this);
  };
  const row = key => view.querySelector(`.messageList > [data-message-key="${key}"]`);
  const topOf = key => row(key).getBoundingClientRect().top;
  const following = () => {
    const tail = view.classList.contains('is-following-tail');
    assert.equal(view.classList.contains('is-reading-history'), !tail, 'the list is in exactly one scroll mode');
    return tail;
  };
  const fire = (target, type, init = {}) => target.dispatchEvent(new w.Event(type, { bubbles: true, ...init }));
  // The browser moves the list, then reports the scroll.
  const scrollTo = top => { view.scrollTop = top; fire(view, 'scroll', { bubbles: false }); };
  const userScrollTo = top => { fire(view, 'wheel'); scrollTo(top); };
  const pressKey = name => view.dispatchEvent(new w.KeyboardEvent('keydown', { bubbles: true, key: name }));
  const beforeLayout = () => fixture.dispatchEvent(new w.CustomEvent('portal:workbench-before-layout', { detail: {} }));
  const resized = () => fixture.dispatchEvent(new w.CustomEvent('portal:workbench-resize', { detail: {} }));
  try {
    assert.equal(following(), true, 'a new Workspace starts following the tail');
    workspace.setMessages(conversation('a'));
    flushFrame();
    assert.deepEqual(rows().filter(item => item.dataset.messageKey !== '__scroll_anchor__').map(item => item.dataset.messageKey),
      conversation('a').map(item => item.id), 'the fixture lays out one row per message');
    assert.equal(view.scrollTop, 700, 'a new conversation opens at its tail');
    assert.equal(following(), true, 'and follows the tail');

    // Which scrolls are the reader's.
    scrollTo(140);
    assert.equal(following(), true, 'a scroll no gesture caused leaves the mode alone');
    userScrollTo(140);
    assert.equal(following(), false, 'a wheel scroll away from the tail reads history');
    scrollTo(700);
    assert.equal(following(), false, 'a wheel gesture accounts for one scroll only');
    pressKey('a');
    scrollTo(700);
    assert.equal(following(), false, 'a key that does not scroll is no gesture');
    let top = 0;
    for (const name of ['End', 'Home', 'PageDown', 'PageUp', 'ArrowDown', 'ArrowUp', ' ']) {
      top = top ? 0 : 700;
      pressKey(name);
      scrollTo(top);
      assert.equal(following(), top === 700, `"${name}" is a scroll gesture`);
    }

    assert.equal(following(), true);
    fire(row('a5'), 'pointerdown');
    scrollTo(140);
    assert.equal(following(), true, 'a press on a message is no scroll gesture');
    fire(view, 'pointerdown');
    scrollTo(140);
    assert.equal(following(), false, 'dragging the scrollbar away from the tail reads history');
    scrollTo(700);
    assert.equal(following(), true, 'the drag keeps counting while the pointer is down');
    fire(w, 'pointerup', { bubbles: false });
    scrollTo(140);
    assert.equal(following(), false, 'the scroll that finishes the drag still counts');
    scrollTo(700);
    assert.equal(following(), false, 'after the drag, scrolls no longer count');
    fire(view, 'pointerdown');
    fire(w, 'pointercancel', { bubbles: false });
    scrollTo(700);
    scrollTo(140);
    assert.equal(following(), true, 'a cancelled pointer ends the drag too');

    fire(view, 'touchstart');
    scrollTo(140);
    assert.equal(following(), false, 'a touch is a scroll gesture');
    scrollTo(700);
    assert.equal(following(), true, 'a touch counts every scroll until it ends');
    fire(view, 'touchend');
    scrollTo(140);
    scrollTo(700);
    assert.equal(following(), false, 'after the touch ends, only its last scroll counted');
    fire(view, 'touchmove');
    scrollTo(700);
    assert.equal(following(), true, 'a moving touch is a scroll gesture');
    fire(view, 'touchcancel');
    scrollTo(140);
    scrollTo(700);
    assert.equal(following(), false, 'a cancelled touch ends it');

    // Opening another conversation forgets a gesture in progress.
    fire(view, 'pointerdown');
    scope = 'conversation-b';
    workspace.setMessages(conversation('b'));
    flushFrame();
    assert.equal(following(), true, 'another conversation opens following its tail');
    assert.equal(view.scrollTop, 700);
    scrollTo(140);
    assert.equal(following(), true, 'the pointer pressed in the previous conversation no longer drags');

    // Workbench layout: the first message whose top edge is in view keeps its offset.
    userScrollTo(140);
    view.style.overflowAnchor = 'auto';
    assert.equal(topOf('b2'), 60, 'b1 is cut at the top, b2 starts 60px down');
    beforeLayout();
    assert.equal(view.style.overflowAnchor, 'none', 'the browser stops anchoring while the Panels lay out');
    rowHeight = 50;
    beforeLayout();
    resized();
    assert.equal(view.scrollTop, 140, 'the place is restored on the next frame');
    flushFrame();
    assert.equal(topOf('b2'), 60, 'narrower rows keep b2 where the reader saw it');
    assert.equal(view.scrollTop, 40, 'the second layout of the same change kept the first capture');
    assert.equal(view.style.overflowAnchor, 'none', 'the place is checked once more before anchoring returns');
    flushFrame();
    assert.equal(view.style.overflowAnchor, 'auto', 'the browser anchors again once the place is settled');
    assert.equal(following(), false);
    // Closing the Panel brings the same message back, though b1 now starts in view.
    assert.equal(topOf('b1') > 0, true);
    beforeLayout();
    rowHeight = 100;
    resized();
    flushFrame();
    flushFrame();
    assert.equal(view.scrollTop, 140, 'opening and closing a Panel returns to the same place');
    // The reader's own scroll lets go of that message.
    userScrollTo(90);
    assert.equal(topOf('b1'), 10);
    beforeLayout();
    rowHeight = 50;
    resized();
    flushFrame();
    flushFrame();
    assert.equal(topOf('b1'), 10, 'after a scroll, the first message starting in view is the anchor again');
    rowHeight = 100;
    userScrollTo(140);

    // A late change between the two frames is corrected on the second.
    beforeLayout();
    rowHeight = 50;
    resized();
    flushFrame();
    assert.equal(view.scrollTop, 40);
    heights.set('b0', 100);
    flushFrame();
    assert.equal(topOf('b2'), 60, 'an image loading above between the frames is corrected');
    assert.equal(view.scrollTop, 90);
    heights.clear();
    rowHeight = 100;
    userScrollTo(140);

    // A render that draws the list anew between the layout and its restore
    // replaces the anchor row.
    beforeLayout();
    const anchorRow = row('b2');
    workspace.setMessages([]);
    workspace.setMessages(conversation('b'));
    assert.notEqual(row('b2'), anchorRow, 'the list was drawn anew');
    rowHeight = 50;
    resized();
    flushFrame();
    flushFrame();
    assert.equal(topOf('b2'), 60, 'the new row of the same message is the anchor');
    rowHeight = 100;
    userScrollTo(140);

    // A message taller than the viewport keeps the share of it scrolled past.
    heights.set('b5', 600);
    userScrollTo(650);
    assert.equal(topOf('b5'), -150, 'b5 fills the viewport, a quarter of it above');
    beforeLayout();
    heights.set('b5', 1200);
    resized();
    flushFrame();
    flushFrame();
    assert.equal(topOf('b5'), -300, 'a quarter of the taller b5 stays above');
    heights.clear();

    // The reader's own scroll replaces the place captured before the layout.
    userScrollTo(140);
    beforeLayout();
    userScrollTo(200);
    assert.equal(view.style.overflowAnchor, 'auto', 'a user scroll gives anchoring back at once');
    rowHeight = 50;
    resized();
    flushFrame();
    flushFrame();
    assert.equal(view.scrollTop, 200, 'the earlier place is not restored over it');
    rowHeight = 100;

    // A conversation opened between two notices of one layout is shown at its
    // tail, and anchoring comes back as it was before the first notice.
    userScrollTo(140);
    assert.equal(view.style.overflowAnchor, 'auto');
    beforeLayout();
    scope = 'conversation-c';
    workspace.setMessages(conversation('c'));
    beforeLayout();
    rowHeight = 120;
    resized();
    flushFrame();
    flushFrame();
    assert.equal(view.scrollTop, 900, 'the new conversation is shown at its tail');
    assert.equal(view.style.overflowAnchor, 'auto', 'anchoring comes back as it was before the first notice');
    rowHeight = 100;

    // While following the tail, a layout keeps the tail in view.
    userScrollTo(700);
    assert.equal(following(), true);
    beforeLayout();
    rowHeight = 120;
    resized();
    flushFrame();
    flushFrame();
    assert.equal(view.scrollTop, 900, 'taller rows still show the tail');

    // A window resize keeps a reader following the tail at the tail.
    rowHeight = 150;
    fire(w, 'resize', { bubbles: false });
    assert.equal(view.scrollTop, 900, 'the tail is followed on the next frame');
    flushFrame();
    assert.equal(view.scrollTop, 1200, 'the window resize kept the tail in view');
    userScrollTo(300);
    rowHeight = 100;
    fire(w, 'resize', { bubbles: false });
    flushFrame();
    assert.equal(view.scrollTop, 300, 'a window resize leaves a history reader in place');

    // Destroying the Workspace drops what it scheduled and stops listening.
    userScrollTo(700);
    assert.equal(following(), true);
    flushFrame();
    const earlier = new Set(frames.keys());
    fire(w, 'resize', { bubbles: false });
    beforeLayout();
    resized();
    const scheduled = [...frames.keys()].filter(id => !earlier.has(id));
    assert.ok(scheduled.length >= 2, 'a tail follow and a restore are pending');
    // A scrollbar drag is in progress as the Workspace goes away.
    fire(view, 'pointerdown');
    workspace.destroy();
    assert.equal(view.style.overflowAnchor, 'auto', 'destroy gives anchoring back');
    assert.deepEqual([...frames.keys()].filter(id => scheduled.includes(id)), [], 'destroy cancels both');
    rowHeight = 150;
    flushFrame();
    assert.equal(view.scrollTop, 700, 'nothing scrolls the list after destroy');
    scrollTo(140);
    userScrollTo(140);
    assert.equal(following(), true, 'the destroyed Workspace no longer follows scrolls');
    beforeLayout();
    assert.equal(view.style.overflowAnchor, 'auto', 'nor Workbench layouts');
    console.log('PASS the message viewport tells reader scrolls apart, follows the tail and keeps the reading place across Workbench layouts');
  } finally {
    for (const observer of observers) observer.disconnect();
    w.close();
  }
})().catch(error => {
  console.error(error);
  process.exitCode = 1;
});
