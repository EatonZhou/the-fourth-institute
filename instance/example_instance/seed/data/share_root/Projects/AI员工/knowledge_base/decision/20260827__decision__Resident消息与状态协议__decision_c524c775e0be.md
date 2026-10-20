---
kb_id: decision_c524c775e0be
type: decision
project: AI员工
created_at: '2026-08-27'
version:
  number: 87
  created_at: '2026-08-27'
  created_by:
    user: ''
    agent: ''
  updated_at: '2026-10-19T19:59:40Z'
  updated_by:
    user: tfi-exported-user
    agent: resident_865c3c24f251
importance: 10
metaness: 2
ai_generated: false
links:
  source:
  - ${code_root}/management_common/principal.py
  - ${code_root}/management_portal/society_service/resident_input_scope.py
  - ${code_root}/management_portal/society_service/isa/conversation.py
  - ${code_root}/management_portal/hatg/catalog/conversation.py
  - ${code_root}/management_portal/static/portal_resident_service.js
  - ${code_root}/management_portal/society_service/resident_protocol.py
  - ${code_root}/management_portal/society_service/resident_domain.py
  - ${code_root}/management_portal/society_service/resident_execution_work.py
  - ${code_root}/management_portal/society_service/active_agent_projection.py
  - ${code_root}/management_portal/society_service/resident_conversation_stream.py
  - ${code_root}/management_portal/society_service/resident_conversation_event_relay.py
  - ${code_root}/management_portal/static/resident_protocol.js
  - ${code_root}/management_portal/static/home.js
  - ${code_root}/management_portal/static/lineage_workbench.js
  - ${code_root}/tests/cases/test_resident_execution_protocol.py
  - ${code_root}/tests/cases/test_resident_state_contract.py
  - ${code_root}/tests/cases/test_resident_activity_contract.py
  - ${code_root}/tests/cases/test_agent_status_active_agents.py
  - ${code_root}/tests/cases/test_resident_sse.py
  related:
  - decision_ae422b92c6f3
  - decision_6591e7793116
  - decision_d19b9909a674
  - decision_2a3ff353165c
  - decision_2fe6fd5367fb
  - decision_741b04821171
  - decision_edc470e09f5a
  - decision_0f57656a2acd
  - decision_898b8911a2ff
  - decision_3e8c2a7f91d4
  - decision_6ad0384e020d
  - decision_b1ab45c4b3ec
  - decision_15f7df737ed5
  - decision_18abbdef3da3
  - decision_f560803947c6
  - decision_eb066fbea4ed
  supersedes: []
  derived_from: []
---
# Resident Conversation、Input 与运行控制协议

Resident 通过对话工作：别人给它发消息，它逐条处理并回复。本协议规定描述这段对话的几种对象——消息（Message）、输入状态（Input state）、等待队列（Queue）、Resident 状态、变化事件（Event）和对话快照（snapshot）——各自包含什么、怎样变化，以及调用方用哪些 HATG ISA 指令读取和控制它们。浏览器页面、其他 Resident、Telepus、承担定时工作的 Resident 和业务程序都通过同一套对象与指令使用 Resident。

本篇只规定对象和接口对外的含义。消息怎样生成、保存和执行见 decision_edc470e09f5a；浏览器怎样缓存、补读和合并回执见 decision_18abbdef3da3；聊天界面见 decision_f560803947c6；Resident 与 Lineage 的身份关系见 decision_0f57656a2acd。

## 基本概念

- **Conversation（对话）**：一个 Resident 的对话记录，按消息开始被处理的先后排列。
- **Message（消息）**：有稳定 ID 的通信事实。正文由当前 trace 的 Segment 版本提供，每个 Segment 不可修改；Harness 可以创建新版本并替换引用。待处理输入以无损包装保存在 Mailbox，开始处理时进入 trace。
- **Input（输入）**：一条用户消息的一次处理。它的状态与消息使用同一个 `message_id`。
- **Queue（等待队列）**：已经收到、还没开始处理的用户消息，按处理顺序排列。
- **Resident 状态**：整个 Resident 当前在做什么、接单开关是否打开的摘要。
- **Activity（活动）**：给调用方观察的当前运行摘要（例如正在调用哪个工具），由运行记录生成，不代替输入状态。
- 原生工具事件中的 `dynamicToolCall` 与命令、MCP 工具一样进入活动投影，保留实际运行、完成和失败状态；页面不得仅因工具类型不同而遗漏活动。
- **Artifact（产物）**：Agent 消息引用的结构化或体积较大的内容，例如 Provider 返回的完整结构化响应。
- **Event（事件）**：对话每次变化时持久保存的一条增量记录，带有递增的 `cursor`（游标）。调用方从某个游标之后继续读取，就能不重复、不遗漏地拿到后续变化。
- **调用者**：真实发起请求的用户、Resident 或服务。每次读取和操作都按调用者身份检查权限。

## 数据对象

### Message

| 字段 | 说明 |
| --- | --- |
| `message_id` | 对话内稳定且唯一的消息 ID。投递时调用者可以省略，由指令入口生成（见“投递消息”）。 |
| `role` | `user`、`agent` 或 `system`。投递输入只接受 `user`。 |
| `content_parts` | 非空的内容分段数组，见下一节。 |
| `created_at` | 消息首次被接收的时间，之后不再改变。 |
| `activated_at` | 消息首次加入可见对话记录的时间；还在队列中时没有这个字段。 |
| `author_id` | 真实发出者 ID，与 `origin.sender` 一致；Agent 回复的发出者是实际执行的 Resident。 |
| `origin` | 可信来源信封（格式 `tfi.message-origin.v1`），记录发出者 `sender`、生成来源的 Portal 服务 `producer`，以及代为发起时的 `initiated_by`。它由入口构造，用户输入不能伪造（decision_ae422b92c6f3）。 |
| `reply_to_message_id` | 回复消息可以带上的被回复消息 ID。 |
| `recipients` | 可选，收件人列表，每项是 `{"kind": "user" 或 "resident", "id": "<ID>"}`，最多 20 项，同一收件人只出现一次。Resident 发给用户的消息、Family 以门面身份发出的消息在门面对话中的记录带这个字段；其他消息省略。它和正文一样写入后不再改变。只有用户收件人在自己的 Mailbox 中看到未读（decision_15f7df737ed5）。 |
| `quote` | 可选，这条消息引用的一条消息：`{"message_id": "<被引用消息 ID>", "author_id": "<它的作者>", "text": "<摘录>"}`。只有 `role` 为 `user` 的消息可以带，一条消息只引用一条。被引用的消息必须在同一段对话中（Family 门面对话中投影的成员回复和门面的外发记录也算），发送者必须能读到它；`author_id` 和 `text`（被引用正文开头最多 1000 字）由服务端在接收时填写，之后不再改变。收到消息的 Resident 在本轮提示中看到引用的作者和摘录：正文前有一个 `<quoted-message author=… author_title=… message_id=…>` 块（184acf1d，`society_service/message_quotes.py`）。 |
| `usage` | 最终回复读取关联 Execution 的单轮用量，转换为统一的 `input_tokens`、`output_tokens`、`total_tokens` 等字段；不使用会话累计或上下文窗口用量。中间进度消息不重复展示整轮统计。实时完成和历史重载都在轮末保留原有用量页脚，统计不进入 Segment。Provider 的 `final_answer` 不单独结束 Portal 这一轮：同一 Execution 续跑后，以结束时最后一条有效回复承载一次用量；成功时取 Agent 回复，失败或停止时取终止回执，运行中的 Execution 不展示结算页脚。卡片和 Harness 输入不参与选择。分页、单条读取与完整历史使用同一 Execution 的完整当前轨迹定位结束回复，截断继承到中途的分支不把过程消息变成最终回复。输入进入终态时，服务端同时发布最终回复的更新，让已打开的聊天读取结算后的用量。 |

- **Provider 的完整结构化响应保存为产物。** 产物 ID 默认是 `{message_id}:provider_response`，Agent 消息用一个 `artifact_reference` 分段指向它。
- **排队中的消息不出现在对话记录里。** 正常投递的消息被接收时，系统保存消息本身和 `queued` 状态，但不把它加入可见记录，也不发布“消息追加”事件。它真正开始被处理时，才以同一个 `message_id` 加入对话记录、写入 `activated_at`，并发布相应的状态变化。等待执行容量、等待派发、排在其他消息后面都属于“还在等待”。同一条消息只加入对话记录一次，接收时间、作者和来源保持不变；正文展示当前 Segment 版本，Harness 转换后可以改变。
- **执行事件重放时不改写已有消息。** 系统从执行事件恢复一条已存在的 Agent 消息时，沿用它第一次保存时的 `created_at`；运行采集对同一完成事件严格核对不可变内容与元数据；执行展示通知读取当前版本，不再生成正文副本。不同 ID 的同文事件仍是不同事件。

### 内容分段

| `type` | 必要内容 | 投递时怎样处理 |
| --- | --- | --- |
| `text` | `text` | 多个文本分段用换行合成执行文本，合成后必须非空 |
| `image` | 图片引用 | 作为原生媒体输入交给模型；目标 Provider 必须支持图片 |
| `file` | 本地文件引用 | 在执行文本中加入 `[file: 绝对路径]`；目标 Provider 必须能使用本地工具（`local_tools` 能力） |
| `artifact_reference` | `artifact_id` 或引用对象 | 作为上下文引用，不自动展开成文本 |
| `resource_reference` | 统一资源地址或引用对象 | 作为上下文引用，由后续上下文组装按权限解析 |
| `context_reference` | `context` 对象 | 合并进运行上下文，见下文 |

- **不认识的分段类型不会被当作文本或媒体执行。** 它原样保存在运行上下文的 `content_parts` 中，留给理解这种类型的组件处理。
- **附件在接收时校验。** 进入执行前，入口检查调用者对目标 Resident 的权限、附件类型和本地路径：上传的附件还要核对上传记录中的调用者、Resident 和文件身份；工作区文件要在允许的目录范围内；路径必须指向有效的本地文件，并满足大小和引用长度限制。附件分段不能替换调用者身份，本地路径也不能代替上传归属检查。
- **校验通过的文件和图片以引用形式留在消息中。** 文件引用只说明接收时这个文件通过了校验，不保证之后文件内容不变；引用也不带来额外的读取权限。
- **`context_reference.context` 中有三个保留字段：** `context_text`（多个值用空行连接成运行上下文文本）、`instruction_skills`（字符串列表，去重并保留首次出现的顺序）、`working_directory`（运行目录，多处给出时必须完全一致，否则拒绝）。其他键按对象合并进运行上下文。消息原文与运行时组装的上下文分开保存。

### Input state

| 字段 | 说明 |
| --- | --- |
| `message_id` | 对应的用户消息 |
| `state` | `queued`、`running`、`completed`、`failed` 或 `cancelled` |
| `reply_message_ids` | 结束后是这条输入的最终回复：聊天服务所选当前消息中最后一条以 `reply_to_message_id` 指向它的消息。`completed` 时取 Agent 的最后一条回复（只有 Portal 回执时取回执），`failed`、`cancelled` 时取最后一条（通常是 Portal 的失败或停止通知）；群体消息的成员取群体消息的最终回复。未结束时为空。“本轮记录”、资源修改和子 Agent 等界面卡片以及工具明细不充当输入的最终回复；即使它们稍后到达并关联同一输入，也不会替换正文或结构化回复。它在读取时由聊天服务合并当前内容和运行通知后推出，不单独保存；要看处理中的全部输出，按 `reply_to_message_id` 读消息 |
| `updated_at` | 最近一次状态更新时间 |
| `error` | 失败或取消时附带的错误对象 |

正常路径是 `queued → running → completed`；`queued` 和 `running` 都可以进入 `failed` 或 `cancelled`。`completed`、`failed`、`cancelled` 是终态，写入后不再改变，也不会改成另一个终态。

### Resident 状态

`ListResidentStates` 在按当前调用者筛选可见身份后，批量读取 Resident 真值生成状态与操作能力，不逐个重新解析完整运行目标和重复读取 Lineage ACL。读取单个 Resident 时传 `resident_ids: [id]`，取 `states[0]`；筛选后已删除的对象从该次列表略过，不令其余可见对象的状态读取整体失败。


| 字段 | 取值与含义 |
| --- | --- |
| `work_state` | `idle` 空闲；`queued` 有消息在等待；`running` 正在处理；`stopping` 正在停止当前处理 |
| `work_phase` | 可省略；有等待或正在启动时说明原因：`starting_session` 正在启动模型会话；`waiting_capacity` 等待执行容量；`waiting_dispatch` 等待账号的派发间隔；`waiting_quota` 账号额度进入保留线，等待额度恢复 |
| `active_message_id` | 正在处理的消息；空闲时为空 |
| `queued_count` | 等待中的消息数 |
| `execution_gate` | 当前只读执行保护：`state=open/closed/unknown`、`reason`；可带当前账号、已确认豁免及额度采样信息。它与人工接单开关和工作状态分别表达。 |
| `intake_state` | 接单开关：`open` 时等待的消息按顺序开始处理；`closed` 时照常接收新消息并排队，但不开始新的处理，正在进行的处理继续完成 |

- 没有保存过状态的 Resident 视为 `idle`、没有活动消息、`queued_count=0`、`intake_state=open`。
- `waiting_capacity`、`waiting_dispatch`、`waiting_quota` 只在 `work_state=queued` 时出现；`starting_session` 只在 `queued` 或 `running` 时出现。三种等待原因分别显示，派发间隔不会被显示成“执行容量已满”。
- 原生 Fork 创建 Session 时，运行中的 `context_fork_create` 作业持续显示 `work_phase=starting_session`，`active_message_id` 保持为空。它不产生用户输入；等待容量时仍显示相应等待原因，创建完成后恢复 `idle`。调用方结合阶段、活动输入与 Fork 后新增输入的差量，区分 Session 管理与处理消息。
- **只有真实的执行进展才让消息变为 `running`。** 暂时收不到新的运行信息时保留最后确认的状态；不能因为会话绑定存在或页面打开就推断为 `running`。

### 可用操作（controls）



服务按当前调用者的权限和 Resident 状态计算可以执行的操作，调用方据此决定按钮是否可用：

| 字段 | 含义 |
| --- | --- |
| `can_send` | 当前调用者能否向这个 Resident 发消息。接单关闭时消息仍会排队，因此有权限时仍为 `true`。 |
| `can_stop` | 当前调用者有控制权限、并且有正在处理的消息时为 `true`。停止请求仍要带上精确的消息 ID，执行入口会重新检查。 |
| `active_turn_input` | 目标 Provider 是否支持把新消息注入正在进行的处理。 |
| `accepts_images` | 发给它的消息能否带图片：执行提供方能读图片时为 `true`。Family Router 只把带图片的请求交给为 `true` 的成员。 |
| `accepts_files` | 发给它的消息能否带文件：执行提供方有本地工具、能按路径读文件时为 `true`。Family Router 只把带文件的请求交给为 `true` 的成员。 |
| `disabled_reason` | 不能发送时的原因，例如“当前对话无发送权限。”；能发送时为空。 |
| `send_hint` | 与发送相关的提示，例如接单关闭时“Agent 已关闭接单，发送的消息将排队等待开启。”；提示不改变权限。 |

- 对话快照、单个和批量状态读取、以及返回完整 Resident 状态的操作回执，都同时返回 `controls`。`resident.state.changed` 事件被读取时，事件内容中也包含按读取者计算的 `controls`。`controls` 按读取者即时生成，不写入共享的对话状态或事件记录。
- `OpenIntake`（`open: true` 开启、`open: false` 关闭接单）返回本次开关变化的确认：`previous_state`、`state`（`open` 或 `closed`）、`changed` 和 `intake_state`。它只说明开关的变化，调用方继续用状态事件或 `ListResidentStates` 取得完整的状态和 `controls`。
- **较旧的数据不能覆盖较新的结果。** 状态、`controls` 和消息都属于同一个 Resident；较旧的快照、事件或回执不能撤销较新的操作确认或终态。登录身份切换或权限失效后，旧身份的在途响应不进入新身份的视图。
- 状态通知缺少完整 `controls` 时，调用方保持最后确认的值并重新读取；只有服务明确返回 `can_send=false` 时才采用这个值和禁用原因。

### Queue item

撤回等待项、清空队列和停止并清空等待项共用同一事务：移出队列、结束群体成员并写入输入终态一起提交；任一步骤失败全部回滚。

队列中的每一项包含 `message_id`、`author_id`、`content_preview`（内容预览，最多 500 字符）、`enqueued_at` 和用于展示的 `content_parts`。队列只列等待中的消息，正在处理的消息由 Resident 状态的 `active_message_id` 表示。

接单关闭时，还没开始处理的第一条消息也在队列中：`work_state=queued`，`active_message_id` 为空，`queued_count` 包含它。等待中的消息可以取消和调整顺序；没有正在进行的处理时不能注入。`CancelInput` 指向一条还在等待的消息时，按取消队列项处理。

### Event 与实时通知

持久事件包含 `schema`（固定为 `tfi.resident-messages-state.v1`）、`event_type`、`resident_id`、递增的 `cursor`、`occurred_at` 和 `payload`。缺少 `schema`、格式不受支持或结构版本不同的事件直接拒绝，不补默认值；同版本的历史前缀也不接受。

| `event_type` | `payload` |
| --- | --- |
| `conversation.message.appended` | `message`：新加入对话记录的消息 |
| `conversation.message.updated` | `message`；或 `message_id` 加 `removed=true`，表示归档后移除 |

- **事件记录发生了什么和先后顺序，消息正文以 trace 为准。** 事件日志里的消息事件只保存消息 ID、`role` 和 `reply_to_message_id`；转发通知、游标补读和 `WatchConversationEvents` 返回事件前，按消息 ID 从 trace 取当前的消息填进 `message`，所以补读到的是消息现在的样子（被更新过就是更新后的）。trace 中已经没有的消息，事件不带 `payload`，标 `payload_omitted: true`，读取方改读快照。`input_state` 的 `reply_message_ids` 同样在读取时从 trace 推出。
| `resident.input.state.changed` | `input_state`：某条消息的处理状态 |
| `resident.state.changed` | `state`，以及读取时按读取者计算的 `controls` |
| `resident.activity.changed` | `activities`：当前的活动集合 |
| `resident.queue.changed` | `queue`：当前的等待队列 |
| `resident.runtime_settings.changed` | `runtime_settings`：已保存的运行设置 |

- 消息内容按消息 ID 追加或替换；内容相同但 ID 不同仍是两条消息。
- **实时通知通过 NATS 消息总线发出，浏览器通过 SSE 接收。** 每个 SocietyService 实例跟踪持久事件并发布到本实例的通知总线（Dev 与 Release 各有一条）。通知可以带完整事件，也可以只带 `schema`、`event_type`、`resident_id`、`cursor`、`occurred_at` 和 `payload_omitted: true`；后一种情况由接收方用 `WatchConversationEvents` 补读完整事件。断线后从最后确认的游标继续。

WatchConversationEvents 的首批立即返回；每批末尾提供检查点 `{cursor, high_watermark}`。`cursor` 是实际交付的最后事件位置，`high_watermark` 是服务端此次读取的最新位置。空页也返回检查点；空页的 high_watermark 更大时，浏览器重新读取快照。检查点不充当对话事件，也不会让连续消息游标跳过未交付事件。

已登记但尚未产生对话事件的 Resident 返回游标为 0 的空事件页，读取本身不创建运行状态。首条事件到来后，从游标 0 继续读取即可收到该事件；不存在的 Resident 仍返回未找到。

### Conversation snapshot

- **快照用于首次加载和断线恢复。** 它包含 `resident`、首屏 `messages`、`input_states`、`state`、`controls`、`activities`、`queue`、`runtime_settings`、`cursor` 和更早消息的分页标记。消息按对话记录顺序返回；还在排队的消息通过输入状态、队列和单条消息读取。更早的历史通过分页读取。
- **快照可能稍旧，但从它的游标继续读取不会漏掉消息。** 并发写入时快照允许略微滞后，返回的 `cursor` 之后的事件覆盖这次读取可能漏掉的新消息，调用方从这个游标接着读。事件出现缺口时，调用方重新读取快照并以它为准继续。
- **快照带上全部未结束的输入。** 首屏消息对应的已结束输入状态会返回；所有 `queued`、`running` 和正在停止的输入无论消息在不在首屏都会返回，避免较早的未完成处理因为分页而消失。输入状态按 `updated_at` 从新到旧排列，`reply_message_ids` 去重。

## 投递消息

### `DeliverMessage` 的参数

| 字段 | 说明 |
| --- | --- |
| `resident_id` 或 `user_id` | 二选一。`user_id` 把消息发给用户（见下文“发给用户”）；`resident_id` 直接指定接收者。按职责登记投递的 `duty` 参数已经移除（decision_6591e7793116）。 |
| `message` | 对象，只允许 `message_id`、`role`、`content_parts` 和可选的 `quote`；`quote` 只需给出 `{"message_id": ...}`，作者和摘录由服务端填写（见消息字段表）。引用多给字段、指向另一段对话或没有正文的消息、指向这条消息自己时返回参数错误；发送者读不了被引用的消息时返回无权限；同一 `message_id` 重试沿用已接受的引用，改引另一条消息返回冲突；info 消息、授权调用的隔离输入和发给用户的消息不带引用。 |
| `activation_policy` | 可选：`enqueue_and_trigger`（正常发送，默认）、`inject_active_turn`（注入正在进行的处理）或 `attach_to_next`（info 消息，随下一次请求送达）。 |
| `business_ref` | 可选，调用方业务记录的统一 ID 或地址，随输入保存，调用方据此把回复和状态对应到自己的业务记录。 |
| `reply_to_message_id` | 可选，发给 Family 门面时指明回复的是门面发来的哪条消息：填调用者自己对话中收到的那条消息的 ID，平台换算成门面对它的发件记录并以 `quote` 引用（decision_44cf587c1721）。这条消息不是收件门面发来的、同时给了 `quote`、是 info 消息、发给用户、Router 转交或授权调用的隔离输入时返回参数错误。 |

- **消息 ID 由调用者给出，或由入口生成。** 省略 `message.message_id` 时，调用带幂等键则由目标 Resident、调用者和幂等键推导出固定 ID，重试得到同一条消息；不带幂等键则生成新 ID。`role` 可以省略，给出时必须是 `user`；`content_parts` 必须是数组，并且合成后有非空文本。`message` 和参数中出现未声明的字段时拒绝。
- **提交前依次检查：** 调用者身份、权限系统对目标 Resident 的授权、消息结构、媒体与目标 Provider 的能力。每条被接收的输入都保存为不可变的消息和处理状态；不会只产生一条模型永远不会处理的聊天通知。
- **普通异步请求的受理回执只确认持久接收。** `accepted=true`、`resident_id` 和 `message_id` 表示输入、处理状态与后续发布任务已经提交；回执可以不带完整 `message`、`queue`、`state`、`controls` 和 `cursor`。客户端通过后续快照、Input state 和事件流补齐展示，不能把受理当成模型已开始处理或已完成。展示发布失败由后台恢复，不影响已提交的输入。Family 门面、info 消息、原生命令和同步 Provider 仍按各自路径返回结果。
- **正常发送（`enqueue_and_trigger`）：** Resident 空闲且接单打开时立即开始处理；正在忙时按接收顺序排队，轮到后自动开始，不需要再发一条消息唤醒。执行容量、全局节流和接单开关各自生效，发送本身不增加等待或延迟。接单关闭时消息照常接收并等待，打开后推进。
- **注入（`inject_active_turn`）：** 要求目标 Provider 支持、并且确实有正在进行的处理；Codex、Claude Code、Kimi Code 和 DSH 支持。只注入文字，带图片的注入消息改为下一轮处理。Provider 不支持、Resident 空闲、没有执行绑定、正在停止或正在压缩上下文时，在接收消息之前就明确拒绝，调用者可以改用正常发送。被接收的注入消息与队列中的“立即注入”（`InjectMailboxItem`）使用同一套持久回执和幂等身份：回执 `pending` 表示正在等当前处理接收，不等于已经送达；注入意图持久化时消息从普通队列转入在途输入，受理回执为 `accepted=true, injected=false, delivery_status=pending`；对话中的展示不表示执行端已经接收。如果当前处理在接收前结束，同一条消息退回普通队首，等待下一轮处理，客户端从后续队列投影和 Input state 观察结果，不重新生成消息，也不重复调用模型。为业务调用隔离的输入不能注入别的处理上下文。
- **随下一次请求送达（`attach_to_next`）：** 用于 info 消息，只能包含消息内容，不能带引用、媒体、运行上下文或附加 Skill，也不能是授权调用的隔离输入。消息进入对话、保持 `queued`，不开始处理；下一条请求生成执行请求时，所有等待中的 info 作为背景更新放在请求正文之前一起送达，之后记为 `completed`。每个 Resident 最多积压 200 条，超过时拒绝。
- **Family 门面接受和任何 Resident 一样的消息内容。** 门面不执行，请求等待 Family Router 转交。请求可以带图片、文件和引用：门面收到时按发送者检查每个附件和引用（只能发送自己上传的附件，工作区路径须在门面的 Workspace 内），不通过的整条拒收；info 消息和发给任何 Resident 的 info 一样只包含消息内容。Router 原样（`content_parts` 不变）转交时，成员按原发送者的权限读取其中的附件和引用，如同发送者直接发给它；消息带 `quote` 时，被引用的是承接成员自己的回复或它承接过的请求，转交副本的 `quote` 指向成员对话中的那条消息，否则只保留作者和摘录；转交时改动了消息的，按转交者自己的权限读取。成员读不了的附件照常被拒绝（图片要求 `accepts_images`，文件要求 `accepts_files`）。`inject_active_turn`、授权调用的隔离输入，以及门面所属 steward 发来的消息仍被拒绝（decision_44cf587c1721）。
- **发给用户。** 给出 `user_id` 时，调用者必须是 Resident，`activation_policy` 只能省略或为 `enqueue_and_trigger`，`content_parts` 只包含文字和资源引用（`text`、`resource_reference`），并且有非空文字。消息不进入任何队列，也不开始工作：它以发送方的身份写入发送方自己的对话，`role` 为 `agent`，没有 `reply_to_message_id`，立即显示，`recipients` 记录这位用户；发送方在 Family 内时，按下一条规则写入门面的对话，以门面身份发出（decision_44cf587c1721）。收件用户必须能查看这段对话，否则以无权限拒绝。省略 `message_id` 并带幂等键时，ID 由收件用户、调用者和幂等键推导，重试得到同一条消息；同一 ID 换了正文返回冲突。结果中的 `resident_id` 是消息所在的对话（发送方或门面），`sender_id` 是实际署名，`recipients` 是收件人，`input_state` 为空对象。用户的 Mailbox 把发给自己、尚未读的消息算作未读（decision_15f7df737ed5）；用户在这段对话中回复，回复是发给这个 Resident 或门面的普通消息。给 Resident 和给用户发消息使用同一条指令，不另设通道。
- **Family 内的成员或 steward 发给 Family 外的 Resident 时，以门面身份发出。** 署名的门面是包含发送者、但不包含收件方的最外层 Family 的门面；来源的 `sender` 记为门面，`initiated_by` 记为实际发出者。发送者自己必须能向收件方发消息，门面不替它取得权限；门面本身也要通过对收件方的检查。消息带的附件和引用按发送者本人的权限核对和读取：只能带发送者自己上传的附件，门面或别人上传的不行；只有平台代发的这次投递如此，门面以成员为发起者的其他操作仍按门面自己的权限。发到另一个 Family 的门面时，那边的 Router 原样转交，成员同样按这位发送者的权限读取，前提是署名的门面仍代表它。门面对话同时记下一条门面署名、`role` 为 `agent`、`recipients` 写明收件 Resident、正文相同的消息（记录的消息 ID 由门面和投递的消息 ID 推导，重试不重复记录）。门面 Chat 把这条记录显示为一行“发给 <收件 Resident> · 由 <实际发出者> 发出”，点开才显示正文；发给用户的消息照常展开，也不把这条记录当作最新回复展开。结果中的 `sender_id` 是门面。以下情况保留本人身份：Family 内部互发（包括成员发给自己的门面）；Family Router 带转交标注的投递；授权调用的隔离输入；收件方最近一次亲自（不经门面转交）写信的对象是发送者本人。收件方最近写给某个内层门面时，以那个内层门面发出；最近写给外层门面或从没亲自写过时，以最外层门面发出。Fork 复制来的历史早于新 Resident 的创建，不算收件方写给它。
- **回复不自动送回发送者。** 收到消息的一方是否回复、回复给谁，由它自己决定；发送者记在来源 `origin.sender`，代为发起者记在 `initiated_by`，需要回复时用 `DeliverMessage` 发给对方。消息来自 Resident 时，收件方每轮的提示写明：本轮回复只留在它自己的对话里，不会自动送回发送者，要让发送者收到须用 `DeliverMessage` 发给它。回复 Family 门面代成员发来的消息时，平台把回复和那条消息关联起来，门面直接交给写它的成员：在处理那条消息的一轮里发给这个门面，自动引用门面的发件记录（那一轮处理的消息中恰有一条来自这个门面时）；以后的轮次用 `reply_to_message_id` 指明。这类消息的包装带一段 `<reply-hint>`，写明这两种回复方式和要填的消息 ID。
- **重复投递返回原结果。** 相同 `message_id` 和相同内容的重复投递返回已有确认；相同 ID 但内容不同时返回冲突，第一次的消息保持不变。
- **只接受上面三个 `activation_policy` 值。** `ForwardMessages` 和调用 `DeliverMessage` 的 Effect 使用相同的取值与默认值；其他值在投递、指令参数和 Lineage 组件配置中都直接拒绝。数据中残留的其他取值的未执行输入不会被恢复器自动启动，操作者可以取消后重新发送。

### 查询数量限制

- 快照 `history_limit` 为 1–1000，默认 200。
- 消息分页 `limit` 为 1–1000，默认 200；`page_token` 必须是正整数序号。
- 搜索 `limit` 为 1–200，默认 50；分页位置不能为负。

跨 Resident 的消息搜索先按调用者批量筛选可见且登记完成的身份，再分批读取这些身份的当前 Chat 投影。每批共享 trace、最近清空位置、正文和执行用量的数据库读取，不逐个解析完整 Resident 运行目标或逐个读取完整历史。搜索仍按展示内容进行 Unicode 大小写折叠后的子串匹配，按消息时间与消息 ID 倒序分页，前后消息 ID 取自所属对话的当前出现顺序。消息包装、Harness 替换、系统通知和最终回复折叠继续使用 Chat 的同一读取规则。
- 事件订阅每批 1–2000，默认 500。

limit、分页标记、游标、消息 ID、产物 ID 或参数类型不合法时返回参数错误；找不到对象或调用者无权看到时返回“不存在”，不泄露目标是否存在。

## 读取和控制这些对象的指令

调用格式、执行规则和通用错误见 decision_898b8911a2ff。

- **对话与上下文类**（decision_2fe6fd5367fb）：`DeliverMessage`、`ForwardMessages`、`GetConversation`、`ListMessages`、`GetMessage`、`ListRecentRounds`、`SearchMessages`、`GetMessageArtifact`、`WatchConversationEvents`、`GetSentPrompt`、`ClearContext`、`AbsorbContext`、`ListResidentRequests`、`GetResidentRequest`、`AnswerResidentRequest`。
- **Resident 执行类**（decision_2a3ff353165c）：`ListResidentStates`、`GetInputState`、`OpenIntake`（`open` 为 true 开启、false 关闭接单）、`CancelInput`、`CancelSubagent`。`CancelInput` 停止正在运行的一轮，或把等待中的请求、群体和 info 从 Mailbox 撤回；已结束的消息保留原结果，结果带 `already_terminal: true`。
- **Mailbox 类**（decision_c54e68a82d7a，Mailbox 模型见 decision_15f7df737ed5）：`GetMailbox`、`WatchMailbox`、`PinMailboxItem`、`MarkMailboxItemRead`、`ClearMailbox`、`ReorderMailbox`、`MergeMailboxItems`、`SplitMailboxItem`、`RequeueMessage`、`InjectMailboxItem`、5 条 Mailbox Todo 指令和 `CreateNotification`。`GetMailbox` 的 `resident_id` 与 `user_id` 恰好传一个，分别读该 Resident 或该 User 的 Mailbox；`ClearMailbox` 带 `resident_id` 时取消该 Resident 全部等待消息，带 `user_id` 时清理该 User 的 Mailbox。用户打开某个 Resident 的对话时，页面用 `MarkMailboxItemRead` 把它当前已完成的回复标为已读。
- **Resident 类**（decision_d19b9909a674）：运行设置 `GetResidentSettings`、`UpdateResidentSettings`，模型会话重启与 Provider、账号切换 `RestartSession`。

Portal 的指令执行地址返回完整的指令结果，调用方从其中的 `result` 读取各指令的结果对象。网络断开、超时或回执丢失都不能说明操作没有被接收；调用方用原消息 ID 或原幂等键确认已有结果。

发送、注入、停止和撤回回执由浏览器协议状态层统一合入。`CancelInput` 的最小回执与可选状态字段统一见 decision_2a3ff353165c：`resident_id`、`message_id`、`input_state` 必有，`cursor`、`state`、`controls` 可选，停止受理不等同后台终态；调用方不能要求每次停止都返回完整快照。带 `resident_id` 的 `ClearMailbox` 返回状态，并以 `cancelled` 表示取消数量。Family 转交后的停止或撤回回执属于实际执行成员，其 `resident_id` 和 `message_id` 指向成员，`facade_message_id` 保留门面请求 ID；调用方不能将成员回执合入门面的游标。

几项操作的补充规则：

- **立即注入（Send now）：** 接口先保存持久注入意图并返回 `accepted=true, injected=false, delivery_status=pending`，由协调器继续准备输入、投递并核对回执。回执 `pending` 表示等待当前处理接收，消息保持原 ID 和未结束状态；注入意图持久化后转入在途输入；当前处理结束前没有接收时，同一条消息退回普通队首，可查看、撤回和清除，仍受接单开关约束。重复点击复用原投递身份。正在确认投递的消息不能按普通等待项撤回或清除。送达以执行服务的持久回执为准：回执完成时同时更新执行记录的变更时间，Dev、Release 和滚动换代中的所有观察者都能看到；Resident 在执行快照变化和重连后的第一个快照时核对回执，不依赖某个进程的临时通知。
- **停止（Stop）：** 请求指向一条精确的消息 ID。请求期间当前处理已经换成另一条消息时返回冲突，后面的消息和队列保持不变。取消被接受后立即在回执中可见，Resident 可能短暂处于 `stopping`；执行确认停止后，消息才进入 `cancelled` 终态。已经结束的消息返回它原有的结果，迟到的回执不会恢复旧的运行状态。
- **清空上下文（Reset，`ClearContext`）：** 只归档操作开始时已有的消息，Resident 身份和之后的新消息保留。清理完成前新消息继续排队。 ClearContext 接纳回执可带 `cleanup_pending: true`，接纳不等于后台归档投影完成。生命周期归档投影在持久化 Reset 边界后，将消息移除事件与 `conversation.context.cleared` 事件一起提交；即使没有显示中的旧消息，也发出后者。共享 Conversation 协议收到完成事件后将已有 Fork 来源卡片的 `context_cleared` 更新为 true，保留来源与历史计数，不删除 Reset 后的新输入，不靠延迟轮询或再次清空推断完成。
- **运行设置：** 按目标 Provider 的能力校验；`collaboration_mode` 只适用于 Codex，取值 `default` 或 `plan`，从下一轮开始生效。回执中的 `next_turn_fields` 列出从下一轮才生效的字段，保存成功不代表当前这一轮已经切换。
- **管理类操作按权限执行：** `UpdateResidentSettings`、`ClearContext` 这类管理操作能否执行由权限系统判断，页面是否显示对应按钮不影响判断。
- **实际发送内容（`GetSentPrompt`）：** 读取前验证调用者身份、个人 AMA 私有范围、目标可见性和消息归属；所需等级由当前权限模板对该指令的映射决定，默认模板要求对目标 Resident 有 admin（decision_b820faf42218）。能读对话不等于能读实际发送内容。

## 调用范围与隔离

- **所有入口使用同一套对象。** 聊天、通知、Home 状态、知识与论文讨论、Telepus、承担定时工作的 Resident 和语义搜索都使用同样的消息和状态对象；调用方可以只展示状态、产物或结构化结果，展示方式不改变输入和回复的归属。
- **语义搜索的每次模型调用都是一条标准输入。** 它产生标准的消息、输入状态和事件，结果通过这条输入的 `reply_message_ids` 与产物取得，取消和错误也沿这条输入观察。Saved Search 继续负责查询范围、搜索记录和结果展示，搜索记录关联到这次输入（decision_eb066fbea4ed）。
- **共享的 Resident 对每次调用分别隔离。** 同一个搜索 Resident 被多人共用时，每次调用分别记录真实发起者、查询、授权候选、上下文和结果；消息作者记录真实来源；新调用不继承其他用户或上一次调用的私有模型历史。读取消息、事件和结果时按当前调用者重新授权，能使用共享 Resident 不代表能读别人的调用内容。重复请求复用原输入，显式刷新产生新输入，旧运行的迟到结果不会覆盖新运行。
- **可以只读取一条输入。** 快照和事件读取可以指定 `input_message_id`，只取得这条输入及其回复；服务仍检查调用者对这条输入的权限。业务调用可以为单条输入登记“输入范围授权”（input scope），允许发起者读取和取消这一条输入，而不获得整个 Resident 的历史和管理权限。授权记录保存在 LineageData 的 输入范围授权记录中，记录发起者和不可修改的描述；同一输入再次登记时，发起者和描述必须与第一次一致。
- 指定输入范围时，快照只包含这条消息、它的回复、输入状态和队列项；其他输入的事件保留有序的游标信封并标记 `payload_omitted: true`，正文不会通过这个范围读到。不指定范围的读取需要整个对话的读取权限。

## 权限与出错时的行为


1. 每次查询和操作都按当前调用者重新授权；`controls` 只说明读取那一刻可以做什么。
2. 通知内容被省略或丢失时按游标补读；事件出现缺口时用新快照恢复。临时网络错误后，已有内容仍可读，恢复连接后继续接收更新。
3. 同一消息 ID 和相同内容的重复投递返回原确认；内容冲突返回 409。
4. 输入终态不会改变；迟到的状态和回执不能让已取消或已完成的输入再次运行。
5. 权限或身份变化后，旧身份的在途消息、历史页、状态和操作回执失效；重新授权失败时返回禁止或不可见。
6. 回复还不可读时显示“未就绪”或失败，不会伪造回复，也不会显示成成功的空结果。
7. 停止请求不会因为故障恢复而被撤销。系统自动产生的恢复输入保留系统来源，不冒充用户新发的消息。
8. 某一条输出不能构成合法消息时，服务报告这一处失败；其他合法消息和控制操作照常继续。
9. 重新执行一条已经结束的输入时，新的尝试使用新的输入消息 ID 和任务 ID，保留原发送者和原失败输入，并记录与原输入的关联；新执行不继承旧的事件游标、隔离记录或取消意图。

## 相邻 Design

- 消息生成、锁、跨存储恢复和通知中继：decision_edc470e09f5a。
- 浏览器事件补读、身份失效和回执合并：decision_18abbdef3da3。
- 聊天界面：decision_f560803947c6；Home 活动展示：decision_e30897dd3dc3。
- 会话重启、Fork 和工作区交接：decision_c47a13e8b962、decision_5bf5bfbd52cb、decision_7ef392b5f60d。

## 内容版本与显示策略

首次内容记录是 Portal 无损包装后的 Segment，不另存一个未包装正文 Segment。Harness 转换或合并后，trace 只引用当前结果；旧版本和来源关系用于审计，不再次发送或显示。聊天服务按内容类型、角色及消息事实选择展示，数据库不保存显示开关或发送给模型的开关。解包失败时显示当前全文。纯界面卡片、失败通知、统计和清空动作是运行记录，不是 Segment；消息事件补读通过同一聊天服务取得当前投影。清空位置独立于内容引用，分页与 Fork 按当前引用边界计算。


通知索引按各 Resident 的输入终态更新时间从新到旧查找可通知结果，并跨 Resident 批量读取候选回复。找到真实回复或失败/取消结果后停止该 Resident 的历史回复查询；没有真实回复的已完成控制回执继续向前查找。历史长度不应导致每次 User Mailbox 刷新重新解析全部旧回复。

读取一条通知的已读版本，在找到该条目的索引页后立即停止，不遍历无关 Resident 的所有分页。服务确认的已读回执在浏览器中保留到相同完成版本的读取投影收敛；较早发出的索引或 Resident 状态请求晚到时不能恢复旧未读标记。新的完成版本仍按正常规则提示未读，失败操作恢复原状态。

事件补读以服务端实际返回的批次为准，包括服务端明确返回的空批次。浏览器等待超时和流提前结束属于可重试的同步错误，不能伪装成没有新事件；重试保留尚未消费的游标和待恢复消息。服务繁忙或网络延迟时，已完成输入对应的回复必须继续补读并显示。



Resident 删除或权限变化使身份事件的目标不再可读时，实时服务仍发送不包含该目标 ID 的 snapshot_required 通知。已打开的 Chat 重新读取自身绑定，收到 403/404 后停止订阅、关闭输入能力并显示不可用状态；过时的协议或 metadata 响应不能恢复该面板。普通不可读 runtime 事件不触发全量刷新。


## 当前状态的持久化边界

人工输入开关以 Resident 的持久接单开关 为准。额度保护不改写这个开关。状态读取另外返回 `execution_gate`：手动关闭优先；否则按当前 Session 实际绑定账号读取 Execution Scheduler 的持久额度、保护阈值和已确认 binding 豁免。额度耗尽（`used_up`）即使阈值设为零也会阻止执行；不能用 `exhausted=false` 推断可运行。`closed` 明确表示执行保护关闭，并区分 `manual`、`quota_used_up`、`quota_reserved`、`account_disabled` 等原因；恢复尚未确认、服务不可达或账号未知以 `unknown` 及相应原因表示，不冒称放行。尚未被 Scheduler 确认的 Resident 豁免不能覆盖它的调度事实。人工开关、`work_state`、当前工作与队列身份保持。

`ListResidentStates` 和对话快照使用相同的只读保护投影。一次批量读取中 Scheduler 的额度/阈值/豁免来自同次观察，但账号目录、Session 绑定和人工开关是相邻采样，不承诺跨服务原子快照或下一次 claim 一定成功。读取不产生执行、取消、重排或数据库迁移。内部可选查询限定等待时间；滚动期间旧服务不支持此读取或暂时不可达时返回 `unknown`。`controls.can_send` 继续表示接收并排队权限，`send_hint` 解释执行保护原因。

界面应显示保护 `CLOSED` 或“未确认”及原因，并通过正式批量状态读取刷新；不能把较新工作事件中缺少 `execution_gate` 解释成已开放。Conversation 游标不代表额度修订，原最小受理回执与事件不增加一套额度状态机。Family Router 的保护和人工开关只描述该 Router，转交后的业务等待或取消须关联下游输入事实显示，不能改写 Router 接单状态。

当前工作状态结合 当前轮次控制记录、Mailbox 和 Execution Scheduler 的批量控制状态读取；空闲 Resident 无需建立工作行。Conversation 已发布状态 保存与其事件游标配套的已发布界面快照，读取对话历史时可以直接使用，启动和取消工作必须读取相应负责者。

停止当前输入先核对输入 ID 与轮次控制记录，持久化停止意图后再交给 Execution；Execution 暂时不可达不会阻止停止意图落盘。额度等待和恢复由 Scheduler 执行，人工输入开关保持原值。轮次完成且结果投递和执行确认结束后，当前轮次行清理，收据永久保留在 轮次收据记录。

格式名与版本须精确匹配当前规范值，校验规则见 decision_86aed2f0e8c7；本篇同时校验各字段和结构版本，不接受历史前缀或其他版本。
