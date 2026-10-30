'use strict';
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const parser = require('@babel/parser');
const traverse = require('@babel/traverse').default;
const {createHarness: harness, plain} = require('./workbench_visibility_state.cjs');
const {chatWorkspaceSources, findChatWorkspaceNode} = require('./chat_workspace_modules.cjs');
const {portalWorkbenchScripts} = require('./portal_workbench_scripts.cjs');
const staticRoot = path.resolve(__dirname, '../../../management_portal/static');
const sourceRoot = process.env.PORTAL_STATE_TEST_SOURCE || staticRoot;
const source = name => fs.readFileSync(path.join(sourceRoot, name), 'utf8');
const workbenchScripts = new Set(portalWorkbenchScripts(staticRoot));
const createHarness = () => harness({sourceTransform: (name, text) =>
  workbenchScripts.has(name) ? source(name) : text});
const stateSource = source('portal_state.js');
const lineageSource = source('lineage_workbench.js');
function expression(text, predicate) {
  let selected;
  traverse(parser.parse(text), {enter(p) { if (predicate(p.node)) { assert.equal(selected, undefined); selected = p.node; } }});
  assert.ok(selected, 'production node found');
  return text.slice(selected.start, selected.end);
}
const noop = () => {};
function install(h, namespace, data = {}) {
  h.window.__PORTAL_SITE__ = {storage_namespace: namespace};
  for (const [key, value] of Object.entries(data)) h.window.localStorage.setItem(key, value);
  h.window.eval(stateSource);
  h.window.PortalIdentity = {username: () => 'reader'};
}
function contents(h) { return Object.fromEntries(Object.entries(h.window.localStorage)); }
function register(h, reads, emits) {
  h.window.PortalWorkbench.registerPanelPackage({
    type: 'binding-fixture', version: 1, presentation: {title: 'Binding', icon: 'chat'},
    sizing: {minWidth: 180, minHeight: 140}, instances: 'multiple',
    capabilities: ['snapshot', 'restore'], behavior: {canHide: true, canCollapse: true, canMove: true, closable: true},
    persistence: {schemaVersion: 1}, context: {channels: []},
    create({panelId, emit}) {
      let value = {};
      emits[panelId] = emit;
      return {snapshot: () => value, restore: next => {value = next; reads.push([panelId, plain(next)]);}};
    },
  });
}
function mount(h, extra = {}) {
  const descriptor = id => ({id, type: 'binding-fixture', restoreOnReload: true, closable: true});
  return h.create({id: 'lineage', initialEditors: [descriptor('chat')],
    defaultLayout: h.window.PortalWorkbench.model.stack(['chat']),
    restorePanel: spec => descriptor(spec.id), ...extra});
}

test('restored editor keeps its locator when the package snapshot stores target separately', async () => {
  const h = createHarness(); const reload = createHarness();
  try {
    install(h, 'binding-roundtrip'); register(h, [], {});
    const binding = {kind: 'chat', resident_id: 'resident_dynamic', lineage_id: 'lineage_saved'};
    const key = h.window.PortalWorkbench.storage.userStorageKey('workbench', 'lineage', 'reader');
    h.window.PortalState.storage.writeJson(key, {
      schemaVersion: 2, panelIds: ['chat', 'dynamic'],
      openEditors: [{id: 'dynamic', type: 'binding-fixture', binding}],
      panelStates: {dynamic: {packageType: 'binding-fixture', packageVersion: 1,
        schemaVersion: 1, value: {target: binding, draft: 'unsent'}}},
      layout: h.window.PortalWorkbench.model.stack(['chat', 'dynamic']), hidden: [], collapsed: [],
    });
    const wb = mount(h);
    wb.hidePanel('chat');
    const saved = h.window.PortalState.storage.readJson(key);
    assert.deepEqual(plain(saved.openEditors.find(spec => spec.id === 'dynamic').binding), binding);
    install(reload, 'binding-roundtrip', contents(h)); const reads = []; register(reload, reads, {});
    const next = mount(reload);
    next.showPanel('chat');
    assert.deepEqual(plain(reload.window.PortalState.storage.readJson(key)
      .openEditors.find(spec => spec.id === 'dynamic').binding), binding);
    assert.equal(reads.find(([id]) => id === 'dynamic')[1].draft, 'unsent');
  } finally { await h.cleanup(); await reload.cleanup(); }
});

test('origin reuse isolates installation state, preserves legacy bytes and same-install drafts/layout', async () => {
  const old = createHarness(); const fresh = createHarness(); const reload = createHarness();
  try {
    const rawKey = 'portal.workbench.lineage.user.reader.v1';
    const legacy = {[rawKey]: JSON.stringify({openEditors: [{id: 'stale'}]}), 'unsent-legacy': 'do not erase'};
    install(old, 'install-one', legacy);
    const reads = [], emits = {}; register(old, reads, emits);
    const wb = mount(old, {initialPanelStates: {chat: {binding: {resident_id: 'resident_same'}}}});
    old.window.PortalState.storage.writeText('chat.draft.reader', '未发送草稿');
    old.window.PortalState.write('lineage', 'reader', {selection: 'same-install'});
    wb.hidePanel('chat');
    const saved = contents(old);
    assert.equal(saved[rawKey], legacy[rawKey]);
    assert.equal(saved['unsent-legacy'], 'do not erase');
    install(fresh, 'install-two', saved); register(fresh, [], {});
    const other = mount(fresh);
    assert.equal(fresh.window.PortalState.storage.readText('chat.draft.reader'), '');
    assert.equal(fresh.window.PortalState.read('lineage', 'reader', {}).selection, undefined);
    assert.equal(other.getState().hidden.includes('chat'), false);
    assert.equal(fresh.window.localStorage.getItem('unsent-legacy'), 'do not erase');
    install(reload, 'install-one', saved); const restored = []; register(reload, restored, {});
    const same = mount(reload);
    assert.equal(reload.window.PortalState.storage.readText('chat.draft.reader'), '未发送草稿');
    assert.equal(reload.window.PortalState.read('lineage', 'reader', {}).selection, 'same-install');
    assert.equal(same.getState().hidden.includes('chat'), true);
    assert.ok(restored.some(([id, value]) => id === 'chat' && value.binding.resident_id === 'resident_same'));
    assert.equal(reload.window.localStorage.getItem(rawKey), legacy[rawKey]);
  } finally { await old.cleanup(); await fresh.cleanup(); await reload.cleanup(); }
});

test('explicit initial binding precedes saved snapshot; confirmed missing binding alone stops restoration', async () => {
  const h = createHarness(); const reload = createHarness();
  try {
    install(h, 'same'); const reads = [], emits = {}; register(h, reads, emits);
    const envelope = value => ({packageType: 'binding-fixture', packageVersion: 1, schemaVersion: 1, value});
    const key = h.window.PortalWorkbench.storage.userStorageKey('workbench', 'lineage', 'reader');
    h.window.PortalState.storage.writeJson(key, {
      schemaVersion: 2, panelIds: ['chat', 'missing', 'valid'],
      openEditors: ['missing','valid'].map(id => ({id, type: 'binding-fixture'})),
      panelStates: Object.fromEntries(['chat', 'missing', 'valid'].map(id => [id, envelope({binding: {resident_id: 'old-'+id}})])),
      layout: h.window.PortalWorkbench.model.stack(['chat', 'missing', 'valid']), hidden: [], collapsed: [],
    });
    const wb = mount(h, {initialPanelStates: {chat: {binding: {resident_id: 'current-url'}}}});
    assert.equal(reads.find(([id]) => id === 'chat')[1].binding.resident_id, 'current-url');
    assert.equal(reads.some(([, value]) => value.binding.resident_id === 'old-chat'), false);
    h.window.PortalState.storage.writeText('draft.missing', 'keep text');
    emits.missing('binding-unavailable');
    let saved = h.window.PortalState.storage.readJson(key);
    assert.equal(saved.openEditors.some(spec => spec.id === 'missing'), false);
    assert.equal(saved.panelStates.missing, undefined);
    assert.ok(saved.openEditors.some(spec => spec.id === 'valid'));
    assert.ok(wb.getPanel('missing'), 'current error panel stays visible');
    install(reload, 'same', contents(h)); const laterReads = []; register(reload, laterReads, {});
    const later = mount(reload);
    assert.equal(later.getPanel('missing'), null);
    assert.ok(later.getPanel('valid'));
    assert.equal(reload.window.PortalState.storage.readText('draft.missing'), 'keep text');
    emits.missing('binding-ready');
    saved = h.window.PortalState.storage.readJson(key);
    assert.ok(saved.openEditors.some(spec => spec.id === 'missing'), 'later valid binding may persist again');
  } finally { await h.cleanup(); await reload.cleanup(); }
});

test('Society opening URL overrides restored constructor binding as well as state; ordinary siblings remain', () => {
  const openingNavigation = {residentId: 'current-resident', lineageId: 'current-lineage', programItemId: ''};
  const bindings = expression(lineageSource, n => n.type === 'VariableDeclarator' && n.id.name === 'openingBindings');
  const restore = expression(lineageSource, n => n.type === 'ObjectProperty' && n.key.name === 'restorePanel');
  const states = expression(lineageSource, n => n.type === 'ObjectProperty' && n.key.name === 'initialPanelStates');
  const ctx = vm.createContext({openingNavigation, lineageEditorDescriptor: v => v});
  const config = vm.runInContext(`const ${bindings}; ({${restore}, ${states}})`, ctx);
  assert.equal(config.restorePanel({id:'chat', type:'chat', binding:{resident_id:'old'}}).resource.resident_id, 'current-resident');
  assert.equal(config.restorePanel({id:'hatg', type:'hatg', binding:{lineageId:'old'}}).resource.lineageId, 'current-lineage');
  assert.equal(config.initialPanelStates.chat.target.resident_id, 'current-resident');
  assert.equal(config.restorePanel({id:'sibling', type:'chat', binding:{resident_id:'valid'}}).resource.resident_id, 'valid');
});

test('Resident owner quarantines confirmed deletion and 404, not permission or temporary failures', () => {
  const method = findChatWorkspaceNode(n => n.type === 'ClassMethod' && n.key.name === 'rejectUnavailableTarget',
    'rejectUnavailableTarget method', chatWorkspaceSources(sourceRoot)).source;
  const ctx = vm.createContext({window: {PortalI18n: {t: s => s}}, residentSessionScope: () => 'scope'});
  const reject = vm.runInContext(`({${method}}).rejectUnavailableTarget`, ctx);
  for (const error of [{code:'resident.deleted'}, ...[404, 403, 429, 500, 0].map(status => ({status}))]) {
    const events = [];
    const panel = {options: {emit: e => events.push(e)}, questions: {reset: noop},
      workspace: {target: () => ({resident_id:'fixture'})}, targetRevision: 0, showError: noop};
    reject.call(panel, error);
    assert.deepEqual(events, error.code === 'resident.deleted' || error.status === 404 ? ['binding-unavailable'] : []);
  }
});

test('HATG quarantines only current GetLineage 404, not other reads or stale requests', async () => {
  const hatgSource = source('hatg_panel_lineage_loading.js');
  const fn = expression(hatgSource, n => n.type === 'FunctionDeclaration' && n.id.name === 'selectLineage');
  for (const [status, fromPreference, stale] of [[404,false,false],[403,false,false],[500,false,false],[404,true,false],[404,false,true]]) {
    const events = []; const state = {detailSeq:0, graphViewRequestSeq:0, workspaceGraphLoadSeq:0};
    const ctx = vm.createContext({state, $id: () => null, panelHost: {hatgComponentOptions: {emit:e => events.push(e)}},
      societyService: {loadLineage: async () => {if(stale) state.detailSeq++; if(!fromPreference) throw {status}; return {}; }},
      loadHatgViewPreference: async () => {if(fromPreference) throw {status}; return {};},
      setWorkspaceViewLoading:noop, activateLineageEdits:noop, setSyncStatus:noop, renderNoLineage:noop, toast:noop,
      window: {PortalI18n: {text: (ss,...vs) => ss.reduce((a,s,i) => a+s+(vs[i]||''),'')}}});
    vm.runInContext(fn, ctx); await ctx.selectLineage('fixture-lineage');
    assert.deepEqual(events, status===404 && !fromPreference && !stale ? ['binding-unavailable'] : []);
  }
});

test('404 quarantine also removes page last-selection fallback without erasing drafts or later healthy selection', () => {
  const names = ['onLineagePanelBinding', 'writeLastSelection'];
  const functions = names.map(name => expression(lineageSource, n => n.type === 'FunctionDeclaration' && n.id.name === name)).join('\n');
  const stale = {kind:'resident', id:'missing', key:'resident:missing'};
  let saved = {lastChatTarget: stale, draft:'never erase'};
  const location = new URL('https://portal.test/lineage?resident=missing&lineage=kept&open_chat=1');
  const ctx = vm.createContext({URL, window:{location},
    history:{state:{},replaceState:(_state,_title,url) => {location.href=new URL(url,location).href;}},
    unavailableResidentBindings:new Set(), state:{chatTarget:stale},
    isResidentChatSelection: s => s?.kind === 'resident',
    readLineageUiState: () => saved, writeLineageUiState: patch => {saved = {...saved, ...patch};}});
  vm.runInContext(functions, ctx);
  const wb = {getPanel: () => ({target: () => ({resident_id:'missing'})})};
  ctx.onLineagePanelBinding({event:'binding-unavailable', sourcePanelId:'chat'}, wb);
  ctx.writeLastSelection();
  assert.equal(saved.lastChatTarget, null);
  assert.equal(saved.draft, 'never erase');
  assert.equal(location.searchParams.get('resident'), null);
  assert.equal(location.searchParams.get('open_chat'), null);
  assert.equal(location.searchParams.get('lineage'), 'kept');
  ctx.onLineagePanelBinding({event:'binding-ready', sourcePanelId:'chat'}, wb);
  ctx.writeLastSelection();
  assert.equal(saved.lastChatTarget.id, 'missing');
});
