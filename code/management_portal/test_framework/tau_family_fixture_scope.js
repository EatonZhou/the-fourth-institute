'use strict';

// Read an already captured, public GetLineage result. Never infer ownership
// from canvas selection, names, generated IDs, or a successful bulk mutation.
function readFamilyFixtureScope(result, expected = {}) {
  const saved = structuredClone(result);
  const target = structuredClone(expected);
  const {lineageId, blockId, familyResourceId, facadeId, memberId, effectIds, eventId} = target;
  const problems = [];
  const check = (ok, field) => { if (!ok) problems.push(field); };
  for (const key of ['lineageId', 'blockId', 'familyResourceId', 'facadeId', 'memberId', 'eventId']) {
    check(typeof target[key] === 'string' && target[key].trim().length > 0, `expected.${key}`);
  }
  check(Array.isArray(effectIds) && effectIds.length === 2
    && effectIds.every(id => typeof id === 'string' && id.trim().length > 0)
    && new Set([...effectIds, eventId]).size === 3, 'expected.effectIds');
  const graph = saved?.lineage;
  check(graph?.lineage_id === lineageId && !!lineageId, 'lineage.lineage_id');
  check(Array.isArray(graph?.function_blocks), 'lineage.function_blocks');
  check(Array.isArray(graph?.program?.components), 'lineage.program.components');
  const blocks = Array.isArray(graph?.function_blocks) ? graph.function_blocks : [];
  const components = Array.isArray(graph?.program?.components) ? graph.program.components : [];
  const candidates = blocks.filter(block => block?.id === blockId);
  check(candidates.length === 1, 'family.candidates');
  const block = candidates.length === 1 ? candidates[0] : null;
  check(block?.family?.resource_id === familyResourceId && !!familyResourceId, 'family.resource_id');
  check(block?.family?.facade === facadeId && !!facadeId, 'family.facade');
  check(Array.isArray(block?.family?.members) && block.family.members.includes(memberId), 'family.members');
  // A missing elements list cannot establish either ownership or its absence.
  check(blocks.every(item => item && typeof item.id === 'string' && Array.isArray(item.elements)), 'ownership.complete');
  const ids = [...(Array.isArray(effectIds) ? effectIds : []), eventId];
  const objects = ids.map((id, index) => {
    const matches = components.filter(component => component?.id === id);
    const owners = blocks.flatMap(parent => (Array.isArray(parent?.elements) ? parent.elements : [])
      .filter(element => element?.id === id).map(element => ({block_id: parent.id, element})));
    const component = matches.length === 1 ? matches[0] : null;
    const kind = index < (Array.isArray(effectIds) ? effectIds.length : 0) ? 'effect' : 'event_source';
    check(matches.length === 1, `objects.${id}.candidates`);
    check(component?.kind === kind, `objects.${id}.kind`);
    check(owners.length === 1 && owners[0].block_id === blockId
      && owners[0].element.kind === 'component'
      && owners[0].element.key === `workflow:${id}`, `objects.${id}.ownership`);
    if (kind === 'event_source') check(component?.config?.event_type === 'manual.triggered', `objects.${id}.event_type`);
    return {id, expected_kind: kind, candidates: matches, owners};
  });
  return {
    scope: 'captured-public-lineage-fixture-ownership-only', expected: target,
    lineage_id: graph?.lineage_id ?? null, revision: graph?.revision ?? null,
    block_candidates: candidates, objects, problems, scope_ready: problems.length === 0,
  };
}

module.exports = {readFamilyFixtureScope};
