import './provider-mechanisms.css';
import {createProviderSimulation, supportsProviderMechanisms, getProviderWorkbenchEntry, createDomainModeSession, getProviderDelegatedModes} from './provider-mechanisms.model.js';
import {createTypedAnswerTeachingExample} from './provider-mechanisms.typed-example.js';
export {supportsProviderMechanisms, getProviderWorkbenchEntry};

// These are local, content-driven diagrams. All evidence and semantic identity
// arrive through the parent; no provider, SDK, catalog or network access lives here.
const words = {
  unknown:'未核实', waiting:'等待完整响应', pending:'尚未处理', idle:'尚未开始', completed:'完成',
  cancelled_wait:'本地等待已结束', cancelled_before_request:'发送前已取消', retrying:'等待后重试',
  failed_retry_budget:'重试预算耗尽', failed_http:'HTTP 错误', failed_empty_text:'正文为空', failed_response_decode:'响应解码失败',
  response_observed:'已观察到响应', open:'连接打开', closed:'连接关闭', valid:'合法', invalid:'不合法',
  absent:'未要求 Schema', no_json:'没有 JSON', mismatch:'结构不匹配', missing_message:'缺少消息',
  bad_question:'问题不合法', oversize:'超过 2 MB', wrong_profile:'接口类型错误', question_mismatch:'题集不匹配',
  invalid_value:'答案数值不合法', missing_usage:'usage 不合法',
  existing_native_account:'既有原生账号', api_profile:'普通 API profile', unsupported_session_route:'拒绝原生会话路线',
  api_profile_ready:'普通 API profile 可用', provider_primary:'显式 primary 条目', first_enabled_chat_entry:'排序第一的可用聊天接口',
  relay_resource_registration:'调用项登记', none:'没有可用接口', stateless:'无状态请求',
  requires_portal_tools:'缺少 portal_tools', ValueError:'操作校验拒绝',
  unresolved:'尚未解析', selected:'已选定', configuration_rejected:'配置校验失败', route_selected:'路线已确认',
  native_cli_account_directory:'原生 CLI 账号目录', registry_key_file:'Registry 密钥文件', explicit_route_binding:'显式路线绑定',
  registry_profile_key_file:'Registry profile 密钥文件', no_api_key:'无需 API key', configured_openai_compatible_endpoint:'profile 配置的 OpenAI 兼容端点',
  openai_compatible_chat:'普通文本接口', typesafe_system_one:'TypeSafe 判定接口', unsupported:'不支持的类型',
  model_mismatch:'覆盖固定模型', unknown_resource:'调用项未登记', stream:'请求流式输出',
};
const label = value => value === '' || value == null ? '尚未确定' : words[value] || String(value);
const scenarioTitle = item => item.title!==item.id?item.title:({normal:'完整响应',invalid_parameters:'请求参数不适用',transient_then_success:'暂时故障后重试成功',retry_exhausted:'两次尝试耗尽预算',fatal_error:'不可重试故障',invalid_response:'响应不满足协议',cancel_before:'发送前取消',cancel_during_call:'等待中取消',late_response:'取消后响应迟到',late_error:'取消后异常迟到',cancel_before_retry:'下次重试前取消',peer_denied:'IP 来源不允许',slots_full:'连接名额已满',empty_frame:'零长度帧',oversize_frame:'正文超过上限',bad_json:'非法 JSON 帧',frame_timeout:'整帧读取超时',bad_payload:'业务正文不合法',bad_version:'协议版本不合法',stream:'拒绝流式输出',unknown_resource:'调用项未登记',model_mismatch:'覆盖固定模型',upstream_failed:'上游执行失败',same_id_twice:'同 request_id 连发两帧',write_disconnect:'写回前客户端断开',business400_then_valid:'业务 400 后，同连接下一帧成功',business403_then_valid:'业务 403 后，同连接下一帧成功'}[item.id]||item.title);
function el(tag, cls, text) { const node=document.createElement(tag); if(cls)node.className=cls; if(text!==undefined)node.textContent=text; return node; }
function badge(text,tone='') { return el('span',`pvd-badge ${tone}`,text); }
function panel(title,tone='') { const node=el('section',`pvd-panel ${tone}`); node.append(el('h3','',title)); return node; }
function row(root,name,value,tone='') { const node=el('div',`pvd-row ${tone}`); node.append(el('span','',name),el('strong','',value));root.append(node);return node; }
function note(root,text) { root.append(el('p','pvd-note',text)); }
function arrow(text) { const node=el('div','pvd-arrow');node.append(el('span','',text)); return node; }
function passed(scene,id) { return scene.trace.some(step=>step.id===id); }
function gate(title,value,done) { const node=panel(title,done?(value?'good':'stop'):'');node.append(badge(done?(value?'通过':'拒绝'):'待检查',done?(value?'good':'stop'):''));return node; }
function chatDiagram(scene) {
  const s=scene.state,root=el('div','pvd-split'),local=panel('Portal · 调用方'),remote=panel('外部 HTTP 请求');
  row(local,'本地状态',label(s.status));row(local,'已发请求',String(s.request_count));
  const cancelled=s.status==='cancelled_wait';
  if(cancelled)note(local,'等待已经结束；迟到结果不会成为本次回答。');
  row(remote,'观察结果',label(s.remote_state),s.remote_state==='unknown'?'uncertain':'good');
  const packet=el('div',`pvd-envelope ${s.request_count?'sent':''}`,s.request_count?'完整 JSON POST 已发出':'HTTP 尚未发出');remote.append(packet);
  if(s.request_count&&s.remote_state==='unknown')note(remote,'没有远端终止确认。请求可能继续执行，仍可能产生费用。');
  if(s.output) {const answer=el('div','pvd-answer');answer.append(el('strong','','本次回答'),el('p','',s.output));local.append(answer);}
  root.append(local,arrow(cancelled?'停止等待':s.request_count?'HTTP':'待发送'),remote);return root;
}
function typedDiagram(scene) {
  const s=scene.state,root=el('div','pvd-typed-answer'),envelopes=el('div','pvd-split'),request=panel('Jev · 问题封套'),answer=panel('Jev · 答案封套');
  request.append(el('div','pvd-document','state\nquestions［ID · type · criteria］\nmodel'));row(request,'请求前提',scene.cursor?label(s.request_check):'待载入');
  row(request,'HTTP',s.request_sent?'已发出':'未发出');note(request,'接口配置固定；非法题型、重复 ID、criteria 或超过 2 MB 的请求在发送前拒绝。');
  answer.append(el('div','pvd-document','model\nanswers［ID · type · value］\nusage'));row(answer,'响应检查',s.request_sent?(scene.next?'等待完整响应':label(s.response_check)):'尚未收到');
  row(answer,'整份答案',s.answers_accepted?'已接受':scene.next?'尚未接受':'未接受',s.answers_accepted?'good':'');
  note(answer,'逐题匹配 ID、类型和数值。choice 概率在 0..1 内；不额外要求概率和为 1。');envelopes.append(request,arrow(s.request_sent?'整份响应':'发送前校验'),answer);root.append(envelopes);
  const sample=createTypedAnswerTeachingExample(s.response_check),observed=s.request_sent&&!scene.next;
  const teaching=panel('按问题 ID 对齐 · 本地教学样例');
  note(teaching,'下面的问题、答案和数值用于解释固定校验规则，不是实际请求或运行结果。整份答案的接受状态仍由上方机制决定。');
  teaching.append(el('div','pvd-teaching-context',sample.context));
  const table=el('table','pvd-answer-alignment'),caption=el('caption','',observed?'逐行查看问题与答案；任一错误使整份响应失败':'先查看请求题目，推进到响应检查后按 ID 核对');table.append(caption);
  const head=el('thead'),tr=el('tr');for(const title of ['问题 ID / 请求','对应答案','逐行核对'])tr.append(el('th','',title));head.append(tr);table.append(head);
  const body=el('tbody');
  for(const item of sample.rows) {
    if(!observed&&!item.question)continue;
    const line=el('tr',observed&&item.problem?'pvd-answer-error':'');line.dataset.questionId=item.id;
    const question=el('th');question.scope='row';question.append(el('code','',item.id),el('p','',item.question?.instructions||'请求中没有此题'));
    if(item.question)question.append(el('small','',`${item.question.type}${item.question.criteria?' · criteria: '+JSON.stringify(item.question.criteria):' · noul 概率 0..1'}`));
    const result=el('td');result.append(el('pre','',observed?(item.answer?JSON.stringify(item.answer,null,2):'缺少答案'):'尚未观察响应'));
    const check=el('td','',observed?item.explanation:'等待核对');line.append(question,result,check);body.append(line);
  }
  table.append(body);teaching.append(table);
  if(observed&&s.response_check==='question_mismatch')note(teaching,'本例缺 q_quality、多 q_unasked。即使其他题正确，也不返回部分成功。');
  if(observed&&s.response_check==='invalid_value')note(teaching,'教学表同时标出类型和范围问题；源码在遇到错误时拒绝整份响应，不要求收集全部错误。');
  if(observed&&s.response_check==='missing_usage')note(teaching,'本例逐题内容相符，但 usage 不合法，整份响应仍失败。');
  root.append(teaching);return root;
}
function completeDiagram(scene) {
  const s=scene.state,root=el('div','pvd-pipeline');
  const schema=gate('调用者 Schema',s.schema_check!=='invalid',passed(scene,'prepare')||passed(scene,'invalid'));
  row(schema,'要求',scene.cursor?label(s.schema_check):'待载入');
  const http=panel('同步 Registry 调用');row(http,'HTTP',s.http_sent?'已发出':'未发出');http.append(badge(`Execution 队列：${s.queue_submitted?'已提交':'未提交'}`));
  const answer=panel('响应校验');row(answer,'JSON 对象 / 数组',s.structured_available?'已验证':scene.next?'待处理':'未提供');
  row(answer,'错误可重试',s.retryable?'是 · 由调用者决定':'未标记');
  note(answer,s.schema_check==='absent'?'未请求 Schema 时保留普通 message 与 usage。':'答案不匹配时不返回伪造的 structured；可重试标记不会自动再发请求。');
  root.append(schema,http,answer);return root;
}
function capabilitiesDiagram(scene) {
  const s=scene.state,root=el('div','pvd-capability');
  const operation=gate('① 操作声明',s.operation_allowed,scene.cursor>1),feature=gate('② 插件能力要求',s.feature_enabled,passed(scene,'requirements')||passed(scene,'missing-cap'));
  note(operation,'ExecutionRequest 的 operation 必须在 Adapter 声明中。');
  note(feature,'满足任一完整 AND 组即可（OR-of-AND）；不把所有备选要求合并。');
  if(s.reason)feature.append(badge(label(s.reason),'stop'));if(s.error)operation.append(badge(label(s.error),'stop'));
  root.append(operation,feature);const boundary=el('div','pvd-provider-boundary');boundary.append(el('strong','','Provider 调用边界'),el('p','','本机制只给出校验结果；通过检查不表示已调用 Provider。资源与 ISA 授权仍分别检查。'));root.append(boundary);return root;
}
function connectionDiagram(scene) {
  const s=scene.state,root=el('div','pvd-connection'),ledger=panel('连接名额'),socket=panel('一条 TCP 连接',s.connection==='closed'?'stop':'');
  row(ledger,'IP 检查',scene.cursor?(s.peer_allowed?'允许':'拒绝'):'待检查');row(ledger,'名额',s.slot_held?'本连接持有':'未占用');
  note(ledger,'名额按连接持有。连接结束的 finally 释放，解码一帧不释放。');
  socket.append(badge(label(s.connection),s.connection==='closed'?'stop':'good'));
  const frame=el('div','pvd-frame');frame.append(el('div','pvd-frame-head','4 字节\n大端长度'),el('div','pvd-frame-body','UTF-8 JSON 对象'));socket.append(frame);
  row(socket,'帧',s.frame_decoded?'已解码':scene.next?'读取前 / 读取中':'没有有效帧');
  row(socket,'状态码',s.response_status?String(s.response_status):'无状态响应');
  const deadline=el('div',`pvd-deadline ${passed(scene,'timeout')?'stop':''}`,'同一个 30 秒读取期限 · recv 不重置');socket.append(deadline);
  if(passed(scene,'timeout'))note(socket,'截止时间到达直接关闭；不伪造一个错误响应帧。');
  root.append(ledger,socket);return root;
}
function routingDiagram(scene) {
  const s=scene.state,root=el('div','pvd-routing'),device=panel('设备请求'),registry=panel('登记的调用项'),result=panel('接口选择结果');
  row(device,'请求检查',scene.cursor?label(s.validation):'待载入');note(device,'设备选择调用项。不能覆盖上游 URL、key、固定 model 或策略。');
  registry.append(el('div','pvd-lock','固定 model'));row(registry,'来源',label(s.model_source));note(registry,'先按登记固定模型，再选命名 primary；无 primary 才选同服务商排序第一候选。');
  row(result,'接口',label(s.api_selection));row(result,'本地状态码',s.status?String(s.status):'尚未决定');
  if(s.status===200)note(result,'路由允许；这里没有声明云端请求成功。');
  if(s.status>=400)note(result,'请求在路由阶段拒绝，不调用上游。业务错误与非法帧关闭连接分别处理。');root.append(device,registry,result);return root;
}
function roundtripDiagram(scene) {
  const s=scene.state,root=el('div','pvd-split'),relay=panel('Relay · 请求与回执'),execution=panel('Execution · 独立生命周期');
  row(relay,'request_id',s.request_id||'未载入');row(relay,'回执状态码',s.response_status?String(s.response_status):'等待 / 未提交');
  relay.append(el('div','pvd-frame',s.response_status===200?'完整 response → body_json':s.response_status===502?'通用上游错误':'完整请求封套'));note(relay,'request_id 只回显对齐请求与响应，不提供 exactly-once。');
  row(execution,'提交',s.submitted?'已创建执行':'未提交');row(execution,'会话方式',label(s.session_mode));row(execution,'远端停止',label(s.remote_stopped),'uncertain');
  note(execution,'等待预算：timeout × (retries + 1) + 30 秒。等待超时不等于执行取消；再次请求可能新建执行。');root.append(relay,arrow(s.submitted?'submit / wait':'待构造'),execution);return root;
}
function protocolDiagram(scene) {
  const s=scene.state,root=el('div','pvd-protocol');const input=panel('接口 kind');input.append(badge(scene.cursor?label(s.kind):'尚未载入'));root.append(input);
  const choices=el('div','pvd-two');for(const [kind,name,shape] of [['openai_compatible_chat','ChatProtocol','messages → choices / message'],['typesafe_system_one','JevProtocol','state / questions → answers']]) {
    const node=panel(name,s.kind===kind&&scene.cursor>1?'good':'');node.append(el('div','pvd-document',shape));choices.append(node);
  }root.append(choices);row(root,'已选解码器',label(s.decoder));note(root,'kind 选择消息形状；具体供应商路线独立。原生 Agent 端点不经过这两个适配器。');return root;
}
function accountDiagram(scene) {
  const s=scene.state,root=el('div','pvd-account'),entry=panel('登记 ID'),capacity=panel('Execution · 容量 / 账务身份');
  row(entry,'已有原生账号',scene.cursor?(s.already_native_account?'是':'否'):'待载入');note(entry,'去重边界是登记 ID；不按相似名称合并。');
  row(capacity,'采用身份',label(s.account_source));row(capacity,'额外身份',s.extra_account_created?'本例创建普通 API profile':'未创建');row(capacity,'结果',label(s.status));
  root.append(entry,arrow('按 ID 对齐'),capacity);note(root,'普通 API profile 不提供原生额度窗口，也不能作为原生 session route。云端限额由供应商另行决定。');return root;
}
function endpointsDiagram(scene,simulation) {
  const s=scene.state,root=el('div','pvd-endpoints');
  const matrix=el('div','pvd-route-map');for(const item of simulation.mechanism.cases.filter(c=>c.id!=='invalid')) {
    const node=el('div',`pvd-route ${scene.cursor&&s.route===item.inputs.route?'selected':''}`);
    node.append(el('strong','',item.inputs.route),el('span','',simulation.mechanism.steps.find(step=>step.id===item.path.at(-1))?.title||item.title));matrix.append(node);
  }root.append(matrix);const result=panel('本次明确绑定');row(result,'服务',label(s.service));row(result,'凭据来源',label(s.credential_authority));row(result,'结果',label(s.status));root.append(result);
  note(root,'路线无效即停止。未确认现场可用性，不借另一供应商、免费路线或另一账户补齐。');return root;
}
function apiLifecycleDiagram(scene) {
  const s=scene.state,root=el('div','pvd-api-lifecycle'),budget=el('div','pvd-budget');
  budget.append(badge(`HTTP 尝试 ${s.attempt_count} / ${s.attempt_limit}`),badge(`剩余预算 ${s.attempts_remaining}`));root.append(budget);
  const split=el('div','pvd-split'),portal=panel('Portal 等待者',s.late_result_disposition==='dropped'?'stop':''),http=panel('HTTP 工作线程');
  row(portal,'等待',s.portal_waiting?'正在等待':s.request_sent?'等待已结束':'尚未开始');
  row(portal,'结果',s.result_accepted?'已接纳':'未接纳');
  note(portal,s.wait_scope==='whole_decide_including_retries'?'Jev 的一次等待包住完整 decide 与其重试。':'Chat 的每次 HTTP 尝试各自检查取消。');
  const payload=el('div','pvd-payload');payload.append(el('strong','',s.protocol_kind==='typesafe_system_one'?'Jev：state / questions / model':'Chat：messages / model'),el('pre','',Object.keys(s.request_payload).length?JSON.stringify(s.request_payload,null,2):'等待构造本地示例请求'));portal.append(payload);
  row(http,'请求',s.request_sent?'已发出':'尚未发出');row(http,'远端计算',label(s.remote_compute_state),'uncertain');row(http,'远端 interrupt',s.upstream_interrupt_sent?'已发送':'未发送');
  if(Object.keys(s.response_payload).length){const response=el('div',`pvd-payload ${s.late_result_disposition==='dropped'?'dropped':''}`);response.append(el('strong','',s.late_result_disposition==='dropped'?'迟到响应 → 丢弃':'收到完整响应'),el('pre','',JSON.stringify(s.response_payload,null,2)));http.append(response);}
  else note(http,s.late_result_disposition==='dropped'?'迟到异常已丢弃，不替换等待者结果。':'HTTP 等待或本地取消不能证明远端计算停止。');
  split.append(portal,arrow(s.portal_waiting?'等待':s.request_sent?'已离开':'待发送'),http);root.append(split);return root;
}
function relayLifecycleDiagram(scene) {
  const s=scene.state,root=el('div','pvd-relay-lifecycle'),line=el('div',`pvd-socket-line ${s.connection_open?'open':'closed'}`);
  line.append(badge(s.connection_open?'同一连接 · 打开':'连接已关闭',s.connection_open?'good':'stop'),badge(s.slot_held?'连接名额仍持有':'未持有名额'),badge(s.can_read_next_frame?'可以读下一帧':'暂不能读下一帧'));
  root.append(line);const frame=el('div','pvd-frame');
  frame.append(el('div','pvd-frame-head',`长度头\n${s.header_bytes_received} / 4 字节`),el('div','pvd-frame-body',`正文 ${s.body_bytes_received} / ${s.frame_length} 字节`));root.append(frame);
  note(root,`整帧共用 ${s.frame_deadline_seconds} 秒截止时间；本例帧上限 ${s.frame_limit_bytes} 字节，部署可配置。`);
  const flow=el('div','pvd-pipeline'),decode=panel('帧结构',s.error_class==='structure'?'stop':''),business=panel('业务校验',s.error_class==='business'?'stop':''),execution=panel('Execution');
  row(decode,'UTF-8 JSON 对象',s.frame_decoded?'已解码':'未解码');row(decode,'截止时间',s.deadline_expired?'已到期':'未到期');
  row(business,'固定模型',s.fixed_model);row(business,'采用模型',s.effective_model||'尚未转发');row(business,'request_id',s.request_id);
  row(execution,'submit 次数',String(s.execution_submit_count));row(execution,'状态观察',label(s.remote_execution_state));flow.append(decode,business,execution);root.append(flow);
  const receipt=el('div',`pvd-relay-receipt ${s.error_class?'stop':''}`);
  receipt.append(el('strong','',s.response_written?`响应 ${s.response_status} 已写回`:'本帧尚无已写回响应'),el('p','',s.can_read_next_frame?'保留连接与名额，继续读取下一帧。':s.connection_open?'当前帧仍在处理，或正在结束连接。':'连接已结束；不能继续读取下一帧。'));
  root.append(receipt);const headers=scene.trace.filter(step=>step.id==='header').length;
  if(Array.isArray(s.completed_frames)&&(s.completed_frames.length||['business400_then_valid','business403_then_valid'].includes(scene.case_id))){
    const ledger=panel('同一连接 · 逐帧写回记录');ledger.classList.add('pvd-frame-ledger');
    if(s.frame_index)row(ledger,'当前帧',String(s.frame_index));
    for(const record of s.completed_frames){const line=row(ledger,`第 ${record.frame_index} 帧 · 响应 ${record.status}`,record.execution_submitted?'已提交 Execution':'未提交 Execution');line.dataset.frameIndex=record.frame_index;}
    if(!s.completed_frames.length)note(ledger,'尚未记录已写回的帧。');
    note(ledger,'这是固定案例的本地观察；下一合法帧是新的输入，不是自动重投失败帧。');root.append(ledger);
  }
  if(headers>1)note(root,`已读取 ${headers} 帧；同一 request_id 只关联回显，不去重。本例 submit ${s.execution_submit_count} 次。`);
  return root;
}
function dispatchDiagram(scene) {
  const s=scene.state,root=el('div','pvd-dispatch'),input=panel('Execution 请求'),branches=el('div','pvd-dispatch-branches');
  row(input,'服务类型',s.service_type);row(input,'原生 Provider 输入',s.provider);root.append(input,arrow('按服务类型分派'));
  const native=panel('原生会话运行时',s.native_session?'good':s.destination==='unsupported_provider'?'stop':''),api=panel('ChatCompletionService',s.destination==='ChatCompletionService'?'good':'');
  const catalog=el('div','pvd-runtime-catalog');for(const name of ['kimi','claude','dsh','opencode'])catalog.append(badge(name,s.native_session&&s.provider===name?'good':''));native.append(catalog);
  note(native,'本图展示四个原生实现；Codex 由独立原生工作台解释。');
  note(native,s.destination==='unsupported_provider'?'未知原生 Provider：ValueError。没有默认回退。':s.native_session?`本次选择 ${s.provider} 的独立会话服务。`:'按原生 Provider 目录选择实现。');
  note(api,'按 API 条目解析接口协议，进行无状态生成。');api.append(badge(s.destination==='ChatCompletionService'?'本次进入无状态服务':'此路线不建立原生会话'));
  branches.append(native,api);root.append(branches);note(root,'原生 Provider 目录与 LLM API 登记表分别选择运行时和接口。相同账号 ID 不会把两种服务合并。');return root;
}
function credentialsDiagram(scene) {
  const s=scene.state,root=el('div','pvd-credentials'),tracks=el('div','pvd-two'),subscription=panel('Claude 订阅登录',s.route==='subscription'?'good':''),api=panel('DeepSeek 兼容路线',s.route==='deepseek'?'good':'');
  subscription.append(el('div','pvd-credential-token','账号独立配置目录'));note(subscription,'Claude CLI 负责登录与刷新。订阅条目不可混入 API key / endpoint。');
  api.append(el('div','pvd-credential-token','Registry key 文件引用'));note(api,'使用转换后的 Anthropic 兼容 URL 配置 SDK。');tracks.append(subscription,api);root.append(tracks);
  const rejected=s.configuration.startsWith('rejected'),boundary=el('div',`pvd-credential-check ${rejected?'stop':''}`);
  boundary.append(el('strong','',rejected?'配置停在会话入口之外':'本次账号配置核对'));
  if(s.route==='subscription'){
    row(boundary,'混入 API 字段',s.conflicting_api_fields?'有冲突':'没有');row(boundary,'会话目录与账号',s.directory_matches?'匹配':'不匹配');
  }else note(boundary,'DeepSeek 使用自己的登记凭据，不读取 Claude 订阅登录态。');
  const ready=s.configuration==='ready_for_sdk_session';
  boundary.append(badge(ready?'配置可交给 SDK':rejected?'拒绝启动本次会话':'尚未完成核对',ready?'good':rejected?'stop':''));root.append(boundary);
  note(root,s.credential_source==='native_cli_login'?'已选凭据来源：原生 CLI 登录。':s.credential_source==='registry_key_file'?'已选凭据来源：Registry 文件引用。':'尚未选定凭据来源。');return root;
}
function kimiDiagram(scene) {
  const s=scene.state,root=el('div','pvd-kimi'),preparation=panel('Portal 启动准备'),runtime=panel('Kimi web · 原生进程'),checks=el('div','pvd-kimi-checks');
  for(const [name,value] of [['回环监听',s.loopback],['可用文本 profile',s.profile_valid],['已安装二进制',s.runtime_exists]])checks.append(badge(`${name}：${value?'满足':'不满足'}`,value?'':'stop'));
  root.append(checks);preparation.append(el('div','pvd-document',passed(scene,'config')?'私有配置已生成\n模型模板 + Registry key 引用':'等待校验登记并准备私有配置'));
  note(preparation,'登记读取完成后，关闭数据库连接池。');
  const fence=el('div','pvd-env-fence');fence.append(el('strong','','进程交接边界'),badge(s.database_env_removed?'PORTAL_DATABASE_URL 已移除':'尚未交接',s.database_env_removed?'good':''));
  const executed=passed(scene,'exec');runtime.append(el('div','pvd-command',executed?'kimi web\n回环地址监听':'kimi web\n等待启动交接'));
  runtime.append(badge(executed?'启动命令已交出':'未交出启动命令',executed?'good':''));
  if(s.stage.startsWith('rejected')||s.stage==='missing_runtime')runtime.append(badge(({rejected_non_loopback:'非回环 host：拒绝',rejected_profile:'profile 不可用：拒绝',missing_runtime:'runtime 缺失：停止'})[s.stage],'stop'));
  const flow=el('div','pvd-kimi-flow');flow.append(preparation,fence,runtime);root.append(flow);note(root,'启动交接不等于现场在线。HTTP 控制与 WebSocket 流由 Kimi 服务承载。');return root;
}
function dshDiagram(scene) {
  const s=scene.state,root=el('div','pvd-dsh'),lock=el('div','pvd-lock');lock.append(el('strong','','同一 Session 的启动锁'),badge(s.launch_lock?'本例持有':passed(scene,'lock')?'已释放':'尚未取得',s.launch_lock?'good':''));root.append(lock);
  const tracks=el('div','pvd-two'),old=panel('已有 Node 进程',s.status==='reused'?'good':''),fresh=panel('新 Node 进程',s.status==='startup_timed_out'?'stop':s.status==='ready'?'good':'');
  old.append(el('div',`pvd-process ${s.status==='reused'?'active':''}`,'已有会话'));row(old,'锁内 health',s.already_healthy?'已有进程健康':'需要准备新进程');
  if(s.status==='reused')old.append(badge('直接复用 · 未启动第二个 Node','good'));
  fresh.append(el('div',`pvd-process ${s.process_started?'active':''}`,s.process_started?'本例启动过 Node':'本例尚未启动 Node'));
  row(fresh,'DeepSeek 显式绑定',s.bound_route_valid?'有效':'无效');
  const budget=el('div','pvd-health-budget');budget.append(el('strong','','25 秒健康观察预算'),el('span','',({waiting_health:'正在等待 health',ready:'已观察到 health',startup_timed_out:'超时 → terminate → 报错',runtime_missing:'runtime 缺失，无法启动',route_rejected:'绑定失效，不换账号',reused:'复用已有健康进程'})[s.status]||'尚未开始健康观察'));
  fresh.append(budget);tracks.append(old,fresh);root.append(tracks);note(root,'启动锁协调同一 Session 的进程启动；Execution 任务租约由另一机制管理。');return root;
}
function bridgeDiagram(scene) {
  const s=scene.state,root=el('div','pvd-bridge'),route=el('div','pvd-bridge-route');
  route.append(badge(s.route==='zen_free'?'Zen free · 无 API key':'OpenRouter · 显式 API 绑定'),badge(s.key_source==='bound_registry_file'?'已取得 key 文件引用':s.key_source==='none'?'不读 API key':'尚未解析路线'));
  root.append(route);const flow=el('div','pvd-bridge-flow'),bridge=panel('Bridge 进程'),server=panel('OpenCode serve',s.status==='server_start_failed'?'stop':s.status==='server_ready'?'good':'');
  bridge.append(el('div','pvd-credential-token',s.key_source==='bound_registry_file'?'key 保留在 Bridge 侧':s.key_source==='none'?'此路线没有 API key':'等待路线解析'));
  note(bridge,'有 key 时，经认证的本地 HTTP 配置 /auth。');
  const channels=el('div','pvd-bridge-channels');channels.append(el('div','',passed(scene,'auth')&&s.key_source==='bound_registry_file'?'PUT /auth →':'HTTP 控制 →'),el('div','',s.event_transport?'← /event SSE':'← 等待事件通道'));
  row(server,'监听地址','127.0.0.1');row(server,'原生子环境 key',passed(scene,'spawn')?(s.child_key_env?'包含':'已移除'):'尚未建立子环境');
  server.append(badge(({unstarted:'未启动',route_ready:'路线就绪',waiting_server:'等待回环端口',server_ready:'服务端口就绪',route_rejected:'路线拒绝',server_start_failed:'无端口：kill 并报错'})[s.status]||s.status,s.status==='server_start_failed'||s.status==='route_rejected'?'stop':''));
  flow.append(bridge,channels,server);root.append(flow);
  if(s.status==='route_rejected')root.append(el('div','pvd-credential-check stop','OpenRouter 缺显式绑定，停在启动前；不回退到 Zen。'));
  note(root,'HTTP 控制和 SSE 事件发生在本地回环服务；API key 不留在原生工具继承的环境变量中。');return root;
}
const diagrams={dispatch:dispatchDiagram,credentials:credentialsDiagram,kimi:kimiDiagram,dsh:dshDiagram,bridge:bridgeDiagram,apiLifecycle:apiLifecycleDiagram,relayLifecycle:relayLifecycleDiagram,chat:chatDiagram,typed:typedDiagram,complete:completeDiagram,capabilities:capabilitiesDiagram,gate:connectionDiagram,routing:routingDiagram,roundtrip:roundtripDiagram,protocol:protocolDiagram,account:accountDiagram,endpoints:endpointsDiagram};

export function createProviderMechanisms(root,{subject,content,ports={},signal,initialState}={}) {
  const objectId=subject?.objectId||subject?.id||content.mechanism.owner_id;
  const modes=createDomainModeSession(content,objectId,getProviderWorkbenchEntry(objectId),createProviderSimulation);
  const delegatedModes=getProviderDelegatedModes(content,objectId);
  let simulation=modes.simulation,shown=true,destroyed=false,focusId=null,sourcesOpen=false;
  const host=el('section','provider-mechanisms');host.setAttribute('aria-label','Provider 机制工作台');root.append(host);
  const listeners=new AbortController();let savedScroll=0;
  const emit=(name,value)=>{if(shown&&!destroyed&&!signal?.aborted)ports[name]?.(value);};
  const button=(text,action)=>{const b=el('button','',text);b.type='button';b.dataset.providerAction=action;return b;};
  function render(restoreFocus) {
    if(destroyed)return;
    const scene=simulation.getScene(),m=simulation.mechanism,scroll=host.scrollTop;
    const header=el('header','pvd-header');header.append(button('返回','return'),el('h2','',m.title),badge('本地模拟'));
    const modeBar=el('div','pvd-controls');
    if(modes.options.length+delegatedModes.length>1){
      const mode=el('select','');mode.dataset.providerAction='mode';mode.id='pvd-mode';
      for(const item of [...modes.options,...delegatedModes]){const option=el('option','',item.title);option.value=item.id;mode.append(option);}mode.value=m.id;
      const label=el('label','','阅读主题');label.htmlFor=mode.id;modeBar.append(label,mode);
    }
    const controls=el('div','pvd-controls'),select=el('select','');select.dataset.providerAction='case';select.id=`pvd-case-${m.id.replaceAll('.','-')}`;
    for(const item of m.cases){const option=el('option','',scenarioTitle(item));option.value=item.id;select.append(option);}select.value=scene.case_id;
    const selectLabel=el('label','','演示情境');selectLabel.htmlFor=select.id;controls.append(selectLabel,select);
    const pendingInput=scene.cursor===0&&!!scene.next;
    const pending=el('aside','pvd-pending-input pvd-receipt');
    pending.append(el('strong','','所选情境尚未载入'),el('p','','下方仍显示机制初始状态。点击“载入所选情境”后，才会应用本情境的演示输入。'));
    const diagram=el('div','pvd-diagram');diagram.dataset.providerView=simulation.descriptor.view;diagram.append(diagrams[simulation.descriptor.view](scene,simulation));
    const actions=el('div','pvd-actions'),next=button(pendingInput?'载入所选情境':scene.next?.title||'本情境完成','next');next.disabled=!scene.next;
    const back=button('上一步','back');back.disabled=!scene.cursor;actions.append(next,back,button('重新演示','reset'));
    const receipt=el('div','pvd-receipt');receipt.setAttribute('role','status');receipt.append(el('strong','',scene.current?.title||'选择情境，开始本地演示'));
    if(typeof scene.outcome==='string')receipt.append(el('p','',scene.outcome));
    const details=el('details','pvd-sources');details.open=sourcesOpen;details.append(el('summary','','规则与固定依据'));
    for(const invariant of m.invariants||[])note(details,invariant);
    for(const id of scene.evidence_ids){const evidence=simulation.evidence.get(id);const b=button(`${evidence.title||evidence.address} · L${evidence.start_line}–${evidence.end_line}`,'source');b.dataset.evidence=id;details.append(b);}
    const nav=el('footer','pvd-nav');nav.append(button('查看所属组成图','owner'));
    if(scene.current)for(const objectId of [...new Set([scene.current.from,scene.current.to])]){const object=simulation.objects.get(objectId);const b=button(object.title||object.name||objectId,'select');b.dataset.objectId=objectId;nav.append(b);}
    host.replaceChildren(header,...(modeBar.childElementCount?[modeBar]:[]),controls,...(pendingInput?[pending]:[]),diagram,actions,receipt,details,nav);host.scrollTop=scroll;
    if(shown&&restoreFocus){const target=[...host.querySelectorAll('[data-provider-action]')].find(b=>b.dataset.providerAction===restoreFocus&&!b.disabled);target?.focus({preventScroll:true});}
  }
  host.addEventListener('click',event=>{
    const b=event.target.closest?.('[data-provider-action]');if(!b||!host.contains(b)||!shown||destroyed||b.disabled)return;
    const action=b.dataset.providerAction;focusId=action;
    if(action==='return')return emit('return');
    if(action==='source')return emit('source',b.dataset.evidence);
    if(action==='select')return emit('select',{objectId:b.dataset.objectId});
    if(action==='owner')return emit('navigate',{kind:'composition',objectId:simulation.mechanism.owner_id,mechanismId:null});
    if(!['next','back','reset'].includes(action))return;
    if(action==='next')simulation.perform(simulation.getScene().next?.id);if(action==='back')simulation.back();if(action==='reset')simulation.reset();render(action);emit('change',simulation.getScene());
  },{signal:listeners.signal});
  host.addEventListener('change',event=>{
    if(!shown||destroyed)return;
    if(event.target.dataset.providerAction==='mode'){
      const delegated=delegatedModes.find(mode=>mode.id===event.target.value);
      if(delegated){event.target.value=simulation.mechanism.id;focusId='mode';emit('navigate',delegated.route);return;}
      const saved=modes.select(event.target.value,getLocalViewState());if(!saved)return;
      simulation=modes.simulation;savedScroll=saved.scrollTop||0;sourcesOpen=saved.sourcesOpen===true;focusId='mode';
      render('mode');host.scrollTop=savedScroll;emit('change',simulation.getScene());return;
    }
    if(event.target.dataset.providerAction!=='case')return;
    simulation.chooseCase(event.target.value);focusId='case';render('case');emit('change',simulation.getScene());
  },{signal:listeners.signal});
  host.addEventListener('toggle',event=>{if(event.target.matches?.('.pvd-sources')&&host.contains(event.target))sourcesOpen=event.target.open;},{capture:true,signal:listeners.signal});
  function restoreViewState(saved) {
    if(destroyed)return false;
    if(saved?.modeState){if(!saved.simulation)return false;const restored=modes.restore(saved.modeState,saved.simulation);if(!restored)return false;simulation=modes.simulation;saved=restored;}
    else if(!simulation.restoreViewState(saved?.simulation))return false;
    savedScroll=Number.isFinite(saved.scrollTop)?Math.max(0,saved.scrollTop):0;
    focusId=typeof saved.focusId==='string'?saved.focusId:null;sourcesOpen=saved.sourcesOpen===true;render();host.scrollTop=savedScroll;
    if(shown&&focusId)[...host.querySelectorAll('[data-provider-action]')].find(b=>b.dataset.providerAction===focusId&&!b.disabled)?.focus({preventScroll:true});return true;
  }
  function getLocalViewState(){return {simulation:simulation.getViewState(),scrollTop:shown?host.scrollTop:savedScroll,focusId,sourcesOpen:host.querySelector('.pvd-sources')?.open===true};}
  function destroy(){if(destroyed)return;destroyed=true;shown=false;listeners.abort();host.remove();simulation=null;signal?.removeEventListener('abort',destroy);}
  render();if(initialState)restoreViewState(initialState);
  signal?.addEventListener('abort',destroy,{once:true});if(signal?.aborted)destroy();
  return {show(){if(!destroyed){shown=true;host.hidden=false;host.scrollTop=savedScroll;}},hide(){if(!destroyed){savedScroll=host.scrollTop;shown=false;host.hidden=true;}},destroy,
    getViewState:()=>{if(destroyed)return null;const saved=getLocalViewState();return {...saved,modeState:modes.snapshot(saved)};},restoreViewState,
    getScene:()=>destroyed?null:simulation.getScene()};
}
