import test from 'node:test';
import assert from 'node:assert/strict';
import { layoutScope, layoutRuntime } from '../src/architecture/layout.js';

const node = (id, parent = null, label = id) => ({ id: `module:${id}`, type: 'module', localId: id, label, data: { parent, responsibility: `${label}的职责。` } });
const root = node('worker', null, 'ExecutionWorker');
const adapter = node('execution.providers', 'worker', 'SessionManager 与 Provider Adapter');
const harness = node('worker.harness', 'worker', 'Harness 执行治理');
const data = { ...node('execution.agent-data', 'execution', 'AgentDataProjector'), path: ['ExecutionService', 'AgentDataProjector'] };
const relation = { id: 'runtime-content', title: 'Runtime 将执行内容交给 AgentDataProjector', kind: 'in_process_content_write',
  interaction: { direction: 'call', initiator_module_id: 'worker', conditions: ['在保存执行内容时'] },
  endpoints: [{ moduleId: 'worker', displayModuleId: 'worker', display: 'root_boundary_role', componentId: null, role: 'Runtime' },
    { moduleId: 'execution.agent-data', displayModuleId: 'execution.agent-data', display: 'shared_component_boundary', componentId: 'execution.agent-data', role: '内容投影' }] };
const model = { runtime: true, scopeRoot: 'worker', boundary: root, nodes: [adapter, harness], context: [data], componentRelations: [relation], protocols: [] };

test('scope lays out only direct children at readable widths, preserving a real root boundary', () => {
  for (const width of [390, 625, 945]) {
    const value = layoutScope({ ...model, nodes: [...model.nodes, data, node('grandchild', 'execution.providers')] }, { width });
    assert.deepEqual(value.visible, [adapter.id, harness.id]);
    assert(!value.positions['module:grandchild']);
    for (const id of value.visible) {
      const p = value.positions[id];
      assert(p.x - p.width / 2 >= 0 && p.x + p.width / 2 <= width);
      assert(p.y - p.height / 2 > value.boundary.headerBottom);
      assert(p.y + p.height / 2 < value.boundary.y + value.boundary.height);
      assert(!('clipped' in p));
    }
    assert(value.positions[root.id].boundary);
    assert(value.positions[data.id].context);
    assert(value.positions[data.id].y > value.boundary.y + value.boundary.height);
  }
});

test('leaf requires its authorized boundary and has no invented node or inherited protocol', () => {
  const leaf = { scopeRoot: 'worker.harness', boundary: harness, nodes: [], context: [], protocols: [], parentProtocols: [{ id: 'protocol:parent', label: '父层协议' }] };
  const value = layoutScope(leaf);
  assert.deepEqual(Object.keys(value.positions), [harness.id]);
  assert.equal(value.visible.length, 0); assert.equal(value.routes.length, 0); assert.equal(value.parentProtocols.length, 1);
  assert.throws(() => layoutScope({ ...leaf, boundary: null }), /real root/);
});

test('root protocols remain compact entries retaining their real participants, without child inheritance', () => {
  const protocol = { id: 'protocol:root', type: 'protocol', label: '资源访问', participants: [root.id, data.id], direction: 'unspecified' };
  const value = layoutScope({ ...model, protocols: [protocol] });
  assert.deepEqual(value.protocols[0].participants, [root.id, data.id]);
  assert.equal(value.protocols[0].direction, 'unspecified');
  assert(!value.routes.some(r => r.id === protocol.id));
  const protocolOnly = layoutScope({ ...model, protocols: [protocol], componentRelations: [] });
  assert.deepEqual(protocolOnly.context, []);
  assert(!protocolOnly.positions[data.id]);
  assert.equal(protocolOnly.protocols.length, 1);
});

test('formal component relation preserves Runtime boundary and shared ownership, local direction only', () => {
  const value = layoutScope(model), route = value.routes[0];
  assert.equal(route.id, 'relation:runtime-content');
  assert.deepEqual(route.routes.map(r => r.arrow), ['end', 'start']);
  assert.equal(route.routes[0].endpoint.componentId, null);
  assert.equal(route.routes[1].endpoint.display, 'shared_component_boundary');
  assert(!value.visible.includes(data.id));
  assert(!Object.keys(value.positions).some(id => id === 'module:runtime'));
  const consistency = layoutScope({ ...model, componentRelations: [{ ...relation, kind: 'shared_transaction', interaction: { direction: 'undirected_consistency' } }] });
  assert(consistency.routes[0].routes.every(r => !r.arrow));
  assert(consistency.routes[0].meaning.includes('无调用方向'));
});

test('authorized unrelated context never enlarges the local collaboration canvas', () => {
  const base = layoutScope(model);
  const next = layoutScope({ ...model, context: [...model.context, ...Array.from({ length: 50 }, (_, i) => node(`external${i}`, null))] });
  assert.deepEqual(next, base);
  for (const row of next.routes) assert(row.labelWidth < next.width / 2);
  assert(next.height < 500);
});

test('missing or filtered endpoint cannot create a visible relation, object or count', () => {
  const value = layoutScope({ ...model, context: [] });
  assert.equal(value.routes.length, 0); assert(!value.positions[data.id]);
  assert.deepEqual(value.visible, [adapter.id, harness.id]);
  const fabricatedChild = { ...relation, endpoints: relation.endpoints.map(e => ({ ...e, display: 'direct_component' })) };
  assert.equal(layoutScope({ ...model, componentRelations: [fabricatedChild] }).routes.length, 0);
});

test('selected and highlighted identities never change scope geometry or implicitly fit', () => {
  const base = layoutScope(model);
  assert.deepEqual(layoutScope({ ...model, selected: data.id, highlighted: [harness.id] }), base);
  const overview = layoutRuntime([root, adapter, harness]);
  assert(!overview.positions[adapter.id]); assert.deepEqual(overview.components, Object.create(null));
});

test('large authorized scope is bounded and preserves all direct children without hidden scroll rows', t => {
  const nodes = Array.from({ length: 500 }, (_, i) => node(`worker.c${i}`, 'worker', `组件 ${i}`));
  const start = performance.now(), value = layoutScope({ ...model, nodes, context: [], componentRelations: [] });
  const elapsed = performance.now() - start;
  t.diagnostic(`500 scoped components: ${elapsed.toFixed(1)}ms pure layout`);
  assert.equal(value.visible.length, 500); assert.equal(Object.keys(value.positions).length, 501);
  assert(elapsed < 1000); assert(Object.values(value.positions).every(p => Number.isFinite(p.x) && Number.isFinite(p.y)));
});
