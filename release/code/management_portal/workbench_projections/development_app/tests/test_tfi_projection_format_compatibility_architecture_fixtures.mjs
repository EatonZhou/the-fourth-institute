// Synthetic contract builders derived from architecture-exploration-data.test.mjs.
// Tags vary only newly constructed in-memory test inputs; no stored fixture is changed.
import { createHash } from 'node:crypto';
import { createExplorationLoader as loaderFactory } from '../src/architecture/exploration-data.js';
const contractFixtures = new WeakMap();
export const createExplorationLoader = options => {
  const f=contractFixtures.get(options.resources), loader=loaderFactory({...options,readingContracts:f?.readingContracts});
  if(f){
    const pair=loader.loadPair, discovered=loader.loadDiscovered;
    return {...loader,
      loadPair:(value,opts)=>{f.authorize(value,opts?.discoveryPin);return pair(value,opts);},
      loadDiscovered:(evidence,opts)=>{const pin=evidence?.discoveryPin?.pinned;if(pin)f.authorize(JSON.parse(f.texts.get(pin.address)),pin);return discovered(evidence,opts);},
    };
  }
  return loader;
};

// Contract-only fixture: it describes no Portal behavior or actual permissions.
export function fixture({ modules = [], tags = {} } = {}) {
  const texts = new Map(), denied = new Set(), reads = [], resolves = [];
  const put = (name, value) => {
    const text = typeof value === 'string' ? value : JSON.stringify(value, (key, item) => ['schema', 'discovery_schema'].includes(key) && Object.hasOwn(tags, item) ? tags[item] : item);
    const sha256 = createHash('sha256').update(text).digest('hex');
    const artifact = { address: `fixture:${name}:${sha256}`, sha256, size: Buffer.byteLength(text) };
    texts.set(artifact.address, text); return artifact;
  };
  const commit = 'a'.repeat(40), map = put('map', { modules }), evolution = put('evolution', 'evolution bytes');
  const consumer = put('consumer', { commit, map, upstream_evolution: { source: evolution } });
  const base = put('base', { schema: 'tfi.portal-architecture-evidence.v1', current_commit: commit, consumer_contract: consumer,
    snapshots: [{ commit, files: { 'architecture/modules.yaml': { ...map, status: 'available' }, 'architecture/evolution.yaml': evolution } }] });
  const frozen = put('source', 'line one\nline two\n');
  const binding = { commit, base_manifest: base, consumer_contract: consumer, module_map: map, evolution };
  const model = { schema: 'tfi.portal-architecture-exploration.v1', revision: 1, binding,
    objects: [{ id: 'test-root', kind: 'module', title: 'Test object', responsibility: 'Contract test only', parent_id: null,
      child_ids: [], host_ids: [], state_owned: [], relation_ids: [], protocol_ids: [], mechanism_ids: [], evidence_ids: ['test-source'],
      identity: { resource_ref: 'fixture:identity', focus_owner_id: 'test-root', focus_kind: 'module:test-root', local_only: false } }],
    relations: [], mechanisms: [], evidence: [{ id: 'test-source', kind: 'code', claim: 'Test excerpt', address: 'fixture:original',
      version: commit, sha256: frozen.sha256, start_line: 1, end_line: 2, symbol: 'test', excerpt: 'line one\nline two', frozen }],
    coverage: [], work_items: [], completion: { status: 'in_progress' } };
  const entry = { schema: 'tfi.portal-architecture-exploration-manifest.v1', revision: 1, binding,
    contract: put('contract', 'test contract'), sources: [frozen], knowledge_snapshot: 'observed_nontransactional' };
  let pair, delivery; const readingContracts = [];
  function seal() {
    entry.model = put('model', model);
    pair = { base_manifest: base, consumer_contract: consumer, exploration: { status: 'available', manifest: put('entry', entry) } };
    const amendment = put('reading-contract', { schema:'tfi.architecture-consumer-amendment.v1', status:'authorized_owner_contract',
      compatible: { binding, manifest:pair.exploration.manifest,model:entry.model,contract:entry.contract,catalogs:entry.catalogs || {} } });
    readingContracts.push({ model:entry.model,contract:amendment });
    delivery = put('delivery', { schema: 'tfi.portal-architecture-exploration-development-delivery.v1', revision: 1,
      purpose: 'development_integration', discovery_replaced: false, ...pair });
  }
  seal();
  const writes = [];
  const resources = {
    async write(...args) { writes.push(args); throw new Error('unexpected persistent write'); },
    async resolve(address) { resolves.push(address); return { readable: !denied.has(address) }; },
    async read(address, format) { reads.push(address); return { readable: !denied.has(address) && texts.has(address),
      data: format === 'yaml' ? JSON.parse(texts.get(address)) : texts.get(address) }; },
  };
  function authorize(pair, pairPin) {
    const manifest=pair?.exploration?.manifest;if(!manifest || !texts.has(manifest.address))return;
    const e=JSON.parse(texts.get(manifest.address));
    const contract=put('reading-contract',{schema:'tfi.architecture-consumer-amendment.v1',status:'authorized_owner_contract',compatible:{
      binding:e.binding,manifest,model:e.model,contract:e.contract,pair:pairPin,catalogs:e.catalogs || {},catalog_supplements:pair.catalog_supplements}});
    readingContracts.push({model:e.model,contract});
  }
  contractFixtures.set(resources, {readingContracts,authorize,texts});
  return { writes, resources, put, texts, readingContracts, denied, reads, resolves, model, entry, base, consumer, seal,
    get pair() { return pair; }, get delivery() { return delivery; } };
}

export function withCatalog(f) {
  const frozen = f.put('catalog-source', 'declaration\n');
  const source = { ...frozen, address: 'fixture:catalog-original', frozen, version: f.model.binding.commit };
  const instruction = { instruction: 'Example', category: 'example', owner: 'example', description: 'Contract test', mutates: false, streaming: false };
  const snapshot = { schema: 'tfi.hatg-instruction-catalog-snapshot.v1', revision: 1,
    provenance: { commit: source.version, observed_at: '2026-10-15T00:00:00Z' }, instructions: [instruction],
    schemas: { Example: { ...instruction, parameters: [], result: [] } },
    instruction_sources: { Example: { address: source.address, sha256: frozen.sha256, frozen, start_line: 1, end_line: 1 } },
    execution_contract: { sources: [source.address] } };
  const entry = { schema: 'tfi.hatg-instruction-catalog-manifest.v1', revision: 1, commit: source.version,
    sources: [source], design_sources: [], catalog: f.put('catalog', snapshot) };
  const seal = () => { entry.catalog = f.put('catalog', snapshot); f.entry.catalogs = f.model.catalogs = { hatg: { status: 'available', manifest: f.put('catalog-entry', entry) } }; f.seal(); };
  seal(); return { source, snapshot, entry, seal };
}


export function withLessons(f) {
  const catalog = withCatalog(f), frozen = f.put('lesson-body', 'first\nimplementation\nlast');
  const source = { ...frozen, address: 'fixture:lesson-original', frozen, version: f.model.binding.commit };
  const common = { revision: 1, commit: f.model.binding.commit, binding: f.model.binding,
    catalog_manifest: f.model.catalogs.hatg.manifest, compatible_exploration_manifests: [f.pair.exploration.manifest] };
  const body = { ...common, schema: 'tfi.hatg-lesson-source-supplement.v1', sources: [source],
    lessons: [{ instruction: 'Example', source_address: source.address, source_sha256: frozen.sha256,
      canonical_ref: source.address + '#L2', requested_ref: source.address + '#L1', start_line: 2, end_line: 2, excerpt: 'implementation' }] };
  const entry = { ...common, schema: 'tfi.hatg-lesson-source-supplement-manifest.v1', sources: [frozen] };
  const seal = () => { entry.artifact = f.put('lessons', body); return f.put('lesson-entry', entry); };
  return { catalog, source, entry, body, seal, pin: seal() };
}


export function independentFixture(tags = {}) {
  const f = fixture({ tags }), old = withLessons(f), commit = 'b'.repeat(40);
  const pair = { schema: 'tfi.portal-architecture-discovery.v1', revision: 1, ...f.pair,
    catalog_supplements: { hatg: { lesson_sources: old.pin } } };
  const pairPin = f.put('pair', pair), root = { discovery_schema: pair.schema, discovery_revision: 1,
    immutable_discovery: pairPin, immutable_manifest: f.base, exploration: pair.exploration };
  const compat = [{ id: 'test', discovery_root: f.put('root', root), discovery_pair: pairPin,
    exploration_manifest: pair.exploration.manifest, model: f.entry.model, binding: f.model.binding }];
  const contract = { schema: 'tfi.hatg-independent-catalog-contract.v1', revision: 1, architecture_compatibility: compat };
  const contractPin = f.put('independent-contract', contract);
  const snapshot = structuredClone(old.catalog.snapshot); snapshot.provenance.commit = commit;
  const catalogEntry = { ...structuredClone(old.catalog.entry), commit,
    sources: old.catalog.entry.sources.map(value => ({...value, version: commit})), catalog: f.put('independent-catalog', snapshot) };
  const catalogPin = f.put('independent-catalog-entry', catalogEntry);
  const common = { revision: 2, commit, catalog_manifest: catalogPin, architecture_compatibility: compat };
  const body = { ...common, schema: 'tfi.hatg-lesson-source-supplement.v2', lessons: structuredClone(old.body.lessons),
    sources: old.body.sources.map(value => ({...value, version: commit})) };
  const entry = { ...common, schema: 'tfi.hatg-lesson-source-supplement-manifest.v2', sources: old.entry.sources };
  const attachment = { schema: 'tfi.hatg-catalog-attachment.v1', revision: 1, catalog_id: 'hatg', catalog_commit: commit,
    catalog_manifest: catalogPin, consumer_contract: contractPin, architecture_compatibility: compat };
  const seal = () => { entry.artifact = f.put('independent-lessons', body);
    attachment.lesson_sources = f.put('independent-lesson-entry', entry); return f.put('attachment', attachment); };
  const loader = createExplorationLoader({resources: f.resources, catalogContracts: [contractPin]});
  return {f, old, pair, pairPin, root, compat, attachment, entry, body, seal, loader, commit};
}

