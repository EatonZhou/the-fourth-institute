import {renderPacketBarrier} from './lineage-mechanisms.packet-barrier.js';
const tag=(h,text,cls='')=>h('span','lm-packet '+cls,text);

export function renderPacket(context) {
  const {session,h,actions}=context;
  if (session.state.teaching_scenario) return renderPacketBarrier(context);
  const s=session.state, root=h('div','lm-packet-workface');root.dataset.operator=s.operator_kind;
  const input=h('section','lm-packet-input');input.append(h('h3','','本情境的输入身份'),h('p','','示例 Packet ID，只描述本地因果链。'));
  for(const id of s.input_packet_ids) input.append(tag(h,id));
  const operator=h('section','lm-packet-operator');operator.append(h('h3','',s.operator_kind==='reducer'?'Reducer · 等待本轮输入':s.operator_kind==='selector'?'Selector · 选择具名出口':'Filter · 保留选中项'),actions('filter','select','empty','wait','cancel','reduce'));
  const downstream=h('section','lm-packet-downstream');downstream.dataset.emptyCompletion=String(s.empty_branch_done);
  if(s.operator_kind==='reducer') {
    downstream.append(h('h3','','本轮冻结输入屏障'));
    const gate=h('div','lm-barrier');gate.dataset.barrier=s.barrier_cancelled?'cancelled':s.barrier_complete?'complete':'waiting';
    gate.append(h('strong','',s.barrier_cancelled?'已取消 · 封住旧批结果':s.barrier_complete?'所有冻结输入均已完成':'仍有冻结输入尚未完成'),h('p','','完成可以携带 Packet，也可以明确为空；未到达不能算空。'));downstream.append(gate);
    const output=h('div','lm-packet-result');output.append(h('h3','','归并产物'));
    if(s.output_packet_ids.length){for(const id of s.output_packet_ids)output.append(tag(h,id,'new'));const parents=h('div','lm-parent-chain');parents.append(h('span','','parents ← '));for(const id of s.parents)parents.append(tag(h,id));output.append(parents);}
    else output.append(h('p','','尚无新 Packet；等待或取消时均不投递结果。'));
    downstream.append(output);
  } else {
    downstream.append(h('h3','','下游按显式 wire 接收'));
    if(s.empty_branch_done){
      const receipt=h('div','lm-empty-receipt');receipt.dataset.receipt='selector-completed';receipt.append(h('strong','','Selector 本次结束 · 没有选中 Packet'),h('p','','本情境只观察 Selector 自身；尚未展示下游逐槽记录。'));
      downstream.append(receipt,h('p','','选择“有包分支先到，空分支完成屏障”或“空分支先完成，等待有包分支”，继续观察下游冻结集合和完成无包的输入槽。'));
    }else if(s.output_packet_ids.length){downstream.append(h('p','','原身份沿选中出口继续流转。'));for(const id of s.output_packet_ids)downstream.append(tag(h,id,'same'));}
    else downstream.append(h('p','','尚未转交，也尚未发出空分支完成通知。'));
  }
  root.append(input,operator,downstream);return root;
}

export function renderOccurrence({session,h,actions}) {
  const s=session.state, root=h('div','lm-occurrence');
  const meter=h('section','lm-occurrence-meter');meter.append(h('h3','','出现预算'),h('strong','lm-number',String(s.occurrence_count)),h('p','','按一次来源事实计数，不按下游 wire 数计数。'));
  const claim=h('section','lm-occurrence-claim');claim.append(h('h3','','同一个来源游标'),h('p','',s.duplicate_cursor?'这个 cursor 已被认领；即使预算已满，重放也不多计一次。':!s.contract_current?'来源契约已经变化，本次不接收。':s.paused?'来源已暂停，本次不接收。':s.limit_reached?'这是新事实，但出现预算已用尽。':'先认领这一次出现，再展开投递。'),actions('claim','duplicate','exhausted','paused','stale'));
  const fan=h('section','lm-fanout');fan.dataset.frozen=String(s.barrier_prepared);fan.append(h('h3','','一次出现 → 实际路由集合'),actions('fanout'));
  const routes=h('div','lm-wire-fan');routes.append(h('span','lm-occurrence-dot',s.accepted?'已识别的出现':'待认领'),h('span','lm-wire-label','显式 wire 集合'),h('strong','',s.barrier_prepared?'先冻结期望输入，再分发各条投递':'尚未展开本轮投递'));fan.append(routes,h('p','','图只说明分发关系，不虚构本轮 wire 数量。新增连线也不把同一个出现计成多次。'));
  root.append(meter,claim,fan);return root;
}

export function renderScript({session,h,actions}) {
  const s=session.state, root=h('div','lm-script');
  const receipt=h('section','lm-run-receipt');receipt.dataset.receipt=s.receipt;
  receipt.append(h('span','tag','异步运行收据'),h('h3','',s.receipt==='none'?'尚未登记':s.receipt==='existing'?'复用已有运行':s.receipt==='already_running'?'已有互斥运行':s.receipt==='failed'?'启动失败已保存':'已登记运行'),h('p','','script_run_id 标识可查询的运行，不等于脚本已经成功。'),actions('admit','duplicate','exclusive'));
  const launch=h('section','lm-process-boundary');launch.append(h('h3','','外部进程启动'),h('p','',s.process_launched?'已启动；后续结果仍由运行记录与产物反映。':s.phase==='launch_failed'?'登记仍在；启动失败可查询，不冒充从未受理。':'必须先保存来源与运行记录，再跨出进程边界。'),actions('launch','launch-fail'));
  const artifact=h('section','lm-artifact');artifact.dataset.read=String(s.artifact_read);artifact.append(h('h3','','从登记产物读取字节片段'));
  const window=h('div','lm-byte-window');window.append(h('span','','片段前的字节'),h('strong','',s.artifact_read?'本次授权片段':'待校验登记与路径'),h('span','','后续字节'));artifact.append(window,h('p','','读取按 Lineage 权限与产物登记核对，再按字节偏移推进；不会把任意文件路径当作产物。'),actions('read','path-denied'));
  root.append(receipt,launch,artifact);return root;
}
