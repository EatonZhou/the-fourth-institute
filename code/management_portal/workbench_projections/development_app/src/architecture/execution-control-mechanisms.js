import './execution-control-mechanisms.css';
import {createExecutionControlSimulation,EXECUTION_CONTROL_FIELDS} from './execution-control-mechanisms.model.js';
import {esc,renderExecutionControlDomain} from './execution-control-mechanisms.views.js';

export function createExecutionControlMechanism(root,{subject,content,ports={},signal,initialState=null}={}) {
  root.replaceChildren();
  const sim=createExecutionControlSimulation(content),snapshot=sim.getContent(),m=snapshot.mechanism;
  const id=typeof subject==='string'?subject:subject?.objectId??m.owner_id;
  const object=snapshot.objects.find(o=>o.id===id);
  if(!object||id!==m.owner_id&&!object.mechanism_ids?.includes(m.id))throw new Error('execution_control:subject');
  const doc=root.ownerDocument,el=doc.createElement('section');el.className='ec-workbench';el.dataset.mechanismId=m.id;root.append(el);
  let disposed=false,visible=true,sourcesOpen=false;
  const name=id=>snapshot.objects.find(o=>o.id===id)?.title||id;
  const emit=(port,...args)=>{if(!disposed&&visible&&!signal?.aborted)ports[port]?.(...args);};
  function render(focusKey=null){
    const s=sim.getScene(),step=s.current;
    sourcesOpen=el.querySelector('.ec-sources')?.open??sourcesOpen;
    const focus=focusKey??(el.contains(doc.activeElement)?doc.activeElement?.dataset.focusKey:null),scroll=el.scrollTop;
    el.innerHTML=`<header class="ec-header"><button type="button" data-action="return" data-focus-key="return">返回架构</button><span>本地模拟 · 不发送执行请求</span><h2>${esc(m.title)}</h2><p>${esc(m.question)}</p></header>
      <div class="ec-controls"><label><span style="white-space:nowrap">情境</span><select data-action="case" data-focus-key="case" aria-label="演示情境">${m.cases.map(c=>`<option value="${esc(c.id)}" ${c.id===s.caseId?'selected':''}>${esc(c.title)}</option>`).join('')}</select></label><button type="button" data-action="back" data-focus-key="back" ${s.cursor>1?'':'disabled'}>上一步</button><button type="button" data-action="next" data-focus-key="next" ${s.next?'':'disabled'}>${s.next?`下一步：${esc(s.next.title)}`:'本情境结束'}</button></div>
      <div class="ec-domain" data-domain="${esc(m.id)}">${renderExecutionControlDomain({mechanism:m,scene:s})}</div>
      <div class="ec-reading" role="status"><strong>${s.cursor===1?'情境已就绪':esc(step.title)}</strong><p>${s.next?'可点击图中当前可用的操作，或使用上方“下一步”。':'已到达本情境结局；可回退比较，或切换另一情境。'}</p></div>
      <div class="ec-participants"><span>当前步骤参与对象</span>${[...new Set([step.from,step.to])].map(id=>`<button type="button" data-action="select" data-object-id="${esc(id)}" data-focus-key="object:${esc(id)}">${esc(name(id))}</button>`).join('')}</div>
      <details class="ec-sources" ${sourcesOpen?'open':''}><summary>依据详情 · 条件、状态与固定来源</summary><p>按本情境的正式路径重放；文字guard作为来源前提，when仅解释声明式条件，不执行代码。</p><p>${esc(m.invariants.join('；'))}</p><p>原始结局：${esc(m.cases.find(c=>c.id===s.caseId).outcome)}</p><pre>${esc(JSON.stringify({inputs:s.inputs,guard:step.guard,when:step.when??null,state:s.state,state_changes:step.state_changes},null,2))}</pre><ul>${s.evidence_ids.map(id=>{const e=snapshot.evidence.find(e=>e.id===id);return `<li><button type="button" data-action="source" data-evidence-id="${esc(id)}" data-focus-key="source:${esc(id)}">${esc(e.claim||e.id)}</button><small>${esc(e.version)} · L${e.start_line}–${e.end_line}</small></li>`;}).join('')}</ul></details>`;
    // Reader labels come from the domain diagram; exact source wording stays
    // in the evidence detail rather than surfacing method/field names as prose.
    const labelFor=id=>[...el.querySelectorAll('[data-step]')].find(b=>b.dataset.step===id)?.textContent;
    if(s.cursor>1)el.querySelector('.ec-reading strong').textContent=labelFor(step.id)||step.title;
    if(s.next)el.querySelector('[data-action=next]').textContent=`下一步：${labelFor(s.next.id)||s.next.title}`;
    el.scrollTop=scroll;
    if(focus){let target=[...el.querySelectorAll('[data-focus-key]')].find(x=>x.dataset.focusKey===focus);if(target?.disabled)target=el.querySelector('[data-action=next]:not(:disabled)')||el.querySelector('[data-action=case]');target?.focus({preventScroll:true});}
  }
  function change(){render();emit('change',sim.getScene());}
  function click(event){if(!visible||disposed)return;const b=event.target.closest('[data-action]');if(!b||!el.contains(b)||b.disabled)return;
    const a=b.dataset.action;
    if(a==='next'&&sim.getScene().next){sim.advance(sim.getScene().next.id);change();}
    if(a==='step'){sim.advance(b.dataset.step);change();}
    if(a==='back'){sim.back();change();}
    if(a==='select')emit('select',{kind:snapshot.objects.find(o=>o.id===b.dataset.objectId)?.kind,objectId:b.dataset.objectId});
    if(a==='source')emit('source',b.dataset.evidenceId);
    if(a==='return')emit('return',getViewState());
  }
  function choose(event){if(!visible||disposed||event.target.dataset.action!=='case')return;sim.selectCase(event.target.value);sourcesOpen=false;el.querySelector('.ec-sources')?.removeAttribute('open');change();}
  function getViewState(){return {...sim.getViewState(),scrollTop:el.scrollTop,sourcesOpen:el.querySelector('.ec-sources')?.open??sourcesOpen,focusKey:el.contains(doc.activeElement)?doc.activeElement?.dataset.focusKey??null:null};}
  function restoreViewState(saved){if(disposed)throw new Error('execution_control:disposed');try{sim.restore(saved);sourcesOpen=!!saved.sourcesOpen;el.replaceChildren();render(saved.focusKey);el.scrollTop=Number.isFinite(saved.scrollTop)?Math.max(0,saved.scrollTop):0;}catch(e){destroy();throw e;}}
  function destroy(){if(disposed)return;disposed=true;el.removeEventListener('click',click);el.removeEventListener('change',choose);signal?.removeEventListener('abort',destroy);el.remove();}
  el.addEventListener('click',click);el.addEventListener('change',choose);signal?.addEventListener('abort',destroy,{once:true});
  if(signal?.aborted){destroy();return {show(){},hide(){},destroy,getScene:()=>null,getViewState:()=>null,restoreViewState};}
  if(initialState)restoreViewState(initialState);else render();
  return {show(){if(!disposed){visible=true;el.hidden=false;}},hide(){visible=false;el.hidden=true;},destroy,getViewState,restoreViewState,getScene:()=>disposed?null:sim.getScene()};
}
// Exact mechanism registration prevents claiming the other eight control domains.
export const executionControlFactories=Object.freeze(Object.fromEntries(Object.keys(EXECUTION_CONTROL_FIELDS).map(id=>[id,(root,options)=>{
  if(options?.content?.mechanism?.id!==id){root.replaceChildren();throw new Error('execution_control:factory_identity');}
  return createExecutionControlMechanism(root,options);
}])));
