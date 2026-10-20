import './provider-session-mechanisms.css';
import {createProviderSessionSimulation, PROVIDER_SESSION_ADAPTERS, getProviderSessionWorkbenchEntry, getProviderSessionDelegatedModes, supportsProviderSessionMechanism} from './provider-session-mechanisms.model.js';
export {PROVIDER_SESSION_ADAPTERS, getProviderSessionWorkbenchEntry, getProviderSessionDelegatedModes, supportsProviderSessionMechanism};

const words = {valid:'设置与目录一致', unsupported_setting:'包含未声明设置', unknown_image_route:'图片 route 未知', foreign_owner:'绑定属于其他 Agent', missing_compact:'compact 所需绑定缺失', replace:'按预期身份替换', replace_raced:'准备后目标已变化', restore_raced:'替换后又有更新', unaccepted_rebuild_blocked:'已有输入接受回执', complete:'同会话正常收尾', execute:'统一执行', execute_error:'执行抛出异常', interrupt_ack:'中断返回 true', interrupt_unknown_provider:'中断目标 Provider 未知', unknown:'未核实'};
const text = value => typeof value === 'boolean' ? value ? '是' : '否' : words[value] || String(value ?? '');

export function createProviderSessionWorkbench(root, {subject, content, ports = {}, signal, initialState} = {}) {
  const objectId = subject?.objectId;
  const object = content?.objects?.find(o => o.id === objectId);
  const entry = getProviderSessionWorkbenchEntry(objectId);
  const mechanismId = subject?.mechanismId || entry?.defaultMechanismId;
  if (!object || !mechanismId || content.mechanism?.id !== mechanismId || !object.mechanism_ids?.includes(mechanismId)) throw Error('provider_session:subject_mismatch');
  // Parents stay compositions by default; an explicit mechanism route can inspect their declared modes.
  let simulation = createProviderSessionSimulation(content);
  const simulations = new Map([[mechanismId, simulation]]);
  for (const id of entry?.modeMechanismIds || []) {
    if (simulations.has(id) || !object.mechanism_ids.includes(id)) continue;
    const candidates = (content.mechanisms || []).filter(m => m.id === id);
    if (candidates.length !== 1 || !supportsProviderSessionMechanism(candidates[0])) continue;
    try { simulations.set(id, createProviderSessionSimulation({...content, mechanism:candidates[0]})); } catch { /* An invalid mode is not offered. */ }
  }
  const delegatedModes = getProviderSessionDelegatedModes(content, objectId);
  const doc = root.ownerDocument, listeners = new AbortController();
  let shown = true, destroyed = false, focusKey = null, savedScroll = 0, sourcesOpen = false;
  const el = (tag, cls, value) => { const n = doc.createElement(tag); if (cls) n.className = cls; if (value !== undefined) n.textContent = value; return n; };
  const host = el('section', 'provider-session-workbench'); host.setAttribute('aria-label', 'Provider 会话机制'); root.append(host);
  const button = (label, action) => { const n = el('button', '', label); n.type = 'button'; n.dataset.psmAction = action; return n; };
  const emit = (name, value) => { if (shown && !destroyed && !signal?.aborted) ports[name]?.(value); };
  function table(headers, rows) {
    const t = el('table', 'psm-table'), head = el('tr');
    for (const h of headers) { const th = el('th', '', h); th.scope = 'col'; head.append(th); }
    const thead = el('thead'); thead.append(head); const body = el('tbody');
    for (const values of rows) { const row = el('tr'); for (const value of values) row.append(el('td', '', String(value))); body.append(row); }
    t.append(thead, body); return t;
  }
  const seen = (scene, id) => scene.trace.some(s => s.id === id);
  function catalog(scene) {
    const s = scene.state, board = el('div', 'psm-board'); board.dataset.psmView = 'catalog';
    const path = simulation.mechanism.cases.find(c => c.id === scene.case_id).path;
    const settingsChecked = seen(scene, 'validate') || seen(scene, 'reject-setting');
    const imageQueried = seen(scene, 'validate') || seen(scene, 'no-image');
    const settingsCovered = path.some(id => ['validate', 'reject-setting'].includes(id));
    const imageCovered = path.some(id => ['validate', 'no-image'].includes(id));
    board.append(el('h3', '', '请求设置 × Provider 声明'), table(['校验位置', '本案例观察'], [
      ['请求设置与目录', scene.cursor ? text(s.selection) : '等待载入案例'],
      ['设置组合被接受', settingsChecked ? text(s.accepted) : settingsCovered ? '待检查' : '本情境未校验'],
      ['native_image_input', imageQueried ? text(s.native_image_input) : imageCovered ? '待查询' : '本情境未查询'],
      ['异常', s.error || '未观察到异常'],
    ]));
    board.append(el('p', 'psm-muted', '显式设置更新走严格校验；reconcile 的自动修正属于独立入口。'));
    if (seen(scene, 'reject-setting')) board.append(el('p', 'psm-output', '未知设置在原生调用前被拒绝'));
    if (seen(scene, 'no-image')) board.append(el('p', 'psm-output', '未知 route 没有图片能力声明'));
    board.append(el('p', '', '能力声明用于选择可用功能；用户权限由授权边界判断。')); return board;
  }
  function resolve(scene) {
    const s = scene.state, board = el('div', 'psm-board'), path = el('div', 'psm-readpath'); board.dataset.psmView = 'resolve';
    const store = el('div', 'psm-document'); store.append(el('h3', '', 'AgentData · 已有 binding'), el('p', '', scene.cursor ? s.binding === 'valid' ? '会话记录与请求前提一致' : text(s.binding) : '等待载入会话前提'), el('p', 'psm-muted', '核对 Agent、Provider 与路由'));
    const snapshot = el('div', 'psm-document'); snapshot.append(el('h3', '', 'RuntimeSession · 准备快照'), el('p', 'psm-output', s.prepared ? '快照可使用' : s.error ? '准备被拒绝' : '等待核对'));
    snapshot.append(el('p', '', s.error || '读取身份快照'), el('span', 'psm-stamp', '只读 prepare'));
    path.append(store, el('div', 'psm-arrow', '→'), snapshot); board.append(path);
    const meter = el('div', 'psm-meter'); meter.append(el('span', '', `binding 写入：${text(s.binding_written)}`), el('span', '', `原生调用：${s.native_calls}`)); board.append(meter);
    board.append(el('p', '', '快照保存准备时观察的身份。完成时仍须按预期身份条件写入，准备阶段没有排它锁。')); return board;
  }
  function bindings(scene) {
    const s = scene.state, board = el('div', 'psm-board'); board.dataset.psmView = 'bindings';
    board.append(el('h3', '', 'AgentData · 当前稳定映射'), el('p', 'psm-muted', scene.cursor ? text(s.operation) : '等待载入条件操作'));
    const ledger = el('div', 'psm-ledger');
    for (const [ref, name] of [['old','准备时身份'], ['prepared','替换候选'], ['newer','后续写入身份']]) {
      const n = el('div', `psm-ref ${s.current_ref === ref ? 'active' : ''}`); n.append(el('strong', '', ref), el('small', '', name)); ledger.append(n);
    }
    board.append(ledger, el('p', 'psm-output', `current_ref = ${s.current_ref}`));
    const receipt = el('div', 'psm-receipt'); receipt.append(el('strong', '', '替换收据 · previous / current'), el('p', '', s.receipt_saved ? 'old → prepared · 已保存' : '本案例尚未保存收据')); board.append(receipt);
    board.append(table(['条件操作观察', '值'], [['异常', s.error || '未观察到异常'], ['附加上下文次数变化', `+${s.injection_count_delta}`]]));
    if (s.error) board.append(el('p', '', s.current_ref === 'newer' ? '预期身份不符，newer 映射保留。' : '已接受输入的事实阻止空会话重建。'));
    board.append(el('p', 'psm-muted', '普通 bind 保持 ref；stateless 不更新稳定映射。')); return board;
  }
  function adapter(scene) {
    const s = scene.state, board = el('div', 'psm-board'); board.dataset.psmView = 'adapter';
    const execution = el('div', 'psm-lane'), steps = el('div', 'psm-track'); execution.append(el('strong', '', '执行与结果'));
    for (const [name, done] of [['context seed', s.context_seed_prepared], ['Backend.execute', s.provider_calls > 0], ['BackendResult → Runtime', s.result_forwarded]]) steps.append(el('span', `psm-chip ${done ? 'done' : ''}`, name));
    execution.append(steps); board.append(execution);
    const control = el('div', 'psm-lane'), ack = el('div'); control.append(el('strong', '', '中断与观察'));
    ack.append(el('p', '', seen(scene, 'interrupt') || seen(scene, 'unknown') ? `Adapter 返回：${s.control_ack}` : '等待中断案例'), el('p', 'psm-unknown', `Provider 停止状态：${text(s.observed_stopped)}`)); control.append(ack); board.append(control);
    board.append(table(['本次调用观察', '值'], [['Provider 调用次数', s.provider_calls], ['已收到同步结果', text(s.result_received)], ['已向 Runtime 返回结果', text(s.result_forwarded)], ['异常已向上透传', text(s.provider_error_propagated)]]));
    board.append(el('p', '', s.request_kind.startsWith('interrupt_') ? '中断交给 Adapter 的目标包含 route、session 和 turn 身份。' : s.provider_error_propagated ? '调用抛异常，成功结果没有返回。' : '同步返回 BackendResult 后，Runtime 的后续收尾仍由 Runtime 负责。'));
    board.append(el('p', 'psm-muted', '中断回执只表达 Adapter 返回值；停止观察保持原模型的 unknown。')); return board;
  }
  const diagrams = {'provider.catalog-settings':catalog, 'provider.session-resolve':resolve, 'provider.binding-cas':bindings, 'provider.adapter-boundary':adapter};
  function focus() {
    if (!shown || !focusKey) return;
    [...host.querySelectorAll('[data-psm-action]')].find(n => n.dataset.psmAction === focusKey && !n.disabled)?.focus({preventScroll:true});
  }
  function render(restoreFocus = false) {
    if (destroyed) return;
    const scene = simulation.getScene(), m = simulation.mechanism, scroll = host.scrollTop;
    const head = el('header', 'psm-head'); head.append(button('返回', 'return'), el('h2', '', m.title), el('span', 'psm-tag', '固定批次 · 本地模拟'));
    const choices = el('div', 'psm-choices'), label = el('label', '', '演示情境'), select = el('select');
    select.id = `psm-case-${objectId}`; label.htmlFor = select.id; select.dataset.psmAction = 'case';
    for (const c of m.cases) { const opt = el('option', '', c.title); opt.value = c.id; select.append(opt); } select.value = scene.case_id; choices.append(label, select);
    const modes = el('nav', 'psm-nav'); modes.setAttribute('aria-label', '本对象的会话机制');
    const hasModes = simulations.size + delegatedModes.length > 1;
    if (hasModes) for (const [id, sim] of simulations) {
      const b = button(sim.mechanism.title, `mode:${id}`); b.setAttribute('aria-current', String(id === m.id)); modes.append(b);
    }
    for (const mode of delegatedModes) modes.append(button(mode.title, `delegate:${mode.id}`));
    const actions = el('div', 'psm-actions'), next = button(scene.next?.title || '本情境完成', 'next'), back = button('上一步', 'back');
    next.disabled = !scene.next; back.disabled = !scene.cursor; actions.append(next, back, button('重新演示', 'reset'));
    const status = el('div', 'psm-status', scene.current?.title || '载入所选情境，查看每一步的领域变化'); status.setAttribute('role', 'status');
    const details = el('details', 'psm-evidence'); details.open = sourcesOpen; details.append(el('summary', '', '规则、状态变化与固定依据'));
    for (const invariant of m.invariants || []) details.append(el('p', '', invariant));
    const trace = el('ol', 'psm-trace');
    for (const t of scene.trace) {
      const li = el('li', '', t.title); for (const c of t.changes) li.append(el('div', '', `${simulation.fields.get(c.field).meaning}：${text(c.before)} → ${text(c.after)}`)); trace.append(li);
    }
    details.append(trace);
    if (scene.current) details.append(el('p', 'psm-muted', `步骤条件说明：${scene.current.guard}`));
    for (const id of scene.evidence_ids) {
      const e = simulation.evidence.get(id); details.append(button(`${e.symbol || e.claim || e.address} · L${e.start_line}–${e.end_line}`, `source:${id}`));
    }
    const nav = el('footer', 'psm-nav');
    const compositionId = object.child_ids?.length ? object.id : object.parent_id;
    if (compositionId && simulation.objects.has(compositionId)) nav.append(button('查看所属组成图', `composition:${compositionId}`));
    for (const id of new Set(m.participants.map(p => p.object_id))) {
      const o = simulation.objects.get(id); nav.append(button(o.title, `select:${id}`));
    }
    host.replaceChildren(head, el('p', 'psm-question', m.question), ...(hasModes ? [modes] : []), choices, diagrams[m.id](scene), actions, status, details, nav);
    host.scrollTop = scroll; if (restoreFocus) focus();
  }
  function getViewState() {
    if (destroyed) return null;
    return {schema:'provider-session-workbench.v1', batchKey:content.batchKey, objectId, activeMechanismId:simulation.mechanism.id,
      simulations:[...simulations].map(([id, sim]) => ({id, state:sim.getViewState()})), scrollTop:shown ? host.scrollTop : savedScroll, focusKey, sourcesOpen};
  }
  function restoreViewState(saved) {
    if (destroyed || saved?.schema !== 'provider-session-workbench.v1' || saved.batchKey !== content.batchKey || saved.objectId !== objectId || !simulations.has(saved.activeMechanismId) || !Array.isArray(saved.simulations) || saved.simulations.length !== simulations.size || !Number.isFinite(saved.scrollTop) || saved.scrollTop < 0 || typeof saved.sourcesOpen !== 'boolean') return false;
    const staged = new Map();
    for (const item of saved.simulations) {
      if (!simulations.has(item.id) || staged.has(item.id)) return false;
      const sim = createProviderSessionSimulation({...content, mechanism:simulations.get(item.id).mechanism});
      if (!sim.restoreViewState(item.state)) return false; staged.set(item.id, sim);
    }
    for (const [id, sim] of staged) simulations.set(id, sim);
    simulation = simulations.get(saved.activeMechanismId); savedScroll = saved.scrollTop; focusKey = typeof saved.focusKey === 'string' ? saved.focusKey : null; sourcesOpen = saved.sourcesOpen;
    render(); host.scrollTop = savedScroll; focus(); return true;
  }
  host.addEventListener('click', event => {
    const b = event.target.closest?.('button[data-psm-action]');
    if (!b || !host.contains(b) || b.disabled || !shown || destroyed || signal?.aborted) return;
    const action = b.dataset.psmAction; focusKey = action;
    if (action === 'return') return emit('return', getViewState());
    if (action.startsWith('source:')) return emit('source', action.slice(7));
    if (action.startsWith('select:')) return emit('select', {objectId:action.slice(7)});
    if (action.startsWith('composition:')) return emit('navigate', {kind:'composition', objectId:action.slice(12), mechanismId:null});
    if (action.startsWith('delegate:')) { const mode = delegatedModes.find(m => m.id === action.slice(9)); if (mode) emit('navigate', mode.route); return; }
    if (action.startsWith('mode:')) {
      const sim = simulations.get(action.slice(5)); if (!sim) return; simulation = sim;
    } else if (action === 'next') simulation.perform(simulation.getScene().next?.id);
    else if (action === 'back') simulation.back(); else if (action === 'reset') simulation.reset(); else return;
    render(true); emit('change', simulation.getScene());
  }, {signal:listeners.signal});
  host.addEventListener('change', event => {
    if (!shown || destroyed || signal?.aborted || event.target.dataset.psmAction !== 'case') return;
    if (!simulation.chooseCase(event.target.value)) return; focusKey = 'case'; render(true); emit('change', simulation.getScene());
  }, {signal:listeners.signal});
  host.addEventListener('toggle', event => { if (shown && event.target.matches?.('.psm-evidence')) sourcesOpen = event.target.open; }, {capture:true, signal:listeners.signal});
  function destroy() {
    if (destroyed) return; destroyed = true; shown = false; listeners.abort(); signal?.removeEventListener('abort', destroy); host.remove(); simulations.clear();
  }
  render(); if (initialState && !restoreViewState(initialState)) { destroy(); throw Error('provider_session:initial_state_rejected'); }
  signal?.addEventListener('abort', destroy, {once:true}); if (signal?.aborted) destroy();
  return {show() { if (!destroyed) { shown = true; host.hidden = false; host.scrollTop = savedScroll; focus(); } },
    hide() { if (!destroyed) { savedScroll = host.scrollTop; shown = false; host.hidden = true; } }, destroy,
    getViewState, restoreViewState, getScene:() => destroyed ? null : simulation.getScene()};
}

export const providerSessionMechanismFactories = Object.freeze(Object.fromEntries(Object.keys(PROVIDER_SESSION_ADAPTERS).map(id => [id, createProviderSessionWorkbench])));
export const providerSessionAdapterFactories = Object.freeze(Object.fromEntries(Object.values(PROVIDER_SESSION_ADAPTERS).map(id => [id, createProviderSessionWorkbench])));
