import { test } from 'node:test';
import assert from 'node:assert/strict';
import { performance } from 'node:perf_hooks';
import { layoutGraph, structureKey, structuralGraph, parentOutlines, edgeEndpoints, spatialNeighbor, RUNTIME_CORE, layoutRuntime, runtimeProtocolGeometry, wrapLabel } from '../src/architecture/layout.js';

const nodes = [
  { id: 'resources', type: 'module', label: '资源' },
  { id: 'projection', type: 'module', label: '投影' },
  { id: 'read', type: 'protocol', label: '读取资源' },
  { id: 'sdk', type: 'module', parent: 'projection', label: 'SDK' },
];
const edges = [
  { id: 'read-resources', source: 'read', target: 'resources', type: 'participation' },
  { id: 'read-projection', source: 'read', target: 'projection', type: 'participation' },
  { id: 'projection-sdk', source: 'projection', target: 'sdk', type: 'membership' },
];

test('layout and structural key are deterministic, input order and presentation do not reorder nodes', () => {
  const before = structuredClone({ nodes, edges });
  const normal = layoutGraph(nodes, edges);
  const reversed = layoutGraph([...nodes].reverse(), [...edges].reverse());
  assert.deepEqual(normal, reversed);
  assert.deepEqual({ nodes, edges }, before);
  assert.equal(structureKey(nodes, edges), structureKey(nodes.map(n => ({ ...n, label: 'changed', status: 'historical', data: { secret: 'not transported' } })).reverse(), [...edges].reverse()));
  for (const p of Object.values(normal.positions)) assert.ok([p.x, p.y, p.width, p.height].every(Number.isFinite));
});

test('undirected participants produce the same layout regardless of source direction', () => {
  const flipped = edges.map(e => e.type === 'participation' ? { ...e, source: e.target, target: e.source } : e);
  assert.deepEqual(layoutGraph(nodes, edges), layoutGraph(nodes, flipped));
});

test('worker structural input strips arbitrary contents and removes dangling endpoints', () => {
  const data = structuralGraph([...nodes, { ...nodes[0], data: { sdk: 'must not cross worker boundary' } }], [...edges,
    { id: 'missing', source: 'read', target: 'unreadable' }, { id: 'loop', source: 'read', target: 'read' }]);
  assert.equal(data.nodes.length, nodes.length);
  assert.equal(data.edges.length, edges.length);
  assert.deepEqual(Object.keys(data.nodes[0]), ['id', 'type', 'parent']);
});

test('surviving nodes keep positions across snapshots and new nodes avoid overlap', () => {
  const first = layoutGraph(nodes, edges);
  const next = layoutGraph([...nodes, { id: 'new', type: 'design', parent: 'sdk' }], [...edges, { id: 'new-link', source: 'sdk', target: 'new', type: 'association' }], { previous: first.positions });
  for (const n of nodes) assert.deepEqual(next.positions[n.id], first.positions[n.id]);
  const added = next.positions.new;
  for (const p of Object.values(first.positions)) assert.ok(Math.abs(p.x - added.x) >= (p.width + added.width) / 2 || Math.abs(p.y - added.y) >= (p.height + added.height) / 2);
  const restored = layoutGraph(nodes, edges, { previous: new Map(Object.entries(next.positions)) });
  assert.deepEqual({ ...restored.positions }, Object.fromEntries(Object.entries(next.positions).filter(([id]) => id !== 'new')));
});

test('parent contours include descendants and cyclic metadata terminates safely', () => {
  const result = layoutGraph(nodes, edges);
  const outline = result.outlines.find(n => n.id === 'projection');
  for (const key of ['projection', 'sdk']) {
    const p = result.positions[key];
    assert.ok(outline.x <= p.x - p.width / 2 && outline.x + outline.width >= p.x + p.width / 2);
    assert.ok(outline.y <= p.y - p.height / 2 && outline.y + outline.height >= p.y + p.height / 2);
  }
  assert.equal(parentOutlines([{ id: 'a', parent: 'b' }, { id: 'b', parent: 'a' }], { a: { x: 0, y: 0, width: 10, height: 10 }, b: { x: 20, y: 20, width: 10, height: 10 } }).length, 2);
});

test('connector endpoints clip rectangle and diamond, including axis-aligned edges', () => {
  const a = { x: 0, y: 0, width: 100, height: 60 };
  assert.deepEqual(edgeEndpoints(a, { ...a, x: 200 }, 'module', 'module'), [{ x: 50, y: 0 }, { x: 150, y: 0 }]);
  assert.deepEqual(edgeEndpoints(a, { ...a, y: 100 }, 'module', 'protocol'), [{ x: 0, y: 30 }, { x: 0, y: 82 }]);
  const [diamond] = edgeEndpoints(a, { ...a, x: 100, y: 100 }, 'protocol', 'module');
  assert.equal(Math.abs(diamond.x) + Math.abs(diamond.y), 18);
  assert.deepEqual(edgeEndpoints(a, a, 'module', 'protocol'), [{ x: 0, y: 0 }, { x: 0, y: 0 }]);
});

test('keyboard navigation uses spatial direction rather than data order', () => {
  const p = { center: { x: 0, y: 0 }, farRight: { x: 300, y: 0 }, right: { x: 90, y: 10 }, up: { x: 0, y: -80 }, diagonal: { x: 30, y: 200 } };
  assert.equal(spatialNeighbor('center', 'ArrowRight', p), 'right');
  assert.equal(spatialNeighbor('center', 'ArrowUp', p), 'up');
  assert.equal(spatialNeighbor('center', 'ArrowLeft', p), null);
});

test('empty and disconnected structures have finite geometry', () => {
  assert.deepEqual(layoutGraph().bounds, { x: 0, y: 0, width: 1, height: 1 });
  const result = layoutGraph([{ id: '__proto__', type: 'module' }, { id: 'orphan', parent: 'absent' }], []);
  assert.equal(Object.keys(result.positions).length, 2);
  assert.ok(result.bounds.width > 0 && Number.isFinite(result.bounds.height));
});

test('500-node / 1000-edge layout benchmark produces complete finite geometry', t => {
  const manyNodes = Array.from({ length: 500 }, (_, i) => ({ id: `n${String(i).padStart(3, '0')}`, type: i % 5 ? 'module' : 'protocol' }));
  const manyEdges = Array.from({ length: 1000 }, (_, i) => {
    const source = i % 500, target = (source + (i < 500 ? 1 : 13)) % 500;
    return { id: `e${i}`, source: manyNodes[source].id, target: manyNodes[target].id, type: 'participation' };
  });
  const started = performance.now();
  const result = layoutGraph(manyNodes, manyEdges);
  const elapsed = performance.now() - started;
  t.diagnostic(`500 nodes / 1000 edges: ${elapsed.toFixed(1)}ms (layout only; production delegates this work to Worker)`);
  assert.equal(Object.keys(result.positions).length, 500);
  for (const p of Object.values(result.positions)) assert.ok(Number.isFinite(p.x) && Number.isFinite(p.y));
  assert.ok(elapsed < 30000, 'bounded local smoke benchmark, not an interaction latency claim');
});

test('semantic overview retains every module and only the named protocol at readable widths', async () => {
  const { layoutOverview } = await import('../src/architecture/layout.js');
  const modules=Array.from({length:8},(_,i)=>({id:'m'+i,type:'module'}));
  const protocols=[{id:'p1',type:'protocol'},{id:'p2',type:'protocol'}];
  const edges=modules.slice(0,6).map(m=>({source:m.id,target:'p1',type:'participation'}));
  for(const width of [585,905]){
    const value=layoutOverview([...modules,...protocols],edges,width,'p1');
    assert.equal(Object.keys(value.positions).length,9);assert(!value.positions.p2);
    for(const m of modules){const p=value.positions[m.id];assert(p.x-p.width/2>=0);assert(p.x+p.width/2<width);assert(p.height>=64);}
    assert(value.positions.p1.x-value.positions.p1.width/2>value.positions.m0.x+value.positions.m0.width/2);
    assert(value.height >= value.positions.m7.y + value.positions.m7.height / 2);assert(value.positions.p1.y<300);
    for(let i=1;i<modules.length;i++)assert(value.positions['m'+i].y-value.positions['m'+(i-1)].y>value.positions['m'+i].height);
    const second=layoutOverview([...modules,...protocols],edges,width,'p2');
    for(const m of modules)assert.deepEqual(second.positions[m.id],value.positions[m.id]);
    assert(second.positions.p2&&!second.positions.p1);
  }
});

const runtimeNodes = [...RUNTIME_CORE, 'router', 'notifications', 'delivery', 'engineering'].map(localId => ({
  id: 'module:' + localId, localId, type: 'module', label: localId, data: { level: 'runtime' },
}));

test('runtime layers keep frontend, entry, services and storage visible at native 625/945 width', () => {
  for (const width of [585,625,905,945]) {
    const value = layoutRuntime(runtimeNodes, { width });
    assert.equal(value.visible.length, RUNTIME_CORE.length);
    for (const id of value.visible) {
      const p = value.positions[id];
      assert(p.x - p.width / 2 >= 0 && p.x + p.width / 2 <= width);
      assert(p.y - p.height / 2 >= 0 && p.y + p.height / 2 < 551);
    }
    assert(value.positions['module:interface'].y < value.positions['module:web'].y);
    assert(value.positions['module:web'].y < value.positions['module:resources'].y);
    assert(value.positions['module:worker'].y < value.positions['module:postgres'].y);
    assert(!value.positions['module:router']);
  }
});

test('overview stays runtime-only when component metadata is present; parent coordinates remain stable', () => {
  const base = layoutRuntime(runtimeNodes, {width:625});
  const children = [
    {id:'module:execution.providers',localId:'execution.providers',type:'module',label:'SessionManager',data:{level:'component',parent:'worker'}},
    {id:'module:execution.scheduling',localId:'execution.scheduling',type:'module',label:'Scheduler',data:{level:'component',parent:'execution'}},
  ];
  const expanded = layoutRuntime([...runtimeNodes,...children], {width:625,showSupporting:true});
  for(const id of base.visible) assert.deepEqual(expanded.positions[id],base.positions[id]);
  assert(!expanded.positions['module:execution.providers']);
  assert(!expanded.positions['module:execution.scheduling']);
  assert(expanded.positions['module:router']);
  const filtered=layoutRuntime([...runtimeNodes.filter(n=>n.localId!=='worker'),...children],{width:625});
  assert(!filtered.positions['module:execution.providers'],'no invented or orphaned parent');
});

test('protocol geometry is one named hyperedge: overview subset, selected complete visible set, never all pairs', () => {
  const protocol={id:'protocol:signed-rpc',localId:'signed-rpc',label:'服务间签名 RPC 与错误协议',participants:['module:web','module:resources','module:lineage','module:execution','module:worker']};
  const layout=layoutRuntime(runtimeNodes,{width:625});
  const [overview]=runtimeProtocolGeometry([protocol],layout);
  assert(overview.partial);assert.equal(overview.routes.length,4);assert.equal(overview.total,5);
  assert.deepEqual(new Set(overview.routes.map(r=>r.participant)),new Set(overview.participants));
  const [full]=runtimeProtocolGeometry([protocol],layout,protocol.id);
  assert.equal(full.routes.length,5);assert.equal(full.partial,false);
  for(const route of full.routes) {
    assert.deepEqual(route.points.at(-1),full.hub);
    assert(!('source' in route) && !('target' in route),'a participation route does not claim caller/callee');
  }
  assert(!Object.keys(layout.positions).some(id=>id.startsWith('protocol:')));
  const missing=layoutRuntime(runtimeNodes.filter(n=>n.localId!=='worker'),{width:625});
  const [partial]=runtimeProtocolGeometry([protocol],missing,protocol.id);
  assert(partial.partial);assert.equal(partial.routes.length,4);
  assert(!partial.routes.some(r=>r.participant==='module:worker'));
});

test('complete labels wrap by English word and preserve Chinese identity without ellipsis', () => {
  const title='共享文件区与 Artifact Store';
  const lines=wrapLabel(title,155,15);
  assert(lines.some(line=>line.includes('Artifact')));
  assert(!lines.some(line=>line.includes('Artif')&&!line.includes('Artifact')));
  assert.equal(lines.join('').replace(/\s/g,''),title.replace(/\s/g,''));
  assert(!lines.join('').includes('…'));
  assert.deepEqual(wrapLabel('ResourceService',100,15),['Resource','Service']);
});

test('overview ignores large child collections; children use their own scope canvas', () => {
  const children=Array.from({length:500},(_,i)=>({id:'module:c'+i,type:'module',label:'component '+i,data:{parent:'worker',level:'component'}}));
  const started=performance.now(),value=layoutRuntime([...runtimeNodes,...children],{width:625});
  assert(!value.positions['module:c0']);
  assert.equal(value.visible.length,10);assert.equal(value.height,layoutRuntime(runtimeNodes,{width:625}).height);
  for (const id of value.visible) assert.deepEqual(value.positions[id],layoutRuntime(runtimeNodes,{width:625}).positions[id]);
  assert(performance.now()-started<5000);
});
