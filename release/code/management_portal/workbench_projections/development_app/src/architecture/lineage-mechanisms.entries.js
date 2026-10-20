import {createLineageSimulation, lineageSupportedMechanisms} from './lineage-mechanisms.model.js';

// Authored choices for exact object identities. A declaration is not a finished
// workface. Only getLineageObjectModes exposes validated, implemented bodies.
const entry = (defaultMechanismId, mechanismIds) => Object.freeze({
  defaultMechanismId, mechanismIds: Object.freeze(mechanismIds),
  factory: 'createLineageMechanismsWorkbench',
});

const leaf = id => entry(id, [id]);
const composition = ids => entry(null, ids);
export const LINEAGE_OBJECT_ENTRIES = Object.freeze({
  'lineage': composition(['mechanism.lineage.rpc-dispatch', 'mechanism.lineage.conversation-round']),
  'lineage.definition': composition(['mechanism.lineage.definition-commit', 'mechanism.lineage.compile', 'mechanism.lineage.composition', 'mechanism.lineage.application-target']),
  'lineage.conversation': composition(['mechanism.lineage.intake', 'mechanism.lineage.execution-binding', 'mechanism.lineage.output-convergence', 'mechanism.lineage.conversation-round']),
  'lineage.automation': composition(['mechanism.lineage.event-occurrence', 'mechanism.lineage.packet-operators', 'mechanism.lineage.effect-journal', 'mechanism.lineage.event-effect-pipeline']),
  'lineage.family': composition(['mechanism.lineage.family-routing', 'mechanism.lineage.family-admission', 'mechanism.lineage.family-projection', 'mechanism.lineage.family-supervision']),
  'operation-identity': leaf('mechanism.lineage.operation-identity'),
  'society-service': leaf('mechanism.lineage.rpc-dispatch'),
  'lineage-events-effects': leaf('mechanism.lineage.event-effect-pipeline'),
  'lineage.rpc-dispatch': leaf('mechanism.lineage.rpc-dispatch'),
  'lineage.definition.transactions': leaf('mechanism.lineage.definition-commit'),
  'lineage.definition.compiler': leaf('mechanism.lineage.compile'),
  'lineage.definition.composition': leaf('mechanism.lineage.composition'),
  'lineage.definition.application-target': leaf('mechanism.lineage.application-target'),
  'lineage.conversation.intake': leaf('mechanism.lineage.intake'),
  'lineage.conversation.execution-bridge': leaf('mechanism.lineage.execution-binding'),
  'lineage.conversation.outputs': leaf('mechanism.lineage.output-convergence'),
  'lineage.automation.events': leaf('mechanism.lineage.event-occurrence'),
  'lineage.automation.routing': leaf('mechanism.lineage.packet-operators'),
  'lineage.automation.effects': composition(['mechanism.lineage.effect-journal', 'mechanism.lineage.script-runs']),
  'lineage.automation.effects.script-runs': leaf('mechanism.lineage.script-runs'),
  'lineage.family.router': leaf('mechanism.lineage.family-routing'),
  'lineage.family.admission': leaf('mechanism.lineage.family-admission'),
  'lineage.family.projection': leaf('mechanism.lineage.family-projection'),
  'lineage.family.supervision': leaf('mechanism.lineage.family-supervision'),
});

// Parent registry shape: objectId -> default mechanismId. Nonleaf objects stay
// composition routes, and unfinished mechanisms never enter this registry.
export const lineageReadyObjectEntries = Object.freeze(Object.fromEntries(
  Object.entries(LINEAGE_OBJECT_ENTRIES)
    .filter(([,e]) => lineageSupportedMechanisms.includes(e.defaultMechanismId))
    .map(([id,e]) => [id,e.defaultMechanismId])));

function lookup(objectId, content) {
  if (!Object.hasOwn(LINEAGE_OBJECT_ENTRIES, objectId) || !content?.batchKey) return null;
  const matches = content.objects?.filter(o => o.id === objectId) || [];
  return matches.length === 1 ? {object: matches[0], declared: LINEAGE_OBJECT_ENTRIES[objectId]} : null;
}

export function getLineageObjectModes(objectId, content) {
  const found = lookup(objectId, content);
  if (!found) return [];
  const {object, declared} = found;
  const mechanisms = new Map((content.mechanisms || []).map(m => [m.id, m]));
  if (content.mechanism) mechanisms.set(content.mechanism.id, content.mechanism);
  return declared.mechanismIds.flatMap(mechanismId => {
    if (!object.mechanism_ids?.includes(mechanismId)) return [];
    const mechanism = mechanisms.get(mechanismId);
    if (!mechanism?.title) return [];
    try { createLineageSimulation({...content, mechanism}); } catch { return []; }
    return [{mechanismId, title: mechanism.title, factory: declared.factory,
      adapter: mechanism.adapter,
      route: {kind: object.kind === 'protocol' ? 'protocol' : 'mechanism', objectId, mechanismId}}];
  });
}

export function resolveLineageObjectEntry(objectId, content) {
  const found = lookup(objectId, content);
  if (!found) return null;
  const {object, declared} = found, modes = getLineageObjectModes(objectId, content);
  if (object.kind !== 'protocol' && object.child_ids?.length) return {
    ...declared, modes, adapter: null, route: {kind: 'composition', objectId, mechanismId: null},
  };
  // A default body must itself be available. An alternative or shared adapter
  // never fills in an unfinished protocol or a changed leaf topology.
  const chosen = modes.find(m => m.mechanismId === declared.defaultMechanismId);
  return chosen ? {...declared, modes, adapter: chosen.adapter, route: chosen.route} : null;
}
