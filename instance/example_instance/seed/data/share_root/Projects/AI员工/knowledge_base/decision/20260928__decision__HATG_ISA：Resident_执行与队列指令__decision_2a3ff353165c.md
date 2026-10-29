---
kb_id: decision_2a3ff353165c
type: decision
project: AI员工
created_at: '2026-09-28'
version:
  number: 49
  created_at: '2026-09-28T10:45:00Z'
  created_by:
    user: tfi-exported-user
    agent: resident_2a1fc6cd272d
  updated_at: '2026-10-27T12:48:33Z'
  updated_by:
    user: ''
    agent: resident_73cb83291316
importance: 8
metaness: 2
ai_generated: false
links:
  source:
  - ${code_root}/management_portal/society_service/resident_intake_domain.py
  - ${code_root}/management_portal/society_service/isa/execution.py
  - ${code_root}/management_portal/society_service/resident_protocol.py
  - ${code_root}/management_portal/society_service/resident_execution_domain.py
  related:
  - decision_4dea0db1f45a
  - decision_898b8911a2ff
  - decision_c524c775e0be
  - decision_c54e68a82d7a
  - decision_edc470e09f5a
  - decision_f560803947c6
  supersedes: []
  derived_from:
  - decision_898b8911a2ff
  - thinking_ca8f397a47de
---
# HATG ISA：Resident 执行与队列指令

Resident 执行指令控制 Resident 当前在做什么：查看它是否在工作、打开或关闭接单，以及停止一条消息（正在执行的停下，还在等待的撤回）。读取和整理 Resident 的等待消息（调序、合并、拆分、放回队列、注入、清空）属于 Mailbox 类（decision_c54e68a82d7a）。所有指令共同遵守的调用格式、执行流程和错误语义见 decision_898b8911a2ff。

## 操作的对象

- **Resident 状态**：整个 Resident 的工作摘要，包括 `work_state`（`idle` 空闲、`queued` 有等待输入、`running` 正在执行、`stopping` 正在停止）、`work_phase`（启动会话、等待执行容量或等待派发）、当前执行的消息 ID、等待数量和 `intake_state`（接单开关，`open` 或 `closed`）。
- **输入状态**：一条用户消息的处理进度，取值为 `queued`、`running`、`completed`、`failed` 或 `cancelled`。后三个是终态，写入后不再改变。
- **等待队列**：尚未开始执行的用户消息，按执行顺序排列。
- **可用操作（controls）**：服务按当前调用者的权限和 Resident 状态计算出的可执行操作，例如能否发送、能否停止。

这些对象的完整字段、状态转换和实时事件见 decision_c524c775e0be，输入从接收到执行的运行架构见 decision_edc470e09f5a。

## 指令一览

| 指令 | 改变状态 | 作用 | 默认方案要求 |
| --- | --- | --- | --- |
| `ListResidentStates` | 否 | 读取一个或多个 Resident 的状态与可用操作 | 对 `resident_ids` 有 @viewer |
| `GetInputState` | 否 | 读取一条消息的处理状态 | 对 `resident_id` 有 @viewer |
| `OpenIntake` | 是 | 打开或关闭接单：打开后等待中的消息开始推进，关闭后新消息只排队不执行 | 对 `resident_id` 有 @contributor；对 `resident_ids` 有 @contributor |
| `CancelInput` | 是 | 停止一条消息：执行中的停止，等待中的撤回，已结束的保留结果 | 对 `resident_id` 有 @contributor |
| `CancelSubagent` | 是 | 停止当前执行中的一个子 agent，父轮次继续 | 对 `resident_id` 有 @contributor |

## 读取状态

### `ListResidentStates`

**读取一个或多个 Resident 的工作状态、接单状态和当前调用者可用的操作。**

结果的 `execution_gate` 独立报告 `open/closed/unknown` 和额度保护、人工关闭或恢复未确认等原因；采用 decision_c524c775e0be 的只读批量采样合同。额度耗尽与保护阈值是否启用分别判断，未知结果不等同已放行；`intake_state` 仍只表示人工开关，`can_send` 仍表示接收并排队权限。 只读一个 Resident 时传只含它的列表，取结果中的第一项。

- 参数：`resident_ids`、`include_viewer_state`（是否包含调用者的未读状态）。
- 结果：每个 Resident 的状态与可用操作；请求时还包括 `viewer_state.unread`。每个 Resident 分别检查调用者能否读取，看不到的 Resident 不出现在结果中。

状态读取共用已经校验可见性的 Resident 记录，按批次取得运行状态；可用操作由该记录的后端定义派生。规范化身份中保存后端，执行模式属于后端定义：模型后端启动会话，`portal` 后端执行确定性处理。读取不要求另存 `mode`，也不把缺少它一律解释为模型会话；传入显式模式的注册或校验请求仍必须与后端一致。混合后端的批量查询包含各类合法 Resident，并保留各自的媒体、工具和实时输入能力。

### `GetInputState`

**读取一条用户消息的处理状态。**

- 参数：`resident_id`、`message_id`、`include_progress`（可选）、`include_context`（可选）。
- 结果：输入状态、回复消息 ID 列表、更新时间和错误。`include_progress` 为真且消息尚未完成时，另给出 `progress`：排队时为前面还有几条（`ahead`，Resident 正在处理的另一条也算在内）；运行时为 Resident 的 `work_state`、`work_phase` 和这条消息的实时活动 `activities`。这与 Family facade 在转交请求下显示的进度相同（decision_44cf587c1721），聊天室用它显示被召唤 Resident 的进度（decision_a6bebf4ac283）。
- `include_context` 为真时另给出 `in_context`：这条消息是否会进入或仍在 Resident 当前的上下文中。等待和执行中的消息为真，它们会进入 Resident 执行时持有的上下文。已处理的消息只有在 Provider 接收了它、接收它的会话仍是 Resident 最新的会话、上下文清空时间在那之前、并且那个会话最近一次压缩在它之前时才为真；Mailbox 群体成员按所在群体判断。Family Router 用它判断给 Router Resident 的完整说明是否还在（decision_44cf587c1721）。

## 接单开关

`OpenIntake` 可以作用于一个 Resident，也可以批量作用于多个 Resident：`resident_id` 和 `resident_ids` 二者提供一个。批量执行时逐个处理每个 Resident，每个 Resident 按权限方案对它的 `resident_id` 单独判定，某个 Resident 失败不影响其他 Resident；结果分别列出发生变化、未变化（本来就处于目标状态）和失败的 Resident。Home 账号面板一次开关一个账号上的 Resident 时，从账号详情取 `open_resident_ids` 或 `closed_resident_ids` 作为 `resident_ids`（decision_4dea0db1f45a）。

### `OpenIntake`

**打开或关闭 Resident 的接单开关。**

- 参数：`resident_id` 或 `resident_ids`（二选一）、`open`（必填；`true` 打开，`false` 关闭）。
- 结果：单个 Resident 时返回变化前的状态、当前状态和本次是否发生变化；批量时返回发生变化、未变化和失败的数量与列表。
- 打开：等待中的消息按顺序开始执行。人工打开同时为该 Resident 当前绑定的账号记录人工放行；以 Scheduler 对当前 binding 已确认的豁免为准，未确认时不能只凭 Resident 设置宣称恢复。额度保护由独立的 `execution_gate` 表达，不回写人工接单开关；满足实际执行条件后等待中的消息恢复调度，不需要重新发送。
- 关闭：之后到达的消息仍被接收并排队，但不会开始执行；当前正在执行的工作继续完成。人工关闭写入持久接单开关，并在 `execution_gate` 中优先表现为 `manual`；额度恢复后仍保持关闭，直到有人重新打开。

## 停止

CancelInput 在默认方案中要求对目标 Resident 有 contributor（decision_600ae27b0538）。服务调用形成的隔离输入也可由有这项权限的调用者通过 CancelInput 取消。发起者取消自己的服务调用则走可信业务 RPC：按不可变输入范围收据确认归属后完成取消（decision_725dedbe89b2）。接单领域负责输入、队列与运行状态的有效性，不重复判断 owner 或发送者权限。

### `CancelInput`

**停止一条消息。** 服务按这条消息此刻的阶段决定怎么做，调用者不必先判断它在执行还是在等待：

1. **正在执行**：请求停止这一轮。可选 `cancellation_requested` 表示已保存停止意图，不能当作 `cancelled` 终态；`backend_cancelled` 也不能代替独立的原生进程退出证据。Resident 可能短暂处于 `stopping`，调用者沿原消息通过 `GetInputState` 或对话事件读取后续事实，执行确认停止并完成结果投影后，该消息才进入 `cancelled` 终态。
2. **还在等待**：从 Mailbox 撤回，状态变为 `cancelled`，它不会被执行。等待中的请求、群体消息和 info 都可以撤回。撤回的 info 不再随请求送达，错误代码 `info_withdrawn`；Family 门面上的 info 到达时已交给 steward，不能撤回。
3. **没有任何工作在处理**：回执停在 `queued` 或 `running`，但这条消息不在队列和当前轮次里，不是 Resident 正在处理的消息，Mailbox 中只剩 `pending` 行或没有行，也从未有过 Execution，例如早期版本队列丢下的请求。服务在 Resident 的状态锁内确认这些条件后，把它记为 `cancelled`，错误代码 `lost_input`，`pending` 行随之移出 Mailbox。任何一处仍持有它时，按上面两种情况处理。这类消息不能放回队列，需要重做时重新发送。已有 Execution 的消息不按这一条结束：运行结束后，结果要在状态锁释放后才写进输入状态，这期间它已离开当前轮次，看起来没有任何工作在处理，它的终态由这次运行的结果决定。
4. **撤回途中开始执行**：消息已被接单并分配 Execution ID 时，撤回改为请求停止这一轮，回执中 `cancellation_requested` 为 `true`。用户点一次撤回，这条消息就不会继续执行。
5. **已经结束**：不报错，保留原有结果；回执的 `input_state` 给出它的结束状态（`completed`、`failed` 或 `cancelled`），`already_terminal` 为 `true`。消息 ID 不存在时返回找不到。

- 参数：`resident_id`、`message_id`。
- 结果必含 `resident_id`、`message_id` 和 `input_state`；`cursor`、对象形式的 Resident `state` 与 `controls` 为可选快照，停止受理不等待完整对话或状态投影。
- 停止入口只读取 Lineage 的访问属性和有效成员 ID 来校验归属与权限，不加载组件正文、连线和运行工作状态；Lineage 正在删除或目标已离开有效节点集合时拒绝请求。执行层仍按指定消息及其 Execution 精确取消。普通活动输入的停止意图和可恢复状态发布意图在同一短事务中提交，不等待 Workspace、Conversation 或执行服务读取；恢复发布只处理这次控制，不重投业务。
- **目标是 Family 门面的消息时**（decision_44cf587c1721，TFI portal 10-06），作用于 Router 转交给成员的那条消息：嵌套 Family 沿转交链找到实际干活的成员，ISA 入口按请求的目标 Resident 检查权限，转交后成员按消息与转交记录执行。回执的 `resident_id`、`message_id` 和 `input_state` 指向成员上的那条消息，完整状态、队列和游标属于该成员，另带 `member_resident_id`、`member_message_id` 和门面原请求 `facade_message_id`。门面保存的原请求和转交身份保持不变，显示按 Family 对话投影处理：成员尚未收到即撤回时，门面只显示“用户已撤回一条信息”，隐藏正文、引用和附件，不补写取消回复；成员已经收到并开始执行后停止时，保留原请求正文及既有输出。投影用成员持久轨迹区分撤回和停止，清空当前 Chat 不改变判断。Router 还没转交的门面消息从门面自己撤回，也只保留撤回提示；普通 Resident 的排队撤回不会因此新增对话消息。

### `CancelSubagent`

必填 `resident_id`、`execution_id`、`tool_use_id`。默认模板要求调用者对该 Resident 有 contributor，指定 Execution 必须归属于该 Resident。停止请求通过当前 Execution 的输入控制交给 Provider，只适用于声明 `subagent_stop` 能力的 Provider；父轮次继续执行。Chat 从运行中卡片所属活动的 `execution_id` 取得 Execution。每次请求使用新的 control_id，Provider 还没登记这个子 agent 时可以再请求一次。返回 `control_id` 和 `status`（delivered、pending 或 failed）；failed 时 `error` 给出原因：`unknown_subagent`（Provider 没有或已不再跟踪这个子 agent）、`subagent_finished`、`subagent_not_running`、`subagent_stop_unsupported`、`no_active_turn`（这一轮已经结束）、`no_session` 或 `session_changed`。子 agent 停下后，卡片以“已停止”结束。

返回控制回执 `control_id`、`status`（`pending`、`delivered` 或 `failed`）及可选错误说明 `error`。送达控制和子 agent 已结束分别由控制回执与 Provider 的后续运行事实表示；页面据后者结束卡片。Execution 不存在或不属于该 Resident 时报告不存在，轮次已结束而不再接受控制时报告冲突。

## 参数名契约

下表列出 arguments 内的顶层参数名；必填条件、类型和枚举以 GetInstructionSchema 为准。调用信封中的 expected_version、idempotency_key 与 wait_seconds 不列入 arguments。

| 指令名 | arguments 参数名 |
| --- | --- |
| `ListResidentStates` | `resident_ids`、`include_viewer_state` |
| `GetInputState` | `resident_id`、`message_id`、`include_progress`、`include_context` |
| `OpenIntake` | `resident_id`、`resident_ids`、`open` |
| `CancelInput` | `resident_id`、`message_id` |
| `CancelSubagent` | `resident_id`、`execution_id`、`tool_use_id` |

## Execution 的唯一身份

Resident 接单时在事务内创建 agent.executions 与 execution_inputs，之后准备输入、等待 Worker、Provider 执行和收尾共用同一 execution_id。准备失败和执行前取消也保留执行记录；单纯等待的消息不提前创建执行。终态输入显式重试时新建 Execution，原记录保留。带 `resident_id` 的 ClearMailbox 撤回未接单输入，不停止已接单但仍在调度器等待的 Execution。多个来源可合并进入一个执行，来源授权按各输入保留；执行表不保存 Trigger Path ID。

### Family 门面的输入状态（TFI portal 10-15）

`GetInputState` 对门面返回原请求的处理状态，而不是 Router 转交回执。已转交时沿 Family 的转交关联（包括嵌套 Family）读取实际成员的状态、最终回复 ID、更新时间和错误，返回的 `message_id` 仍为门面原输入。`include_progress` 给出排队位置、启动阶段和实时活动；尚未转交时给出与门面对话相同的路由进度。`include_context` 跟随实际成员的上下文，尚未转交时为 false。调用者仍通过门面 `GetMessage` 读取对应回复。
