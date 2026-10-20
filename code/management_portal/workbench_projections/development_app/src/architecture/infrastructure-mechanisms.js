import './infrastructure-mechanisms.css';
import {renderSubscriber} from './infrastructure-mechanisms.subscriber.js';
import {infrastructureObjectEntries, resolveInfrastructureEntry} from './infrastructure-mechanisms.entries.js';
export {infrastructureObjectEntries, resolveInfrastructureEntry} from './infrastructure-mechanisms.entries.js';
import {createInfrastructureSimulation, infrastructureAdapters} from './infrastructure-mechanisms.model.js';

const el = (tag, cls, text) => { const n = document.createElement(tag); if (cls) n.className = cls; if (text != null) n.textContent = text; return n; };
const box = (...children) => { const n = el('div'); n.append(...children); return n; };
const group = (cls, ...children) => { const n = box(...children); n.className = cls; return n; };
const paragraph = text => el('p', 'infra-caption', text);
function panel(title, value, note = '', tone = '') {
  return group(`infra-panel ${tone}`, el('span', '', title), el('strong', '', value), el('small', '', note));
}
function flow(...items) { return group('infra-flow', ...items.flatMap((item, i) => i ? [el('span', 'infra-arrow', '→'), item] : [item])); }
function table(headings, rows) {
  const t = el('table', 'infra-table'), head = el('thead'), body = el('tbody'), tr = el('tr');
  headings.forEach(s => tr.append(el('th', '', s))); head.append(tr);
  rows.forEach(row => { const r = el('tr'); row.forEach(v => r.append(el('td', '', v))); body.append(r); });
  t.append(head, body); return t;
}
function tokens(values, selected = [], gap = false) {
  return group('infra-token-row', ...values.map(v => el('span', `infra-token ${selected.includes(v) ? 'is-selected' : ''} ${gap ? 'is-gap' : ''}`, String(v))));
}
const yes = (value, positive, negative) => value ? positive : negative;
const text = (value, map) => map[value] ?? String(value);
const observed = (scene, id) => scene.events.includes(id);

function refresh({state: s}) {
  return box(flow(panel('领域数据', yes(s.committed, '已提交新值', '原值'), '由业务领域保存', s.committed ? 'is-good' : ''),
    panel('变更提示', yes(s.connected, '连接可用', '连接中断'), '提示不保存业务历史', !s.connected ? 'is-warn' : ''),
    panel('浏览器视图', text(s.view, {old: '仍是旧视图', current: '已读到当前快照', unavailable: '该对象不可见'}), '重新读取时再次鉴权', s.view === 'current' ? 'is-good' : '')),
    group('infra-pair', panel('可见性过滤', yes(s.visible, '允许展示', '未展示对象')), panel('恢复方式', yes(s.snapshot_required, '必须重新读取快照', '无快照请求挂起'))),
    paragraph('提交与页面更新分开观察。重连会要求快照；只有重新读取成功，右侧视图才变为当前值。'));
}
function publishBuffer({state: s}) {
  return box(group('infra-pair', panel('业务调用线程', s.result === 'rejected' ? '入队被拒绝' : s.result === 'pending' ? '等待本地演示' : '已返回入队结果', '后台连接与 flush 不阻塞业务提交'),
    panel('后台发送器', text(s.result, {pending: '等待提示', queued: '准备发送', retry_wait: '关闭故障连接，等待重连', sent: '已完成 flush', rejected: '没有入队'}), '', s.result === 'retry_wait' ? 'is-warn' : '')),
    group('infra-queue', el('h3', '', '有界发送队列'), tokens(s.queued ? ['提示'] : ['空']), el('small', '', `本情境已发送 ${s.published} · 已拒绝 ${s.dropped}`)),
    paragraph('重连重试时，上一份提示可能已经抵达。重复提示促使重读，不会重新执行原业务。'));
}
function hubCursor({state: s}) {
  return box(group('infra-track', group('infra-track-label', el('b', '', '本进程通知序号'), el('span', '', `最新 ${s.seq}`)), tokens([s.cursor, ...(s.seq !== s.cursor ? [s.seq] : [])], [s.cursor], s.snapshot_required)),
    group('infra-pair', panel('订阅者游标', String(s.cursor), '只在当前进程的保留窗口中有意义'),
      panel('恢复决定', s.snapshot_required ? '窗口已有缺口：读快照' : s.callback_failed ? '回调失败：订阅自行恢复' : s.cursor === s.seq ? '目前已追上' : '等待后续通知', '', s.snapshot_required || s.callback_failed ? 'is-warn' : '')),
    paragraph('通知已经保存的序号，不因某个订阅回调失败而回滚。缓存不承担持久历史账本。'));
}
function directory({state: s}) {
  return box(flow(panel('目录监听', yes(s.ready, '监听可用', '监听失败'), text(s.hint, {none: '尚无提示', path: '路径变化', overflow: '路径提示溢出'}), !s.ready ? 'is-stop' : ''),
    panel('领域投影', s.projection === 'dirty' ? '需要重新判断' : '没有待重读标记', '事件不直接成为业务记录'),
    panel('领域拥有者重读', s.rescanned ? '已重新扫描并比较' : '尚未扫描', '', s.rescanned ? 'is-good' : '')),
    paragraph('路径序列丢失时扩大重读范围；不能用残缺的 inotify 提示补造完整业务顺序。'));
}
function releasePublication({state: s}) {
  return box(panel('同一次发布 · 当前活动代码版本', s.active_commit === 'previous' ? '仍是原活动版本' : s.active_commit,
    s.active_commit === 'previous' ? '本情境尚未确认目标版本已成为活动版本' : '完成事实必须与这个活动版本匹配'),
    table(['本地 Release', '远端 push', '完成事实'], [[text(s.local_status, {pending: '待切换与健康检查', completed: '本地已完成', failed: '本地失败'}),
    text(s.push_status, {pending: '尚未观察到结果', ok: '远端成功', failed: '远端异常已返回'}), s.publication_recorded ? '已记录完成发布' : '尚未记入']]),
    group('infra-response', el('b', '', s.publication_recorded ? '后续消费者能够读取这次本地发布事实' : '只有本地完成条件成立，才能记录完成事实'),
      paragraph(s.push_status === 'failed' ? '此情境已收到远端异常，结果中单独保留；已确认的本地状态继续有效。' : '未观察到远端结果时保持待观察，不由等待推出失败。')),
    paragraph(s.publication_recorded ? '后续核对需读取这个发布事实所指向的代码版本；本情境没有给出具体发布序号。' : '下一份完成依据需同时确认本地结果、活动版本和发布记录条件。'));
}
function requests({state: s}) {
  return box(group('infra-pair', panel('正在处理的请求版本', String(s.processing_revision), '确认时仍必须匹配此版本'), panel('当前待办版本', String(s.revision), s.components.join(' + '), s.revision !== s.processing_revision ? 'is-warn' : '')),
    el('div', 'infra-response', s.retained ? '请求仍保留，等待对应版本被处理' : '精确确认通过，已移除该请求'),
    paragraph(s.failed_token === 'none' ? '处理中新增组件会合并到新版本。旧确认不能删除新待办。' : '动作失败保留诊断 token；同一失败不会造成紧循环，新版本可再处理。'));
}
function generation({state: s}) {
  return box(table(['组件', '当前标记', '本情境范围'], [['Web', s.web_build === 'old-web' ? '原内容版本' : '选定的新内容版本', s.accepted ? '已接受选择' : '选择未接受'],
    ['Worker', s.worker_build === 'old-worker' ? '原内容版本' : s.worker_build, '未随 Web 更新']]),
    paragraph('写入版本标记与确认进程健康是两个步骤。该机制只展示内容版本激活，健康确认由协调器负责。'));
}
const stopsBeforeCleanup = scene => scene.mechanismId === 'infra.mechanism.deployment-handoff' && scene.complete && scene.current?.id === 'unsupported' && scene.state.lock === 'held';
const cleanupShown = scene => scene.mechanismId === 'infra.mechanism.deployment-handoff' && observed(scene, 'release_operation_lock')
  || scene.mechanismId === 'infra.mechanism.tau-resume' && observed(scene, 'release_module_slot');
function deployment(scene) {
  const s = scene.state, selection = scene.inputs.selection, lineage = observed(scene, 'lineage');
  const lane = selection === 'resources+lineage' ? flow(panel('ResourceService', s.resource_ready ? '健康已确认' : '等待恢复', '组合选择时先停止 Lineage'),
    panel('SocietyService', lineage ? '已恢复选定代次' : '等待 Resource 健康')) : selection === 'worker' ? flow(panel('显式 Worker 请求', s.worker_status === 'rejected' ? '能力条件不足，被拒绝' : observed(scene, 'worker') ? '换代已请求' : '等待能力核验与请求'),
    panel('新身份健康门', s.result === 'ready' ? '新 worker_id 已确认' : '尚未确认', '不会隐式改用杀进程')) : flow(panel('组件选择', observed(scene, 'activate') ? '已激活选定标记' : '等待选择激活'),
    panel('Web 显示窗口', s.notice_seconds ? `${s.notice_seconds} 秒倒计时已安排` : '本步骤尚未安排', '本地演示不等待真实倒计时'));
  const lockLabel = observed(scene, 'release_operation_lock') ? '退出清理 · 实例操作锁已释放'
    : scene.current?.id === 'unsupported' && s.lock === 'held' ? '抛错现场 · 实例操作锁仍持有'
      : s.lock === 'held' ? '实例操作锁 · 当前步骤已取得' : '实例操作锁 · 当前未持有';
  return box(el('div', 'infra-lock', lockLabel), lane,
    el('div', 'infra-response', text(s.result, {pending: '版本激活后，仍要等待对应健康门', ready: '该选择集合的健康门已确认', unavailable: '本次换代不可用，保留诊断'})),
    ...(scene.current?.id === 'unsupported' ? [paragraph(stopsBeforeCleanup(scene)
      ? '当前冻结路径停在抛错现场，尚未展示退出清理。'
      : '当前停在拒绝现场。继续下一步，观察退出作用域时的锁释放。')] : []),
    ...(observed(scene, 'release_operation_lock') ? [paragraph('锁释放结束本次受保护操作；换代仍被拒绝，健康门尚未确认。')] : []),
    paragraph('每个情境只解释自己的选择集合。未选服务不会被自动放入一条全局重启顺序。'));
}
function publicationLedger({state: s}) {
  return box(table(['发布事实的身份', '序号观察'], [['提交 + 开始时间', `当前最大序号 ${s.last_seq}`], ['重复同一次发布', '复用已有事实'], ['同提交重新发布', '新的开始时间形成不同发布身份']]),
    group('infra-pair', panel('完成条件', s.eligible ? '允许记账' : '尚未满足或被拒绝'), panel('本次结果', s.returned_seq ? `返回序号 ${s.returned_seq}` : '没有返回序号', s.inserted ? '新增事实' : '没有新增事实')));
}
function releaseCursor({state: s}) {
  const values = Array.from({length: Math.min(12, Math.max(0, s.latest - s.cursor))}, (_, i) => s.cursor + i + 1);
  return box(group('infra-track', group('infra-track-label', el('b', '', `已有游标 ${s.cursor}`), el('b', '', `账本最新 ${s.latest}`)),
    tokens(values.length ? values : [s.latest], s.selected)),
    group('infra-pair', panel('本次候选', s.selected.length ? s.selected.join('、') : '没有补放候选', '首次绑定从最新序号开始等待'),
      panel('核对报告', s.audit_commit === 'none' ? '尚无成功核对观察' : '绑定所选事件的提交', '消费者失败不回滚已经完成的发布')),
    paragraph('逐条追赶保留所有待处理事实；合并追赶只选择最新事实。这里的选择不等于消费已经成功。'));
}
function rpc(scene) {
  const s = scene.state, envelope = el('section', 'infra-envelope'), dl = el('dl');
  for (const [a, b] of [['目标', '对应服务的 socket'], ['身份', 'actor / subject'], ['防重放', 'issued_at + nonce'], ['完整性', '请求内容的 HMAC']]) dl.append(el('dt', '', a), el('dd', '', b));
  envelope.append(el('h3', '', '请求信封'), dl, el('small', '', '只解释字段用途，不生成密钥或真实签名。'));
  const gates = group('infra-gates', el('div', `infra-gate ${s.signature_valid ? 'is-good' : observed(scene, 'reject') ? 'is-stop' : ''}`, `签名 / 时窗 / nonce：${s.signature_valid ? '通过' : observed(scene, 'reject') ? '拒绝' : '待验证'}`),
    el('div', `infra-gate ${s.authorized ? 'is-good' : observed(scene, 'deny') ? 'is-stop' : ''}`, `业务授权：${s.authorized ? '通过' : observed(scene, 'deny') ? '拒绝' : '尚未放行'}`),
    el('div', `infra-gate ${s.dispatched ? 'is-good' : ''}`, s.dispatched ? '已进入具名操作' : '尚未进入具名操作'));
  return box(group('infra-pair', envelope, gates), group(`infra-response ${s.commit_state === 'known_response' ? 'is-known' : ''}`,
    el('strong', '', text(s.commit_state, {not_committed: '客户端：尚未派发或已被前置拒绝', unknown: '客户端：提交结果未知', known_response: '客户端：收到匹配响应'})),
    paragraph(s.commit_state === 'unknown' ? '响应缺失不能说明操作未执行。先恢复或查询具名操作的结果，不能直接重放。' : '验签确认请求来源，业务授权独立决定能否执行。')));
}
function admission({state: s}) {
  return box(group('infra-lock', el('span', '', `配置版本 ${s.revision}`), el('strong', '', s.paused ? '新 Run 准入已暂停' : '新 Run 准入开放'), el('span', '', `目标 ${s.target}`)),
    group('infra-pair', panel('新 Run', s.new_run === 'persisted' ? '已经落盘' : '尚未创建', '共享锁覆盖准入检查到创建完成'),
      panel('已有 Run', s.existing_run === 'recovering' ? '同一运行正在恢复' : '原运行继续', '暂停新建不会取消已有运行')),
    paragraph(s.conflict ? '本次配置更新被拒绝，原目标与准入状态保留。' : '换绑前后保持暂停；新配置确认后再显式开放准入。'));
}
function audit({state: s}) {
  return box(group('infra-pair', panel('代码观察', s.code_commit === 'none' ? '未固定' : '已读取固定提交', '源码和模块输入来自同一提交'),
    panel('Design 观察', s.design_snapshot === 'unobserved' ? '尚未观察' : '逐份记录实际版本', '跨文档观察不伪装成事务快照')),
    table(['核对过程', '内容结论'], [[s.audit_failed ? '过程失败，报告不完整' : `当前退出码 ${s.exit_code}`, s.has_deviations ? '发现具体偏离' : '当前步骤未记录偏离']]),
    paragraph('过程成功且报告有偏离是合法组合。报告提供具体规则与依据，由负责者判断需要改代码还是设计。'));
}
function validation({state: s}) {
  return box(flow(panel('计划', s.plan === 'none' ? '尚未建立' : '可执行且来源已固定'), panel('准入', s.admitted ? '允许创建' : '尚未放行'),
    panel('持久 Run', text(s.run, {none: '没有记录', created: '已经创建', error: '保留启动错误记录'})), panel('独立执行器', text(s.executor, {absent: '尚未启动', running: '正在执行', failed: '启动失败'}))),
    paragraph('先重建选择计划，再通过准入创建持久 Run。执行器启动失败也保留原 Run 的责任记录。'));
}
function catalog({state: s}) {
  return box(flow(panel('声明与人工编辑', s.catalog_entry === 'merged' ? '已形成目录投影' : '等待投影'),
    panel('源文件与入口', s.digest === 'none' ? '尚无有效源摘要' : '来源摘要已核对', s.diagnostics.length ? '存在无效或重复条目' : ''),
    panel('进入可执行计划', s.included ? '允许进入' : '未进入', '', s.included ? 'is-good' : '')),
    paragraph('目录里有标题并不能证明实现存在。无效入口保留诊断，并从可执行集合排除。'));
}
function plan({state: s}) {
  return box(group('infra-pair', panel('展开后的计划条目', String(s.entries), '按 runner / profile / 实现展开'),
    panel('来源约束', s.source_frozen ? '随条目固定' : '尚未固定')),
    table(['执行一致性', '计划身份'], [[s.executable ? '允许执行' : text(s.diagnostic, {none: '待检查', mixed_runners: 'TAU 与 local 混用，被拒绝', tau_fail_fast_unsupported: 'TAU 不支持 fail-fast'}), s.plan_id === 'none' ? '尚无可执行计划身份' : '由规范计划内容生成']]),
    paragraph('同一组选择先展开为明确执行条目，再形成可复核计划。配置不受支持时不能靠标题相同继续启动。'));
}
function run({state: s}) {
  return box(el('div', 'infra-run-id', `持久记录 · ${s.run_id}`), group('infra-pair', panel('运行状态', text(s.status, {running: '运行中', cancelling: '已请求取消，等待执行侧收敛'})),
    panel('执行器', text(s.executor, {absent: '当前不存在', launching: '按原身份恢复中', running: '继续原记录'}))),
    paragraph(s.launch_identity === 'none' ? '取消先写入请求状态，完成清理由执行侧收敛。' : '恢复继续使用同一个 Run 和稳定执行器身份，保留已有结果，不创建另一轮掩盖失败。'));
}
function lifetime({state: s}) {
  return box(group('infra-lifetime', el('b', '', '浏览器 HTTP'), el('div', `infra-bar ${s.http_connected ? '' : 'is-closed'}`, s.http_connected ? '连接仍在' : '连接已结束')),
    group('infra-lifetime', el('b', '', '独立执行器'), el('div', `infra-bar ${s.launch_status === 'failed' ? 'is-failed' : ''}`, text(s.launch_status, {pending: '待启动', accepted: '已接受，独立继续', failed: '启动失败'}))),
    group('infra-pair', panel('运行身份', s.unit === 'none' ? '尚未分配' : '稳定的测试 Run unit'),
      panel('寿命约束', text(s.lifetime, {none: '尚未配置', infinity: 'TAU 无固定时长上限', '28800s': '本地测试上限 8 小时'}))),
    paragraph('页面连接与执行器的寿命分开。执行器是否已启动，由 systemd 的实际接受结果决定。'));
}
function tau(scene) {
  const s = scene.state, released = observed(scene, 'release_module_slot'), cancelledBeforeLock = observed(scene, 'cancel_before_slot');
  const lockLabel = released ? '退出作用域后已释放' : cancelledBeforeLock ? '取消前未取得，直接返回' : s.slot === 'held' ? '当前步骤持有' : '当前未持有';
  return box(group('infra-pair', panel('模块互斥锁', lockLabel, '同模块串行；容量租约与调用身份分别记录'),
    panel('已保存调用身份', s.invocation === 'confirmed' ? '已确认，可恢复等待' : '还没有已确认调用')),
    table(['来源核对', '本情境是否新增发送', '结果'], [[s.source_valid ? '本次新调用已核对' : '本步骤未进行新调用核对', s.new_send ? '是，已保存调用身份' : '否',
      text(s.result, {pending: '等待', recorded: '已记入原 Run', source_changed: '来源变化，拒绝新执行', cancelled: '取消阻止新派发'})]]),
    ...(released ? [paragraph('模块互斥已释放，原拒绝或取消结论保持，未新增输入。业务清理是否成功仍需独立结果。')]
      : cancelledBeforeLock ? [paragraph('本情境在尝试模块锁之前观察到取消，没有取得模块锁或容量租约，也没有新增输入。')]
        : ['changed', 'cancel'].includes(scene.current?.id) && scene.next?.id === 'release_module_slot' ? [paragraph('当前是拒绝或取消的观察点，模块互斥仍持有；下一步展示退出作用域后的释放。')] : []),
    paragraph('已有确认身份时恢复等待同一输入。只有新的派发才需要先核对冻结来源，并保存可恢复的调用身份。'));
}

const renderers = Object.freeze({
  'change-refresh': refresh, 'publish-buffer': publishBuffer, 'hub-cursor': hubCursor, 'subscriber-overflow': renderSubscriber,
  'directory-watch': directory, 'release-publication': releasePublication, 'request-coalescing': requests,
  'component-generation': generation, 'deployment-handoff': deployment, 'publication-ledger': publicationLedger,
  'release-cursor': releaseCursor, 'rpc-envelope': rpc, 'target-freeze': admission, 'architecture-audit': audit,
  'validation-flow': validation, 'catalog-evidence': catalog, 'plan-freeze': plan, 'durable-run': run,
  'executor-lifetime': lifetime, 'tau-resume': tau,
});

export function createInfrastructureWorkbench(root, {subject, content, ports = {}, signal, initialState} = {}) {
  const simulation = createInfrastructureSimulation(content), safe = simulation.getContent(), m = safe.mechanism;
  const objects = new Map(safe.objects.map(o => [o.id, o])), evidence = new Map(safe.evidence.map(o => [o.id, o]));
  const renderer = renderers[m.id.slice('infra.mechanism.'.length)];
  if (!renderer) throw new Error('infrastructure:renderer_missing');
  const entry = safe.entryObjectId ? resolveInfrastructureEntry(safe.entryObjectId, safe, {mechanismId: m.id}) : null;
  let destroyed = false, hidden = false, sourcesOpen = false;
  const surface = el('section', 'infra-workbench'); surface.setAttribute('aria-label', m.title); surface.dataset.mechanismId = m.id;
  const active = () => !destroyed && !hidden && !signal?.aborted;
  const invoke = (name, payload) => { if (active()) ports[name]?.(payload); };
  const button = (label, key, callback, disabled = false) => {
    const n = el('button', '', label); n.type = 'button'; n.dataset.infraAction = key; n.disabled = disabled;
    n.addEventListener('click', () => { if (active()) callback(); }); return n;
  };
  function notify(key) { sourcesOpen = surface.querySelector('details')?.open ?? sourcesOpen; render(key); invoke('change', simulation.getScene()); }
  function render(focus) {
    if (destroyed) return;
    const scene = simulation.getScene(), fragments = [];
    if (safe.provenance?.stage === 'research') fragments.push(el('div', 'infra-review', '研究候选 · 内容待正式冻结及逐项评审'));
    fragments.push(group('infra-head', box(el('p', 'infra-kicker', '本地机制演示'), el('h2', '', m.title), el('p', 'infra-question', m.question)), button('返回上个视图', 'return', () => invoke('return'))));
    if (entry?.modes.length > 1) {
      const modes = el('div', 'infra-controls'); modes.setAttribute('aria-label', '机制模式');
      for (const mode of entry.modes) {
        const control = button(mode.title, `mode-${mode.mechanismId}`, () => selectMechanism(mode.mechanismId));
        control.setAttribute('aria-pressed', String(mode.mechanismId === m.id)); modes.append(control);
      }
      fragments.push(modes);
    }
    const label = el('label', '', '选择情境'), select = el('select'); select.setAttribute('aria-label', '机制情境'); select.dataset.infraAction = 'case';
    for (const c of m.cases) { const option = el('option', '', c.title); option.value = c.id; select.append(option); }
    select.value = scene.caseId; select.addEventListener('change', () => { if (active()) { simulation.selectCase(select.value); notify('case'); } }); label.append(select);
    fragments.push(group('infra-controls', label, button('从头演示', 'reset', () => { simulation.reset(); notify('reset'); })));
    const board = el('div', 'infra-domain'); board.dataset.presentation = m.id.slice('infra.mechanism.'.length); board.append(renderer(scene)); fragments.push(board);
    const observation = el('div', 'infra-observation'); observation.setAttribute('aria-live', 'polite');
    observation.append(el('strong', '', scene.current?.title || '开始观察这个情境'), el('p', '', scene.current?.observable || '每次操作推进一个有依据的领域动作。'));
    if (scene.complete) observation.append(el('p', 'infra-outcome', stopsBeforeCleanup(scene) ? '抛错现场，尚未展示退出清理。当前冻结路径停在异常抛出，不表示返回后仍然持锁。' : scene.outcome)); fragments.push(observation);
    fragments.push(group('infra-actions', button('上一步', 'back', () => { simulation.back(); notify('back'); }, scene.position === 0),
      button(scene.next?.title || (stopsBeforeCleanup(scene) ? '已到当前事实停点' : cleanupShown(scene) ? '退出清理已展示' : '情境已完成'), 'next', () => { simulation.advance(); notify('next'); }, scene.complete)));
    fragments.at(-1).lastElementChild.classList.add('infra-primary');
    const participants = el('div', 'infra-participants');
    for (const p of m.participants || []) participants.append(button(`${objects.get(p.object_id).title} · ${p.role}`, `select-${p.object_id}`, () => invoke('select', {objectId: p.object_id})));
    if (objects.get(m.owner_id)?.child_ids?.length) participants.append(button('查看该层组成关系', 'composition', () => invoke('navigate', {kind: 'composition', objectId: m.owner_id})));
    fragments.push(participants);
    const details = el('details', 'infra-source'); details.open = sourcesOpen;
    details.addEventListener('toggle', () => { if (active() && details.isConnected) sourcesOpen = details.open; });
    details.append(el('summary', '', '前提与依据'));
    if (scene.next) details.append(el('p', '', `下一动作的前提：${scene.next.guard}`));
    if (scene.current) details.append(el('p', '', `${objects.get(scene.current.from).title} → ${objects.get(scene.current.to).title}`));
    for (const invariant of m.invariants || []) details.append(el('p', '', invariant));
    const sources = el('div', 'infra-source-list'), ids = [...(scene.current?.evidence_ids || m.evidence_ids)];
    if (stopsBeforeCleanup(scene)) {
      details.append(el('p', '', '固定操作锁依据包含 finally 释放；本路径尚未登记该尾步。'));
      if (evidence.has('infra.evidence.service-lock') && !ids.includes('infra.evidence.service-lock')) ids.push('infra.evidence.service-lock');
    }
    for (const id of ids) { const e = evidence.get(id); sources.append(button(e.claim || e.title || e.address || id, `source-${id}`, () => invoke('source', id))); }
    details.append(sources); fragments.push(details); surface.replaceChildren(...fragments);
    if (focus) [...surface.querySelectorAll('[data-infra-action]')].find(n => n.dataset.infraAction === focus)?.focus({preventScroll: true});
  }
  function destroy() { if (destroyed) return; destroyed = true; signal?.removeEventListener('abort', destroy); surface.remove(); }
  function selectMechanism(mechanismId) {
    if (!active()) return;
    if (!entry || !entry.modes.some(mode => mode.mechanismId === mechanismId)) throw new Error('infrastructure:entry_mode_unavailable');
    if (mechanismId !== m.id) invoke('navigate', {...entry.route, mechanismId});
  }
  function restoreViewState(state) {
    if (destroyed) return;
    simulation.restoreViewState(state.simulation); sourcesOpen = !!state.sourcesOpen; render(state.focusKey);
    surface.scrollTop = Number.isFinite(state.scrollTop) ? Math.max(0, state.scrollTop) : 0;
  }
  if (!signal?.aborted) {
    root.replaceChildren(surface); signal?.addEventListener('abort', destroy, {once: true});
    if (initialState) restoreViewState(initialState); else render();
  } else destroyed = true;
  return {
    show() { if (!destroyed) { hidden = false; surface.hidden = false; } },
    hide() { hidden = true; surface.hidden = true; }, destroy,
    getViewState() { return {simulation: simulation.getViewState(), sourcesOpen: surface.querySelector('details')?.open ?? sourcesOpen, scrollTop: surface.scrollTop,
      focusKey: surface.contains(document.activeElement) ? document.activeElement?.dataset?.infraAction || null : null}; },
    restoreViewState, getScene: () => simulation.getScene(),
    getModes: () => structuredClone(entry?.modes || []), selectMechanism,
  };
}

export const infrastructureRendererRegistry = Object.freeze(Object.fromEntries(Object.keys(infrastructureAdapters).map(adapter => [adapter, createInfrastructureWorkbench])));
export const infrastructureObjectEntryRegistry = Object.freeze(Object.fromEntries(Object.entries(infrastructureObjectEntries)
  .map(([id, entry]) => [id, Object.freeze({...entry, create: createInfrastructureWorkbench})])));
