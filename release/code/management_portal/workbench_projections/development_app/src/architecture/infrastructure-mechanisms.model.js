// Local replay infrastructure. Only the owner-authored case is executable;
// a prose guard is a displayed premise, never inferred code.
export const infrastructureAdapters = Object.freeze(Object.fromEntries([
  'change-refresh', 'publish-buffer', 'hub-cursor', 'subscriber-overflow', 'directory-watch',
  'release-publication', 'request-coalescing', 'component-generation', 'deployment-handoff',
  'publication-ledger', 'release-cursor', 'rpc-envelope', 'target-freeze', 'architecture-audit',
  'validation-flow', 'catalog-evidence', 'plan-freeze', 'durable-run', 'executor-lifetime', 'tau-resume',
].map(name => [`portal.infrastructure.${name}.v1`, `infra.mechanism.${name}`])));

const clone = value => structuredClone(value);
const own = (value, key) => Object.prototype.hasOwnProperty.call(value, key);
const fail = message => { throw new Error(`infrastructure:${message}`); };
const equal = (a, b) => JSON.stringify(a) === JSON.stringify(b);
function index(items, name) {
  if (!Array.isArray(items)) fail(`${name}_missing`);
  const map = new Map();
  for (const item of items) {
    if (!item || typeof item.id !== 'string' || !item.id || map.has(item.id)) fail(`${name}_identity`);
    map.set(item.id, item);
  }
  return map;
}
function check(value, schema) {
  const types = {string: v => typeof v === 'string', boolean: v => typeof v === 'boolean', integer: Number.isSafeInteger,
    number: v => typeof v === 'number' && Number.isFinite(v), array: Array.isArray,
    object: v => v !== null && typeof v === 'object' && !Array.isArray(v), null: v => v === null};
  if (!schema || !types[schema.type]?.(value)) fail('value_type');
  if (schema.enum && !schema.enum.some(v => equal(v, value))) fail('value_enum');
  if (own(schema, 'const') && !equal(schema.const, value)) fail('value_const');
  if (schema.type === 'object') {
    for (const key of schema.required || []) if (!own(value, key)) fail('input_required');
    for (const [key, v] of Object.entries(value)) {
      if (schema.properties?.[key]) check(v, schema.properties[key]);
      else if (schema.additionalProperties === false) fail('input_extra');
    }
  }
  if (schema.type === 'array' && schema.items) value.forEach(v => check(v, schema.items));
  if (schema.minimum !== undefined && value < schema.minimum) fail('value_minimum');
  if (schema.maximum !== undefined && value > schema.maximum) fail('value_maximum');
}
function condition(expr, state) {
  if (!expr || typeof expr !== 'object') fail('guard_invalid');
  for (const key of ['all', 'any']) if (own(expr, key)) {
    if (Object.keys(expr).length !== 1 || !Array.isArray(expr[key])) fail('guard_invalid');
    const values = expr[key].map(item => condition(item, state));
    return key === 'all' ? values.every(Boolean) : values.some(Boolean);
  }
  if (!own(state, expr.field)) fail('guard_field');
  const keys = Object.keys(expr).filter(k => k !== 'field');
  if (keys.length !== 1) fail('guard_invalid');
  const key = keys[0], expected = expr[key], value = state[expr.field];
  if (key === 'equals') return equal(value, expected);
  if (key === 'not_equals') return !equal(value, expected);
  if (key === 'in' && Array.isArray(expected)) return expected.some(v => equal(v, value));
  if (key === 'gte' && typeof value === 'number' && typeof expected === 'number') return value >= expected;
  fail('guard_operator');
}

export function createInfrastructureSimulation(input) {
  const content = clone(input), m = content?.mechanism;
  if (!m || infrastructureAdapters[m.adapter] !== m.id || m.mode !== 'simulated') fail('unsupported_adapter');
  if (typeof content.batchKey !== 'string' || !content.batchKey) fail('batch_missing');
  const objects = index(content.objects, 'objects'), evidence = index(content.evidence, 'evidence');
  const steps = index(m.steps, 'steps'), fields = index(m.state_fields, 'fields'), cases = index(m.cases, 'cases');
  if (!objects.has(m.owner_id) || !steps.size || !fields.size || !cases.size) fail('incomplete_mechanism');
  const sourceIds = new Set();
  function sources(list) {
    if (!Array.isArray(list) || !list.length) fail('evidence_missing');
    list.forEach(id => { if (!evidence.has(id)) fail('evidence_unavailable'); sourceIds.add(id); });
  }
  sources(m.evidence_ids);
  for (const p of m.participants || []) if (!objects.has(p.object_id)) fail('participant_unavailable');
  const initial = Object.fromEntries([...fields].map(([id, field]) => { check(field.initial, field); return [id, clone(field.initial)]; }));
  for (const step of steps.values()) {
    sources(step.evidence_ids);
    if (!objects.has(step.from) || !objects.has(step.to)) fail('endpoint_unavailable');
    if (!Array.isArray(step.next) || step.next.some(id => !steps.has(id))) fail('next_invalid');
    for (const change of step.state_changes || []) {
      if (!fields.has(change.field) || !['set', 'increment', 'append'].includes(change.op)) fail('state_change_invalid');
      if (change.writer_id && !objects.has(change.writer_id)) fail('writer_unavailable');
    }
  }
  function replay(caseId, position) {
    const scenario = cases.get(caseId);
    if (!scenario || !Number.isInteger(position) || position < 0 || position > scenario.path.length) fail('position_invalid');
    const state = clone(initial), events = [];
    for (const id of scenario.path.slice(0, position)) {
      const step = steps.get(id);
      if (step.when && !condition(step.when, state)) fail('guard_rejected');
      for (const change of step.state_changes || []) {
        let value = change.value;
        if (value && typeof value === 'object' && !Array.isArray(value) && own(value, 'input')) {
          if (Object.keys(value).length !== 1 || !own(scenario.inputs, value.input)) fail('input_reference');
          value = scenario.inputs[value.input];
        }
        if (change.op === 'set') state[change.field] = clone(value);
        if (change.op === 'increment') {
          if (typeof state[change.field] !== 'number' || typeof value !== 'number') fail('increment_type');
          state[change.field] += value;
        }
        if (change.op === 'append') {
          if (!Array.isArray(state[change.field])) fail('append_type');
          state[change.field].push(clone(value));
        }
        check(state[change.field], fields.get(change.field));
      }
      events.push(id);
    }
    return {state, events, scenario};
  }
  for (const scenario of cases.values()) {
    sources(scenario.evidence_ids); check(scenario.inputs, m.input_schema);
    if (!Array.isArray(scenario.path) || !scenario.path.length || scenario.path.some(id => !steps.has(id))) fail('case_path');
    for (let n = 1; n < scenario.path.length; n++) if (!steps.get(scenario.path[n - 1]).next.includes(scenario.path[n])) fail('case_transition');
    replay(scenario.id, scenario.path.length);
  }
  let caseId = cases.keys().next().value, position = 0;
  const key = JSON.stringify([content.batchKey, m.id]);
  return {
    getContent: () => clone(content),
    selectCase(id) { if (!cases.has(id)) fail('case_unknown'); caseId = id; position = 0; },
    advance() { if (position < cases.get(caseId).path.length) position++; },
    back() { position = Math.max(0, position - 1); },
    reset() { position = 0; },
    getViewState: () => ({key, caseId, position}),
    restoreViewState(saved) { if (saved?.key !== key) fail('state_batch_mismatch'); replay(saved.caseId, saved.position); caseId = saved.caseId; position = saved.position; },
    getScene() {
      const {state, events, scenario} = replay(caseId, position);
      return clone({mode: 'simulated', objectId: m.owner_id, mechanismId: m.id, caseId, position, inputs: scenario.inputs, state, events,
        complete: position === scenario.path.length, outcome: position === scenario.path.length ? scenario.outcome : null,
        current: position ? steps.get(events.at(-1)) : null, next: steps.get(scenario.path[position]) || null,
        evidenceIds: [...sourceIds]});
    },
  };
}
