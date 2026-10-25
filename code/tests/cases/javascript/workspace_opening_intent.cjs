const {test} = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');

const {findWorkbenchFunction} = require('./lineage_workbench_modules.cjs');
const owner = name => findWorkbenchFunction(name).source;
const deferred = () => {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return {promise, resolve, reject};
};
const tick = () => new Promise(resolve => setImmediate(resolve));
function page({identity, resolveResource, readRoot} = {}) {
  const opened = [], urls = [], errors = [], focus = [], reads = [];
  const state = {loadAllSeq: 0, sideViewInteractionSeq: 0, chatTarget: null, residents: [], sideView: 'agents'};
  const nodes = new Map();
  const context = {
    URLSearchParams, performance: {getEntriesByType: () => []},
    state, usernameLabel: null, agentChatPanel: null, lineageActivityRail: null,
    PERSONAL_AMA_UI_NAMESPACE: 'personal-ama',
    document: {querySelector: () => null, querySelectorAll: () => []},
    unavailableResidentBindings: new Set(),
    openingNavigation: {workspaceId: 'workspace_sample', residentId: '', lineageId: '', messageId: '', chatroomId: ''},
    pendingWorkspaceId: 'workspace_sample',
    $id: id => { if (!nodes.has(id)) nodes.set(id, {}); return nodes.get(id); },
    consoleStateHtml: message => message,
    window: {
      location: {search: '?workspace=workspace_sample'},
      PortalIdentity: {load: identity || (async () => ({username: 'viewer'})), listUIAssociations: async () => ({items: []})},
      PortalLineageCatalogs: {projectCatalog: async () => ({}), users: async () => [], agentBackends: async () => ({}), populateBackends() {}},
      PortalI18n: {t: text => text, text: (parts, ...values) => parts.reduce((s, p, i) => s + p + (values[i] || ''), '')},
      PortalUI: {toast: message => errors.push(message)},
      PortalNavigation: {normalizeLineageView: value => value},
    },
    apiGet: async url => {
      if (!url.startsWith('/api/resources/resolve')) return {};
      reads.push(url);
      return resolveResource ? resolveResource() : {exists: true, resource_type: 'workspace', record: {title: 'Sample', attributes: {path: '/sample'}}};
    },
    fetchResidentSidebarSnapshot: async () => ({}),
    waitForResidentSidebarProjection: async () => true,
    restoreLatestWhichResident: async () => {},
    readLastSelection: () => null,
    residentChatTargetForLineage: () => null,
    initialResidentIdFromUrl: () => '', initialLineageIdFromUrl: () => '', initialChatroomIdFromUrl: () => '',
    residentSidebarRefreshCoordinator: {start() {}},
    societyServiceRecovery: {isUnavailable: () => false},
    lineageWorkbench: {getState: () => ({hidden: []}), focusPanel: id => focus.push(id), isPanelExpanded: () => true},
    currentLineageWorkbench: () => context.lineageWorkbench,
    showPersonalWorkspaces: () => ({openResource: async resource => { opened.push(resource); await readRoot?.(); }}),
    history: {state: {}, replaceState: (_state, _title, url) => urls.push(url)},
  };
  for (const name of ['observeLineageApiFailure', 'refreshPersonalAssistantShortcut', 'ensureLineageWorkbench',
    'applyLineageUiState', 'applyOpeningHatgNavigation', 'renderAgentList', 'renderActivityRecentRail',
    'applyResidentSidebarShortcuts', 'applyPersonalAmaAssociation', 'syncResidentBackendRuntimeFields',
    'updateProjectOptions', 'syncLineageHatgPanel', 'renderFavoriteList', 'renderChatroomList',
    'syncSelectedResidentSurface', 'writeLineageUiState', 'loadChatrooms']) context[name] = () => {};
  vm.createContext(context);
  vm.runInContext(['openWorkspaceResource', 'setSideView', 'loadAll'].map(owner).join('\n'), context);
  return {context, state, opened, urls, errors, focus, reads,
    load: () => context.loadAll(),
    navigate: view => context.setSideView(view, {userInitiated: true})};
}

test('Workspace opening survives a superseded initial identity/catalog load', async () => {
  const first = deferred(); let calls = 0;
  const p = page({identity: () => ++calls === 1 ? first.promise : Promise.resolve({username: 'viewer'})});
  const obsolete = p.load();
  await p.load();
  first.resolve({username: 'viewer'}); await obsolete;
  assert.deepEqual(p.errors, []);
  assert.equal(p.opened.length, 1);
  assert.equal(p.opened[0].workspaceId, 'workspace_sample');
  assert.equal(p.state.sideView, 'workspaces');
  assert.deepEqual(p.urls, ['/lineage?workspace=workspace_sample']);
  await p.load();
  assert.equal(p.opened.length, 1, 'ordinary refresh must not reopen an already consumed URL');
});

test('a delayed Workspace resolution cannot override a later explicit sidebar choice', async () => {
  const response = deferred(); const p = page({resolveResource: () => response.promise});
  const loading = p.load(); await tick();
  assert.equal(p.reads.length, 1);
  p.navigate('chatrooms');
  response.resolve({exists: true, resource_type: 'workspace', record: {title: 'Sample', attributes: {path: '/sample'}}});
  await loading;
  assert.deepEqual(p.errors, []);
  assert.equal(p.opened.length, 0);
  assert.equal(p.state.sideView, 'chatrooms');
  assert.deepEqual(p.urls, []);
});

test('failed Workspace resolution keeps the intent for an explicit refresh and reports the error', async () => {
  let attempt = 0;
  const p = page({resolveResource: async () => {
    if (++attempt === 1) throw Error('temporary read failure');
    return {exists: true, resource_type: 'workspace', record: {title: 'Sample', attributes: {path: '/sample'}}};
  }});
  await p.load();
  assert.equal(p.errors.length, 1); assert.match(p.errors[0], /temporary read failure/);
  await p.load();
  assert.equal(p.opened.length, 1);
  assert.equal(p.reads.length, 2);
});

test('Workspace URL completion waits for the directory operation and does not steal later navigation', async () => {
  const root = deferred(); const p = page({readRoot: () => root.promise});
  const loading = p.load(); await tick();
  assert.equal(p.opened.length, 1); assert.deepEqual(p.urls, []);
  p.navigate('agents'); root.resolve(); await loading;
  assert.equal(p.state.sideView, 'agents');
  assert.deepEqual(p.urls, []);
});

test('an obsolete resource response cannot overwrite the root selected by the newer load', async () => {
  const first = deferred(); let requests = 0;
  const p = page({resolveResource: () => ++requests === 1 ? first.promise : Promise.resolve({
    exists: true, resource_type: 'workspace', record: {title: 'Current', attributes: {path: '/current'}},
  })});
  const obsolete = p.load(); await tick();
  await p.load();
  first.resolve({exists: true, resource_type: 'workspace', record: {title: 'Old', attributes: {path: '/old'}}});
  await obsolete;
  assert.deepEqual(p.errors, []);
  assert.equal(p.opened.length, 1);
  assert.equal(p.opened[0].path, '/current');
  assert.equal(p.urls.length, 1);
});

test('an unavailable resource reports failure without fabricating a directory', async () => {
  const p = page({resolveResource: async () => ({exists: false, resource_type: 'workspace'})});
  await p.load();
  assert.equal(p.opened.length, 0);
  assert.equal(p.errors.length, 1);
  assert.match(p.errors[0], /Workspace 已删除或不可用/);
  assert.equal(p.urls.length, 0);
});
