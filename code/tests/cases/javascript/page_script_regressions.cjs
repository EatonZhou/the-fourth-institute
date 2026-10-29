const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {readHatgModule, createHatgModule} = require('./hatg_modules.cjs');

// Execute production owners with controlled inputs. Page journeys cover their DOM;
// these cases isolate request ordering and language-dependent presentation.
// The production Family part of the HATG panel, with the replaced dependencies.
function familyBlocks(window, replaced) {
  vm.runInContext(readHatgModule('hatg_family_blocks.js'), vm.createContext({window, console}));
  return createHatgModule(window.PortalHatgFamilyBlocks, 'createFamilyBlocks', replaced);
}

// A page's own scripts in load order: those whose name starts with `prefix`.
function pageScripts(page, prefix) {
  const html = fs.readFileSync(path.resolve(__dirname, '../../../management_portal', page), 'utf8');
  return [...html.matchAll(/<script src="\/static\/([a-z_]+\.js)"/g)].map(match => match[1])
    .filter(file => file.startsWith(prefix));
}
const ARTICLE_SCRIPTS = pageScripts('article_workspace.html', 'article_workspace');
const REPO_SCRIPTS = pageScripts('repo_workspace.html', 'repo_workspace');

// `file` names one static script or a list of them; each function comes from
// the script that declares it.
function owner(file, names, dependencies) {
  const sources = [].concat(file).map(name => fs.readFileSync(path.resolve(__dirname, '../../../management_portal/static', name), 'utf8'));
  const functions = names.map(name => {
    const pattern = new RegExp(`^( +)(?:async )?function ${name}\\(`, 'm');
    const source = sources.find(text => pattern.test(text)) || '';
    const match = pattern.exec(source);
    if (!match) throw new Error(`Missing request owner ${name}`);
    const end = source.indexOf(`\n${match[1]}}\n`, match.index);
    return source.slice(match.index, end + match[1].length + 3);
  });
  const context = vm.createContext({URLSearchParams, console, ...dependencies});
  vm.runInContext(functions.join('\n'), context);
  return context;
}

test('Workbench navigation follows the readable catalog, including built-in feature links', async () => {
  const ITEMS = [{id: 'resource', href: '/resource/knowledge'},
    {id: 'development', href: '/development', builtin: true}, {id: 'wall', href: '/wall'}];
  let workbenches = [];
  const nav = owner('portal_nav.js', ['hydrateWorkbenches', 'orderedItems', 'normalizedItemOrder'], {
    ITEMS, currentItemOrder: ['wall', 'development', 'resource'], secondaryNavs: new Map(),
    document: {querySelectorAll: () => []}, render: () => {},
    window: {PortalI18n: {t: value => value}, PortalAPI: {get: async () => ({workbenches,
      builtins: {development: {path: '/development'}, wall: {path: '/wall'}}})}},
  });
  await nav.hydrateWorkbenches();
  assert.deepEqual(Array.from(nav.orderedItems(), item => item.id), ['resource']);
  workbenches = [{workbench_id: 'workbench_111111111111', builtin: 'wall', published_revision: 1}];
  await nav.hydrateWorkbenches();
  assert.deepEqual(Array.from(nav.orderedItems(), item => item.id), ['wall', 'resource']);
  workbenches = [];
  await nav.hydrateWorkbenches();
  assert.deepEqual(Array.from(nav.orderedItems(), item => item.id), ['resource']);
});
const noop = () => {};
function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}
const i18n = {t: value => value, text: (parts, ...values) => parts.reduce((text, part, i) => text + part + (values[i] ?? ''), '')};

async function familySettingsFixture({runtime = true, restart = true} = {}) {
  const member = 'resident_member', facade = 'resident_facade';
  const calls = [], settingsDialogs = [], sessionDialogs = [], notices = [], listeners = {};
  const root = {hidden: true, isConnected: true, innerHTML: '', remove: noop, setAttribute: noop,
    closest: () => null, addEventListener: (name, handler) => {listeners[name] = handler;},
    querySelector: selector => selector === '.familyStatusBody' ? {scrollTop: 0} : null};
  const snapshot = {state: {work_state: 'idle', intake_state: 'open'},
    runtime_settings: {model: 'model_one', reasoning_effort: 'high', service_tier: ''}, messages: []};
  const detail = {resident: {backend: 'codex'}, allows: {RestartSession: restart},
    runtime_settings: {...snapshot.runtime_settings}, execution_account: {account_id: 'member_account'},
    chat_workspace: {actions: runtime ? {runtime_settings: {
      instruction: 'UpdateResidentSettings', arguments: {resident_id: member},
    }} : {}}};
  const catalog = {models: ['model_one'], service_tiers_by_model: {model_one: ['priority']},
    runtime_capabilities: {settings: ['reasoning_effort', 'service_tier']}};
  const window = {PortalI18n: {...i18n, html: i18n.text, markup: value => value},
    setInterval: () => 1, clearInterval: noop, addEventListener: noop,
    PortalUIPrimitives: {randomUUID: () => "fixture"},
    PortalUI: {toast: (...args) => notices.push(args)},
    PortalAPI: {get: async url => {calls.push(['get', url]); return url.includes('agent-runtime-catalog') ? catalog : {accounts: []};}},
    PortalResidentProtocol: {executionGateView: () => ({state: '', text: '', label: ''}), stateView: () => ({status: 'idle', display_work_state: 'idle'}),
      acquire: () => ({destroy: noop, load: async () => snapshot})},
    PortalResidentService: {
      projection: async ({residentId}) => residentId === facade
        ? {family_facade: {title: 'Team', nodes: [{resident_id: member, title: 'Worker'}]}} : detail,
      observeSessionRestart: () => noop,
      sessionRestartOptions: async id => {calls.push(['restartOptions', id]); return {accounts: {}};},
      restartSession: async values => {calls.push(['restart', values]); return {status: 'accepted'};},
    },
  };
  Object.assign(window.PortalResidentService, {
    runtimeSettings: async id => {calls.push(['GetResidentSettings', {resident_id: id}]); return {settings: snapshot.runtime_settings};},
    runtimeCatalog: async ({residentId}) => {calls.push(['get', `/api/agent-workbench/agent-runtime-catalog?resident_id=${residentId}`]); return catalog;},
    updateRuntimeSettings: async (id, changes) => {
      calls.push(['UpdateResidentSettings', {resident_id: id, changes}]);
      snapshot.runtime_settings = {...snapshot.runtime_settings, ...changes};
      detail.runtime_settings = snapshot.runtime_settings;
      return {settings: snapshot.runtime_settings};
    },
  });
  // Exercise the production workflow owner, with only transports and dialog rendering isolated.
  vm.runInNewContext(fs.readFileSync(path.resolve(__dirname,
    '../../../management_portal/static/portal_resident_runtime_controller.js'), 'utf8'), {window});
  window.PortalResidentRuntimeController = window.PortalResidentRuntimeController.create({
    service: window.PortalResidentService,
    dialogs: {
      settings: options => ({open: (...args) => settingsDialogs.push(args), close: options.onClose, destroy: noop}),
      restart: options => ({open: (...args) => sessionDialogs.push(args), close: options.onClose, destroy: noop}),
    },
  });
  vm.runInNewContext(fs.readFileSync(path.resolve(__dirname,
    '../../../management_portal/static/chat_workspace_family_status.js'), 'utf8'), {
    window, URLSearchParams, document: {createElement: () => root, activeElement: null}, CSS: {escape: value => value},
  });
  const familyStatus = window.PortalChatWorkspaceFamilyStatus.createFamilyStatus({
    escapeHtml: value => String(value ?? ''), footerTime: () => '', sessionRestartActive: () => false,
  });
  const view = familyStatus.createFamilyStatusView(facade, null, null, null, {append: noop});
  view.open();
  await new Promise(resolve => setImmediate(resolve));
  return {root, view, calls, settingsDialogs, sessionDialogs, notices,
    click: async (entry = 'settings') => { listeners.click({target: {closest: selector =>
      selector === `[data-family-status-${entry}]` ? {dataset: entry === 'settings'
        ? {familyStatusSettings: member} : {familyStatusRuntime: member}} : null}}); await new Promise(resolve => setImmediate(resolve)); }};
}

test('Family Backend/account entry edits the member live without requesting a restart', async () => {
  const fixture = await familySettingsFixture();
  await fixture.click();
  assert.deepEqual(fixture.notices, []);
  assert.equal(fixture.settingsDialogs.length, 0, 'members with Session permission use the unified card');
  assert.equal(fixture.sessionDialogs.length, 1, 'the member card actually opened');
  const [config] = fixture.sessionDialogs[0];
  const {runtime_settings: initial, onSave: save} = config;
  assert.equal(config.resident_id, 'resident_member');
  assert.equal(typeof save, 'function', 'the same card exposes live settings');
  assert.equal(initial.reasoning_effort, 'high');
  assert.match(fixture.calls.find(call => call[0] === 'get' && call[1].includes('agent-runtime-catalog'))[1], /resident_id=resident_member/);
  await save({...initial, reasoning_effort: 'low', service_tier: 'priority'});
  const update = fixture.calls.find(call => call[0] === 'UpdateResidentSettings');
  assert.deepEqual(JSON.parse(JSON.stringify(update[1])), {
    resident_id: 'resident_member', changes: {reasoning_effort: 'low', service_tier: 'priority'},
  });
  assert.equal(fixture.calls.some(call => call[0] === 'restart'), false, 'reading Session options never submits a restart');
  assert.equal(fixture.sessionDialogs.length, 1, 'saving stays in the same card');
  assert.match(fixture.root.innerHTML, /low · Fast 开/);
  await fixture.click('settings');
  const [reopened] = fixture.sessionDialogs[1];
  assert.equal(reopened.runtime_settings.service_tier, 'priority');
  const before = fixture.calls.length;
  await reopened.onSave(reopened.runtime_settings);
  assert.equal(fixture.calls.length, before, 'unchanged settings do not write');
  fixture.view.destroy();
});

test('Family live settings remain available without restart permission', async () => {
  const live = await familySettingsFixture({restart: false});
  await live.click();
  assert.deepEqual(live.notices, []);
  assert.equal(live.settingsDialogs.length, 1);
  assert.equal(live.sessionDialogs.length, 0);
  const [initial, save, options] = live.settingsDialogs[0];
  assert.equal(options.residentId, 'resident_member');
  assert.match(options.hint, /不会重启/);
  await save({...initial, reasoning_effort: 'low'});
  assert.deepEqual(JSON.parse(JSON.stringify(live.calls.find(call => call[0] === 'UpdateResidentSettings')[1])), {
    resident_id: 'resident_member', changes: {reasoning_effort: 'low'},
  });
  assert.equal(live.calls.some(call => call[0].startsWith('restart')), false, 'live settings do not request Session options or a restart');
  live.view.destroy();
});

test('Family read-only providers can migrate only after an explicit Session submission', async () => {
  const migration = await familySettingsFixture({runtime: false});
  await migration.click();
  assert.deepEqual(migration.notices, []);
  assert.equal(migration.settingsDialogs.length, 0);
  assert.equal(migration.sessionDialogs.length, 1);
  const [config, submit] = migration.sessionDialogs[0];
  assert.equal(config.resident_id, 'resident_member');
  assert.equal(config.onSave, null, 'read-only providers cannot live-save settings');
  assert.equal(migration.calls.some(call => ['restart', 'UpdateResidentSettings'].includes(call[0])), false);
  const selected = {target_provider: 'claude', target_account: 'target_account', context_strategy: 'visible'};
  assert.equal((await submit(selected)).status, 'accepted');
  const restarts = migration.calls.filter(call => call[0] === 'restart');
  assert.equal(restarts.length, 1);
  assert.deepEqual(JSON.parse(JSON.stringify(restarts[0][1])), {...selected, resident_id: 'resident_member'});
  assert.equal(migration.calls.some(call => call[0] === 'UpdateResidentSettings'), false);
  migration.view.destroy();
});

test('Family canvas actions load the Menu owner before confirming and cancellation never mutates', async () => {
  let confirmed = false;
  const prompts = [], mutations = [], loads = [];
  const window = {PortalI18n: i18n, PortalUI: {confirm: async options => { prompts.push(options); return confirmed; }},
    PortalInstructionClient: {execute: async (...args) => { mutations.push(args); return {done: true}; }}};
  const menu = owner('portal_resident_menu.js', ['removeFamily'], {window});
  const nav = owner('portal_nav.js', ['removeFamily'], {window, loadBackpackAsset: async (path, name) => {
    loads.push([path, name]); window[name] = {removeFamily: menu.removeFamily};
  }});
  const options = {family: {id: 'fbi_one', title: 'One'}, lineageId: 'lineage_one', action: 'unfamilize'};
  window.PortalNav = {removeFamily: nav.removeFamily};
  const graph = familyBlocks(window, {
    state: {lineage: {lineage_id: 'lineage_one'}},
    functionBlockInstanceById: () => options.family,
    applyProgramMutation: request => request(), executeHatgInstruction: window.PortalInstructionClient.execute,
    programFeature: {clearSelection: noop},
  });
  assert.equal(await graph.removeFamilyInstance('fbi_one'), undefined);
  assert.equal(prompts[0].title, 'Unfamilize（保留成员）？');
  assert.match(prompts[0].message, /保留成员、子 Family、业务工作流/);
  assert.equal(mutations.length, 0);
  confirmed = true;
  await graph.removeFamilyInstance('fbi_one');
  assert.equal(mutations[0][0], 'Unfamilize');
  assert.deepEqual(JSON.parse(JSON.stringify(mutations[0][1])), {lineage_id: 'lineage_one', family_id: 'fbi_one'});
  await nav.removeFamily({...options, action: 'delete'});
  assert.equal(prompts[2].title, '删除整个 Family？');
  assert.equal(mutations[1][0], 'DeleteFamily');
  assert.deepEqual(loads[0], ['/static/portal_resident_menu.js', 'PortalResidentMenu']);
});

function unfamilizeViewportFixture() {
  const frames = [], calls = [];
  const controller = {
    refresh: options => calls.push(['measure', options.measureNodes]),
    ensureNodeVisible: id => calls.push(['reveal', id]),
  };
  const state = {lineage: {lineage_id: 'lineage_one'}, graphController: controller,
    graphViewMode: 'context', selectedNodeId: 'removed_facade'};
  const receipt = {member_resident_ids: ['hidden_worker', 'retained_inner_facade']};
  const window = {requestAnimationFrame: callback => frames.push(callback),
    PortalNav: {removeFamily: async options => options.execute('Unfamilize', {})}};
  const blocks = familyBlocks(window, {
    state, nodeResidentId: node => node.resident_id || node.id,
    graphVisibleModel: () => ({nodes: [{id: 'unrelated'}, {id: 'inner_card', resident_id: 'retained_inner_facade'}]}),
    functionBlockInstanceById: () => ({id: 'outer'}),
    programFeature: {clearSelection: noop}, applyProgramMutation: request => request(),
    executeHatgInstruction: async () => receipt,
  });
  vm.runInContext(readHatgModule('hatg_resident_dialogs.js'), vm.createContext({window, console}));
  const menu = createHatgModule(window.PortalHatgResidentDialogs, 'createResidentDialogs', {
    state, revealUnfamilizedMember: blocks.revealUnfamilizedMember,
    societyService: {loadLineage: async () => {calls.push(['load']); return state.lineage;}},
    commitLineage: () => calls.push(['render']),
  });
  const graph = {removeFamilyInstance: blocks.removeFamilyInstance,
    revealUnfamilizedMember: blocks.revealUnfamilizedMember, residentMenuHandlers: menu.residentMenuHandlers};
  return {graph, state, frames, calls, receipt};
}

for (const entry of ['canvas', 'menu']) {
  test(`Family ${entry} ungroup reveals a retained nested facade after rendering without changing Chat`, async () => {
    const f = unfamilizeViewportFixture();
    if (entry === 'canvas') await f.graph.removeFamilyInstance('outer');
    else await f.graph.residentMenuHandlers().onChanged({type: 'unfamilize', lineageId: 'lineage_one',
      residentId: 'removed_facade', result: f.receipt});
    assert.equal(f.calls.some(call => call[0] === 'reveal'), false);
    assert.equal(f.frames.length, 1);
    f.frames.shift()();
    assert.deepEqual(f.calls.slice(-2), [['measure', true], ['reveal', 'inner_card']]);
    assert.equal(f.state.selectedNodeId, 'removed_facade');
    assert.deepEqual(f.receipt.member_resident_ids, ['hidden_worker', 'retained_inner_facade']);
  });
}

test('Family viewport completion cannot move a newer Lineage, controller or workspace view', () => {
  for (const change of [
    state => {state.lineage = {lineage_id: 'lineage_other'};},
    state => {state.graphController = {};},
    state => {state.graphViewMode = 'workspace';},
  ]) {
    const f = unfamilizeViewportFixture();
    f.graph.revealUnfamilizedMember('lineage_one', f.receipt);
    change(f.state);
    f.frames.shift()();
    assert.deepEqual(f.calls, []);
  }
});

test('Family deletion or non-visible retained members never invent a viewport target', async () => {
  const f = unfamilizeViewportFixture();
  await f.graph.residentMenuHandlers().onChanged({type: 'delete-family', lineageId: 'lineage_one',
    residentId: 'removed_facade', result: f.receipt});
  assert.equal(f.frames.length, 0);
  f.graph.revealUnfamilizedMember('lineage_one', {member_resident_ids: []});
  assert.equal(f.frames.length, 0);
  f.graph.revealUnfamilizedMember('lineage_one', {member_resident_ids: ['hidden_worker']});
  f.frames.shift()();
  assert.equal(f.calls.some(call => call[0] === 'reveal'), false);
});

test('Family action load failures show one error without opening a confirmation', async () => {
  const notices = [], failure = new Error('asset unavailable');
  const window = {PortalI18n: i18n, PortalUI: {toast: (...args) => notices.push(args)}};
  const nav = owner('portal_nav.js', ['removeFamily'], {window, loadBackpackAsset: async () => {throw failure;}});
  await assert.rejects(nav.removeFamily({}), error => error === failure);
  assert.deepEqual(notices, [['无法加载 Family 操作：asset unavailable', 'error']]);
});

test('Article compile results and controls belong to the initiating Article', async () => {
  const state = {selectedId: 'A', compilingArticles: new Set(), build: {}, autoCompileQueued: false};
  const pending = new Map(), options = [], views = [];
  const button = {disabled: false, dataset: {}, textContent: 'Compile'};
  const context = owner(ARTICLE_SCRIPTS, ['compileArticle'], {
    state, window: {PortalI18n: i18n}, $: () => button,
    setCompileMenu: noop, hasDirtyDocuments: () => false, renderBuildSummary: noop,
    renderCompileMenu: noop, compileSettingValue: () => 'normal', showToast: noop,
    setOutputView: view => views.push(view), loadArticles: async () => {},
    loadBuild: async () => {}, scheduleAutoCompile: noop,
    request: (url, opts) => { const gate = deferred(); pending.set(url, gate); options.push(opts); return gate.promise; },
  });
  const first = context.compileArticle();
  state.selectedId = 'B'; state.build = {id: 'B'};
  const second = context.compileArticle();
  assert.equal(pending.size, 2);
  assert.equal(options.every(option => option.timeoutMs > 120000), true);
  pending.get('/api/articles/A/compile').resolve({id: 'A', pdf_url: '/A.pdf'});
  await first;
  assert.equal(state.build.id, 'B');
  assert.equal(state.compilingArticles.has('B'), true);
  assert.equal(button.disabled, true);
  assert.deepEqual(views, []);
  pending.get('/api/articles/B/compile').resolve({id: 'B', pdf_url: '/B.pdf'});
  await second;
  assert.equal(state.build.pdf_url, '/B.pdf');
  assert.equal(state.compilingArticles.size, 0);
  assert.equal(button.disabled, false);
});

function articleUnregisterFixture({confirm = true} = {}) {
  const state = {selectedId: 'A', sideView: 'git', gitHistory: [], gitStatus: null};
  const removal = deferred(), reads = [], writes = [], selections = [], catalogLoads = [];
  const timers = new Map();
  let nextTimer = 0;
  const context = owner(ARTICLE_SCRIPTS, ['unregisterCurrentArticle'], {
    state, AbortController, ARTICLE_GIT_REFRESH_MS: 5000,
    PortalUIPrimitives: {randomUUID: () => 'fixture'},
    document: {hidden: false, addEventListener: noop, removeEventListener: noop},
    setTimeout: callback => {const id = ++nextTimer; timers.set(id, callback); return id;},
    clearTimeout: id => timers.delete(id),
    window: {addEventListener: noop, removeEventListener: noop, PortalI18n: i18n,
      PortalUI: {confirm: async () => confirm}, PortalUIPrimitives: {randomUUID: () => 'fixture'},
      PortalInstructionClient: {execute: (instruction, args) => {writes.push({instruction, args}); return removal.promise;}}},
    selectedArticle: () => ({id: state.selectedId}), renderArticleGit: noop, showToast: noop,
    detailResourceGuest: {deleted: noop}, persistArticleSelection: id => selections.push(id), updateUrl: noop,
    loadArticles: async options => catalogLoads.push(options),
    request: (url, {signal}) => {
      const gate = deferred(), read = {url, signal, gate};
      reads.push(read);
      signal.addEventListener('abort', () => gate.reject(Object.assign(new Error('aborted'), {name: 'AbortError'})), {once: true});
      return gate.promise;
    },
  });
  const staticRoot = path.resolve(__dirname, '../../../management_portal/static');
  vm.runInContext(fs.readFileSync(path.join(staticRoot, 'portal_refresh_coordinator.js'), 'utf8'), context);
  const source = ARTICLE_SCRIPTS.map(file => fs.readFileSync(path.join(staticRoot, file), 'utf8'))
    .find(text => text.includes('const unregisteringArticles ='));
  vm.runInContext(source.slice(source.search(/^ +const unregisteringArticles =/m),
    source.search(/^ +async function updateArticleGitStage\(/m)) + '\nglobalThis.refreshOwner = articleGitRefresh;', context);
  return {context, state, removal, reads, writes, selections, catalogLoads};
}
const flushTasks = () => new Promise(resolve => setImmediate(resolve));

test('Article unregister aborts pending Git reads and suppresses refresh until its receipt', async () => {
  const f = articleUnregisterFixture();
  const loading = f.context.loadArticleGit({includeHistory: true});
  await flushTasks();
  assert.equal(f.reads.length, 2);
  const removing = f.context.unregisterCurrentArticle();
  await flushTasks();
  await loading;
  assert.ok(f.reads.every(read => read.signal.aborted));
  f.context.scheduleArticleGitRefresh(0);
  await f.context.loadArticleGit();
  await f.context.unregisterCurrentArticle();
  assert.equal(f.reads.length, 2);
  assert.equal(f.writes.length, 1);
  f.removal.resolve({});
  await removing;
  assert.equal(f.state.selectedId, '');
  assert.deepEqual(f.selections, ['']);
  assert.equal(f.catalogLoads[0].preserve, false);
  f.context.scheduleArticleGitRefresh(0);
  await f.context.loadArticleGit();
  assert.equal(f.reads.length, 2);
  f.context.refreshOwner.destroy();
});

test('Article unregister failure restores the selected Article Git refresh', async () => {
  const f = articleUnregisterFixture();
  const removing = f.context.unregisterCurrentArticle();
  await flushTasks();
  f.removal.reject(new Error('permission denied'));
  await removing;
  const retry = f.context.loadArticleGit();
  await flushTasks();
  assert.equal(f.state.selectedId, 'A');
  assert.deepEqual(f.selections, []);
  assert.equal(f.reads.length, 2);
  f.reads.forEach(read => read.gate.resolve({commits: []}));
  await retry;
  assert.equal(f.state.gitStatus.commits.length, 0);
  f.context.refreshOwner.destroy();
});

test('Article unregister receipt cannot clear another Article selected during the request', async () => {
  const f = articleUnregisterFixture();
  const removing = f.context.unregisterCurrentArticle();
  await flushTasks();
  f.state.selectedId = 'B';
  const loading = f.context.loadArticleGit();
  await flushTasks();
  assert.ok(f.reads.every(read => read.url.startsWith('/api/articles/B/')));
  f.reads.forEach(read => read.gate.resolve({commits: []}));
  await loading;
  f.removal.resolve({});
  await removing;
  assert.equal(f.state.selectedId, 'B');
  assert.deepEqual(f.selections, []);
  assert.equal(f.catalogLoads[0].preserve, true);
  assert.equal(f.catalogLoads[0].reloadSelected, false);
  f.context.refreshOwner.destroy();
});

test('Cancelling Article unregister leaves existing Git reads running', async () => {
  const f = articleUnregisterFixture({confirm: false});
  const loading = f.context.loadArticleGit();
  await flushTasks();
  await f.context.unregisterCurrentArticle();
  assert.equal(f.writes.length, 0);
  assert.ok(f.reads.every(read => !read.signal.aborted));
  f.reads.forEach(read => read.gate.resolve({commits: []}));
  await loading;
  assert.equal(f.state.selectedId, 'A');
  f.context.refreshOwner.destroy();
});

test('Repo pagination carries current filters through every page and bulk commit', async () => {
  const reads = [], executions = [];
  const context = owner(REPO_SCRIPTS, ['queryAllRepoRecords', 'startAllRepoAutomation'], {
    REPO_QUERY_LIMIT: 500, state: {automationActive: false, automationNames: new Map()},
    window: {PortalI18n: i18n, PortalUI: {confirm: async () => true}}, queryState: () => ({q: 'needle', project: 'visible'}),
    getJson: async url => {
      const query = new URL(url, 'https://test.invalid').searchParams;
      reads.push(Object.fromEntries(query));
      const offset = Number(query.get('offset'));
      return {total: 501, resources: Array.from({length: offset ? 1 : 500}, (_, i) => ({resource_id: `repo_${offset + i}`}))};
    },
    beginAutomation: noop, showToast: noop, renderAutomationDialog: noop,
    loadAutomationStatuses: async records => records.map(record => ({id: record.resource_id, dirty: true, actionable: true})),
    executeAutomationTargets: async targets => executions.push(targets),
  });
  await context.startAllRepoAutomation();
  assert.deepEqual(reads.map(read => read.offset), ['0', '500']);
  assert.equal(reads.every(read => read.q === 'needle' && read.project === 'visible'), true);
  assert.equal(executions[0].length, 501);
});

for (const name of ['loadCommitList', 'loadCredentials', 'refreshOverview']) {
  test(`Repo ${name} discards replies from a previous workspace`, async () => {
    const state = {selectedId: 'A', workspaceSequence: 1, commitListSequence: 0, credentialsSequence: 0,
      overviewSequence: 0, activeTab: 'commits', commitRevision: 'HEAD', commits: null};
    const gate = deferred();
    const context = owner(REPO_SCRIPTS, [name], {state, getJson: () => gate.promise,
      renderWorkspace: noop, showToast: noop, queryState: () => ({})});
    const pending = context[name]();
    state.selectedId = 'B'; state.workspaceSequence += 1;
    state.commits = {commits: [{sha: 'B'}]}; state.credentials = {owner: 'B'}; state.overview = {id: 'B'};
    gate.resolve({id: 'A', owner: 'A', commits: [{sha: 'A'}]});
    await pending;
    assert.equal(state.commits.commits[0].sha, 'B');
    assert.equal(state.credentials.owner, 'B');
    assert.equal(state.overview.id, 'B');
  });
}

