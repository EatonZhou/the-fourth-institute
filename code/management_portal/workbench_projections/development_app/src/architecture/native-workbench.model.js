// Local, evidence-backed playback. This module never submits a Provider request.
export {createDomainModeSession} from './provider-mechanisms.model.js';
const clone = value => structuredClone(value);
const own = (value, key) => Object.prototype.hasOwnProperty.call(value, key);
const equal = (a, b) => JSON.stringify(a) === JSON.stringify(b);
const fail = reason => { throw new Error(`native_workbench:${reason}`); };
const safe = key => typeof key === 'string' && key && !['__proto__', 'constructor', 'prototype'].includes(key);

export const NATIVE_ADAPTER = 'codex.native-turn.v1';
export const BUDGET_ADAPTER = 'codex.recovery-budget.v1';
export const NATIVE_OPERATIONS = Object.freeze({
  'codex.fork.v1': {id: 'codex.fork', fields: ['account_id', 'source_thread_id', 'result_thread_id', 'fork_rpc_count', 'thread_create_count', 'turn_start_count', 'history_available', 'history_injected', 'backend_result_returned', 'outcome']},
  'codex.compact.v1': {id: 'codex.compact', fields: ['thread_id', 'loaded', 'resume_count', 'compact_start_count', 'context_committed', 'technical_turn_active', 'local_busy', 'canonical_recorded', 'backend_result_returned', 'recovery_kind', 'total_deadline_reset', 'no_progress_reset', 'interrupt_sent', 'outcome']},
  'codex.goal.v1': {id: 'codex.goal', fields: ['thread_id', 'goal_status', 'objective', 'provider_budget', 'requested_budget', 'goal_get_count', 'goal_set_count', 'goal_clear_count', 'goal_set_fields', 'follow_attached', 'turn_completed', 'interrupt_requested', 'terminal_notification_preserved', 'outcome']},
  'codex.dynamic-tools.v1': {id: 'codex.dynamic-tools', fields: ['schema_valid', 'owns_turn', 'dispatcher_registered', 'detaching', 'active_calls', 'reply_count', 'rpc_error_code', 'tool_success', 'result_governance_failed', 'detached', 'outcome']},
  'provider.native-socket.v1': {id: 'provider.native-socket', fields: ['session_id', 'route_id', 'client_call_count', 'input_delivery', 'result_returned', 'final_answer', 'compaction_observed', 'usage_observed', 'compacted', 'nothing_to_compact', 'canonical_recorded', 'outcome']},
});
export const supportsNativeWorkbench = mechanism => mechanism?.mode === 'simulated'
  && ([NATIVE_ADAPTER, BUDGET_ADAPTER].includes(mechanism.adapter)
    || Object.hasOwn(NATIVE_OPERATIONS, mechanism.adapter) && NATIVE_OPERATIONS[mechanism.adapter].id === mechanism.id);
export const NATIVE_FIELDS = ['account_id', 'thread_id', 'thread_loaded', 'connection', 'accepted_turn_id', 'checkpoint_turn_id',
  'provider_turn_status', 'observed_turn_status', 'observed_turn_id', 'turn_start_count', 'thread_resume_count', 'native_control_count',
  'control_target_turn_id', 'control_result', 'control_error', 'control_rejection_reason', 'attached', 'interrupt_requested',
  'input_identity', 'lookup_result', 'last_other_turn_id', 'failure'];
export const BUDGET_FIELDS = ['attempts', 'checkpoint_attempts', 'turn_id', 'seen_item_ids', 'last_event', 'replenished',
  'claim_count', 'reconnect_probes', 'deferred', 'error'];
const nativeEntry = (kind, primary, modes = [primary]) => Object.freeze({kind, defaultMechanismId: kind === 'composition' ? null : primary,
  factory: kind === 'composition' ? null : 'createNativeWorkbench', primaryMechanismId: primary, modeMechanismIds: Object.freeze(modes)});
export const NATIVE_OBJECT_ENTRIES = Object.freeze({
  'provider-native': nativeEntry('protocol', 'codex.native-turn', ['codex.native-turn', 'codex.fork', 'codex.compact', 'codex.goal', 'codex.dynamic-tools', 'provider.native-socket']),
  'execution.providers.codex': nativeEntry('composition', 'codex.native-turn', ['codex.native-turn', 'codex.recovery-budget', 'codex.fork', 'codex.compact', 'codex.goal', 'codex.dynamic-tools']),
  'execution.providers.codex.client': nativeEntry('mechanism', 'codex.native-turn', ['codex.native-turn', 'codex.fork', 'codex.compact', 'codex.goal', 'codex.dynamic-tools']),
  'providers.codex-app-server': nativeEntry('mechanism', 'codex.native-turn', ['codex.native-turn', 'codex.fork', 'codex.compact', 'codex.goal', 'codex.dynamic-tools']),
  'execution.providers.codex.recovery-budget': nativeEntry('mechanism', 'codex.recovery-budget'),
  'execution.providers': nativeEntry('composition', 'provider.native-socket'),
});
export function getNativeWorkbenchEntry(objectId) {
  return Object.hasOwn(NATIVE_OBJECT_ENTRIES, objectId) ? structuredClone({objectId, ...NATIVE_OBJECT_ENTRIES[objectId]}) : null;
}

function index(items, label) {
  if (!Array.isArray(items)) fail(`${label}_missing`);
  const result = new Map();
  for (const item of items) {
    if (!safe(item?.id) || result.has(item.id)) fail(`${label}_identity`);
    result.set(item.id, item);
  }
  return result;
}
function typed(value, schema) {
  if (!schema || typeof schema !== 'object') fail('schema_missing');
  const types = {string: v => typeof v === 'string', boolean: v => typeof v === 'boolean',
    integer: Number.isSafeInteger, number: v => typeof v === 'number' && Number.isFinite(v),
    null: v => v === null, object: v => v !== null && typeof v === 'object' && !Array.isArray(v), array: Array.isArray};
  const allowed = Array.isArray(schema.type) ? schema.type : [schema.type];
  if (schema.type && !allowed.some(type => types[type]?.(value))) fail('value_type');
  if (schema.enum && !schema.enum.some(v => equal(v, value))) fail('value_enum');
  if (own(schema, 'const') && !equal(schema.const, value)) fail('value_const');
  if (schema.minimum !== undefined && value < schema.minimum) fail('value_minimum');
  if (schema.maximum !== undefined && value > schema.maximum) fail('value_maximum');
  if (schema.type === 'object') {
    for (const key of schema.required || []) if (!own(value, key)) fail('input_required');
    for (const [key, v] of Object.entries(value)) {
      if (!safe(key)) fail('input_key');
      if (own(schema.properties || {}, key)) typed(v, schema.properties[key]);
      else if (schema.additionalProperties === false) fail('input_unknown');
    }
  }
  if (schema.type === 'array' && schema.items) value.forEach(v => typed(v, schema.items));
}
function when(expression, state) {
  if (!expression || typeof expression !== 'object' || Array.isArray(expression)) fail('guard_invalid');
  if (own(expression, 'all') || own(expression, 'any')) {
    const key = own(expression, 'all') ? 'all' : 'any';
    if (Object.keys(expression).length !== 1 || !Array.isArray(expression[key])) fail('guard_invalid');
    const values = expression[key].map(part => when(part, state));
    return key === 'all' ? values.every(Boolean) : values.some(Boolean);
  }
  if (!own(state, expression.field)) fail('guard_field');
  const operators = Object.keys(expression).filter(key => key !== 'field');
  if (operators.length !== 1) fail('guard_invalid');
  const op = operators[0], value = state[expression.field], expected = expression[op];
  if (op === 'equals') return equal(value, expected);
  if (op === 'not_equals') return !equal(value, expected);
  if (op === 'in' && Array.isArray(expected)) return expected.some(item => equal(item, value));
  if (op === 'gte' && typeof value === 'number' && typeof expected === 'number') return value >= expected;
  fail('guard_operator');
}

export function createNativeSimulation(input) {
  const content = clone(input), mechanism = content?.mechanism;
  if (!supportsNativeWorkbench(mechanism)) fail('unsupported_adapter');
  if (typeof content.batchKey !== 'string' || !content.batchKey) fail('batch_missing');
  const objects = index(content.objects, 'objects'), evidence = index(content.evidence, 'evidence');
  const fields = index(mechanism.state_fields, 'fields'), steps = index(mechanism.steps, 'steps'), cases = index(mechanism.cases, 'cases');
  if (!objects.has(mechanism.owner_id) || !cases.size || !steps.size) fail('mechanism_incomplete');
  const requiredFields = NATIVE_OPERATIONS[mechanism.adapter]?.fields || (mechanism.adapter === BUDGET_ADAPTER ? BUDGET_FIELDS : NATIVE_FIELDS);
  for (const id of requiredFields) if (!fields.has(id)) fail(`native_field_missing:${id}`);
  function sources(ids) {
    if (!Array.isArray(ids) || !ids.length) fail('evidence_missing');
    for (const id of ids) {
      const source = evidence.get(id);
      if (!source?.address || !source.frozen?.address || !source.excerpt || !/^[a-f0-9]{64}$/.test(source.sha256 || '')
        || source.sha256 !== source.frozen.sha256 || !Number.isInteger(source.start_line) || source.start_line < 1
        || !Number.isInteger(source.end_line) || source.end_line < source.start_line) fail('evidence_unavailable');
    }
  }
  sources(mechanism.evidence_ids);
  const participants = new Set((mechanism.participants || []).map(p => p.object_id));
  for (const participant of participants) if (!objects.has(participant)) fail('participant_unavailable');
  const initial = Object.create(null);
  for (const field of fields.values()) {
    if (!objects.has(field.authority_id) || !participants.has(field.authority_id)) fail('state_authority_unavailable');
    if (!['observation', 'authoritative'].includes(field.state_scope)) fail('state_scope_invalid');
    typed(field.initial, field); initial[field.id] = clone(field.initial);
  }
  for (const step of steps.values()) {
    if (!objects.has(step.from) || !objects.has(step.to)) fail('endpoint_unavailable');
    if (!Array.isArray(step.next) || step.next.some(id => !steps.has(id))) fail('next_unknown');
    if (!Array.isArray(step.state_changes)) fail('changes_missing');
    for (const change of step.state_changes) {
      if (!fields.has(change.field)) fail('change_field');
      if (!['set', 'increment', 'append'].includes(change.op)) fail('change_operator');
      if (change.writer_id !== fields.get(change.field).authority_id) fail('change_authority');
    }
    sources(step.evidence_ids);
  }
  function replay(scenario, cursor) {
    const state = clone(initial), trace = [];
    for (const id of scenario.path.slice(0, cursor)) {
      const step = steps.get(id);
      if (step.when && !when(step.when, state)) fail(`guard_failed:${id}`);
      const changes = [];
      for (const change of step.state_changes) {
        let value = change.value;
        if (value && typeof value === 'object' && !Array.isArray(value) && own(value, 'input')) {
          if (Object.keys(value).length !== 1 || !own(scenario.inputs, value.input)) fail('input_reference');
          value = scenario.inputs[value.input];
        }
        const before = clone(state[change.field]);
        if (change.op === 'set') state[change.field] = clone(value);
        if (change.op === 'increment') {
          if (typeof before !== 'number' || typeof value !== 'number') fail('increment_type');
          state[change.field] += value;
        }
        if (change.op === 'append') {
          if (!Array.isArray(before)) fail('append_type');
          state[change.field].push(clone(value));
        }
        typed(state[change.field], fields.get(change.field));
        changes.push({field: change.field, before, after: clone(state[change.field])});
      }
      trace.push({id, title: step.title, changes});
    }
    return {state, trace};
  }
  for (const scenario of cases.values()) {
    typed(scenario.inputs, mechanism.input_schema);
    sources(scenario.evidence_ids);
    if (!Array.isArray(scenario.path) || !scenario.path.length || scenario.path.some(id => !steps.has(id))) fail('case_path');
    scenario.path.forEach((id, i) => { if (i && !steps.get(scenario.path[i - 1]).next.includes(id)) fail('case_transition'); });
    replay(scenario, scenario.path.length);
  }
  let caseId = mechanism.cases[0].id, cursor = 0;
  function getScene() {
    const scenario = cases.get(caseId), {state, trace} = replay(scenario, cursor);
    const current = cursor ? steps.get(scenario.path[cursor - 1]) : null, next = steps.get(scenario.path[cursor]) || null;
    return clone({mode: 'simulated', batchKey: content.batchKey, mechanism_id: mechanism.id, owner_id: mechanism.owner_id,
      case_id: caseId, cursor, state, current, next, trace, outcome: next ? null : scenario.outcome,
      evidence_ids: [...new Set([...(current?.evidence_ids || mechanism.evidence_ids), ...scenario.evidence_ids])]});
  }
  function getViewState() { return {schema: 'native-workbench-state.v1', batchKey: content.batchKey, mechanismId: mechanism.id, caseId, cursor}; }
  function restoreViewState(saved) {
    if (saved?.schema !== 'native-workbench-state.v1' || saved.batchKey !== content.batchKey || saved.mechanismId !== mechanism.id
      || !cases.has(saved.caseId) || !Number.isInteger(saved.cursor) || saved.cursor < 0 || saved.cursor > cases.get(saved.caseId).path.length) return false;
    caseId = saved.caseId; cursor = saved.cursor; return true;
  }
  return {mechanism, objects, evidence, fields, getScene, getViewState, restoreViewState,
    perform(id) { if (steps.get(cases.get(caseId).path[cursor])?.id !== id) return false; cursor++; return true; },
    back() { if (!cursor) return false; cursor--; return true; },
    reset() { cursor = 0; },
    chooseCase(id) { if (!cases.has(id)) return false; caseId = id; cursor = 0; return true; }};
}
