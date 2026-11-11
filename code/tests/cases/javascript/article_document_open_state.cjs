'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const window = {
  PortalI18n: { t: text => text, text: (parts, ...values) => parts.reduce((s, p, i) => s + p + (values[i] ?? ''), '') },
  PortalWorkspaceSurface: { renderDocumentTabs() {} },
  PortalDocumentSession: { create: () => ({ present() {}, updateDraft() {} }) },
  PortalPreviewContract: { isTextDocument: () => true },
  PortalPreview: { normalizeDocument: value => ({ ...value, contentKind: 'text', capabilities: {} }), configureDownloadLink() {}, dispose() {} },
  requestAnimationFrame() {},
};
vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../../../management_portal/static/article_workspace_documents.js'), 'utf8'), { window });

function setup() {
  const elements = new Map();
  const $ = id => {
    if (!elements.has(id)) elements.set(id, { textContent: '', value: '', classList: { toggle() {} },
      setAttribute() {}, setSelectionRange() {}, replaceChildren() {}, querySelector() { return null; } });
    return elements.get(id);
  };
  const state = { selectedId: 'article_current', openFile: 'main.tex', source: 'unsaved text', savedSource: 'saved text',
    documents: new Map([['main.tex', { path: 'main.tex', editable: true, textDocument: true, source: 'unsaved text', savedSource: 'saved text', viewMode: 'code' }]]),
    openTabs: ['main.tex'] };
  $('articleEditorState').textContent = 'Unsaved changes';
  $('articleSourceEditor').value = state.source;
  const requests = [], toasts = [];
  const deps = Object.fromEntries(window.PortalArticleWorkspaceDocuments.dependencies.map(key => [key, () => {}]));
  const owner = window.PortalArticleWorkspaceDocuments.createDocuments({ ...deps, $, state,
    editorValue: () => $('articleSourceEditor').value,
    request: url => new Promise((resolve, reject) => requests.push({ url, resolve, reject })),
    showToast: text => toasts.push(text),
    updateDirtyState: () => { $('articleEditorState').textContent = state.source === state.savedSource ? 'Saved' : 'Unsaved changes'; },
  });
  return { owner, state, $, requests, toasts };
}
const tick = () => new Promise(resolve => setImmediate(resolve));

(async () => {
  const f = setup();
  const loading = f.owner.openArticleFile('sections/definition.tex', { activate: false });
  assert.equal(f.$('articleEditorState').textContent, 'Unsaved changes', 'background definition lookup never advertises an editor open');
  f.requests[0].resolve({ path: 'sections/definition.tex' }); await tick();
  f.requests[1].resolve({ content: 'definition', revision: 'r1' });
  await loading;
  assert.equal(f.state.openFile, 'main.tex');
  assert.equal(f.state.source, 'unsaved text');
  assert.equal(f.$('articleEditorState').textContent, 'Unsaved changes');
  assert.equal(f.state.documents.get('sections/definition.tex').source, 'definition');

  const failedLookup = f.owner.openArticleFile('missing.tex', { activate: false });
  f.requests.at(-1).reject(new Error('file missing')); await failedLookup;
  assert.equal(f.$('articleEditorState').textContent, 'Unsaved changes', 'background error cannot overwrite current file status');
  assert.match(f.toasts.at(-1), /missing.tex.*file missing/);

  const opened = f.owner.openArticleFile('sections/new.tex');
  assert.match(f.$('articleEditorState').textContent, /Opening.*sections\/new.tex/);
  f.requests.at(-1).resolve({ path: 'sections/new.tex' }); await tick();
  f.requests.at(-1).resolve({ content: 'new file', revision: 'r2' }); await opened;
  assert.equal(f.state.openFile, 'sections/new.tex');
  assert.equal(f.$('articleEditorState').textContent, 'Saved', `successful activation clears opening state: ${JSON.stringify(f.toasts)}`);
  const denied = f.owner.openArticleFile('private.tex');
  f.requests.at(-1).reject(new Error('denied')); await denied;
  assert.match(f.$('articleEditorState').textContent, /Unable to open file.*private.tex/);

  const stale = f.owner.openArticleFile('old.tex');
  const oldNoticeCount = f.toasts.length;
  f.state.selectedId = 'article_other'; f.$('articleEditorState').textContent = 'Saved';
  f.requests.at(-1).reject(new Error('old request')); await stale;
  assert.equal(f.$('articleEditorState').textContent, 'Saved');
  assert.equal(f.toasts.length, oldNoticeCount, 'stale failure cannot affect another Article');

  const refreshed = setup();
  refreshed.state.source = refreshed.state.savedSource = 'saved text';
  refreshed.$('articleSourceEditor').value = 'saved text';
  refreshed.state.filesByPath = new Map([['main.tex', {}]]);
  const reload = refreshed.owner.refreshArticleDocuments('article_current');
  refreshed.requests[0].resolve({ path: 'main.tex' }); await tick();
  refreshed.requests[1].resolve({ content: 'remote change', revision: 'remote' }); await reload;
  assert.equal(refreshed.$('articleSourceEditor').value, 'remote change');
  assert.equal(refreshed.state.savedSource, 'remote change');
  assert.equal(refreshed.owner.hasDirtyDocuments(), false);

  const racing = refreshed.owner.refreshArticleDocuments('article_current');
  refreshed.requests.at(-1).resolve({ path: 'main.tex' }); await tick();
  refreshed.$('articleSourceEditor').value = 'typed during network';
  refreshed.requests.at(-1).resolve({ content: 'new remote change' }); await racing;
  assert.equal(refreshed.$('articleSourceEditor').value, 'typed during network');
  assert.equal(refreshed.owner.hasDirtyDocuments(), true);
  assert.equal(refreshed.state.documents.get('main.tex').source, 'typed during network');

  const removed = setup();
  removed.state.source = removed.state.savedSource = 'saved text';
  removed.$('articleSourceEditor').value = 'saved text';
  removed.state.filesByPath = new Map();
  await removed.owner.refreshArticleDocuments('article_current');
  assert.equal(removed.state.openTabs.length, 0);
  assert.equal(removed.state.openFile, '');
  assert.equal(removed.$('articleSourceEditor').value, '');
  console.log('PASS: background reads preserve editor status; foreground names file and settles; stale errors ignored');
})().catch(error => { console.error(error); process.exitCode = 1; });
