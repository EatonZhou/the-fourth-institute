export function renderDefinition({session,h,actions}) {
  const s=session.state, root=h('div','lm-definition');
  const compare=h('div','lm-three-way');
  for(const [title,body,cls] of [['读取时的基线','本方修改的起点','base'],['已提交的当前定义',s.concurrent_change?'已有另一编辑提交；必须保留其未冲突变更。':'与读取时一致。','current'],['本方差异','仅把相对基线的变化带入当前定义。','ours']]){
    const sheet=h('section','lm-diff-sheet '+cls);sheet.append(h('h3','',title),h('p','',body));compare.append(sheet);
  }
  const tx=h('section','lm-transaction');tx.dataset.committed=String(s.committed);tx.append(h('span','tag','短事务边界'),h('h3','','锁定当前行 → 校验前提 → 合并 → 保存'),actions('lock','conflict','merge','removed','commit'));
  const result=h('div','lm-merge-result');result.append(h('strong','',s.committed?`已保存 · 图 revision ${s.revision}`:s.phase==='conflict'?'整体拒绝 · 已提交定义不变':s.merged?'本方差异已合入当前定义，尚待保存':s.phase==='locked'?'正在基于锁内当前定义裁决':'尚未写入'));
  if(s.concurrent_change)result.append(h('p','',s.deleted_reference?'本方仍引用已被删除对象；清理会撤销本方改动，因此拒绝整次提交。':'三方合并保留对方改动，不用本方旧快照覆盖整个当前定义。'));
  const retained=h('div','lm-retained-deltas');retained.setAttribute('aria-label','结果中保留的内容');
  retained.append(h('span','baseline','基线的未改内容'));
  if(s.concurrent_change)retained.append(h('span','theirs','另一编辑的已提交变更'));
  if((s.merged||s.committed)&&s.phase!=='conflict')retained.append(h('span','ours','＋ 本方差异'));
  if(s.phase==='conflict')retained.append(h('span','rejected','本方差异未提交'));
  result.append(retained,h('p','','色块仅示意内容归属，不虚构实际修改字段。第一次成功短提交消费版本前提；回滚不消耗它。图版本与布局版本分别推进。'));tx.append(result);root.append(compare,tx);return root;
}

export function renderCompile({session,h,actions}) {
  const s=session.state, root=h('div','lm-compile');
  const graph=h('section','lm-authoring-graph');graph.append(h('h3','','可编辑图 · 情境示意'));
  const shape=h('div','lm-graph-shape');shape.append(h('span','lm-node','来源端口'),h('span','lm-wire',s.connected?'→':'⋯ 未连完 ⋯'),h('span','lm-node',s.unique_ids?'算子身份':'重复身份'),h('span','lm-wire',s.operator_cycle?'↶ 路由环':'→'),h('span','lm-node','真实出口'));
  graph.append(shape,h('p','','组成、端口与 wire 身份先规范化；界面上的接近或归属不自动生成调用。'),actions('normalize','cycle','ids'));
  const result=h('section','lm-plan-sheet');result.dataset.plan=s.plan_status;result.append(h('h3','','执行映射与诊断'),actions('compile','draft'));
  if(s.phase==='rejected')result.append(h('strong','','形状非法 · 不保存'),h('p','',!s.unique_ids?'重复身份无法唯一定位端点。':'Packet 算子路由环会破坏本轮推进关系。'));
  else if(s.phase==='partial')result.append(h('strong','','编辑图已保存 · 仍有断开的路径'),h('p','','未连完是可继续编辑的状态，不能据此声称全部路径可执行。'));
  else if(s.phase==='compiled')result.append(h('strong','','按真实 wire 形成可执行映射'),h('p','','命名端点、入站 wire 与轮次契约来自规范化后的图。'));
  else result.append(h('p','',s.stored?'规范化图已保存；继续检查可执行路径。':'等待图形状校验；尚无运行计划。'));
  root.append(graph,result);return root;
}

export function renderComposition({session,h,actions}) {
  const s=session.state, root=h('div','lm-composition');
  const outer=h('section','lm-container-frame');outer.append(h('h3','','本地组合示意 · 端点不随边界改写'));
  const boundary=h('div',s.membership_saved?'lm-inner-block enclosed':'lm-inner-block');boundary.dataset.enclosed=String(s.membership_saved);
  boundary.append(h('strong','',s.membership_saved?'普通 Function Block · 直接归属边界':'未被此块封装的元素'));
  const endpoints=h('div','lm-endpoint-pair');for(const id of s.endpoint_ids)endpoints.append(h('code','lm-node',id));boundary.append(endpoints,h('p','','示例端点身份在封装与解除封装前后保持不变。'));
  outer.append(boundary);
  const controls=h('section','lm-membership-rule');controls.append(h('h3','','改变归属，不改写协议端点'),actions('commit','unpack','duplicate','cycle','family'));
  controls.append(h('p','',s.phase==='family_api_required'?'Family 的成员和名称必须通过 Family 专门入口改变。':!s.unique_owner?'元素已有直接归属：拒绝同时放进另一个直接容器。':s.cycle?'嵌套会形成环：拒绝提交。':s.phase==='unpacked'?'边界已移除；原端点继续存在。':'每个元素至多一个直接容器；嵌套必须无环。'));
  root.append(outer,controls);return root;
}

export function renderTarget({session,h,actions}) {
  const s=session.state, root=h('div','lm-target-switch');
  const gate=h('section','lm-target-gate');gate.dataset.paused=String(s.already_paused);gate.append(h('h3','','接单门'),h('strong','',s.already_paused?'切换前已经暂停':'旧目标仍在接收任务'),h('p','',s.requested_paused?'本次提交也要求保持暂停。':'本次请求未保持暂停。'));
  const old=h('section','lm-old-target');old.append(h('h3','','原目标'),h('strong','',s.target_switched?'已移出当前接收配置':'仍是当前目标'));
  const next=h('section','lm-new-target');next.dataset.switched=String(s.target_switched);next.append(h('h3','','请求切换到的目标'),h('strong','',s.target_switched?'新目标 · 仍暂停':s.target_available?'目标存在，尚未切换':'目标已不可用'),h('p','',`配置 revision ${s.configuration_revision}`));
  const operation=h('div','lm-target-operation');operation.append(actions('switch','fence','conflict','missing'),h('p','','“本次要求暂停”不能替代“切换前已暂停”。成功切换后仍须另行恢复接单。'));
  root.append(gate,old,next,operation);return root;
}
