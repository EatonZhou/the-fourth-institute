import './resource-mechanisms.policy.css';

// Only presentation of the parent's frozen simulation. No authority, I/O or state changes.
const escape = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const paragraph = text => `<p class="rp-note">${escape(text)}</p>`;
const label = (text, tone='idle') => `<span class="rp-status rp-${tone}">${escape(text)}</span>`;
const arrow = (text, active=false) => `<div class="rp-arrow ${active?'rp-active':''}"><span>${escape(text)}</span><b aria-hidden="true">→</b></div>`;
const panel = (title, body, tone='idle') => `<section class="rp-box rp-${tone}"><h4 class="rp-box-title">${escape(title)}</h4>${body}</section>`;
const figure = (kind, title, body, note='') => `<figure class="rp-figure rp-${kind}" data-rp-view="${kind}"><figcaption class="rp-heading">${escape(title)}</figcaption>${body}${note?paragraph(note):''}</figure>`;
const textList = values => (Array.isArray(values)?values:[]).map(value=>`<span class="rp-token">${escape(value)}</span>`).join('');
const happened = (ctx, suffix) => ctx.frames.slice(0, ctx.cursor+1).some(frame=>frame.step?.id===`${ctx.mechanism.id}.${suffix}`);
function boundary(ctx, suffix, title) {
  const index=ctx.frames.findIndex(frame=>frame.step?.id===`${ctx.mechanism.id}.${suffix}`);
  return index<0?'':`<button class="rp-boundary" type="button" data-seek="${index}" aria-current="${ctx.cursor===index?'step':'false'}">${escape(title)}</button>`;
}
const boundaries = (...buttons) => `<nav class="rp-boundaries" aria-label="跳到机制边界">${buttons.join('')}</nav>`;

function authorization(ctx) {
  const s=ctx.state, denied=String(s.outcome).startsWith('instruction.'), exempt=s.credential_gate==='exempt';
  const credential=denied?'凭证关口拒绝':exempt?'当前指令无凭证要求':s.credential_gate==='allow'?'凭证要求满足':'等待检查保护集合';
  const business=s.outcome==='domain_rejected'?'业务规则拒绝':s.outcome==='success'?'业务规则通过':s.owner_called?'业务 owner 已进入，等待裁定':'业务关口尚未进入';
  return figure('gates','一次请求，两个不同的裁定',
    `<div class="rp-request">${label(s.mutates?'写入请求':'读取请求')}${paragraph('执行身份随请求进入；凭证豁免不会产生管理员身份。')}</div>
    <div class="rp-flow">${panel('ISA 凭证关口',label(credential,denied?'bad':s.credential_gate!=='pending'?'good':'idle')+paragraph(exempt?'当前保护集合没有要求，继续交给业务规则。':'由当前保护集合决定所需凭证。'),denied?'bad':'idle')}
    ${arrow(s.owner_called?'交给业务 owner':denied?'在此停止':'等待前一道裁定',s.owner_called)}
    ${panel('业务关口',label(business,s.outcome==='domain_rejected'?'bad':s.outcome==='success'?'good':'idle')+paragraph('业务对象自己的约束仍然生效。'),s.outcome==='domain_rejected'?'bad':'idle')}</div>
    <div class="rp-result">${label(s.outcome==='pending'?'尚无返回结果':s.outcome,denied||s.outcome==='domain_rejected'?'bad':s.outcome==='success'?'good':'idle')}</div>
    ${boundaries(boundary(ctx,'owner','进入业务关口'),boundary(ctx,'business-deny','查看业务拒绝'),boundary(ctx,'deny-read','查看读拒绝'),boundary(ctx,'deny-write','查看写拒绝'))}`);
}

function subjects(ctx) {
  const s=ctx.state, revoked=happened(ctx,'inactive'), foreign=happened(ctx,'foreign');
  const effective=Array.isArray(s.effective_sources)?s.effective_sources:[];
  return figure('subjects','执行绑定决定哪些来源可以参与授权',
    `<div class="rp-binding ${revoked||foreign?'rp-broken':''}">
      <div class="rp-identity"><b class="rp-name">本次认证执行</b><p class="rp-detail">executor · execution</p></div>
      <div class="rp-binding-check">${label(foreign?'执行绑定不匹配':s.validated?'绑定已验证':'等待绑定验证',foreign?'bad':s.validated?'good':'idle')}<span class="rp-binding-line" aria-hidden="true"></span></div>
      <div class="rp-identity"><b class="rp-name">实际权限主体</b><p class="rp-detail">保存 executor / execution 绑定与生命周期</p>${label(revoked?'主体已撤销或过期':s.validated?'当前生命周期有效':'生命周期待检查',revoked?'bad':'idle')}</div>
    </div>
    <div class="rp-source-conduit"><div class="rp-source-list"><b class="rp-name">主体声明的来源</b><div class="rp-tokens">${textList(s.sources)||label('未列来源')}</div></div>
    ${arrow(revoked||foreign?'阻断':effective.length?'验证后组合':'等待验证',effective.length>0)}
    <div class="rp-source-list"><b class="rp-name">本次有效来源</b><div class="rp-tokens">${effective.length?textList(effective):label('有效来源尚未形成',revoked||foreign?'bad':'idle')}</div></div></div>
    ${boundaries(boundary(ctx,'validate','核对执行绑定'),boundary(ctx,'inherit','查看来源组合'))}`,
    '执行器身份不自动成为额外授权来源。每次使用主体都要重新核对撤销、过期和当前执行绑定。');
}

function policy(ctx) {
  const s=ctx.state, denied=s.result==='forbidden', conflict=s.result==='ResourceVersionConflict';
  const verdict=denied?'旧策略不授权':conflict?'版本冲突，候选未生效':s.changed?'策略和审计已一起提交':s.locked?'已锁定旧策略，检查授权和预期版本':'等待锁定当前策略';
  return figure('policy','修改策略的权力，来自修改前的策略',
    `<div class="rp-policy-comparison">
    <section class="rp-policy-current"><h4 class="rp-box-title">当前策略 · BEFORE</h4><div class="rp-version">版本 ${escape(s.before_version)}</div>${label(s.locked?'事务内已锁定':'修改前的授权依据',s.locked?'good':'idle')}${paragraph('授权检查只读此侧。')}</section>
    <section class="rp-policy-candidate"><h4 class="rp-box-title">请求中的候选策略</h4><div class="rp-candidate-lines" aria-hidden="true"><i class="rp-stroke"></i><i class="rp-stroke"></i><i class="rp-stroke"></i></div>${label(s.changed?'候选已生效':'候选未生效',s.changed?'good':'idle')}${paragraph('候选内容不能为这次编辑补授权限。')}</section></div>
    <div class="rp-policy-commit ${s.changed?'rp-good':denied||conflict?'rp-bad':''}"><b class="rp-name">${escape(verdict)}</b>
    <div class="rp-commit-members">${label('策略版本')}${label('审计记录')}<span class="rp-detail">同一事务边界</span></div></div>
    ${boundaries(boundary(ctx,'lock','查看锁定的旧策略'),boundary(ctx,'commit','查看原子提交'),boundary(ctx,'deny','查看自授权被拒'),boundary(ctx,'conflict','查看版本冲突'))}`,
    '本图不填造策略条文或提交后的版本号。下一请求重新读取已提交策略。');
}

function postgres(ctx) {
  const s=ctx.state, retried=happened(ctx,'retry'), exhausted=happened(ctx,'deadline');
  const count=Number.isInteger(s.attempts)&&s.attempts>=0?s.attempts:0;
  const rows=[];
  for(let n=1;n<=Math.min(count,20);n++){
    const rollback=(n<count)||exhausted;
    const state=rollback?(n<count?'冲突后回滚，整个操作重来':'期限内失败，未提交'):s.committed?'SQL 已提交':'当前尝试进行中';
    rows.push(`<div class="rp-ledger-row ${rollback?'rp-rolled-back':s.committed?'rp-committed':''}"><b class="rp-attempt">尝试 ${n}</b><div class="rp-transaction-body"><span class="rp-sql-mark">完整领域操作</span>${label(state,rollback?'bad':s.committed?'good':'idle')}</div></div>`);
  }
  const callback=s.callback==='logged_failure'?'回调错误已记录':s.callback==='success'?'回调执行完成':s.committed?'等待 after_commit 回调':'提交前不会执行回调';
  return figure('ledger','事务可以重试；提交后的回调独立收尾',
    `<div class="rp-ledger-layout"><section class="rp-ledger"><h4 class="rp-box-title">数据库事务账本</h4>${rows.join('')||paragraph('尚未开始领域操作')}
    ${retried?paragraph('当前样例展示两次完整尝试；嵌套事务冲突交给最外层处理。'):''}</section>
    <section class="rp-after-commit"><h4 class="rp-box-title">事务边界之外</h4>${arrow(s.committed?'提交已完成':'等待提交',s.committed)}${label(callback,s.callback==='logged_failure'?'bad':s.callback==='success'?'good':'idle')}
    ${paragraph(s.committed?'SQL 已提交；回调失败不会回滚或重做这次写入。':'没有提交的事务不会触发提交后通知。')}</section></div>
    ${boundaries(boundary(ctx,'retry','看整次重试'),boundary(ctx,'commit','停在提交边界'),boundary(ctx,'callback-fail','看回调失败'),boundary(ctx,'deadline','看期限失败'))}`);
}

function storage(ctx) {
  const s=ctx.state, late=s.reported==='resource.mutation_post_commit_failed', early=s.reported==='domain_error';
  return figure('commit-split','正文先成为事实，授权与索引随后收敛',
    `<div class="rp-split-store"><section class="rp-durable ${s.domain_committed?'rp-good':''}"><h4 class="rp-box-title">领域正文 / 版本</h4>
    <div class="rp-file-sheet">${label(s.domain_committed?'正文已提交':early?'提交前失败':'正文尚未提交',s.domain_committed?'good':early?'bad':'idle')}<span class="rp-file-fold" aria-hidden="true"></span></div></section>
    ${arrow(s.domain_committed?'提交后同步':early?'未进入后处理':'等待正文',s.domain_committed)}
    <section class="rp-projection ${late?'rp-bad':''}"><h4 class="rp-box-title">授权及索引投影</h4>${label(late?'后处理失败':s.reported==='success'?'投影同步完成':early?'尚未开始':'等待同步',late?'bad':s.reported==='success'?'good':'idle')}
    ${paragraph(late?'文件保持已提交内容。恢复处理应收敛投影，不能因本次错误盲目再次写正文。':'投影完成与正文提交各有自己的时点。')}</section></div>
    ${boundaries(boundary(ctx,'write','查看正文已提交'),boundary(ctx,'projection-failed','查看后处理失败'),boundary(ctx,'project','查看投影完成'))}`);
}

function workspace(ctx) {
  const s=ctx.state, failed=Boolean(s.failure);
  const failure={not_found:'当前关系不允许访问',unsafe_file:'链接或非普通目标被拒绝',invalid_range_or_size:'大小或范围超限'}[s.failure];
  return figure('workspace','权限通过后，沿目录固定一个文件描述符',
    `<div class="rp-path-root"><b class="rp-name">Workspace 授权根</b>${label(s.failure==='not_found'?'未越过权限关口':s.fd_pinned?'权限已通过':'先检查当前关系',s.failure==='not_found'?'bad':'idle')}
    <div class="rp-path-chain"><span class="rp-path-segment">根目录</span><b class="rp-path-join" aria-hidden="true">/</b><span class="rp-path-segment">逐级目录 · NOFOLLOW</span><b class="rp-path-join" aria-hidden="true">/</b><span class="rp-path-segment ${s.failure==='unsafe_file'?'rp-bad':''}">普通文件</span></div></div>
    <div class="rp-fd-transfer">${panel('固定的文件描述符',label(s.fd_pinned?'已固定 fd':'尚未固定',s.fd_pinned?'good':'idle')+paragraph('读取同一 inode，避免重新按路径取另一个文件。'))}
    ${arrow(s.returned?'从此 fd 读取':failed?'本次读取停止':'检查读取范围',s.returned)}
    ${panel('本次返回',label(s.returned?'固定文件的正文':failed?failure:'尚未返回正文',failed?'bad':s.returned?'good':'idle'))}</div>
    ${boundaries(boundary(ctx,'walk','查看已固定 fd'),boundary(ctx,'read','读取同一 fd'),boundary(ctx,'limit','查看超限拒绝'))}`,
    '当前工作目录和路径布局不授予权限；大小或范围错误保留其真实原因。');
}

const families=[
  {id:'effect',title:'Effect',owner:'lineage.automation',fields:'activation · purpose · size · digest',use:'恢复材料与正式输出分别管理'},
  {id:'harness',title:'Harness',owner:'worker.harness',fields:'handle · scope grant · envelope',use:'校验作用域后读取输出片段'},
  {id:'application',title:'应用包',owner:'web',fields:'receipt · index · 文件校验',use:'读取冻结应用包声明的资产'},
  {id:'script',title:'ScriptRun',owner:'lineage.automation',fields:'run_dir / io / artifacts',use:'运行文件布局，权限仍由脚本入口判断'},
];
function artifacts(ctx) {
  const s=ctx.state, selected=families.find(f=>f.id===s.family), routed=s.authority!=='unselected';
  return figure('artifact-routing','引用携带用途，交给对应的产物负责者',
    `<div class="rp-reference">${label('当前引用家族')}<strong class="rp-reference-name">${escape(selected?.title||s.family)}</strong>${paragraph('单独一个 SHA-256 不包含读取权或生命周期。')}</div>
    <div class="rp-router">${families.map(f=>`<section class="rp-route ${f.id===s.family?'rp-route-selected':''}"><h4 class="rp-box-title">${escape(f.title)}</h4><span class="rp-owner">${escape(f.owner)}</span><p class="rp-detail">${escape(f.fields)}</p><p class="rp-detail">${escape(f.use)}</p>${f.id===s.family?label(routed?'已选定此负责者':'等待分派',routed?'good':'idle'):''}</section>`).join('')}</div>
    <div class="rp-result">${label(s.result==='usable'?'本家族校验通过，可以使用':s.result==='unusable'?'引用或内容校验失败，停止使用':'尚未完成引用校验',s.result==='usable'?'good':s.result==='unusable'?'bad':'idle')}</div>`);
}

function writer(ctx) {
  const s=ctx.state, absolute=s.address_kind==='absolute', conflict=s.route==='instruction.version_conflict', late=s.route==='notify_error_after_file_commit';
  const forbidden=s.route==='forbidden';
  return figure('write-routes','两种入口走各自的写入边界',
    `<div class="rp-write-branches"><section class="rp-write-branch ${!absolute?'rp-route-selected':''}"><h4 class="rp-box-title">统一资源地址</h4>
    <div class="rp-lock-enclosure"><b class="rp-name">父目录 flock EX</b><div class="rp-lock-interior"><span class="rp-detail">比较内容摘要</span><span class="rp-detail">同目录 temp → dir_fd replace</span></div>
    ${!absolute?label(s.directory_locked?'当前持有目录锁':s.file_replaced?'替换完成，目录锁已释放':conflict?'摘要冲突，锁已释放':'尚未持锁',conflict?'bad':'idle'):''}</div>
    ${paragraph('此资源地址路径未显式 fsync。通知在锁外执行。')}</section>
    <section class="rp-write-branch ${absolute?'rp-route-selected':''}"><h4 class="rp-box-title">绝对路径</h4><div class="rp-delegation"><b class="rp-name">Lineage writer</b>${paragraph('按其授权与版本检查调用文本替换 helper。')}${absolute?label(s.file_fsynced?'文件已 fsync':'等待写入',s.file_fsynced?'good':'idle'):''}</div>${paragraph('不能把另一分支的父目录锁当作这里的锁内 CAS 保证。')}</section></div>
    <div class="rp-result">${label(forbidden?'写权限拒绝，未替换':conflict?'摘要冲突，返回 current_version，未替换':late?'通知异常；文件保持替换后内容':s.file_replaced?'文件已替换':'文件尚未替换',forbidden||conflict||late?'bad':s.file_replaced?'good':'idle')}
    ${s.route==='notified'?label('锁外通知完成','good'):''}</div>
    ${boundaries(boundary(ctx,'lock','查看锁内比较'),boundary(ctx,'replace','停在文件替换后'),boundary(ctx,'notify-error','查看通知异常'),boundary(ctx,'absolute','查看绝对路径结果'))}`);
}

function effect(ctx) {
  const s=ctx.state, corrupt=s.status==='unconfirmed_recoverable', foreign=s.status==='invalid_reference';
  const validated=s.usable;
  return figure('effect-shelves','恢复材料与正式输出分开保留',
    `<div class="rp-envelope ${corrupt||foreign?'rp-bad':''}"><b class="rp-name">引用校验封套</b><div class="rp-tokens">${textList(['lineage','activation','purpose','size','SHA-256'])}</div>${label(foreign?'作用域或用途不匹配':corrupt?'恢复结果未知':validated?'封套和字节匹配，已解析 JSON':'等待引用与字节校验',foreign||corrupt?'bad':validated?'good':'idle')}</div>
    <div class="rp-shelves"><section class="rp-shelf ${s.purpose==='recovery'?'rp-route-selected':''}"><h4 class="rp-box-title">recovery 恢复目录</h4>
    <div class="rp-journal ${s.recovery_deleted?'rp-removed':''}">${label(s.recovery_deleted?'恢复目录已清理':s.status==='retained'?'恢复材料保留':s.purpose==='recovery'?'当前引用属于恢复用途':'独立的恢复材料',s.recovery_deleted?'good':'idle')}</div>
    ${paragraph(s.terminal_committed?'本案例条件：终态与清理意图已持久化；校验通过后才能进入本路径的清理。':'本案例终态尚未提交，恢复材料需要继续保留。')}</section>
    <section class="rp-shelf ${s.purpose==='output'?'rp-route-selected':''}"><h4 class="rp-box-title">output 正式输出</h4>${label(s.status==='output_retained'?'正式输出保留':'独立生命周期',s.status==='output_retained'?'good':'idle')}${paragraph('clear_recovery 不删除正式输出。')}</section></div>
    ${corrupt?paragraph('材料缺失或损坏不能决定业务成功或最终失败，也不能据此盲目重做已完成的有副作用调用。'):''}
    ${boundaries(boundary(ctx,'read','查看校验后的材料'),boundary(ctx,'cleanup','查看恢复清理'),boundary(ctx,'keep','查看未提交时保留'),boundary(ctx,'output','查看输出保留'))}`);
}

function harness(ctx) {
  const s=ctx.state, denied=s.result==='scope_denied', invalid=s.result==='artifact_invalid', returned=s.result==='slice';
  const offset=Number.isSafeInteger(s.offset)?s.offset:null, limit=Number.isSafeInteger(s.limit)?s.limit:null;
  const end=offset!==null&&limit!==null&&Number.isSafeInteger(offset+limit)?offset+limit:null;
  return figure('output-slice','先校验作用域，再打开输出的字符窗口',
    `<div class="rp-slice-seal"><span class="rp-token">handle</span>${arrow(s.checked?'scope + envelope 已验证':denied?'scope 拒绝':invalid?'封套损坏':'等待验证',s.checked)}${label(s.checked?'可以按范围读取':'读取窗口未打开',denied||invalid?'bad':'idle')}</div>
    <div class="rp-ruler ${returned?'rp-window-open':''}" aria-label="请求的字符窗口，不表示输出总长度">
    <div class="rp-ruler-labels"><span class="rp-ruler-label">offset = ${escape(offset??'未提供')}</span><span class="rp-ruler-label">end ≤ ${escape(end??'未提供')}</span></div>
    <div class="rp-ruler-track"><span class="rp-ruler-before">${offset===0?'正文起点':'前段省略'}</span><div class="rp-window"><b class="rp-name">最多 ${escape(limit??'未提供')} 个字符</b><span class="rp-detail">${returned?'本次字符片段':'尚未返回片段'}</span></div><span class="rp-ruler-after">后段若有</span></div>
    <div class="rp-ruler-caption">请求区间 [${escape(offset??'?')}, ${escape(end??'?')}) · 末端不含</div></div>
    ${paragraph(returned?'返回范围受剩余正文长度限制；图中的宽度为示意，不代表实际输出总长。':denied?'当前 scope 未获授此 handle，未返回片段。':invalid?'envelope 或摘要校验失败，未返回片段。':'offset / limit 按字符计；HMAC handle 不能替代 scope grant。')}
    ${boundaries(boundary(ctx,'validate','停在校验后'),boundary(ctx,'slice','打开字符窗口'))}`);
}

function application(ctx) {
  const s=ctx.state, receiptRejected=s.action==='receipt_rejected', corrupt=s.action==='integrity_rejected';
  const exists=s.existing_digest, published=s.action==='directory_committed', reused=happened(ctx,'reuse');
  return figure('application-receipt','冻结目录要与持久回执逐项相符',
    `<div class="rp-receipt-layout"><section class="rp-receipt ${receiptRejected?'rp-bad':''}"><h4 class="rp-box-title">已授权修订的构建回执</h4><div class="rp-receipt-line">receipt / index</div><div class="rp-receipt-line">声明文件 · 元数据</div>${label(receiptRejected?'回执或声明路径被拒绝':s.served?'回执校验通过':reused||published?'回执提供文件校验依据':'等待回执校验',receiptRejected?'bad':s.served?'good':'idle')}</section>
    <div class="rp-package"><h4 class="rp-box-title">digest 目录</h4>${label(exists?(s.served?'复用原目录，不覆盖':corrupt?'原目录未覆盖，读取已拒绝':reused?'原目录未覆盖，等待读取核验':'目录已存在，等待核验'):(published?'staged 已提交为目录':'准备 staged 目录'),corrupt?'bad':published||s.served?'good':'idle')}
    <div class="rp-package-file ${corrupt?'rp-bad':''}"><b class="rp-name">回执声明的文件</b>${label(corrupt?'实际文件校验失败':s.served?'文件与回执匹配':'逐文件核对 size / digest',corrupt?'bad':s.served?'good':'idle')}</div>
    ${!exists?paragraph('staged 写入并同步后 rename 提交目录。'):paragraph('名称相同的目录仍须逐文件核验，不能仅凭目录 digest 返回资产。')}</div></div>
    <div class="rp-result">${label(s.served?'返回 bytes / MIME':'未返回文件',receiptRejected||corrupt?'bad':s.served?'good':'idle')}</div>
    ${boundaries(boundary(ctx,'publish','查看目录提交'),boundary(ctx,'reuse','查看原目录复用'),boundary(ctx,'bad-file','查看文件不符'),boundary(ctx,'serve','查看读取结果'))}`);
}

function database(ctx) {
  const s=ctx.state, local=s.local_state==='committed', remote=s.remote_state==='confirmed', ack=s.outbox==='acknowledged';
  const failed=happened(ctx,'ack-fail'), unknown=happened(ctx,'unknown'), rollback=happened(ctx,'rollback');
  return figure('outbox','两个领域分别提交，以持久意图和回执衔接',
    `<div class="rp-owner-pair"><section class="rp-local-owner"><h4 class="rp-box-title">本地领域</h4><div class="rp-local-transaction ${local?'rp-good':rollback?'rp-bad':''}"><b class="rp-name">一个本地事务</b>
    <div class="rp-local-record">领域事实 ${label(local?'已提交':rollback?'保持原状':'尚未提交',local?'good':'idle')}</div>
    <div class="rp-local-record">outbox ${label(s.outbox==='absent'?'尚无提交的意图':ack?'确认已记录':'待同步意图仍保留',ack?'good':'idle')}</div></div></section>
    <div class="rp-messages"><div class="rp-message ${local?'rp-active':''}"><b class="rp-name">${local?'投递已提交意图':'等待本地提交'}</b><span class="rp-detail">稳定 ID / 目标版本 <span class="rp-direction" aria-hidden="true">→</span></span></div>
    <div class="rp-message ${remote?'rp-active':''}"><b class="rp-name">${failed?'确认写入失败':ack?'确认已收敛':remote?'远端已确认，等待本地记录':unknown?'等待确认，结果未知':'尚无确认'}</b><span class="rp-detail"><span class="rp-direction" aria-hidden="true">←</span> 独立回执</span></div></div>
    <section class="rp-remote-owner"><h4 class="rp-box-title">远端领域</h4><div class="rp-remote-transaction">${label(remote?'远端已确认':'远端结果未知',remote?'good':'idle')}${paragraph('独立调用、独立事务')}</div></section></div>
    ${failed?paragraph('远端已完成，本地确认尚未写入。保留待处理记录，按稳定标识和目标版本收敛，不能将远端结果当作失败后盲目重做。'):paragraph(unknown?'确认未知不撤销本地已提交事实。':'本地原子边界包含事实与意图，跨领域没有共同事务边界。')}
    ${boundaries(boundary(ctx,'commit','查看本地提交'),boundary(ctx,'remote','查看远端确认'),boundary(ctx,'ack-fail','查看确认写入失败'),boundary(ctx,'ack','查看本地收敛'))}`);
}

const renderers = new Map([
  ['resources.mechanism.postgres', postgres],
  ['resources.mechanism.storage', storage],
  ['resources.mechanism.storage.workspace', workspace],
  ['resources.mechanism.storage.artifacts', artifacts],
  ['resources.mechanism.resources.permissions.subjects', subjects],
  ['resources.mechanism.resources.permissions.policy', policy],
  ['resources.mechanism.storage.workspace.address-writer', writer],
  ['resources.mechanism.storage.artifacts.effect-store', effect],
  ['resources.mechanism.storage.artifacts.harness-store', harness],
  ['resources.mechanism.storage.artifacts.application-store', application],
  ['resources.mechanism.resource-authorization', authorization],
  ['resources.mechanism.database-schema', database],
]);

export function renderPolicyMechanism({mechanism, scene, frames=[], cursor=0, observed}={}) {
  const renderer=renderers.get(mechanism?.id);
  if(!renderer) return null;
  // observed is reserved for the parent; none of these mechanisms add a reader probe.
  void observed;
  return renderer({mechanism,state:scene?.state??{},frames:Array.isArray(frames)?frames:[],cursor:Number.isInteger(cursor)?cursor:0});
}
