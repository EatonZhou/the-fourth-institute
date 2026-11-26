---
kb_id: decision_2fe6fd5367fb
type: decision
project: AI员工
created_at: '2026-09-28'
version:
  number: 62
  created_at: "2026-09-28T10:45:00Z"
  created_by:
    user: "weihao"
    agent: "resident_2a1fc6cd272d"
  updated_at: "2026-11-19T08:46:57Z"
  updated_by:
    user: ""
    agent: "resident_60ae6d0cdf74"
importance: 8
metaness: 2
ai_generated: false
links:
  source:
  - /opt/tfi/the-fourth-institute-dev/code/management_portal/resident_handoff.py
  - /opt/tfi/the-fourth-institute-dev/code/management_portal/society_service/isa/conversation.py
  - /opt/tfi/the-fourth-institute-dev/code/management_portal/society_service/resident_protocol.py
  related:
  - decision_6591e7793116
  - decision_3e063aac15b3
  - decision_15f7df737ed5
  - decision_c54e68a82d7a
  - decision_b77fde81e7a0
  - decision_898b8911a2ff
  - decision_c524c775e0be
  - decision_edc470e09f5a
  - decision_c47a13e8b962
  - decision_5bf5bfbd52cb
  - decision_c616d197abbb
  - decision_18abbdef3da3
  - decision_b1ab45c4b3ec
  - decision_25eb93d803a4
  supersedes: []
  derived_from:
  - decision_898b8911a2ff
  - thinking_ca8f397a47de
---
# HATG ISA：对话与上下文指令

对话与上下文指令处理 Resident 的“记忆”：给 Resident 发消息、转发消息、读取和搜索对话、订阅对话变化、回答 Resident 提出的问题、查看实际发送给模型的内容、清空上下文，以及吸收其他 Resident 的上下文。User 与 Resident 的 Mailbox、个人 Todo 和给人类参与者的通知属于 Mailbox 类（decision_c54e68a82d7a）；转交门面请求属于门面与转交类（decision_b77fde81e7a0）。多位参与者共同读写的帖子和资源讨论属于“公共讨论”类（decision_3e063aac15b3）。所有指令共同遵守的调用格式、执行流程和错误语义见 decision_898b8911a2ff。

## 操作的对象

- **对话（Conversation）**：一个 Resident 按执行顺序排列的消息记录。每个 Resident 有一段对话。
- **消息（Message）**：有稳定 ID、内容不可变的一条消息，角色为 `user`、`agent` 或 `system`。用户消息被接收后先等待执行，开始执行时才加入可见对话。消息、内容分段（content part）、输入状态和对话事件的字段定义见 decision_c524c775e0be。
- **产物（Artifact）**：Agent 消息引用的结构化或大体量内容，例如 Provider 的完整响应。
- **对话事件**：对话变化的持久记录，每条带递增的游标（cursor）。调用者从已读到的游标继续读取，就能不遗漏地跟上变化。
- **模型上下文**：模型在 Resident 当前会话中已经积累的内容。它可以被清空，也可以吸收其他 Resident 的上下文。切换 Provider、账号并重启模型会话属于 Resident 指令（decision_d19b9909a674）。上下文清空的过程见 decision_c47a13e8b962。
- **待回答请求**：Resident 在工作中向人发出的提问或批准请求，以一条带待处理工具调用的 Agent 消息出现。回答送回原来那一轮工作后，Resident 继续执行。

## 指令一览

| 指令 | 改变状态 | 作用 | 默认方案要求 |
| --- | --- | --- | --- |
| `DeliverMessage` | 是 | 向 Resident 投递新请求或下一轮告知。 | 对 `resident_id` 有 @message |
| `PrepareResidentInput` | 是 | 按固定 ID 准备一条可随后随任务授权的独立请求，无投递或唤醒。 | 对 `resident_id` 有 @message，并复核普通 `DeliverMessage` 及原领域投递限制 |
| `ForwardMessagesToResident` | 是 | 把选中的消息整理成一条新消息发给 Resident | 对 `target_resident_id` 有 @message |
| `ForwardMessagesToChatroom` | 是 | 把选中的消息整理成一条新消息发到聊天室 | 对 `target_chatroom_id` 有 @contributor |
| `GetConversation` | 否 | 读取对话快照 | 对 `resident_id` 有 @viewer |
| `GetDeletedResidentHistory` | 否 | 有界读取已删除 Resident 的保留历史及精确工具证据 | 对精确 `resident_id` 有 @admin；仍核验个人空间边界 |
| `ListMessages` | 否 | 从 GetConversation 的游标读取更早的消息。 | 对 `resident_id` 有 @viewer |
| `ResolveMessageContextReferences` | 否 | 受理前读取已鉴权材料快照，保留来源身份与范围 | 对 `target_resident_id` 有 @viewer |
| `GetMessage` | 否 | 读取一条消息 | 对 `resident_id` 有 @viewer |
| `ListRecentRounds` | 否 | 读取最近几轮往来：请求、由谁答复、结果和回复开头；也列出主动发给这位发送者的消息 | 对 `resident_id` 有 @viewer |
| `GetMessageArtifact` | 否 | 读取消息引用的产物 | 对 `resident_id` 有 @viewer |
| `WatchConversationEvents` | 否（流式） | 从游标之后订阅对话事件 | 对 `resident_id` 有 @viewer |
| `GetSentPrompt` | 否 | 读取某条消息实际发送给模型的内容 | 对 `resident_id` 有 @admin |
| `GetSubagentRun` | 否 | 按分页读取一个子 agent 的任务、工具步骤、消息与时间 | 对 `resident_id` 有 @viewer |
| `ClearContext` | 是 | 提交上下文重置并归档当前对话；后续输入待清理完成后从新的模型上下文开始，Resident 身份保持不变。此操作不保证删除 Provider 端保留的会话历史。 | 对 `resident_id` 有 @admin |
| `AbsorbContext` | 是 | 让一个 Resident 吸收另一个 Resident 的上下文 | 对 `source_resident_id` 有 @viewer；对 `target_resident_id` 有 @contributor |
| `ListResidentRequests` | 否 | 列出 Resident 等待回答的提问和批准请求 | 对 `resident_ids` 有 @viewer |
| `GetResidentRequest` | 否 | 读取一个请求及其回答状态 | 对 `resident_id` 有 @viewer |
| `AnswerResidentRequest` | 是 | 回答问题或作出审批决定，送回等待中的轮次。 | 对 `resident_id` 有 @message |
| `DeliverUserMessage` | 是 | Resident 向用户投递消息，写入发送方对话及收件人 Mailbox。 | 对 `user_id` 有 @message |
| `ListMessageAttempts` | 否 | 读取按业务对象权限过滤的尝试记录。 | 对 `resident_id` 有 @viewer |
| `ListResidentInputHistory` | 否 | 读取调用者本人在该 Resident 会话发过的输入，包括其可读的继承输入。 | 对 `resident_id` 有 @viewer |
| `SearchResidentMessages` | 否 | 在指定 Resident 的对话中按内容搜索。 | 对 `resident_ids` 有 @viewer |
| `CreateContextReset` | 是 | 安排当前轮结束后、下一条输入提交前清空上下文，保留排队消息及原账号。 | 对 `resident_id` 有 @admin |
| `CancelResidentRequest` | 是 | 关闭等待中的请求或消息问题组。 | 对 `resident_id` 有 @message |

## 投递消息

### `DeliverMessage`

**向 Resident 投递一条用户消息；Resident 空闲时立即开始处理，忙碌时按接收顺序排队。**

- 参数：`resident_id`、`message`、`activation_policy`、`context_policy`、`execution_constraints`、`business_ref`。消息由 `message_id`、`role`、`content_parts`、可选 `metadata` 和 `quote` 组成。
- 授权需随接收方任务生效时，按下一节准备、签发和投递。普通消息直接投递。按时长临时凭证沿权限指令原规则独立签发（decision_22227d0ad075）。
- 消息中的资源引用不做权限检查：发送方可以引用任何地址，收件方读取时按自己的权限判定（weihao 2026-10-18）。需要让收件方读到它原本读不到的资源时，用 `CreateTemporaryCredential` 授权。
- Family 的成员或 steward 发给 Family 外的 Resident 时，以包含它、但不包含收件方的最外层门面投递，门面对话另记一条写明收件人的记录；Family 内部互发和外部直接找成员开始的对话保留本人身份（decision_44cf587c1721）。
- `business_ref` 是调用方业务记录的统一 ID 或地址（例如一次论文评审任务），随输入保存。调用方据此把回复、产物和输入状态对应到自己的业务记录。
- `message.message_id` 可省略：调用带幂等键时，Runtime 根据幂等键生成；不带时生成新 ID。`role` 可省略，显式提供时必须是 `user`；`content_parts` 至少要产生非空文本。
- `activation_policy` 取值：`enqueue_and_trigger`（默认，空闲即执行、忙碌时排队）或 `attach_to_next`（info 消息，concept_43c793b4f3ab）。
- `attach_to_next` 的消息只告知变化，不开始工作：它进入对话、状态为等待，并记入该 Resident 的待附带列表；之后第一条开始执行的请求在生成执行请求时，把所有待附带的 info 按到达顺序、连同作者和时间放在请求正文之前，随后把这些 info 记入对话并标记为完成。info 消息只接受消息内容，不接受 Context 引用、附件、运行说明或 Skill；同一 `message_id` 重复投递只记一次。
- 结果包含消息 ID、该消息的输入状态、实际署名 `sender_id` 与消息所在对话的 `resident_id`。
- 相同消息 ID 和相同内容的重复投递返回原结果；相同 ID 但内容不同时返回冲突，第一次的消息保持不变。
- Resident 关闭接单时，消息仍被接收并等待，重新打开接单后才开始执行。
- `message.quote` 引用当前对话中的消息。按收到的门面消息追溯原写作者时，使用 `RelayMessage` 的 `reply_to_message_id`。要注入正在执行的工作时，用 `InjectMailboxItem`。

### `PrepareResidentInput`

按固定 `message.message_id` 准备一条独立排队的新请求。认证 User 或 Resident 使用本人身份提供接收 `resident_id`、完整消息及原投递参数，`activation_policy` 省略或为 `enqueue_and_trigger`。准备绑定发起人、目标、ID、消息、`context_policy`、`execution_constraints` 和 `business_ref`，检查原投递权限与领域限制，且不产生接收输入或唤醒。立即执行的斜杠命令和 info 沿普通投递处理。

准备返回 `resident_id`、`message_id` 和 `status`。同一意图重试复用准备记录。需要授权的结构化问题回答还返回 `credential_recipient_id`、`credential_message_id`，按 decision_1e2eb869bc10 使用；Family 门面仍是投递目标，实际获权方是原提问成员。

同一发起人为每项权限调用 `CreateTemporaryCredential` 并等待成功，任务型签发省略 `expires_in_seconds`。普通任务的凭证目标是所准备的接收 Resident 和消息 ID，结构化审批采用准备回执的准确绑定。待生效凭证不参与权限判定。然后原样调用 `DeliverMessage`；服务在一个事务中复核原意图、受理输入并启用凭证，提交成功才唤醒。受理失败回滚本次受理与激活；同参原 ID 重试，已释放不补签。准备发起人可以在原会话调用 `CancelInput` 取消未投递的准备。取消准备或输入完成、失败、取消时按绑定输入回收凭证。

### `DeliverUserMessage`

接受 `user_id` 和 `message`。只有 Resident 可以使用。消息不进入队列、不开始工作：它写在发送方自己的对话中（Family 成员或 steward 发出时写在门面的对话中，以门面署名），`recipients` 记录这位用户；收件用户必须能查看这段对话。`content_parts` 只包含文字和资源引用。规则见 decision_c524c775e0be。

### `ForwardMessagesToResident`

**把选中的若干条消息整理成一条新消息，发给 Resident。**

- 参数：`target_resident_id`、`messages`（每项包含来源 `resident_id` 与 `message_id`；聊天室中的消息改为给出 `chatroom_id` 与 `message_id`）、`note`（可选的附加说明）、`activation_policy`（可选，取值与默认值同 `DeliverMessage`）。
- 结果：新消息的 `message_id` 与 `input_state`。
- 每条消息都以调用者自己的权限重新读取：Resident 对话中的消息需要调用者能读这个 Resident 的对话；聊天室中的消息对已认证调用者开放，不以成员或召唤关系限制（decision_a6bebf4ac283），转发内容的标题注明来自哪个聊天室。读不到的消息返回 `instruction.not_found`。
- 投递遵守 `DeliverMessage` 的全部规则。

### `ForwardMessagesToChatroom`

**把选中的若干条消息整理成一条新消息，发到聊天室。**

- 参数：`target_chatroom_id`、`messages`（含义同上）、`note`（可选的附加说明）。没有 `activation_policy`。
- 结果：新消息所在的 `chatroom_id`、`message` 与 `deliveries`（新消息中 @ 到的 Resident 的投递，同 `CreateChatroomMessage`），以及转发生成的新消息 `message_id`。
- 来源消息的读取规则同上。新消息由同一调用者通过 `CreateChatroomMessage` 发送，沿用它的权限、字数上限、未读、发布事件与幂等语义。
- 转发正文保留每条来源的作者与文字，以 Markdown 引用呈现；原文中的 @ 用户、@全员与 @Resident 不重新触发。附加说明写在引用外，其中显式写出的 @ 按普通聊天室发言处理。超出聊天室消息上限时返回原发送接口的长度错误，失败时不生成新消息。

## 读取对话

读取类指令的数量上限：快照 `history_limit` 为 1–1000，默认 200；分页 `limit` 为 1–1000，默认 200；搜索 `limit` 为 1–200，默认 50；事件订阅的单批上限为 1–2000，默认 500。参数超出范围或格式错误时返回 `instruction.invalid_arguments`；目标不存在或调用者无权看到时返回 `instruction.not_found`。

### `GetDeletedResidentHistory`

仅用于已删除 Resident 的保留历史。参数为精确的 `resident_id`、页面类别 `section`，以及可选的 `cursor` 和 `limit`；每页最多 100 项。`identity` 报告已保存的删除事实；`messages` 包含清空聊天窗口之前仍保留的通信记录；`executions` 包含没有生成 Segment 的失败执行；`segments` 只返回元数据和产物引用索引，不读取工具正文或附件字节。

`section=tool_evidence` 用于精确工具取证，必须指定目标原 trace 中的正整数 `ordinal`。默认返回无 input/output 正文的工具索引；读取正文须同时提供索引返回的 `tool_index`、`tool_item_id` 和 `expected_hash`。服务重新校验 ordinal 成员关系、内容 hash、创建 Agent 及已记录执行的目标归属。只支持权威 `segment_type=tool` 且具有非空字符串 `tool_name`、`call_id` 的标准工具对象；普通消息中的同形内容，以及带 `type` 或 `tools` 封装的对象，不作为标准工具证据。未支持的类型或结构明确返回缺口，不扫描其他消息猜测工具。索引及正文均返回原始 Segment 类型和 `status`，不把 result 存在视为执行成功。

工具容器仅在 SQL 内按不超过 1 MiB 的上限解析。标准对象包含单项工具；索引不附正文。单项 input/result 的 JSON 文本各最多 16 KiB，未记录或超限明确标注，正文没有分片或续读。附件和媒体块省略，不读磁盘或外链。工具索引游标还绑定 ordinal、内容 hash 和位置；正文选择不得携带游标。其他 section 不接受工具选择参数。

每次读取重新按当前调用者核验目标管理权限和个人 AMA 规则。显式 deny、撤权和过期委托仍拒绝；游标绑定目标及页面类别，不承载权限。外部或无法证实归属的来源不得随目标权限展开。读取不依赖原 Lineage 或工作目录仍存在，不创建 Agent、Conversation、初始化、会话或队列，也不触发模型调用。超出预算时明确标注省略，不能把当前页或省略正文称为完整历史导出。缺失、非删除目标、空历史和无权限按各自结果报告。工具记录的读取不产生资源登记恢复或 ACL 修改许可。

### `GetConversation`

**读取一个 Resident 的对话快照，用于首次加载或断线恢复。**

- 参数：`resident_id`、`history_limit`（可选）、`input_message_id`（可选，只读取这一条输入及其回复）、`include_context_evidence`（可选布尔值，默认 false）、`include_inherited_context`（可选布尔值，默认 false）。
- 结果：Resident 摘要、最近的消息、输入状态、Resident 状态与可用操作、当前活动、等待队列、运行设置、事件游标 `cursor`，以及还有更早消息时的 `next_cursor`（交给 `ListMessages` 续读）。
- 调用者从返回的游标调用 `WatchConversationEvents`，即可接着收到快照之后的所有变化。

**Fork 出的 Resident 的快照另带 `inheritance`，说明它的上下文从哪里继承。** Chat 用它在历史开头显示继承卡片（decision_5bf5bfbd52cb）。内容包括：直接来源与上下文来源的 Resident ID，调用者能看到来源时另给标题和所在 Lineage；`fork_mode`、`forked_from_at` 和消息 Fork 的 `source_message_id`；创建时继承的片段数、其中的对话消息数和最后一条继承消息的 ID；之后是否清除过上下文；继承的窗口以来源的一次压缩开头时，`opening_compaction` 给出这次压缩的时间。继承范围由 AgentData 在创建 Fork 时写下的回执确定；没有回执的早期 Fork 只给出来源与方式，`recorded` 为 false。用 `input_message_id` 限定的读取不带这一项。

显式设置 `include_inherited_context=true` 时，`opening_compaction` 另带 `items` 和 `encrypted`。`items` 是压缩 replacement history 中可读的用户与 Agent 消息（文字及图片数量），来源会话的指令（developer、system 消息）不返回；Provider 加密保存的摘要不能显示，只记为 `encrypted=true`。读取资格与读取实际发送的 Prompt 相同：调用者能看到这个 Resident，并且是用户或 Resident。

显式设置 `include_context_evidence=true` 时，额外返回 `context_evidence`（`tfi.resident-context-evidence.v1`）。SocietyService 按当前调用者检查 Resident 可见性与实际模型输入读取资格，再由 AgentDataStore 在同一个 PostgreSQL 只读快照中读取完整轨迹计数、有效窗口及 Fork 导入事实。普通聊天加载不执行这项诊断读取；`input_message_id` 限定聊天消息，额外证据仍描述 Resident 当前有效上下文。

诊断结果同时提供有效窗口和每次 Fork 导入的有序 `references`（Segment ID、类型、内容 SHA-256、Provider turn ID），每组最多 1,000 项，超出时显式标记 `references_truncated`。压缩项带内容 SHA-256 和持久化 `compressed_from` 来源 ID；来源关系最多返回 2,000 条，超出时标记 `compressed_from_truncated`。这些标识与哈希来自同一数据库快照，不返回消息正文、图片字节或 Provider 的加密摘要。

- `trace` 给出完整轨迹的 Segment 数量和类型分布；`effective_context` 给出应用压缩及重置边界后的数量、首尾 Segment ID、类型分布及压缩 replacement history 摘要。排队而尚未投递的输入不计入有效窗口。
- `trace.positions`（`tfi.trace-position-evidence.v1`）给出同一只读 `repeatable read` 快照中的 `trace_id`、`trace_updated_at`、`sampled_at`、`consistency=repeatable_read_read_only` 及按 ordinal 排列的 `entries`。每项只包含 `ordinal`、`segment_id`、`segment_type`、`content_sha256`、`message_id`、`execution_id`、`segment_started_at`、`segment_finished_at`、`provider_session_ref`、`provider_turn_id`、`provider`、`backend_route_id`、`backend_session_id`。最多返回当前 trace 最前 2,000 项，超过时 `truncated=true`；完整数量仍由 `trace.segment_count` 给出。原生会话与 turn 只从该 Segment 的实际 Execution 关联读取，不能用当前 Resident 配置填补。缺失时间为 null；时间含义遵循 decision_25eb93d803a4，不将 Segment 完成、首 token 或 Execution 总跨度解释为一次原生模型请求的起止。
- `trace.positions.entries` 可选的 `provider_item_id` 是首次捕获 Provider 消息或工具事件时实际收到的 item ID；它与该条目的 Execution、原生会话及 turn 一起用于核对身份。无原生 ID、合成的最终回复和以前未记录该事实的片段均省略此字段，不以文本、内容摘要、内部 Segment ID 或重放来补造；合法同文但不同原生 ID 的消息分别保留。
- `effective_context.tool_observations` 至多返回 200 条工具身份、状态与起止时间，超出时标记 `truncated`。有实际捕获时返回 `provider_item_id`；原生 commandExecution 的 `command_sha256` 是完整命令 UTF-8 字节的 SHA-256，`observed_exit_code` 只取原生事件携带的整数退出码，完成状态不能代替缺失退出码；原生等待仍可返回已观测的 `observed_duration_ms`。诊断不返回命令正文、参数或输出，不猜测缺失或格式错误的事实。相同原生事件重放不改写已存在片段及其原有证据缺口。
- `transformations` 返回该 Resident 最新至多 2,000 条持久转换事实。每项包含 `schema=tfi.trace-transformation.v1`、`receipt_id`、`trace_id`、`operation_id`、`operation_type`、`recorded_at`、`execution_id`、`sources[{ordinal, segment_id, message_id, content_sha256}]`、`target_segment_id`、`target_ordinal`。这些来源与位置由 AgentData 在转换事务中保存，不由诊断读取重建；新输入即刻准备后仍可按实际输入 ID、Execution ID 核对原位置与版本链。当前 trace 快照只能说明当前状态，不能代替旧转换事实。超过上限时 `transformations_truncated=true`，调用方不得据截断结果宣称完整。
- `transformations_available` 表示后端是否支持读取这类事实，不代表历史完整。滚动加载期间旧 Execution 后端没有相关字段时，`trace.positions=null`、`transformations_available=false`；后端支持但没有记录时返回空列表，仍不能证明历史没有发生转换。外层事务隔离条件不满足时诊断读取失败，不降级为混合时点。该读取不执行模型、不触发转换、不补写历史，沿用现有 Resident 可见性和实际模型输入读取资格，不授予全局运行设置权限。
- Codex 历史摘要使用实际导入器相同的序列化规则，提供 SHA-256、序列化字节数、原生 item/内容分段类型计数、消息文字 UTF-8 字节数和图片 URL UTF-8 字节数。图片 URL 大小包含 data URL 编码开销，单独统计，不当作普通文字大小或解码图片大小。工具映射按 call_id 统计调用及返回，最多列出 200 个 ID，超出时标记截断，并保留全量未配对计数。无法按 Codex 结构转换的有效窗口明确标记 unavailable。
- `fork_deliveries` 返回最新至多 20 条请求/确认事实及是否还有更早记录：目标原生 Session、请求或 acknowledged 状态、记录的历史摘要、从其冻结 Segment 引用重新计算的摘要及是否一致。requested 状态不会被推断为已确认。请求和确认中的同一冻结历史只计算一次。
- 返回内容不含原始 prompt、工具参数或输出、图片地址/Base64、压缩原文或加密正文。摘要用于核对实际持久化与交付边界；模型回答是否正确仍由真实对话验证。


### 输入送达与执行工作关联

GetConversation.input_states[] 与 GetInputState.input_state 的每项提供独立的 work：state 为 queued、running、completed、failed、cancelled 或 unknown；kind 为 primary、appended 或 unknown；execution_id、primary_message_id、provider_turn_id 在有可信关联时提供；delivery 为 pending、delivered 或 unknown；updated_at 为对应执行/送达事实时间，缺证据时为空字符串。

原 state、updated_at、reply_message_ids 和 in_context 保持原语义。特别是已送达的追加输入可同时具有 state=completed、work.state=running；in_context 只说明上下文可见性。工作中的绿色标识只据 work.state=running，不据 Resident 整体 busy 或最新模型会话推测。

AgentData 拥有执行输入与 Provider 追加受理的持久关联。追加输入通过 source Segment、provider_input.append 派生及 control 身份锁定原 execution/Provider turn；只读同一执行的真实主输入和状态，不能沿 primary_message_id 的最新重试串到新 execution。同一 execution 的 Provider turn 已改变时，保留已证实送达事实，工作状态回 unknown。历史无可靠证据不得回填猜测；未送达的失败控制不能成为旧轮接纳凭据。

Society 拥有尚未预留 Execution 的精确 Mailbox 队列与撤回事实：确有持久队列项的输入可显示 work.queued，execution_id 可缺省；新发生的正式队列撤回保存 queue_withdrawn 原因并显示未送达的 work.cancelled。合并队列撤回在同一事务为尚未终止的原成员冻结 queue_withdrawn 与原 group 因果及撤回时间；成员不追随群体将来的重试，已有独立终态不覆盖。仅历史 state=queued/cancelled 本身不足以推断。读取只组合事实，不改旧输入终态。正式撤回导致原输入退出 Chat 的既有行为保持；GetInputState 可读取取消回执，不为显示 work 而恢复已移除正文。

work.updated_at 不借用追加输入已不再变化的顶层更新时间，也不使用读取当前时间。更新经既有 resident.input.state.changed 事件发布，payload.input_state 可保持原 completed 而 work 推进；Conversation 持久 cursor 为事件顺序，状态失效为 unknown 时同样推进 cursor。调用者按 cursor 应用事件，跨 execution/turn 不以墙钟比较归属；已知状态遇缺 work 的旧增量应重读快照，不以旧顶层 state 覆盖 work。无需新增轮询或 HATG 指令。

事件生产按精确 execution 的输入集合批量核验并合并未变化事实，覆盖同一 running 状态内新增 delivered 输入与 Provider turn 更换；不能只用 execution.status 是否变化做门控。旧执行的迟到通知不能把同一 primary 消息在新 attempt 的状态覆成旧终态；迟到 running 也不能使原执行终态复活。Family 转交保留实际成员的 work 与变化标识；Mailbox 不覆盖独立终态或已有独立关联。

Family 门面的 input_state.work 已沿正式 relay holder 投影实际承接成员，与同一快照的 message.relay.work 一致。工作标识以 work 为准，旧 input_state.state 或 message.relay.state 的 completed 不能覆盖 work.running；work.unknown 也不能回退旧状态或整体 busy 推断。缺 input_state.work 而有正式 relay.work 时可使用后者。此语义优先级不替代新鲜度校验：仍按同一 Conversation 的持久 cursor 更新，不跨 stream 比较 cursor，不让过期 work 覆盖更晚事实。

按当前页批量解析输入，禁止逐消息 RPC 或扫描全部历史；按 execution 推送时只读取该 execution 的索引关联集合。返回关联 ID、状态和时间，不返回控制输入正文或其他消息内容。

### `ListMessages`

**从快照或上一页给出的 `next_cursor` 继续读取更早的消息。**

- 参数：`resident_id`、`cursor`（必填，`GetConversation` 或上一页返回的 `next_cursor`，原样交回）、`limit`（可选）。
- 结果：`messages`，一页消息；`next_cursor`，还有更早的消息时返回。

### `ListResidentInputHistory`

**读取调用者本人在指定 Resident 会话中发过的输入。** 输入框按上箭头翻输入历史时，由服务端直接给出这份列表，浏览器不再从已加载的消息里推导（weihao 2026-10-16）。

- 参数：`resident_id`；`cursor`（可选，上一页返回的 `next_cursor`，不填时从最新的输入读起）；`limit`（可选，默认 50，最多 200）。
- 结果：`messages`，一页从新到旧；`next_cursor`，还有更早的输入时返回。
- 消息形式与 `ListMessages` 相同。范围是整条 trace，包括 Fork 继承的输入和清除上下文之前的输入。只取发送者等于调用者、并且消息来源是用户（或早期消息没有来源记录）的输入；Resident 投递、Mailbox 合并的请求和系统消息不在其中，其他成员的输入也不在其中。发送者用消息的 `actor_ref` 比较，不区分大小写。可见性与 `ListMessages` 相同；Family 门面同样投影。
聊天室输入历史使用 `ListChatroomInputHistory`（decision_3e063aac15b3）。

用 `ListMessageAttempts` 读按业务对象权限过滤的执行尝试。省略 `input_message_id` 时按尝试记录分页；提供它时，按当前权限读取对应正式输入及回复。

### `GetMessage`

**按消息 ID 读取一条消息。**

- 参数：`resident_id`、`message_id`。
- 结果：消息。尚在排队、未加入可见对话的消息也可以读取。

### `ListRecentRounds`

**读取一个 Resident 最近几轮往来，可以只看某位发送者。** Family Router 用它判断新消息是否延续这位发送者的上一轮（decision_44cf587c1721）。

- 参数：`resident_id`；`sender_id`（可选，只列出这位用户或 Resident 发来的请求，以及发给它的消息）；`before_message_id`（可选，只列出这条消息之前的轮次）；`limit`（可选，1–10，默认 3）。
- 结果：`rounds`，从旧到新。每轮包含请求的 `message_id`、发送者 `sender`、时间 `created_at`、请求开头 `request`、答复者 `answered_by`（Resident ID 与名称）、结果 `state` 和最后一条回复的开头 `reply`。门面上的答复者是请求被转交给的成员，结果与回复取自该成员；尚未转交的请求没有答复者。每轮的 `kind` 为 `request`。
- 给出 `sender_id` 时，Resident 或它的 Family 主动发给这位发送者的消息（`recipients` 中有它，decision_c524c775e0be）也算一轮：`kind` 为 `sent`，`sender` 是这段对话的 Resident（门面），`request` 为空，`answered_by` 是写这条消息的成员（门面上取来源的 `initiated_by`，其他 Resident 是它自己），`state` 为 `sent`，`reply` 是这条消息的开头。发送者回应这条消息时，Family Router 因此交回写它的成员。
- Family 门面上给出 `sender_id` 时，结果另有 `direct`：这位发送者在门面之外直接和成员的最近对话，从新到旧，最多 3 个成员。每项包含成员 `resident_id` 与 `title`、时间 `created_at`、`kind`（`request` 是发送者最后发给这个成员的消息；成员后来又直接发给发送者时为 `sent`，取成员那条）、`message_id` 和开头 `text`。门面转交给成员的副本不算。用户常在成员自己的 Chat 里继续讨论，再到门面说“按我们的讨论”，Router 由此知道指的是哪个成员（ET 10-12）。其他 Resident 或不给 `sender_id` 时没有这一项。
- 请求和回复各取前 400 个字符，超出时以“…”结尾；只在最近 200 条消息中查找。调用者需要能读取这段对话。

### `SearchResidentMessages`

**在一个或多个 Resident 的对话中按内容搜索消息。**

- 参数：`resident_ids`、`query`（必填，最多 200 字符）、`cursor`（可选，上一页返回的 `next_cursor`）、`limit`（可选，默认 50，最多 200）。
- 结果为命中列表 `results` 和下一页的 `next_cursor`。每个命中包含来源 `resident_id`、消息 `message` 和用于定位上下文的 `previous_message_id`、`next_message_id`。每个 Resident 分别检查调用者能否读取。
聊天室全文搜索使用 `SearchChatroomMessages`（decision_3e063aac15b3）。

### `GetMessageArtifact`

**读取 Agent 消息引用的一项产物。**

- 参数：`resident_id`、`artifact_id`。
- 结果：产物内容或元数据。

### `WatchConversationEvents`

**从指定游标之后订阅一个 Resident 的对话事件，按顺序持续送出新事件。**

- 参数：`resident_id`、`after_cursor`、`limit`（可选，每批最多送出的事件数）、`input_message_id`（可选，只送出这一条输入的事件正文，其他事件只保留游标）。
- 结果：首批立即返回已有积压，没有积压也立即返回检查点。事件项包含 `event` 与 `cursor`；每批最后一项为 `{cursor, high_watermark}` 检查点，cursor 是最后交付事件的位置，high_watermark 是服务端本次读取的最新位置。检查点不含 event，不作为消息应用。之后持续送出新事件，并在空闲时发送检查点心跳；重新订阅沿用最后交付的 cursor，不用 high_watermark 跳过尚未读取的事件。
- 只需读一次的调用者（例如页面断线后补读事件）取首批后关闭订阅：首批就是游标之后最多 `limit` 条事件加检查点。
- 事件包括消息追加与更新、输入状态变化、Resident 状态与可用操作变化、当前活动变化、等待队列变化和运行设置变化。等待某条消息执行完成的调用者订阅这些事件，不需要反复读取状态。
- 通知省略事件内容时（decision_c524c775e0be），接收方用这条指令补读完整事件。

### `GetSentPrompt`

**读取某条用户消息实际发送给模型的完整内容。**

- 参数：`resident_id`、`message_id`。
- 结果：发送给 Provider 的完整输入，包括组装进去的上下文。
- 实际发送内容可能包含系统注入的上下文。默认权限模板中 GetSentPrompt 要求对目标 resident_id 有 admin；对话可读性不授予这项管理权限。

## 模型上下文

### `GetSubagentRun`

必填 `resident_id`、`trace_ref`；`trace_ref` 使用子 agent 卡片给出的轨迹键。`offset` 默认 0，`limit` 默认 200、最大 500。调用者需能读取该 Resident 的对话；仓储只读取归属于该 Resident 的子 agent 轨迹。

返回 `run`，包含 `trace_ref`、记录总数 `total`、分页起点 `offset`、`updated_at` 和 `steps`。步骤按自身轨迹给出输入任务、工具调用输入及输出摘要、消息和思考；读取历史不启动子 agent。每个步骤带 `index`，即它在轨迹中的位置。跟随运行中的子 agent 时，调用者以第一个仍在运行的工具步骤的 `index`（没有时用 `total`）作为 `offset` 续读，并替换该位置及之后的步骤；正在运行的工具步骤随完成原位更新。工具输入每个字符串最多 4000 字、输出最多 6000 字，消息和思考最多 20000 字。

### `ClearContext`

**归档 Resident 当前的对话，清空模型上下文，Resident 身份保持不变。**

- 参数：`resident_id`。
- 结果：`cleanup_pending`（清理是否仍在进行）与 `operation_id`。
- 清理进行期间新到的消息继续排队，清理完成后按顺序执行。只归档本次操作开始时已有的消息。

### `CreateContextReset`

用 `resident_id` 安排当前工作完成后、下一条输入提交前清空上下文。保留排队输入及原账号。返回清理是否待处理、操作 ID、重启记录 ID 与状态，后续用 `GetSessionRestart` 或 `CancelSessionRestart` 查询或取消。

### `AbsorbContext`

**让目标 Resident 按需读取来源 Resident 的讨论与工作理解，并纳入当前工作。**

- 参数：`source_resident_id`、`target_resident_id`。
- 结果：来源与目标 ID、记录这次吸收的关系 ID，以及投递给目标的消息 ID。
- 系统把来源 Resident 的统一资源 ID 和吸收要求整理成一条消息，按 `DeliverMessage` 的规则投递给目标。目标依据自己的读取权限和当前职责选择需要的来源内容；来源可以是 Fork 派生节点，也可以是其他 Resident，读取范围由目标决定。

## 由 Resident 完成的 AI 业务任务

深读论文、解释或生成图表、评审论文、提取知识归档候选、生成 Repo 描述、生成 Lineage 草案、推荐 Resident、给 Notice 分类、语义搜索，这些工作都由某个 Resident 完成，不设专门的指令。业务从已登记的应用配置取得承担工作的 Resident，按 `resident_id` 投递。发起方用 `DeliverMessage` 带上 `business_ref`，再用 `GetInputState`（decision_2a3ff353165c）、`GetMessage` 和 `GetMessageArtifact` 读取进度与结果，把结果写回自己的业务记录。指令只负责投递和读取；任务怎样完成由承担工作的 Resident 决定。

## 回答 Resident 的请求

Resident 使用 Provider 原生的提问或批准工具时，这一轮工作会停下来等待回答。回答通过下面的指令送回同一轮工作；Resident 在回复中使用 Portal 提问卡片时，回答是一条普通用户消息，用 `DeliverMessage` 投递。

### `ListResidentRequests`

**列出一个或多个 Resident 等待回答的请求。**

- 参数：`resident_ids`（必填）。每个 Resident 按权限方案单独判定；列表范围由参数决定，不按调用者推断（decision_898b8911a2ff“声明格式”）。
- 结果：请求列表，每项包含 Resident ID、请求所在的消息 ID、请求种类（提问或批准）、问题与可选答案，以及发出时间。

### `GetResidentRequest`

**读取一个请求及其回答状态。**

- 参数：`resident_id`、`message_id`。
- 结果：请求内容与状态：`pending` 等待回答或送达确认，`answered` 已送回，`cancelled` 表示用户取消了请求，`expired` 表示请求已失效。

### `AnswerResidentRequest`

**回答一个请求，并把回答送回 Resident 正在等待的那一轮工作。**

- 参数：`resident_id`、`message_id`，以及 `answers` 或 `decision` 二选一。提问的 `answers` 按问题序号给出；审批的 `decision` 为 `accept` 或 `decline`。接受 MCP 请求时可带 `content`。
- 结果：与 `GetResidentRequest` 相同的请求状态：`pending` 等待送达确认，`answered` 回答或批准决定已送回，`cancelled` 取消已送回，`expired` 请求已失效。`pending` 时通过 `GetResidentRequest` 继续查询。
- 请求已经回答、取消或失效时返回冲突。回答按问题的定义校验；所在的一轮工作已经换代时拒绝，不会送到另一轮。

关闭一个不再需要的请求用 `CancelResidentRequest`，以所属 Resident 与请求消息定位，按当前请求状态核验。

## 参数名契约

下表列出 arguments 内的顶层参数名；必填条件、类型和枚举以 GetInstructionSchema 为准。调用信封中的 expected_version、idempotency_key 与 wait_seconds 不列入 arguments。

| 指令名 | arguments 参数名 |
| --- | --- |
| `DeliverMessage` | `resident_id`、`message`、`activation_policy`、`context_policy`、`execution_constraints`、`business_ref` |
| `PrepareResidentInput` | `resident_id`、`message`、`activation_policy`、`context_policy`、`execution_constraints`、`business_ref` |
| `ForwardMessagesToResident` | `target_resident_id`、`messages`、`note`、`activation_policy` |
| `ForwardMessagesToChatroom` | `target_chatroom_id`、`messages`、`note` |
| `GetConversation` | `resident_id`、`history_limit`、`input_message_id`、`include_context_evidence`、`include_inherited_context` |
| `ListMessages` | `resident_id`、`cursor`、`limit` |
| `ResolveMessageContextReferences` | `target_resident_id`、`reference` |
| `GetMessage` | `resident_id`、`message_id` |
| `ListRecentRounds` | `resident_id`、`sender_id`、`before_message_id`、`limit` |
| `GetDeletedResidentHistory` | `resident_id`、`section`、`ordinal`、`tool_index`、`expected_hash`、`tool_item_id`、`cursor`、`limit` |
| `GetMessageArtifact` | `resident_id`、`artifact_id` |
| `WatchConversationEvents` | `resident_id`、`after_cursor`、`limit`、`input_message_id` |
| `GetSentPrompt` | `resident_id`、`message_id` |
| `GetSubagentRun` | `resident_id`、`trace_ref`、`offset`、`limit` |
| `ClearContext` | `resident_id` |
| `AbsorbContext` | `source_resident_id`、`target_resident_id` |
| `ListResidentRequests` | `resident_ids` |
| `GetResidentRequest` | `resident_id`、`message_id` |
| `AnswerResidentRequest` | `resident_id`、`message_id`、`answers`、`decision`、`content` |
| `DeliverUserMessage` | `user_id`、`message`、`business_ref` |
| `ListMessageAttempts` | `resident_id`、`input_message_id`、`cursor`、`limit` |
| `ListResidentInputHistory` | `resident_id`、`cursor`、`limit` |
| `SearchResidentMessages` | `resident_ids`、`query`、`cursor`、`limit` |
| `CreateContextReset` | `resident_id` |
| `CancelResidentRequest` | `resident_id`、`message_id` |


格式名与版本须精确匹配当前规范值，校验规则见 decision_86aed2f0e8c7；本篇同时校验各字段和结构版本，不接受历史前缀或其他版本。
