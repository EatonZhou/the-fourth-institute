'use strict';
// The composer's height: it starts from the height this Chat last kept, within
// limits that follow the Workspace's height; the reader drags the splitter
// above the composer or moves it with the arrow keys, and the height is kept
// when the move ends, with the draft being written. A shrinking window brings
// the height back within the limits, a conversation's draft brings its own
// height, a Chat without resizing has no splitter, and a destroyed Chat no
// longer answers the splitter or the window.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { JSDOM } = require('jsdom');
const { chatWorkspaceScripts } = require('./chat_workspace_modules.cjs');
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const dom = new JSDOM('<section id="fixture"></section><section id="fixed"></section><section id="sized"></section><section id="early"></section>', {
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
  'portal_command_registry.js', 'portal_workbench_context.js', 'portal_workbench.js', 'resident_protocol.js', ...chatWorkspaceScripts()]) {
  w.eval(fs.readFileSync(path.resolve(__dirname, '../../../management_portal/static', name), 'utf8'));
}
const settle = async () => { for (let turn = 0; turn < 5; turn += 1) await new Promise(resolve => setTimeout(resolve, 0)); };
const CONTRACT = { schema: 'portal.chat_workspace.v3', messages: [], composer: { can_send: true, queue: { items: [] } } };
const storage = w.PortalState.storage;

// The save timer never fires: the test sees each draft the Chat hands over.
const store = w.PortalChatWorkspace.createDraftStore({ owner: 'viewer', namespace: 'chat', delay: 600_000 });
const saved = [];
const recording = {
  ...store,
  schedule(scope, draft) { saved.push(`${scope}: ${draft.text} @${draft.input_height}`); return store.schedule(scope, draft); },
  write(scope, draft) { saved.push(`${scope}: ${draft.text} @${draft.input_height}`); return store.write(scope, draft); },
};
const take = list => list.splice(0);

// The Workspace's height decides how tall the composer may grow.
const heights = new Map();
const sized = (rootId, height) => {
  const root = w.document.getElementById(rootId);
  heights.set(rootId, height);
  root.getBoundingClientRect = () => ({ top: 0, left: 0, width: 600, height: heights.get(rootId), right: 600, bottom: heights.get(rootId) });
  return root;
};
let scope = 'conversation-a';
const mount = (rootId, extra = {}) => w.PortalChatWorkspace.createWorkspace({
  root: w.document.getElementById(rootId), id: rootId, historyScope: () => scope,
  draftStore: recording, contract: CONTRACT, ...extra,
});
const height = rootId => w.document.getElementById(rootId).style.getPropertyValue('--portal-composer-input-h');
const kept = rootId => storage.readText(`portal.workspaceComposerHeight.${rootId}`, '');
const splitter = rootId => w.document.querySelector(`#${rootId} .composerSplitter`);
const pointer = (target, type, clientY) => {
  const Event = w.PointerEvent || w.MouseEvent;
  target.dispatchEvent(new Event(type, { bubbles: true, cancelable: true, button: 0, clientY }));
};
const drag = (rootId, from, to) => {
  const handle = splitter(rootId);
  pointer(handle, 'pointerdown', from);
  pointer(handle, 'pointermove', to);
  return () => pointer(handle, 'pointerup', to);
};
const key = (rootId, name) => {
  splitter(rootId).dispatchEvent(new w.KeyboardEvent('keydown', { bubbles: true, cancelable: true, key: name }));
};
const resize = async (rootId, next) => {
  heights.set(rootId, next);
  w.dispatchEvent(new w.Event('resize'));
  await settle();
};
const workspaces = [];

(async () => {
  try {
    storage.writeText('portal.workspaceComposerHeight.fixture', '400');
    sized('fixture', 500);
    const workspace = mount('fixture');
    workspaces.push(workspace);
    await settle();
    assert.equal(height('fixture'), '230px', 'the kept height is held to 46% of the Workspace');
    assert.equal(kept('fixture'), '400', 'starting the Chat keeps nothing');

    // A reader following the tail stays at it when the window resizes.
    const messages = w.document.getElementById('fixtureMessages');
    let scrolledTo = null;
    Object.defineProperties(messages, {
      clientHeight: { configurable: true, get: () => 400 },
      scrollHeight: { configurable: true, get: () => 1000 },
      scrollTop: { configurable: true, get: () => scrolledTo ?? 0, set: value => { scrolledTo = value; } },
    });
    messages.getClientRects = () => [{ top: 0, height: 400 }];
    await resize('fixture', 1000);
    await new Promise(resolve => setTimeout(resolve, 50));
    assert.equal(scrolledTo, 1000, 'a window resize keeps the tail in view');
    assert.equal(height('fixture'), '230px', 'a taller window leaves the height as it is');
    const input = w.document.querySelector('#fixture .composerInput');
    input.textContent = 'hello';
    input.dispatchEvent(new w.Event('input', { bubbles: true }));
    await settle();
    take(saved);

    let release = drag('fixture', 300, 250);
    assert.equal(height('fixture'), '280px', 'dragging the splitter up 50 px makes the composer 50 px taller');
    assert.equal(kept('fixture'), '400', 'the height is not kept while dragging');
    assert.deepEqual(saved, [], 'nor is the draft written while dragging');
    release();
    assert.equal(kept('fixture'), '280', 'letting go keeps the height');
    assert.deepEqual(take(saved), ['conversation-a: hello @280'], 'and writes the draft with it');
    assert.equal(splitter('fixture').getAttribute('aria-valuenow'), '280', 'the splitter reports the height');

    release = drag('fixture', 300, -400);
    assert.equal(height('fixture'), '360px', 'the composer grows no taller than its largest height');
    release();
    release = drag('fixture', 300, 900);
    assert.equal(height('fixture'), '56px', 'nor shorter than its smallest');
    release();
    take(saved);

    key('fixture', 'ArrowUp');
    assert.equal(height('fixture'), '80px', 'ArrowUp on the splitter makes the composer 24 px taller');
    assert.equal(kept('fixture'), '80', 'and keeps the height at once');
    assert.deepEqual(take(saved), ['conversation-a: hello @80'], 'with the draft');
    key('fixture', 'Home');
    assert.equal(height('fixture'), '56px', 'Home moves the splitter to the smallest composer');

    key('fixture', 'End');
    assert.equal(height('fixture'), '360px', 'End to the largest');
    await resize('fixture', 300);
    assert.equal(height('fixture'), '138px', 'a shorter window brings the height within the new limit');
    assert.equal(kept('fixture'), '360', 'without keeping it');
    take(saved);

    await resize('fixture', 1000);
    store.write('conversation-b', { text: 'draft for B', input_height: 150 });
    scope = 'conversation-b';
    workspace.setComposer({ ...CONTRACT.composer });
    await settle();
    assert.equal(height('fixture'), '150px', 'a conversation\'s draft brings its own height');

    sized('fixed', 500);
    const fixed = mount('fixed', { composerResize: false });
    workspaces.push(fixed);
    await settle();
    assert.equal(splitter('fixed'), null, 'a Chat without resizing has no splitter');
    await resize('fixed', 200);
    assert.equal(height('fixed'), '', 'and leaves the composer height to the page');

    // Drafts belong to the conversation, so this Chat shows another one.
    scope = 'conversation-c';
    sized('sized', 800);
    const other = mount('sized', { composerInputHeight: 120, composerMinHeight: 100, composerMaxHeight: 200 });
    workspaces.push(other);
    await settle();
    assert.equal(height('sized'), '120px', 'a Chat with no kept height starts from the host\'s height');
    key('sized', 'End');
    assert.equal(height('sized'), '200px', 'up to the host\'s largest height');
    key('sized', 'Home');
    assert.equal(height('sized'), '100px', 'and down to its smallest');

    // A Chat made before its Workspace is laid out: the forward dock sits above
    // the composer, so the height holds before a composer is drawn, and the
    // Workspace's height applies once it is.
    storage.writeText('portal.workspaceComposerHeight.early', '300');
    scope = 'conversation-d';
    sized('early', 0);
    const early = w.PortalChatWorkspace.createWorkspace({
      root: w.document.getElementById('early'), id: 'early', historyScope: () => scope, draftStore: recording,
    });
    workspaces.push(early);
    assert.equal(w.document.querySelector('#early .composerInput'), null, 'a Chat without a contract draws no composer yet');
    assert.equal(height('early'), '300px', 'its kept height is in place already, held to the window while the Workspace has no height');
    heights.set('early', 500);
    early.applyContract(CONTRACT, {});
    await settle();
    assert.ok(w.document.querySelector('#early .composerInput'), 'the composer is drawn');
    assert.equal(height('early'), '230px', 'and its height is held to the Workspace');

    // A drag that began before the Chat was destroyed.
    pointer(splitter('fixture'), 'pointerdown', 300);
    workspace.destroy();
    take(saved);
    const before = kept('fixture');
    pointer(splitter('fixture'), 'pointermove', 250);
    pointer(splitter('fixture'), 'pointerup', 250);
    pointer(splitter('fixture'), 'pointerdown', 300);
    assert.equal(w.document.body.classList.contains('resizingComposer'), false, 'a destroyed Chat\'s splitter starts no drag');
    pointer(splitter('fixture'), 'pointerup', 300);
    drag('fixture', 300, 200)();
    key('fixture', 'ArrowUp');
    assert.equal(height('fixture'), '150px', 'a destroyed Chat no longer answers its splitter');
    assert.equal(kept('fixture'), before, 'nor keeps a height');
    assert.deepEqual(saved, [], 'nor writes a draft');
    await resize('fixture', 200);
    assert.equal(height('fixture'), '150px', 'nor follows the window');
    console.log('PASS the composer height follows the splitter, the window and the draft, and is kept when a move ends');
  } finally {
    for (const workspace of workspaces) workspace.destroy();
    await new Promise(resolve => setImmediate(resolve));
    for (const observer of observers) observer.disconnect();
    w.close();
  }
})().catch(error => {
  console.error(error);
  process.exitCode = 1;
});
