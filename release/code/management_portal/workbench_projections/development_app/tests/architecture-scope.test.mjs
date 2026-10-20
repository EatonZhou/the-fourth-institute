import test from 'node:test';
import assert from 'node:assert/strict';
import { graphForScope, createScopeNavigation } from '../src/architecture/scope.js';
import { semanticNode, semanticObject, visibleModuleAssociations } from '../src/architecture/model.js';

const module = (id, parent) => ({ id, parent, title: id, responsibility: 'Responsibility of ' + id });
const fixture = () => ({ mode: 'current', modelVersion: 2, mapHash: 'map-a', mapRef: 'repo/file/map.yaml',
  observedAt: '2026-10-15T00:00:00Z', manifest: { pinned: { sha256: 'batch-a' } },
  modules: [module('resources'), module('resources.access', 'resources'), module('resources.types', 'resources'),
    module('resources.leaf', 'resources.types'), module('lineage'), module('worker')],
  protocols: [
    { id: 'root', title: 'Root participation', modules: ['lineage', 'resources', 'worker'] },
    { id: 'child-external', modules: ['resources.access', 'lineage'] },
    { id: 'internal', modules: ['resources.access', 'resources.types'] },
  ], designMeta: {}, plans: [] });

test('scope graph contains direct children, root boundary and exact participants; never infers call edges', () => {
  const d = fixture(), original = structuredClone(d);
  const top = graphForScope(d);
  assert.deepEqual(top.nodes.map(n => n.localId), ['resources', 'lineage', 'worker']);
  const scoped = graphForScope(d, { scopeRoot: 'resources' });
  assert.deepEqual(scoped.nodes.map(n => n.localId), ['resources.access', 'resources.types']);
  assert.equal(scoped.boundary.localId, 'resources');
  assert.deepEqual(scoped.protocols.find(p => p.localId === 'root').participants, ['module:lineage', 'module:resources', 'module:worker']);
  assert.deepEqual(scoped.protocols.find(p => p.localId === 'child-external').participants, ['module:resources.access', 'module:lineage']);
  assert.deepEqual(scoped.context.map(n => n.localId), ['lineage', 'worker']);
  assert.deepEqual(scoped.edges, []);
  assert(scoped.protocols.every(p => p.direction === 'unspecified'));
  assert.deepEqual(d, original);
});

test('leaf stays a real boundary with no invented children or inherited parent protocol', () => {
  const d = fixture();
  const leaf = graphForScope(d, { scopeRoot: 'resources.leaf' });
  assert.equal(leaf.boundary.localId, 'resources.leaf');
  assert.deepEqual(leaf.nodes, []); assert.deepEqual(leaf.protocols, []);
  assert.equal(leaf.childrenStatus, 'not-registered');
  const context = graphForScope(d, { scopeRoot: 'resources.leaf', parentContext: true });
  assert.deepEqual(context.protocols, []);
  assert.deepEqual(context.parentProtocols.map(p => p.localId), ['internal']);
  assert.deepEqual(leaf.parentProtocols, []);
});

test('missing source fails; revoked participants vanish before layout, without placeholders or hidden counts', () => {
  const d = fixture(); d.modules = d.modules.filter(m => m.id !== 'lineage');
  const scoped = graphForScope(d, { scopeRoot: 'resources' });
  assert(!JSON.stringify(scoped).includes('lineage'));
  assert.deepEqual(scoped.protocols.find(p => p.localId === 'root').participants, ['module:resources', 'module:worker']);
  assert.throws(() => graphForScope({}), /尚未可读/);
  assert.throws(() => graphForScope(d, { scopeRoot: 'lineage' }), /不存在或已不可读/);
});

test('three levels return to independent cameras, selections and filters; entering does not select a child', () => {
  const d = fixture(), nav = createScopeNavigation();
  const overview = { ...nav.restore(d), camera: { x: 19, y: 12, k: 1.4 }, selectedId: 'module:worker', filters: { query: 'Worker' } };
  let view = nav.enter(d, 'resources', overview);
  assert.equal(view.selectedId, null); assert.equal(view.camera, null);
  view = { ...view, camera: { x: 2, y: 4, k: .8 }, selectedId: 'module:resources.types', returnFocusId: 'module:resources.types' };
  let leaf = nav.enter(d, 'resources.types', view);
  leaf = { ...leaf, camera: { x: -10, y: 10, k: 2 } };
  const restored = nav.parent(d, leaf);
  assert.deepEqual(restored, view);
  assert.deepEqual(nav.parent(d, restored), overview);
  overview.camera.x = 900;
  assert.equal(nav.restore(d).camera.x, 19);
});

test('history cannot be saved or restored; changing current batch discards departed view memory', () => {
 const d=fixture(),nav=createScopeNavigation();
 nav.save(d,{scopeRoot:'resources',camera:{x:2,y:4,k:1.2}});
 const historical={...d,mode:'history',commit:'old'};
 assert.throws(()=>nav.restore(historical,'resources'),/版本/);
 assert.throws(()=>nav.save(historical,{scopeRoot:'resources'}),/版本/);
 const next={...d,manifest:{pinned:{sha256:'new'}}};
 assert.equal(nav.restore(next,'resources').camera,null);
 assert.equal(nav.restore(d,'resources').camera,null);
});

test('same-batch revocation cleans saved selections and roots and cannot resurrect them after regrant', () => {
  const d = fixture(), nav = createScopeNavigation();
  nav.save(d, { scopeRoot: 'resources', selectedId: 'module:resources.types', returnFocusId: 'module:resources.types' });
  nav.save(d, { scopeRoot: 'resources.types', camera: { x: 20, y: 1, k: 2 } });
  const revoked = { ...d, modules: d.modules.filter(m => !['resources.types', 'resources.leaf'].includes(m.id)) };
  assert.equal(nav.restore(revoked, 'resources').selectedId, null);
  assert.equal(nav.restore(revoked, 'resources').returnFocusId, null);
  assert.throws(() => nav.enter(revoked, 'resources.types'), /不可读/);
  assert.equal(nav.restore(d, 'resources.types').camera, null);
});

test('source links keep the actual ref without inventing a missing or invalid line', () => {
  const d = fixture();
  for (const line of [undefined, null, 'undefined', 0, -1]) {
    d.anchors = { protocols: { root: line } };
    assert.equal(semanticNode(d, 'protocol', 'root').ref, d.mapRef);
  }
  d.anchors.protocols.root = 31;
  assert.equal(semanticNode(d, 'protocol', 'root').ref, d.mapRef + '#L31');
});


test('exact direction associations highlight visible ancestors without changing scope or identity', () => {
  const data = fixture(), before = structuredClone(data);
  const exact = ['resources.leaf', 'resources.access', 'worker', 'missing'];
  const top = graphForScope(data);
  assert.deepEqual(visibleModuleAssociations(data, top, exact), [
    { sourceId: 'resources.leaf', visibleId: 'resources' },
    { sourceId: 'resources.access', visibleId: 'resources' },
    { sourceId: 'worker', visibleId: 'worker' },
  ]);
  const scope = graphForScope(data, { scopeRoot: 'resources' });
  assert.deepEqual(visibleModuleAssociations(data, scope, ['resources.leaf', 'resources']), [
    { sourceId: 'resources.leaf', visibleId: 'resources.types' },
    { sourceId: 'resources', visibleId: 'resources' },
  ]);
  const denied = { ...data, modules: data.modules.filter(m => m.id !== 'resources.leaf') };
  assert.deepEqual(visibleModuleAssociations(denied, scope, ['resources.leaf']), []);
  assert.deepEqual(data, before);
  assert.deepEqual(top.nodes.map(n => n.localId), ['resources', 'lineage', 'worker']);
});


test('local selection restores only through current authorized identity resolver', () => {
  const data = fixture(), allowed = new Set(['relation:local']);
  const nav = createScopeNavigation({ resolveIdentity: (_data, id, root) => root === 'resources' && allowed.has(id) });
  nav.save(data, { scopeRoot: 'resources', selectedId: 'relation:local', returnFocusId: 'relation:local' });
  assert.equal(nav.restore(data, 'resources').selectedId, 'relation:local');
  allowed.clear(); assert.equal(nav.restore(data, 'resources').selectedId, null);
  allowed.add('relation:local'); assert.equal(nav.restore(data, 'resources').selectedId, null);
});
