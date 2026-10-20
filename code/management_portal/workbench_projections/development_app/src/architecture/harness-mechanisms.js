import './harness-mechanisms.css';
import {HARNESS_ADAPTERS, createHarnessSimulation, getHarnessWorkbenchEntry} from './harness-mechanisms.model.js';
export {HARNESS_ADAPTERS, HARNESS_OBJECT_ENTRIES, getHarnessWorkbenchEntry, supportsHarnessMechanisms} from './harness-mechanisms.model.js';

const el = (tag, cls, text) => { const n = document.createElement(tag); if (cls) n.className = cls; if (text !== undefined) n.textContent = text; return n; };
const note = text => el('p', 'hrn-note', text);
const token = (text, tone = '') => el('span', `hrn-token ${tone}`, text);
const did = (scene, id) => scene.trace.some(s => s.id === id);
const display = value => value === '' ? '尚未形成' : typeof value === 'boolean' ? (value ? '是' : '否') : String(value);
function station(title, body, active = false, cls = '') {
  const n = el('div', `hrn-station ${active ? 'active' : ''} ${cls}`);
  n.append(el('strong', '', title), el('span', '', body)); return n;
}
function arrow(text = '↓') { return el('div', 'hrn-arrow', text); }
function planDiagram(scene) {
  const s = scene.state, root = el('div', 'hrn-plan'), lanes = el('div', 'hrn-plan-lanes');
  const fresh = el('div', 'hrn-plan-new');
  fresh.append(station('声明能力 + 策略', scene.cursor ? ({normal:'正常计算', policy_failure:'可选策略普通异常', recovered_turn:'已有 turn 接管', control_signal:'执行控制信号'})[s.plan_condition] : '等待载入情境', scene.cursor > 0),
    arrow('能力筛选 ↓'), station('本轮有效计划', s.effective_plan || '待选择', Boolean(s.effective_plan)),
    note('缺能力插件：skipped / requires_<capability>。按 Adapter 声明检查能力。'));
  const checkpoint = el('div', 'hrn-checkpoint');
  checkpoint.append(el('div', 'hrn-checkpoint-lid', 'Runtime 恢复点'),
    el('div', 'hrn-checkpoint-body', did(scene, 'save') ? '实际计划已保存' : did(scene, 'replay') ? '回放已保存计划' : did(scene, 'fallback') ? 'manual；存在 saved plan 时回放' : '等待保存或恢复'),
    token(did(scene, 'replay') ? '已接受 turn · 跳过输入改写' : '恢复时保护原输入', did(scene, 'replay') ? 'good' : ''),
    note('恢复点在此只作为机制参与者。Runtime 的组成由父图展示。'));
  lanes.append(fresh, arrow(did(scene, 'replay') ? '← 回放' : '保存 →'), checkpoint); root.append(lanes);
  const boundary = el('div', `hrn-control-exit ${s.error ? 'active' : ''}`);
  boundary.append(token('执行控制出口'), el('strong', '', s.error || 'ExecutionControlSignal 原样向外传播'));
  root.append(boundary); return root;
}
function toolsDiagram(scene) {
  const s = scene.state, root = el('div', 'hrn-tools');
  const gate = el('div', `hrn-tools-gate ${did(scene, 'unavailable') ? 'closed' : ''}`);
  gate.append(el('strong', '', 'portal_tools 能力闸门'), token(did(scene, 'unavailable') ? 'requires_portal_tools · 原 invocation 保留' : scene.cursor ? '按本次前提检查' : '等待载入'));
  root.append(gate);
  const assembly = el('div', 'hrn-assembly');
  const incoming = el('div', 'hrn-tool-input'); incoming.append(el('strong', '', '本次请求的工具入口'), el('div', 'hrn-tool-plug', did(scene, 'empty') ? '空名字 → ValueError' : scene.cursor && s.condition === 'already_attached' ? '同名入口' : '合法具名入口'));
  const set = el('div', 'hrn-tool-set'); set.append(el('strong', '', '本轮 invocation.harness_tools'),
    el('div', `hrn-tool-slot ${did(scene, 'attach') ? 'filled' : ''}`, did(scene, 'attach') ? '新增 / 升级：1' : did(scene, 'same-name') ? '同名已暴露 · 不再加入' : '本案例尚无新增'),
    token(`新增 / 升级入口数：${s.attached_delta}`, s.attached_delta ? 'good' : ''));
  assembly.append(incoming, arrow('按名装配 →'), set); root.append(assembly);
  root.append(el('div', `hrn-tool-result ${s.error ? 'error' : ''}`, s.error || s.reason || '每次准备都显式检查能力与名字'),
    note('装配先于可选插件；原生会话的固定工具缺失由会话生命周期处理。'));
  return root;
}
function pluginDiagram(scene) {
  const s = scene.state, root = el('div', 'hrn-plugin');
  const applied = did(scene, 'apply'), failedOpen = did(scene, 'failed-open'), batchError = did(scene, 'batch-error'), controlled = did(scene, 'control');
  root.append(station('before_turn / after_tool_batch', scene.cursor ? ({applied:'正常变换', ordinary_error:'普通插件异常', control_signal:'取消 / 移交控制信号', invalid_batch_transform:'批次变换返回错误类型'})[s.plugin_result] : '等待载入情境', scene.cursor > 0));
  const fork = el('div', 'hrn-plugin-fork'), passage = el('div', `hrn-plugin-passage ${controlled ? 'unused' : ''}`), control = el('div', `hrn-plugin-control ${controlled ? 'active' : ''}`);
  passage.dataset.pluginBranch = controlled ? 'not-taken' : applied ? 'applied' : failedOpen ? 'failed-open' : batchError ? 'batch-error' : 'pending';
  passage.append(el('h3', '', controlled ? '继续执行分支 · 未走' : '插件结果与继续执行'),
    station('本插件观察状态', s.plugin_status, s.plugin_status !== 'pending'));
  if (batchError) passage.append(token('错误类型 · 保留进入插件的当前批次', 'warn'));
  if (applied || failedOpen) passage.append(arrow(applied ? '正常变换 applied · 变换后继续 ↓' : '普通异常 failed_open · 容错放行 ↓'));
  else passage.append(note(controlled ? '控制信号从独立出口透传；未走普通放行路径。' : batchError ? '本案例保留当前批次，未进入 Provider。' : '等待插件结果，尚未选择继续执行路径。'));
  passage.append(station('Provider 调用边界', s.provider_called ? '本案例已进入 Adapter' : controlled || batchError ? '本案例未进入 Adapter' : '本案例尚未进入 Adapter', s.provider_called, 'hrn-provider'));
  if (!controlled && !batchError) passage.append(note('进入 Adapter 只表示调用已发生，不能据此断言成功。'));
  control.append(el('h3', '', '执行控制独立出口'), el('div', 'hrn-stop-sign', '↗'), token('ExecutionControlSignal', controlled ? 'error' : ''),
    note(controlled ? '控制信号向外透传；Provider 未调用。' : applied || failedOpen || batchError ? '本案例未走控制出口。' : '出现取消 / 移交信号时从此出口向外透传。'),
    note('原生内部工具只读观察；Harness 工具结果的变换属于明确的插件生命周期。'));
  fork.append(passage, control); root.append(fork); return root;
}
function observationDiagram(scene) {
  const s = scene.state, root = el('div', 'hrn-observation');
  const timeline = el('ol', 'hrn-timeline');
  for (const [title, body, active] of [
    ['prepared', s.prepared_observed ? '调用前准备事实已观察' : '尚无 prepared 事实', s.prepared_observed],
    ['Provider 结果', did(scene, 'failed') ? '异常向外传播' : did(scene, 'completed') ? '正常返回' : did(scene, 'telemetry-failed') ? '遥测失败隔离 · 结果不变' : '尚无结果事实', Boolean(s.local_report_status)],
    ['本地报告 / finally', s.local_report_status || '尚无报告', Boolean(s.local_report_status)],
  ]) { const item = el('li', active ? 'active' : ''); item.append(el('strong', '', title), el('span', '', body)); timeline.append(item); }
  root.append(timeline);
  const split = el('div', 'hrn-authority-split');
  split.append(el('strong', '', 'Scheduler · queued / running / terminal'), token('本机制终态写入：0'),
    note('Harness 的 completed / failed 描述本地观察。Scheduler 保有执行终态权威。'));
  root.append(split);
  if (s.original_error_preserved) root.append(el('div', 'hrn-original-error', 'Provider 原始异常保留，继续向外传播。'));
  if (did(scene, 'telemetry-failed')) root.append(note('本案例未观察到 prepared；completed 不能补造该事实。'));
  return root;
}
const diagrams = {'harness.plan-selection': planDiagram, 'harness.tool-attachment': toolsDiagram,
  'harness.plugin-lifecycle': pluginDiagram, 'harness.observation': observationDiagram};

export function createHarnessWorkbench(root, {subject, content, ports = {}, signal, initialState} = {}) {
  content = structuredClone(content);
  const objectId = subject?.objectId, entry = getHarnessWorkbenchEntry(objectId);
  const object = content?.objects?.find(o => o.id === objectId), mechanismId = subject?.mechanismId ?? content?.mechanism?.id;
  if (!entry || !object || mechanismId !== content?.mechanism?.id || !entry.modeMechanismIds.includes(mechanismId)
    || !object.mechanism_ids?.includes(mechanismId)) throw Error('harness_mechanisms:subject_mismatch');
  const inputs = new Map([[mechanismId, content]]), simulations = new Map([[mechanismId, createHarnessSimulation(content)]]);
  for (const id of entry.modeMechanismIds) {
    if (id === mechanismId || !object.mechanism_ids.includes(id)) continue;
    const matches = (content.mechanisms || []).filter(m => m.id === id);
    if (matches.length !== 1) continue;
    const candidate = {...content, mechanism: matches[0]};
    try { const sim = createHarnessSimulation(candidate); inputs.set(id, candidate); simulations.set(id, sim); }
    catch { /* Invalid/unavailable modes expose neither their label nor their count. */ }
  }
  let activeId = mechanismId, shown = true, destroyed = false, scrollTop = 0, focusKey = null, sourcesOpen = false;
  let savedModes = new Map();
  const host = el('section', 'harness-mechanisms'); host.setAttribute('aria-label', 'Harness 机制工作台'); root.append(host);
  const listeners = new AbortController();
  const sim = () => simulations.get(activeId);
  const emit = (name, value) => { if (shown && !destroyed && !signal?.aborted) ports[name]?.(value); };
  function button(title, action, value) {
    const b = el('button', '', title); b.type = 'button'; b.dataset.harnessAction = action;
    if (value) b.dataset.value = value; return b;
  }
  function focus() {
    if (shown && focusKey) [...host.querySelectorAll('[data-harness-action]')].find(n => `${n.dataset.harnessAction}:${n.dataset.value || ''}` === focusKey && !n.disabled)?.focus({preventScroll: true});
  }
  function render() {
    if (destroyed) return;
    const simulation = sim(), m = simulation.mechanism, scene = simulation.getScene();
    const header = el('header', 'hrn-header'); header.append(button('返回', 'return'), el('h2', '', m.title), token('本地案例', 'local'));
    const controls = el('div', 'hrn-controls');
    if (simulations.size > 1) {
      const modeLabel = el('label', '', '阅读主题'), select = el('select'); select.dataset.harnessAction = 'mode';
      for (const id of entry.modeMechanismIds.filter(id => simulations.has(id))) { const option = el('option', '', simulations.get(id).mechanism.title); option.value = id; select.append(option); }
      select.value = activeId; modeLabel.append(select); controls.append(modeLabel);
    }
    const caseLabel = el('label', '', '演示情境'), caseSelect = el('select'); caseSelect.dataset.harnessAction = 'case';
    for (const c of m.cases) { const option = el('option', '', c.title); option.value = c.id; caseSelect.append(option); }
    caseSelect.value = scene.case_id; caseLabel.append(caseSelect); controls.append(caseLabel);
    const diagram = diagrams[m.id](scene); diagram.dataset.harnessDiagram = m.id; diagram.setAttribute('aria-label', `${m.title}图示`);
    const actions = el('div', 'hrn-actions'), next = button(scene.next?.title || '本案例完成', 'next'), back = button('上一步', 'back');
    next.disabled = !scene.next; back.disabled = !scene.cursor;
    actions.append(next, back, button('重新演示', 'reset'), el('span', 'hrn-counter', `${scene.cursor} / ${scene.total_steps}`));
    const status = el('div', 'hrn-receipt'); status.setAttribute('role', 'status');
    status.append(el('strong', '', scene.current?.title || m.question));
    if (scene.current) for (const c of scene.trace.at(-1).changes) {
      const line = el('div', 'hrn-change');
      line.append(el('span', '', simulation.fields.get(c.field).meaning), el('code', '', `${display(c.before)} → ${display(c.after)}`)); status.append(line);
    }
    const details = el('details', 'hrn-sources'); details.open = sourcesOpen; details.append(el('summary', '', '正式规则与固定来源'));
    for (const invariant of m.invariants || []) details.append(note(invariant));
    if (scene.current) {
      details.append(note(`guard 说明：${scene.current.guard}`));
      details.append(el('pre', 'hrn-rule', JSON.stringify({when: scene.current.when ?? null, next: scene.current.next, state_changes: scene.current.state_changes}, null, 2)));
    }
    for (const id of scene.evidence_ids) {
      const e = simulation.evidence.get(id);
      const b = button(`${e.symbol || e.title || id} · L${e.start_line}–${e.end_line}`, 'source', id);
      b.title = `${e.frozen.address}\nSHA-256 ${e.frozen.sha256}`; details.append(b);
    }
    const nav = el('footer', 'hrn-nav'); nav.append(button('查看 Harness 组成', 'composition', 'worker.harness'));
    const ids = new Set([m.owner_id, ...(scene.current ? [scene.current.from, scene.current.to] : [])]);
    for (const id of ids) nav.append(button(simulation.objects.get(id).title || id, 'select', id));
    host.replaceChildren(header, controls, diagram, actions, status, details, nav); host.scrollTop = scrollTop; focus();
  }
  function localState() { return {simulation: sim().getViewState(), scrollTop: shown ? host.scrollTop : scrollTop, focusKey,
    sourcesOpen: host.querySelector('.hrn-sources')?.open ?? sourcesOpen}; }
  function getViewState() {
    if (destroyed) return null;
    const views = new Map(savedModes); views.set(activeId, localState());
    return structuredClone({schema: 'harness-workbench.v1', batchKey: content.batchKey, objectId, activeMechanismId: activeId,
      views: [...views].map(([mechanismId, view]) => ({mechanismId, view}))});
  }
  function localValid(view) {
    return view && Number.isFinite(view.scrollTop) && view.scrollTop >= 0 && (view.focusKey === null || typeof view.focusKey === 'string') && typeof view.sourcesOpen === 'boolean';
  }
  function restoreViewState(state) {
    if (destroyed || state?.schema !== 'harness-workbench.v1' || state.batchKey !== content.batchKey || state.objectId !== objectId
      || !simulations.has(state.activeMechanismId) || !Array.isArray(state.views)) return false;
    const staged = new Map(), views = new Map();
    for (const item of state.views) {
      if (!inputs.has(item?.mechanismId) || staged.has(item.mechanismId) || !localValid(item.view)) return false;
      const simulation = createHarnessSimulation(inputs.get(item.mechanismId));
      if (!simulation.restoreViewState(item.view.simulation)) return false;
      staged.set(item.mechanismId, simulation); views.set(item.mechanismId, structuredClone(item.view));
    }
    if (!staged.has(state.activeMechanismId)) return false;
    // Validate the complete saved frame before replacing any active state.
    for (const [id, input] of inputs) if (!staged.has(id)) staged.set(id, createHarnessSimulation(input));
    simulations.clear(); for (const [id, simulation] of staged) simulations.set(id, simulation);
    savedModes = views; activeId = state.activeMechanismId;
    const saved = views.get(activeId); ({scrollTop, focusKey, sourcesOpen} = saved); render(); return true;
  }
  host.addEventListener('click', event => {
    const target = event.target.closest?.('[data-harness-action]');
    if (!target || !host.contains(target) || !shown || destroyed || target.disabled || signal?.aborted) return;
    const {harnessAction: action, value} = target.dataset; focusKey = `${action}:${value || ''}`;
    scrollTop = host.scrollTop;
    if (action === 'return') return emit('return', getViewState());
    if (action === 'source') return emit('source', value);
    if (action === 'select') return emit('select', {objectId: value});
    if (action === 'composition') return emit('navigate', {kind: 'composition', objectId: value, mechanismId: null});
    if (!['next', 'back', 'reset'].includes(action)) return;
    if (action === 'next') sim().advance(); if (action === 'back') sim().back(); if (action === 'reset') sim().reset();
    render(); emit('change', sim().getScene());
  }, {signal: listeners.signal});
  host.addEventListener('change', event => {
    if (!shown || destroyed || signal?.aborted) return;
    const action = event.target.dataset.harnessAction;
    if (action === 'mode') {
      const id = event.target.value;
      if (!simulations.has(id) || activeId === id) return;
      savedModes.set(activeId, localState()); activeId = id;
      const saved = savedModes.get(id); scrollTop = saved?.scrollTop || 0; sourcesOpen = saved?.sourcesOpen || false;
      focusKey = 'mode:'; render(); emit('change', sim().getScene()); return;
    }
    if (action !== 'case' || !sim().chooseCase(event.target.value)) return;
    scrollTop = host.scrollTop; focusKey = 'case:'; render(); emit('change', sim().getScene());
  }, {signal: listeners.signal});
  host.addEventListener('toggle', event => {
    if (shown && !destroyed && event.target.matches?.('.hrn-sources') && host.contains(event.target)) sourcesOpen = event.target.open;
  }, {capture: true, signal: listeners.signal});
  function destroy() { if (destroyed) return; destroyed = true; shown = false; listeners.abort(); host.remove(); simulations.clear(); savedModes.clear(); signal?.removeEventListener('abort', destroy); }
  render(); if (initialState) restoreViewState(initialState);
  signal?.addEventListener('abort', destroy, {once: true}); if (signal?.aborted) destroy();
  return {show() { if (!destroyed) { shown = true; host.hidden = false; host.scrollTop = scrollTop; focus(); } },
    hide() { if (!destroyed) { scrollTop = host.scrollTop; shown = false; host.hidden = true; } },
    destroy, getViewState, restoreViewState, getScene: () => destroyed ? null : sim().getScene()};
}

export const HARNESS_FACTORIES = Object.freeze(Object.fromEntries(Object.keys(HARNESS_ADAPTERS).map(adapter => [adapter, createHarnessWorkbench])));
