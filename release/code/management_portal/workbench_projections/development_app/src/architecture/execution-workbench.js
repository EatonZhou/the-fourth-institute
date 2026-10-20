import './execution-workbench.css';
import {createExecutionSimulation} from './execution-workbench.model.js';

const escape = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
const labels = {
  new: '尚未入队', queued: '排队中', claimed: '已领取', running: '运行中', succeeded: '成功终态', cancelled: '取消终态', failed: '失败终态',
  not_called: '尚未调用', accepted: '输入已接受', completed: '已保存完成恢复点', interrupted: '已执行中断步骤',
  empty: '完成收尾未观察', captured: '内容收尾已完成', unknown: '未知', updated: '已更新',
  none: '尚无完成恢复点', provider_completed: '完成恢复点已持久化', waiting: '等待结果', applied: '已应用并确认',
};
const fieldLabels = {execution_state: 'Scheduler', provider_phase: 'Provider', content_phase: '内容', binding_updated: '会话绑定',
  claim_valid: '当前资格有效', claim_generation: '资格代次', cancel_requested: '取消请求', business_phase: 'Lineage', rejected_old_write: '旧写入被拒绝',
  checkpoint_phase: '持久恢复点', provider_call_count: '原生输入提交次数', result_recovered: '原结果已恢复',
  interrupt_requested: '已尝试中断', binding_observation: '绑定观察', content_observation: '内容观察'};
const valueText = value => typeof value === 'boolean' ? value ? '是' : '否' : labels[value] || String(value);

// Reader-facing wording for this domain's owner-authored steps. The immutable
// titles, observations, guards and transitions remain available in source detail.
const stepCopy = {
  facade: ['提交一次有稳定身份的执行', '即使提交响应不确定，也用原来的身份重试，避免产生第二次执行。'],
  submit: ['核对请求并交给调度器', '调度器先检查原始请求摘要，识别重复提交。'],
  'request-prepare': ['准备账号、能力与会话路由', '先确定本次调用需要的条件；此时还没有把执行写入队列。'],
  enqueue: ['写入队列并通知工作者', '排队记录保存成功后，才唤醒等待领取任务的工作者。'],
  'cancel-queued': ['在队列中取消', '执行已经取消，模型没有收到输入。'],
  claim: ['领取本次执行资格', '工作者取得处理资格，但还没有开始调用模型。'],
  start: ['确认执行开始', '调度器把执行从已领取改为运行中；模型调用在后续步骤发生。'],
  runtime: ['交给运行时编排', '工作者把执行交给运行时；保存恢复点与控制执行仍受当前处理资格约束。'],
  prepare: ['准备原有会话', '根据稳定的对应关系，找到这次执行要使用的模型会话。'],
  service: ['选择持续会话服务', '运行时选择会话服务，再由会话服务选择模型提供方。'],
  harness: ['准备工具与上下文', '按本次可用能力，准备将要发送的调用内容。'],
  adapter: ['转换为模型的原生操作', '适配器把统一调用转换为这个模型提供方的会话操作。'],
  invoke: ['模型接受输入', '记录原生会话与轮次，并保存恢复点。'],
  'provider-done': ['先保存模型完成结果', '持久保存输出、用量和原生会话身份。模型完成了，业务仍有收尾工作。'],
  capture: ['完成输出收尾', '把完成内容、事件与用量关联到这次执行；此前可能已经存有流式内容。'],
  binding: ['更新会话对应关系', '后续请求可以通过稳定的对应关系继续使用这个会话。'],
  finish: ['由调度器接受最终结果', '执行结果、用量与处理资格的释放，在同一个事务中完成。'],
  apply: ['业务侧应用结果并确认', '把结果归入原来的业务输入轮次，再确认已经应用。'],
  'cancel-active': ['提出停止请求', '当前只记录了停止请求，还不能据此认定模型已经停止。'],
  interrupt: ['尝试中断模型', '已经尝试发送中断，但模型是否真正停止，仍需后续观察。'],
  'finish-cancel': ['以取消结束这次执行', '晚到的成功不能覆盖取消。内容可能已被采集，清理过程也可能保留新的会话对应关系。'],
  'lease-lost': ['失联后收回旧资格', '同一次执行重新排队；模型中的原轮次可能仍在运行。'],
  'stale-write': ['拒绝旧工作者写入', '旧资格已经失效，迟到的写入不能覆盖新持有者。'],
  reclaim: ['新工作者接回原执行', '继续原来的模型轮次，不再次发送已经被接受的输入。'],
  'completed-fault': ['结果已保存，收尾前发生故障', '完整结果留在恢复点中。完成输出还没有收尾，但此前可能已有流式内容。'],
  'completed-old-write': ['隔离旧资格的迟到结果', '拒绝旧工作者的写入，保护已经保存的结果与新的处理资格。'],
  'completed-claim': ['领取原执行的收尾任务', '新工作者取得新的处理资格，继续处理同一次执行。'],
  'completed-start': ['开始恢复收尾', '继续原来的业务执行，不新建一次执行。'],
  'completed-runtime': ['运行时接回持久结果', '准备会话后，进入已有完成结果的恢复分支。'],
  'completed-session': ['恢复原会话上下文', '准备会话对应关系，保留原来的上下文，不再次调用模型。'],
  'recover-result': ['找回已经保存的原结果', '恢复原来的输出、用量和会话身份，接着完成收尾。模型仍只收到一次输入。'],
};
const outcomes = {
  normal: ['调度器已接受成功结果，业务侧也已应用并确认。'],
  cancel_queued: ['执行在排队时取消，模型没有收到输入。'],
  cancel_running: ['调度器已接受取消结果，并已尝试中断模型。', '内容和会话对应关系可能已经改变，取消状态不能回答它们是否写入。'],
  recover_accepted: ['新工作者恢复了原轮次，没有再次发送已被接受的输入。', '旧资格的迟到写入已被拒绝，同一次执行继续完成。'],
  recover_completed: ['沿用已保存的原结果，模型输入仍只提交了一次。', '新工作者完成内容、会话对应关系和业务确认；旧资格的迟到写入已被拒绝。'],
};

export function createExecutionWorkbench(root, {subject, content, ports = {}, signal, initialState = null} = {}) {
  // Clear any previous protected view even when the new input is rejected.
  root.replaceChildren();
  if (signal?.aborted) throw new Error('execution_workbench:aborted');
  const simulation = createExecutionSimulation(content);
  const data = simulation.getContent(), mechanism = data.mechanism;
  const objects = new Map(data.objects.map(item => [item.id, item]));
  const evidence = new Map(data.evidence.map(item => [item.id, item]));
  const modelDescriptor = data.modelDescriptor;
  if (modelDescriptor !== undefined && (!modelDescriptor || typeof modelDescriptor.address !== 'string'
    || !/^[a-f0-9]{64}$/.test(modelDescriptor.sha256 || '') || !Number.isSafeInteger(modelDescriptor.size)
    || modelDescriptor.size < 0)) throw new Error('execution_workbench:model_descriptor_invalid');
  const subjectId = typeof subject === 'string' ? subject : subject?.objectId || subject?.id || mechanism.owner_id;
  const owner = objects.get(subjectId);
  if (!owner || (subjectId !== mechanism.owner_id && !owner.mechanism_ids?.includes(mechanism.id))) {
    throw new Error('execution_workbench:subject_unavailable');
  }
  if (initialState) simulation.restore(initialState);
  const doc = root.ownerDocument;
  const surface = doc.createElement('section');
  surface.className = 'exec-workbench'; surface.setAttribute('aria-label', 'Execution 生命周期模拟工作台');
  root.replaceChildren(surface);
  let hidden = false, destroyed = false, failed = false, sourcesOpen = false, notice = '';
  const active = () => !destroyed && !hidden && !failed && !signal?.aborted;
  const name = id => objects.get(id)?.title || id;
  const invoke = (port, ...args) => { if (active()) return ports[port]?.(...args); };
  const button = (action, text, disabled = false, extra = '') => `<button type="button" data-exec-action="${action}" ${disabled ? 'disabled' : ''} ${extra}>${escape(text)}</button>`;
  const objectButton = id => button('object', name(id), false, `data-object-id="${escape(id)}"`);
  const sourceButton = id => {
    const source = evidence.get(id);
    return button('source', `${source.claim} · L${source.start_line}–${source.end_line}`, false,
      `data-evidence-id="${escape(id)}" title="${escape(`${source.frozen.address}#L${source.start_line}-L${source.end_line}`)}"`);
  };

  function milestone(id, label, scene, extra = false) {
    const passed = scene.prefix.includes(id) || extra, current = scene.step_id === id;
    return `<li class="${passed ? 'passed' : ''} ${current ? 'current' : ''}" data-milestone="${id}"><span class="exec-dot" aria-hidden="true">${passed ? '✓' : '·'}</span><span>${escape(label)}</span><span class="exec-sr">${passed ? '已到达' : '未到达'}</span></li>`;
  }
  function deltaText(change) {
    const transition = `${valueText(change.before)} → ${valueText(change.after)}`;
    return change.writer_id ? `${transition} · ${name(change.writer_id)} 写入${change.state_scope === 'observation' ? '观察值' : '权威状态'}` : transition;
  }
  function track(key, title, state, milestones, scene, description) {
    const relatedFields = key === 'content_phase' ? ['binding_updated', 'binding_observation', 'content_observation']
      : key === 'provider_phase' ? ['checkpoint_phase', 'result_recovered', 'interrupt_requested'] : [];
    const changed = scene.changes.some(change => change.field === key || relatedFields.includes(change.field));
    return `<section class="exec-track ${changed ? 'changed' : ''}" data-track="${key}"><div class="exec-track-label"><b>${title}</b><output>${escape(valueText(state))}</output></div><div><ol class="exec-milestones">${milestones}</ol><p>${escape(description)}</p></div></section>`;
  }
  function tracks(scene) {
    const s = scene.state, cancelled = s.execution_state === 'cancelled';
    const recovered = s.result_recovered === true;
    const providerDescription = s.interrupt_requested
      ? '已尝试发送中断；Provider 是否停止未知，执行终态由 Scheduler 接受。'
      : recovered ? '沿用已保存的原结果，继续完成收尾；模型不会再次收到这份输入。'
        : s.checkpoint_phase === 'provider_completed' ? '结果已持久化，可在新资格下恢复；完成输出、绑定和业务确认仍分别推进。'
          : '接受或返回模型结果，均不会自动写入业务终态。';
    const bindingObservation = s.binding_observation ?? (s.binding_updated ? 'updated' : 'unknown');
    const contentDescription = `绑定观察：${valueText(bindingObservation)}。${s.content_phase === 'unknown'
      ? '取消不证明内容未写入，也不证明绑定未更新。'
      : s.content_phase === 'empty' ? '完成输出收尾尚未观察到；此前可能已有流式内容。'
        : '已完成输出收尾；终态与业务应用各有独立步骤。'}`;
    return `<div class="exec-tracks" aria-label="四个职责分别记录进度">
      ${track('provider_phase', 'Provider / 恢复点', s.provider_phase,
        milestone('invoke', '接受输入', scene) + milestone('provider-done', '完成恢复点', scene)
        + (mechanism.cases.find(item => item.id === scene.case_id).path.includes('recover-result') ? milestone('recover-result', '恢复原结果', scene) : ''), scene,
        providerDescription)}
      ${track('content_phase', '内容 / 会话绑定', s.content_phase,
        milestone('capture', 'AgentData 内容', scene) + milestone('binding', 'Session 绑定', scene), scene,
        contentDescription)}
      ${track('execution_state', 'Scheduler 权威状态', s.execution_state,
        milestone('enqueue', '入队', scene) + milestone('claim', '领取', scene) + milestone('start', '开始', scene)
        + milestone(cancelled ? scene.step_id === 'cancel-queued' ? 'cancel-queued' : 'finish-cancel' : 'finish', cancelled ? '取消终态' : '提交终态', scene), scene,
        s.cancel_requested ? '取消请求已记录；不得用晚到成功覆盖取消。' : '领取资格、开始执行与提交终态分别发生；已领取过不代表当前资格仍有效。')}
      ${track('business_phase', 'Lineage 业务结果', s.business_phase,
        milestone('apply', '应用结果并确认', scene), scene,
        s.business_phase === 'applied' ? '关联原执行与业务正文后，结果归入原轮次。' : '模型、内容和调度器的进度不会自动代替业务确认。')}
    </div>`;
  }
  function sourceDetail(scene, scenario) {
    const current = scene.current, next = scene.next;
    return `<details class="exec-sources" ${sourcesOpen ? 'open' : ''}><summary>依据详情 · 来源、状态与条件</summary>
      <p>${escape(mechanism.state_model)}</p>
      <div class="exec-model-proof">${modelDescriptor
        ? `<b>固定模型 SHA-256</b><code>${escape(modelDescriptor.sha256)}</code><span>${escape(modelDescriptor.address)}</span>`
        : '<p>父层未传模型描述符；批次隔离仍按父层提供的固定身份执行。</p>'}</div>
      <p>模拟只沿正式情境的步骤路径推进，逐步应用 state_changes；回退从初始值重算。文本 guard 是正式情境的前提，若存在声明式 when，则额外校验该条件。</p>
      <dl class="exec-step-facts">${current ? `<dt>原文步骤</dt><dd>${escape(current.title)}</dd><dt>原文观察</dt><dd>${escape(current.observable)}</dd><dt>触发</dt><dd>${escape(current.trigger)}</dd><dt>guard</dt><dd>${escape(current.guard)}</dd>` : `<dt>进入条件</dt><dd>${escape(mechanism.entry_conditions.join('；'))}</dd>`}
        ${next ? `<dt>下一步 guard</dt><dd>${escape(next.guard)}</dd>` : `<dt>原文结局</dt><dd>${escape(scenario.outcome)}</dd>`}</dl>
      <ul class="exec-deltas" aria-label="本步状态变化">${scene.changes.length ? scene.changes.map(change => `<li><b>${escape(fieldLabels[change.field] || change.field)}</b> ${escape(deltaText(change))}</li>`).join('') : '<li>本步没有修改演示状态字段。</li>'}</ul>
      <div class="exec-source-buttons">${scene.evidence_ids.map(sourceButton).join('')}</div>
      <ul>${mechanism.invariants.map(value => `<li>${escape(value)}</li>`).join('')}</ul>
      <dl class="exec-field-meanings">${mechanism.state_fields.map(field => `<dt>${escape(fieldLabels[field.id] || field.id)}</dt><dd>${escape(field.meaning || field.description || '')}${field.authority_id ? ` · ${escape(name(field.authority_id))} · ${field.state_scope === 'authoritative' ? '权威状态' : '观察值'}` : ''}</dd>`).join('')}</dl></details>`;
  }
  function render(focusKey = null) {
    if (destroyed || failed) return;
    const scene = simulation.getScene(), s = scene.state;
    const scenario = mechanism.cases.find(item => item.id === scene.case_id);
    const current = scene.current, next = scene.next;
    const currentCopy = current ? stepCopy[current.id] || [current.title, current.observable] : null;
    const nextTitle = next ? (stepCopy[next.id]?.[0] || next.title) : '';
    const scrollTop = surface.scrollTop;
    surface.innerHTML = `<header class="exec-heading"><div><span class="exec-tag">本地模拟 · Execution</span><h2>一次执行，四处分别完成</h2><p>模型已经返回，为什么执行还没结束？故障恢复时，怎样避免重复提交输入？</p></div>${button('return', '返回架构')}</header>
      <div class="exec-controls"><label>选择情境<select data-exec-action="case" aria-label="Execution 演示情境">${mechanism.cases.map(item => `<option value="${escape(item.id)}" ${scene.case_id === item.id ? 'selected' : ''}>${escape(item.title)}</option>`).join('')}</select></label>${button('reset', '重置当前情境', scene.step_count === 0)}</div>
      <div class="exec-context"><div><b>${escape(name(subjectId))}</b><span> · 同一次执行 · 已推进 ${scene.step_count}/${scene.total_steps} 步</span></div>${button('running', '建立运行前置情境', !simulation.canEstablishRunning())}<p>${simulation.canEstablishRunning() ? '从该情境起点重放到执行开始：资格已领取，随后交给运行时处理。' : '本情境在队列中结束，没有运行前置状态。可选择“正常完成”等运行情境。'}</p></div>
      <div class="exec-claim ${s.rejected_old_write ? 'fenced' : ''}" aria-label="执行资格与原生输入"><div><small>Scheduler 当前资格</small><b>${s.claim_generation ? `第 ${s.claim_generation} 代 · ${s.claim_valid ? '有效' : '已失效 / 释放'}` : '尚未领取'}</b></div><div><small>模型输入提交</small><b>${scene.explanation.provider_submissions} 次</b></div><div><small>旧 Worker 写入</small><b>${s.rejected_old_write ? '已拒绝 · 资格已变化' : '本情境尚未出现旧写入'}</b></div></div>
      ${tracks(scene)}
      <section class="exec-exchange" aria-label="刚发生的交互"><div class="exec-exchange-title"><span>${current ? '刚完成的步骤' : '准备开始'}</span><h3>${escape(currentCopy?.[0] || scenario.title)}</h3></div>${current ? `<div class="exec-endpoints">${objectButton(current.from)}<span class="exec-arrow" aria-label="传递至">${current.from === current.to ? '↻' : '→'}</span>${objectButton(current.to)}</div><p class="exec-observable">${escape(currentCopy[1])}</p>` : '<p>可从提交开始，也可通过上方入口重放有依据的运行前置步骤。</p>'}</section>
      <div class="exec-advance">${button('back', '退回一步', !scene.step_count)}${button('next', next ? `下一步：${nextTitle}` : '本情境已结束', !next, 'class="exec-primary"')}</div>
      <div class="exec-next" role="status" aria-live="polite">${(notice ? [notice] : next ? ['逐步观察谁接手这次执行，以及哪些进度发生了变化。'] : outcomes[scene.case_id] || [scene.outcome]).map(text => `<p>${escape(text)}</p>`).join('')}</div>
      ${sourceDetail(scene, scenario)}`;
    surface.scrollTop = scrollTop;
    if (focusKey) {
      const target = [...surface.querySelectorAll('[data-exec-action]')].find(el => keyFor(el) === focusKey);
      if (target && !target.disabled) target.focus({preventScroll: true});
      else surface.querySelector('[data-exec-action="back"]:not(:disabled)')?.focus({preventScroll: true});
    }
  }
  function keyFor(el) { return [el.dataset.execAction, el.dataset.objectId || el.dataset.evidenceId || ''].join(':'); }
  function changed(focusKey) { render(focusKey); invoke('change', getScene()); }
  function click(event) {
    const target = event.target.closest?.('[data-exec-action]');
    if (!active() || !target || !surface.contains(target) || target.disabled || target.tagName === 'SELECT') return;
    const action = target.dataset.execAction, focusKey = keyFor(target);
    notice = '';
    if (action === 'return') { invoke('return', getViewState()); return; }
    if (action === 'source') { if (evidence.has(target.dataset.evidenceId)) invoke('source', target.dataset.evidenceId); return; }
    if (action === 'object') {
      const id = target.dataset.objectId;
      if (objects.has(id)) invoke('select', id);
      return;
    }
    if (action === 'back') simulation.back();
    else if (action === 'next') simulation.advance();
    else if (action === 'reset') simulation.reset();
    else if (action === 'running') {
      const scene = simulation.establishRunning();
      notice = `已按正式情境重放 ${scene.step_count} 步，执行已经开始；下一步交给运行时，模型尚未接收输入。`;
    } else return;
    changed(focusKey);
  }
  function change(event) {
    if (!active() || event.target.dataset.execAction !== 'case') return;
    simulation.selectCase(event.target.value); notice = ''; changed('case:');
  }
  function toggle(event) { if (surface.contains(event.target) && event.target.matches?.('.exec-sources')) sourcesOpen = event.target.open; }
  function getScene() { return destroyed || failed ? null : {...simulation.getScene(), object_id: subjectId}; }
  function getViewState() {
    if (destroyed || failed) return null;
    const focused = surface.contains(doc.activeElement) ? doc.activeElement : null;
    return {...simulation.getViewState(), scrollTop: surface.scrollTop, sourcesOpen,
      focusKey: focused?.dataset?.execAction ? keyFor(focused) : null};
  }
  function restoreViewState(saved) {
    if (destroyed || signal?.aborted) return false;
    try { simulation.restore(saved); }
    catch (error) { failed = true; surface.replaceChildren(); throw error; }
    sourcesOpen = !!saved.sourcesOpen; notice = ''; render(saved.focusKey || null);
    surface.scrollTop = Number.isFinite(saved.scrollTop) ? Math.max(0, saved.scrollTop) : 0;
    return true;
  }
  function destroy() {
    if (destroyed) return;
    destroyed = true; hidden = true;
    surface.removeEventListener('click', click); surface.removeEventListener('change', change);
    surface.removeEventListener('toggle', toggle, true); signal?.removeEventListener('abort', destroy);
    surface.remove();
  }
  surface.addEventListener('click', click); surface.addEventListener('change', change);
  surface.addEventListener('toggle', toggle, true); signal?.addEventListener('abort', destroy, {once: true});
  render();
  if (initialState) restoreViewState(initialState);
  return {show() { if (!destroyed && !failed) { hidden = false; surface.hidden = false; } },
    hide() { hidden = true; surface.hidden = true; }, destroy, getViewState, restoreViewState, getScene};
}
