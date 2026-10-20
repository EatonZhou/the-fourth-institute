// Pure, local explanations of frozen scenario data. Nothing here calls a business API.
export const MAIN_BRANCH = '$main';
const copy = value => value == null ? value : JSON.parse(JSON.stringify(value));
const scenarioId = scenario => scenario?.scenario_id || scenario?.id || '';
const labels = {
  'normal-arrival': '正常到达', 'a-arrives-after-b': '并发：B先返回，A晚到',
  confirmed: '确认并提交', rejected: '拒绝并保留草稿', 'activity-timeout': '活动预算耗尽',
  forbidden: '读取权限被拒绝', partial_body: '正文传输不完整',
};

export function validateScenario(scenario) {
  if (!scenario || scenario.status !== 'simulated' || !scenarioId(scenario)) {
    throw new TypeError('播放器只接受具名的 simulated 场景');
  }
  const participants = new Set();
  for (const item of scenario.participants || []) {
    if (!item.id || participants.has(item.id)) throw new TypeError('参与角色 ID 缺失或重复');
    participants.add(item.id);
  }
  const checkSteps = steps => {
    const ids = new Set();
    for (const step of steps || []) {
      if (!step.id || ids.has(step.id)) throw new TypeError('同一路径的步骤 ID 缺失或重复');
      if (!participants.has(step.from) || !participants.has(step.to)) throw new TypeError('步骤引用未知参与角色');
      ids.add(step.id);
    }
    return ids;
  };
  const common = checkSteps(scenario.steps);
  const branches = new Set([MAIN_BRANCH]);
  for (const branch of scenario.branches || []) {
    if (!branch.id || branches.has(branch.id)) throw new TypeError('分支 ID 缺失或重复');
    if (!common.has(branch.at_step)) throw new TypeError('分支没有有效的公共步骤锚点');
    branches.add(branch.id);
    checkSteps(branch.steps);
  }
  return scenario;
}

export function branchOptions(scenario) {
  const branches = scenario.branches || [];
  const hasContinuation = branches.some(branch => branch.kind !== 'exception');
  return [
    { id: MAIN_BRANCH, label: hasContinuation ? '公共步骤（后续待选）' : '主路径', condition: '' },
    ...branches.map(branch => ({ id: branch.id, label: branch.title || labels[branch.id] || branch.id,
      condition: branch.condition || '', kind: branch.kind })),
  ];
}

export function defaultBranch(scenario) {
  return (scenario.branches || []).find(branch => branch.kind !== 'exception')?.id || MAIN_BRANCH;
}

export function timelineFor(scenario, branchId = defaultBranch(scenario)) {
  const branch = (scenario.branches || []).find(item => item.id === branchId);
  if (branchId !== MAIN_BRANCH && !branch) throw new RangeError('未知场景分支');
  const common = scenario.steps || [];
  const pivot = branch ? common.findIndex(step => step.id === branch.at_step) : common.length - 1;
  const timeline = common.slice(0, pivot + 1).map(step => ({ ...copy(step), phase: 'common' }));
  if (branch) {
    const at = timeline.at(-1);
    at.branch_condition = branch.condition;
    // These two resource branches fail during the pivot operation; the normal
    // pivot's successful result must not be shown as having occurred.
    if (scenarioId(scenario) === 'resident-read-small-resource' && branch.kind === 'exception') {
      at.result = '该操作进入所选异常条件；接下来查看失败结果。';
      at.condition = branch.condition;
    }
    timeline.push(...(branch.steps || []).map(step => ({ ...copy(step), phase: 'branch' })));
  }
  return timeline.map((step, index) => ({ ...step, index,
    key: JSON.stringify([scenarioId(scenario), branchId, step.phase, step.id]) }));
}

export function createPlayback(scenario, branchId = defaultBranch(scenario)) {
  validateScenario(scenario);
  return { scenario_id: scenarioId(scenario), branch_id: branchId,
    index: timelineFor(scenario, branchId).length ? 0 : -1, status: 'simulated' };
}

export function movePlayback(scenario, state, delta) {
  const end = timelineFor(scenario, state.branch_id).length - 1;
  return { ...state, status: 'simulated', index: end < 0 ? -1 : Math.max(0, Math.min(end, state.index + delta)) };
}

export function selectPlaybackBranch(scenario, branchId) {
  // Restart the local explanation. No branch inherits a previous terminal state.
  return createPlayback(scenario, branchId);
}

export function sourcesFor(scenario, step) {
  const refs = step?.source_refs || scenario.source_refs || [];
  return refs.map(entry => {
    const source = typeof entry === 'string' ? { ref: entry } : copy(entry);
    const version = (scenario.source_versions || []).find(item => item.ref === source.ref) || {};
    const snapshot = source.snapshot_address || version.snapshot_address || version.body?.address ||
      (source.ref?.includes('/objects/') ? source.ref : null);
    const first = Number(source.line_start);
    const last = Number(source.line_end || source.line_start);
    const hasLines = Number.isInteger(first) && first > 0 && Number.isInteger(last) && last >= first;
    const anchor = hasLines ? `#L${first}${last > first ? `-L${last}` : ''}` : '';
    return { ...copy(version), ...source, snapshot_address: snapshot,
      body_sha256: source.body_sha256 || version.body_sha256 || version.body?.sha256 || null,
      frozen_ref: snapshot ? `${snapshot.split('#')[0]}${anchor || (snapshot.includes('#') ? `#${snapshot.split('#')[1]}` : '')}` : null,
      line_start: hasLines ? first : null, line_end: hasLines ? last : null };
  });
}

// State is replayed from the selected path on every move, so backtracking has
// exactly the same meaning as reaching that step for the first time.
export function explainState(scenario, state) {
  const steps = timelineFor(scenario, state.branch_id).slice(0, state.index + 1);
  if (scenarioId(scenario) === 'focus-revision-order') {
    const result = { type: 'revision', status: 'simulated', current_revision: null,
      pending_reads: [], visible_detail: null, discarded: [], received_focus_republished: false };
    for (const step of steps) {
      if (step.id === 'select-a') result.current_revision = 'rA';
      if (step.id === 'select-b') result.current_revision = 'rB';
      if (step.id === 'start-read-a') result.pending_reads.push('rA');
      if (step.id === 'start-read-b') result.pending_reads.push('rB');
      if (['apply-a', 'apply-b', 'discard-late-a'].includes(step.id)) {
        const revision = step.id === 'apply-b' ? 'rB' : 'rA';
        result.pending_reads = result.pending_reads.filter(item => item !== revision);
        if (revision === result.current_revision) result.visible_detail = revision === 'rA' ? 'A' : 'B';
        else result.discarded.push(revision);
      }
    }
    return result;
  }
  if (scenarioId(scenario) === 'feedback-confirmation-budget') {
    const fixture = scenario.budget_example;
    if (!fixture || !Number.isFinite(fixture.initial_ms) || !Number.isFinite(fixture.active_before_pending_ms)) {
      return { type: 'budget', status: 'simulated', evidence_missing: true };
    }
    const result = { type: 'budget', status: 'simulated', initial_ms: fixture.initial_ms,
      remaining_ms: fixture.initial_ms, confirmation_wait_ms: 0, timer: 'inactive',
      submission_count: 0, draft_retained: true, delivery: 'not_submitted', reply: 'none', outcome: null };
    for (const step of steps) {
      if (step.id === 'request-confirmation') result.timer = 'active';
      if (step.id === 'pending') {
        result.remaining_ms = Math.max(0, fixture.initial_ms - fixture.active_before_pending_ms);
        result.timer = 'paused';
      }
      if (step.id === 'waiting') result.confirmation_wait_ms = fixture.confirmation_wait_ms ?? null;
      if (['resume-confirmed', 'resume-before-timeout'].includes(step.id)) result.timer = 'active';
      if (step.id === 'resume-before-timeout') { result.delivery = 'unknown'; result.submission_count = null; }
      if (step.id === 'submit-once') { result.submission_count = 1; result.delivery = 'submitted'; }
      if (step.id === 'receipt') { result.delivery = 'receipt'; result.timer = 'settled'; }
      if (step.id === 'reply') result.reply = 'native_chat';
      if (step.id === 'reject') { result.timer = 'settled'; result.outcome = 'rejected'; }
      if (step.id === 'exhaust-budget') {
        result.remaining_ms = 0; result.timer = 'expired'; result.outcome = 'projection.request_timeout';
        result.delivery = 'unknown'; result.submission_count = null;
      }
    }
    return result;
  }
  return null;
}

export function sceneFor(scenario, state) {
  const timeline = timelineFor(scenario, state.branch_id);
  const step = timeline[state.index];
  return { status: 'simulated', protocol_id: scenario.protocol_id, scenario_id: scenarioId(scenario),
    branch_id: state.branch_id, step_id: step?.id || null, step_key: step?.key || null,
    step_index: state.index, step_count: timeline.length, condition: step?.condition || null,
    source_versions: copy(scenario.source_versions || []), source_refs: sourcesFor(scenario, step),
    explanation: explainState(scenario, state) };
}
