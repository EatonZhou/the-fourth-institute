export function renderFamilyRouting({session,h,actions}) {
  const s=session.state, root=h('div','lm-family-routing');
  const request=h('section','lm-facade-request');request.append(h('span','tag','同一原请求'),h('h3','','Family 门面'),h('p','','门面保存对外请求，本身没有模型会话。'));
  const roster=h('section','lm-roster');roster.append(h('h3','',`当前业务成员 ${s.member_count}`));
  for(let i=0;i<s.member_count;i++)roster.append(h('span','lm-member',`成员位置 ${i+1}`));
  roster.append(h('p','','位置仅作本地名册示意，不构造真实 Resident 身份。'));
  const routing=h('section','lm-router-choice');routing.dataset.routerNeeded=String(s.router_needed);routing.append(h('h3','',s.quoted_member?'引用指向原接手者':s.member_count===0?'等待成员':s.member_count===1?'直接确定唯一候选':'交 Router 选择'),actions('quote','empty','single','multiple','selected','invalid'));
  routing.append(h('p','',s.phase==='invalid_selection'?'选择已不在当前名册，不能交给候选成员。':s.router_needed?'Router 根据当前成员摘要与状态选人，回复须关联原请求并复核名册。':s.quoted_member?'根据引用回到原接手者，仍要经过转交准入。':s.member_count===1?'无需模型选人，但没有免除准入校验。':'没有业务成员时保留原请求等待。'));
  if(s.candidate_ready)routing.append(h('span','lm-candidate-token','原请求 → 已确定的同一候选'));
  const admission=h('section','lm-routing-admission');admission.dataset.requested=String(s.admission_requested);admission.append(h('h3','','共同的转交准入'),actions('admit'),h('strong','',s.admission_requested?'已提出准入请求，尚不代表投递完成':s.candidate_ready?'候选已确定，等待准入':'尚无可进入准入的候选'));
  if(s.admission_requested)admission.append(h('span','lm-candidate-token','同一原请求 + 同一候选 → 准入检查'));
  root.append(request,roster,routing,admission);return root;
}

export function renderFamilySupervision({session,h,actions}) {
  const s=session.state, root=h('div','lm-supervision');
  const incident=h('section','lm-incident-record');incident.append(h('span','tag','始终追踪同一原输入'),h('h3','','恢复事件'),h('strong','',`原输入：${({failed:'失败',completed:'已完成',cancelled:'已取消',queued:'等待',running:'执行中'})[s.original_state]||s.original_state}`),actions('observe'));
  const attempts=h('section','lm-repair-attempts');attempts.append(h('h3','','有界修复'));const marks=h('div','lm-attempt-marks');
  for(let i=0;i<3;i++){const mark=h('span',i<s.attempts_before?'used':'',String(i+1));mark.setAttribute('aria-label',`第 ${i+1} 次${i<s.attempts_before?'已使用':'尚未使用'}`);marks.append(mark);}attempts.append(marks,actions('repair'),h('p','',s.notice_outstanding?'修复通知已发起，原输入仍须后续观察；发通知不等于修复成功。':s.repair_pending?'已有修复在途，不叠加另一条。':'只有失败且可接单、无在途修复时才发起下一次。'));
  const disposition=h('section','lm-incident-disposition');disposition.dataset.incident=s.incident_state;disposition.append(h('h3','','以原输入的现状决定去向'),actions('resolved','cancelled','wait','paused','escalate','missing-creator'));
  const text={resolved:'原输入已完成：关闭恢复事件。',cancelled:'原输入已取消：不再重试。',waiting:'等待原输入或既有修复推进，不新增通知。',paused:'接单已关闭，恢复暂停。',escalated:'修复次数用尽，升级给已知的人类创建者。',missing_creator:'找不到原人类创建者：保留缺失状态，不猜收件人。',open:'尚未收敛，继续观察同一原输入。'};
  disposition.append(h('strong','',text[s.incident_state]));root.append(incident,attempts,disposition);return root;
}
