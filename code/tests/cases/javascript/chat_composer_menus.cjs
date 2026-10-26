'use strict';
// The composer's two menus, opened from what is typed: slash commands after "/"
// and Resident mentions after "@". Each Workspace has its own menus.
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
w.PortalIdentity = { username: () => 'sample_admin' };
w.eval(input.routeScript);
for (const name of ['portal_state.js', 'portal_page_state.js', 'portal_text.js', 'portal_ui_primitives.js', 'portal_ui.js',
  'portal_overlay.js', 'portal_resource_chips.js', 'portal_image_editor.js', 'portal_editor_open_service.js',
  'portal_command_registry.js', 'portal_workbench_context.js', 'portal_workbench.js', 'resident_protocol.js', ...chatWorkspaceScripts()]) {
  w.eval(fs.readFileSync(path.resolve(__dirname, '../../../management_portal/static', name), 'utf8'));
}
const wait = ms => new Promise(resolve => setTimeout(resolve, ms));
// Timers, promise callbacks and one animation frame.
const settle = async () => { for (let turn = 0; turn < 3; turn += 1) await wait(20); };
const deferred = () => { let resolve, reject; const promise = new Promise((a, b) => { resolve = a; reject = b; }); return { promise, resolve, reject }; };

const ALICE = { value: 'resident_aaaaaaaaaaaa', title: 'Alice', label: 'resident_aaaaaaaaaaaa' };
const BOB = { value: 'resident_bbbbbbbbbbbb', title: 'Bob', disabled: true, disabled_reason: 'not in this Lineage' };
const CAROL = { value: 'resident_cccccccccccc', title: 'Carol' };

(async () => {
  const inputs = [];
  const errors = [];
  const searches = [];
  let answer = () => Promise.resolve([ALICE, BOB, CAROL]);
  const workspace = w.PortalChatWorkspace.createWorkspace({
    root: w.document.getElementById('fixture'), id: 'chat', historyScope: () => 'conversation-menus', refs: false,
    onInput: (payload, controller) => inputs.push({ text: payload.text, controller }),
    onError: error => errors.push(error.message),
    mentionTitle: 'Residents',
    mentionCandidates: (query, controller, options) => {
      searches.push({ query, controller, signal: options.signal });
      return answer(query, options);
    },
    contract: {
      schema: 'portal.chat_workspace.v3', messages: [],
      composer: {
        can_send: true, queue: { items: [] },
        slash_commands: [
          { command: '/model', title: 'model', description: 'switch the model' },
          { command: '/plan', title: 'plan' },
          { command: '/compact', title: 'compact' },
        ],
      },
    },
  });
  const composer = workspace.els.input;
  // The browser's contenteditable composer inserts a mention chip.
  Object.defineProperty(composer, 'isContentEditable', { value: true });
  const slashMenu = workspace.els.slashCommands;
  const mentionMenu = workspace.els.mentions;
  const type = async (text, { at = text.length } = {}) => {
    composer.textContent = text;
    const range = w.document.createRange();
    range.setStart(composer.firstChild || composer, composer.firstChild ? at : 0);
    range.collapse(true);
    w.getSelection().removeAllRanges();
    w.getSelection().addRange(range);
    composer.dispatchEvent(new w.Event('input', { bubbles: true }));
    await settle();
  };
  // Typing on, as a browser does: the same text node grows.
  const typeOn = async chars => {
    const node = composer.lastChild;
    node.appendData(chars);
    const range = w.document.createRange();
    range.setStart(node, node.length);
    range.collapse(true);
    w.getSelection().removeAllRanges();
    w.getSelection().addRange(range);
    composer.dispatchEvent(new w.Event('input', { bubbles: true }));
    await settle();
  };
  const key = async (name, init = {}) => {
    const event = new w.KeyboardEvent('keydown', { bubbles: true, cancelable: true, key: name, ...init });
    composer.dispatchEvent(event);
    await settle();
    return event.defaultPrevented;
  };
  const slashCommands = () => [...slashMenu.querySelectorAll('[data-portal-slash-command]')].map(item => item.dataset.portalSlashCommand);
  const activeSlash = () => slashMenu.querySelector('.composerSlashCommandItem.active')?.dataset.portalSlashCommand;
  const mentionValues = () => [...mentionMenu.querySelectorAll('[data-portal-mention-index]')].map(item => item.dataset.portalMentionIndex);
  const activeMention = () => mentionMenu.querySelector('.composerMentionItem.active')?.dataset.portalMentionIndex;
  try {
    workspace.setMessages([], { history: { can_load_older: false } });
    await settle();
    composer.focus();

    // Slash commands.
    await type('/');
    assert.equal(slashMenu.hidden, false, '"/" opens the slash command menu');
    assert.deepEqual(slashCommands(), ['/model', '/plan', '/compact'], 'every command the session answers is listed');
    assert.equal(activeSlash(), '/model', 'the first command starts active');
    await type('/pl');
    assert.deepEqual(slashCommands(), ['/plan'], 'typing narrows the menu to commands with that prefix');
    await type('/act');
    assert.deepEqual(slashCommands(), ['/compact'], 'a command whose title contains the text is listed too');
    await type('/');
    assert.equal(activeSlash(), '/compact', 'the active command stays active as the menu refreshes');
    assert.equal(await key('ArrowDown'), true, 'ArrowDown belongs to the open menu');
    assert.equal(activeSlash(), '/model', 'ArrowDown past the last command wraps to the first');
    await key('ArrowDown');
    assert.equal(activeSlash(), '/plan', 'ArrowDown moves to the next command');
    await key('ArrowUp');
    await key('ArrowUp');
    assert.equal(activeSlash(), '/compact', 'ArrowUp from the first command wraps to the last');
    await key('ArrowUp');
    inputs.length = 0;
    assert.equal(await key('Enter'), true, 'Enter chooses the active command');
    assert.equal(composer.textContent.trim(), '/plan', 'the chosen command replaces what was typed');
    assert.equal(slashMenu.hidden, true, 'choosing closes the menu');
    assert.equal(inputs.length, 1, 'choosing reports the input once');
    assert.equal(inputs[0].text.trim(), '/plan', 'the report carries the composer content');
    assert.equal(inputs[0].controller, workspace, 'the report names this Workspace');
    await type('/');
    await key('Escape');
    assert.equal(slashMenu.hidden, true, 'Escape closes the slash command menu');
    await type('/');
    slashMenu.querySelector('[data-portal-slash-command="/compact"]').click();
    await settle();
    assert.equal(composer.textContent.trim(), '/compact', 'clicking a command chooses it');
    await type('plain text');
    assert.equal(slashMenu.hidden, true, 'text that does not start with "/" opens no menu');

    // Mentions.
    searches.length = 0;
    await type('hello @');
    assert.deepEqual(searches.map(item => item.query), [''], '"@" alone searches at once');
    assert.equal(searches[0].controller, workspace, 'the search is given this Workspace');
    assert.equal(mentionMenu.hidden, false, 'the mention menu opens with the results');
    assert.deepEqual(mentionValues(), ['0', '1', '2'], 'every candidate is listed');
    assert.equal(activeMention(), '0', 'the first enabled candidate starts active');
    await key('ArrowDown');
    assert.equal(activeMention(), '2', 'ArrowDown skips a disabled candidate');
    await key('ArrowDown');
    assert.equal(activeMention(), '0', 'ArrowDown wraps past the end');

    searches.length = 0;
    const first = deferred();
    const second = deferred();
    answer = query => (query === 'a' ? first.promise : second.promise);
    await type('hello @a');
    await type('hello @al');
    await wait(200);
    assert.deepEqual(searches.map(item => item.query), ['al'], 'keyword edits within 150 ms make one search for the last text');
    assert.equal(mentionMenu.textContent.includes('正在查找 Resident'), true, 'the menu says it is searching');
    const replaced = searches[0];
    searches.length = 0;
    await type('hello @a');
    await wait(200);
    assert.deepEqual(searches.map(item => item.query), ['a'], 'a changed keyword searches again');
    assert.equal(replaced.signal.aborted, true, 'the replaced search is told to stop');
    assert.equal(searches[0].signal.aborted, false, 'the current search keeps going');
    first.resolve([CAROL]);
    second.resolve([ALICE]);
    await settle();
    assert.equal(mentionMenu.textContent.includes('Alice'), false, 'results of a replaced search are dropped');
    assert.equal(mentionMenu.textContent.includes('Carol'), true, 'the current search shows its results');

    answer = () => Promise.resolve([ALICE, BOB, CAROL]);
    await type('hello @');
    await key('ArrowDown');
    assert.equal(await key('Enter'), true, 'Enter chooses the active candidate');
    const chip = composer.querySelector('.portalMention');
    assert.equal(chip?.dataset.mentionValue, CAROL.value, 'the chosen Resident becomes a mention chip');
    assert.equal(chip.textContent, '@Carol', 'the chip shows the Resident title');
    assert.equal(chip.contentEditable, 'false', 'the chip is edited as one piece');
    assert.equal(mentionMenu.hidden, true, 'choosing closes the mention menu');

    searches.length = 0;
    await type('hello @al');
    await wait(200);
    await key('Escape');
    assert.equal(mentionMenu.hidden, true, 'Escape closes the mention menu');
    await typeOn('i');
    await wait(200);
    assert.deepEqual(searches.map(item => item.query), ['al'], 'after Escape the same "@" does not search again');
    assert.equal(mentionMenu.hidden, true, 'after Escape the same "@" stays closed');

    answer = () => Promise.resolve([]);
    await type('see @zz');
    await wait(200);
    assert.equal(mentionMenu.textContent.includes('没有匹配的 Resident'), true, 'a search without results says so');
    answer = () => Promise.reject(new Error('directory offline'));
    await type('see @yy');
    await wait(200);
    assert.equal(mentionMenu.textContent.includes('候选加载失败，请重新输入重试'), true, 'a failed search asks to type again');
    assert.deepEqual(errors, ['directory offline'], 'a failed search reports its error');

    answer = () => Promise.resolve([ALICE]);
    await type('see @');
    assert.equal(mentionMenu.hidden, false);
    composer.dispatchEvent(new w.FocusEvent('blur'));
    await settle();
    assert.equal(mentionMenu.hidden, true, 'leaving the composer closes the mention menu');

    // Destroying the Workspace closes the menu and drops the render it had scheduled.
    await type('see @');
    const requested = [];
    const cancelled = [];
    const requestFrame = w.requestAnimationFrame;
    const cancelFrame = w.cancelAnimationFrame;
    w.requestAnimationFrame = callback => { const id = requestFrame.call(w, callback); requested.push(id); return id; };
    w.cancelAnimationFrame = id => { cancelled.push(id); return cancelFrame.call(w, id); };
    workspace.destroy();
    w.requestAnimationFrame = requestFrame;
    w.cancelAnimationFrame = cancelFrame;
    assert.equal(mentionMenu.hidden, true, 'destroy closes the mention menu');
    assert.ok(requested.length, 'closing schedules a render');
    assert.deepEqual(requested.filter(id => !cancelled.includes(id)), [], 'destroy cancels every render it scheduled');
    console.log('PASS the composer opens, moves through, chooses from and closes its slash command and mention menus');
  } finally {
    for (const observer of observers) observer.disconnect();
    w.close();
  }
})().catch(error => {
  console.error(error);
  process.exitCode = 1;
});
