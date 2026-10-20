import './execution-control-mechanisms.entry.css';
import {executionControlFactories} from './execution-control-mechanisms.js';
import {createExecutionWorkbench} from './execution-workbench.js';
import {executionControlObjectPlan,EXECUTION_CONTROL_OBJECT_STATE} from './execution-control-mechanisms.entry-model.js';
export {executionControlObjectDefaults} from './execution-control-mechanisms.entry-model.js';

export function createExecutionControlObjectWorkbench(root,{subject,content,ports={},signal,initialState=null}={}){
  root.replaceChildren();
  const plan=executionControlObjectPlan(subject,content);
  let saved=initialState?plan.restore(initialState):null;
  let modeStates=saved?.modeStates||{},activeId=saved?.activeMechanismId||plan.defaultMechanismId;
  let child=null,disposed=false,visible=true;
  const doc=root.ownerDocument,surface=doc.createElement('section'),bar=doc.createElement('nav'),mount=doc.createElement('div');
  surface.className='ec-object-workbench';surface.dataset.objectId=plan.objectId;
  bar.className='ec-object-modes';bar.setAttribute('aria-label','当前对象的机制');
  const select=doc.createElement('select');select.setAttribute('aria-label','当前对象的机制');
  for(const mode of plan.modes){const option=doc.createElement('option');option.value=mode.mechanismId;option.textContent=mode.label;select.append(option);}
  const label=doc.createElement('label');label.append('阅读内容 ',select);bar.append(label);bar.hidden=plan.modes.length<2;
  mount.className='ec-object-content';surface.append(bar,mount);root.append(surface);
  const live=()=>!disposed&&visible&&!signal?.aborted;
  const emit=(name,...args)=>{if(live())ports[name]?.(...args);};
  const snapshot=()=>{
    if(disposed)return null;
    if(child)modeStates[activeId]=child.getViewState();
    return structuredClone({schema:EXECUTION_CONTROL_OBJECT_STATE,objectId:plan.objectId,batchKey:plan.batchKey,
      activeMechanismId:activeId,modeStates,modeSelectorFocused:doc.activeElement===select});
  };
  function destroy(){
    if(disposed)return;disposed=true;child?.destroy();child=null;
    select.removeEventListener('change',choose);signal?.removeEventListener('abort',destroy);surface.remove();modeStates={};
  }
  function activate(id,state=null){
    child?.destroy();child=null;mount.replaceChildren();activeId=id;select.value=id;
    const normalizedPorts={...ports,
      select:value=>emit('select',typeof value==='string'?{kind:content.objects.find(o=>o.id===value)?.kind,objectId:value}:value),
      navigate:value=>emit('navigate',value),source:value=>emit('source',value),
      change:scene=>emit('change',scene),return:()=>emit('return',snapshot()),
    };
    const factory=id==='execution.lifecycle'?createExecutionWorkbench:executionControlFactories[id];
    child=factory(mount,{subject:{objectId:plan.objectId},content:plan.getContent(id),ports:normalizedPorts,signal,initialState:state});
    if(!visible)child.hide();
  }
  function choose(){
    if(!live())return;
    try{modeStates[activeId]=child.getViewState();activate(select.value,modeStates[select.value]||null);select.focus({preventScroll:true});emit('change',child.getScene());}
    catch(error){destroy();throw error;}
  }
  function restoreViewState(value){
    if(disposed)throw new Error('execution_control_entry:disposed');
    try{const restored=plan.restore(value);modeStates=restored.modeStates;activate(restored.activeMechanismId,modeStates[restored.activeMechanismId]);if(restored.modeSelectorFocused)select.focus({preventScroll:true});}
    catch(error){destroy();throw error;}
  }
  select.addEventListener('change',choose);signal?.addEventListener('abort',destroy,{once:true});
  if(signal?.aborted)destroy();
  else try{activate(activeId,modeStates[activeId]||null);if(saved?.modeSelectorFocused)select.focus({preventScroll:true});}
  catch(error){destroy();throw error;}
  return {show(){if(!disposed){visible=true;surface.hidden=false;child.show();}},hide(){visible=false;surface.hidden=true;child?.hide();},
    destroy,getViewState:snapshot,restoreViewState,getScene:()=>disposed?null:child?.getScene()};
}

// Nonleaf Runtime and Execution deliberately have no object-entry factory:
// their composition graph remains the double-click destination.
export const executionControlObjectFactories=Object.freeze(Object.fromEntries(
  ['execution.client','resident-execution'].map(objectId=>[objectId,(root,options)=>{
    if((typeof options?.subject==='string'?options.subject:options?.subject?.objectId)!==objectId){root.replaceChildren();throw new Error('execution_control_entry:factory_identity');}
    return createExecutionControlObjectWorkbench(root,options);
  }]),
));
