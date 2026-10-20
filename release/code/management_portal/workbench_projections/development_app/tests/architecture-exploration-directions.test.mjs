import test from 'node:test';
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { selectExplorationDirection, projectDirectionHighlights } from '../src/architecture/exploration-directions.js';
import { createExplorationLoader } from '../src/architecture/exploration-data.js';
import { EVOLUTION_REF } from '../src/architecture/model.js';

const module = (id, parent_id) => ({ id, parent_id, kind: 'module' });
function setup() {
  const objects = [module('web', null), module('web.projections', 'web'), module('interface', null), module('interface.workbenches', 'interface')];
  const evolution = { schema: 'tfi.portal-evolution.v2', module_map: { revision: 'a'.repeat(64), model_version: 2 },
    directions: [{ id: 'future', title: 'Future', modules: ['web.projections', 'interface.workbenches'], plans: [] }] };
  const semantic = { ref: EVOLUTION_REF + '#L10', kind: 'direction:future', status: 'proposed', source: EVOLUTION_REF, observed_at: '2026-10-15T15:00:00Z' };
  const text = JSON.stringify(evolution), pin = { address: 'fixture:fixed-evolution', sha256: createHash('sha256').update(text).digest('hex'), size: Buffer.byteLength(text) };
  const bundle = { status: 'available', mode: 'current', batchKey: 'batch-a', binding: { module_map: { sha256: 'a'.repeat(64) }, evolution: pin },
    model: { objects }, requiredRefs: ['fixture:fixed-evolution'] };
  return { evolution, semantic, bundle, text };
}
test('direction retains its five fields and direct scope without requiring a Plan', () => {
  const f = setup(), result = selectExplorationDirection(f.bundle, f.evolution, f.semantic);
  assert.deepEqual(result.semantic, f.semantic); assert.deepEqual(result.moduleIds, ['web.projections', 'interface.workbenches']);
  assert.equal(result.mappingValid, true); assert.equal(result.incomplete, false);
  assert.deepEqual(f.bundle.model.objects.map(o => o.id), ['web', 'web.projections', 'interface', 'interface.workbenches']);
});
test('map revision drift does not hide known related modules', () => {
  const f = setup(); f.evolution.module_map.revision = 'b'.repeat(64);
  const result = selectExplorationDirection(f.bundle, f.evolution, f.semantic);
  assert.deepEqual(result.moduleIds, ['web.projections', 'interface.workbenches']); assert.deepEqual(result.semantic, f.semantic); assert(!result.incomplete);
});
test('unknown, foreign-source and historical directions do not become local selections', () => {
  const f = setup();
  for (const bad of [{ ...f.semantic, kind: 'direction:unknown' }, { ...f.semantic, source: 'elsewhere' },
    { ...f.semantic, ref: 'elsewhere' }, { ...f.semantic, status: 'historical' }]) {
    assert.equal(selectExplorationDirection(f.bundle, f.evolution, bad), null);
  }
  assert.equal(selectExplorationDirection({ ...f.bundle, mode: 'history' }, f.evolution, f.semantic), null);
});
test('visible ancestors represent association while the actual child identities remain unchanged', () => {
  const f = setup(), direction = selectExplorationDirection(f.bundle, f.evolution, f.semantic), [web, projection, ui, workbench] = f.bundle.model.objects;
  assert.deepEqual(projectDirectionHighlights(f.bundle.model, { root: null, children: [web, ui], boundaryEndpoints: [] }, direction), ['web', 'interface']);
  assert.deepEqual(projectDirectionHighlights(f.bundle.model, { root: web, children: [projection], boundaryEndpoints: [workbench] }, direction), ['web.projections', 'interface.workbenches']);
  assert.deepEqual(direction.moduleIds, ['web.projections', 'interface.workbenches']);
});
test('a hidden ancestor does not promote its child or expose an absent endpoint', () => {
  const f = setup(), direction = selectExplorationDirection(f.bundle, f.evolution, f.semantic);
  const model = { objects: f.bundle.model.objects.filter(x => x.id !== 'web') };
  assert.deepEqual(projectDirectionHighlights(model, { root: null, children: [model.objects.find(x => x.id === 'interface')], boundaryEndpoints: [] }, direction), ['interface']);
  assert.deepEqual(projectDirectionHighlights(model, { root: null, children: [], boundaryEndpoints: [] }, direction), []);
});
test('direction loading reads the current workspace and preserves explicit denial', async () => {
  const f = setup(), denied = new Set(), reads = [], resources = {
    async resolve(ref) { return { readable: !denied.has(ref) }; },
    async read(ref, format) { reads.push([ref, format]); return { readable: !denied.has(ref), data: format === 'yaml' ? f.evolution : f.text, error: denied.has(ref) ? { code: 'permission_denied' } : undefined }; },
  };
  const loader = createExplorationLoader({ resources });
  assert.deepEqual((await loader.direction(f.bundle, f.semantic)).semantic, f.semantic);
  assert(reads.every(([ref]) => ref === EVOLUTION_REF));
  denied.add(EVOLUTION_REF);
  await assert.rejects(loader.direction(f.bundle, f.semantic), e => e.reason === 'permission_denied');
  denied.clear(); denied.add(f.bundle.binding.evolution.address);
  assert(await loader.direction(f.bundle, f.semantic));
});
test('current evolution changes can supply labels without a frozen digest', async () => {
  const f = setup(), loader = createExplorationLoader({ resources: {
    async resolve() { return { readable: true }; }, async read() { return { readable: true, data: { ...f.evolution, directions: [{ ...f.evolution.directions[0], title: 'Updated' }] } }; },
  } });
  assert.equal((await loader.direction(f.bundle, f.semantic)).title, 'Updated');
});
