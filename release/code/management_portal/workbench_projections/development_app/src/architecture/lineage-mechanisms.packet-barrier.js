// Local teaching identities come only from the authorized mechanism graph.
// They never become semantic selections or SDK objects.
export function renderPacketBarrier({session,mechanism,h,action,available}) {
  const s=session.state,g=mechanism.teaching_graph,root=h('div','lm-packet-teaching');
  root.dataset.teachingScenario=s.teaching_scenario;
  const source=g.nodes.find(n=>n.kind==='event_source'),selector=g.nodes.find(n=>n.kind==='selector'),reducer=g.nodes.find(n=>n.kind==='reducer');
  const entry=g.wires.find(w=>w.source===source.id&&w.target===selector.id);
  const declared=g.barriers.find(b=>b.operator_id===reducer.id),expected=s[g.expected_set_field],slots=s[g.slot_state_field],arrivals=s[g.arrival_field];
  const prepared=expected.length>0,legacy=s.phase==='legacy_recreated';
  const names=new Map([[source.id,'Event Source'],[selector.id,'Selector'],[reducer.id,'Reducer']]);
  const packet=(id,cls='')=>h('code','lm-packet '+cls,id);
  const identity=h('p','lm-teaching-identity','本图全部节点、wire、event 和 Packet 都是本地教学身份 · simulated');
  identity.append(h('code','',g.event_id));root.append(identity);
  const incoming=h('div','lm-teaching-incoming');
  for(const [node,label] of [[source,'同一次来源触发'],[selector,'保留原 Packet 身份，按具名出口分发']]){
    const box=h('section','lm-teaching-node');box.dataset.teachingNode=node.id;box.append(h('strong','',names.get(node.id)),h('small','',label),h('code','',node.id));
    incoming.append(box);
    if(node===source&&entry){const wire=h('div','lm-teaching-entry');wire.dataset.teachingWire=entry.id;wire.append(h('span','','→'),h('code','',entry.id),h('small','',`${entry.source_port} → ${entry.target_port}`));incoming.append(wire);}
  }
  root.append(incoming);
  const controls=h('section','lm-teaching-controls');controls.append(h('strong','','沿本情境观察到达顺序'));
  const ids=[...new Set([session.history.at(-1),...available.map(x=>x.id)])].filter(id=>id!=='barrier_load');
  const buttons=h('div','actions');buttons.append(...ids.map(id=>action(id)));controls.append(buttons,h('p','','切换情境是独立重置；到达先后不是所有运行的固定顺序。输入合并按冻结的 expected_wire_ids 顺序。'));root.append(controls);
  if(legacy){const warning=h('aside','lm-teaching-legacy');warning.dataset.legacy='recreated';warning.append(h('strong','','历史实现 a0dac：迟到输入临时重建单边屏障'),h('p','','这是固定代码的历史边界。v128 要求缺失屏障或来源不符时拒绝，不能把这里的重建行为说成该规范已实现。'),h('p','','下方保留的是前次归并产物，本步没有生成第二个输出。'));root.append(warning);}
  const barrier=h('section','lm-teaching-barrier');barrier.dataset.ready=String(s.barrier_ready);barrier.dataset.consumed=String(s.barrier_consumed);
  barrier.append(h('h3','',`${names.get(reducer.id)} · ${prepared?'本次屏障期望集合':'尚未冻结本次屏障'}`));
  const set=h('p','lm-expected-set');set.dataset.expectedWires=expected.join(',');set.append(h('span','',prepared?'expected_wire_ids：':'教学图可达输入（尚非本次冻结记录）：'));
  for(const id of prepared?expected:declared.expected_packet_wire_ids)set.append(h('code','',id));barrier.append(set);
  const rows=h('div','lm-slot-rows');
  for(const id of declared.expected_packet_wire_ids){
    const wire=g.wires.find(w=>w.id===id),slot=slots[id],inSet=expected.includes(id);
    const branch=g.selector_branches.find(b=>wire.source_port===`branch:${b.id}`);
    const state=!prepared?'not-frozen':!inSet?'outside':!slot?.received?'missing':slot.packet_ids.length?'packets':'empty';
    const row=h('section','lm-slot-row');row.dataset.slotWire=id;row.dataset.slotState=state;
    const route=h('div','lm-slot-route');route.append(h('strong','',branch?.label||wire.source_port),h('code','',id),h('small','',`${names.get(wire.source)} / ${wire.source_port} → ${names.get(wire.target)} / ${wire.target_port}`));
    const predicate=branch?.match?.conditions||[];
    if(predicate.length)route.append(h('small','',predicate.map(c=>`${c.from} ${c.operator} ${String(c.value)}`).join('；')));
    const receipt=h('div','lm-slot-receipt');receipt.append(h('strong','',({ 'not-frozen':'尚未建立槽位',outside:'不在当前重建集合',missing:'未到达 · 继续等待',packets:'已到达 · 原 Packet',empty:'已到达 · 完成，无包' })[state]));
    if(slot?.received){receipt.append(h('small','',`input_state = ${slot.input_state}；received/completed 表示记录存在`));for(const id of slot.packet_ids)receipt.append(packet(id,'same'));}
    row.append(route,h('span','lm-slot-arrow','→'),receipt);rows.append(row);
  }
  barrier.append(rows,h('p','lm-arrival-order',arrivals.length?'实际到达记录：'+arrivals.join(' → '):'本情境尚无到达记录。'));
  const status=h('strong','lm-barrier-status',!prepared?'先冻结实际可达输入边':s.barrier_consumed?'完整屏障已取出并消费':s.barrier_ready?'全部期望槽已收到，可以取出归并':'仍有期望槽未到达，不能归并');
  barrier.append(status,h('p','','“完成，无包”是收到空输入，实际 input_state 可以是 continue；未收到不是空输入。'));root.append(barrier);
  const result=h('section','lm-teaching-result');result.dataset.outputCount=String(s.output_packet_ids.length);result.append(h('h3','',legacy?'前次归并产物（保留）':'归并产物'));
  if(s.output_packet_ids.length){for(const id of s.output_packet_ids)result.append(packet(id,'new'));const parents=h('p','lm-parent-chain');parents.append(h('span','','parents ← '));for(const id of s.parents)parents.append(packet(id));result.append(parents);}
  else result.append(h('strong','',s.barrier_consumed?'所有输入均为空 · 本次完成但不造 Packet':'尚无输出 Packet'));
  root.append(result);return root;
}
