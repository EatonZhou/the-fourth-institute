import test from 'node:test';
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { loadFacts, REFS, semanticObject, linkedDirections } from '../src/evolution/data.js';

const prefix = 'test_tfi_projection_format_compatibility_evolution';
const sha256 = text => createHash('sha256').update(text).digest('hex');
function freeze(value) {
  if (value && typeof value === 'object') {
    Object.values(value).forEach(freeze);
    Object.freeze(value);
  }
  return value;
}

function fixture(schema, options = {}) {
  const mapSchema = options.mapSchema ?? 'tfi.portal-architecture.v1';
  const mapText = `schema: ${mapSchema}\nmodules:\n  - id: direct\n    title: Direct module\n`;
  const mapHash = sha256(mapText);
  const map = { schema: mapSchema, modules: [{ id: 'direct', title: 'Direct module' }] };
  const planText = '## 问题\nProblem\n\n## 预期体验\nExperience\n\n## 成功检验\nCheck';
  const direction = { id: 'future', title: 'Future', status: 'proposed', modules: ['direct'], plans: ['plan_example'] };
  const raw = {
    schema, directions: [direction], plans: [{ id: 'plan_example', title: 'Plan' }],
    module_map: { ref: REFS.modules, schema: options.bindingSchema ?? mapSchema,
      revision: mapHash, snapshot_ref: 'snapshot' },
  };
  if (options.mapping) Object.assign(raw.module_map, options.mapping);
  if (options.invalidDirections) raw.directions = {};
  const evolutionText = `schema: ${schema}\ndirections:\n  - id: future\n`;
  const records = freeze({
    [REFS.evolution]: { data: raw, text: evolutionText, hash: sha256(evolutionText) },
    [REFS.modules]: { data: map, text: mapText, hash: mapHash },
    snapshot: { data: map, text: mapText, hash: mapHash },
    plan_example: { text: planText, hash: sha256(planText) },
  });
  const serializedBefore = JSON.stringify(records);
  const calls = [], nonReadAccess = [];
  const resources = new Proxy({
    async read(ref, format) {
      assert.ok(['yaml', 'text'].includes(format));
      calls.push([ref, format]);
      const record = records[ref];
      assert.ok(record, `unexpected resource: ${ref}`);
      if (options.denied === ref) return { readable: false };
      const hash = options.torn === ref && format === 'text' ? '0'.repeat(64) : record.hash;
      return freeze({ readable: true, data: format === 'yaml' ? record.data : record.text,
        metadata: { content_sha256: hash, record: { version: { number: '2' } } } });
    },
  }, {
    get(target, key) {
      if (key !== 'read') {
        nonReadAccess.push(String(key));
        assert.fail(`loadFacts requested a non-read capability: ${String(key)}`);
      }
      return target[key];
    },
  });
  return { records, resources, calls, mapHash,
    assertReadOnly() {
      assert.deepEqual(nonReadAccess, []);
      assert.equal(JSON.stringify(records), serializedBefore, 'original objects/text/hashes must be unchanged');
    },
  };
}


  for (const tag of ['tfi.portal-evolution.v2']) {
    test(`${prefix} ${tag}: valid read preserves source bytes and repeated results`, async () => {
      const f = fixture(tag);
      const first = await loadFacts(f.resources), second = await loadFacts(f.resources);
      assert.equal(first.validity, 'ready');
      assert.equal(first.mapMatches, true);
      assert.deepEqual(first.directions.map(d => d.id), ['future']);
      assert.deepEqual(first.plans.map(p => p.id), ['plan_example']);
      assert.equal(first.sources.evolution.version, f.records[REFS.evolution].hash);
      assert.equal(first.sources.modules.version, f.mapHash);
      assert.equal(first.documents.plan_example.hash, f.records.plan_example.hash);
      assert.equal(first.documents.plan_example.version, '2');
      assert.equal(first.texts.evolution, f.records[REFS.evolution].text);
      assert.ok(semanticObject({ kind: 'module', id: 'direct' }, first));
      assert.deepEqual(linkedDirections({ kind: 'module:direct', source: REFS.modules }, first), ['future']);
      assert.deepEqual({ ...first, observedAt: null }, { ...second, observedAt: null });
      f.assertReadOnly();
    });
  }

  test(`${prefix} rejects wrong versions, tags, types and missing directions`, async () => {
    for (const schema of [
      'archive.portal-evolution.v1', 'archive.portal-evolution.v2', 'archive.portal-evolution.v3',
      'tfi.portal-evolution.v1', 'tfi.portal-evolution.v3', 'tfi.portal-evolution.v02',
      'tfi.portal-evolution.v2.extra', 'tfi.portal-evolution.v2\n',
      ' tfi.portal-evolution.v2', 'TFI.portal-evolution.v2',
      'tfi.portal-architecture.v2', 'other.portal-evolution.v2', null, 2,
    ]) {
      const f = fixture(schema);
      await assert.rejects(loadFacts(f.resources), /需要正式的演化图 v2/, String(schema));
      assert.equal(f.calls.some(([ref]) => ref === 'plan_example'), false);
      f.assertReadOnly();
    }
    for (const schema of ['tfi.portal-evolution.v2']) {
      const f = fixture(schema, { invalidDirections: true });
      await assert.rejects(loadFacts(f.resources), /需要正式的演化图 v2/);
      f.assertReadOnly();
    }
  });

  test(`${prefix} raw binding schema equality never treats prefixes as equivalent`, async () => {
    for (const schema of ['tfi.portal-evolution.v2']) {
      for (const bindingSchema of ['archive.portal-architecture.v1', 'tfi.portal-architecture.v1']) {
        for (const mapSchema of ['archive.portal-architecture.v1', 'tfi.portal-architecture.v1']) {
          const f = fixture(schema, { bindingSchema, mapSchema });
          const facts = await loadFacts(f.resources);
          const same = bindingSchema === mapSchema && mapSchema === 'tfi.portal-architecture.v1';
          assert.equal(facts.mapMatches, same);
          assert.equal(facts.modules.length > 0, mapSchema === 'tfi.portal-architecture.v1');
          assert.equal('boundModules' in facts, false);
          assert.equal(!!semanticObject({ kind: 'module', id: 'direct' }, facts), same);
          assert.deepEqual(linkedDirections({ kind: 'module:direct', source: REFS.modules }, facts), same ? ['future'] : []);
          assert.equal(facts.sources.modules.version, f.mapHash);
          assert.equal(f.records[REFS.evolution].data.module_map.schema, bindingSchema);
          assert.equal(f.records[REFS.modules].data.schema, mapSchema);
          f.assertReadOnly();
        }
      }
    }
  });

  test(`${prefix} binding ref and model/schema mismatches disable current-map navigation`, async () => {
    for (const schema of ['tfi.portal-evolution.v2']) {
      for (const mapping of [
        { ref: 'other-map' }, { model_version: 2 },
        { schema: 'archive.portal-architecture.v2' }, { schema: 'tfi.portal-architecture.v2' },
      ]) {
        const f = fixture(schema, { mapping });
        const facts = await loadFacts(f.resources);
        assert.equal(facts.validity, 'ready');
        assert.equal(facts.mapMatches, false);
        assert.equal(semanticObject({ kind: 'module', id: 'direct' }, facts), null);
        assert.deepEqual(linkedDirections({ kind: 'module:direct', source: REFS.modules }, facts), []);
        assert.equal(f.calls.some(([ref]) => ref === 'snapshot'), false);
        f.assertReadOnly();
      }
    }
  });

  test(`${prefix} paired byte hashes and authorization remain mandatory`, async () => {
    for (const schema of ['tfi.portal-evolution.v2']) {
      for (const ref of [REFS.evolution, REFS.modules]) {
        const f = fixture(schema, { torn: ref });
        await assert.rejects(loadFacts(f.resources), /正文与结构版本不同/);
        assert.equal(f.calls.filter(([r]) => r === ref).length, 4, 'one bounded retry of the pair');
        f.assertReadOnly();
      }
      const denied = fixture(schema, { denied: REFS.evolution });
      assert.deepEqual(await loadFacts(denied.resources), { validity: 'revoked', deniedRefs: [REFS.evolution] });
      denied.assertReadOnly();
    }
  });
