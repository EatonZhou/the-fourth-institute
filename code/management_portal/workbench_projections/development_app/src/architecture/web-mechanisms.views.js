import {renderCursorSnapshot} from './web-mechanisms.cursors.js';
export const escape = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;'}[c]));
const text = value => escape(Array.isArray(value) ? value.join('、') || '尚无' : value);
const note = value => `<p class="web-note">${escape(value)}</p>`;
const heading = (title, body, cls = '') => `<section class="web-zone ${cls}"><h3>${escape(title)}</h3>${body}</section>`;
const value = (label, v) => `<div class="web-reading"><span>${escape(label)}</span><strong>${text(v)}</strong></div>`;
const tag = (label, ok) => `<span class="web-tag ${ok ? 'yes' : ''}">${escape(label)}</span>`;
const flow = items => `<ol class="web-flow">${items.map(([label, active, desc]) => `<li class="${active ? 'reached' : ''}"><b>${escape(label)}</b><span>${escape(desc || '')}</span></li>`).join('')}</ol>`;
const split = (...items) => `<div class="web-split">${items.join('')}</div>`;
const passed = (scene, id) => scene.prefix.includes(id);
const packet = (title, body, cls = '') => `<article class="web-packet ${cls}"><b>${escape(title)}</b><div>${body}</div></article>`;

function cursors(scene, mechanism) {
  if (Object.hasOwn(scene.state,'installed_snapshot_label')) return renderCursorSnapshot(scene,mechanism);
  const s = scene.state, low = mechanism?.state_fields?.find(f=>f.id==='conversation_cursor')?.initial ?? 10, high = Math.max(low + 2, s.state_cursor, s.conversation_cursor);
  const track = (label, cursor, desc) => heading(label, `<div class="web-cursor" aria-label="${escape(label)} ${cursor}">${Array.from({length: high - low + 1}, (_, i) => low + i).map(n => `<div class="${n <= cursor ? 'known' : ''} ${n === cursor ? 'needle' : ''}"><span>${n}</span><b>${n === cursor ? '▼' : '·'}</b></div>`).join('')}</div>${note(desc)}`);
  return `<div class="web-cursors">${track('已连续读取的消息', s.conversation_cursor, '按这根游标补读消息；运行回执不能替它跳过中间事件。')}${track('已接受的运行状态', s.state_cursor, '运行状态可以先抵达；它不包含尚未补读的消息正文。')}</div>`
    + split(heading('状态回执', passed(scene,'ack') ? packet(`已接受状态 ${s.state_cursor}`,'仅进入运行状态刻度；不携带缺失消息。','arrived') : note('等待运行状态回执。')),
      heading('消息收件区', `<div class="web-message">保留原快照 · 消息到 ${text(low)}</div>${s.applied.map(x => `<div class="web-message incoming">已合并 ${escape(x)}</div>`).join('')}${s.snapshot_reads ? `<div class="web-gap">从消息 ${text(s.conversation_cursor)} 后断开 → 重新读取快照</div>${note(scene.inputs.event_kind==='payload_omitted'?'消息正文被省略，本次不应用该事件。':'未收到连续消息，本次不越过缺口。')}${packet('快照读取已发起','本情境尚未给出返回快照的正文与新游标；保留原快照等待权威结果。')}` : !s.applied.length ? note('新消息尚未连续应用。') : ''}`));
}
function documentEdit(scene, mechanism) {
  const s = scene.state, sent = passed(scene, 'start'), stale = passed(scene, 'stale');
  const originalDraft = mechanism?.state_fields?.find(f=>f.id==='draft')?.initial ?? s.draft;
  const sentId = mechanism?.state_fields?.find(f=>f.id==='save_id')?.initial ?? s.save_id;
  return `<div class="web-document"><div class="web-document-tab">当前文档 · 编辑中 ${tag(s.dirty ? '未保存' : '已保存', !s.dirty)}</div><pre class="web-draft">${text(s.draft)}</pre>${note('草稿属于当前 Document Session。')}</div>`
    + split(heading('发出的保存副本', sent ? packet(`保存请求 · 代次 ${sentId}`, `<pre class="web-frozen-draft">${text(originalDraft)}</pre>${note('发送时冻结的草稿副本；后续回执须对应同一保存身份。')}`) : note('还没有发出本次保存。')),
      heading('回执闸门', stale ? `<div class="web-rejected">代次 ${text(sentId)} 的旧回执已隔离</div>${value('当前保存代次', s.save_id)}${note('回执退回已失效的保存请求；当前草稿仍未保存。')}`
        : s.error ? `<div class="web-rejected">保存被拒绝</div>${note('草稿仍在编辑区。修正权限或冲突后，可以再次保存。')}`
          : !s.dirty ? `<div class="web-success">本次保存已接受</div>${note('仍可继续编辑，不退出文档会话。')}` : note(s.saving ? '等待本次回执，保留草稿。' : '尚未收到保存回执。')));
}
function stream(scene) {
  const s = scene.state, started = !!s.last_cursor;
  const frames = [];
  if (started) frames.push(packet('batch', value('末条记录游标', s.last_cursor), 'arrived'));
  if (s.phase === 'ended') frames.push(packet('end', '流正常结束', 'arrived'));
  if (s.phase === 'error') frames.push(packet('error', '流生产失败；保留已收到的批次', 'failed'));
  if (s.phase === 'disconnected') frames.push(packet('连接中断', '没有把中断伪装成正常 end', 'failed'));
  return flow([['浏览器请求', passed(scene,'request'), '请求与取消信号'], ['Router', passed(scene,'authenticate'), '转发连接'], ['ASGI 流', s.authenticated, s.phase === 'login' ? '认证拒绝 · 未开流' : '认证通过才开流']])
    + `<div class="web-stream">${frames.join('') || note('等待第一批数据；观察游标尚为空。')}</div>`
    + split(heading('本次传输', `${value('取消清理', s.cancel_signalled ? '已触发' : '未触发')}${value('观察游标', s.last_cursor || '尚无')}${note('空闲心跳维持连接，不推进业务游标。')}`),
      heading('业务提交结果', `<div class="web-unknown">需要另读权威记录</div>${note('收到帧、断连或清理完成，都不能单独证明业务已经回滚。')}`));
}
function refresh(scene) {
  const s = scene.state, old = passed(scene, 'drop');
  return heading('页面当前目标', `${value('目标代次', s.generation)}<div class="web-target ${s.applied ? 'filled' : ''}">${s.applied ? '采用了当前响应' : '等待当前目标内容'}</div>`)
    + `<div class="web-transfer">${packet('在途读取', `${value('请求代次', s.request_generation)}${note(old ? '晚到结果已丢弃' : '返回时比较目标与请求代次')}`, old ? 'discarded' : '')}<span aria-hidden="true">→</span>${packet('强制刷新槽', s.pending_force ? '已合并到一个待执行刷新' : '空')}</div>`
    + note('切换目标后继续使用当前目标读取；旧请求完成不恢复已经离开的页面。');
}
function retry(scene) {
  const s = scene.state;
  return heading('一个业务信封', `<div class="web-envelope">${value('幂等键', s.key)}${note('重试沿用同一业务键；传输次数与业务动作次数分别计。')}</div>`)
    + heading('传输尝试', `<div class="web-attempts">${Array.from({length:s.transport_attempt},(_,i)=>packet(`HTTP 尝试 ${i+1}`, `传输身份 ${i+1} · 同一业务键`)).join('') || note('尚未传输。')}</div>`)
    + (s.result === 'unknown' ? `<div class="web-unknown">结果未知，需要核实原调用</div>` : s.result === 'committed' ? `<div class="web-success">已收到提交结果</div>` : note('等待返回。明确的投影暂态才进入同键修复重试。'));
}
function home(scene) {
  const s=scene.state;
  return `<div class="web-home-window"><div class="web-home-sidebar">Home</div><div class="web-home-panel ${s.status==='deferred'?'veiled':''}"><b>当前组件</b>${value('连接次数',s.connections)}${value('组件状态',({deferred:'等待可见',mounting:'正在连接',ready:'已连接',error:'局部错误',destroyed:'已释放'})[s.status])}</div></div>`
    + flow([['自身与祖先可见',passed(scene,'connect'),'隐藏时不首次读取'],['建立连接',s.connections>0,'组件拿到上下文与取消信号'],['逆序释放',s.cleanup,'取消请求、清理回调、移除监听']])
    + note(s.status==='error'?'这个组件报错；其他组件的状态继续保留。':'可见性决定何时首次连接，关闭页面结束这次实例的资源生命周期。');
}
function router(scene) {
  const s=scene.state;
  return `<div class="web-route"><div class="web-route-origin">浏览器请求<br>${tag(s.body_sent?'body 已发送一次':'body 尚未发送',s.body_sent)}</div><div class="web-route-branches">${['dev','release'].map(b=>`<div class="web-backend ${s.backend===b?'chosen':''}">${b==='dev'?'Dev 后端':'Release 后端'}${s.backend===b?'<b>← 本次连接</b>':''}</div>`).join('')}</div></div>`
    + heading('连接窗口', `${value('连接尝试',s.connection_attempts)}${passed(scene,'wait')?note('首次连接被拒绝；在请求体送出前等待并重新建立连接。'):''}${s.status==='502'?'<div class="web-rejected">502 · 后端不可用</div>':note(s.body_sent?'后端已收到 body；随后只转发响应，不重放请求体。':'此时还没有把 body 交给后端。')}`);
}
function request(scene) {
  const s=scene.state;
  return `<div class="web-request-buffer"><span>请求体</span><div class="web-chunks"><i></i><i></i><i></i></div><span>临时缓冲 ${s.body_closed?'已关闭':'等待释放'}</span></div>`
    + flow([['读取并限制大小',passed(scene,'read'),'先接收、暂存 body'],['同步处理器',s.handler_started,s.handler_started?'在线程中处理':'尚未执行'],['响应',!!s.status,s.status ? `HTTP ${s.status}`:'等待']])
    + note(s.status===413?'超限在领域处理前拒绝；处理器没有启动。':s.status===500?'处理器异常转结构化错误，body 在 finally 中释放。':'响应保留状态与头并分块输出；临时请求体有明确释放时点。');
}
function identity(scene) {
  const s=scene.state;
  return `<div class="web-identity"><div class="web-challenge"><h3>登录挑战</h3>${tag('密码已通过',true)}${tag('二因素挑战',passed(scene,'challenge'))}${s.phase==='challenge-rejected'?'<p class="web-rejected">验证码错误 · 不签发</p>':''}</div><div class="web-cookie ${s.token?'issued':''}"><h3>签名 Cookie</h3>${s.token?'用户 · 到期时间 · 会话版本':'尚未签发'}</div><div class="web-session-gate"><h3>下一次请求</h3>${s.authorized?'<b class="web-success">当前身份有效</b>':s.phase==='session-rejected'?'<b class="web-rejected">版本已撤销 · 拒绝旧 Cookie</b>':'继续校验签名、用户、到期与会话版本'}</div></div>`
    + note('拿到合法签名不保证永久可用。每个请求还要通过当前会话版本校验。');
}
function chatroom(scene) {
  const s=scene.state;
  return heading('同一条消息的位置', `<div class="web-message">消息位置保持不变 <span class="web-version">${text(s.segment)}</span></div>`)
    + split(heading('本次编辑条件', `${value('期望修订',scene.inputs.expected_version)}${value('消息作者',scene.inputs.own_message?'本人':'他人')}${s.outcome==='rejected'?'<div class="web-rejected">拒绝覆盖当前修订</div>':''}`),
      heading('变更订阅', `${value('变更游标',s.change_cursor)}${s.outcome==='edited'?packet('updated_messages','同一消息的新 Segment 可被补读','arrived'):note('没有新增修订。')}${value('新增提及任务',s.new_mentions)}`));
}
function discussion(scene) {
  const s=scene.state;
  return `<div class="web-address"><b>根资源</b><span>／章节</span><span>／片段</span></div>${note('讨论归属根资源；章节和片段不创建另一份讨论身份。')}`
    + flow([['根地址',scene.inputs.address_kind==='root',scene.inputs.address_kind==='root'?'本次使用根资源':'本次使用内部地址'],['当前可读',scene.inputs.readable,'与正文权限一致'],['留言记录',s.written,s.written?'锁内保存':'未写入']])
    + (s.error?'<div class="web-rejected">入口无效或不可读；不返回讨论作者和数量。</div>':s.written?heading('共享留言',packet('新增留言','与正文分开持久化；更新作者读标记。')):note('核验通过后才读取或写入讨论。'));
}
function pulse(scene) {
  const s=scene.state;
  return `<div class="web-publication"><div class="web-private"><b>个人摘要</b>${tag(scene.inputs.caller_is_owner?'当前所有者':'非所有者',scene.inputs.caller_is_owner)}</div><span aria-hidden="true">→</span><div class="web-public"><b>公开摘要副本</b>${s.row_count?packet(s.publication_id,'同一来源只保留一条公开记录','arrived'):note('尚未接受本次发布。')}</div></div>`
    + (s.write_result==='rejected'?'<div class="web-rejected">所有者核验未通过，不新增公开记录。</div>':note(scene.inputs.existing?'这次输入声明已有同来源记录；再次发布复用公开身份。':'首次发布把个人摘要投影成公开记录。'));
}
function task(scene) {
  const s=scene.state;
  return `<div class="web-persistence"><section><h3>业务持久层</h3><div class="web-disk">${s.job_saved?'job 已保存':'尚未保存 job'}</div>${value('业务记录',({new:'尚未建立',queued:'已排队','owner result recorded':'业务方已保存结果',error:'已记录错误'})[s.job_status] || s.job_status)}</section><section class="${passed(scene,'exit')?'process-gone':''}"><h3>当前进程</h3><div class="web-thread">${s.active?'线程运行中':'没有活动线程'}</div>${value('本轮启动次数',s.thread_count)}</section></div>`
    + note(passed(scene,'exit')?'进程退出后线程结束。持久 job 留下，是否恢复由领域负责。':passed(scene,'duplicate')?'同 ID 已在运行，第二次启动被拒绝；业务记录仍指向同一个任务。':'先存业务 job 再启动线程。业务结果的持久化属于业务 owner，线程工具只管理本进程的活动调用。');
}
function paper(scene) {
  const s=scene.state;
  return `<div class="web-receipt"><h3>已经提交的输入回执</h3>${value('输入身份',scene.inputs.confirmed_input?'已确认':'尚未确认')}${value('旧宿主',scene.inputs.old_process_alive?'仍存活':'已结束')}</div>`
    + `<div class="web-observation">${s.observing?'↳ 重新认领并观察原 job / 原 input_message_id':'保留原记录，等待恢复条件'}${value('新输入提交次数',s.new_input_count)}</div>`
    + note('恢复观察要有原输入身份、旧进程状态与本地认领检查；未知时不靠再提交一份输入掩盖问题。');
}
function wall(scene) {
  const s=scene.state;
  return split(heading('设计点的当前版本',`${tag(scene.inputs.revision_matches?'评审对应当前内容':'评审内容已过期',scene.inputs.revision_matches)}${value('评审记录',s.review_saved?'已保存':'未保存')}`),
    heading('动作任务',s.job_created?'<div class="web-success">建立新任务</div>':s.job_reused?'<div class="web-success">复用已有活动任务</div>':s.error?`<div class="web-rejected">${s.error==='stale revision'?'旧评审不生效':'当前状态不适用该动作'}</div>`:note('评审通过以后，还需单独检查动作是否适用。')))
    + note('保存评审与启动动作是两个决定；版本、动作适用性和已有任务分别约束它们。');
}
function archive(scene) {
  const s=scene.state, map={http403:['403','权限失败'],http404:['404','资源不存在'],http503:['503','远端不可用'],invalid_json:['非 JSON','响应协议损坏'],'remote result':['合法 JSON','远端业务结果']};
  const result=map[s.result];
  return `<div class="web-transfer">${packet('Portal 归档连接器','向已配置的归档服务发出请求')}<span aria-hidden="true">⇄</span>${packet('远端归档服务',result?`${value('响应',result[0])}${note(result[1])}`:'等待返回',passed(scene,'fail')?'failed':'')}</div>`
    + note('权限错误、资源不存在、服务不可用和坏响应各自保留含义；连接器不把错误变成空结果，也不额外提交本地候选。');
}
function navigation(scene) {
  const s=scene.state;
  return `<div class="web-tabs"><span class="${s.binding==='previous-document'?'active':''}">先前文档</span><span class="${s.binding==='new-document'?'active':''}">新文档</span></div>`
    + `<div class="web-preview">${s.rendered?'新文档内容已采用':s.status==='error'?'恢复先前绑定，显示当前加载错误':'新文档正在加载'}</div>`
    + split(heading('当前请求',value('请求修订',s.request_revision)),heading('到达的结果',passed(scene,'ignore')?packet('旧结果','成功与失败都不能覆盖当前目标','discarded'):note(s.status==='ready'?'内容采用后再次核验代次，才写入导航历史。':s.status==='error'?'当前加载失败允许恢复之前的绑定。':'等待与当前请求匹配的结果。')));
}

function commitReplay(scene) {
  const s=scene.state, rejected=s.result==='instruction.version_conflict';
  return flow([['声明与授权',passed(scene,'enter'),'进入指定 owner 内核'],['版本检查',s.committed,rejected?'版本不匹配 · 未执行变更':'通过后才运行领域处理'],['业务结果',s.committed,s.committed?'已有提交记录':'尚未提交']])
    + split(heading('业务执行账本',`${value('领域处理次数',s.handler_runs)}${s.committed?'<div class="web-success">业务已提交</div>':rejected?'<div class="web-rejected">版本冲突 · 处理次数保持 0</div>':note('等待核验。')}`),
      heading('结果投影',s.projection==='complete'?packet(passed(scene,'replay')?'同键重放完成':'投影完成',passed(scene,'replay')?'沿已有提交记录修复投影，返回原业务引用。':'可返回已提交的业务结果。','arrived'):s.committed?packet('投影暂未完成','业务提交仍有效；重试应读取同一幂等记录。','failed'):note('未提交的业务没有可恢复投影。')))
    + note('处理次数和投影状态分别记录。投影暂态失败不能把已经提交的业务重新执行一次。');
}
function backup(scene) {
  const s=scene.state;
  return split(heading('真实图上的备份入口',`${tag(scene.inputs.available?'入口可用':'入口暂停或断开',scene.inputs.available)}${tag(scene.inputs.active?'已有活动操作':'无活动冲突',!scene.inputs.active)}${note('先读取绑定与运行状态，再决定能否触发同一 Event。')}`),
      heading('目标执行回执',flow([['Event',s.triggered,s.triggered?'已触发':'未触发'],['目标 Effect',s.accepted_effect,s.accepted_effect?'回执确认执行':'尚未确认']])) )
    + `<div class="web-backup-receipts">${packet('请求接受',s.status==='queued'?'<b class="web-success">已排队</b>':s.status==='unavailable_or_conflict'?'<b class="web-rejected">不可用或冲突，未确认接受</b>':'等待入口与目标回执')}${packet('成功快照',s.backup_success?'外部状态已确认新快照':'本情境没有确认新的成功恢复点；继续读取外部备份状态。')}</div>`
    + note('事件被触发、目标 Effect 接受、备份成功是三个不同的事实。排队回执不能更新成功恢复点。');
}

export const WEB_VIEWS = Object.freeze({
  'resident-cursors':cursors, 'document-edit':documentEdit, 'http-stream':stream,
  'refresh-generation':refresh, 'instruction-retry':retry, 'home-activation':home,
  'router-connection':router, 'web-request':request, 'identity-session':identity,
  'chatroom-revision':chatroom, 'resource-discussion':discussion, 'pulse-publication':pulse,
  'business-task':task, 'paper-recovery':paper, 'wall-action':wall,
  'archive-errors':archive, 'document-navigation':navigation,
  'isa-commit-replay':commitReplay, 'backup-receipt':backup,
});
