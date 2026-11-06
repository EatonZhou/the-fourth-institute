const assert = require('node:assert/strict');
const test = require('node:test');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const root = path.resolve(__dirname, '../../../management_portal/static');
const resource = 'thinking_0123456789ab';
const markdown = (body, version = 3) => `---\nkb_id: ${resource}\ntype: thinking\nproject: fixture\nversion:\n  number: ${version}\n---\n${body}`;
const tick = () => new Promise(setImmediate);

function harness() {
  const requests = [], notices = [], renders = [], changes = [], identities = [];
  const state = {
    detailFileId: resource, detailSelectedFileId: resource,
    detailSelectedFileKind: 'markdown', detailSelectedFileLinked: false,
    detailExpandedFolders: new Set(), detailLinkedFiles: new Map(),
    detailReadOnly: false, detailSaving: false, detailDeleting: false,
    selected: {kb_id: resource, type: 'thinking', title: 'Fixture'},
  };
  const editor = {value: '', addEventListener() {}, focus() {}};
  const el = Object.fromEntries(['mDownload', 'mEdit', 'mDelete', 'mSave', 'mCancelEdit', 'mClose', 'mText', 'mSub', 'mResourceIdentity']
    .map(key => [key, {hidden: false, disabled: false, textContent: '', className: ''}]));
  el.mText.querySelector = selector => selector === '[data-detail-body]' ? editor : null;
  el.mText.querySelectorAll = () => [];
  const context = vm.createContext({
    state, el, AbortController, URLSearchParams, setTimeout,
    document: {getElementById: () => editor},
    detailDocumentSession: null, detailPreviewRoot: null,
    detailResourceGuest: {embedded: () => true, changed: value => changes.push(value)},
    usesPackageStyleEditor: () => false,
    detailFiles: () => [{address: state.detailSelectedFileId, kind: 'markdown'}],
    renderDetailFileTree() {}, detailGraphEnabled: () => false, detailChatPayload: () => null,
    byId: () => state.selected, loadIndex: async () => {},
    alertError: error => notices.push({error}),
    knowledgeApi: {
      request: async (url, options) => {
        const payload = JSON.parse(options.body);
        requests.push({url, payload});
        return {text: payload.text, version: {number: 4}};
      },
      requestResult: async () => ({response: {ok: false, status: 404}}),
      requireJson: result => result.payload,
    },
    window: {
      PortalUIPrimitives: {escapeHtml: value => value},
      PortalI18n: {t: value => value},
      PortalUI: {
        confirm: async () => true,
        errorMessage: (action, error) => `${action}: ${error.message}`,
        toast: (message, tone, options) => notices.push({message, tone, options}),
      },
      PortalPreview: {configureDownloadLink() {}, dispose() {}},
      PortalResourceHeader: {mount: (_el, value) => identities.push(value)},
    },
  });
  for (const name of ['portal_workspace_surface_session_transitions.js', 'portal_workspace_surface_session_loading.js',
    'portal_workspace_surface_session_editing.js', 'portal_workspace_surface.js',
    'knowledge_board_detail_metadata.js', 'knowledge_board_detail.js']) {
    vm.runInContext(fs.readFileSync(path.join(root, name), 'utf8'), context, {filename: name});
  }
  // The DOM rendering is observed; all editing, draft parsing, state and request logic are production scripts.
  context.renderDetailBody = () => {
    renders.push({body: state.detailBodyDraft, meta: state.detailMetaDraft});
    context.syncDetailButtons();
  };
  context.initDetailDocumentSession();
  const session = context.detailDocumentSession;
  session.present({content: markdown('original'), version: {number: 3}}, {requestKey: resource});
  return {context, state, el, editor, session, requests, notices, renders, changes, identities};
}

test('saving a primary keeps its editor usable and presents the server canonical text', async () => {
  const h = harness();
  h.context.knowledgeApi.request = async (_url, options) => {
    h.requests.push(JSON.parse(options.body));
    return {text: markdown('saved body', 4), version: {number: 4}};
  };
  await h.context.setDetailEditing(true);
  h.session.updateDraft(markdown('first edit'));
  await h.context.saveDetail();
  assert.equal(h.state.detailEditing, true);
  assert.equal(h.state.detailMetaDraft?.fields.kb_id, resource);
  assert.match(h.state.detailMetaDraft.otherLines.join('\n'), /number: 4/);
  assert.equal(h.renders.at(-1).body, 'saved body');
  h.editor.value = 'second edit';
  h.context.updateDetailDraftFromDom();
  assert.equal(h.session.snapshot().dirty, true);
  assert.match(h.session.snapshot().draft, /second edit$/);
  await h.context.saveDetail();
  assert.equal(h.requests.length, 2);
  assert.match(h.requests[1].text, /second edit$/);
});

test('a failed save retains the draft; retry belongs to that document and draft', async () => {
  const h = harness();
  h.context.knowledgeApi.request = async (_url, options) => {
    h.requests.push(JSON.parse(options.body));
    throw Object.assign(new Error('HTTP 403'), {status: 403});
  };
  await h.context.setDetailEditing(true);
  h.session.updateDraft(markdown('private draft'));
  await h.context.saveDetail();
  assert.equal(h.session.snapshot().dirty, true);
  assert.match(h.session.snapshot().draft, /private draft$/);
  const retry = h.notices.at(-1).options.retry;
  await retry();
  assert.equal(h.requests.length, 2);
  const other = 'thinking_fedcba987654';
  h.state.detailFileId = h.state.detailSelectedFileId = other;
  h.session.present({content: 'other document'}, {requestKey: other});
  await h.context.setDetailEditing(true);
  h.session.updateDraft('other unsaved draft');
  await retry();
  assert.equal(h.requests.length, 2, 'an obsolete error must not save another document');
  assert.equal(h.session.snapshot().dirty, true);
});

test('conflict and rejected discard preserve the same draft; accepted cancel does not save', async () => {
  const h = harness();
  h.context.knowledgeApi.request = async () => { throw Object.assign(new Error('version conflict'), {status: 409}); };
  await h.context.setDetailEditing(true);
  h.session.updateDraft(markdown('unsaved'));
  await h.context.saveDetail();
  const retry = h.notices.at(-1).options.retry;
  h.context.window.PortalUI.confirm = async () => false;
  await h.context.setDetailEditing(false);
  assert.equal(h.session.snapshot().dirty, true);
  assert.match(h.session.snapshot().draft, /unsaved$/);
  h.context.window.PortalUI.confirm = async () => true;
  await h.context.setDetailEditing(false);
  assert.equal(h.session.snapshot().editing, false);
  assert.equal(h.session.snapshot().dirty, false);
  assert.equal(h.session.snapshot().payload.version.number, 3);
  h.context.knowledgeApi.request = async () => { assert.fail('cancelled draft cannot be retried'); };
  await retry();
});

test('read failure clears old content and hides mutation actions; a fresh read recovers', async () => {
  const h = harness();
  await h.context.openDetailFile({address: resource, kind: 'markdown'});
  assert.equal(h.session.snapshot().status, 'error');
  assert.equal(h.session.snapshot().payload, null);
  assert.equal(h.state.detailText, '');
  assert.equal(h.el.mEdit.hidden, true);
  assert.equal(h.el.mDelete.hidden, true);
  assert.match(h.el.mText.textContent, /404/);
  h.context.knowledgeApi.requestResult = async () => ({response: {ok: true}, payload: {kb_id: resource, text: markdown('recovered'), kind: 'markdown'}});
  await h.context.openDetailFile({address: resource, kind: 'markdown'});
  assert.equal(h.session.snapshot().status, 'ready');
  assert.equal(h.el.mEdit.hidden, false);
  assert.equal(h.el.mDelete.hidden, false);
});

test('read-only details cannot enter editing or offer deletion', async () => {
  const h = harness();
  h.state.detailReadOnly = true;
  h.context.syncDetailButtons();
  assert.equal(h.el.mEdit.hidden, true);
  assert.equal(h.el.mDelete.hidden, true);
  await h.context.setDetailEditing(true);
  assert.equal(h.session.snapshot().editing, false);
  assert.equal(h.requests.length, 0);
});

test('post-save refresh rejection is handled without losing the successful save', async () => {
  const h = harness();
  h.context.detailResourceGuest.embedded = () => false;
  h.context.loadIndex = async () => { throw new Error('index unavailable'); };
  await h.context.setDetailEditing(true);
  h.session.updateDraft(markdown('saved'));
  await h.context.saveDetail();
  await tick();
  assert.equal(h.session.snapshot().dirty, false);
  assert.equal(h.changes.length, 1);
  assert(h.notices.some(item => item.message === '资源已保存'));
  assert(h.notices.some(item => (item.error?.message || item.message || '').includes('index unavailable')));
  assert(h.state.detailMetaDraft);
});

test('a slow post-save index refresh cannot present over a later resource', async () => {
  const h = harness();
  let finish;
  h.context.detailResourceGuest.embedded = () => false;
  h.context.loadIndex = () => new Promise(resolve => { finish = resolve; });
  await h.context.setDetailEditing(true);
  h.session.updateDraft(markdown('saved'));
  await h.context.saveDetail();
  const other = 'thinking_fedcba987654';
  h.state.detailFileId = h.state.detailSelectedFileId = other;
  h.state.selected = {kb_id: other, type: 'thinking'};
  h.session.present({content: 'other text'}, {requestKey: other});
  h.identities.length = 0;
  finish();
  await tick();
  assert.equal(h.identities.length, 0, 'stale save callback must not render the current resource');
  assert.equal(h.session.snapshot().payload.content, 'other text');
});
