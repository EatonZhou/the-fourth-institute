const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs'), path = require('node:path'), vm = require('node:vm');
const {babelParse} = require('./babel_parse.cjs');
const {workbenchModuleFiles} = require('./lineage_workbench_modules.cjs');
const {chatWorkspaceScripts} = require('./chat_workspace_modules.cjs');
const {STATIC: SURFACE_STATIC, portalWorkspaceSurfaceScripts} = require('./portal_workspace_surface_scripts.cjs');
const noop = () => {};
const i18n = {t: s => s, text: (parts, ...values) => parts.reduce((s, p, i) => s + p + (values[i] ?? ''), '')};
// The named declarations in one script, or in any of several scripts.
function declarations(files, names) {
  const found = {};
  for (const file of [].concat(files)) {
    const source = fs.readFileSync(path.resolve(__dirname, '../../../management_portal/static', file), 'utf8');
    const visit = node => {
      if (!node || typeof node !== 'object') return;
      const name = node.type === 'ClassMethod' ? node.key?.name : node.id?.name;
      if (names.includes(name)) {
        const fragment = source.slice(node.start, node.end);
        found[name] = node.type === 'VariableDeclarator' ? `const ${fragment};`
          : node.type === 'ClassMethod' ? `const ${name} = ({${fragment}}).${name};` : fragment;
      }
      for (const child of Object.values(node)) {
        if (Array.isArray(child)) child.forEach(visit);
        else if (child && typeof child === 'object') visit(child);
      }
    };
    visit(babelParse(source, file));
  }
  return names.map(name => {assert.ok(found[name], `${files}: ${name}`); return found[name];}).join('\n')
    + '\n' + names.map(name => `this.${name} = ${name};`).join('\n');
}
function deferred() {let resolve, reject; const promise = new Promise((a, b) => {resolve = a; reject = b;}); return {promise, resolve, reject};}

function deletionFixture() {
  const target = {id: 'resident_deleted', kind: 'resident', key: 'resident:resident_deleted', lineage_id: 'lineage_one', title: 'Known title'};
  const state = {chatTarget: target, residents: [{resident_id: target.id, title: target.title}], folderRequestSequence: 0};
  const removal = deferred(), historyRead = deferred(), calls = [], notices = [];
  const location = new URL(`https://portal.test/lineage?resident=${target.id}`);
  const shared = vm.createContext({window: {PortalI18n: i18n,
    PortalResidentService: {inputHistory: () => historyRead.promise}}, residentSessionScope: t => t.resident_id,
    residentProtocolMessageToWorkspace: message => message,
    RESIDENT_CHAT_WORKSPACE_SCHEMA: 'portal.chat_workspace.v3', newMessageIdentity: () => 'notice'});
  vm.runInContext(declarations(chatWorkspaceScripts(), ['rejectUnavailableTarget', 'showError', 'loadInputHistory']), shared);
  function panel(id) {
    const identity = {resident_id: id, title: 'Known title'};
    return {target: () => identity, workspace: {target: () => identity, inputHistoryScope: () => identity.resident_id}, options: {}, questions: {reset: noop},
      targetRevision: 0, protocol: {destroy: () => calls.push(['detach', id])},
      rejectUnavailableTarget: shared.rejectUnavailableTarget, showError: shared.showError,
      loadInputHistory: shared.loadInputHistory,
      showState(contract) {this.contract = contract;}, currentDetail: () => null,
      closeSurface: () => {throw Error('must not erase identity');}};
  }
  const primary = panel(target.id), secondary = panel(target.id), other = panel('resident_other');
  const ctx = vm.createContext({window: {location, PortalI18n: i18n,
      PortalUI: {toast: (...args) => notices.push(args), errorMessage: (label, err) => `${label}: ${err.message}`},
      PortalConversationStore: {discardResident: id => calls.push(['discard', id])}},
    URL, history: {state: {}, replaceState: (_a, _b, url) => {location.href = new URL(url, location).href;}},
    state, pendingResidentDeletions: new Set(), unavailableResidentBindings: new Set(),
    agentFloatingMenuOverlay: null, agentChatPanel: primary,
    lineageWorkbench: {getPanelsByType: () => [primary, secondary, other]},
    currentPreviewTabsController: () => null, currentPersonalWorkspaces: () => null,
    residentService: {remove: () => {calls.push(['remove']); return removal.promise;}, folder: () => {throw Error('deleted folder read');}},
    currentChatTarget: () => state.chatTarget, selectedResidentId: () => state.chatTarget?.id || '',
    currentResident: () => state.residents.find(r => r.resident_id === state.chatTarget?.id),
    commitChatTarget: t => {state.chatTarget = t;}, beginChatWorkspaceNavigation: noop,
    writeLineageUiState: noop, resetChatRenderState: noop, dismissRecentAgentSession: noop, recentAgentSessionKey: x => x.resident_id,
    renderAgentList: noop, renderFavoriteList: noop, syncSelectedResidentSurface: noop,
    refreshResidentSidebarIfDue: async () => {}, syncLineageHatgPanel: async () => {},
    activeChatTargetId: () => state.chatTarget?.key, activeLineageId: () => 'lineage_one',
    writeLastSelection: noop, syncChatTargetLocation: noop, stopPreviewRefresh: noop,
  });
  vm.runInContext(declarations(['lineage_workbench.js', ...workbenchModuleFiles()], ['deleteResidentFromWorkbench', 'retireDeletedResidentSurfaces',
    'clearDeletedResidentLocation', 'loadDetail', 'loadFolder']), ctx);
  return {ctx, target, state, removal, historyRead, calls, notices, primary, secondary, other, location};
}

test('a locally retired Resident detaches Chat and does not suggest waiting for service recovery', () => {
  const f = deletionFixture();
  const panel = f.primary;
  const error = Object.assign(new Error('Resident has been deleted'), {code:'resident.deleted'});
  assert.equal(panel.rejectUnavailableTarget(error), true);
  assert.equal(error.status, undefined, 'a local lifecycle error is not a fabricated HTTP 404');
  assert.equal(panel.protocol, null);
  assert.equal(panel.contract.subtitle, 'Resident 不可用');
  assert.equal(panel.contract.composer.meta, '请打开其他 Resident。');
  assert.equal(panel.contract.composer.can_send, false);
  assert.equal(panel.contract.composer.can_edit, false);
  assert.equal(f.other.contract, undefined);
});

test('history for a deleted Resident uses the unavailable Chat state instead of a global error', async () => {
  const f = deletionFixture();
  const pending = f.primary.loadInputHistory(f.target.id);
  f.historyRead.reject(Object.assign(Error('resident not found'), {status: 404}));
  assert.equal(await pending, null);
  assert.equal(f.primary.target().resident_id, f.target.id);
  assert.equal(f.primary.contract.subtitle, 'Resident 不可用');
  assert.equal(f.primary.contract.composer.can_send, false);
  assert.equal(f.primary.protocol, null);
  assert.equal(f.other.contract, undefined);
});

test('late history results cannot affect a new Chat binding, including a reload of the same Resident', async () => {
  for (const outcome of ['missing', 'retired', 'success']) {
    for (const sameResident of [true, false]) {
      const f = deletionFixture();
      const pending = f.primary.loadInputHistory(f.target.id);
      f.primary.targetRevision++;
      if (!sameResident) f.primary.target().resident_id = 'resident_other';
      if (outcome === 'missing') f.historyRead.reject(Object.assign(Error('resident not found'), {status: 404}));
      else if (outcome === 'retired') f.historyRead.reject(Object.assign(Error('Resident has been deleted'), {code: 'resident.deleted'}));
      else f.historyRead.resolve({messages: [{message_id: 'old-message'}]});
      assert.equal(await pending, null);
      assert.equal(f.primary.contract, undefined);
      assert.ok(f.primary.protocol);
    }
  }
});

test('history rejected by local deletion retires only its current Chat binding', async () => {
  const f = deletionFixture();
  const pending = f.primary.loadInputHistory(f.target.id);
  const error = Object.assign(Error('Resident has been deleted'), {code: 'resident.deleted'});
  f.historyRead.reject(error);
  assert.equal(await pending, null);
  assert.equal(error.status, undefined);
  assert.equal(f.primary.contract.subtitle, 'Resident 不可用');
  assert.equal(f.primary.contract.composer.can_send, false);
  assert.equal(f.primary.protocol, null);
  assert.equal(f.other.contract, undefined);
});

test('history-specific permission and service errors still reach the error handler', async () => {
  for (const status of [403, 500]) {
    const f = deletionFixture();
    const pending = f.primary.loadInputHistory(f.target.id);
    const error = Object.assign(Error('history unavailable'), {status});
    f.historyRead.reject(error);
    await assert.rejects(pending, value => value === error);
    assert.equal(f.primary.contract, undefined);
    assert.ok(f.primary.protocol);
  }
});

test('successful deletion keeps every matching Chat identity with a disabled composer', async () => {
  const f = deletionFixture();
  const pending = f.ctx.deleteResidentFromWorkbench({residentId: f.target.id});
  assert.equal(f.state.chatTarget, f.target, 'pending mutation keeps identity');
  f.removal.resolve({resident_id: f.target.id});
  assert.ok(await pending);
  assert.equal(f.state.chatTarget.id, f.target.id);
  assert.equal(f.location.searchParams.get('resident'), f.target.id);
  assert.equal(f.state.residents.length, 0);
  for (const panel of [f.primary, f.secondary]) {
    assert.equal(panel.target().resident_id, f.target.id);
    assert.equal(panel.contract.title, 'Known title');
    assert.equal(panel.contract.subtitle, 'Resident 不可用');
    assert.equal(panel.contract.composer.can_send, false);
    assert.equal(panel.contract.composer.can_edit, false);
    assert.equal(panel.protocol, null);
  }
  assert.equal(f.other.contract, undefined);
  await f.ctx.loadDetail(true);
  await f.ctx.loadFolder();
  assert.equal(f.notices.length, 0);
});

test('failed deletion and navigation while deletion is pending preserve the current selection', async () => {
  for (const fail of [true, false]) {
    const f = deletionFixture();
    const pending = f.ctx.deleteResidentFromWorkbench({residentId: f.target.id});
    const next = {id: 'resident_other', key: 'resident:resident_other'};
    f.state.chatTarget = next;
    f.ctx.loadDetail = async () => {};
    f.ctx.loadFolder = async () => {};
    if (fail) f.removal.reject(Error('permission denied')); else f.removal.resolve({resident_id: f.target.id});
    const result = await pending;
    assert.equal(Boolean(result), !fail);
    assert.equal(f.state.chatTarget, next);
    assert.equal(f.primary.contract !== undefined, !fail);
  }
});

test('explicit parent fallback works and a post-delete refresh failure never rebinds the deleted Resident', async () => {
  for (const refreshFails of [true, false]) {
    const f = deletionFixture();
    const parent = {id: 'resident_parent', key: 'resident:resident_parent'};
    f.ctx.loadDetail = async () => {};
    f.ctx.loadFolder = async () => {};
    if (refreshFails) f.ctx.refreshResidentSidebarIfDue = async () => {throw Error('sidebar unavailable');};
    const pending = f.ctx.deleteResidentFromWorkbench({residentId: f.target.id, fallbackTarget: parent});
    f.removal.resolve({resident_id: f.target.id});
    assert.ok(await pending);
    assert.equal(f.state.chatTarget, parent);
    if (refreshFails) assert.match(f.notices[0][0], /刷新删除结果/);
  }
});

function previewFixture({restoreDocument} = {}) {
  const emptySnapshots = [], rendered = [], saved = [];
  const ctx = vm.createContext({window: {PortalI18n: i18n, PortalPreview: {dispose: noop}}, console,
    options: {emit: event => {
      assert.equal(event, 'persistence-change');
      saved.push({binding: {...ctx.binding}, tabs: JSON.parse(JSON.stringify(tabs.persistenceSnapshot()))});
    }}, destroyed: false, persistenceScheduled: false,
    tabControllers: new Set(), restoredTabSnapshots: [], ownDocumentSession: x => x,
    resolveRoot: () => null, previewRoot: {textContent: ''}, requestRevision: 0, binding: {},
    documentValue: null, navigationHistory: [], pendingBindingRestore: {path: 'old'}, previewRender: null,
    surface: {setDocumentStatistics: noop, setDownload: noop, setPath: noop, setState: noop},
    updateDocumentHeader: noop, updateBackAction: noop, hasPreviewTarget: x => Boolean(x?.path),
    focusInitialPreviewLine: noop, rememberPreviousResource: noop,
    renderDocument: async doc => {ctx.documentValue = doc; rendered.push(doc); return doc;},
  });
  // The part scripts register their factories on the fixture's window, and the
  // Preview panel's shared state is the context itself, so cases read it as globals.
  const surfaceScripts = portalWorkspaceSurfaceScripts();
  surfaceScripts.slice(0, -1).forEach(file => vm.runInContext(fs.readFileSync(path.join(SURFACE_STATIC, file), 'utf8'), ctx));
  ctx.previewPanelState = ctx;
  vm.runInContext(declarations(surfaceScripts, ['surfacePart', 'renderDocumentTabs', 'createDocumentSession', 'createDocumentTabsController', 'createOwnedTabsController', 'clearPreview', 'openResource', 'notifyPersistence']), ctx);
  let dirty = false;
  const errors = [];
  const tabs = ctx.createOwnedTabsController({
    createSession: document => {
      const session = ctx.createDocumentSession({onPresent: payload => {
        if (tabs.isActive(document.key)) void ctx.openResource({path: document.path, document: payload});
      }});
      return {...session, guardDiscard: async () => !dirty,
        open: request => session.open({...request, load: request.load || (async () => request.document)})};
    },
    onActivate: (document, snapshot) => {if (snapshot.payload) void ctx.openResource({path: document.path, document: snapshot.payload});},
    onError: error => errors.push(error.message),
    serializeDocument: doc => ({path: doc.path}),
    restoreDocument,
    onChange: snapshot => {if (!snapshot.documents.length) emptySnapshots.push({...ctx.binding});},
  });
  return {ctx, tabs, rendered, emptySnapshots, errors, saved, setDirty: value => {dirty = value;}};
}

test('Preview commits, tab choices and closes save locators without a layout edit', async () => {
  const f = previewFixture();
  const settle = () => new Promise(resolve => setImmediate(resolve));
  await f.tabs.open({key: 'A', path: '/A', document: {path: '/A', content: 'private body'}});
  await settle();
  assert.equal(f.saved.at(-1).binding.path, '/A');
  f.tabs.togglePin();
  await settle();
  assert.equal(f.saved.at(-1).tabs.documents[0].pinned, true);
  await f.tabs.open({key: 'B', path: '/B', document: {path: '/B'}, mode: 'new-tab'});
  f.tabs.activate('A');
  await settle();
  assert.equal(f.saved.at(-1).tabs.activeKey, 'A');
  assert.equal(f.saved.at(-1).binding.path, '/A');
  const saved = f.saved.length;
  f.setDirty(true);
  assert.equal(await f.tabs.close('A'), false);
  await settle();
  assert.equal(f.saved.length, saved, 'cancelled close does not save a new locator');
  f.setDirty(false);
  await f.tabs.close('A');
  await f.tabs.close('B');
  await settle();
  assert.equal(f.saved.at(-1).tabs.documents.length, 0);
  assert.deepEqual(f.saved.at(-1).binding, {});
  assert.equal(JSON.stringify(f.saved.map(value => value.tabs)).includes('private body'), false);
});

test('Preview restoration never saves a partially restored tab collection', async () => {
  const read = deferred();
  const f = previewFixture({restoreDocument: value => ({
    key: value.path, path: value.path,
    load: () => value.path === '/B' ? read.promise : Promise.resolve({path: value.path}),
  })});
  const pending = f.tabs.restorePersistence({schemaVersion: 1, activeKey: '/A', documents: [
    {key: '/A', pinned: true, value: {path: '/A'}},
    {key: '/B', pinned: true, value: {path: '/B'}},
  ]});
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(f.tabs.isRestoring(), true);
  assert.equal(f.ctx.binding.path, '/A');
  assert.equal(f.saved.length, 0, 'restored first file must not replace the full saved set');
  read.resolve({path: '/B'});
  await pending;
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(f.tabs.isRestoring(), false);
  assert.equal(f.saved.at(-1).binding.path, '/A');
  assert.deepEqual(f.saved.at(-1).tabs.documents.map(d => d.value.path), ['/A', '/B']);
});

test('last Preview tab clears its persistent binding before observers run; cancelled closes keep it', async () => {
  const f = previewFixture();
  await f.tabs.open({key: 'A', path: '/A', document: {path: '/A'}});
  await f.tabs.open({key: 'B', path: '/B', document: {path: '/B'}, mode: 'new-tab'});
  await f.tabs.close('A');
  assert.equal(f.ctx.binding.path, '/B');
  f.setDirty(true);
  assert.equal(await f.tabs.close('B'), false);
  assert.equal(f.ctx.binding.path, '/B');
  f.setDirty(false);
  await f.tabs.close('B');
  assert.equal(Object.keys(f.ctx.binding).length, 0);
  assert.equal(f.ctx.documentValue, null);
  assert.equal(f.ctx.pendingBindingRestore, null);
  assert.equal(f.tabs.persistenceSnapshot().documents.length, 0);
  assert.deepEqual(f.emptySnapshots, [{}]);
});

test('a late Preview read cannot revive a closed document, and a new open still works', async () => {
  const f = previewFixture(), read = deferred();
  const pending = f.tabs.open({key: 'A', path: '/A', load: () => read.promise});
  await f.tabs.close('A');
  read.resolve({path: '/A'});
  await pending;
  assert.equal(f.rendered.length, 0);
  assert.equal(Object.keys(f.ctx.binding).length, 0);
  await f.tabs.open({key: 'B', path: '/B', document: {path: '/B'}});
  assert.equal(f.ctx.binding.path, '/B');
  assert.equal(f.rendered.length, 1);
});

test('unreadable replacement keeps the prior session, document and persisted locator', async () => {
  const f = previewFixture();
  await f.tabs.open({key: 'B', path: '/B', document: {path: '/B', content: 'kept'}});
  const previous = f.tabs.active();
  const pendingRead = deferred();
  const pending = f.tabs.open({key: 'A', path: '/A', load: () => pendingRead.promise,
    onLoading: () => {throw Error('pending request must not replace visible document');},
    onError: () => {throw Error('failed request must not replace visible document');}});
  await Promise.resolve();
  assert.equal(f.tabs.active(), previous);
  assert.equal(f.tabs.persistenceSnapshot().documents[0].value.path, '/B');
  pendingRead.reject(Error('404 unreadable'));
  const result = await pending;
  assert.match(result.error.message, /404/);
  assert.equal(f.tabs.active(), previous);
  assert.equal(f.ctx.binding.path, '/B');
  assert.equal(f.ctx.documentValue.content, 'kept');
  assert.equal(f.tabs.snapshot().documents.length, 1);
  assert.deepEqual(f.errors, ['404 unreadable']);
});

test('replacement cancellation and racing reads never discard current edits or restore stale tabs', async () => {
  const f = previewFixture();
  await f.tabs.open({key: 'B', path: '/B', document: {path: '/B', content: 'kept'}});
  f.setDirty(true);
  const cancelled = await f.tabs.open({key: 'A', path: '/A', load: () => {throw Error('cancelled read');}});
  assert.equal(cancelled.accepted, false);
  assert.equal(f.ctx.binding.path, '/B');
  f.setDirty(false);
  const read = deferred();
  const pending = f.tabs.open({key: 'A', path: '/A', load: () => read.promise});
  await Promise.resolve();
  await f.tabs.open({key: 'C', path: '/C', document: {path: '/C'}});
  read.resolve({path: '/A'});
  assert.equal((await pending).stale, true);
  assert.equal(f.tabs.active().key, 'C');
  assert.equal(f.ctx.binding.path, '/C');
  assert.deepEqual(Array.from(f.tabs.snapshot().tabs), ['C']);
});
