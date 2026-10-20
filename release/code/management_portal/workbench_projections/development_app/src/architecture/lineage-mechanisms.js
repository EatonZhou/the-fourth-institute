import './lineage-mechanisms.css';
import './lineage-mechanisms.family.css';
import './lineage-mechanisms.output.css';
import './lineage-mechanisms.domains.css';
import {lineageDomainViews} from './lineage-mechanisms.views.js';
import {createLineageSimulation, lineageSupportedMechanisms} from './lineage-mechanisms.model.js';
import {createFamilyViews} from './lineage-mechanisms.family.js';
import {renderOutput} from './lineage-mechanisms.output.js';
import {getLineageObjectModes, resolveLineageObjectEntry} from './lineage-mechanisms.entries.js';
export {lineageSupportedMechanisms, supportsLineageMechanism} from './lineage-mechanisms.model.js';
export {LINEAGE_OBJECT_ENTRIES, lineageReadyObjectEntries, getLineageObjectModes, resolveLineageObjectEntry} from './lineage-mechanisms.entries.js';

export const lineageMechanismFactories = Object.freeze(Object.fromEntries(
  lineageSupportedMechanisms.map(id => [id, createLineageMechanismsWorkbench])));

// Parent owns authorization, frozen sources, semantic focus and the return stack.
// This workbench receives one complete authorized batch and performs no I/O.
export function createLineageMechanismsWorkbench(root, {subject, content, ports = {}, signal, initialState} = {}) {
  const simulation = createLineageSimulation(content), safe = simulation.getContent(), m = safe.mechanism;
  const doc = root.ownerDocument;
  const h = (tag, cls, text) => { const n = doc.createElement(tag); if (cls) n.className = cls; if (text != null) n.textContent = text; return n; };
  const family = createFamilyViews(h);
  const surface = h('section', 'lineage-mechanisms');
  surface.setAttribute('aria-label', m.title); surface.dataset.mechanism = m.id;
  const objectById = new Map(safe.objects.map(o => [o.id, o]));
  const evidenceById = new Map(safe.evidence.map(e => [e.id, e]));
  let destroyed = false, hidden = false, session, focusKey = null, expanded = {};
  const active = () => !destroyed && !hidden && !signal?.aborted;
  const invoke = (name, value) => { if (active()) return ports[name]?.(value); };
  function button(text, key, fn, cls = '') {
    const b = h('button', cls, text); b.type = 'button'; b.dataset.key = key;
    b.onclick = () => { if (active() && surface.contains(b)) fn(); }; return b;
  }
  function remember() {
    if (surface.contains(doc.activeElement)) focusKey = doc.activeElement.dataset.key || null;
    for (const detail of surface.querySelectorAll('details[data-detail]')) expanded[detail.dataset.detail] = detail.open;
  }
  function details(name, title) {
    const d = h('details', name); d.dataset.detail = name; d.open = !!expanded[name]; d.append(h('summary', '', title)); return d;
  }
  function change(callback) { if (!active()) return; remember(); callback(); render(); invoke('change', simulation.getScene()); }
  function action(id, label) {
    const step = m.steps.find(s => s.id === id), allowed = simulation.available().some(s => s.id === id), done = session.history.includes(id);
    const b = button((done ? '✓ ' : '') + (label || step.title), `action:${id}`, () => { if (allowed) change(() => simulation.perform(id)); }, 'action');
    b.hidden = !allowed && !done; b.setAttribute('aria-disabled', String(!allowed)); if (done) b.classList.add('done'); b.title = step.guard; return b;
  }
  function actions(...ids) { const row = h('div', 'actions'); row.append(...ids.map(id => action(id))); return row; }
  function renderEffect() {
      const s=session.state, unresolved=s.call_state==='unresolved', restored=s.phase==='restored', committed=s.call_state==='committed', rejected=s.call_state==='rejected', stopped=!s.effect_open||!s.script_matches;
      const surface=h('div','effect-workface');surface.dataset.effectState=s.phase;
      const admission=h('section','effect-admission');admission.append(h('h3','','先找到同一份激活日志'),h('p','',!s.effect_open?'当前 Effect 已暂停；本次不准入指令。':!s.script_matches?'脚本已改变；旧位置的结果不能套用到新脚本。':s.journal_finished?'这次激活已有完成日志；直接读取终态。':s.journal_started?'激活与脚本已核对、日志已持久保存。接着处理当前调用位置。':'开始时先保存激活与脚本；每条指令仍按当前暂停状态准入。'),actions('begin','replay-finished','paused','changed'));
      const ledger=h('section','effect-position-ledger');
      const heading=h('div','effect-ledger-heading');heading.append(h('h3','','按调用位置记账'));
      const count=h('div','effect-call-count');count.append(h('span','','本次新调用'),h('strong','',String(s.business_calls)));count.dataset.newCalls=String(s.business_calls);heading.append(count);ledger.append(heading);
      const table=h('table','effect-call-table');table.append(h('caption','','同一激活 + 同一脚本位置 → 同一个调用键'));
      const head=h('thead'),headrow=h('tr');for(const text of ['脚本位置','保留在日志中的记录','本次处理']){const th=h('th','',text);th.scope='col';headrow.append(th);}head.append(headrow);table.append(head);
      const body=h('tbody'),current=h('tr',unresolved?'position-unresolved':restored?'position-restored':committed?'position-committed':'');current.dataset.position='current';
      const position=h('th','position-label');position.scope='row';position.append(h('span','position-dot',s.journal_finished?'整体':'当前'),h('strong','',s.journal_finished?'完成的激活':'同一个调用位置'));
      if(!s.journal_finished)position.append(h('small','','恢复时仍找到这里'));
      const record=h('td','position-record');
      let status='尚未记录本次调用',detail='先持久保存激活日志。';
      if(s.restored_receipt){status='原位置已有确定结果';detail='历史调用的结果已保存；本次从这里读取。';}
      if(s.journal_started&&!s.restored_receipt){status='激活已保存，调用尚未开始';detail='先记录调用位置，再发出 ISA。';}
      if(s.call_started){status='已记录开始，并发出调用';detail='等待回包确认；已经发出不等于已知提交结果。';}
      if(unresolved){status='未决 · 提交结果未知';detail='原位置继续保留。不能当作未发生，再换一个键调用。';}
      if(committed){status=restored?'已保存的确定结果 · 本次复用':'确定成功 · 结果已保存';detail=restored?'复用原位置回执；本次没有重新发出 ISA。':'当前位置已有确定回执。';}
      if(rejected){status='确定拒绝 · 失败结果已保存';detail='这是已知失败，与提交状态未知不同。';}
      if(s.journal_finished){status=s.phase==='replayed'?'完成日志 · 本次复用':'已有完成日志';detail='复用整次激活终态，不重新执行脚本。';}
      if(stopped){status='本次未准入调用';detail=!s.script_matches?'旧日志保留，不套用到变化的脚本。':'暂停边界阻止新调用。';}
      record.append(h('strong','record-state',status),h('p','',detail));
      if(s.restored_receipt||committed){const receipt=h('div','effect-receipt',s.restored_receipt?'原位置的成功回执':'本位置的成功回执');receipt.dataset.receipt='known';record.append(receipt);}
      else if(unresolved){const receipt=h('div','effect-receipt unresolved-receipt','? 未知不是失败回执');receipt.dataset.receipt='unresolved';record.append(receipt);}
      const operation=h('td','position-operation');operation.append(actions('call','restore','commit','unknown','reject'));
      operation.append(h('p','effect-operation-note',unresolved?'停在这里，等待恢复确认。':restored?'读取原回执；历史上曾调用，本次新增为 0。':committed?'结果已持久保存。':rejected?'把已知失败交回解释器，按脚本错误分支处理。':s.call_started?'这次调用已发出，接下来处理回包。':s.restored_receipt?'核对激活与脚本后，直接读取已有结果。':stopped?'没有发出本次业务调用。':'在这个位置执行一次调用。'));
      current.append(position,record,operation);body.append(current);
      const continuation=h('tr','effect-continuation');continuation.dataset.continuation=unresolved?'blocked':committed?'result-known':rejected?'error':stopped?'not-admitted':s.journal_finished?'finished':'waiting';
      const next=h('th','position-label');next.scope='row';next.append(h('span','continuation-stem','↓'),h('strong','','后续指令入口'));
      const boundary=h('td','continuation-boundary');boundary.colSpan=2;
      const boundaryTitle=unresolved?'停住：当前位置仍未决':committed?'当前结果已确定，可以继续解释脚本':rejected?'进入脚本的错误处理':stopped?'停住：未通过本次准入':s.journal_finished?'无需重新执行后续指令':'等待当前位置的确定结果';
      boundary.append(h('strong','',boundaryTitle),h('p','',unresolved?'保留上方同一个位置；后续指令尚未推进。':committed?'下一条指令仍要重新检查当前暂停状态。':rejected?'不把确定拒绝画成未知提交，也不代替脚本决定分支。':stopped?'不沿用旧激活快照绕过暂停或脚本校验。':s.journal_finished?'已有终态被复用，本次没有新的业务调用。':'先完成上方位置的处理，才有后续的准入判断。'));
      continuation.append(next,boundary);body.append(continuation);table.append(body);ledger.append(table);surface.append(admission,ledger);return surface;
  }
  function render() {
    if (!active()) return;
    session = simulation.getSession(); surface.dataset.phase = session.state.phase;
    const header = h('header'); header.append(h('span','tag','本地机制演示 · simulated'), h('h2','',m.title), h('p','question',m.question));
    const modes = getLineageObjectModes(subject?.objectId || m.owner_id, safe);
    if (modes.length > 1) {
      const modeBar = h('nav','controls'); modeBar.setAttribute('aria-label','查看本对象的其他机制');
      for (const mode of modes) {
        const b = button(mode.title,`mode:${mode.mechanismId}`,() => {
          if (mode.mechanismId !== m.id) invoke('navigate',mode.route);
        });
        b.setAttribute('aria-current',mode.mechanismId === m.id ? 'page' : 'false'); modeBar.append(b);
      }
      header.append(modeBar);
    }
    const controls = h('div','controls'), label = h('label','','选择情境 '), select = h('select');
    select.dataset.key = 'case'; select.setAttribute('aria-label','选择情境');
    for (const c of m.cases) { const o = h('option','',c.title); o.value = c.id; select.append(o); }
    select.value = session.caseId;
    select.onchange = () => { if (surface.contains(select)) change(() => simulation.choose(select.value)); };
    const faces = {
      'mechanism.lineage.effect-journal': {render:renderEffect, note:'每个情境独立重置；“恢复已确定调用”不是对上一个未知结果的确认。'},
      'mechanism.lineage.output-convergence': {render:() => renderOutput({session,h,action,actions}), note:'每个情境独立重置；沿同一条回复观察落盘与工作完成，不表示真实执行或耗时。'},
      'mechanism.lineage.family-admission': {render:() => family.renderFamilyAdmission({session,action}), note:'每个情境独立重置；已批准情境不是对上一个等待请求的真实审批。画中角色与消息标记只用于本地说明。'},
      'mechanism.lineage.family-projection': {render:() => family.renderFamilyProjection({session,action}), note:'每个情境独立重置，切换只改变本地示例。画中角色与消息标记不代表真实请求或新发送。'},
    };
    const face = faces[m.id] || {render:() => lineageDomainViews[m.id]({session,h,action,actions,mechanism:m,available:simulation.available()}),
      note:'每个情境独立重置；所有操作只改变本地机制示意，未调用服务或发送消息。'};
    label.append(select); controls.append(label, button('重置本情境','reset',() => change(() => simulation.reset())), button('返回来路','return',() => invoke('return')),
      h('p','case-boundary',face.note));
    const stage = h('div','stage'); stage.dataset.renderer = m.id.slice('mechanism.lineage.'.length); stage.append(face.render());
    const step = m.steps.find(s => s.id === session.history.at(-1));
    const description = m.id === 'mechanism.lineage.family-admission' || m.id === 'mechanism.lineage.family-projection'
      ? (session.history.length === 1 ? m.question : step.title) : session.history.length === 1 ? m.question : step.observable;
    const outcome = h('p','outcome', description); outcome.setAttribute('role','status');
    const context = details('context','参与者与协作依据');
    for (const p of m.participants) {
      const o = objectById.get(p.object_id), item = h('div','participant');
      item.append(button(o.title,`select:${o.id}`,() => invoke('select',{objectId:o.id})), h('span','',p.role));
      const entry = resolveLineageObjectEntry(o.id, safe);
      if (entry) item.append(button('进入',`navigate:${o.id}`,() => invoke('navigate',entry.route)));
      context.append(item);
    }
    const usedRelations = new Set(m.steps.map(s => s.relation_id).filter(Boolean));
    for (const r of (safe.relations || []).filter(r => usedRelations.has(r.id))) context.append(h('p','',`${objectById.get(r.from).title} → ${objectById.get(r.to).title} · ${r.kind} · ${r.contract?.operation || r.label || ''}`));
    const sources = details('sources','查看规则与来源');
    for (const rule of m.invariants) sources.append(h('p','',rule));
    for (const id of m.evidence_ids) {
      const e = evidenceById.get(id); sources.append(button(`${e.claim} · ${e.start_line}–${e.end_line}`,`source:${id}`,() => invoke('source',id)));
    }
    const raw = details('raw-observation','核对模型原始观测'); raw.append(h('pre','',JSON.stringify({caseId:session.caseId,history:session.history,state:session.state,observable:step.observable},null,2))); sources.append(raw);
    surface.replaceChildren(header,controls,stage,outcome,context,sources);
    if (focusKey) {
      const target = [...surface.querySelectorAll('[data-key]')].find(n => n.dataset.key === focusKey && !n.hidden);
      if (target) target.focus({preventScroll:true}); else { outcome.tabIndex = -1; outcome.focus({preventScroll:true}); }
    }
  }
  const api = {
    show() { if (destroyed || signal?.aborted) return; hidden = false; surface.hidden = false; render(); },
    hide() { if (destroyed) return; remember(); hidden = true; surface.hidden = true; },
    destroy() { if (destroyed) return; destroyed = true; surface.remove(); signal?.removeEventListener('abort',api.destroy); },
    getViewState() { remember(); return {...simulation.getViewState(), expanded:structuredClone(expanded),focusKey}; },
    restoreViewState(saved) {
      if (destroyed || signal?.aborted) return;
      simulation.restore(saved); expanded = Object.fromEntries(['context','sources','raw-observation'].map(k => [k, saved.expanded?.[k] === true]));
      focusKey = typeof saved.focusKey === 'string' ? saved.focusKey : null; render();
    },
    getScene() { return simulation.getScene(); },
  };
  if (initialState) api.restoreViewState(initialState);
  if (signal?.aborted) { api.destroy(); return api; }
  signal?.addEventListener('abort',api.destroy,{once:true}); root.replaceChildren(surface); render(); return api;
}
