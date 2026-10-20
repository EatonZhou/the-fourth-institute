import {createExecutionControlSimulation} from './execution-control-mechanisms.model.js';
import {createExecutionSimulation} from './execution-workbench.model.js';

// Reading intent determines the entry, independently of fact-array ordering.
export const executionControlObjectDefaults=Object.freeze({
  'execution.client':'control.submission',
  'resident-execution':'control.request-protocol',
  'execution.server':'control.rpc-dispatch',
  'execution.preparation':'control.preparation',
  'worker.runtime.agent-session':'control.agent-service',
  'worker.runtime.chat-completion':'control.chat-service',
});
const objectModes={
  'execution.client':[
    ['control.submission','提交与回执'],['control.request-protocol','观察与确认'],
  ],
  'resident-execution':[
    ['control.request-protocol','观察与确认'],['control.submission','提交与回执'],
    ['control.rpc-dispatch','请求认证'],['execution.lifecycle','完整执行链'],
  ],
};
export const EXECUTION_CONTROL_OBJECT_STATE='execution-control-object-view.v1';
const fail=reason=>{throw new Error(`execution_control_entry:${reason}`);};
const clone=value=>structuredClone(value);

export function executionControlObjectPlan(subject,content){
  content=clone(content);
  const objectId=typeof subject==='string'?subject:subject?.objectId;
  const candidates=objectModes[objectId],object=content?.objects?.find(o=>o.id===objectId);
  if(!candidates||!object||object.child_ids?.length)fail('object_entry');
  if(typeof content.batchKey!=='string'||!content.batchKey)fail('batch');
  if(!Array.isArray(content.mechanisms))fail('authorized_mechanisms_required');
  const byId=new Map();
  for(const mechanism of content.mechanisms){
    if(!mechanism?.id||byId.has(mechanism.id))fail('duplicate_mechanism');
    byId.set(mechanism.id,mechanism);
  }
  const modes=candidates.filter(([id])=>byId.has(id)&&object.mechanism_ids?.includes(id))
    .map(([mechanismId,label])=>({mechanismId,label}));
  const defaultMechanismId=executionControlObjectDefaults[objectId];
  if(!modes.some(m=>m.mechanismId===defaultMechanismId))fail('default_unavailable');
  const getContent=mechanismId=>{
    if(!modes.some(m=>m.mechanismId===mechanismId))fail('mode_unavailable');
    return {mechanism:clone(byId.get(mechanismId)),objects:clone(content.objects),evidence:clone(content.evidence),batchKey:content.batchKey,
      ...(content.modelDescriptor?{modelDescriptor:clone(content.modelDescriptor)}:{})};
  };
  const simulation=mechanismId=>mechanismId==='execution.lifecycle'
    ?createExecutionSimulation(getContent(mechanismId)):createExecutionControlSimulation(getContent(mechanismId));
  // Each visible mode is an authorized, complete domain; do not retain a menu
  // entry whose formal evidence cannot be rendered by its domain component.
  for(const mode of modes)simulation(mode.mechanismId);
  function restore(saved){
    if(saved?.schema!==EXECUTION_CONTROL_OBJECT_STATE||saved.batchKey!==content.batchKey||saved.objectId!==objectId
      ||!modes.some(m=>m.mechanismId===saved.activeMechanismId)||!saved.modeStates||Array.isArray(saved.modeStates))fail('restore_scope');
    if(!Object.hasOwn(saved.modeStates,saved.activeMechanismId))fail('active_state_missing');
    for(const [id,state] of Object.entries(saved.modeStates))simulation(id).restore(state);
    return clone(saved);
  }
  return {objectId,defaultMechanismId,modes:clone(modes),batchKey:content.batchKey,getContent,restore};
}
