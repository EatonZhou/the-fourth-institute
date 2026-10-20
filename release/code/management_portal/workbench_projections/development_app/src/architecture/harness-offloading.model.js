export const HARNESS_OFFLOADING_ID = 'harness.tool-output-offloading';
export const HARNESS_OFFLOADING_ADAPTER = `${HARNESS_OFFLOADING_ID}.v1`;
export const HARNESS_PIPELINE_MODE_IDS = Object.freeze(['harness.plugin-lifecycle', HARNESS_OFFLOADING_ID]);
const clone = value => structuredClone(value);
const fail = code => { throw Error(`harness_offloading:${code}`); };
const equal = (a,b) => JSON.stringify(a) === JSON.stringify(b);
const own = (o,k) => Object.hasOwn(o,k);
function index(list) {
  if (!Array.isArray(list)) fail('list_missing');
  const result = new Map();
  for (const item of list) {
    if (typeof item?.id !== 'string' || !item.id || ['__proto__','constructor','prototype'].includes(item.id) || result.has(item.id)) fail('identity');
    result.set(item.id,item);
  }
  return result;
}
function typed(v,spec) {
  const types = {string:v=>typeof v==='string',integer:Number.isSafeInteger,boolean:v=>typeof v==='boolean'};
  if (!types[spec?.type]?.(v) || spec.enum && !spec.enum.some(item=>equal(item,v))) fail('value_type');
}
function when(expr,state) {
  if (!expr || typeof expr !== 'object' || Array.isArray(expr)) fail('condition');
  if (own(expr,'all') || own(expr,'any')) {
    const op=own(expr,'all')?'all':'any';
    if (Object.keys(expr).length!==1 || !Array.isArray(expr[op])) fail('condition');
    const values=expr[op].map(item=>when(item,state));return op==='all'?values.every(Boolean):values.some(Boolean);
  }
  if (Object.keys(expr).length!==2 || !own(expr,'equals') || !own(state,expr.field)) fail('condition');
  return equal(state[expr.field],expr.equals);
}
const requiredFields = ['scenario','phase','original_bytes','original_chars','trigger_bytes','preview_budget','artifact_retained',
  'original_retrievable','artifact_content_version','provider_visible_version','replaced','plugin_status','reason','propagated_error',
  'offloaded_count','failure_count','put_attempts','retained_exact','source','stable_scope','recall_tool','recall_schema','portal_tools',
  'result_kind','tool_name','already_offloaded'];
export function createHarnessOffloadingSimulation(input) {
  const content=clone(input),m=content?.mechanism;
  if (m?.id!==HARNESS_OFFLOADING_ID || m.adapter!==HARNESS_OFFLOADING_ADAPTER || m.mode!=='simulated') fail('adapter');
  if (typeof content.batchKey!=='string' || !content.batchKey) fail('batch');
  if (m.state_model?.condition_source!=='declared_state_only' || m.state_model.unknown_operation!=='reject'
    || Object.keys(m.state_model.operations||{}).some(op=>op!=='set')) fail('state_model');
  const objects=index(content.objects), evidence=index(content.evidence), relations=index(content.relations);
  const fields=index(m.state_fields),steps=index(m.steps),cases=index(m.cases),fixtures=index(m.teaching_fixtures);
  if (!objects.has(m.owner_id) || m.owner_id!=='worker.harness.pipeline' || !cases.size) fail('owner');
  const participants=new Set(m.participants.map(p=>p.object_id));
  for (const id of participants) if(!objects.has(id))fail('participant');
  function sources(ids) {
    if (!Array.isArray(ids)||!ids.length)fail('evidence');
    for(const id of ids){const e=evidence.get(id);if(!e?.address || !e.excerpt || !e.frozen?.address || !/^[a-f0-9]{64}$/.test(e.sha256||'')
      || e.sha256!==e.frozen.sha256 || !Number.isInteger(e.start_line)||e.start_line<1 || !Number.isInteger(e.end_line)||e.end_line<e.start_line)fail('evidence');}
  }
  sources(m.evidence_ids);
  const initial={};
  for(const id of requiredFields)if(!fields.has(id))fail('field_missing');
  for(const f of fields.values()){
    typed(f.initial,f);if(!['observation','authoritative'].includes(f.state_scope)||!participants.has(f.authority_id))fail('field_authority');
    initial[f.id]=clone(f.initial);
  }
  for(const s of steps.values()){
    if(!participants.has(s.from)||!participants.has(s.to))fail('endpoint');
    if(!Array.isArray(s.next)||s.next.some(id=>!steps.has(id)))fail('next');
    if(s.when)when(s.when,initial);
    if(s.relation_id){const r=relations.get(s.relation_id);if(!r||r.from!==s.from||r.to!==s.to)fail('relation');}
    if(!Array.isArray(s.state_changes))fail('changes');
    for(const c of s.state_changes){
      if(c.op!=='set'||!fields.has(c.field)||!participants.has(c.writer_id))fail('change');
      // Observation fields may be copied into the pipeline's final report by
      // its explicit writer; authority_id describes the observed fact owner.
      if(fields.get(c.field).state_scope==='authoritative'&&fields.get(c.field).authority_id!==c.writer_id)fail('writer');
    }
    sources(s.evidence_ids);
  }
  function replay(c,cursor){
    const state=clone(initial),trace=[],observed=new Set();
    for(const id of c.path.slice(0,cursor)){
      const step=steps.get(id),changes=[];if(step.when&&!when(step.when,state))fail('condition_failed');
      for(const change of step.state_changes){let value=change.value;
        if(value&&typeof value==='object'){
          if(Object.keys(value).length!==1||!own(value,'input')||!own(c.inputs,value.input))fail('input_binding');
          value=c.inputs[value.input];
        }
        typed(value,fields.get(change.field));changes.push({field:change.field,before:clone(state[change.field]),after:clone(value),writer_id:change.writer_id});
        state[change.field]=clone(value);observed.add(change.field);
      }
      trace.push({id,title:step.title,changes});
    }
    return {state,trace,observed_fields:[...observed]};
  }
  for(const c of cases.values()){
    if(m.input_schema?.type!=='object'||Object.keys(c.inputs||{}).length!==1||c.inputs.scenario!==c.id)fail('input');
    typed(c.inputs.scenario,m.input_schema.properties.scenario);sources(c.evidence_ids);
    if(!Array.isArray(c.path)||!c.path.length||c.path.length>256||c.path[0]!=='load'||c.path.some(id=>!steps.has(id)))fail('path');
    c.path.forEach((id,i)=>{if(i&&!steps.get(c.path[i-1]).next.includes(id))fail('transition');});
    if(steps.get(c.path.at(-1)).next.length)fail('incomplete');
    const result=replay(c,c.path.length);
    if(!c.outcome||typeof c.outcome!=='object')fail('outcome');
    for(const [field,value]of Object.entries(c.outcome))if(!fields.has(field)||!equal(value,result.state[field]))fail('outcome');
    const f=fixtures.get(c.id);
    if(!f?.values_are_synthetic||typeof f.content_pattern!=='string'||!f.content_pattern||!Number.isSafeInteger(f.content_repeat)||f.content_repeat<0||f.content_repeat>1000000)fail('teaching_fixture');
    const original=f.content_pattern.repeat(f.content_repeat);
    if(original.length!==result.state.original_chars||new TextEncoder().encode(original).length!==result.state.original_bytes)fail('fixture_size');
  }
  let caseId=m.cases[0].id,cursor=0;
  function getScene(){const c=cases.get(caseId),result=replay(c,cursor);return clone({...result,mode:'simulated',batchKey:content.batchKey,
    mechanism_id:m.id,adapter:m.adapter,case_id:caseId,cursor,total_steps:c.path.length,complete:cursor===c.path.length,
    current:cursor?steps.get(c.path[cursor-1]):null,next:steps.get(c.path[cursor])||null,outcome:cursor===c.path.length?c.outcome:null,
    evidence_ids:[...new Set([...(cursor?steps.get(c.path[cursor-1]).evidence_ids:m.evidence_ids),...c.evidence_ids])]});}
  function original(){const f=fixtures.get(caseId);return f.content_pattern.repeat(f.content_repeat);}
  return {mechanism:m,objects,evidence,getScene,original,
    recall(offset,limit){const scene=getScene();if(!scene.observed_fields.includes('original_retrievable')||!scene.state.original_retrievable)return null;
      if(!Number.isSafeInteger(offset)||offset<0||!Number.isSafeInteger(limit)||limit<1||limit>2000)return null;
      return {identity:'full-v1',offset,limit,text:original().slice(offset,offset+limit),totalChars:original().length};},
    chooseCase(id){if(!cases.has(id))return false;caseId=id;cursor=0;return true;},
    advance(){if(!getScene().next)return false;cursor++;return true;},back(){if(!cursor)return false;cursor--;return true;},reset(){cursor=0;},
    getViewState:()=>({schema:'harness-offloading-simulation.v1',batchKey:content.batchKey,mechanismId:m.id,caseId,cursor}),
    restoreViewState(v){if(v?.schema!=='harness-offloading-simulation.v1'||v.batchKey!==content.batchKey||v.mechanismId!==m.id||!cases.has(v.caseId)
      ||!Number.isSafeInteger(v.cursor)||v.cursor<0||v.cursor>cases.get(v.caseId).path.length)return false;caseId=v.caseId;cursor=v.cursor;return true;},
  };
}
