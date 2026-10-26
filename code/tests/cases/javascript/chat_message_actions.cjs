'use strict';
// What a reader can do to a message: right-click it for its menu (the gap after
// a message belongs to it), copy it or its whole run of replies, open a link it
// contains, run a host action, ask another Resident from a handoff card, and
// fold or unfold a run of replies.
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
w.CSS = { escape: value => String(value).replace(/[^a-zA-Z0-9_-]/g, char => `\\${char}`) };
w.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} };
w.matchMedia = query => Object.assign(new w.EventTarget(), { media: query, matches: false });
w.PortalIdentity = { username: () => 'viewer' };
if (!('innerText' in w.HTMLElement.prototype)) {
  Object.defineProperty(w.HTMLElement.prototype, 'innerText', { get() { return this.textContent; } });
}
// Rows with a box are laid out, and so is an open menu; everything else takes no space.
const boxes = new Map();
const MENU_BOX = { top: 0, bottom: 120, left: 0, right: 160, width: 160, height: 120, x: 0, y: 0 };
const boxOf = element => {
  if (element.matches?.('[data-portal-message-menu]')) return element.hidden ? null : MENU_BOX;
  return element.matches?.('.messageList > [data-message-key]') ? boxes.get(element.dataset.messageKey) : null;
};
w.Element.prototype.getClientRects = function () { const box = boxOf(this); return box ? [box] : []; };
w.Element.prototype.getBoundingClientRect = function () {
  return boxOf(this) || { top: 0, bottom: 0, left: 0, right: 0, width: 0, height: 0, x: 0, y: 0 };
};
const layOut = (key, top, height = 40) => boxes.set(key, { top, bottom: top + height, left: 100, right: 500, width: 400, height, x: 100, y: top });
w.eval(input.routeScript);
for (const name of ['portal_state.js', 'portal_page_state.js', 'portal_text.js', 'portal_ui_primitives.js', 'portal_ui.js',
  'portal_overlay.js', 'portal_resource_chips.js', 'portal_image_editor.js', 'portal_editor_open_service.js',
  'portal_command_registry.js', 'portal_workbench_context.js', 'portal_workbench.js', 'resident_protocol.js', ...chatWorkspaceScripts()]) {
  w.eval(fs.readFileSync(path.resolve(__dirname, '../../../management_portal/static', name), 'utf8'));
}
const copied = [];
w.PortalUI.copyText = async text => { copied.push(text); };
const opened = [];
w.open = (...args) => { opened.push(args); return null; };
const settle = () => new Promise(resolve => setTimeout(resolve, 0));

let minute = 0;
const at = () => `2026-10-26T10:${String(minute++).padStart(2, '0')}:00Z`;
const human = (id, content = `${id} asks`) => ({ id, role: 'user', content, created_at: at() });
const agent = (id, content = `${id} answers`, extra = {}) => ({ id, role: 'assistant', content, created_at: at(), ...extra });
// Bold and links are enough Markdown to tell the rendered text from the source.
const renderMarkdown = value => String(value).replace(/&/g, '&amp;').replace(/</g, '&lt;')
  .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>').replace(/\[(.+?)\]\((.+?)\)/g, '<a href="$2">$1</a>');
const workspaces = [];
const fresh = (name, extra = {}) => {
  assert.equal(w.document.querySelectorAll('body > [data-portal-message-menu]').length, 0, 'the previous case left no menu open');
  const root = w.document.createElement('section');
  root.id = name;
  w.document.getElementById('fixtures').append(root);
  const interactions = [];
  const errors = [];
  const workspace = w.PortalChatWorkspace.createWorkspace({
    root, id: name, historyScope: () => name, refs: false, can_attach: false, renderMarkdown,
    onInteractionChange: (open, menu) => interactions.push(`${open ? 'open' : 'close'} ${menu.dataset.portalMessageKey}`),
    onError: error => errors.push(error.message), ...extra,
  });
  workspaces.push(workspace);
  const row = id => workspace.els.messages.querySelector(`.messageList > [data-message-key="${id}"]`);
  const list = () => workspace.els.messages.querySelector('.messageList');
  // An open menu sits in the page, outside its message.
  const menuOf = id => workspace.els.messages.querySelector(`[data-portal-message-menu="${id}"]`)
    || w.document.querySelector(`body > [data-portal-message-menu="${id}"]`);
  const rightClick = (target, x = 200, y = 0) => {
    const event = new w.MouseEvent('contextmenu', { bubbles: true, cancelable: true, clientX: x, clientY: y });
    target.dispatchEvent(event);
    return event;
  };
  const click = target => target.dispatchEvent(new w.MouseEvent('click', { bubbles: true, cancelable: true }));
  return { workspace, row, list, menuOf, rightClick, click, interactions, errors };
};
const isOpen = menu => menu.hidden === false && menu.parentNode === w.document.body;
const pressEscape = () => w.document.dispatchEvent(new w.KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));

(async () => {
  try {
    {
      // Right-click a message: its menu opens at the pointer, out in the page.
      const { workspace, row, menuOf, rightClick, interactions } = fresh('menu');
      workspace.setMessages([human('h0'), agent('a1'), human('h2')]);
      const event = rightClick(row('h0').querySelector('.messageContent'));
      assert.equal(event.defaultPrevented, true, 'the reading pane owns its context menu');
      assert.equal(isOpen(menuOf('h0')), true, 'the message menu opens');
      assert.equal(row('h0').classList.contains('menuOpen'), true);
      rightClick(row('a1').querySelector('.messageContent'));
      assert.equal(isOpen(menuOf('a1')), true, 'another message opens its own menu');
      assert.equal(menuOf('h0').hidden, true, 'and the first one closes');
      assert.equal(row('h0').querySelector('[data-portal-message-menu]'), menuOf('h0'), 'back in its message');
      assert.equal(row('h0').classList.contains('menuOpen'), false);
      pressEscape();
      assert.equal(menuOf('a1').hidden, true, 'Escape closes the menu');
      assert.deepEqual(interactions, ['open h0', 'close h0', 'open a1', 'close a1']);
      rightClick(row('a1').querySelector('.messageContent'));
      w.document.body.dispatchEvent(new w.Event('pointerdown', { bubbles: true }));
      assert.equal(menuOf('a1').hidden, true, 'a press outside closes it');
    }
    {
      // Text fields keep the browser menu; blank space gets none and closes an open one.
      const { workspace, row, list, menuOf, rightClick } = fresh('blank', {
        renderMessageFooter: item => `<input data-test-field="${item.id}">`,
      });
      workspace.setMessages([human('h0'), agent('a1')]);
      assert.equal(rightClick(row('h0').querySelector('[data-test-field]')).defaultPrevented, false, 'a text field keeps its own menu');
      rightClick(row('h0').querySelector('.messageContent'));
      const event = rightClick(list(), 200, 500);
      assert.equal(event.defaultPrevented, true, 'blank space gets no browser menu');
      assert.equal(menuOf('h0').hidden, true, 'and closes the open message menu');
    }
    {
      // The gap after a message, up to the next message, belongs to it.
      const { workspace, list, menuOf, rightClick } = fresh('gap', {
        renderAfterMessage: (item, index, messages, context) => item.id === 'a1'
          ? `<div class="messageDelivery" data-message-key="${context.key}">Delivered</div>` : '',
      });
      workspace.setMessages([human('h0'), agent('a1'), human('h2')], { runtimeLive: true, runtimeDetails: [{ title: 'Reading', status: 'running' }] });
      const delivery = list().querySelector('.messageDelivery');
      layOut('h0', 0); layOut('a1', 50); layOut(delivery.dataset.messageKey, 95, 10); layOut('h2', 120); layOut('__runtime__', 170);
      rightClick(list(), 200, 45);
      assert.equal(isOpen(menuOf('h0')), true, 'the gap below a message opens its menu');
      rightClick(list(), 50, 45);
      assert.equal(menuOf('h0').hidden, true, 'a side gutter is nobody\'s');
      rightClick(list(), 200, 92);
      assert.equal(menuOf('a1').hidden, true, 'the gap before a row that is not a message is not the message\'s');
      rightClick(list(), 200, 165);
      assert.equal(menuOf('h2').hidden, true, 'nor the gap before a runtime card');
      rightClick(list(), 200, 300);
      assert.equal(menuOf('h2').hidden, true, 'nor the space after the conversation');
      boxes.clear();
    }
    {
      // A web link under the pointer adds "open link" to the menu.
      const { workspace, row, menuOf, rightClick, click } = fresh('link');
      workspace.setMessages([agent('a0', 'see [docs](https://docs.fixture.invalid/x) or [mail](mailto:x@fixture.invalid)')]);
      rightClick(row('a0').querySelector('a[href^="https:"]'));
      const openLink = menuOf('a0').querySelector('[data-portal-message-open-link]');
      assert.ok(openLink, 'a web link offers to open it');
      assert.equal(openLink.nextElementSibling, menuOf('a0').querySelector('[data-portal-message-copy]'), 'before the copy items');
      click(openLink);
      assert.deepEqual(opened.splice(0), [['https://docs.fixture.invalid/x', '_blank', 'noopener,noreferrer']]);
      assert.equal(menuOf('a0').hidden, true, 'opening the link closes the menu');
      rightClick(row('a0').querySelector('.messageContent'));
      assert.equal(menuOf('a0').querySelector('[data-portal-message-open-link]'), null, 'away from a link the item is gone');
      rightClick(row('a0').querySelector('a[href^="mailto:"]'));
      assert.equal(menuOf('a0').querySelector('[data-portal-message-open-link]'), null, 'only web links are offered');
      pressEscape();
    }
    {
      // Copy one message as text or Markdown, or the whole run of replies.
      const { workspace, row, menuOf, rightClick, click } = fresh('copy');
      workspace.setMessages([human('h0'), agent('a1', '**first** reply'), agent('a2', 'second reply'), human('h3')]);
      const copyFrom = (key, selector) => {
        rightClick(row(key).querySelector('.messageContent'));
        click(menuOf(key).querySelector(selector));
      };
      copyFrom('a1', '[data-portal-message-copy="text"]:not([data-portal-message-copy-scope])');
      assert.equal(menuOf('a1').hidden, true, 'copying closes the menu');
      copyFrom('a1', '[data-portal-message-copy="markdown"]:not([data-portal-message-copy-scope])');
      copyFrom('a1', '[data-portal-message-copy="text"][data-portal-message-copy-scope="replies"]');
      copyFrom('a1', '[data-portal-message-copy="markdown"][data-portal-message-copy-scope="replies"]');
      assert.equal(menuOf('a2').querySelector('[data-portal-message-copy-scope]'), null, 'a later reply in the run copies only itself');
      copyFrom('h0', '[data-portal-message-copy="markdown"]');
      await settle();
      assert.deepEqual(copied.splice(0), ['first reply', '**first** reply', 'first reply\n\nsecond reply', '**first** reply\n\nsecond reply', 'h0 asks']);
    }
    {
      // A host may supply what is copied.
      const seen = [];
      const { workspace, row, menuOf, rightClick, click } = fresh('copy-value');
      const messageCopyValue = ({ format, item, key, article, workspace: owner }) => {
        seen.push([format, key, article === row(key), owner === workspace]);
        return `${format}:${item.id}`;
      };
      workspace.setMessages([human('h0'), agent('a1'), agent('a2')], { messageCopyValue });
      rightClick(row('a1').querySelector('.messageContent'));
      click(menuOf('a1').querySelector('[data-portal-message-copy="markdown"][data-portal-message-copy-scope="replies"]'));
      await settle();
      assert.deepEqual(copied.splice(0), ['markdown:a1\n\nmarkdown:a2']);
      assert.deepEqual(seen, [['markdown', 'a1', true, true], ['markdown', 'a2', true, true]]);
    }
    {
      // A message action goes to the host with the message it belongs to.
      const calls = [];
      const { workspace, row, menuOf, rightClick, click, errors } = fresh('actions', {
        renderMessageMenuItems: () => '<button type="button" data-portal-message-action="pin">Pin</button>',
        renderMessageFooter: () => '<button type="button" data-portal-message-action="retry">Retry</button>',
        onMessageAction: context => { calls.push(['workspace', context.action, context.key]); },
      });
      workspace.setMessages([human('h0'), agent('a1')]);
      rightClick(row('a1').querySelector('.messageContent'));
      click(menuOf('a1').querySelector('[data-portal-message-action="pin"]'));
      assert.equal(menuOf('a1').hidden, true, 'a menu action closes the menu');
      click(row('h0').querySelector('[data-portal-message-action="retry"]'));
      const onMessageAction = ({ action, key, item, button, workspace: owner }) => {
        calls.push(['render', action, key, item.id, button.dataset.portalMessageAction, owner === workspace]);
        return Promise.reject(new Error(`${action} failed`));
      };
      workspace.setMessages([human('h0'), agent('a1')], { onMessageAction });
      click(row('a1').querySelector('[data-portal-message-action="retry"]'));
      await settle();
      assert.deepEqual(calls, [['workspace', 'pin', 'a1'], ['workspace', 'retry', 'h0'], ['render', 'retry', 'a1', 'a1', 'retry', true]],
        'the render options\' handler comes first');
      assert.deepEqual(errors, ['retry failed'], 'a failed action is reported');
    }
    {
      // A handoff card asks the other Resident in a message of its own.
      let finish;
      const sent = [];
      const card = { resident_id: 'resident_222222222222', title: 'Helper', reason: 'knows', ask_message: 'what is x?', recommended_action: 'ask' };
      const { workspace, row, click } = fresh('handoff', {
        onSend: (payload, owner, sendOptions) => {
          sent.push(JSON.parse(JSON.stringify([payload, owner === workspace, sendOptions])));
          return new Promise(resolve => { finish = resolve; });
        },
      });
      workspace.applyContract({ schema: 'portal.chat_workspace.v3', target: { kind: 'resident', id: 'resident_333333333333' },
        header: { title: 'Handoff' }, composer: { can_send: true } });
      workspace.setMessages([agent('a0', `Ask them:\n\`\`\`portal-resident-handoff\n${JSON.stringify(card)}\n\`\`\``)]);
      const ask = () => row('a0').querySelector('[data-portal-message-action="resident-handoff-ask"]');
      click(ask());
      assert.deepEqual(sent, [[{ text: '替我问一下 Helper（resident_222222222222）：what is x?', refs: [], attachments: [] }, true,
        { clear: false, focus: false, quote: false, includeContext: false, clearRefsOnSuccess: false }]]);
      assert.equal(ask().disabled, true, 'the button waits for the send');
      finish();
      await settle();
      assert.equal(ask().disabled, false);
      workspace.setComposer({ can_send: false });
      click(ask());
      assert.equal(sent.length, 1, 'nothing is asked while the Chat cannot send');
    }
    {
      // A run of replies folds and unfolds; a host may start runs folded.
      const { workspace, row, click } = fresh('runs');
      const messages = [human('h0'), agent('a1'), agent('a2')];
      workspace.setMessages(messages);
      const toggle = () => row('a2').querySelector('[data-portal-agent-group-toggle]');
      assert.equal(toggle().getAttribute('aria-expanded'), 'true');
      click(toggle());
      assert.equal(toggle().getAttribute('aria-expanded'), 'false', 'the run folds');
      click(toggle());
      assert.equal(toggle().getAttribute('aria-expanded'), 'true', 'and unfolds');
      workspace.setMessages(messages, { autoCollapseAgentGroups: true });
      assert.equal(toggle().getAttribute('aria-expanded'), 'false', 'a folded-by-default run starts folded');
      click(toggle());
      assert.equal(toggle().getAttribute('aria-expanded'), 'true', 'and the reader can unfold it');
    }
    {
      // Folding a Family work keeps focus on its toggle.
      const { workspace, row, click } = fresh('work');
      const reply = id => agent(id, `${id} answers`, { family_reply: { request_message_id: 'request-r', resident_id: 'resident_111111111111' } });
      workspace.setMessages([human('request-r'), reply('r1'), reply('r2')]);
      click(row('r1').querySelector('[data-portal-work-group-toggle="request-r"]'));
      const toggle = row('r1').querySelector('[data-portal-work-group-toggle="request-r"]');
      assert.equal(toggle.getAttribute('aria-expanded'), 'false', 'the work folds');
      assert.equal(w.document.activeElement, toggle, 'focus stays on its toggle');
    }
    {
      // Destroying the Workspace closes its open menu and stops its actions.
      const { workspace, row, menuOf, rightClick, click, interactions } = fresh('destroy');
      workspace.setMessages([human('h0'), agent('a1'), agent('a2')]);
      rightClick(row('a1').querySelector('.messageContent'));
      const menu = menuOf('a1');
      workspace.destroy();
      workspaces.splice(workspaces.indexOf(workspace), 1);
      assert.equal(menu.hidden, true, 'the open menu closes');
      assert.equal(row('a1').querySelector('[data-portal-message-menu]'), menu, 'back in its message');
      assert.deepEqual(interactions, ['open a1', 'close a1']);
      assert.equal(rightClick(row('a1').querySelector('.messageContent')).defaultPrevented, false, 'the browser menu is back');
      assert.equal(menu.hidden, true);
      click(row('a2').querySelector('[data-portal-agent-group-toggle]'));
      assert.equal(row('a2').querySelector('[data-portal-agent-group-toggle]').getAttribute('aria-expanded'), 'true', 'a run no longer folds');
    }
    console.log('PASS a message offers its actions');
  } finally {
    for (const workspace of workspaces) workspace.destroy();
    for (const observer of observers) observer.disconnect();
    w.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
