// Authored interpretation, separate from the authoritative ISA declaration.
// Values in experiments are fictional; this module has no I/O capability.
const variant = (id, label, before, after, result, explanation) => ({id, label, before, after, result, explanation});
const lesson = (question, meaning, visual, parameters, variants, source) => ({question, meaning, visual, parameters, variants, source});
export const lessons = {
  ReadResource: lesson('读到的究竟是正文，还是一个下载入口？', '统一资源地址定位资源或内部文件。ResourceService 用真实调用者读取，并在回执中标明内容类型、大小与版本。', 'receipt',
    {address:'读取哪一个资源或内部文件。', max_bytes:'直接返回正文的字节上限；默认 4 MiB。'}, [
      variant('text', '小文本', ['address → 示例说明文件', 'max_bytes = 4 MiB'], ['text：欢迎阅读', 'content_type：text/plain', 'size：正文实际字节数', 'version：正文 SHA256'], {text:'欢迎阅读',content_type:'text/plain',size:12,version:'<正文 SHA256>'}, '内部文件按原始字节给出摘要；同样的标题不代表同一个正文版本。'),
      variant('large', '超过上限', ['address → 示例大文件', '正文超出 max_bytes'], ['download_url：短期下载入口', 'expires_at：到期时间', 'size：完整大小'], {download_url:'<仅本次调用者有效的临时地址>',expires_at:'<到期时间>',size:8388608}, '不把缺少 text 当空正文。下载地址只对本次调用者有效，支持 Range；这里不发起下载。'),
    ], 'management_portal/resources/isa.py#L95'),
  UpdateUser: lesson('改资料和改用户分类，需要相同的权限吗？', 'username 选择既有用户。资料、账号状态和用户分类有各自的检查；category_changed 只说明分类是否改变。', 'compare',
    {username:'稳定的账号身份。', changes:'display_name、recovery_email、avatar、status 或 category；分类为 portal_admin / member / intern。'}, [
      variant('rename', '改显示名称', ['username：reader_demo', 'display_name：读者'], ['username：reader_demo', 'display_name：新显示名', '用户分类未变'], {user:{username:'reader_demo',display_name:'新显示名',category:'member'},category_changed:false}, '名称变化不会创建另一个账号；category_changed:false 不表示资料没有变化。'),
      variant('category', '管理员调整分类', ['调用者：Portal 管理员', 'reader_demo：intern', 'changes.category = member'], ['reader_demo：member', 'username 保持'], {user:{username:'reader_demo',category:'member'},category_changed:true}, '无论权限系统是否启用，改变分类都要求调用者是 Portal 管理员。停用账号的 status 检查另需 DeleteUser 权限。'),
      variant('category_same', '分类已经相同', ['reader_demo：member', 'changes.category = member'], ['分类保持 member'], {user:{username:'reader_demo',category:'member'},category_changed:false}, '同值分类返回未改变，不把它当作失败或一次新的身份迁移。'),
      variant('category_forbidden', '一般用户试图升为管理员', ['调用者：一般用户', 'changes.category = portal_admin'], ['分类保持原值'], {error:'instruction.forbidden'}, '关闭权限系统也不会开放用户分类的修改资格。'),
      variant('last_admin', '降级最后一位启用的管理员', ['目标：唯一启用的 Portal 管理员', 'changes.category = member'], ['保留管理员分类'], {error:'instruction.invalid_arguments'}, '至少保留一位启用中的 Portal 管理员；此例只提交 category，不夹带其他修改。'),
      variant('invalid', '混入未知字段', ['changes：{ nickname: "新名" }'], ['原用户资料保持'], {error:'instruction.invalid_arguments'}, '服务拒绝不在允许字段中的 nickname；不能用任意字典偷偷扩展用户资料。'),
    ], 'management_portal/web_isa.py#L615'),
  ForkResident: lesson('Fork 产生新成员，哪些上下文跟过去？', '从具体成员派生新 Resident。来源身份和新身份不同，继承方式决定新会话得到的上下文范围。', 'graph',
    {lineage_id:'新成员所在 Lineage。', source_resident_id:'被派生的具体成员；Family 门面不能 Fork。', fork_mode:'full / compacted / recent-5 / recent-10 / message 选择上下文来源。', message:'message 模式必须提供的历史截止点 {message_id}。'}, [
      variant('full', '完整上下文', ['来源成员 R₁', '已存在的模型上下文'], ['来源 R₁ → 派生 R₂', 'R₂ 获得所选上下文', '继承关系有独立 ID'], {resident_id:'resident_demo_child',inheritance_edge_id:'demo_edge'}, '派生关系记录来源，新成员仍是独立 Resident。此图不表示共用一个可写会话。'),
      variant('message', '缺少历史截止点', ['fork_mode = message', '未提供 message: {message_id}'], ['没有新成员'], {error:'instruction.invalid_arguments'}, '按历史消息截取上下文必须指定截止消息；缺少截止点，在派生前即拒绝。'),
      variant('facade', '选了 Family 门面', ['source = Family 门面'], ['不创建派生成员'], {error:'instruction.conflict'}, '门面代表整个 Family；需要 Fork 其中的具体成员。'),
    ], 'management_portal/society_service/isa/residents.py#L109'),
  DeliverMessage: lesson('投递成功，等于对方已经回答了吗？', '一条消息进入指定收件人的对话及输入状态。Resident 的排队、执行和回复是后续事实。', 'queue',
    {resident_id:'收件 Resident；与 user_id 二选一。', user_id:'收件人为用户时写入 Mailbox，不启动 Resident 工作；不接受 quote 或 reply_to_message_id。', message:'保留原文和消息 ID；message.role 只能是 user。', activation_policy:'新工作、当前轮插入或下轮背景，各有不同投递语义。'}, [
      variant('busy', 'Resident 正忙', ['当前轮正在执行', 'activation_policy = enqueue_and_trigger', '新消息 M 到达'], ['当前轮继续', 'M 进入等待队列'], {message_id:'msg_demo',input_state:{state:'queued'}}, 'queued 是收件回执，不是任务完成。之后按同一个消息 ID 查询 GetInputState。'),
      variant('human', '发给用户', ['user_id = user_demo', '消息正文'], ['用户 Mailbox 未读', '没有 Resident 执行队列'], {input_state:{}}, '发给人类的消息不启动 Agent。不能把两类收件人都画成同一条执行流水线。'),
    ], 'management_portal/society_service/isa/conversation.py#L88'),
  WatchConversationEvents: lesson('断开后怎样只看新的对话事件？', '订阅从 after_cursor 之后继续。cursor 保留最后交付事件的位置，high_watermark 表示服务端水位；续读不能把两者混用。', 'stream',
    {resident_id:'订阅哪个 Resident 的事件。', after_cursor:'从已见游标之后继续，而不是重取整个会话。', input_message_id:'可限定某条输入的事件正文。', limit:'每批事件上限，默认 500。'}, [
      variant('resume', '从游标 12 恢复', ['已处理 11、12', 'after_cursor = 12'], ['事件 13', '事件 14', '批末检查点 14'], {cursor:14,high_watermark:14}, '按顺序处理事件，保存游标再继续；13、14 是示例编号。'),
      variant('empty', '本批没有正文', ['最后交付游标 14', '本批没有匹配事件', '服务端水位已到 16'], ['续读 cursor：仍为 14', 'high_watermark：16'], {cursor:14,high_watermark:16}, '空批不推进交付游标。下一次仍用 cursor=14，不能用水位 16 跳过尚未交付的事件。'),
      variant('limited', '每批只取一条', ['after_cursor = 12，limit = 1', '服务端已有事件 13、14'], ['本批交付事件 13', '续读 cursor：13', 'high_watermark：14'], {cursor:13,high_watermark:14}, '按返回的 cursor 续读才能收到 14；水位只说明服务器已到哪里。'),
    ], 'management_portal/society_service/isa/conversation.py#L241'),
  CloseIntake: lesson('关闭接单，会终止当前工作吗？', '接单开关控制新工作的启动，不替代取消输入。当前轮继续完成，后来消息进入队列。', 'queue',
    {resident_id:'单个 Resident。', resident_ids:'指定一组 Resident；与单个或 account_id 互斥。', account_id:'按执行账号选择批量对象。'}, [
      variant('working', '正在工作时关闭', ['当前任务 A：运行中', '接单：开放'], ['当前任务 A：继续', '接单：关闭', '新消息 B：排队'], {previous:'open',current:'closed',changed:true}, '关闭接单没有取消 A；若要取消当前输入，需要另一条明确的取消指令。'),
      variant('already', '已经关闭', ['接单：关闭', '再次手动 CloseIntake'], ['接单：仍关闭', '手动关闭后不会因额度恢复自动打开'], {previous:'closed',current:'closed',changed:false}, 'changed 只比较接单开关。手动调用仍可把额度守卫造成的关闭转为手动关闭，不能据 false 推断没有任何内部写入。'),
    ], 'management_portal/society_service/isa/execution.py#L115'),
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
  CreateWire: lesson('同样一条箭头，激活、观察和绑定分别是什么？', 'source → target 始终从发起方到接收方或被引用对象。kind 决定这条边的语义，不由箭头外观决定。', 'graph',
    {lineage_id:'连线所属 Lineage。', source:'激活输出节点、观察事件源或 Effect。', target:'接收节点或被引用对象。', kind:'route 激活；watch 观察；target 绑定。'}, [
      variant('route', '激活连线 route', ['输出节点 source', '接收节点 target'], ['source ──激活──→ target'], {wire:{source:'source_demo',target:'target_demo',kind:'route'}}, '只有正式触发发生时才沿激活连线传递；创建边本身不是执行一次任务。'),
      variant('watch', '观察绑定 watch', ['观察事件源 source', '被观察对象 target'], ['事件源 ──观察──→ 对象'], {wire:{source:'source_demo',target:'target_demo',kind:'watch'}}, '箭头指向被观察对象，不表示该对象向事件源推送一条激活。'),
      variant('target', '目标绑定 target', ['Effect source', '引用对象 target'], ['Effect ──目标──→ 对象'], {wire:{source:'source_demo',target:'target_demo',kind:'target'}}, '目标绑定说明 Effect 操作哪个对象，与运行路径的传递关系不同。'),
    ], 'management_portal/society_service/isa/definition.py#L180'),
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
  ApplyTemplate: lesson('预览模板和真正施加模板差在哪里？', '模板由其类型决定应用语义。结构模板可先显示拟找到、删除、修改与新建的元素。', 'compare',
    {lineage_id:'模板施加到的 Lineage。', template_id:'要应用的已登记模板。', target:'模板作用的目标，由模板类型解释。', dry_run:'只预览结构模板的变更计划。'}, [
      variant('preview', '结构模板 dry_run', ['现有元素 A', '模板拟增加组件 B'], ['原结构：仍只有 A', '预览计划：预计新建 1 个组件', 'created：尚无新对象身份'], {summary:'预览施加结果。',created:{resident_ids:[],component_ids:[],wire_ids:[]}}, '预览中的预计数量不是已创建的对象，回执的 created 身份集合仍为空。预览没有施加变更，也不代替提交时检查。'),
      variant('apply', '结构模板实际应用的含义', ['现有元素 A', '同一模板拟增加组件 B'], ['示例结构 A、B', 'created.component_ids 返回 B 的新身份'], {created:{resident_ids:[],component_ids:['component_demo_b'],wire_ids:[]}}, '这里只解释真实应用后的身份回执；当前按钮仍只改变本地演示。不同模板的业务效果由类型实现决定。'),
    ], 'management_portal/society_service/isa/templates.py#L52'),
  CreateReply: lesson('回复帖子之后，谁知道它有了新内容？', '回复属于一个既有帖子；创建回复时同时推进该帖的通知修订。', 'thread',
    {post_id:'回复哪一篇帖子。', content:'回复正文，示例文本不发送。'}, [
      variant('reply', '追加回复', ['帖子 P', 'notification_revision：8'], ['帖子 P', '└ 示例回复', 'notification_revision：9'], {notification_revision:9}, '回复对象与通知水位一起返回。通知修订变化不是改写原帖正文。'),
    ], 'management_portal/web_isa.py#L102'),
  PublishPulseItem: lesson('公开个人动态，是发送一段任意文本吗？', 'Portal Web 根据当前调用者和来源定位原个人条目，再生成公开记录。传入的是来源身份，不是替代正文。', 'receipt',
    {source_period:'原条目的周期：daily、weekly 或 monthly。', source_key:'该周期的来源键。', item_id:'调用者个人动态中的真实条目 ID；服务端重新核验。'}, [
      variant('own', '公开自己的条目', ['调用者：reader_demo', 'daily / demo-day / item_demo 属于自己'], ['读取原条目正文与依据', '建立公开记录 pub_demo', '回传更新后的 Pulse'], {publication:{publication_id:'pub_demo',owner:'reader_demo',source_period:'daily',source_key:'demo-day',source_item_id:'item_demo'},pulse:{actor:'reader_demo',enabled:true,periods:{daily:{mine:{items:[{id:'item_demo',publication_id:'pub_demo'}]}}}}}, '示例只展示回执部分字段；公开正文来自服务端原条目，不能用本地改写的文本代替。'),
      variant('repeat', '再次公开同一条目', ['同一调用者、周期、来源键和条目', '已有 pub_demo'], ['复用 pub_demo', '不新增第二条公开记录'], {publication:{publication_id:'pub_demo',owner:'reader_demo',source_item_id:'item_demo'}}, '服务端先重读并核验原条目，再复用既有公开身份；重复发布不是更新公开正文的接口。'),
      variant('foreign', '来源不属于自己', ['item_id 指向他人个人条目'], ['不创建公开记录'], {error:'instruction.forbidden'}, '无法从调用者自己的来源集合找到原条目就拒绝；知道一个条目 ID 不等于拥有发布资格。'),
    ], 'management_portal/web_isa.py#L246'),
  RelayFamilyMessage: lesson('Router 选好成员，原请求就一定已送达吗？', '转交保留门面请求的原作者、内容和引用，并建立门面到成员的消息关联。额度不足时先交本 Family steward 复核。', 'queue',
    {resident_id:'接到原请求的 Family 门面。', message_id:'门面上的原请求，不是改写后的任务正文。', member_id:'该 Family 中承接工作的成员。', quota_reviewed:'只能由本 Family steward 对已有额度复核放行。', reason:'放行复核必须给出判断理由。'}, [
      variant('relay', '额度充足时转交', ['门面原请求 msg_facade', 'Router 选择 member_demo', '成员额度充足'], ['原作者、内容和引用保持', '成员收到关联消息 msg_member'], {status:'relayed',member_id:'member_demo',message_id:'msg_member'}, '成员副本有可追溯的消息关联；relayed 只表示转交，不表示业务任务已经完成。'),
      variant('quota', '周额度低于 10%', ['原请求 msg_facade', '成员周额度剩余 8%'], ['原请求保留在门面', '尚未投给成员', '请本 Family steward 复核'], {status:'waiting_for_quota_review',message_id:'msg_facade',member_id:'member_demo',steward_message_id:'msg_review'}, '不能在等待复核时把页面画成成员已经执行；复核针对这条原请求。'),
      variant('reviewed', 'Steward 放行原请求', ['已有这条请求的待复核记录', '调用者：本 Family steward', 'quota_reviewed = true；reason = 短任务额度足够'], ['投递原请求', '保留原作者而非替换成 steward'], {status:'relayed',member_id:'member_demo',message_id:'msg_member'}, '只有对应 steward 可以放行已有复核；传 true 不会让普通调用者获得资格。'),
      variant('wrong_reviewer', '其他成员自称已复核', ['调用者不是本 Family steward', 'quota_reviewed = true，reason 已填写'], ['不向成员投递'], {error:'instruction.forbidden'}, '角色资格按当前调用者检查，不能靠参数声明自己已经获批。'),
    ], 'management_portal/society_service/isa/conversation.py#L103'),
  MoveResident: lesson('迁移成员，会换掉它的身份和会话吗？', '迁移改变所属 Lineage，保留 Resident 身份与会话；前提是它没有被流程引用。Family 成员随之离开 Family，继承等关系断开。', 'graph',
    {lineage_id:'当前所属的源 Lineage。', resident_id:'要迁移的成员。', target_lineage_id:'目的 Lineage。', allow_empty_source:'迁走源 Lineage 唯一的 Resident 时须显式允许；默认 false。'}, [
      variant('free', '没有流程引用', ['源 Lineage〔R、S〕', '目标 Lineage〔 〕'], ['源 Lineage〔S〕', '目标 Lineage〔R〕', 'R 的身份与会话保持'], {operation_id:'move_demo'}, 'operation_id 是迁移操作的身份，不能拿它充当新 Resident ID；源仍有 S，不涉及迁空源的许可。'),
      variant('referenced', '仍被流程引用', ['源 Lineage〔Effect → R〕'], ['R 留在原归属', '先处理真实引用关系'], {error:'迁移领域拒绝；具体错误取决于真实引用关系'}, '不能把一个仍被流程引用的成员直接搬走；这不是靠改变画布坐标可以解决的关系。'),
    ], 'management_portal/society_service/isa/organization.py#L12'),
  CheckPermission: lesson('没有授权和明确禁止，为什么是两种拒绝？', '本实验假设权限系统已启用。ResourceService 按当前方案检查主体、动作与资源，逐项返回原因；不执行被检查的业务指令，也不签发权限。', 'compare',
    {principal_id:'被检查的主体；省略时为真实调用者。', instruction:'检查这条指令在当前方案中的各项要求，与 arguments 配合。', arguments:'被检查指令的业务参数，用于解析真实资源。', a:'直接检查时使用方案中已存在的 A 树节点，与 resource_id 配合；不要和指令形式混填。', resource_id:'直接检查的资源。'}, [
      variant('permitted', '命中有效 permit', ['示例方案存在 @demo_read 节点', '主体 reader_demo 对 resource_demo 有有效 permit', '没有覆盖该请求的 deny'], ['allowed：true', 'reason：permitted', '不读取资源正文'], {principal_id:'reader_demo',scheme_id:'demo',allowed:true,requests:[{action:'@demo_read',resource:'resource_demo',allowed:true,reason:'permitted'}]}, '这是虚构方案的局部回执。真实返回还带命中的 permit 表项；检查通过不代替业务调用时再次检查。'),
      variant('no_permit', '没有有效 permit', ['同一主体、动作与资源', '没有覆盖请求的有效 permit'], ['allowed：false', 'reason：no_permit'], {principal_id:'reader_demo',scheme_id:'demo',allowed:false,requests:[{action:'@demo_read',resource:'resource_demo',allowed:false,reason:'no_permit'}]}, '没有许可不要求先存在一条 deny；过期表项也不能当作有效许可。'),
      variant('denied', 'permit 中有 deny 洞', ['有效 permit 覆盖该请求', '有效 deny 也覆盖同一主体、动作与资源'], ['allowed：false', 'reason：denied'], {principal_id:'reader_demo',scheme_id:'demo',allowed:false,requests:[{action:'@demo_read',resource:'resource_demo',allowed:false,reason:'denied'}]}, '真实回执带命中的 deny。更具体的许可不能简单抵消仍覆盖该请求的禁止；需要看方案中的真实表项和范围。'),
    ], 'management_portal/permission_system/management.py#L68'),
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
  resource:'用统一地址定位正文、文件和资源动作。', user:'维护人的账号、资料与个人导航。',
  resident:'管理持续成员的身份、上下文继承和运行设置。', conversation:'消息、回复和上下文构成协作的记忆。',
  resident_execution:'Resident 执行：观察输入、开关接单和停止执行；等待消息的整理归 Mailbox。',
  mailbox:'按主体读取 User 对话通知或 Resident 等待工作；argument_owners 区分 Web 与 SocietyService。',
  lineage_definition:'组成、连接与定义修订描述协作结构。',
  lineage_state:'共享状态单元保存运行时值，比较交换避免覆盖已变值。', lineage_run:'事件与激活路径表达正在发生的协作。',
  script_run:'脚本启动、输出和停止有独立的异步运行身份。', function_block:'封装选区并保持内部对象身份与行为。',
  family:'把成员、接待、路由与团队维护组织在一起。',
  family_internal:'Family 内部转交、额度与范围复核、恢复及摘要维护；由各自职责和真实调用者约束。',
  template:'按登记类型复用结构与生成规则。',
  discussion:'公开帖子、回复和聊天室；知识动态独立归 Pulse。',
  pulse:'读取日、周、月动态，并公开或撤回自己的个人条目；公开内容从原来源重读。',
  organization:'组织归属与迁移保持对象身份连续。',
  permission:'权限方案、权限表项、临时凭证和有效权限由权限领域负责。', environment:'查询运行环境，或请求改变服务和模型配置。',
  isa:'查询指令本身，以及仍保留的修改执行记录。',
};

export const sourceCommit = "f25572ad4949a008259d0d5fc5b02f942d0bdfea";
export const declarationHashes = {
  "ReadResource": "944cb63fd8dfea494d60c5d8fcd1bfc12f5a1adabdf552a34c4f816862e1d0cc",
  "UpdateUser": "1eb2490d8f99002490af41da90d5f5792660269c0c940ce4980eb4f60220e37f",
  "ForkResident": "c0de3e9ca0c892da8f835bfa8bc997bcf7119d92002552661f356beff816bb92",
  "DeliverMessage": "9e8231808f392578363cafc3aea6f5868922d52126c15bb890038cea7877e409",
  "WatchConversationEvents": "d807e787b995fa6a06be3e2a2ce0c004e9e0ff025413b52eac0d95ba6686cb18",
  "CloseIntake": "29c0001a32c85f5bc21f2ec1616784f532e283cbd1da8f1613d8bdac4bb87680",
  "CancelInput": "385daf269649127d7fe05b449ebde20332e7547a3fe9a7b3b7f43d65e82f10f4",
  "GetMailbox": "9eb6666390c9cbb6bf7617a90a759c7791a8aff3c993f838e1e41d2072f8d706",
  "CreateWire": "9b4d39ddb16358a55dad62e8cf4a4c65383d8467cc3d2668f6cc7d7a9fd57e0e",
  "UpdateSharedState": "e91b0147a500e468ff057171ba039bb6564f14f661745424eb8aee0c6f3a826c",
  "TriggerEvent": "934b01a878e5a5474b5eafa3617d661ac2346ca6afb459da65bc9d5e762e5ac2",
  "RunScript": "51e9c8c4f52767dc8cc502fa5ec74bae8a3cd1d5632d76e685f594fae6598e7b",
  "Functionalize": "9fa6c98c2160e42cee31c9eb5f49a79699df2999290fef1ed29bab69e4720ba8",
  "Familize": "6b4b2fddf16deb2521bf31d3bf784e06d04db85dcc6c7be9eaccacbed664affe",
  "ApplyTemplate": "5b39c1bae89246949f32ccb5ef379698b0737acef57b403864ac597035aa19cd",
  "CreateReply": "fe90d6d5dbee60436812f38829e4607a04206b6e0ce3acbaa82d0c2b064e43da",
  "PublishPulseItem": "8d6fa549873e1aa9eb8ffe3a46187a33e4e0efff7e5999c4edee1fb7ed93d94f",
  "RelayFamilyMessage": "9b9681a6f4f2050e48924097eeb312ca129e1459b24772992adf6f419f0091e7",
  "MoveResident": "dbd3c8e1d2e45147f3865c5b887e12104caa846dd9e7537c17a8728cdabf1164",
  "CheckPermission": "2342ae88704aa954aed736ffaaccdc1cfe12c4393dd4fbfc225686ed80c53813",
  "GetGpuStatus": "c5908c15bea9f53d48abad4285ab10a91736a85d342dcb9637f5b91c4e9eb11a",
  "GetInstructionExecution": "e3f98c1ca8bf61944ca55a1f3ff84d6868ce5403254536a06572e94f2c47217c"
};
