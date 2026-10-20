import './resource-mechanisms.access.css';

// Only render the parent's frozen state. No I/O, branch selection or simulation here.
const escape = value => String(value ?? '未提供').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const code = value => `<code>${escape(value)}</code>`;
const integer = value => Number.isSafeInteger(value) && value >= 0;
const message = (title, text, tone = '') => `<div class="ra-message ${tone ? `ra-${tone}` : ''}"><strong>${escape(title)}</strong><p>${escape(text)}</p></div>`;
const cell = (title, body, tone = '') => `<section class="ra-cell ${tone ? `ra-${tone}` : ''}"><h4>${escape(title)}</h4>${body}</section>`;
const arrow = text => `<div class="ra-connector"><span aria-hidden="true">→</span><small>${escape(text)}</small></div>`;
const term = (label, value) => `<div><dt>${escape(label)}</dt><dd>${escape(value)}</dd></div>`;
const known = (value, labels, fallback = '等待观察') => Object.hasOwn(labels, value) ? labels[value] : fallback;

function boundaries({mechanism, frames = [], cursor}, suffixes) {
  const buttons = suffixes.flatMap(suffix => {
    const index = frames.findIndex(frame => frame.step?.id === `${mechanism.id}.${suffix}`);
    if (index < 0) return [];
    return [`<button type="button" class="ra-boundary" data-seek="${index}" ${cursor === index ? 'aria-current="step"' : ''}>${escape(frames[index].step.title)}</button>`];
  });
  return buttons.length ? `<nav class="ra-boundaries" aria-label="观察机制边界">${buttons.join('')}</nav>` : '';
}

function addressSegments(address) {
  if (typeof address !== 'string') return message('地址未提供', '没有可分段的地址。');
  const parts = address.split('/');
  return `<div class="ra-address" aria-label="地址分段">${parts.map((part, i) => `<div class="ra-segment ${part === '' ? 'ra-empty-segment' : ''}"><small>${i === 0 ? '根资源标识' : `后缀 · ${i}`}</small>${code(part || '空段')}${i === 0 ? '<span>根标识决定类型</span>' : ''}</div>`).join('<span class="ra-slash" aria-hidden="true">/</span>')}</div><p class="ra-caption">这里分解的是固定案例中的演示地址；后缀交给相应类型的 provider 解释。</p>`;
}

function service(p) {
  const s = p.scene.state, remote = s.route === 'resource-rpc', local = s.route === 'local-facade';
  const result = known(s.result, {pending: '尚未分派', dispatched: '请求已分派', service_unavailable: '远端不可达', authentication_rejected: '身份验证拒绝'});
  return `<div class="ra-service">
    <div class="ra-config">配置模式 ${code(s.mode)}<p>入口由配置选择；连接失败不切换入口。</p></div>
    <div class="ra-fork">
      <section class="ra-route ${remote ? 'ra-active' : 'ra-idle'}"><h4>client → resource-rpc</h4><div class="ra-wire ${s.result === 'service_unavailable' ? 'ra-cut' : ''}"><span>签名请求</span><span>ResourceService</span></div><p>${escape(remote ? result : '此分支未选择')}</p>${s.result === 'service_unavailable' ? '<strong>不改走本地</strong>' : ''}</section>
      <section class="ra-route ${local ? 'ra-active' : 'ra-idle'}"><h4>local / server → local-facade</h4><div class="ra-wire"><span>明确本地模式</span><span>领域分派</span></div><p>${escape(local ? result : '此分支未选择')}</p></section>
    </div>
    ${s.result === 'authentication_rejected' ? message('请求停在身份关口', '身份校验失败，不能进入业务分派。', 'blocked') : message(result, s.result === 'dispatched' ? '入口已完成分派；领域操作仍执行自己的权限检查。' : '观察实际选中的请求路径，不把远端失败解释成本地读取。')}
    ${boundaries(p, ['client','local','unavailable','reject','dispatch'])}
  </div>`;
}

function access(p) {
  const s = p.scene.state, ready = ['provider_ready','read','unreadable','unsupported_content'].includes(s.phase);
  const received = s.bytes_returned === true && s.phase === 'read';
  const phase = known(s.phase, {initial:'尚未解析',parsed:'已解析地址',provider_ready:'已选择 provider',invalid_address:'地址无效',unreadable:'当前读取被拒绝',unsupported_content:'无正文读取能力',read:'正文已返回'});
  return `${addressSegments(s.address)}<div class="ra-read-flow">
    ${cell('地址与描述', `<p>${escape(phase)}</p><p class="ra-caption">描述可以说明资源位置，不能替代这次读取的权限判断。</p>`, s.phase === 'invalid_address' ? 'blocked' : '')}
    ${arrow('本次 read')}
    ${cell('当前主体 / provider', `<p>${s.phase === 'unreadable' ? '当前主体不能读取' : s.phase === 'unsupported_content' ? '无正文读取能力' : received ? '本次检查已允许读取' : ready ? '进入当前读取检查' : '尚未进入读取'}</p><p class="ra-caption">每次读取依据当前主体检查。</p>`, ['unreadable','unsupported_content'].includes(s.phase) ? 'blocked' : received ? 'active' : '')}
    ${arrow('正文出口')}
    <section class="ra-body ${received ? 'ra-active' : 'ra-idle'}" data-content="${received ? 'returned' : 'withheld'}"><h4>bytes</h4><div class="ra-body-lines" aria-hidden="true"></div><p>${received ? '已返回内容' : '未返回内容'}</p></section>
  </div>${boundaries(p,['parse','bad-address','dispatch','deny','unsupported','read'])}`;
}

function router(p) {
  const s = p.scene.state;
  const parsed = ['typed_address','resolver_selected','resolver_error'].includes(s.phase);
  return `${addressSegments(s.raw)}
    <div class="ra-routing">
      <div class="ra-grammar ${s.phase === 'syntax_error' ? 'ra-blocked' : parsed ? 'ra-active' : ''}"><h4>语法关口</h4><p>清理地址 → 解析根标识 → 检查后缀空段</p><strong>${escape(known(s.phase,{raw:'尚未解析',syntax_error:'拒绝地址，不触及内容',typed_address:'已形成类型地址',resolver_selected:'类型地址有效',resolver_error:'类型地址有效'}))}</strong></div>
      ${arrow('根类型选择能力')}
      <div class="ra-provider ${s.phase === 'resolver_error' ? 'ra-blocked' : s.phase === 'resolver_selected' ? 'ra-active' : 'ra-idle'}"><h4>resolver</h4><p>${s.phase === 'resolver_selected' ? '已选择根类型 resolver，后缀交给它解释。' : s.phase === 'resolver_error' ? '类型能力不可用；不能猜测文件路径继续读取。' : '语法通过后，才进入类型能力路由。'}</p></div>
    </div>${boundaries(p,['parse','invalid','route','missing'])}`;
}

function types(p) {
  const s = p.scene.state;
  const result = known(s.registry, {old:'保留原 registry',transaction_open:'安装事务尚未提交',new_committed:'新能力已提交',duplicate_owner_rejected:'重复 owner，拒绝安装',indexed:'使用 indexed resolver',unsupported_capability:'无可调用 owner'});
  return `<div class="ra-types-layout"><div class="ra-matrix"><h4>类型 × 能力归属</h4><p class="ra-caption">只展示本案例查询的能力；其他行未被本次输入观察。</p><table><thead><tr><th>能力</th><th>目录声明的 owner 数</th><th>本次选择</th></tr></thead><tbody>${['resolver','mutation','action'].map(capability => `<tr class="${capability === s.capability ? 'ra-current-row' : ''}"><th>${escape(capability)}</th><td>${capability === s.capability ? escape(s.owner_count) : '未观察'}</td><td>${capability === s.capability ? escape(result) : '—'}</td></tr>`).join('')}</tbody></table></div>
    <section class="ra-install"><h4>安装边界</h4><div class="ra-registry-pair"><div><small>对外 registry</small><strong>${s.registry === 'new_committed' ? '新注册项' : s.registry === 'indexed' ? 'indexed' : '原注册项'}</strong></div><div class="${s.registry === 'transaction_open' ? 'ra-active' : ''}"><small>事务暂存</small><strong>${s.registry === 'transaction_open' ? '安装中' : s.registry === 'new_committed' ? '已提交' : '没有待发布项'}</strong></div></div><p>${escape(result)}</p><p class="ra-caption">${s.registry === 'unsupported_capability' ? '此能力查询已记载；无可调用 owner。加载记载不等于支持这项能力。' : s.loaded === true ? '成功的能力加载已记载。' : '尚未写入成功加载标记。失败安装不留下半注册项。'}</p></section>
    </div>${boundaries(p,['choose','duplicate','fallback','unsupported','commit','rollback'])}`;
}

function indexQuery(p) {
  const s = p.scene.state, counted = ['filtered','paged'].includes(s.phase) && integer(s.total);
  const paged = s.phase === 'paged' && integer(s.page_size);
  const total = counted ? s.total : null;
  const offset = integer(s.offset) ? s.offset : null;
  const size = paged ? s.page_size : null;
  // Positions are anonymous ordinal marks, not invented resource records. Cap drawing, not the displayed count.
  const marks = counted ? Array.from({length: Math.min(total, 40)}, (_, i) => `<li class="${paged && offset !== null && i >= offset && i < offset + size ? 'ra-in-page' : ''}"><span>${i + 1}</span></li>`).join('') : '';
  return `<div class="ra-query"><div class="ra-index-gate ${s.phase === 'service_unavailable' ? 'ra-blocked' : counted ? 'ra-active' : ''}"><h4>索引快照</h4><p>${s.phase === 'service_unavailable' ? '等待后仍不可用，查询未得到结果集合。' : counted ? '已取得候选并刷新、过滤可见事实。' : '请求刷新并等待可用快照。'}</p></div>
    <section class="ra-page-window" ${counted ? `data-total="${escape(total)}"` : ''} ${paged ? `data-page-size="${escape(size)}"` : ''}><header><h4>过滤后的可见集合</h4><strong>${counted ? `${escape(total)} 条` : '尚未计数'}</strong></header>
      ${counted ? total ? `<ol class="ra-result-positions" aria-label="过滤后结果位置">${marks}</ol>${total > 40 ? '<p>仅画前 40 个位置；总数见上方。</p>' : ''}` : '<p class="ra-empty-results">过滤已完成：可见集合为空。</p>' : '<div class="ra-unavailable">尚未计数<br><small>未就绪不等于空集合</small></div>'}
      <dl class="ra-query-params">${term('offset',s.offset)}${term('limit',s.limit)}${term('本页返回',paged ? `${size} 条` : '尚未分页')}</dl>
      <p class="ra-caption">色块表示本页窗口。序号只是结果位置，没有虚构资源名称或权限之外的数量。</p>
    </section></div>${boundaries(p,['wait','unavailable','filter','page','empty'])}`;
}

function registration(p) {
  const s = p.scene.state, a = typeof s.request_a_id === 'string' ? s.request_a_id : '', b = typeof s.request_b_id === 'string' ? s.request_b_id : '';
  const ids = [...new Set([a,b].filter(Boolean))];
  return `<div class="ra-reservation"><div class="ra-request-pair">
    <section class="ra-request"><h4>请求 A</h4><p>actor + type + 规范化 payload</p><strong>${a ? code(a) : '尚未取得预留 ID'}</strong></section>
    <div class="ra-fingerprint"><span>${s.same_fingerprint === true ? '相同摘要' : s.same_fingerprint === false ? '不同摘要' : '摘要关系未提供'}</span><p>${s.same_fingerprint === true ? '争用同一个唯一键' : s.same_fingerprint === false ? '各自使用独立唯一键' : '等待案例输入'}</p></div>
    <section class="ra-request ${s.result === 'reservation_error' ? 'ra-blocked' : ''}"><h4>请求 B</h4><p>actor + type + 规范化 payload</p><strong>${b ? code(b) : '未返回 ID'}</strong></section></div>
    <div class="ra-ledger" data-ledger-rows="${ids.length}"><h4>预留账本 · fingerprint 唯一约束</h4><table><thead><tr><th>已观察的预留 ID</th><th>取得该 ID 的请求</th></tr></thead><tbody>${ids.length ? ids.map(id => `<tr><td>${code(id)}</td><td>${[a === id ? 'A' : '',b === id ? 'B' : ''].filter(Boolean).join('、')}</td></tr>`).join('') : '<tr><td colspan="2">尚无本案例观察到的预留行</td></tr>'}</tbody></table></div>
    ${s.result === 'reservation_error' ? message('B 无法读取获胜预留行', '失败不返回候选 ID；不能假定预留成功。','blocked') : message(s.result === 'same_id' ? 'A、B 收敛到同一 ID' : s.result === 'independent_id' ? '两个请求独立预留' : '等待账本返回', '预留 ID 与领域对象创建是两个边界；这里没有创建领域对象。')}
    </div>${boundaries(p,['winner','reuse','different','unreadable'])}`;
}

function catalog(p) {
  const s = p.scene.state, staged = s.registry_generation === 'staged', installed = s.registry_generation === 'installed';
  const aborted = p.frames[p.cursor]?.step?.id === `${p.mechanism.id}.abort`;
  return `<div class="ra-transaction"><div class="ra-lock-label">同一能力的加载锁 <small>resolver / mutation / action 各自记载</small></div>
    <div class="ra-registry-shelves">
      <section data-published="${installed ? 'installed' : 'before'}" class="ra-shelf ra-active"><h4>对外可用的 registry</h4><strong>${installed ? '已安装的版本' : '原版本 · before'}</strong><p>${installed ? '调用者可使用已完整安装的注册项。' : '暂存安装尚未替换原注册项。'}</p></section>
      <section class="ra-shelf ${staged ? 'ra-staging' : 'ra-idle'}"><h4>事务暂存区</h4><strong>${staged ? 'staged · 安装中' : installed ? '没有未提交注册项' : aborted ? '安装已失败，暂存项已撤销' : '尚未进入安装'}</strong><p>只有事务成功提交，才能发布注册项并记载加载完成。</p></section>
    </div>
    <p class="ra-caption">${s.already_loaded && installed ? '本次复用已加载能力，没有再次打开安装事务。' : s.loaded ? '成功加载已记载。' : '未记载成功加载；异常不会保留部分安装。'}</p>
    </div>${boundaries(p,['transaction','commit','abort','reuse'])}`;
}

function workspace(p) {
  const s = p.scene.state, start = integer(s.start) ? s.start : null, end = integer(s.end) ? s.end : null;
  const delivered = s.result === 'bytes';
  const actual = delivered && integer(s.actual_start) && integer(s.actual_end) && s.actual_end >= s.actual_start;
  // A coordinate ruler shows requested positions, not a claimed file size or fabricated file contents.
  const max = Math.max(start ?? 0, end ?? 0), first = Math.max(0, Math.min(start ?? 0, end ?? 0) - 1);
  const count = Math.min(18, max - first + 2);
  const marks = start !== null && end !== null ? Array.from({length:count}, (_,j) => {
    const i = first+j, included = actual && i >= s.actual_start && i <= s.actual_end;
    return `<span class="ra-byte ${included ? 'ra-in-page' : ''}"><b>${escape(i)}</b><small>${i === start && i === end ? '起 / 止' : i === start ? '起' : i === end ? '止' : '·'}</small></span>`;
  }).join('') : '';
  return `<div class="ra-file-read"><div class="ra-fd-route">
    ${cell('Workspace 路径边界', `<p>${s.result === 'unreadable' ? '权限或安全路径检查未通过' : s.inode_pinned ? '安全打开已通过' : '检查权限与路径边界'}</p><p class="ra-caption">不跟随符号链接逃出允许范围。</p>`,s.result === 'unreadable' ? 'blocked' : '')}
    ${arrow('安全打开')}
    ${cell('固定文件描述符 fd', `<strong>${s.inode_pinned ? '已固定打开的文件' : '尚未打开'}</strong><p>后续沿 fd 读取；不把路径交给 HTTP 再打开一次。</p>`,s.inode_pinned ? 'active' : 'idle')}
    </div>
    <section class="ra-range" ${actual ? `data-range="${escape(s.actual_start)}:${escape(s.actual_end)}"` : ''}><header><h4>字节坐标 · 两端包含</h4><span>请求 ${escape(s.start)} → ${escape(s.end)}</span></header><div class="ra-byte-ruler">${marks}</div><p class="ra-caption">刻度表示位置，不表示文件长度或实际正文。${count === 18 ? '区间较长，仅显示局部刻度。' : ''}</p>
    ${s.result === 'range_error' ? message('区间无效', '即使文件已安全打开，反向区间仍不能读取。','blocked') : actual ? message(`返回 [${s.actual_start}, ${s.actual_end}]`, `本次返回 ${s.actual_end-s.actual_start+1} 个字节；实际内容不在本模型中。`,'active') : message('尚无正文返回',s.result === 'unreadable' ? '路径检查未通过，没有可读取的 fd。' : '安全打开与区间读取是两个独立边界。')}
    </section></div>${boundaries(p,['pin','deny','slice','range-error'])}`;
}

function knowledge(p) {
  const s = p.scene.state, compared = ['updated','ResourceVersionConflict'].includes(s.outcome);
  return `<div class="ra-version-edit"><section class="ra-draft"><h4>提交的正文</h4><pre>${escape(s.text)}</pre><p>适配器把修改交给知识领域 writer。</p></section>
    <section class="ra-version-gate ${s.outcome === 'ResourceVersionConflict' || s.outcome === 'invalid_address' ? 'ra-blocked' : ''}"><h4>同一资源根 → 版本比较</h4><div class="ra-version-pair"><div><small>请求带来的版本</small>${code('expected_version')}</div><span class="ra-comparison">${compared ? (s.outcome === 'updated' ? '=' : '≠') : '待比对'}</span><div><small>领域当前版本</small>${code('current_version')}</div></div><p>${s.outcome === 'invalid_address' ? '资源根不一致：没有调用领域 writer。' : s.outcome === 'ResourceVersionConflict' ? 'ResourceVersionConflict：拒绝覆盖当前正文。' : s.outcome === 'updated' ? '版本匹配，领域 writer 已更新正文并返回版本。' : s.domain_called ? '已进入领域 writer，等待版本检查结果。' : '尚未调用领域 writer。'}</p><p class="ra-caption">模型给出匹配关系，没有给出具体版本号或前一版正文。</p></section>
    </div>${boundaries(p,['delegate','root-deny','conflict','write'])}`;
}

function envelope(p) {
  const s = p.scene.state, rejected = ['content_stream_truncated','response_identity_error'].includes(s.outcome);
  const accepted = s.accepted === true && s.outcome === 'content_received';
  const status = accepted ? 'accepted' : rejected ? 'rejected' : 'pending';
  const label = known(s.outcome, {pending:'等待响应',authentication_error:'请求身份拒绝',transport_timeout_commit_unknown:'客户端超时',response_identity_error:'响应身份不匹配',content_stream_truncated:'正文流提前结束',content_received:'完整正文已接收'});
  return `<div class="ra-rpc"><div class="ra-envelope-pair">
    <section class="ra-envelope"><h4>请求信封</h4><dl>${term('id','request.id')}${term('method / params','本案例没有展开具体值')}${term('auth 校验要求','签名主体须与业务主体一致')}</dl><p>${s.outcome === 'authentication_error' ? '签名身份拒绝，请求未分派。' : s.dispatched ? '服务端已分派该请求。' : '等待身份检查和分派。'}</p></section>
    <section class="ra-envelope ${s.outcome === 'response_identity_error' ? 'ra-blocked' : ''}"><h4>响应信封</h4><div class="ra-id-match">${code('request.id')}<b>${s.outcome === 'response_identity_error' ? '≠' : accepted || s.outcome === 'content_stream_truncated' ? '=' : '?'}</b>${code('response.id')}</div><p>${s.outcome === 'response_identity_error' ? '响应身份不匹配，不能接受此响应。' : accepted || s.outcome === 'content_stream_truncated' ? '信封身份匹配，继续检查正文流。' : '尚未确认可接受的响应信封。'}</p></section>
    </div>
    <section class="ra-stream ${rejected ? 'ra-blocked' : accepted ? 'ra-active' : ''}" data-body="${status}"><header><h4>bytes 正文流</h4><strong>${escape(label)}</strong></header><div class="ra-stream-track ${s.outcome === 'content_stream_truncated' ? 'ra-truncated' : accepted ? 'ra-complete' : ''}"><span>信封后读取正文</span><span>${accepted ? '完整接收' : s.outcome === 'content_stream_truncated' ? '中途断开' : '尚未接受'}</span></div><p class="ra-caption">示意条只表达接收边界，不代表字节数或完成百分比。</p>
    ${s.outcome === 'transport_timeout_commit_unknown' ? message('是否提交仍未知', '请求已分派；客户端超时不能消除服务端可能已经产生的领域副作用。','blocked') : s.outcome === 'content_stream_truncated' ? message('有响应元数据仍可能失败', '正文未按约定完整读完，不能把部分流当作完整内容。','blocked') : accepted ? message('正文已接受','响应身份与正文完整性均通过。','active') : message(label,'信封校验与正文读取分别决定是否接受这次响应。')}
    </section></div>${boundaries(p,['dispatch','auth-reject','id-error','short','timeout','accept'])}`;
}

// Exact IDs are an integration contract: shared adapters do not imply shared state fields.
const renderers = new Map([
  ['resources.mechanism.resources',service],
  ['resources.mechanism.resources.access',access],
  ['resources.mechanism.resources.types',types],
  ['resources.mechanism.resources.access.address-router',router],
  ['resources.mechanism.resources.access.index',indexQuery],
  ['resources.mechanism.resources.access.registration',registration],
  ['resources.mechanism.resources.types.catalog',catalog],
  ['resources.mechanism.resources.types.workspace-provider',workspace],
  ['resources.mechanism.resources.types.knowledge-provider',knowledge],
  ['resources.mechanism.resource-access',envelope],
]);

export function renderAccessMechanism({mechanism,scene,frames = [],cursor,observed} = {}) {
  const render = renderers.get(mechanism?.id);
  if (!render) return null;
  if (!scene?.state) return `<section class="ra-view">${message('尚无模拟状态','等待父视图提供此机制的案例状态。')}</section>`;
  return `<section class="ra-view" aria-label="${escape(mechanism.title)}">${render({mechanism,scene,frames,cursor,observed})}</section>`;
}
