import test from 'node:test';
import assert from 'node:assert/strict';
import { createEvidenceLoader, sha256 } from '../src/architecture/data.js';
import { MAP_REF, EVOLUTION_REF, graphForView, semanticNode, semanticObject, directionSelection, impactOf, validateMap } from '../src/architecture/model.js';

const owned = 'decision_111111111111', protocolOwned = 'decision_222222222222', related = 'decision_333333333333';
const planId = 'plan_111111111111';
const module = (id, parent, layer = 'service') => ({ id, parent, layer, level: parent ? 'component' : 'runtime',
  title: id, responsibility: 'Responsibility of ' + id, designs: [], code_paths: [], instruction_owners: [] });
const object = id => ({ ref: EVOLUTION_REF + '#L99', source: EVOLUTION_REF, kind: 'direction:' + id,
  status: 'proposed', observed_at: '2026-10-15T00:00:00Z' });

async function fixture() {
  const map = { schema: 'tfi.portal-architecture.v1', model_version: 2, modules: [
    module('interface', null, 'frontend'), module('web', null, 'entry'), module('worker'), module('storage', null, 'storage'),
    { ...module('worker.session', 'worker'), designs: [owned], instruction_owners: ['session'], deployed_in: ['worker'] },
    module('worker.sibling', 'worker'), module('session.detail', 'worker.session'), module('web.component', 'web'),
  ], protocols: [
    { id: 'rpc', title: 'Signed RPC', modules: ['web', 'worker'], designs: [protocolOwned], related_designs: [owned, related], description: 'Signed requests' },
    { id: 'local', title: 'Local session', modules: ['worker.session', 'storage'], designs: [], related_designs: [] },
    { id: 'sibling', title: 'Sibling only', modules: ['worker.sibling'], designs: [], related_designs: [] },
  ] };
  const mapText = 'schema: ' + map.schema + '\nmodel_version: 2\nmodules:\n' + map.modules.map(m => '  - id: ' + m.id + '\n').join('') +
    'protocols:\n' + map.protocols.map(p => '  - id: ' + p.id + '\n').join('');
  const hash = await sha256(mapText);
  const evolution = { schema: 'tfi.portal-evolution.v2', module_map: { ref: MAP_REF, schema: map.schema, model_version: 2, revision: hash },
    directions: [
      { id: 'without-plan', title: 'Independent idea', description: 'A future experience', concept: 'Keep the narrative',
        modules: ['worker.session', 'web.component'], module_roles: { 'worker.session': 'Own the runtime', 'web.component': 'Expose it', storage: 'Not a declared member' }, plans: [] },
      { id: 'with-plan', title: 'Idea with Plan', modules: ['worker.session'], plans: [planId] },
    ], plans: [{ id: planId, title: 'Plan', modules: ['web'], directions: ['without-plan'] }],
  };
  const evolutionText = () => 'schema: ' + evolution.schema + '\nmodule_map: ' + JSON.stringify(evolution.module_map) + '\ndirections:\n' +
    evolution.directions.map(d => '  - id: ' + d.id + '\n    value: ' + JSON.stringify(d) + '\n').join('') + 'plans:\n  - id: ' + planId + '\n';
  const denied = new Set(), reads = [], resolves = [];
  const entries = { [MAP_REF]: { text: mapText, data: map }, [EVOLUTION_REF]: { text: evolutionText(), data: evolution } };
  const resources = {
    resolve: async ref => { resolves.push(ref); return { readable: !denied.has(ref), title: 'Readable ' + ref }; },
    read: async (ref, format) => {
      reads.push({ ref, format }); const value = entries[ref];
      if (!value || denied.has(ref)) return { readable: false, data: null };
      return { readable: true, data: format === 'yaml' ? structuredClone(value.data) : value.text, metadata: {} };
    },
  };
  const loader = createEvidenceLoader({ resources, sdk: { hatg: async () => ({ instructions: [{ instruction: 'SessionGet', owner: 'session' }, { instruction: 'NoOwner', owner: 'unmapped' }] }) } });
  const evidence = { snapshots: [], audits: [], scenario_catalog: [] };
  const refreshEvolution = () => { entries[EVOLUTION_REF].text = evolutionText(); };
  return { map, evolution, hash, entries, denied, reads, resolves, loader, evidence, refreshEvolution };
}

test('runtime graph has one direct-child scope and truthful boundary endpoint representations', async () => {
  const f = await fixture(), d = await f.loader.current(f.evidence);
  const graph = graphForView(d, { level: 3, showPlans: true, query: 'not-a-runtime-name' });
  assert.equal(graph.runtime, true); assert.deepEqual(graph.edges, []);
  assert.deepEqual(graph.nodes.map(n => n.localId), ['interface', 'web', 'worker', 'storage']);
  assert(graph.nodes.every(n => n.type === 'module'));
  assert.deepEqual(graph.protocols.find(p => p.localId === 'local').participants, ['module:worker', 'module:storage']);
  assert.equal(graph.protocols.find(p => p.localId === 'rpc').label, 'Signed RPC');
  assert.equal(graph.protocols.find(p => p.localId === 'rpc').id, 'protocol:rpc');
  const scoped = graphForView(d, { scopeRoot: 'worker' });
  assert.deepEqual(scoped.nodes.map(n => n.localId), ['worker.session', 'worker.sibling']);
  assert.equal(scoped.boundary.localId, 'worker');
  assert.deepEqual(scoped.protocols.find(p => p.localId === 'local').participants, ['module:worker.session', 'module:storage']);
  assert.deepEqual(graph.protocols.find(p => p.localId === 'local').actualParticipants, ['module:worker.session', 'module:storage']);
  const deep = graphForView(d, { scopeRoot: 'worker.session' });
  assert.deepEqual(deep.nodes.map(n => n.localId), ['session.detail']);
  assert.deepEqual(scoped.nodes[0].data.deployed_in, ['worker']);
});

test('semantic lookup finds authorized collapsed objects and refuses absent or unowned instructions', async () => {
  const f = await fixture(), d = await f.loader.current(f.evidence);
  assert(!graphForView(d, {}).nodes.some(n => n.localId === 'session.detail'));
  const child = semanticNode(d, 'module', 'session.detail');
  assert.equal(child.localId, 'session.detail'); assert.match(child.ref, /#L\d+$/);
  assert.equal(semanticNode(d, 'protocol', 'rpc').data.id, 'rpc');
  assert.equal(semanticNode(d, 'design', protocolOwned).data.owner.type, 'protocol');
  assert.equal(semanticNode(d, 'plan', planId).status, 'proposed');
  await f.loader.instructions(d);
  assert.equal(semanticNode(d, 'instruction', 'SessionGet').data.owner, 'session');
  for (const [type, id] of [['module', 'missing'], ['protocol', 'missing'], ['design', 'decision_absent'], ['plan', 'absent'], ['instruction', 'NoOwner']]) assert.equal(semanticNode(d, type, id), null);
  const published = semanticObject(d, child);
  assert.deepEqual(Object.keys(published).sort(), ['kind', 'observed_at', 'ref', 'source', 'status']);
  assert.equal(published.kind, 'module:session.detail');
});

test('primary Design ownership is unique across modules/protocols; related references remain non-owning', async () => {
  const f = await fixture();
  validateMap(f.map);
  f.map.protocols[0].designs.push(owned);
  assert.throws(() => validateMap(f.map), /主归属不唯一/);
  delete f.map.model_version;
  assert.doesNotThrow(() => validateMap(f.map));
});

test('protocol Design ownership and inherited endpoints preserve component boundaries', async () => {
  const f = await fixture(), d = await f.loader.current(f.evidence);
  const ownedImpact = impactOf(d, semanticNode(d, 'design', protocolOwned));
  assert.deepEqual(ownedImpact.modules, ['web', 'worker']);
  assert(ownedImpact.protocols.includes('rpc'));
  assert(ownedImpact.paths.some(p => p.relation === 'Design 归属协议的参与端点'));
  const child = impactOf(d, semanticNode(d, 'module', 'session.detail'));
  assert.deepEqual(child.protocols, ['rpc', 'local']);
  assert(!child.modules.includes('worker.sibling'));
  const sibling = impactOf(d, semanticNode(d, 'module', 'worker.sibling'));
  assert(!sibling.protocols.includes('local'));
  const parent = impactOf(d, semanticNode(d, 'module', 'worker'));
  assert(!parent.protocols.includes('local')); assert(!parent.protocols.includes('sibling'));
  const evidenceOnly = impactOf(d, semanticNode(d, 'design', related));
  assert.deepEqual(evidenceOnly.modules, []); assert.deepEqual(evidenceOnly.protocols, ['rpc']);
  assert(evidenceOnly.paths.every(p => p.relation === '协议依据（非归属）'));
  assert.equal(d.designMeta[related].owner, undefined);
  assert.deepEqual(d.designMeta[related].relatedProtocols, ['rpc']);
});

test('a Plan-free direction retains all directly declared modules and their roles', async () => {
  const f = await fixture(), d = await f.loader.current(f.evidence);
  const selection = directionSelection(d, object('without-plan'));
  assert.deepEqual(selection.data.modules, ['worker.session', 'web.component']);
  assert.deepEqual(selection.data.plans, []); // Old Plan.directions must not manufacture an association.
  assert.deepEqual(Object.keys(selection.data.module_roles), ['worker.session', 'web.component']);
  assert.equal(selection.data.incomplete, false);
  assert.deepEqual(directionSelection(d, object('with-plan')).data.plans.map(p => p.id), [planId]);
  assert.equal(semanticObject(d, selection).kind, 'direction:without-plan');
});

test('current-only binding keeps module identity without revision, commit or snapshot', async () => {
  const f = await fixture(); delete f.evolution.module_map.revision; f.refreshEvolution();
  const d = await f.loader.current(f.evidence);
  assert.equal(d.evolutionMappingValid, true);
  assert.deepEqual(directionSelection(d, object('without-plan')).data.modules, ['worker.session', 'web.component']);
});
test('wrong map model/schema retains direction narrative and identity but no verified scope or Plan highlights', async () => {
  for (const change of [{ model_version: 1 }, { schema: 'wrong' }, { ref: 'wrong' }]) {
    const f = await fixture(); Object.assign(f.evolution.module_map, change); f.refreshEvolution();
    const d = await f.loader.current(f.evidence), selection = directionSelection(d, object('without-plan'));
    assert.deepEqual(selection.data.modules, []); assert.deepEqual(selection.data.module_roles, {});
    assert.equal(selection.data.concept, 'Keep the narrative'); assert.equal(selection.data.incomplete, true);
    assert.match(selection.data.mappingIssue, /版本不一致/);
    assert.equal(semanticObject(d, selection).kind, 'direction:without-plan');
    assert.deepEqual(d.plans[0].modules, []);
    assert.deepEqual(impactOf(d, semanticNode(d, 'module', 'web')).plans, []);
  }
});

test('denied Design ownership, related evidence and Plans are filtered before model exposure', async () => {
  const f = await fixture(); f.denied.add(protocolOwned); f.denied.add(related); f.denied.add(planId);
  const d = await f.loader.current(f.evidence);
  assert.equal(d.modelVersion, 2); assert.equal(d.evolutionMappingValid, true);
  assert.deepEqual(d.protocols[0].designs, []); assert.deepEqual(d.protocols[0].related_designs, [owned]);
  assert.equal(semanticNode(d, 'design', related), null); assert.equal(semanticNode(d, 'plan', planId), null);
  assert.deepEqual(directionSelection(d, object('with-plan')).data.plans, []);
  assert.deepEqual(directionSelection(d, object('with-plan')).data.modules, ['worker.session']);
  assert.equal(d.designMeta[owned].owner.id, 'worker.session');
  assert(f.resolves.includes(related));
});

test('missing module IDs stay incomplete and a missing evolution source yields no direction', async () => {
  const f = await fixture(); f.evolution.directions[0].modules.push('unregistered'); f.refreshEvolution();
  const d = await f.loader.current(f.evidence), selection = directionSelection(d, object('without-plan'));
  assert.deepEqual(selection.data.modules, ['worker.session', 'web.component']);
  assert.equal(selection.data.incomplete, true); assert.equal(semanticNode(d, 'module', 'unregistered'), null);
  f.denied.add(EVOLUTION_REF);
  const revoked = await f.loader.current(f.evidence);
  assert.equal(directionSelection(revoked, object('without-plan')), null);
  assert.deepEqual(revoked.plans, []);
});

test('v2 current reuse checks binding bytes; watch forces fresh related-Design authorization', async () => {
  const f = await fixture(), initial = await f.loader.current(f.evidence);
  const reused = await f.loader.current(f.evidence, { reuse: initial });
  assert.equal(reused.designMeta, initial.designMeta);
  f.evolution.module_map.revision = '0'.repeat(64); f.refreshEvolution();
  const changed = await f.loader.current(f.evidence, { reuse: initial });
  assert.equal(changed.evolutionMappingValid, true); assert.notEqual(changed.designMeta, initial.designMeta);
  f.denied.add(related);
  const revalidated = await f.loader.current(f.evidence, { reuse: changed, canReuse: () => false });
  assert.equal(revalidated.designMeta[related], undefined); assert(!revalidated.protocols[0].related_designs.includes(related));
  assert(initial.protocols[0].related_designs.includes(related));
});

test('a current v1 map retains module-owned Designs and the legacy graph', async () => {
  const f = await fixture(), oldMap = structuredClone(f.map); delete oldMap.model_version;
  oldMap.protocols[0].designs = [owned];
  const oldText = f.entries[MAP_REF].text.replace('model_version: 2\n', '');
  f.entries[MAP_REF] = { text: oldText, data: oldMap };
  const current = await f.loader.current(f.evidence);
  assert.equal(current.modelVersion, 1); assert.equal(current.designMeta[owned].owner.type, 'module');
  const graph = graphForView(current, { branch: 'worker.session', level: 1 });
  assert(graph.nodes.some(n => n.type === 'protocol')); assert(graph.nodes.some(n => n.type === 'design'));
  assert(graph.edges.some(e => e.type === 'participation')); assert.equal(graph.runtime, undefined);
  assert.equal(semanticObject(current, semanticNode(current, 'module', 'worker.session')).status, 'current');
});
