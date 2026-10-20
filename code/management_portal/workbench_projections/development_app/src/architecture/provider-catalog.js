import './provider-catalog.css';
import {CATALOG_ID,TUPLE_ID,catalogAdapters,catalogObjectEntries,catalogObjectModes,createProviderCatalogSimulation,getProviderCatalogModes,catalogCaseGroup} from './provider-catalog.model.js';
import {createCatalogViews} from './provider-catalog.views.js';
export {catalogAdapters,catalogObjectEntries,catalogObjectModes,getProviderCatalogModes};

export function createProviderCatalogWorkbench(root,{subject,content,ports={},signal,initialState}={}) {
  const objectId=subject?.objectId,object=content?.objects?.find(o=>o.id===objectId);
  const mechanismId=subject?.mechanismId||catalogObjectEntries[objectId];
  if(!object||content.mechanism?.id!==mechanismId||!object.mechanism_ids?.includes(mechanismId))throw Error('provider_catalog:subject');
  const modes=getProviderCatalogModes(content,objectId),simulations=new Map();
  let simulation=createProviderCatalogSimulation(content);simulations.set(mechanismId,simulation);
  for(const mode of modes.filter(m=>m.local))if(!simulations.has(mode.id))simulations.set(mode.id,createProviderCatalogSimulation({...content,mechanism:content.mechanisms.find(m=>m.id===mode.id)}));
  const doc=root.ownerDocument,listeners=new AbortController();
  const el=(tag,cls,value)=>{const n=doc.createElement(tag);if(cls)n.className=cls;if(value!==undefined)n.textContent=value;return n;};
  const button=(title,action)=>{const n=el('button','',title);n.type='button';n.dataset.catalogAction=action;return n;};
  const host=el('section','provider-catalog');host.setAttribute('aria-label','Provider 目录工作台');root.append(host);
  const views=createCatalogViews(el);let shown=true,destroyed=false,focusAction=null,sourcesOpen=false,savedScroll=0;
  const emit=(name,value)=>{if(shown&&!destroyed&&!signal?.aborted)ports[name]?.(value);};
  function focus(){if(shown&&focusAction)[...host.querySelectorAll('[data-catalog-action]')].find(n=>n.dataset.catalogAction===focusAction&&!n.disabled)?.focus({preventScroll:true});}
  function render(restoreFocus=false){
    if(destroyed)return;
    const scene=simulation.getScene(),m=simulation.mechanism,scroll=host.scrollTop,group=catalogCaseGroup(m.id,scene.case_id);
    const header=el('header','pc-head');header.append(button('返回','return'),el('h2','',m.title),el('span','pc-tag','冻结教学样本'));
    const context=el('div','pc-context');context.append(el('strong','','实时账号目录：unavailable'),el('p','pc-muted','demo.account / demo.route / demo.model 只用于固定实现演示；目录能力不授予访问权限。'));
    const nav=el('nav','pc-modes');nav.setAttribute('aria-label','Provider目录同对象模式');
    for(const mode of modes){const b=button(mode.title,`${mode.local?'mode':'delegate'}:${mode.id}`);b.setAttribute('aria-current',String(mode.id===m.id));nav.append(b);}
    const groups=m.id===CATALOG_ID?[['pages','分页与缓存','pages'],['registration','注册与账号路由','registered_routes']]:[['tuple','设置组合','valid'],['media','媒体派生','media_supported']];
    const sections=el('nav','pc-modes');sections.setAttribute('aria-label','当前机制操作面');
    for(const [id,title,caseId]of groups)if(m.cases.some(c=>c.id===caseId)){const b=button(title,`case:${caseId}`);b.setAttribute('aria-current',String(group===id));sections.append(b);}
    const scenarios=el('div','pc-scenarios'),label=el('label','','冻结情境'),select=el('select');select.dataset.catalogAction='scenario';select.id=`catalog-scenario-${objectId}`;label.htmlFor=select.id;
    for(const c of m.cases.filter(c=>catalogCaseGroup(m.id,c.id)===group)){const option=el('option','',c.title);option.value=c.id;select.append(option);}select.value=scene.case_id;scenarios.append(label,select);
    const actions=el('div','pc-actions'),next=button(scene.next?.title||'本情境完成','next'),back=button('上一步','back');next.disabled=!scene.next;back.disabled=!scene.cursor;actions.append(next,back,button('重置情境','reset'));
    const status=el('div','pc-status',scene.current?.title||'选择冻结情境，再执行下一步');status.setAttribute('role','status');
    const details=el('details','pc-sources');details.open=sourcesOpen;details.append(el('summary','','固定规则、执行变化与来源'));
    for(const invariant of m.invariants)details.append(el('p','pc-muted',invariant));
    const trace=el('ol','pc-trace');for(const step of scene.trace){const row=el('li','',step.title);row.append(el('small','',` · ${step.changes.map(c=>c.field).join(' / ')}`));trace.append(row);}details.append(trace);
    for(const id of scene.evidence_ids){const e=simulation.evidence.get(id);details.append(button(`${e.symbol||e.claim||e.address} · L${e.start_line}–${e.end_line}`,`source:${id}`));}
    const footer=el('footer','pc-footer'),owner=simulation.objects.get(m.owner_id);
    if(owner.parent_id&&simulation.objects.has(owner.parent_id))footer.append(button('查看所属组成图',`composition:${owner.parent_id}`));
    footer.append(button(owner.title,`select:${owner.id}`));
    if(m.id===TUPLE_ID)footer.append(el('span','pc-muted','ISA授权：not_evaluated · 原生turn调用：0'));
    host.replaceChildren(header,context,el('p','',m.question),nav,sections,scenarios,views.render(scene),actions,status,details,footer);host.scrollTop=scroll;if(restoreFocus)focus();
  }
  function getViewState(){return destroyed?null:{schema:'provider-catalog-view.v1',batchKey:content.batchKey,objectId,activeMechanismId:simulation.mechanism.id,
    views:[...simulations].map(([id,sim])=>({id,state:sim.getViewState()})),scrollTop:shown?host.scrollTop:savedScroll,focusAction,sourcesOpen};}
  function restoreViewState(saved){
    if(destroyed||saved?.schema!=='provider-catalog-view.v1'||saved.batchKey!==content.batchKey||saved.objectId!==objectId||!simulations.has(saved.activeMechanismId)||!Array.isArray(saved.views)||saved.views.length!==simulations.size||!Number.isFinite(saved.scrollTop)||saved.scrollTop<0||typeof saved.sourcesOpen!=='boolean')return false;
    const staged=new Map();
    for(const view of saved.views){if(!simulations.has(view.id)||staged.has(view.id))return false;const sim=createProviderCatalogSimulation({...content,mechanism:simulations.get(view.id).mechanism});if(!sim.restoreViewState(view.state))return false;staged.set(view.id,sim);}
    for(const [id,sim]of staged)simulations.set(id,sim);simulation=simulations.get(saved.activeMechanismId);sourcesOpen=saved.sourcesOpen;focusAction=typeof saved.focusAction==='string'?saved.focusAction:null;savedScroll=saved.scrollTop;
    render();host.scrollTop=savedScroll;focus();return true;
  }
  host.addEventListener('click',event=>{
    const b=event.target.closest?.('button[data-catalog-action]');if(!b||!host.contains(b)||b.disabled||!shown||destroyed||signal?.aborted)return;
    const action=b.dataset.catalogAction;focusAction=action;
    if(action==='return')return emit('return',getViewState());
    if(action.startsWith('source:'))return emit('source',action.slice(7));
    if(action.startsWith('select:'))return emit('select',{objectId:action.slice(7)});
    if(action.startsWith('composition:'))return emit('navigate',{kind:'composition',objectId:action.slice(12),mechanismId:null});
    if(action.startsWith('delegate:')){const mode=modes.find(m=>m.id===action.slice(9)&&!m.local);if(mode)emit('navigate',mode.route);return;}
    if(action.startsWith('mode:')){const sim=simulations.get(action.slice(5));if(!sim)return;simulation=sim;}
    else if(action.startsWith('case:')){if(!simulation.chooseCase(action.slice(5)))return;}
    else if(action==='next')simulation.perform(simulation.getScene().next?.id);
    else if(action==='back')simulation.back();else if(action==='reset')simulation.reset();else return;
    render(true);emit('change',simulation.getScene());
  },{signal:listeners.signal});
  host.addEventListener('change',event=>{if(!shown||destroyed||signal?.aborted||event.target.dataset.catalogAction!=='scenario')return;if(!simulation.chooseCase(event.target.value))return;focusAction='scenario';render(true);emit('change',simulation.getScene());},{signal:listeners.signal});
  host.addEventListener('toggle',event=>{if(shown&&event.target.matches?.('.pc-sources'))sourcesOpen=event.target.open;},{capture:true,signal:listeners.signal});
  function destroy(){if(destroyed)return;destroyed=true;shown=false;listeners.abort();host.remove();simulations.clear();signal?.removeEventListener('abort',destroy);}
  render();if(initialState&&!restoreViewState(initialState)){destroy();throw Error('provider_catalog:initial_state');}
  signal?.addEventListener('abort',destroy,{once:true});if(signal?.aborted)destroy();
  return {show(){if(!destroyed){shown=true;host.hidden=false;host.scrollTop=savedScroll;focus();}},hide(){if(!destroyed){savedScroll=host.scrollTop;shown=false;host.hidden=true;}},destroy,getViewState,restoreViewState,getScene:()=>destroyed?null:simulation.getScene()};
}
export const catalogMechanismFactories=Object.freeze(Object.fromEntries(Object.keys(catalogAdapters).map(id=>[id,createProviderCatalogWorkbench])));
export const catalogAdapterFactories=Object.freeze(Object.fromEntries(Object.values(catalogAdapters).map(id=>[id,createProviderCatalogWorkbench])));
