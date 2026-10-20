import { createArchitectureReader, workspaceSource } from './exploration-resources.js';
import { readLessonSources } from './exploration-lessons.js';
import { readCatalogAttachment } from './instruction-catalog-binding.js';
import { resourceAddress, requireReadable } from '../shared/resources.js';
import { selectExplorationDirection } from './exploration-directions.js';
import { MAP_REF, EVOLUTION_REF } from './model.js';
import { schemaMatches } from '../shared/format-tags.js';
import { contentDigest } from '../shared/content-digest.js';

const clone = value => structuredClone(value);
const freeze = value => {
  if (value && typeof value === 'object' && !Object.isFrozen(value)) {
    Object.values(value).forEach(freeze); Object.freeze(value);
  }
  return value;
};
const fail = message => { throw new Error('架构说明暂不可用：' + message); };

function catalogOwnerRoutes(map, model, instructions) {
  const declarations = new Map();
  for (const module of Array.isArray(map?.modules) ? map.modules : []) {
    for (const owner of Array.isArray(module?.instruction_owners) ? module.instruction_owners : []) {
      const matches = declarations.get(owner) || [];
      matches.push(module.id); declarations.set(owner, matches);
    }
  }
  const routes = [];
  for (const owner of new Set(instructions.map(value => value.owner))) {
    const ids = declarations.get(owner);
    if (typeof owner !== 'string' || !owner || ids?.length !== 1) continue;
    const objects = model.objects.filter(value => value.id === ids[0]);
    if (objects.length !== 1) continue;
    const object = objects[0], identity = object.identity;
    if (object.kind !== 'module' || identity.local_only || identity.focus_owner_id !== object.id
      || identity.focus_kind !== `module:${object.id}`) continue;
    routes.push([owner, { kind: 'composition', objectId: object.id, mechanismId: null }]);
  }
  return Object.fromEntries(routes);
}


/** Read authored architecture independently of the code revision it describes. */
export function createExplorationLoader({ resources, signal }) {
  const reader = () => createArchitectureReader(resources, signal);
  const catalogsByBundle = new WeakMap();
  const check = () => { if (signal?.aborted) throw new DOMException('已关闭', 'AbortError'); };

  async function loadPair(pair, { deliveryPin = null, discoveryPin = null } = {}) {
    const mode = 'current';
    const declaration = pair?.exploration;
    if (!declaration) return freeze({ status: 'not_declared', mode });
    if (declaration.status === 'missing') return freeze({ status: 'missing', mode, reason: declaration.reason_code });
    if (!declaration.manifest?.address) fail('没有提供模型入口');
    const read = reader(), entry = await read.json(declaration.manifest);
    if (!schemaMatches(entry.schema, 'tfi.portal-architecture-exploration-manifest.v1')) fail('模型入口格式不受支持');
    const model = await read.json(entry.model);
    if (!schemaMatches(model.schema, 'tfi.portal-architecture-exploration.v1')) fail('模型格式不受支持');
    // Only the shape consumed by the renderers is required. Origin commits,
    // byte digests, excerpts and companion documents do not certify the prose.
    if (!Array.isArray(model.objects) || !model.objects.length
      || !['relations', 'mechanisms', 'evidence'].every(key => Array.isArray(model[key]))) fail('模型缺少可显示的对象或关系');
    const designTitles = Object.fromEntries(model.evidence.filter(source => source.kind === 'design')
      .map(source => [source.frozen?.address || source.address, (source.symbol || '').replace(/^#+\s*/, '')]));
    const fingerprint = contentDigest(JSON.stringify(model));
    return freeze({
      status: 'available', mode, batchKey: JSON.stringify([declaration.manifest.address, fingerprint]),
      pinned: clone({ exploration: declaration.manifest, model: entry.model, discovery: discoveryPin, delivery: deliveryPin }),
      binding: { module_map: { address: MAP_REF }, evolution: { address: EVOLUTION_REF } },
      model, designTitles, catalogs: clone(entry.catalogs || model.catalogs || {}),
      catalogSupplements: clone(pair.catalog_supplements || {}),
      requiredRefs: read.refs(), verification: { model: 'read', sourceBodies: 'unread', catalogs: 'unread' },
    });
  }

  async function loadDelivery(pin, options = {}) {
    const delivery = await reader().json(pin);
    if (!schemaMatches(delivery.schema, 'tfi.portal-architecture-exploration-development-delivery.v1')) fail('交付入口格式不受支持');
    return loadPair(delivery, { ...options, deliveryPin: pin });
  }
  async function loadDiscovered(evidence, options = {}) {
    const pin = evidence?.discoveryPin;
    return loadPair(pin || {}, { ...options, discoveryPin: pin?.pinned });
  }

  async function revalidate(bundle) {
    if (bundle?.status !== 'available') fail('尚未加载说明');
    // A server denial of the displayed document still applies. Neither live
    // code nor the documentation's optional references are access prerequisites.
    const read = reader();
    await Promise.all((bundle.requiredRefs || []).map(read.access));
    check(); return true;
  }

  async function direction(bundle, semantic) {
    if (bundle.mode !== 'current' || !semantic?.kind?.startsWith('direction:')
      || semantic.source !== EVOLUTION_REF || resourceAddress(semantic.ref) !== EVOLUTION_REF) return null;
    const result = requireReadable(await resources.read(EVOLUTION_REF, 'yaml'), EVOLUTION_REF); check();
    return selectExplorationDirection(bundle, result.data, semantic);
  }

  async function source(bundle, evidenceId) {
    const evidence = bundle.model.evidence.find(value => value.id === evidenceId);
    if (!evidence) fail('未找到这条来源');
    const ref = workspaceSource(evidence.address), text = await reader().text(ref);
    return freeze({ ...clone(evidence), text, excerpt: '', ref, sha256: null });
  }

  async function catalog(bundle, id, { lessonSourceSupplement = null, catalogAttachment = null } = {}) {
    const key = JSON.stringify([id, lessonSourceSupplement, catalogAttachment]);
    const cached = catalogsByBundle.get(bundle)?.get(key);
    if (cached) { await revalidate(cached); return cached; }
    const read = reader();
    const attachment = catalogAttachment ? await readCatalogAttachment({ read, pin: catalogAttachment }) : null;
    const declaration = attachment ? { manifest: attachment.catalog_manifest } : bundle.catalogs?.[id];
    if (!declaration) return freeze({ status: 'not_declared', batchKey: bundle.batchKey });
    const entry = await read.json(declaration.manifest);
    if (!schemaMatches(entry.schema, 'tfi.hatg-instruction-catalog-manifest.v1')) fail('指令目录入口格式不受支持');
    const snapshot = await read.json(entry.catalog);
    if (!schemaMatches(snapshot.schema, 'tfi.hatg-instruction-catalog-snapshot.v1')) fail('指令目录格式不受支持');
    if (!Array.isArray(snapshot.instructions) || !snapshot.schemas) fail('指令目录缺少声明');
    const sources = {};
    for (const value of [...(entry.sources || []), ...(entry.design_sources || [])]) {
      if (!value.address) continue;
      const current = { address: workspaceSource(value.address) };
      sources[current.address] = current;
      sources[value.address] = current;
      if (value.frozen?.address) sources[value.frozen.address] = current;
    }
    sources[entry.catalog.address] = entry.catalog;
    const lessonPin = attachment?.lesson_sources || bundle.catalogSupplements?.hatg?.lesson_sources || lessonSourceSupplement;
    let lessons = {}, lessonSources = new Map(), lessonWarning = null;
    if (lessonPin) {
      try {
        const lessonBundle = await readLessonSources({ read: reader(), instructions: snapshot.instructions, pin: lessonPin });
        lessons = lessonBundle.lessons; lessonSources = lessonBundle.sources;
      } catch (error) {
        if (signal?.aborted) throw error;
        lessonWarning = error.message;
      }
    }
    for (const [address, artifact] of lessonSources) {
      sources[address] = artifact;
      sources[artifact.address] = artifact;
    }
    let ownerRoutes = {};
    try {
      const map = requireReadable(await resources.read(MAP_REF, 'yaml'), MAP_REF);
      ownerRoutes = catalogOwnerRoutes(map.data, bundle.model, snapshot.instructions);
    } catch (error) { if (signal?.aborted) throw error; }
    check();
    const result = freeze({
      status: 'available', batchKey: bundle.batchKey, requiredRefs: read.refs(), sources,
      pinned: { manifest: declaration.manifest, catalog: entry.catalog, attachment: catalogAttachment },
      content: { schema: 'tfi.hatg-instruction-workbench.v1',
        catalog: { instructions: snapshot.instructions, schemas: snapshot.schemas },
        lesson_sources: lessons, lessonWarning, owner_routes: ownerRoutes,
        provenance: { ...snapshot.provenance, catalog_ref: entry.catalog.address,
          source_refs: (entry.sources || []).map(value => workspaceSource(value.address)) } },
    });
    if (!catalogsByBundle.has(bundle)) catalogsByBundle.set(bundle, new Map());
    catalogsByBundle.get(bundle).set(key, result); return result;
  }

  async function catalogSource(bundle, catalogBundle, ref) {
    const address = resourceAddress(ref), artifact = catalogBundle?.sources?.[address];
    if (!artifact) fail('未找到这条指令来源');
    const current = workspaceSource(artifact.address), text = await reader().text(current);
    return freeze({ ref: current, originalRef: ref, text });
  }
  return Object.freeze({ loadDelivery, loadDiscovered, loadPair, revalidate, direction, source, catalog, catalogSource });
}
