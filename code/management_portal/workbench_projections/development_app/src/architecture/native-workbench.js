import './native-workbench.css';
import {createNativeSimulation, supportsNativeWorkbench, BUDGET_ADAPTER, NATIVE_OPERATIONS, getNativeWorkbenchEntry, createDomainModeSession} from './native-workbench.model.js';
import {renderNativeOperation} from './native-workbench.operations.js';
export {supportsNativeWorkbench, getNativeWorkbenchEntry};

const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
const label = value => ({ready: '已连接', disconnected: '连接中断', in_progress: '运行中', completed: '原生轮次完成',
  unknown: '尚未核实', rejected: '本地拒绝，未发往原生', none: '尚无', interrupted: '该轮次已中断', pending: '等待控制确认',
  applied: '控制请求已返回', unconfirmed: '控制结果未确认', found: '已找到精确轮次', error: '查询失败'}[value] || String(value ?? '尚未建立'));
const caseTitle = scenario => ({normal: '新建 thread 并完成一轮', load_existing: '加载已有 thread', recover_accepted: '已接受后断线，恢复原 turn',
  planned_handover: 'Worker 交接，继续跟随原 turn', ignore_child_turn: '忽略无关子 turn', lookup_failure: '精确轮次查询失败',
  steer: '给当前轮次追加输入', stale_control: '拒绝过期轮次控制', interrupt: '中断确认与轮次终态', interrupt_uncertain: '中断结果未确认',
  foreign_input: 'thread 被另一输入占用', connect_failure_before_accept: '建立连接失败，尚未发送 turn/start'}[scenario.id] || scenario.title);
const budgetCaseTitle = scenario => ({exhausted: '持续无进展，耗尽恢复次数', progress: '新的有效结果，恢复次数归零',
  replayed_item: '重放旧结果，保留已用次数', no_progress_events: '有活动，但没有新的有效结果', new_turn: '换 turn，仍属于同一次执行',
  new_claim: '换 Worker，读取已保存次数', planned_handover: '计划交接，次数保持', one_disconnect: '同一次断线，多次探测只扣一次',
  invalid_checkpoint: '恢复点计数无效，按耗尽处理'}[scenario.id] || scenario.title);
const budgetEvents = {new_current_turn_completed_work: ['有效新结果', '当前 turn 的新 item/completed', '归零已用次数，同时保存'],
  duplicate_completed_item: ['重复结果', '该完成结果已经见过', '保留已用次数'], control_activity: ['控制活动', '心跳、开始或压缩完成', '保留已用次数'],
  failed_or_empty_item: ['无有效工作结果', '失败或空结果', '保留已用次数'], foreign_turn_item: ['其他 turn 的结果', '不属于当前接受轮次', '保留已用次数']};

export function createNativeWorkbench(root, {subject, content, ports = {}, signal, initialState} = {}) {
  const subjectId = subject?.objectId || subject?.id || content.mechanism.owner_id;
  const modes = createDomainModeSession(content, subjectId, getNativeWorkbenchEntry(subjectId), createNativeSimulation);
  let simulation = modes.simulation, mechanism = simulation.mechanism, isBudget = mechanism.adapter === BUDGET_ADAPTER, isOperation = !!NATIVE_OPERATIONS[mechanism.adapter];
  const surface = root.ownerDocument.createElement('section');
  surface.className = `native-workbench${isBudget ? ' native-budget' : ''}${isOperation ? ' native-operations' : ''}`;
  surface.setAttribute('aria-label', isOperation ? mechanism.title : isBudget ? 'Codex 恢复预算工作台' : 'Provider 原生轮次工作台');
  let destroyed = false, visible = true, sourcesOpen = false, scrollTop = 0, focusKey = null;
  let objects = simulation.objects;
  const authority = fieldId => mechanism.state_fields.find(field => field.id === fieldId)?.authority_id;
  let clientId = authority('connection'), providerId = authority('provider_turn_status'), checkpointId = authority('checkpoint_turn_id');
  const objectName = id => objects.get(id)?.title || '';
  const objectButton = id => objects.has(id) ? `<button type="button" data-native-action="object" data-id="${esc(id)}">${esc(objectName(id))}</button>` : '';
  const button = (action, text, disabled = false, extra = '') => `<button type="button" data-native-action="${action}" ${disabled ? 'disabled' : ''} ${extra}>${esc(text)}</button>`;
  const identity = (name, value) => `<div><dt>${esc(name)}</dt><dd>${esc(value || '尚未建立')}</dd></div>`;
  const key = node => node?.dataset?.nativeAction ? `${node.dataset.nativeAction}:${node.dataset.id || ''}` : node?.tagName === 'SUMMARY' ? (node.parentElement.classList.contains('native-sources') ? 'sources' : 'trace') : null;
  function emit(name, value) { if (!destroyed && visible && !signal?.aborted) ports[name]?.(value); }
  function activateMode() {
    simulation = modes.simulation; mechanism = simulation.mechanism; isBudget = mechanism.adapter === BUDGET_ADAPTER; isOperation = !!NATIVE_OPERATIONS[mechanism.adapter];
    objects = simulation.objects; clientId = authority('connection'); providerId = authority('provider_turn_status'); checkpointId = authority('checkpoint_turn_id');
    surface.className = `native-workbench${isBudget ? ' native-budget' : ''}${isOperation ? ' native-operations' : ''}`;
    surface.setAttribute('aria-label', isOperation ? mechanism.title : isBudget ? 'Codex 恢复预算工作台' : 'Provider 原生轮次工作台');
  }
  function actions(scene, side) {
    const step = scene.next;
    if (!step) return '';
    // Endpoints determine where an action belongs; they never imply order.
    const onProvider = step.from === providerId;
    if ((side === 'provider') !== onProvider) return '';
    return `<div class="native-actions">${button('step', step.title, false, `class="native-primary" data-id="${esc(step.id)}"`)}</div>`;
  }
  function budgetMarkup(scene) {
    const state = scene.state, current = scene.current, replenishing = current?.id === 'progress';
    const event = current?.state_changes.some(change => change.field === 'last_event') ? budgetEvents[state.last_event] : null;
    const slots = (count, prefix) => [0, 1, 2].map(i => `<span class="native-budget-slot ${i < count ? 'used' : ''}" aria-label="${prefix}第${i + 1}次${i < count ? '已用' : '可用'}">${i < count ? '已用' : '可用'}<b>${i + 1}</b></span>`).join('');
    return `<header class="native-heading"><div><span class="native-eyebrow">本地模拟 · Codex 恢复预算</span><h2>恢复次数跟随同一次执行</h2><p>首次正常请求不扣次；安排技术恢复前先扣一次并保存。</p></div>${button('return', '返回架构')}</header>
      <div class="native-controls"><label>恢复预算情境<select aria-label="恢复预算情境" data-native-action="case">${mechanism.cases.map(c => `<option value="${esc(c.id)}" ${c.id === scene.case_id ? 'selected' : ''}>${esc(budgetCaseTitle(c))}</option>`).join('')}</select></label>${button('reset', '重新试验', !scene.cursor)}</div>
      <main class="native-budget-boundary" aria-label="同一次执行的恢复预算"><div class="native-budget-title"><strong>同一次 Execution</strong><span>这里只演示已被恢复策略允许的路径</span></div>
        <section class="native-budget-ledger" aria-label="内存计数与持久恢复点">
          <div class="native-budget-row"><div><span class="native-eyebrow">ADAPTER · 当前计数</span><h3>连续无进展已用 <b data-native-value="attempts">${state.attempts}</b> / 3</h3></div><div class="native-budget-slots">${slots(state.attempts, '当前')}</div></div>
          <div class="native-budget-save"><span aria-hidden="true">↓</span>${replenishing ? '有效新结果：清零并保存' : '扣次或归零时保存'}<span aria-hidden="true">↓</span></div>
          <div class="native-budget-row native-budget-checkpoint"><div><span class="native-eyebrow">SCHEDULER · 恢复点</span><h3>已保存 <b data-native-value="checkpoint_attempts">${state.checkpoint_attempts}</b> / 3</h3></div><div class="native-budget-slots">${slots(state.checkpoint_attempts, '恢复点')}</div></div>
        </section>
        <section class="native-budget-identities" aria-label="调度身份与结果去重">
          <div><span>Worker 领取次数</span><strong data-native-value="claim_count">${state.claim_count}</strong><small>重新领取不增加恢复额度</small></div>
          <div><span>当前 turn</span><strong data-native-value="turn_id">${esc(state.turn_id)}</strong><small>换 turn 只清结果 ID 集合</small></div>
          <div><span>本 turn 已见的有效结果</span><strong data-native-value="seen_item_ids">${state.seen_item_ids.length ? state.seen_item_ids.map(esc).join('、') : '尚无'}</strong><small>保留 ID 去重，不保存模型或工具正文</small></div>
        </section>
        <section class="native-budget-event ${replenishing ? 'accepted' : ''}" aria-label="当前事件如何影响预算"><div><span class="native-eyebrow">事件输入</span><strong>${esc(event?.[0] || current?.title || '尚未推进演示')}</strong><p>${esc(event?.[1] || current?.observable || '从所选情境的第一个操作开始。')}</p></div><span class="native-budget-arrow" aria-hidden="true">→</span><div><span class="native-eyebrow">计数结果</span><strong>${esc(state.error ? '拒绝继续技术恢复' : event?.[2] || `${state.attempts} 次已用，${3 - state.attempts} 次可用`)}</strong><p data-native-value="budget-error">${esc(state.error || '仅改变当前演示')}</p></div></section>
        ${scene.case_id === 'one_disconnect' ? `<section class="native-budget-reconnect"><h3>同一次断线观察</h3><div class="native-budget-probes"><span>故障：已扣 ${state.attempts} 次</span><span aria-hidden="true">→</span><strong data-native-value="reconnect_probes">socket 探测 ${state.reconnect_probes} 次</strong><span aria-hidden="true">→</span><span>${state.deferred ? '期限耗尽：保存原 turn，延迟重领' : '等待原连接恢复'}</span></div><p>探测次数不是故障扣次。原 turn <b>${esc(state.turn_id)}</b> 保持，重新领取沿用保存的 ${state.checkpoint_attempts} 次。</p></section>` : ''}
      </main>
      <div class="native-budget-controls">${button('back', '退回上次操作', !scene.cursor)}${button('step', scene.next?.title || '本情境结束', !scene.next, `class="native-primary" data-id="${esc(scene.next?.id || '')}"`)}</div>
      <p class="native-budget-receipt" role="status" aria-live="polite">${esc(current?.observable || mechanism.question)}</p>
      <div class="native-foot"><p>业务重试属于另一条输入；预算本身不决定错误是否允许恢复。</p>${objectButton(mechanism.owner_id)}</div>
      <details class="native-trace"><summary>已执行的预算操作</summary><ol>${scene.trace.map(t => `<li>${esc(t.title)}</li>`).join('')}</ol></details>
      <details class="native-sources" ${sourcesOpen ? 'open' : ''}><summary>本步固定依据</summary><div class="native-source-links">${scene.evidence_ids.map(id => { const e = simulation.evidence.get(id); return button('source', `${e.claim || e.id} · L${e.start_line}–${e.end_line}`, false, `data-id="${esc(id)}"`); }).join('')}</div><ul>${(mechanism.invariants || []).map(text => `<li>${esc(text)}</li>`).join('')}</ul></details>`;
  }
  function render() {
    if (destroyed) return;
    const scene = simulation.getScene(), state = scene.state, scenario = mechanism.cases.find(c => c.id === scene.case_id);
    const connected = state.connection === 'ready', checkpoint = state.checkpoint_turn_id;
    const current = scene.current, next = scene.next;
    surface.innerHTML = isOperation ? renderNativeOperation({scene, mechanism, simulation, button, objectButton, sourcesOpen}) : isBudget ? budgetMarkup(scene) : `<header class="native-heading"><div><span class="native-eyebrow">本地模拟 · Codex App Server</span><h2>断线后，找回被接受的那一轮</h2><p>${esc(mechanism.question)}</p></div>${button('return', '返回架构')}</header>
      <div class="native-controls"><label>原生协议情境<select aria-label="原生协议情境" data-native-action="case">${mechanism.cases.map(c => `<option value="${esc(c.id)}" ${c.id === scene.case_id ? 'selected' : ''}>${esc(caseTitle(c))}</option>`).join('')}</select></label>${button('reset', '重新试验', !scene.cursor)}</div>
      <div class="native-context"><b>${esc(caseTitle(scenario))}</b><small>独立的原生协议试验 · ${esc(objectName(subjectId))} · 操作仅改变当前演示</small></div>
      <div class="native-wire" aria-label="客户端与原生服务的身份和连接">
        <section class="native-peer native-adapter" aria-label="客户端观察与持久恢复点"><div class="native-peer-title"><span class="native-eyebrow">PORTAL · 客户端观察</span>${objectButton(clientId)}</div>
          <dl class="native-identity">${identity('原账号', state.account_id)}${identity('原 thread', state.thread_id)}${identity('此客户端已加载 thread', state.thread_loaded ? '是' : '否')}${identity('已接受的 turn', state.accepted_turn_id)}</dl>
          <div class="native-anchor ${checkpoint ? '' : 'empty'}"><small>受 claim 保护的持久恢复点 · ${esc(objectName(checkpointId))}</small><strong data-native-value="checkpoint">${esc(checkpoint || '尚未保存 turn 身份')}</strong></div>
          <div class="native-count"><span>turn/start 请求</span><b data-native-value="starts">${esc(state.turn_start_count)} 次</b></div>
          <div class="native-observation"><span>客户端已核实 · ${esc(state.observed_turn_id || '尚无轮次')}</span><strong data-native-value="observed">${esc(label(state.observed_turn_status))}</strong><small>thread/resume ${esc(state.thread_resume_count)} 次 · ${state.attached ? '正在跟随' : '未跟随'}</small></div>${actions(scene, 'adapter')}
        </section>
        <div class="native-transport ${connected ? '' : 'broken'}" aria-label="连接状态"><span class="native-link" aria-hidden="true">${connected ? '⇄' : '×'}</span><strong data-native-value="connection">${connected ? '已连接' : '已断开'}</strong></div>
        <section class="native-peer native-provider" aria-label="App Server 原生轮次"><div class="native-peer-title"><span class="native-eyebrow">独立原生进程</span>${objectButton(providerId)}</div>
          <dl class="native-identity">${identity('thread', state.thread_id)}</dl>
          <div class="native-turn ${state.accepted_turn_id ? '' : 'empty'}"><small>案例明确模拟的 Provider 状态</small><b data-native-value="turn">${esc(state.accepted_turn_id || '尚无已接受轮次')}</b><span class="native-state" data-native-value="turn-state">${esc(label(state.provider_turn_status))}</span><p>这里的模拟真值与客户端最后核实的观察分开。</p></div>
          ${actions(scene, 'provider')}
        </section>
      </div>
      ${state.control_target_turn_id ? `<section class="native-control-result" aria-label="客户端控制目标检查"><h3>客户端控制闸门</h3><div class="native-request-line"><span>期望控制目标</span><code>${esc(state.control_target_turn_id)}</code><span>正在跟随的轮次</span><code>${esc(state.accepted_turn_id)}</code></div><strong>${esc(label(state.control_result))}</strong><p>实际原生控制 <b data-native-value="controls">${esc(state.native_control_count)}</b> 次</p>${state.control_error ? `<p>${esc(state.control_error)} · ${esc(state.control_rejection_reason || '请求未确认')}</p>` : ''}${state.interrupt_requested ? '<p>中断请求与轮次终态分别确认；请求返回不证明全部原生子进程已停止。</p>' : ''}</section>` : ''}
      <section class="native-exchange" aria-label="原生协议观察"><h3>${esc(current?.title || '从连接和 thread 开始')}</h3>${current ? `<div class="native-request-line"><span>${esc(objectName(current.from))}</span><span aria-hidden="true">→</span><span>${esc(objectName(current.to))}</span></div>` : ''}<p role="status" aria-live="polite">${esc(current?.observable || mechanism.entry_conditions?.join('；'))}</p>${!connected && state.accepted_turn_id ? '<p>连接中断不会自动停止原 turn；客户端未核实的新结果不会写入左侧观察。</p>' : ''}${state.last_other_turn_id ? `<p>另一个子 turn：${esc(state.last_other_turn_id)}，不替代当前 ${esc(state.accepted_turn_id)} 的终态。</p>` : ''}${state.lookup_result === 'error' ? '<p>查询失败不等于原 turn 不存在，也不允许重新提交输入或跟随列表顶部轮次。</p>' : ''}${state.failure ? `<p>本地异常：${esc(state.failure)}</p>` : ''}${next ? `<p class="native-guard">${esc(`下一操作条件：${next.guard}`)}</p>` : ''}</section>
      <div class="native-foot">${button('back', '退回上次操作', !scene.cursor)}<p>原生返回之后，Runtime 仍有自己的内容与会话收尾；此试验不改写另一份 Execution 演示。</p>${objects.has('worker.runtime') ? button('runtime', '查看 Runtime 组成') : ''}</div>
      <details class="native-trace"><summary>已执行的原生交互</summary><ol>${scene.trace.map(t => `<li>${esc(t.title)}</li>`).join('')}</ol></details>
      <details class="native-sources" ${sourcesOpen ? 'open' : ''}><summary>本步固定依据</summary><div class="native-source-links">${scene.evidence_ids.map(id => {const e = simulation.evidence.get(id); return button('source', `${e.claim || e.symbol || e.id} · L${e.start_line}–${e.end_line}`, false, `data-id="${esc(id)}"`);}).join('')}</div><ul>${(mechanism.invariants || []).map(text => `<li>${esc(text)}</li>`).join('')}</ul></details>`;
    if (modes.options.length > 1) {
      const bar = surface.ownerDocument.createElement('div'); bar.className = 'native-controls';
      bar.innerHTML = `<label>阅读主题<select aria-label="阅读主题" data-native-action="mode">${modes.options.map(mode => `<option value="${esc(mode.id)}" ${mode.id === mechanism.id ? 'selected' : ''}>${esc(mode.title)}</option>`).join('')}</select></label>`;
      surface.querySelector('header').after(bar);
    }
    surface.scrollTop = scrollTop;
    if (visible && focusKey) {
      const target = [...surface.querySelectorAll('[data-native-action],summary')].find(node => key(node) === focusKey && !node.disabled);
      (target || surface.querySelector('[data-native-action="back"]:not(:disabled)') || surface.querySelector('[data-native-action="case"]'))?.focus({preventScroll: true});
    }
  }
  function handle(event) {
    if (!visible || destroyed || signal?.aborted) return;
    const node = event.target.closest('[data-native-action]');
    if (!node || !surface.contains(node) || node.disabled) return;
    const action = node.dataset.nativeAction;
    const selection = action === 'case' || action === 'mode';
    if (selection && event.type !== 'change' || !selection && event.type !== 'click') return;
    if (action === 'mode') {
      const saved = modes.select(node.value, getLocalViewState()); if (!saved) return;
      activateMode(); scrollTop = saved.scrollTop || 0; sourcesOpen = saved.sourcesOpen === true; focusKey = 'mode:';
      render(); emit('change', simulation.getScene()); return;
    }
    scrollTop = surface.scrollTop; focusKey = key(node); sourcesOpen = !!surface.querySelector('.native-sources')?.open;
    if (action === 'return') { emit('return'); return; }
    if (action === 'source') { emit('source', node.dataset.id); return; }
    if (action === 'object') { emit('select', {objectId: node.dataset.id}); return; }
    if (action === 'runtime') { emit('navigate', {kind: 'composition', objectId: 'worker.runtime', mechanismId: null}); return; }
    if (action === 'case') simulation.chooseCase(node.value);
    if (action === 'reset') simulation.reset();
    if (action === 'back') simulation.back();
    if (action === 'step' && !simulation.perform(node.dataset.id)) return;
    render(); emit('change', simulation.getScene());
  }
  function rememberView() {
    if (!visible || destroyed) return;
    scrollTop = surface.scrollTop; focusKey = key(surface.ownerDocument.activeElement);
    sourcesOpen = !!surface.querySelector('.native-sources')?.open;
  }
  function getLocalViewState() { rememberView(); return {simulation: simulation.getViewState(), scrollTop, focusKey, sourcesOpen}; }
  function getViewState() { if (destroyed) return null; const saved = getLocalViewState(); return {...saved, modeState: modes.snapshot(saved)}; }
  function restoreViewState(saved) {
    if (destroyed) return false;
    if (saved?.modeState) { if (!saved.simulation) return false; const restored = modes.restore(saved.modeState, saved.simulation); if (!restored) return false; activateMode(); saved = restored; }
    else if (!simulation.restoreViewState(saved?.simulation)) return false;
    scrollTop = Number.isFinite(saved.scrollTop) ? Math.max(0, saved.scrollTop) : 0;
    focusKey = typeof saved.focusKey === 'string' ? saved.focusKey : null;
    sourcesOpen = saved.sourcesOpen === true; render(); return true;
  }
  function destroy() {
    if (destroyed) return;
    destroyed = true; visible = false;
    surface.removeEventListener('click', handle); surface.removeEventListener('change', handle);
    signal?.removeEventListener('abort', destroy); surface.remove();
  }
  root.append(surface); surface.addEventListener('click', handle); surface.addEventListener('change', handle);
  if (!initialState || !restoreViewState(initialState)) render();
  signal?.addEventListener('abort', destroy, {once: true}); if (signal?.aborted) destroy();
  return {show() {if (!destroyed) {visible = true; surface.hidden = false; surface.scrollTop = scrollTop;}}, hide() {rememberView(); visible = false; surface.hidden = true;}, destroy,
    getViewState, restoreViewState, getScene: () => destroyed ? null : simulation.getScene()};
}
