// Presentation only: state and transitions come from the parent's verified batch.
const e = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const reached = (s, id) => s.frames.some(f => f.step.id === id);
const row = (label, value) => `<div class="projection-pair"><span>${e(label)}</span><strong>${e(value)}</strong></div>`;
const card = (title, body, name = '') => `<section class="pm-host-card ${name}"><h3>${e(title)}</h3>${body}</section>`;
const note = text => `<p class="projection-domain-note">${e(text)}</p>`;
const yes = value => value ? '持有' : '未持有';
const empty = value => value || '尚无';
export const projectionCaseLabel = c => ({
  facade_first:'门面先授权，定义还在读取', definition_first:'定义先到，门面仍待授权', binding_mismatch:'两条读取的门面绑定不一致',
  abort_after_preload:'预加载开始后退出', exit_after_mount:'装配完成后退出', authorized_read:'合法窗口请求读取资源',
  wrong_source:'其他窗口冒用通道', wrong_channel:'本窗口使用未知通道', destroyed:'请求在途时销毁',
  a_late:'先选 A 再选 B，A 最后返回', batch_changed:'读取期间切换内容批次',
  long_confirmation:'确认框等待两分钟', hidden_cancel:'确认期间隐藏区域', timeout_after_send:'已经进入发送，SDK 等待超时',
  permission_denied:'确认之后对象权限被收回', focus_changed_after_validation:'对象校验在途时切换焦点',
}[c.id] || c.title);

function assembly(s) {
  const a=s.state, definition={not_started:'尚未请求',pending:'独立读取中',ready:'定义已返回'}[a.definition];
  const auth={not_started:'尚未请求',pending:'当前用户权限检查中',authorized:'已返回授权门面'}[a.facade];
  const lane=(title,value,detail,active)=>card(title,`<strong class="pm-lane-state">${e(value)}</strong><p>${e(detail)}</p>`,active?'active':'');
  return `<div class="pm-parallel" aria-label="打开时同时开始的两条读取">
    ${lane('工作台定义',definition,'读取功能区、布局与绑定；这条请求不等待门面读取。',a.definition==='ready')}
    ${lane('门面授权 → 原生 Chat 数据',auth,a.preload_started?'已发起原生数据预加载。数据是否已经安装，本步骤没有给出结果。':'一旦授权门面返回，就开始预加载，不必等定义和应用资源。',a.preload_started)}
  </div><div class="pm-host-grid">
    ${card('会合处：核对同一个绑定',`<div class="pm-join ${a.binding_error?'stopped':''}">${a.binding_error?'绑定冲突 · 不创建门面 Chat':a.chat_panel?'绑定匹配 · 已装配 Chat':a.closed?'已退出 · 禁止后续装配':'等待读取与绑定核对'}</div>${row('内容区域',a.regions_mounted?'已装配':'未装配')}${row('门面 Chat 面板',a.chat_panel?'已装配':'未装配')}<p>预加载开始与编辑器装配是两个时刻。绑定冲突只在门面处报错，功能区仍可装配。</p>`)}
    ${card('谁保持原生订阅存活',`<div class="pm-subscriptions"><div class="${a.temporary_subscription?'held':''}"><b>打开过程的临时订阅</b><span>${yes(a.temporary_subscription)}</span></div><div class="${a.chat_subscription?'held':''}"><b>Chat 自己的订阅</b><span>${yes(a.chat_subscription)}</span></div></div><p>Chat 自己取得订阅后，打开过程释放临时订阅；两个句柄分别负责清理。</p>${a.closed?note('已退出：晚到读取仍可能返回，但不能重新安装区域或 Chat。'):''}`)}
  </div>`;
}
function opaque(s) {
  const a=s.state, checked=s.position>1;
  return `<div class="pm-boundary-grid" aria-label="应用隔离与宿主消息桥">
    <section class="pm-outer-boundary"><h3>可信空白外容器</h3><p>${a.outer_navigation_guard?'frame-src none 阻止内层发起页面导航。':'等待宿主装配。'}外容器不执行应用代码。</p><div class="pm-inner-boundary"><h3>应用代码只在内层 iframe</h3>${row('同源访问',a.opaque_origin?'opaque origin · 没有 allow-same-origin':'尚未装配')}${row('直接联网',a.connect_allowed?'模型允许':'connect-src none')}<p>脚本和表单能力受 sandbox 与 CSP 同时约束；应用不能读宿主 DOM、Cookie 或存储。</p></div></section>
    ${card('postMessage 信封检查',`${row('活跃 iframe 记录',a.active_channel?'存在':'不存在')}${row('发送窗口',!checked?'尚未检查':a.source_matches?'与记录中的窗口一致':'不匹配或已失效')}${row('通道',!checked?'尚未检查':a.channel_matches?'与当前记录一致':'未知或已失效')}<p>requestId 必须是字符串。窗口和通道共同匹配，才进入宿主方法。</p><div class="pm-bridge-count">进入宿主代理 <b>${e(a.proxy_dispatch_count)}</b> 次</div>`,a.proxy_dispatch_count?'active':'')}
    ${card('资源能力检查',`<div class="pm-join">${a.proxy_dispatch_count?'仍须按当前用户逐操作鉴权':'请求尚未进入业务授权'}</div><p>通过消息桥只证明来自登记的 iframe；资源读取、门面和绑定权限继续由对应操作核验。</p>${a.destroyed?note('frame 记录已销毁，晚到请求没有代理入口。'):''}`)}
  </div>`;
}
function hostFocus(s) {
  const a=s.state;
  const events=s.frames.filter(f=>['request_a','request_b','b_first','a_late','old_sdk','destroy','reject_a'].includes(f.step.id));
  return `<div class="pm-race" aria-label="宿主焦点校验完成顺序">
    ${card('用户选择与在途授权',`${row('最近发起的选择',empty(a.latest_requested))}${row('A 的授权请求',a.pending_a?'仍在等待':'不在等待')}${row('B 的授权请求',a.pending_b?'仍在等待':'不在等待')}<p>A、B 是本地示例对象。请求先后顺序与授权完成顺序可能相反。</p>`)}
    ${card('已经发生的顺序',`<ol class="pm-events">${events.map(f=>`<li><strong>${e(f.step.title)}</strong><span>宿主 ${e(empty(f.state.host_focus))} / revision ${e(f.state.host_revision)}</span></li>`).join('')||'<li>尚未发起选择。</li>'}</ol>`)}
    ${card('宿主与接收面板',`${row('宿主焦点',empty(a.host_focus))}${row('宿主 revision',a.host_revision)}${row('SDK 显示',empty(a.sdk_focus))}${row('SDK revision',a.sdk_revision)}${a.outcome==='completion_order_wins'?'<p class="projection-stop">A 最晚返回后获得更新的 revision 2，覆盖 B。SDK 会接受这个更新修订。</p>':a.destroyed?note('销毁检查会阻止 A 提交；它与两次选择的代次检查不同。'):''}<p>这个固定版本没有为两次宿主 focus 请求分配代次围栏。SDK 只过滤旧修订，不能识别 A 是更早的选择。</p>`)}
  </div>`;
}
function detailOrder(s) {
  const a=s.state, readA=reached(s,'read_a'), readB=reached(s,'read_b');
  return `<div class="pm-detail-race" aria-label="来源正文的接纳条件">
    ${card('发出读取时留下凭据',`<div class="pm-ticket">A：${readA?'token 1 · batch-1':'尚未发出'}</div><div class="pm-ticket">B：${readB?'token 2 · batch-1':'尚未发出'}</div><p>token 与数据对象身份随读取保留。发起 B 不等于取消远端 A 请求。</p>`)}
    ${card('安装正文的三道条件',`<ol class="pm-detail-checks"><li>区域仍存活 <strong>${a.disposed?'否':'是'}</strong></li><li>token 等于当前代次 <strong>${e(a.detail_revision)}</strong></li><li>数据对象仍是当前批次 <strong>${e(a.data_identity)}</strong></li></ol>${a.discarded_response?`<p class="projection-stop">${e(a.discarded_response)} 的晚到正文被丢弃，不写入视图。</p>`:'<p>全部条件通过，正文才可安装。</p>'}`)}
    ${card('读者眼前的正文',`<div class="pm-body-preview"><span>当前选择 ${e(empty(a.current_selection))}</span><strong>${a.visible_body?e(a.visible_body)+' 的正文':'尚未安装正文'}</strong></div><p>这里解释架构区域 showSource 的检查，不能把它推成所有区域或宿主 focus 的共同保证。</p>`)}
  </div>`;
}
function budget(s) {
  const a=s.state, paused=a.timer_paused, active=a.request_pending&&!paused;
  const stopped=a.sdk_outcome!=='pending';
  const sec=ms=>`${ms/1000} 秒`;
  const budgetStatus=stopped?'请求已结束':paused?'暂停扣减':active?'活动计时中':'尚未开始计时';
  const continuation=stopped?'本次请求已结束，活动预算不再继续计时。':paused?`清理后若请求仍在途，将按保存的 ${sec(a.stored_remaining_ms)} 恢复计时。`:active&&reached(s,'pause')?`已恢复活动计时，当前剩余 ${sec(a.remaining_ms)}。`:'';
  const result={pending:'等待 SDK 结果',result:'SDK 接纳成功回执',cancelled:'SDK 接纳取消结果',error:'SDK 接纳授权错误',timeout_unknown:'SDK 超时 · 业务结果未知'}[a.sdk_outcome];
  return `<div class="pm-budget-workbench" aria-label="反馈活动预算与发送边界">
    ${card('两个时钟，只有一个消耗额度',`<div class="pm-clock-pair"><div><span>活动等待</span><strong>${sec(a.active_elapsed_ms)}</strong></div><div class="${paused?'paused':''}"><span>确认等待 · 不扣预算</span><strong>${sec(a.confirmation_elapsed_ms)}</strong></div></div><div class="pm-budget-remaining"><b>${sec(a.remaining_ms)}</b><span>${budgetStatus}</span></div><progress aria-label="剩余活动预算" value="${e(a.remaining_ms)}" max="90000"></progress><p>总活动预算 90 秒。${reached(s,'prepare')?'确认前的 1 秒已消费。':'确认之前的活动等待也计入预算。'}确认等待不扣减，也不充值。${continuation}</p><small>这些时间是固定模型中的本地示例，不是测得的网络耗时。</small>`)}
    ${card('发送之前仍需通过',`${row('用户接受原确认',a.confirmation_accepted?'已接受':'尚未接受')}${row('当前对象授权',({not_checked:'尚未检查',allowed:'通过',denied:'拒绝'})[a.object_authorization])}${row('当前门面授权',a.facade_authorization==='allowed'?'通过':'尚未检查')}${row('确认封存 / 当前修订',`${a.captured_revision} / ${a.focus_revision}`)}${row('区域可见',a.visible?'是':'否')}${row('最后一次 guard',({not_checked:'尚未检查',passed:'通过',revision_mismatch:'修订变化 · 拒绝'})[a.guard_result])}<p>每次异步授权返回后再检查情境；授权通过不能抵消焦点变化或隐藏取消。</p>${a.aborted?'<p class="projection-stop">区域隐藏，使原确认失效。</p>':''}`)}
    ${card('原生入口与结果分别观察',`<div class="pm-send-count">进入 Chat.send <strong>${e(a.native_send_entries)}</strong> 次</div><h3 class="pm-sdk-outcome">${e(result)}</h3>${a.sdk_outcome==='timeout_unknown'?'<p class="projection-stop">进入过发送，但 SDK 没收到可接纳的结果。不能显示“未发送”，也不能自动重投。</p>':a.business_outcome==='not_sent'?'<p>本例在发送前停止，没有进入原生发送。</p>':a.business_outcome==='receipt_observed'?'<p>已观察到本次成功回执；任务完成仍是另一种业务观察。</p>':'<p>入口调用不等于接递回执、任务完成或最终业务结果。</p>'}${row('SDK 在途记录',a.request_pending?'仍存在':'不存在')}${row('远端中断',a.remote_interrupt?'模型声明已请求':'未发起')}${a.late_result_ignored?note('迟到结果找不到在途记录，不能恢复已超时的 Promise。'):''}`)}
  </div>`;
}
export const HOST_PROJECTION_VIEWS = Object.freeze({
  'portal.architecture.projection-assembly.v1':assembly,
  'portal.architecture.projection-opaque-bridge.v1':opaque,
  'portal.architecture.projection-host-focus-order.v1':hostFocus,
  'portal.architecture.projection-region-detail-order.v1':detailOrder,
  'portal.architecture.projection-feedback-budget.v1':budget,
});
