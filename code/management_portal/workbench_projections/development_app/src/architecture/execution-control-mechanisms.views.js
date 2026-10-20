export const esc = value => String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const note = text => `<p class="ec-note">${esc(text)}</p>`;
const arrow = label => `<span class="ec-arrow" aria-hidden="true">↓<small>${esc(label)}</small></span>`;
const metric = (label,value) => `<span class="ec-metric"><b>${esc(value)}</b>${esc(label)}</span>`;
const pane = (title,body,status='') => `<section class="ec-piece ${status}"><h3>${esc(title)}</h3>${body}</section>`;
const yes = value => value?'已完成':'尚未完成';
function part(ctx,stepId,label,body='') {
  const {scene,mechanism}=ctx, s=mechanism.steps.find(s=>s.id===stepId);
  if(!s)throw new Error(`execution_control:domain_step:${stepId}`);
  const active=scene.next?.id===stepId,done=scene.prefix.includes(stepId),current=scene.current.id===stepId;
  return `<div class="ec-part ${done?'is-done':''} ${current?'is-current':''}" data-domain-step="${esc(stepId)}"><span class="ec-mark">${done?'✓':'○'}</span><div><button type="button" data-action="step" data-focus-key="step:${esc(stepId)}" data-step="${esc(stepId)}" ${active?'':'disabled'}>${esc(label)}</button>${body?`<p>${esc(body)}</p>`:''}</div></div>`;
}
const views = {
  'control.submission': c => {
    const s=c.scene.state;
    return `<div class="ec-ledger"><div class="ec-envelope"><strong>同一个 execution ID</strong><p>原始请求摘要</p>${part(c,'send','提交原始请求')}${part(c,'retry','重试同一请求','回执丢失后等待250ms；不换ID')}</div><span class="ec-transfer">→</span><div class="ec-ledger-book"><h3>Scheduler 提交账本</h3><div class="ec-ledger-count">${metric('本次新增队列行',s.queue_rows)}${metric('本次技术准备',s.prepare_count)}</div><p>${s.existing?'该ID已有记录':'检查该ID是否已有记录'}</p>${part(c,'new','首次准备并入队')}${part(c,'same','同ID同摘要：取回原记录')}${part(c,'conflict','同ID不同摘要：拒绝')}</div></div><div class="ec-receipt">${part(c,'reply','收到首次快照')}<p>${s.result==='existing'?'原记录已返回；没有再次准备或新增队列行。':s.result==='digest_conflict'?'原记录保持不变，请求被拒绝。':s.reply_lost?'回执可能丢失，不能因此判定未入队。':'回执说明此次提交结果，执行结果仍需观察。'}</p></div>${note('计数只统计本示例新增行。稳定ID用于找回未知回执；更换ID将表示另一项执行。')}`;
  },
  'control.rpc-dispatch': c => {
    const s=c.scene.state;
    return `<div class="ec-security"><div class="ec-socket">Socket连接可达</div>${arrow('仍须逐项验证')}<div class="ec-gates">${pane('① 请求封套',part(c,'parse','解析对象与参数')+part(c,'invalid','拒绝错误参数'),s.error==='ValueError'?'is-denied':'')}${pane('② 调用认证',part(c,'authenticate','验证认证断言')+part(c,'deny','拒绝无效认证'),s.error==='authentication_error'?'is-denied':'')}${pane('③ 身份一致',note('参数中的actor必须与认证断言一致。')+part(c,'actor','拒绝身份冒充'),s.error==='PermissionError'?'is-denied':'')}</div>${arrow(s.error?'在关口停止':'全部通过才分派')}<div class="ec-destination ${s.dispatched?'is-done':''}">${part(c,'dispatch','进入Scheduler')}${note(s.dispatched?'已交给Scheduler处理提交。':'尚未进入Scheduler。')}</div></div>${note('这条路径只演示执行提交分派。能够连接socket不授予业务身份；来源中的规范差异保留在依据中。')}`;
  },
  'control.preparation': c => {
    const s=c.scene.state;
    return `<div class="ec-route">${part(c,'select','按服务类型选择准备路径')}<div class="ec-fork"><section><h3>有会话的执行</h3>${part(c,'resolve','初解账号路由')}${arrow('查询已有会话')}${part(c,'session','读取持久会话绑定')}<div class="ec-fork">${part(c,'bound','跟随已有会话的账号','会话绑定会影响最终路由')}${part(c,'fresh','选择新会话的规范路由')}</div>${part(c,'fail','准备失败，回滚首次提交')}</section><section class="ec-bypass"><h3>无状态完成请求</h3>${part(c,'bypass','保留原路由','不读取SessionManager绑定')}</section></div>${arrow('准备成功返回后')}<div class="ec-destination">${part(c,'enqueue','持久入队')}${note(s.queued?'本次已经入队。':s.route==='failed'?'本次未产生queued行。':'尚未入队。')}</div></div>${note('技术准备属于首次提交；业务输入和AgentData预创建有各自owner。')}`;
  },
  'control.runtime-slot': c => {
    const s=c.scene.state;
    return `<div class="ec-slot-map"><div class="ec-lock-title">同一进程 · 同一个 execution ID</div><div class="ec-slot-pair"><section class="ec-predecessor"><h3>前任执行</h3><strong>${s.predecessor_active?'仍占据本地槽':'本地槽已无前任'}</strong>${note('旧claim被释放，不保证进程中的前任已经退出。')}</section><section class="ec-successor"><h3>当前尝试</h3>${part(c,'check','在条件锁下检查')}${part(c,'wait','等待前任退出','本步包含前任退出并唤醒的示例条件')}</section></div><div class="ec-one-slot ${s.slot_acquired?'is-done':''}"><strong>唯一执行槽</strong><b>${s.slot_acquired?'当前尝试占用':'未被当前尝试占用'}</b>${part(c,'acquire','占槽后运行')}${part(c,'finally','结束时释放槽')}</div><div class="ec-exits">${part(c,'cancel','取消：不进入运行')}${part(c,'defer','交接：延期并交回恢复')}</div></div>${note('本地单飞只防止同进程同ID并行；数据库租约围栏仍独立检查资格。')}`;
  },
  'control.runtime-finalization': c => {
    const s=c.scene.state;
    return `<div class="ec-finalize">${part(c,'select','选择本次结果来源')}<div class="ec-result-sources">${part(c,'fresh','首次调用','没有接受证据')}${part(c,'follow','跟随原轮次','仅适用可接回且仍运行的turn')}${part(c,'recover','重建已完成结果','跳过服务调用')}</div><div class="ec-count-strip">${metric('本机制新增原生输入',s.provider_call_count)}${metric('进入服务调用',s.service_called?'是':'否')}${metric('结果由完成点恢复',s.result_recovered?'是':'否')}</div>${arrow('先保存完成证据')}${part(c,'checkpoint','持久化完成恢复点')}<div class="ec-checkpoint"><strong>${s.checkpoint_phase==='provider_completed'?'已有完成证据':s.checkpoint_phase==='accepted'?'已有接受证据':'尚无完成证据'}</strong>${part(c,'defer','发生交接：保留证据，延期收尾')}</div>${arrow('当前Worker继续收尾')}<div class="ec-finish-lanes">${pane('最终内容',part(c,'content','写入最终投影')+note(s.content_phase==='finalized'?'最终内容已写入。':'最终投影尚未完成；此前流式内容可能已写。'))}${pane('取消检查',part(c,'cancel','内容后观察到取消')+note(s.outcome==='ExecutionCancelled'?'取消不能撤销已写内容，也不能证明绑定未变。':'在内容之后检查取消。'))}${pane('会话绑定',part(c,'binding','完成绑定并返回结果')+note(s.binding_phase==='completed'?'完成绑定已确认。':'绑定观察未知。'))}</div><div class="ec-outside">Worker → Scheduler 终态提交在本机制外，Runtime返回结果不等于执行已终态。</div></div>`;
  },
  'control.agent-service': c => {
    const s=c.scene.state;
    return `<div class="ec-service-route">${part(c,'lookup','按Provider选择Backend')}${part(c,'unsupported','未知Provider：拒绝')}<div class="ec-shared-preparation"><h3>两条执行路径都经过的准备</h3>${part(c,'prepare','准备调用')}<ol><li>原生选项：${yes(s.native_options_prepared)}</li><li>Harness调用准备：${yes(s.harness_prepared)}</li></ol></div><div class="ec-fork"><section class="ec-harness-wrap"><h3>Harness运行</h3>${part(c,'wrapped','由Harness执行Provider回调')}</section><section><h3>直接回调</h3>${part(c,'direct','不启用Harness运行','依然保留上面的两项准备')}</section></div>${arrow('同一个已选择Provider')}${part(c,'provider','补充上下文后调用Provider')}</div>${note('Runtime按服务类型选服务；AgentSessionService再按Provider选Backend。Harness不运行与没有准备是两件事。')}`;
  },
  'control.request-protocol': c => {
    const s=c.scene.state;
    return `<div class="ec-observation"><section class="ec-observer"><h3>调用方的观察窗口</h3>${part(c,'observe','用原ID和游标读取')}${part(c,'timeout','本次等待超时','可凭游标继续观察')}${part(c,'deny','其他owner：拒绝读取')}</section><div class="ec-read-link">读取提示 →<br><small>不重新提交 / 不隐式取消</small></div><section class="ec-authority"><h3>执行状态快照</h3><strong>${!s.owner_matches?'该调用方无权读取':s.terminal?'观察情境：已终态':'观察情境：仍活动'}</strong>${note('通知只作提示，终态以快照为准。')}</section></div><div class="ec-count-strip">${metric('本次发送取消',s.cancel_sent?'是':'否')}${metric('本次重新提交',s.resubmitted?'是':'否')}</div><div class="ec-ack-ledger"><h3>消费结果后的确认</h3>${part(c,'not-terminal','活动执行不能确认')}${part(c,'ack','确认已消费的终态')}<p>${s.acknowledged?'已清理请求恢复点；完整结果仍保留。':'尚未确认；不会因此清掉活动请求。'}</p></div>${note('显式停止需要独立取消操作，本地模拟没有发送该操作。')}`;
  },
  'control.chat-service': c => {
    const s=c.scene.state;
    return `<div class="ec-chat-compose">${part(c,'start','检查调用前取消')}${part(c,'cancel','已取消：不发调用')}<div class="ec-message-stack"><h3>构造本次消息序列</h3><div class="ec-message optional">系统上下文（若附加）</div><div class="ec-message optional">业务system prompt（若提供）</div><div class="ec-message history">已有消息</div><div class="ec-message user ${s.messages!=='unprepared'?'is-done':''}">${s.messages==='replace_last_user'?'末条user → 替换为本次输入':s.messages==='append_user'?'新user → 追加本次输入':'本次user输入待放入序列'}</div></div><div class="ec-fork">${part(c,'replace','末条是user：替换')}${part(c,'append','末条非user：追加')}</div><div class="ec-gates">${pane('协议参数',part(c,'invalid','参数错误：停止'))}${pane('模型选择',part(c,'missing','模型缺失：停止'))}</div>${arrow('两项都有效')}${part(c,'execute','交给协议Adapter调用')}${note(s.network_called?'本示例已进入协议调用并封装结果。':'本示例尚未进入网络调用。')}</div>${note('无状态完成请求不经过Agent会话与Harness路径。运行期interrupt返回false不说明远端已停止。')}`;
  },
};
export function renderExecutionControlDomain(context) {
  const render=views[context.mechanism.id];if(!render)throw new Error('execution_control:unsupported_view');return render(context);
}
