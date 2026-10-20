// Content-specific views over the owner's frozen state. No extra simulator,
// concrete Resident/message IDs, business payloads, SDK or outbound effects.
export function createFamilyViews(h) {
const group=(cls,...items)=>{const n=h('div',cls);n.append(...items);return n;};
const paragraph=text=>h('p','',text);
function heading(label,small){return group('family-lane-head',h('h3','',label),h('small','',small));}
function request(label='原请求',detail='原发送者 · 原正文与附件'){
 const n=h('article','family-request');n.dataset.requestRef='original-request';
 n.append(h('span','family-request-mark',label),h('strong','','同一份用户请求'),paragraph(detail));return n;
}
function reply({unrelated=false,reference=false}={}){
 const n=h('article',`family-reply ${reference?'family-reply-reference':''}`);n.dataset.replyRef=unrelated?'unrelated-reply':'worker-reply';
 n.append(h('span','family-reply-mark',reference?'原回复的显示引用':'实际 worker 写下的回复'),
  h('strong','',reference?'门面显示这一条回答':'正文保存在 worker 对话'),
  paragraph(reference?'读取实际 worker 对话中的原回复，不重新生成答案。':'可以是中间输出，也可以是最终回复。'),
  h('div',`family-reply-to ${unrelated?'family-unrelated':''}`,unrelated?'↳ 回答另一请求':'↳ 回答本次转交的成员输入'));
 return n;
}
function stepActions(action,...ids){return group('family-actions',...ids.map(id=>action(id)));}
function sourceNote(text){return paragraph(text);}
function renderFamilyAdmission({session,action}){
 const s=session.state,duplicate=s.phase==='already_relayed',sent=s.sent,waiting=s.review_waiting,blocked=s.phase==='withdrawn'||s.phase==='invalid_member';
 const surface=h('div','family-admission-face');surface.dataset.familyPhase=s.phase;
 const premise=h('div','family-premise');
 premise.append(h('strong','',s.original_cancelled?'本情境的原请求已经撤回，检查不能继续放行':!s.member_current?'本情境的旧候选已离开名册，检查不能继续放行':s.review_approved?'独立情境：本请求与当前目标已有本 Family steward 的批准':s.weekly_below_threshold?'当前候选额度低于保留阈值：先复核这份原请求':s.relay_exists?'独立情境：原请求已有稳定转交关联':'候选已选定；投递前还要检查原请求与当前成员'));
 const facade=h('section','family-facade-lane');facade.append(heading('门面 · 原请求所在处','门面不运行模型会话'),request());
 const status=h('p','family-request-location');status.dataset.originalLocation=waiting?'facade-waiting':sent||duplicate?'facade-linked':blocked?'facade-stopped':'facade';
 status.textContent=waiting?'原请求留在这里，等待复核。':sent||duplicate?'原请求与成员输入的关联已保留。':s.relay_exists?'原请求已有成员输入，正在核对既有关联。':s.phase==='withdrawn'?'原请求已撤回；停止这一转交。':s.phase==='invalid_member'?'原请求仍在；旧候选已经离开名册。':'当前只选定了目标，尚未投给成员。';
 facade.append(status);
 const review=h('section',`family-review-desk ${waiting?'is-waiting':''}`);review.dataset.reviewOwner='own-family-steward';
 review.append(heading('本 Family 的 steward','复核角色'),h('strong','',waiting?'等待对原请求的判断':s.review_approved?'本情境已有批准':'是否需要复核，由当前准入决定'));
 review.append(paragraph(waiting?'复核指向门面中的同一份原请求与已选成员；不会发出替代任务。':s.review_approved?'批准只绑定这一请求与目标，不能替代原请求状态和成员身份检查。':'未知额度不按零处理；选人完成不等于允许投递。'));
 if(waiting)review.append(h('div','family-review-pointer','↖ 复核这一份原请求 · 目标为已选成员'));
 facade.append(review);
 const gate=h('section',`family-admission-gate ${waiting||blocked?'is-stopped':''}`);gate.dataset.deliveryGate=waiting?'review-wait':blocked?'rejected':s.admission_saved?'saved':duplicate?'reuse':'checking';
 gate.append(h('span','family-flow-symbol',waiting||blocked?'Ⅱ':sent||duplicate?'→':'⇢'),h('strong','',waiting?'尚未投递':s.phase==='withdrawn'?'撤回优先':s.phase==='invalid_member'?'候选失效':duplicate?'复用既有关联':s.admission_saved?'准入先保存':'检查后才能投递'),stepActions(action,'withdraw','member','existing','review','admission','send'));
 if(waiting)gate.append(paragraph('没有“批准”演示按钮。切换已批准情境会独立重置。'));
 const member=h('section','family-member-lane');member.append(heading('已选成员 · 接收处','投递保留原发送者'));
 if(sent||duplicate){const copy=request('关联的成员输入',duplicate?'原有稳定成员消息 · 本次复用':'原发送者、正文与附件沿用原请求');copy.dataset.requestRole='member-input';member.append(copy,h('p','family-link-label',duplicate?'原请求 ↔ 原有成员消息；没有新副本':'原请求 ↔ 已接受的稳定成员消息'));}
 else member.append(group('family-empty-slot',h('strong','',s.relay_exists?'已有成员输入，等待核对关联':'尚未接受本次转交'),paragraph(s.relay_exists?'接下来复用已保存的消息身份，不再制造副本。':waiting?'请求仍在门面。':s.phase==='invalid_member'?'不使用旧名册把请求投给已离开的成员。':s.phase==='withdrawn'?'没有新投递；取消竞态由平台收敛已接受副本。':'选择了成员并不代表请求已经进入其对话。')));
 surface.append(premise,group('family-admission-route',facade,gate,member),sourceNote('这里追踪消息的角色与位置，不编造实际请求正文或成员姓名。稳定成员输入与原请求的关联来自本批冻结源码。'));
 return surface;
}
function renderFamilyProjection({session,action}){
 const s=session.state,read=['read','projected','excluded'].includes(s.phase),unrelated=s.reply_exists&&(!s.agent_reply||!s.reply_to_matches);
 const surface=h('div','family-projection-face');surface.dataset.familyPhase=s.phase;
 const trace=h('section','family-relay-trace');trace.append(heading('先沿这份原请求找回答者','只沿已保存的转交关联'));
 const path=group('family-holder-path',group('family-holder',h('strong','','外层门面'),h('small','','原请求')),
  h('span','family-flow-symbol','→'));
 if(s.nested)path.append(group('family-holder',h('strong','','内层门面'),h('small','','外层所选成员 · 转交经过这里')),h('span','family-flow-symbol','→'));
 path.append(group('family-holder',h('strong','',s.resolved_worker?'实际 worker 已定位':'实际 worker 待定位'),h('small','','真正写下回答的人')));trace.append(path,stepActions(action,'resolve'));
 const view=h('section','family-visible-conversation');view.append(heading('用户看到的门面对话','显示引用 · 不是第二份消息存储'),request());
 const projection=h('div','family-projection-slot');projection.dataset.projected=String(s.projected);
 if(s.projected)projection.append(reply({reference:true}),h('p','family-origin-label',s.nested?'来源：实际 worker → 经内层门面 → 原请求':'来源：实际 worker → 原请求'));
 else projection.append(group('family-empty-slot',h('strong','',s.phase==='excluded'?'原请求下面不显示这条回答':'原请求下面还没有投影回复'),paragraph(s.phase==='excluded'?(s.reply_exists?'同一成员写的其他任务回答，也不属于这个请求。':'尚无实际回复，门面不补造答案。'):'先找到实际 worker，再核对其回复到底回答了哪一份输入。')));
 view.append(projection);
 const bridge=h('section',`family-projection-bridge ${s.phase==='excluded'?'is-stopped':''}`);bridge.dataset.replyBoundary=s.projected?'projected':s.phase==='excluded'?'excluded':read?'checked':'unread';
 bridge.append(h('span','family-flow-symbol',s.projected?'←':s.phase==='excluded'?'Ⅱ':'⇠'),h('strong','',s.projected?'显示同一条原回复':s.phase==='excluded'?'原请求边界保留':'核对回复关联'),paragraph(read?(s.reply_exists?(unrelated?'回复关联指向其他输入。':'Agent 回复关联匹配这份转交输入。'):'实际 worker 对话中没有这条回复。'):'读取前不把候选结果当成已经匹配。'),stepActions(action,'read','exclude','project'));
 const worker=h('section','family-worker-conversation');worker.append(heading('实际 worker 的对话','唯一保存回答正文的位置'));
 if(!read)worker.append(group('family-empty-slot',h('strong','','尚未读取这份对话'),paragraph('定位 worker 后，按保存的转交输入核对回复。')));
 else if(!s.reply_exists)worker.append(group('family-empty-slot',h('strong','','回复尚未到达'),paragraph('没有原回复可供门面显示。')));
 else{
  worker.append(reply({unrelated}),group(`family-reply-target ${unrelated?'is-unrelated':''}`,h('strong','',unrelated?'这条回复回答另一份输入':'对应本次转交的成员输入'),paragraph(unrelated?'它仍留在 worker 对话中；不能接到门面的原请求下面。':'该成员输入沿转交链对应门面中的同一份原请求。')));
 }
 surface.append(trace,group('family-projection-route',view,bridge,worker),sourceNote('消息图使用“原请求 / 成员输入 / 原回复”身份标记，未给出业务正文或具体消息 ID。门面不创建模型会话；回复正文保留在实际 worker 对话。'));
 return surface;
}

return {renderFamilyAdmission,renderFamilyProjection};
}
