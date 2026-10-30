'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { JSDOM, VirtualConsole } = require('jsdom');
const { chatWorkspaceScripts } = require('./chat_workspace_modules.cjs');
const { portalWorkbenchScripts } = require('./portal_workbench_scripts.cjs');

const staticRoot = path.resolve(__dirname, '../../../management_portal/static');
const MEMBER = 'resident_111111111111';
const FACADE = 'resident_222222222222';
const NESTED_FACADE = 'resident_333333333333';
const REQUEST_A = 'request-a';
const REQUEST_B = 'request-b';
const activity = (content = 'Inspecting the isolated fixture') => ({
  kind: 'tool', title: 'Fixture activity', status: 'running', content,
});
const request = (id, relay = {}) => ({
  message_id: id, role: 'user', author_id: 'playwright', created_at: '2026-10-15T08:00:00Z',
  content_parts: [{ type: 'text', text: `Please handle ${id}` }],
  relay: { state: 'running', resident_id: MEMBER, title: 'Actual worker',
    via: [{ resident_id: FACADE, title: 'Outer team' }, { resident_id: NESTED_FACADE, title: 'Inner team' }],
    activities: [activity()], ...relay },
});
const reply = (id, requestId) => ({
  message_id: id, role: 'agent', author_id: FACADE, created_at: '2026-10-15T08:01:00Z',
  content_parts: [{ type: 'text', text: `Output ${id}` }],
  family_reply: { request_message_id: requestId, resident_id: MEMBER, title: 'Actual worker' },
});

// Real DOM, complete production modules, real CSS and original network data.
// No DOM query, rendering, grouping, visibility or controller method is mocked.
// jsdom has no layout engine: browser coverage retains physical folding/Stop,
// worker navigation and the chat scroll contracts. These checks inspect the
// state-driven DOM and CSS display contract, not pixel geometry.
function mount(routeScript, messages, { older = [], family = true, transform = (_name, source) => source } = {}) {
  const errors = [];
  const virtualConsole = new VirtualConsole();
  virtualConsole.on('jsdomError', error => errors.push(error.message));
  virtualConsole.on('error', (...args) => errors.push(args.map(String).join(' ')));
  const dom = new JSDOM('<!doctype html><html lang="zh-CN"><body><section id="familyWorkFixture"></section></body></html>', {
    url: 'https://message-state.fixture.invalid/lineage.html', pretendToBeVisual: true,
    runScripts: 'outside-only', virtualConsole,
  });
  const { window } = dom;
  const observers = new Set();
  const NativeMutationObserver = window.MutationObserver;
  window.MutationObserver = class extends NativeMutationObserver {
    constructor(callback) { super(callback); observers.add(this); }
  };
  window.CSS = { escape(value) {
    if (value === 'agent:ordinary-1') return 'agent\\:ordinary-1';
    assert.match(String(value), /^[A-Za-z_][A-Za-z0-9_-]*$/, 'known fixture selector');
    return String(value);
  } };
  // Resize observations are an absent browser input; they do not determine
  // the group controller's hidden state. Geometry remains native jsdom zeros.
  window.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} };
  window.matchMedia = query => Object.assign(new window.EventTarget(), { media: query, matches: false });
  window.PortalIdentity = { username: () => 'playwright' };
  window.eval(routeScript);
  try {
    for (const name of ['portal_state.js', 'portal_page_state.js', 'portal_text.js', 'portal_ui_primitives.js', 'portal_ui.js',
      'portal_overlay.js', 'portal_resource_chips.js', 'portal_image_editor.js',
      'portal_editor_open_service.js', 'portal_command_registry.js', 'portal_workbench_context.js',
      ...portalWorkbenchScripts(), 'resident_protocol.js', ...chatWorkspaceScripts()]) {
      window.eval(transform(name, fs.readFileSync(path.join(staticRoot, name), 'utf8')) + `\n//# sourceURL=${name}`);
    }
    const style = window.document.createElement('style');
    style.textContent = transform('portal_components.css', fs.readFileSync(path.join(staticRoot, 'portal_components.css'), 'utf8'));
    window.document.head.append(style);
    const snapshot = { messages, ...(family ? { family_facade: { resident_id: FACADE } } : {}) };
    let remaining = older;
    const workspace = window.PortalChatWorkspace.createWorkspace({
      root: window.document.getElementById('familyWorkFixture'), id: 'familyWorkChat', refs: false, can_attach: false,
      assistantName: 'Family facade', foldLongMessages: false,
      historyScope: () => 'isolated-family-work-groups',
      renderAfterMessage: (item, _index, _items, context) => item.family_status
        ? window.PortalChatWorkspace.requestProgressHtml(item.family_status, context) : '',
      onLoadOlder: () => { snapshot.messages = [...remaining, ...snapshot.messages]; remaining = []; render(); },
    });
    const render = () => workspace.setMessages(
      window.PortalChatWorkspace.projectResidentConversation({}, snapshot).messages,
      { history: { can_load_older: remaining.length > 0 } },
    );
    render();
    const root = window.document.getElementById('familyWorkChatMessages');
    const one = selector => {
      const nodes = root.querySelectorAll(selector);
      assert.equal(nodes.length, 1, `exactly one ${selector}`);
      return nodes[0];
    };
    return { window, root, workspace, snapshot, render,
      output: id => one(`article[data-message-key="${id}"]`),
      toggle: id => one(`button[data-portal-work-group-toggle="${id}"]`),
      progress: id => one(`.familyRequestStatus[data-family-request="${id}"]`),
      one,
      display(node, expected) {
        assert.ok(node?.isConnected, 'queried production DOM node is connected');
        assert.equal(window.getComputedStyle(node).display === 'none', expected === 'none',
          `${node.tagName}.${node.className} CSS display ${expected}`);
        if (expected !== 'none') {
          for (let ancestor = node; ancestor; ancestor = ancestor.parentElement) {
            const style = window.getComputedStyle(ancestor);
            assert.notEqual(style.display, 'none', 'shown DOM content has no display:none ancestor');
            assert.ok(!['hidden', 'collapse'].includes(style.visibility), 'shown DOM content has visible ancestors');
          }
        }
      },
      async close() {
        // Let jsdom's initial DOMContentLoaded finish before disposing owners;
        // otherwise its late enhancer startup would outlive the closed window.
        await new Promise(resolve => setImmediate(resolve));
        workspace.destroy();
        for (const observer of observers) observer.disconnect();
        await new Promise(resolve => setImmediate(resolve));
        window.close();
        assert.deepEqual(errors, [], 'no production/jsdom errors');
      },
    };
  } catch (error) { window.close(); throw error; }
}

const checks = {
  async 'live-roster'(routeScript) {
    const app = mount(routeScript, []);
    try {
      const metadata = { chat_workspace: { rendering: { assistant_name: 'Old family', empty_text: 'Stale member roster', user_name: 'Viewer' },
        header: { subtitle_parts: [{ identity: 'family', text: 'Family facade' }] } } };
      const snapshot = { messages: [], family_facade: { title: 'Current family', members: [{ resident_id: MEMBER, title: 'First member' }] } };
      function draw() {
        const contract = app.window.PortalChatWorkspace.projectResidentConversation(metadata, snapshot).chat_workspace;
        app.workspace.setMessages(contract.messages, { emptyText: contract.rendering.empty_text });
        assert.equal(contract.rendering.user_name, 'Viewer', 'other rendering options survive');
        return contract;
      }
      draw();
      assert.match(app.root.textContent, /First member/);
      snapshot.family_facade.members.push({ resident_id: NESTED_FACADE, title: 'Second <img> member' });
      const added = draw();
      assert.match(added.header.subtitle_parts[0].text, /2/);
      assert.match(app.root.textContent, /First member、Second <img> member/);
      assert.equal(app.root.querySelector('img'), null, 'titles remain text, not executable markup');
      snapshot.family_facade.members.shift();
      draw();
      assert.doesNotMatch(app.root.textContent, /First member|Stale member roster/);
      snapshot.family_facade.members.length = 0;
      draw();
      assert.doesNotMatch(app.root.textContent, /Second|成员：/);
      assert.match(app.root.textContent, /Current family/);
      const ordinary = app.window.PortalChatWorkspace.projectResidentConversation(metadata, { messages: [] }).chat_workspace;
      assert.equal(ordinary.rendering.empty_text, 'Stale member roster', 'ordinary chats keep their own introduction');
    } finally { await app.close(); }
  },
  async 'interleaved-append'(routeScript, options = {}) {
    const app = mount(routeScript, [request(REQUEST_A), reply('a1', REQUEST_A), request(REQUEST_B), reply('b1', REQUEST_B)], options);
    try {
      app.toggle(REQUEST_A).click();
      app.display(app.output('b1').querySelector('.messageContent'), 'shown');
      const message = reply('a2', REQUEST_A);
      app.snapshot.messages.push(message);
      assert.equal(app.workspace.appendMessage(app.window.PortalChatWorkspace.residentProtocolMessageToWorkspace(message)), true,
        'actual suffix append path, not setMessages fallback');
      assert.equal(app.output('a2').dataset.workGroupId, REQUEST_A);
      app.display(app.output('a2'), 'none');
      assert.equal(app.toggle(REQUEST_A).getAttribute('aria-expanded'), 'false');
      app.display(app.output('b1').querySelector('.messageContent'), 'shown');
      assert.equal(app.toggle(REQUEST_B).getAttribute('aria-expanded'), 'true');
      app.toggle(REQUEST_B).click();
      app.toggle(REQUEST_A).click();
      app.display(app.output('a2'), 'shown');
      app.display(app.output('b1').querySelector('.messageContent'), 'none');
      app.display(app.progress(REQUEST_A).querySelector('.familyRequestActivity'), 'shown');
      app.display(app.progress(REQUEST_B).querySelector('.familyRequestActivity'), 'none');
    } finally { await app.close(); }
  },
  async 'status-completion'(routeScript, options = {}) {
    const app = mount(routeScript, [request(REQUEST_A), reply('a1', REQUEST_A)], options);
    try {
      app.toggle(REQUEST_A).click();
      app.snapshot.messages[0].relay.activities = [activity('Updated runtime content')];
      app.snapshot.messages.push(reply('a2', REQUEST_A));
      app.render();
      assert.equal(app.toggle(REQUEST_A).getAttribute('aria-expanded'), 'false');
      app.display(app.output('a2'), 'none');
      assert.equal(app.progress(REQUEST_A).dataset.messageAfterOf, 'a2');
      assert.match(app.progress(REQUEST_A).querySelector('.familyRequestActivity').textContent, /Updated runtime content/);
      app.display(app.progress(REQUEST_A).querySelector('.familyRequestActivity'), 'none');
      app.snapshot.messages[0].relay.state = 'completed';
      app.render();
      assert.equal(app.root.querySelectorAll(`.familyRequestStatus[data-family-request="${REQUEST_A}"]`).length, 0);
      app.display(app.output('a1').querySelector('.messageContent'), 'none');
      app.display(app.output('a2'), 'none');
      app.toggle(REQUEST_A).click();
      app.display(app.output('a1').querySelector('.messageContent'), 'shown');
      app.display(app.output('a2'), 'shown');
    } finally { await app.close(); }
  },
  async 'paging-header'(routeScript, options = {}) {
    const app = mount(routeScript, [reply('a2', REQUEST_A), reply('a3', REQUEST_A)], {
      ...options, older: [request(REQUEST_A, { state: 'completed' }), reply('a1', REQUEST_A)],
    });
    try {
      app.toggle(REQUEST_A).click();
      app.one('[data-workspace-load-older]').click();
      assert.equal(app.root.querySelectorAll(`[data-work-group-id="${REQUEST_A}"][data-work-group-head]`).length, 1);
      assert.equal(app.output('a1').dataset.workGroupHead, 'true');
      assert.equal(app.toggle(REQUEST_A).getAttribute('aria-expanded'), 'false');
      app.display(app.output('a1').querySelector('.messageHead'), 'shown');
      app.display(app.output('a1').querySelector('.messageContent'), 'none');
      assert.notEqual(app.output('a2').dataset.workGroupHead, 'true');
      app.display(app.output('a2'), 'none');
      app.display(app.output('a3'), 'none');
      app.toggle(REQUEST_A).click();
      app.display(app.output('a2'), 'shown');
      app.display(app.output('a3'), 'shown');
    } finally { await app.close(); }
  },
  async 'reveal-work-item'(routeScript, options = {}) {
    const app = mount(routeScript, [request(REQUEST_A), reply('a1', REQUEST_A), reply('a2', REQUEST_A),
      request(REQUEST_B), reply('b1', REQUEST_B)], options);
    try {
      app.toggle(REQUEST_A).click();
      app.toggle(REQUEST_B).click();
      assert.equal(await app.workspace.revealMessage('a2'), true);
      app.display(app.output('a2'), 'shown');
      assert.equal(app.toggle(REQUEST_A).getAttribute('aria-expanded'), 'true');
      assert.equal(app.toggle(REQUEST_B).getAttribute('aria-expanded'), 'false');
      app.toggle(REQUEST_A).click();
      assert.equal(await app.workspace.revealMessage('a1'), true);
      app.display(app.output('a1').querySelector('.messageContent'), 'shown');
      assert.equal(app.toggle(REQUEST_B).getAttribute('aria-expanded'), 'false');
    } finally { await app.close(); }
  },
  async 'ordinary-intermediate-output'(routeScript, options = {}) {
    const messages = [request('ordinary'), ...['ordinary-1', 'ordinary-2', 'ordinary-3'].map(id => {
      const message = reply(id, 'ordinary');
      delete message.family_reply;
      return message;
    })];
    const app = mount(routeScript, messages, { ...options, family: false });
    try {
      assert.equal(app.root.querySelectorAll('[data-portal-work-group-toggle]').length, 0);
      const toggle = app.one('[data-portal-agent-group-toggle]');
      assert.equal(toggle.getAttribute('aria-expanded'), 'true');
      toggle.click();
      app.display(app.output('ordinary-1'), 'none');
      app.display(app.output('ordinary-2'), 'none');
      app.display(app.output('ordinary-3'), 'shown');
      assert.equal(await app.workspace.revealMessage('ordinary-1'), true);
      app.display(app.output('ordinary-1'), 'shown');
      app.display(app.output('ordinary-2'), 'shown');
      assert.equal(app.one('[data-portal-agent-group-toggle]').getAttribute('aria-expanded'), 'true');
    } finally { await app.close(); }
  },
  async 'file-reference-order'(routeScript, options = {}) {
    assert.ok(options.message, 'Python production projection is required');
    const app = mount(routeScript, [], options);
    try {
      app.workspace.setMessages([app.window.PortalChatWorkspace.residentProtocolMessageToWorkspace(options.message)]);
      const row = app.output('msg_file_order');
      const files = row.querySelectorAll('[data-message-attachment-kind="file"]');
      assert.equal(files.length, 1);
      assert.equal(files[0].dataset.messageAttachmentPath, `/api/uploads/upload_${'a'.repeat(24)}`);
      const content = row.querySelector('.messageContent').cloneNode(true);
      content.querySelector('[data-message-attachment-kind="file"]').replaceWith('[ATTACHMENT]');
      assert.match(content.textContent, /Before the attachment:\s*\[ATTACHMENT\]\s*After the attachment\./);
      assert.doesNotMatch(row.textContent, /\/uploads\//);
    } finally { await app.close(); }
  },
};

module.exports = { checks, mount };
if (require.main === module) {
  const name = process.argv[2];
  assert.ok(Object.hasOwn(checks, name), `Unknown independent contract: ${name}`);
  const input = JSON.parse(fs.readFileSync(0, 'utf8'));
  Promise.resolve(checks[name](input.routeScript, { message: input.message })).then(() => process.stdout.write(`PASS ${name}\n`));
}
