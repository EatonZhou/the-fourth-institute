// @design decision_4adcc1ff2f2c decision_8838269736d3 decision_7ef392b5f60d
const assert = require('node:assert/strict');
global.window = {};
require('../../../management_portal/static/hatg_workspace_projection.js');
const { project } = window.PortalHatgWorkspaceProjection;
const residents = [
  { id: 'resident_a', title: 'Alpha', x: 12, y: 20 },
  { id: 'resident_b', title: 'Beta', x: 40, y: 50 },
  { id: 'resident_c', title: 'No registered workspace', x: 70, y: 80 },
];
const root = (workspace_id, path) => ({ workspace_id, path, root_kind: 'workspace', label: path.split('/').at(-1) });
const shared = root('workspace_shared', '/shared');
const bindings = new Map([
  ['resident_a', { roots: [root('workspace_a', '/new-a'), shared, shared] }],
  ['resident_b', { roots: [root('workspace_b', '/b'), shared] }],
  ['resident_c', { roots: [{ directory_id: 'resident_folder', root_kind: 'resident_folder', path: '/resident-c' }] }],
]);
const relation = { id: 'handoff_ab', type: 'handoff', source: 'resident_a', target: 'resident_b', status: 'ready', items: [
  { item_id: 'copy', source: { workspace_id: 'workspace_a', path: '/old-a' }, target: {}, result: { target_workspace_id: 'workspace_b', target_path: '/b' } },
  { item_id: 'shared', source: { workspace_id: 'workspace_shared' }, target: {}, transfer: { mode: 'direct' } },
  { item_id: 'retired', source: { workspace_id: 'workspace_old', path: '/shared' }, target: { workspace_id: 'workspace_b' } },
  { item_id: 'unknown', source: { workspace_id: 'workspace_a' }, target: { path: '/not-registered' } },
] };
const snapshot = JSON.stringify({ residents, relation, bindings: [...bindings] });
const graph = project(residents, [relation], bindings);
const cards = graph.nodes.filter(node => node.kind === 'workspace');
assert.deepEqual(cards.map(node => node.workspace_id).sort(), ['workspace_a', 'workspace_b', 'workspace_shared']);
assert.deepEqual(cards.find(node => node.workspace_id === 'workspace_shared').member_ids, ['resident_a', 'resident_b']);
assert.equal(cards.find(node => node.workspace_id === 'workspace_a').workspace_path, '/new-a');
assert.equal(graph.nodes.filter(node => node.kind === 'workspace_resident').length, 3);
assert.equal(graph.edges.filter(edge => edge.type === 'workspace_binding').length, 4);
const handoffs = graph.edges.filter(edge => edge.type === 'handoff');
assert.equal(handoffs.length, 2);
assert.deepEqual(handoffs.map(edge => [edge.source, edge.target]), [
  ['workspace::workspace_a', 'workspace::workspace_b'],
  ['workspace::workspace_shared', 'workspace::workspace_shared'],
]);
assert.ok(handoffs.every(edge => edge.original_edge_id === 'handoff_ab' && edge.interaction_edge_id === 'handoff_ab'));
assert.equal(new Set(graph.edges.map(edge => edge.id)).size, graph.edges.length);
assert.equal(JSON.stringify({ residents, relation, bindings: [...bindings] }), snapshot, 'projection cannot mutate Context coordinates or bindings');
assert.equal(project(residents, [], bindings).edges.filter(edge => edge.type === 'handoff').length, 0);
assert.equal(project(residents, [{ ...relation, items: [] }, { ...relation, type: 'inheritance' }], bindings).edges.filter(edge => edge.type === 'handoff').length, 0);
const pathOnly = { ...relation, items: [{ source: { path: '/new-a' }, target: { path: '/b' } }] };
assert.equal(project(residents, [pathOnly], bindings).edges.filter(edge => edge.type === 'handoff').length, 1);
assert.equal(project(residents, [{ ...relation, draft_preview: true }], bindings).edges.filter(edge => edge.type === 'handoff').length, 0);
console.log('Workspace identity, all bindings, item-level Handoff endpoints, stale-ID exclusion and Context immutability passed.');
