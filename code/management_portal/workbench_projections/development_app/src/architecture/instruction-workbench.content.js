// Authored interpretation, separate from the authoritative ISA declaration.
// Values in experiments are fictional; this module has no I/O capability.
const variant = (id, label, before, after, result, explanation) => ({id, label, before, after, result, explanation});
// Requests are shown as data; opening a lesson never invokes an instruction.
// Replace fictional IDs with read-back identities before using an example.
const request = (instruction, args, id, label, before, after, result, explanation) => ({
  ...variant(id, label, [JSON.stringify({instruction, arguments: args}), ...before], after, result, explanation),
  request: {instruction, arguments: args},
});
const lesson = (question, meaning, visual, parameters, variants, source) => ({question, meaning, visual, parameters, variants, source});
export const lessons = {
  ReadResource: lesson('读到的究竟是正文，还是一个下载入口？', '统一资源地址定位资源或内部文件。ResourceService 用真实调用者读取，并在回执中标明内容类型、大小与版本。', 'receipt',
    {address:'读取哪一个资源或内部文件。', max_bytes:'直接返回正文的字节上限；默认 4 MiB。'}, [
      variant('text', '小文本', ['address → 示例说明文件', 'max_bytes = 4 MiB'], ['text：欢迎阅读', 'content_type：text/plain', 'size：正文实际字节数', 'version：正文 SHA256'], {text:'欢迎阅读',content_type:'text/plain',size:12,version:'<正文 SHA256>'}, '内部文件按原始字节给出摘要；同样的标题不代表同一个正文版本。'),
      variant('large', '超过上限', ['address → 示例大文件', '正文超出 max_bytes'], ['download_url：短期下载入口', 'expires_at：到期时间', 'size：完整大小'], {download_url:'<仅本次调用者有效的临时地址>',expires_at:'<到期时间>',size:8388608}, '不把缺少 text 当空正文。下载地址只对本次调用者有效，支持 Range；这里不发起下载。'),
    ], 'management_portal/resources/isa.py#L95'),
  UpdateUser: lesson('改显示名称，会改变账号分类吗？', 'user_id 选择既有账号；资料修改仅接受显示名称、恢复邮箱和头像。分类与启停使用各自的指令。', 'compare',
    {user_id:'稳定的用户名。', changes:'display_name、recovery_email 或 avatar。'}, [
      request('UpdateUser', {user_id:'reader_demo',changes:{display_name:'新显示名'}}, 'rename', '修改资料', ['原显示名：读者', '分类：member'], ['显示名：新显示名', '账号身份与分类保持'], {user:{username:'reader_demo',display_name:'新显示名',category:'member'}}, '显示名称可变化，user_id 仍定位同一个账号。'),
      request('UpdateUser', {user_id:'reader_demo',changes:{category:'portal_admin'}}, 'invalid', '把分类混入资料', ['调用资料接口并夹带 category'], ['原资料和分类保持'], {error:'instruction.invalid_arguments'}, '分类不属于资料字段；应使用 UpdateUserCategory，并接受该操作的管理员资格检查。'),
    ], 'management_portal/hatg/catalog/user.py'),
  UpdateUserCategory: lesson('修改用户分类，如何保护管理员身份？', '分类由专用指令管理。管理员资格、本人保护和最后一位启用管理员保护都在账号领域检查。', 'compare',
    {user_id:'要调整的用户名。', category:'portal_admin、member 或 intern。'}, [
      request('UpdateUserCategory', {user_id:'reader_demo',category:'member'}, 'category', '管理员调整分类', ['调用者：有资格的其他管理员', 'reader_demo：intern'], ['reader_demo：member'], {user:{username:'reader_demo',category:'member'},category_changed:true}, '分类变更与管理员名单在同一个账号事务中完成。'),
      request('UpdateUserCategory', {user_id:'reader_demo',category:'member'}, 'same', '分类已经相同', ['reader_demo：member'], ['分类保持'], {user:{username:'reader_demo',category:'member'},category_changed:false}, '同值请求正常返回；false 仅说明分类未变化。'),
      request('UpdateUserCategory', {user_id:'reader_demo',category:'portal_admin'}, 'forbidden', '一般用户提升分类', ['调用者：一般用户'], ['分类保持'], {error:'instruction.forbidden'}, '管理员资格来自真实调用者，不能在参数里声明；关闭权限方案不取消这项领域资格检查。'),
      request('UpdateUserCategory', {user_id:'admin_demo',category:'member'}, 'last_admin', '降级最后一位启用管理员', ['admin_demo：唯一启用的管理员'], ['保留管理员身份'], {error:'instruction.invalid_arguments'}, '本人管理员身份与最后一位启用管理员均受保护。'),
    ], 'management_portal/hatg/catalog/user.py'),
  UpdateUserStatus: lesson('停用账号和修改资料有什么区别？', '启停是管理员操作；停用时撤销账号会话，同时保留管理员安全约束。', 'compare',
    {user_id:'要启停的用户名。', status:'active 启用，disabled 停用。'}, [
      request('UpdateUserStatus', {user_id:'reader_demo',status:'disabled'}, 'disable', '停用普通账号', ['调用者：Portal 管理员', 'reader_demo：active'], ['reader_demo：disabled', '账号会话被撤销'], {user:{username:'reader_demo',status:'disabled'}}, '停用保留账号身份；后续启用使用同一指令的 active 值。'),
    ], 'management_portal/hatg/catalog/user.py'),
  ForkResident: lesson('Fork 产生新成员，哪些上下文跟过去？', '从具体成员派生新 Resident。来源身份和新身份不同，继承方式决定新会话得到的上下文范围。', 'graph',
    {lineage_id:'新成员所在 Lineage。', source_resident_id:'被派生的具体成员；Family 门面不能 Fork。', fork_mode:'full / compacted / recent / message 选择上下文来源。', recent_rounds:'recent 时必填的正整数轮数；其他方式不填写。', message:'message 模式必须提供的历史截止点 {message_id}。'}, [
      variant('full', '完整上下文', ['来源成员 R₁', '已存在的模型上下文'], ['来源 R₁ → 派生 R₂', 'R₂ 获得所选上下文', '继承关系有独立 ID'], {resident_id:'resident_demo_child',inheritance_edge_id:'demo_edge'}, '派生关系记录来源，新成员仍是独立 Resident。此图不表示共用一个可写会话。'),
      request('ForkResident', {lineage_id:'lineage_demo',source_resident_id:'resident_parent',fork_mode:'recent',recent_rounds:5}, 'recent', '最近五轮', ['来源成员已有历史轮次'], ['按最近五轮派生独立成员'], {resident_id:'resident_demo_child',inheritance_edge_id:'demo_edge'}, 'recent_rounds 明确表达轮数；派生后仍是独立身份和会话。'),
      request('ForkResident', {lineage_id:'lineage_demo',source_resident_id:'resident_parent',fork_mode:'recent'}, 'recent_missing', '遗漏轮数', ['fork_mode = recent'], ['没有新成员'], {error:'instruction.invalid_arguments'}, '必须给出 recent_rounds，不能隐式猜测五轮或十轮。'),
      variant('message', '缺少历史截止点', ['fork_mode = message', '未提供 message: {message_id}'], ['没有新成员'], {error:'instruction.invalid_arguments'}, '按历史消息截取上下文必须指定截止消息；缺少截止点，在派生前即拒绝。'),
      variant('facade', '选了 Family 门面', ['source = Family 门面'], ['不创建派生成员'], {error:'instruction.conflict'}, '门面代表整个 Family；需要 Fork 其中的具体成员。'),
    ], 'management_portal/society_service/isa/residents.py#L109'),
  DeliverMessage: lesson('投递成功，等于对方已经回答了吗？', '此指令向 Resident 发起新请求或附加下轮背景。排队、执行和业务完成是后续事实。', 'queue',
    {resident_id:'收件 Resident。', message:'消息正文与 content_parts；role 只能是 user。', activation_policy:'enqueue_and_trigger 发起工作；attach_to_next 作为下轮背景。'}, [
      request('DeliverMessage', {resident_id:'resident_demo',message:{content_parts:[{type:'text',text:'检查示例'}]},activation_policy:'enqueue_and_trigger'}, 'busy', 'Resident 正忙', ['另一轮正在执行'], ['另一轮继续', '新消息进入等待队列'], {resident_id:'resident_demo',message_id:'msg_demo',accepted:true}, 'accepted 只说明受理。用 GetInputState 观察同一消息，不能假定已排队回执就是已完成。'),
      request('DeliverMessage', {resident_id:'resident_demo',message:{content_parts:[{type:'text',text:'补充示例背景'}]},activation_policy:'attach_to_next'}, 'info', '提供下轮背景', ['收件人当前没有新任务'], ['保存背景', '不单独启动执行'], {resident_id:'resident_demo',message_id:'msg_info'}, '当前轮插入由 InjectMailboxItem 表达；转交原消息和回复归属使用 RelayMessage；临时授权使用 CreateTemporaryCredential。不同操作不再混装为投递模式。'),
    ], 'management_portal/hatg/catalog/conversation.py'),
  DeliverUserMessage: lesson('发给用户，会启动一个 Resident 吗？', '给用户的消息写在发送方对话中，并在用户 Mailbox 形成未读；没有 Agent 输入队列。', 'queue',
    {user_id:'收件用户名。', message:'文字与资源引用；不带对话 quote。'}, [
      request('DeliverUserMessage', {user_id:'reader_demo',message:{content_parts:[{type:'text',text:'示例结果已备好'}]}}, 'human', '给用户结果', ['调用者：Resident', '收件人：reader_demo'], ['发送方对话留下消息', '用户 Mailbox 未读'], {message_id:'msg_user_demo'}, 'Mailbox 中的未读与 Resident 的输入状态不同。此演示不会实际发信。'),
    ], 'management_portal/hatg/catalog/conversation.py'),
  WatchConversationEvents: lesson('断开后怎样只看新的对话事件？', '订阅从 after_cursor 之后继续。cursor 保留最后交付事件的位置，high_watermark 表示服务端水位；续读不能把两者混用。', 'stream',
    {resident_id:'订阅哪个 Resident 的事件。', after_cursor:'从已见游标之后继续，而不是重取整个会话。', input_message_id:'可限定某条输入的事件正文。', limit:'每批事件上限，默认 500。'}, [
      variant('resume', '从游标 12 恢复', ['已处理 11、12', 'after_cursor = 12'], ['事件 13', '事件 14', '批末检查点 14'], {cursor:14,high_watermark:14}, '按顺序处理事件，保存游标再继续；13、14 是示例编号。'),
      variant('empty', '本批没有正文', ['最后交付游标 14', '本批没有匹配事件', '服务端水位已到 16'], ['续读 cursor：仍为 14', 'high_watermark：16'], {cursor:14,high_watermark:16}, '空批不推进交付游标。下一次仍用 cursor=14，不能用水位 16 跳过尚未交付的事件。'),
      variant('limited', '每批只取一条', ['after_cursor = 12，limit = 1', '服务端已有事件 13、14'], ['本批交付事件 13', '续读 cursor：13', 'high_watermark：14'], {cursor:13,high_watermark:14}, '按返回的 cursor 续读才能收到 14；水位只说明服务器已到哪里。'),
    ], 'management_portal/society_service/isa/conversation.py#L241'),
  SetIntakeOpen: lesson('关闭接单，会终止当前工作吗？', '接单开关控制新工作的启动。当前工作继续，后来消息进入队列；取消某条输入使用 CancelInput。', 'queue',
    {resident_ids:'明确列出的 Resident 身份数组，单个对象也用数组。', open:'false 关闭，true 打开。'}, [
      request('SetIntakeOpen', {resident_ids:['resident_demo'],open:false}, 'working', '正在工作时关闭', ['任务 A 正在执行', '接单开放'], ['A 继续', '新消息 B 排队'], {changed_resident_ids:['resident_demo'],unchanged_resident_ids:[],failed:[],changed_count:1,unchanged_count:0,failed_count:0}, '开关结果按每个 Resident 返回；不能把批量受理等同全部成功。'),
      request('SetIntakeOpen', {resident_ids:['resident_demo'],open:false}, 'already', '已经关闭', ['接单已关闭'], ['接单保持关闭'], {changed_resident_ids:[],unchanged_resident_ids:['resident_demo'],failed:[],changed_count:0,unchanged_count:1,failed_count:0}, '未改变的是接单状态，不据此推断领域内部完全没有写入。'),
    ], 'management_portal/hatg/catalog/resident_execution.py'),
  CancelInput: lesson('取消一条输入，什么时候才算停止？', '按 Resident 和消息 ID 定位同一条输入。等待项撤回，运行中的任务请求停止，已经结束的消息保留原结果。', 'queue',
    {resident_id:'处理消息的 Resident；门面请求会沿已记录的转交关系找到实际成员。', message_id:'要停止的输入消息；不会取消这个 Resident 的其他输入。'}, [
      variant('queued', '撤回等待项', ['msg_demo：queued', '当前另一轮继续运行'], ['msg_demo 离开 Mailbox 等待项', 'msg_demo：cancelled', '另一轮不受影响'], {resident_id:'resident_demo',message_id:'msg_demo',input_state:{state:'cancelled'}}, '请求、群体或 info 都按原输入身份撤回；若撤回期间开始执行，会转为对同一输入请求停止。'),
      variant('running', '请求停止当前轮', ['msg_demo：running'], ['停止请求已受理', '等待执行后端完成取消'], {resident_id:'resident_demo',message_id:'msg_demo',cancellation_requested:true,backend_cancelled:false,input_state:{state:'running'}}, '受理停止不等于后端已终止。继续用 GetInputState 或对话事件观察终态。'),
      variant('completed', '已经完成', ['msg_demo：completed', '原回复 reply_demo 已存在'], ['保留 completed 与原回复', '不重新取消或执行'], {resident_id:'resident_demo',message_id:'msg_demo',already_terminal:true,input_state:{state:'completed'}}, '已终态正常返回，不把 completed 改写成 cancelled；真实回复仍按原消息读取。'),
      variant('failed', '已经失败', ['msg_demo：failed'], ['保留 failed 与原失败结果'], {resident_id:'resident_demo',message_id:'msg_demo',already_terminal:true,input_state:{state:'failed'}}, '取消不掩盖已经发生的失败，也不重试这条输入。'),
      variant('cancelled', '已经取消', ['msg_demo：cancelled'], ['仍为 cancelled'], {resident_id:'resident_demo',message_id:'msg_demo',already_terminal:true,input_state:{state:'cancelled'}}, '重复取消已终态输入正常返回，不创建新消息。'),
    ], 'management_portal/society_service/isa/execution.py#L161'),
  GetMailbox: lesson('同一条读取指令，为什么有两种回执？', 'resident_id 与 user_id 二选一。Resident Mailbox 由 SocietyService 管理等待工作，User Mailbox 由 Portal Web 管理对话和通知；读取不改变未读状态。', 'routing',
    {resident_id:'读取 Resident 的 pending、active、recent 与整理能力；按 argument_owners 路由到 SocietyService。', user_id:'读取 User 的 items 与 unread_count；由 Portal Web 按真实调用者检查读取资格。', related_resident_id:'只可与 user_id 配合，筛选与某个 Resident 相关的条目。'}, [
      variant('resident', '读取 Resident Mailbox', ['resident_id = resident_demo', '一条工作正在执行，一条请求在等待'], ['SocietyService', 'pending：等待请求', 'active：当前轮', 'recent：最近结束的轮次'], {pending:[{kind:'request',message_id:'msg_wait',preview:'示例等待请求'}],active:{message_id:'msg_active'},recent:[],can_organize:true,revision:'<等待顺序标识>'}, 'active 不属于 pending。revision 标识等待顺序，不是用户阅读状态；can_organize 取决于实际调用者。'),
      variant('user', '读取自己的 User Mailbox', ['调用者 = reader_demo', 'user_id = reader_demo'], ['Portal Web', 'items：对话与通知', '读取后仍保留未读'], {items:[{key:'resident:resident_demo',unread:true,version:'8'}],unread_count:1}, 'User 的 items 不等同于 Resident 输入队列。条目的 version 用于阅读状态并发检查，不能当成等待顺序 revision。'),
      variant('related', '按相关 Resident 筛选', ['user_id = reader_demo', 'related_resident_id = resident_demo'], ['Portal Web 读取 User Mailbox 的相关条目'], {items:[],unread_count:0}, '这里示例筛选后没有条目；related_resident_id 不会把 owner 改成 SocietyService，也不能冒用该 Resident 身份。'),
      variant('both', '同时提供两种主体', ['resident_id = resident_demo', 'user_id = reader_demo'], ['互斥校验拒绝', '不合并两种 Mailbox'], {error:'instruction.invalid_arguments'}, '必须且只能选择一个主体，不能靠参数顺序决定路由。'),
      variant('resident_filter', '给 Resident 表单加 User 筛选', ['resident_id = resident_demo', 'related_resident_id = resident_other'], ['拒绝不适用的参数组合'], {error:'instruction.invalid_arguments'}, 'related_resident_id 只用于 User Mailbox。选择 Resident 分支后不能混用 User 分支的过滤含义。'),
    ], 'management_portal/hatg/catalog/mailbox.py#L25'),
  ApplyLineageChanges: lesson('同样一条箭头，激活、观察和绑定分别是什么？', 'operations 描述一项或多项定义修改。每项分别核验权限，全部成功才提交一个新版本；连线内部的 kind 决定箭头语义。', 'graph',
    {lineage_id:'定义所属 Lineage。', operations:'create / update / delete / clone；kind 选择 component、wire 或 resident。create 的 value 保存连线结构，Resident 仅支持 delete。', function_block_id:'编辑已有职责时指定；调用信封须携带读回的 expected_version。', duty_changes:'职责元数据；memory_level 使用 trigger、turn、session 或 on_demand。'}, [
      request('ApplyLineageChanges', {lineage_id:'lineage_demo',operations:[{op:'create',kind:'wire',value:{kind:'route',source:'event_source_demo',source_port:'output',target:'effect_demo',target_port:'invoke'}}]}, 'route', '创建激活连线', ['已读取合法输出与 Invoke 端口'], ['事件源 ──激活──→ Effect', '定义版本推进一次'], {created_ids:['wire_demo'],updated_ids:[],deleted_ids:[],revision:8}, '创建边不会启动工作。只有事件发生时才沿 route 传递；正式调用用当前图中的真实端点。'),
      request('ApplyLineageChanges', {lineage_id:'lineage_demo',operations:[{op:'create',kind:'wire',value:{kind:'watch',source:'event_source_demo',target:'resident_demo'}}]}, 'watch', '创建观察绑定', ['观察事件源与目标存在'], ['事件源 ──观察──→ Resident'], {created_ids:['wire_demo'],updated_ids:[],deleted_ids:[],revision:8}, 'watch 指向被观察对象；观察事件源须支持该目标种类。'),
      request('ApplyLineageChanges', {lineage_id:'lineage_demo',operations:[{op:'create',kind:'wire',value:{kind:'target',source:'effect_demo',target:'resident_demo'}}]}, 'target', '创建目标绑定', ['Effect 引用目标存在'], ['Effect ──目标──→ Resident'], {created_ids:['wire_demo'],updated_ids:[],deleted_ids:[],revision:8}, 'target 表达 Effect 操作哪个对象，不承担 route 的激活传递。'),
      request('ApplyLineageChanges', {lineage_id:'lineage_demo',operations:[{op:'update',kind:'component',id:'effect_demo',changes:{title:'新标题'}},{op:'delete',kind:'wire',id:'wire_demo'}]}, 'atomic', '第二项没有权限', ['第一项具备更新权限', '第二项缺少删除连线权限', '原定义版本：7'], ['标题未改', '连线未删', '版本仍为 7'], {error:'instruction.forbidden'}, '整个事务拒绝，不能留下第一项成功的半成品。通用变更入口不提供越过单项权限的捷径。'),
    ], 'management_portal/society_service/isa/definition.py'),
  UpdateSharedState: lesson('两个调用者都想改同一个状态，谁会写入？', '附带 expected_value 时比较的是状态值，不是 Lineage 定义版本。只有读到的期望值仍等于当前值，才写 value。', 'compare',
    {lineage_id:'状态单元所属 Lineage。', key:'状态单元名。', expected_value:'调用者认为仍成立的旧值；省略时直接写入。', value:'匹配时写入的新值。'}, [
      variant('match', '值仍相等', ['当前值：3', '期望值：3', '拟写入：4'], ['当前值：4'], {matched:true,changed:true,value:4}, '比较和写入在同一个状态事务中完成。'),
      variant('changed', '被另一调用者改过', ['当前值：5', '期望值：3', '拟写入：4'], ['当前值：5'], {matched:false,changed:false,value:5}, '这是一次成功返回的不匹配结果，不是请求异常；旧值不会覆盖 5。'),
      variant('same', '新值与旧值相同', ['当前值：3', '期望值：3', '拟写入：3'], ['当前值：3'], {matched:true,changed:false,value:3}, '匹配成功也可以没有变化。matched 与 changed 必须分开理解。'),
    ], 'management_portal/society_service/lineage_graph_state.py#L129'),
  TriggerEvent: lesson('从一个事件源触发，还是只走它的一条连线？', '选择已存在的 manual.triggered 事件源或它的一条 route 连线，并提交本次输入。事件类型来自源定义，不由调用者填写 event_type。', 'stages',
    {lineage_id:'事件源所属 Lineage。', event_source_id:'手动事件源，与 wire_id 二选一；按源的连线分发。', wire_id:'手动事件源发出的 route 连线；只选这条连线。', prompt:'本次运行的文本输入。', content_parts:'图片、文件或上下文引用，与组合后的消息共享投递预算。', event_name:'Event 的用途名称，不改变 manual.triggered 类型。', subject_resident_id:'本次 Event 涉及的 Resident。', payload:'结构化输入，写入 Event payload.data。'}, [
      variant('source', '从手动事件源触发', ['event_source_id = manual_demo', 'manual_demo 类型：manual.triggered', 'prompt = 检查示例', 'payload = {topic: "demo"}'], ['创建 manual.triggered Event', '结构化输入进入 payload.data', '按 manual_demo 的连线分发'], {event_id:'event_demo',trigger_path_id:'path_demo',delivery:{matched_count:2,consumed_count:2}}, '示例假定两条下游投递均被消费；这不是所有下游业务工作已经完成的证明。'),
      variant('wire', '只触发一条 route', ['wire_id = route_demo', 'route_demo 来自 manual_demo', '同源另有 route_other'], ['仅选择 route_demo', 'route_other 不在本次选择内'], {event_id:'event_wire_demo',trigger_path_id:'path_wire_demo',delivery:{matched_count:1,consumed_count:1}}, '连线必须真实存在、kind 为 route，且源是 manual.triggered；不能随便填写一条观察或目标绑定。'),
      variant('named', '名称不会变成系统事件', ['event_source_id = manual_demo', 'event_name = portal.release_published'], ['Event 类型仍为 manual.triggered', 'payload.name 保存这个用途名称'], {event_id:'event_named_demo',trigger_path_id:'path_named_demo',delivery:{}}, '名称只是输入数据。它不证明发布成功，也不会伪造运行时的发布事件。'),
      variant('system', '选择系统事件源', ['event_source_id = release_demo', 'release_demo 类型：portal.release_published'], ['创建手动 Event 前拒绝'], {error:'instruction.invalid_arguments'}, '只有 manual.triggered 事件源可手动触发；发布等系统事实由运行时产生。'),
      variant('both', '同时选择源和连线', ['event_source_id = manual_demo', 'wire_id = route_demo'], ['ISA 互斥校验拒绝'], {error:'instruction.invalid_arguments'}, '公开指令要求二选一，即使这条线确实属于该源，也只提交一种入口。'),
    ], 'management_portal/society_service/isa/runs.py#L44'),
  RunScript: lesson('脚本启动回执，为什么不等于输出结果？', 'SocietyService 登记异步脚本运行，再由运行服务执行。脚本有独立 ID、状态与输出读取入口。', 'stages',
    {path:'真实主机脚本路径；本演示不会读取或运行。', lineage_id:'Effect 由激活确定，Resident 默认所在 Lineage；User 直接调用必须填写。', runtime:'python、javascript 或 bash。', exclusive_key:'同一 Lineage 下防止同键脚本并行启动。'}, [
      variant('start', '启动脚本', ['示例脚本 path', 'runtime：python'], ['登记 script_run_id', '状态 running', '稍后再读取输出与状态'], {script_run_id:'script_demo',status:'running'}, '返回 running 仅说明异步运行已启动；此处不伪造一个已完成的结果。'),
      variant('exclusive', '同键脚本仍在运行', ['exclusive_key：demo', '已存在同键运行 script_demo'], ['返回已有运行', '不再启动第二份'], {script_run_id:'script_demo',status:'already_running'}, '重复启动被合并到已存在的运行；应继续观察已有 script_run_id。'),
    ], 'management_portal/society_service/script_runs/service.py#L103'),
  Functionalize: lesson('把选区封装起来，会复制里面的对象吗？', 'Function Block 将当前选区作为一个可折叠模块管理，元素保持原有行为与身份。', 'graph',
    {lineage_id:'选区所属 Lineage。', element_ids:'被封装的元素身份集合。'}, [
      variant('group', '封装选区', ['节点 A', '节点 B', '连线 A → B'], ['Function Block〔A → B〕', '外部继续引用原对象'], {function_block_id:'fbi_demo'}, '这里改变组织方式，不把 A、B 克隆成新的执行节点。'),
    ], 'management_portal/society_service/isa/function_blocks.py#L5'),
  Familize: lesson('Family 除了成员分组，还增加了什么？', 'Family 把成员和专属工作流组织起来，同时建立门面、Router 与 steward 的协作职责。', 'graph',
    {lineage_id:'成员必须在同一 Lineage。', resident_ids:'选中的成员或已有 Family。', scope:'选择范围，由指令定义决定。'}, [
      variant('team', '把成员组成团队', ['成员 A', '成员 B'], ['门面：接待外部', 'Router：路由请求', 'steward：维护团队', '成员 A、B：完成工作'], {family_id:'fbi_demo',resource_id:'family_demo'}, 'Family 的正式资源 ID 与 Function Block 键是两种身份；回执分别给出，不应混用。'),
    ], 'management_portal/society_service/isa/families.py#L7'),
  GetTemplateApplyPreview: lesson('预览模板，会创建对象或投递工作吗？', '只读预览根据模板和目标计算应用计划，不创建对象、不发事件、不投递消息。', 'compare',
    {lineage_id:'拟应用到的 Lineage。', template_id:'已登记的模板。', target:'resident、selection 或 canvas 目标，字段由目标种类决定。', parameters:'模板参数；仍须通过模板约束。'}, [
      request('GetTemplateApplyPreview', {lineage_id:'lineage_demo',template_id:'template_demo',target:{kind:'canvas',x:0,y:0}}, 'preview', '只读预览结构', ['现有元素 A', '模板计划新建 B'], ['原结构仍只有 A', '返回预览计划', '没有真实新对象身份'], {summary:'预览施加结果。',created:{resident_ids:[],component_ids:[],wire_ids:[]},preview:[{target_resident_id:'',found:[],removed:[],updated:[],ambiguous:[],created:{roles:0,components:1,wires:0},error:''}]}, 'preview 中的 created 是预计数量，顶层 created 是真实新身份集合，此时仍为空。预览不会锁住定义；提交前仍需读取当前版本。'),
    ], 'management_portal/hatg/catalog/template.py'),
  ApplyTemplate: lesson('看过计划之后，怎样真正应用模板？', 'ApplyTemplate 执行模板的应用语义。先用 GetTemplateApplyPreview 阅读计划，再提交应用，服务重新校验当前目标、权限和版本。', 'compare',
    {lineage_id:'应用到的 Lineage。', template_id:'要施加的模板。', target:'与拟应用范围一致的真实目标。', parameters:'实际应用使用的模板参数。'}, [
      request('ApplyTemplate', {lineage_id:'lineage_demo',template_id:'template_demo',target:{kind:'canvas',x:0,y:0}}, 'apply', '实际应用结构', ['当前结构 A', '读回当前版本并放入调用信封 expected_version'], ['结构 A、B', 'created 中出现真实新身份'], {created:{resident_ids:[],component_ids:['component_demo_b'],wire_ids:[]}}, '此回执为本地示意。实际模板可能建立职责或投递工作，必须按真实模板类型理解效果；预览成功不等于应用已经发生。'),
    ], 'management_portal/hatg/catalog/template.py'),
  CreateReply: lesson('回复帖子之后，谁知道它有了新内容？', '回复属于一个既有帖子；创建回复时同时推进该帖的通知修订。', 'thread',
    {post_id:'回复哪一篇帖子。', content:'回复正文，示例文本不发送。'}, [
      variant('reply', '追加回复', ['帖子 P', 'notification_revision：8'], ['帖子 P', '└ 示例回复', 'notification_revision：9'], {notification_revision:9}, '回复对象与通知水位一起返回。通知修订变化不是改写原帖正文。'),
    ], 'management_portal/web_isa.py#L102'),
  CheckRelayTarget: lesson('成员暂时不可用，要由谁决定下一步？', '只读检查给出目标可用性与原因；等待、改派或升级由工作流策略决定。', 'compare',
    {resident_id:'接待原请求的门面。', member_id:'拟承接工作的成员。'}, [
      request('CheckRelayTarget', {resident_id:'resident_facade',member_id:'resident_member'}, 'quota', '额度不足', ['门面已登记成员', '目标额度低'], ['得到 quota_low', '没有转交或复核消息'], {status:'quota_low'}, '工作流可把判断写入共享状态、等待恢复或选择其他成员。这个检查不替调用者做策略决策，也不签发转交许可。'),
    ], 'management_portal/hatg/catalog/family_internal.py'),
  RelayMessage: lesson('转交原请求，怎样保留作者与回复归属？', '通用门面把已存在的原消息交给成员，保留内容、附件、原作者及关联。策略由工作流表达，转交回执不代表任务完成。', 'queue',
    {resident_id:'接待原消息的门面。', message_id:'门面上已有的原消息 ID。', member_id:'门面登记的承接成员。', notice:'布尔值；由门面收下告知，不与指定成员、改派或延续同用。', continue_previous:'是否附带已核定的上一轮背景。', reassign:'明确要求改派，不与延续同用。', reason:'转交或改派的理由。', reply_to_message_id:'回复门面外发消息时指定原消息，保留回复归属。', message:'回复分支的正文或附件 content_parts；转交已有原请求时不重写原文。'}, [
      request('RelayMessage', {resident_id:'resident_facade',message_id:'msg_original',member_id:'resident_member'}, 'relay', '转交原消息', ['原作者提交 msg_original', '门面已通过 UpdateFacadeMembers 登记承接成员'], ['成员收到关联消息', '原作者、附件和内容保持'], {status:'relayed',member_id:'resident_member',message_id:'msg_member'}, '不把原文复制成一个新作者任务。重复调用沿原请求关联处理，业务完成仍需读取成员实际结果。'),
      request('RelayMessage', {resident_id:'resident_facade',message_id:'msg_original',member_id:'resident_other',reassign:true,reason:'原成员不可用，工作流选择另一成员'}, 'reassign', '工作流决定改派', ['已读取原请求与转交关系', '候选成员属于该门面'], ['按原请求关系改派', '来源和回复关联继续保留'], {status:'relayed',member_id:'resident_other',message_id:'msg_other'}, '可用性检查与共享状态共同支持工作流判断；ISA 不内置固定额度阈值或复核角色。'),
    ], 'management_portal/hatg/catalog/family_internal.py'),
  MoveLineageObjects: lesson('迁移成员，会换掉它的身份和会话吗？', '通过 object_ids 选择一个或多个对象迁入目标 Lineage。迁移保留身份、会话与运行游标，并核对选区和外部关系。', 'graph',
    {lineage_id:'源 Lineage。', object_ids:'Resident、组件或 Function Block 的身份数组；只迁一个成员也用数组。', target_lineage_id:'目的 Lineage；省略时新建。', allow_empty_source:'显式允许迁空源；默认 false。'}, [
      request('MoveLineageObjects', {lineage_id:'lineage_source',object_ids:['resident_demo'],target_lineage_id:'lineage_target'}, 'free', '迁移一个独立成员', ['源〔R、S〕', '目标〔 〕', 'R 没有选区外流程依赖'], ['源〔S〕', '目标〔R〕', 'R 身份与会话保持'], {operation_id:'move_demo',source_lineage_id:'lineage_source',target_lineage_id:'lineage_target',detached:[]}, 'operation_id 用于 GetMoveOperation 跟踪目录迁移，不能充当新的 Resident ID，也不表示文件迁移已全部结束。'),
      request('MoveLineageObjects', {lineage_id:'lineage_source',object_ids:['resident_demo'],target_lineage_id:'lineage_target',allow_empty_source:true}, 'last', '迁出唯一成员', ['源仅有 R', '已确认允许迁空'], ['通过迁空前置检查', '继续核验引用、归属与工作区依赖'], {operation_id:'move_demo',source_lineage_id:'lineage_source',target_lineage_id:'lineage_target',detached:[]}, '允许迁空只改变这一项前置条件，不跳过目标权限或其他关系检查。涉及 Function Block 时按真实选区规则整体迁移。'),
      variant('referenced', '存在选区外依赖', ['仅选择 R', '选区外 Effect 仍引用 R'], ['先按诊断补齐迁移集合或处理引用'], {error:'迁移领域拒绝；具体错误由真实关系决定'}, '不能把孤立成员的旧迁移假设套在通用集合上；边界关系必须由正式迁移入口核验。'),
    ], 'management_portal/hatg/catalog/organization.py'),
  CheckPermission: lesson('没有授权和明确禁止，为什么是两种拒绝？', '本实验假设权限系统已启用。ResourceService 按当前方案检查主体、动作与资源，逐项返回原因；不执行被检查的业务指令，也不签发权限。', 'compare',
    {principal_id:'明确指定被检查的主体。', a:'方案中已存在的 A 树节点，与 resource_id 配合；整条指令的检查使用 CheckInstructionPermission。', resource_id:'直接检查的资源。'}, [
      variant('permitted', '命中有效 permit', ['示例方案存在 @demo_read 节点', '主体 reader_demo 对 resource_demo 有有效 permit', '没有覆盖该请求的 deny'], ['allowed：true', 'reason：permitted', '不读取资源正文'], {principal_id:'reader_demo',scheme_id:'demo',allowed:true,requests:[{action:'@demo_read',resource:'resource_demo',allowed:true,reason:'permitted'}]}, '这是虚构方案的局部回执。真实返回还带命中的 permit 表项；检查通过不代替业务调用时再次检查。'),
      variant('no_permit', '没有有效 permit', ['同一主体、动作与资源', '没有覆盖请求的有效 permit'], ['allowed：false', 'reason：no_permit'], {principal_id:'reader_demo',scheme_id:'demo',allowed:false,requests:[{action:'@demo_read',resource:'resource_demo',allowed:false,reason:'no_permit'}]}, '没有许可不要求先存在一条 deny；过期表项也不能当作有效许可。'),
      variant('denied', 'permit 中有 deny 洞', ['有效 permit 覆盖该请求', '有效 deny 也覆盖同一主体、动作与资源'], ['allowed：false', 'reason：denied'], {principal_id:'reader_demo',scheme_id:'demo',allowed:false,requests:[{action:'@demo_read',resource:'resource_demo',allowed:false,reason:'denied'}]}, '真实回执带命中的 deny。更具体的许可不能简单抵消仍覆盖该请求的禁止；需要看方案中的真实表项和范围。'),
    ], 'management_portal/permission_system/management.py#L68'),
  CheckInstructionPermission: lesson('检查一次指令调用，为什么需要完整参数？', '检查器根据指令与业务参数解析全部目标，并保留管理员保底判断。检查本身不执行业务，也不签发凭证。', 'compare',
    {principal_id:'明确指定被检查的主体。', instruction:'要检查的具名指令。', arguments:'拟调用的真实参数，用来解析受影响对象。'}, [
      request('CheckInstructionPermission', {principal_id:'resident_demo',instruction:'ReadResource',arguments:{address:'article_demo'}}, 'check', '检查读取权限', ['主体与资源均存在', '示例权限方案允许读取'], ['返回检查判断', '不读取 article_demo 正文'], {principal_id:'resident_demo',scheme_id:'demo',allowed:true,requests:[]}, '示例省略具体命中表项；允许结果不能替代 ReadResource 实际调用时的鉴权。对一个 A 节点与资源的直接判断使用 CheckPermission。'),
    ], 'management_portal/hatg/catalog/permission.py'),
  GetGpuStatus: lesson('没有设备数据，能否解释为 GPU 空闲？', 'Portal Web 返回所选设备的状态。设备集合为空和设备利用率为零，是两个不同事实。', 'receipt',
    {device_id:'可指定一台设备；省略时读取设备组。', refresh:'是否强制刷新状态采集。'}, [
      variant('group', '读取设备组', ['未指定 device_id'], ['devices：设备状态集合'], {devices:[{example:'设备状态对象；本例不包含真实指标'}]}, '这是回执形状的示意，没有生成随机实时负载，也没有实际采集设备。'),
      variant('unknown', '未知设备', ['device_id：未知示例 ID'], ['devices：空集合'], {devices:[]}, '空集合表示没有对应设备结果，不能画成利用率 0% 的健康设备。'),
    ], 'management_portal/web_isa.py#L833'),
  GetInstructionExecution: lesson('执行记录为什么要找原来的拥有服务？', '入口从 instruction_id 的服务标记识别记录归属。记录只在保留期内存在，业务对象需要另行读取。', 'routing',
    {instruction_id:'含服务标记的执行 ID，例如 inst_res_… 指向 ResourceService。', instruction:'没有执行 ID 时，与原 idempotency_key 配对查询；按参数分流的指令不接受这种查法。', idempotency_key:'原修改调用的非空幂等键，与 instruction 配对，不能和 instruction_id 混用。', lineage_id:'实际授权目标取自保存记录的真实归属，请求参数不能覆盖。'}, [
      variant('record', '有持久记录的修改', ['inst_res_demo'], ['入口读取 res 标记', '路由 ResourceService', '返回状态与业务对象引用'], {status:'committed',references:{example:'业务对象引用；另行读取其内容'}}, '这不是原始完整响应回放。查询状态后，用业务对象的读取指令查看当前内容。'),
      variant('readonly', '只读调用的执行 ID', ['inst_ent_demo'], ['没有持久执行记录'], {error:'instruction.not_found'}, '只读调用不保存恢复记录。没有记录不代表某次修改没有发生；到期记录也可能已回收。'),
      variant('argument_owner', '只知道双 owner 指令和幂等键', ['instruction = ClearMailbox', 'idempotency_key = key_demo', '没有 instruction_id'], ['无法仅从指令名确定记录 owner', '要求用 instruction_id 查询'], {error:'instruction.invalid_arguments'}, 'ClearMailbox 按参数分到 Web 或 Lineage。指令名和幂等键不足以确定原 owner，不能任选服务重试。'),
    ], 'management_portal/hatg/routing.py#L210'),
};

export const categoryNotes = {
  resource:'用统一地址读取正文与文件，按具名指令修改资源。', user:'维护人的账号、资料与个人导航。',
  resident:'管理持续成员的身份、上下文继承和运行设置。', conversation:'消息、回复和上下文构成协作的记忆。',
  resident_execution:'Resident 执行：观察输入、开关接单和停止执行；等待消息的整理归 Mailbox。',
  mailbox:'按主体读取 User 对话通知或 Resident 等待工作；argument_owners 区分 Web 与 SocietyService。',
  lineage_definition:'组成、连接与定义修订描述协作结构。',
  lineage_state:'共享状态单元保存运行时值，比较交换避免覆盖已变值。', lineage_run:'事件与激活路径表达正在发生的协作。',
  script_run:'脚本启动、输出和停止有独立的异步运行身份。', function_block:'封装选区并保持内部对象身份与行为。',
  family:'把成员、接待、路由与团队维护组织在一起。',
  family_internal:'门面成员、原消息转交、关联回复与汇总；策略由工作流和共享状态表达。',
  template:'按登记类型复用结构与生成规则。',
  discussion:'公开帖子、回复和聊天室。',
  organization:'组织归属与迁移保持对象身份连续。',
  permission:'权限方案、权限表项、临时凭证和有效权限由权限领域负责。', environment:'查询运行环境，或请求改变服务和模型配置。',
  isa:'查询指令本身，以及仍保留的修改执行记录。',
};
