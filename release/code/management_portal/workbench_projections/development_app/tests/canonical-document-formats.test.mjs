import test from 'node:test';
import assert from 'node:assert/strict';
import { fixture, independentFixture, withLessons } from './test_tfi_projection_format_compatibility_architecture_fixtures.mjs';
import { createExplorationLoader } from '../src/architecture/exploration-data.js';
import { readLessonSources } from '../src/architecture/exploration-lessons.js';
import { readCatalogAttachment } from '../src/architecture/instruction-catalog-binding.js';
import { loadDiscoveredBatch } from '../src/architecture/discovery.js';

const invalid = tag => [tag.replace(/^tfi\./, 'archive.'), tag + '\n', tag.replace(/\.v\d+$/, '.v999'), null];

test('model, delivery and catalog readers reject noncanonical document tags', async () => {
  for (const name of ['portal-architecture-exploration-manifest', 'portal-architecture-exploration',
    'portal-architecture-exploration-development-delivery', 'hatg-instruction-catalog-manifest',
    'hatg-instruction-catalog-snapshot', 'hatg-catalog-attachment']) {
    const tag = `tfi.${name}.v1`;
    for (const schema of invalid(tag)) {
      const x = independentFixture({[tag]: schema}), pin = x.seal(), before = [...x.f.texts];
      await assert.rejects(async () => {
        const loader = createExplorationLoader({resources:x.f.resources});
        const batch = name.endsWith('development-delivery') ? await loader.loadDelivery(x.f.delivery) : await loader.loadPair(x.pair);
        if (name.startsWith('hatg-')) await loader.catalog(batch, 'hatg', {catalogAttachment:pin});
      }, /格式不受支持/);
      assert.deepEqual([...x.f.texts], before);
    }
  }
});

test('lesson readers retain exact v1 and v2 semantics and reject historical or mismatched tags', async () => {
  for (const version of [1, 2]) {
    const f = fixture(), bundle = version === 1 ? withLessons(f) : independentFixture();
    const facts = version === 1 ? f : bundle.f;
    const attachment = bundle.seal(), pin = version === 1 ? attachment : bundle.attachment.lesson_sources;
    const read = {json: async ref => JSON.parse(facts.texts.get(ref.address))};
    assert.ok((await readLessonSources({read, pin, instructions:[{instruction:'Example'}]})).lessons.Example);
    for (const [ref, name] of [[pin, 'hatg-lesson-source-supplement-manifest'], [bundle.entry.artifact, 'hatg-lesson-source-supplement']]) {
      const original = facts.texts.get(ref.address), document = JSON.parse(original);
      for (const schema of [...invalid(`tfi.${name}.v${version}`), `tfi.${name}.v${version === 1 ? 2 : 1}`]) {
        facts.texts.set(ref.address, JSON.stringify({...document, schema}));
        await assert.rejects(readLessonSources({read, pin, instructions:[]}), /格式不受支持/);
      }
      facts.texts.set(ref.address, original);
    }
  }
});

test('discovery, evidence and attachment formats reject historical prefixes before consumption', async () => {
  const expected = 'tfi.portal-architecture-discovery.v1';
  for (const schema of invalid(expected)) {
    const root = {discovery_schema:schema, immutable_discovery:{address:'index'}};
    const readJSON = async () => ({schema});
    // Null is not a declaration on the pointer DTO, but its referenced document
    // must still have the complete canonical format.
    await assert.rejects(loadDiscoveredBatch(root,{readJSON}), /格式不受支持/);
  }
  for (const schema of invalid('tfi.portal-architecture-evidence.v1')) {
    await assert.rejects(loadDiscoveredBatch({immutable_manifest:{address:'evidence'}},
      {readJSON:async () => ({schema,snapshots:[]})}), /格式不受支持/);
  }
  for (const schema of invalid('tfi.hatg-catalog-attachment.v1')) {
    await assert.rejects(readCatalogAttachment({pin:{address:'attachment'},
      read:{json:async () => ({schema,catalog_manifest:{address:'catalog'}})}}), /格式不受支持/);
  }
});
