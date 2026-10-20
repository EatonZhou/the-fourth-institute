---
kb_id: decision_edc470e09f5a
type: decision
project: AI员工
created_at: '2026-09-03'
version:
  number: 34
  created_at: '2026-09-03'
  created_by:
    user: ''
    agent: ''
  updated_at: '2026-10-20T00:59:06Z'
  updated_by:
    user: ''
    agent: resident_6199a7a36480
importance: 10
metaness: 3
ai_generated: false
links:
  source:
  - ${code_root}/management_portal/society_service/resident_input_scope.py
  - ${code_root}/management_portal/execution/service.py
  - ${code_root}/management_portal/society_service/resident_execution_domain.py
  - ${code_root}/management_portal/society_service/resident_intake_domain.py
  - ${code_root}/management_portal/society_service/invariant_locks.py
  - ${code_root}/management_portal/society_service/resident_domain.py
  - ${code_root}/management_portal/society_service/resident_execution_work.py
  - ${code_root}/management_portal/society_service/resident_conversation_stream.py
  - ${code_root}/management_portal/society_service/resident_execution_provider.py
  - ${code_root}/management_portal/society_service/resident_conversation_event_relay.py
  - ${code_root}/management_portal/execution/runtime.py
  - ${code_root}/management_portal/execution/agent_data_projection.py
  - ${code_root}/management_portal/resident_chat_projection.py
  related:
  - decision_725dedbe89b2
  - decision_91eb3626d323
  - decision_0f57656a2acd
  - decision_c524c775e0be
  - decision_1b2bdf5c33d6
  - decision_25eb93d803a4
  - decision_b1ab45c4b3ec
  - decision_c616d197abbb
  - decision_ae422b92c6f3
  - decision_18abbdef3da3
  - decision_f560803947c6
  - decision_eb066fbea4ed
  supersedes: []
  derived_from: []
---
# Resident 输入、执行与 Conversation 运行架构

本篇说明一条消息从“已经提交”到“Resident 已经回复”的完整路径：哪些组件参与、每一步把什么写到哪里、各类状态归谁管，以及进程重启、通知丢失或写入延迟时怎样恢复。读完后应能回答四个问题：一条输入什么时候算“已接收”；队列和状态由谁负责；模型的执行结果怎样变成可见的回复；出故障后从哪里恢复。

对外可见的对象字段和指令语义由 decision_c524c775e0be 规定；Resident 身份与所属 Lineage 见 decision_0f57656a2acd。本篇负责架构和状态归属。

## 参与者

- **Resident**：跨多次运行持续存在的 Agent 身份。
- **Conversation（对话）**：面向用户和业务的持久对话记录，由消息、输入状态、等待队列和运行状态组成。
- **Message（消息）**：可以单独按稳定 ID 读取的通信事实，其正文来自当前 Segment 版本。“消息被接收”和“消息加入可见对话记录”是两个分别保存的事实：队列中还没开始处理的消息已经有 ID，但不出现在对话历史里。用户或 Resident 发来的输入保存为 `role=user`，模型回复保存为 `role=agent`。消息的 `author_id` 与来源 `origin.sender` 记录同一个真实发出者：回复的发出者是实际执行的 Resident，系统通知的发出者是 Portal 服务。执行记录另外保存请求提交者和执行者，取消、调序另外记录实际操作者。来源与不可修改规则见 decision_ae422b92c6f3。
- **Input（输入）**：一条用户消息的一次处理，与消息使用同一个 ID，依次经过排队、运行和结束。
- **SocietyService**：Resident 相关请求的入口服务（decision_1b2bdf5c33d6）。它校验身份和权限、保存消息、维护对话状态，并把要执行的工作交给执行系统。
- **ExecutionService**：执行调度服务（decision_25eb93d803a4）。它接收 SocietyService 已在 AgentData 登记的 Execution，沿用同一个 execution_id 分配给 Worker。
- **AgentData**：实际模型交互的运行记录，包括发送给模型的完整输入、模型事件、用量和原始回复（decision_b1ab45c4b3ec）。模型“实际做了什么”以它为准。
- **Conversation 投影**：把 AgentData 中适合给人看的结果写成对话消息和状态的过程。
- **Provider Session（模型会话）**：Codex、Claude、Kimi 或模型 API 当前使用的连续会话线程，可以跨多次输入复用，也可以重启。
- **Binding（绑定）**：把 Resident、执行执行和模型会话连起来的运行关系。
- **事件中继（Event relay）**：把持久的对话事件转成实时通知的组件。通知只用来“叫醒”观察者。

## 状态由谁负责

| 状态或记录 | 负责者 | 用途 |
| --- | --- | --- |
| Resident 身份与默认配置 | Resident Catalog（SocietyService 内） | 确定谁在运行 |
| 消息、可见对话顺序、输入状态、等待队列、对话事件 | 对话存储（Conversation repository，SocietyService 内） | 记录已接收的内容、可见顺序、排队、回复和终态 |
| 接单开关 | residents.intake_open，由输入控制领域写入 | 人工决定能否开始新输入 |
| 当前轮次工作 | resident_execution_work，由轮次执行领域写入 | 保存准备、提交、取消、投递与确认进度；工作状态结合 Mailbox 和 Execution Scheduler 派生 |
| 执行请求与执行 | ExecutionService | 管理 `execution_id`、执行租约、检查点、调度、取消和恢复 |
| 模型请求、事件、用量、原始回复 | AgentData | 保存实际模型交互 |
| 模型会话绑定 | SessionManager 与 Binding 记录 | 复用或替换模型会话 |
| 页面实时提示 | 通知总线（Notification Hub） | 快速唤醒，不作为事实依据 |

每项事实只有一个负责者：对话存储不复制模型会话的全部事件，AgentData 也不代替用户看到的消息历史。

## 一次输入怎样流动

```text
调用方
  │ 1. 投递：保存消息与 queued 输入状态
  ▼
对话存储
  │ 2. 开始处理：同一消息 ID 加入可见对话记录，写入 activated_at
  ▼
SocietyService ──3. 提交执行请求──> ExecutionService
                                        │ 4. 组装上下文，取得模型会话
                                        ▼
                                  模型 + AgentData
                                        │ 5. 原始结果与用量
                                        ▼
Conversation 投影 ──6. 回复消息、终态、事件──> 调用方 / 页面
```

## 设计点

### 接收与开始处理

准入的事务同时保存不可修改的输入、轮次或排队状态，以及 `resident_delivery_work` 中的投递发布意图。确认返回以这些事实提交为准，不等待工作区装配、Conversation 投影、事件唤醒或 Provider 注入完成。发布意图按 Resident 与消息 ID 合并，保存相应 Job revision；协调器在状态锁外处理，按观察到的 revision 确认，较新的意图不能被旧处理删除。唤醒失败不撤销已经提交的输入，重启扫描继续处理未确认的意图。

准入读取只包含身份与配置。执行准备和提交后收尾需要工作区完整记录；它们在状态锁外装配，再于锁内重新核对原 Execution 与输入控制标识后提交。Send now 的本地接收与远端送达分别记录，未确定的远端回执保持待对账，不能据此重复投递到新的轮次。

- **消息先被接收，开始处理时才进入对话记录。** SocietyService 先检查调用者权限、消息结构和后端能力，通过后在对话存储中保存不可修改的用户消息和 `queued` 输入状态。调用方拿到确认时，就可以凭 Resident ID 和消息 ID 查到这条输入，不论模型是否已经启动。
- **开始处理时，同一消息 ID 加到对话记录末尾，并写入 `activated_at`。** 页面按对话记录顺序显示，因此等待期间完成的上一轮回复出现在这条新输入之前。加入当前 trace 时写入 Portal 包装后的内容；包装完整保留正文。Harness 生成新版本后替换当前引用，聊天服务展示新版本，`created_at`、角色、作者和来源仍保持通信事实。
- **接收确认、实际送达模型和模型完成是三件不同的事。** 各自有独立的记录；页面上显示了消息，不代表模型已经收到。

准备阶段首次预留输入时保存原接收项和成员准备事实的持久检查点，早于正文材料化和模型提示构造；后续追加 info 时补入同一检查点，不覆盖原接收项。这样准备失败或进程中断后，仍能验证原消息身份和顺序。检查点不替代不可变消息，也不授权从提示词推导缺失正文。

### 投递方式

- **正常发送（`enqueue_and_trigger`，默认）：** Resident 空闲且接单打开时立即开始；忙时排队，轮到后自动开始，不需要另一条消息唤醒。执行容量与全局节流照常生效，发送本身不增加延迟。
- **注入（`inject_active_turn`）：** 把消息补充进正在进行的处理，Codex、Claude Code、Kimi Code 和 DSH 支持（各 Provider 的做法见 decision_425e428410e0）。系统在接收消息之前检查目标能力和是否确有正在进行的处理，空闲、正在停止或正在压缩上下文时拒绝。注入使用同一个消息 ID、同一个队列和执行服务的输入控制，没有另一套注入运行器：执行服务的持久回执确认接收后，消息才离开队列；当前处理在接收前结束时，这条输入进入下一轮，并向调用方明确返回“未注入、下一轮处理”。
- **随下一次请求送达（`attach_to_next`）：** 用于 info 消息，只包含消息内容。消息照常进入对话并处于 `queued`，但不开始处理；它作为 Mailbox 中等待随行的 info 保存（每个 Resident 最多 200 条）。下一条请求生成执行请求时，所有等待中的 info 以“背景更新”的形式与请求共同包装、合并为当前内容后送达，携带的 info 身份与最终 prompt 一起保存到持久执行请求，再按该列表消费并记为 `completed`。恢复已有请求时读取原列表，避免同一 info 再次进入下一条请求。授权调用的隔离输入不能作为 info 消息。
- **几种方式共用身份、不可修改的消息、幂等、队列和状态实现。** 业务调用、`ForwardMessages`、调用 `DeliverMessage` 的 Effect 和页面队列中的“立即注入”都遵守这套规则。接单开关是 Resident 整体的执行门控，投递方式不能替代它。其他取值在接收和 Lineage 组件配置入口直接拒绝；数据中残留的无效取值的未执行输入不会被恢复器自动启动。

### 接单开关与运行门控

- **Family 门面只接收、不执行。** 发给 Family 门面的消息只能用 `enqueue_and_trigger`，只包含消息内容；消息进入门面的对话后保持 `queued`，由 Family Router 转交给成员，门面自己不建立执行（decision_44cf587c1721）。
- **接单开关 `intake_state` 控制是否开始新的处理。** `open` 时等待的输入按顺序推进；`closed` 时新输入照常接收并排队，但不开始新的处理，正在处理的输入继续完成；重新打开后继续调度等待项。
- **启动 Session 已属于当前输入的处理过程。** 输入通过接单开关时，在持久执行进度中记下 `intake_admitted_at`，不等执行器提交回执。随后关闭接单，不把这条输入放回 Mailbox 等待队列，也不阻止它继续准备 Session；重新读取状态、恢复执行和更新进度都保留这次准入。提升下一条输入或重新入队时不继承前一轮准入，仍须检查当前接单开关。Chat、Queue 和输入状态使用同一判断。
- **开始处理前还要通过几项检查：** Resident 可以运行、当前没有其他活动输入、执行容量足够、当前 Provider 支持输入中携带的媒体和工具能力。
- **`work_state` 是给人看的摘要。** 取值 `idle`、`queued`、`running` 或 `stopping`；等待中的原因由 `work_phase` 细分为 `starting_session`（启动模型会话）、`waiting_capacity`（等待执行容量）、`waiting_dispatch`（等待账号派发间隔）和 `waiting_quota`（账号额度进入保留线）。这些摘要不代替每条输入各自的状态。

### 执行请求与模型会话

- **执行请求绑定稳定的 Resident 身份。** 准备阶段在同一事务登记 agent.executions、execution_inputs 和 resident_execution_work。ExecutionService 沿用这个 execution_id 接收 Resident 身份、输入、调用者、路由、设置与上下文。提交重试保留 ID；终态后重新执行分配新 ID。Execution 不保存 Trigger Path 身份。
- **模型会话优先复用。** SessionManager 优先复用这个 Resident 当前有效、后端匹配的模型会话；重启或绑定失效时创建新会话并更新绑定（decision_91eb3626d323）。对话和输入 ID 不随会话更换而改变。
- **上下文在执行时组装。** 组装器把 Resident 初始说明、规则 Skill、当前对话、Fork 继承的上下文、调用方给出的上下文引用、工作目录信息和运行设置组成实际输入，最后加上所有者边界说明和 Resident 身份信封。系统指令清单 `SystemInstructions` 标明哪些系统 Skill 只在创建模型会话时注入一次，避免每轮重复发送。哪些来源被采用由入口和上下文策略决定（decision_c616d197abbb）。组装结果随执行请求写入 AgentData，可以通过 `GetSentPrompt` 查看；对话中的消息保持用户的原始表达。
- **工作目录默认是 Resident 的默认工作区。** owner 可以为某一轮选择另一个已绑定的工作区根目录。Provider 不支持请求中的上下文、媒体或 Skill 时，组装阶段拒绝这次输入，不悄悄丢掉材料。

### 结果与终态

- **模型的所有事实先写入 AgentData。** 流式事件、工具交互、用量、最终文本和结构化响应都先写入 AgentData；执行失败也在这里留下执行和错误。即使对话投影暂时失败，实际的模型交互仍可以读回来。
- **投影把结果写回对话。** 投影器把可显示的文本写成一条或多条 Agent 消息，把结构化响应保存为产物并在消息中引用，再把回复消息 ID 写入输入状态。之后输入推进为 `completed`；模型或执行出错为 `failed`；用户取消为 `cancelled`。
- **终态顺序固定。** 先提交回复消息和它的“消息追加”事件，再提交带回复 ID 的输入终态，最后发布 Resident 回到空闲。模型已经结束但回复还没写入时，这条输入仍显示为处理中；仅凭模型的终态不能提前完成输入。
- **终态不会被改回。** `completed`、`failed`、`cancelled` 写入后，迟到的事件不能把它改回排队、运行或另一个终态。

## 实现说明

### 两个短时入口：执行状态与对话写入

每个 Resident 的状态修改走两个互相独立的串行入口，每次都只持有很短时间：

| 入口 | 锁名 | 串行保护的内容 |
| --- | --- | --- |
| 执行状态入口 | `resident:<resident_id>` | 输入排队与接收、配置、执行与执行绑定、取消意图、结果归并、跨存储写入的授权记录 |
| 对话写入入口 | `resident-conversation:<resident_id>` | 消息、输入状态、队列、活动、运行状态和事件游标的短写入 |

- **两把锁都不包住外部调用。** 调用执行系统、创建或 Fork 模型会话、等待执行结果、读取模型记录、组装消息内容、对话投影、快照组装、资源索引和通知，都在短提交之外完成。
- **运行中输入和设置先记意图，再调用执行服务。** 在 Resident 状态锁内保存输入或设置意图，解锁后发送，回到锁内核对原来的 Execution 身份后写回执；旧请求不得写入后来的轮次。延迟重启只读取持久接单开关，不在锁内查询执行状态。
- **跨两个存储的写入用“授权记录 + 幂等写入 + 确认”协调。** 要把一条执行结果写成对话消息时：先在执行状态入口中核对当前执行和执行，保存一条带稳定消息 ID 的写入授权；释放执行锁后，在对话写入入口中按这个 ID 幂等写入消息和事件；最后再用一次短提交确认授权。这样不需要用一把跨存储的长锁假装成一个事务。
- **发出外部动作前先保存意图。** 收到结果后重新读取当前 Resident，核对消息 ID、`execution_id`、版本号（revision，每次提交递增）和有效代次（generation，会话或执行换代时递增），再归并结果。Resident 被删除或替换后，旧任务的事件只能作为历史记录，不能恢复当前的运行状态。
- **提交后的效果互不牵连。** 执行提交后，对话状态投影带着这次的版本号在解锁后运行，期间有新提交时有限次重读最新版本并补齐。取消通知、执行绑定唤醒和对话投影彼此独立，一个失败不影响其他。
- **同一 Lineage 中的不同 Resident 互不等待。** Lineage 自身只在事务中修改节点、连线、成员关系和协作定义；单个 Resident 的慢调用不会占住其他 Resident 的推进通道。
- **对话快照和其他读取不取任何锁。** 读取消息页之前先记下游标，再读消息和状态；游标在此期间变了就重读一次。持续写入时返回“最后一次读消息之前”的游标，保证没读到的新消息一定在之后的事件范围内。

### 回复写入与坏消息隔离

- **完成等待者按新增的执行事件写回复。** 它还按已确认的文件位置增量读取持久活动记录，补上瞬时事件窗口漏掉的消息。整批历史的读取、解析、内容组装和遍历都在执行锁之外进行；状态快照重复到达而活动记录没有新增时，不再重读已确认的历史。
- **读取位置由等待者自己保存，消息写入成功后才前进。** 等待者重建、文件被替换或截短时，从记录开头重新补齐，仍按消息身份核对和幂等写入。
- **一条输出写不进去时，把它隔离，继续处理后面的。** 某条输出无法构成合法消息，或消息 ID 与已有内容冲突时，系统记录 `execution_id`、消息 ID、事件序号和错误，把这个消息 ID 放进一个有上限的隔离列表（quarantine），然后推进事件游标。之后重放时跳过已隔离的消息，不会无限重试，也不会卡住停止、队列和后续消息；执行本身的终态不受影响。最终结果（Final）仍由执行结果的终结路径写入。
- **有 `execution_id` 只说明请求已经绑定，不代表 Worker 已经开始。** 调度快照还没到时，活动输入保持已有的公开状态，首次绑定显示为启动阶段，不自行推断为 `running`。没有调度快照的本地更新不覆盖已观察到的调度状态；停止意图和输入终态始终优先。执行被真正重新排队时，如实显示调度器给出的等待原因。

### 停止一条输入

- **停止正在处理的消息和撤回排队中的消息都用 `CancelInput`。** 它按消息 ID 找到这条输入当前所处的位置：正在运行就停止这一轮，还在 Mailbox 中等待就撤回并记为 `cancelled`，已经结束就保留原结果并在回执中带 `already_terminal: true`；不会因为一条消息而停止另一条消息的当前工作。
- **停止先保存意图，再更新运行状态。** 停止意图写在该 Resident 的执行记录中，Resident 身份记录不变。停止校验当前输入与 resident_execution_work 的控制字段；保存停止意图前不请求 Execution 状态，不以界面缓存作为控制真值。对已绑定的普通活动输入，在同一短事务内按 resident_id、message_id 和 execution_id 校验当前工作，保存停止意图与 resident_delivery_work 中的状态发布意图；该入口不等待 Workspace 解析、Conversation 读取或状态投影。准备态、暂存输入和清队列等路径仍遵守各自的精确输入契约。
- **受理回执不依赖完整对话快照。** CancelInput 必须返回实际目标 resident_id、message_id 和 input_state；cursor、Resident state 和 controls 可以省略。cancellation_requested 只说明停止意图已保存，不代表执行已取消或原生进程已退出。客户端沿原消息身份通过输入状态与对话事件观察终态，不能把回执或 stopping 当作终结证据。
- **状态发布和取消唤醒可以恢复。** 状态发布意图与停止意图同提交；提交后的进程内唤醒丢失时，持久工作扫描继续唤醒对应执行并发布最新状态。状态工作按 revision 确认，旧工作不能清除更新的意图。仅发布状态的工作不再投递业务输入或触发消息接收监听。
- **停止意图始终跟着这条输入。** 等待输入只有消息 ID；一旦进入准备阶段就使用同一个 execution_id。停止与提交交错时，后续提交保留停止意图并取消那个确切的 Execution。执行记录的版本检查、状态合并和写回在同一个 PostgreSQL 行锁事务中完成，因此迟到的快照不会把已请求停止的输入重新显示为排队、运行或成功。
- **停止请求立即被接受，由等待者转交执行服务。** 带取消检查的等待者每次等待前都检查意图，没有新事件时最多每 250 毫秒检查一次，不必等模型新输出、工具结束或 30 秒长轮询超时。执行服务暂时不可达时，用同一个 `execution_id` 重试，送达后不重复发送。250 毫秒只是检查和转发的间隔上限；只有执行服务确认取消并给出对应结果后，这条输入才结束。
- **取消的结果：** 排队中的输入移出队列并变为 `cancelled`；运行中的输入请求执行服务停止对应执行，Resident 可能短暂显示 `stopping`，终态写回后转向下一项或回到 `idle`。取消与队列、执行之间出现竞争时重新读取并有限次重试；已经结束的输入返回 `already_terminal`，不会重新执行。
- **取消终态和停止者分别取证。** Execution 的 `cancelled` 结果只确认这一轮取消；通知只有在同一执行记录中存在持久的 `stop_requested_by` 时，才将该操作者表述为停止者。`actor` 是执行请求的提交身份，不能在停止者缺失时替代它，也不能默认归给 Portal。缺少停止记录时显示不指认操作者的取消通知，实际取消来源仍为未知；不从通知反推人工 Stop、Provider 行为或基础设施原因。通知继续使用原输入与 Execution 对应的稳定消息身份，重复投影不新增通知，不改变人工停止、接收闸门或后续队列语义。
- **终态投影不占住入口命令锁。** 在短锁内核对原 Execution 与输入并取得投影授权后，Workspace 访问和 Conversation 写入在入口命令锁之外完成，仍按稳定消息身份幂等写入停止结果。写回输入终态和释放活动位置前再次核对原身份；迟到的收尾不能取消、清空或覆盖替换后的执行。保留先完成结果投影、再写输入终态并转向下一项或 idle 的顺序，不用提前结束掩盖慢投影。

### 额度检查

- **定期额度检查一次取得全部账号。** SocietyService 从 ExecutionService 一次取得账号清单：已有模型会话的 Resident 按 Provider 和实际账号路线 `backend_route_id` 对应到账号；还没有会话的 Resident 按 Provider 和 `provider_profile` 分组，每组只解析一次默认账号。账号请求数因此取决于不同配置的数量，而不是 Resident 的总数。
- **额度等待由 Execution Scheduler 负责。** SocietyService 同步显式额度放行配置，不因额度改变人工输入开关。账号恢复额度时，Scheduler 继续原队列；人工关闭的 Resident 仍保持关闭。

当前未完成协调状态在完成结果投递并确认 Execution 后清理；执行历史永久保留在 agent.executions，输入关系保留在 agent.execution_inputs。重启恢复从这些未完成工作继续，不从界面状态或旧进程内线程列表重建事实。

执行离开当前活动位置但仍需收尾时，resident_execution_release_work 保存 execution_id、resident_id 与调用者。恢复器按这些字段对账：仍由当前轮次持有的执行保持不变；已释放轮次对应的执行仍在运行时请求取消，等待 Worker 提交终态后才确认 Execution 并删除收尾状态。控制连接故障保留意图供下一次扫描重试，单项失败不阻塞其他轮次。业务收尾不改写新输入、不重放原提示，也不绕过 Scheduler 对 Provider 中断和会话占用的确认。

### 实时通知

- **通知不是事实来源。** 事件中继跟踪持久事件，向通知总线（NATS）发布轻量通知；页面和同步等待者收到通知后，再按游标到对话存储读取。通知可能断开、合并或丢失，它只用来减少轮询延迟。
- **大事件只发摘要。** 单条通知序列化后超过 16 KiB 时，中继省略 `payload`，只发 `payload_omitted: true`、事件类型、Resident ID、游标和时间，接收方按游标补读。
- **中继的节奏：** 默认每 0.1 秒轮询一次，每批读 500 条（上限 2000）；出错后至少等 0.5 秒再重试。中继的运行状态和最近错误公开在健康投影中，供运维判断实时通知是否退化；中继不健康时，查询和按游标补读仍然可用。每个 SocietyService 实例独立跟踪同一个对话数据库，Dev 与 Release 各用自己的通知总线。

### 重置、归档与会话重启

- **重置（`ClearContext`）只归档开始时已有的消息。** 它先在短事务中保存要归档的精确消息 ID 集合、归档时间和操作身份，再在锁外归档消息、清理旧模型会话。之后新增的输入不属于这次重置。AgentData 已经归档而对话事件还没写入时，恢复过程读取同一个归档凭证补写“移除”事件，不会重新归档当前所有消息。旧会话清理完成前，新输入只保存、不开始执行。
- **归档与恢复在写入入口之外搬运数据。** 归档时 AgentData 的搬移在对话写入入口之外进行，之后只为确定的消息 ID 集合短暂写入“移除”事件；没有持久操作凭证的临时归档如果事件写入失败，就撤销这次 AgentData 归档；重置这类有持久操作身份的归档保留结果和凭证，由恢复过程补写事件。恢复（restore）同样在写入入口之外读写 AgentData，只在追加“更新”事件时短暂加锁。
- **会话重启不覆盖用户后来的接单操作。** 会话重启临时关闭接单时，记下这次开关变化的唯一身份；之后任何显式的接单命令（包括再次关闭）都会替换它。重启结束后只释放自己仍然持有的那次变化，不会覆盖用户后来的关闭。

### 服务内部的查询接口

- 会话活动、完成引用和失败引用是 SocietyService 的进程内读取接口，复用对话存储和权限；对外的指令由指令目录登记。完成引用按请求者过滤，只返回有 Agent 回复证据的已完成输入；证据查询关联输入消息和第一条可读回复；对账查询区分“状态缺失”“终态证据缺失”和“已对账”。引用 `limit` 为 1–200，失败引用只返回稳定的错误含义。
- **创建 Resident 不投递消息。** `CreateResident` 登记身份、成员关系、工作区和运行设置；第一条真实消息或 Lineage 事件到达后，才沿统一入口建立输入，并按需创建模型会话。

### 共享 Resident 的隔离输入（语义搜索）

- **业务调用可以为单条输入登记“输入范围授权”。** 授权按 `resident_id + message_id` 保存在 LineageData 的 `input_scopes` 表中，记录发起者和一份不可修改的描述（目前是本次提示词的摘要 `input_sha256`）。它让发起者可以读取和取消这一条输入，而不获得整个 Resident 的历史与管理权限；运行、排队、完成和取消状态仍由标准的消息、输入、队列和事件保存。
- **这类输入不带入 Resident 的私有上下文。** 执行服务处理带输入范围授权的输入时，核对执行请求的调用者就是登记的发起者；不注入 Fork 继承的起步上下文；`provider_api` 后端的 Resident 只发送本次消息原文，不带执行配置中预置的历史消息。相邻的输入仍经过同一个队列。
- **语义搜索使用这一入口。** 业务领域先检查真实用户、页面绑定和候选权限，再把每个搜索批次作为一条消息通过 `DeliverMessage` 提交给 `search.<surface>` 职责指定的 Resident；等待、断线补读和回复读取复用 `resident_handoff`，每一批保存消息 ID 和代次，用来丢弃已被刷新或取消的旧结果，取消通过标准的单条输入控制完成（decision_725dedbe89b2、decision_eb066fbea4ed）。

## Execution 与 Trigger Path 的边界

系统的业务执行只有 Trigger Path 和 Execution。本文的“一轮处理”表示一次 Execution；没有另一种 Job 或 Turn 身份。Provider 原生 turn ID 只用于适配层。

用户消息、Mailbox 输入、图投递和业务调用共用同一执行入口。等待输入不预建 Execution；进入准备阶段后，准备、调度、提交重试、取消、结果和恢复沿用同一个 ID。多个输入可以合并进一次 Execution。

Trigger Path 的 Resident 步骤从图一侧记录 execution_id，多个步骤可以关联同一次 Execution。Execution 不反向保存 Trigger Path。Resident 输出或指令产生新 Event 时开始新的 Trigger Path。

Mailbox 最近执行从 Agent 读取。resident_mailbox_progress 只保留有上限的输入重试检查点。清空队列只撤回等待输入，已提交 Execution 通过 CancelInput 停止。resident_execution_work 和 resident_execution_release_work 都是完成后清理的协调状态。

## 失败与恢复

- **执行失败标记为 `failed`。** 启动失败、容量错误或模型与执行异常，会把能确定归属的输入标记为 `failed`，并附上错误对象。无法确定归属的执行错误保留在执行记录中，由恢复过程重新对账。对话消息写入失败不改写执行终态，按上面的隔离规则处理。
- **自动恢复不覆盖停止意图，也不违背不可重试标记。** 已请求停止或已取消的执行，不会因为后续异常自动追加“继续”。执行结果标明 `error_details.retryable=false` 时也不自动恢复；错误分类由 ExecutionService 负责，Resident 不重复解析错误文字。允许的那一次自动恢复仍走正常的输入入口，以一条幂等的系统消息说明来源，不冒充用户发送。
- **进程重启后按持久记录对账。** 恢复过程读取对话存储中的活动输入、队列和输入状态，再与执行服务的执行、AgentData 结果和会话绑定核对：
  1. 已经结束的输入不再调度；
  2. 有活动执行的输入继续等待或补做投影；
  3. 执行已完成但回复没写入，或还有未确认的写入授权时，从 AgentData 和执行事件按稳定消息 ID 补做幂等写入；
  4. 队列中未开始的输入按顺序重新提交；
  5. 无法继续且有明确错误的输入标记为 `failed`，不会永远停在 `running`。
- 已接收的排队输入没有执行位置时，有权限的主体可用 `RequeueMessage` 从同一 Resident 的完整接收项及准备记录或本地检查点显式恢复。原请求、成员和 info 必须逐项验证；缺失或不一致时拒绝。恢复与队列发布在输入 owner 的锁和事务中完成，不改写既有 Segment，不复活已取消或完成输入，不绕过接单和执行额度。具体条件见 decision_c54e68a82d7a、decision_15f7df737ed5。
- 归档对话或删除运行投影必须走各自的生命周期接口；归档不是删除身份，也不会丢掉尚未对账的输入。

## 架构约束

1. 确认接收之前，消息和输入状态必须已经持久保存；只有真正开始处理的输入才进入可见的对话记录。
2. 对话存储负责业务状态，AgentData 负责实际的模型交互。
3. 模型会话、执行执行和 Resident 是三个不同层级的身份。
4. 通知只负责唤醒；断线后按游标或完整快照恢复。
5. 输入终态不会改变，迟到事件不能让已结束的输入重新运行。
6. 重启后从持久记录对账，不依赖任何进程的内存状态。
7. 消息 ID 是输入的幂等键：相同 ID 和相同内容的重复投递返回已有记录，内容不同则失败；接收和加入对话记录分别幂等，同一 ID 最多得到一个可见位置和一个 `activated_at`。

## 当前内容与运行事实

AgentData 的 trace 只引用当前内容版本。输入包装、执行准备和 Harness 转换的中间版本由 Segment Relation 保存来源，当前 trace 不累加这些版本。界面卡片、执行统计、投递回执和清空记录归独立运行表。聊天显示策略由 Portal 服务提供，Provider 上下文按当前 trace 窗口和最近压缩段读取；数据库不存显示或发送资格标记。执行展示通知只宣布采集入口已经记录的模型内容，不再写入一份聊天正文。具体数据结构和原库整理约束见 decision_b1ab45c4b3ec。

Fork 继承收据中的消息计数沿目标 trace 的成员顺序读取内容和操作通知。查询先限定该 trace 的成员与位置，再按成员 ID 读取对应 Segment 或 Notice；其他 Resident 的操作记录不参与该计数。普通 Chat 分页也以目标 trace 和页界限限定读取范围，仍按同一聊天显示策略决定哪些消息可见。

### 通知中继与历史读取边界

每个实例从持久通知序号尾部开始转发，同时回看最近 10,000 个序号，并按 Resident ID 与事件游标去重。较早取号、较晚提交的事务仍能在回看范围内被发现；发布晚到事件不会让扫描位置倒退。超出窗口才提交的通知仍可能遗漏，持久 Conversation 事实保留，读取方通过游标补读或快照恢复。这个窗口不构成终态消费确认机制。

回看只读取投递索引中的序号、Resident ID 和事件游标；去重后，才按本批未见过的索引读取事件内容并组装通知。没有新事件时，不读取事件内容。已发布事件的去重记录在扫描窗口推进后按批清理，空闲轮询不重复清理，逐条发布也不扫描整个缓存。

上下文读取沿历史分页取得真实消息总数，再按请求的条数和字符上限返回最近的内容并报告省略量；分页游标不前进时明确失败。普通会话快照统一默认返回最近 200 条，更早内容通过分页取得。

## 消息录入与 prompt 构造时机

Resident 的流式执行输出由 Execution 活动记录按写入顺序交给一个 Conversation 消费路径。ExecutionService 保存模型输出的原始 Segment，但不提前把主 Resident 的可见模型消息追加到其 Trace；消费者逐条将消息放入 Trace、发布对应的 Conversation 事件，再处理下一条。最终答复与资源卡片在流式输出处理完后提交，使用同一 Conversation 追加入口。运行快照只投影活动与状态，不作为第二条流式消息交付路径。Chat 实时界面按事件顺序追加，刷新后按 Trace 顺序读取。

Resident 用户消息由 SocietyService 在授权后依据当前接单状态处理。普通异步请求在一个准入事务中保存不可变输入、轮次或排队状态、初始 Input state 事件与待发布记录，提交后返回 `accepted=true` 和消息 ID。协调器继续发布 Conversation 投影、通知消息观察者，并推进执行。发布或唤醒失败时保留待办，由协调器恢复；已经提交的消息不因展示更新失败而重新生成。页面可以先显示本地发送预览，再依据服务端消息、状态和队列事件更新它。

普通文本准入只读取数据库中的 Resident 身份、Provider 配置和接单状态。默认 Workspace 的路径与绑定在执行准备阶段解析。附件、显式工作目录、原生命令和同步 Provider 需要的环境仍在各自处理路径核对；资源引用继续按发送者鉴权。

待发布表 `resident_delivery_work` 按 Resident ID、消息 ID 保存工作修订号，与输入和 Job 同事务提交。协调器按 Resident 合并展示更新，逐个通知本批消息观察者，只删除已处理修订以内的待办；处理期间新受理的消息留给下一批。发布可重试，消息身份与投递控制 ID 保持稳定。普通请求的受理回执无需读取完整 Conversation 快照、controls 或事件游标，调用方通过快照和事件流取得这些投影。

队列“立即注入”在短 Resident 临界区内检查目标和能力，把原消息从队列转成持久在途意图，返回 `accepted=true, injected=false, delivery_status=pending`。协调器在锁外读取执行环境、构造输入、查询与提交执行服务回执；提交前及结算时重新核对捕获的执行 ID 和控制 ID。准备期间发生停止或轮次切换时不把消息追加给新的执行。确定拒收时原消息退回普通队首；回执暂不可知时保留在途状态，避免重复执行。

接单和队列提升只保存原始与有效输入，完整 prompt 及其构造时间在执行阶段产生。Chat 收到消息事件即可展示，无需等待 prompt 或执行回执。
