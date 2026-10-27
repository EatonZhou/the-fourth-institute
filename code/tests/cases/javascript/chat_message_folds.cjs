'use strict';
// Long message bodies fold. JSDOM has no layout, so each Chat gets a small one:
// a 300px viewport and rows stacked by height. A row is a 40px header, its body
// and a 20px fold button; a body is as tall as the test says, and a folded body
// that is measured long shows 150px of it, as the stylesheet does.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { JSDOM } = require('jsdom');
const { chatWorkspaceScripts } = require('./chat_workspace_modules.cjs');
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
const resizeObservers = [];
w.ResizeObserver = class {
  constructor(callback) { this.callback = callback; this.targets = new Set(); resizeObservers.push(this); }
  observe(target) { this.targets.add(target); }
  unobserve(target) { this.targets.delete(target); }
  disconnect() { this.targets.clear(); }
};
w.CSS = { escape: value => String(value).replace(/[^a-zA-Z0-9_-]/g, char => `\\${char}`) };
w.matchMedia = query => Object.assign(new w.EventTarget(), { media: query, matches: false });
w.PortalIdentity = { username: () => 'viewer' };

const VIEWPORT = 300;
const SHORT = 60;
const LONG = 400;
const FOLDED = 150;
const heights = new Map();
const views = new Map();
let laidOut = true;
const keyOf = node => node.closest('[data-message-key]')?.dataset.messageKey || '';
const shownHeight = body => {
  const full = heights.get(keyOf(body)) ?? SHORT;
  return body.dataset.messageFold === 'collapsed' && body.dataset.messageFoldSize === 'long' ? Math.min(full, FOLDED) : full;
};
const rowsOf = view => [...view.querySelectorAll('.messageList > [data-message-key]')];
const rowHeight = row => {
  if (row.hidden || row.dataset.messageKey === '__scroll_anchor__') return 0;
  const body = row.querySelector('.messageContent[data-message-fold]');
  return 40 + (body ? shownHeight(body) + 20 : 0);
};
const contentHeight = view => rowsOf(view).reduce((sum, row) => sum + rowHeight(row), 0);
const box = (top, height) => ({ top, bottom: top + height, height, left: 0, right: 600, width: 600, x: 0, y: top });
const rowTop = (view, target) => {
  let top = 0;
  for (const row of rowsOf(view)) {
    if (row === target) return top;
    top += rowHeight(row);
  }
  return 0;
};
const viewOf = node => [...views.keys()].find(view => view.contains(node));
w.Element.prototype.getBoundingClientRect = function getBoundingClientRect() {
  if (views.has(this)) return box(0, VIEWPORT);
  const view = viewOf(this);
  const row = this.closest?.('.messageList > [data-message-key]');
  if (!view || !row) return box(0, 0);
  const top = rowTop(view, row) - view.scrollTop;
  const body = row.querySelector('.messageContent[data-message-fold]');
  if (this === row) return box(top, rowHeight(row));
  if (this === body) return box(top + 40, shownHeight(body));
  if (this.matches('[data-message-fold-toggle]') && body) return box(top + 40 + shownHeight(body), 20);
  return box(top, 0);
};
w.Element.prototype.getClientRects = function getClientRects() {
  if (!laidOut || !viewOf(this) || this.closest('[hidden]')) return [];
  return [this.getBoundingClientRect()];
};
Object.defineProperty(w.HTMLElement.prototype, 'scrollHeight', {
  configurable: true,
  get() { return this.matches('.messageContent[data-message-fold]') ? heights.get(keyOf(this)) ?? SHORT : 0; },
});

w.eval(input.routeScript);
for (const name of ['portal_state.js', 'portal_page_state.js', 'portal_text.js', 'portal_ui_primitives.js', 'portal_ui.js',
  'portal_overlay.js', 'portal_resource_chips.js', 'portal_image_editor.js', 'portal_editor_open_service.js',
  'portal_command_registry.js', 'portal_workbench_context.js', 'portal_workbench.js', 'resident_protocol.js', ...chatWorkspaceScripts()]) {
  w.eval(fs.readFileSync(path.resolve(__dirname, '../../../management_portal/static', name), 'utf8'));
}

let minute = 0;
const at = () => `2026-10-26T11:${String(minute++).padStart(2, '0')}:00Z`;
const human = id => ({ id, role: 'user', content: `${id} asks`, created_at: at() });
const agent = (id, extra = {}) => ({ id, role: 'assistant', content: `${id} answers`, created_at: at(), ...extra });
const conversation = prefix => Array.from({ length: 8 }, (_, index) => (index % 2 ? agent : human)(`${prefix}${index}`));
const renderMarkdown = value => String(value).replace(/&/g, '&amp;').replace(/</g, '&lt;')
  .replace(/!\[\]\((.+?)\)/g, '<img src="$1" alt="">');
const workspaces = [];
const fresh = name => {
  const root = w.document.createElement('section');
  root.id = name;
  w.document.getElementById('fixtures').append(root);
  const workspace = w.PortalChatWorkspace.createWorkspace({ root, id: name, historyScope: () => name, refs: false, can_attach: false, renderMarkdown });
  workspaces.push(workspace);
  const view = workspace.els.messages;
  const layout = { width: 600, scrollTop: 0 };
  views.set(view, layout);
  const maxScroll = () => Math.max(0, contentHeight(view) - VIEWPORT);
  Object.defineProperties(view, {
    clientHeight: { configurable: true, get: () => VIEWPORT },
    clientWidth: { configurable: true, get: () => layout.width },
    scrollHeight: { configurable: true, get: () => Math.max(VIEWPORT, contentHeight(view)) },
    scrollTop: {
      configurable: true,
      get: () => Math.min(layout.scrollTop, maxScroll()),
      set: value => { layout.scrollTop = Math.max(0, Math.min(Number(value) || 0, maxScroll())); },
    },
  });
  const row = id => view.querySelector(`.messageList > [data-message-key="${id}"]`);
  const body = id => row(id).querySelector('.messageContent[data-message-fold]');
  const fold = id => body(id).dataset.messageFold;
  const size = id => body(id).dataset.messageFoldSize;
  const toggle = id => row(id).querySelector('[data-message-fold-toggle]');
  // The reader scrolls with the wheel, as the viewport tells reader scrolls apart.
  const readAt = top => {
    view.dispatchEvent(new w.WheelEvent('wheel', { bubbles: true }));
    view.scrollTop = top;
    view.dispatchEvent(new w.Event('scroll'));
  };
  const resize = width => {
    layout.width = width;
    for (const observer of resizeObservers) if (observer.targets.has(view)) observer.callback([]);
  };
  const reading = () => view.classList.contains('is-reading-history');
  return { workspace, view, row, body, fold, size, toggle, readAt, resize, reading };
};

try {
  {
    // A body is measured after it is drawn; a size measured before is drawn
    // right away, and a body with no layout keeps it.
    const { workspace, fold, size } = fresh('measure');
    const messages = [human('m0'), agent('m1'), human('m2'), agent('m3')];
    heights.set('m1', LONG);
    workspace.setMessages(messages);
    assert.equal(size('m1'), 'long');
    assert.equal(size('m3'), 'short');
    assert.equal(fold('m1'), 'collapsed', 'an older reply starts folded');
    assert.equal(fold('m3'), 'expanded', 'the newest reply starts open');
    laidOut = false;
    heights.set('m1', SHORT);
    workspace.setMessages([]);
    workspace.setMessages(messages);
    assert.equal(size('m1'), 'long', 'the measured size is drawn again');
    laidOut = true;
  }
  {
    // An image that loads in a body measures that body again.
    const { workspace, row, size } = fresh('image');
    workspace.setMessages([human('i0'), agent('i1', { content: '![](https://img.fixture.invalid/a.png) a picture' }), human('i2')]);
    assert.equal(size('i1'), 'short');
    heights.set('i1', LONG);
    row('i1').querySelector('img').dispatchEvent(new w.Event('load'));
    assert.equal(size('i1'), 'long', 'the loaded image made it long');
  }
  {
    // The list changes width: bodies are measured again, and the first message
    // in view keeps its place. A body that grows long in view stays open.
    const { workspace, view, row, fold, size, readAt, resize } = fresh('width');
    workspace.setMessages(conversation('w'));
    readAt(500);
    heights.set('w0', LONG);
    const anchorTop = () => row('w2').getBoundingClientRect().top;
    const before = anchorTop();
    resize(500);
    assert.equal(size('w0'), 'long');
    assert.equal(fold('w0'), 'collapsed', 'a body out of view folds');
    assert.equal(anchorTop(), before, 'the first message in view stays put');
    heights.set('w3', LONG);
    resize(450);
    assert.equal(size('w3'), 'long');
    assert.equal(fold('w3'), 'expanded', 'a body that grows long in view stays open');
    heights.set('w4', LONG);
    resize(450);
    assert.equal(size('w4'), 'short', 'the same width measures nothing');
    resize(0);
    assert.equal(size('w4'), 'short', 'a hidden list measures nothing');
    resize(450);
    assert.equal(size('w4'), 'long', 'it is measured when it shows again');
    view.scrollTop = view.scrollHeight;
    readAt(view.scrollHeight);
    heights.set('w5', LONG);
    resize(420);
    assert.equal(fold('w5'), 'collapsed', 'while the list follows its tail, a body that grows long folds');
  }
  {
    // The open newest reply grows long in view: nothing is pinned open, so it
    // folds once it is no longer the newest and the reader looks elsewhere.
    const { workspace, fold, size, readAt, resize } = fresh('grow-open');
    const messages = conversation('g');
    workspace.setMessages(messages);
    readAt(600);
    heights.set('g7', LONG);
    resize(500);
    assert.equal(size('g7'), 'long');
    readAt(0);
    workspace.setMessages([...messages, human('g8'), agent('g9')]);
    assert.equal(fold('g7'), 'collapsed');
  }
  {
    // Toggling keeps the text in place when it opens and the button when it
    // closes; the choice holds on every Chat of the page and across renders.
    const first = fresh('toggle');
    const second = fresh('toggle-copy');
    const messages = [human('t0'), agent('t1'), human('t2'), agent('t3')];
    heights.set('t1', LONG);
    heights.set('t3', LONG);
    first.workspace.setMessages(messages);
    second.workspace.setMessages(messages);
    first.readAt(300);
    const bodyTop = first.body('t1').getBoundingClientRect().top;
    first.toggle('t1').click();
    assert.equal(first.fold('t1'), 'expanded');
    assert.equal(first.toggle('t1').getAttribute('aria-expanded'), 'true');
    assert.equal(first.body('t1').getBoundingClientRect().top, bodyTop, 'opening keeps the text in place');
    assert.equal(second.fold('t1'), 'expanded', 'the other Chat shows the choice too');
    const buttonTop = first.toggle('t1').getBoundingClientRect().top;
    first.toggle('t1').click();
    assert.equal(first.fold('t1'), 'collapsed');
    assert.equal(first.toggle('t1').getBoundingClientRect().top, buttonTop, 'closing keeps the button under the pointer');
    first.toggle('t3').click();
    first.workspace.setMessages([...messages, human('t4'), agent('t5')]);
    assert.equal(first.fold('t3'), 'collapsed', 'a reader\'s choice holds across renders');
    first.workspace.setMessages(messages);
    assert.equal(first.fold('t3'), 'collapsed', 'even where a default would open it');
    // The reader toggles at the top: the list stops following its tail.
    first.readAt(first.view.scrollHeight);
    assert.equal(first.reading(), false);
    first.view.scrollTop = 0;
    first.toggle('t1').click();
    assert.equal(first.reading(), true, 'a toggle away from the tail reads history');
  }
  {
    // Going to a message puts it in the middle of the view, or its top near the
    // top when it is taller, opens it when folded, and the list stops following its tail.
    const { workspace, view, row, fold, readAt, reading } = fresh('reveal');
    heights.set('r1', LONG);
    workspace.setMessages(conversation('r'));
    readAt(view.scrollHeight);
    assert.equal(reading(), false);
    workspace.revealMessage('r2');
    assert.equal(row('r2').getBoundingClientRect().top, (VIEWPORT - row('r2').getBoundingClientRect().height) / 2);
    assert.equal(reading(), true, 'the reader is now in the history');
    readAt(view.scrollHeight);
    assert.equal(fold('r1'), 'collapsed');
    workspace.revealMessage('r1');
    assert.equal(fold('r1'), 'expanded', 'a folded message opens');
    assert.equal(row('r1').getBoundingClientRect().top, 12);
    assert.equal(reading(), true);
  }
  // A new message is drawn by a whole render or appended; both follow the same
  // rules. Reader choices are kept per message ID for the page, so each way
  // uses messages of its own.
  for (const via of ['setMessages', 'appendMessage']) {
    const id = name => `${via}-${name}`;
    const add = (workspace, messages, more, renderOptions = {}) => {
      if (via === 'setMessages') workspace.setMessages([...messages, ...more], renderOptions);
      else for (const message of more) assert.equal(workspace.appendMessage(message), true, `${message.id} is appended`);
    };
    {
      // A newer reply arrives while the reader reads the newest one: it stays open.
      const { workspace, fold, readAt } = fresh(`reading-${via}`);
      const messages = [human(id('r0')), agent(id('r1')), human(id('r2')), agent(id('r3'))];
      heights.set(id('r3'), LONG);
      workspace.setMessages(messages);
      readAt(500);
      add(workspace, messages, [human(id('r4')), agent(id('r5'))]);
      assert.equal(fold(id('r3')), 'expanded', `${via}: the reply being read stays open`);
      assert.equal(fold(id('r5')), 'expanded');
    }
    {
      // A reply the reader folded stays folded while they look at it.
      const { workspace, fold, toggle, readAt } = fresh(`chosen-${via}`);
      const messages = [human(id('c0')), agent(id('c1')), human(id('c2')), agent(id('c3'))];
      heights.set(id('c3'), LONG);
      workspace.setMessages(messages);
      readAt(400);
      toggle(id('c3')).click();
      add(workspace, messages, [human(id('c4')), agent(id('c5'))]);
      assert.equal(fold(id('c3')), 'collapsed', `${via}: the reader's own choice wins`);
      const shown = workspace.state.messages;
      workspace.setMessages([]);
      workspace.setMessages(shown);
      assert.equal(fold(id('c3')), 'collapsed', `${via}: and is still the choice when the row is drawn again`);
    }
    {
      // When the reader looks elsewhere it folds.
      const { workspace, fold, readAt } = fresh(`elsewhere-${via}`);
      const messages = [human(id('e0')), agent(id('e1')), human(id('e2')), agent(id('e3'))];
      heights.set(id('e3'), LONG);
      workspace.setMessages(messages);
      readAt(0);
      add(workspace, messages, [human(id('e4')), agent(id('e5'))]);
      assert.equal(fold(id('e3')), 'collapsed', `${via}: a reply out of view folds`);
    }
    {
      // A running turn keeps its own output open, not an older turn's reply.
      const { workspace, fold } = fresh(`running-${via}`);
      const messages = [human(id('u0')), agent(id('u1')), human(id('u2'))];
      heights.set(id('u1'), LONG);
      workspace.setMessages(messages, { runtimeLive: true });
      assert.equal(fold(id('u1')), 'expanded', 'the newest reply is open as the turn starts');
      add(workspace, messages, [agent(id('u3'))], { runtimeLive: true });
      assert.equal(fold(id('u1')), 'collapsed', `${via}: the older turn's reply folds`);
      assert.equal(fold(id('u3')), 'expanded');
    }
    {
      const { workspace, fold } = fresh(`run-${via}`);
      const messages = [human(id('n0')), agent(id('n1'))];
      workspace.setMessages(messages, { runtimeLive: true });
      add(workspace, messages, [agent(id('n2'))], { runtimeLive: true });
      assert.equal(fold(id('n1')), 'expanded', `${via}: the running turn's earlier output stays open`);
    }
  }
  {
    // A message still sending starts open.
    const { workspace, fold } = fresh('pending');
    workspace.setMessages([agent('p0'), human('p1'), { ...human('p2'), pending: true }]);
    assert.equal(fold('p1'), 'collapsed');
    assert.equal(fold('p2'), 'expanded');
  }
  {
    // A destroyed Chat measures nothing.
    const { workspace, row, size, resize } = fresh('destroy');
    workspace.setMessages([human('d0'), agent('d1', { content: '![](https://img.fixture.invalid/d.png)' }), human('d2')]);
    workspace.destroy();
    workspaces.splice(workspaces.indexOf(workspace), 1);
    heights.set('d0', LONG);
    heights.set('d1', LONG);
    resize(500);
    row('d1').querySelector('img').dispatchEvent(new w.Event('load'));
    assert.equal(size('d0'), 'short', 'a width change is no longer observed');
    assert.equal(size('d1'), 'short', 'nor an image load');
  }
  console.log('PASS long message bodies fold and keep the reader\'s place');
} finally {
  for (const workspace of workspaces) workspace.destroy();
  for (const observer of observers) observer.disconnect();
  w.close();
}
