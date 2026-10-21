const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

// Execute production owners with controlled inputs. Page journeys cover their DOM;
// these cases isolate request ordering and language-dependent presentation.
function owner(file, names, dependencies) {
  const source = fs.readFileSync(path.resolve(__dirname, '../../../management_portal/static', file), 'utf8');
  const functions = names.map(name => {
    const match = new RegExp(`^( +)(?:async )?function ${name}\\(`, 'm').exec(source);
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
  const calls = [], dialogs = [], migrations = [], notices = [], listeners = {};
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
      restartSession: async values => {calls.push(['restart', values]);},
    },
    PortalInstructionClient: {execute: async (instruction, args) => {
      calls.push([instruction, args]);
      return {settings: {...snapshot.runtime_settings, ...args.changes}};
    }},
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
      settings: options => ({open: (...args) => dialogs.push(args), close: options.onClose, destroy: noop}),
      restart: options => ({open: (...args) => migrations.push(args), close: options.onClose, destroy: noop}),
    },
  });
  const context = owner('portal_chat_workspace.js', ['createFamilyStatusView', 'requestWorkspaceAction'], {
    window, document: {createElement: () => root, activeElement: null}, CSS: {escape: value => value},
    PortalUIPrimitives: {randomUUID: () => 'fixture'},
    escapeHtml: value => String(value ?? ''), footerTime: () => '', sessionRestartActive: () => false,
    notifyRuntimeSettingsSaved: noop,
    createRuntimeSettingsDialog: () => ({open: (...args) => dialogs.push(args), destroy: noop}),
    createSessionRestartDialog: () => ({open: (...args) => migrations.push(args), destroy: noop}),
  });
  const view = context.createFamilyStatusView(facade, null, null, null, {append: noop});
  view.open();
  await new Promise(resolve => setImmediate(resolve));
  return {root, view, calls, dialogs, migrations, notices,
    click: async (entry = 'settings') => { listeners.click({target: {closest: selector =>
      selector === `[data-family-status-${entry}]` ? {dataset: entry === 'settings'
        ? {familyStatusSettings: member} : {familyStatusRuntime: member}} : null}}); await new Promise(resolve => setImmediate(resolve)); }};
}

test('Family Backend/account entry edits the member live without requesting a restart', async () => {
  const fixture = await familySettingsFixture();
  await fixture.click();
  const [initial, save, options] = fixture.dialogs[0];
  assert.equal(initial.reasoning_effort, 'high');
  assert.match(options.hint, /不会重启/);
  assert.match(fixture.calls.find(call => call[0] === 'get' && call[1].includes('agent-runtime-catalog'))[1], /resident_id=resident_member/);
  await save({...initial, reasoning_effort: 'low', service_tier: 'priority'});
  const update = fixture.calls.find(call => call[0] === 'UpdateResidentSettings');
  assert.deepEqual(JSON.parse(JSON.stringify(update[1])), {
    resident_id: 'resident_member', changes: {reasoning_effort: 'low', service_tier: 'priority'},
  });
  assert.equal(fixture.calls.some(call => call[0].startsWith('restart')), false);
  assert.match(fixture.root.innerHTML, /low · Fast 开/);
  await fixture.click('settings');
  assert.equal(fixture.dialogs[1][0].service_tier, 'priority');
  const before = fixture.calls.length;
  await fixture.dialogs[1][1](fixture.dialogs[1][0]);
  assert.equal(fixture.calls.length, before, 'unchanged settings do not write');
  await options.onSessionRestart();
  assert.equal(fixture.migrations.length, 1);
  assert.equal(fixture.calls.some(call => call[0] === 'restart'), false, 'opening restart still requires confirmation');
  fixture.view.destroy();
});

test('Family live settings remain available without restart permission and read-only providers can migrate', async () => {
  const live = await familySettingsFixture({restart: false});
  await live.click();
  assert.equal(live.dialogs[0][2].onSessionRestart, null);
  assert.equal(live.migrations.length, 0);
  live.view.destroy();
  const migration = await familySettingsFixture({runtime: false});
  await migration.click();
  assert.equal(migration.dialogs.length, 0);
  assert.equal(migration.migrations.length, 1);
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
  const graph = owner('hatg_panel.js', ['removeFamilyInstance'], {
    window, state: {lineage: {lineage_id: 'lineage_one'}},
    functionBlockInstanceById: () => options.family, familyRoles: () => null,
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
  const context = owner('article_workspace.js', ['compileArticle'], {
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

test('Repo pagination carries current filters through every page and bulk commit', async () => {
  const reads = [], executions = [];
  const context = owner('repo_workspace.js', ['queryAllRepoRecords', 'startAllRepoAutomation'], {
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
    const context = owner('repo_workspace.js', [name], {state, getJson: () => gate.promise,
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

