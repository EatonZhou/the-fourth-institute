const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const test = require('node:test');
const { STATIC } = require('./hatg_modules.cjs');

function load(...files) {
  const window = {
    PortalI18n: { t: value => value, text: (strings, ...values) => strings.reduce((r, s, i) => r + s + (values[i] ?? ''), '') },
  };
  window.window = window;
  const context = vm.createContext(window);
  for (const file of files) vm.runInContext(fs.readFileSync(path.join(STATIC, file), 'utf8'), context, { filename: file });
  return context;
}

// Values built inside the vm context carry its own Array prototype.
const plain = value => JSON.parse(JSON.stringify(value));

const board = { id: 'blackboard_aaaaaaaaaaaa', kind: 'blackboard', primitive_kind: 'blackboard', config: {} };
const resident = { id: 'resident_aaaaaaaaaaaa', primitive_kind: 'agent' };
const effect = { id: 'effect_aaaaaaaaaaaa', primitive_kind: 'effect', config: {} };

test('the board offers one attach handle and the Resident has none', () => {
  const program = load('lineage_program.js').PortalLineageProgram;
  const boardPorts = program.portsForNode(board);
  assert.deepEqual(plain(boardPorts.map(port => [port.port_id, port.type, port.direction])), [['members', 'attach', 'out']]);
  assert.ok(!program.portsForNode(resident).some(port => port.type === 'attach'));
});

test('dragging the board handle onto a Resident builds a Resident -> board attach wire', () => {
  const program = load('lineage_program.js').PortalLineageProgram;
  const handle = program.portsForNode(board)[0];
  const [target] = program.connectionTargets(handle, resident);
  assert.equal(target.node_id, resident.id);
  assert.deepEqual(plain(program.connectionTargets(handle, effect)), []);
  assert.deepEqual(plain(program.connectionTargets(handle, board)), []);
  const request = program.buildWireSaveRequest({ lineage_id: 'lineage_aaaaaaaaaaaa' }, handle, target, { access: 'read' });
  assert.equal(request.kind, 'attach');
  assert.equal(request.source, resident.id);
  assert.equal(request.target, board.id);
  assert.equal(request.config.access, 'read');
  const existing = [{ id: 'wire_aaaaaaaaaaaa', kind: 'attach', source: resident.id, source_port: 'attach', target: board.id }];
  const again = program.validateConnection(handle, target, { existingWires: existing });
  assert.equal(again.ok, false);
  assert.equal(again.reason, 'duplicate');
});

test('an attach wire is projected with its access as the label', () => {
  const program = load('lineage_program.js').PortalLineageProgram;
  const graph = program.projectGraph({
    components: [board],
    wires: [{ id: 'wire_aaaaaaaaaaaa', kind: 'attach', source: resident.id, target: board.id, config: { access: 'write' } }],
  }, [resident]);
  assert.equal(graph.edges.length, 1);
  assert.equal(graph.edges[0].type, 'attach');
  assert.equal(graph.edges[0].display_label, '只写');
});

test('the graph keeps the board handle as a drawable out port', () => {
  const context = load('lineage_program.js', 'lineage_graph_ports.js');
  const program = context.PortalLineageProgram;
  const dependencies = Object.fromEntries(context.PortalHatgGraphPorts.dependencies.map(name => [name, undefined]));
  const nodeMap = new Map([[board.id, board]]);
  Object.assign(dependencies, {
    clamp: value => value, isWorkspaceNode: () => false, layers: {}, nodeHalfExtents: () => ({ x: 1, y: 1 }),
    nodeHasQuickFork: () => false, nodePosition: () => ({ x: 0, y: 0 }), portElements: () => [],
    primitiveNodeKind: node => program.primitiveKind(node), root: { dataset: {} },
    state: { nodeMap, model: { nodes: [board], edges: [], wireDraft: null } }, worldSize: { width: 100, height: 100 },
    options: { portsForNode: node => program.portsForNode(node) },
  });
  const ports = context.PortalHatgGraphPorts.createPorts(dependencies);
  const handle = ports.portsForNode(board);
  assert.equal(handle.length, 1);
  assert.equal(handle[0].type, 'attach');
  assert.ok(ports.normalizeWireDraft(handle[0]));
});
