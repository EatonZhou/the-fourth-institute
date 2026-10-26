'use strict';
// The files and references a message carries: adding one puts a token in the
// input and a chip in the tray, and uploads it; it can be retried, removed from
// the tray or by deleting its token, replaced by an edited image, or undone. An
// upload that lands after its attachment is gone is deleted again, and every
// preview URL the Chat made is released.
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
// The image editor is replaced by one that records what the Chat asks of it.
let previewOptions = null;
w.PortalImageEditor = { open() {}, bind(root, options) { previewOptions = options; return root; } };
w.eval(input.routeScript);
for (const name of ['portal_state.js', 'portal_page_state.js', 'portal_text.js', 'portal_ui_primitives.js', 'portal_ui.js',
  'portal_overlay.js', 'portal_resource_chips.js', 'portal_editor_open_service.js',
  'portal_command_registry.js', 'portal_workbench_context.js', 'portal_workbench.js', 'resident_protocol.js', ...chatWorkspaceScripts()]) {
  w.eval(fs.readFileSync(path.resolve(__dirname, '../../../management_portal/static', name), 'utf8'));
}
const settle = async () => { for (let turn = 0; turn < 6; turn += 1) await new Promise(resolve => setTimeout(resolve, 0)); };
const take = list => list.splice(0);

const uploads = [];
const deleted = [];
const changes = [];
const errors = [];
let inputs = 0;
let scope = 'conversation-a';
const contract = () => ({
  schema: 'portal.chat_workspace.v3', messages: [],
  composer: { can_send: true, can_attach: true, queue: { items: [] }, attachment_upload_context: { conversation: scope } },
});
const workspaces = [];
const fresh = (name, extra = {}) => {
  const root = w.document.createElement('section');
  root.id = name;
  w.document.getElementById('fixtures').append(root);
  const workspace = w.PortalChatWorkspace.createWorkspace({
    root, id: name, historyScope: () => `${name}:${scope}`, draftOwner: 'viewer', draftSaveDelay: 600_000, contract: contract(),
    attachmentUploadContext: { target: 'resident_111111111111' },
    uploadAttachment: (file, context) => new Promise((resolve, reject) => uploads.push({ file, context, resolve, reject })),
    deleteAttachment: (item, context) => { deleted.push(`${item.upload_id}@${context.conversation}`); return Promise.resolve({ deleted: true }); },
    onAttachmentsChange: payload => changes.push([...payload.attachments].map(item => item.name)),
    onError: error => errors.push(error.message),
    onInput: () => { inputs += 1; },
    ...extra,
  });
  workspaces.push(workspace);
  const inputEl = () => root.querySelector('.composerInput');
  const tokens = () => [...inputEl().querySelectorAll('[data-portal-composer-attachment-token]')].map(node => node.dataset.portalComposerAttachmentToken);
  const chips = () => [...root.querySelectorAll('[data-portal-composer-attachment-id]')];
  const chip = id => root.querySelector(`[data-portal-composer-attachment-id="${w.CSS.escape(id)}"]`);
  const only = () => { const list = workspace.attachments(); assert.equal(list.length, 1); return list[0]; };
  const typed = () => inputEl().dispatchEvent(new w.Event('input', { bubbles: true }));
  return { workspace, root, inputEl, tokens, chips, chip, only, typed };
};
const image = (name = 'a.png', bytes = 3) => new w.File([new Uint8Array(bytes)], name, { type: 'image/png' });
// Put "before after" in the input with the caret after "before ".
const caretInText = input => {
  input.textContent = 'before after';
  input.focus();
  const range = w.document.createRange();
  range.setStart(input.firstChild, 7);
  range.collapse(true);
  w.getSelection().removeAllRanges();
  w.getSelection().addRange(range);
};
const tokenAtCaret = (input, id) => {
  assert.equal(input.firstChild.textContent, 'before ');
  assert.equal(input.childNodes[1].dataset.portalComposerAttachmentToken, id, 'the token goes in at the caret');
};
const uploaded = (id, extra = {}) => ({ upload_id: id, path: `uploads/${id}.png`, preview_url: `/media/${id}`, filename: `${id}.png`, content_type: 'image/png', size: 3, ...extra });

(async () => {
  try {
    {
      // A file goes in as a token and a chip, and uploads.
      const { workspace, inputEl, tokens, chip, only } = fresh('upload');
      caretInText(inputEl());
      const adding = workspace.addFiles([image()]);
      const draft = only();
      assert.equal(draft.uploading, true);
      assert.match(draft.preview_url, /^blob:/, 'an image shows from its file until it is uploaded');
      assert.deepEqual(tokens(), [draft.id], 'its token goes into the input');
      tokenAtCaret(inputEl(), draft.id);
      assert.ok(chip(draft.id).classList.contains('uploading'), 'its chip shows the upload');
      const [request] = take(uploads);
      assert.equal(request.file.name, 'a.png');
      assert.equal(request.context.target, 'resident_111111111111', 'the host\'s upload context is passed');
      assert.equal(request.context.conversation, 'conversation-a', 'and the Chat\'s');
      assert.equal(request.context.item.id, draft.id);
      assert.equal(request.context.workspace, workspace);
      assert.equal(workspace.payload().attachments.length, 0, 'an attachment still uploading is not sent');
      request.resolve(uploaded('up1'));
      await adding;
      const done = only();
      assert.equal(done.uploading, false);
      assert.equal(done.upload_id, 'up1');
      assert.equal(done.path, 'uploads/up1.png');
      assert.equal(done.preview_url, '/media/up1', 'the uploaded preview replaces the file\'s');
      assert.deepEqual(take(revoked), [draft.preview_url], 'and the file\'s preview is released');
      assert.equal(workspace.payload().attachments[0].upload_id, 'up1');
      assert.deepEqual(changes.at(-1), ['up1.png'], 'the host hears of each change');
      await workspace.addFiles([image()]);
      assert.equal(uploads.length, 0, 'the same file added again at once is ignored');
      await assert.rejects(workspace.retryAttachment(done.id), /原始附件/, 'an uploaded attachment has no file left to upload');
    }
    {
      // Several files go in together and upload one after another.
      const { workspace, tokens } = fresh('several');
      const adding = workspace.addFiles([image('m1.png'), image('m2.png', 4)]);
      const ids = [...workspace.attachments()].map(item => item.id);
      assert.equal(ids.length, 2, 'putting in the first token does not drop the second file');
      assert.deepEqual(tokens(), ids);
      take(uploads)[0].resolve(uploaded('up16'));
      await settle();
      assert.equal(uploads.length, 1, 'the second uploads after the first');
      take(uploads)[0].resolve(uploaded('up17'));
      await adding;
      assert.deepEqual([...workspace.attachments()].map(item => item.upload_id), ['up16', 'up17']);
    }
    {
      // A failed upload can be retried from its chip.
      const { workspace, root, chip, only } = fresh('retry');
      const adding = workspace.addFiles([image('b.png')]);
      take(uploads)[0].reject(new Error('disk full'));
      await adding;
      const failed = only();
      assert.equal(failed.error, 'disk full');
      assert.equal(failed.uploading, false);
      assert.deepEqual(errors, [], 'a failed upload shows on its chip');
      const retryButton = () => chip(failed.id).querySelector('[data-retry-portal-composer-attachment]');
      retryButton().click();
      take(uploads)[0].reject(new Error('still full'));
      await settle();
      assert.deepEqual(take(errors), ['still full'], 'a failed retry is reported');
      assert.equal(only().error, 'still full');
      retryButton().click();
      const [retry] = take(uploads);
      assert.equal(retry.file.name, 'b.png', 'the retry uploads the same file');
      assert.equal(only().uploading, true);
      assert.ok(chip(failed.id).classList.contains('uploading'), 'its chip shows the upload again');
      assert.equal(retryButton(), null);
      retry.resolve(uploaded('up2'));
      await settle();
      assert.equal(only().upload_id, 'up2');
      assert.equal(only().error, '');
      assert.equal(root.querySelector('[data-retry-portal-composer-attachment]'), null);
    }
    {
      // A file too large is refused.
      const { workspace } = fresh('too-large', { maxFileSize: 1024 * 1024 });
      await assert.rejects(workspace.addFiles([image('big.png', 2 * 1024 * 1024)]), /big\.png/);
      assert.equal(workspace.attachments().length, 0);
      assert.equal(uploads.length, 0);
    }
    {
      // Removing from the tray deletes the uploaded file; removing while it
      // uploads releases its preview, and the upload is deleted when it lands.
      const { workspace, tokens, chip, only } = fresh('remove');
      const first = workspace.addFiles([image('c.png')]);
      take(uploads)[0].resolve(uploaded('up3'));
      await first;
      take(revoked);
      chip(only().id).querySelector('[data-remove-portal-composer-attachment]').click();
      await settle();
      assert.equal(workspace.attachments().length, 0);
      assert.deepEqual(tokens(), [], 'its token leaves the input');
      assert.deepEqual(take(deleted), ['up3@conversation-a'], 'the uploaded file is deleted');
      assert.deepEqual(take(revoked), [], 'a preview the server gave is not the Chat\'s to release');
      const second = workspace.addFiles([image('d.png')]);
      const pending = only();
      const typedBefore = inputs;
      chip(pending.id).querySelector('[data-remove-portal-composer-attachment]').click();
      assert.equal(inputs, typedBefore + 1, 'the host hears that the input changed');
      assert.deepEqual(take(revoked), [pending.preview_url], 'the preview of a removed upload is released');
      assert.deepEqual(take(deleted), [], 'nothing is uploaded yet');
      take(uploads)[0].resolve(uploaded('up4'));
      await second;
      assert.deepEqual(take(deleted), ['up4@conversation-a'], 'the late upload is deleted');
      assert.equal(workspace.attachments().length, 0);
    }
    {
      // Deleting a token from the input removes its attachment.
      const { workspace, inputEl, chips, typed } = fresh('token');
      const adding = workspace.addFiles([image('e.png')]);
      take(uploads)[0].resolve(uploaded('up5'));
      await adding;
      inputEl().querySelector('[data-portal-composer-attachment-token]').remove();
      typed();
      await settle();
      assert.equal(workspace.attachments().length, 0);
      assert.deepEqual(chips(), [], 'its chip goes');
      assert.deepEqual(changes.at(-1), [], 'and the host hears of it');
      assert.deepEqual(take(deleted), ['up5@conversation-a']);
    }
    {
      // An edited image replaces the attachment once it is uploaded; a failed
      // upload puts the previous one back.
      const { workspace, chip, only } = fresh('edit');
      const adding = workspace.addFiles([image('f.png')]);
      take(uploads)[0].resolve(uploaded('up6', { preview_url: '' }));
      await adding;
      const original = only();
      assert.match(original.preview_url, /^blob:/, 'without a preview from the server the file\'s stays');
      const editors = [];
      previewOptions.onImagePreview({ attachmentId: original.id, attachment: previewOptions.attachmentById(original.id), title: 'f.png', open: config => editors.push(config) });
      await settle();
      const [editor] = take(editors);
      assert.equal(editor.editable, true, 'a composer image opens for editing');
      assert.equal(editor.meta.attachmentId, original.id);
      assert.equal(editor.name, 'up6.png');
      take(revoked);
      const saving = editor.onSave({ file: image('f-edited.png', 5), previewUrl: 'blob:https://chat.fixture.invalid/edited' });
      assert.equal(only().uploading, true);
      assert.equal(only().preview_url, 'blob:https://chat.fixture.invalid/edited', 'the edit shows while it uploads');
      const [request] = take(uploads);
      assert.equal(request.context.editedAttachmentId, original.id);
      assert.equal(request.file.name, 'f-edited.png');
      await assert.rejects(editor.onSave({ file: image('f-twice.png'), previewUrl: 'blob:https://chat.fixture.invalid/twice' }), /还在上传/,
        'an image still uploading cannot be replaced');
      assert.equal(uploads.length, 0);
      request.resolve(uploaded('up7'));
      await saving;
      const edited = only();
      assert.equal(edited.id, original.id, 'it keeps its place');
      assert.equal(edited.upload_id, 'up7');
      assert.equal(edited.preview_url, '/media/up7');
      assert.equal(edited.original.upload_id, 'up6', 'and remembers the image it replaced');
      assert.deepEqual(take(revoked), [original.preview_url, 'blob:https://chat.fixture.invalid/edited'],
        'the previews of the replaced image and of the edit are released');
      assert.ok(chip(edited.id));
      const failing = editor.onSave({ file: image('f-again.png'), previewUrl: 'blob:https://chat.fixture.invalid/again' });
      take(uploads)[0].reject(new Error('upload refused'));
      await assert.rejects(failing, /upload refused/);
      assert.equal(only().upload_id, 'up7', 'the previous image comes back');
      assert.equal(only().uploading, false);
      assert.deepEqual(take(revoked), ['blob:https://chat.fixture.invalid/again']);
      const removing = editor.onSave({ file: image('f-gone.png'), previewUrl: 'blob:https://chat.fixture.invalid/gone' });
      chip(edited.id).querySelector('[data-remove-portal-composer-attachment]').click();
      take(uploads)[0].resolve(uploaded('up8'));
      await removing;
      assert.equal(workspace.attachments().length, 0, 'an image removed while its edit uploads stays removed');
      assert.ok(take(deleted).includes('up8@conversation-a'), 'and the edit\'s upload is deleted');
      assert.ok(take(revoked).includes('blob:https://chat.fixture.invalid/gone'));
    }
    {
      // An edit that lands after its conversation was left does not touch the
      // conversation now shown, even an attachment there with the same ID.
      const { workspace, only } = fresh('edit-away');
      const shown = conversation => {
        scope = conversation;
        workspace.setComposer(contract().composer);
      };
      shown('conversation-b');
      workspace.setAttachments([{ id: 'shared', kind: 'image', name: 'b.png', upload_id: 'upb', path: 'uploads/b.png', preview_url: '/media/b' }]);
      shown('conversation-a');
      workspace.setAttachments([{ id: 'shared', kind: 'image', name: 'a.png', upload_id: 'upa', path: 'uploads/a.png', preview_url: '/media/a' }]);
      const editors = [];
      previewOptions.onImagePreview({ attachmentId: 'shared', attachment: only(), open: config => editors.push(config) });
      await settle();
      const saving = editors[0].onSave({ file: image('a-edited.png'), previewUrl: 'blob:https://chat.fixture.invalid/away' });
      shown('conversation-b');
      assert.equal(only().upload_id, 'upb');
      take(uploads)[0].resolve(uploaded('up9'));
      await saving;
      assert.equal(only().upload_id, 'upb', 'the other conversation\'s attachment is left alone');
      assert.deepEqual(take(deleted), ['up9@conversation-b'], 'the edit\'s upload is deleted');
      assert.deepEqual(take(revoked), ['blob:https://chat.fixture.invalid/away']);
      shown('conversation-a');
      const failing = editors[0].onSave({ file: image('a-again.png'), previewUrl: 'blob:https://chat.fixture.invalid/away-again' });
      shown('conversation-b');
      take(uploads)[0].reject(new Error('gone'));
      await assert.rejects(failing, /gone/);
      assert.equal(only().upload_id, 'upb', 'a failed edit restores nothing in the other conversation');
      assert.equal(only().name, 'b.png');
      shown('conversation-a');
    }
    {
      // A reference to a passage goes in as a token, without a chip or upload.
      const { workspace, inputEl, tokens, chips } = fresh('reference');
      caretInText(inputEl());
      const reference = workspace.addReference({ text: 'the quoted lines', title: 'Notes' });
      assert.ok(reference);
      assert.deepEqual(tokens(), [reference.id]);
      tokenAtCaret(inputEl(), reference.id);
      assert.deepEqual(chips(), [], 'a reference has no chip');
      assert.equal(uploads.length, 0);
      assert.equal(workspace.payload().attachments[0].reference_text, 'the quoted lines');
      assert.equal(workspace.addReference({ text: '' }), null, 'an empty passage is not added');
    }
    {
      // Undo takes back the attachments just added.
      const { workspace, inputEl, tokens } = fresh('undo');
      const adding = workspace.addFiles([image('h.png')]);
      take(uploads)[0].resolve(uploaded('up10'));
      await adding;
      take(revoked);
      inputEl().dispatchEvent(new w.KeyboardEvent('keydown', { key: 'z', ctrlKey: true, bubbles: true, cancelable: true }));
      await settle();
      assert.equal(workspace.attachments().length, 0);
      assert.deepEqual(tokens(), []);
      assert.deepEqual(take(deleted), ['up10@conversation-a'], 'the undone upload is deleted');
      const again = workspace.addFiles([image('k.png')]);
      take(uploads)[0].resolve(uploaded('up15'));
      await again;
      inputEl().append(w.document.createTextNode(' and more'));
      inputEl().dispatchEvent(new w.Event('input', { bubbles: true }));
      inputEl().dispatchEvent(new w.KeyboardEvent('keydown', { key: 'z', ctrlKey: true, bubbles: true, cancelable: true }));
      await settle();
      assert.equal(workspace.attachments().length, 1, 'once more was typed, undo leaves the attachment to the input\'s own undo');
      assert.deepEqual(take(deleted), []);
    }
    {
      // A host can set or patch the attachments.
      const { workspace, chip } = fresh('host');
      take(changes);
      workspace.setAttachments([{ id: 'given', kind: 'file', name: 'given.txt', path: 'docs/given.txt' }]);
      assert.ok(chip('given'));
      workspace.updateAttachment('given', { name: 'renamed.txt' });
      assert.equal(chip('given').querySelector('.composerImageName').textContent, 'renamed.txt');
      assert.deepEqual(take(changes), [['given.txt'], ['renamed.txt']]);
    }
    {
      // Clearing the composer and destroying the Chat release the previews it made.
      const { workspace } = fresh('release');
      const adding = workspace.addFiles([image('i.png')]);
      const pending = workspace.attachments()[0];
      take(revoked);
      workspace.clear();
      assert.deepEqual(take(revoked), [pending.preview_url], 'clearing releases the previews of what it clears');
      take(uploads)[0].resolve(uploaded('up11'));
      await adding;
      take(deleted);
      const next = workspace.addFiles([image('j.png')]);
      const kept = workspace.attachments()[0];
      workspace.destroy();
      workspaces.splice(workspaces.indexOf(workspace), 1);
      assert.ok(take(revoked).includes(kept.preview_url), 'destroying releases every preview it still holds');
      take(uploads)[0].resolve(uploaded('up12'));
      await next.catch(() => {});
    }
    assert.deepEqual(errors, []);
    console.log('PASS a message\'s attachments upload, change and go, and release their previews');
  } finally {
    for (const workspace of workspaces) workspace.destroy();
    for (const observer of observers) observer.disconnect();
    w.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
