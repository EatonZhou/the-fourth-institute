const assert = require('node:assert/strict');
const vm = require('node:vm');
const {test} = require('node:test');
const {readHatgModule, createHatgModule} = require('./hatg_modules.cjs');

// A Lineage Workspace, the selected Resident's own Workspace and a shared
// directory, as the Resident Workspace service lists them.
const ROOTS = [
  {directory_id: 'dir_lineage', is_lineage_workspace: true},
  {directory_id: 'dir_own', is_default_cwd: true},
  {directory_id: 'dir_shared', workspace_usage: 'primary'},
];
const ENTRIES = [
  {path: 'dir_own/report.md', rel_path: 'report.md', type: 'file', root_kind: 'workspace'},
  {path: 'dir_lineage/plan.md', rel_path: 'plan.md', type: 'file', is_lineage_workspace: true},
  {path: 'dir_shared/plan.md', rel_path: 'plan.md', type: 'file', root_kind: 'workspace'},
  {path: 'dir_shared/notes.md', rel_path: 'notes.md', type: 'file', root_kind: 'shared'},
  {path: 'dir_own/notes.md', rel_path: 'notes.md', type: 'file', root_kind: 'workspace'},
  {path: 'dir_own/out', rel_path: 'out', type: 'directory', root_kind: 'workspace'},
];

// The production Workspace results; the Resident Workspace service, the
// Preview route and the page redraws are the replaced parts.
function harness({roots = ROOTS, entries = ENTRIES, options = {}} = {}) {
  const calls = {previews: [], workspaces: [], headers: 0, graphs: 0, fits: 0, toasts: []};
  const state = {
    lineage: {lineage_id: 'lineage_one', nodes: [{id: 'resident_one'}, {id: 'resident_two'}]},
    selectedNodeId: 'resident_one', selectedSeedId: '', selectedResultPath: '',
    resultWorkspace: {roots, entries}, resultWorkspaceNodeId: 'resident_one', resultWorkspaceSeq: 0,
    workspaceGraphBindings: new Map(), workspaceGraphLoadSeq: 0, graphViewMode: 'context',
    graphController: {fit: () => { calls.fits += 1; }},
  };
  const workspaces = {};
  const context = vm.createContext({
    PortalI18n: {t: value => value, text: (strings, ...values) => String.raw({raw: strings}, ...values)},
    PortalResidentService: {workspaces: id => {
      calls.workspaces.push(id);
      return workspaces[id] ? workspaces[id]() : Promise.resolve({roots: [], entries: []});
    }},
    requestAnimationFrame: callback => callback(),
  });
  context.window = context;
  vm.runInContext(readHatgModule('hatg_workspace_results.js'), context);
  const seeds = new Map();
  const results = createHatgModule(context.PortalHatgWorkspaceResults, 'createWorkspaceResults', {
    state,
    currentHatgComponentOptions: () => ({
      onPreviewRequest: async request => { calls.previews.push(request); },
      ...options,
    }),
    currentNode: () => state.lineage.nodes.find(node => node.id === state.selectedNodeId) || null,
    displaySeedById: id => seeds.get(id) || null,
    seedById: () => null,
    renderLineageHeader: () => { calls.headers += 1; },
    renderGraph: () => { calls.graphs += 1; },
    toast: (message, kind) => calls.toasts.push({message, kind}),
  });
  return {results, state, calls, seeds, workspaces, context};
}

async function previewPath(h, path) {
  await h.results.loadResultFile(path);
  return h.calls.previews.at(-1).path;
}

test('a result path opens the file of the Workspace it names', async () => {
  const h = harness();
  assert.equal(await previewPath(h, 'dir_own/report.md'), 'dir_own/report.md', 'an exact listed path');
  assert.equal(await previewPath(h, '/dir_shared/other/x.md'), 'dir_shared/other/x.md', 'a path inside a listed root');
  assert.equal(await previewPath(h, '\\report.md\\'), 'dir_own/report.md', 'the only file with that relative path');
  assert.equal(await previewPath(h, 'plan.md'), 'dir_lineage/plan.md', 'the Lineage Workspace wins between equal names');
  assert.equal(await previewPath(h, 'notes.md'), 'dir_own/notes.md', 'then a Resident Workspace wins over a shared directory');
  assert.equal(await previewPath(h, 'out'), 'dir_own/out', 'a directory is not a file match, so the default cwd is used');
  assert.equal(await previewPath(h, 'missing.md'), 'dir_own/missing.md');
  h.seeds.set('seed_one', {id: 'seed_one', artifact_path: '/missing.md'});
  h.state.selectedSeedId = 'seed_one';
  assert.equal(await previewPath(h, 'missing.md'), 'dir_lineage/missing.md', "the selected Seed's artifact is in the Lineage Workspace");
});

test('without a default cwd the primary Workspace, then the path itself, is used', async () => {
  const withPrimary = harness({roots: ROOTS.filter(root => !root.is_default_cwd), entries: []});
  assert.equal(await previewPath(withPrimary, 'a.md'), 'dir_shared/a.md');
  const bare = harness({roots: [], entries: []});
  assert.equal(await previewPath(bare, 'a.md'), 'a.md');
});

test('the Preview request names the Lineage Workspace file and remembers it', async () => {
  const h = harness();
  assert.equal(await h.results.loadResultFile('dir_own/report.md', {reload: true}), true);
  assert.deepEqual({...h.calls.previews[0]}, {
    locatorKind: 'lineage-workspace', lineageId: 'lineage_one', residentId: 'resident_one',
    path: 'dir_own/report.md', title: 'report.md', source: 'hatg', reload: true,
  });
  assert.equal(h.state.selectedResultPath, 'dir_own/report.md');
  assert.equal(await h.results.loadResultFile(''), false);
  h.state.resultWorkspaceNodeId = '';
  assert.equal(await h.results.loadResultFile('dir_own/report.md'), false);
  assert.equal(h.calls.previews.length, 1);
});

test('the page Preview route is used when the component gives none, and its absence is reported', async () => {
  const routed = [];
  const h = harness({options: {onPreviewRequest: undefined}});
  h.context.PortalPreview = {request: async request => { routed.push(request.path); }};
  await h.results.loadResultFile('dir_own/report.md');
  assert.deepEqual(routed, ['dir_own/report.md']);
  delete h.context.PortalPreview;
  await assert.rejects(h.results.loadResultFile('dir_own/report.md'), /统一 Preview 路由尚未初始化/);
});

test("refreshing results reads the selected Resident's Workspaces and reopens the open file", async () => {
  const h = harness();
  const listed = {roots: ROOTS, entries: ENTRIES};
  h.workspaces.resident_one = async () => listed;
  h.state.selectedResultPath = 'dir_own/report.md';
  await h.results.loadResults();
  assert.equal(h.state.resultWorkspace, listed);
  assert.equal(h.state.workspaceGraphBindings.get('resident_one'), listed);
  assert.equal(h.calls.headers, 1);
  assert.equal(h.calls.previews.at(-1).reload, true);
  // A read that finishes after another Resident was selected keeps the binding
  // for the Workspace view and leaves the shown results alone.
  let finish;
  h.workspaces.resident_one = () => new Promise(resolve => { finish = resolve; });
  const pending = h.results.loadResults();
  h.state.selectedNodeId = 'resident_two';
  const later = {roots: [], entries: []};
  finish(later);
  await pending;
  assert.equal(h.state.resultWorkspace, listed);
  assert.equal(h.state.workspaceGraphBindings.get('resident_one'), later);
  assert.equal(h.calls.headers, 1);
  h.workspaces.resident_two = async () => { throw new Error('denied'); };
  await h.results.loadResults();
  assert.deepEqual(h.calls.toasts, [{message: '成果列表刷新失败：denied', kind: 'error'}]);
});

test('the Workspace view reads only missing bindings and drops reads of another Lineage', async () => {
  const h = harness();
  h.state.graphViewMode = 'workspace';
  h.state.workspaceGraphBindings.set('resident_one', {roots: []});
  h.workspaces.resident_two = async () => { throw new Error('denied'); };
  assert.deepEqual({...await h.results.loadWorkspaceGraphBindings()}, {complete: false, failedCount: 1, stale: false});
  assert.deepEqual(h.calls.workspaces, ['resident_two']);
  assert.deepEqual([h.calls.graphs, h.calls.fits], [1, 1]);
  h.workspaces.resident_two = async () => ({roots: []});
  assert.deepEqual({...await h.results.loadWorkspaceGraphBindings(undefined, {fit: false})}, {complete: true, failedCount: 0, stale: false});
  assert.deepEqual([h.calls.graphs, h.calls.fits], [2, 1]);
  assert.deepEqual({...await h.results.loadWorkspaceGraphBindings()}, {complete: true, failedCount: 0, stale: false});
  assert.deepEqual(h.calls.workspaces, ['resident_two', 'resident_two'], 'complete bindings are not read again');
  h.state.workspaceGraphBindings = new Map();
  const shown = h.state.lineage;
  h.workspaces.resident_one = async () => { h.state.lineage = {lineage_id: 'lineage_two', nodes: []}; return {roots: []}; };
  assert.deepEqual({...await h.results.loadWorkspaceGraphBindings(shown)}, {complete: false, failedCount: 0, stale: true});
  assert.equal(h.state.workspaceGraphBindings.size, 0);
});
