const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {performance} = require('node:perf_hooks');

const html = fs.readFileSync(path.resolve(__dirname, '../../../knowledge_board_lan/knowledge_board.html'), 'utf8');
const start = html.indexOf('      function detailGraphRingPositions(');
const end = html.indexOf('      function detailGraphLinePosition(', start);
assert(start >= 0 && end > start);
const context = vm.createContext({});
vm.runInContext(html.slice(start, end), context);

// A normal two-hop knowledge item can have dozens of neighbours. Rendering
// must keep every node without monopolising the browser's main thread.
const nodes = [{kb_id: 'root', title: 'Root', depth: 0}];
for (let i = 0; i < 15; i++) nodes.push({kb_id: `a${i}`, title: `A${i}`, depth: 1});
for (let i = 0; i < 55; i++) nodes.push({kb_id: `b${i}`, title: `B${i}`, depth: 2});
const edges = [];
for (let i = 0; i < 15; i++) {
  edges.push({source: 'root', target: `a${i}`});
  for (let j = 0; j < 11; j++) edges.push({source: `a${i}`, target: `b${(i * 7 + j * 3) % 55}`});
}
const before = JSON.stringify({nodes, edges});
Object.assign(context, {nodes, edges});
const started = performance.now();
const layout = vm.runInContext('detailGraphBasePositions(nodes, edges)', context, {timeout: 1000});
const elapsed = performance.now() - started;
assert.equal(layout.positions.size, nodes.length);
for (const node of nodes) {
  const position = layout.positions.get(node.kb_id);
  assert(Number.isFinite(position.x) && Number.isFinite(position.y));
  assert(position.x >= 0 && position.x <= layout.size && position.y >= 0 && position.y <= layout.size);
}
assert.equal(JSON.stringify({nodes, edges}), before);
assert.equal(JSON.stringify([...layout.positions]), JSON.stringify([...vm.runInContext('detailGraphBasePositions(nodes, edges)', context, {timeout: 1000}).positions]));

// Keep the useful crossing reduction on small graphs, and manual positioning.
vm.runInContext(`
  const root = {kb_id:'r'};
  const first = [{kb_id:'a'}, {kb_id:'b'}, {kb_id:'c'}];
  const second = [{kb_id:'x'}, {kb_id:'y'}, {kb_id:'z'}];
  const links = [{source:'a',target:'z'}, {source:'b',target:'x'}, {source:'c',target:'y'}];
  const initial = detailGraphCrossingCount(root, first, second, links);
  const ordered = optimizeDetailGraphLayerOrder(root, first, second, links);
  if (detailGraphCrossingCount(root, ordered.first, ordered.second, links) > initial) throw Error('Crossings increased');
`, context, {timeout: 1000});
const manual = vm.runInContext("detailGraphPositions(nodes, edges, {root:{x:111,y:222}})", context, {timeout: 1000});
assert.equal(manual.positions.get('root').x, 111);
assert.equal(manual.positions.get('root').y, 222);
console.log(JSON.stringify({nodes: nodes.length, edges: edges.length, layout_ms: elapsed, passed: true}));
