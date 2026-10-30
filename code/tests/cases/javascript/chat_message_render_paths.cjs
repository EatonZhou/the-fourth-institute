'use strict';
// The ways a Workspace draws its message list: the first and forced renders,
// redrawing an edited tail, older messages loaded above, one message appended,
// and an unchanged list. Whatever was drawn gets its buttons bound, the host
// hears how much changed, and the reading place is kept. JSDOM has no layout, so
// the list gets a 300px viewport with 100px rows.
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
const wait = ms => new Promise(resolve => setTimeout(resolve, ms));
const settle = async () => { for (let turn = 0; turn < 3; turn += 1) await wait(10); };
const message = (id, index, extra = {}) => ({
  id, role: index % 2 ? 'assistant' : 'user', content: `${id} says hello`,
  created_at: `2026-10-26T08:${String(index).padStart(2, '0')}:00Z`, ...extra,
});
// Every reply carries a file and an image attachment and a Skill it used; all
// three are buttons.
const reply = (id, index, file = `/share/files/${id}.txt`) => message(id, index, {
  attachments: [{ kind: 'file', path: file }, { kind: 'image', path: `/share/images/${id}.png` }],
  response_sources: { skills: [{ name: `${id}-skill`, path: `/share/Skills/${id}/SKILL.md` }] },
});
const conversation = ids => ids.map((id, index) => (index % 2 ? reply(id, index) : message(id, index)));

(async () => {
  const fixture = w.document.getElementById('fixture');
  const rendered = [];
  const previews = [];
  const copied = [];
  const editorOpened = [];
  w.PortalUI.copyText = value => { copied.push(value); };
  w.PortalImageEditor = { ...w.PortalImageEditor, open: src => { editorOpened.push(src); } };
  const workspace = w.PortalChatWorkspace.createWorkspace({
    root: fixture, id: 'chat', historyScope: () => 'conversation-render', refs: false, can_attach: false,
    onLinkPreview: request => { previews.push(request.path); },
    onMessagesRendered: (root, controller, incremental, changed) => rendered.push({ root, controller, incremental, changed }),
  });
  const view = workspace.els.messages;
  const VIEWPORT = 300;
  const ROW = 100;
  const rows = () => [...view.querySelectorAll('.messageList > [data-message-key]')]
    .filter(row => row.dataset.messageKey !== '__scroll_anchor__');
  const maxScroll = () => Math.max(0, rows().length * ROW - VIEWPORT);
  let scrollTop = 0;
  Object.defineProperties(view, {
    clientHeight: { configurable: true, get: () => VIEWPORT },
    scrollHeight: { configurable: true, get: () => Math.max(VIEWPORT, rows().length * ROW) },
    scrollTop: {
      configurable: true,
      get: () => Math.min(scrollTop, maxScroll()),
      set: value => { scrollTop = Math.max(0, Math.min(Number(value) || 0, maxScroll())); },
    },
    getClientRects: { configurable: true, value: () => [{ top: 0, height: VIEWPORT }] },
  });
  const nativeBox = w.Element.prototype.getBoundingClientRect;
  w.Element.prototype.getBoundingClientRect = function getBoundingClientRect() {
    if (this === view) return { top: 0, bottom: VIEWPORT, height: VIEWPORT, left: 0, right: 400, width: 400 };
    const index = rows().indexOf(this);
    if (index >= 0) {
      const top = index * ROW - view.scrollTop;
      return { top, bottom: top + ROW, height: ROW, left: 0, right: 400, width: 400 };
    }
    return nativeBox.call(this);
  };
  const row = id => view.querySelector(`.messageList > [data-message-key="${id}"]`);
  const keys = () => rows().map(item => item.dataset.messageKey);
  const userScrollTo = top => {
    view.dispatchEvent(new w.Event('wheel', { bubbles: true }));
    view.scrollTop = top;
    view.dispatchEvent(new w.Event('scroll'));
  };
  // A drawn reply's file and Skill open through the Workspace's preview; its
  // image has its path copied, there being no preview URL for it.
  const assertBound = async (id, file, label) => {
    previews.length = 0;
    copied.length = 0;
    row(id).querySelector('[data-message-attachment-kind="file"]').click();
    row(id).querySelector('[data-message-attachment-kind="image"]').click();
    row(id).querySelector('[data-portal-source-path]').click();
    await settle();
    assert.deepEqual(previews.sort(), [file, `/share/Skills/${id}/SKILL.md`].sort(), `${label}: ${id}'s file and Skill open`);
    assert.deepEqual(copied, [`/share/images/${id}.png`], `${label}: ${id}'s image path is copied`);
  };
  const lastRender = label => {
    assert.equal(rendered.length, 1, `${label} tells the host once`);
    const [call] = rendered;
    rendered.length = 0;
    assert.equal(call.root, view, `${label} names the message list`);
    assert.equal(call.controller, workspace, `${label} names the Workspace`);
    return call;
  };
  try {
    // The first render draws everything and opens at the tail.
    let ids = ['m0', 'm1', 'm2', 'm3', 'm4', 'm5', 'm6', 'm7'];
    workspace.setMessages(conversation(ids));
    await settle();
    assert.deepEqual(keys(), ids);
    assert.equal(!!lastRender('the first render').incremental, false, 'the first render is a whole render');
    await assertBound('m1', '/share/files/m1.txt', 'the first render');
    await assertBound('m7', '/share/files/m7.txt', 'the first render');
    assert.equal(view.scrollTop, maxScroll(), 'the first render shows the tail');

    // The same messages draw nothing.
    const kept = row('m3');
    workspace.setMessages(conversation(ids));
    await settle();
    assert.equal(rendered.length, 0, 'an unchanged list is not drawn');
    assert.equal(row('m3'), kept);

    // An edited last reply redraws only the tail.
    userScrollTo(140);
    const edited = conversation(ids);
    edited[7] = reply('m7', 7, '/share/files/m7-edited.txt');
    workspace.setMessages(edited);
    await settle();
    assert.equal(!!lastRender('redrawing the tail').incremental, false, 'the host hears of a whole render');
    assert.equal(row('m3'), kept, 'the untouched messages keep their rows');
    await assertBound('m7', '/share/files/m7-edited.txt', 'redrawing the tail');
    assert.equal(view.scrollTop, 140, 'redrawing the tail keeps the reading place');

    // Older messages loaded above keep the reader's place.
    assert.equal(row('m1').getBoundingClientRect().top, -40);
    const older = [message('o0', 0), reply('o1', 1)];
    const withOlder = [...older, ...edited];
    workspace.setMessages(withOlder);
    await settle();
    ids = ['o0', 'o1', ...ids];
    assert.deepEqual(keys(), ids);
    assert.equal(!!lastRender('loading older messages').incremental, false);
    assert.equal(row('m3'), kept, 'the loaded rows go above the existing ones');
    await assertBound('o1', '/share/files/o1.txt', 'loading older messages');
    assert.equal(row('m1').getBoundingClientRect().top, -40, 'the message the reader saw stays in place');

    // One appended message draws only its own group.
    userScrollTo(maxScroll());
    const appended = reply('m8', 9);
    assert.equal(workspace.appendMessage(appended), true, 'a reply after a request is appended');
    await settle();
    const call = lastRender('appending');
    assert.equal(call.incremental, true, 'the host hears that only part was drawn');
    assert.ok(call.changed.some(node => node.dataset.messageKey === 'm8'), 'the changed rows include the new message');
    assert.ok(call.changed.every(node => view.contains(node)), 'the changed rows are in the list');
    assert.equal(row('m3'), kept);
    await assertBound('m8', '/share/files/m8.txt', 'appending');
    assert.equal(view.scrollTop, maxScroll(), 'a reader at the tail follows the appended message');
    userScrollTo(140);
    assert.equal(workspace.appendMessage(message('m9', 10)), true);
    await settle();
    assert.equal(lastRender('appending while reading').incremental, true);
    assert.equal(view.scrollTop, 140, 'a reader in the history stays where they are');
    assert.equal(workspace.appendMessage(appended), false, 'a message already shown is not appended again');
    assert.equal(rendered.length, 0);

    // A forced render draws everything again.
    workspace.setMessages(workspace.state.messages, { force: true });
    await settle();
    assert.equal(!!lastRender('a forced render').incremental, false);
    await assertBound('m8', '/share/files/m8.txt', 'a forced render');

    // With a preview URL from the render options an image opens in the editor.
    const previewUrlForPath = file => (file.endsWith('.png') ? `https://chat.fixture.invalid/preview${file}` : '');
    const withImage = [...workspace.state.messages, reply('m10', 11)];
    userScrollTo(maxScroll());
    workspace.setMessages(withImage, { previewUrlForPath });
    await settle();
    lastRender('a render with preview URLs');
    assert.equal(view.scrollTop, maxScroll(), 'a reader at the tail follows a redrawn list');
    row('m10').querySelector('[data-message-attachment-kind="image"]').click();
    await settle();
    assert.deepEqual(editorOpened, ['https://chat.fixture.invalid/preview/share/images/m10.png'], 'the image opens in the image editor');
    console.log('PASS each way of drawing the message list binds what it drew, tells the host and keeps the place');
  } finally {
    workspace.destroy();
    for (const observer of observers) observer.disconnect();
    w.close();
  }
})().catch(error => {
  console.error(error);
  process.exitCode = 1;
});
