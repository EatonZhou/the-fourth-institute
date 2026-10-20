export const SCHEDULING_ADAPTER = 'execution-control.declarative-transitions.v1';
export const SCHEDULING_MECHANISMS = Object.freeze([
  'control.admission', 'control.fairness', 'control.quota', 'control.worker-protocol',
  'control.expiry', 'control.worker-slots', 'control.generation', 'control.handover',
]);
const clone = value => JSON.parse(JSON.stringify(value));
const fail = message => { throw new Error(`Scheduling mechanism: ${message}`); };
const own = (object, key) => Object.prototype.hasOwnProperty.call(object, key);
const sameStateValue = (a, b) => a === b || (a !== null && b !== null && typeof a === 'object' && typeof b === 'object'
  && Array.isArray(a) === Array.isArray(b) && Object.keys(a).length === Object.keys(b).length
  && Object.keys(a).every(key => own(b, key) && sameStateValue(a[key], b[key])));
function conforms(value, spec) {
  const typeOK = spec.type === 'integer' ? Number.isSafeInteger(value)
    : spec.type === 'number' ? Number.isFinite(value)
    : spec.type === 'array' ? Array.isArray(value)
    : spec.type === 'object' ? value !== null && typeof value === 'object' && !Array.isArray(value)
    : spec.type === 'null' ? value === null : typeof value === spec.type;
  return typeOK && (!spec.enum || spec.enum.includes(value));
}
export function matchesSchedulingCondition(condition, state) {
  if (!condition) return true;
  if (own(condition, 'all')) {
    if (Object.keys(condition).length !== 1 || !Array.isArray(condition.all)) fail('invalid all condition');
    return condition.all.map(item => matchesSchedulingCondition(item, state)).every(Boolean);
  }
  if (own(condition, 'any')) {
    if (Object.keys(condition).length !== 1 || !Array.isArray(condition.any)) fail('invalid any condition');
    return condition.any.map(item => matchesSchedulingCondition(item, state)).some(Boolean);
  }
  if (Object.keys(condition).length !== 2 || !own(condition, 'field') || !own(condition, 'equals') || !own(state, condition.field)) fail('unsupported condition');
  return state[condition.field] === condition.equals;
}

/** Replay only the authorized case paths. The guard prose is never executed. */
export function createSchedulingSimulation(content) {
  if (!content || typeof content.batchKey !== 'string' || !content.batchKey) fail('batchKey required');
  const safe = clone(content), m = safe.mechanism;
  if (!m || m.adapter !== SCHEDULING_ADAPTER || !SCHEDULING_MECHANISMS.includes(m.id) || m.mode !== 'simulated') fail('unsupported mechanism');
  if (m.state_model?.kind !== 'declarative_transition_system' || m.state_model?.condition_scope !== 'state') fail('unsupported state model');
  const index = (items, label) => {
    if (!Array.isArray(items)) fail(`${label} required`);
    const result = new Map();
    for (const item of items) { if (!item?.id || result.has(item.id)) fail(`invalid ${label} identity`); result.set(item.id, item); }
    return result;
  };
  const objects = index(safe.objects, 'objects'), evidence = index(safe.evidence, 'evidence');
  const steps = index(m.steps, 'steps'), cases = index(m.cases, 'cases'), fields = index(m.state_fields, 'fields');
  if (!steps.size || !cases.size || !fields.size) fail('empty mechanism');
  const initial = {};
  for (const field of fields.values()) {
    if (!conforms(field.initial, field)) fail(`invalid initial ${field.id}`);
    initial[field.id] = clone(field.initial);
    if (field.authority_id && !objects.has(field.authority_id)) fail(`missing authority ${field.authority_id}`);
  }
  const checkEvidence = ids => {
    if (!Array.isArray(ids) || !ids.length || ids.some(id => !evidence.has(id))) fail('missing authorized evidence');
  };
  if (!objects.has(m.owner_id)) fail('missing owner');
  for (const participant of m.participants || []) if (!objects.has(participant.object_id)) fail('missing participant');
  for (const step of steps.values()) {
    if (!objects.has(step.from) || !objects.has(step.to)) fail(`missing endpoint ${step.id}`);
    checkEvidence(step.evidence_ids);
    if (!Array.isArray(step.next) || step.next.some(id => !steps.has(id))) fail(`invalid next ${step.id}`);
    matchesSchedulingCondition(step.when, initial);
    if (!Array.isArray(step.state_changes)) fail(`missing changes ${step.id}`);
    for (const change of step.state_changes) if (!fields.has(change.field) || !['set', 'increment', 'append'].includes(change.op)) fail(`unsupported change ${step.id}`);
  }
  function checkInputs(inputs) {
    const schema = m.input_schema;
    if (!schema || schema.type !== 'object' || !inputs || typeof inputs !== 'object' || Array.isArray(inputs)) fail('invalid inputs');
    for (const key of schema.required || []) if (!own(inputs, key)) fail(`missing input ${key}`);
    for (const [key, value] of Object.entries(inputs)) if (!schema.properties?.[key] || !conforms(value, schema.properties[key])) fail(`invalid input ${key}`);
  }
  function replay(selected, count) {
    const state = clone(initial), trace = [];
    let previous = null;
    for (const id of selected.path.slice(0, count)) {
      const step = steps.get(id);
      if (!step || (previous && !previous.next.includes(id)) || !matchesSchedulingCondition(step.when, state)) fail(`illegal case path ${selected.id}:${id}`);
      for (const change of step.state_changes) {
        let value = change.value;
        if (value && typeof value === 'object' && own(value, 'input')) {
          if (Object.keys(value).length !== 1 || !own(selected.inputs, value.input)) fail('invalid input binding');
          value = selected.inputs[value.input];
        }
        const next = change.op === 'set' ? clone(value)
          : change.op === 'increment' ? state[change.field] + value
          : Array.isArray(state[change.field]) ? [...state[change.field], clone(value)] : undefined;
        if (!conforms(next, fields.get(change.field))) fail(`invalid state change ${change.field}`);
        state[change.field] = next;
      }
      trace.push(step); previous = step;
    }
    return {state, trace};
  }
  for (const c of cases.values()) {
    checkInputs(c.inputs); checkEvidence(c.evidence_ids);
    if (!Array.isArray(c.path) || !c.path.length || c.path.length > 256 || c.path[0] !== 'load') fail('invalid case start');
    const final = replay(c, c.path.length);
    // An authored case may intentionally stop at a nonterminal observation, such
    // as receiving a claim before start. Its complete expected state is required.
    if (own(c, 'expected_state') && !sameStateValue(c.expected_state, final.state)) fail(`invalid expected state ${c.id}`);
    if (steps.get(c.path.at(-1)).next.length && !own(c, 'expected_state')) fail('incomplete case path');
  }
  let caseId = m.cases[0].id, count = 0;
  function getScene() {
    const selected = cases.get(caseId), {state, trace} = replay(selected, count), current = trace.at(-1) || null;
    return clone({mechanism_id: m.id, adapter: m.adapter, batchKey: safe.batchKey, mode: 'simulated', case_id: caseId,
      inputs: selected.inputs, state, current, next: steps.get(selected.path[count]) || null, trace,
      step_count: count, total_steps: selected.path.length, complete: count === selected.path.length,
      outcome: count === selected.path.length ? selected.outcome : null,
      evidence_ids: current?.evidence_ids || selected.evidence_ids});
  }
  return {
    getContent: () => clone(safe), getScene,
    advance() { count = Math.min(count + 1, cases.get(caseId).path.length); return getScene(); },
    back() { count = Math.max(0, count - 1); return getScene(); },
    reset() { count = 0; return getScene(); },
    selectCase(id) { if (!cases.has(id)) fail('unknown case'); caseId = id; count = 0; return getScene(); },
    getViewState: () => ({schema: 'scheduling-view.v1', batchKey: safe.batchKey, mechanismId: m.id, caseId, count}),
    restore(value) {
      if (value?.schema !== 'scheduling-view.v1' || value.batchKey !== safe.batchKey || value.mechanismId !== m.id
        || !cases.has(value.caseId) || !Number.isSafeInteger(value.count) || value.count < 0 || value.count > cases.get(value.caseId).path.length) fail('incompatible saved view');
      // Reconstruct from the authorized inputs/path; never trust a persisted state object.
      replay(cases.get(value.caseId), value.count); caseId = value.caseId; count = value.count; return getScene();
    },
  };
}
