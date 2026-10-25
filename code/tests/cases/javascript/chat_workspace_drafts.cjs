'use strict';
// Each conversation keeps its own composer draft: switching away saves it at
// once and switching back restores the text and the picked references; another
// view of the same Chat sees an edit that is not written yet; a draft survives
// a remount; an empty composer leaves no draft; a send that fails puts the
// input back and keeps it, a send that succeeds removes it.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { JSDOM } = require('jsdom');
const { chatWorkspaceScripts } = require('./chat_workspace_modules.cjs');
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const dom = new JSDOM('<section id="fixture"></section><section id="other"></section>', {
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
const settle = async () => { for (let turn = 0; turn < 5; turn += 1) await new Promise(resolve => setTimeout(resolve, 0)); };
const CONTRACT = { schema: 'portal.chat_workspace.v3', messages: [], composer: { can_send: true, queue: { items: [] } } };
const keys = w.PortalChatWorkspace.createDraftStore({ owner: 'sample_admin', namespace: 'chat' });
const stored = scope => w.PortalState.storage.readJson(keys.storageKey(scope), null);

// The save timer never fires here, so every write below comes from a save the
// workspace makes at once.
let scope = 'conversation-a';
const mount = (rootId = 'fixture') => w.PortalChatWorkspace.createWorkspace({
  root: w.document.getElementById(rootId), id: 'chat', historyScope: () => scope,
  draftOwner: 'sample_admin', draftSaveDelay: 600_000, contract: CONTRACT,
  contextOptions: { skills: [{ id: 'shared/plan', name: 'Plan' }], agents: [] },
});
const composer = (rootId = 'fixture') => w.document.querySelector(`#${rootId} .composerInput`);
const type = async text => {
  composer().textContent = text;
  composer().dispatchEvent(new w.Event('input', { bubbles: true }));
  await settle();
};
const switchTo = async next => {
  scope = next;
  workspace.setComposer({ ...CONTRACT.composer });
  await settle();
};
let workspace = null;

(async () => {
  try {
    workspace = mount();
    await settle();
    await type('draft for A');
    workspace.setRefs({ skills: ['shared/plan'] });
    await settle();

    const other = mount('other');
    await settle();
    assert.equal(composer('other').textContent, 'draft for A', 'another view of the same Chat sees the edit before it is written');
    other.destroy();

    await switchTo('conversation-b');
    assert.equal(stored('conversation-a')?.text, 'draft for A', 'switching away writes the draft at once');
    assert.deepEqual([...stored('conversation-a').refs.skills], ['shared/plan'], 'with the picked references');
    assert.equal(composer().textContent, '', 'another conversation starts from its own, empty draft');
    assert.equal(workspace.refs().skills.size, 0, 'and its own references');
    await type('draft for B');

    await switchTo('conversation-a');
    assert.equal(composer().textContent, 'draft for A', 'switching back restores the text');
    assert.deepEqual([...workspace.refs().skills], ['shared/plan'], 'and the references');
    assert.equal(stored('conversation-b')?.text, 'draft for B', 'switching away saved the other draft');

    await type('');
    workspace.saveDraft();
    assert.deepEqual([...(stored('conversation-a')?.refs.skills || [])], ['shared/plan'], 'picked references alone are still a draft');
    workspace.setRefs({});
    workspace.saveDraft();
    assert.equal(stored('conversation-a'), null, 'an empty composer leaves no draft behind');

    workspace.destroy();
    scope = 'conversation-b';
    workspace = mount();
    await settle();
    assert.equal(composer().textContent, 'draft for B', 'a remounted workspace reads the saved draft');

    await assert.rejects(workspace.submit(() => Promise.reject(new Error('offline'))), /offline/);
    await settle();
    assert.equal(composer().textContent, 'draft for B', 'a failed send puts the input back');
    assert.equal(stored('conversation-b')?.text, 'draft for B', 'and keeps it as the draft');
    assert.ok(stored('conversation-b').submitted_message_id, 'marked with the message that failed');

    await workspace.submit(async () => ({ accepted: true }));
    await settle();
    assert.equal(composer().textContent, '', 'a successful send empties the composer');
    assert.equal(stored('conversation-b'), null, 'and removes the draft');
    console.log('PASS each conversation keeps, restores and clears its own composer draft');
  } finally {
    workspace?.destroy();
    await new Promise(resolve => setImmediate(resolve));
    for (const observer of observers) observer.disconnect();
    w.close();
  }
})().catch(error => {
  console.error(error);
  process.exitCode = 1;
});
