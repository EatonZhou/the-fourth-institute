// Content-specific diagrams for frozen native operations. All actions replay local cases.
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
const titles = {
  'codex.fork': ['派生 thread，保留来源身份', 'Fork 不启动新 turn；返回目标身份后，由调用方负责业务绑定。'],
  'codex.compact': ['压缩完成，还要等写者退出', '提交上下文、排空技术 turn、确认压缩记录，是三个可分别观察的条件。'],
  'codex.goal': ['跟随目标，直到目标状态改变', '单个 turn 结束后，原生 Goal 可以继续运行。'],
  'codex.dynamic-tools': ['谁有权回答这次工具请求', '新请求与已开始的工具调用，在移交时走不同路径。'],
  'provider.native-socket': ['原生调用怎样交回结果', '观察原输入、故障续接、计划移交与退出后的内容和用量记录。'],
};
const cases = {
  'codex.fork': {normal: '派生独立 thread', source_missing_with_history: '来源缺失，有可恢复历史', source_missing_without_history: '来源缺失，无可恢复历史', other_provider_error: '其他 Provider 错误', missing_result_id: '响应缺少目标 ID'},
  'codex.compact': {normal: '提交后等待写者排空', already_compacting: '已有本地压缩等待', total_timeout: '总时限到期', no_progress_timeout: '无进展时限到期', cancel_wait: '取消本地等待', record_missing: '压缩后缺少正式记录'},
  'codex.goal': {same_active_objective: '同目标运行中，保留预算', set_new: '设置新目标', pause: '暂停已有目标', resume: '恢复并核对目标状态', clear: '清除目标', terminal_notification_first: '终态通知先于命令响应', planned_handover: '移交跟随，目标继续', user_stop: '用户停止，尝试暂停与中断', pause_request_failure: '暂停失败，仍尝试中断', missing_goal: '读取不到目标', invalid_input: '非法命令或空目标', ephemeral_goal: '临时会话拒绝 Goal'},
  'codex.dynamic-tools': {invalid_declaration: '工具声明无效', stale_subscriber: '旧订阅者不抢答', detaching_new_request: '移交时新请求留给下一跟随者', missing_dispatcher: '当前拥有者缺少 handler', success: '工具成功并回传', harness_failure: '工具业务失败', unknown_tool: '未知工具名', governance_failure: '结果处理失败，保留原结果', dispatcher_failure: '分发器协议错误', drain_before_detach: '在途调用回完再移交'},
  'provider.native-socket': {normal: '正常调用与结果返回', route_changed: '路由改变，调用前拒绝', handover: '计划移交，保留原输入运行', cancel_before_attach: '恢复跟随前已取消', provider_error_continuation: '故障后发送续接输入', release_active: '释放时仍有活跃 turn', release: '释放成功', fork_supported: '已声明支持 Fork 的 Provider', compact: '实际压缩并记录', nothing_to_compact: '明确无需压缩', compact_failed: '压缩响应不满足成功条件'},
};
const value = (field, text) => `<strong data-native-field="${esc(field)}">${esc(text)}</strong>`;
const metric = (field, label, text) => `<div class="native-op-metric"><span>${esc(label)}</span>${value(field, text)}</div>`;
const flag = (field, label, enabled, yes = '已确认', no = '未确认') => `<div class="native-op-flag ${enabled ? 'confirmed' : ''}"><span>${esc(label)}</span>${value(field, enabled ? yes : no)}</div>`;
const status = text => ({active: '运行中', paused: '已暂停', complete: '目标完成', blocked: '受阻', budgetLimited: '预算已达上限', none: '无目标'}[text] || text);

function fork(s) {
  return `<div class="native-fork-tree" aria-label="Fork来源与目标"><section class="native-op-entity"><h3>原来源保持</h3>${metric('account_id', '固定示例账号', s.account_id)}${metric('source_thread_id', '来源 thread', s.source_thread_id)}</section>
    <div class="native-fork-bridge"><span aria-hidden="true">↗</span>${metric('fork_rpc_count', 'thread/fork', `${s.fork_rpc_count} 次`)}<small>不声明幂等</small></div>
    <section class="native-op-entity native-fork-target"><h3>目标身份</h3>${metric('result_thread_id', 'Provider 返回', s.result_thread_id || '尚无合法目标 ID')}${flag('backend_result_returned', 'Adapter 返回 BackendResult', s.backend_result_returned)}</section></div>
    <div class="native-fork-history" aria-label="来源缺失时的恢复"><h3>仅来源缺失时检查历史</h3><div class="native-op-row">${flag('history_available', '同身份 canonical 历史', s.history_available, '可用', '未确认可用')}${flag('history_injected', '替代 thread 初始化历史', s.history_injected)}${metric('thread_create_count', '替代 thread 创建', `${s.thread_create_count} 次`)}</div></div>
    <div class="native-op-emphasis">${metric('turn_start_count', 'turn/start', `${s.turn_start_count} 次`)}<p>新 thread 身份与新一轮输入分别处理。</p></div>`;
}
function compact(s) {
  const gate = (field, title, yes, caption) => `<section class="native-compact-gate ${yes ? 'confirmed' : ''}"><h3>${esc(title)}</h3>${value(field, yes ? '已满足' : '尚未满足')}<p>${esc(caption)}</p></section>`;
  return `<div class="native-op-row">${metric('thread_id', '原 thread', s.thread_id)}${flag('loaded', '本客户端加载', s.loaded)}${metric('compact_start_count', 'compact/start', `${s.compact_start_count} 次`)}</div>
    <div class="native-compact-gates" aria-label="压缩返回的三个条件">${gate('context_committed', '① 上下文提交', s.context_committed, '收到 thread/compacted')}${gate('technical_turn_active', '② 技术写者排空', !s.technical_turn_active, s.technical_turn_active ? '技术 turn 仍活跃，继续等待' : '轮询确认无活跃 turn')}${gate('canonical_recorded', '③ 正式记录', s.canonical_recorded, 'Adapter 核验新 canonical 来源')}</div>
    <div class="native-compact-clocks" aria-label="两个独立的等待时限"><section><h3>总时限</h3>${value('total_deadline_reset', s.total_deadline_reset ? '已重置' : '不随进展重置')}<p>从请求前计算</p></section><section><h3>无进展窗口</h3>${value('no_progress_reset', s.no_progress_reset ? '已随进展刷新' : '尚无刷新')}<p>请求接受或技术 turn 状态改变</p></section></div>
    <div class="native-op-row">${flag('local_busy', '本地 waiter / busy', s.local_busy, '占用中', '未占用')}${flag('interrupt_sent', '本方法发 interrupt', s.interrupt_sent, '已发送', '未发送')}${flag('backend_result_returned', 'Adapter 返回成功', s.backend_result_returned)}</div>
    ${s.recovery_kind ? `<p class="native-op-warning">已保存恢复状态：${value('recovery_kind', s.recovery_kind)}；等待后续恢复。</p>` : ''}`;
}
function goal(s) {
  return `<section class="native-goal-board" aria-label="Provider Goal与单轮次"><div class="native-goal-objective"><span>原生 Goal · ${esc(s.thread_id)}</span><h3>${esc(s.objective)}</h3>${value('goal_status', status(s.goal_status))}</div>
    <div class="native-goal-turn">${flag('turn_completed', '本例单个 turn', s.turn_completed, '已结束', '尚未结束')}<p>Goal 的终态由目标通知或 goal/get 确认。</p>${flag('follow_attached', '客户端跟随', s.follow_attached, '跟随中', '未跟随')}</div></section>
    <div class="native-goal-budget" aria-label="请求预算和已有预算">${metric('requested_budget', '本例请求预算', s.requested_budget)}<span aria-hidden="true">→</span>${metric('provider_budget', 'Provider 已有预算', s.provider_budget)}<p>同目标 active 分支跳过 set，保留已有预算。</p></div>
    <div class="native-op-row">${metric('goal_get_count', 'goal/get', `${s.goal_get_count} 次`)}${metric('goal_set_count', 'goal/set', `${s.goal_set_count} 次`)}${metric('goal_clear_count', 'goal/clear', `${s.goal_clear_count} 次`)}</div>
    <section class="native-goal-payload"><h3>本次 set 提交的字段</h3><div data-native-field="goal_set_fields">${s.goal_set_fields.length ? s.goal_set_fields.map(k => `<code>${esc(k)}</code>`).join('') : '尚未发送 set'}</div></section>
    <div class="native-op-row">${flag('terminal_notification_preserved', '提前到达的目标终态', s.terminal_notification_preserved, '已保留', '本例尚未观察')}${flag('interrupt_requested', '中断当前 turn 的请求', s.interrupt_requested, '已尝试', '未尝试')}</div>`;
}
function tools(s, scene) {
  const businessObserved = scene.trace.some(t => t.changes.some(c => c.field === 'tool_success'));
  const envelope = s.reply_count ? s.rpc_error_code ? `JSON-RPC error ${s.rpc_error_code}` : `result.success: ${s.tool_success}` : '尚未回传信封';
  return `<div class="native-tool-ownership" aria-label="工具请求的回答所有权">${flag('schema_valid', '工具声明', s.schema_valid, '合法', '无效')}${flag('owns_turn', '本客户端 turn / Goal waiter', s.owns_turn, '持有', '不持有')}${flag('dispatcher_registered', '该 thread 的 handler', s.dispatcher_registered, '已登记', '缺失')}</div>
    <div class="native-tool-handover"><div><h3>新请求入口</h3>${value('detaching', s.detaching ? '移交中：留给下一跟随者' : '未进入移交')}<p>旧订阅者和移交中的客户端保持无答。</p></div><div><h3>已经开始的调用</h3>${metric('active_calls', '在途工具', `${s.active_calls} 个`)}${flag('detached', '解除旧跟随', s.detached, '已解除', '尚未解除')}</div></div>
    <div class="native-tool-delivery" aria-label="工具业务结果和RPC信封"><section><h3>工具业务结果</h3>${value('tool_success', businessObserved ? `success: ${s.tool_success}` : '尚无业务结果')}${flag('result_governance_failed', '可选结果处理', s.result_governance_failed, '失败，保留原结果', '未观察失败')}</section><span aria-hidden="true">→</span><section class="native-tool-envelope"><h3>实际回传</h3>${value('rpc_error_code', envelope)}${metric('reply_count', 'JSON-RPC 信封', `${s.reply_count} 个`)}</section></div>`;
}
function socket(s) {
  const delivery = {original: '原输入', continued: '故障续接输入', kept_running: '原输入保留运行'}[s.input_delivery] || s.input_delivery;
  return `<div class="native-socket-route"><section>${metric('session_id', '固定示例 session', s.session_id)}${metric('route_id', '固定示例 route', s.route_id)}</section><div class="native-socket-boundary"><h3>原生客户端边界</h3>${metric('client_call_count', '本例跨边界调用', `${s.client_call_count} 次`)}<small>此计数不等于 turn/start</small></div><section>${metric('input_delivery', '输入处理方式', delivery)}<p>各 Provider 的协议和支持能力保持独立。</p></section></div>
    <div class="native-socket-outputs"><section><h3>答案观察</h3>${flag('final_answer', '提交 final_answer', s.final_answer)}</section><section><h3>Adapter 结果</h3>${flag('result_returned', '返回 BackendResult', s.result_returned)}</section></div>
    <section class="native-socket-finally"><h3>退出时仍执行的观察</h3><div class="native-op-row">${flag('compaction_observed', '压缩内容观察', s.compaction_observed, '本例回调成功', '尚未观察')}${flag('usage_observed', '用量观察', s.usage_observed, '本例回调成功', '尚未观察')}</div></section>
    <div class="native-op-row">${flag('compacted', 'Provider 已压缩', s.compacted)}${flag('nothing_to_compact', 'Provider 明确无需压缩', s.nothing_to_compact)}${flag('canonical_recorded', '写入新压缩记录', s.canonical_recorded)}</div>`;
}
const diagrams = {'codex.fork': fork, 'codex.compact': compact, 'codex.goal': goal, 'codex.dynamic-tools': tools, 'provider.native-socket': socket};

export function renderNativeOperation({scene, mechanism, simulation, button, objectButton, sourcesOpen}) {
  const [title, explanation] = titles[mechanism.id], s = scene.state, step = scene.current, next = scene.next;
  const objectName = id => simulation.objects.get(id)?.title || id;
  return `<header class="native-heading"><div><span class="native-eyebrow">本地模拟 · 原生机制</span><h2>${esc(title)}</h2><p>${esc(explanation)}</p></div>${button('return', '返回架构')}</header>
    <div class="native-controls"><label>原生操作情境<select aria-label="原生操作情境" data-native-action="case">${mechanism.cases.map(c => `<option value="${esc(c.id)}" ${c.id === scene.case_id ? 'selected' : ''}>${esc(cases[mechanism.id][c.id] || c.title)}</option>`).join('')}</select></label>${button('reset', '重新试验', !scene.cursor)}</div>
    <div class="native-context"><b>${esc(cases[mechanism.id][scene.case_id])}</b><small>固定源码机制的本地示例 · 示例身份与数值不代表真实账号或执行</small></div>
    <main class="native-operation-diagram" data-native-operation="${esc(mechanism.id)}">${diagrams[mechanism.id](s, scene)}</main>
    <section class="native-exchange"><h3>${esc(step?.title || '选择情境，观察原生边界')}</h3><p role="status" aria-live="polite">${esc(step?.observable || mechanism.entry_conditions.join('；'))}</p>
      ${next ? `<div class="native-request-line"><span>下一步：</span><b>${esc(objectName(next.from))}</b><span aria-hidden="true">→</span><b>${esc(objectName(next.to))}</b></div><p class="native-guard">${esc(next.guard)}</p>${button('step', next.title, false, `class="native-primary" data-id="${esc(next.id)}"`)}` : `<p class="native-op-result">${esc(scene.outcome)}</p>`}
      <p class="native-op-result-code">本例状态：${value('outcome', s.outcome)}</p></section>
    <footer class="native-foot">${button('back', '上一步', !scene.cursor)}${objectButton(mechanism.owner_id)}${simulation.objects.has('worker.runtime') ? button('runtime', '返回 Runtime 阅读位置') : ''}<p>返回只恢复阅读位置；本地结果不会写入真实执行。</p></footer>
    <details class="native-trace"><summary>已经观察的交互</summary><ol>${scene.trace.map(t => `<li>${esc(t.title)}</li>`).join('')}</ol></details>
    <details class="native-sources" ${sourcesOpen ? 'open' : ''}><summary>本步固定依据</summary><div class="native-source-links">${scene.evidence_ids.map(id => {const e = simulation.evidence.get(id); return button('source', `${e.claim || e.symbol || e.id} · L${e.start_line}–${e.end_line}`, false, `data-id="${esc(id)}"`);}).join('')}</div><ul>${mechanism.invariants.map(t => `<li>${esc(t)}</li>`).join('')}</ul></details>`;
}
