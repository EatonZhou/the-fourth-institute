import './player.css';
import { branchOptions, createPlayback, movePlayback, selectPlaybackBranch,
  timelineFor, sceneFor, sourcesFor } from './playback.js';

let nextPlayerId = 0;
const asText = value => value == null ? '未提供' : typeof value === 'string' ? value : JSON.stringify(value, null, 2);
const shorten = (text, length = 22) => Array.from(asText(text)).length > length
  ? `${Array.from(asText(text)).slice(0, length - 1).join('')}…` : asText(text);

/** Render authorized, frozen scenario data. All controls affect local simulation only. */
export function createProtocolPlayer(root, { scenario: initialScenario, onSource, onReturn, onChange } = {}) {
  const doc = root.ownerDocument;
  const win = doc.defaultView;
  const instanceId = `arch-player-${++nextPlayerId}`;
  let scenario = null;
  let state = null;
  let playing = false;
  let hidden = false;
  let destroyed = false;
  let timer = null;
  let stepButtons = [];
  let hasManualMotionPreference = false;
  const media = win?.matchMedia?.('(prefers-reduced-motion: reduce)');

  const el = (tag, className, text) => {
    const node = doc.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = asText(text);
    return node;
  };
  const svgEl = (tag, attrs = {}, text) => {
    const node = doc.createElementNS('http://www.w3.org/2000/svg', tag);
    Object.entries(attrs).forEach(([name, value]) => node.setAttribute(name, String(value)));
    if (text !== undefined) node.textContent = asText(text);
    return node;
  };
  const button = (text, action) => {
    const node = el('button', 'portalButton arch-player-button', text);
    node.type = 'button';
    node.addEventListener('click', action);
    return node;
  };
  const host = el('section', 'arch-player');
  host.setAttribute('aria-label', '协议条件演示');
  host.dataset.status = 'simulated';
  const heading = el('div', 'arch-player-heading');
  const title = el('h3');
  const badge = el('span', 'arch-player-badge', 'simulated · 设计演示');
  heading.append(title, badge);
  badge.title = '仅在本地逐步解释所选条件；不执行真实请求、确认或反馈。';
  const choices = el('div', 'arch-player-choice');
  const branchLabel = el('label', null, '条件路径');
  branchLabel.htmlFor = `${instanceId}-branch`;
  const select = el('select', 'arch-player-select');
  select.id = branchLabel.htmlFor;
  select.addEventListener('change', () => {
    if (!scenario || destroyed) return;
    stop();
    state = selectPlaybackBranch(scenario, select.value);
    rebuildStepList();
    render();
    notifyChange();
  });
  choices.append(branchLabel, select);
  const controls = el('div', 'arch-player-controls');
  const previous = button('上一步', () => navigate(-1));
  const next = button('下一步', () => navigate(1));
  const play = button('播放', () => {
    if (!scenario || destroyed) return;
    if (playing) stop();
    else if (state.index < timelineFor(scenario, state.branch_id).length - 1 && !hidden && !doc.hidden) {
      playing = true;
      schedule();
    }
    render();
    notifyChange();
  });
  const reset = button('重置', () => {
    if (!scenario || destroyed) return;
    stop();
    state = createPlayback(scenario, state.branch_id);
    render();
    notifyChange();
  });
  const counter = el('span', 'arch-player-context');
  counter.setAttribute('role', 'status');
  const motionLabel = el('label', 'arch-player-motion');
  const motion = el('input');
  motion.type = 'checkbox';
  motion.checked = Boolean(media?.matches);
  motion.addEventListener('change', () => {
    hasManualMotionPreference = true;
    render();
    notifyChange();
  });
  motionLabel.append(motion, doc.createTextNode('减弱动画'));
  controls.append(previous, play, next, reset, counter, motionLabel);
  const condition = el('p', 'arch-player-condition');
  const activeMessage = el('p', 'arch-player-current');
  activeMessage.setAttribute('aria-live', 'polite');
  const diagram = el('div', 'arch-player-diagram');
  diagram.tabIndex = 0;
  diagram.setAttribute('role', 'region');
  diagram.setAttribute('aria-label', '角色时序图，可滚动；完整文本见当前步骤和步骤目录');
  const legend = el('p', 'arch-player-legend', '每列是一项独立角色；箭头为场景明确的消息方向，回环为自身动作。强调色表示当前步骤，虚线表示尚未到达。');
  const details = el('section', 'arch-player-details');
  details.setAttribute('aria-live', 'polite');
  const stepTitle = el('h4');
  const fields = el('dl');
  const values = {};
  for (const [key, label] of [['roles', '参与角色'], ['condition', '成立条件'], ['request', '请求 / 动作'], ['result', '结果']]) {
    values[key] = el('dd');
    fields.append(el('dt', null, label), values[key]);
  }
  const stateView = el('div', 'arch-player-state');
  details.append(stepTitle, fields, stateView);
  const evidence = el('details', 'arch-player-evidence');
  const evidenceTitle = el('summary', null, '本步骤依据');
  const sourceList = el('div');
  evidence.append(evidenceTitle, sourceList);
  const stepsDisclosure = el('details');
  const stepList = el('ol', 'arch-player-step-list');
  stepsDisclosure.append(el('summary', null, '完整步骤目录（可直接定位）'), stepList);
  const assumptions = el('details');
  const assumptionsContent = el('div', 'arch-player-context');
  assumptions.append(el('summary', null, '前提、版本与证据覆盖'), assumptionsContent);
  const returnButton = button('返回实际协议', () => {
    stop();
    render();
    invoke(onReturn, getScene());
  });
  returnButton.disabled = typeof onReturn !== 'function';
  const returnRow = el('div', 'arch-player-controls');
  heading.append(returnButton);
  returnRow.append(el('span', 'arch-player-context', '本演示不执行真实请求。返回实际协议后可进入“讨论协议解释”。'));
  const error = el('p', 'arch-player-error');
  error.setAttribute('role', 'alert');
  error.hidden = true;
  host.append(heading, choices, controls, condition, activeMessage, diagram, legend, details,
    evidence, stepsDisclosure, assumptions, returnRow, error);

  function invoke(callback, value) {
    if (destroyed || typeof callback !== 'function') return;
    error.hidden = true;
    try {
      Promise.resolve(callback(value)).catch(showError);
    } catch (failure) { showError(failure); }
  }
  function showError(failure) {
    if (destroyed) return;
    error.textContent = `操作未完成：${failure?.message || '宿主回调失败'}`;
    error.hidden = false;
  }
  function getScene() {
    if (!scenario || !state) return { status: 'simulated', scenario_id: null, playing: false };
    return { ...sceneFor(scenario, state), playing, reduced_motion: motion.checked };
  }
  function notifyChange() { invoke(onChange, getScene()); }
  function stop() {
    playing = false;
    if (timer !== null) win.clearTimeout(timer);
    timer = null;
  }
  function schedule() {
    if (!playing || hidden || destroyed) return;
    timer = win.setTimeout(() => {
      timer = null;
      if (!playing || hidden || destroyed || doc.hidden) { stop(); return; }
      state = movePlayback(scenario, state, 1);
      if (state.index >= timelineFor(scenario, state.branch_id).length - 1) stop();
      render();
      notifyChange();
      schedule();
    }, 1800);
  }
  function navigate(delta) {
    if (!scenario || destroyed) return;
    stop();
    state = movePlayback(scenario, state, delta);
    render();
    notifyChange();
  }
  function roleName(id) {
    const role = scenario.participants.find(item => item.id === id);
    return role ? `${role.role || role.id} [${role.id}]` : id;
  }
  function rebuildStepList() {
    stepList.replaceChildren();
    stepButtons = timelineFor(scenario, state.branch_id).map(step => {
      const item = el('li');
      const jump = button(`${roleName(step.from)} → ${roleName(step.to)}：${asText(step.request)}`, () => {
        if (destroyed) return;
        stop();
        state = { ...state, index: step.index };
        render();
        notifyChange();
      });
      jump.dataset.stepKey = step.key;
      item.append(jump);
      stepList.append(item);
      return jump;
    });
  }
  function renderDiagram(timeline) {
    const laneWidth = 100;
    const width = Math.max(640, scenario.participants.length * laneWidth + 60);
    const top = 36;
    const spacing = 76;
    const height = top + Math.max(1, timeline.length) * spacing + 30;
    const track = el('div', 'arch-player-track');
    track.style.minWidth = `${width}px`;
    const laneHeader = el('div', 'arch-player-lane-header');
    laneHeader.style.gridTemplateColumns = `repeat(${Math.max(1, scenario.participants.length)}, minmax(0, 1fr))`;
    laneHeader.style.paddingLeft = laneHeader.style.paddingRight = `${30 / width * 100}%`;
    const svg = svgEl('svg', { viewBox: `0 0 ${width} ${height}`, role: 'img',
      'aria-labelledby': `${instanceId}-graph-title ${instanceId}-graph-description` });
    svg.append(svgEl('title', { id: `${instanceId}-graph-title` }, `${scenario.title || scenario.scenario_id} · 角色时序图`),
      svgEl('desc', { id: `${instanceId}-graph-description` }, '角色保持独立，即使属于同一个模块。步骤方向来自场景明示字段；完整条件与结果在图下方。'));
    const positions = new Map();
    scenario.participants.forEach((role, index) => {
      const x = (index + .5) * (width - 60) / Math.max(1, scenario.participants.length) + 30;
      positions.set(role.id, x);
      const label = el('div', 'arch-player-role-label');
      label.dataset.participant = role.id;
      label.title = `${role.role || role.id} · ${role.module_id || '模块未提供'}`;
      label.setAttribute('aria-label', label.title);
      label.append(el('strong', null, role.id));
      laneHeader.append(label);
      const group = svgEl('g');
      group.append(svgEl('title', {}, `${role.role || role.id} · ${role.module_id || '模块未提供'}`));
      group.append(svgEl('line', { x1: x, y1: 0, x2: x, y2: height - 15, class: 'arch-player-lane' }));
      svg.append(group);
    });
    timeline.forEach(step => {
      const y = top + step.index * spacing;
      const from = positions.get(step.from);
      const to = positions.get(step.to);
      const self = from === to;
      const direction = to > from ? 1 : -1;
      const selfDirection = from > width - 190 ? -1 : 1;
      const path = self ? `M${from},${y} h${60 * selfDirection} v24 h${-60 * selfDirection}` : `M${from},${y} H${to}`;
      const row = svgEl('g', { class: 'arch-player-row', 'data-step-key': step.key,
        'data-state': step.index === state.index ? 'active' : step.index < state.index ? 'done' : 'future' });
      row.append(svgEl('title', {}, `${step.index + 1}. ${roleName(step.from)} → ${roleName(step.to)}\n条件：${asText(step.condition)}\n动作：${asText(step.request)}\n结果：${asText(step.result)}`),
        svgEl('path', { d: path, class: 'arch-player-message' }));
      const arrowY = y + (self ? 24 : 0);
      const arrowDirection = self ? -selfDirection : direction;
      row.append(svgEl('path', { d: `M${to - arrowDirection * 9},${arrowY - 5} L${to},${arrowY} L${to - arrowDirection * 9},${arrowY + 5} Z`, class: 'arch-player-arrow' }),
        svgEl('text', { x: self ? from + 8 * selfDirection : (from + to) / 2, y: y - 12,
          'text-anchor': self ? selfDirection > 0 ? 'start' : 'end' : 'middle', class: 'arch-player-caption' }, `${step.index + 1}. ${shorten(step.request, self ? 15 : Math.max(7, Math.floor(Math.abs(to - from) / 12)))}`));
      if (step.index === state.index && playing && !motion.checked && !hidden) {
        const pulse = svgEl('circle', { r: 4, class: 'arch-player-pulse' });
        pulse.append(svgEl('animateMotion', { path, dur: '1.25s', repeatCount: '1', fill: 'freeze' }));
        row.append(pulse);
      }
      svg.append(row);
    });
    track.append(laneHeader, svg);
    diagram.replaceChildren(track);
    const rowY = top + state.index * spacing;
    const scale = track.clientWidth > 0 ? track.clientWidth / width : 1;
    const screenY = rowY * scale + laneHeader.clientHeight;
    if (state.index === 0) diagram.scrollTop = 0;
    else if (screenY > diagram.scrollTop + diagram.clientHeight - 70 || screenY < diagram.scrollTop + laneHeader.clientHeight + 30) {
      diagram.scrollTop = Math.max(0, screenY - diagram.clientHeight / 2);
    }
  }
  function renderExplanation(explanation) {
    stateView.replaceChildren();
    stateView.hidden = !explanation;
    if (!explanation) return;
    stateView.append(el('strong', null, '本地演示状态'));
    if (explanation.type === 'revision') {
      stateView.append(el('p', null, `通道修订：${explanation.current_revision || '尚未分配'} · 当前详情：${explanation.visible_detail || '尚未应用'}`),
        el('p', null, `读取在途：${explanation.pending_reads.join('、') || '无'} · 已丢弃晚到结果：${explanation.discarded.join('、') || '无'}`),
        el('p', 'arch-player-context', '每个响应使用发起读取时的修订进行核对。订阅方接收 focus 后不反向发布。'));
    } else if (explanation.evidence_missing) {
      stateView.append(el('p', null, '预算示例缺失；仅展示有来源的步骤说明。'));
    } else {
      const timerLabels = { inactive: '尚未启动', active: '活动处理', paused: '等待确认，暂停计时', settled: '已结束', expired: '预算耗尽' };
      const deliveryLabels = { not_submitted: '尚未提交', submitted: '已提交，等待回执', receipt: '已收到投递回执', unknown: '执行结果待核实' };
      const gauge = el('progress', 'arch-player-budget');
      gauge.max = explanation.initial_ms;
      gauge.value = explanation.remaining_ms;
      gauge.setAttribute('aria-label', '示例剩余活动预算');
      stateView.append(el('p', null, `剩余活动预算 ${explanation.remaining_ms / 1000} / ${explanation.initial_ms / 1000} 秒 · ${timerLabels[explanation.timer]}`), gauge,
        el('p', null, `确认等待 ${explanation.confirmation_wait_ms / 1000} 秒（示例） · ${deliveryLabels[explanation.delivery]}`),
        el('p', null, `本路径提交：${explanation.submission_count == null ? '未知' : `${explanation.submission_count} 次`} · 正式回复：${explanation.reply === 'native_chat' ? '进入宿主原生对话区' : '尚无'} · 草稿保留`));
      if (explanation.outcome) stateView.append(el('p', null, `结果：${explanation.outcome}`));
      stateView.append(el('p', 'arch-player-context', `${asText(scenario.budget_example.assumption)}。步骤播放间隔不计作协议预算；暂停确认不会重置剩余预算。`));
    }
  }
  function renderSources(step) {
    const sources = sourcesFor(scenario, step);
    evidenceTitle.textContent = `本步骤依据（${sources.length}）`;
    sourceList.replaceChildren();
    if (!sources.length) sourceList.append(el('p', null, '该步骤没有可用来源；保留证据缺失。'));
    for (const source of sources) {
      const item = el('div', 'arch-player-source');
      const lines = source.line_start ? `L${source.line_start}–L${source.line_end}` : '未提供行范围';
      item.append(el('p', null, `${source.ref || '来源未命名'} · ${source.section || '未提供段落'} · ${lines}`),
        el('p', 'arch-player-context', `正文版本 ${source.body_version ?? '未知'} / 响应版本 ${source.response_version ?? '未知'}`),
        el('p', 'arch-player-context', `SHA256 ${source.body_sha256 || '未提供'}`));
      if (source.excerpt) item.append(el('pre', null, source.excerpt));
      const sourceButton = button(source.frozen_ref ? '查看冻结来源' : '冻结正文入口缺失', () => invoke(onSource, source.frozen_ref));
      sourceButton.disabled = !source.frozen_ref || typeof onSource !== 'function';
      if (source.frozen_ref) sourceButton.title = source.frozen_ref;
      item.append(sourceButton);
      sourceList.append(item);
    }
  }
  function renderAssumptions() {
    assumptionsContent.replaceChildren();
    for (const [label, items] of [['前提', scenario.preconditions], ['限制', scenario.limitations]]) {
      for (const item of Array.isArray(items) ? items : items ? [items] : []) assumptionsContent.append(el('p', null, `${label}：${asText(item)}`));
    }
    const concurrency = scenario.concurrency;
    if (concurrency) assumptionsContent.append(el('p', null, `并发依据：${concurrency.status || '未知'} · ${concurrency.reason || concurrency.rule || '见已提供的条件路径'}`));
    if (concurrency?.status === 'evidence_missing') {
      const missing = button('并发步骤缺少依据', () => {});
      missing.disabled = true;
      assumptionsContent.append(missing);
    }
    for (const source of scenario.source_versions || []) assumptionsContent.append(el('p', null,
      `${source.ref} · 正文 ${source.body_version ?? '未知'} / 响应 ${source.response_version ?? '未知'} · 观察 ${source.observed_at || '未知'} · SHA256 ${source.body_sha256 || source.body?.sha256 || '未知'}`));
    if (scenario.review_status) assumptionsContent.append(el('p', null, `场景事实审阅：${scenario.review_status}`));
  }
  function render() {
    if (destroyed || !scenario) return;
    const timeline = timelineFor(scenario, state.branch_id);
    const step = timeline[state.index];
    const branch = branchOptions(scenario).find(item => item.id === state.branch_id);
    title.textContent = scenario.title || scenario.scenario_id || scenario.id;
    select.value = state.branch_id;
    previous.disabled = state.index <= 0;
    next.disabled = state.index >= timeline.length - 1;
    play.disabled = !playing && next.disabled;
    play.textContent = playing ? '暂停' : '播放';
    play.setAttribute('aria-pressed', String(playing));
    counter.textContent = timeline.length ? `${state.index + 1} / ${timeline.length} · ${playing ? '播放中' : '已暂停'}` : '步骤证据缺失';
    condition.textContent = branch.condition ? `所选条件：${branch.condition}` : '主路径按各步骤的成立条件展开。';
    stepTitle.textContent = step ? `步骤 ${state.index + 1} · ${step.id}` : '没有已提供的步骤';
    values.roles.textContent = step ? `${roleName(step.from)} → ${roleName(step.to)}` : '未提供';
    for (const key of ['condition', 'request', 'result']) values[key].textContent = asText(step?.[key]);
    stepButtons.forEach((item, index) => {
      if (index === state.index) item.setAttribute('aria-current', 'step');
      else item.removeAttribute('aria-current');
    });
    activeMessage.textContent = step ? `${state.index + 1} / ${timeline.length} · ${step.from} → ${step.to}` : '步骤依据缺失';
    renderDiagram(timeline);
    renderExplanation(sceneFor(scenario, state).explanation);
    renderSources(step);
  }
  function setScenario(nextScenario) {
    if (destroyed) return;
    // Validate before replacing a working scene; caller keeps its current view on failure.
    const nextState = createPlayback(nextScenario);
    stop();
    scenario = JSON.parse(JSON.stringify(nextScenario));
    state = nextState;
    error.hidden = true;
    const options = branchOptions(scenario).map(item => {
      const option = el('option', null, item.label);
      option.value = item.id;
      return option;
    });
    select.replaceChildren(...options);
    rebuildStepList();
    renderAssumptions();
    render();
    notifyChange();
  }
  function handleVisibility() {
    if (doc.hidden) { stop(); render(); notifyChange(); }
  }
  function handleMotion(event) {
    if (!hasManualMotionPreference) { motion.checked = event.matches; render(); }
  }
  doc.addEventListener('visibilitychange', handleVisibility);
  media?.addEventListener?.('change', handleMotion);
  try { setScenario(initialScenario); }
  catch (failure) {
    doc.removeEventListener('visibilitychange', handleVisibility);
    media?.removeEventListener?.('change', handleMotion);
    throw failure;
  }
  root.append(host);
  return {
    show() { if (!destroyed) { hidden = false; host.hidden = false; render(); } },
    hide() { if (!destroyed) { stop(); hidden = true; host.hidden = true; render(); notifyChange(); } },
    destroy() {
      if (destroyed) return;
      stop(); destroyed = true;
      doc.removeEventListener('visibilitychange', handleVisibility);
      media?.removeEventListener?.('change', handleMotion);
      host.remove();
    },
    getScene,
    setScenario,
  };
}
