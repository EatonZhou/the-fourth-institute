// Output has two durable locations and a separate completion acknowledgement.
export function renderOutput({session,h,action,actions}) {
      const s=session.state, ignored=s.phase==='ignored',pending=s.phase==='ack_pending',writeFailed=s.phase==='projection_pending';
      const surface=h('div','output-workface');surface.dataset.outputPhase=s.phase;
      const timeline=h('ol','handoff-scopes');timeline.setAttribute('aria-label','锁的使用范围，按逻辑顺序排列，不表示耗时');
      for(const [label,note,complete] of [['① 许可','Resident 短锁',s.projection_intent],['② 追加','释放 Resident 锁后写入',s.message_persisted],['③ 确认','再次进入 Resident 短锁',s.projection_confirmed]]){const item=h('li',complete?'scope-done':'');item.append(h('strong','',label),h('span','',note));timeline.append(item);}surface.append(timeline);
      const board=h('div','handoff-board');
      const resident=h('section','resident-track');resident.append(h('h3','','Resident · 工作与许可'));
      const gate=h('div',`reply-admission ${ignored?'reply-blocked':''}`);gate.dataset.outputGate=ignored?'blocked':s.projection_intent?'allowed':'unchecked';gate.append(h('span','scope-label','① 短锁内核对输入与执行'));
      const reason=!s.binding_matches?'这条 final 来自旧执行，与当前工作不匹配。':s.stop_before_final?'停止请求先于这条 final 的许可。':'这条回复属于当前输入与当前执行。';
      gate.append(h('p','',reason),actions('authorize','obsolete'));
      if(ignored)gate.append(h('strong','blocked-reply','⊣ final 停在这里，不写入当前对话'));
      else if(s.projection_intent)gate.append(h('div','permit-slip','同一回复的许可已保存 · 已释放短锁'));
      else gate.append(h('div','reply-origin','待处理：这条 final 回调'));
      resident.append(gate);
      const job=h('div',s.job_cleared?'work-binding work-released':'work-binding');job.dataset.workBinding=s.job_cleared?'released':'retained';
      if(s.job_cleared){job.append(h('strong','','旧工作已清理 / 推进'),h('p','','完成 ACK 后重新核对了绑定；不再保留这条旧工作。'));}
      else{job.append(h('span','scope-label','持久工作记录'),h('strong','',s.binding_matches?'本次输入 ↔ 当前执行':'当前有效工作 ↔ 另一执行'),h('p','',ignored?'当前工作保持原状，旧回调不能清掉它。':pending?'旧工作仍在这里，等待完成 ACK 重试。':s.projection_confirmed?'回复落盘已确认；旧工作仍等待完成 ACK。':s.message_persisted?'回复已保存；还需确认落盘，再完成 ACK。':'保留工作绑定，等待输出与完成处理。'));}
      resident.append(job);
      const confirm=h('div','projection-confirm');confirm.append(h('span','scope-label','③ 短锁内确认同一条回复'),action('confirm'));
      confirm.append(h('p','',s.projection_confirmed?'已确认对话里保存的这条回复。':s.message_persisted?'收到落盘结果，等待回到短锁内确认。':'尚未收到这条回复的落盘结果。'));resident.append(confirm);
      const connector=h('div','reply-connection');connector.setAttribute('aria-label','同一回复从许可到对话；落盘结果回到短锁确认');connector.append(h('span','connection-arrow',ignored?'⊣':'→'),h('span','',ignored?'被挡住':'同一回复'),h('span','connection-identity','稳定消息身份'));
      if(s.message_persisted){const returned=h('span','connection-return');returned.append(h('span','return-arrow','←'),h('span','','落盘结果'));connector.append(returned);}else if(writeFailed)connector.append(h('span','connection-return','追加失败，许可保留'));
      const conversation=h('section','conversation-track');conversation.append(h('h3','','Conversation · 持久消息'),h('span','scope-label','② 在 Resident 锁外追加'));
      const paper=h('div','conversation-paper');paper.dataset.replyStore=s.message_persisted?'persisted':'empty';
      if(s.message_persisted){
        const reply=h('article','persisted-reply');reply.dataset.replyToken='same-reply';reply.append(h('span','message-saved','已保存'),h('h4','','本轮回复'),h('p','','实际 Resident 的回复，归属这次输入。'),h('small','','同一稳定消息身份；重复回调沿用它。'));paper.append(reply);
        paper.append(h('p','persistent-note',pending?'完成 ACK 正在等待；这条回复继续保存在这里。':s.job_cleared?'工作已完成收尾；这条回复仍保存在这里。':'这条回复已持久保存，后续是确认与工作收尾。'));
      }else{
        const blank=h('div','reply-placeholder');blank.append(h('strong','',ignored?'这条 final 未进入当前对话':writeFailed?'这次追加尚未成功':'这里尚未保存本轮回复'),h('p','',ignored?'门前已经拦截，不混入新一轮对话。':writeFailed?'Resident 的许可与工作都保留，等待恢复追加。':'只有通过许可的同一条回复，才能在这里落盘。'));paper.append(blank);
      }
      conversation.append(paper,action('append','追加这条回复到对话'),action('retry','本情境：对话追加失败'));board.append(resident,connector,conversation);surface.append(board);
      const completion=h('section',`completion-channel ${pending?'ack-pending':''}`);completion.dataset.completion=pending?'ack-pending':s.job_cleared?'finalized':s.projection_confirmed?'ready':'not-ready';
      const origin=h('div','completion-origin');origin.append(h('span','scope-label','工作完成路径'),h('strong','',s.job_cleared?'旧工作收尾完成':'从 Resident 保留的工作绑定出发'),h('p','',s.projection_confirmed?'落盘已确认，可以处理执行完成。':'先确认回复已落盘，再处理执行完成。'));
      const exchange=h('div','ack-exchange');exchange.append(h('span','ack-arrow',pending?'⇥':'→'),h('strong','','Execution 完成 ACK'));
      const result=h('div','completion-result');result.append(action('ack','接收完成 ACK，并重新核对绑定'),action('ack-pending','本情境：完成 ACK 暂失败'),h('strong','completion-state',pending?'ACK 未完成 · 旧工作继续保留':s.job_cleared?'ACK 完成 · 已核对并清理 / 推进旧工作':s.projection_confirmed?'等待完成 ACK':'尚未进入完成 ACK'));
      result.append(h('p','',pending?'等待重试的是完成 ACK。上方消息已经存在，不退回追加阶段。':s.job_cleared?'回复仍在对话中；这条旧工作完成了收尾。':'完成处理在消息落盘之后；每次推进还要核对当前工作。'));
      completion.append(origin,exchange,result);surface.append(completion);
      const reading=h('p','output-reading',pending?'此刻：对话里已有回复；Resident 仍保留旧工作。缺的是完成 ACK，而不是再写一条回复。':ignored?'此刻：回调在许可处被挡住；当前对话和有效工作都没有被旧回调改写。':writeFailed?'此刻：回复尚未保存；许可与旧工作仍在，等待恢复追加。':s.job_cleared?'此刻：同一回复保存在对话中，旧工作已完成收尾。':'沿同一回复观察：许可、落盘、确认，与工作完成分别发生。');surface.append(reading);return surface;
}
