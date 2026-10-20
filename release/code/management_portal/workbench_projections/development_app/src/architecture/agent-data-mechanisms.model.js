// Fixed AgentData cases are replayed locally. Authorization and batch selection
// belong to the architecture parent; this module never reads resources.
export const AGENT_DATA_KEYS = Object.freeze(['segment-identity', 'trace-revision', 'execution-receipt', 'read-views', 'actual-input', 'acceptance-projection', 'completed-content']);
export const AGENT_DATA_ADAPTERS = Object.freeze(AGENT_DATA_KEYS.map(key => `agent-data.${key}.v1`));
const clone = value => structuredClone(value);
const own = (o, k) => Object.hasOwn(o, k);
const object = x => x !== null && typeof x === 'object' && !Array.isArray(x);
const fail = code => { throw new Error(`agent_data_workbench:${code}`); };
const equal = (a, b) => JSON.stringify(a) === JSON.stringify(b);
function typed(value, field) {
  const ok = field.type === 'integer' ? Number.isSafeInteger(value)
    : field.type === 'number' ? typeof value === 'number' && Number.isFinite(value)
      : field.type === 'array' ? Array.isArray(value)
        : field.type === 'object' ? object(value)
          : field.type === 'null' ? value === null : typeof value === field.type;
  return ok && (!field.enum || field.enum.some(v => equal(v, value)));
}
function condition(when, state) {
  if (!when) return true;
  if (Array.isArray(when.all)) return when.all.map(w => condition(w, state)).every(Boolean);
  if (Array.isArray(when.any)) return when.any.map(w => condition(w, state)).some(Boolean);
  if (!own(state, when.field)) fail('unknown_guard_field');
  const ops = ['equals', 'not_equals', 'in', 'gte'].filter(k => own(when, k));
  if (ops.length !== 1) fail('invalid_guard');
  const v = state[when.field];
  if (ops[0] === 'equals') return equal(v, when.equals);
  if (ops[0] === 'not_equals') return !equal(v, when.not_equals);
  if (ops[0] === 'in') return Array.isArray(when.in) && when.in.some(x => equal(x, v));
  return typeof v === 'number' && v >= when.gte;
}
export function createAgentDataSimulation(content) {
  if (!content?.batchKey || !content.mechanism || !Array.isArray(content.objects) || !Array.isArray(content.evidence)) fail('invalid_content');
  const data = clone(content), m = data.mechanism;
  if (!AGENT_DATA_KEYS.some(k => m.id === `agent-data.${k}` && m.adapter === `${m.id}.v1`) || m.mode !== 'simulated') fail('unsupported_mechanism');
  const objects = new Map(data.objects.map(o => [o.id, o])), sources = new Set(data.evidence.map(e => e.id));
  const fields = new Map(m.state_fields?.map(f => [f.id, f])), steps = new Map(m.steps?.map(s => [s.id, s])), cases = new Map(m.cases?.map(c => [c.id, c]));
  if (!objects.has(m.owner_id) || m.participants?.some(p => !objects.has(p.object_id))) fail('object_unavailable');
  if (!fields.size || fields.size !== m.state_fields.length || !steps.size || steps.size !== m.steps.length || !cases.size || cases.size !== m.cases.length) fail('invalid_model');
  for (const entity of [m, ...steps.values(), ...cases.values()]) if (!entity.evidence_ids?.length || entity.evidence_ids.some(id => !sources.has(id))) fail('evidence_unavailable');
  for (const f of fields.values()) {
    if (!typed(f.initial, f)) fail('invalid_initial');
    if (f.authority_id && !objects.has(f.authority_id)) fail('authority_unavailable');
  }
  for (const s of steps.values()) {
    if (!objects.has(s.from) || !objects.has(s.to)) fail('endpoint_unavailable');
    if (!Array.isArray(s.next) || s.next.some(id => !steps.has(id))) fail('invalid_next');
    for (const change of s.state_changes || []) {
      const f = fields.get(change.field);
      if (!f || !['set', 'increment', 'append'].includes(change.op)) fail('unsupported_change');
      if (!objects.has(change.writer_id)) fail('writer_unavailable');
      if (f.state_scope === 'authoritative' && f.authority_id && f.authority_id !== change.writer_id) fail('wrong_authority');
    }
  }
  function replay(c, length) {
    const state = Object.fromEntries([...fields.values()].map(f => [f.id, clone(f.initial)]));
    for (const id of c.path.slice(0, length)) {
      const s = steps.get(id);
      if (!s || !condition(s.when, state)) fail('guard_failed');
      for (const ch of s.state_changes || []) {
        let v = ch.value;
        if (object(v) && own(v, 'input')) {
          if (Object.keys(v).length !== 1 || !own(c.inputs, v.input)) fail('invalid_input_reference');
          v = c.inputs[v.input];
        }
        if (ch.op === 'set') state[ch.field] = clone(v);
        else if (ch.op === 'increment') {
          if (typeof state[ch.field] !== 'number' || typeof v !== 'number') fail('invalid_increment');
          state[ch.field] += v;
        } else {
          if (!Array.isArray(state[ch.field])) fail('invalid_append');
          state[ch.field].push(clone(v));
        }
        if (!typed(state[ch.field], fields.get(ch.field))) fail('invalid_change_type');
      }
    }
    return state;
  }
  for (const c of cases.values()) {
    const schema = m.input_schema;
    if (!object(c.inputs) || schema?.type !== 'object') fail('invalid_inputs');
    if ((schema.required || []).some(k => !own(c.inputs, k))) fail('missing_input');
    for (const [k,v] of Object.entries(c.inputs)) if (!schema.properties?.[k] || !typed(v,schema.properties[k])) fail('invalid_input');
    if (!c.path?.length || c.path.some(id => !steps.has(id))) fail('invalid_path');
    for (let i=1;i<c.path.length;i++) if (!steps.get(c.path[i-1]).next.includes(c.path[i])) fail('disconnected_path');
    const result = replay(c,c.path.length);
    if (object(c.outcome)) for (const [k,v] of Object.entries(c.outcome)) if (!own(result,k) || !equal(result[k],v)) fail('outcome_mismatch');
  }
  let caseId = m.cases[0].id, position = 0;
  const prefixLength = c => c.path[0] === 'load' ? 1 : 0;
  const total = c => c.path.length - prefixLength(c);
  function scene() {
    const c=cases.get(caseId), count=prefixLength(c)+position, prefix=c.path.slice(0,count);
    const step=position ? steps.get(prefix.at(-1)) : null;
    return {status:'simulated',mechanismId:m.id,caseId,position,total:total(c),stepId:step?.id||null,
      step:clone(step),next:clone(steps.get(c.path[count])||null),state:replay(c,count),inputs:clone(c.inputs),prefix,
      complete:position===total(c),outcome:position===total(c)?clone(c.outcome):null};
  }
  return {
    getContent:()=>clone(data), getScene:scene,
    choose(id) { if (!cases.has(id)) fail('unknown_case');caseId=id;position=0;return scene(); },
    advance() { position=Math.min(position+1,total(cases.get(caseId)));return scene(); },
    previous() { position=Math.max(position-1,0);return scene(); },
    reset() { position=0;return scene(); },
    getViewState:()=>({batchKey:data.batchKey,mechanismId:m.id,caseId,position}),
    restore(snapshot) {
      if (snapshot?.batchKey!==data.batchKey || snapshot?.mechanismId!==m.id || !cases.has(snapshot?.caseId)
          || !Number.isInteger(snapshot.position) || snapshot.position<0 || snapshot.position>total(cases.get(snapshot.caseId))) fail('invalid_restore');
      caseId=snapshot.caseId;position=snapshot.position;return scene();
    },
  };
}
