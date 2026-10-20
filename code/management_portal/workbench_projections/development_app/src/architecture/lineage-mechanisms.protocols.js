// Protocol-specific workfaces. The supplied simulation remains the sole owner
// of state; these diagrams explain its observations without executing services.
export function renderRpc({session,h,actions}) {
  const s=session.state, root=h('div','lm-rpc');
  const envelope=h('div','lm-envelope');envelope.append(h('strong','','领域调用封套'),h('p','','认证调用者 → 方法与参数 → 当前权限'));
  const gates=h('ol','lm-gates');
  for(const [label,ok,note] of [
    ['方法目录',s.method_known,'只有登记的方法才能找到领域处理者'],
    ['参数绑定',s.signature_valid,'按真实签名绑定参数和默认值'],
    ['当前权限',s.authorized,'服务认证身份与本次授权仍须匹配'],
  ]) {const row=h('li',ok?'pass':'blocked');row.append(h('strong','',label),h('span','',ok?'本情境条件满足':'在此拒绝'),h('p','',note));gates.append(row);}
  gates.setAttribute('aria-label','进入领域处理者的检查条件');
  const split=h('div','lm-rpc-pools');
  for(const [mode,title,detail] of [['read','读取通道','读取池只改变执行位置，不改变可见范围；不取领域写锁。'],['write-side-effect-scope','状态 owner','在所属 Lineage 的状态范围内调用；成功提交后汇集变化。']]) {
    const pool=h('section','lm-pool');pool.dataset.pool=mode;pool.dataset.reached=String(s.handler_called&&s.pool===mode);
    pool.append(h('h3','',title),h('p','',detail),h('strong','',s.handler_called&&s.pool===mode?'已到达处理者':'未进入'));split.append(pool);
  }
  const decision=h('div','lm-decision');decision.append(h('p','','read/write 由目录声明，调用者不能换通道绕过校验。'),actions('unknown','bind-error','deny','read','write'));
  root.append(envelope,gates,decision,split);return root;
}

export function renderIdentity({session,h,actions}) {
  const s=session.state, root=h('div','lm-identity');
  const stored=h('section','lm-identity-record');stored.append(h('h3','','随原操作保存的身份'));
  for(const [name,meaning] of [['principal','谁执行：按它检查当前权限'],['initiated_by','谁发起：恢复后仍保留原发起者'],['producer','谁写入：服务署名，不是额外凭证']]) {
    const row=h('div','lm-identity-field');row.append(h('code','',name),h('span','',meaning));stored.append(row);
  }
  const ambient=h('section','lm-ambient');ambient.dataset.cleared=String(s.ambient_scope_cleared);
  ambient.append(h('h3','','恢复线程的临时环境'),h('strong','',s.ambient_scope_cleared?'已清除环境授权':'不能拿线程身份替代原操作'),h('p','','异步重放从保存的身份恢复，不借用刚好处理它的线程权限。'));
  const bridge=h('div','lm-identity-bridge');bridge.append(actions('missing','mismatch','inactive','restore','manual'),h('p','',!s.identity_present?'缺失：必须重新发起操作。':!s.principal_matches?'不匹配：拒绝安装身份。':!s.users_active?'用户已停用：拒绝本次恢复。':s.stored_identity_installed?'已安装本次合法身份；继续生成 Effect 上下文。':'核对保存身份与当前可用用户。'));
  const context=h('section','lm-identity-context');context.dataset.identityInstalled=String(s.stored_identity_installed);
  context.append(h('h3','','Effect 上下文'),actions('context'),h('strong','',s.effect_caller_source==='identity.initiated_by'?'caller ← identity.initiated_by':'尚未生成 caller'),h('p','','恢复署名不会扩大当前权限；界面显示名不参与授权。'));
  root.append(stored,ambient,bridge,context);return root;
}

export function renderPipeline({session,h,actions}) {
  const s=session.state, root=h('div','lm-pipeline'), flow=h('ol','lm-transform-chain');
  const stages=[
    ['来源事实',s.occurrence_claimed,'认领同一出现；过时契约在入口拒绝。',['claim','reject']],
    ['Packet 路由',s.routes_frozen,'按显式 wire 冻结本轮投递与汇合输入。',['routes']],
    ['Effect 激活日志',s.activation_logged,'接收指定 Packet；先保存日志，暂停仍阻断准入。',['effect','pause']],
    ['Resident 输入',s.input_accepted,'只有已知投递结果才能确认输入已接收。',['invoke','unknown']],
  ];
  for(const [title,reached,note,ids] of stages){const li=h('li',reached?'reached':'');li.append(h('h3','',title),h('p','',note),actions(...ids));flow.append(li);}
  root.append(flow);
  const recovery=h('section','lm-recovery-pocket');recovery.dataset.recovery=s.phase==='recovery_pending'?'pending':'none';
  recovery.append(h('h3','',s.phase==='recovery_pending'?'未决保留在原激活位置':'一次输入经过不同的承诺边界'),h('p','',s.phase==='recovery_pending'?'调用可能已经提交。这里保留恢复工作，不能把未知画成拒绝，也不能宣称 Resident 已接收。':s.phase==='effect_paused'?'路由事实已保留；Effect 暂停，尚未越过指令准入。':s.input_accepted?'本情境确认输入已接受；这不表示模型任务或整次业务已经完成。':'出现被认领、路由被冻结、激活被保存、输入被接收，分别属于不同的状态。'));
  root.append(recovery);return root;
}
