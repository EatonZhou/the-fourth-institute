// The parent owns permission checks and the fixed batch. This interpreter only
// replays declared local cases; it never starts requests or invents transitions.
export const PROVIDER_ADAPTERS = Object.freeze({
  'portal.architecture.runtime-selection.v1': {view: 'dispatch', fields: ['service_type','provider','destination','native_session']},
  'portal.architecture.claude-credentials.v1': {view: 'credentials', fields: ['route','conflicting_api_fields','directory_matches','credential_source','configuration']},
  'portal.architecture.kimi-launch.v1': {view: 'kimi', fields: ['loopback','profile_valid','runtime_exists','stage','database_env_removed','listen_scope']},
  'portal.architecture.dsh-start.v1': {view: 'dsh', fields: ['already_healthy','bound_route_valid','startup_result','launch_lock','process_started','status']},
  'portal.architecture.opencode-bridge.v1': {view: 'bridge', fields: ['route','binding_valid','server_ready','key_source','child_key_env','status','event_transport']},
  'portal.architecture.api-chat-lifecycle.v1': {view: 'apiLifecycle', fields: ['request_payload','response_payload','wait_scope','attempt_count','attempt_limit','attempts_remaining','portal_waiting','request_sent','result_accepted','transport_observation','remote_compute_state','upstream_interrupt_sent','late_result_disposition']},
  'portal.architecture.api-jev-lifecycle.v1': {view: 'apiLifecycle', fields: ['request_payload','response_payload','wait_scope','attempt_count','attempt_limit','attempts_remaining','portal_waiting','request_sent','result_accepted','transport_observation','remote_compute_state','upstream_interrupt_sent','late_result_disposition']},
  'portal.architecture.relay-connection.v1': {view: 'relayLifecycle', fields: ['connection_open','slot_held','can_read_next_frame','header_bytes_received','body_bytes_received','frame_length','frame_limit_bytes','frame_deadline_seconds','error_class','execution_submit_count','response_status','response_written','remote_execution_state']},
  'portal.architecture.service-protocol.v1': {view: 'protocol', fields: ['kind', 'decoder', 'status']},
  'portal.architecture.chat-request.v1': {view: 'chat', fields: ['request_count', 'status', 'remote_state', 'output']},
  'portal.architecture.typed-answer.v1': {view: 'typed', fields: ['request_sent', 'request_check', 'response_check', 'status', 'answers_accepted']},
  'portal.architecture.complete-chat.v1': {view: 'complete', fields: ['http_sent', 'queue_submitted', 'schema_check', 'answer_check', 'structured_available', 'retryable', 'status']},
  'portal.architecture.api-account.v1': {view: 'account', fields: ['already_native_account', 'account_source', 'extra_account_created', 'status']},
  'portal.architecture.native-endpoints.v1': {view: 'endpoints', fields: ['route', 'service', 'credential_authority', 'status']},
  'portal.architecture.relay-gate.v1': {view: 'gate', fields: ['peer_allowed', 'slot_available', 'frame_result', 'slot_held', 'connection', 'response_status', 'frame_decoded']},
  'portal.architecture.relay-routing.v1': {view: 'routing', fields: ['validation', 'api_selection', 'model_source', 'status']},
  'portal.architecture.relay-roundtrip.v1': {view: 'roundtrip', fields: ['submitted', 'session_mode', 'request_id', 'response_status', 'remote_stopped']},
  'provider-capabilities.negotiate.v1': {view: 'capabilities', fields: ['request', 'operation_allowed', 'feature_enabled', 'reason', 'error']},
});
export const supportsProviderMechanisms = mechanism => Object.hasOwn(PROVIDER_ADAPTERS, mechanism?.adapter || '') && mechanism.mode === 'simulated';
// Authored entries use exact object identities. Composition roots keep their graph.
const providerEntry = (kind, primary, modes = [primary], delegatedModes = []) => Object.freeze({kind, defaultMechanismId: kind === 'composition' ? null : primary,
  factory: kind === 'composition' ? null : 'createProviderMechanisms', primaryMechanismId: primary, modeMechanismIds: Object.freeze(modes),
  delegatedModes: Object.freeze(delegatedModes.map(mode => Object.freeze(mode)))});
export const PROVIDER_OBJECT_ENTRIES = Object.freeze({
  providers: providerEntry('composition', 'ps.mechanism.runtime-selection'),
  'model-services': providerEntry('composition', 'ps.mechanism.service-protocol'),
  'providers.relay': providerEntry('composition', 'ps.mechanism.relay-connection', ['ps.mechanism.relay-connection', 'ps.mechanism.relay-roundtrip']),
  'provider-capabilities': providerEntry('protocol', 'provider-capabilities.negotiate', ['provider-capabilities.negotiate'],
    [{mechanismId: 'execution.lifecycle', adapter: 'execution.lifecycle.v1', factory: 'createExecutionWorkbench'},
      {mechanismId: 'provider-capabilities.preflight', adapter: 'provider-capabilities.preflight.v1', factory: 'createProviderPreflight'}]),
  'model-api': providerEntry('protocol', 'ps.mechanism.api-chat-lifecycle', ['ps.mechanism.api-chat-lifecycle', 'ps.mechanism.api-jev-lifecycle',
    'ps.mechanism.complete-chat', 'ps.mechanism.api-account', 'ps.mechanism.relay-connection', 'ps.mechanism.relay-roundtrip', 'ps.mechanism.chat-request', 'ps.mechanism.typed-answer']),
  'providers.relay.connection-gate': providerEntry('mechanism', 'ps.mechanism.relay-gate'),
  'providers.relay.resource-routing': providerEntry('mechanism', 'ps.mechanism.relay-routing'),
  'providers.claude-runtime': providerEntry('mechanism', 'ps.mechanism.claude-credentials'),
  'providers.kimi-runtime': providerEntry('mechanism', 'ps.mechanism.kimi-launch'),
  'providers.dsh-runtime': providerEntry('mechanism', 'ps.mechanism.dsh-start'),
  'providers.opencode-runtime': providerEntry('mechanism', 'ps.mechanism.opencode-bridge'),
  'model-services.chat-completion': providerEntry('mechanism', 'ps.mechanism.api-chat-lifecycle', ['ps.mechanism.api-chat-lifecycle', 'ps.mechanism.chat-request']),
  'model-services.typed-decisions': providerEntry('mechanism', 'ps.mechanism.api-jev-lifecycle', ['ps.mechanism.api-jev-lifecycle', 'ps.mechanism.typed-answer']),
  'model-services.native-inference': providerEntry('mechanism', 'ps.mechanism.native-endpoints'),
});
export function getProviderWorkbenchEntry(objectId) {
  return Object.hasOwn(PROVIDER_OBJECT_ENTRIES, objectId) ? structuredClone({objectId, ...PROVIDER_OBJECT_ENTRIES[objectId]}) : null;
}
export function getProviderDelegatedModes(content, objectId) {
  const entry = getProviderWorkbenchEntry(objectId), subject = content.objects.find(object => object.id === objectId);
  if (!subject) return [];
  return (entry?.delegatedModes || []).flatMap(mode => {
    const matches = (content.mechanisms || []).filter(mechanism => mechanism.id === mode.mechanismId && mechanism.adapter === mode.adapter && mechanism.mode === 'simulated');
    if (!subject.mechanism_ids?.includes(mode.mechanismId) || matches.length !== 1) return [];
    // The parent authorizes the complete content and validates the target again on navigation.
    const mechanism = matches[0], ids = [...(mechanism.evidence_ids || []), ...(mechanism.steps || []).flatMap(step => step.evidence_ids || []), ...(mechanism.cases || []).flatMap(item => item.evidence_ids || [])];
    if (!ids.length || ids.some(id => !content.evidence.some(evidence => evidence.id === id))) return [];
    return [{id: mode.mechanismId, title: mechanism.title, route: {kind: entry.kind, objectId, mechanismId: mode.mechanismId}}];
  });
}

// Navigation/state plumbing shared by this author's two workbenches, not a shared domain renderer.
// Only the explicit subject's declared, supplied and independently valid modes are offered.
export function createDomainModeSession(content, objectId, entry, createSimulation) {
  const current = createSimulation(content), simulations = new Map([[current.mechanism.id, current]]), inputs = new Map([[current.mechanism.id, content]]);
  const subject = content.objects.find(object => object.id === objectId), allowed = entry?.modeMechanismIds || [];
  for (const id of allowed) {
    if (simulations.has(id) || !subject?.mechanism_ids?.includes(id)) continue;
    const matches = (content.mechanisms || []).filter(mechanism => mechanism.id === id);
    if (matches.length !== 1) continue;
    const input = {...content, mechanism: matches[0]};
    try { simulations.set(id, createSimulation(input)); inputs.set(id, input); } catch { /* Unavailable mode: no name or count is exposed. */ }
  }
  let activeId = current.mechanism.id, views = new Map();
  const order = [...allowed.filter(id => simulations.has(id)), ...[...simulations.keys()].filter(id => !allowed.includes(id))];
  return {
    options: order.map(id => ({id, title: simulations.get(id).mechanism.title})),
    get simulation() { return simulations.get(activeId); },
    select(id, viewState) {
      if (!simulations.has(id) || id === activeId) return null;
      views.set(activeId, structuredClone(viewState)); activeId = id;
      return structuredClone(views.get(id) || {simulation: simulations.get(id).getViewState(), scrollTop: 0, sourcesOpen: false});
    },
    snapshot(viewState) {
      const saved = new Map(views); saved.set(activeId, structuredClone(viewState));
      return {schema: 'provider-domain-modes.v1', batchKey: content.batchKey, objectId, activeMechanismId: activeId,
        views: [...saved].map(([mechanismId, state]) => ({mechanismId, state}))};
    },
    restore(saved, expectedActiveSimulation) {
      if (saved?.schema !== 'provider-domain-modes.v1' || saved.batchKey !== content.batchKey || saved.objectId !== objectId
        || !simulations.has(saved.activeMechanismId) || !Array.isArray(saved.views)) return null;
      const checked = new Map(), staged = new Map();
      for (const item of saved.views) {
        if (!item || checked.has(item.mechanismId) || !simulations.has(item.mechanismId)) return null;
        const simulation = createSimulation(inputs.get(item.mechanismId));
        if (!simulation.restoreViewState(item.state?.simulation)) return null;
        checked.set(item.mechanismId, structuredClone(item.state)); staged.set(item.mechanismId, simulation);
      }
      if (!checked.has(saved.activeMechanismId)) return null;
      if (expectedActiveSimulation && JSON.stringify(checked.get(saved.activeMechanismId).simulation) !== JSON.stringify(expectedActiveSimulation)) return null;
      // All paths validate before changing any current mode or view.
      for (const [id, input] of inputs) if (!staged.has(id)) staged.set(id, createSimulation(input));
      for (const [id, simulation] of staged) simulations.set(id, simulation);
      views = checked; activeId = saved.activeMechanismId; return structuredClone(checked.get(activeId));
    }
  };
}
const clone = value => structuredClone(value);
const own = (value, key) => Object.prototype.hasOwnProperty.call(value, key);
const safe = key => typeof key === 'string' && key && !['__proto__', 'constructor', 'prototype'].includes(key);
const equal = (a, b) => JSON.stringify(a) === JSON.stringify(b);
const fail = reason => { throw new Error(`provider_mechanisms:${reason}`); };
function index(items, label) {
  if (!Array.isArray(items)) fail(`${label}_missing`);
  const result = new Map();
  for (const item of items) { if (!safe(item?.id) || result.has(item.id)) fail(`${label}_identity`); result.set(item.id, item); }
  return result;
}
function typed(value, schema) {
  if (!schema || typeof schema !== 'object') fail('schema_missing');
  const types = {string: v => typeof v === 'string', boolean: v => typeof v === 'boolean', integer: Number.isSafeInteger,
    number: v => typeof v === 'number' && Number.isFinite(v), null: v => v === null,
    object: v => v !== null && typeof v === 'object' && !Array.isArray(v), array: Array.isArray};
  if (schema.type && !(Array.isArray(schema.type) ? schema.type : [schema.type]).some(type => types[type]?.(value))) fail('value_type');
  if (schema.enum && !schema.enum.some(v => equal(v, value))) fail('value_enum');
  if (own(schema, 'const') && !equal(value, schema.const)) fail('value_const');
  if (schema.minimum !== undefined && value < schema.minimum || schema.maximum !== undefined && value > schema.maximum) fail('value_range');
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
function when(expr, state) {
  if (!expr || typeof expr !== 'object' || Array.isArray(expr)) fail('guard_invalid');
  if (own(expr, 'all') || own(expr, 'any')) {
    const key = own(expr, 'all') ? 'all' : 'any';
    if (Object.keys(expr).length !== 1 || !Array.isArray(expr[key])) fail('guard_invalid');
    const matches = expr[key].map(part => when(part, state));
    return key === 'all' ? matches.every(Boolean) : matches.some(Boolean);
  }
  if (!own(state, expr.field)) fail('guard_field');
  const ops = Object.keys(expr).filter(k => k !== 'field');
  if (ops.length !== 1) fail('guard_invalid');
  const op = ops[0], value = state[expr.field], expected = expr[op];
  if (op === 'equals') return equal(value, expected);
  if (op === 'not_equals') return !equal(value, expected);
  if (op === 'in' && Array.isArray(expected)) return expected.some(v => equal(v, value));
  if (op === 'gte' && typeof value === 'number' && typeof expected === 'number') return value >= expected;
  fail('guard_operator');
}
export function createProviderSimulation(input) {
  if (!supportsProviderMechanisms(input?.mechanism)) fail('unsupported_adapter');
  return createDeclaredProviderSimulation(input, {...PROVIDER_ADAPTERS[input.mechanism.adapter], adapter: input.mechanism.adapter});
}
// Shared typed replay only. Each dedicated view supplies its exact adapter contract.
export function createDeclaredProviderSimulation(input, descriptor) {
  const content = clone(input), mechanism = content?.mechanism;
  if (mechanism?.mode !== 'simulated' || !descriptor?.adapter || descriptor.adapter !== mechanism.adapter
    || descriptor.mechanismId && descriptor.mechanismId !== mechanism.id) fail('unsupported_adapter');
  // Parent-validated opaque identity; preserve every byte for snapshot matching.
  if (typeof content.batchKey !== 'string' || content.batchKey.length === 0) fail('batch_missing');
  const objects = index(content.objects, 'objects'), evidence = index(content.evidence, 'evidence');
  const fields = index(mechanism.state_fields, 'fields'), steps = index(mechanism.steps, 'steps'), cases = index(mechanism.cases, 'cases');
  if (!objects.has(mechanism.owner_id) || !steps.size || !cases.size) fail('mechanism_incomplete');
  for (const id of descriptor.fields) if (!fields.has(id)) fail(`domain_field_missing:${id}`);
  const participants = new Set((mechanism.participants || []).map(p => p.object_id));
  for (const id of participants) if (!objects.has(id)) fail('participant_unavailable');
  function sources(ids) {
    if (!Array.isArray(ids) || !ids.length) fail('evidence_missing');
    for (const id of ids) {
      const e = evidence.get(id);
      if (!e?.address || !e.frozen?.address || !e.excerpt || !/^[a-f0-9]{64}$/.test(e.sha256 || '') || e.sha256 !== e.frozen.sha256
        || !Number.isInteger(e.start_line) || e.start_line < 1 || !Number.isInteger(e.end_line) || e.end_line < e.start_line) fail('evidence_unavailable');
    }
  }
  sources(mechanism.evidence_ids);
  const initial = Object.create(null);
  for (const field of fields.values()) {
    if (!['observation', 'authoritative'].includes(field.state_scope)) fail('state_scope');
    if (field.authority_id && (!objects.has(field.authority_id) || !participants.has(field.authority_id))) fail('state_authority');
    typed(field.initial, field); initial[field.id] = clone(field.initial);
  }
  for (const step of steps.values()) {
    if (!participants.has(step.from) || !participants.has(step.to)) fail('endpoint_unavailable');
    if (!Array.isArray(step.next) || step.next.some(id => !steps.has(id))) fail('next_unknown');
    if (!Array.isArray(step.state_changes)) fail('changes_missing');
    for (const change of step.state_changes) {
      if (!fields.has(change.field)) fail('change_field');
      if (!['set', 'increment', 'append'].includes(change.op)) fail('change_operator');
      if (change.writer_id && !participants.has(change.writer_id)) fail('writer_unavailable');
      if (fields.get(change.field).authority_id && change.writer_id !== fields.get(change.field).authority_id) fail('change_authority');
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
        if (change.op === 'increment') { if (typeof before !== 'number' || typeof value !== 'number') fail('increment_type'); state[change.field] += value; }
        if (change.op === 'append') { if (!Array.isArray(before)) fail('append_type'); state[change.field].push(clone(value)); }
        typed(state[change.field], fields.get(change.field)); changes.push({field: change.field, before, after: clone(state[change.field])});
      }
      trace.push({id, title: step.title, changes});
    }
    return {state, trace};
  }
  for (const scenario of cases.values()) {
    typed(scenario.inputs, mechanism.input_schema); sources(scenario.evidence_ids);
    if (!Array.isArray(scenario.path) || !scenario.path.length || scenario.path.some(id => !steps.has(id))) fail('case_path');
    scenario.path.forEach((id, i) => { if (i && !steps.get(scenario.path[i - 1]).next.includes(id)) fail('case_transition'); });
    const {state} = replay(scenario, scenario.path.length);
    if (scenario.outcome && typeof scenario.outcome === 'object') for (const [field, value] of Object.entries(scenario.outcome)) {
      if (fields.has(field) && !equal(state[field], value)) fail('case_outcome');
    }
  }
  let caseId = mechanism.cases[0].id, cursor = 0;
  function getScene() {
    const scenario = cases.get(caseId), {state, trace} = replay(scenario, cursor);
    const current = cursor ? steps.get(scenario.path[cursor - 1]) : null, next = steps.get(scenario.path[cursor]) || null;
    return clone({mode: 'simulated', batchKey: content.batchKey, mechanism_id: mechanism.id, owner_id: mechanism.owner_id,
      adapter: mechanism.adapter, case_id: caseId, cursor, state, current, next, trace, outcome: next ? null : scenario.outcome,
      evidence_ids: [...new Set([...(current?.evidence_ids || mechanism.evidence_ids), ...scenario.evidence_ids])]});
  }
  return {mechanism, descriptor, objects, evidence, fields, getScene,
    getViewState: () => ({schema: 'provider-mechanisms-state.v1', batchKey: content.batchKey, mechanismId: mechanism.id, caseId, cursor}),
    restoreViewState(saved) {
      if (saved?.schema !== 'provider-mechanisms-state.v1' || saved.batchKey !== content.batchKey || saved.mechanismId !== mechanism.id
        || !cases.has(saved.caseId) || !Number.isInteger(saved.cursor) || saved.cursor < 0 || saved.cursor > cases.get(saved.caseId).path.length) return false;
      caseId = saved.caseId; cursor = saved.cursor; return true;
    },
    chooseCase(id) { if (!cases.has(id)) return false; caseId = id; cursor = 0; return true; },
    perform(id) { if (steps.get(cases.get(caseId).path[cursor])?.id !== id) return false; cursor++; return true; },
    back() { if (!cursor) return false; cursor--; return true; }, reset() { cursor = 0; }};
}
