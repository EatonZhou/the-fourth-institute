import './agent-data-mechanisms.css';
import {AGENT_DATA_ADAPTERS, createAgentDataSimulation} from './agent-data-mechanisms.model.js';
import {AGENT_DATA_VIEWS, escape} from './agent-data-mechanisms.views.js';

export const supportsAgentDataWorkbench = mechanism => AGENT_DATA_ADAPTERS.includes(mechanism?.adapter) && mechanism.adapter === `${mechanism.id}.v1`;
export function createAgentDataWorkbench(root, {subject, content, ports={}, signal, initialState}={}) {
  if (signal?.aborted) throw new Error('agent_data_workbench:aborted');
  const simulation=createAgentDataSimulation(content), data=simulation.getContent(), m=data.mechanism;
  const objects=new Map(data.objects.map(o=>[o.id,o])), sources=new Map(data.evidence.map(e=>[e.id,e]));
  const objectId=typeof subject==='string'?subject:subject?.objectId||subject?.id||m.owner_id;
  if (!objects.has(objectId) || (objectId!==m.owner_id && !objects.get(objectId).mechanism_ids?.includes(m.id))) throw new Error('agent_data_workbench:subject_unavailable');
  const surface=root.ownerDocument.createElement('section');surface.className='agent-data-workbench';surface.setAttribute('aria-label',m.title);
  const key=m.id.slice('agent-data.'.length);
  let destroyed=false,hidden=false,sourcesOpen=false;
  const active=()=>!destroyed&&!hidden&&!signal?.aborted;
  const call=(name,value)=>{if(active())return ports[name]?.(value);};
  const button=(action,label,extra='')=>`<button type="button" data-ad-action="${action}" data-ad-focus="${action}" ${extra}>${escape(label)}</button>`;
  function sourceDetails(scene) {
    const ids=[...new Set([...(scene.step?.evidence_ids||m.evidence_ids),...(key==='completed-content'&&scene.caseId==='fallback'?['es.ev.design-data']:[])])].filter(id=>sources.has(id));
    return `<aside class="ad-sources" aria-label="本步固定依据"><h3>本步依据</h3>${ids.map(id=>{
      const e=sources.get(id),range=Number.isInteger(e.start_line)&&Number.isInteger(e.end_line)?` · L${e.start_line}–${e.end_line}`:'';
      return `<button type="button" data-ad-action="source" data-ad-focus="source:${escape(id)}" data-evidence-id="${escape(id)}">${escape(e.claim||id)}${range}</button>`;
    }).join('')}<details><summary>模拟规则、状态归属与固定批次</summary><pre>${escape(JSON.stringify({batchKey:data.batchKey,mechanism:m.id,entry_conditions:m.entry_conditions,invariants:m.invariants,step:scene.step,state:scene.state,state_fields:m.state_fields},null,2))}</pre></details></aside>`;
  }
  function render() {
    if(destroyed)return;
    const scene=simulation.getScene(),focus=surface.contains(root.ownerDocument.activeElement)?root.ownerDocument.activeElement?.dataset.adFocus:null;
    surface.innerHTML=`<header class="ad-header">${button('return','返回架构')}<span class="ad-local">本地演示</span><h2>${escape(m.title)}</h2><p>${escape(m.question)}</p></header>
      <div class="ad-controls"><label>选择情境<select data-ad-action="case" data-ad-focus="case">${m.cases.map(c=>`<option value="${escape(c.id)}" ${scene.caseId===c.id?'selected':''}>${escape(c.title)}</option>`).join('')}</select></label>${button('reset','重新开始')}</div>
      <div class="ad-domain" data-ad-domain="${key}" role="group" aria-label="${escape(m.title)}的机制图">${AGENT_DATA_VIEWS[key](scene)}</div>
      <div class="ad-step-status" aria-live="polite"><strong>${escape(scene.step?.title||'情境已载入，可以开始观察')}</strong>${scene.state.error?`<span class="ad-error">${escape(scene.state.error)}</span>`:''}</div>
      <div class="ad-controls ad-actions">${button('previous','上一步',scene.position?'':'disabled')}${button('next',scene.next?.title||'已到本案例结局',scene.complete?'disabled':'')}<span>${scene.position} / ${scene.total}</span></div>
      <footer>${button('sources',sourcesOpen?'收起依据':'查看本步依据',`aria-expanded="${sourcesOpen}"`)}<details class="ad-participants"><summary>相关组成</summary>${m.participants.map(p=>`<button type="button" data-ad-action="object" data-ad-focus="object:${escape(p.object_id)}" data-object-id="${escape(p.object_id)}">查看 ${escape(objects.get(p.object_id).title)} 的组成</button>`).join('')}</details></footer>
      ${sourcesOpen?sourceDetails(scene):''}`;
    if(focus){const node=[...surface.querySelectorAll('[data-ad-focus]')].find(e=>e.dataset.adFocus===focus&&!e.disabled);(node||surface.querySelector('[data-ad-action="previous"]:not(:disabled)'))?.focus({preventScroll:true});}
  }
  function onClick(event) {
    if(!active())return;
    const target=event.target.closest('[data-ad-action]');if(!target||!surface.contains(target)||target.disabled)return;
    const action=target.dataset.adAction;
    if(action==='return')return call('return');
    if(action==='source')return call('source',target.dataset.evidenceId);
    if(action==='object')return call('navigate',{kind:'composition',objectId:target.dataset.objectId,mechanismId:null});
    if(action==='case')return;
    if(action==='sources')sourcesOpen=!sourcesOpen;
    else if(action==='next')simulation.advance();
    else if(action==='previous')simulation.previous();
    else if(action==='reset')simulation.reset();
    render();call('change',simulation.getScene());
  }
  function onChange(event){if(active()&&event.target.dataset.adAction==='case'){simulation.choose(event.target.value);render();call('change',simulation.getScene());}}
  function restore(snapshot){simulation.restore(snapshot);sourcesOpen=snapshot.sourcesOpen===true;render();surface.scrollTop=Number.isFinite(snapshot.scrollTop)?Math.max(0,snapshot.scrollTop):0;if(snapshot.focus)[...surface.querySelectorAll('[data-ad-focus]')].find(e=>e.dataset.adFocus===snapshot.focus)?.focus({preventScroll:true});}
  function destroy(){if(destroyed)return;destroyed=true;surface.removeEventListener('click',onClick);surface.removeEventListener('change',onChange);signal?.removeEventListener('abort',destroy);surface.replaceChildren();surface.remove();}
  render();root.replaceChildren(surface);surface.addEventListener('click',onClick);surface.addEventListener('change',onChange);signal?.addEventListener('abort',destroy,{once:true});
  if(initialState){try{restore(initialState);}catch(error){destroy();throw error;}}
  return {show(){if(!destroyed&&!signal?.aborted){hidden=false;surface.hidden=false;surface.inert=false;}},hide(){hidden=true;surface.hidden=true;surface.inert=true;},destroy,
    getViewState(){return {...simulation.getViewState(),sourcesOpen,scrollTop:surface.scrollTop,focus:surface.contains(root.ownerDocument.activeElement)?root.ownerDocument.activeElement?.dataset.adFocus:null};},
    restoreViewState(snapshot){if(!destroyed)restore(snapshot);},getScene(){return destroyed?null:simulation.getScene();}};
}
export const agentDataWorkbenchFactories=Object.freeze(Object.fromEntries(AGENT_DATA_ADAPTERS.map(a=>[a,createAgentDataWorkbench])));
