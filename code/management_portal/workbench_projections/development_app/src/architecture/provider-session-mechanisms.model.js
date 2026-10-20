// The four local demonstrations consume only the parent's authorized snapshot.
export const PROVIDER_SESSION_ADAPTERS = Object.freeze({
  'provider.catalog-settings': 'provider.catalog-settings.v1',
  'provider.session-resolve': 'provider.session-resolve.v1',
  'provider.binding-cas': 'provider.binding-cas.v1',
  'provider.adapter-boundary': 'provider.adapter-boundary.v1',
});
const entries = {
  'execution.providers': ['composition', null, Object.keys(PROVIDER_SESSION_ADAPTERS)],
  'execution.providers.sessions': ['composition', null, ['provider.session-resolve', 'provider.binding-cas']],
  'execution.providers.catalog': ['mechanism', 'provider.catalog-settings', ['provider.catalog-settings']],
  'execution.providers.sessions.resolve': ['mechanism', 'provider.session-resolve', ['provider.session-resolve']],
  'execution.providers.sessions.bindings': ['mechanism', 'provider.binding-cas', ['provider.binding-cas']],
  'execution.providers.adapters': ['mechanism', 'provider.adapter-boundary', ['provider.adapter-boundary']],
};
const clone = value => structuredClone(value);
const equal = (a, b) => JSON.stringify(a) === JSON.stringify(b);
const own = (value, key) => Object.hasOwn(value, key);
const fail = reason => { throw Error(`provider_session:${reason}`); };
export const supportsProviderSessionMechanism = m => !!m && own(PROVIDER_SESSION_ADAPTERS, m.id) && PROVIDER_SESSION_ADAPTERS[m.id] === m.adapter && m.mode === 'simulated';
export function getProviderSessionWorkbenchEntry(objectId) {
  if (!own(entries, objectId)) return null;
  const [kind, defaultMechanismId, modeMechanismIds] = entries[objectId];
  return clone({objectId, kind, defaultMechanismId, modeMechanismIds,
    delegatedModeMechanismIds:objectId === 'execution.providers.catalog' ? ['provider-capabilities.preflight'] : [],
    factory: kind === 'composition' ? null : 'createProviderSessionWorkbench'});
}
export function getProviderSessionDelegatedModes(content, objectId) {
  const entry = getProviderSessionWorkbenchEntry(objectId), object = content.objects.find(o => o.id === objectId);
  if (!object) return [];
  return (entry?.delegatedModeMechanismIds || []).flatMap(id => {
    if (!object.mechanism_ids?.includes(id)) return [];
    const matches = (content.mechanisms || []).filter(m => m.id === id && m.mode === 'simulated');
    if (matches.length !== 1) return [];
    const m = matches[0], ids = [...m.evidence_ids, ...m.steps.flatMap(s => s.evidence_ids), ...m.cases.flatMap(c => c.evidence_ids)];
    if (!ids.length || ids.some(id => !content.evidence.some(e => e.id === id))) return [];
    return [{id, title:m.title, route:{kind:'mechanism', objectId, mechanismId:id}}];
  });
}
function index(items, label) {
  if (!Array.isArray(items)) fail(label);
  const map = new Map();
  for (const item of items) {
    if (typeof item?.id !== 'string' || !item.id || ['__proto__', 'constructor', 'prototype'].includes(item.id) || map.has(item.id)) fail(`${label}_identity`);
    map.set(item.id, item);
  }
  return map;
}
function typed(value, schema) {
  const types = {string: v => typeof v === 'string', boolean: v => typeof v === 'boolean', integer: Number.isSafeInteger,
    number: v => typeof v === 'number' && Number.isFinite(v), array: Array.isArray, object: v => v !== null && typeof v === 'object' && !Array.isArray(v)};
  if (!schema || !types[schema.type]?.(value)) fail('value_type');
  if (schema.enum && !schema.enum.some(v => equal(v, value))) fail('value_enum');
  if (schema.type === 'object') {
    for (const name of schema.required || []) if (!own(value, name)) fail('input_required');
    for (const [name, item] of Object.entries(value)) {
      if (!own(schema.properties || {}, name)) { if (schema.additionalProperties === false) fail('input_unknown'); }
      else typed(item, schema.properties[name]);
    }
  }
}
function condition(when, state) {
  if (!when) return true;
  // Natural-language guard text is displayed, never parsed as a predicate.
  if (!own(state, when.field) || !own(when, 'equals') || Object.keys(when).some(k => !['field', 'equals'].includes(k))) fail('when_unsupported');
  return equal(state[when.field], when.equals);
}
export function createProviderSessionSimulation(input) {
  const content = clone({mechanism:input.mechanism, objects:input.objects, relations:input.relations, evidence:input.evidence, batchKey:input.batchKey}), m = content.mechanism;
  if (!supportsProviderSessionMechanism(m) || typeof content.batchKey !== 'string' || !content.batchKey) fail('unsupported_content');
  const objects = index(content.objects, 'objects'), evidence = index(content.evidence, 'evidence');
  const fields = index(m.state_fields, 'fields'), steps = index(m.steps, 'steps'), cases = index(m.cases, 'cases');
  const required = {
    'provider.catalog-settings':['selection','accepted','native_image_input','error'],
    'provider.session-resolve':['binding','prepared','binding_written','native_calls','error'],
    'provider.binding-cas':['operation','current_ref','receipt_saved','injection_count_delta','error'],
    'provider.adapter-boundary':['request_kind','context_seed_prepared','provider_calls','result_received','result_forwarded','provider_error_propagated','control_ack','observed_stopped'],
  };
  if (required[m.id].some(id => !fields.has(id))) fail('domain_field_missing');
  const participants = new Set(m.participants?.map(p => p.object_id));
  if (!objects.has(m.owner_id) || !participants.size || !cases.size || !steps.size) fail('incomplete');
  for (const id of participants) if (!objects.has(id)) fail('participant');
  const checkSources = ids => {
    if (!Array.isArray(ids) || !ids.length) fail('source_missing');
    for (const id of ids) {
      const e = evidence.get(id);
      if (!e?.address || !e.excerpt || !e.frozen?.address || !/^[a-f0-9]{64}$/.test(e.sha256 || '') || e.sha256 !== e.frozen.sha256 || !Number.isInteger(e.start_line) || e.start_line < 1 || !Number.isInteger(e.end_line) || e.end_line < e.start_line) fail('source_invalid');
    }
  };
  checkSources(m.evidence_ids);
  const initial = {};
  for (const f of fields.values()) {
    if (!['observation', 'authoritative'].includes(f.state_scope) || (f.authority_id && !participants.has(f.authority_id))) fail('field_authority');
    typed(f.initial, f); initial[f.id] = clone(f.initial);
  }
  for (const s of steps.values()) {
    if (!participants.has(s.from) || !participants.has(s.to) || !Array.isArray(s.next) || s.next.some(id => !steps.has(id))) fail('step_endpoint');
    if (s.relation_id) {
      const r = (content.relations || []).find(r => r.id === s.relation_id);
      if (!r || r.from !== s.from || r.to !== s.to) fail('step_relation');
    }
    checkSources(s.evidence_ids); condition(s.when, initial);
    if (!Array.isArray(s.state_changes)) fail('changes_missing');
    for (const c of s.state_changes) {
      const field = fields.get(c.field);
      if (!field || !['set', 'increment', 'append'].includes(c.op) || !participants.has(c.writer_id) || (field.authority_id && c.writer_id !== field.authority_id)) fail('change_authority');
    }
  }
  function replay(scenario, cursor) {
    const state = clone(initial), trace = [];
    for (const id of scenario.path.slice(0, cursor)) {
      const step = steps.get(id);
      if (!condition(step.when, state)) fail('when_failed');
      const changes = [];
      for (const c of step.state_changes) {
        let value = c.value;
        if (value && typeof value === 'object' && !Array.isArray(value) && own(value, 'input')) {
          if (Object.keys(value).length !== 1 || !own(scenario.inputs, value.input)) fail('input_reference');
          value = scenario.inputs[value.input];
        }
        const before = clone(state[c.field]);
        if (c.op === 'set') state[c.field] = clone(value);
        if (c.op === 'increment') {
          if (typeof before !== 'number' || typeof value !== 'number') fail('increment_type');
          state[c.field] += value;
        }
        if (c.op === 'append') { if (!Array.isArray(before)) fail('append_type'); state[c.field].push(clone(value)); }
        typed(state[c.field], fields.get(c.field));
        changes.push({field: c.field, before, after: clone(state[c.field]), writer_id: c.writer_id});
      }
      trace.push({id, title: step.title, changes});
    }
    return {state, trace};
  }
  for (const c of cases.values()) {
    typed(c.inputs, m.input_schema); checkSources(c.evidence_ids);
    if (!Array.isArray(c.path) || !c.path.length || c.path.some(id => !steps.has(id))) fail('case_path');
    c.path.forEach((id, i) => { if (i && !steps.get(c.path[i - 1]).next.includes(id)) fail('case_next'); });
    const {state} = replay(c, c.path.length);
    if (c.outcome && typeof c.outcome === 'object') for (const [field, value] of Object.entries(c.outcome)) {
      if (!fields.has(field) || !equal(state[field], value)) fail('case_outcome');
    }
  }
  let caseId = m.cases[0].id, cursor = 0;
  const getScene = () => {
    const scenario = cases.get(caseId), current = cursor ? steps.get(scenario.path[cursor - 1]) : null, next = steps.get(scenario.path[cursor]) || null;
    return clone({mode: 'simulated', batchKey: content.batchKey, mechanism_id: m.id, owner_id: m.owner_id,
      case_id: caseId, cursor, ...replay(scenario, cursor), current, next, outcome: next ? null : scenario.outcome,
      evidence_ids: [...new Set([...(current?.evidence_ids || m.evidence_ids), ...scenario.evidence_ids])]});
  };
  return {mechanism: m, objects, evidence, fields, getScene,
    chooseCase(id) { if (!cases.has(id)) return false; caseId = id; cursor = 0; return true; },
    perform(id) { if (cases.get(caseId).path[cursor] !== id || id === undefined) return false; cursor++; return true; },
    back() { if (!cursor) return false; cursor--; return true; }, reset() { cursor = 0; },
    getViewState: () => ({schema: 'provider-session-simulation.v1', batchKey: content.batchKey, mechanismId: m.id, caseId, cursor}),
    restoreViewState(saved) {
      if (saved?.schema !== 'provider-session-simulation.v1' || saved.batchKey !== content.batchKey || saved.mechanismId !== m.id || !cases.has(saved.caseId) || !Number.isInteger(saved.cursor) || saved.cursor < 0 || saved.cursor > cases.get(saved.caseId).path.length) return false;
      caseId = saved.caseId; cursor = saved.cursor; return true;
    },
  };
}
