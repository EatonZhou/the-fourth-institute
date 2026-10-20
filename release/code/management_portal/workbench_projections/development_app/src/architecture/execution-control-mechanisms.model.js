// The parent supplies an authorized, pinned snapshot. Only these eight domains
// belong to this interpreter; other users of the adapter have separate owners.
export const EXECUTION_CONTROL_ADAPTER = 'execution-control.declarative-transitions.v1';
export const EXECUTION_CONTROL_FIELDS = Object.freeze({
  'control.submission': ['existing','same_digest','reply_lost','queue_rows','prepare_count','result'],
  'control.rpc-dispatch': ['envelope_valid','authenticated','actor_matches','dispatched','error'],
  'control.preparation': ['service','bound','preparation_ok','route','queued'],
  'control.runtime-slot': ['predecessor_active','cancelled','handover','slot_acquired','outcome'],
  'control.runtime-finalization': ['checkpoint_phase','cancel_after_content','handover_after_checkpoint','provider_call_count','service_called','result_recovered','content_phase','binding_phase','outcome'],
  'control.agent-service': ['provider_known','harness_runs','native_options_prepared','harness_prepared','path'],
  'control.request-protocol': ['owner_matches','terminal','wait_timeout','cancel_sent','resubmitted','acknowledged','result_retained','outcome'],
  'control.chat-service': ['cancelled_before_call','model_present','parameters_valid','last_message_user','messages','network_called','result'],
});
export const supportsExecutionControlMechanism = m => m?.adapter === EXECUTION_CONTROL_ADAPTER
  && m.mode === 'simulated' && Object.hasOwn(EXECUTION_CONTROL_FIELDS, m.id);
const copy = x => structuredClone(x), own = (x,k) => Object.prototype.hasOwnProperty.call(x,k);
const fail = x => { throw new Error(`execution_control:${x}`); };
const safe = x => typeof x === 'string' && x && !['__proto__','prototype','constructor'].includes(x);
const equal = (a,b) => JSON.stringify(a) === JSON.stringify(b);
function index(rows, label) {
  if (!Array.isArray(rows)) fail(`${label}_missing`);
  const map = new Map();
  for (const x of rows) { if (!safe(x?.id) || map.has(x.id)) fail(`${label}_identity`); map.set(x.id,x); }
  return map;
}
function typed(value,schema) {
  if (!schema || typeof schema !== 'object') fail('schema');
  const types = {boolean:v=>typeof v==='boolean',string:v=>typeof v==='string',integer:Number.isSafeInteger,
    number:v=>typeof v==='number'&&Number.isFinite(v),object:v=>v!==null&&typeof v==='object'&&!Array.isArray(v),array:Array.isArray,null:v=>v===null};
  if (schema.type && ![schema.type].flat().some(t=>types[t]?.(value))) fail('type');
  if (schema.enum && !schema.enum.some(v=>equal(v,value))) fail('enum');
  if (own(schema,'const') && !equal(schema.const,value)) fail('const');
  if (schema.minimum!==undefined && value<schema.minimum || schema.maximum!==undefined && value>schema.maximum) fail('range');
  if (schema.type==='object') {
    for (const k of schema.required||[]) if (!own(value,k)) fail('input_required');
    for (const [k,v] of Object.entries(value)) {
      if (!safe(k)) fail('input_key');
      if (own(schema.properties||{},k)) typed(v,schema.properties[k]);
      else if (schema.additionalProperties===false) fail('input_unknown');
    }
  }
  if (schema.type==='array'&&schema.items) value.forEach(v=>typed(v,schema.items));
}
function matches(expr,state) {
  if (!expr || typeof expr!=='object' || Array.isArray(expr)) fail('guard');
  if (own(expr,'all')||own(expr,'any')) {
    const k=own(expr,'all')?'all':'any';
    if (Object.keys(expr).length!==1||!Array.isArray(expr[k])||!expr[k].length) fail('guard');
    const tests=expr[k].map(x=>matches(x,state));return k==='all'?tests.every(Boolean):tests.some(Boolean);
  }
  if (!own(state,expr.field)) fail('guard_field');
  const ops=Object.keys(expr).filter(k=>k!=='field'); if(ops.length!==1) fail('guard');
  const op=ops[0],v=state[expr.field],expected=expr[op];
  if(op==='equals')return equal(v,expected);
  if(op==='not_equals')return !equal(v,expected);
  if(op==='in'&&Array.isArray(expected))return expected.some(x=>equal(v,x));
  if(op==='gte'&&typeof v==='number'&&typeof expected==='number')return v>=expected;
  fail('guard_operator');
}
export function createExecutionControlSimulation(input) {
  const content=copy(input),m=content?.mechanism;
  if(!supportsExecutionControlMechanism(m))fail('unsupported_mechanism');
  if(typeof content.batchKey!=='string'||!content.batchKey)fail('batch');
  const objects=index(content.objects,'objects'),evidence=index(content.evidence,'evidence');
  const fields=index(m.state_fields,'fields'),steps=index(m.steps,'steps'),cases=index(m.cases,'cases');
  if(!objects.has(m.owner_id)||!steps.size||!cases.size)fail('incomplete');
  for(const id of EXECUTION_CONTROL_FIELDS[m.id])if(!fields.has(id))fail(`domain_field:${id}`);
  function sources(ids) {
    if(!Array.isArray(ids)||!ids.length)fail('evidence_missing');
    for(const id of ids){const e=evidence.get(id);if(!e?.address||!e.frozen?.address||!e.excerpt
      ||!/^[a-f0-9]{64}$/.test(e.sha256||'')||e.frozen.sha256!==e.sha256
      ||!Number.isInteger(e.start_line)||e.start_line<1||!Number.isInteger(e.end_line)||e.end_line<e.start_line)fail('evidence_unavailable');}
  }
  sources(m.evidence_ids);
  for(const p of m.participants||[])if(!objects.has(p.object_id))fail('participant_unavailable');
  const initial={};
  for(const f of fields.values()){
    typed(f.initial,f);if(f.authority_id&&!objects.has(f.authority_id))fail('authority_unavailable');
    if(f.state_scope&&!['observation','authoritative'].includes(f.state_scope))fail('state_scope');initial[f.id]=copy(f.initial);
  }
  for(const s of steps.values()){
    if(!objects.has(s.from)||!objects.has(s.to))fail('endpoint_unavailable');sources(s.evidence_ids);
    if(!Array.isArray(s.next)||s.next.some(id=>!steps.has(id))||!Array.isArray(s.state_changes))fail('step_contract');
    for(const c of s.state_changes){if(!fields.has(c.field)||!['set','increment','append'].includes(c.op))fail('change_contract');
      if(c.writer_id&&!objects.has(c.writer_id))fail('writer_unavailable');}
  }
  function replay(c,n){
    const state=copy(initial),trace=[];
    for(const id of c.path.slice(0,n)){
      const s=steps.get(id);if(own(s,'when')&&!matches(s.when,state))fail(`guard_blocked:${id}`);
      const changes=[];
      for(const change of s.state_changes){let value=change.value;
        if(value&&typeof value==='object'&&own(value,'input')){
          if(Object.keys(value).length!==1||!own(c.inputs,value.input))fail('input_reference');value=c.inputs[value.input];}
        const before=copy(state[change.field]);
        if(change.op==='set')state[change.field]=copy(value);
        if(change.op==='increment'){if(typeof before!=='number'||typeof value!=='number')fail('increment');state[change.field]+=value;}
        if(change.op==='append'){if(!Array.isArray(before))fail('append');state[change.field].push(copy(value));}
        typed(state[change.field],fields.get(change.field));changes.push({field:change.field,before,after:copy(state[change.field]),writer_id:change.writer_id??null,authority_id:fields.get(change.field).authority_id??null});
      }
      trace.push({id,title:s.title,changes});
    }return {state,trace};
  }
  for(const c of cases.values()){
    sources(c.evidence_ids);typed(c.inputs,m.input_schema);
    if(!Array.isArray(c.path)||!c.path.length||c.path[0]!=='load'||new Set(c.path).size!==c.path.length)fail('case_path');
    c.path.forEach((id,i)=>{if(!steps.has(id)||i&&!steps.get(c.path[i-1]).next.includes(id))fail('case_next');});
    if(steps.get(c.path.at(-1)).next.length)fail('case_incomplete');replay(c,c.path.length);
  }
  let caseId=m.cases[0].id,cursor=1;
  function getScene(){const c=cases.get(caseId),{state,trace}=replay(c,cursor),current=steps.get(c.path[cursor-1]),next=steps.get(c.path[cursor])??null;
    return copy({mode:'simulated',mechanismId:m.id,adapter:m.adapter,batchKey:content.batchKey,caseId,cursor,prefix:c.path.slice(0,cursor),state,trace,current,next,inputs:c.inputs,outcome:next?null:c.outcome,
      evidence_ids:[...new Set([...m.evidence_ids,...c.evidence_ids,...(current?.evidence_ids||[])])]});}
  function getViewState(){return {schema:'execution-control-view.v1',batchKey:content.batchKey,mechanismId:m.id,caseId,cursor};}
  return {getContent:()=>copy(content),getScene,getViewState,
    selectCase(id){if(!cases.has(id))fail('case_unknown');caseId=id;cursor=1;return getScene();},
    advance(id){if(getScene().next?.id!==id)fail('step_not_next');cursor++;return getScene();},
    back(){cursor=Math.max(1,cursor-1);return getScene();},
    restore(saved){if(saved?.schema!=='execution-control-view.v1'||saved.batchKey!==content.batchKey||saved.mechanismId!==m.id||!cases.has(saved.caseId)
      ||!Number.isInteger(saved.cursor)||saved.cursor<1||saved.cursor>cases.get(saved.caseId).path.length)fail('restore_scope');caseId=saved.caseId;cursor=saved.cursor;return getScene();}
  };
}
