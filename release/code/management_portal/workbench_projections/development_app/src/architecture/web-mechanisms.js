import './web-mechanisms.css';
import {createWebSimulation, WEB_ADAPTERS} from './web-mechanisms.model.js';
import {WEB_VIEWS, escape} from './web-mechanisms.views.js';
import {getWebObjectModes, resolveWebObjectEntry} from './web-mechanisms.entries.js';
import {CURSOR_CASE_LABELS, cursorEndLabel} from './web-mechanisms.cursors.js';
export {WEB_OBJECT_ENTRIES} from './web-mechanisms.entries.js';

export const supportsWebWorkbench = mechanism => WEB_ADAPTERS.includes(mechanism?.adapter);

export function createWebWorkbench(root, {subject, content, ports = {}, signal, initialState} = {}) {
  root.replaceChildren();
  if (signal?.aborted) throw new Error('web_workbench:aborted');
  const simulation = createWebSimulation(content);
  const data = simulation.getContent(), m = data.mechanism;
  const objects = new Map(data.objects.map(o => [o.id, o]));
  const sources = new Map(data.evidence.map(e => [e.id, e]));
  const objectId = typeof subject === 'string' ? subject : subject?.objectId || subject?.id || m.owner_id;
  const owner = objects.get(objectId);
  if (!owner || (objectId !== m.owner_id && !owner.mechanism_ids?.includes(m.id))) throw new Error('web_workbench:subject_unavailable');
  const surface = root.ownerDocument.createElement('section');
  surface.className = 'web-workbench'; surface.setAttribute('aria-label', m.title);
  let destroyed = false, hidden = false, sourceOpen = false;
  const active = () => !destroyed && !hidden && !signal?.aborted;
  const call = (name, arg) => { if (active()) return ports[name]?.(arg); };
  const button = (action, label, extra = '') => `<button type="button" data-web-action="${action}" ${extra}>${escape(label)}</button>`;
  const key = m.id.slice(6);
  const modes = getWebObjectModes(objectId, data);
  function render() {
    if (destroyed) return;
    const scene = simulation.getScene();
    const endLabel = key === 'resident-cursors' ? cursorEndLabel(scene) : '已到结局';
    const oldFocus = surface.contains(root.ownerDocument.activeElement) ? root.ownerDocument.activeElement?.dataset.webFocus : null;
    surface.innerHTML = `<header class="web-wb-header">${button('return','返回架构','data-web-focus="return"')}<span>本地演示</span><h2>${escape(m.title)}</h2>${key==='resident-cursors' && m.question===m.title?'':`<p>${escape(m.question)}</p>`}</header>
      ${modes.length > 1 ? `<div class="web-wb-controls"><label>阅读模式<select data-web-action="mode" data-web-focus="mode">${modes.map(mode => `<option value="${escape(mode.mechanismId)}" ${mode.mechanismId===m.id?'selected':''}>${escape(mode.title)}</option>`).join('')}</select></label></div>` : ''}
      <div class="web-wb-controls"><label>选择情境<select data-web-action="case" data-web-focus="case">${m.cases.map(c => `<option value="${escape(c.id)}" ${scene.caseId===c.id?'selected':''}>${escape(key==='resident-cursors' && c.title===c.id ? CURSOR_CASE_LABELS[c.id] || c.title : c.title)}</option>`).join('')}</select></label>${button('reset','重新开始','data-web-focus="reset"')}</div>
      <div class="web-domain" data-web-domain="${key}">${WEB_VIEWS[key](scene,m)}</div>
      <div class="web-wb-observation" aria-live="polite"><strong>${escape(scene.step?.title || '开始前')}</strong>${scene.step?'':'<p>选择情境后，按动作观察谁收到什么，以及哪些记录发生变化。</p>'}</div>
      <div class="web-wb-controls web-wb-step-controls">${button('previous','上一步',`data-web-focus="previous" ${scene.position?'':'disabled'}`)}${button('next',scene.next?.title || endLabel,`data-web-focus="next" ${scene.complete?'disabled':''}`)}<span>${scene.position} / ${scene.total}</span></div>
      <footer>${button('sources',sourceOpen?'收起依据':'查看本步依据',`data-web-focus="sources" aria-expanded="${sourceOpen}"`)}<div class="web-wb-participants">${m.participants.map(p=>button('object',`${objects.get(p.object_id).title} · ${p.role}`,`data-object-id="${escape(p.object_id)}" data-web-focus="object:${escape(p.object_id)}"`)).join('')}</div></footer>
      ${sourceOpen?sourceDetails(scene):''}`;
    if (oldFocus) {
      const focusTarget = [...surface.querySelectorAll('[data-web-focus]')].find(el=>el.dataset.webFocus===oldFocus && !el.disabled);
      (focusTarget || surface.querySelector('[data-web-action="previous"]:not(:disabled)'))?.focus({preventScroll:true});
    }
  }
  function sourceDetails(scene) {
    const ids = scene.step?.evidence_ids || m.evidence_ids;
    return `<aside class="web-wb-sources" aria-label="固定依据"><h3>本步依据</h3>${ids.map(id=>{
      const e=sources.get(id); const lines = Number.isInteger(e.start_line) && Number.isInteger(e.end_line) ? ` · L${e.start_line}–${e.end_line}` : '';
      return button('source',`${e.claim || e.id}${lines}`,`data-evidence-id="${escape(id)}" data-web-focus="source:${escape(id)}"`);
    }).join('')}<details><summary>模拟规则与当前记录</summary><pre>${escape(JSON.stringify({batchKey:data.batchKey,mechanismId:m.id,step:scene.step,state:scene.state,outcome:scene.outcome},null,2))}</pre></details></aside>`;
  }
  function onClick(event) {
    if (!active()) return;
    const target = event.target.closest('[data-web-action]');
    if (!target || !surface.contains(target) || target.disabled) return;
    const action = target.dataset.webAction;
    if (action === 'return') return call('return');
    if (action === 'source') return call('source', target.dataset.evidenceId);
    if (action === 'object') return call('navigate', {kind:'composition',objectId:target.dataset.objectId,mechanismId:null});
    if (action === 'case') return;
    if (action === 'sources') sourceOpen = !sourceOpen;
    else if (action === 'next') simulation.advance();
    else if (action === 'previous') simulation.previous();
    else if (action === 'reset') simulation.reset();
    render(); call('change', simulation.getScene());
  }
  function onChange(event) {
    if (!active()) return;
    if (event.target.dataset.webAction === 'mode') {
      const target = modes.find(mode => mode.mechanismId === event.target.value);
      event.target.value = m.id; // The parent owns remount, permission checks and return state.
      if (target && target.mechanismId !== m.id) call('navigate', {...target.route});
      return;
    }
    if (event.target.dataset.webAction!=='case') return;
    simulation.choose(event.target.value); render(); call('change', simulation.getScene());
  }
  function restore(snapshot) {
    if (snapshot?.objectId !== undefined && snapshot.objectId !== objectId) throw new Error('web_workbench:invalid_restore_subject');
    simulation.restore(snapshot);
    sourceOpen = snapshot.sourcesOpen === true;
    render();
    surface.scrollTop = Number.isFinite(snapshot.scrollTop) ? Math.max(0,snapshot.scrollTop) : 0;
    if (snapshot.focus) [...surface.querySelectorAll('[data-web-focus]')].find(el=>el.dataset.webFocus===snapshot.focus)?.focus({preventScroll:true});
  }
  function destroy() {
    if (destroyed) return;
    destroyed = true;
    surface.removeEventListener('click',onClick); surface.removeEventListener('change',onChange);
    signal?.removeEventListener('abort',destroy);
    surface.replaceChildren(); surface.remove();
  }
  render(); root.append(surface);
  surface.addEventListener('click',onClick); surface.addEventListener('change',onChange);
  signal?.addEventListener('abort',destroy,{once:true});
  if (initialState) { try { restore(initialState); } catch(error) { destroy(); throw error; } }
  return {
    show() { if (!destroyed && !signal?.aborted) { hidden=false;surface.hidden=false;surface.inert=false; } },
    hide() { hidden=true;surface.hidden=true;surface.inert=true; },
    destroy,
    getViewState() { return {...simulation.getViewState(),objectId,sourcesOpen:sourceOpen,scrollTop:surface.scrollTop,focus:surface.contains(root.ownerDocument.activeElement)?root.ownerDocument.activeElement.dataset.webFocus:null}; },
    restoreViewState(snapshot) { if (!destroyed) restore(snapshot); },
    getScene() { return destroyed ? null : simulation.getScene(); },
  };
}

export const webWorkbenchFactories = Object.freeze(Object.fromEntries(WEB_ADAPTERS.map(adapter=>[adapter,createWebWorkbench])));

export function resolveWebWorkbenchEntry(objectId, content, viewState) {
  const entry = resolveWebObjectEntry(objectId, content, viewState);
  return entry ? {...entry, create: entry.route.kind === 'composition' ? null : createWebWorkbench} : null;
}
