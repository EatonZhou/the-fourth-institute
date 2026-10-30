'use strict';
// A render keeps the rows that cannot have changed and redraws the rest. Each
// case below changes a row the edited messages do not include: a reply that is
// no longer the newest, a group that lost its last member, a Family work whose
// first reply is now on an older page. The row must come out redrawn.
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
let minute = 0;
const at = () => `2026-10-26T09:${String(minute++).padStart(2, '0')}:00Z`;
const human = id => ({ id, role: 'user', content: `${id} asks`, created_at: at() });
const agent = (id, extra = {}) => ({ id, role: 'assistant', content: `${id} answers`, created_at: at(), ...extra });
const workspaces = [];
const fresh = name => {
  const root = w.document.createElement('section');
  root.id = name;
  w.document.getElementById('fixtures').append(root);
  const workspace = w.PortalChatWorkspace.createWorkspace({ root, id: name, historyScope: () => name, refs: false, can_attach: false });
  workspaces.push(workspace);
  const row = id => workspace.els.messages.querySelector(`.messageList > [data-message-key="${id}"]`);
  const fold = id => row(id).querySelector('.messageContent[data-message-fold]').dataset.messageFold;
  return { workspace, row, fold };
};
const runtime = { runtimeLive: true, runtimeDetails: [{ title: 'Reading files', status: 'running', content: 'src/' }] };

try {
  {
    // The live runtime alone changes: it is patched in.
    const { workspace } = fresh('runtime-only');
    const messages = [human('h0'), agent('a1'), human('h2')];
    workspace.setMessages(messages);
    workspace.setMessages(messages, runtime);
    assert.ok(workspace.els.messages.querySelector('[data-message-key="__runtime__"]'), 'a turn that starts shows its live runtime');
  }
  {
    // A turn that starts expands every reply after the last request.
    const { workspace, fold } = fresh('fold-only');
    const messages = [human('h0'), agent('a1'), human('h2'), agent('a3'), agent('a4')];
    workspace.setMessages(messages);
    assert.equal(fold('a3'), 'collapsed', 'only the newest reply starts expanded');
    workspace.setMessages(messages, { runtimeLive: true });
    assert.equal(fold('a3'), 'expanded', 'the running turn expands its earlier output too');
  }
  {
    // A newer reply arrives: the reply that was newest folds.
    const { workspace, fold } = fresh('newest-reply');
    const messages = [human('h0'), agent('a1'), human('h2'), agent('a3')];
    workspace.setMessages(messages);
    assert.equal(fold('a3'), 'expanded');
    workspace.setMessages([...messages, human('h4'), agent('a5')]);
    assert.equal(fold('a5'), 'expanded', 'the new reply starts expanded');
    assert.equal(fold('a3'), 'collapsed', 'the previous newest reply folds');
  }
  {
    // A group of consecutive replies loses its last member.
    const { workspace, row } = fresh('group-tail');
    const messages = [human('h0'), agent('a1'), agent('a2'), agent('a3')];
    workspace.setMessages(messages);
    assert.ok(row('a3').querySelector('[data-portal-agent-group-toggle]'), 'the group toggle sits on its last reply');
    assert.equal(row('a2').querySelector('[data-portal-agent-group-toggle]'), null);
    workspace.setMessages(messages.slice(0, 3));
    assert.ok(row('a2').querySelector('[data-portal-agent-group-toggle]'), 'the toggle moves to the new last reply');
  }
  {
    // The group's last reply turns out to come from someone else: it leaves the
    // group, and stays the newest reply.
    const { workspace, row, fold } = fresh('group-tail-edited');
    const messages = [human('h0'), agent('a1'), agent('a2'), agent('a3')];
    workspace.setMessages(messages);
    const edited = [...messages.slice(0, 3), { ...messages[3], author_id: 'helper' }];
    workspace.setMessages(edited);
    assert.equal(row('a3').querySelector('[data-portal-agent-group-toggle]'), null, 'the edited reply stands alone');
    assert.equal(fold('a3'), 'expanded', 'and is still the newest');
    assert.ok(row('a2').querySelector('[data-portal-agent-group-toggle]'), 'the group now ends at the reply before it');
  }
  {
    // Older messages end with a reply that the first shown reply continues.
    const { workspace, row } = fresh('prepend-joins');
    const older = [human('o0'), agent('o1')];
    const shown = [agent('a2'), human('h3'), agent('a4')];
    workspace.setMessages(shown);
    assert.equal(row('a2').classList.contains('continued'), false);
    workspace.setMessages([...older, ...shown]);
    assert.equal(row('a2').classList.contains('continued'), true, 'the first shown reply now continues the loaded one');
  }
  {
    // A Family work's first reply turns up on an older page, past other requests.
    const { workspace, row } = fresh('prepend-work');
    const reply = id => agent(id, { family_reply: { request_message_id: 'request-r', resident_id: 'resident_111111111111' } });
    const older = [human('request-r'), reply('r-first')];
    const shown = [human('h2'), agent('x3'), human('h4'), reply('r-later')];
    workspace.setMessages(shown);
    assert.ok(row('r-later').querySelector('[data-portal-work-group-toggle="request-r"]'), 'the shown reply heads the work');
    workspace.setMessages([...older, ...shown]);
    assert.ok(row('r-first').querySelector('[data-portal-work-group-toggle="request-r"]'), 'the older reply heads it now');
    assert.equal(row('r-later').querySelector('[data-portal-work-group-toggle]'), null, 'the later reply no longer does');
    assert.equal(row('r-later').classList.contains('continued'), true, 'it continues the work');
  }
  {
    // Older messages load as a turn starts: the tail gets the live runtime too.
    const { workspace } = fresh('prepend-runtime');
    const older = [human('o0'), agent('o1')];
    const shown = [human('h2'), agent('a3'), human('h4')];
    workspace.setMessages(shown);
    workspace.setMessages([...older, ...shown], runtime);
    assert.ok(workspace.els.messages.querySelector('[data-message-key="__runtime__"]'), 'the live runtime is drawn with the older page');
  }
  console.log('PASS a partial render redraws every row its change reaches');
} finally {
  for (const workspace of workspaces) workspace.destroy();
  for (const observer of observers) observer.disconnect();
  w.close();
}
