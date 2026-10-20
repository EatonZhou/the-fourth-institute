import { schemaMatches } from '../shared/format-tags.js';
// Interpret the fact owner's declared transition semantics. No runtime requests.
const clone = value => structuredClone(value);
export function condition(rule, state) {
  if (!rule) return true;
  if (rule.all) return rule.all.every(item => condition(item, state));
  if (rule.any) return rule.any.some(item => condition(item, state));
  if ('equals' in rule) return state[rule.field] === rule.equals;
  if ('gte' in rule) return state[rule.field] >= rule.gte;
  if ('in' in rule) return rule.in.includes(state[rule.field]);
  throw new Error('未支持的事实条件');
}
export function createSession(mechanism, caseId = mechanism.cases[0].id) {
  if (mechanism.adapter !== 'lineage.declarative-transitions.v1' || mechanism.mode !== 'simulated') throw new Error('不支持的机制合同');
  const scenario = mechanism.cases.find(item => item.id === caseId);
  if (!scenario) throw new Error('情境不属于当前机制');
  const state = Object.fromEntries(mechanism.state_fields.map(field => [field.id, clone(field.initial)]));
  const session = { mechanismId: mechanism.id, caseId, state, inputs: clone(scenario.inputs), history: [] };
  return transition(mechanism, session, scenario.path[0]);
}
export function available(mechanism, session) {
  const previous = mechanism.steps.find(step => step.id === session.history.at(-1));
  const entry = mechanism.cases.find(c => c.id === session.caseId)?.path[0];
  return (previous?.next || [entry]).map(id => mechanism.steps.find(step => step.id === id)).filter(step => step && condition(step.when, session.state));
}
export function transition(mechanism, previous, action) {
  if (previous.mechanismId !== mechanism.id) throw new Error('机制身份不匹配');
  const step = available(mechanism, previous).find(item => item.id === action);
  if (!step) throw new Error('当前条件不允许此动作');
  const session = clone(previous);
  for (const change of step.state_changes) {
    if (!Object.hasOwn(session.state, change.field)) throw new Error('未声明状态字段');
    const value = change.value && typeof change.value === 'object' && 'input' in change.value
      ? session.inputs[change.value.input] : change.value;
    if (change.op === 'set') session.state[change.field] = clone(value);
    else if (change.op === 'increment') session.state[change.field] += value;
    else if (change.op === 'append') session.state[change.field].push(clone(value));
    else throw new Error('未支持的事实变换');
  }
  session.history.push(step.id);
  return session;
}
export function restore(mechanism, saved) {
  if (!saved || saved.mechanismId !== mechanism.id || !Array.isArray(saved.history)) throw new Error('失效的视图状态');
  let result = createSession(mechanism, saved.caseId);
  if (saved.history[0] !== mechanism.cases.find(c => c.id === saved.caseId)?.path[0]) throw new Error('缺少起始状态');
  for (const id of saved.history.slice(1)) result = transition(mechanism, result, id);
  return result;
}


// Each implemented workface declares the state it actually explains. A shared
// transition interpreter does not imply that another workface is implemented.
const workfaceContracts = {
  "mechanism.lineage.rpc-dispatch": {
    fields:["method_known", "signature_valid", "authorized", "mode", "phase", "handler_called", "pool"],
    steps:["load", "unknown", "bind-error", "deny", "read", "write"],
  },
  "mechanism.lineage.definition-commit": {
    fields:["expected_matches", "concurrent_change", "deleted_reference", "phase", "revision", "merged", "committed"],
    steps:["load", "lock", "conflict", "merge", "removed", "commit"],
  },
  "mechanism.lineage.compile": {
    fields:["unique_ids", "operator_cycle", "connected", "phase", "stored", "plan_status"],
    steps:["load", "ids", "cycle", "normalize", "compile", "draft"],
  },
  "mechanism.lineage.composition": {
    fields:["unique_owner", "cycle", "family_target", "phase", "membership_saved", "endpoint_ids"],
    steps:["load", "duplicate", "cycle", "family", "commit", "unpack"],
  },
  "mechanism.lineage.application-target": {
    fields:["revision_matches", "already_paused", "requested_paused", "target_available", "phase", "target_switched", "configuration_revision"],
    steps:["load", "conflict", "fence", "missing", "switch"],
  },
  "mechanism.lineage.intake": {
    fields:["intake_open", "duplicate_id", "has_active", "cancel_requested", "phase", "accepted", "input_state", "stop_intent", "queue_size", "queue_observation", "internal_queue_size", "public_queued_count", "new_input_location", "work_state", "active_message_id"],
    steps:["load", "duplicate", "accept", "hold", "start", "stop", "observe_normal", "observe_closed", "observe_busy"],
  },
  "mechanism.lineage.execution-binding": {
    fields:["submitted_before", "request_saved", "stop_before_submit", "phase", "submitted_new", "resumed", "bound"],
    steps:["load", "save", "cancel", "submit", "resume"],
  },
  "mechanism.lineage.conversation-round": {
    fields:["current_binding", "stop_before_submit", "phase", "input_state", "request_durable", "execution_bound", "reply_saved"],
    steps:["load", "accept", "prepare", "cancel", "submit", "obsolete", "reply", "finalize"],
  },
  "mechanism.lineage.event-occurrence": {
    fields:["contract_current", "paused", "duplicate_cursor", "limit_reached", "phase", "occurrence_count", "accepted", "barrier_prepared"],
    steps:["load", "stale", "paused", "duplicate", "exhausted", "claim", "fanout"],
  },
  "mechanism.lineage.packet-operators": {
    fields:["operator_kind", "barrier_complete", "barrier_cancelled", "selected_any", "input_packet_ids", "phase", "output_packet_ids", "parents", "empty_branch_done", "teaching_scenario", "expected_wire_ids", "slots", "barrier_ready", "barrier_consumed", "received_wire_ids"],
    steps:["load", "cancel", "wait", "filter", "select", "empty", "reduce", "barrier_load"],
  },
  "mechanism.lineage.script-runs": {
    fields:["same_admission", "exclusive_busy", "launch_ok", "artifact_registered", "phase", "admission_saved", "process_launched", "receipt", "artifact_read"],
    steps:["load", "duplicate", "exclusive", "admit", "launch", "launch-fail", "read", "path-denied"],
  },
  "mechanism.lineage.event-effect-pipeline": {
    fields:["event_current", "effect_open", "call_known", "phase", "occurrence_claimed", "routes_frozen", "activation_logged", "input_accepted"],
    steps:["load", "reject", "claim", "routes", "effect", "pause", "invoke", "unknown"],
  },
  "mechanism.lineage.family-routing": {
    fields:["member_count", "quoted_member", "selected_current_member", "phase", "router_needed", "candidate_ready", "admission_requested"],
    steps:["load", "quote", "empty", "single", "multiple", "invalid", "selected", "admit"],
  },
  "mechanism.lineage.family-supervision": {
    fields:["original_state", "intake_open", "attempts_before", "repair_pending", "human_creator_known", "phase", "incident_state", "notice_outstanding"],
    steps:["load", "observe", "resolved", "cancelled", "wait", "paused", "repair", "escalate", "missing-creator"],
  },
  "mechanism.lineage.operation-identity": {
    fields:["identity_present", "principal_matches", "users_active", "replay", "phase", "ambient_scope_cleared", "stored_identity_installed", "effect_caller_source"],
    steps:["load", "missing", "mismatch", "inactive", "restore", "manual", "context"],
  },
  'mechanism.lineage.effect-journal': {
    fields:['effect_open','journal_finished','script_matches','restored_receipt','phase','journal_started','call_started','call_state','business_calls'],
    steps:['load','begin','replay-finished','paused','changed','call','restore','commit','unknown','reject'],
  },
  'mechanism.lineage.output-convergence': {
    fields:['binding_matches','stop_before_final','append_ok','ack_ok','phase','projection_intent','message_persisted','projection_confirmed','job_cleared'],
    steps:['load','obsolete','authorize','append','retry','confirm','ack','ack-pending'],
  },
  'mechanism.lineage.family-admission': {
    fields:['original_cancelled','member_current','relay_exists','weekly_below_threshold','review_approved','phase','admission_saved','sent','review_waiting'],
    steps:['load','withdraw','member','existing','review','admission','send'],
  },
  'mechanism.lineage.family-projection': {
    fields:['reply_exists','agent_reply','reply_to_matches','nested','phase','resolved_worker','projected'],
    steps:['load','resolve','read','exclude','project'],
  },
};
export const lineageSupportedMechanisms = Object.freeze(Object.keys(workfaceContracts));
export function supportsLineageMechanism(mechanism) {
  return mechanism?.adapter === 'lineage.declarative-transitions.v1'
    && mechanism.mode === 'simulated' && lineageSupportedMechanisms.includes(mechanism.id);
}
const fail = message => { throw new Error(`lineage:${message}`); };
function index(items, name) {
  if (!Array.isArray(items)) fail(`${name}_missing`);
  const result = new Map();
  for (const item of items) {
    if (!item || typeof item.id !== 'string' || !item.id || result.has(item.id)) fail(`${name}_identity`);
    result.set(item.id, item);
  }
  return result;
}
export function createLineageSimulation(input) {
  const content = clone(input), m = content?.mechanism;
  if (!supportsLineageMechanism(m)) fail('mechanism_not_implemented');
  if (typeof content.batchKey !== 'string' || !content.batchKey) fail('batch_missing');
  const objects = index(content.objects, 'objects'), evidence = index(content.evidence, 'evidence');
  const fields = index(m.state_fields, 'fields'), steps = index(m.steps, 'steps');
  index(m.cases, 'cases');
  if (!objects.has(m.owner_id)) fail('owner_unavailable');
  function sources(ids) {
    if (!Array.isArray(ids) || !ids.length) fail('evidence_missing');
    for (const id of ids) if (!evidence.has(id)) fail('evidence_unavailable');
  }
  sources(m.evidence_ids);
  for (const p of m.participants) if (!objects.has(p.object_id)) fail('participant_unavailable');
  for (const id of workfaceContracts[m.id].fields) {
    if (!fields.has(id)) fail('workface_field_missing');
  }
  for (const id of workfaceContracts[m.id].steps) {
    if (!steps.has(id)) fail('workface_step_missing');
  }
  const relations = index(content.relations || [], 'relations');
  for (const step of steps.values()) {
    sources(step.evidence_ids);
    if (!objects.has(step.from) || !objects.has(step.to)) fail('endpoint_unavailable');
    if (step.relation_id) {
      const r = relations.get(step.relation_id);
      if (!r || r.from !== step.from || r.to !== step.to) fail('relation_unavailable');
    }
    if (!Array.isArray(step.next) || step.next.some(id => !steps.has(id))) fail('next_unavailable');
    for (const change of step.state_changes) {
      if (!fields.has(change.field) || !['set','increment','append'].includes(change.op)) fail('state_change_invalid');
    }
  }
  for (const c of m.cases) {
    sources(c.evidence_ids);
    if (!Array.isArray(c.path) || !c.path.length || c.path.some(id => !steps.has(id))) fail('case_path_invalid');
  }
  if (m.id === 'mechanism.lineage.packet-operators') {
    const graph = m.teaching_graph;
    if (!schemaMatches(graph?.schema, 'tfi.lineage-packet-teaching-graph.v1') || graph.mode !== 'simulated' || graph.id_scope !== 'mechanism_local_only') fail('teaching_graph_invalid');
    const nodes=index(graph.nodes,'teaching_nodes'), wires=index(graph.wires,'teaching_wires');
    sources(graph.evidence_ids);
    for (const n of nodes.values()) if (n.owner_object_id !== m.owner_id) fail('teaching_owner_invalid');
    for (const kind of ['event_source','selector','reducer']) if ([...nodes.values()].filter(n=>n.kind===kind).length!==1) fail('teaching_topology_unsupported');
    for (const w of wires.values()) if (!nodes.has(w.source)||!nodes.has(w.target)) fail('teaching_wire_invalid');
    if (!Array.isArray(graph.barriers) || !graph.barriers.length) fail('teaching_barriers_missing');
    for (const b of graph.barriers) {
      if (!nodes.has(b.event_source_id)||!nodes.has(b.operator_id)||!Array.isArray(b.expected_packet_wire_ids)||!b.expected_packet_wire_ids.length) fail('teaching_barrier_invalid');
      if (new Set(b.expected_packet_wire_ids).size!==b.expected_packet_wire_ids.length || b.expected_packet_wire_ids.some(id=>wires.get(id)?.target!==b.operator_id)) fail('teaching_barrier_wire_invalid');
    }
    for (const name of ['slot_state_field','expected_set_field','arrival_field']) if (!fields.has(graph[name])) fail('teaching_field_invalid');
  }
  let session = createSession(m);
  return {
    getContent: () => clone(content),
    getSession: () => clone(session),
    available: () => clone(available(m, session)),
    perform(id) { session = transition(m, session, id); },
    choose(id) { session = createSession(m, id); },
    reset() { session = createSession(m, session.caseId); },
    getViewState: () => ({batchKey: content.batchKey, mechanismId: m.id, caseId: session.caseId, history: [...session.history]}),
    restore(saved) {
      if (saved?.batchKey !== content.batchKey) fail('restore_batch');
      // Replay the immutable case/history; never trust caller-provided state or inputs.
      session = restore(m, saved);
    },
    getScene: () => ({status: 'simulated', batchKey: content.batchKey, mechanismId: m.id,
      caseId: session.caseId, history: [...session.history], state: clone(session.state)}),
  };
}
