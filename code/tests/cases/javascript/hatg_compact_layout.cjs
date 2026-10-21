const assert = require('node:assert/strict');
const test = require('node:test');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const context = vm.createContext({ window: { PortalGraphGeometry: {}, PortalI18n: { t: text => text } } });
vm.runInContext(fs.readFileSync(path.resolve(__dirname, '../../../management_portal/static/lineage_graph.js'), 'utf8'), context);
const layout = context.window.PortalHatgGraph.computeBranchAwareLayout;
const nodes = Array.from({ length: 12 }, (_, index) => ({ id: String(index), x: index * 12, y: index * 8 }));
const options = { horizontalGap: 14.53, verticalGap: 11.78, nodeWidth: 12.03, nodeHeight: 10.44, componentGap: 3, componentColumnGap: 3, componentWrapSpan: 0, feedbackEdgeTypes: ['watch', 'target'] };
const edge = (source, target, type = 'inheritance') => ({ source: String(source), target: String(target), type });
const dimensions = positions => {
  const values = [...positions.values()];
  return { width: Math.max(...values.map(p => p.x)) - Math.min(...values.map(p => p.x)), height: Math.max(...values.map(p => p.y)) - Math.min(...values.map(p => p.y)) };
};
function noOverlap(positions) {
  assert.equal(positions.size, nodes.length);
  const values = [...positions.values()];
  for (const p of values) assert.ok(Number.isFinite(p.x) && Number.isFinite(p.y));
  for (let i = 0; i < values.length; i++) for (let j = i + 1; j < values.length; j++) {
    assert.ok(Math.abs(values[i].x - values[j].x) >= options.nodeWidth - .02 || Math.abs(values[i].y - values[j].y) >= options.nodeHeight - .02);
  }
}

test('independent components follow canvas aspect ratio and remain deterministic', () => {
  const wide = layout(nodes, [], { ...options, componentAspectRatio: 2.5 });
  const narrow = layout(nodes, [], { ...options, componentAspectRatio: .4 });
  noOverlap(wide); noOverlap(narrow);
  assert.ok(dimensions(wide).width > dimensions(narrow).width);
  assert.ok(dimensions(wide).height < dimensions(narrow).height);
  assert.equal(JSON.stringify([...wide]), JSON.stringify([...layout(nodes, [], { ...options, componentAspectRatio: 2.5 })]));
});

for (const [name, edges] of [
  ['branches', [edge(0, 1), edge(0, 2), edge(1, 3), edge(1, 4), edge(2, 5), edge(2, 6)]],
  ['convergence and feedback', [edge(0, 1), edge(0, 2), edge(1, 3), edge(2, 3), edge(3, 0, 'watch'), edge(4, 5)]],
  ['cycles', [edge(0, 1), edge(1, 2), edge(2, 0), edge(2, 3), edge(4, 5), edge(5, 4)]],
]) test(`compact ${name} keeps nodes separated and topology intact`, () => {
  const source = JSON.stringify({ nodes, edges });
  const result = layout(nodes, edges, { ...options, componentAspectRatio: 1.4 });
  noOverlap(result);
  assert.equal(JSON.stringify({ nodes, edges }), source);
});

test('Leader stays beside its connected work in compact mode', () => {
  const result = layout(nodes, [edge(0, 1), edge(1, 2)], { ...options, leaderId: '0', componentAspectRatio: 1.4 });
  noOverlap(result);
  assert.ok([...result].filter(([id]) => id !== '0').every(([, p]) => p.x > result.get('0').x));
  assert.equal(result.get('0').y, result.get('1').y);
});
