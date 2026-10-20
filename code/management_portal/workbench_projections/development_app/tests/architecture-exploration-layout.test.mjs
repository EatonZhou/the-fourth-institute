import test from 'node:test';
import assert from 'node:assert/strict';
import { layoutExploration, intersects, initialGraphCamera, wrapGraphText, readingGraphCamera } from '../src/architecture/exploration-layout.js';

const object = (id, extra = {}) => ({ id, kind: 'module', title: id, responsibility: '合成对象用于验证图的几何合同', child_ids: [], mechanism_ids: [], host_ids: [], ...extra });
function sample() {
  return {
    root: object('scope', { title: '合成范围', child_ids: ['a', 'b', 'c', 'd'] }),
    children: ['a', 'b', 'c', 'd'].map(id => object(id)),
    boundaryEndpoints: [object('outside', { title: '范围外真实协作者' })],
    internalRelations: [
      { id: 'ab', from: 'a', to: 'b', label: '正常请求从实际发起方交给实际接收方', kind: 'calls' },
      { id: 'ac', from: 'a', to: 'c', label: '独立控制请求', kind: 'interrupts' },
      { id: 'bd', from: 'b', to: 'd', label: '写入当前观察', kind: 'writes' },
      { id: 'cb', from: 'c', to: 'b', label: '回传通知', kind: 'emits' },
    ],
    boundaryRelations: [{ id: 'dx', from: 'd', to: 'outside', label: '跨越当前组成边界', kind: 'submits' }],
  };
}
const segmentHits = (a, b, rect) => a.x === b.x
  ? a.x > rect.x + .1 && a.x < rect.x + rect.width - .1 && Math.max(a.y, b.y) > rect.y + .1 && Math.min(a.y, b.y) < rect.y + rect.height - .1
  : a.y > rect.y + .1 && a.y < rect.y + rect.height - .1 && Math.max(a.x, b.x) > rect.x + .1 && Math.min(a.x, b.x) < rect.x + rect.width - .1;

test('resource callers retain their actual targets without pushing physical callers past port peers', () => {
 const callers=['web','caller-a','caller-b','caller-c','caller-d'];
 const content={root:object('resources'),children:[object('resources.access'),object('resources.types')],
  boundaryEndpoints:callers.map(id=>object(id)),
  internalRelations:[{id:'dispatch',from:'resources.access',to:'resources.types',label:'类型分派'}],
  boundaryRelations:callers.map(id=>({id:'read-'+id,from:id,to:'resources.access',label:'按当前 actor 读取资源'}))};
 const graph=layoutExploration(content,{width:734}),all=[graph.root,...graph.nodes,...graph.context];
 assert.equal(graph.context.length,callers.length);assert.equal(graph.nodes.length,2);
 for(let i=0;i<all.length;i++)assert(all.slice(i+1).every(b=>!intersects(all[i],b)),`${all[i].id} overlaps another object`);
 const web=graph.context.find(n=>n.id==='web'),ports=graph.edges.filter(e=>e.presentation==='port');
 assert.equal(ports.length,4);
 for(const edge of ports){
  const source=graph.context.find(n=>n.id===edge.from);assert(source.y>web.y+web.height);
  assert.equal(edge.to,'resources.access');assert(edge.labelBox.x>=source.x&&edge.labelBox.y>=source.y);
  assert(edge.labelBox.y+edge.labelBox.height<=source.y+source.height);
 }
 assert(graph.edges.some(e=>e.from==='web'&&e.to==='resources.access'&&e.presentation==='path'));
});

test('625/945/1440 keep all endpoints and complete captions within graph bounds', () => {
  for (const width of [625, 945, 1440]) {
    const input = sample(), before = structuredClone(input), graph = layoutExploration(input, { width });
    const boxes = [graph.root, ...graph.nodes, ...graph.context];
    assert.deepEqual(input, before);
    assert.equal(graph.edges.length, 5);
    for (const box of [...boxes, ...graph.edges.map(e => e.labelBox)]) {
      assert.ok(box.x >= 0 && box.y >= 0);
      assert.ok(box.x + box.width <= graph.width, JSON.stringify({ width, box }));
      assert.ok(box.y + box.height <= graph.height);
    }
    for (const edge of graph.edges) assert.equal(edge.labelBox.lines.join(''), edge.readingLabel);
  }
});

test('captions never cover objects or other captions and routes do not cross either', () => {
  for (const width of [625, 945, 1440]) {
    const graph = layoutExploration(sample(), { width });
    const boxes = [graph.root, ...graph.nodes, ...graph.context], labels = graph.edges.map(e => e.labelBox);
    labels.forEach((label, i) => {
      assert.ok(boxes.every(box => !intersects(box, label)), `label ${i} at ${width}`);
      assert.ok(labels.slice(i + 1).every(other => !intersects(other, label)));
    });
    for (const edge of graph.edges) for (const path of edge.paths) for (let i = 1; i < path.length; i++) {
      assert.ok(path[i - 1].x === path[i].x || path[i - 1].y === path[i].y, 'orthogonal segment');
      for (const box of [...boxes, ...labels]) assert.ok(!segmentHits(path[i - 1], path[i], box), `${width}/${edge.id} crosses ${box.id || 'label'}`);
    }
  }
});

test('boundaries and hosting never become invented children or relations', () => {
  const p = sample(); p.children[0].host_ids = ['outside'];
  const graph = layoutExploration(p);
  assert.deepEqual(new Set(graph.nodes.map(n => n.id)), new Set(['a', 'b', 'c', 'd']));
  assert.deepEqual(graph.context.map(n => n.id), ['outside']);
  assert.match(graph.nodes.find(n => n.id === 'a').hosting.join(''), /承载：范围外真实协作者/);
  assert.deepEqual(graph.edges.map(e => [e.id, e.from, e.to]), [...p.internalRelations, ...p.boundaryRelations].map(e => [e.id, e.from, e.to]));
});

test('self loops, reverse edges and duplicate endpoint pairs preserve direction and identity', () => {
  const p = sample(); p.internalRelations.push(
    { id: 'ba', from: 'b', to: 'a', label: '反向回复', kind: 'emits' },
    { id: 'aa', from: 'a', to: 'a', label: '本地更新', kind: 'writes' },
    { id: 'ab2', from: 'a', to: 'b', label: '另一次协议操作', kind: 'observes' });
  const graph = layoutExploration(p, { width: 625 });
  for (const e of p.internalRelations) { const visual=graph.edges.find(r=>r.memberIds.includes(e.id));assert.equal(visual.from,e.from);assert.equal(visual.to,e.to); }
  assert.equal(new Set(graph.edges.flatMap(e => e.memberIds)).size, 8);
  assert.equal(graph.edges.some(e=>e.bidirectional),false);
});

test('leaf scopes retain their real root and do not fabricate children', () => {
  const p = { root: object('leaf', { mechanism_ids: ['actual-mechanism'] }), children: [], boundaryEndpoints: [], internalRelations: [], boundaryRelations: [] };
  const g = layoutExploration(p); assert.equal(g.root.id, 'leaf'); assert.equal(g.nodes.length, 0); assert.equal(g.edges.length, 0);
});

test('unresolved endpoints fail instead of turning a hidden ID into a diagram object', () => {
  const p = sample(); p.boundaryEndpoints = [];
  assert.throws(() => layoutExploration(p), /Missing authorized endpoint/);
});

test('layout is deterministic across re-entry and fits readable width without shrinking a tall graph', () => {
  const first = layoutExploration(sample(), { width: 625 });
  assert.deepEqual(first, layoutExploration(sample(), { width: 625 }));
  const camera = readingGraphCamera({ width: 625, height: 3000 }, { width: 625, height: 500 });
  assert.ok(camera.k >= .85); assert.equal(camera.y, 12);
});

test('Unicode and unbroken identifiers wrap without dropping their content', () => {
  for (const value of ['ExecutionWorker领取与控制循环', '一个很长的协议标签不应在左右两侧被截断', 'VeryLongIdentifierWithoutAnySpaces']) {
    const lines = wrapGraphText(value, 160, 16); assert.equal(lines.join(''), value); assert.ok(lines.length > 1);
  }
});


test('themes emphasize without removing real composition, outside endpoints or relations', () => {
 const children=['execution.client','execution.server','execution.preparation','execution.scheduling','execution.agent-data'].map(id=>object(id));
 const outside=['lineage.conversation','worker.claim-loop','worker.runtime','other'].map(id=>object(id));
 const r=(id,from,to)=>({id,from,to,kind:'calls',label:'请求、领取与观察的协议名称'});
 const input={root:object('execution'),children,boundaryEndpoints:outside,
 internalRelations:[r('ex.enqueue',children[0].id,children[1].id),r('ex.prepare',children[3].id,children[2].id),r('es.scheduler.record',children[3].id,children[4].id)],
 boundaryRelations:[r('ex.submit.rpc',outside[0].id,children[0].id),r('ex.claim.rpc',outside[1].id,children[3].id),r('other-observation',outside[3].id,children[0].id)]};
 const before=structuredClone(input), g=layoutExploration(input);
 assert.deepEqual(input,before);
 assert.deepEqual(g.nodes.map(n=>n.id),children.map(n=>n.id));
 assert.deepEqual(g.context.map(n=>n.id),outside.map(n=>n.id));
 const labels=g.edges.map(e=>e.labelBox);
 labels.forEach((label,i)=>{assert(![...g.nodes,...g.context,...labels.slice(i+1)].some(b=>intersects(label,b)));});
 for(const viewport of [{width:904,height:475},{width:625,height:600},{width:295,height:400}]) {
  const camera=initialGraphCamera(g,viewport);
  for(const b of [...g.nodes,...g.context,...labels]) {
   assert(b.x*camera.k+camera.x>=0 && (b.x+b.width)*camera.k+camera.x<=viewport.width);
   assert(b.y*camera.k+camera.y>=0 && (b.y+b.height)*camera.k+camera.y<=viewport.height);
  }
 }
 const selected=layoutExploration({...input,readingLens:'relation',readingFocus:'other-observation'});
 assert.deepEqual(selected.context.map(n=>n.id),g.context.map(n=>n.id));
 assert.deepEqual(selected.edges.map(e=>e.id),g.edges.map(e=>e.id));
 assert.deepEqual(selected.edges.filter(e=>e.emphasized).map(e=>e.id),['other-observation']);
});


test('long-distance continuations attach to actual sources and preserve the opposite direction', () => {
 const input={overview:true,children:['interface','web','lineage','execution','worker','providers','storage'].map(id=>object(id)),boundaryEndpoints:[],
 internalRelations:[
 {id:'request',from:'worker',to:'providers',protocol_id:'provider-native',label:'原生请求'},
 {id:'event',from:'providers',to:'worker',protocol_id:'provider-native',label:'事件返回'},
 {id:'far',from:'interface',to:'lineage',protocol_id:'hatg-isa',label:'指令'},
 {id:'save',from:'worker',to:'storage',protocol_id:null,label:'保存原文'}],boundaryRelations:[]};
 const before=structuredClone(input),g=layoutExploration(input,{width:734});
 assert.deepEqual(input,before);
 for(const e of g.edges){
  assert(e.members.every(m=>m.from===e.from&&m.to===e.to));
  if(e.presentation==='port'){
   const source=g.nodes.find(n=>n.id===e.from),label=e.labelBox;
   assert(label.x>=source.x && label.x+label.width<=source.x+source.width);
   assert(label.y>=source.y+source.headerHeight && label.y+label.height<=source.y+source.height);
   assert(label.lines.join('').startsWith('→ '));assert.equal(e.paths.length,0);
  }
 }
 assert.equal(g.edges.find(e=>e.id==='request').readingLabel,'原生请求');
 assert.equal(g.edges.find(e=>e.id==='event').readingLabel,'事件返回');
 assert.equal(g.edges.find(e=>e.id==='far').presentation,'port');
 assert.equal(g.edges.find(e=>e.id==='save').protocol_id,null);
});

test('cancellation bypass remains a named facade port with the original Scheduler target', () => {
 const relation={id:'ex.cancel.request',from:'execution.client',to:'execution.scheduling',label:'登记取消意图',kind:'interrupts',protocol_id:'resident-execution',contract:{operation:'execution.cancel',state_effects:['先持久化请求，尚不保证Provider已停']}};
 const content={root:object('execution'),children:[object('execution.client',{title:'客户端 facade'}),object('execution.scheduling',{title:'持久调度与资格'})],boundaryEndpoints:[],internalRelations:[relation],boundaryRelations:[]};
 const graph=layoutExploration(content,{width:734}),edge=graph.edges[0],source=graph.nodes.find(n=>n.id===relation.from);
 assert.equal(edge.presentation,'port');assert.equal(edge.paths.length,0);
 assert.deepEqual(edge.summary.relations,[relation]);assert.equal(edge.to,'execution.scheduling');
 assert.match(edge.labelBox.lines.join(''),/^→ 持久调度与资格.*登记取消意图$/);
 assert(edge.labelBox.y>=source.y+source.headerHeight);
 assert(edge.labelBox.y+edge.labelBox.height<=source.y+source.height);
});
