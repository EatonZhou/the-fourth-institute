'use strict';
// Ctrl/⌘+Z in the composer takes back what the Chat itself put in: pasted text
// and inserted attachments, one at a time, newest first, for the last twenty.
// Once the composition has changed since, or the paste went in natively, the
// key is left to the browser's own undo. Clearing the composer forgets them.
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
let blobs = 0;
const revoked = [];
w.URL.createObjectURL = () => `blob:https://chat.fixture.invalid/${++blobs}`;
w.URL.revokeObjectURL = url => { revoked.push(url); };
w.eval(input.routeScript);
for (const name of ['portal_state.js', 'portal_page_state.js', 'portal_text.js', 'portal_ui_primitives.js', 'portal_ui.js',
  'portal_overlay.js', 'portal_resource_chips.js', 'portal_image_editor.js', 'portal_editor_open_service.js',
  'portal_command_registry.js', 'portal_workbench_context.js', 'portal_workbench.js', 'resident_protocol.js', ...chatWorkspaceScripts()]) {
  w.eval(fs.readFileSync(path.resolve(__dirname, '../../../management_portal/static', name), 'utf8'));
}
const settle = async () => { for (let turn = 0; turn < 5; turn += 1) await new Promise(resolve => setTimeout(resolve, 0)); };
const CONTRACT = { schema: 'portal.chat_workspace.v3', messages: [], composer: { can_send: true, queue: { items: [] } } };
let inputs = 0;
const uploads = [];
const deleted = [];
// What the host heard of the attachments, and the drafts the Chat saved.
const changes = [];
const saved = [];
const workspaces = [];
const fresh = name => {
  const root = w.document.createElement('section');
  w.document.getElementById('fixtures').append(root);
  const store = w.PortalChatWorkspace.createDraftStore({ owner: 'viewer', namespace: `undo-${name}`, delay: 600_000 });
  const workspace = w.PortalChatWorkspace.createWorkspace({
    root, id: name, historyScope: () => name, contract: CONTRACT,
    draftStore: { ...store, schedule(scope, draft) { saved.push(draft.text); return store.schedule(scope, draft); } },
    contextOptions: { skills: [{ id: 'shared/plan', name: 'Plan' }], agents: [] },
    onInput: () => { inputs += 1; },
    onAttachmentsChange: (_payload, view) => changes.push(view.attachments().length),
    uploadAttachment: () => new Promise(resolve => uploads.push(resolve)),
    deleteAttachment: item => { deleted.push(item.upload_id); return Promise.resolve({ deleted: true }); },
  });
  workspaces.push(workspace);
  const field = root.querySelector('.composerInput');
  // Put text in the input with the caret at `offset` (the end by default).
  const write = (text, offset = text.length) => {
    field.textContent = text;
    field.focus();
    const range = w.document.createRange();
    range.setStart(field.firstChild, offset);
    range.collapse(true);
    w.getSelection().removeAllRanges();
    w.getSelection().addRange(range);
    field.dispatchEvent(new w.Event('input', { bubbles: true }));
  };
  const paste = text => {
    const event = new w.Event('paste', { bubbles: true, cancelable: true });
    Object.defineProperty(event, 'clipboardData', {
      value: { files: [], items: [], types: ['text/plain'], getData: type => (type === 'text/plain' ? text : '') },
    });
    field.dispatchEvent(event);
  };
  // Whether the Chat took the key, leaving the browser's own undo out.
  const undo = () => {
    const event = new w.KeyboardEvent('keydown', { key: 'z', ctrlKey: true, bubbles: true, cancelable: true });
    field.dispatchEvent(event);
    return event.defaultPrevented;
  };
  const tokens = () => field.querySelectorAll('[data-portal-composer-attachment-token]').length;
  const chips = () => root.querySelectorAll('[data-portal-composer-attachment-id]').length;
  return { workspace, field, write, paste, undo, tokens, chips };
};
const take = list => list.splice(0);
// The Chat ignores the same file added twice at once, so each case adds its own.
const image = name => new w.File([new Uint8Array(3)], name, { type: 'image/png' });
const uploaded = id => ({ upload_id: id, path: `uploads/${id}.png`, preview_url: `/media/${id}`, filename: 'a.png', content_type: 'image/png', size: 3 });

(async () => {
  try {
    {
      // A paste is taken back, caret and all.
      const { field, write, paste, undo } = fresh('paste');
      write('hello', 2);
      paste('XY');
      assert.equal(field.textContent, 'heXYllo');
      const heard = inputs;
      take(saved);
      assert.equal(undo(), true, 'the Chat takes back its paste');
      assert.equal(field.textContent, 'hello');
      const caret = w.getSelection().getRangeAt(0);
      assert.equal(field.contains(caret.startContainer) && caret.startOffset, 2, 'the caret is where it was');
      assert.ok(inputs > heard, 'the host hears that the input changed');
      assert.equal(saved.at(-1), 'hello', 'the draft is saved as it is now');
      assert.equal(undo(), false, 'with nothing left the key goes to the browser');
      assert.equal(field.textContent, 'hello');
    }
    {
      // Once the composition has changed since, the key goes to the browser.
      const { workspace, field, write, paste, undo } = fresh('changed');
      write('hello');
      paste(' world');
      write('hello world!');
      assert.equal(undo(), false, 'typing after the paste');
      assert.equal(field.textContent, 'hello world!');
      write('again');
      paste(' more');
      workspace.setRefs({ skills: ['shared/plan'] });
      assert.equal(undo(), false, 'picking a reference after the paste');
      assert.equal(field.textContent, 'again more');
    }
    {
      // A paste the browser put in itself is the browser's to undo.
      const { field, write, paste, undo } = fresh('native');
      write('hello');
      const execCommand = w.document.execCommand;
      w.document.execCommand = (command, _ui, value) => {
        if (command !== 'insertText') return false;
        field.append(w.document.createTextNode(value));
        return true;
      };
      try {
        paste(' world');
      } finally {
        w.document.execCommand = execCommand;
      }
      assert.equal(field.textContent, 'hello world');
      assert.equal(undo(), false);
    }
    {
      // The last twenty are kept, newest first.
      const { field, write, paste, undo } = fresh('limit');
      write('a');
      for (let count = 0; count < 21; count += 1) paste('b');
      assert.equal(field.textContent, `a${'b'.repeat(21)}`);
      for (let count = 20; count > 0; count -= 1) {
        assert.equal(undo(), true);
        assert.equal(field.textContent, `a${'b'.repeat(count)}`);
      }
      assert.equal(undo(), false, 'the oldest is gone');
      assert.equal(field.textContent, 'ab');
    }
    {
      // A paste the reader took out by hand is not taken back again.
      const { field, write, paste, undo } = fresh('again');
      write('hello');
      paste('X');
      write('hello');
      paste('X');
      assert.equal(undo(), true);
      assert.equal(field.textContent, 'hello');
      write('helloX');
      assert.equal(undo(), false, 'what the reader typed is the browser\'s to undo');
    }
    {
      // An inserted attachment is taken back with the space after it.
      const { workspace, field, write, undo, tokens } = fresh('insert');
      write('hello');
      workspace.addReference({ text: 'quoted' });
      assert.equal(tokens(), 1);
      const heard = inputs;
      assert.equal(undo(), true);
      assert.equal(tokens(), 0);
      assert.equal(workspace.attachments().length, 0);
      assert.equal(field.textContent, 'hello', 'the input is as it was');
      assert.ok(inputs > heard, 'the host hears that the input changed');
      write('hello ');
      workspace.addReference({ text: 'quoted' });
      assert.equal(undo(), true);
      assert.equal(field.textContent, 'hello ', 'a space typed before it stays');
      write('hello world', 5);
      workspace.addReference({ text: 'quoted' });
      assert.equal(tokens(), 1);
      assert.equal(undo(), true);
      assert.equal(field.textContent, 'hello world', 'the text after it is as it was');
    }
    {
      // A file taken back lets its preview go, and its upload is deleted.
      const { workspace, write, undo, tokens, chips } = fresh('file');
      write('hello');
      take(revoked);
      const adding = workspace.addFiles([image('b.png')]);
      const [draft] = workspace.attachments();
      assert.equal(chips(), 1);
      take(changes);
      assert.equal(undo(), true, 'while it uploads');
      assert.equal(tokens(), 0);
      assert.equal(chips(), 0, 'its chip leaves the tray');
      assert.equal(changes.at(-1), 0, 'the host hears that it went');
      assert.deepEqual(take(revoked), [draft.preview_url], 'its preview is let go');
      uploads.shift()(uploaded('up2'));
      await adding;
      assert.deepEqual(take(deleted), ['up2'], 'the upload finishing after it is deleted');
      const again = workspace.addFiles([image('c.png')]);
      uploads.shift()(uploaded('up3'));
      await again;
      assert.equal(undo(), true, 'once uploaded');
      assert.equal(workspace.attachments().length, 0);
      assert.deepEqual(take(deleted), ['up3'], 'its upload is deleted');
    }
    {
      // A paste after an attachment went again is a paste of its own.
      const { workspace, field, write, paste, undo, tokens } = fresh('reused');
      write('hello');
      workspace.addReference({ text: 'quoted' });
      // The reader deletes the attachment and its space by hand.
      write('hello');
      assert.equal(workspace.attachments().length, 0);
      assert.equal(tokens(), 0);
      paste(' world');
      assert.equal(field.textContent, 'hello world');
      assert.equal(undo(), true);
      assert.equal(field.textContent, 'hello', 'undo takes back the paste');
      assert.equal(tokens(), 0);
    }
    {
      // An upload that finished after a paste stays finished when the paste is taken back.
      const { workspace, field, write, paste, undo } = fresh('upload');
      write('hello');
      const adding = workspace.addFiles([image('a.png')]);
      paste(' world');
      assert.equal(uploads.length, 1);
      uploads.shift()(uploaded('up1'));
      await adding;
      assert.equal(undo(), true);
      assert.ok(!field.textContent.includes('world'), 'the paste is taken back');
      const [item] = workspace.attachments();
      assert.equal(item.uploading, false, 'the attachment is still uploaded');
      assert.equal(item.upload_id, 'up1');
      assert.equal(workspace.payload().attachments.length, 1, 'and is sent');
      const token = field.querySelector('[data-portal-composer-attachment-token]');
      assert.ok(!token.classList.contains('uploading'), 'the input shows it uploaded');
      assert.equal(token.dataset.portalAttachmentPath, 'uploads/up1.png');
      assert.equal(token.querySelector('img').getAttribute('src'), '/media/up1', 'with the uploaded preview');
    }
    {
      // A send that fails puts its composition back, attachments and all.
      const { workspace, field, write, tokens, chips } = fresh('failed');
      write('hello');
      const adding = workspace.addFiles([image('d.png')]);
      uploads.shift()(uploaded('up4'));
      await adding;
      await assert.rejects(workspace.submit(() => Promise.reject(new Error('offline'))), /offline/);
      assert.ok(field.textContent.startsWith('hello'));
      assert.equal(workspace.attachments().length, 1, 'the attachment is back');
      assert.equal(tokens(), 1);
      assert.equal(chips(), 1, 'in the tray too');
      assert.equal(workspace.payload().attachments[0]?.upload_id, 'up4', 'and goes with the next send');
      assert.deepEqual(take(deleted), [], 'its upload is kept');
    }
    {
      // Clearing the composer forgets what could be taken back.
      const { workspace, write, paste, undo } = fresh('clear');
      write('hello');
      paste(' world');
      workspace.clear();
      write('hello world');
      assert.equal(undo(), false);
    }
    console.log('PASS Ctrl+Z takes back the Chat\'s own pastes and inserts, and leaves the rest to the browser');
  } finally {
    await settle();
    for (const workspace of workspaces) workspace.destroy();
    for (const observer of observers) observer.disconnect();
    w.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
