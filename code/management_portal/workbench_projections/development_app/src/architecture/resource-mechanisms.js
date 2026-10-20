import './resource-mechanisms.css';
import './resource-mechanisms.authorization.css';
import {createResourceSimulation, workNotes} from './resource-mechanisms.model.js';
import {lockView, permissionSnapshotView} from './resource-mechanisms.views.js';
import {renderAuthorizationMechanism} from './resource-mechanisms.authorization.js';
import {renderFileMechanism} from './resource-mechanisms.files.js';
import {renderAccessMechanism} from './resource-mechanisms.access.js';
import {renderPolicyMechanism} from './resource-mechanisms.policy.js';
import {RESOURCE_MECHANISMS, resourceModeLabels} from './resource-mechanisms.entries.js';
export {resourceObjectEntries, resourceObjectModes} from './resource-mechanisms.entries.js';
let instanceCounter=0;
const escape=value=>String(value??'').replace(/[&<>"']/g,x=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[x]));
const graphCaseLabels={explicit_root:'显式根沿父图覆盖',admin_private:'全局 admin 与 private 目标',combined_private:'全局 admin 加显式 viewer',admin_public:'全局 admin 与公开目标',mixed_private:'公开与 private 双父',all_private:'全部父节点均为 private',max_levels:'多个来源取最高等级',inactive_bridge:'失效中间节点阻断',missing_parent:'父节点缺失',cycle:'父图含环',revision_missing:'已有 revision 但凭证行缺失',revision_mismatch:'凭证集合 revision 不一致',empty_revision:'空 revision 的合法空集合',empty_matching_set:'匹配 revision 的空凭证集合'};
const version=value=>({'file:v1':'v1 · 旧完整内容','file:v2':'v2 · 新完整内容',empty:'空文件 · 尚未写完整',absent:'路径不存在',old_complete:'旧完整目标',new_complete:'新完整目标'})[value]||value;
export function createResourceWorkbench(root,{subject,content,ports={},signal,initialState=null}={}) {
 const simulation=createResourceSimulation(content), frozen=simulation.getContent(), m=frozen.mechanism;
 const doc=root.ownerDocument, surface=doc.createElement('section'), prefix=`resource-${++instanceCounter}-`;
 surface.className='resource-workbench';surface.setAttribute('aria-label',m.title);
 let destroyed=false, hidden=false, saved=null, observed=null;
 const invoke=(name,value)=>{if(!destroyed&&!hidden&&!signal?.aborted)ports[name]?.(value);};
 const sourceLabel=id=>{const e=frozen.evidence.find(x=>x.id===id);return e?.symbol?`${e.symbol} · L${e.start_line}–${e.end_line}`:e?.claim||id;};
 const sourceButton=id=>`<button type="button" data-source="${escape(id)}">${escape(sourceLabel(id))}</button>`;
 const alive=()=>!destroyed&&!signal?.aborted;
 function getScene(){return destroyed?null:{...simulation.getScene(),subject,observation:observed?{...observed}:null};}
 function getViewState(){
  if(destroyed)return null;
  return {...simulation.getViewState(),readerCursor:observed?.cursor??null,scrollTop:surface.scrollTop,canvasLeft:surface.querySelector('.causal-wrap')?.scrollLeft||0,
   expanded:[...surface.querySelectorAll('details[open]')].map(x=>x.dataset.detail),
   focusKey:surface.contains(doc.activeElement)?doc.activeElement.dataset.key||null:null};
 }
 function position(state){
  if(!state)return;
  surface.scrollTop=state?.scrollTop||0;
  const canvas=surface.querySelector('.causal-wrap');if(canvas)canvas.scrollLeft=state?.canvasLeft||0;
  for(const detail of surface.querySelectorAll('details'))detail.open=(state?.expanded||[]).includes(detail.dataset.detail);
  if(state?.focusKey) [...surface.querySelectorAll('[data-key]')].find(x=>x.dataset.key===state.focusKey)?.focus({preventScroll:true});
 }
 function readAt(cursor){
  const s=simulation.getFrames()[cursor]?.state;if(!s)return null;
  let text;
  if(m.id==='resources.mechanism.storage.workspace.locks')text=!s.cooperating?'绕锁写者不受该锁约束，无法保证排队':s.b_can_enter?'B 可以进入':s.held?'B 等待同一 lockfile':'B 尚未尝试';
  else if(m.id==='resources.review.mechanism.index-snapshot-publication')text=`${s.read_visible_version}：${s.memory_records.join('、')||'空集合'}`;
  else if(m.id==='resources.mechanism.file-storage')text=version(s.target);
  else if(['resources.mechanism.storage.workspace.atomic-files','resources.review.mechanism.atomic-create-only'].includes(m.id))text=version(s.read_visible_version);
  return text===undefined?null:{cursor,text};
 }
 function render(state){
  if(destroyed)return;
  const scene=simulation.getScene(),frames=simulation.getFrames(),cursor=scene.cursor,ctx={mechanism:m,scene,frames,cursor,observed};
  const diagram=m.id==='resources.mechanism.storage.workspace.locks'?lockView(scene.state,frames,cursor):m.id==='resources.mechanism.resources.permissions'?permissionSnapshotView(scene.state):renderAuthorizationMechanism(ctx)??renderFileMechanism(ctx)??renderAccessMechanism(ctx)??renderPolicyMechanism(ctx);
  if(diagram==null)throw Error('resource_view_unsupported');
  const next=frames[cursor+1]?.step;
  surface.innerHTML=`<header><div><p class="resource-kicker">本地模拟 · 不执行真实读写或授权</p><h2>${escape(m.title)}</h2></div><button type="button" data-return>返回访问处</button></header>
   <p>${escape(m.question)}</p>
   ${frozen.modes.length>1?`<nav class="resource-modes" aria-label="机制模式">${frozen.modes.map(mode=>`<button type="button" data-mode="${escape(mode.id)}" aria-pressed="${mode.id===m.id}">${escape(resourceModeLabels[mode.id]||mode.title)}</button>`).join('')}</nav>`:''}
   <label class="resource-case">观察情境 <select aria-label="观察情境" data-case>${simulation.getCases().map(c=>`<option value="${escape(c.id)}" ${c.id===scene.case_id?'selected':''}>${escape(m.id==='resources.mechanism.resources.permissions.coverage'?(graphCaseLabels[c.id]||c.title):c.title)}</option>`).join('')}</select></label>
   <div class="resource-actions"><button type="button" data-start>重新观察</button><button type="button" data-next ${next?'':'disabled'}>${escape(next?.title||'已到本例结尾')}</button><span>${cursor} / ${frames.length-1} 个逻辑时点</span></div>
   <div class="resource-diagram">${diagram.replaceAll('id="waiting"',`id="${prefix}waiting"`).replaceAll('url(#waiting)',`url(#${prefix}waiting)`)}</div>
   <div class="resource-time"><button type="button" data-prev ${cursor===0?'disabled':''}>上一个时点</button><input aria-label="观察时点" type="range" min="0" max="${frames.length-1}" value="${cursor}" data-time><button type="button" data-end>本例结尾</button></div>
   <p class="resource-observation" aria-live="polite">${escape(simulation.getObservation())}</p>
   <details data-detail="boundaries"><summary>机制边界</summary><ul>${m.invariants.map(x=>`<li>${escape(x)}</li>`).join('')}</ul></details>
   ${workNotes(frozen).map(n=>`<details data-detail="${escape(n.id)}"><summary>${n.resolved?'已解决的规范差异':'相关研究记录'}</summary><p>${escape(n.description)}</p><details data-detail="${escape(n.id)}-history"><summary>当时的观察</summary><p>${escape(n.history)}</p></details><div class="resource-sources">${n.evidence_ids.map(sourceButton).join('')}</div></details>`).join('')}
   <details data-detail="sources"><summary>查看源码与 Design</summary><div class="resource-sources">${m.evidence_ids.map(sourceButton).join('')}</div></details>`;
  [...surface.querySelectorAll('details')].forEach((el,index)=>{el.dataset.detail||=`${m.id}:detail:${index}`;});
  for(const el of surface.querySelectorAll('button,input,select,summary'))el.dataset.key=el.dataset.source||el.dataset.mode||el.dataset.detail||el.getAttribute('aria-label')||el.textContent;
  if(m.id==='resources.mechanism.storage.workspace.locks'&&observed)surface.querySelector('#probe-observation').textContent=observed.text;
  position(state);
 }
 function change(action){const state=getViewState();action();render(state);invoke('change',getScene());}
 function onClick(event){
  if(!alive()||hidden)return;
  const b=event.target.closest('button');if(!b||!surface.contains(b)||b.disabled)return;
  if(b.hasAttribute('data-return'))invoke('return');
  else if(b.dataset.source&&frozen.evidence.some(e=>e.id===b.dataset.source))invoke('source',b.dataset.source);
  else if(b.dataset.mode&&b.dataset.mode!==m.id&&frozen.modes.some(mode=>mode.id===b.dataset.mode)){
   invoke('navigate',{kind:frozen.entryObject.kind==='protocol'?'protocol':'mechanism',objectId:frozen.entryObjectId,mechanismId:b.dataset.mode});
  }
  else if(b.hasAttribute('data-start'))change(()=>{simulation.reset();observed=null;});
  else if(b.hasAttribute('data-end'))change(()=>simulation.seek(simulation.getFrames().length-1));
  else if(b.hasAttribute('data-prev'))change(()=>simulation.seek(Math.max(0,simulation.getScene().cursor-1)));
  else if(b.hasAttribute('data-next')||b.hasAttribute('data-advance'))change(()=>simulation.seek(Math.min(simulation.getScene().cursor+1,simulation.getFrames().length-1)));
  else if(b.hasAttribute('data-seek'))change(()=>simulation.seek(Number(b.dataset.seek)));
  else if(b.dataset.probe)change(()=>{observed=readAt(simulation.getScene().cursor);});
 }
 function onChange(event){if(!alive()||hidden)return;
  if(event.target.hasAttribute('data-case'))change(()=>{simulation.selectCase(event.target.value);observed=null;});
  if(event.target.hasAttribute('data-time'))change(()=>simulation.seek(Number(event.target.value)));
 }
 function restoreViewState(value){if(destroyed)return;simulation.restore(value);observed=Number.isInteger(value.readerCursor)?readAt(value.readerCursor):null;render(value);}
 function destroy(){if(destroyed)return;destroyed=true;surface.removeEventListener('click',onClick);surface.removeEventListener('change',onChange);signal?.removeEventListener('abort',destroy);surface.replaceChildren();surface.remove();saved=null;observed=null;}
 surface.addEventListener('click',onClick);surface.addEventListener('change',onChange);
 if(signal?.aborted)destroy();else{root.replaceChildren(surface);signal?.addEventListener('abort',destroy,{once:true});if(initialState)restoreViewState(initialState);else render();}
 return {show(){if(!destroyed){hidden=false;surface.hidden=false;if(saved)position(saved);}},hide(){if(!destroyed){saved=getViewState();hidden=true;surface.hidden=true;}},destroy,getViewState,restoreViewState,getScene};
}
export const resourceMechanismFactories=Object.freeze(Object.fromEntries(Object.keys(RESOURCE_MECHANISMS).map(id=>[id,createResourceWorkbench])));
