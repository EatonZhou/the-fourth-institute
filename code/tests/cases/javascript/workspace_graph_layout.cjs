const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const repo = path.resolve(__dirname, '../../..');
const ctx = vm.createContext({ window: { PortalGraphGeometry: {}, PortalI18n: { t: x => x } } });
for (const name of ['lineage_graph_layout.js', 'lineage_graph.js']) {
  vm.runInContext(fs.readFileSync(path.join(repo, 'management_portal/static', name), 'utf8'), ctx);
}
const layout = ctx.window.PortalHatgGraph.computeWorkspaceGraphLayout;
function check(name, nodes, edges) {
  const input = JSON.stringify([nodes, edges]);
  const started = performance.now(), positions = layout(nodes, edges);
  const elapsed = performance.now() - started;
  const reversed = layout([...nodes].reverse(), [...edges].reverse());
  assert.equal(JSON.stringify([nodes, edges]), input, 'Source objects must not change');
  assert.equal(positions.size, nodes.length);
  for (const [i, n] of nodes.entries()) {
    const a = positions.get(n.id);
    assert(Number.isFinite(a.x) && Number.isFinite(a.y));
    assert.deepEqual(a, reversed.get(n.id), 'Stable positions regardless of input ordering');
    for (const bNode of nodes.slice(i + 1)) {
      const b = positions.get(bNode.id);
      assert(Math.abs(a.x - b.x) >= 18 || Math.abs(a.y - b.y) >= 14, `No overlapping labels: ${n.id}/${bNode.id}`);
    }
  }
  const xs = [...positions.values()].map(p => p.x), ys = [...positions.values()].map(p => p.y);
  const width = Math.max(...xs) - Math.min(...xs) + 18, height = Math.max(...ys) - Math.min(...ys) + 14;
  const aspect = width * 1280 / (height * 900);
  console.log(JSON.stringify({ name, nodes: nodes.length, width, height, aspect, elapsed }));
  if (nodes.length >= 20) assert(aspect >= 0.55 && aspect <= 2.8, `${name}: graph must use both screen dimensions, aspect=${aspect}`);
  assert(elapsed < 500, `${name}: bounded layout computation`);
  return positions;
}
function pairGraph(count) {
  const nodes = [{id:'shared',kind:'workspace'}], edges=[];
  for(let i=0;i<count;i++) {
    nodes.push({id:`w${i}`,kind:'workspace'},{id:`r${i}`,kind:'workspace_resident'});
    edges.push({id:`local${i}`,type:'workspace_binding',source:`w${i}`,target:`r${i}`},
      {id:`shared${i}`,type:'workspace_binding',source:'shared',target:`r${i}`});
  }
  return {nodes,edges};
}
for(const count of [3,72,200]) {
  const {nodes,edges}=pairGraph(count), positions=check(`private-plus-shared-${count}`,nodes,edges);
  for(let i=0;i<count;i++) {
    const a=positions.get(`w${i}`), b=positions.get(`r${i}`);
    assert(Math.hypot(a.x-b.x,a.y-b.y)<35,'Resident remains near its specific Workspace');
  }
}
const many = pairGraph(72);
check('one-shared-many-residents', many.nodes.filter(n=>n.id==='shared'||n.kind==='workspace_resident'), many.edges.filter(e=>e.source==='shared'));
check('unbound',many.nodes,[]);
check('handoff-chain-and-cycle',many.nodes,[...many.edges,...Array.from({length:71},(_,i)=>({id:'h'+i,type:'handoff',source:'w'+i,target:'w'+(i+1)})),{id:'cycle',type:'handoff',source:'w71',target:'w0'}]);
assert.equal(layout([],[]).size,0);
if(process.argv[2]) {
  const data=JSON.parse(fs.readFileSync(process.argv[2],'utf8'));
  vm.runInContext(fs.readFileSync(path.join(repo,'management_portal/static/hatg_workspace_projection.js'),'utf8'),ctx);
  const graph=ctx.window.PortalHatgWorkspaceProjection.project(data.residents,data.relations,new Map(data.bindings));
  check('captured-lineage',graph.nodes,graph.edges);
}
