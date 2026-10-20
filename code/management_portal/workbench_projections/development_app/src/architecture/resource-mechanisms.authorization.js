import { schemaMatches } from '../shared/format-tags.js';
// Pure presentation of frozen permission_graph.v1 observations. No authorization execution.
const ID = 'resources.mechanism.resources.permissions.coverage';
const ADAPTER = 'tfi.resources.permission_graph.v1';
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const code = value => `<code class="rg-code">${esc(value)}</code>`;
const levels = new Set(['unknown','none','viewer','contributor','admin']);
const arrayFields = ['graph_nodes','permission_edges','named_private_ids','derived_private_ids','persistent_grants_before','persistent_grants_after','graph_order','processed_node_ids','invalid_parent_edges','unprocessed_node_ids','direct_roots','global_contributions','node_results','source_paths'];
const stringFields = ['subject_id','target_id','credential_revision','credential_set_revision','credential_status','graph_error','outcome','effective_level'];
const caseNames = {
  explicit_root:'显式根如何覆盖后代', admin_private:'global admin 遇到 private', combined_private:'private 内仍有显式 viewer',
  admin_public:'global 对公开目标的贡献', mixed_private:'有公开父节点的混合路径', all_private:'全部父节点都在 private 内',
  max_levels:'多条显式来源共同到达目标', inactive_bridge:'未激活节点截断显式路径', missing_parent:'父节点缺失',
  cycle:'父关系形成环', revision_missing:'revision 指向的凭证行缺失', revision_mismatch:'凭证行 revision 不匹配',
  empty_revision:'空 revision 是空凭证集合', empty_matching_set:'已匹配的空凭证集合',
};
function valid(state) {
  const grant = g => g && typeof g.credential_id === 'string' && typeof g.credential_type === 'string';
  const source = s => s && typeof s.credential_id === 'string';
  const path = p => p && typeof p.credential_id === 'string' && Array.isArray(p.nodes) && p.nodes.every(id => typeof id === 'string');
  return state && arrayFields.every(k => Array.isArray(state[k])) && stringFields.every(k => typeof state[k] === 'string') &&
    typeof state.credential_row_present === 'boolean' && Number.isInteger(state.credential_read_count) &&
    Number.isInteger(state.new_grant_count) && levels.has(state.effective_level) &&
    state.graph_nodes.every(n => n && typeof n.id === 'string' && typeof n.active === 'boolean' && Array.isArray(n.permission_parents) && n.permission_parents.every(p=>typeof p==='string')) &&
    new Set(state.graph_nodes.map(n => n.id)).size === state.graph_nodes.length &&
    state.persistent_grants_before.every(grant) && state.persistent_grants_after.every(grant) &&
    state.direct_roots.every(r=>r && typeof r.resource_id==='string' && levels.has(r.level) && source(r.source)) &&
    state.global_contributions.every(g=>g && levels.has(g.level) && source(g.source)) && state.source_paths.every(path) &&
    state.invalid_parent_edges.every(e=>e && typeof e.child_id==='string' && typeof e.parent_id==='string') &&
    state.permission_edges.every(e => e && typeof e.child_id === 'string' && typeof e.parent_id === 'string') &&
    state.node_results.every(n => n && typeof n.node_id === 'string' && levels.has(n.effective_level) && levels.has(n.explicit_level) &&
      ['sources','paths','global_applied_ids','global_rejected_ids','parent_contributions','direct_roots'].every(k => Array.isArray(n[k])) &&
      ['private','named_private','all_parents_private','active'].every(k => typeof n[k] === 'boolean') &&
      n.paths.every(path) && n.sources.every(s=>source(s) && levels.has(s.level) && typeof s.determines_level==='boolean'));
}
const unavailable = () => `<section class="rg-workbench rg-unavailable"><h3 class="rg-title">授权图观察尚不完整</h3><p class="rg-copy">缺少正式字段；暂停覆盖贡献。请重新载入同批机制与观察状态。</p><p class="rg-outcome" data-target-level="unknown">目标等级：未知（unknown）</p></section>`;

// Layout only: place ready parents before children. Unresolved/cyclic nodes stay visible.
// These ranks never establish coverage, private status, sources, or effective levels.
function positions(nodes, narrow) {
  const ranks = new Map();
  for (let round = 0; round < nodes.length; round++) for (const node of nodes) {
    if (ranks.has(node.id)) continue;
    if (node.permission_parents.every(id => ranks.has(id))) ranks.set(node.id, node.permission_parents.length ? Math.max(...node.permission_parents.map(id => ranks.get(id))) + 1 : 0);
  }
  let tail = ranks.size ? Math.max(...ranks.values()) + 1 : 0;
  for (const node of nodes) if (!ranks.has(node.id)) ranks.set(node.id, tail++);
  const groups = [];
  for (const node of nodes) (groups[ranks.get(node.id)] ||= []).push(node);
  const maxGroup = Math.max(1,...groups.map(g => g?.length || 0));
  const width = narrow ? Math.max(340,maxGroup * 164 + 12) : Math.max(780,groups.length * 254 + 24);
  const height = narrow ? groups.length * 184 + 8 : Math.max(300,maxGroup * 156 + 16);
  const box = {width:narrow ? 150 : 210,height:118};
  const points = new Map();
  groups.forEach((group,rank) => group?.forEach((node,i) => points.set(node.id, {
    x:narrow ? (width-group.length*164+14)/2 + i*164 : 14+rank*254,
    y:narrow ? 12+rank*184 : (height-group.length*156+38)/2+i*156,
  })));
  return {width,height,box,points};
}
function graph(state, frames, failed, narrow) {
  const {width,height,box,points} = positions(state.graph_nodes,narrow);
  const results = new Map(state.node_results.map(n => [n.node_id,n]));
  const activeEdges = new Set();
  if (!failed) for (const result of state.node_results) for (const path of result.paths) for (let i=1;i<path.nodes.length;i++) activeEdges.add(`${path.nodes[i-1]}→${path.nodes[i]}`);
  const edges = state.permission_edges.map(edge => {
    const child = points.get(edge.child_id), parent = points.get(edge.parent_id);
    if (!child) return '';
    const identity = `${edge.parent_id}→${edge.child_id}`;
    const active = activeEdges.has(identity);
    let x1,y1,x2,y2;
    if (narrow) {
      x2=child.x+box.width/2;y2=child.y;
      x1=parent?parent.x+box.width/2:x2;y1=parent?parent.y+box.height:y2-40;
    } else {
      x2=child.x;y2=child.y+box.height/2;
      x1=parent?parent.x+box.width:x2-38;y1=parent?parent.y+box.height/2:y2;
    }
    // Back edges use an exterior bend, making cycles visible without computing a result.
    const back = narrow ? y1>=y2 : x1>=x2;
    let d;
    if (back && parent) {
      if (narrow) { x1=parent.x;y1=parent.y+box.height/2;x2=child.x;y2=child.y+box.height/2;d=`M${x1},${y1} C1,${y1} 1,${y2} ${x2},${y2}`; }
      else { x1=parent.x+box.width/2;y1=parent.y;x2=child.x+box.width/2;y2=child.y;d=`M${x1},${y1} C${x1},3 ${x2},3 ${x2},${y2}`; }
    } else d=narrow ? `M${x1},${y1} C${x1},${(y1+y2)/2} ${x2},${(y1+y2)/2} ${x2},${y2}` : `M${x1},${y1} C${(x1+x2)/2},${y1} ${(x1+x2)/2},${y2} ${x2},${y2}`;
    const arrow = back ? (narrow ? `${x2},${y2} ${x2-8},${y2-5} ${x2-8},${y2+5}` : `${x2},${y2} ${x2-5},${y2-8} ${x2+5},${y2-8}`) : narrow ? `${x2},${y2} ${x2-5},${y2-8} ${x2+5},${y2-8}` : `${x2},${y2} ${x2-8},${y2-5} ${x2-8},${y2+5}`;
    return `<g class="rg-edge ${active?'rg-edge-active':''} ${!parent||failed?'rg-edge-paused':''}" data-edge="${esc(identity)}"><title>${esc(identity)}；父引用：${esc(edge.child_id)} → ${esc(edge.parent_id)}</title><path d="${d}"/><polygon points="${arrow}"/>${!parent?`<text class="rg-missing-label" x="${narrow?x1-56:child.x}" y="${narrow?y1-8:child.y-10}">缺失 parent</text>`:''}</g>`;
  }).join('');
  const cards = state.graph_nodes.map(node => {
    const result = results.get(node.id), p=points.get(node.id), target=node.id===state.target_id;
    const isPrivate = state.named_private_ids.includes(node.id) || state.derived_private_ids.includes(node.id);
    const explicitRoot = !failed && state.direct_roots.some(root => root.resource_id===node.id);
    const seek = frames.findIndex(f => f.state?.node_results?.some(r=>r.node_id===node.id));
    const inner = `<span class="rg-node-id">${esc(node.id)}</span><span class="rg-node-flags">${target?'目标 · ':''}${isPrivate?'private 边界':result?'非 private':'边界待推导'}${!node.active?' · 未激活':''}</span><span class="rg-node-level">${failed?'未知':result?esc(result.effective_level):'待推导'}</span><span class="rg-node-note">${explicitRoot?'显式凭证根 · ':''}${failed?'贡献已暂停':result?`显式 ${esc(result.explicit_level)}`:'仅显示父关系'}</span>`;
    return `<foreignObject x="${p.x}" y="${p.y}" width="${box.width}" height="${box.height}" data-node="${esc(node.id)}">${seek>=0&&!failed?`<button xmlns="http://www.w3.org/1999/xhtml" type="button" class="rg-node ${isPrivate?'rg-node-private':''} ${target?'rg-node-target':''}" data-seek="${seek}" aria-label="查看 ${esc(node.id)} 的推导时点">${inner}</button>`:`<div xmlns="http://www.w3.org/1999/xhtml" class="rg-node ${isPrivate?'rg-node-private':''} ${target?'rg-node-target':''}">${inner}</div>`}</foreignObject>`;
  }).join('');
  return `<svg class="rg-graph ${narrow?'rg-graph-narrow':'rg-graph-wide'}" viewBox="0 0 ${width} ${height}" role="group" aria-label="资源父关系与显式覆盖路径">${edges}${cards}</svg>`;
}
function revision(state, failed) {
  const label = {not_read:'尚未读取凭证',matched:'集合 revision 已匹配',empty_pointer:'空指针：有效空集合，不读取凭证行','authorization.credentials_unavailable':'凭证集合不可用'}[state.credential_status] || state.credential_status;
  const issues = state.invalid_parent_edges.map(e=>`缺父：${code(e.child_id)} → ${code(e.parent_id)}`).join('；');
  const unfinished = state.unprocessed_node_ids.length ? `未完成拓扑处理：${state.unprocessed_node_ids.map(code).join('、')}。这些节点不一定都是环成员。` : '';
  return `<details class="rg-revision" ${failed?'open':''}><summary class="rg-summary">${esc(label)} · 凭证读取 ${state.credential_read_count} 次</summary><div class="rg-revision-body"><p class="rg-copy">主体指针 ${code(state.credential_revision||'（空）')}</p><p class="rg-copy">集合行 ${state.credential_row_present?'存在':'不存在'} · 行 revision ${code(state.credential_set_revision||'（空）')}</p>${issues?`<p class="rg-copy">${issues}</p>`:''}${unfinished?`<p class="rg-copy">${unfinished}</p>`:''}</div></details>`;
}
function grantList(items) {
  return items.length ? `<ul class="rg-grants">${items.map(g=>`<li class="rg-grant"><strong class="rg-grant-id">${esc(g.credential_id)}</strong><span class="rg-grant-kind">${esc(g.credential_type)}${g.resource_id?` · 根 ${esc(g.resource_id)} · ${esc(g.level)}`:' · 持久记录未写 resource 根'}</span><span class="rg-grant-issuer">颁发者 ${esc(g.issued_by)}</span></li>`).join('')}</ul>` : `<p class="rg-copy">空集合</p>`;
}

export function renderAuthorizationMechanism({mechanism,scene,frames=[],cursor=0,observed} = {}) {
  if (mechanism?.id !== ID || !schemaMatches(mechanism?.adapter, ADAPTER)) return null;
  const state=scene?.state;
  if (!valid(state) || !Object.hasOwn(caseNames,scene.case_id||state.case_input_scenario)) return unavailable();
  const failed=Boolean(state.graph_error)||state.credential_status==='authorization.credentials_unavailable';
  const target=state.node_results.find(n=>n.node_id===state.target_id);
  const ready=state.outcome==='coverage_complete'&&!failed;
  const level=ready?state.effective_level:'unknown';
  const globals=state.global_contributions;
  const sources=failed?[]:target?.sources||[];
  const paths=failed?[]:target?.paths||[];
  const boundary=target ? target.named_private?'显式 private':target.all_parents_private?'全部父节点 private，继承边界':target.private?'private':'非 private' : '边界尚待推导';
  const roots=failed?[]:state.direct_roots;
  const graphFrame=frames.findIndex(f=>f.state?.graph_nodes?.length);
  // Empty initial arrays are not observations. Use only already played formal
  // assignments, so seeking backwards cannot borrow a future roots result.
  const played=frames.slice(0,cursor+1);
  const emptyContribution=(field,label)=>{
    const parsed=played.some(f=>f.step?.state_changes?.some(c=>c.op==='set'&&c.field===field));
    if(parsed)return `已解析凭证集合：没有${label}。`;
    if(state.credential_status==='not_read')return '等待凭证集合，尚未解析贡献。';
    return `${state.credential_status==='empty_pointer'?'已确认空凭证集合':'凭证已读取'}，尚未解析${label}。`;
  };
  const globalBody=failed?'<p class="rg-copy">依据不可用；暂停覆盖贡献。</p>':globals.length?globals.map(g=>{
    const credential=g.source?.credential_id;
    const rejected=target?.global_rejected_ids.includes(credential);
    const applied=target?.global_applied_ids.includes(credential);
    return `<div class="rg-global-source" data-contribution="${applied?'active':rejected?'rejected':'pending'}"><strong class="rg-global-level">${esc(g.level)}</strong> ${code(credential)}<p class="rg-copy">${rejected?'private 排除这条 global 贡献':applied?'目标接受这条图外贡献':'目标尚未推导'}</p></div>`;
  }).join(''):`<p class="rg-copy">${emptyContribution('global_contributions',' global 贡献')}</p>`;
  const sourceBody=sources.length?`<ul class="rg-sources">${sources.map(s=>`<li class="rg-source" data-contribution="active"><div class="rg-source-head"><strong class="rg-source-level">${esc(s.level)}</strong><span class="rg-source-role">${s.determines_level?'决定等级':'有效较低贡献'}</span></div>${code(s.credential_id)}<p class="rg-copy">${s.coverage==='global'?'图外 global':`显式根 ${esc(s.root_id)} · ${s.coverage==='root'?'根本身':'后代覆盖'}`}</p></li>`).join('')}</ul>`:`<p class="rg-copy">${failed?'暂停覆盖贡献。':ready?'当前目标没有覆盖来源。':'到达目标的来源会随正式时点显示。'}</p>`;
  return `<section class="rg-workbench" data-scenario="${esc(scene.case_id||state.case_input_scenario)}" data-cursor="${Number.isInteger(cursor)?cursor:0}">
    <header class="rg-header"><p class="rg-kicker">固定教学图 · 本地模拟</p><h3 class="rg-title">${esc(caseNames[scene.case_id||state.case_input_scenario]||mechanism.title)}</h3><p class="rg-copy">主体 ${code(state.subject_id||'待载入')} → 目标 ${code(state.target_id||'待载入')}。切换外层情境，可比较固定的主体、目标与凭证组合。</p></header>
    ${failed?`<div class="rg-failure" role="status"><strong class="rg-failure-title">暂停覆盖贡献</strong><p class="rg-copy">${esc(state.graph_error||state.credential_status)}。本例尚不能得到目标等级；未知不等于 none。</p></div>`:''}
    ${revision(state,failed)}
    <div class="rg-flow"><section class="rg-root-sources"><h4 class="rg-subtitle">主体的显式凭证根</h4>${roots.length?roots.map(r=>`<p class="rg-root-source">${code(r.source?.credential_id)} <span class="rg-arrow">→</span> ${code(r.resource_id)} <strong class="rg-root-level">${esc(r.level)}</strong></p>`).join(''):`<p class="rg-copy">${failed?'依据不可用，未解析根。':emptyContribution('direct_roots','显式根')}</p>`}</section><aside class="rg-global"><h4 class="rg-subtitle">global · 图外贡献</h4>${globalBody}<p class="rg-footnote">portal_admin 不创建父节点，也不沿父边传播。</p></aside></div>
    <div class="rg-diagram"><h4 class="rg-subtitle">父关系怎样承载显式覆盖</h4><p class="rg-legend"><span class="rg-legend-line"></span>覆盖流向 parent → child；绿色表示已记录的显式路径。<span class="rg-legend-private">private</span> 排除 global，保留有效显式来源。</p>${state.graph_nodes.length?`${graph(state,frames,failed,false)}${graph(state,frames,failed,true)}`:`<p class="rg-copy">等待载入固定父图。</p>${graphFrame>=0?`<button type="button" class="rg-load" data-seek="${graphFrame}">显示本例父图</button>`:''}`}<p class="rg-footnote">点击节点回到它的推导时点。节点快照包含模型补充的 none 节点，不表示运行时逐个访问。</p><details class="rg-edge-list"><summary class="rg-summary">查看存储的父引用（child → parent）</summary><ul class="rg-edge-items">${state.permission_edges.map(e=>`<li class="rg-edge-item">${code(e.child_id)} → ${code(e.parent_id)}</li>`).join('')}</ul></details></div>
    <section class="rg-target"><div class="rg-target-heading"><div class="rg-target-label"><h4 class="rg-subtitle">${esc(state.target_id||'目标')} · ${esc(boundary)}</h4><p class="rg-copy">${ready?'当前教学情境的最终覆盖等级':'观察尚未完成'}</p></div><strong class="rg-outcome" data-target-level="${esc(level)}">${level==='unknown'?'未知（unknown）':esc(level)}</strong></div>${sourceBody}${paths.length?`<div class="rg-paths"><h4 class="rg-subtitle">通向目标的显式路径</h4>${paths.map(p=>`<div class="rg-path"><p class="rg-path-caption">${code(p.credential_id)} · ${esc(p.level)}</p><ol class="rg-path-nodes">${p.nodes.map(n=>`<li class="rg-path-node">${code(n)}</li>`).join('')}</ol></div>`).join('')}</div>`:''}<p class="rg-footnote">coverage_complete 只表示覆盖计算完成；执行指令仍由动态 ISA、当前 actor 与业务规则判断。</p></section>
    <details class="rg-ledger"><summary class="rg-summary">持久 grant 账本 · 新增 ${state.new_grant_count} 条</summary><div class="rg-ledger-columns" data-new-grants="${state.new_grant_count}"><section class="rg-ledger-column"><h4 class="rg-subtitle">推导前</h4>${grantList(state.persistent_grants_before)}</section><section class="rg-ledger-column"><h4 class="rg-subtitle">推导后</h4>${grantList(state.persistent_grants_after)}</section></div><p class="rg-footnote">派生等级、路径与 source 留在计算结果中，不写成新的 grant；多条路径也不增加账本记录。</p></details>
  </section>`;
}
