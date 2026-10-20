// Interprets the supplied fixed case; contains no host requests or copied facts.
export const PROJECTION_ADAPTERS = Object.freeze({
  'portal.architecture.panel-lifecycle.v1': 'interface.workbenches',
  'portal.architecture.projection-scope.v1': 'web.projections',
  'portal.architecture.focus-revision.v1': 'projection-family',
  'portal.architecture.feedback-confirmation.v1': 'projection-family',
  'portal.architecture.semantic-validation.v1': 'web.projections.semantic-validation',
  'portal.architecture.generation-save.v1': 'web.projections.generation-save',
  'portal.architecture.ui-association.v1': 'web.projections.ui-association',
  'portal.architecture.projection-assembly.v1': 'interface.workbenches.projection-host',
  'portal.architecture.projection-opaque-bridge.v1': 'interface.workbenches.projection-host',
  'portal.architecture.projection-host-focus-order.v1': 'interface.workbenches.projection-host',
  'portal.architecture.projection-region-detail-order.v1': 'interface.workbenches',
  'portal.architecture.projection-feedback-budget.v1': 'interface.workbenches.projection-host',
});
const own = (o, k) => Object.hasOwn(o, k);
const clone = x => structuredClone(x);
const record = x => x !== null && typeof x === 'object' && !Array.isArray(x);
const fail = message => { throw new Error(`projection_mechanism:${message}`); };
function typed(value, rule) {
  const ok = rule?.type === 'integer' ? Number.isSafeInteger(value)
    : rule?.type === 'number' ? typeof value === 'number' && Number.isFinite(value)
      : rule?.type === 'array' ? Array.isArray(value)
        : rule?.type === 'object' ? record(value)
          : rule?.type === 'null' ? value === null : typeof value === rule?.type;
  return ok && (!rule.enum || rule.enum.includes(value));
}
function condition(c, state) {
  if (!c) return true;
  if (c.all) return c.all.map(x => condition(x, state)).every(Boolean);
  if (c.any) return c.any.map(x => condition(x, state)).some(Boolean);
  if (!own(state, c.field)) fail('unknown_condition_field');
  const operators = ['equals', 'not_equals', 'in', 'gte'].filter(x => own(c, x));
  if (operators.length !== 1) fail('unsupported_condition');
  const v = state[c.field];
  switch (operators[0]) {
    case 'equals': return v === c.equals;
    case 'not_equals': return v !== c.not_equals;
    case 'in': if (!Array.isArray(c.in)) fail('invalid_condition'); return c.in.includes(v);
    case 'gte': if (typeof v !== 'number') fail('invalid_condition'); return v >= c.gte;
    default: return fail('unsupported_condition');
  }
}
export function createProjectionSimulation(input) {
  if (!record(input) || typeof input.batchKey !== 'string' || !input.batchKey
      || !Array.isArray(input.objects) || !Array.isArray(input.evidence)) fail('invalid_content');
  const data = clone(input), m = data.mechanism;
  if (!m || !own(PROJECTION_ADAPTERS, m.adapter) || PROJECTION_ADAPTERS[m.adapter] !== m.owner_id
      || m.mode !== 'simulated') fail('unsupported_adapter');
  const objects = new Map(data.objects.map(x => [x.id, x]));
  const evidence = new Map(data.evidence.map(x => [x.id, x]));
  const steps = new Map((m.steps || []).map(x => [x.id, x]));
  const fields = new Map((m.state_fields || []).map(x => [x.id, x]));
  const cases = new Map((m.cases || []).map(x => [x.id, x]));
  if (!objects.has(m.owner_id) || !steps.size || !fields.size || !cases.size
      || steps.size !== m.steps.length || fields.size !== m.state_fields.length || cases.size !== m.cases.length) fail('invalid_model');
  for (const p of m.participants || []) if (!objects.has(p.object_id)) fail('participant_missing');
  for (const entity of [m, ...m.steps, ...m.cases]) {
    if (!entity.evidence_ids?.length) fail('evidence_missing');
    for (const id of entity.evidence_ids) {
      const e = evidence.get(id);
      if (!e?.frozen?.address || !/^[a-f0-9]{64}$/.test(e.frozen.sha256) || !e.excerpt) fail('evidence_missing');
    }
  }
  for (const field of fields.values()) if (!typed(field.initial, field)) fail('invalid_initial');
  for (const step of steps.values()) {
    if (!objects.has(step.from) || !objects.has(step.to)) fail('endpoint_missing');
    for (const change of step.state_changes || []) {
      if (!fields.has(change.field) || !['set', 'increment', 'append'].includes(change.op)) fail('unsupported_change');
    }
  }
  function run(c, path) {
    const state = Object.fromEntries([...fields.values()].map(f => [f.id, clone(f.initial)]));
    const frames = [];
    for (const id of path) {
      const step = steps.get(id);
      if (!step || !condition(step.when, state)) fail('invalid_case_guard');
      for (const change of step.state_changes || []) {
        let value = change.value;
        if (record(value) && own(value, 'input')) {
          if (Object.keys(value).length !== 1 || !own(c.inputs, value.input)) fail('input_missing');
          value = c.inputs[value.input];
        }
        if (change.op === 'set') state[change.field] = clone(value);
        else if (change.op === 'increment') {
          if (typeof state[change.field] !== 'number' || typeof value !== 'number') fail('invalid_increment');
          state[change.field] += value;
        } else {
          if (!Array.isArray(state[change.field])) fail('invalid_append');
          state[change.field].push(clone(value));
        }
        if (!typed(state[change.field], fields.get(change.field))) fail('invalid_change_type');
      }
      frames.push({step: clone(step), state: clone(state)});
    }
    return {state, frames};
  }
  for (const c of cases.values()) {
    if (!record(c.inputs) || m.input_schema?.type !== 'object') fail('invalid_inputs');
    for (const key of m.input_schema.required || []) if (!own(c.inputs, key)) fail('input_missing');
    for (const [key, value] of Object.entries(c.inputs)) {
      if (!typed(value, m.input_schema.properties?.[key])) fail('invalid_input');
    }
    if (!c.path?.length || c.path.some(id => !steps.has(id))) fail('invalid_path');
    for (let i = 1; i < c.path.length; i++) if (!steps.get(c.path[i - 1]).next?.includes(c.path[i])) fail('invalid_path');
    run(c, c.path);
  }
  let caseId = m.cases[0].id, position = 0;
  const path = c => c.path.filter(id => id !== 'load_case_inputs');
  const viewState = () => ({version: 1, batchKey: data.batchKey, mechanismId: m.id, caseId, position});
  function scene() {
    const c = cases.get(caseId), ids = path(c);
    const prefix = [...(c.path[0] === 'load_case_inputs' ? ['load_case_inputs'] : []), ...ids.slice(0, position)];
    const {state, frames} = run(c, prefix);
    return {status: 'simulated', ...viewState(), ownerId: m.owner_id, caseTitle: c.title,
      inputs: clone(c.inputs), state, frames: frames.filter(f => f.step.id !== 'load_case_inputs'),
      step: position ? clone(steps.get(ids[position - 1])) : null,
      next: position < ids.length ? clone(steps.get(ids[position])) : null,
      total: ids.length, complete: position === ids.length, outcome: position === ids.length ? c.outcome : null};
  }
  return {
    getContent: () => clone(data), getScene: scene, getViewState: viewState,
    choose(id) { if (!cases.has(id)) fail('case_missing'); caseId = id; position = 0; return scene(); },
    branch(id) {
      const nextCase = cases.get(id), currentPath = path(cases.get(caseId));
      if (!nextCase || path(nextCase).length <= position
          || currentPath.slice(0, position).some((stepId, i) => path(nextCase)[i] !== stepId)) fail('branch_mismatch');
      const prefix = nextCase.path.slice(0, position + (nextCase.path[0] === 'load_case_inputs' ? 1 : 0));
      run(nextCase, prefix);
      caseId = id;
      return scene();
    },
    next() { position = Math.min(position + 1, path(cases.get(caseId)).length); return scene(); },
    previous() { position = Math.max(0, position - 1); return scene(); },
    reset() { position = 0; return scene(); },
    restore(saved) {
      if (saved?.version !== 1 || saved.batchKey !== data.batchKey || saved.mechanismId !== m.id
          || !cases.has(saved.caseId) || !Number.isInteger(saved.position) || saved.position < 0
          || saved.position > path(cases.get(saved.caseId)).length) fail('restore_mismatch');
      caseId = saved.caseId; position = saved.position;
    },
  };
}
