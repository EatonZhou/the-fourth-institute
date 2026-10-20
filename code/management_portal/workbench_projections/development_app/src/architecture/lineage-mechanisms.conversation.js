export function renderIntake({session,h,actions}) {
  const s=session.state,root=h('div','lm-intake'),observed=s.queue_observation==='observed';
  const accepted=h('section','lm-input-record');accepted.dataset.accepted=String(s.accepted);accepted.append(h('h3','','输入持久记录'),h('strong','',s.accepted?'身份、内容和上下文种子已保存':s.duplicate_id?'重复身份：读取已有投递结果':'新输入等待接收'),actions('accept','duplicate'));
  if(s.accepted)accepted.append(h('p','',`本次输入 input_state = ${s.input_state}`));
  const scheduling=h('section','lm-scheduling');scheduling.append(h('h3','','读取同一次持久化分支'),h('p','',!s.intake_open?'接单关闭：接受消息，保留但不启动。':s.has_active?'已有活动工作：新输入追加等待。':'接单开放且无活动工作：新输入可成为 job head。'),actions('hold','start'));
  const waiting=h('section','lm-queue-slot');waiting.dataset.observation=s.queue_observation;waiting.dataset.scheduling=s.phase;
  waiting.append(h('h3','','输入在哪里，公开计数又数什么'),h('p','','接收与读取观测是对同一次持久化分支的分步解释，不表示数据库曾提交过“已 queued、计数还未更新”的中间状态。'),actions('observe_normal','observe_closed','observe_busy'));
  if(!observed)waiting.append(h('strong','lm-queue-unobserved','尚未读取队列观测 · 不展示初值 0 作为排队事实'));
  else {
    const positions=h('div','lm-job-positions');
    const head=h('div','lm-job-head');head.dataset.newInput=String(s.new_input_location==='job_head');head.append(h('strong','','job head'),h('code','',s.new_input_location==='job_head'?'demo.new':s.active_message_id),h('p','',s.new_input_location==='job_head'?(s.intake_open?'新输入已获准，尚无执行进展':'新输入保留在头部，接单关闭'):'原活动输入继续占据头部'));
    const queue=h('div','lm-job-waiters');queue.dataset.newInput=String(s.new_input_location==='internal_queue');queue.append(h('strong','','内部 job.queue'),h('code','',s.new_input_location==='internal_queue'?'demo.new':'没有内部等待项'),h('p','',`内部长度 ${s.internal_queue_size}`));positions.append(head,queue);waiting.append(positions);
    const counts=h('dl','lm-queue-counts');
    for(const [field,label] of [['public_queued_count','公开等待数 queued_count'],['queue_size','新输入对公开等待数的贡献'],['work_state','公开 work_state'],['active_message_id','公开 active_message_id']]){counts.append(h('dt','',label));const value=h('dd','',String(s[field]|| (field==='active_message_id'?'空':s[field])));value.dataset.queueField=field;counts.append(value);}
    waiting.append(counts,h('p','',!s.intake_open?'关闭接单的 head 也计入公开等待项，所以内部长度 0 可以对应公开等待数 1。':s.new_input_location==='job_head'?'已获准的 head 不计入公开等待项：input_state=queued、work_state=queued 与公开等待数 0 可以同时成立。':'已有活动 head 不计入等待数，新输入位于内部 queue 并计为一个公开等待项。'));
  }
  const cancel=h('section','lm-cancel-intent');cancel.dataset.stopIntent=String(s.stop_intent);cancel.append(h('h3','','取消先保存意图'),actions('stop'),h('p','',s.stop_intent?'取消意图已落盘，随后异步收敛；上方保留本案例读取时的观测。':'只有本情境请求取消时才进入停止分支。'));
  root.append(accepted,scheduling,waiting,cancel);return root;
}

export function renderBinding({session,h,actions}) {
  const s=session.state, root=h('div','lm-binding');
  const request=h('section','lm-durable-request');request.dataset.saved=String(s.request_saved);request.append(h('h3','','Lineage 持久工作'),h('strong','',s.request_saved?'不可变 Execution 请求已保存':'请求尚未持久保存'),h('p','','这是提交与恢复共享的原请求，不在重启时重新拼一份。'),actions('save'));
  const channel=h('section','lm-submission-channel');channel.dataset.path=s.resumed?'resume':s.submitted_new?'submit':'none';channel.append(h('h3','',s.submitted_before?'恢复原 execution_id':'首次提交'),actions('submit','resume','cancel'),h('strong','',s.resumed?'重新订阅原执行 · 新提交 0':s.submitted_new?'已提交这份持久请求':s.phase==='cancelled_before_submit'?'停止意图抢先 · 新提交 0':s.submitted_before?'已有原执行，当前进程尚待恢复订阅 · 本轮新提交 0':'尚未越过提交边界'));
  const execution=h('section','lm-execution-owner');execution.append(h('h3','','Execution'),h('p','',s.bound?'Lineage 已绑定该执行并接收它的进展。':'尚无新的执行绑定。'),h('p','','Execution 负责模型任务；Lineage 保留领域工作与绑定，服务恢复不等于新建任务。'));
  root.append(request,channel,execution);return root;
}

export function renderRound({session,h,actions}) {
  const s=session.state, root=h('div','lm-round');
  const work=h('section','lm-round-owner');work.append(h('h3','','Lineage 始终持有本轮领域工作'),h('strong','',s.input_state==='completed'?'旧工作已结束':s.request_durable?'原输入与不可变请求已保存':s.input_state==='queued'?'原输入已保存':'等待原输入'),actions('accept','prepare'));
  const exchanges=h('div','lm-round-exchanges');
  function exchange(key,from,transfer,to,reached,ids,detail){
    const row=h('section','lm-round-exchange');row.dataset.exchange=key;row.dataset.reached=String(reached);
    const origin=h('div','lm-exchange-origin');origin.append(h('strong','',from));
    const wire=h('div','lm-exchange-wire');wire.append(h('span','',transfer),h('span','lm-exchange-arrow','→'),actions(...ids));
    const target=h('div','lm-exchange-target');target.append(h('strong','',to),h('p','',detail));row.append(origin,wire,target);exchanges.append(row);
  }
  exchange('submit','Lineage · 持久请求','提交同一份请求','Execution · 模型任务',s.execution_bound,['submit','cancel'],s.phase==='cancelled'?'提交前取消：没有新任务。':s.execution_bound?'请求已提交并绑定；领域工作仍留在 Lineage。':'等待提交；不能从未持久的输入直接启动。');
  exchange('reply','Execution · final','当前绑定许可 → 追加回复','Conversation · 本轮消息',s.reply_saved,['reply','obsolete'],s.phase==='ignored'?'迟到绑定被拒绝，未追加回复。':s.reply_saved?'获许可的回复已保存。':'结果仍须匹配本轮绑定；迟到结果不能写入新一轮。');
  exchange('ack',s.reply_saved?'回复已保存':'尚无已保存回复','确认完成 ACK','Lineage · 收回旧工作',s.input_state==='completed',['finalize'],s.input_state==='completed'?'旧工作完成，才能继续推进队列。':s.reply_saved?'等待确认；看到回复不等于领域工作已经结束。':'回复未保存，不能确认完成或结束旧工作。');
  root.append(work,exchanges);return root;
}
