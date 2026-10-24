const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {test} = require('node:test');
const {JSDOM} = require('jsdom');
const {readHatgModule, createHatgModule} = require('./hatg_modules.cjs');

const html = fs.readFileSync(path.resolve(__dirname, '../../../management_portal/hatg-panel.html'), 'utf8');

// The production header and toolbar over the panel's own markup, with the
// production node lookups. The canvas selection, Family, layout and
// subgraph-move answers are fixture values each case sets.
function harness(lineage = null) {
  const dom = new JSDOM(`<!doctype html><body>${html}</body>`, {runScripts: 'outside-only'});
  const {window} = dom;
  window.PortalI18n = {t: value => value, text: (strings, ...values) => String.raw({raw: strings}, ...values)};
  const headers = [];
  window.PortalResourceHeader = {mount: (element, resource) => headers.push({element: element?.id, ...resource})};
  window.eval(readHatgModule('hatg_graph_model.js'));
  window.eval(readHatgModule('hatg_toolbar.js'));
  const state = {
    lineage, lineages: [], selectedNodeId: '', selectedCanvasObjectIds: new Set(), runtimeStatus: new Map(),
    graphViewMode: 'context', handoffMode: false, globalStopInFlight: false,
    programMutationInFlight: false, canvasSelectionDeleteInFlight: false,
  };
  const model = window.PortalHatgGraphModel.createGraphModel({esc: value => String(value), state});
  const fixture = {facades: new Set(), deletable: new Set(), familyCandidates: [], moving: false, autoLayout: 0};
  const toolbar = createHatgModule(window.PortalHatgToolbar, 'createToolbar', {
    state, $id: id => window.document.getElementById(id),
    activeSeeds: model.activeSeeds, currentNode: model.currentNode, displayNodeById: model.displayNodeById,
    effectiveNodeStatus: model.effectiveNodeStatus, runningLineageNodes: model.runningLineageNodes,
    familyForFacade: id => (fixture.facades.has(id) ? {facade_id: id} : null),
    selectedCanvasObjects: () => [...state.selectedCanvasObjectIds].map(id => ({id})),
    canvasObjectCanDelete: object => fixture.deletable.has(object.id),
    selectedFamilyCandidateIds: () => fixture.familyCandidates,
    subgraphMoveBusy: () => fixture.moving,
    renderAutoLayoutControl: () => { fixture.autoLayout += 1; },
  });
  const button = id => window.document.getElementById(id);
  return {toolbar, state, fixture, headers, button};
}

function lineage() {
  return {
    lineage_id: 'lineage_one', title: 'Review | Lineage', leader_resident_id: 'resident_lead',
    nodes: [{id: 'resident_lead', title: 'Lead'}, {id: 'resident_two', title: 'Two', status: 'done'}],
    edges: [], revision: 4,
  };
}

test('without a Lineage every Lineage action and view is unavailable', () => {
  const h = harness();
  h.toolbar.renderControls();
  for (const id of ['lineageSettingsBtn', 'addNodeBtn', 'autoLayoutBtn', 'addTemplateBtn', 'branchNodeBtn',
    'stopLineageBtn', 'handoffModeBtn', 'graphContextViewBtn', 'graphWorkspaceViewBtn', 'graphLeaderJumpBtn']) {
    assert.equal(h.button(id).disabled, true, id);
  }
  assert.equal(h.button('graphLeaderJumpBtn').title, '当前 Lineage 没有可用的 Leader');
  assert.equal(h.fixture.autoLayout, 1, 'the arrange control is painted with the toolbar');
  h.toolbar.renderLineageHeader();
  assert.deepEqual(h.headers.at(-1), {element: 'activeLineageIdentity', title: '选择 Lineage', type: '',
    resource_id: '', copy_label: 'lineage_id'});
});

test('a shown Lineage enables its actions, its Leader and the global Stop only while a node runs', () => {
  const h = harness(lineage());
  h.state.selectedNodeId = 'resident_two';
  h.toolbar.renderControls();
  for (const id of ['addNodeBtn', 'branchNodeBtn', 'handoffModeBtn', 'graphContextViewBtn']) {
    assert.equal(h.button(id).disabled, false, id);
  }
  assert.equal(h.button('graphLeaderJumpBtn').getAttribute('aria-label'), '跳转到 Leader：Lead');
  assert.equal(h.button('stopLineageBtn').disabled, true, 'nothing runs');
  for (const id of ['batchDeleteSelectionBtn', 'cancelCanvasSelectionBtn', 'moveSubgraphBtn']) {
    assert.equal(h.button(id).hidden, true, `${id} belongs to a selection`);
  }
  h.state.runtimeStatus.set('resident_lead', 'running');
  h.toolbar.renderControls();
  assert.equal(h.button('stopLineageBtn').disabled, false);
  h.state.globalStopInFlight = true;
  h.toolbar.renderControls();
  const stop = h.button('stopLineageBtn');
  assert.deepEqual([stop.disabled, stop.classList.contains('busy'), stop.getAttribute('aria-busy'),
    stop.querySelector('span').textContent], [true, true, 'true', 'Stopping…']);
  // A selected Family facade cannot be branched.
  h.fixture.facades.add('resident_two');
  h.toolbar.renderControls();
  assert.deepEqual([h.button('branchNodeBtn').disabled, h.button('branchNodeBtn').hidden], [true, true]);
  h.toolbar.renderLineageHeader();
  assert.deepEqual(h.headers.at(-1), {element: 'activeLineageIdentity', title: 'Review', type: 'lineage',
    resource_id: 'lineage_one', copy_label: 'lineage_id'});
});

test('a canvas selection shows only selection actions and deletes only deletable objects', () => {
  const h = harness(lineage());
  h.state.selectedCanvasObjectIds = new Set(['resident_lead', 'resident_two']);
  h.toolbar.renderControls();
  for (const id of ['addNodeBtn', 'handoffModeBtn', 'stopLineageBtn']) assert.equal(h.button(id).hidden, true, id);
  for (const id of ['wrapFunctionBlockBtn', 'batchDeleteSelectionBtn', 'cancelCanvasSelectionBtn', 'moveSubgraphBtn']) {
    assert.equal(h.button(id).hidden, false, id);
  }
  assert.equal(h.button('wrapFunctionBlockCount').textContent, '2');
  assert.equal(h.button('wrapFunctionBlockBtn').classList.contains('is-active'), true);
  assert.equal(h.button('familizeBtn').disabled, true, 'no Family candidates');
  const remove = h.button('batchDeleteSelectionBtn');
  assert.deepEqual([remove.disabled, remove.dataset.portalTooltip], [true, '当前选区只包含不可删除的同步主轨迹节点。']);
  h.fixture.deletable.add('resident_two');
  h.fixture.familyCandidates = ['resident_two'];
  h.toolbar.renderControls();
  assert.deepEqual([remove.disabled, remove.dataset.portalTooltip], [false, '批量删除当前选中的 2 个 HATG 元素。']);
  assert.equal(h.button('familizeBtn').disabled, false);
  assert.equal(h.button('graphActions').getAttribute('aria-label'), 'Context 多选操作，已选择 2 个元素');
  // A running edit or subgraph move holds the selection actions.
  h.state.programMutationInFlight = true;
  h.fixture.moving = true;
  h.toolbar.renderControls();
  assert.deepEqual([remove.disabled, h.button('moveSubgraphBtn').disabled,
    h.button('saveSelectionTemplateBtn').disabled], [true, true, true]);
});

test('the Workspace view hides the Context actions and marks its own tab', () => {
  const h = harness(lineage());
  h.state.graphViewMode = 'workspace';
  h.toolbar.renderControls();
  for (const id of ['addNodeBtn', 'branchNodeBtn', 'handoffModeBtn', 'stopLineageBtn']) assert.equal(h.button(id).hidden, true, id);
  assert.equal(h.button('graphWorkspaceViewBtn').getAttribute('aria-selected'), 'true');
  assert.equal(h.button('graphContextViewBtn').getAttribute('aria-selected'), 'false');
  assert.equal(h.button('graphActions').dataset.viewMode, 'workspace');
  assert.equal(h.button('graphActions').getAttribute('aria-label'), 'Workspace 操作');
});

test('handoff needs two Residents and shows when it is choosing', () => {
  const single = harness({...lineage(), nodes: [{id: 'resident_lead', title: 'Lead'}]});
  single.toolbar.renderControls();
  assert.equal(single.button('handoffModeBtn').disabled, true);
  const h = harness(lineage());
  h.state.handoffMode = true;
  h.toolbar.renderControls();
  const handoff = h.button('handoffModeBtn');
  assert.deepEqual([handoff.disabled, handoff.classList.contains('is-active'), handoff.getAttribute('aria-pressed')],
    [false, true, 'true']);
  assert.equal(handoff.dataset.portalTooltip, '工作交接：正在选择交接节点；可通过提示栏退出交接模式。');
});

test("the Lineage list entry follows the shown Lineage's counts and run states", () => {
  const h = harness(lineage());
  h.state.runtimeStatus.set('resident_lead', 'running');
  h.toolbar.updateLineageSummary();
  assert.equal(h.state.lineages.length, 1);
  const [entry] = h.state.lineages;
  assert.deepEqual([entry.lineage_id, entry.node_count, entry.edge_count, entry.active_seed_count, entry.revision],
    ['lineage_one', 2, 0, 0, 4]);
  assert.deepEqual({...entry.statuses}, {running: 1, done: 1});
  h.state.runtimeStatus.delete('resident_lead');
  h.toolbar.updateLineageSummary();
  assert.equal(h.state.lineages.length, 1, 'the existing entry is updated in place');
  assert.equal(h.state.lineages[0], entry);
  assert.deepEqual({...entry.statuses}, {idle: 1, done: 1});
  h.toolbar.setSyncStatus('已保存', 'saved');
  assert.deepEqual([h.button('graphSyncStatus').textContent, h.button('graphSyncStatus').dataset.state], ['已保存', 'saved']);
});
