import './protocol-workbench.css';
import {
  MAIN_BRANCH, branchOptions, createPlayback, movePlayback, selectPlaybackBranch,
  timelineFor, sceneFor, sourcesFor,
} from './playback.js';

const RESOURCE = 'resource-access/resident-read-small-resource';
const FOCUS = 'projection-family/focus-revision-order';
const pair = scenario => `${scenario?.protocol_id}/${scenario?.scenario_id || scenario?.id}`;
const text = value => value == null ? '未提供' : typeof value === 'string' ? value : JSON.stringify(value);
const clone = value => JSON.parse(JSON.stringify(value));

export function supportsProtocolWorkbench(scenario) {
  return scenario?.status === 'simulated' && [RESOURCE, FOCUS].includes(pair(scenario));
}

// A projection of the existing playback prefix, not a second transition engine.
// In particular, an earlier accepted A result is not the detail of a later B selection.
export function protocolWorkbenchView(scenario, state) {
  if (!supportsProtocolWorkbench(scenario)) throw new Error('此场景没有专属协议工作台');
  const timeline = timelineFor(scenario, state.branch_id);
  const step = timeline[state.index];
  const prefix = timeline.slice(0, state.index + 1);
  const has = id => prefix.some(item => item.id === id);
  if (pair(scenario) === RESOURCE) {
    const denied = state.branch_id === 'forbidden' && has('resolve');
    const partial = state.branch_id === 'partial_body' && has('return');
    const outcome = has('deny') ? 'denied' : has('incomplete') ? 'incomplete'
      : has('return') && !partial ? 'complete' : 'pending';
    return {type: 'resource', step, outcome, request: has('request'),
      gate: denied ? 'denied' : has('resolve') ? 'allowed' : 'waiting',
      reader: has('read') ? 'returned' : 'not_started',
      receipt: outcome === 'complete' ? 'complete' : outcome === 'denied' ? 'denied'
        : partial ? 'incomplete' : 'waiting',
      response: has('return') || has('deny'),
    };
  }
  const explanation = sceneFor(scenario, state).explanation;
  const selected = explanation?.current_revision === 'rB' ? 'B' : explanation?.current_revision === 'rA' ? 'A' : null;
  const displayed = explanation?.visible_detail === selected ? selected : null;
  const packets = ['A', 'B'].filter(letter => has(`select-${letter.toLowerCase()}`)).map(letter => {
    const revision = `r${letter}`;
    const status = explanation.discarded.includes(revision) ? 'discarded'
      : explanation.pending_reads.includes(revision) ? 'pending'
        : has(`apply-${letter.toLowerCase()}`) ? 'applied' : 'not_started';
    return {letter, revision, status, current: selected === letter};
  });
  return {type: 'focus', step, selected, displayed, revision: explanation?.current_revision, packets};
}

let nextInstance = 0;
export function createProtocolWorkbench(root, {scenario: initialScenario, onSource, onReturn, onChange} = {}) {
  const doc = root.ownerDocument, win = doc.defaultView;
  const instance = `protocol-workbench-${++nextInstance}`;
  const media = win.matchMedia?.('(prefers-reduced-motion: reduce)');
  let scenario, state, timer = null, playing = false, hidden = false, destroyed = false, motionChanged = false;
  const el = (tag, className, value) => {
    const node = doc.createElement(tag);
    if (className) node.className = className;
    if (value != null) node.textContent = value;
    return node;
  };
  const button = (label, action, role) => {
    const node = el('button', 'portalButton', label);
    node.type = 'button';
    if (role) node.dataset.role = role;
    node.addEventListener('click', action);
    return node;
  };
  const host = el('section', 'arch-protocol-workbench');
  host.dataset.testid = 'protocol-workbench'; host.dataset.status = 'simulated';
  const header = el('header', 'apw-header'), heading = el('h2'), headingId = `${instance}-title`;
  heading.id = headingId; host.setAttribute('aria-labelledby', headingId);
  const headingGroup = el('div');
  headingGroup.append(el('p', 'apw-simulated', 'simulated · 固定 Design 解释 · 仅本地播放'), heading);
  const returnButton = button('返回实际协议', () => { stop(); render(); notify(); invoke(onReturn, getScene()); }, 'return');
  returnButton.disabled = typeof onReturn !== 'function';
  header.append(headingGroup, returnButton);
  const branchRow = el('div', 'apw-branches'); branchRow.setAttribute('role', 'group'); branchRow.setAttribute('aria-label', '选择演示条件');
  const branchCondition = el('p', 'apw-condition'); branchCondition.dataset.role = 'branch-condition';
  const mechanism = el('div', 'apw-mechanism'); mechanism.dataset.role = 'mechanism';
  const controls = el('div', 'apw-controls'); controls.setAttribute('role', 'group'); controls.setAttribute('aria-label', '播放控制');
  const previous = button('上一步', () => move(-1), 'previous');
  const play = button('播放', () => {
    if (playing) stop();
    else if (!hidden && !doc.hidden && state.index < timelineFor(scenario, state.branch_id).length - 1) playing = true;
    render(); notify(); schedule();
  }, 'play');
  const next = button('下一步', () => move(1), 'next');
  const reset = button('重新开始', () => { stop(); state = createPlayback(scenario, state.branch_id); render(); notify(); }, 'reset');
  const counter = el('span', 'apw-counter'); counter.dataset.role = 'counter'; counter.setAttribute('role', 'status');
  const motionLabel = el('label', 'apw-motion'), motion = el('input');
  motion.type = 'checkbox'; motion.checked = !!media?.matches;
  motion.addEventListener('change', () => { motionChanged = true; render(); notify(); });
  motionLabel.append(motion, doc.createTextNode('减弱动画'));
  controls.append(previous, play, next, reset, counter, motionLabel);
  const explanation = el('div', 'apw-step'); explanation.dataset.role = 'step'; explanation.setAttribute('aria-live', 'polite');
  const stepHeading = el('h3'), result = el('p'); result.dataset.role = 'result';
  explanation.append(stepHeading, result);
  const details = el('details', 'apw-evidence'), evidenceTitle = el('summary', null, '这一步的条件与来源');
  const evidence = el('div'); details.append(evidenceTitle, evidence);
  const directory = el('details', 'apw-directory'), steps = el('div');
  directory.append(el('summary', null, '步骤目录'), steps);
  const assumptions = el('details', 'apw-assumptions'), assumptionsBody = el('div');
  assumptions.append(el('summary', null, '场景前提、范围与冻结版本'), assumptionsBody);
  const error = el('p', 'apw-error'); error.hidden = true; error.setAttribute('role', 'alert');
  host.append(header, branchRow, branchCondition, mechanism, controls, explanation, details, directory, assumptions, error);

  function invoke(callback, value) {
    if (destroyed || typeof callback !== 'function') return;
    const fail = failure => { if (!destroyed) { error.textContent = `操作未完成：${failure?.message || String(failure)}`; error.hidden = false; } };
    try { Promise.resolve(callback(value)).catch(fail); } catch (failure) { fail(failure); }
  }
  function getScene() {
    const scene = sceneFor(scenario, state);
    // Report the visible detail, not a previously accepted result hidden by the new selection.
    if (scene.explanation?.type === 'revision') scene.explanation.visible_detail = protocolWorkbenchView(scenario, state).displayed;
    return {...scene, playing, reduced_motion: motion.checked};
  }
  function notify() { invoke(onChange, getScene()); }
  function stop() { playing = false; if (timer != null) win.clearTimeout(timer); timer = null; }
  function schedule() {
    if (!playing || hidden || destroyed || doc.hidden || timer != null) return;
    timer = win.setTimeout(() => {
      timer = null;
      if (!playing || hidden || destroyed || doc.hidden) { stop(); return; }
      state = movePlayback(scenario, state, 1);
      if (state.index >= timelineFor(scenario, state.branch_id).length - 1) stop();
      render(); notify(); schedule();
    }, 1800);
  }
  function move(delta) { stop(); state = movePlayback(scenario, state, delta); render(); notify(); }
  function changeBranch(id) { stop(); state = selectPlaybackBranch(scenario, id); rebuildDirectory(); render(); notify(); }
  function rebuildDirectory() {
    steps.replaceChildren(...timelineFor(scenario, state.branch_id).map((step, index) => {
      const node = button(`${index + 1}. ${step.id}`, () => { stop(); state = {...state, index}; render(); notify(); });
      node.dataset.stepId = step.id; node.dataset.stepIndex = index; return node;
    }));
  }
  function svgElement(tag, attributes = {}, content) {
    const node = doc.createElementNS('http://www.w3.org/2000/svg', tag);
    for (const [name, value] of Object.entries(attributes)) node.setAttribute(name, value);
    if (content != null) node.textContent = content;
    return node;
  }
  function renderResource(view) {
    const svg = svgElement('svg', {viewBox: '0 0 600 400', role: 'img', 'aria-label': '请求经过授权与正文读取，由 ResourceService 返回同一调用者', 'data-role': 'resource-diagram'});
    const defs = svgElement('defs');
    for (const name of ['normal', 'failure', 'waiting']) {
      const marker = svgElement('marker', {id: `${instance}-${name}`, viewBox: '0 0 10 10', refX: 9, refY: 5, markerWidth: 7, markerHeight: 7, orient: 'auto-start-reverse'});
      marker.append(svgElement('path', {d: 'M 0 0 L 10 5 L 0 10 Z', class: `apw-arrow-${name}`})); defs.append(marker);
    }
    svg.append(defs, svgElement('rect', {x: 304, y: 20, width: 282, height: 365, rx: 16, class: 'apw-service-boundary'}),
      svgElement('text', {x: 320, y: 43, class: 'apw-svg-label'}, 'ResourceService 内部'));
    function flow(role, path, state, label, x, y) {
      const group = svgElement('g', {'data-flow': role, 'data-state': state, class: `apw-flow apw-flow-${state}`});
      group.append(svgElement('path', {d: path, 'marker-end': `url(#${instance}-${state})`}), svgElement('text', {x, y, class: 'apw-svg-label'}, label));
      svg.append(group);
    }
    flow('request', 'M 212 98 H 330', view.request ? 'normal' : 'waiting', '请求', 252, 83);
    flow('read', 'M 380 155 V 278', view.reader === 'returned' ? 'normal' : 'waiting', view.gate === 'denied' ? '未进入正文读取' : '调用 reader', 392, 211);
    flow('reader-return', 'M 530 278 V 155', view.reader === 'returned' ? 'normal' : 'waiting', '字节返回服务', 469, 245);
    const responseState = !view.response ? 'waiting' : view.receipt === 'complete' ? 'normal' : 'failure';
    flow('service-return', 'M 330 133 H 270 V 302 H 212', responseState,
      view.receipt === 'denied' ? '拒绝错误返回' : view.receipt === 'incomplete' ? '正文未收完整' : '服务返回', 163, 198);
    function box(role, x, y, width, height, title, lines, status = '') {
      const group = svgElement('g', {'data-role': role, 'data-state': status, class: `apw-box apw-box-${status}`});
      group.append(svgElement('rect', {x, y, width, height, rx: 12}), svgElement('text', {x: x + 14, y: y + 27, class: 'apw-svg-title'}, title));
      lines.forEach((line, index) => group.append(svgElement('text', {x: x + 14, y: y + 52 + index * 23, class: 'apw-svg-copy'}, line)));
      svg.append(group);
    }
    box('requester', 20, 57, 192, 82, '调用者', ['认证入口发起读取'], 'normal');
    box('authorization', 330, 65, 238, 90, '解析与权限关口', [view.gate === 'denied' ? '权限拒绝 · 正文不读取' : view.gate === 'allowed' ? '授权通过，允许读取' : '等待解析与权限检查'], view.gate === 'denied' ? 'failure' : view.gate === 'allowed' ? 'normal' : 'waiting');
    box('reader', 330, 278, 238, 85, '类型正文 reader', [view.reader === 'returned' ? '原始字节已返回服务' : '尚未读取正文'], view.reader === 'returned' ? 'normal' : 'waiting');
    const receipt = {complete: ['正文完整，读取成功'], denied: ['收到拒绝错误', '未收到正文'], incomplete: ['部分字节不算完整正文', '拒绝成功'], waiting: ['等待本次读取结果']}[view.receipt];
    box('receipt', 20, 252, 192, 108, '同一调用者收件', receipt, view.receipt === 'waiting' ? 'waiting' : view.receipt === 'complete' ? 'normal' : 'failure');
    const outcome = el('p', `apw-outcome apw-outcome-${view.outcome}`);
    outcome.dataset.role = 'outcome'; outcome.dataset.outcome = view.outcome;
    outcome.textContent = view.outcome === 'complete' ? '收件完成：RPC 描述和约定长度的完整字节都已收到。'
      : view.outcome === 'denied' ? '权限拒绝：正文读取未发生，拒绝结果返回原调用者。'
        : view.outcome === 'incomplete' ? '收件失败：部分字节不能当作完整正文。'
          : view.gate === 'denied' ? '停在权限关口；后续只返回拒绝结果。'
            : view.receipt === 'incomplete' ? '收件未完整，正在进入失败处理；不能显示读取成功。'
              : '本次读取尚未完成。';
    mechanism.replaceChildren(svg, outcome);
  }
  function renderFocus(view) {
    const comparison = el('div', 'apw-comparison');
    for (const [role, label, value, detail] of [
      ['selected', '用户已经选择', view.selected || '—', `当前通道修订 ${view.revision || '未分配'}`],
      ['displayed', '当前显示的详情', view.displayed || '—', view.displayed ? `仅显示与 ${view.revision} 匹配的回复` : `等待 ${view.selected || '所选对象'} 的匹配回复`],
    ]) {
      const panel = el('section', 'apw-comparison-panel');
      panel.append(el('h3', null, label)); const valueNode = el('strong', 'apw-letter', value); valueNode.dataset.role = role;
      panel.append(valueNode, el('p', null, detail)); comparison.append(panel);
    }
    const packets = el('div', 'apw-packets'); packets.setAttribute('aria-label', '读取与回复的去向');
    for (const packet of view.packets) {
      const row = el('div', `apw-packet apw-packet-${packet.status}`); row.dataset.packet = packet.letter; row.dataset.state = packet.status;
      const envelope = el('div', 'apw-envelope'); envelope.append(el('span', 'apw-envelope-icon', '✉'), doc.createTextNode(` ${packet.letter} 回复 · ${packet.revision}`));
      const route = el('div', 'apw-packet-route');
      route.textContent = {pending: '··· 在途 ···', applied: packet.current ? '──────── →' : '已用于先前的 A 详情', discarded: '──── ×', not_started: '等待读取启动'}[packet.status];
      const destination = el('strong', 'apw-packet-destination', {pending: '等候返回', applied: packet.current ? `进入详情 ${packet.letter}` : '当前已选 B', discarded: '旧修订，丢弃', not_started: '尚未发起读取'}[packet.status]);
      row.append(envelope, route, destination); packets.append(row);
    }
    const rule = el('p', 'apw-focus-rule', view.packets.some(item => item.status === 'discarded')
      ? `A 的旧回复被丢弃；用户仍选择 ${view.selected}，详情仍显示 ${view.displayed}。`
      : view.packets.filter(item => item.status === 'pending').length === 2
        ? 'A、B 同时在途；等候 B 的匹配回复，旧 A 详情不占据显示区。'
        : '响应携带发起读取时的修订；只有匹配当前选择的回复进入详情。');
    mechanism.replaceChildren(comparison, packets, rule);
  }
  function renderEvidence(step) {
    evidence.replaceChildren();
    for (const [label, value] of [['角色', `${step?.from || '未知'} → ${step?.to || '未知'}`], ['成立条件', step?.condition], ['本步请求', step?.request], ['本步结果', step?.result]]) {
      const line = el('p'); line.append(el('strong', null, `${label}：`), doc.createTextNode(text(value))); evidence.append(line);
    }
    const sources = sourcesFor(scenario, step);
    if (!sources.length) evidence.append(el('p', null, '本步骤来源缺失。'));
    for (const source of sources) {
      const item = el('section', 'apw-source');
      item.append(el('p', null, `${source.ref || '未命名来源'} · ${source.section || '段落未提供'} · ${source.line_start ? `L${source.line_start}–L${source.line_end}` : '行范围未提供'}`),
        el('p', null, `正文版本 ${source.body_version ?? '未知'} / 响应版本 ${source.response_version ?? '未知'}`),
        el('p', 'apw-hash', `SHA256 ${source.body_sha256 || '未提供'}`));
      if (source.excerpt) item.append(el('blockquote', null, source.excerpt));
      const open = button(source.frozen_ref ? '查看冻结来源' : '冻结正文入口缺失', () => invoke(onSource, source.frozen_ref), 'source');
      open.disabled = !source.frozen_ref || typeof onSource !== 'function'; if (source.frozen_ref) open.title = source.frozen_ref;
      item.append(open); evidence.append(item);
    }
  }
  function render() {
    if (destroyed || !scenario) return;
    const timeline = timelineFor(scenario, state.branch_id), view = protocolWorkbenchView(scenario, state);
    host.dataset.scenario = scenario.scenario_id || scenario.id; host.dataset.protocol = scenario.protocol_id;
    host.dataset.branch = state.branch_id; host.dataset.step = view.step?.id || ''; host.dataset.playing = playing;
    host.dataset.reducedMotion = motion.checked;
    heading.textContent = view.type === 'resource' ? '一份正文，何时才算读到了？' : '选了 B，为什么慢回来的 A 不能盖掉它？';
    branchRow.querySelectorAll('button').forEach(node => node.setAttribute('aria-pressed', String(node.dataset.branch === state.branch_id)));
    const branch = branchOptions(scenario).find(item => item.id === state.branch_id);
    branchCondition.textContent = branch?.condition || '正常读取：资源可解析，调用者有权限，正文小于返回上限。';
    if (view.type === 'resource') renderResource(view); else renderFocus(view);
    previous.disabled = state.index <= 0; next.disabled = state.index >= timeline.length - 1;
    play.disabled = !playing && next.disabled; play.textContent = playing ? '暂停' : '播放'; play.setAttribute('aria-pressed', String(playing));
    counter.textContent = `${state.index + 1} / ${timeline.length} · ${playing ? '播放中' : '已暂停'}`;
    const stepLabels = {request:'发起读取', resolve:'解析与授权', read:'读取正文字节', return:'返回原调用者', deny:'返回权限拒绝', incomplete:'拒绝不完整收件',
      'select-a':'选择 A', 'start-read-a':'启动 A 的读取', 'apply-a':'采用 A 的回复', 'select-b':'选择 B', 'start-read-b':'启动 B 的读取', 'apply-b':'采用 B 的回复', 'discard-late-a':'丢弃晚到的 A'};
    stepHeading.textContent = `${state.index + 1}. ${stepLabels[view.step?.id] || view.step?.id || '步骤缺失'}`;
    result.textContent = text(view.step?.result);
    steps.querySelectorAll('button').forEach(node => { if (+node.dataset.stepIndex === state.index) node.setAttribute('aria-current', 'step'); else node.removeAttribute('aria-current'); });
    renderEvidence(view.step);
  }
  function setScenario(nextScenario) {
    if (destroyed) return;
    if (!supportsProtocolWorkbench(nextScenario)) throw new Error('此场景没有专属协议工作台');
    const nextState = createPlayback(nextScenario); // Validate before touching the displayed scene.
    stop(); scenario = clone(nextScenario); state = nextState; error.hidden = true;
    branchRow.replaceChildren(...branchOptions(scenario).filter(option => pair(scenario) === RESOURCE || option.id !== MAIN_BRANCH).map(option => {
      const labels = pair(scenario) === RESOURCE
        ? {[MAIN_BRANCH]: '正常读取', forbidden: '没有读取权限', partial_body: '正文未收完整'}
        : {'normal-arrival': '按顺序到达', 'a-arrives-after-b': 'B 先到，A 迟到'};
      const node = button(labels[option.id] || option.label, () => changeBranch(option.id)); node.dataset.branch = option.id; return node;
    }));
    assumptionsBody.replaceChildren(el('p', null, `场景：${scenario.protocol_id} / ${scenario.scenario_id || scenario.id}`));
    for (const [label, values] of [['前提', scenario.preconditions], ['范围', scenario.limitations]]) {
      for (const value of Array.isArray(values) ? values : values ? [values] : []) assumptionsBody.append(el('p', null, `${label}：${text(value)}`));
    }
    if (scenario.concurrency) assumptionsBody.append(el('p', null, `并发依据：${scenario.concurrency.status} · ${scenario.concurrency.reason || scenario.concurrency.rule || '见已提供分支'}`));
    for (const source of scenario.source_versions || []) assumptionsBody.append(el('p', 'apw-hash', `${source.ref} · 正文 ${source.body_version ?? '未知'} / 响应 ${source.response_version ?? '未知'} · SHA256 ${source.body_sha256 || source.body?.sha256 || '未知'} · 观察 ${source.observed_at || '未知'}`));
    rebuildDirectory(); render(); notify();
  }
  function visibility() { if (doc.hidden) { stop(); render(); notify(); } }
  function motionPreference(event) { if (!motionChanged) { motion.checked = event.matches; render(); } }
  setScenario(initialScenario);
  doc.addEventListener('visibilitychange', visibility); media?.addEventListener?.('change', motionPreference);
  root.append(host);
  return {
    show() { if (!destroyed) { hidden = false; host.hidden = false; render(); } },
    hide() { if (!destroyed) { stop(); hidden = true; host.hidden = true; render(); notify(); } },
    destroy() { if (!destroyed) { stop(); destroyed = true; doc.removeEventListener('visibilitychange', visibility); media?.removeEventListener?.('change', motionPreference); host.remove(); } },
    getScene, setScenario,
  };
}
