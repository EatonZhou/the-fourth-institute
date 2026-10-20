import { schemaMatches } from '../shared/format-tags.js';
// Local interpretation of one authorized, immutable exploration mechanism. No I/O.
import {RESOURCE_MECHANISMS, resourceObjectModes} from './resource-mechanisms.entries.js';
export function matches(when, state) {
  if (!when) return true;
  if (when.all) return when.all.every(x => matches(x, state));
  if (when.any) return when.any.some(x => matches(x, state));
  if (Object.hasOwn(when, 'equals')) return state[when.field] === when.equals;
  if (Object.hasOwn(when, 'not_equals')) return state[when.field] !== when.not_equals;
  if (when.in) return when.in.includes(state[when.field]);
  if (Object.hasOwn(when, 'gte')) return state[when.field] >= when.gte;
  throw new Error('研究模型包含尚不支持的条件');
}
export function simulate(mechanism, scenario) {
  const state = Object.fromEntries(mechanism.state_fields.map(f => [f.id, structuredClone(f.initial)]));
  const frames = [{state: structuredClone(state), step: null}];
  let prior;
  for (const id of scenario.path) {
    const step = mechanism.steps.find(x => x.id === id);
    if (!step) throw new Error(`未知模型步骤 ${id}`);
    if (prior && !prior.next.includes(id)) throw new Error(`模型路径未相连 ${id}`);
    if (!matches(step.when, state)) throw new Error(`模型条件不满足 ${id}`);
    for (const change of step.state_changes) {
      if (!Object.hasOwn(state, change.field)) throw new Error('未声明状态字段');
      const v = change.value && typeof change.value === 'object' && Object.hasOwn(change.value, 'input')
        ? scenario.inputs[change.value.input] : change.value;
      if (v === undefined || change.op !== 'set') throw new Error('未声明输入或不支持的操作');
      state[change.field] = structuredClone(v);
    }
    frames.push({state: structuredClone(state), step});
    prior = step;
  }
  return frames;
}

const copy = value => structuredClone(value);
export function workNotes(content) {
 return (content.work_items || []).filter(x=>x.object_ids?.includes(content.object.id)).map(x=>({
  id:x.id, resolved:x.status==='resolved',
  description:x.status==='resolved' ? x.resolution?.description||x.resolution?.text||'此项已解决。' : x.action||x.description||x.problem,
  history:x.problem||x.description,
  evidence_ids:[...new Set([...(x.evidence_ids||[]),...(x.resolution?.evidence_ids||[])])],
 }));
}
export function createResourceSimulation(input) {
 const content=copy(input), m=content?.mechanism;
 const def=RESOURCE_MECHANISMS[m?.id];
 const stateContract=m?.id==='resources.mechanism.resources.permissions.coverage'
  ? schemaMatches(m.state_model?.teaching_graph_schema, 'tfi.permission-teaching-graph.v1')
  : schemaMatches(m?.state_model?.schema, 'tfi.architecture.simulation-state.v1');
 if (!def || !schemaMatches(m.adapter,def.adapter) || m.mode!=='simulated' || !stateContract) throw Error('resource_adapter_unsupported');
 const unique=(items,id)=>{const found=items.filter(x=>x.id===id);if(found.length!==1)throw Error('resource_owner_mismatch');return found[0];};
 if(content.objects){
  const owner=unique(content.objects,m.owner_id);
  if(content.object&&content.object.id!==owner.id)throw Error('resource_owner_mismatch');
  content.object=owner;
 }
 if(def.ownerId!==m.owner_id || content.object?.id!==m.owner_id)throw Error('resource_owner_mismatch');
 if(!def.fields.every(id=>m.state_fields.some(f=>f.id===id)) || new Set(m.state_fields.map(f=>f.id)).size!==m.state_fields.length)throw Error('resource_state_contract_mismatch');
 content.entryObjectId ||= content.object.id;
 content.entryObject=content.objects?unique(content.objects,content.entryObjectId):content.object;
 if(content.entryObject.id!==content.entryObjectId || !content.entryObject.mechanism_ids.includes(m.id))throw Error('resource_entry_mismatch');
 if (typeof content.batchKey!=='string' || !content.batchKey || !(content.binding||content.modelDescriptor)) throw Error('resource_batch_missing');
 const evidence=new Set(content.evidence?.map(x=>x.id));
 const references=[...m.evidence_ids,...m.steps.flatMap(x=>x.evidence_ids||[]),...workNotes(content).flatMap(x=>x.evidence_ids)];
 if (references.some(id=>!evidence.has(id))) throw Error('resource_evidence_incomplete');
 const cases=m.cases;
 if (!cases.length) throw Error('resource_reviewed_case_unavailable');
 const supplied=content.modeMechanisms||content.mechanisms||[m];
 const modes=(resourceObjectModes[content.entryObjectId]||[m.id]).flatMap(id=>{
  const found=supplied.filter(x=>x.id===id);
  if(found.length>1)throw Error('resource_entry_duplicate');
  if(!found.length)return [];
  const mode=found[0], expected=RESOURCE_MECHANISMS[id];
  if(!content.entryObject.mechanism_ids.includes(id)||!expected||expected.ownerId!==mode.owner_id||!schemaMatches(mode.adapter,expected.adapter))throw Error('resource_entry_mismatch');
  if(content.objects)unique(content.objects,mode.owner_id);
  // Parent provides only the currently authorized mechanisms. Do not expose a
  // mode whose required evidence is missing from that snapshot.
  if([...mode.evidence_ids,...mode.steps.flatMap(s=>s.evidence_ids||[])].some(id=>!evidence.has(id)))return [];
  return [mode];
 });
 content.modes=modes;
 const opening=()=>Math.min(m.id==='resources.mechanism.resources.permissions.coverage'?2:1,frames.length-1);
 let scenario=cases[0], frames=simulate(m,scenario), cursor=opening();
 function seek(value) { if (!Number.isInteger(value)||value<0||value>=frames.length) throw Error('resource_cursor_invalid');cursor=value; }
 function selectCase(id) { const chosen=cases.find(x=>x.id===id);if(!chosen)throw Error('resource_case_not_reviewed');scenario=chosen;frames=simulate(m,scenario);cursor=opening(); }
 function getViewState() { return {schema:2,batchKey:content.batchKey,entryObjectId:content.entryObjectId,mechanismId:m.id,caseId:scenario.id,cursor}; }
 function restore(saved) {
  if(saved?.schema!==2||saved.batchKey!==content.batchKey||saved.mechanismId!==m.id||saved.entryObjectId!==content.entryObjectId)throw Error('resource_batch_mismatch');
  const chosen=cases.find(x=>x.id===saved.caseId);if(!chosen)throw Error('resource_case_not_reviewed');
  const next=simulate(m,chosen);
  if(!Number.isInteger(saved.cursor)||saved.cursor<0||saved.cursor>=next.length)throw Error('resource_cursor_invalid');
  scenario=chosen;frames=next;cursor=saved.cursor;
 }
 function getObservation(){
  return frames[cursor].step?.observable || (cursor===frames.length-1?scenario.outcome:'选择一个输入情境，观察关键边界前后的变化。');
 }
 return {seek,selectCase,restore,getViewState,getObservation,reset:()=>{cursor=opening();},
  getCases:()=>copy(cases), getContent:()=>copy(content),
  getFrames:()=>copy(frames),
  getScene:()=>({mode:'simulated',batchKey:content.batchKey,entry_object_id:content.entryObjectId,object_id:m.owner_id,mechanism_id:m.id,case_id:scenario.id,cursor,state:copy(frames[cursor].state)}),
 };
}
