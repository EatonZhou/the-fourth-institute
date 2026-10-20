import './scheduling-mechanisms.css';
import {createSchedulingSimulation, SCHEDULING_MECHANISMS} from './scheduling-mechanisms.model.js';

const el = (tag, cls, text) => { const n = document.createElement(tag); if (cls) n.className = cls; if (text != null) n.textContent = text; return n; };
const line = (title, text, cls = '') => { const n = el('div', `sched-line ${cls}`); n.append(el('strong', '', title), el('span', '', text)); return n; };
const arrow = (text = '↓') => el('div', 'sched-arrow', text);
const has = (scene, ...ids) => scene.trace.some(step => ids.includes(step.id));
const box = (title, text, cls = '') => { const n = el('section', `sched-domain-box ${cls}`); n.append(el('h3', '', title), el('p', '', text)); return n; };
const result = (title, text, cls = '') => { const n = box(title, text, `sched-result ${cls}`); n.dataset.result = title; return n; };
const caseNames = {quota_reserve: '额度保护阻挡', deferred: '尚未到可领取时间', capacity: '账号容量已满', dispatch_interval: '派发间隔未到', session_binding: '同一会话仍被占用'};
const samples = {available: '可用读数', percent_unknown: '百分比读数未知', balance_unknown: '余额读数未知', reserved: '明确进入保留额度', disabled: '阈值为 0，保护关闭'};

function admission(scene) {
  const n = el('div', 'sched-admission'), s = scene.state, loaded = scene.step_count > 0;
  n.append(box('待领取的执行', loaded ? '从同一准入快照检查；尚未授予 Worker 资格。' : '建立情境后，查看哪道条件挡住领取。', 'sched-ticket'));
  const gates = el('ol', 'sched-gates');
  const rows = [['quota_paused', '额度保护', 'quota_reserve'], ['deferred', '可领取时间 available_at', 'deferred'], ['capacity_full', '账号共享容量', 'capacity'], ['dispatch_wait', '账号派发间隔', 'dispatch_interval'], ['binding_busy', '同一会话 binding 互斥', 'session_binding']];
  rows.forEach(([field, label, reason], i) => {
    const gate = el('li', `sched-gate ${s.reason === reason ? 'is-blocked' : loaded && !s[field] ? 'is-clear' : ''}`);
    gate.append(el('b', 'sched-gate-number', String(i + 1)), el('span', '', label), el('strong', '', !loaded ? '未检查' : s[field] ? '阻挡' : '条件允许'));
    gates.append(gate);
  });
  n.append(gates, result(s.eligible ? '成为可选候选' : s.reason === 'unchecked' ? '等待准入结论' : '本次不领取', s.eligible ? '仍需公平选择与原子授予资格。' : s.reason === 'unchecked' ? '检查条件不会消耗公平轮次或派发间隔。' : `阻挡原因：${caseNames[s.reason] || s.reason}；公平与间隔均不推进。`, s.eligible ? 'is-clear' : ''));
  return n;
}

function fairness(scene) {
  const n = el('div', 'sched-fairness'), s = scene.state, loaded = scene.step_count > 0;
  n.append(el('p', 'sched-caption', loaded ? `两 actor 均曾派发；上次派发 ${s.last_actor}。${s.priority_case === 'A_higher' ? 'A 优先级更高，优先级先行。' : 'A / B 优先级相同。'}` : '示例队列 A1、A2 与 B1 都已满足准入；A1 早于 A2。'));
  for (const [actor, tickets] of [['A', ['A1', 'A2']], ['B', ['B1']]]) {
    const lane = el('div', 'sched-queue'); lane.append(el('b', '', `actor ${actor}`));
    tickets.forEach((id, i) => { if (i) lane.append(el('span', '', '→')); lane.append(el('span', `sched-queue-ticket ${s.candidate === id ? 'is-selected' : ''}`, id)); });
    lane.append(el('small', '', s.candidate.startsWith(actor) ? '本次候选' : '仍在队列')); n.append(lane);
  }
  n.append(arrow('候选 → 原子领取'));
  const stamp = result(s.candidate || '尚未选出候选', has(scene, 'raced') ? '资格竞态失败，候选没有成功领取。' : s.fairness_advanced ? '领取成功，再记录 actor 派发与账号节奏。' : '候选查询完成不等于领取成功。', has(scene, 'raced') ? 'is-blocked' : s.fairness_advanced ? 'is-clear' : '');
  const ledger = el('div', 'sched-two-ledgers');
  ledger.append(line('公平账 (priority, actor)', s.fairness_advanced ? '已推进本 actor 轮次' : '未推进'), line('账号节奏 capacity_key', s.dispatch_reserved ? '已记录本次派发' : '未消耗派发间隔'));
  n.append(stamp, ledger); return n;
}

function quota(scene) {
  const n = el('div', 'sched-quota'), s = scene.state, loaded = scene.step_count > 0;
  n.append(box('账号额度样本与策略', loaded ? samples[s.sample] : '先选择额度情境，建立样本与例外条件。', 'sched-sample'));
  if (loaded) n.append(line('绑定例外 / 活动豁免', `${s.override ? '本 binding 放行' : '无绑定例外'}；${s.active_exempt ? '修改前的活动执行受豁免' : '无活动豁免'}`));
  n.append(arrow('额度判断产生两条不同后果'));
  const forks = el('div', 'sched-forks');
  const decided = has(scene, 'open', 'unknown', 'reserved');
  forks.append(result('新领取入口', !decided ? '尚未完成额度判断' : s.paused ? '暂停领取' : '额度允许；其他准入条件仍需检查', s.paused ? 'is-blocked' : decided ? 'is-clear' : ''));
  forks.append(result('已在运行的执行', s.active_cancel_requested ? '已请求取消；等待 Worker 实际停止' : has(scene, 'exempt') ? '阈值修改前活动执行继续' : '本片段尚未提出取消意图', s.active_cancel_requested ? 'is-pending' : ''));
  n.append(forks, el('p', 'sched-caption', '百分比未知会暂停新领取，余额未知允许额度准入。明确 reserved 才进入活动取消 / 豁免判断；样本过期且原为 reserved 时保持保护。'));
  return n;
}

// Older pinned batches have only the original qualification slice. Keep its
// historical rendering; the richer grant view consumes only its own new fields.
function workerProtocolSlice(scene) {
  const n = el('div', 'sched-fence-view'), s = scene.state;
  const lane = el('div', 'sched-fence-lane');
  lane.append(box('Worker 资格', s.claim_generation ? `示例领取轮次 #${s.claim_generation}（非真实 token）` : '尚未建立本次领取资格', 'sched-ticket'));
  lane.append(el('div', `sched-fence ${s.error ? 'is-blocked' : ''}`, s.error ? '✕ 资格围栏拒绝' : 'owner + token\n逐次校验'));
  lane.append(box('Scheduler 持久账', `phase = ${s.phase}`, 'sched-ledger'));
  n.append(lane);
  const transactions = el('ol', 'sched-transactions');
  for (const [ids, label] of [[['claim'], '领取请求'], [['start'], '携当前 token 启动'], [['renew'], '续租'], [['checkpoint'], '完成证据持久化'], [['complete', 'cancel'], '提交终态并清资格']]) {
    const li = el('li', has(scene, ...ids) ? 'is-done' : '', label); transactions.append(li);
  }
  n.append(transactions);
  if (s.error) n.append(result(s.error, s.error === 'worker_not_active' ? 'standby 不能领取。' : '回收 / 交接已改变 owner 或 token；旧持有者的 heartbeat / finish 被拒绝，不覆盖新结果。', 'is-blocked'));
  else n.append(result(s.phase === 'cancelled' ? '取消意图胜出' : s.phase === 'completed' ? '终态已保存' : s.lease_extended ? '当前资格续租成功' : '等待持有者操作', s.phase === 'cancelled' ? '是否写入内容和绑定要另查，不能由 cancelled 推断。' : '业务 actor 权限与 Worker 领取资格分别校验。'));
  n.append(el('p', 'sched-caption', '租约时刻到达不等于 owner/token 已改变。此例的旧代拒绝发生在回收或交接改写资格之后。'));
  return n;
}

function workerProtocol(scene) {
  if (!Object.hasOwn(scene.state, 'queue_membership')) return workerProtocolSlice(scene);
  const n = el('div', 'sched-lease-protocol'), s = scene.state;
  const current = scene.current?.id, inTransaction = current === 'recover_queued';
  const terminal = ['completed', 'cancelled'].includes(s.phase);
  n.dataset.claimTransaction = inTransaction ? 'uncommitted' : 'none';
  const identity = el('p', 'sched-lease-identity');
  identity.append(el('strong', '', '同一执行'), el('code', '', s.execution_id), el('span', '', '教学身份与令牌'));
  const queue = el('section', `sched-lease-queue ${inTransaction ? 'is-pending' : ''}`);
  queue.dataset.leaseSpace = 'queue';
  queue.append(el('h3', '', inTransaction ? '队列投影 · claim 事务内' : '同一执行的队列投影'),
    el('strong', '', s.queue_membership === 'queued' ? 'queued · 待领取' : 'not_queued · 已离开待领取队列'),
    el('p', '', inTransaction ? '回收语句已重排；尚未独立提交，其他连接不能据此观察到重排。' : '由 Scheduler 的执行状态投影；进入队列仍需通过共享准入。'));
  n.append(identity, queue);
  const flow = el('ol', 'sched-transactions sched-lease-flow');
  for (const [id, text] of [['claim', 'A 请求领取'], ['grant', 'Scheduler 授予'], ['receive_claim', 'A 接纳回执'], ['start', 'A 携 token 启动']]) {
    const li = el('li', has(scene, id) ? 'is-done' : '', text); li.dataset.leaseStage = id;
    if (id === current) li.setAttribute('aria-current', 'step');
    flow.append(li);
  }
  n.append(flow);
  if (has(scene, 'claim_again')) {
    const reclaim = el('ol', 'sched-transactions sched-lease-flow'); reclaim.setAttribute('aria-label', 'B 重新领取同一执行');
    for (const [id, text] of [['claim_again', 'B 请求领取'], ['recover_queued', '事务内回收'], ['grant_again', 'B 新资格提交'], ['receive_reclaim', 'B 接纳回执'], ['start_reclaimed', 'B 携 token 启动']]) {
      const li = el('li', has(scene, id) ? 'is-done' : '', text); li.dataset.leaseStage = id;
      if (id === current) li.setAttribute('aria-current', 'step');
      reclaim.append(li);
    }
    n.append(reclaim);
  }
  const spaces = el('div', 'sched-lease-spaces');
  const ledger = el('section', `sched-lease-ledger ${inTransaction ? 'is-pending' : ''}`); ledger.dataset.leaseSpace = 'ledger';
  ledger.append(el('h3', '', 'Scheduler · 持久资格账'),
    el('p', 'sched-lease-commit', inTransaction ? 'claim 事务内部语句状态 · 尚未提交' : '已提交的执行行'));
  const values = [
    ['phase', '执行状态', s.phase], ['ledger_owner_worker_id', '持有者', s.ledger_owner_worker_id || '无'],
    ['ledger_claim_token', '持久 token', s.ledger_claim_token || '无'],
    ['lease_recorded', '租约记录', !s.lease_recorded ? '无' : s.lease_due ? '仍保留 · 已观察到期' : '已记录'],
    ['attempts', '成功领取次数', s.attempts], ['checkpoint_resumable', '恢复依据', s.checkpoint_resumable ? '已保存可恢复 checkpoint' : '本片段尚未建立可恢复 checkpoint'],
  ];
  const dl = el('dl', 'sched-lease-ledger-fields');
  for (const [key, label, value] of values) { const dd = el('dd', '', String(value)); dd.dataset.leaseField = key; dl.append(el('dt', '', label), dd); }
  ledger.append(dl);
  const workers = el('section', 'sched-lease-workers'); workers.dataset.leaseSpace = 'workers'; workers.append(el('h3', '', 'Worker · 本地凭据'));
  for (const who of ['a', 'b']) {
    const worker = s[`worker_${who}_id`], token = s[`worker_${who}_claim_token`], execution = s[`worker_${who}_execution_id`];
    const matches = !!token && worker === s.ledger_owner_worker_id && token === s.ledger_claim_token;
    const status = !token ? '尚未接纳领取回执' : inTransaction ? '保留本地副本；回收事务尚未提交'
      : matches ? '与当前持久 owner/token 匹配' : terminal ? '本地副本保留；持久资格已清除' : '旧本地副本；与当前持久资格不匹配';
    const ticket = el('div', `sched-lease-credential ${token && !matches && !inTransaction ? 'is-blocked' : matches ? 'is-clear' : ''}`);
    ticket.dataset.leaseWorker = who;
    ticket.append(el('strong', '', `Worker ${who.toUpperCase()} · ${worker}`), el('code', '', token || '—'), el('small', '', execution || '尚无本次执行 ID'), el('p', '', status));
    workers.append(ticket);
  }
  spaces.append(ledger, workers); n.append(spaces);
  const fence = el('div', `sched-lease-fence ${s.old_write_rejected ? 'is-blocked' : ''}`);
  fence.append(el('strong', '', s.old_write_rejected ? '✕ 旧请求被 owner + token 围栏拒绝' : '写入围栏 · owner_worker_id + claim_token'),
    el('p', '', s.old_write_rejected ? `A 的旧 ${current === 'stale_start' ? 'start' : 'finish'} 收到 ${s.error}；B 的持久状态、owner/token 与 attempts 保持。`
      : inTransaction ? 'B 的领取事务尚未提交；回收和新授予一起完成。' : 'start、续租与结果写入逐次校验；接纳回执只复制凭据，不转移账本写权。'));
  n.append(fence);
  let title = '领取前', detail = '队列中的执行尚未授予；Worker 本地没有本次凭据。';
  if (current === 'claim') { title = '领取请求已发出'; detail = '等待 Scheduler 授予；请求本身不产生持有资格。'; }
  else if (current === 'inactive') { title = 'standby 不获授予'; detail = '本例没有建立持久资格，也没有向 Worker 返回 token。'; }
  else if (current === 'grant' || current === 'grant_again') { title = '领取已提交，等待接纳回执'; detail = `Scheduler 已将同一执行授予 ${current === 'grant' ? 'A' : 'B'}；claimed 与 running 分开，尚未执行本次 start。`; }
  else if (current === 'receive_claim' || current === 'receive_reclaim') { title = '凭据已接纳，尚未 start'; detail = 'Worker 已有执行 ID 与 token；持久状态保持 claimed，不据此推断 Provider 已调用。'; }
  else if (current === 'start' || current === 'start_reclaimed') { title = '携已有 token 启动'; detail = 'claimed → running；start 不分配 token，也不增加成功领取次数。'; }
  else if (current === 'lease_expires') { title = '到期条件已观察，资格记录仍在'; detail = '到期时刻不自动清账；Worker 本地 deadline 围栏与 Scheduler 回收分别发生。'; }
  else if (current === 'claim_again') { title = 'B 发起领取请求'; detail = '请求不指定 execution_id；本固定场景由 Scheduler 再次选中同一执行。'; }
  else if (inTransaction) { title = '同一 claim 事务内：回收后等待新授予'; detail = '这里是内部语句停点；下一步 grant_again 才提交 B 的新 owner/token 与领取次数。'; }
  else if (s.old_write_rejected) { title = '旧写拒绝，B 继续持有'; detail = '同一 execution 使用 B 的新 token；A 的旧本地副本不能改写当前账。'; }
  else if (terminal) { title = s.phase === 'cancelled' ? '取消胜出，持久资格已清' : '终态已保存，持久资格已清'; detail = '本地 token 副本可以残留；它不再赋予写入资格。'; }
  else if (current) { title = scene.current.title; detail = scene.current.observable; }
  n.append(result(title, detail, s.old_write_rejected || current === 'inactive' ? 'is-blocked' : inTransaction ? 'is-pending' : ''),
    el('p', 'sched-caption', 'demo.* 仅为固定教学身份。领取轮次不等于随机 token 或 Worker 进程代际；重排不等于重新调用 Provider。'));
  return n;
}

function expiry(scene) {
  const n = el('div', 'sched-expiry'), s = scene.state;
  n.append(box('到期的同一 execution', scene.step_count ? `${s.phase} · ${s.resumable ? '有 resumable 恢复证据' : '无 resumable 恢复证据'}` : '先建立到期前的执行状态与恢复证据。', 'sched-ticket'), arrow('锁定后，按证据分流'));
  const tree = el('ol', 'sched-decision-tree');
  const rows = [
    ['cleanup', '仍可能有原生工作', 'queued_interrupt_only', '只进入 abandoned 清理队列'],
    ['cancel', '无原生工作，已有取消意图', 'cancelled', '收敛取消'],
    ['fail', '无原生工作且无取消意图；running 且无恢复证据', 'failed', '失败，不重放原请求'],
    ['requeue', '无原生工作且无取消意图；未开始或有恢复证据', 'queued_same_execution', '清旧资格，原 execution 重新排队'],
  ];
  for (const [id, condition, outcome, label] of rows) {
    const row = el('li', has(scene, id) ? 'is-selected' : ''); row.append(el('span', '', condition), el('b', '', '→'), el('strong', '', label)); row.dataset.expiryOutcome = outcome; tree.append(row);
  }
  n.append(tree, result('原生输入', '本回收过程没有发送新 prompt。重新排队保留原 execution 身份。', 'is-clear'));
  return n;
}

function workerSlots(scene) {
  const n = el('div', 'sched-slots'), s = scene.state, loaded = scene.step_count > 0;
  const pool = box('Worker 本地并发槽', 'active_count < max_parallel', 'sched-slot-pool');
  pool.append(el('div', `sched-slot ${loaded && !s.slot_free ? 'is-occupied' : ''}`, !loaded ? '待检查' : s.slot_free ? '有空位' : '本地已满'));
  n.append(pool, arrow(has(scene, 'claim') ? '有空位 → 向 Scheduler 领取' : '先检查本地；尚未领取'));
  const grant = result('Scheduler 共享准入', !has(scene, 'claim') ? '尚未收到领取请求' : has(scene, 'capacity') ? '账号容量不足；本地空位保留' : has(scene, 'standby') ? 'Worker standby，等待激活' : s.retired ? 'Worker 退休或登记消失，结束领取' : s.thread_started ? '成功授予本次 claim' : '等候领取结论', has(scene, 'capacity', 'retire') ? 'is-blocked' : s.thread_started ? 'is-clear' : '');
  n.append(grant, arrow(s.thread_started ? '成功领取 → 建立本地线程' : '没有领取资格 → 不创建线程'));
  const thread = box('本次执行线程', s.thread_started ? `已创建；本地 marker = ${s.slot_marker}` : '未创建', s.thread_started ? 'is-clear' : '');
  n.append(thread); return n;
}

function generation(scene) {
  const n = el('div', 'sched-generation'), s = scene.state;
  const stages = el('div', 'sched-generation-stages');
  for (const [ids, label] of [[['spawn'], '生成 standby'], [['activate', 'failed'], '健康 / 身份检查'], [['switch', 'reject'], 'Scheduler 原子切换'], [['reap', 'wait'], '旧进程退出后处理']]) stages.append(el('span', has(scene, ...ids) ? 'is-done' : '', label));
  n.append(stages);
  const table = el('table'); table.setAttribute('aria-label', '换代进程泳道');
  const head = el('thead'), hr = el('tr'); ['进程', '当前状态', '领取 / 收尾职责'].forEach(t => hr.append(el('th', '', t))); head.append(hr);
  const body = el('tbody');
  for (const [name, status, note] of [
    ['候选进程', s.candidate, s.candidate === 'active' ? '已激活，可领取' : s.candidate === 'discarded' ? '候选丢弃，不替换旧进程' : '等待身份检查和原子激活'],
    ['被替换旧进程', s.previous, s.old_claims_handled ? '进程已退出；处理本例遗留资格，等待可注销' : s.previous === 'draining' ? '停止新领取，逐项交接；尚未完成遗留处理' : '仍保持 active'],
    ['同 build 其他进程', s.same_build_peer, '未显式指定替换时，不因同 build 一并退休'],
  ]) { const row = el('tr'); row.append(el('th', '', name), el('td', `sched-process ${status === 'active' ? 'is-clear' : status === 'discarded' ? 'is-blocked' : ''}`, status), el('td', '', note)); body.append(row); }
  table.append(head, body); n.append(table);
  n.append(result(s.old_claims_handled ? '本例遗留资格已处理，注销仍须满足条件' : has(scene, 'wait') ? '旧进程仍在逐项退出' : has(scene, 'failed', 'reject') ? '候选失败，旧进程不退役' : '进程切换与资格收尾分别发生', 'Manager 管理进程生命周期；Scheduler 承担持久激活与领取资格。进程退出也不能直接当作 blocked_execution_ids 已处理。'));
  return n;
}

function handover(scene) {
  const n = el('div', 'sched-handover'), s = scene.state;
  const claim = el('div', `sched-claim-ribbon ${has(scene, 'release', 'cancel') ? 'is-released' : ''}`, has(scene, 'release', 'cancel') ? '经过释放屏障，进入持久结果处理' : '当前 Worker 继续持有领取资格'); n.append(claim);
  const prepare = box('1 · 准备交接', has(scene, 'not-ready') ? '没有恢复依据：not_ready，保留原资格' : s.prepared ? (s.keeps_turn ? '已有恢复依据，可准备解除本地跟随' : '已有恢复依据，进入需要中断的交接路径') : 'prepare 本身不释放资格，也不提交终态', has(scene, 'not-ready') ? 'is-blocked' : s.prepared ? 'is-clear' : '');
  n.append(prepare, arrow());
  const forks = el('div', 'sched-forks');
  forks.append(box('2a · 保留原 turn', '解除本地跟随，保留原 turn；仍须经过释放判断', has(scene, 'detach') ? 'is-selected' : ''), box('2b · 需要 interrupt', '提出中断要求；实际停止仍需回执', has(scene, 'interrupt') ? 'is-selected' : ''));
  n.append(forks, arrow(), box('3 · 旧控制路径返回屏障', s.old_control_returned ? '旧控制路径已返回，可以进入释放判断' : '必须等中断 / 跟随路径返回；尚未释放', s.old_control_returned ? 'is-clear' : 'sched-barrier'));
  n.append(arrow(), result('4 · 释放时重新判断取消意图', s.result === 'queued_same_execution' ? (s.keeps_turn ? '原 execution 重排，后续恢复原 turn' : '原 execution 重排；按已保存的恢复依据继续') : s.result === 'cancellation_or_abandoned_cleanup' ? '进入取消或 abandoned 清理，不宣称原生工作已经停止' : s.result === 'not_ready' ? '准备不足，保留原资格' : '仍持有资格，等待安全释放', has(scene, 'cancel') ? 'is-pending' : ''));
  return n;
}

const renderers = Object.freeze({
  'control.admission': admission, 'control.fairness': fairness, 'control.quota': quota,
  'control.worker-protocol': workerProtocol, 'control.expiry': expiry,
  'control.worker-slots': workerSlots, 'control.generation': generation, 'control.handover': handover,
});

export function createSchedulingMechanismWorkbench(root, {subject, content, ports = {}, signal, initialState} = {}) {
  const simulation = createSchedulingSimulation(content), data = simulation.getContent(), m = data.mechanism;
  const objects = new Map(data.objects.map(item => [item.id, item])), sources = new Map(data.evidence.map(item => [item.id, item]));
  let hidden = false, destroyed = false, events = new AbortController();
  const surface = el('section', 'scheduling-workbench'); surface.dataset.mechanismId = m.id; surface.setAttribute('aria-label', m.title);
  root.replaceChildren(surface);
  const active = () => !destroyed && !hidden && !signal?.aborted;
  const invoke = (name, value) => { if (active()) ports[name]?.(value); };
  function button(label, key, action, disabled = false) {
    const b = el('button', '', label); b.type = 'button'; b.dataset.schedulingAction = key; b.disabled = disabled;
    b.addEventListener('click', () => { if (active()) action(); }, {signal: events.signal}); return b;
  }
  function changed(key) { render(key); invoke('change', getScene()); }
  function render(focusKey) {
    if (destroyed) return;
    events.abort(); events = new AbortController();
    const scene = simulation.getScene();
    const header = el('header', 'sched-header'), title = el('div');
    title.append(el('p', 'sched-kicker', '本地演示 · 执行调度'), el('h2', '', m.title));
    header.append(title, button('返回上个视图', 'return', () => invoke('return')));
    const question = el('p', 'sched-question', m.question);
    const controls = el('div', 'sched-controls'), label = el('label', '', '选择情境 '), select = el('select');
    select.setAttribute('aria-label', '机制情境'); select.dataset.schedulingAction = 'case';
    for (const c of m.cases) { const option = el('option', '', caseNames[c.id] || c.title); option.value = c.id; select.append(option); }
    select.value = scene.case_id; select.addEventListener('change', () => { if (active()) { simulation.selectCase(select.value); changed('case'); } }, {signal: events.signal}); label.append(select);
    controls.append(label, button('重新开始', 'reset', () => { simulation.reset(); changed('reset'); }));
    const actions = el('div', 'sched-actions');
    actions.append(button('上一步', 'back', () => { simulation.back(); changed('back'); }, !scene.step_count));
    const next = button(scene.next ? scene.next.id === 'load' ? '建立本次情境' : scene.next.title : '情境已到达结果', 'advance', () => { simulation.advance(); changed('advance'); }, !scene.next); next.className = 'sched-primary'; actions.append(next);
    const counter = el('span', 'sched-counter', `${scene.step_count} / ${scene.total_steps}`); actions.append(counter);
    const board = el('div', 'sched-domain'); board.dataset.presentation = m.presentation; board.setAttribute('aria-label', m.question); board.append(renderers[m.id](scene));
    const observation = el('div', 'sched-observation'); observation.setAttribute('aria-live', 'polite');
    observation.append(el('strong', '', scene.current?.title || '先建立本次情境'), el('p', '', scene.outcome || scene.current?.observable || '使用固定案例的输入，从有来源的前置条件开始。'));
    const inputs = el('details', 'sched-details'); inputs.append(el('summary', '', '本情境的输入与规则'));
    const dl = el('dl'); for (const [key, value] of Object.entries(scene.inputs)) { dl.append(el('dt', '', m.input_schema.properties[key].description || key), el('dd', '', typeof value === 'boolean' ? value ? '是' : '否' : value)); } inputs.append(dl);
    const rules = el('ul'); for (const text of m.invariants || []) rules.append(el('li', '', text)); inputs.append(rules);
    const sourcePanel = el('details', 'sched-details'); sourcePanel.append(el('summary', '', '当前步骤的固定依据'));
    for (const id of scene.evidence_ids) {
      const source = sources.get(id), location = Number.isInteger(source.start_line) && Number.isInteger(source.end_line) ? ` · L${source.start_line}–${source.end_line}` : '';
      sourcePanel.append(button(`${source.title || source.symbol || source.id}${location}`, `source-${id}`, () => invoke('source', id)));
    }
    const participants = el('nav', 'sched-participants'); participants.setAttribute('aria-label', '机制参与者');
    for (const p of m.participants || []) {
      const o = objects.get(p.object_id);
      participants.append(button(o.title || o.name || p.role, `select-${o.id}`, () => invoke('select', {objectId: o.id})));
    }
    participants.append(button('查看所属对象的组成关系', 'composition', () => invoke('navigate', {kind: 'composition', objectId: m.owner_id, mechanismId: null})));
    surface.replaceChildren(header, question, controls, actions, board, observation, inputs, sourcePanel, participants);
    if (focusKey) {
      const target = [...surface.querySelectorAll('[data-scheduling-action]')].find(n => n.dataset.schedulingAction === focusKey && !n.disabled);
      (target || surface.querySelector('[data-scheduling-action="back"]'))?.focus({preventScroll: true});
    }
  }
  function getScene() { return destroyed ? null : {...simulation.getScene(), object_id: subject?.id || subject?.objectId || m.owner_id}; }
  function getViewState() {
    return destroyed ? null : {...simulation.getViewState(), scrollTop: surface.scrollTop,
      focusKey: surface.contains(document.activeElement) ? document.activeElement.dataset.schedulingAction || null : null,
      openDetails: [...surface.querySelectorAll('details')].map(detail => detail.open)};
  }
  function restoreViewState(value) {
    if (destroyed) return;
    simulation.restore(value); render();
    surface.querySelectorAll('details').forEach((d, i) => { d.open = value.openDetails?.[i] === true; });
    if (!hidden && value.focusKey) [...surface.querySelectorAll('[data-scheduling-action]')]
      .find(n => n.dataset.schedulingAction === value.focusKey && !n.disabled)?.focus({preventScroll: true});
    surface.scrollTop = Number.isFinite(value.scrollTop) ? Math.max(0, value.scrollTop) : 0;
  }
  function destroy() { if (destroyed) return; destroyed = true; events.abort(); signal?.removeEventListener('abort', destroy); surface.remove(); }
  if (signal?.aborted) destroy(); else { signal?.addEventListener('abort', destroy, {once: true}); if (initialState) restoreViewState(initialState); else render(); }
  return {show() { if (!destroyed) { hidden = false; surface.hidden = false; } }, hide() { hidden = true; surface.hidden = true; }, destroy, getScene, getViewState, restoreViewState};
}

// The adapter is shared by sixteen mechanisms; this registry owns exactly eight.
export const schedulingMechanismFactories = Object.freeze(Object.fromEntries(SCHEDULING_MECHANISMS.map(id => [id,
  (root, options) => {
    if (options?.content?.mechanism?.id !== id) throw new Error(`Scheduling factory requires ${id}`);
    return createSchedulingMechanismWorkbench(root, options);
  },
])));

// These are authored object entries, not the first item in mechanism_ids. Non-leaf
// Worker / Scheduler objects deliberately retain their parent's composition view.
export const schedulingObjectDefaults = Object.freeze({
  'worker-lease': 'control.worker-protocol',
  'worker.claim-loop': 'control.worker-slots',
  'execution.scheduling.admission': 'control.admission',
  'execution.scheduling.leases': 'control.worker-protocol',
  'execution.scheduling.accounts': 'control.quota',
  'worker.manager': 'control.generation',
});
const objectModes = Object.freeze({
  'worker-lease': ['control.worker-protocol', 'control.expiry', 'control.handover', 'control.generation'],
  'worker.claim-loop': ['control.worker-slots', 'control.worker-protocol', 'control.handover'],
  'execution.scheduling.admission': ['control.admission', 'control.fairness'],
  'execution.scheduling.leases': ['control.worker-protocol', 'control.expiry'],
  'execution.scheduling.accounts': ['control.quota'],
  'worker.manager': ['control.generation'],
});
const modeLabels = Object.freeze({
  'control.worker-protocol': '领取与资格围栏', 'control.expiry': '到期回收',
  'control.handover': '安全交接', 'control.generation': '进程换代',
  'control.worker-slots': '空槽与领取', 'control.admission': '准入条件',
  'control.fairness': '候选公平选择', 'control.quota': '额度保护',
});

export function createSchedulingObjectWorkbench(root, {subject, content, ports = {}, signal, initialState} = {}) {
  const objectId = subject?.objectId || subject?.id;
  if (subject?.objectId && subject?.id && subject.objectId !== subject.id) throw new Error('Scheduling subject identity mismatch');
  const defaultId = schedulingObjectDefaults[objectId];
  if (!defaultId) throw new Error(`No scheduling object entry for ${objectId}`);
  const data = structuredClone(content);
  const object = data?.objects?.find(o => o.id === objectId);
  if (!object || object.child_ids?.length || !Array.isArray(object.mechanism_ids)) throw new Error('Scheduling object must be an authorized leaf or protocol');
  if (!Array.isArray(data.mechanisms)) throw new Error('Scheduling object entry requires authorized content.mechanisms');
  const available = new Map();
  for (const m of data.mechanisms) {
    if (!m?.id || available.has(m.id)) throw new Error('Duplicate or invalid scheduling mode');
    available.set(m.id, m);
  }
  const ids = objectModes[objectId].filter(id => object.mechanism_ids.includes(id) && available.has(id));
  if (!ids.includes(defaultId)) throw new Error(`Required default mechanism is unavailable: ${defaultId}`);
  const modeContent = id => ({...data, mechanism: available.get(id)});
  // Each mode must have its own authorized evidence and endpoints before mounting.
  ids.forEach(id => createSchedulingSimulation(modeContent(id)));
  const entryId = subject?.mechanismId || defaultId;
  if (!ids.includes(entryId)) throw new Error(`Unsupported scheduling entry mode: ${entryId}`);
  function validatedState(value) {
    if (value?.schema !== 'scheduling-object-view.v1' || value.objectId !== objectId || value.batchKey !== data.batchKey || !ids.includes(value.activeMode) || !value.modes || typeof value.modes !== 'object' || Array.isArray(value.modes)) throw new Error('Invalid or stale scheduling object state');
    const copy = structuredClone(value);
    for (const [id, state] of Object.entries(copy.modes)) {
      if (!ids.includes(id)) throw new Error(`Unauthorized saved scheduling mode: ${id}`);
      createSchedulingSimulation(modeContent(id)).restore(state);
    }
    if (!copy.modes[copy.activeMode]) throw new Error('Active scheduling mode state missing');
    return copy;
  }
  const restored = initialState ? validatedState(initialState) : null;
  let modes = restored?.modes || {}, activeId = restored?.activeMode || entryId;
  let child, hidden = false, destroyed = false, lastChildFocus = null;
  const events = new AbortController();
  const shell = el('section', 'scheduling-object-workbench'); shell.dataset.objectId = objectId;
  shell.setAttribute('aria-label', object.title || object.name || objectId);
  const heading = el('div', 'sched-object-heading', object.title || object.name || objectId);
  const navigation = el('nav', 'sched-object-modes'); navigation.setAttribute('aria-label', '本对象的机制模式');
  const body = el('div', 'sched-object-body');
  const active = () => !destroyed && !hidden && !signal?.aborted;
  const invoke = (name, value) => { if (active()) ports[name]?.(value); };
  const modeButtons = new Map();
  function remember() {
    if (!child) return;
    const state = child.getViewState();
    if (state) modes[activeId] = {...state, focusKey: state.focusKey || lastChildFocus};
  }
  function mount(id, state, restoreFocus) {
    child?.destroy(); activeId = id; lastChildFocus = state?.focusKey || null;
    child = createSchedulingMechanismWorkbench(body, {subject: {objectId}, content: modeContent(id),
      ports: Object.fromEntries(['select', 'navigate', 'source', 'change', 'return'].map(name => [name, value => invoke(name, value)]))});
    if (hidden || !restoreFocus) child.hide();
    if (state) child.restoreViewState(state);
    if (!hidden) child.show();
    for (const [modeId, button] of modeButtons) button.setAttribute('aria-pressed', String(modeId === id));
    shell.dataset.activeMode = id;
  }
  for (const id of ids) {
    const button = el('button', '', modeLabels[id]); button.type = 'button'; button.dataset.schedulingMode = id;
    button.addEventListener('click', () => {
      if (!active() || activeId === id) return;
      remember(); mount(id, modes[id], false); button.focus({preventScroll: true}); invoke('change', getScene());
    }, {signal: events.signal});
    navigation.append(button); modeButtons.set(id, button);
  }
  // The lifecycle has its own author/factory. Expose an explicit parent route only
  // when this very object declares it and the parent supplied it as authorized.
  if (object.mechanism_ids.includes('execution.lifecycle') && available.has('execution.lifecycle')) {
    const link = el('button', 'sched-object-continuation', '查看完整执行链'); link.type = 'button'; link.dataset.schedulingContinuation = 'execution.lifecycle';
    link.addEventListener('click', () => invoke('navigate', {kind: 'mechanism', objectId, mechanismId: 'execution.lifecycle'}), {signal: events.signal});
    navigation.append(link);
  }
  shell.addEventListener('focusin', event => {
    if (body.contains(event.target) && event.target.dataset.schedulingAction) lastChildFocus = event.target.dataset.schedulingAction;
  }, {signal: events.signal});
  shell.append(heading, navigation, body); root.replaceChildren(shell);
  function getScene() { return destroyed ? null : child.getScene(); }
  function getViewState() {
    if (destroyed) return null;
    remember();
    return {schema: 'scheduling-object-view.v1', objectId, batchKey: data.batchKey, activeMode: activeId,
      modes: structuredClone(modes), focusMode: navigation.contains(document.activeElement) ? document.activeElement.dataset.schedulingMode || null : null,
      focusContinuation: navigation.contains(document.activeElement) && document.activeElement.dataset.schedulingContinuation === 'execution.lifecycle'};
  }
  function restoreViewState(value) {
    if (destroyed) return;
    const saved = validatedState(value);
    modes = saved.modes; mount(saved.activeMode, modes[saved.activeMode], !saved.focusMode && !saved.focusContinuation);
    if (!hidden) {
      if (saved.focusMode) modeButtons.get(saved.focusMode)?.focus({preventScroll: true});
      if (saved.focusContinuation) navigation.querySelector('[data-scheduling-continuation]')?.focus({preventScroll: true});
    }
  }
  function destroy() { if (destroyed) return; destroyed = true; events.abort(); child?.destroy(); signal?.removeEventListener('abort', destroy); shell.remove(); }
  if (signal?.aborted) destroy();
  else {
    signal?.addEventListener('abort', destroy, {once: true});
    if (restored) restoreViewState(restored); else mount(activeId, null, false);
  }
  return {show() { if (!destroyed) { hidden = false; shell.hidden = false; child.show(); } },
    hide() { if (!destroyed) { hidden = true; shell.hidden = true; child.hide(); } },
    destroy, getScene, getViewState, restoreViewState};
}

export const schedulingObjectFactories = Object.freeze(Object.fromEntries(Object.keys(schedulingObjectDefaults).map(objectId => [objectId,
  (root, options) => {
    if ((options?.subject?.objectId || options?.subject?.id) !== objectId) throw new Error(`Scheduling object factory requires ${objectId}`);
    return createSchedulingObjectWorkbench(root, options);
  },
])));
