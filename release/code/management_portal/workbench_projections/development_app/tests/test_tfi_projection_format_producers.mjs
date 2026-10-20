import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { gunzipSync } from 'node:zlib';
import { createHash } from 'node:crypto';
import { createExecutionSimulation } from '../src/architecture/execution-workbench.model.js';
import { readContent, CONTENT_SCHEMA } from '../src/architecture/instruction-workbench.model.js';
import { evolutionBinding, MAP_REF } from '../src/architecture/model.js';
import { independentFixture } from './test_tfi_projection_format_compatibility_architecture_fixtures.mjs';

function freeze(value) {
  if (value && typeof value === 'object') { Object.values(value).forEach(freeze); Object.freeze(value); }
  return value;
}
const sha = value => createHash('sha256').update(value).digest('hex');
const bytes = readFileSync(new URL('./fixtures/frozen-exploration/model.json.gz', import.meta.url));
const frozenModel = freeze(JSON.parse(gunzipSync(bytes)));

test('execution state rejects a historical prefix and restores only its exact canonical version', () => {
  const factory = createExecutionSimulation, tag = 'execution-workbench-state';
  const input = freeze({...frozenModel, batchKey: 'fixed-model:' + sha(bytes),
    mechanism: frozenModel.mechanisms.find(value => value.id === 'execution.lifecycle')});
  const inputBefore = JSON.stringify(input), sim = factory(input);
  sim.advance();
  const saved = sim.getViewState(), scene = sim.getScene();
  assert.equal(saved.schema, `tfi.${tag}.v1`);
  const old = freeze({...saved, schema: `archive.${tag}.v1`});
  const before = JSON.stringify(old);
  sim.reset(); assert.throws(() => sim.restore(old));
  sim.reset(); sim.restore(freeze(structuredClone(saved)));
  assert.deepEqual(sim.getScene(), scene);
  assert.equal(JSON.stringify(old), before);
  assert.equal(JSON.stringify(input), inputBefore);
  for (const change of [{schema: `tfi.${tag}.v2`}, {batchKey: 'other'},
    {mechanismId: 'other'}, {prefix: ['invented']}]) assert.throws(() => sim.restore({...saved, ...change}));
  if ('adapter' in saved) assert.throws(() => sim.restore({...saved, adapter: 'renamed'}));
});

test('new teaching wrapper retains the exact frozen directory, lesson and architecture bindings', async () => {
  const x = independentFixture(), pin = x.seal();
  const batch = await x.loader.loadPair(x.pair, {discoveryPin: x.pairPin});
  const originals = [...x.f.texts], binding = JSON.stringify(batch.binding);
  const result = await x.loader.catalog(batch, 'hatg', {catalogAttachment: pin});
  assert.equal(result.content.schema, 'tfi.hatg-instruction-workbench.v1');
  assert.equal(CONTENT_SCHEMA, result.content.schema);
  assert.equal(readContent(result.content).provenance.commit, x.commit);
  assert.equal(result.content.provenance.architecture_commit, batch.binding.commit);
  assert.deepEqual(result.pinned.attachment, pin);
  const old = freeze({...result.content, schema: 'archive.hatg-instruction-workbench.v1'});
  assert.throws(() => readContent(old));
  assert.equal(old.schema, 'archive.hatg-instruction-workbench.v1');
  assert.deepEqual([...x.f.texts], originals);
  assert.equal(JSON.stringify(batch.binding), binding);
  assert.deepEqual(x.f.writes, []);
});

test('catalog loading rejects a historical format even with an otherwise usable document', async () => {
  const x = independentFixture(), pin = x.seal();
  const batch = await x.loader.loadPair(x.pair, {discoveryPin: x.pairPin});
  const address = x.attachment.catalog_manifest.address, original = x.f.texts.get(address);
  const changed = original.replace('tfi.hatg-instruction-catalog-manifest.v1', 'archive.hatg-instruction-catalog-manifest.v1');
  assert.notEqual(changed, original);
  assert.notEqual(sha(changed), x.attachment.catalog_manifest.sha256);
  x.f.texts.set(address, changed); // Deliberate corruption of a synthetic test source only.
  await assert.rejects(() => x.loader.catalog(batch, 'hatg', {catalogAttachment: pin}), /指令目录入口格式不受支持/);
  assert.deepEqual(x.f.writes, []);
});

test('outer format preserves exact canonical map bindings and digest', () => {
  const raw = 'schema: tfi.portal-architecture.v1\nmodel_version: 2\nmodules: []\n';
  const digest = sha(raw), map = {schema: 'tfi.portal-architecture.v1', model_version: 2};
  const mapping = freeze({ref: MAP_REF, schema: map.schema, model_version: 2, revision: digest});
  const input = {evolutionAvailable: true, evolutionSchema: 'tfi.portal-evolution.v2',
    modelVersion: 2, mapSchema: map.schema, mapHash: digest, moduleMap: mapping};
  assert.equal(evolutionBinding(input).valid, true);
  assert.equal(evolutionBinding({...input, moduleMap: {...mapping, schema: 'archive.portal-architecture.v1'}}).valid, false);
  assert.equal(evolutionBinding({...input, mapHash: sha(raw.replace('tfi.', 'archive.'))}).valid, false);
  assert.equal(mapping.revision, sha(raw));
  assert.equal(mapping.schema, map.schema);
});
