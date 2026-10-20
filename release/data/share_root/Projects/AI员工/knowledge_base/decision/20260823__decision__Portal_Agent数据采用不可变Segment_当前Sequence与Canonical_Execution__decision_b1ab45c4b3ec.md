---
kb_id: decision_b1ab45c4b3ec
type: decision
project: AI员工
created_at: '2026-08-23'
version:
  number: 87
  created_at: '2026-08-23'
  created_by:
    user: ''
    agent: ''
  updated_at: '2026-10-19T19:59:40Z'
  updated_by:
    user: tfi-exported-user
    agent: resident_865c3c24f251
importance: 10
metaness: 3
ai_generated: false
links:
  source:
  - ${code_root}/tests/cases/test_session_transcript_from_trace.py
  - ${code_root}/tests/cases/test_subagent_trace.py
  - ${code_root}/tests/cases/test_live_trace_ingress.py
  - ${code_root}/management_common/provider_subagent_runs.py
  - ${code_root}/management_portal/codex_subagent_cards.py
  - ${code_root}/tests/cases/test_interrupted_turn_continues.py
  - ${code_root}/tests/cases/test_recovered_turn_item_identity.py
  - ${code_root}/tests/cases/test_claude_compaction_projection.py
  - ${code_root}/management_portal/execution/claude_transcript_compaction.py
  - ${code_root}/management_portal/execution/codex_rollout_history.py
  - ${code_root}/management_portal/execution/agent_data_store.py
  - ${code_root}/management_portal/execution/agent_data_projection.py
  - ${code_root}/management_portal/execution/runtime.py
  - ${code_root}/management_portal/execution/recovery_messages.py
  - ${code_root}/tests/cases/test_execution_layer.py
  related:
  - decision_6b9e42f1c8a7
  - decision_3948ce9b9443
  - decision_3e8c2a7f91d4
  - decision_ef1d6c925955
  - decision_91eb3626d323
  - decision_8ac0b5a6c0e5
  - decision_5bf5bfbd52cb
  - decision_71aa039e3211
  - decision_5c62deb8253a
  - decision_c47a13e8b962
  - decision_6ad0384e020d
  - decision_25eb93d803a4
  - decision_425e428410e0
  - decision_2fe6fd5367fb
  supersedes: []
  derived_from: []
---

# AgentData 的当前内容轨迹、不可变修订与运行记录

AgentData 保存 Agent 工作使用的内容。Segment 是不可变的内容版本，trace 是当前有效版本的有序引用。Portal、Agent Harness 和 Provider 共用这条内容轨迹；聊天页面从当前内容计算展示，Provider 会话从当前轨迹选择上下文窗口。

## 内容、顺序与归属

- Payload 是正文原始字节的唯一存储，以 SHA-256 共享相同字节的物理存储；Segment 只保存 content_ref、校验值和内容类型，不再内联复制正文。
- Segment 保存一种内容及其作者、角色、来源、执行、时间等事实。内容与身份不可覆盖。相同正文的两次独立事件保留独立 Segment；读取上下文不按正文去重。
- Segment Relation 保存版本转换、压缩、合并等来源关系，包含操作身份与执行者。
- Sequence 只保存有序 Segment 引用。Agent 通过 trace_sequence_id 拥有一条 Sequence；sequence_kind 和 parent_agent_id 属于 Agent。主 Agent 与 Provider 子 Agent 分别拥有自己的轨迹，子 Agent 的直接父级未知时保持为空，等待真实证据补齐。
- Provider Session 保存 Provider、路由和原生会话身份；Binding 保存稳定绑定与系统指令注入的进度。Execution 保存运行状态、用量和对应 Provider turn。AgentData 的 executions 永久保留，供 Chat 底栏查询历史用量，不按时间清理。

## Resident 身份与轨迹的生命周期

**每个成功登记的 Resident 都有同 ID 的主 Agent 和一条可以为空的 trace。** SocietyService 登记身份时调用 AgentData 的幂等创建入口，完成 Agent 与 Sequence 后才将登记标为可用并返回成功。普通创建、Fork、个人入口、Project 初始化和登记恢复遵守同一规则；运行入口核对这条关系，避免首次产生模型内容时才发现缺少轨迹。没有 AI 内容的 Resident 仍拥有普通空 trace。

**删除 Resident 保留同一身份和 Agent 的工作历史。** Resident 身份永久保留在 `residents`，`deleted_at` 和 `deleted_by` 记录删除事实。普通列表、调度和消息投递只读取未删除身份，历史读取使用显式入口并检查权限；原 Lineage 或 Workspace 已不存在时，也不编造缺失字段或重新创建运行目录。Portal 不提供回收站、恢复或清空回收站流程，也不再维护 `resident_deletions` 这套第二身份表。Agent、trace、Segment、Provider 会话和 Execution 历史继续使用原 ID，不保存 Resident 删除状态。删除与消息、info 和 Job 的录入在事务中协调，旧服务快照不能向已删除身份重新录入工作。

**父 Agent 字段仅表达 Provider 子 Agent 的直接父级。** `agent_trace` 的 `parent_agent_id` 为空；`subagent_trace` 可以指向真正启动它的父 Agent。Portal Fork 的来源在 Resident 派生关系中保存，两端都是独立主 Agent。父级未知时不凭标题、目录相似或共享内容推断，已知父链不得自指或成环。

## 首次包装与当前版本

用户、Resident 或系统提交的消息先经过规则化包装。包装完整保留正文，包括前后空白，不改写内容。首次形成 Segment 的是包装后的内容；没有单独存入 trace 的原始正文副本。消息身份、角色、作者、投递关系和附件信息是通信事实。metadata.message 保存这些事实，正文只由 Segment 的 Payload 提供；执行用量从 Execution 读取。消息身份统一使用 message_id，作者统一使用 author_id。聊天服务在 Execution 结束后，把 Scheduler 已记录的整轮用量附于最后一条有效回复；成功时选 Agent 回复，失败或停止时选终止回执，每次执行只显示一次。Provider 中途给出的 final_answer 后仍可续跑，因此 phase 不决定统计的挂载位置。选择基于该 Execution 的完整当前轨迹，分页窗口和截断分支不另选终点；运行卡片及 Harness 输入不参与选择。Execution 用量中的 api_id、model 来源字段保留，供业务产物记录实际模型归属；普通进度消息不重复显示用量，未知 Provider 原始字段不透传为聊天属性。

例如正文为 content 1，首次内容为 `<user-message>\ncontent 1\n</user-message>`。执行准备增加上下文或 Harness 改写内容时，形成新的不可变 Segment，并在同一事务中替换原 trace 引用、记录来源关系：`[s2] → [s3]`。原版本可供关系审计，当前 trace 只保留 s3。

多条输入合并时，在第一条被消费输入的位置保留合并结果，移除其余被消费引用。其他消息的位置和独立事件保持不变。Fork 已经引用的旧版本保持不变，父 Agent 的后续加工只修改父 Agent 的 Sequence。投递回放依据同一个输入、执行和操作身份保持幂等。

转换前的位置必须在转换事务中记录，不能从事后的当前轨迹推断。`transform_agent_segments` 持有该 trace 的行锁时，在写入新 Segment、来源关系及替换当前引用的同一事务中，以既有 `agent_operations` 的 `trace_transformation` 类别保存不可变事实（`tfi.trace-transformation.v1`）。它记录 `trace_id`、原 `operation_id` / `operation_type`、`execution_id`、`recorded_at`，以及按原位置排列的 `sources[{ordinal, segment_id, message_id, content_sha256}]` 和 `target_segment_id` / `target_ordinal`。`recorded_at` 沿用转换事件的时间参数，未给出时取当前 UTC，不是数据库提交时间。目标保留第一条被消费输入的位置；这些 ordinal 表示转换当时的位置，不会随之后的重排更新。

该事实不复制正文、完整 metadata、附件或 Provider 请求，也不成为内容 Segment。记录失败时整个转换回滚；重放已完成转换不改写原事实，旧转换缺少记录时也不回填猜测。它随 AgentData 长期保留，不受 Scheduler 的确认清理或终态回收影响。首次接纳输入后立即完成转换的路径同样留证，无需轮询捕捉短暂的原引用；这只证明已记录的转换，不保证历史未记录操作或 Provider 内部加工可还原。

显式上下文诊断在同一个只读 `repeatable read` 快照中返回当前 trace 位置、当前内容统计、有效窗口、Fork 导入与转换事实；若外层事务不能满足该隔离级别与只读条件，读取失败，不返回混合时点。当前位置包含 trace 身份、采样时间与 Sequence 更新时间、按 ordinal 排列的 Segment 身份和摘要、实际消息 / Execution 关联，以及由该 Segment 的原 Execution 关联取得的 Provider 会话和 turn 身份、Segment 起止字段。Sequence 更新时间不是 CAS 版本令牌，也不覆盖每项原生身份的变化。不从 Resident 当前配置推断历史原生身份。每次最多返回最前 2,000 个当前位置和最新 2,000 条转换事实，分别标明截断；缺少旧转换记录不能解释为没有发生转换。正式授权读取及字段定义见 decision_2fe6fd5367fb。

## 聊天投影

Chat 展示策略由 Portal 的 conversation/message projection service 拥有。默认读取通信消息及运行通知；其他前端可以选择不同角色或内容类型。Segment、Trace 和运行记录均不保存 hidden_from_chat、show_in_chat、send_to_ai 等消费者资格标记。

已完成输入的回复优先取其明确标记为 `final_answer` 的模型消息；重放或延迟到达的 commentary 不覆盖该最终答案。同一输入有多个最终答案时取轨迹中最后一个，没有最终标记时才取最后一条模型消息。失败或停止输入仍取结束该输入的通知。选择只读取已有通信事实，不改写消息身份、轨迹顺序或正文。

聊天始终解包当前 Segment。包装完整时返回其中的当前正文；包装不存在、被破坏或无法确定地解包时，返回当前完整内容。解包失败不报错，不恢复转换前的正文。合并后的 trace 只有一个结果，聊天也只显示这个结果。消息的旧正文不保存在 metadata 中供显示层回退。

输入投递重放沿已记录的转换关系核对不可变的已接纳版本，返回当前版本供聊天显示；不会把旧版本重新放回 trace。未曾接纳的新正文冒用原消息 ID 仍然是身份冲突。模型和 Harness 的展示通知引用已采集的当前内容，通知自身不再创建一份正文。附件等运行产物跟随当前通信身份投影，不写成额外的输入或模型输出。

会话摘要可在内存中按 actor、过滤规则和 Sequence 分别复用。每次读取在同一只读一致性快照中核对轨迹版本，新增、替换、通知、清空或切换轨迹后立即重新计算；相同时间戳和相同长度不代表同一版本。写事务内不使用或填充此缓存，避免回滚污染。缓存限制条目数、请求身份总量与存活时间，返回独立副本。Mailbox、完成回执和未读签名继续读取当前业务事实，不纳入内容摘要缓存。

所有 Chat 读取都读消息定位投影 `chat_message_positions`，不逐条解析 Segment JSON。投影为 trace 中每条通信消息和运行通知保存一行：位置、Segment 类型、消息 ID 和类别，发送时间、作者、`business_ref` 和收件用户（小写的 user ID），以及这条消息回复哪个输入、能否作为回复（不是 Harness 输入，也不带工作卡片）。`chat_actor_activity` 为每条 trace 和每个人保存他们在最近一次清空之后最后一次发言或被点名的时间，Mailbox 按它排列会话，Family 门面转发的副本不算发送者的活动（decision_44cf587c1721）。Chat 显示投影中的每一行，只有一个例外：final_answer Segment 带的消息已由同一 trace 中更早的 Segment 带过时不显示。读取需要 metadata 或正文时，按行的来源逐条从 Segment 或通知取回。用户消息数、最新用户消息与作者、actor 的请求、发给 actor 的消息、最近活动、按消息 ID 查找、翻页、回复窗口、消息头和 Fork 计数都只读这两张表；按消息 ID、发送者和收件用户的部分索引支持跨 trace 的查询。输入的最终回复先从 Segment 关系找出包含它的 Mailbox 组合消息，再在投影中按 trace 和回复目标找最后一条回复。

追加 Segment、放置通知、替换、清空和重排在写入它们的同一事务中维护这些行和最近活动，并把这条 trace 的同步标记设为 Sequence 行当前的 MVCC 版本。读取先核对所读 trace 的标记：标记落后时（某个写入者没有维护投影，或 Sequence 行被改写），在轨迹行锁内从 Segment 和通知重建这条 trace 后再读；先按消息 ID 查找再写入的操作在同一把锁内完成这一步。Mailbox 列表在开始时一次核对所有 trace，各页共用。这两张表属于 AgentData schema，新建存储直接创建，schema 检查要求它们的列和索引齐全。

Execution 的最终回复在写入时选定，记入 `executions.final_message_id`：结束 Execution 的写入和给它追加消息的写入从 trace 本身选出这条回复，因为它们持有 Execution 而不是 trace 的锁；空串表示终态 Execution 没有可显示的回复。消息只按这个指针显示用量，读取时不再补算。

翻页和已读位置使用顺序位置。原位替换保留位置；插入和移除成员时，后续内容与操作位置在同一事务中一起移动，保持相对顺序。位置允许存在空隙，不能作为永久内容身份。正文按页批量读取，消息头和计数不加载整条轨迹或正文。服务层可把运行通知按记录的位置与内容消息合并显示；通知的位置不会成为 trace 的 Segment 成员。

## 运行记录

纯 UI 卡片、资源修改卡片、执行统计、输入接收回执、历史注入回执及清空动作存入运行记录，不形成 Segment。agent_operations 保存操作身份、类别、Agent、会话、时间与事实；operation_positions 保存操作相对 Sequence 的位置。它们记录发生过的动作，不保存逐条消息的显示开关。

清空动作结束前一个聊天窗口，旧内容仍然可供审计。调用方必须在发起清空时捕获并提供明确的 trace_position，运行记录保存这个位置，晚到的投影不能掩盖清空后提交的消息。上下文已经被 Provider 接收的回执由 Binding 保存；Execution 永久保留运行状态与用量。

Harness 实际送给模型的 continue、提醒等文本属于模型内容，照常形成 Segment。耗时、特性评估报告、展示用 diff 卡片等运行信息由对应运行记录保存。

## Provider 上下文与 Fork

Provider transcript 和当前 context 都从 Agent 的当前 trace 读取，没有额外维护的一套内容 Sequence。会话开始接收的上下文与执行范围提供窗口边界；当前 context 从窗口内最后一个实际压缩段开始。读取不沿派生图寻找“应该发送”的版本，也不比较正文来消除版本重复。

持续会话只接收这次新增的输入。会话重建、切换 Provider 和 Fork 使用所选择窗口的 Segment 内容。Fork 创建时把选定有效窗口的 Segment ID 有序写入子 Agent 的 trace，再创建独立模型会话；最近 N 轮仅引用选中的轮次。父 Agent 后续追加或转换不改变子轨迹，未开始工作的嵌套 Fork 直接读取自身 trace。上下文接收事实通过 record_context_received 保存为运行记录，内容必须已经按顺序进入 trace；缺少成员时报告错误，不在轨迹头部补插。Resident 不再另存初始化材料或接收状态副本；首轮交付根据自身 trace 和当前会话的接收回执决定。全新会话成功替换后记录 provider_context_reset 及位置，只清空后续模型窗口，保留聊天历史。

窗口按尾部分页读取，遇到最后一个压缩段即可结束，不为一个小窗口读取整个历史。只存在不透明压缩检查点而没有实际替换内容时，跨会话恢复明确报告内容不可恢复，不从 Provider 会话日志补齐。

Provider 子 Agent 的输出、工具、输入和压缩属于各自轨迹；父轨迹保留父模型实际收到的调用与返回。子 Agent 的详细上下文不混入父 Agent 的重建窗口。

### 运行期间的单入口采集

Provider 的模型输出和工具结果只从运行中的事件通道进入 AgentData。后端适配器把通知、SDK 回调和运行中的 WebSocket 事件转换为完成事件；AgentDataProjector 是这些事件的唯一内容写入入口。结束或取消时不扫描原生持久文件补录普通内容。Codex 技术恢复重放是一个明确例外：适配器可从原生轮次读取消息的原生身份，用于重放已有规范事件；它不把未观察到的内容作为新事件导入。

- **收到完成事件即保存。** 完成的消息、reasoning 和工具结果在 turn 仍运行时写入。流式增量和进行中的累积快照只作为临时活动，等片段完成后才形成不可变 Segment。执行结束不能把未收到结果的工具伪装成完成记录。
- **运行期间未取得的内容暂缺。** 后端没有通过当前运行接口提供的内部提示、隐式工具调用、后台通知、加密思考和子 agent 内部步骤，本阶段不从原生文件补齐。原生会话仍可继续运行，但 AgentData 的可恢复历史只包含实际采集到的内容，不宣称完整还原后端窗口。统计用量和 Provider 压缩状态仍遵循各自协议，独立于普通内容采集。

当前适配器使用以下运行通道，转换后的完成事件都交给同一个 AgentDataProjector：
    
| Provider | 运行来源与转换方式 |
| --- | --- |
| Codex | 使用子 thread 的通知，以 thread/started 的父 thread 信息、spawnAgent 的 receiverThreadIds 或父 thread 的 subAgentActivity（started）建立父子关系。Codex 0.162 只向加入了子 thread 的连接推送它的通知，所以父 thread 报告子 agent 开始时，客户端在后台用不带覆盖设置的 thread/resume 加入这个正在运行的子 thread，报告结束时退出；加入之前的少量步骤暂缺。提前到达的子条目进入有界缓冲，关系明确后路由，支持嵌套子 agent。只消费能追溯到本次活动根执行的事件，不把其他 thread 的通知混入父回复。 |
| Kimi | 在提交 prompt 前完成 /api/v1/ws 的 session 订阅，不设置只看 main 的过滤器；按 agentId 分流主 agent 与子 agent。assistant.delta、thinking.delta 在片段边界形成完成条目；tool.call.started 与明确的 tool.result 形成工具段，保留结构化 output 和 isError。子 agent 先开始自己的 turn，发起它的 agent 随后发出 subagent.spawned，其中 parentToolCallId 是启动它的 Agent 工具调用、parentAgentId 是直接父 agent；子 agent 的条目在 spawned 到达前暂存，到达后以这次调用为父调用、按已知链路给出祖先，轮末仍未见 spawned 时不带父级写入。子 agent 的 turn.started 带 prompt 时记录这段实际可见的输入。Kimi 在每个 session 内从 agent-0 起为子 agent 编号，trace_key 因此由 session ID 与 agentId 组成，同一 Resident 换 session 后的子 agent 不会并入旧轨迹。 |
| Claude | 使用 SDK 的 AssistantMessage、UserMessage；父调用标识决定子轨迹。普通工具及 ServerToolUseBlock/ServerToolResultBlock 都进入同一工具事件转换，服务端工具的结构化结果与错误保留。 |
| DSH | 使用现有 session/event 回调；完成的 assistant/message 提供文字与可见 reasoning，tool/result 保留全部结构化结果；后端发出的 system/developer/user 消息保存为 input 段，角色放在元数据中。Portal 自己的输入回显不再重复录入。 |

Kimi 快照只用于运行状态、审批与控制，不再提供模型或工具正文，最终回复也不能从历史消息回退获取。断线后仍从同一 WebSocket 通道按 epoch/seq 续订；游标用于协议重放，不用于按正文合并事件。volatile 增量无法重放，断线、重新接手和 resync 都使未完成文本失效，等新的明确步骤边界后才采集完整文本；没有完整实时最终回复时明确报错。相同步骤编号的重试由边界序号及片段序号区分，不复用不同事件的身份。

当前仍有明确缺口：Provider 未公开的内部上下文、Kimi 默认不可观察的 context 事件及已被协议剔除的附件信息、未返回的加密思考原文、连接中断期间丢失的文字增量，以及父执行采集已结束后才产生的后台子 agent 内容（Claude 的轮次会等自己启动的后台子 agent 结束，见“子 agent 卡片”；其他 Provider 仍有这一缺口）。工具结果只保留 Provider 返回的内容，不能恢复其上游已截断部分。这些缺口没有文件补录任务。统计用量与压缩状态沿用独立协议，部分信息仍在轮末读取。
- **事件身份与正文存储分离。** Provider 提供稳定事件 ID 时，同一已完成事件重复投递沿用相同 Segment ID，正文、类型和元数据冲突一律拒绝，内存缓存也不能绕过校验。Provider 没有事件 ID 时，每次观察分配独立身份，不以正文或密文哈希推断两个事件相同；因此无 ID 的传输重放无法自动识别为同一事件。不同消息和思考事件可共享正文 Payload，但不能共享事件身份。不建立第二套记录再按正文或 native_id 匹配。
- **子 agent 独立路由。** 运行事件明确携带父调用与子 agent 身份时，投影层使用同一写入规则，按 trace_key 路由到 subagent_trace；相同条目 ID 在不同子轨迹中不会冲突，子步骤不进入父轨迹、context 或父回复。Claude SDK 的 parent_tool_use_id、Codex 的父子 thread 关系和 Kimi 的 agentId 与 subagent.spawned 决定实时路由；各通道没有提供的步骤暂缺，不读文件补齐。多成员调用使用不同 trace_key。适配器按请求计数的临时条目在恢复请求时继续使用请求范围，工具自带 ID 保持不变。
- **子 agent 卡片。** Resident 的模型等待子 agent 时，Chat 在这一轮的活动上方为每次运行显示一张实时卡片：类型、任务、前台或后台、正在做的一步、最近几步的成败、步数和计时；多个子 agent 并行时合为一组，更新只替换卡片里变化的部分。卡片上的类型按作用显示各 Provider 的内置类型：Claude Code 的 general-purpose 与 Codex 的 default 为“通用”，Explore、explore、explorer 为“探索”，Plan、plan 为“规划”，Kimi Code 的 coder 为“编码”，Codex 的 worker 为“执行”，鼠标悬停显示原名；Provider 或用户自定义的类型照原名显示；Provider 没有给出类型时只写“子 agent”，不补任何 Provider 的类型名。适配器在运行开始和每次步骤变化时用 `subagent/progress` 事件给出运行中的卡片（最后 6 步）。它只是 Execution 活动：每次运行一条 kind 为 subagent 的活动，新进度把它移到末尾，仍在运行的卡片不会被较新的活动挤出显示窗口；它不写入 AgentData，也不成为消息。模型拿到子 agent 的结果时，Chat 显示这次运行的结果卡片：子 agent 类型、任务、前台或后台、状态、用时和步数，下面默认折叠为一行，只显示结果开头的纯文字和用过的工具；展开后才显示完整结果和最后 40 步的简要步骤，展开状态在消息重绘后保留。同一条活动随之以完成结束。Claude 的 Agent 调用没有写 `run_in_background` 时，以 CLI 返回的后台启动结果判断前台或后台。结果卡片是运行记录中的 artifact，不进入 context。适配器用 `SubagentRuns` 从实时事件跟踪主 agent 启动的每次运行：Claude 守护进程在 Agent 调用返回结果时给出前台运行的卡片；后台运行的卡片随 CLI 的 task_notification 系统消息给出，结果取子 agent 最后一条消息，用量取通知。CLI 只在通知到达一个仍在进行的轮次时才把它作为 user 消息回放到流里，为取通知自己开始一轮时不回放；回放与系统消息都到达时，同一次结束只出一张卡片。守护进程重启前开始的运行没有卡片。Claude 的后台运行让 Portal 轮次保持打开：CLI 报告本轮结果后，只要本轮启动的后台运行还没结束，或已结束运行的通知还没被模型取走（CLI 为每条通知各开一轮，或回放进正在进行的一轮），守护进程就继续读取；没有运行在进行时最多再等 15 秒，CLI 仍未开始新一轮就以最后一次回答结束；CLI 为取 task-notification 自己开始的那一轮仍属这个 Portal 轮次，卡片、续写的回答、文件改动和用量都记在本轮，先前的回答作为过程文字保留。等待期间仍可插话；停止本轮时守护进程对仍在运行的后台运行调用 stop_task，卡片以已停止结束。CLI 只报告任务 killed 且等待窗口内没有开始新一轮时，本轮以最后一次回答结束；Kimi 在发起它的主 agent 收到 subagent.completed、subagent.failed 或 subagent.cancelled 时给出卡片；Codex 后端按 thread 跟踪：父 thread 的 subAgentActivity 开始时出运行中卡片，子 thread 每一步更新，subAgentActivity 结束（completed 或 interrupted）时给出卡片，类型取子 thread 的 Codex 角色或昵称，任务取任务名，结果取子 agent 最后一条回复；Codex 的子 agent 不阻塞模型的调用，卡片都标为后台；DSH 在父会话的 subagent 调用开始时出运行中卡片，子会话（由桥接插件按任务描述对应到这次调用）每一步更新，调用返回结果时给出卡片，类型为“通用”，结果取工具调用的返回内容，DSH 的子 agent 只在前台运行。Claude 以外的 Provider，Portal 停止跟随这一轮之后才结束的后台运行没有卡片。子 agent 再启动的子 agent 只有自己的轨迹，不出卡片。Portal 按能力协议处理子 agent，不看 Provider 名称：声明 `subagents` 的 Provider（Claude、Kimi、Codex、DSH）在 Chat 中有“允许开启 sub agent”开关，声明 `subagent_events` 的适配器给出上面三种事件；DSH 两项都从 2026-10-11 起声明，此前没有开关也没有卡片。两项能力的定义、字段口径和适配器测试的登记见 `decision_5c62deb8253a` 的“能力一览”。
- **子 agent 卡片的交互。** 卡片还带这次运行的完整任务 `prompt`（最多 8000 字）、它改动的文件 `files`（取写类工具的路径参数和补丁头，最多 50 个，带工具名和次数）、token 用量 `usage`（total、input、cached_input、output、reasoning 和工具调用次数）、每一步的起止时间，以及子 agent 自己那条轨迹的键 `trace_ref`（活动脱敏会遮住以 key 结尾的字段，所以不叫 trace_key）。用量来源：Claude 取 CLI 的任务进度、Agent 调用结果和 task-notification，Codex 取子线程的 tokenUsage，Kimi 累加子 agent 每步的 usage 并以 subagent.completed 的用量为最终值，DSH 由桥接插件把子会话每次请求的用量作为 `thread/tokenUsage/updated` 放进 `subagent/event`，卡片累加这些请求（input 含缓存读写）；OpenCode 桥接按子会话每个 step-finish 的 tokens 累计，以 `usage/updated` 放进 `subagent/event`。Codex 的多 agent 把任务正文加密后发给子线程，Portal 读不到，Codex 卡片和窗口显示“这个 Provider 没有给出任务全文”。运行中卡片显示当前这一步已用的时间、token 和改动文件数，并有“详情”“纠偏”“停止”三个按钮。点卡片或“详情”打开这次运行的窗口：任务、改动的文件和全部步骤都来自 `GetSubagentRun` 读取的子 agent 轨迹；工具调用展开后是完整输入和输出摘要，失败的步骤标红并默认展开，路径和资源 ID 可以点开预览。运行期间窗口每 2.5 秒从第一个仍在运行的步骤（没有时从记录末尾）续读，读者滚到底部时跟随最新一步。“停止”只停这一个子 agent，父 turn 继续：读者确认后 Chat 调用 `CancelSubagent`，Execution 把 `subagent_stop` 输入控制交给 Provider；按钮只在 Provider 声明 `subagent_stop`、读者也能停止本轮时出现。“纠偏”把“关于子 agent「任务」：”放进输入框，读者写完发送后，如果 Provider 接受轮中输入，这条消息立即从队列注入当前轮。运行结束时，结果卡片从运行中卡片原来的位置移入并高亮一次；页面隐藏时标题显示“✓ N 个子 agent 已结束”，回到页面后恢复；页面可见但 Chat 不在视野内时弹出提示。结果卡片展开后显示改动的文件和它收到的任务，并有“全部步骤”“引用到输入框”“复制结果”“保存为文件”：引用把结果开头作为引用块放进输入框；保存写入 Resident 自己 Workspace 的 `subagent-results/<结束时间>-<任务>.md`，内容是任务、结果和改动的文件。卡片里的结果被截断时，复制和保存从轨迹取子 agent 最后说的完整一段（轨迹里的消息最多保留 20000 字）。Claude 守护进程从 CLI 的 task_started、task_progress、task_notification 记下每次运行的 task 和用量，`subagent/stop` 调用 CLI 的 stop_task；CLI 只报告任务 killed、不回放通知时，守护进程在这时以已停止结束卡片一次，之后到达的通知不再出卡。Kimi 读取会话任务，找到 `parent_tool_call_id` 是这次调用的子 agent 任务并取消，被取消的运行以已停止结束。DSH 的桥接以父 agent 的身份取消这次 `subagent` 调用的子 agent（子会话还没对应到调用时，对应后立即取消），调用以 cancelled 和已有的部分回答返回；OpenCode 的桥接只 abort 这次 `task` 调用的子会话（子会话还没建立时，建立后立即 abort），调用以中止失败返回。这两个桥接在接受停止时先向 Worker 发 `subagent/stopping`，调用以失败结束时卡片标为已停止；这个事件只交给卡片，不进入这一轮的事件。调用拿到结果后，子会话迟到的步骤不再生成运行中卡片：被 abort 的 OpenCode 子会话会在调用失败之后再更新一次它正在运行的那一步。支持停止之前启动的 DSH 或 OpenCode 进程返回 `subagent_stop_unsupported`。Codex 的子 agent 在自己的线程里运行，停止时向子线程当前轮发送 turn/interrupt；从父线程外部中断时父线程收不到 subAgentActivity，所以卡片以子线程这一轮的 turn/completed（interrupted 或 failed）结束，这一轮迟到的事件不再生成新卡片。父线程用 followup_task 给已结束的子 agent 派新任务时报告 `interacted`，Portal 重新加入它的线程，这次运行另起一张卡片，只含这次的步骤、结果和用量：`tokenUsage.total` 是整个子线程的累计值，卡片减去这次运行开始前的用量（第一条请求的 total 减去 last）。Codex 把命令输出放在 `aggregatedOutput`、文件改动的路径放在 `changes[].path`，运行窗口的步骤据此显示命令输出和改动的文件。
- **Portal 自己送达的内容。** 输入、准备后的输入、一轮中实际送达的 Harness 提醒和恢复输入在送达时记录；不依靠后端文件反查。Fork 和跨 Provider 迁移继续引用已保存的 Segment，历史原生补录段保持可读。
- **统一的不可变校验。** 运行时对所有记录使用同一条写入和重放校验路径，不根据历史版本、native_id 或元数据缺失放宽约束。数据库不保存 native_id。结构整理只通过显式版本转换执行；运行入口不根据旧格式放宽校验。历史来源标签仍表示实际来源，无须旧采集器或兼容写入分支。



执行活动日志按 Scheduler 事件序号增量追加，并按原生轮次和条目身份去重。进程中缓存已写位置；首次接手或检测到文件变化时从日志恢复，正常追加不重读完整文件。过期日志清理受时间预算限制，耗尽预算后保留目录遍历位置，下次继续。

Provider 会话窗口先选出属于该会话的 Execution，再通过执行与 Segment 索引定位轨迹位置，避免逐条检查完整轨迹。

## Provider 思考内容

Provider 明确返回的、已经完成的思考（thinking 或 reasoning）条目保存为 `model` 类型的 Segment，并在 `metadata.kind` 中标记为 `reasoning`；Segment 类型和表结构不变。这里只保存 Provider 实际提供的数据，Portal 不推导、补写或另外去获取 Provider 没有返回的内容。

- **一个完成的条目对应一条 Segment。** 流式增量、累积的思考快照，以及没有任何内容或加密信息的空条目，不形成 Segment。生成中途失败或取消的内容不伪装成已完成，之前已完成的记录保留。
- **各 Provider 的来源。**
  - Codex：使用已完成的条目。实时接口没有提供的思考信息本阶段暂缺，不读取原生持久文件补录；恢复用的重建快照也不作为思考事件。Codex 默认请求 `show_raw_agent_reasoning=true` 与 `model_reasoning_summary=auto`，调用方显式设置时以调用方为准；这两个开关不能解密密文，也不能让不提供原文的模型提供原文。
  - Kimi：实时订阅中的 thinking.delta 在片段边界完整结束后生成；断线残片与历史快照不参与补齐。
  - DSH：从完成且未标记 interrupted 的 assistant/message 中提取可见 reasoning 内容。
  - Claude：从已完成的 AssistantMessage 中的 ThinkingBlock 生成。
- **内容格式。** 内容是 JSON：拿到原文时保存 `text`（可同时保留签名），拿到摘要时保存 `summary`。只有密文时保存 `encrypted: true` 标记，绝不把 `encrypted_content`、`redacted_thinking.data` 或它们的编码副本写入 Segment 或 Payload；摘要与密文同时存在时保存摘要和加密标记，不把摘要称为完整思考。`metadata.formats` 列出 `full`、`summary`、`encrypted` 中出现的形式，`metadata.format` 按原文、摘要、加密占位的顺序标出首要形式。
- **思考 token 数。** 只在 Provider 明确给出属于这个条目的非负整数时记录 `reasoning_tokens`，可接受 `item.reasoning_tokens`、`item.usage.reasoning_tokens` 或 `item.usage.output_tokens_details.reasoning_tokens`；没有时省略，不把整轮的 token 数分给某一段，也不按密文长度估算。
- **身份。** Segment ID 由执行范围、Provider turn 和条目身份共同确定；Provider 未提供身份时为每次观察分配独立标识，不以正文摘要判定事件相同。
- **用途。** 这些 Segment 加入完整轨迹和对应 Provider 会话的 context，Fork 时同样只建立引用。它们不会额外发给模型，不创建聊天消息，也不成为最终回复。加密占位只用于记录和检查，不能用来重建加密的原始上下文。有权读取原轨迹的调用方可以在轨迹或调试读取中看到这些记录和占位。


## Execution 的唯一身份与生命周期

agent.executions 是每次 Resident 执行的唯一长期记录。输入只在 Mailbox 排队时不创建 Execution；接纳输入开始准备时分配 execution_id，状态为 preparing。准备失败、取消、排队、运行和完成均更新同一记录。状态集合为 preparing、queued、running、completed、failed、cancelled，created_at 保存接纳时间，started_at 与 finished_at 表达实际开始和结束。

执行请求的服务类型与 operation 是同一 execution_id 的长期来源事实，与运行状态和用量一起保留。Scheduler 在 queued 提交的同一事务中写入 AgentData 的不可变 `execution_submission` 操作事实；它使用已有 `agent_operations` 存储，只保存经校验的 `service_type`、`operation`、原请求摘要和提交时间，不另建调度或执行身份。来源不能从 Provider capability、当前 Agent 配置、空输入或最终消息推断。准备阶段尚未形成提交事实、或历史请求已清理而未保存来源时，`service_type`、`operation`、`submitted_at` 均为 null；已有来源冲突不能覆盖。

`AgentDataStore.execution` 将该提交事实和 `execution_inputs` 的实际关系与长期记录一起读取，`ExecutionService.record` / `execution.record` 是按提交 actor 核验的正式读取入口。输入仅返回消息身份、角色和接纳时间；没有输入是事实，不是操作分类。此事实存储不向内容 trace 添加 Segment，也不复制 prompt、system instructions、原请求 metadata 或附件。

Scheduler 确认完成时仍清除完整请求和 checkpoint；其终态工作被回收不删除 AgentData 的来源与输入关联。长期来源不包含 prompt、system instructions、原始 metadata 或附件内容，也不形成另一套执行生命周期。操作名说明该执行提交的动作，不能替代 Provider 实际事件证明所有副作用。

agent.execution_inputs 保存本次执行实际接纳的输入身份。多条输入可以合并到一个 Execution，同一输入显式重新执行时关联新 Execution；仅重试提交或恢复同一执行时保留原 ID。运行中追加输入成功接纳后加入同一输入集合。输入状态查询以这些关系和执行终态为准，不需要另建 job、轮次收据或 invocation 执行记录。

Lineage Trigger Path 从自己的关联表引用 execution_id；Execution 不保存 trigger_path_id 或来源路径列表。Scheduler 在队列状态事务中提交执行的 queued、running 和终态；Lineage 输入协调者负责 preparing 与提交前的失败或取消。AgentDataProjector 保存正文、Provider 身份和用量，不能独立推进第二套生命周期。终态通知与界面投影读取这个共同事实。

## 聊天回复索引

聊天位置投影保存轨迹内消息的位置、回复目标与回复资格。读取输入的最终回复时，按轨迹、被回复消息、消息类型和倒序位置，通过索引分别选取最后一条 Agent 回复及运行通知，再遵循当前聊天规则归并；结果数量不随同一输入的过程消息数量增长。群体输入沿既有转换关系找到群体消息，重复 final 的隐藏规则、Fork 的轨迹归属和继续执行后的最终回复含义保持一致。

投影的同步标记必须与轨迹版本一致。标记落后的 trace 在下一次读取或写入时于轨迹行锁内从事实重建，不能只推进标记。时间列等物理结构转换若重写轨迹行，只能为转换前已验证同步的投影更新其版本标记，原本失效的投影仍需从事实重建。投影不建立另一套消息真值。

## PostgreSQL 结构与一致性

AgentData 通过 `agent_store_meta` 中的结构版本核对当前存储契约，直接维护已有 PostgreSQL 数据库。正文统一从 Payload 读取；按 Agent 检查内容时沿 trace 的 Sequence 成员定位，不建立按内容作者与时间排列的全局 Segment 索引。规范内容表为 payloads、segments、segment_relations、sequences、sequence_segments、agents；会话及执行表为 provider_sessions、provider_bindings、executions、execution_inputs；运行记录表为 agent_operations、operation_positions。agent_store_meta 保存结构版本。

Segment 类型为 input、model、tool、compaction、harness。工具和模型输出在完成时进入内容库，流式增量不覆盖已完成 Segment。纯 UI artifact 与 context_reset 不属于 Segment 类型。

内容插入、转换关系与当前引用替换在事务中完成。现有数据的结构整理直接在原库执行：先核对类型与转换关系，将运行事实移出 Segments，清除旧正文副本，再按已记录转换关系收敛当前引用。相同正文不能作为删除独立事件的依据。整理过程中保留不可变内容的审计关系，并核对已有 Fork 与聊天窗口。

AgentData 的健康查询核对数据库 schema 版本；需要内容、用量或条目数量的业务读取对应事实。调度器决定运行和重试，Provider 适配器解释原生协议，Chat service 决定投影规则，业务权限服务决定访问权限。

排队输入在 Mailbox 中也使用同一无损正文包装，读取时按规则解包；进入 trace 后删除 Mailbox 中的等待记录。群组递交在一个 AgentData 事务中将成员替换为群组，成员原文仅通过转换关系用于审计与重新排队，不重新成为当前 trace 条目。

## 完成事件与界面投影的顺序

所有 Resident 执行都以同一 Portal 输入身份建立正文替换及回复关联。原生会话传输选项中的 client_user_message_id 与 API 请求元数据中的 user_message_id 都引用该输入；录入时将其归一到同一关联规则。执行输入消费原输入当前引用，模型完成事件的 reply_to_message_id 指向该输入身份，业务服务据此读取结果。API 请求不能留下原输入与执行输入两个当前条目，也不能因 Provider 类型而失去回复关联。

完成的 Provider 事件是模型 Segment 的唯一写入来源。界面通知先到达时作为运行通知保存；完成事件到达后，在同一事务中用内容事件替换该通知的位置。重复通知读取当前内容，不再次写入模型正文。相同执行的多条真实 final 事件均保留在 trace，其他消费者可以使用不同策略。聊天服务投递最终回复时，在共享该回复身份的已捕获 final 中，按时间顺序选第一条正文与这条回复相同的，没有相同的就报错 `Captured message does not match the selected content`，不随便拿一条。比较之前，两边的 `portal-question` 代码块都先转成提问卡片再去掉：投递的回复已经把代码块转成了 `async_questions` 部分，已捕获的 Segment 仍保留代码块原文（`agent_data_store._answer_text`）。

模型输出中的提问卡片由聊天服务读取时解析。模型文字中出现的用户包装标签是输出正文，不执行输入解包。模型原始响应附件和用量由运行记录及 Execution 提供，消息元数据不复制正文或执行统计。多段文字与附件以结构顺序和文字长度恢复；Harness 改写后长度不再匹配时展示当前文字及附件。

一轮中追加的图像在实际送达时冻结到内容 Payload，后续 Fork 和投递重放不依赖原图片路径。历史结构整理依据已记录输入转换关系替换当前引用；旧模型的聊天副本必须同时满足执行与作者一致、事件时间或正式最终回复身份相同、内容校验一致，且映射为一对一，才建立转换关系并合并引用。单纯正文相同的独立事件保留。

Codex Fork 的空线程在后端丢失时，只能在该会话尚未接受任何输入的条件下从 PostgreSQL 冻结窗口重建。新线程接收首条输入前，以准备时的 Provider Session 身份进行事务比较并切换 Binding；并发替换或已有输入时拒绝重建。完成回合、Resident 状态与下一轮继续使用同一新线程，不能让模型成功后再因旧 Binding 冲突把输入标为失败。

聊天快照和输入终态的回复查询在消息投影中按所需消息身份和回复目标查找消息与运行通知，随后判断消息类型和关联 trace；不能为了一个输入解析全库通知正文。Family 按当前页的输入集合批量读取成员回复。AgentData 一起解析涉及的 trace、清空位置及各输入的回复边界，再读取每个输入各自限定的窗口；正文、执行用量和附加信息按返回的消息集合批量加载，同一内容只加载一次。每个输入独立限制回复数量并保持顺序，缺失输入返回空结果；清空后保留的最终回复按成员轨迹和消息身份批量读取。数据库往返次数随批次数变化，不能随页面历史轮数逐轮增加。查询先限定成员 trace 的位置，再按每个位置的内容或运行记录身份读取并判断回复；同一输入存在于多个 Fork 时，也不能把其他轨迹的通知逐条解析或按候选消息重复扫描。转换链按当前节点通过来源索引逐步展开，不能每一步扫描整张转换关系表。性能验收使用实际历史长度的聊天快照与 User Mailbox，而不只检查空样本。

直接在 Segment 中找消息的查询只剩群体与转换链、重复 final 的判断和 Execution 结束时选定最终回复，它们按消息身份查找。这个索引是部分表达式索引，PostgreSQL 不为部分索引的表达式收集统计，segments 因此为消息身份表达式建一个扩展统计（`CHAT_STATISTICS_SQL`）。缺少它时，一次查 8 个消息身份会被估成近 18,000 行，群体输入的转换链递归按这个估计为 UNION 去重准备约 180 万行的哈希表，每次批量回复查询固定多花约 20 ms；有统计时同一查询低于 1 ms。新 schema 建库时创建这个统计；已有库一次性创建后执行 `ANALYZE segments (metadata_json)`，此后由自动 ANALYZE 维护。投影重建时用 `segments_chat_idx`（只收录通信消息 Segment）跳过 trace 中的工具、推理等其他 Segment，不读取它们的行。

按消息身份读取消息头或单条消息时，在消息投影中按消息 ID 查找（`chat_message_positions_identity_idx`）。这个索引只以消息 ID 为键：一次读取同时列出多条消息和多条 trace 时，若 trace 也是键，PostgreSQL 16 会对两组取值的每一对组合各下降一次索引。

Chat 从 trace 最后一次清空之后开始，几乎每次 Chat 读取都要先确定这个位置。`operations_context_reset_idx` 只收录 `context_reset` 与 `provider_context_reset` 两类运行记录，确定清空位置时只核对这些记录，不必逐条回表检查 trace 定位的每个运行记录的类型；建立后 agent_operations 主键查找从每分钟约 750 万次降到约 1.7 万次。

执行终态在 executions 行锁内一次结算，后续普通写入必须与已保存的状态、时间、Provider turn 和用量一致。后台维护只按实际调度与准备状态结算仍未结束的记录；若 Worker 已先完成结算，应保留其终态与用量，跳过过时的维护快照，不发布重复变更通知。

## Segment 的请求起止时间

Segment 的时间字段为 `started_at`、`finished_at`，不再保存独立的 `created_at`。模型段的 `started_at` 是 Portal 在本地实际发出这一次模型请求的时间；`finished_at` 是本地收完响应或原生运行时报告该响应完成的时间。它们描述本地观察到的请求耗时，包含网络和远端排队时间。同一次模型响应产生的推理段、正文段共享该次请求的开始时间；结束时间采用对应输出完成或响应收完的观测时间。各段区间可能重叠，不能将它们相加作为整轮耗时。

原生 Adapter 只有 turn/step 开始、首字或消息创建时间时，模型段的开始时间保持 NULL。历史段同样只保留有依据的起止时间，不用旧创建时间回填模型请求开始时间。用户消息的发送时间属于 Message 事实，仍可在消息投影中使用；Segment 时间与消息时间分别承担各自职责。内容转换、压缩边界和 trace 顺序继续依据各自的事实或位置读取。

模型请求计时由 Portal 自有 Adapter 或传输观测器提供，随完成事件写入 Segment。重试后的成功响应采用实际生成该响应的请求时间；子 Agent 请求按其自身会话关联，不继承父 Agent 的请求时间。

计时是可选的本地观察，遵循 decision_25eb93d803a4 的“模型请求时间观察”和 decision_425e428410e0。Portal 自有 API 传输可复制实际请求边界，DSH 可由其既有 fetch 通路复制；Codex、Claude、Kimi 与 OpenCode 只有在既有原生事件给出可靠请求边界时才记录，当前缺少可靠 dispatch 事件的路径将 `started_at` 保持 NULL。已观察到的 Segment 完成时间不能与未知请求开始时间拼成请求耗时，Execution 总跨度、首 token、turn/step 开始也不能替代它。

新 Execution 不为计时启动代理，不改变模型 base URL、认证、代理环境、TLS、压缩、重试或 HTTP/WebSocket 路由，也不解析模型正文补取关联 ID。旧绑定会话所需的代理兼容代码不证明新请求启用了计时。全局开关只控制可选时间副本的持久化；关闭、读取设置失败或缺少关联都不改变业务执行，不丢弃输出或重跑请求。历史缺失值继续为空。

格式名与版本须精确匹配当前规范值，校验规则见 decision_86aed2f0e8c7；本篇同时校验各字段和结构版本，不接受历史前缀或其他版本。
