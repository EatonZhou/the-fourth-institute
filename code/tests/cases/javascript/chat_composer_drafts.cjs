'use strict';
// When a Chat saves the message being written: an edit is saved after a pause
// and at once when the page is hidden or left, the conversation switches, or
// the Chat goes; loading, hiding or destroying an untouched composition writes
// nothing; a caret move updates the saved draft only while it is the draft
// shown; a cleared or confirmed draft is not written back. A send that fails
// is kept as the draft of the message it tried to send, and goes once that
// message turns out to have arrived.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { JSDOM } = require('jsdom');
const { chatWorkspaceScripts } = require('./chat_workspace_modules.cjs');
const { portalWorkbenchScripts } = require('./portal_workbench_scripts.cjs');
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
w.PortalIdentity = { username: () => 'viewer' };
let visibility = 'visible';
Object.defineProperty(w.document, 'visibilityState', { configurable: true, get: () => visibility });
w.eval(input.routeScript);
for (const name of ['portal_state.js', 'portal_page_state.js', 'portal_text.js', 'portal_ui_primitives.js', 'portal_ui.js',
  'portal_overlay.js', 'portal_resource_chips.js', 'portal_image_editor.js', 'portal_editor_open_service.js',
  'portal_command_registry.js', 'portal_workbench_context.js', ...portalWorkbenchScripts(), 'resident_protocol.js', ...chatWorkspaceScripts()]) {
  w.eval(fs.readFileSync(path.resolve(__dirname, '../../../management_portal/static', name), 'utf8'));
}
const settle = async () => { for (let turn = 0; turn < 5; turn += 1) await new Promise(resolve => setTimeout(resolve, 0)); };
const CONTRACT = { schema: 'portal.chat_workspace.v3', messages: [], composer: { can_send: true, queue: { items: [] } } };

// The save timer never fires: a draft is written only when the Chat writes it at once.
const store = w.PortalChatWorkspace.createDraftStore({ owner: 'viewer', namespace: 'chat', delay: 600_000 });
const writes = [];
const recording = {
  ...store,
  schedule(scope, draft) { writes.push(`schedule ${scope}: ${draft.text}`); return store.schedule(scope, draft); },
  flush(scope) { writes.push(`flush ${scope}`); return store.flush(scope); },
  clear(scope) { writes.push(`clear ${scope}`); return store.clear(scope); },
  write(scope, draft) { writes.push(`write ${scope}: ${draft.text}`); return store.write(scope, draft); },
};
const stored = scope => w.PortalState.storage.readJson(store.storageKey(scope), null);
const take = list => list.splice(0);

let scope = 'conversation-a';
const views = new Map();
const mount = (rootId = 'fixture') => {
  const workspace = w.PortalChatWorkspace.createWorkspace({
    root: w.document.getElementById(rootId), id: 'chat', historyScope: () => scope,
    draftStore: recording, contract: CONTRACT,
    contextOptions: { skills: [{ id: 'shared/plan', name: 'Plan' }], agents: [] },
  });
  views.set(rootId, workspace);
  return workspace;
};
const composer = (rootId = 'fixture') => w.document.querySelector(`#${rootId} .composerInput`);
const type = async (text, rootId = 'fixture') => {
  composer(rootId).textContent = text;
  composer(rootId).dispatchEvent(new w.Event('input', { bubbles: true }));
  await settle();
};
const caretAt = (offset, rootId = 'fixture') => {
  const range = w.document.createRange();
  range.setStart(composer(rootId).firstChild, offset);
  range.collapse(true);
  w.getSelection().removeAllRanges();
  w.getSelection().addRange(range);
};
const hide = () => {
  visibility = 'hidden';
  w.document.dispatchEvent(new w.Event('visibilitychange'));
  visibility = 'visible';
};
const show = () => w.document.dispatchEvent(new w.Event('visibilitychange'));
const switchTo = async (next, workspace) => {
  scope = next;
  workspace.setComposer({ ...CONTRACT.composer });
  await settle();
};

(async () => {
  try {
    {
      // An untouched composition is not an edit.
      store.write('conversation-a', { text: 'saved before' });
      const before = stored('conversation-a');
      const workspace = mount();
      await settle();
      assert.equal(composer().textContent, 'saved before', 'the saved draft is loaded');
      take(writes);
      hide();
      w.dispatchEvent(new w.Event('pagehide'));
      workspace.setLoading('loading');
      workspace.clearLoading();
      await switchTo('conversation-b', workspace);
      await switchTo('conversation-a', workspace);
      assert.equal(composer().textContent, 'saved before');
      workspace.destroy();
      assert.deepEqual(take(writes).filter(entry => entry.startsWith('schedule')), [], 'nothing was written');
      assert.deepEqual(stored('conversation-a'), before, 'the saved draft is as it was');
    }
    {
      // An edit is saved after a pause, and at once when the page is hidden or left.
      const workspace = mount();
      await settle();
      take(writes);
      await type('first edit');
      assert.deepEqual(take(writes), ['schedule conversation-a: first edit'], 'an edit is saved after a pause');
      assert.equal(stored('conversation-a').text, 'saved before', 'and not yet written');
      show();
      assert.deepEqual(take(writes), [], 'the page staying visible writes nothing');
      hide();
      assert.equal(stored('conversation-a').text, 'first edit', 'hiding the page writes the edit');
      assert.deepEqual(take(writes), ['flush conversation-a'], 'without saving it a second time');
      await type('second edit');
      take(writes);
      w.dispatchEvent(new w.Event('pagehide'));
      assert.equal(stored('conversation-a').text, 'second edit', 'leaving the page writes the edit');
      await type('third edit');
      await switchTo('conversation-b', workspace);
      assert.equal(stored('conversation-a').text, 'third edit', 'switching away writes the edit');
      await switchTo('conversation-a', workspace);
      await type('fourth edit');
      workspace.restoreDraft('conversation-b');
      assert.equal(stored('conversation-a').text, 'fourth edit', 'showing another conversation\'s draft writes the edit');
      workspace.restoreDraft('conversation-a');
      assert.equal(composer().textContent, 'fourth edit');
      await type('last edit');
      workspace.destroy();
      assert.equal(stored('conversation-a').text, 'last edit', 'destroying the Chat writes the edit');
      take(writes);
      hide();
      w.dispatchEvent(new w.Event('pagehide'));
      assert.deepEqual(take(writes), [], 'a destroyed Chat no longer listens to the page');
    }
    {
      // A caret move updates the saved draft only while it is the draft shown.
      const workspace = mount();
      const other = mount('other');
      await settle();
      caretAt(2);
      take(writes);
      workspace.saveDraft();
      assert.deepEqual(stored('conversation-a').selection.startOffset, 2, 'a caret move is saved with the draft it belongs to');
      assert.deepEqual(take(writes), ['schedule conversation-a: last edit', 'flush conversation-a']);
      await type('written elsewhere', 'other');
      other.saveDraft();
      take(writes);
      caretAt(4);
      workspace.saveDraft();
      assert.equal(stored('conversation-a').text, 'written elsewhere', 'a caret move does not write over a newer draft');
      assert.deepEqual(take(writes).filter(entry => entry.startsWith('schedule')), []);
      other.destroy();
      workspace.destroy();
    }
    {
      // A cleared draft is not written back.
      const workspace = mount();
      await settle();
      await type('to be kept');
      workspace.clear({ clearDraft: false });
      assert.equal(composer().textContent, '');
      assert.equal(stored('conversation-a').text, 'to be kept', 'clearing the input can keep the draft');
      take(writes);
      hide();
      assert.deepEqual(take(writes).filter(entry => entry.startsWith('schedule')), [], 'and the empty input is not saved over it');
      await type('to be dropped');
      take(writes);
      workspace.clear();
      assert.equal(stored('conversation-a'), null, 'clearing drops the draft');
      hide();
      assert.equal(stored('conversation-a'), null);
      assert.deepEqual(take(writes).filter(entry => entry.startsWith('schedule')), [], 'and the emptied input is not saved');
      await type('shown draft');
      hide();
      assert.equal(workspace.clearDraft('conversation-a', { text: 'another draft' }), false, 'a draft is cleared only if it is the one expected');
      assert.equal(stored('conversation-a').text, 'shown draft');
      assert.equal(workspace.clearDraft('conversation-a', { text: 'shown draft' }), true);
      assert.equal(stored('conversation-a'), null);
      take(writes);
      hide();
      assert.deepEqual(take(writes).filter(entry => entry.startsWith('schedule')), [], 'a cleared draft is not written back');
      store.write('conversation-b', { text: 'b draft' });
      assert.equal(workspace.clearDraft('conversation-b'), true, 'another conversation\'s draft can be cleared');
      assert.equal(stored('conversation-b'), null);
      workspace.destroy();
    }
    {
      // A send that fails is kept as the draft of the message it tried to send.
      const workspace = mount();
      await settle();
      await type('please send');
      let attempt = null;
      await assert.rejects(workspace.submit((_payload, context) => {
        attempt = context.messageIdentity;
        return Promise.reject(new Error('offline'));
      }), /offline/);
      assert.equal(composer().textContent, 'please send', 'the input comes back');
      assert.equal(stored('conversation-a').submitted_message_id, attempt, 'and is saved as the message it tried to send');
      workspace.destroy();
      const reopened = mount();
      await settle();
      assert.equal(composer().textContent, 'please send');
      reopened.confirmSubmittedMessages(['some-other-message']);
      assert.equal(composer().textContent, 'please send', 'another message arriving changes nothing');
      reopened.confirmSubmittedMessages([attempt], 'conversation-b');
      assert.equal(composer().textContent, 'please send', 'nor does the message arriving in another conversation');
      take(writes);
      reopened.confirmSubmittedMessages([attempt]);
      assert.equal(composer().textContent, '', 'once that message arrives the recovered input goes');
      assert.equal(stored('conversation-a'), null, 'and so does its draft');
      hide();
      assert.deepEqual(take(writes).filter(entry => entry.startsWith('schedule')), [], 'and it is not written back');

      await type('with a reference');
      await assert.rejects(reopened.submit((_payload, context) => {
        attempt = context.messageIdentity;
        return Promise.reject(new Error('offline'));
      }), /offline/);
      reopened.setRefs({ skills: ['shared/plan'] });
      hide();
      assert.equal(stored('conversation-a').submitted_message_id, '', 'a recovery given a reference is a new draft');
      reopened.confirmSubmittedMessages([attempt]);
      assert.equal(composer().textContent, 'with a reference', 'and stays when the old message arrives');
      reopened.clear();

      await type('send again');
      let second = null;
      await assert.rejects(reopened.submit((_payload, context) => {
        second = context.messageIdentity;
        return Promise.reject(new Error('offline'));
      }), /offline/);
      await type('send again, edited');
      hide();
      assert.equal(stored('conversation-a').submitted_message_id, '', 'an edited recovery is a new draft');
      reopened.confirmSubmittedMessages([second]);
      assert.equal(composer().textContent, 'send again, edited', 'and stays when the old message arrives');
      assert.equal(stored('conversation-a').text, 'send again, edited');

      reopened.clear();
      await type('sent from b');
      let away = null;
      let fail = null;
      const sending = reopened.submit((_payload, context) => {
        away = context.messageIdentity;
        return new Promise((_resolve, reject) => { fail = reject; });
      });
      await settle();
      await switchTo('conversation-b', reopened);
      fail(new Error('offline'));
      await assert.rejects(sending, /offline/);
      assert.equal(composer().textContent, '', 'a send that fails after the conversation changed leaves the input shown');
      assert.equal(stored('conversation-a').text, 'sent from b', 'it is kept as the draft of its own conversation');
      assert.equal(stored('conversation-a').submitted_message_id, away);
      await switchTo('conversation-a', reopened);
      reopened.confirmSubmittedMessages([away], 'conversation-a');
      assert.equal(composer().textContent, '');
      assert.equal(stored('conversation-a'), null);

      await type('sent once more');
      const later = reopened.submit(() => new Promise((_resolve, reject) => { fail = reject; }));
      await settle();
      await switchTo('conversation-b', reopened);
      store.write('conversation-a', { text: 'written meanwhile' });
      fail(new Error('offline'));
      await assert.rejects(later, /offline/);
      assert.equal(stored('conversation-a').text, 'written meanwhile', 'it does not write over a draft written there meanwhile');
      reopened.destroy();
    }
    console.log('PASS a Chat saves the message being written when it changes, and only then');
  } finally {
    for (const view of views.values()) view.destroy();
    for (const observer of observers) observer.disconnect();
    w.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
