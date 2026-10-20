import test from 'node:test';
import assert from 'node:assert/strict';
import { createExplorationLoader } from '../src/architecture/exploration-data.js';
import { createEvidenceLoader } from '../src/architecture/data.js';
import { createArchitectureCandidateLoader } from '../src/architecture/exploration-entry.js';
import { MAP_REF, MANIFEST_REF } from '../src/architecture/model.js';

function fixture() {
  const texts = new Map(), denied = new Map(), reads = [], resolves = [];
  const put = (address, value) => {
    texts.set(address, typeof value === 'string' ? value : JSON.stringify(value));
    return { address, sha256: 'obsolete', size: 1 };
  };
  const current = 'repo_111111111111/file/systemd/tfi-ai-socket-relay.service';
  const old = current.replace('/file/', '/commit/' + 'a'.repeat(40) + '/file/');
  const model = { schema:'tfi.portal-architecture-exploration.v1', objects: [{ id: 'worker', kind: 'module', identity: {
    local_only: false, resource_ref: MAP_REF, focus_owner_id: 'worker', focus_kind: 'module:worker' } }],
    relations: [], mechanisms: [], evidence: [{ id: 'relay', kind: 'code', address: old,
      sha256: 'old', version: 'old', excerpt: 'old', frozen: { address: 'gone:frozen' } }] };
  const modelPin = put('fixture:model', model);
  const manifest = put('fixture:entry', { schema:'tfi.portal-architecture-exploration-manifest.v1', model: modelPin, binding: { commit: 'different' },
    contract: { address: 'gone:contract' }, sources: [{ address: 'gone:source' }] });
  const pair = { exploration: { status: 'available', manifest }, base_manifest: { address: 'gone:base' } };
  put(MANIFEST_REF, pair); put(current, 'current workspace text'); put(MAP_REF, { modules: [
    { id: 'worker', instruction_owners: ['worker'] }] });
  const resources = {
    async resolve(ref) { resolves.push(ref); return result(ref); },
    async read(ref, format) {
      reads.push(ref); const value = result(ref);
      return { ...value, data: value.readable ? format === 'yaml' ? JSON.parse(texts.get(ref)) : texts.get(ref) : null };
    },
  };
  function result(ref) {
    const code = denied.get(ref) || (!texts.has(ref) ? 'file_not_found' : null);
    return code ? { readable: false, ref, error: { code } } : { readable: true, ref };
  }
  return { texts, put, reads, resolves, denied, model, modelPin, manifest, pair, current, old, resources,
    loader: createExplorationLoader({ resources }) };
}

test('formal discovery loads documentation without code, contracts, origin commits or matching digests', async () => {
  const f = fixture(), candidate = createArchitectureCandidateLoader({
    evidenceLoader: createEvidenceLoader({ resources: f.resources }), explorationLoader: f.loader });
  const value = await candidate.discover();
  assert.equal(value.kind, 'exploration'); assert.equal(value.exploration.model.objects[0].id, 'worker');
  assert.deepEqual(f.reads, [MANIFEST_REF, f.manifest.address, f.modelPin.address]);
  assert.equal(value.exploration.binding.commit, undefined);
});
test('same document addresses can refresh content without revision certificates', async () => {
  const f = fixture(), previous = await f.loader.loadPair(f.pair);
  f.model.objects[0].title = 'Updated explanation'; f.put(f.modelPin.address, f.model);
  const next = await f.loader.loadPair(f.pair);
  assert.notEqual(previous.batchKey, next.batchKey);
  assert.equal(next.model.objects[0].title, 'Updated explanation');
  assert.equal(previous.model.objects[0].title, undefined);
});
test('source click reads current code every time without comparing historical excerpts', async () => {
  const f = fixture(), bundle = await f.loader.loadPair(f.pair);
  assert.equal((await f.loader.source(bundle, 'relay')).ref, f.current);
  f.put(f.current, 'changed after first click');
  assert.equal((await f.loader.source(bundle, 'relay')).text, 'changed after first click');
  assert(!f.reads.includes(f.old)); assert(!f.reads.includes('gone:frozen'));
});
test('missing optional source keeps its file error and never invalidates the graph', async () => {
  const f = fixture(), bundle = await f.loader.loadPair(f.pair); f.texts.delete(f.current);
  await assert.rejects(f.loader.source(bundle, 'relay'), { code: 'file_not_found' });
  assert(await f.loader.revalidate(bundle)); assert.equal(bundle.status, 'available');
  assert(f.resolves.every(ref => [f.manifest.address, f.modelPin.address].includes(ref)));
});
test('real document denial is preserved and prior access grants are not reused', async () => {
  const f = fixture(), bundle = await f.loader.loadPair(f.pair);
  f.denied.set(f.modelPin.address, 'permission_denied');
  await assert.rejects(f.loader.revalidate(bundle), { code: 'permission_denied' });
  await assert.rejects(f.loader.loadPair(f.pair), { code: 'permission_denied' });
  f.denied.clear(); assert.equal((await f.loader.loadPair(f.pair)).status, 'available');
});
test('absent and malformed documents have their own errors', async () => {
  const f = fixture();
  assert.equal((await f.loader.loadPair({})).status, 'not_declared');
  f.put(f.modelPin.address, 'not json'); await assert.rejects(f.loader.loadPair(f.pair), /JSON/);
  f.put(f.modelPin.address, {schema:'tfi.portal-architecture-exploration.v1'}); await assert.rejects(f.loader.loadPair(f.pair), /模型缺少/);
});
function withCatalog(f) {
  const catalog = f.put('fixture:catalog', { schema:'tfi.hatg-instruction-catalog-snapshot.v1', instructions: [{ instruction: 'Example', owner: 'worker' }],
    schemas: {}, provenance: { commit: 'another revision' } });
  const manifest = f.put('fixture:catalog-entry', { schema:'tfi.hatg-instruction-catalog-manifest.v1', catalog, binding: { commit: 'different again' },
    sources: [{ address: f.old, frozen: { address: 'gone:catalog-code' } }] });
  const lessons = f.put('fixture:lessons', { schema:'tfi.hatg-lesson-source-supplement.v1', lessons: [{ instruction: 'Example', explanation: 'Authored text' }],
    sources: [{ address: f.old, frozen: { address: 'gone:lesson-code' } }] });
  const lessonPin = f.put('fixture:lesson-entry', { schema:'tfi.hatg-lesson-source-supplement-manifest.v1', artifact: lessons, compatible_exploration_manifests: [] });
  const attachment = f.put('fixture:attachment', { schema:'tfi.hatg-catalog-attachment.v1', catalog_manifest: manifest, lesson_sources: lessonPin });
  return { catalog, manifest, lessonPin, attachment };
}
test('catalog ignores commit and proof files; current sources open on demand', async () => {
  const f = fixture(), c = withCatalog(f), bundle = await f.loader.loadPair(f.pair), n = f.reads.length;
  const catalog = await f.loader.catalog(bundle, 'hatg', { catalogAttachment: c.attachment });
  assert.equal(catalog.content.lesson_sources.Example.explanation, 'Authored text');
  assert.equal(catalog.content.owner_routes.worker.objectId, 'worker');
  assert(!f.reads.slice(n).includes(f.current)); assert(!f.reads.some(ref => ref.startsWith('gone:')));
  assert.equal((await f.loader.catalogSource(bundle, catalog, 'gone:lesson-code#L1')).ref, f.current);
  f.denied.set(c.catalog.address, 'permission_denied');
  await assert.rejects(f.loader.catalog(bundle, 'hatg', { catalogAttachment: c.attachment }), { code: 'permission_denied' });
});
test('missing optional lessons and map keep catalog readable with a local warning', async () => {
  const f = fixture(), c = withCatalog(f), bundle = await f.loader.loadPair(f.pair);
  f.texts.delete(c.lessonPin.address); f.texts.delete(MAP_REF);
  const catalog = await f.loader.catalog(bundle, 'hatg', { catalogAttachment: c.attachment });
  assert.equal(catalog.content.catalog.instructions[0].instruction, 'Example');
  assert.match(catalog.content.lessonWarning, /来源文件不存在/);
  assert.deepEqual(catalog.content.owner_routes, {}); assert.equal(bundle.status, 'available');
});
test('cancellation prevents reads', async () => {
  const f = fixture(), abort = new AbortController(); abort.abort();
  await assert.rejects(createExplorationLoader({ resources: f.resources, signal: abort.signal }).loadPair(f.pair), { name: 'AbortError' });
  assert.deepEqual(f.reads, []);
});

test('obsolete history mode cannot load or retain a historical architecture',async()=>{
 const f=fixture(),bundle=await f.loader.loadPair(f.pair,{mode:'history',commit:'old'});
 assert.equal(bundle.mode,'current');assert(!f.reads.includes(f.old));
});
