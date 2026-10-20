// Authored entry choices for domains model 116d3a72. This is a presentation
// registry, not authorization or a second source of architecture facts.
const rows = [
  ['notifications', 'module', 'change-refresh'],
  ['delivery', 'module', 'release-publication'],
  ['engineering', 'module', 'architecture-audit'],
  ['delivery.services', 'module', 'deployment-handoff'],
  ['engineering.validation', 'module', 'validation-flow'],
  ['notifications.clients', 'module', 'change-refresh'],
  ['signed-rpc', 'protocol', 'rpc-envelope'],
  ['change-refresh', 'protocol', 'change-refresh'],
  ['deployment-control', 'protocol', 'deployment-handoff'],
  ['release-events', 'protocol', 'release-cursor'],
  ['application-target', 'protocol', 'target-freeze'],
  ['notifications.broker', 'concept', 'change-refresh'],
  ['notifications.clients.publisher', 'concept', 'publish-buffer'],
  ['notifications.clients.receiver', 'concept', 'change-refresh'],
  ['notifications.clients.hub', 'concept', 'hub-cursor'],
  ['notifications.clients.fanout', 'concept', 'subscriber-overflow'],
  ['notifications.clients.file-watch', 'concept', 'directory-watch'],
  ['delivery.services.requests', 'concept', 'request-coalescing'],
  ['delivery.services.generation', 'concept', 'component-generation'],
  ['delivery.services.coordinator', 'concept', 'deployment-handoff'],
  ['delivery.services.release', 'concept', 'release-publication'],
  ['delivery.services.publications', 'concept', 'publication-ledger'],
  ['engineering.architecture-audit', 'concept', 'architecture-audit'],
  ['engineering.validation.catalog', 'concept', 'catalog-evidence'],
  ['engineering.validation.plans', 'concept', 'plan-freeze'],
  ['engineering.validation.runs', 'concept', 'durable-run'],
  ['engineering.validation.executor', 'concept', 'executor-lifetime'],
  ['engineering.validation.tau', 'concept', 'tau-resume'],
];

export const infrastructureObjectEntries = Object.freeze(Object.fromEntries(rows.map(([objectId, objectKind, name]) => {
  const defaultMechanismId = `infra.mechanism.${name}`;
  return [objectId, Object.freeze({objectId, objectKind, defaultMechanismId,
    factory: 'createInfrastructureWorkbench',
    modes: Object.freeze([Object.freeze({mechanismId: defaultMechanismId, adapter: `portal.infrastructure.${name}.v1`})])})];
})));

const fail = reason => { throw new Error(`infrastructure:entry_${reason}`); };
export function resolveInfrastructureEntry(objectId, content, {mechanismId} = {}) {
  const definition = Object.hasOwn(infrastructureObjectEntries, objectId) && infrastructureObjectEntries[objectId];
  if (!definition) fail('unsupported_object');
  const matches = (content?.objects || []).filter(o => o.id === objectId);
  if (matches.length !== 1) fail('object_unavailable');
  const object = matches[0];
  if (object.kind !== definition.objectKind) fail('object_kind_mismatch');
  if (!Array.isArray(object.child_ids) || !Array.isArray(object.mechanism_ids)) fail('object_incomplete');
  // Composition remains available when mechanism sources are unavailable.
  if (!mechanismId && object.kind !== 'protocol' && object.child_ids.length) {
    return {route: {kind: 'composition', objectId, mechanismId: null}, defaultMechanismId: definition.defaultMechanismId,
      factory: null, modes: []};
  }
  if (typeof content.batchKey !== 'string' || !content.batchKey) fail('batch_missing');
  const mechanisms = content.mechanisms || (content.mechanism ? [content.mechanism] : []);
  const modes = definition.modes.flatMap(mode => {
    if (!object.mechanism_ids.includes(mode.mechanismId)) return [];
    const found = mechanisms.filter(m => m.id === mode.mechanismId);
    if (found.length > 1) fail('duplicate_mechanism');
    if (!found.length) return [];
    const m = found[0];
    if (m.adapter !== mode.adapter || m.mode !== 'simulated' || typeof m.title !== 'string' || !m.title.trim()) fail('mechanism_mismatch');
    return [{...mode, title: m.title}];
  });
  const selectedId = mechanismId || definition.defaultMechanismId;
  if (!modes.some(m => m.mechanismId === selectedId)) fail('mechanism_unavailable');
  return {route: {kind: object.kind === 'protocol' ? 'protocol' : 'mechanism', objectId, mechanismId: selectedId},
    defaultMechanismId: definition.defaultMechanismId, factory: definition.factory, modes};
}
