import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createHash } from 'node:crypto';
import { validateExplorationModel, projectExplorationScope } from '../src/architecture/exploration-model.js';

// Synthetic schema fixtures only. IDs, source hashes and mechanisms below do not
// describe Portal behavior and must never be loaded as product evidence.
const digest = 'a'.repeat(64), commit = 'b'.repeat(40);
const descriptor = name => ({ address: `fixture:${name}`, sha256: digest, size: 1 });
const identity = id => ({ resource_ref: `fixture:${id}`, focus_owner_id: id, focus_kind: 'module', local_only: false });
const object = (id, parent_id = null, kind = 'module') => ({
  id, kind, title: id, responsibility: `Synthetic responsibility ${id}`, parent_id,
  child_ids: [], host_ids: [], state_owned: [], relation_ids: [], protocol_ids: [], mechanism_ids: [], evidence_ids: ['source'],
  identity: { ...identity(id), focus_kind: kind === 'protocol' ? `protocol:${id}` : 'module' },
});
const interaction = () => ({ operation: 'synthetic-operation', transport: 'local-test', input: [], output: [], preconditions: [], state_effects: [], errors: [], ordering: 'No global ordering guarantee' });
function relation(id, from, to, scope_id, placement, internal_endpoint_ids, protocol_id = null) {
  return { id, kind: 'calls', from, to, label: id, contract: interaction(), protocol_id, evidence_ids: ['source'],
    scope_views: [{ scope_id, placement, internal_endpoint_ids }] };
}
function mechanism(owner_id, id = `${owner_id}-mechanism`) {
  return { id, owner_id, title: id, question: 'What changes after the input?', presentation: 'synthetic_counter', adapter: 'synthetic_counter.v1',
    state_model: 'A test-only counter', mode: 'simulated', entry_conditions: [], invariants: ['Counter remains numeric'],
    input_schema: { type: 'object', properties: { delta: { type: 'integer' } }, required: ['delta'], additionalProperties: false },
    participants: [{ object_id: owner_id, role: 'Test owner' }],
    state_fields: [{ id: 'counter', label: 'Counter', meaning: 'Test state', type: 'integer', initial: 0 }],
    steps: [{ id: 'add', title: 'Add', from: owner_id, to: owner_id, relation_id: null, trigger: 'test', guard: 'Always',
      when: { field: 'counter', gte: 0 }, state_changes: [{ field: 'counter', op: 'increment', value: { input: 'delta' } }],
      observable: 'New counter', next: ['done'], evidence_ids: ['source'] },
    { id: 'done', title: 'Done', from: owner_id, to: owner_id, relation_id: null, trigger: 'test', guard: 'Always', state_changes: [], observable: 'Done', next: [], evidence_ids: ['source'] }],
    cases: [{ id: 'sample', title: 'Sample', inputs: { delta: 2 }, path: ['add', 'done'], outcome: 'Counter is two', evidence_ids: ['source'] }], evidence_ids: ['source'] };
}
function fixture() {
  const root = object('root'), left = object('left', 'root'), right = object('right', 'root');
  const leaf = object('leaf', 'left', 'concept'), outside = object('outside'), protocol = object('wire', null, 'protocol');
  root.child_ids = ['left', 'right']; root.protocol_ids = ['wire']; left.child_ids = ['leaf'];
  leaf.identity = { ...left.identity, local_only: true }; leaf.host_ids = ['outside']; leaf.mechanism_ids = ['leaf-mechanism'];
  protocol.contract = { ...interaction(), roles: [{ object_id: 'left', role: 'Sender', guarantees: ['Preserves input'] },
    { object_id: 'outside', role: 'Receiver', guarantees: ['Returns result'] }], invariants: ['Synthetic invariant'],
    operations: [{ id: 'exchange', request_fields: ['value'], response_fields: ['result'], failures: ['Test rejection'] }] };
  const objects = [root, left, right, leaf, outside, protocol];
  return { schema: 'tfi.portal-architecture-exploration.v1', revision: 1,
    binding: { commit, base_manifest: descriptor('base'), consumer_contract: descriptor('consumer'), module_map: descriptor('map'), evolution: descriptor('evolution') },
    objects, relations: [relation('local', 'left', 'right', 'root', 'internal', ['left', 'right'], 'wire'),
      relation('outgoing', 'right', 'outside', 'root', 'boundary', ['right'], 'wire'),
      relation('deep', 'leaf', 'right', 'left', 'boundary', ['leaf'])],
    mechanisms: [mechanism('leaf')],
    evidence: [{ id: 'source', kind: 'design', claim: 'Only a schema test', address: 'fixture:source', version: 1, sha256: digest,
      start_line: 1, end_line: 1, symbol: 'Synthetic source', excerpt: 'x', frozen: descriptor('frozen-source') }],
    coverage: objects.map(o => ({ object_id: o.id, status: 'researching', owner: 'test',
      responsibility: 'pending', composition: 'pending', interactions: 'pending', mechanism: 'pending', sources: 'pending', work_item_ids: ['research'] })),
    work_items: [{ id: 'research', title: 'Synthetic pending research' }], completion: { status: 'in_progress', pending_object_ids: objects.map(o => o.id) } };
}
const find = (model, id) => model.objects.find(o => o.id === id);
function invalid(model, code) {
  const result = validateExplorationModel(model);
  assert.equal(result.valid, false);
  assert.ok(result.errors.some(error => error.code === code), JSON.stringify(result.errors));
}
function freeze(value) {
  if (value && typeof value === 'object') { Object.freeze(value); for (const child of Object.values(value)) freeze(child); }
  return value;
}

test('partial research is structurally valid and does not gain complete status', () => {
  const model = fixture();
  assert.deepEqual(validateExplorationModel(model), { valid: true, errors: [] });
  assert.equal(model.completion.status, 'in_progress');
});

test('scope contains ordered direct composition, explicit relations and real boundary endpoints', () => {
  const scope = projectExplorationScope(fixture(), { rootId: 'root' });
  assert.equal(scope.root.id, 'root');
  assert.deepEqual(scope.children.map(o => o.id), ['left', 'right']);
  assert.deepEqual(scope.internalRelations.map(r => [r.id, r.from, r.to]), [['local', 'left', 'right']]);
  assert.deepEqual(scope.boundaryRelations.map(r => [r.id, r.from, r.to]), [['outgoing', 'right', 'outside']]);
  assert.deepEqual(scope.boundaryEndpoints.map(o => o.id), ['outside']);
  assert.equal(scope.boundaryRelations[0].scopeView.placement, 'boundary');
  assert.deepEqual(scope.protocols.map(o => o.id), ['wire']);
  assert.equal(scope.evidence[0].frozen.address, 'fixture:frozen-source');
});

test('a deeper scope retains its actual endpoints and never inherits parent protocols', () => {
  const scope = projectExplorationScope(fixture(), { rootId: 'left' });
  assert.deepEqual(scope.children.map(o => o.id), ['leaf']);
  assert.deepEqual(scope.boundaryEndpoints.map(o => o.id), ['right']);
  assert.equal(scope.boundaryRelations[0].protocol_id, null);
  assert.deepEqual(scope.protocols, []);
  assert.deepEqual(scope.internalRelations, []);
});

test('leaf exposes its own mechanism with owner identity, not an empty-children message', () => {
  const model = fixture(), scope = projectExplorationScope(model, { rootId: 'leaf' });
  assert.deepEqual(scope.children, []);
  assert.equal(scope.mechanismEntries[0].adapter, 'synthetic_counter.v1');
  assert.deepEqual(scope.mechanismEntries[0].steps[0].state_changes, [{ field: 'counter', op: 'increment', value: { input: 'delta' } }]);
  assert.deepEqual(scope.root.identity, { ...find(model, 'left').identity, local_only: true });
  assert.equal(scope.root.identity.focus_owner_id, 'left');
});

test('hosting and role participation never create composition or interactions', () => {
  const model = fixture(); model.relations = [];
  assert.deepEqual(projectExplorationScope(model, { rootId: 'outside' }).children, []);
  const scope = projectExplorationScope(model, { rootId: 'root' });
  assert.deepEqual(scope.internalRelations, []); assert.deepEqual(scope.boundaryRelations, []);
  assert.equal(scope.protocols[0].contract.roles.length, 2);
  assert.deepEqual(projectExplorationScope(model, { rootId: 'leaf' }).root.host_ids, ['outside']);
});

test('protocol root preserves guarantees and opens its own full mechanism without composition', () => {
  const model = fixture(); model.mechanisms.push(mechanism('wire')); find(model, 'wire').mechanism_ids = ['wire-mechanism'];
  const scope = projectExplorationScope(model, { rootId: 'wire' });
  assert.deepEqual(scope.children, []);
  assert.deepEqual(scope.root.contract.roles[0].guarantees, ['Preserves input']);
  assert.equal(scope.protocols[0].contract.operations[0].id, 'exchange');
  assert.equal(scope.mechanismEntries[0].owner_id, 'wire');
  assert.deepEqual(validateExplorationModel(model), { valid: true, errors: [] });
});

test('overview aggregates only real cross-root relations and retains original endpoints', () => {
  const model = fixture(), before = structuredClone(model);
  const overview = projectExplorationScope(model);
  assert.deepEqual(overview.children.map(o => o.id), ['root', 'outside']);
  assert.deepEqual(overview.internalRelations.map(r => [r.from,r.to]), [['root','outside']]);
  assert.deepEqual(overview.internalRelations[0].summary.relations.map(r => [r.id,r.from,r.to]), [['outgoing','right','outside']]);
  assert.deepEqual(model, before);
  // An explicit root view contributes its real relationship to the same group.
  model.relations.push(relation('overview', 'root', 'outside', null, 'internal', ['root', 'outside']));
  const groups = projectExplorationScope(model).internalRelations;
  assert.deepEqual(groups.flatMap(r=>r.summary.relations.map(v=>v.id)), ['outgoing','overview']);
  assert.equal(groups.length, 2, 'distinct protocol identities stay distinct');
  model.relations = [];
  // Protocol roles, hosting and membership cannot manufacture communication.
  assert.deepEqual(projectExplorationScope(model).internalRelations, []);
  assert.deepEqual(projectExplorationScope(model).protocols.map(p => p.id), ['wire']);
});

test('arbitrary composition depth is not collapsed into hosting or fixed runtime levels', () => {
  const model = fixture(); model.objects = []; model.relations = []; model.mechanisms = []; model.coverage = [];
  for (let i = 0; i < 1500; i++) {
    const node = object(`n${i}`, i ? `n${i - 1}` : null);
    node.child_ids = i < 1499 ? [`n${i + 1}`] : []; model.objects.push(node);
  }
  model.completion.pending_object_ids = model.objects.map(o => o.id);
  assert.equal(validateExplorationModel(model).valid, true);
  assert.deepEqual(projectExplorationScope(model, { rootId: 'n1000' }).children.map(o => o.id), ['n1001']);
});

test('frozen inputs are not mutated and output UI state cannot change a pinned model', () => {
  const model = freeze(fixture());
  assert.equal(validateExplorationModel(model).valid, true);
  const scope = projectExplorationScope(model, { rootId: 'leaf' });
  scope.root.title = 'Changed locally'; scope.mechanismEntries[0].state_fields[0].initial = 99;
  assert.equal(find(model, 'leaf').title, 'leaf'); assert.equal(model.mechanisms[0].state_fields[0].initial, 0);
});

test('a hidden parent never promotes remaining children to overview roots', () => {
  const model = fixture(); model.objects = model.objects.filter(o => o.id !== 'root'); model.relations = [];
  model.coverage = []; model.evidence = []; for (const o of model.objects) o.evidence_ids = [];
  const scope = projectExplorationScope(model);
  assert.deepEqual(scope.children.map(o => o.id), ['outside']);
  assert.ok(!scope.children.some(o => o.id === 'left' || o.id === 'right'));
});

test('a retained relation with a removed endpoint is rejected without a synthetic node', () => {
  const model = fixture(); model.objects = model.objects.filter(o => o.id !== 'outside');
  assert.throws(() => projectExplorationScope(model, { rootId: 'root' }), error => error.code === 'INVALID_EXPLORATION_SCOPE');
});

test('a kept protocol or mechanism cannot disclose unresolved participant references', () => {
  const model = fixture();
  model.mechanisms[0].participants.push({ object_id: 'removed', role: 'Unavailable' });
  assert.throws(() => projectExplorationScope(model, { rootId: 'leaf' }), error => error.code === 'INVALID_EXPLORATION_SCOPE');
  find(model, 'wire').contract.roles.push({ object_id: 'removed', role: 'Unavailable', guarantees: [] });
  assert.throws(() => projectExplorationScope(model, { rootId: 'wire' }), error => error.code === 'INVALID_EXPLORATION_SCOPE');
});

test('root endpoint remains the real root, not a fabricated boundary object', () => {
  const model = fixture(); model.relations.push(relation('entry', 'root', 'left', 'root', 'boundary', ['left']));
  assert.equal(validateExplorationModel(model).valid, true);
  const scope = projectExplorationScope(model, { rootId: 'root' });
  assert.deepEqual(scope.boundaryEndpoints.map(o => o.id), ['outside']);
  assert.equal(scope.internalRelations.find(r=>r.id==='entry').from, 'root');
  assert.equal(scope.internalRelations.find(r=>r.id==='entry').scopeView.placement, 'boundary', 'original declaration is preserved');
});

for (const [name, mutate, code] of [
  ['duplicate object', m => m.objects.push(structuredClone(m.objects[0])), 'duplicate_id'],
  ['duplicate relation', m => m.relations.push(structuredClone(m.relations[0])), 'duplicate_id'],
  ['duplicate mechanism step', m => m.mechanisms[0].steps.push(structuredClone(m.mechanisms[0].steps[0])), 'duplicate_id'],
  ['dangling child', m => find(m, 'root').child_ids.push('absent'), 'dangling_reference'],
  ['dangling endpoint', m => m.relations[0].to = 'absent', 'dangling_reference'],
  ['dangling host', m => find(m, 'leaf').host_ids.push('absent'), 'dangling_reference'],
  ['dangling mechanism', m => find(m, 'leaf').mechanism_ids.push('absent'), 'dangling_reference'],
  ['dangling source', m => m.relations[0].evidence_ids.push('absent'), 'dangling_reference'],
  ['dangling step next', m => m.mechanisms[0].steps[0].next = ['absent'], 'dangling_reference'],
  ['dangling case path', m => m.mechanisms[0].cases[0].path = ['absent'], 'dangling_reference'],
  ['dangling protocol role', m => find(m, 'wire').contract.roles[0].object_id = 'absent', 'dangling_reference'],
  ['protocol composition', m => { find(m, 'wire').parent_id = 'root'; find(m, 'root').child_ids.push('wire'); }, 'protocol_composition'],
  ['nonreciprocal composition', m => find(m, 'left').parent_id = 'outside', 'composition_reciprocal'],
  ['cyclic composition', m => { find(m, 'root').parent_id = 'left'; find(m, 'left').child_ids.push('root'); }, 'composition_cycle'],
  ['false internal relation', m => m.relations[1].scope_views[0].placement = 'internal', 'scope_placement'],
  ['invented internal endpoint', m => m.relations[1].scope_views[0].internal_endpoint_ids.push('outside'), 'scope_endpoints'],
  ['dangling mechanism owner', m => m.mechanisms[0].owner_id = 'absent', 'dangling_reference'],
  ['concept public identity', m => find(m, 'leaf').identity.local_only = false, 'local_identity'],
  ['invented concept identity', m => find(m, 'leaf').identity.resource_ref = 'fixture:fake', 'local_identity'],
  ['unknown state operation', m => m.mechanisms[0].steps[0].state_changes[0].op = 'execute', 'state_operation'],
  ['incompatible initial state', m => m.mechanisms[0].state_fields[0].initial = 'zero', 'state_type'],
  ['incompatible state change', m => m.mechanisms[0].steps[0].state_changes[0] = { field: 'counter', op: 'set', value: [] }, 'state_type'],
  ['unknown comparison', m => m.mechanisms[0].steps[0].when = { field: 'counter', execute: true }, 'condition'],
  ['invalid source range', m => m.evidence[0].end_line = 0, 'evidence_lines'],
  ['false verified', m => m.coverage[0].status = 'verified', 'false_verified'],
  ['false complete', m => m.completion.status = 'complete', 'false_complete'],
]) test(`contract rejects ${name}`, () => { const model = fixture(); mutate(model); invalid(model, code); });

test('documentation does not require a source commit or historical binding', () => {
  const model = fixture(); delete model.binding;
  assert.equal(validateExplorationModel(model).valid, true);
});

test('complete research requires actual evidenced mechanisms, not coverage labels alone', () => {
  const model = fixture();
  for (const o of model.objects) {
    if (!o.mechanism_ids.length) { const mech = mechanism(o.id); model.mechanisms.push(mech); o.mechanism_ids = [mech.id]; }
  }
  for (const coverage of model.coverage) Object.assign(coverage, { status: 'verified', responsibility: 'complete', composition: 'complete', interactions: 'complete', mechanism: 'complete', sources: 'complete', work_item_ids: [] });
  model.work_items = []; model.completion = { status: 'complete', pending_object_ids: [] };
  assert.deepEqual(validateExplorationModel(model), { valid: true, errors: [] });
  model.completion.pending_object_ids = ['root']; invalid(model, 'false_complete'); model.completion.pending_object_ids = [];
  model.mechanisms[0].steps = [];
  invalid(model, 'false_verified');
});

test('unsupported schema and malformed values are diagnostics, not accidental TypeErrors', () => {
  for (const value of [null, [], {}, { schema: 'other', objects: [null], mechanisms: [null] }]) {
    const result = validateExplorationModel(value); assert.equal(result.valid, false); assert.ok(result.errors.length > 0);
  }
});

test('shared mechanism entries preserve their real owner and deduplicate across scope objects', () => {
  const model = fixture(), shared = model.mechanisms[0]; shared.owner_id = 'root';
  for (const id of ['root', 'left', 'right', 'outside', 'wire']) find(model, id).mechanism_ids = [shared.id];
  assert.deepEqual(validateExplorationModel(model), { valid: true, errors: [] });
  const scope = projectExplorationScope(model, { rootId: 'root' });
  assert.deepEqual(scope.mechanisms, [shared]);
  assert.equal(scope.mechanismEntries[0].owner_id, 'root');
  const leaf = projectExplorationScope(model, { rootId: 'leaf' });
  assert.equal(leaf.mechanismEntries[0].owner_id, 'root');
  assert.equal(leaf.mechanisms[0].title, shared.title);
  assert.equal(projectExplorationScope(model, { rootId: 'wire' }).mechanisms[0].owner_id, 'root');
  scope.mechanisms[0].title = 'Local display change'; assert.equal(shared.title, 'leaf-mechanism');
});

test('child and real boundary mechanism collections are complete without promoting ownership', () => {
  const model = fixture();
  const childMechanism = mechanism('left'), boundaryMechanism = mechanism('outside');
  model.mechanisms.push(childMechanism, boundaryMechanism);
  find(model, 'left').mechanism_ids = [childMechanism.id]; find(model, 'outside').mechanism_ids = [boundaryMechanism.id];
  const scope = projectExplorationScope(model, { rootId: 'root' });
  assert.deepEqual(scope.mechanismEntries, []);
  assert.deepEqual(scope.mechanisms, [childMechanism, boundaryMechanism]);
  assert.deepEqual(scope.mechanisms.map(m => m.owner_id), ['left', 'outside']);
  // Grandchild mechanisms are not pulled in just because their parent is visible.
  assert.ok(!scope.mechanisms.some(m => m.id === 'leaf-mechanism'));
  find(model, 'outside').mechanism_ids = ['removed'];
  assert.throws(() => projectExplorationScope(model, { rootId: 'root' }), error => error.code === 'INVALID_EXPLORATION_SCOPE');
});

test('partial protocol research can omit contract only with a pending record and work item', () => {
  const model = fixture(); delete find(model, 'wire').contract;
  assert.equal(validateExplorationModel(model).valid, true);
  const research = model.coverage.find(c => c.object_id === 'wire');
  research.work_item_ids = []; invalid(model, 'protocol_contract'); research.work_item_ids = ['research'];
  research.status = 'verified'; invalid(model, 'protocol_contract'); research.status = 'researching';
  model.completion.status = 'complete'; invalid(model, 'false_complete'); invalid(model, 'protocol_contract');
});

test('frozen Execution first package validates and projects named shared mechanisms', {
  skip: !process.env.EXPLORATION_MODEL_FIXTURE,
}, () => {
  const bytes = readFileSync(process.env.EXPLORATION_MODEL_FIXTURE);
  assert.equal(bytes.length, 204868);
  assert.equal(createHash('sha256').update(bytes).digest('hex'), '3f0a40767d900168d1e03ae366f740373963ac3d9c4b3b0e7dc2d4ebd324a6f3');
  const model = JSON.parse(bytes);
  assert.equal(model.objects.length, 71); assert.equal(model.relations.length, 18);
  assert.deepEqual(validateExplorationModel(model), { valid: true, errors: [] });
  const scopes = [];
  for (const root of model.objects) {
    const scope = projectExplorationScope(model, { rootId: root.id }); scopes.push(scope);
    const visible = [scope.root, ...scope.children, ...scope.boundaryEndpoints];
    const requested = [...new Set(visible.flatMap(o => o.mechanism_ids))];
    assert.deepEqual(scope.mechanisms.map(m => m.id), requested);
    for (const mechanism of scope.mechanisms) assert.deepEqual(mechanism, model.mechanisms.find(m => m.id === mechanism.id));
  }
  const worker = scopes.find(s => s.root.id === 'worker');
  assert.equal(worker.mechanismEntries[0].id, 'execution.lifecycle');
  assert.equal(worker.mechanismEntries[0].owner_id, 'execution');
  assert.equal(worker.mechanismEntries[0].title, '一次执行：提交、领取、运行、内容与终态');
  const protocol = scopes.find(s => s.root.id === 'resident-execution');
  assert.equal(protocol.root.identity.focus_kind, 'protocol:resident-execution');
  assert.equal(protocol.mechanisms[0].owner_id, 'execution');
  assert.deepEqual(protocol.root.contract, find(model, 'resident-execution').contract);
  assert.equal(model.completion.status, 'in_progress');
  assert.ok(model.completion.pending_object_ids.length > 0);
});

test('increment provenance keeps authority, observation and writer distinct without adding graph endpoints', () => {
  const model = fixture(), entry = model.mechanisms[0];
  entry.state_fields[0].authority_id = 'outside'; entry.state_fields[0].state_scope = 'observation';
  entry.steps[0].state_changes[0].writer_id = 'right';
  assert.deepEqual(validateExplorationModel(model), { valid: true, errors: [] });
  const scope = projectExplorationScope(freeze(model), { rootId: 'leaf' });
  assert.deepEqual(scope.mechanisms, [entry]);
  assert.deepEqual(scope.boundaryEndpoints, []);
  assert.equal(scope.mechanisms[0].owner_id, 'leaf');
  assert.equal(scope.mechanisms[0].state_fields[0].authority_id, 'outside');
  assert.equal(scope.mechanisms[0].steps[0].state_changes[0].writer_id, 'right');
});

test('increment provenance rejects absent authority and writer references in models and authorized scopes', () => {
  for (const field of ['authority_id', 'writer_id']) {
    const model = fixture(), entry = model.mechanisms[0];
    if (field === 'authority_id') Object.assign(entry.state_fields[0], { authority_id: 'unavailable', state_scope: 'observation' });
    else entry.steps[0].state_changes[0].writer_id = 'unavailable';
    invalid(model, 'dangling_reference');
    assert.throws(() => projectExplorationScope(model, { rootId: 'leaf' }), error => error.code === 'INVALID_EXPLORATION_SCOPE'
      && error.errors.some(item => item.code === 'dangling_reference' && item.path.endsWith(field)));
  }
  const model = fixture(); model.mechanisms[0].state_fields[0].authority_id = 'outside';
  invalid(model, 'text');
});

test('increment catalog declaration validates fixed descriptors and preserves the protocol link', () => {
  const model = fixture(); model.catalogs = { catalog: { status: 'available', manifest: descriptor('catalog-manifest') } };
  find(model, 'wire').catalog_id = 'catalog';
  assert.deepEqual(validateExplorationModel(model), { valid: true, errors: [] });
  const scope = projectExplorationScope(model, { rootId: 'wire' });
  assert.equal(scope.root.catalog_id, 'catalog');
  assert.deepEqual(scope.root.contract, find(model, 'wire').contract);
  // The parent resolves this explicit descriptor; projection performs no reads.
  assert.equal(model.catalogs[scope.root.catalog_id].manifest.address, 'fixture:catalog-manifest');
  delete model.catalogs.catalog.manifest; invalid(model, 'descriptor');
  model.catalogs = {}; invalid(model, 'dangling_reference');
  model.catalogs = []; invalid(model, 'catalogs');
});

test('increment frozen Execution metadata preserves completed-result recovery, cancellation observations and catalog', {
  skip: !process.env.EXPLORATION_INCREMENT_FIXTURE,
}, () => {
  const bytes = readFileSync(process.env.EXPLORATION_INCREMENT_FIXTURE);
  assert.equal(bytes.length, 230282);
  assert.equal(createHash('sha256').update(bytes).digest('hex'), '3d26023d6dde185d4b74f5ae95c2f8ec7ab04dadad1291e753ce1357cfab329a');
  const model = JSON.parse(bytes);
  assert.equal(model.objects.length, 71); assert.equal(model.relations.length, 18);
  assert.deepEqual(validateExplorationModel(model), { valid: true, errors: [] });
  const original = model.mechanisms.find(m => m.id === 'execution.lifecycle');
  const scope = projectExplorationScope(freeze(model), { rootId: 'worker.runtime' });
  const entry = scope.mechanisms.find(m => m.id === original.id);
  assert.deepEqual(entry, original); assert.equal(entry.owner_id, 'execution');
  assert.equal(scope.mechanismEntries[0].id, original.id);
  const steps = new Map(entry.steps.map(step => [step.id, step]));
  const recovery = entry.cases.find(example => example.id === 'recover_completed');
  assert.ok(recovery.path.indexOf('provider-done') < recovery.path.indexOf('completed-fault'));
  assert.ok(recovery.path.indexOf('recover-result') < recovery.path.indexOf('capture'));
  const afterFault = recovery.path.slice(recovery.path.indexOf('completed-fault'));
  assert.ok(!afterFault.includes('service') && !afterFault.includes('invoke'));
  assert.deepEqual(recovery.path.flatMap(id => steps.get(id).state_changes).filter(c => c.field === 'provider_call_count'),
    [{ field: 'provider_call_count', op: 'increment', value: 1, writer_id: 'execution.providers' }]);
  assert.ok(steps.get('provider-done').state_changes.some(c => c.field === 'checkpoint_phase' && c.value === 'provider_completed'));
  assert.deepEqual(steps.get('recover-result').state_changes,
    [{ field: 'result_recovered', op: 'set', value: true, writer_id: 'worker.runtime' }]);
  const cancellation = steps.get('finish-cancel').state_changes;
  for (const id of ['binding_observation', 'content_observation']) assert.equal(cancellation.find(c => c.field === id).value, 'unknown');
  assert.equal(entry.state_fields.find(f => f.id === 'binding_updated').state_scope, 'observation');
  assert.equal(entry.state_fields.find(f => f.id === 'checkpoint_phase').authority_id, 'execution.scheduling');
  assert.equal(entry.state_fields.find(f => f.id === 'provider_call_count').initial, 0);
  const protocol = projectExplorationScope(model, { rootId: 'hatg-isa' }).root;
  assert.equal(protocol.catalog_id, 'hatg'); assert.equal(protocol.identity.focus_kind, 'protocol:hatg-isa');
  assert.equal(model.catalogs[protocol.catalog_id].manifest.sha256, '2f4f49e3a53f5232ac0b74843823a2e11ebefbb02fa45c03ca47912955600e54');
  // This checks preserved declarations, not execution of the domain interpreter.
  assert.equal(model.completion.status, 'in_progress');
});

test('domains preserve ordering statements and structured state contracts without selecting an interpreter', () => {
  const model = fixture(), entry = model.mechanisms[0];
  model.relations[0].contract.ordering = ['Input precedes output', 'Independent operations have no global order'];
  entry.state_model = { domain: 'synthetic', supported_ops: ['increment'], semantics: { counter: 'Test-only local count' } };
  assert.deepEqual(validateExplorationModel(model), { valid: true, errors: [] });
  const scope = projectExplorationScope(model, { rootId: 'root' });
  assert.deepEqual(scope.internalRelations[0].contract.ordering, model.relations[0].contract.ordering);
  const leaf = projectExplorationScope(model, { rootId: 'leaf' });
  assert.deepEqual(leaf.mechanismEntries[0].state_model, entry.state_model);
  assert.equal(leaf.mechanismEntries[0].adapter, entry.adapter);
  for (const ordering of [[], [''], ['a', null], null, {}]) {
    model.relations[0].contract.ordering = ordering; invalid(model, 'ordering');
  }
  model.relations[0].contract.ordering = 'Explicit order';
  for (const declaration of [{}, [], null, false, '']) {
    entry.state_model = declaration; invalid(model, 'state_model');
  }
});

test('domains local input observations omit authority without inventing a business owner', () => {
  const model = fixture(), field = model.mechanisms[0].state_fields[0];
  field.state_scope = 'observation';
  assert.deepEqual(validateExplorationModel(model), { valid: true, errors: [] });
  const copy = projectExplorationScope(model, { rootId: 'leaf' }).mechanisms[0].state_fields[0];
  assert.deepEqual(copy, field); assert.equal(Object.hasOwn(copy, 'authority_id'), false);
  field.authority_id = 'unavailable'; invalid(model, 'dangling_reference');
  assert.throws(() => projectExplorationScope(model, { rootId: 'leaf' }), e => e.code === 'INVALID_EXPLORATION_SCOPE');
  field.authority_id = null; invalid(model, 'dangling_reference');
  delete field.authority_id; field.state_scope = 'inferred'; invalid(model, 'state_scope');
});

test('domains explicit null condition preserves readable guard and rejects malformed machine guards', () => {
  const model = fixture(), step = model.mechanisms[0].steps[0];
  step.when = null;
  assert.deepEqual(validateExplorationModel(model), { valid: true, errors: [] });
  const copy = projectExplorationScope(model, { rootId: 'leaf' }).mechanisms[0].steps[0];
  assert.equal(copy.when, null); assert.equal(copy.guard, step.guard);
  for (const when of [false, '', [], {}]) { step.when = when; invalid(model, 'condition'); }
  step.when = { field: 'unavailable', equals: true }; invalid(model, 'dangling_reference');
});

test('domains frozen package validates new domain declarations and projects their named mechanisms', {
  skip: !process.env.EXPLORATION_DOMAINS_FIXTURE,
}, () => {
  const bytes = readFileSync(process.env.EXPLORATION_DOMAINS_FIXTURE);
  assert.equal(createHash('sha256').update(bytes).digest('hex'), '053a04e22722a72244bb8262e586425ea1580b7fd17a8a98a13c336764e31ea6');
  const fixture = JSON.parse(bytes);
  assert.equal(fixture.manifest.sha256, '325256bb7ff8a085092db5808c31ab835bf23df43b6242c41ed9ac0087e51a3b');
  const manifestText = fixture.facts[fixture.manifest.address].text;
  assert.equal(createHash('sha256').update(manifestText).digest('hex'), fixture.manifest.sha256);
  assert.equal(Buffer.byteLength(manifestText), fixture.manifest.size);
  const manifest = JSON.parse(manifestText), modelText = fixture.facts[manifest.model.address].text;
  assert.equal(manifest.model.sha256, '116d3a72b0e185fd54e6465452a2b0c6606a708a2ffb4947004fe843d8fc7eb0');
  assert.equal(createHash('sha256').update(modelText).digest('hex'), manifest.model.sha256);
  assert.equal(Buffer.byteLength(modelText), manifest.model.size);
  const model = JSON.parse(modelText);
  assert.deepEqual(model.binding, manifest.binding);
  assert.deepEqual([model.objects.length, model.relations.length, model.mechanisms.length], [155, 117, 95]);
  assert.deepEqual(validateExplorationModel(model), { valid: true, errors: [] });
  assert.equal(new Set(model.mechanisms.map(m => m.adapter)).size, 73);
  // Inspect newly populated domain entries, without rerunning Execution's old
  // simulated journeys or interpreting any domain's state machine.
  const domainEntries = model.objects.filter(o => o.mechanism_ids.some(id => id !== 'execution.lifecycle'));
  assert.ok(domainEntries.length > 0);
  for (const object of domainEntries) {
    const scope = projectExplorationScope(model, { rootId: object.id });
    assert.deepEqual(scope.mechanismEntries.map(m => m.id), object.mechanism_ids);
    for (const mechanism of scope.mechanisms) assert.deepEqual(mechanism, model.mechanisms.find(m => m.id === mechanism.id));
    assert.deepEqual(scope.root.identity, object.identity);
  }
  const observation = projectExplorationScope(model, { rootId: 'interface' }).mechanisms
    .find(m => m.id === 'web.m.refresh-generation').state_fields.find(f => f.id === 'case_input_target_changed');
  assert.equal(observation.state_scope, 'observation');
  assert.equal(Object.hasOwn(observation, 'authority_id'), false);
  assert.equal(model.completion.status, 'in_progress');
  assert.equal(model.completion.pending_object_ids.length, 19);
  for (const id of ['execution', 'worker', 'storage.workspace', 'file-storage']) assert.ok(model.completion.pending_object_ids.includes(id));
});

test('expanded observations and outcomes retain distinct domain representations', () => {
  const model = fixture(), entry = model.mechanisms[0];
  entry.steps[0].observable = ['Input observed', 'Local counter advanced'];
  entry.cases[0].outcome = { counter: 2, error: '', recovered: false };
  assert.deepEqual(validateExplorationModel(model), { valid: true, errors: [] });
  const scope = projectExplorationScope(model, { rootId: 'leaf' });
  assert.deepEqual(scope.mechanismEntries[0].steps[0].observable, entry.steps[0].observable);
  assert.deepEqual(scope.mechanisms[0].cases[0].outcome, entry.cases[0].outcome);
  scope.mechanisms[0].cases[0].outcome.counter = 99;
  assert.equal(entry.cases[0].outcome.counter, 2);
  for (const observable of [null, '', [], {}, false, [''], ['Valid', 4]]) {
    entry.steps[0].observable = observable; invalid(model, 'observable');
  }
  entry.steps[0].observable = 'Observed';
  for (const outcome of [null, '', [], {}, false]) { entry.cases[0].outcome = outcome; invalid(model, 'outcome'); }
});

test('expanded return channel keeps one call edge and its own fixed evidence', () => {
  const model = fixture(), call = model.relations[1];
  model.evidence.push({ ...model.evidence[0], id: 'return-source', symbol: 'Return contract' });
  call.contract.result_flow = { from: call.to, to: call.from, delivery: 'synchronous_return', payload: ['Result'],
    consumption: 'The caller observes the returned value', condition: 'Successful synchronous completion only', evidence_ids: ['return-source'] };
  assert.deepEqual(validateExplorationModel(model), { valid: true, errors: [] });
  const scope = projectExplorationScope(model, { rootId: 'root' });
  assert.equal(scope.boundaryRelations.length, 1);
  assert.deepEqual(scope.boundaryRelations[0].contract.result_flow, call.contract.result_flow);
  assert.deepEqual(scope.boundaryEndpoints.map(o => o.id), ['outside']);
  assert.ok(scope.evidence.some(e => e.id === 'return-source'));
  model.evidence.pop();
  invalid(model, 'dangling_reference');
  assert.throws(() => projectExplorationScope(model, { rootId: 'root' }), error => error.code === 'INVALID_EXPLORATION_SCOPE'
    && error.errors.some(e => e.path.includes('result_flow.evidence_ids')));
});

test('expanded return channel rejects invented endpoints and unknown delivery semantics', () => {
  const valid = { from: 'outside', to: 'right', delivery: 'synchronous_return', payload: ['Result'],
    consumption: 'Observe the result', condition: 'Successful return', evidence_ids: ['source'] };
  for (const [change, code] of [[null, 'result_flow'], [{ ...valid, from: 'right', to: 'outside' }, 'result_flow_endpoints'],
    [{ ...valid, from: 'missing' }, 'dangling_reference'], [{ ...valid, delivery: 'async' }, 'result_flow_delivery'],
    [{ ...valid, payload: ['Result', null] }, 'string_array'], [{ ...valid, condition: '' }, 'text']]) {
    const model = fixture(); model.relations[1].contract.result_flow = change;
    invalid(model, code);
    assert.throws(() => projectExplorationScope(model, { rootId: 'root' }), error => error.code === 'INVALID_EXPLORATION_SCOPE');
  }
});

function localExternalFixture() {
  const model = fixture(), root = object('model-services', null, 'external');
  const children = ['chat-completion', 'typed-decisions', 'native-inference'].map(name => object(`model-services.${name}`, root.id, 'concept'));
  root.child_ids = children.map(child => child.id);
  root.identity = { resource_ref: 'repo_711b6ab6d5c8/file/code/architecture/modules.yaml', focus_owner_id: root.id, focus_kind: null, local_only: true };
  for (const child of children) child.identity = { ...root.identity };
  const localMechanism = mechanism(root.id, 'external-contract-example');
  root.mechanism_ids = [localMechanism.id]; children[0].mechanism_ids = [localMechanism.id];
  model.mechanisms.push(localMechanism); model.objects.push(root, ...children);
  for (const value of [root, ...children]) {
    model.coverage.push({ ...model.coverage[0], object_id: value.id });
    model.completion.pending_object_ids.push(value.id);
  }
  return model;
}

test('local external contract preserves the declared root and concept identity without a global kind', () => {
  const model = localExternalFixture();
  assert.deepEqual(validateExplorationModel(model), { valid: true, errors: [] });
  const scope = projectExplorationScope(model, { rootId: 'model-services' });
  assert.deepEqual(scope.children.map(o => o.id), find(model, 'model-services').child_ids);
  assert.equal(scope.root.kind, 'external'); assert.equal(scope.root.parent_id, null);
  for (const value of [scope.root, ...scope.children]) {
    assert.deepEqual(value.identity, find(model, 'model-services').identity);
    assert.equal(value.identity.focus_kind, null); assert.equal(value.identity.local_only, true);
  }
  const childScope = projectExplorationScope(model, { rootId: 'model-services.chat-completion' });
  assert.equal(childScope.mechanismEntries[0].owner_id, 'model-services');
  assert.deepEqual(childScope.mechanisms[0], model.mechanisms.at(-1));
});

test('local external map identity survives repository relocation without weakening child identity', () => {
  for (const ref of ['repo_123456789abc/file/architecture/modules.yaml',
                    'repo_abcdef123456/file/code/architecture/modules.yaml']) {
    const model = localExternalFixture();
    for (const value of model.objects.filter(value => value.id.startsWith('model-services')))
      value.identity.resource_ref = ref;
    assert.deepEqual(validateExplorationModel(model), { valid: true, errors: [] });
    find(model, 'model-services.chat-completion').identity.resource_ref = 'repo_000000000000/file/architecture/modules.yaml';
    invalid(model, 'local_external_identity');
  }
});

test('local external contract follows composition ancestry across concept levels', () => {
  const model = localExternalFixture(), root = find(model, 'model-services');
  const parent = find(model, 'model-services.chat-completion'), child = find(model, 'model-services.native-inference');
  root.child_ids = root.child_ids.filter(id => id !== child.id);
  parent.child_ids.push(child.id); child.parent_id = parent.id;
  assert.deepEqual(validateExplorationModel(model), { valid: true, errors: [] });
  const scope = projectExplorationScope(model, { rootId: parent.id });
  assert.equal(scope.children[0].id, child.id);
  assert.deepEqual(scope.children[0].identity, root.identity);
});

for (const [name, mutate] of [
  ['broadcastable root', m => find(m, 'model-services').identity.local_only = false],
  ['invented module kind', m => find(m, 'model-services').identity.focus_kind = 'module:model-services'],
  ['module instead of external', m => find(m, 'model-services').kind = 'module'],
  ['different root owner', m => find(m, 'model-services').identity.focus_owner_id = 'root'],
  ['different root source', m => find(m, 'model-services').identity.resource_ref = 'fixture:other'],
  ['external root inside a module', m => { find(m, 'model-services').parent_id = 'root'; find(m, 'root').child_ids.push('model-services'); }],
  ['broadcastable concept', m => find(m, 'model-services.chat-completion').identity.local_only = false],
  ['different concept owner', m => find(m, 'model-services.chat-completion').identity.focus_owner_id = 'root'],
  ['different concept source', m => find(m, 'model-services.chat-completion').identity.resource_ref = 'fixture:other'],
  ['kind invented for concept', m => find(m, 'model-services.chat-completion').identity.focus_kind = 'module:model-services.chat-completion'],
  ['module instead of concept', m => find(m, 'model-services.chat-completion').kind = 'module'],
  ['host used as ancestry', m => {
    const root = find(m, 'model-services'), child = find(m, 'model-services.chat-completion');
    root.child_ids = root.child_ids.filter(id => id !== child.id); child.parent_id = 'left'; child.host_ids = [root.id]; find(m, 'left').child_ids.push(child.id);
  }],
  ['detached concept', m => { find(m, 'model-services').child_ids.shift(); find(m, 'model-services.chat-completion').parent_id = null; }],
  ['concept ancestry cycle', m => {
    const first = find(m, 'model-services.chat-completion'), second = find(m, 'model-services.native-inference');
    find(m, 'model-services').child_ids = ['model-services.typed-decisions'];
    first.parent_id = second.id; second.parent_id = first.id; first.child_ids = [second.id]; second.child_ids = [first.id];
  }],
]) test(`local external contract rejects ${name}`, () => { const model = localExternalFixture(); mutate(model); invalid(model, 'local_external_identity'); });

test('local external contract does not permit other null kinds or weaken missing references and symbols', () => {
  for (const id of ['root', 'wire', 'outside', 'leaf']) {
    const model = localExternalFixture(), value = find(model, id);
    value.identity.focus_kind = null; value.identity.local_only = true;
    invalid(model, 'text');
  }
  const external = localExternalFixture(); find(external, 'outside').kind = 'external';
  find(external, 'outside').identity = { ...find(external, 'model-services').identity, focus_owner_id: 'outside' };
  invalid(external, 'local_identity');
  const unknownConcept = localExternalFixture(), leaf = find(unknownConcept, 'leaf');
  find(unknownConcept, 'left').child_ids = []; leaf.parent_id = 'model-services';
  leaf.identity = { ...find(unknownConcept, 'model-services').identity }; find(unknownConcept, 'model-services').child_ids.push(leaf.id);
  invalid(unknownConcept, 'local_identity');
  const missing = localExternalFixture(); missing.objects = missing.objects.filter(o => o.id !== 'model-services');
  invalid(missing, 'dangling_reference');
  const noSymbol = localExternalFixture(); delete noSymbol.evidence[0].symbol; invalid(noSymbol, 'text');
  noSymbol.evidence[0].symbol = ' '; invalid(noSymbol, 'text');
});

test('local external frozen aggregate validates the new package and changed identity scopes', {
  skip: !process.env.EXPLORATION_LOCAL_EXTERNAL_FIXTURE,
}, () => {
  const bytes = readFileSync(process.env.EXPLORATION_LOCAL_EXTERNAL_FIXTURE);
  assert.equal(createHash('sha256').update(bytes).digest('hex'), 'daf11b2d9e220ca7ef37fc058e798931c6d610b8c98dd65ca326097ef05cecd7');
  assert.equal(bytes.length, 4931539);
  const model = JSON.parse(bytes);
  assert.deepEqual([model.objects.length, model.relations.length, model.mechanisms.length, model.evidence.length], [184, 186, 159, 544]);
  assert.deepEqual(validateExplorationModel(model), { valid: true, errors: [] });
  const root = find(model, 'model-services');
  assert.equal(root.kind, 'external'); assert.equal(root.identity.focus_kind, null);
  for (const id of [root.id, ...root.child_ids]) {
    const scope = projectExplorationScope(model, { rootId: id });
    assert.deepEqual(scope.root.identity, root.identity);
    for (const mechanism of scope.mechanisms) assert.deepEqual(mechanism, model.mechanisms.find(value => value.id === mechanism.id));
    for (const source of scope.evidence) assert.deepEqual(source, model.evidence.find(value => value.id === source.id));
  }
  const fixedSources = model.evidence.filter(e => e.id.startsWith('control.ev.design-'));
  assert.equal(fixedSources.length, 9);
  assert.ok(fixedSources.every(e => typeof e.symbol === 'string' && e.symbol.trim()));
  assert.equal(model.completion.status, 'in_progress');
  assert.deepEqual(model.completion.pending_object_ids, ['execution.providers', 'provider-capabilities', 'execution.providers.catalog']);
});

test('deep internal descendants project to real visible ancestors without importing child scopes', () => {
 const m=fixture();
 m.relations.push(relation('deep-at-root','leaf','right','root','boundary',['right'],'wire'),
  relation('hidden-local','leaf','left','root','boundary',['left']));
 const p=projectExplorationScope(m,{rootId:'root'});
 assert.deepEqual(p.boundaryEndpoints.map(o=>o.id),['outside']);
 const originals=p.internalRelations.flatMap(r=>r.summary?.relations || [r]);
 assert(originals.some(r=>r.id==='deep-at-root' && r.from==='leaf'));
 assert(!originals.some(r=>r.id==='deep'));
 assert.equal(p.localRelations[0].objectId,'left');
 assert.deepEqual(p.localRelations[0].relation,m.relations.at(-1));
 const selected=m.relations.filter(r=>r.scope_views.some(v=>v.scope_id==='root'));
 const retained=[...p.internalRelations,...p.boundaryRelations].flatMap(r=>r.summary?.relations || [r]).concat(p.localRelations.map(l=>l.relation));
 assert.deepEqual(retained.map(r=>r.id).sort(),selected.map(r=>r.id).sort());
 find(m,'leaf').parent_id='leaf';
 assert.throws(()=>projectExplorationScope(m,{rootId:'root'}),e=>e.code==='INVALID_EXPLORATION_SCOPE');
});
