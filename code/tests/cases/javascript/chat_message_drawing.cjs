'use strict';
// What each draw of a Workspace's message list renders. The host's
// renderMessageBody runs once for every message drawn, so its calls show which
// messages a draw rendered and with which place in the conversation; after each
// change the list must equal a fresh Workspace's draw of the same messages.
// Edits redraw from the first changed message and its reply group, older
// messages loaded above redraw the shown rows they change, a new render
// function or another Resident redraws everything, and new runtime activity
// patches its rows without rendering a message. An appended message renders
// with its reply group and is refused when the list cannot take it. The live
// runtime rows keep their order, phase and render options, a turn that ends
// folds its replies again, and a subagent that ended is announced whichever
// entry removed its live card. What a draw shows gets the host's resource,
// link and avatar handling, and its images open in the image editor, whose
// saved copy joins the composer.
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
  'portal_overlay.js', 'portal_preview_contract.js', 'portal_resource_chips.js', 'portal_image_editor.js', 'portal_editor_open_service.js',
  'portal_command_registry.js', 'portal_workbench_context.js', ...portalWorkbenchScripts(), 'resident_protocol.js', ...chatWorkspaceScripts()]) {
  w.eval(fs.readFileSync(path.resolve(__dirname, '../../../management_portal/static', name), 'utf8'));
}
const settle = async () => { for (let turn = 0; turn < 6; turn += 1) await new Promise(resolve => setTimeout(resolve, 0)); };
const errors = [];
w.PortalUI.toast = (message, tone) => errors.push(`toast:${tone}:${message}`);
// The avatar modules are present, so authors' avatars draw and bind at once.
const avatarRoots = [];
w.PortalResidentAvatar = {};
w.PortalUserAvatar = {
  messageAvatarHtml: id => `<span class="portalUserAvatar messageAuthorAvatar" data-message-avatar-principal="${id}"></span>`,
  bind: root => avatarRoots.push(root),
};

const A = 'resident_aaaaaaaaaaaa';
const B = 'resident_bbbbbbbbbbbb';
let clock = 0;
const at = () => `2026-10-27T08:${String(Math.floor(clock / 60)).padStart(2, '0')}:${String(clock++ % 60).padStart(2, '0')}Z`;
const ask = (id, extra = {}) => ({ id, role: 'user', content: `${id} asks`, created_at: at(), ...extra });
const answer = (id, extra = {}) => ({ id, role: 'assistant', content: `${id} answers`, created_at: at(), ...extra });

// Every Workspace renders bodies through `bodies`, which records the message
// ids, their indexes and the conversation's length.
const bodies = [];
const renderMessageBody = (item, context) => {
  bodies.push([item.id, context.index, context.messages.length]);
  return context.bodyHtml;
};
const drawn = () => bodies.splice(0).map(([id]) => id);
const places = () => bodies.splice(0).map(([id, index, length]) => `${id}@${index}/${length}`);
const workspaces = [];
const fixtures = w.document.getElementById('fixtures');
const makeWorkspace = (name, extra = {}) => {
  const root = w.document.createElement('section');
  root.id = `drawing_${name}`;
  fixtures.append(root);
  const workspace = w.PortalChatWorkspace.createWorkspace({
    root, id: root.id, historyScope: () => `conversation-${name}`, refs: false, can_attach: false, renderMessageBody, ...extra,
  });
  workspaces.push(workspace);
  return workspace;
};
// The list as drawn, without what names this Workspace.
const listHtml = workspace => (workspace.els.messages.firstElementChild?.outerHTML || '')
  .replaceAll(workspace.root.id, '<id>');
const rowKeys = workspace => [...workspace.els.messages.querySelectorAll('.messageList > [data-message-key]')]
  .map(row => row.dataset.messageKey);
// A fresh Workspace draws the same messages with the same render options.
let freshCount = 0;
const assertFresh = (workspace, label, contract = null) => {
  const fresh = makeWorkspace(`fresh${freshCount += 1}`);
  if (contract) fresh.applyContract({ ...contract, messages: [] });
  fresh.setMessages(workspace.state.messages, {
    ...workspace.state.messageRenderOptions,
    runtimeDetails: workspace.state.runtimeDetails,
    runtimeLive: workspace.state.runtimeLive,
    runtimePhase: workspace.state.runtimePhase,
  });
  bodies.length = 0;
  assert.equal(listHtml(workspace), listHtml(fresh), `${label}: the list is what a fresh draw shows`);
  fresh.destroy();
  fresh.root.remove();
};

(async () => {
  try {
    // Edits redraw from the first changed message and its reply group.
    const chat = makeWorkspace('chat');
    let messages = [ask('u0'), answer('a1'), ask('u2'), answer('a3'), answer('a4'), ask('u5'), answer('a6')];
    chat.setMessages(messages);
    assert.deepEqual(places(), ['u0@0/7', 'a1@1/7', 'u2@2/7', 'a3@3/7', 'a4@4/7', 'u5@5/7', 'a6@6/7'],
      'the first draw renders every message with its place in the conversation');
    messages = [...messages.slice(0, 6), { ...messages[6], content: 'a6 answers again' }];
    chat.setMessages(messages);
    assert.deepEqual(drawn(), ['a6'], 'an edited last reply renders only that reply');
    assertFresh(chat, 'an edited last reply');
    messages = [...messages.slice(0, 4), ...messages.slice(5)];
    chat.setMessages(messages);
    assert.deepEqual(drawn(), ['a3', 'u5', 'a6'], 'a reply group that lost its last reply renders from its first reply');
    assertFresh(chat, 'a shortened reply group');
    messages = [...messages.slice(0, 4), ask('u7')];
    chat.setMessages(messages);
    assert.deepEqual(drawn(), ['a3', 'u7'], 'a reply that becomes the newest renders again');
    assertFresh(chat, 'a reply that became the newest');
    messages = [...messages, answer('a8'), answer('a9'), ask('u10')];
    chat.setMessages(messages);
    drawn();
    messages = messages.slice(0, -1);
    chat.setMessages(messages);
    assert.deepEqual(drawn(), ['a8', 'a9'], 'removing the last request renders the replies before it');
    assertFresh(chat, 'a removed last request');
    chat.setMessages(messages, { force: true });
    assert.equal(drawn().length, messages.length, 'a forced draw renders every message');

    // The render context: a new render function and a new live runtime.
    const otherBody = (item, context) => renderMessageBody(item, context);
    chat.setMessages(messages, { renderMessageBody: otherBody });
    assert.equal(drawn().length, messages.length, 'a new render function renders every message');
    chat.setMessages(messages, { renderMessageBody: otherBody, runtimeDetails: [{ kind: 'text', text: 'thinking' }] });
    assert.deepEqual(drawn(), [], 'new runtime activity renders no message');
    assert.ok(chat.els.messages.querySelector('[data-message-key="__runtime__"]'), 'and shows itself');
    chat.setMessages(messages, { renderMessageBody: otherBody });
    drawn();

    // Older messages loaded above redraw only the shown rows they change.
    // Older pages hold earlier messages.
    const oldest = [ask('o0'), answer('o1')];
    const older = [ask('p0'), answer('p1')];
    const paged = makeWorkspace('paged');
    let shown = [ask('p2'), answer('p3')];
    paged.setMessages(shown, { history: { can_load_older: true } });
    assert.ok(paged.els.messages.querySelector('[data-message-key="__history__"]'), 'a conversation with older messages offers them');
    drawn();
    shown = [...older, ...shown];
    paged.setMessages(shown, { history: { can_load_older: true } });
    assert.deepEqual(drawn(), ['p0', 'p1'], 'loading older messages renders only them');
    assertFresh(paged, 'loading an older page');
    paged.setMessages([...oldest, ...shown], { history: { can_load_older: false } });
    assert.equal(paged.els.messages.querySelector('[data-message-key="__history__"]'), null, 'the oldest page offers no more');
    assertFresh(paged, 'loading the oldest page');
    drawn();
    const facadeHistory = answer('h0', { family_history: { resident_id: A, title: 'AMA' } });
    const family = makeWorkspace('family');
    const facadeRequest = ask('f1');
    family.setMessages([facadeRequest]);
    drawn();
    family.setMessages([facadeHistory, facadeRequest]);
    assert.deepEqual(drawn(), ['h0', 'f1'], 'the first history page renders the message where the Family takes over');
    assertFresh(family, 'a Family history page');
    const newest = makeWorkspace('newest');
    clock = 100;
    newest.setMessages([ask('n1'), answer('n2')]);
    drawn();
    clock = 500;
    newest.setMessages([answer('n0'), ...newest.state.messages]);
    assert.deepEqual(drawn(), ['n0', 'n1', 'n2'], 'an older page holding the newest reply renders the reply that was newest');

    // A Chat of another Resident or another conversation draws everything.
    const contract = (residentId, items) => ({
      schema: 'portal.chat_workspace.v3', messages: items, target: { kind: 'resident', id: residentId, resident_id: residentId },
      composer: { can_send: true, queue: { items: [] } },
    });
    const fromA = [ask('r0'), answer('r1', { origin: { sender: { kind: 'resident', id: A } }, author_id: A })];
    const owned = makeWorkspace('owned');
    owned.applyContract(contract(A, fromA));
    drawn();
    owned.applyContract(contract(B, fromA));
    assert.deepEqual(drawn(), ['r0', 'r1'], 'the same messages in another Resident\'s Chat render again');
    assertFresh(owned, 'another Resident\'s Chat', contract(B, []));
    let scope = 'one';
    const scoped = makeWorkspace('scoped', { historyScope: () => `conversation-${scope}` });
    const sharedStart = [ask('s0'), answer('s1')];
    scoped.setMessages([...sharedStart, ask('s2')]);
    drawn();
    scope = 'two';
    scoped.setMessages([...sharedStart, ask('s3')]);
    assert.deepEqual(drawn(), ['s0', 's1', 's3'], 'another conversation renders every message');

    // Appending renders the new message with its reply group.
    const appending = makeWorkspace('appending');
    appending.applyContract(contract(A, [ask('q0'), answer('q1')]));
    drawn();
    assert.equal(appending.appendMessage(answer('q2')), true);
    assert.deepEqual(places(), ['q1@1/3', 'q2@2/3'], 'an appended reply renders with its reply group, in place');
    assertFresh(appending, 'an appended reply', contract(A, []));
    appending.applyContract(appending.state.contract);
    assert.deepEqual(rowKeys(appending).filter(key => !key.startsWith('__')), ['q0', 'q1', 'q2'],
      'the Workspace\'s contract keeps the appended reply');
    assert.deepEqual(drawn(), [], 'and drawing it again renders nothing');
    appending.setRuntime([{ kind: 'text', text: 'working' }], true);
    assert.equal(appending.appendMessage(answer('q3')), true, 'a reply is appended during a turn');
    assertFresh(appending, 'a reply appended during a turn', contract(A, []));
    assert.equal(appending.appendMessage(ask('q4')), false, 'a person\'s message during a turn is left to a whole draw');
    assert.equal(appending.appendMessage(answer('q3')), false, 'a message already shown is not appended again');
    const forked = makeWorkspace('forked');
    forked.setMessages([], { inheritance: { source_resident_id: A, items: [{ id: 'old', role: 'user', content: 'before' }] } });
    assert.ok(forked.els.messages.querySelector('.messageList'), 'a Fork Chat with no messages yet shows its inheritance');
    assert.equal(forked.appendMessage(answer('x1')), false, 'a Chat with no messages is drawn whole');
    let shownScope = 'first';
    const switching = makeWorkspace('switching', { historyScope: () => `conversation-${shownScope}` });
    switching.setMessages([ask('w0')]);
    shownScope = 'second';
    assert.equal(switching.appendMessage(answer('w1')), false, 'a message for a conversation not drawn yet is refused');
    drawn();

    // An appended input of the viewer's reaches the input history and the search.
    const loads = [];
    const own = makeWorkspace('own', {
      onLoadInputHistory: async scopeKey => { loads.push(scopeKey); return { messages: [] }; },
    });
    own.setMessages([ask('i0'), answer('i1')]);
    await settle();
    assert.equal(loads.length, 1, 'the input history reads its first page');
    own.root.querySelector('[data-workspace-history-search]').click();
    await settle();
    // Only this Chat's search is open; its panel sits at the page's top level.
    const fragments = () => [...w.document.querySelectorAll('[data-search-message]')].map(node => node.dataset.searchMessage);
    assert.deepEqual(fragments(), ['i0'], 'the search lists the people\'s messages');
    assert.equal(own.appendMessage(ask('i2', { origin: { sender: { kind: 'user', id: 'viewer' } } })), true);
    await settle();
    assert.equal(loads.length, 2, 'an appended input of the viewer\'s reads the history again');
    assert.deepEqual(fragments(), ['i2', 'i0'], 'and the search lists it');
    drawn();

    // The live runtime rows keep their order, phase and render options.
    const live = makeWorkspace('live');
    const turn = [ask('l0'), answer('l1'), answer('l2')];
    live.setMessages(turn, { assistantName: 'Helper', runtimeLive: true, runtimePhase: 'waiting_quota' });
    assert.ok(live.els.messages.querySelector('[data-message-key="__runtime_phase__"]'), 'a turn waiting for quota says so');
    live.setRuntime([], true);
    assert.ok(live.els.messages.querySelector('[data-message-key="__runtime_phase__"]'), 'and keeps saying so when the runtime changes');
    assertFresh(live, 'a runtime change while waiting for quota');
    live.setRuntime([{ kind: 'text', text: 'thinking' }], true, { runtimePhase: '' });
    assert.equal(live.els.messages.querySelector('[data-message-key="__runtime__"] .messageTitle')?.textContent, 'Helper',
      'the runtime row keeps the Chat\'s render options');
    const subagent = { tool_use_id: 'tu_live', agent_type: 'plan', description: 'Plan', started_at: at(), steps: [] };
    live.setRuntime([{ kind: 'subagent', status: 'in_progress', execution_id: 'exec_live', subagent }, { kind: 'text', text: 'thinking' }], true);
    assert.deepEqual(rowKeys(live).slice(-3), ['__subagents__', '__runtime__', '__scroll_anchor__'],
      'a subagent row joins before the runtime row');
    drawn();
    live.setRuntime([], false);
    assert.deepEqual(drawn(), ['l1', 'l2'], 'a turn that ends renders its replies again');

    // A subagent that ended is announced whichever entry removed its live card.
    const finished = [];
    const nativeBox = w.Element.prototype.getBoundingClientRect;
    w.Element.prototype.getBoundingClientRect = () => ({ left: 0, top: 0, right: 320, bottom: 120, width: 320, height: 120 });
    const runs = makeWorkspace('runs', {
      renderMessageBody: w.PortalChatWorkspace.residentMessageBody, onSubagentFinished: run => finished.push(run.tool_use_id),
    });
    const run = id => ({ tool_use_id: id, agent_type: 'Explore', description: `Run ${id}`, started_at: at(), steps: [] });
    const liveRun = id => ({ kind: 'subagent', status: 'in_progress', execution_id: `exec_${id}`, subagent: run(id) });
    const resultOf = id => answer(`result_${id}`, {
      content: '', content_parts: [{ type: 'subagent_run', tool_use_id: id, agent_type: 'Explore', description: `Run ${id}`, status: 'completed', result: 'done' }],
    });
    runs.setMessages([ask('t0')], { runtimeDetails: [liveRun('tu_1')], runtimeLive: true });
    runs.setMessages([ask('t0'), resultOf('tu_1')], { runtimeDetails: [], runtimeLive: true });
    assert.deepEqual(finished, ['tu_1'], 'a draw that replaces the live card by its result announces the run');
    runs.setMessages([ask('t0'), resultOf('tu_1')], { runtimeDetails: [liveRun('tu_2')], runtimeLive: true });
    runs.setMessages([ask('t0'), resultOf('tu_1'), resultOf('tu_2')], { runtimeDetails: [liveRun('tu_2')], runtimeLive: true });
    assert.deepEqual(finished, ['tu_1'], 'a run whose live card still shows is not announced yet');
    runs.setRuntime([], true);
    assert.deepEqual(finished, ['tu_1', 'tu_2'], 'removing the live card announces it');
    w.Element.prototype.getBoundingClientRect = nativeBox;
    drawn();

    // What a draw shows gets the host's handling.
    const resources = [];
    const links = [];
    const uploads = [];
    const opened = [];
    w.PortalImageEditor = { ...w.PortalImageEditor, open: (src, title, config) => opened.push({ src, title, config }) };
    w.PortalAPI = { get: async () => ({ address: 'repo_0123456789ab/logo.png', resource_type: 'repo' }) };
    w.URL.createObjectURL = () => 'blob:https://chat.fixture.invalid/1';
    w.URL.revokeObjectURL = () => {};
    const image = 'https://chat.fixture.invalid/preview/share/images/view.png';
    const handled = makeWorkspace('handled', {
      enableLinkPreview: true,
      can_attach: true,
      renderMarkdown: text => (text.includes('[doc]')
        ? '<a href="docs/guide.md" data-portal-preview-link="docs/guide.md">doc</a>'
        : text.includes('[image]')
          ? `<img src="${image}" data-portal-preview-image="${image}" title="view.png" alt="view">`
          : text),
      onResourcePreview: context => resources.push(context.address),
      onLinkPreview: request => links.push(request.href),
      uploadAttachment: file => { uploads.push(file.name); return Promise.resolve({ upload_id: 'up1', path: `/uploads/${file.name}` }); },
      onError: error => errors.push(error.message),
    });
    handled.setMessages([
      ask('h1', { content: 'see repo_0123456789ab/logo.png', author_id: 'someone' }),
      answer('h2', { content: '[doc]' }),
      answer('h3', { content: '[image]' }),
    ]);
    await settle();
    handled.root.querySelector('.portalResourceChip[data-resource-address]').click();
    await settle();
    assert.deepEqual(resources, ['repo_0123456789ab/logo.png'], 'a resource in a message opens through the host');
    handled.root.querySelector('[data-portal-preview-link]').click();
    assert.deepEqual(links, ['docs/guide.md'], 'a link in a message opens through the host');
    const avatar = handled.root.querySelector('[data-message-avatar-principal="someone"]');
    assert.ok(avatar && avatarRoots.some(root => root.contains(avatar)), 'the authors get their avatars');
    handled.root.querySelector('[data-portal-preview-image]').click();
    await settle();
    assert.deepEqual(opened.map(item => [item.src, item.config.editable]), [[image, true]], 'a message image opens in the image editor');
    await opened[0].config.onSave({ file: new w.File(['png'], 'view-annotated.png', { type: 'image/png' }) });
    await settle();
    assert.deepEqual(uploads, ['view-annotated.png'], 'the saved copy joins the composer');
    w.PortalImageEditor.open = () => { throw new Error('editor offline'); };
    errors.length = 0;
    handled.root.querySelector('[data-portal-preview-image]').click();
    await settle();
    assert.deepEqual(errors, ['editor offline'], 'an image editor that cannot open says why');
    console.log('PASS each draw of the message list renders what changed and hands what it drew to the host');
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
