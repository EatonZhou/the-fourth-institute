export const escape = value => String(value ?? '').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const tokens = (values, tone='') => `<span class="ad-tokens">${values.map(v=>`<span class="ad-token ${tone}">${escape(v)}</span>`).join('')}</span>`;
const flow = (label='') => `<div class="ad-flow" aria-hidden="true"><span>↓</span>${escape(label)}</div>`;
const text = (title,body,classes='') => `<div class="ad-piece ${classes}"><strong>${escape(title)}</strong><span>${escape(body)}</span></div>`;
const mark = (active,label) => `<span class="ad-mark ${active?'is-written':''}">${active?'●':'○'} ${escape(label)}</span>`;

function segment(scene) {
  const s=scene.state, isNew=s.identity==='new', done=scene.complete;
  return `<div class="ad-identity"><div class="ad-layer-label">事件身份 · Segment</div><div class="ad-event-rows">
    ${text('已有事件','身份与内容四元组已固定','ad-event')}
    ${text(isNew?'待写：不同的事件身份':s.identity==='same'?'重放：同一事件身份':'覆盖请求：原身份、另一正文',
      s.error?'拒绝覆盖，原事件保留':done?(isNew?'新事件已插入':'返回已有 ID，未新增事件'):'等候身份核对',`ad-event ${s.error?'is-error':done?'is-written':''}`)}
    </div><div class="ad-payload-links"><span>已有内容引用</span><span>${isNew?(s.payload_shared?'相同字节 → 复用引用':'核对正文摘要'):s.identity==='same'?'四元组一致 → 同一事件':'四元组冲突 ⊣ 停止'}</span></div>
    <div class="ad-payload">Payload · 一份物理正文<small>按原始字节的 SHA-256 复用</small></div>
    <p class="ad-conclusion">本次新增事件：<b>${s.segment_count_delta}</b>。${isNew?'相同正文仍可属于两个不同事件。':'事件身份核对包含类型、内容引用、内容类型与 metadata。'}</p></div>`;
}
function trace(scene) {
  const s=scene.state, changed=s.current.includes('s3');
  return `<div class="ad-revision"><div class="ad-trace-row"><strong>父 Agent 当前轨迹</strong>${tokens(s.current,'ad-current')}<span>${changed?'当前引用已选择 s3':'当前有效成员'}</span></div>
    <div class="ad-trace-row"><strong>先前 Fork 的轨迹</strong>${tokens(s.fork,'ad-fork')}<span>独立的有序引用</span></div>
    <div class="ad-transform ${changed?'is-written':''}">${tokens(['s1','s2'])}<span class="ad-transform-arrow">${changed?'→ transformed_from →':'待原子转换 →'}</span>${tokens(['s3'],changed?'ad-current':'ad-pending')}</div>
    <div class="ad-archive"><strong>审计源保留</strong>${tokens(['s1','s2'])}<span>${s.source_retained?'旧 Segment 仍可追查':'未观察到保留'}</span></div>
    <p class="ad-conclusion">${s.error?'来源已退出当前窗口，事务拒绝转换；保留调用前的成员。':changed?'父轨迹改选新版本，Fork 仍引用原 s1/s2。':'请观察当前引用的替换与 Fork 引用的稳定。'}</p></div>`;
}
function receipt(scene) {
  const s=scene.state;
  return `<div class="ad-ledger"><div class="ad-ledger-head"><span>写入边界</span><span>当前记录</span><span>读者可判断</span></div>
    <div class="ad-ledger-row ad-receipt"><strong>输入接受回执<small>AgentData / 运行记录</small></strong>${mark(s.accepted,s.accepted?'已持久保存':'未登记')}<span>${s.accepted?'Provider 接受过本轮输入':'没有接受记录'}</span></div>
    <div class="ad-ledger-row ad-terminal"><strong>执行终态<small>Scheduler 提交</small></strong><span class="ad-status ${s.execution_status==='failed'?'is-error':''}">${escape(s.execution_status)}</span><span>${s.execution_status==='running'?'尚未提交终态':s.execution_status==='failed'?'本次执行失败':'已保存首次完成终态'}</span></div>
    <div class="ad-ledger-rule">回执跨越终态变化保留 ─────────────────</div>
    <p class="ad-conclusion">${s.error?(s.event==='empty_turn'?'没有非空 turn，接受记录不能建立。':'冲突的第二份终态被拒绝；首次 completed 保持。'):s.accepted&&s.execution_status==='failed'?'执行失败不会撤销已经发生的输入接受。':'接受事实与执行结果各有自己的记录。'}</p></div>`;
}
function readViews(scene) {
  const s=scene.state;
  return `<div class="ad-read-split"><div class="ad-trace-source"><strong>同一条 Agent 轨迹</strong><span>正文按有序 Segment 读取；运行通知另有记录。</span></div>
    <div class="ad-split-arrows" aria-hidden="true">↙ 查询投影 ↘</div><div class="ad-reading-columns">
    <section class="ad-chat-window"><h3>Chat 展示</h3><div class="ad-chat-bubble">当前通信正文</div><div class="ad-notice ${s.chat_includes_notice?'is-written':'ad-pending'}">运行通知${s.chat_includes_notice?'已合入':'待查询'}</div><p>通知有展示位置，不新增正文 Segment。</p></section>
    <section class="ad-model-window"><h3>模型窗口</h3><div class="ad-window-start">从有效压缩边界开始</div>${s.window.length?tokens(s.window):'<div class="ad-empty-window">尚未取得上下文窗口</div>'}<div class="ad-notice-excluded">运行通知 ⊣ 不进入模型窗口</div></section></div>
    <p class="ad-conclusion">${s.error?'截止消息不属于本 Agent trace：拒绝查询，窗口保持空。':'同一轨迹可有不同读取策略；展示资格不写回内容身份。'}</p></div>`;
}
function actualInput(scene) {
  const s=scene.state, rewritten=s.effective_input==='harness-transformed';
  return `<div class="ad-input-sheet"><div class="ad-input-label">Fork 合成输入的边界</div><div class="ad-inherited"><strong>继承区</strong><span>已有上下文正文</span><b>保持原样</b></div>
    <div class="ad-cut-line">新输入边界 · ${s.canonical_occurrence==='one'?'唯一可定位':'丢失或出现多次'}</div>
    <div class="ad-edit-region ${rewritten?'is-written':''} ${s.error?'is-error':''}"><strong>${rewritten?'实际输入版本':'原新输入'}</strong><span>${rewritten?'Harness 处理后的 prompt / media':'尚未替换的输入正文'}</span></div>
    <div class="ad-replacement"><span>Harness 实际输入</span><b>${s.error?'⊣':rewritten?'↑ 已替换':'↑ 待替换'}</b><span>只进入新输入区</span></div>
    <p class="ad-conclusion">${s.error?'边界不能唯一定位，整次替换被拒绝；原新输入和继承区均保留。':rewritten?'当前新输入引用已替换，Fork 继承的正文未改。':'替换只作用于唯一的新输入片段。'}</p></div>`;
}
function acceptance(scene) {
  const s=scene.state;
  return `<div class="ad-acceptance"><div class="ad-gate-track">
    ${text('Provider 事件',s.identity==='missing_turn'?'turn 为空':s.identity==='changed_session'?'事件中的会话身份改变':'实际会话身份与非空 turn')}${flow('关联执行')}
    ${text('执行 → Provider Session',s.session_linked?'会话关联已核对并保存':s.identity==='changed_session'&&s.error?'会话冲突：拒绝第二个身份':'待核对会话',s.session_linked?'is-written':'')}${flow('先关联，再登记')}
    ${text('输入接受回执',s.receipt?'非空 turn 已保存为接受回执':s.error?'未写入回执':s.session_linked?'会话已关联，待登记非空 turn 回执':'等待会话关联',s.receipt?'is-written':'')}</div>
    <aside class="ad-binding-boundary"><strong>稳定 Binding</strong><span>本投影步骤没有更新它。</span><p>执行内会话关联与稳定 Binding 是两个责任边界。</p></aside>
    <p class="ad-conclusion">${s.error?(s.identity==='missing_turn'?'空 turn 在登记前被拒绝。':'会话改变在关联处被拒绝。'):'沿路径观察：会话关联先建立，随后才保存接受回执。'}</p></div>`;
}
function completed(scene) {
  const s=scene.state, fallback=s.result==='fallback_response';
  return `<div class="ad-output-stream"><div class="ad-output-inlets"><div class="ad-activity"><strong>流式活动 / 运行卡片</strong><span>临时活动不覆盖完成内容</span><span class="ad-stream-dots" aria-hidden="true">· · · · · · ⊣</span></div>
    <div class="ad-complete-inlet ${s.segments?'is-written':''}"><strong>${fallback?'完成返回值 response':'已完成 Provider 条目'}</strong><span>${fallback?'非空 response，且没有捕获 final':'完整 item 携带稳定身份'}</span>${flow(fallback?'固定代码回填分支':'完成内容采集')}</div></div>
    <div class="ad-content-store"><strong>不可变内容记录 · Segment</strong><span class="ad-content-count">${s.segments}</span><span>${s.source_kind==='completed_provider_item'?'来源：已完成 Provider item':s.source_kind==='completion_response_fallback'?'来源：完成返回值回填':'本案例尚未写入完成内容'}</span></div>
    ${flow('收尾校验')}<div class="ad-output-tail"><strong>用量 / turn 记录</strong><span>${scene.prefix.includes('output')?'已记录收尾信息':'等待收尾'}</span><span>Execution 终态由 Scheduler 处理，本步未写终态。</span></div>
    ${fallback?'<p class="ad-discrepancy">固定代码存在此回填分支；“仅完成事件采集”的规范描述仍待事实 owner 对齐。可从依据查看两侧原文。</p>':''}
    <p class="ad-conclusion">${s.error?(s.result==='missing_session'?'缺少可关联的会话身份，完成结果被拒绝。':'compact 只有完成信号，没有规范正文，收尾被拒绝。'):s.segments?'内容入库与执行终态是两个独立动作。':'先选择情境，观察什么能成为完成内容。'}</p></div>`;
}
export const AGENT_DATA_VIEWS = Object.freeze({'segment-identity':segment,'trace-revision':trace,'execution-receipt':receipt,'read-views':readViews,'actual-input':actualInput,'acceptance-projection':acceptance,'completed-content':completed});
