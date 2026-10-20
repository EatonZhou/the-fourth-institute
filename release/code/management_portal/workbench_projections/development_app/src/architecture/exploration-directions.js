import { schemaMatches } from '../shared/format-tags.js';
import { EVOLUTION_REF } from './model.js';
import { resourceAddress } from '../shared/resources.js';

// Direction identity stays owned by Evolution. This projection never produces
// a replacement module focus or promotes a child into a top-level object.
export function selectExplorationDirection(bundle, evolution, semantic) {
  if (bundle.mode !== 'current' || !['current', 'proposed'].includes(semantic?.status)
    || semantic.source !== EVOLUTION_REF || resourceAddress(semantic.ref) !== EVOLUTION_REF
    || !semantic.kind?.startsWith('direction:') || typeof semantic.observed_at !== 'string') return null;
  const id = semantic.kind.slice('direction:'.length);
  const direction = evolution?.directions?.find(value => value.id === id);
  if (!direction || !schemaMatches(evolution.schema, 'tfi.portal-evolution.v2')) return null;
  const mappingValid = Array.isArray(direction.modules);
  const declared = Array.isArray(direction.modules) ? direction.modules : [];
  const modules = mappingValid ? bundle.model.objects.filter(value => value.kind === 'module'
    && declared.includes(value.id)).map(value => value.id) : [];
  return {
    id, title: direction.title || id, description: direction.description || '',
    semantic: Object.fromEntries(['ref', 'kind', 'status', 'source', 'observed_at'].map(key => [key, semantic[key]])),
    moduleIds: modules, mappingValid,
    incomplete: !mappingValid || declared.some(id => !modules.includes(id)),
    batchKey: bundle.batchKey,
  };
}

export function projectDirectionHighlights(model, projection, direction) {
  if (!direction?.mappingValid) return [];
  const objects = new Map(model.objects.map(value => [value.id, value]));
  const visible = new Set([projection.root, ...projection.children, ...projection.boundaryEndpoints]
    .filter(Boolean).map(value => value.id));
  const result = new Set();
  for (const id of direction.moduleIds) {
    let at = objects.get(id); const seen = new Set();
    while (at && !seen.has(at.id)) {
      seen.add(at.id);
      if (visible.has(at.id)) { result.add(at.id); break; }
      at = objects.get(at.parent_id);
    }
  }
  return [...result];
}
