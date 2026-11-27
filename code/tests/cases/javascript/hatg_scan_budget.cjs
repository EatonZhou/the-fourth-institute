'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const staticRoot = path.resolve(__dirname, '../../../management_portal/static');
const count = 140;
const read = (name, reference) => {
  const old = reference && path.join(reference, name + '.js');
  return fs.readFileSync(old && fs.existsSync(old) ? old : path.join(staticRoot, name + '.js'), 'utf8');
};

function run(reference) {
  const window = {
    PortalI18n: { t: String },
    PortalUIPrimitives: { escapeHtml: String },
    PortalCompatibility: { identifier: String },
  };
  const context = vm.createContext({ window });
  for (const name of ['hatg_graph_model', 'hatg_family_blocks', 'hatg_graph_projection', 'lineage_template_graph']) {
    vm.runInContext(read(name, reference), context, { filename: name + '.js' });
  }

  const reads = { residentId: 0, family: 0, forkType: 0, wireEndpoint: 0 };
  const numbers = Array.from({ length: count }, (_, index) => index);
  const nodes = numbers.map(index => ({
    get id() { reads.residentId++; return 'resident_' + index; },
    title: 'Resident ' + index,
  }));
  const blocks = numbers.map(index => ({
    id: 'block_' + index,
    get family() { reads.family++; return { facade: 'resident_' + index, members: [] }; },
    elements: [],
  }));
  const edges = numbers.map(index => ({
    get type() { reads.forkType++; return 'candidate_fork'; },
    source: 'resident_' + index,
    target: 'seed_' + index,
  }));
  const components = numbers.map(index => ({ id: 'seed_' + index, kind: 'seed', config: { status: 'active' } }));
  const state = { lineage: { nodes, edges, function_blocks: blocks, program: { components } } };
  const model = window.PortalHatgGraphModel.createGraphModel({ state, esc: String });
  const familyModule = window.PortalHatgFamilyBlocks;
  const noops = Object.fromEntries(familyModule.dependencies.map(key => [key, () => {}]));
  const family = familyModule.createFamilyBlocks({ ...noops, ...model, state, esc: String });
  for (const index of numbers) {
    assert.equal(model.displayNodeById('resident_' + index), nodes[index]);
    assert.equal(family.familyForFacade('resident_' + index).instance, blocks[index]);
  }
  assert.equal(model.activeSeeds().length, count);
  const lookupReads = { ...reads };
  state.lineage = { ...state.lineage, nodes: [nodes[1], nodes[0]], function_blocks: [blocks[1]] };
  assert.equal(model.displayNodeById('resident_2'), null);
  assert.equal(family.familyForFacade('resident_0'), null);
  assert.equal(family.familyForFacade('resident_1').instance, blocks[1]);
  if (model.lineageObjectsOf) {
    assert.deepEqual(Array.from(model.lineageObjectsOf('nodes', new Set(['resident_0', 'resident_1']))), [nodes[1], nodes[0]]);
  }
  assert.equal(model.displaySeedById('seed_0').source_resident_id, 'resident_0');
  state.lineage = { ...state.lineage, edges: [] };
  assert.equal(model.displaySeedById('seed_0').source_resident_id, '');

  const roles = numbers.map(index => ({ key: 'slot_' + index, from: 'target' }));
  const wires = numbers.slice(1).map(index => ({
    get source() { reads.wireEndpoint++; return 'slot_' + (index - 1); },
    get target() { reads.wireEndpoint++; return 'slot_' + index; },
  }));
  const bindings = window.LineageTemplateGraph.bindings({ roles, wires, match: [] });
  assert.equal(bindings.slot_0.direction, 'out');
  assert.equal(bindings.slot_1.direction, 'both');
  assert.equal(bindings['slot_' + (count - 1)].direction, 'in');
  let workspaceLayouts = 0;
  window.PortalHatgWorkspaceProjection = {
    project(residents) { return { nodes: residents, edges: [] }; },
  };
  window.PortalHatgGraph = {
    computeWorkspaceGraphLayout() { workspaceLayouts++; return new Map(); },
  };
  const graphModule = window.PortalHatgGraphProjection;
  const graphNoops = Object.fromEntries(graphModule.dependencies.map(key => [key, () => {}]));
  const preferences = new Map();
  const graph = graphModule.createGraphProjection({ ...graphNoops, state, hatgViewPreferences: preferences });
  state.lineage = { lineage_id: 'lineage_test', nodes };
  state.workspaceGraphBindings = new Map();
  for (let index = 0; index < 8; index++) assert.equal(graph.workspaceGraphModel().overviewNodes.length, count);
  preferences.set('lineage_test', { workspace_positions: { resident_0: { x: 12 } } });
  assert.equal(graph.workspaceGraphModel().overviewNodes[0].x, 12);
  state.workspaceGraphBindings = new Map();
  graph.workspaceGraphModel();
  state.lineage = { ...state.lineage, nodes: [nodes[0]] };
  assert.equal(graph.workspaceGraphModel().overviewNodes.length, 1);

  return { ...lookupReads, wireEndpoint: reads.wireEndpoint, workspaceLayouts };
}

const actual = run();
assert.deepEqual(actual, { residentId: count, family: count, forkType: count, wireEndpoint: (count - 1) * 2, workspaceLayouts: 3 });
console.log(JSON.stringify({ ...(process.env.HATG_SCAN_REFERENCE ? { before: run(process.env.HATG_SCAN_REFERENCE) } : {}), after: actual }));
