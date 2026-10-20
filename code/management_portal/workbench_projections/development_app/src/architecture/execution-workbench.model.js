import { schemaMatches } from '../shared/format-tags.js';
// The Execution adapter interprets only an authorized execution.lifecycle.v1
// mechanism. It does not create execution requests or infer paths from diagrams.
const copy = value => structuredClone(value);
const fail = reason => { throw new Error(`execution_workbench:${reason}`); };
const own = (value, key) => Object.prototype.hasOwnProperty.call(value, key);
const same = (a, b) => JSON.stringify(a) === JSON.stringify(b);

function indexed(items, label) {
  if (!Array.isArray(items)) fail(`${label}_missing`);
  const map = new Map();
  for (const item of items) {
    if (!item || typeof item.id !== 'string' || !item.id || map.has(item.id)) fail(`${label}_identity`);
    map.set(item.id, item);
  }
  return map;
}

function checkValue(value, schema, label) {
  if (!schema || typeof schema !== 'object') fail(`${label}_schema`);
  if (schema.enum && !schema.enum.some(item => same(item, value))) fail(`${label}_enum`);
  if (own(schema, 'const') && !same(value, schema.const)) fail(`${label}_const`);
  const types = {
    string: v => typeof v === 'string', boolean: v => typeof v === 'boolean',
    number: v => typeof v === 'number' && Number.isFinite(v), integer: Number.isSafeInteger,
    object: v => v !== null && typeof v === 'object' && !Array.isArray(v), array: Array.isArray,
    null: v => v === null,
  };
  if (schema.type && (!types[schema.type] || !types[schema.type](value))) fail(`${label}_type`);
  if (schema.type === 'object') {
    for (const field of schema.required || []) if (!own(value, field)) fail(`${label}_required`);
    for (const [key, item] of Object.entries(value)) {
      if (own(schema.properties || {}, key)) checkValue(item, schema.properties[key], label);
      else if (schema.additionalProperties === false) fail(`${label}_extra`);
    }
  }
  if (schema.type === 'array' && schema.items) value.forEach(item => checkValue(item, schema.items, label));
  if (schema.minimum !== undefined && value < schema.minimum) fail(`${label}_minimum`);
  if (schema.maximum !== undefined && value > schema.maximum) fail(`${label}_maximum`);
}

// Only declarative when expressions are executable. Textual guard remains an
// explicit premise of the owner-authored case, never JS or a guessed predicate.
function condition(expression, state) {
  if (!expression || typeof expression !== 'object') fail('guard_invalid');
  if (own(expression, 'all') || own(expression, 'any')) {
    const key = own(expression, 'all') ? 'all' : 'any';
    if (Object.keys(expression).length !== 1 || !Array.isArray(expression[key])) fail('guard_invalid');
    const results = expression[key].map(item => condition(item, state));
    return key === 'all' ? results.every(Boolean) : results.some(Boolean);
  }
  if (!own(state, expression.field)) fail('guard_unknown_field');
  const keys = Object.keys(expression).filter(key => key !== 'field');
  if (keys.length !== 1) fail('guard_invalid');
  const key = keys[0], value = state[expression.field], expected = expression[key];
  if (key === 'equals') return same(value, expected);
  if (key === 'not_equals') return !same(value, expected);
  if (key === 'in' && Array.isArray(expected)) return expected.some(item => same(item, value));
  if (key === 'gte' && typeof value === 'number' && typeof expected === 'number') return value >= expected;
  fail('guard_operator');
}

export function createExecutionSimulation(input) {
  const content = copy(input);
  const mechanism = content?.mechanism;
  if (mechanism?.adapter !== 'execution.lifecycle.v1' || mechanism?.id !== 'execution.lifecycle'
    || mechanism.mode !== 'simulated') fail('unsupported_adapter');
  if (typeof content.batchKey !== 'string' || !content.batchKey) fail('batch_missing');
  const objects = indexed(content.objects, 'objects');
  const evidence = indexed(content.evidence, 'evidence');
  const fields = indexed(mechanism.state_fields, 'fields');
  const steps = indexed(mechanism.steps, 'steps');
  const cases = indexed(mechanism.cases, 'cases');
  if (!cases.size || !steps.size || !fields.size) fail('empty_mechanism');
  if (!objects.has(mechanism.owner_id)) fail('owner_unavailable');
  const sourceIds = new Set();
  function references(ids) {
    if (!Array.isArray(ids) || !ids.length) fail('evidence_missing');
    for (const id of ids) {
      const source = evidence.get(id);
      if (!source || !source.address || !source.frozen?.address || !source.excerpt
        || !/^[a-f0-9]{64}$/.test(source.sha256 || '') || source.frozen.sha256 !== source.sha256
        || !Number.isInteger(source.start_line) || source.start_line < 1
        || !Number.isInteger(source.end_line) || source.end_line < source.start_line) fail('evidence_unavailable');
      sourceIds.add(id);
    }
  }
  references(mechanism.evidence_ids);
  for (const participant of mechanism.participants || []) if (!objects.has(participant.object_id)) fail('participant_unavailable');
  const initial = {};
  for (const field of fields.values()) {
    if (['__proto__', 'prototype', 'constructor'].includes(field.id)) fail('field_identity');
    if (own(field, 'authority_id') && !objects.has(field.authority_id)) fail('authority_unavailable');
    if (own(field, 'state_scope') && !['authoritative', 'observation'].includes(field.state_scope)) fail('state_scope');
    checkValue(field.initial, field, 'state'); initial[field.id] = copy(field.initial);
  }
  for (const step of steps.values()) {
    if (!objects.has(step.from) || !objects.has(step.to)) fail('endpoint_unavailable');
    if (!Array.isArray(step.next) || step.next.some(id => !steps.has(id))) fail('next_unknown');
    if (!Array.isArray(step.state_changes)) fail('changes_missing');
    for (const change of step.state_changes) {
      if (!fields.has(change.field)) fail('change_unknown_field');
      if (!['set', 'increment', 'append'].includes(change.op)) fail('change_operator');
      if ((own(change, 'writer_id') || own(fields.get(change.field), 'authority_id'))
        && !objects.has(change.writer_id)) fail('writer_unavailable');
    }
    references(step.evidence_ids);
  }
  const firstId = cases.values().next().value.path?.[0];
  for (const scenario of cases.values()) {
    references(scenario.evidence_ids);
    checkValue(scenario.inputs, mechanism.input_schema, 'inputs');
    if (!Array.isArray(scenario.path) || !scenario.path.length || scenario.path[0] !== firstId
      || new Set(scenario.path).size !== scenario.path.length) fail('case_path');
    scenario.path.forEach((id, i) => {
      if (!steps.has(id) || (i && !steps.get(scenario.path[i - 1]).next.includes(id))) fail('case_next');
    });
    if (steps.get(scenario.path.at(-1)).next.length) fail('case_incomplete');
  }

  function replay(scenario, count) {
    if (!Number.isInteger(count) || count < 0 || count > scenario.path.length) fail('step_count');
    let state = copy(initial), before = copy(initial), changes = [];
    for (const id of scenario.path.slice(0, count)) {
      const step = steps.get(id);
      if (own(step, 'when') && !condition(step.when, state)) fail(`guard_blocked:${id}`);
      before = copy(state); changes = [];
      for (const change of step.state_changes) {
        let value = change.value;
        if (value && typeof value === 'object' && !Array.isArray(value) && own(value, 'input')) {
          if (Object.keys(value).length !== 1 || !own(scenario.inputs, value.input)) fail('change_input');
          value = scenario.inputs[value.input];
        }
        if (change.op === 'set') state[change.field] = copy(value);
        if (change.op === 'increment') {
          if (typeof state[change.field] !== 'number' || typeof value !== 'number') fail('change_increment');
          state[change.field] += value;
        }
        if (change.op === 'append') {
          if (!Array.isArray(state[change.field])) fail('change_append');
          state[change.field].push(copy(value));
        }
        checkValue(state[change.field], fields.get(change.field), 'state');
        const definition = fields.get(change.field);
        changes.push({field: change.field, before: copy(before[change.field]), after: copy(state[change.field]),
          writer_id: change.writer_id ?? null, authority_id: definition.authority_id ?? null,
          state_scope: definition.state_scope ?? null});
      }
    }
    return {state, before, changes};
  }
  // Reject a corrupt authorized candidate before showing any of its state.
  for (const scenario of cases.values()) replay(scenario, scenario.path.length);
  let caseId = cases.keys().next().value, count = 0;
  function scene() {
    const scenario = cases.get(caseId), current = count ? steps.get(scenario.path[count - 1]) : null;
    const next = steps.get(scenario.path[count]) || null;
    const replayed = replay(scenario, count);
    return copy({status: 'simulated', batchKey: content.batchKey, mechanism_id: mechanism.id,
      adapter: mechanism.adapter, owner_id: mechanism.owner_id, case_id: caseId, inputs: scenario.inputs,
      step_id: current?.id || null, step_count: count, total_steps: scenario.path.length,
      prefix: scenario.path.slice(0, count), ...replayed, current, next,
      complete: count === scenario.path.length, outcome: count === scenario.path.length ? scenario.outcome : null,
      evidence_ids: current?.evidence_ids || scenario.evidence_ids,
      explanation: {state_model: mechanism.state_model, guard: (next || current)?.guard || '',
        provider_submissions: own(replayed.state, 'provider_call_count') ? replayed.state.provider_call_count
          : scenario.path.slice(0, count).filter(id => id === 'invoke').length}});
  }
  function viewState() {
    return {schema: 'tfi.execution-workbench-state.v1', batchKey: content.batchKey,
      mechanismId: mechanism.id, adapter: mechanism.adapter, caseId, prefix: cases.get(caseId).path.slice(0, count)};
  }
  return {
    getContent: () => copy({...content, evidence: [...sourceIds].map(id => evidence.get(id))}),
    getScene: scene, getViewState: viewState,
    selectCase(id) { if (!cases.has(id)) fail('unknown_case'); caseId = id; count = 0; return scene(); },
    advance(id) {
      const scenario = cases.get(caseId), expected = scenario.path[count];
      if (!expected) fail('case_complete');
      if (id !== undefined && id !== expected) fail('step_out_of_order');
      replay(scenario, count + 1); count++; return scene();
    },
    back() { count = Math.max(0, count - 1); return scene(); },
    reset() { count = 0; return scene(); },
    canEstablishRunning() { return cases.get(caseId).path.includes('start'); },
    establishRunning() {
      const scenario = cases.get(caseId), start = scenario.path.indexOf('start');
      if (start === -1) fail('case_has_no_start');
      replay(scenario, start + 1); count = start + 1; return scene();
    },
    restore(saved) {
      caseId = cases.keys().next().value; count = 0;
      if (!saved || !schemaMatches(saved.schema, 'tfi.execution-workbench-state.v1')
        || saved.batchKey !== content.batchKey || saved.mechanismId !== mechanism.id
        || saved.adapter !== mechanism.adapter) fail('restore_batch_mismatch');
      const scenario = cases.get(saved.caseId);
      if (!scenario || !Array.isArray(saved.prefix) || saved.prefix.length > scenario.path.length
        || !same(saved.prefix, scenario.path.slice(0, saved.prefix.length))) fail('restore_prefix');
      replay(scenario, saved.prefix.length); caseId = scenario.id; count = saved.prefix.length;
      return scene();
    },
  };
}
