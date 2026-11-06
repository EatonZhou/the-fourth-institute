const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const context = {window: {PortalI18n: {t: value => value}}, structuredClone};
vm.runInNewContext(fs.readFileSync(path.resolve(__dirname,
  '../../../management_portal/static/lineage_program.js'), 'utf8'), context);
const program = context.window.PortalLineageProgram;
const leader = {id: 'resident_aaaaaaaaaaaa', title: 'Ordinary Leader'};
const dedicated = {id: 'resident_bbbbbbbbbbbb', operator_executor: {component_id: 'reducer_cccccccccccc'}};
const components = [
  {id: 'filter_aaaaaaaaaaaa', kind: 'filter', config: {operator_implementation: 'resident', resident_executor_id: leader.id}},
  {id: 'selector_bbbbbbbbbbbb', kind: 'selector', config: {operator_implementation: 'resident', resident_executor_id: leader.id}},
  {id: 'reducer_cccccccccccc', kind: 'reducer', config: {operator_implementation: 'resident', resident_executor_id: dedicated.id}},
  {id: 'event_source_dddddddddddd', kind: 'event_source', config: {event_type: 'resident.work_finished'}},
];
const watch = {id: 'wire_eeeeeeeeeeee', kind: 'watch', source: components[3].id, source_port: 'watch', target: leader.id};
for (const retained of [components, components.slice(1)]) {
  const graph = program.projectGraph({components: retained, wires: [watch]}, [leader, dedicated]);
  assert.equal(graph.nodes.filter(node => node.id === leader.id).length, 1,
    'a shared ordinary member remains a graph node, including after one operator is deleted');
  assert.equal(graph.nodes.find(node => node.id === leader.id).primitive_kind, 'agent');
  assert.equal(graph.nodes.some(node => node.id === dedicated.id), false,
    'dedicated executors continue to be represented by their operator');
  assert.equal(graph.edges.some(edge => edge.id === watch.id && edge.target === leader.id), true,
    'passive references to the ordinary Leader retain their target');
}
assert.equal(leader.operator_executor, undefined, 'projection does not assign dedicated ownership');
console.log('Shared ordinary operator members retain graph identity and references; dedicated executors stay hidden');
