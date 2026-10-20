import { schemaMatches } from './format-tags.js';

// Stable IDs belong to the current map. Source byte integrity is checked by
// each reader; prose edits do not invalidate otherwise unchanged identities.
export function matchesCurrentModuleMap(binding, { ref, schema, modelVersion = 1 }) {
  return schemaMatches(schema, 'tfi.portal-architecture.v1')
    && binding?.ref === ref && binding.schema === schema
    && (binding.model_version ?? 1) === modelVersion;
}
