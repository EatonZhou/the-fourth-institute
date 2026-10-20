export const escape = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const e = escape;
const reached = (s, id) => s.frames.some(f => f.step.id === id);
const chip = (text, tone = '') => `<span class="projection-chip ${tone}">${e(text)}</span>`;
const box = (title, content, tone = '') => `<section class="projection-station ${tone}"><h3>${e(title)}</h3>${content}</section>`;
const pair = (title, value) => `<div class="projection-pair"><span>${e(title)}</span><strong>${e(value)}</strong></div>`;
const note = (s, fallback) => `<p class="projection-domain-note">${e(s.step?.observable || fallback)}</p>`;

function panel(s) {
  const state = s.state, looked = reached(s, 'lookup');
  return `<div class="projection-assembly" aria-label="包与实例装配台">
    ${box('① 包登记库', `<div class="projection-package">Panel Package<br>${chip(looked ? (s.inputs.factory === 'missing' ? '没有登记' : '找到 factory') : '等待查找', s.inputs.factory === 'missing' && looked ? 'warning' : '')}</div><p>内容和创建方法属于登记包；实例不能临时换一个 factory。</p>`, looked ? 'active' : '')}
    <div class="projection-connector" aria-hidden="true">→</div>
    ${box('② 运行实例', `<div class="projection-mount ${state.instance ? 'occupied' : ''}">${state.instance ? '<b>独立面板实例</b><span>受限 Context · 挂载 DOM</span>' : state.cleanup ? '<b>创建已终止</b><span>本次上下文与挂载点已清理</span>' : '<span>等待创建</span>'}</div>`, state.instance ? 'active' : state.cleanup ? 'warning' : '')}
    <div class="projection-connector" aria-hidden="true">→</div>
    ${box('③ 布局可见集合', `${pair('实例存在', state.instance ? '是' : '否')}${pair('集合包含此实例', state.visible ? '是' : '否')}<div class="projection-hook">${reached(s, 'visible') ? `集合已提交 → 通知 ${state.visible ? 'show' : 'hide'}` : '尚未回调 show / hide'}</div><p>先提交集合，再回调；重入时看到的仍是新集合。</p>`, state.visible ? 'active' : '')}
  </div>${note(s, '先查登记包，再创建带受限上下文的实例，最后让布局提交可见集合。')}`;
}
function scope(s) {
  const state = s.state, bound = reached(s, 'binding'), injected = reached(s, 'parameters'), denied = reached(s, 'deny');
  const gates = [
    ['已发布工作台', bound ? '按当前调用者读取' : '等待读取', bound],
    ['调用形式', s.inputs.streaming ? '流式订阅被拒绝' : s.inputs.mutates && !s.inputs.confirmed ? '变更尚未确认' : bound ? '一次调用 / 确认条件具备' : '等待检查', bound && !s.inputs.streaming && (!s.inputs.mutates || s.inputs.confirmed)],
    ['绑定与真实成员', injected ? s.inputs.resident_member ? '绑定注入，成员仍在图中' : '目标 Resident 不在绑定团队中' : '尚未进入成员检查', injected && s.inputs.resident_member],
  ];
  return `<div class="projection-scope" aria-label="投影调用边界">
    <section class="projection-frame"><h3>应用 iframe</h3><p>请求角色、流程或一次指令</p>${pair('本例目标', s.inputs.resident_member ? '绑定内成员' : '其他团队成员')}<p>请求中的名字不授予权限，也不决定 actor。</p></section>
    <div class="projection-gates">${gates.map(([title, text, ok]) => `<section class="projection-gate ${ok ? 'passed' : ''}"><span aria-hidden="true">${ok ? '✓' : '·'}</span><div><h3>${e(title)}</h3><p>${e(text)}</p></div></section>`).join('')}${pair('Lineage 参数来源', state.lineage_source === 'app.binding' ? '工作台的正式绑定' : '尚未注入')}${denied ? `<div class="projection-stop" role="status">请求在投影检查层停止<br><strong>${!s.inputs.resident_member ? '成员检查未通过' : s.inputs.streaming ? '不支持流式调用' : '变更未获确认'}</strong></div>` : state.dispatched ? '<div class="projection-route-arrow">检查通过 → 派发给领域 owner</div>' : ''}</div>
    ${box('HATG 领域 owner', `<div class="projection-receipt ${state.dispatched ? 'occupied' : ''}"><strong>${state.dispatched ? '本地演示已到达派发入口' : '尚未收到这个请求'}</strong></div><p>派发保留当前 actor、幂等键与版本条件；业务权限仍由目标领域核验。</p>`, state.dispatched ? 'active' : '')}
  </div>${note(s, '看同一请求在哪一层停下；前一层可读，不等于后一层业务可执行。')}`;
}
function focus(s) {
  const kind = s.inputs.scenario, received = reached(s, 'receive'), dropped = reached(s, 'drop');
  const revision = kind === 'newer' ? '3' : kind === 'older' ? '1' : '本场景未指定';
  const source = kind === 'wrong_source' ? '非宿主窗口' : '宿主窗口';
  const channel = kind === 'wrong_channel' ? '其他 iframe 通道' : '本 iframe 通道';
  return `<div class="projection-focus" aria-label="焦点消息接纳台">
    <section class="projection-envelope ${received ? 'arrived' : ''}"><h3>待到达的消息</h3>${pair('窗口来源', source)}${pair('通道', channel)}${pair('修订号', revision)}${chip(received ? '消息已到达' : '在途消息')}<p>消息到达顺序可以与用户选择顺序不同。</p></section>
    <div class="projection-filter"><h3>SDK 接纳条件</h3><ol><li class="${received ? 'examined' : ''}">窗口必须是本次宿主</li><li class="${received ? 'examined' : ''}">channel 必须属于本 iframe</li><li class="${received ? 'examined' : ''}">revision 必须大于已接受修订</li></ol>${s.state.accepted ? chip('通过：通知 onFocus', 'success') : dropped ? chip('忽略：不通知监听器', 'warning') : chip('等待接纳判断')}</div>
    <section class="projection-screen"><div class="projection-screen-bar">订阅面板的已接受情境</div><h3>${e(s.state.focus_ref)}</h3><strong class="projection-revision">修订 ${e(s.state.focus_revision)}</strong><p>${s.state.accepted ? '新对象替换旧情境。' : '已有情境保留；晚到或错通道消息不使它倒退。'}</p></section>
  </div>${note(s, '本例从已接受 revision 2 开始。选择晚到消息，观察旧内容为什么不能盖过当前情境。')}`;
}
function feedback(s, m) {
  const state = s.state, waiting = state.busy && !reached(s, 'send') && !reached(s, 'abort');
  const sealed=reached(s,'confirm'), aborted=reached(s,'abort'), sent=reached(s,'send');
  const preflightRejected=reached(s,'reject_simulated');
  const changedFocus=aborted && s.inputs.scenario==='focus_changed', changedBinding=aborted && s.inputs.scenario==='binding_changed';
  const budgetTitle=!sealed ? '未进入暂停' : state.timer_paused ? '剩余额度暂停' : '恢复剩余额度';
  const budgetReason=!sealed ? '尚未打开原生确认。' : state.timer_paused ? aborted ? '原确认已失效，等待清理阶段发出恢复通知。' : sent ? '已到发送入口，等待清理阶段发出恢复通知。' : '等待用户决定，不扣活动时间。' : '清理阶段结束原确认等待；继续使用暂停前剩余额度。';
  const alternatives = waiting ? m.cases.filter(c => {
    const path = c.path.filter(id => id !== 'load_case_inputs');
    return path.length > s.position && s.frames.every((frame, i) => path[i] === frame.step.id);
  }) : [];
  return `<div class="projection-feedback-flow" aria-label="封存请求经过当前情境复查再进入Chat">
    <section class="projection-confirmation ${waiting ? 'active' : ''}"><div class="projection-dialog-title">宿主原生确认 · 本地示意</div><h3>${sealed ? '封存的确认请求' : '确认前检查'}</h3>
      ${pair('确认对象',s.inputs.scenario==='simulated' ? '模拟对象' : sealed ? '确认开始时的对象' : '当前阅读对象')}${pair('情境修订',sealed ? '保留确认开始时的修订' : '尚未封存')}${pair('收件团队',sealed ? '确认开始时的门面绑定' : '等待检查绑定')}
      <p>用户原文和所指对象随原请求保留；本例不填造真实姓名、消息或修订号。</p><details data-projection-detail="confirmation"><summary>技术情境</summary><p>五字段、anchor、scene 随原请求。确认开始后的变化不会改写这份请求。</p></details>
      ${waiting ? `<p>在原确认中比较接下来的本地变化：</p><div class="projection-dialog-actions">${alternatives.map(c => `<button type="button" data-projection-action="alternative" data-case-id="${e(c.id)}" data-projection-focus="alternative:${e(c.id)}">${e(c.title)}</button>`).join('')}</div>` : ''}
      ${s.inputs.scenario==='simulated' && reached(s,'reject_simulated') ? '<p class="projection-stop">在打开确认前停止：模拟对象不能触发真实反馈。</p>' : ''}
    </section>
    <div class="projection-connector" aria-hidden="true">→</div>
    <section class="projection-recheck ${aborted ? 'stopped' : sent ? 'passed' : ''}"><h3>当前情境复查闸门</h3>
      ${pair('当前对象',!sealed ? '尚无封存对象可比较' : changedFocus ? '已经切换；与封存对象不同' : '仍是同一对象')}${pair('当前绑定',!sealed ? '尚未进入确认后复查' : changedBinding ? '已经改变；与原请求不同' : '未观察到绑定变化')}
      ${pair('对象权限',preflightRejected ? '未进入发送前核验' : sent ? '本分支再次核验通过' : '进入发送前再次核验')}
      <p>${preflightRejected ? '确认前检查已拒绝，本次不会打开原确认，也不会执行发送前复查。' : !sealed ? '先完成确认前检查；当前还没有待确认请求。' : changedFocus || changedBinding ? '原确认失效，封存请求留在这道闸门前。随后确认也不能把它交给 Chat。' : aborted ? '用户取消，原确认结束；不调用 Chat。' : sent ? '当前情境、对象权限和门面绑定再次核验后，才进入原生 Chat。' : '尚未经过最终核验。点击原确认中的本地选择，观察请求能否通过。'}</p>
      ${chip(preflightRejected ? '未进入：确认前已停止' : !sealed ? '尚未进入确认后复查' : aborted ? '停止：不发送' : sent ? '本地检查通过' : '等待原确认决定',aborted ? 'warning' : sent ? 'success' : '')}
    </section>
    <div class="projection-connector ${!sent ? 'projection-route-closed' : ''}" aria-hidden="true">${aborted || preflightRejected ? '×' : '→'}</div>
    ${box('原生 Chat 入口',`${chip(state.would_send ? '达到 send 入口（本地模拟）' : '尚未收到这份请求',state.would_send ? 'success' : '')}<p>入口调用、投递回执、任务完成和业务结果分别观察。此处没有真实发送。</p>`,sent ? 'active' : '')}
  </div><section class="projection-feedback-budget" aria-label="确认等待与清理预算"><h3>SDK 活动预算</h3><div class="projection-budget ${state.timer_paused ? 'paused' : ''}"><strong>${budgetTitle}</strong><span>${budgetReason}</span></div><p>恢复剩余额度，不重新获得完整预算。当前情境只展示暂停与恢复，不填造耗时。</p></section>`;
}
function semantic(s) {
  const resolved = reached(s, 'resolve'), rejected = reached(s, 'reject');
  const status = {current:'当前实现',proposed:'拟议方案',verified:'已验证结果',simulated:'模拟演示'}[s.inputs.status];
  return `<div class="projection-semantic" aria-label="语义对象检查与展示边界">
    <section class="projection-object-card"><h3>待加入的对象 · 演示输入</h3>${chip(status)}<dl>
      <dt>对象地址 · ref</dt><dd>待核验的对象引用</dd><dt>面板角色 · kind</dt><dd>本例未指定具体角色</dd>
      <dt>状态 · status</dt><dd>${e(s.inputs.status)}</dd><dt>判断依据 · source</dt><dd>${s.inputs.source_present ? '已提供依据引用' : '缺少依据'}</dd>
      <dt>观察时刻 · observed_at</dt><dd>结构校验要求带时区的 ISO 时间</dd></dl><p>这里只描述输入条件，不生成真实资源或观察时间。</p></section>
    <section class="projection-validation"><h3>两道检查，分别负责什么</h3>
      <div class="projection-gate ${reached(s,'shape') ? 'passed' : ''}"><span>1</span><div><h3>形状与证据要求</h3><p>严格五字段；状态、角色与时间合法。标为 verified 时必须提供依据。</p>${chip(rejected && s.inputs.status==='verified' && !s.inputs.source_present ? '缺少依据，停在这里' : reached(s,'shape') ? '已检查输入结构' : '等待检查',rejected && !resolved ? 'warning' : '')}</div></div>
      <div class="projection-gate ${s.state.accepted ? 'passed' : ''}"><span>2</span><div><h3>当前用户能否读取</h3><p>按真实当前 actor 解析 ref 和已提供的 source。状态标签不会授予权限。</p>${chip(resolved ? s.state.accepted ? '对象和依据可读' : rejected ? '来源不可读' : '正在核验引用' : '尚未进入资源核验',rejected && resolved ? 'warning' : '')}</div></div>
    </section>
    <section class="projection-visible-table"><h3>展示对象表</h3>${s.state.revealed ? '<div class="projection-valid-object">通过的模拟对象<br><small>现在才可加入摘要、计数与连线</small></div>' : '<div class="projection-empty-object">没有加入这个对象</div>'}<p>${rejected ? '被拒绝的对象不计入摘要、数量或连线。' : '只有结构与读取权限都通过，才允许展示。'}</p></section>
  </div>`;
}
function generation(s) {
  const state=s.state, conflict=!!state.error, interrupted=s.inputs.scenario==='crash_after_update' && state.writes===1 && !state.receipt;
  const revision=s.inputs.scenario==='moved_revision' ? '外部已推进，具体版本未给出' : String(state.revision);
  return `<div class="projection-generation" aria-label="生成结果保存与中断恢复">
    <section class="projection-job"><h3>同一个生成任务</h3><div class="projection-lock">任务锁内核验</div><p>工作台、Resident 和基线都要与任务一致；内容形成稳定摘要。</p>${pair('任务基线','7')}${pair('本次提交',s.inputs.scenario==='changed_payload' ? '相同任务，内容不同' : '同一份生成内容')}<p>提交内容形成草稿；发布需要另行明确选择。</p></section>
    <div class="projection-save-records"><section class="projection-pending ${state.pending ? 'active' : ''}"><h3>持久待保存记录</h3>${chip(state.pending ? 'pending_save 已保存' : state.receipt ? '回执完成后移除 pending' : '尚未保存')}<p>先记下内容摘要、内容与保存者，再改变页面。</p></section>
      <section class="projection-revision-card ${state.writes ? 'active' : ''}"><h3>工作台页面</h3>${pair('版本观察',revision)}${pair('本次页面应用次数',state.writes)}${interrupted ? '<p class="projection-interruption">页面已经改变，最终回执尚未保存。中断不能把这次写入当作未发生。</p>' : ''}</section>
      <section class="projection-saved-receipt ${state.receipt ? 'active' : ''}"><h3>持久保存回执</h3>${chip(state.receipt ? 'save_receipt 已保存' : '尚无最终回执',state.receipt ? 'success' : '')}<p>回执保留任务、页面、版本、内容摘要和保存者。</p></section></div>
    <section class="projection-recovery"><h3>${conflict ? '这次提交被拒绝' : reached(s,'recover') ? '恢复只补齐回执' : '同包恢复的核对条件'}</h3>
      <ol><li>pending 的摘要与保存者匹配</li><li>当前页面版本正好是基线 + 1</li><li>页面内容与待保存内容相同</li></ol>
      ${reached(s,'recover') ? chip('已有更新被识别，应用次数仍为 1','success') : conflict ? chip(state.error==='changed_payload' ? '同任务不能换内容' : '外部版本推进，不覆盖','warning') : '<p>条件同时成立才复用已有页面更新，不能仅凭一个任务 ID 跳过校验。</p>'}
      <p>保存回执记录页面保存结果，不是业务任务已经完成的证明。</p>
    </section></div>`;
}
function association(s) {
  const state=s.state, rejected=reached(s,'reject');
  const value=state.value.resident_id==='previous' ? '原来记住的 Resident（previous）' : state.value.resident_id==='selected' ? '这次选择的 Resident（selected）' : '本次关联值';
  return `<div class="projection-association" aria-label="界面关联的用户边界与记录替换">
    <section class="projection-association-key"><h3>定位一条界面记录</h3><div class="projection-key-parts"><span>当前用户</span><b>＋</b><span>界面命名空间</span><b>＋</b><span>记录键</span></div><p>用户来自当前 Session；请求不能替自己指定另一位用户。</p><div class="projection-ledger-row"><span>这条记录中的引用</span><strong>${e(value)}</strong></div>${chip(state.written ? '整条 value 与更新时间已提交' : rejected ? '超限拒绝，旧值保留' : '等待事务检查',rejected ? 'warning' : state.written ? 'success' : '')}<p>更新整体替换这一条值。其他键、其他命名空间和其他用户的关联不变。</p></section>
    <section class="projection-association-transfer"><h3>换设备以后</h3><div class="projection-device">另一设备 · 同一登录用户</div><p>${state.written ? '可以读取这条已经保存的引用。' : '尚未提交新引用，已保存的旧记录仍有效。'}</p><div class="projection-permission-gate">使用引用时<br><strong>重新检查对象当前权限</strong></div>${pair('这条关联额外授予的权限',state.permissions_granted ? '模型声明已授予' : '无')}<p>记住对象地址，不等于获得这个对象的访问权。浏览器草稿和布局偏好由各自的偏好层保存。</p></section>
  </div>`;
}
import {HOST_PROJECTION_VIEWS} from './projection-mechanisms.host-views.js';
export const PROJECTION_VIEWS = Object.freeze({
  ...HOST_PROJECTION_VIEWS,
  'portal.architecture.panel-lifecycle.v1': panel,
  'portal.architecture.projection-scope.v1': scope,
  'portal.architecture.focus-revision.v1': focus,
  'portal.architecture.feedback-confirmation.v1': feedback,
  'portal.architecture.semantic-validation.v1': semantic,
  'portal.architecture.generation-save.v1': generation,
  'portal.architecture.ui-association.v1': association,
});
