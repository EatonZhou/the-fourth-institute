import { REPOSITORY } from './model.js';

import { descriptor, sameArtifact, supplementEntryMatches, fullBindingMatches, verificationMatches } from './discovery.js';

const clone = value => structuredClone(value);
const unique = items => [...new Set(items)];
const empty = (status, reason, extra = {}) => ({ status, reason, coverage: 'partial', relations: [], ...extra });
class Unavailable extends Error {
  constructor(reason) { super(reason); this.reason = reason; }
}

function bindingMatches(data, binding) {
  return data?.mode === 'current' && binding
    && sameArtifact(data.manifest?.pinned, binding.base_manifest)
    && sameArtifact(data.manifest?.consumer_contract, binding.consumer_contract)
    && data.manifest?.current_commit === binding.commit
    && data.mapHash === binding.map?.sha256
    && data.evolutionHash === binding.evolution_sha256
    && (!binding.evolution || fullBindingMatches(binding, data.manifest, data.manifest.discoveryPin?.consumer));
}

// The complete ancestor chain must be visible; a missing parent is not a new root.
function rootOf(modules, id) {
  const seen = new Set(); let item = modules.get(id);
  while (item?.parent) {
    if (seen.has(item.id)) return null;
    seen.add(item.id); item = modules.get(item.parent);
  }
  return item?.id || null;
}

function endpointFor(modules, endpoint, scopeRoot) {
  const item = modules.get(endpoint.module_id);
  if (!item || !rootOf(modules, item.id)) return null;
  let display; let displayModuleId = item.id;
  if (item.id === scopeRoot) display = 'root_boundary_role';
  else if (item.parent === scopeRoot && endpoint.component_id === item.id) display = 'direct_component';
  else if (item.deployed_in?.includes(scopeRoot)) display = 'shared_component_boundary';
  else { display = 'external_root_boundary'; displayModuleId = rootOf(modules, item.id); }
  return { moduleId: item.id, displayModuleId, display, role: endpoint.role, componentId: endpoint.component_id };
}

/** Project an already authorized supplement; never changes modules, protocols or scenes. */
export function projectComponentRelations(data, bundle, scopeRoot) {
  const result = { status: bundle?.status || 'unavailable', reason: bundle?.reason || 'not_loaded',
    coverage: 'partial', componentRelations: [], contextModuleIds: [] };
  if (bundle?.status !== 'available') return result;
  if (!bindingMatches(data, bundle.binding)) return { ...result, status: 'unavailable', reason: 'binding_mismatch' };
  const modules = new Map((data.modules || []).map(item => [item.id, item]));
  const protocols = new Set((data.protocols || []).map(item => item.id));
  if (!modules.has(scopeRoot)) return { ...result, status: 'unavailable', reason: 'scope_unavailable' };
  const relationIds = new Set(bundle.scopes?.find(scope => scope.root_module_id === scopeRoot)?.relation_ids || []);
  for (const relation of bundle.relations) {
    if (!relationIds.has(relation.id) || !relation.scope_roots.includes(scopeRoot)) continue;
    if (relation.protocol_id && !protocols.has(relation.protocol_id)) continue;
    if (relation.related_protocol_ids.some(id => !protocols.has(id))) continue;
    if ((relation.interaction.via || []).some(via => !rootOf(modules, via.module_id))) continue;
    const endpoints = relation.endpoints.map(endpoint => endpointFor(modules, endpoint, scopeRoot));
    if (endpoints.some(endpoint => !endpoint)) continue;
    const interaction = clone(relation.interaction);
    // This relation is a transaction consistency boundary, never an RPC arrow.
    if (relation.kind === 'shared_transaction') {
      interaction.direction = 'undirected_consistency';
      delete interaction.initiator_module_id; delete interaction.responder_module_id;
    }
    interaction.ordered = false;
    result.componentRelations.push({ id: relation.id, title: relation.title, kind: relation.kind,
      protocolId: relation.protocol_id, relatedProtocolIds: [...relation.related_protocol_ids], endpoints,
      interaction, sourceBasis: clone(relation.source_basis), codeEvidence: clone(relation.codeEvidence) });
  }
  result.reason = result.componentRelations.length ? null : 'no_readable_relations';
  result.contextModuleIds = unique(result.componentRelations.flatMap(relation => relation.endpoints
    .filter(endpoint => endpoint.display === 'shared_component_boundary' || endpoint.display === 'external_root_boundary')
    .map(endpoint => endpoint.displayModuleId)));
  return result;
}

/** Cache only verified immutable text. Every invocation rebuilds grants and the projection input. */
export function createComponentRelationsLoader({ resources, access, sha256, limitedMap, check }) {
  const bytes = new Map();
  async function readArtifact(artifact, readable) {
    if (!descriptor(artifact)) throw new Unavailable('invalid_descriptor');
    if (!await readable(artifact.address)) throw new Unavailable('supplement_unreadable');
    const key = `${artifact.address}:${artifact.sha256}:${artifact.size}`;
    let text = bytes.get(key);
    if (text == null) {
      const result = await resources.read(artifact.address, 'text'); check();
      if (!result.readable) throw new Unavailable('supplement_unreadable');
      text = result.data;
      if (typeof text !== 'string' || new TextEncoder().encode(text).byteLength !== artifact.size)
        throw new Unavailable('size_mismatch');
      if (await sha256(text) !== artifact.sha256) throw new Unavailable('hash_mismatch');
      bytes.set(key, text);
    }
    try { return JSON.parse(text); } catch { throw new Unavailable('invalid_json'); }
  }

  async function loadSupplement(data) {
    if (data?.mode !== 'current') throw new Unavailable('historical_evidence_missing');
    const state = data.manifest?.discoveryPin?.component_relations;
    if (!state || state.status === 'not_declared') throw new Unavailable('not_declared');
    if (state.status === 'missing') throw new Unavailable('declared_missing:' + state.reason_code);
    if (state.status !== 'available' || !descriptor(state.manifest)) throw new Unavailable('invalid_descriptor');
    // Permissions are shared only within this invocation, never across viewer/watch generations.
    const grants = new Map();
    const readable = ref => {
      if (!ref) return Promise.resolve(false);
      if (!grants.has(ref)) grants.set(ref, (async () => { check(); const value = await access(ref); check(); return value.readable === true; })());
      return grants.get(ref);
    };
    check();
      const entry = await readArtifact(state.manifest, readable);
      const body = await readArtifact(entry.artifact, readable);
      if (!supplementEntryMatches(entry, body)) throw new Unavailable('invalid_contract');
      if (!bindingMatches(data, body.binding)) throw new Unavailable('binding_mismatch');
      if (entry.revision === 3 && !verificationMatches(await readArtifact(entry.verification, readable), data.manifest, body))
        throw new Unavailable('verification_binding_mismatch');
      const authority = [REPOSITORY, data.mapRef, data.evolutionRef, entry.base_manifest.address,
        entry.consumer_contract.address, entry.map.address, ...(entry.binding?.evolution ? [entry.binding.evolution.address] : []),
        ...(data.manifest.discoveryPin?.pinned ? [data.manifest.discoveryPin.pinned.address] : [])];
      if (!(await limitedMap(authority, readable)).every(Boolean)) throw new Unavailable('base_unreadable');
    return { entry, body, readable, entryPin: state.manifest };
  }

  function failure(error) {
    if (error?.name === 'AbortError') throw error;
    if (error instanceof Unavailable) return empty('unavailable', error.reason);
    return empty('error', 'read_failed', { error });
  }

  async function componentRelations(data) {
    try {
      check();
      const { entry, body, readable, entryPin } = await loadSupplement(data);
      const modules = new Map((data.modules || []).map(item => [item.id, item]));
      const protocols = new Set((data.protocols || []).map(item => item.id));
      const components = new Map(body.components.map(item => [item.module_id, item]));
      const evidence = new Map(body.code_evidence.map(item => [item.id, item]));
      const scopesByRoot = new Map(body.scopes.map(scope => [scope.root_module_id, scope]));
      const moduleRef = id => data.mapRef + (data.anchors?.modules?.[id] ? `#L${data.anchors.modules[id]}` : '');
      const protocolRef = id => data.mapRef + (data.anchors?.protocols?.[id] ? `#L${data.anchors.protocols[id]}` : '');
      const relations = await limitedMap(body.relations, async relation => {
        if (!Array.isArray(relation.endpoints) || !relation.endpoints.length || !relation.source_basis?.length
          || !relation.code_evidence_ids?.length || relation.interaction?.ordered !== false) throw new Unavailable('invalid_relation');
        const endpointIds = relation.endpoints.map(endpoint => endpoint.module_id);
        const viaIds = (relation.interaction.via || []).map(via => via.module_id);
        if (viaIds.some(id => !rootOf(modules, id))) return null;
        const relatedProtocols = [relation.protocol_id, ...relation.related_protocol_ids].filter(Boolean);
        if (relatedProtocols.some(id => !protocols.has(id))) return null;
        for (const endpoint of relation.endpoints) {
          const item = modules.get(endpoint.module_id);
          if (!item || !rootOf(modules, item.id)) return null;
          if (endpoint.kind === 'boundary_role') {
            if (endpoint.component_id !== null || item.parent) throw new Unavailable('invalid_endpoint');
          } else {
            const declaration = components.get(item.id);
            if (endpoint.kind !== 'component' || endpoint.component_id !== item.id || !declaration)
              throw new Unavailable('invalid_endpoint');
            if (item.parent !== declaration.parent
              || JSON.stringify([...(item.deployed_in || [])].sort()) !== JSON.stringify([...(declaration.deployed_in || [])].sort()))
              throw new Unavailable('component_binding_mismatch');
          }
        }
        const code = relation.code_evidence_ids.map(id => evidence.get(id));
        if (code.some(item => !item || item.commit !== body.binding.commit || !item.path)) throw new Unavailable('invalid_code_evidence');
        const referencedModules = unique([...endpointIds, ...viaIds]);
        const refs = unique([...referencedModules.map(moduleRef), ...referencedModules.map(id => moduleRef(rootOf(modules, id))),
          ...relatedProtocols.map(protocolRef), ...relation.source_basis.flatMap(source => [source.design_id, source.body?.address]),
          ...code.map(item => `${REPOSITORY}/commit/${item.commit}/file/${item.path}`)]);
        if (!(await limitedMap(refs, readable)).every(Boolean)) return null;
        const scopes = [];
        for (const id of relation.scope_roots) {
          if (scopesByRoot.get(id)?.relation_ids.includes(relation.id) && modules.has(id) && await readable(moduleRef(id))) scopes.push(id);
        }
        if (!scopes.length) return null;
        return { ...clone(relation), scope_roots: scopes, codeEvidence: clone(code) };
      });
      check();
      const visible = relations.filter(Boolean);
      const scopes = body.scopes.map(scope => ({ root_module_id: scope.root_module_id,
        relation_ids: visible.filter(relation => relation.scope_roots.includes(scope.root_module_id)).map(relation => relation.id) }))
        .filter(scope => scope.relation_ids.length);
      return { status: 'available', reason: visible.length ? null : 'no_readable_relations', coverage: 'partial',
        binding: clone(body.binding), pinned: { entry: clone(entryPin), artifact: clone(entry.artifact) }, scopes, relations: visible };
    } catch (error) { return failure(error); }
  }

  // Private catalog path shares fixed bytes/binding with the relation loader, not its authorization results.
  async function scenarioCatalog(data, protocolId) {
    try {
      check();
      const { body, readable } = await loadSupplement(data);
      if (!data.protocols?.some(protocol => protocol.id === protocolId)) throw new Unavailable('protocol_unreadable');
      const ref = data.mapRef + (data.anchors?.protocols?.[protocolId] ? `#L${data.anchors.protocols[protocolId]}` : '');
      if (!await readable(ref)) throw new Unavailable('protocol_unreadable');
      const entries = body.scenario_catalog.filter(item => item.origin === 'supplement_v2' && item.protocol_id === protocolId);
      const allowed = await limitedMap(entries, async item => {
        if (!descriptor(item.artifact) || item.status !== 'simulated') throw new Unavailable('invalid_scenario');
        if (!await readable(item.artifact.address)) throw new Unavailable('scenario_unreadable');
        return clone(item);
      });
      check();
      return { status: 'available', reason: null, entries: allowed };
    } catch (error) { return { ...failure(error), entries: [] }; }
  }
  return { componentRelations, scenarioCatalog };
}
