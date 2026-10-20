// Harness owns only the declared, local case replay. Parent content is already
// authorized; no discovery, provider execution, or navigation history lives here.
export const HARNESS_ADAPTERS = Object.freeze({
  'harness.plan-selection.v1': 'harness.plan-selection',
  'harness.tool-attachment.v1': 'harness.tool-attachment',
  'harness.plugin-lifecycle.v1': 'harness.plugin-lifecycle',
  'harness.observation.v1': 'harness.observation',
});
const entry = (kind, primary, modes = [primary]) => Object.freeze({kind,
  defaultMechanismId: kind === 'composition' ? null : primary,
  factory: kind === 'composition' ? null : 'createHarnessWorkbench',
  modeMechanismIds: Object.freeze(modes)});
export const HARNESS_OBJECT_ENTRIES = Object.freeze({
  'worker.harness': entry('composition', null, Object.values(HARNESS_ADAPTERS)),
  'worker.harness.plan': entry('mechanism', 'harness.plan-selection'),
  'worker.harness.tools': entry('mechanism', 'harness.tool-attachment'),
  'worker.harness.pipeline': entry('mechanism', 'harness.plugin-lifecycle'),
  'worker.harness.observation': entry('mechanism', 'harness.observation'),
});
export const getHarnessWorkbenchEntry = objectId => Object.hasOwn(HARNESS_OBJECT_ENTRIES, objectId)
  ? structuredClone({objectId, ...HARNESS_OBJECT_ENTRIES[objectId]}) : null;
export const supportsHarnessMechanisms = m => Object.hasOwn(HARNESS_ADAPTERS, m?.adapter || '')
  && HARNESS_ADAPTERS[m.adapter] === m.id && m.mode === 'simulated';
const copy = value => structuredClone(value);
const domainFields = {
  'harness.plan-selection': ['plan_condition', 'effective_plan', 'rewrite_input', 'error'],
  'harness.tool-attachment': ['condition', 'attached_delta', 'reason', 'error'],
  'harness.plugin-lifecycle': ['plugin_result', 'plugin_status', 'provider_called', 'batch_preserved', 'error'],
  'harness.observation': ['provider_outcome', 'prepared_observed', 'local_report_status', 'original_error_preserved', 'scheduler_terminal_written'],
};
const fail = reason => { throw Error(`harness_mechanisms:${reason}`); };
const equal = (a, b) => JSON.stringify(a) === JSON.stringify(b);
const safeKey = key => typeof key === 'string' && key.length && !['__proto__', 'prototype', 'constructor'].includes(key);
function index(items, name) {
  if (!Array.isArray(items)) fail(`${name}_required`);
  const map = new Map();
  for (const item of items) {
    if (!safeKey(item?.id) || map.has(item.id)) fail(`${name}_identity`);
    map.set(item.id, item);
  }
  return map;
}
function typed(value, spec) {
  const types = {string: v => typeof v === 'string', boolean: v => typeof v === 'boolean',
    integer: Number.isSafeInteger, number: Number.isFinite, array: Array.isArray,
    object: v => v !== null && typeof v === 'object' && !Array.isArray(v)};
  if (!types[spec?.type]?.(value) || (spec.enum && !spec.enum.some(v => equal(v, value)))) fail('value_type');
  if (spec.minimum !== undefined && value < spec.minimum || spec.maximum !== undefined && value > spec.maximum) fail('value_range');
  if (spec.type === 'object') {
    for (const key of spec.required || []) if (!Object.hasOwn(value, key)) fail('input_required');
    for (const [key, v] of Object.entries(value)) {
      if (!safeKey(key) || !Object.hasOwn(spec.properties || {}, key)) fail('input_unknown');
      typed(v, spec.properties[key]);
    }
  }
}
function matches(condition, state) {
  if (!condition || typeof condition !== 'object' || Array.isArray(condition)) fail('condition_invalid');
  if (Object.hasOwn(condition, 'all') || Object.hasOwn(condition, 'any')) {
    const op = Object.hasOwn(condition, 'all') ? 'all' : 'any';
    if (Object.keys(condition).length !== 1 || !Array.isArray(condition[op])) fail('condition_invalid');
    const values = condition[op].map(part => matches(part, state));
    return op === 'all' ? values.every(Boolean) : values.some(Boolean);
  }
  if (Object.keys(condition).length !== 2 || !Object.hasOwn(condition, 'equals') || !Object.hasOwn(state, condition.field)) fail('condition_invalid');
  return equal(state[condition.field], condition.equals);
}

export function createHarnessSimulation(input) {
  const content = copy(input), m = content?.mechanism;
  if (!supportsHarnessMechanisms(m)) fail('unsupported_adapter');
  if (typeof content.batchKey !== 'string' || !content.batchKey) fail('batch_required');
  if (m.state_model?.condition_source !== 'declared_state_only'
    || m.state_model.unknown_operation !== 'reject' || m.state_model.unknown_field !== 'reject') fail('unsupported_state_model');
  const objects = index(content.objects, 'objects'), evidence = index(content.evidence, 'evidence');
  const relations = index(content.relations, 'relations'), fields = index(m.state_fields, 'fields');
  const steps = index(m.steps, 'steps'), cases = index(m.cases, 'cases');
  const participants = new Set((m.participants || []).map(p => p.object_id));
  if (!objects.has(m.owner_id) || !fields.size || !steps.size || !cases.size) fail('incomplete_mechanism');
  for (const id of domainFields[m.id]) if (!fields.has(id)) fail('domain_field_missing');
  for (const id of participants) if (!objects.has(id)) fail('participant_missing');
  function sources(ids) {
    if (!Array.isArray(ids) || !ids.length) fail('evidence_required');
    for (const id of ids) {
      const e = evidence.get(id);
      if (!e?.address || !e.excerpt || !e.frozen?.address || !/^[a-f0-9]{64}$/.test(e.sha256 || '')
        || e.sha256 !== e.frozen.sha256 || !Number.isInteger(e.start_line) || e.start_line < 1
        || !Number.isInteger(e.end_line) || e.end_line < e.start_line) fail('evidence_unavailable');
    }
  }
  sources(m.evidence_ids);
  const initial = {};
  for (const f of fields.values()) {
    if (!['observation', 'authoritative'].includes(f.state_scope)) fail('state_scope');
    if (f.authority_id && !participants.has(f.authority_id)) fail('state_authority');
    typed(f.initial, f); initial[f.id] = copy(f.initial);
  }
  for (const step of steps.values()) {
    if (!participants.has(step.from) || !participants.has(step.to)) fail('endpoint_missing');
    if (!Array.isArray(step.next) || step.next.some(id => !steps.has(id))) fail('next_unknown');
    if (step.when) matches(step.when, initial);
    if (step.relation_id) {
      const r = relations.get(step.relation_id);
      if (!r || r.from !== step.from || r.to !== step.to) fail('relation_mismatch');
    }
    if (!Array.isArray(step.state_changes)) fail('changes_required');
    for (const change of step.state_changes) {
      if (!fields.has(change.field) || !['set', 'increment', 'append'].includes(change.op)) fail('change_unsupported');
      if (!participants.has(change.writer_id)) fail('writer_missing');
      if (fields.get(change.field).authority_id && change.writer_id !== fields.get(change.field).authority_id) fail('writer_authority');
    }
    sources(step.evidence_ids);
  }
  function replay(c, cursor) {
    const state = copy(initial), trace = [];
    for (const id of c.path.slice(0, cursor)) {
      const step = steps.get(id), changes = [];
      if (step.when && !matches(step.when, state)) fail('condition_failed');
      // The prose guard is displayed as evidence, never evaluated.
      for (const change of step.state_changes) {
        let value = change.value;
        if (value && typeof value === 'object' && Object.hasOwn(value, 'input')) {
          if (Object.keys(value).length !== 1 || !Object.hasOwn(c.inputs, value.input)) fail('input_binding');
          value = c.inputs[value.input];
        }
        const before = copy(state[change.field]);
        if (change.op === 'set') state[change.field] = copy(value);
        if (change.op === 'increment') {
          if (!Number.isFinite(before) || !Number.isFinite(value)) fail('increment_type');
          state[change.field] += value;
        }
        if (change.op === 'append') {
          if (!Array.isArray(before)) fail('append_type');
          state[change.field].push(copy(value));
        }
        typed(state[change.field], fields.get(change.field));
        changes.push({field: change.field, before, after: copy(state[change.field]), writer_id: change.writer_id});
      }
      trace.push({id, title: step.title, changes});
    }
    return {state, trace};
  }
  for (const c of cases.values()) {
    typed(c.inputs, m.input_schema); sources(c.evidence_ids);
    if (!Array.isArray(c.path) || !c.path.length || c.path.length > 256 || c.path[0] !== 'load'
      || c.path.some(id => !steps.has(id))) fail('case_path');
    c.path.forEach((id, i) => { if (i && !steps.get(c.path[i - 1]).next.includes(id)) fail('case_transition'); });
    if (steps.get(c.path.at(-1)).next.length) fail('case_incomplete');
    const {state} = replay(c, c.path.length);
    if (!c.outcome || typeof c.outcome !== 'object') fail('case_outcome');
    for (const [key, value] of Object.entries(c.outcome)) if (!fields.has(key) || !equal(state[key], value)) fail('case_outcome');
  }
  let caseId = m.cases[0].id, cursor = 0;
  function getScene() {
    const c = cases.get(caseId), {state, trace} = replay(c, cursor);
    const current = cursor ? steps.get(c.path[cursor - 1]) : null, next = steps.get(c.path[cursor]) || null;
    return copy({batchKey: content.batchKey, mechanism_id: m.id, adapter: m.adapter, mode: 'simulated',
      case_id: caseId, cursor, total_steps: c.path.length, complete: !next, state, trace, current, next,
      outcome: next ? null : c.outcome, evidence_ids: [...new Set([...(current?.evidence_ids || m.evidence_ids), ...c.evidence_ids])]});
  }
  return {mechanism: m, objects, evidence, fields, getScene,
    chooseCase(id) { if (!cases.has(id)) return false; caseId = id; cursor = 0; return true; },
    advance() { if (!getScene().next) return false; cursor++; return true; },
    back() { if (!cursor) return false; cursor--; return true; }, reset() { cursor = 0; },
    getViewState: () => ({schema: 'harness-simulation.v1', batchKey: content.batchKey, mechanismId: m.id, caseId, cursor}),
    restoreViewState(saved) {
      if (saved?.schema !== 'harness-simulation.v1' || saved.batchKey !== content.batchKey || saved.mechanismId !== m.id
        || !cases.has(saved.caseId) || !Number.isSafeInteger(saved.cursor) || saved.cursor < 0 || saved.cursor > cases.get(saved.caseId).path.length) return false;
      caseId = saved.caseId; cursor = saved.cursor; return true;
    },
  };
}
