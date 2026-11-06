const {test} = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const {readHatgModule, createHatgModule} = require('./hatg_modules.cjs');

const noop = () => {};
function harness(query = '?lineage=lineage_one&resident=resident_one&view=workspace') {
  const context = vm.createContext({URL, URLSearchParams, location: new URL(`http://portal/lineage${query}`),
    requestAnimationFrame: callback => callback(),
    PortalI18n: {t: value => value, text: (strings, ...values) => String.raw({raw: strings}, ...values)},
    PortalHatgPanelFeatures: {createFeatures: () => ({}), attachAliases: state => state},
  });
  context.window = context;
  context.history = {replaceState: (_state, _title, url) => { context.location = new URL(url); }};
  for (const file of ['hatg_panel_state.js', 'hatg_panel_lineage_loading.js', 'hatg_graph_view.js', 'hatg_workspace_results.js']) {
    vm.runInContext(readHatgModule(file), context);
  }
  const state = context.PortalHatgPanelState.createState();
  const lineages = {lineage_one: {lineage_id: 'lineage_one', nodes: [{id: 'resident_one'}], program: {components: []}}};
  const calls = {reads: [], renders: [], toasts: []};
  context.PortalResidentService = {workspaces: async id => {
    calls.reads.push(id);
    return {roots: [{directory_id: 'dir_one'}], entries: []};
  }};
  const results = createHatgModule(context.PortalHatgWorkspaceResults, 'createWorkspaceResults', {state, renderGraph: noop});
  const toast = message => calls.toasts.push(message);
  const renderLineageSurface = () => calls.renders.push({mode: state.graphViewMode, bindings: state.workspaceGraphBindings.size});
  const graph = createHatgModule(context.PortalHatgGraphView, 'createGraphView', {
    state, $id: () => null, loadWorkspaceGraphBindings: results.loadWorkspaceGraphBindings,
    clearCanvasSelection: noop, clearGraphNodeSearch: noop, displayEdgeById: () => null,
    renderLineageSurface, toast,
  });
  const loading = createHatgModule(context.PortalHatgPanelLineageLoading, 'createLineageLoading', {
    state, $id: () => null, societyService: {loadLineage: async id => lineages[id]},
    loadHatgViewPreference: async () => ({expanded_family_facade_ids: []}),
    loadWorkspaceGraphBindings: results.loadWorkspaceGraphBindings,
    setWorkspaceViewLoading: graph.setWorkspaceViewLoading,
    activateLineageEdits: noop, clearGraphNodeSearch: noop, clearNodeSelection: noop,
    panelHost: {hatgComponentOptions: {}, templateController: {load: async () => {}}, panelActive: false},
    programFeature: {clearForLineageChange: noop}, publishLocation: noop, updateLineageSummary: noop,
    renderLineageSurface, setSyncStatus: noop, nodeResidentId: node => node.id,
    selectNode: async id => { state.selectedNodeId = id; }, toast,
  });
  return {context, state, calls, graph, loading, lineages};
}

test('Workspace URL survives a tab switch and refresh restores its bindings before rendering', async () => {
  const first = harness('');
  await first.loading.selectLineage('lineage_one');
  await first.graph.setGraphViewMode('workspace');
  assert.equal(first.context.location.searchParams.get('view'), 'workspace');
  const refreshed = harness(first.context.location.search);
  await refreshed.loading.selectLineage('lineage_one', {residentId: 'resident_one'});
  assert.equal(refreshed.state.graphViewMode, 'workspace');
  assert.deepEqual(refreshed.calls.reads, ['resident_one']);
  assert.deepEqual(refreshed.calls.renders, [{mode: 'workspace', bindings: 1}]);
  assert.equal(refreshed.state.selectedNodeId, 'resident_one');
  await refreshed.graph.setGraphViewMode('context');
  assert.equal(refreshed.context.location.searchParams.has('view'), false);
});

test('view changes preserve other URL targets and Map still restores', async () => {
  const h = harness();
  h.state.graphViewMode = 'context';
  await h.graph.setGraphViewMode('frontier');
  assert.equal(h.context.location.searchParams.get('resident'), 'resident_one');
  assert.equal(h.context.location.searchParams.get('lineage'), 'lineage_one');
  assert.equal(harness(h.context.location.search).state.graphViewMode, 'frontier');
  h.context.location = new URL('http://portal/home?view=workspace');
  assert.equal(h.context.PortalHatgPanelState.createState().graphViewMode, 'context');
});

test('failed restored bindings explain the failure and retain a usable Context graph', async () => {
  const h = harness();
  h.context.PortalResidentService.workspaces = async () => { throw new Error('offline'); };
  await h.loading.selectLineage('lineage_one');
  assert.equal(h.state.lineage.lineage_id, 'lineage_one');
  assert.equal(h.state.graphViewMode, 'context');
  assert.equal(h.state.workspaceViewLoading, false);
  assert.match(h.calls.toasts[0], /Binding/);
});

test('a late Workspace restore cannot replace a view explicitly chosen while loading', async () => {
  const h = harness();
  let release;
  const waiting = new Promise(resolve => { release = resolve; });
  h.context.PortalResidentService.workspaces = () => waiting;
  const loaded = h.loading.selectLineage('lineage_one');
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(h.state.workspaceViewLoading, true);
  await h.graph.setGraphViewMode('frontier');
  release({roots: [], entries: []});
  await loaded;
  assert.equal(h.state.graphViewMode, 'frontier');
  assert.equal(h.state.workspaceViewLoading, false);
});
