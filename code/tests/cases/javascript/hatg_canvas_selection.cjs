const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const test = require('node:test');
const { babelParse, traverse } = require('./babel_parse.cjs');
const { readHatgModule, createHatgModule } = require('./hatg_modules.cjs');
const root = path.resolve(__dirname, '../../..');
const source = fs.readFileSync(path.join(root, 'management_portal/static/hatg_panel.js'), 'utf8');
const plain = value => JSON.parse(JSON.stringify(value));
const R = 'resident_bbbbbbbbbbbb', B = 'fbi_111111111111', F = 'filter_222222222222';
const W = 'wire_333333333333', OUT = 'resident_cccccccccccc';

function fixture() {
  const ast = babelParse(source, 'hatg_panel.js');
  const names = new Set(['canvasObjectById', 'currentCanvasSelectionAnchorId', 'applyCanvasSelection',
    'toggleCanvasObjectSelection', 'addCanvasObjectsToSelection', 'reconcileGraphInteractionState',
    'openFunctionalize', 'visibleCanvasObjectIds', 'reconcileCanvasSelection']);
  const extracted = [];
  traverse(ast, { FunctionDeclaration(p) {
    if (names.has(p.node.id.name)) extracted.push(source.slice(p.node.start, p.node.end));
  } });
  const state = { lineage: { nodes: [{ id: R }, { id: OUT }],
    program: { components: [{ id: F }], wires: [{ id: W, source: F, target: OUT }] },
    function_blocks: [{ id: B, elements: [{ id: R, kind: 'resident' }] }] },
    selectedNodeId: R, selectedSeedId: '', selectedProgramObjectId: '', selectedEdgeId: '',
    graphViewMode: 'context', graphFocusMode: false };
  const expanded = new Set(), requests = [], snapshots = [];
  const context = vm.createContext({ state, console, Set, Map,
    // The viewer's open blocks are the browser preference the module reads.
    localStorage: { getItem: () => JSON.stringify([...expanded]), setItem() {} },
    PortalI18n: { t: s => s }, PortalUI: { prompt: async () => 'Outer' },
    renderCanvasSelection: () => {}, clearNodeSelection: () => {},
    clearCanvasSelection: () => context.programFeature.clearSelection(),
    applyProgramMutation: fn => fn(),
    executeHatgInstruction: async (instruction, args) => requests.push({ instruction, ...plain(args) }),
  });
  context.window = context;
  for (const file of ['hatg_panel_features.js', 'lineage_program.js']) {
    vm.runInContext(fs.readFileSync(path.join(root, 'management_portal/static', file), 'utf8'), context);
  }
  // Lookups, block membership and folding come from the production module scripts.
  vm.runInContext(readHatgModule('hatg_graph_model.js'), context);
  const model = createHatgModule(context.PortalHatgGraphModel, 'createGraphModel', { state });
  for (const name of ['nodeById', 'displayNodeById', 'displaySeedById', 'programComponentById',
    'displayProgramComponentById', 'displayProgramWireById', 'functionBlockInstanceById',
    'displayGraphItemById', 'isFunctionBlockInstance', 'graphSelectedId', 'graphSelectedEdgeId']) {
    context[name] = model[name];
  }
  vm.runInContext(readHatgModule('hatg_family_blocks.js'), context);
  const blocks = createHatgModule(context.PortalHatgFamilyBlocks, 'createFamilyBlocks', {
    state, nodeById: model.nodeById, displayNodeById: model.displayNodeById,
    programComponentById: model.programComponentById,
    functionBlockInstanceById: model.functionBlockInstanceById,
    isFunctionBlockInstance: model.isFunctionBlockInstance,
  });
  Object.assign(context, {
    functionBlockProjection: blocks.functionBlockProjection,
    functionBlockElements: blocks.functionBlockElements,
    expandedFunctionBlockIds: blocks.expandedFunctionBlockIds,
  });
  const features = context.window.PortalHatgPanelFeatures.createFeatures();
  context.programFeature = features.program;
  context.window.PortalHatgPanelFeatures.attachAliases(state, features);
  context.graphVisibleModel = () => {
    const projection = context.functionBlockProjection([
      ...state.lineage.nodes, ...state.lineage.program.components, ...state.lineage.function_blocks,
    ], state.lineage.program.wires);
    return { nodes: projection.nodes, edges: projection.edges, functionBlockBoundaries: projection.boundaries };
  };
  vm.runInContext(extracted.join('\n'), context);
  return { state, expanded, requests, context, snapshots,
    record(label) { snapshots.push({ label, anchor: state.canvasSelectionAnchorId,
      selected: [...state.selectedCanvasObjectIds], visible: context.graphVisibleModel().nodes.map(n => n.id) }); },
  };
}

test('folded resident anchor, modifier block, refresh, modifier Filter sends only block and Filter', async () => {
  const f = fixture();
  f.state.canvasSelectionAnchorId = R;
  f.record('folded, stale resident anchor');
  f.context.toggleCanvasObjectSelection(B);
  f.record('modifier block');
  f.context.reconcileGraphInteractionState();
  f.record('definition refresh');
  f.context.toggleCanvasObjectSelection(F);
  f.record('modifier Filter');
  await f.context.openFunctionalize();
  console.log(JSON.stringify({ snapshots: f.snapshots, requests: f.requests }));
  assert.deepEqual(f.requests[0].element_ids, [B, F]);
});

test('refresh retains a visible block and wire; collapse removes hidden selected residents', () => {
  const f = fixture();
  f.expanded.add(B);
  f.state.selectedCanvasObjectIds = new Set([R, B, W]);
  f.context.reconcileGraphInteractionState();
  assert.deepEqual([...f.state.selectedCanvasObjectIds], [R, B, W]);
  f.expanded.delete(B);
  f.context.reconcileGraphInteractionState();
  assert.deepEqual([...f.state.selectedCanvasObjectIds], [B, W]);
});

test('box selection and stale anchors use visible objects, never folded members or visual-only edges', () => {
  const f = fixture();
  f.state.canvasSelectionAnchorId = R;
  f.context.addCanvasObjectsToSelection([R, B, F, W], { includeCurrent: true });
  assert.deepEqual([...f.state.selectedCanvasObjectIds], [B, F, W]);
  f.state.lineage.program.wires[0].source = R;
  f.context.reconcileGraphInteractionState();
  assert.deepEqual([...f.state.selectedCanvasObjectIds], [B, F]);
});

test('a selected real wire anchors itself, not its source node', () => {
  const f = fixture();
  f.state.selectedProgramObjectId = W;
  f.context.toggleCanvasObjectSelection(B);
  assert.deepEqual([...f.state.selectedCanvasObjectIds], [W, B]);
});

test('Functionalize never appends a hidden single wire to the visible multi-selection', async () => {
  const f = fixture();
  f.state.lineage.program.wires[0].source = R;
  f.state.selectedProgramObjectId = W;
  f.state.selectedCanvasObjectIds = new Set([B, F]);
  await f.context.openFunctionalize();
  assert.deepEqual(f.requests[0].element_ids, [B, F]);
});

test('a collapse during the naming prompt invalidates the request instead of wrapping a subset', async () => {
  const f = fixture();
  f.expanded.add(B);
  f.state.selectedCanvasObjectIds = new Set([R, F]);
  let confirm;
  f.context.window.PortalUI.prompt = () => new Promise(resolve => { confirm = resolve; });
  const pending = f.context.openFunctionalize();
  f.expanded.delete(B);
  f.context.reconcileGraphInteractionState();
  confirm('Outer');
  await assert.rejects(pending, /选区已变化/);
  assert.deepEqual(f.requests, []);
});

test('explicit visible member selection preserves domain rejection and the selection for correction', async () => {
  const f = fixture();
  f.expanded.add(B);
  f.state.selectedCanvasObjectIds = new Set([R, F]);
  f.context.executeHatgInstruction = async (_instruction, args) => {
    assert.deepEqual(plain(args.element_ids), [R, F]);
    throw new Error('Element already belongs to a Function Block: ' + R);
  };
  await assert.rejects(f.context.openFunctionalize(), /already belongs/);
  assert.deepEqual([...f.state.selectedCanvasObjectIds], [R, F]);
});
