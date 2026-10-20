// Case replay is shared infrastructure. Domain explanation lives in the view for
// each adapter; presentation hints never select a generic renderer.
export const WEB_MECHANISM_KEYS = Object.freeze([
  'refresh-generation', 'instruction-retry', 'resident-cursors', 'document-edit',
  'home-activation', 'router-connection', 'web-request', 'identity-session',
  'chatroom-revision', 'resource-discussion', 'pulse-publication', 'business-task',
  'paper-recovery', 'wall-action', 'archive-errors', 'http-stream', 'document-navigation',
  'isa-commit-replay', 'backup-receipt',
]);
export const WEB_ADAPTERS = Object.freeze(WEB_MECHANISM_KEYS.map(key => `portal.architecture.${key}.v1`));
const own = (o, k) => Object.hasOwn(o, k);
const copy = value => structuredClone(value);
const fail = reason => { throw new Error(`web_workbench:${reason}`); };
const record = x => x !== null && typeof x === 'object' && !Array.isArray(x);
function typed(value, schema) {
  if (!schema || !schema.type) return false;
  const ok = schema.type === 'integer' ? Number.isSafeInteger(value)
    : schema.type === 'number' ? typeof value === 'number' && Number.isFinite(value)
      : schema.type === 'array' ? Array.isArray(value)
        : schema.type === 'object' ? record(value)
          : schema.type === 'null' ? value === null : typeof value === schema.type;
  return ok && (!schema.enum || schema.enum.includes(value));
}
function validateInputs(inputs, schema) {
  if (!record(inputs) || schema?.type !== 'object') fail('invalid_inputs');
  for (const key of schema.required || []) if (!own(inputs, key)) fail('missing_input');
  for (const [key, value] of Object.entries(inputs)) {
    const rule = schema.properties?.[key];
    if (!rule || !typed(value, rule)) fail(`invalid_input:${key}`);
  }
}
function allowed(condition, state) {
  if (!condition) return true;
  if (condition.all) return condition.all.map(c => allowed(c, state)).every(Boolean);
  if (condition.any) return condition.any.map(c => allowed(c, state)).some(Boolean);
  if (!own(state, condition.field)) fail('unknown_condition_field');
  const value = state[condition.field];
  const operators = ['equals', 'not_equals', 'in', 'gte'].filter(k => own(condition, k));
  if (operators.length !== 1) fail('unsupported_condition');
  switch (operators[0]) {
    case 'equals': return value === condition.equals;
    case 'not_equals': return value !== condition.not_equals;
    case 'in': return Array.isArray(condition.in) && condition.in.includes(value);
    case 'gte': return typeof value === 'number' && value >= condition.gte;
    default: return fail('unsupported_condition');
  }
}

export function createWebSimulation(content) {
  if (!content?.batchKey || !content.mechanism || !Array.isArray(content.objects) || !Array.isArray(content.evidence)) fail('invalid_content');
  const data = copy(content), m = data.mechanism;
  const key = m.id?.replace(/^web\.m\./, '');
  if (!WEB_MECHANISM_KEYS.includes(key) || m.adapter !== `portal.architecture.${key}.v1` || m.mode !== 'simulated') fail('unsupported_adapter');
  const objects = new Map(data.objects.map(x => [x.id, x]));
  const evidence = new Map(data.evidence.map(x => [x.id, x]));
  if (!objects.has(m.owner_id)) fail('owner_unavailable');
  for (const p of m.participants || []) if (!objects.has(p.object_id)) fail('participant_unavailable');
  const fields = new Map((m.state_fields || []).map(f => [f.id, f]));
  const steps = new Map((m.steps || []).map(s => [s.id, s]));
  if (!fields.size || steps.size !== m.steps?.length || fields.size !== m.state_fields?.length || !m.cases?.length) fail('invalid_model');
  for (const entity of [m, ...m.steps, ...m.cases]) {
    if (!entity.evidence_ids?.length || entity.evidence_ids.some(id => !evidence.has(id))) fail('evidence_unavailable');
  }
  for (const f of fields.values()) if (!typed(f.initial, f)) fail(`invalid_initial:${f.id}`);
  for (const s of steps.values()) {
    if (!objects.has(s.from) || !objects.has(s.to)) fail('endpoint_unavailable');
    for (const change of s.state_changes || []) {
      if (!fields.has(change.field) || !['set', 'increment', 'append'].includes(change.op)) fail('unsupported_change');
    }
  }
  const cases = new Map(m.cases.map(c => [c.id, c]));
  if (cases.size !== m.cases.length) fail('duplicate_case');
  function run(c, prefix) {
    const state = Object.fromEntries([...fields.values()].map(f => [f.id, copy(f.initial)]));
    for (const id of prefix) {
      const step = steps.get(id);
      if (!step || !allowed(step.when, state)) fail('case_guard_failed');
      for (const change of step.state_changes || []) {
        let value = change.value;
        if (record(value)) {
          if (Object.keys(value).length !== 1 || !own(value, 'input') || !own(c.inputs, value.input)) fail('unsupported_dynamic_value');
          value = c.inputs[value.input];
        }
        if (change.op === 'set') state[change.field] = copy(value);
        else if (change.op === 'increment') {
          if (typeof state[change.field] !== 'number' || typeof value !== 'number') fail('invalid_increment');
          state[change.field] += value;
        } else {
          if (!Array.isArray(state[change.field])) fail('invalid_append');
          state[change.field].push(copy(value));
        }
        if (!typed(state[change.field], fields.get(change.field))) fail('invalid_change_type');
      }
    }
    return state;
  }
  for (const c of cases.values()) {
    validateInputs(c.inputs, m.input_schema);
    if (!c.path?.length || c.path.some(id => !steps.has(id))) fail('unknown_step');
    for (let i = 1; i < c.path.length; i++) if (!steps.get(c.path[i - 1]).next?.includes(c.path[i])) fail('invalid_case_path');
    run(c, c.path); // Reject corrupt candidates before showing any partial scene.
  }
  let caseId = m.cases[0].id, position = 0;
  const visiblePath = c => c.path.filter(id => id !== 'load_case_inputs');
  function scene() {
    const c = cases.get(caseId), path = visiblePath(c);
    const prefix = [...(c.path[0] === 'load_case_inputs' ? ['load_case_inputs'] : []), ...path.slice(0, position)];
    const stepId = position ? path[position - 1] : null;
    return {status: 'simulated', mechanismId: m.id, caseId, position, total: path.length,
      stepId, state: run(c, prefix), prefix, inputs: copy(c.inputs), complete: position === path.length,
      step: stepId ? copy(steps.get(stepId)) : null, next: position < path.length ? copy(steps.get(path[position])) : null,
      outcome: position === path.length ? c.outcome : null};
  }
  return {
    getContent: () => copy(data), getScene: scene,
    choose(id) { if (!cases.has(id)) fail('unknown_case'); caseId = id; position = 0; return scene(); },
    advance() { position = Math.min(position + 1, visiblePath(cases.get(caseId)).length); return scene(); },
    previous() { position = Math.max(0, position - 1); return scene(); },
    reset() { position = 0; return scene(); },
    getViewState: () => ({batchKey: data.batchKey, mechanismId: m.id, caseId, position}),
    restore(snapshot) {
      if (snapshot?.batchKey !== data.batchKey || snapshot?.mechanismId !== m.id || !cases.has(snapshot?.caseId)
          || !Number.isInteger(snapshot.position) || snapshot.position < 0 || snapshot.position > visiblePath(cases.get(snapshot.caseId)).length) fail('invalid_restore');
      caseId = snapshot.caseId; position = snapshot.position; return scene();
    },
  };
}
