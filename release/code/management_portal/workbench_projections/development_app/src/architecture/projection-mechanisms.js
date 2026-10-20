import './projection-mechanisms.css';
import {createProjectionSimulation, PROJECTION_ADAPTERS} from './projection-mechanisms.model.js';
import {PROJECTION_VIEWS, escape as e} from './projection-mechanisms.views.js';
import {projectionCaseLabel} from './projection-mechanisms.host-views.js';

export const supportsProjectionWorkbench = mechanism => Object.hasOwn(PROJECTION_ADAPTERS, mechanism?.adapter || '');
function referencedMechanisms(subject, objects) {
  const id = typeof subject === 'string' ? subject : subject?.objectId || subject?.id;
  const matches = Array.isArray(objects) ? objects.filter(object => object.id === id) : [];
  if (!id || matches.length !== 1 || !Array.isArray(matches[0].mechanism_ids)) throw new Error('projection_mechanism:subject_mismatch');
  return new Set(matches[0].mechanism_ids);
}
function createSingleProjectionWorkbench(root, {subject, content, ports = {}, signal, initialState} = {}) {
  if (signal?.aborted) throw new Error('projection_mechanism:aborted');
  const model = createProjectionSimulation(content), data = model.getContent(), m = data.mechanism;
  const objects = new Map(data.objects.map(o => [o.id, o]));
  const sources = new Map(data.evidence.map(o => [o.id, o]));
  if (!referencedMechanisms(subject, data.objects).has(m.id)) throw new Error('projection_mechanism:subject_mismatch');
  const surface = root.ownerDocument.createElement('section');
  surface.className = 'projection-workbench'; surface.setAttribute('aria-label', m.title);
  let destroyed = false, hidden = false, showSources = false, openDetails = new Set();
  const live = () => !destroyed && !hidden && !signal?.aborted;
  const call = (name, arg) => { if (live()) return ports[name]?.(arg); };
  const button = (action, label, extra = '') => `<button type="button" data-projection-action="${action}" data-projection-focus="${action}" ${extra}>${e(label)}</button>`;
  function focusKey() { return surface.contains(root.ownerDocument.activeElement) ? root.ownerDocument.activeElement.dataset.projectionFocus : null; }
  function restoreFocus(key) {
    const target = [...surface.querySelectorAll('[data-projection-focus]')].find(el => el.dataset.projectionFocus === key && !el.disabled);
    (target || (key ? surface.querySelector('[data-projection-action="previous"]:not(:disabled)') : null))?.focus({preventScroll: true});
  }
  function captureDetails() {
    for (const node of surface.querySelectorAll('[data-projection-detail]')) {
      if (node.open) openDetails.add(node.dataset.projectionDetail);
      else openDetails.delete(node.dataset.projectionDetail);
    }
  }
  function render(preserveDetails = true) {
    if (destroyed) return;
    if (preserveDetails) captureDetails();
    const s = model.getScene(), focus = focusKey();
    surface.innerHTML = `<header class="projection-heading">${button('return', '返回架构')}<span class="projection-local">本地演示</span><h2>${e(m.title)}</h2><p>${e(m.question)}</p></header>
      <div class="projection-controls"><label>比较情境<select data-projection-action="case" data-projection-focus="case">${m.cases.map(c => `<option value="${e(c.id)}" ${c.id === s.caseId ? 'selected' : ''}>${e(m.id.startsWith('projection.mechanism.') ? projectionCaseLabel(c) : c.title)}</option>`).join('')}</select></label>${button('reset', '重新开始')}</div>
      ${PROJECTION_VIEWS[m.adapter](s, m)}
      <section class="projection-observation" aria-live="polite"><h3>${e(s.step?.title || '开始前')}</h3><p>${e(s.outcome || s.step?.observable || '点击下面的下一步，观察本次情境如何改变。')}</p></section>
      <div class="projection-controls projection-playback">${button('previous', '上一步', s.position ? '' : 'disabled')}${button('next', s.next?.title || '本情境已结束', s.complete ? 'disabled' : '')}<span>${s.position} / ${s.total}</span></div>
      <footer class="projection-footer">${button('sources', showSources ? '收起本步依据' : '查看本步依据', `aria-expanded="${showSources}"`)}<div class="projection-participants">${m.participants.map(p => `<button type="button" data-projection-action="navigate" data-object-id="${e(p.object_id)}" data-projection-focus="object:${e(p.object_id)}">${e(objects.get(p.object_id).title)} · ${e(p.role)}</button>`).join('')}</div></footer>
      ${showSources ? `<aside class="projection-sources"><h3>固定批次依据</h3>${(s.step?.evidence_ids || m.evidence_ids).map(sourceId => { const source = sources.get(sourceId); return `<button type="button" data-projection-action="source" data-source-id="${e(sourceId)}" data-projection-focus="source:${e(sourceId)}">${e(source.claim)} · L${e(source.start_line)}–${e(source.end_line)}</button>`; }).join('')}<details data-projection-detail="state"><summary>本地状态与批次</summary><pre>${e(JSON.stringify({batchKey: data.batchKey, mechanismId: m.id, inputs: s.inputs, state: s.state}, null, 2))}</pre></details></aside>` : ''}`;
    for (const node of surface.querySelectorAll('[data-projection-detail]')) node.open = openDetails.has(node.dataset.projectionDetail);
    restoreFocus(focus);
  }
  function click(event) {
    if (!live()) return;
    const target = event.target.closest('[data-projection-action]');
    if (!target || !surface.contains(target) || target.disabled) return;
    const action = target.dataset.projectionAction;
    if (action === 'return') return call('return');
    if (action === 'source') return call('source', target.dataset.sourceId);
    if (action === 'navigate') return call('navigate', {kind: 'composition', objectId: target.dataset.objectId, mechanismId: null});
    if (action === 'case') return;
    if (action === 'sources') showSources = !showSources;
    else if (action === 'next') model.next();
    else if (action === 'previous') model.previous();
    else if (action === 'reset') model.reset();
    else if (action === 'alternative') { model.branch(target.dataset.caseId); model.next(); }
    else return;
    render(); call('change', model.getScene());
  }
  function change(event) {
    if (!live() || event.target.dataset.projectionAction !== 'case') return;
    model.choose(event.target.value); render(); call('change', model.getScene());
  }
  function restore(saved) {
    model.restore(saved); showSources = saved.showSources === true;
    openDetails = new Set((Array.isArray(saved.openDetails) ? saved.openDetails : []).filter(key => ['confirmation', 'state'].includes(key)));
    render(false);
    surface.scrollTop = Number.isFinite(saved.scrollTop) ? Math.max(0, saved.scrollTop) : 0;
    if (live()) restoreFocus(saved.focus);
  }
  function destroy() {
    if (destroyed) return;
    destroyed = true; surface.removeEventListener('click', click); surface.removeEventListener('change', change);
    signal?.removeEventListener('abort', destroy); surface.replaceChildren(); surface.remove();
  }
  root.replaceChildren(surface); surface.addEventListener('click', click); surface.addEventListener('change', change);
  signal?.addEventListener('abort', destroy, {once: true});
  try { render(); if (initialState) restore(initialState); } catch (error) { destroy(); throw error; }
  return {
    show() { if (!destroyed && !signal?.aborted) { hidden = false; surface.hidden = false; surface.inert = false; } },
    hide() { hidden = true; surface.hidden = true; surface.inert = true; }, destroy,
    getViewState() { captureDetails(); return {...model.getViewState(), showSources, openDetails: [...openDetails].sort(), scrollTop: surface.scrollTop, focus: focusKey()}; },
    restoreViewState(saved) { if (!destroyed) restore(saved); },
    getScene() { return destroyed ? null : model.getScene(); },
  };
}
export function createProjectionWorkbench(root, options = {}) {
  const {content,signal,ports = {}}=options, modes=content?.modeMechanisms;
  if (modes === undefined) return createSingleProjectionWorkbench(root,options);
  const referenced=referencedMechanisms(options.subject,content?.objects);
  if (!Array.isArray(modes) || !modes.length || modes.some(m=>!m || !referenced.has(m.id))) throw new Error('projection_mechanism:invalid_modes');
  const definitions=new Map(modes.map(m=>[m.id,m]));
  if (definitions.size!==modes.length || !definitions.has(content.mechanism.id)) throw new Error('projection_mechanism:invalid_modes');
  const validators=new Map(modes.map(mechanism=>[mechanism.id,createProjectionSimulation({...content,mechanism})]));
  let activeId=content.mechanism.id, stored={}, view=null, hidden=false, destroyed=false;
  const shell=root.ownerDocument.createElement('section'),bar=root.ownerDocument.createElement('nav'),body=root.ownerDocument.createElement('div');
  shell.className='projection-mode-shell';bar.className='projection-mode-bar';bar.setAttribute('aria-label','同一协议的工作面模式');body.className='projection-mode-body';
  shell.append(bar,body);
  const live=()=>!destroyed && !hidden && !signal?.aborted;
  const safePorts=Object.fromEntries(['select','navigate','source','change','return'].map(name=>[name,arg=>{if(live())return ports[name]?.(arg)}]));
  function verify(saved) {
    if (!saved || saved.batchKey!==content.batchKey || saved.version!==1) throw new Error('projection_mechanism:restore_mismatch');
    const active=saved.kind==='projection-modes' ? saved.activeMechanismId : saved.mechanismId;
    const views=saved.kind==='projection-modes' ? saved.views : {[active]:saved};
    if (!definitions.has(active) || !views || typeof views!=='object' || Array.isArray(views)) throw new Error('projection_mechanism:restore_mismatch');
    for (const [id,state] of Object.entries(views)) {
      if (!validators.has(id)) throw new Error('projection_mechanism:restore_mismatch');
      validators.get(id).restore(state);
    }
    return {active,views:structuredClone(views)};
  }
  function mount(id) {
    view?.destroy();activeId=id;
    view=createSingleProjectionWorkbench(body,{...options,content:{...content,mechanism:definitions.get(id)},ports:safePorts,initialState:stored[id]});
    bar.replaceChildren(...modes.map(m=>{
      const button=root.ownerDocument.createElement('button');button.type='button';button.dataset.projectionMode=m.id;button.textContent=m.title;
      button.setAttribute('aria-pressed',String(m.id===activeId));return button;
    }));
    if(hidden)view.hide();
  }
  function changeMode(event) {
    if(!live())return;
    const button=event.target.closest('[data-projection-mode]');if(!button || !bar.contains(button))return;
    const id=button.dataset.projectionMode;if(id===activeId)return;
    stored[activeId]=view.getViewState();mount(id);
    [...bar.children].find(node=>node.dataset.projectionMode===id)?.focus();safePorts.change(view.getScene());
  }
  function destroy(){if(destroyed)return;destroyed=true;view?.destroy();bar.removeEventListener('click',changeMode);signal?.removeEventListener('abort',destroy);shell.remove();}
  function restore(saved){const valid=verify(saved);stored=valid.views;mount(valid.active);}
  if(signal?.aborted)throw new Error('projection_mechanism:aborted');
  if(options.initialState){const valid=verify(options.initialState);activeId=valid.active;stored=valid.views;}
  root.replaceChildren(shell);bar.addEventListener('click',changeMode);signal?.addEventListener('abort',destroy,{once:true});
  try{mount(activeId);}catch(error){destroy();throw error;}
  return {
    show(){if(!destroyed && !signal?.aborted){hidden=false;shell.hidden=false;shell.inert=false;view.show();}},
    hide(){if(destroyed)return;stored[activeId]=view.getViewState();hidden=true;shell.hidden=true;shell.inert=true;view.hide();},destroy,
    getViewState(){if(!hidden && !destroyed)stored[activeId]=view.getViewState();return {version:1,kind:'projection-modes',batchKey:content.batchKey,activeMechanismId:activeId,views:structuredClone(stored)};},
    restoreViewState(saved){if(!destroyed)restore(saved);},getScene(){return destroyed ? null : view.getScene();},
  };
}
export const projectionWorkbenchFactories = Object.freeze(Object.fromEntries(Object.keys(PROJECTION_ADAPTERS).map(adapter => [adapter, createProjectionWorkbench])));
