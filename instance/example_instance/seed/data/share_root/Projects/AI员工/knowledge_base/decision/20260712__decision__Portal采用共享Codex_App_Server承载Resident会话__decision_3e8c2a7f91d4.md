---
kb_id: decision_3e8c2a7f91d4
type: decision
project: AI员工
created_at: '2026-07-12'
version:
  number: 13
  created_at: '2026-07-12'
  created_by:
    user: ''
    agent: ''
  updated_at: '2026-10-19T19:42:22Z'
  updated_by:
    user: tfi-exported-user
    agent: resident_865c3c24f251
importance: 9
metaness: 2
ai_generated: false
links:
  source:
  - ${code_root}/management_portal/execution/worker.py
  - ${code_root}/management_portal/execution/runtime.py
  - https://learn.chatgpt.com/docs/app-server
  - ${code_root}/management_portal/codex_app_server_client.py
  - ${code_root}/management_portal/execution/backends.py
  - ${code_root}/tests/cases/test_codex_app_server_client.py
  - ${code_root}/tests/cases/test_execution_layer.py
  related:
  - decision_5c62deb8253a
  - decision_b1ab45c4b3ec
  - decision_91eb3626d323
  - decision_3dd52c0692c1
  - decision_e587fe1c472e
  - decision_c47a13e8b962
  - decision_6ad0384e020d
  - decision_075f66430606
  - decision_425e428410e0
  supersedes: []
  derived_from: []
---
# 共享 Codex App Server 的会话与 turn 协议

Resident 选择 Codex 作为 Provider 时，实际的模型对话由 Codex App Server 承载。Codex App Server 是 OpenAI Codex 命令行工具提供的服务模式（`codex app-server`），它保存对话、运行 Codex 自带的工具，并通过双向消息协议持续报告进展。Portal 为每个 Codex 账号运行一个 App Server 进程，多个 Resident 共享同一个进程；Portal 通过一个长连接客户端把统一执行请求翻译成 App Server 的协议。这样，Resident 可以在同一个对话中持续工作、派生新对话、压缩长历史、在运行中追加输入或修改速度，也可以随时停止。

本 Design 说明 Portal 与 App Server 之间的连接、对话与每一轮的生命周期、事件和工具、并发与中断，以及 Worker 换代时怎样接着运行。选择哪个账号见 decision_3dd52c0692c1；App Server 程序的安装与按账号部署见 decision_e587fe1c472e；Portal 的排队、执行终态和恢复检查点见 decision_6ad0384e020d。

## 基本概念

- **thread**：Codex 中一个持久保存的对话，包含 Codex 自己的上下文和历史。它保存在账号目录中，App Server 重启后可以重新加载。
- **turn**：thread 中由一次输入引起的一轮生成。一轮会产生消息、工具调用、用量、错误和完成事件，结束时有明确的终态。
- **会话记录（binding）**：Portal 保存的会话归属，说明某个 Resident 的会话对应哪个 Provider、哪个账号路由和哪个 thread。它由 SessionManager 保存（decision_91eb3626d323），与 App Server 进程中的缓存互相独立。
- **规范历史**：Portal 按统一消息格式保存的用户与助手消息，由 AgentData 保存（decision_b1ab45c4b3ec）。
- **执行检查点（checkpoint）**：ExecutionService 为一次执行保存的恢复记录，例如“这一轮已被 App Server 接受，turn ID 是多少”。Worker 换代或重启后据此续接，而不是重发输入。
- **`CodexBackend`**：ExecutionService 中的 Codex 适配器，把统一的执行请求转换成创建 thread、运行 turn、Fork、压缩、Goal 和释放等操作，并把结果转换回 Portal 的格式。
- **`CodexAppServerClient`**：连接一个 App Server 的客户端，负责连接、请求与响应的配对、分发事件和 App Server 发来的请求、记录已加载的 thread 与正在运行的 turn。
- **动态工具**：Portal 在创建 thread 时声明、由 App Server 在 turn 中回调 Portal 执行的工具，例如 HATG ISA 工具。
- **App Server 请求（server request）**：App Server 主动发给 Portal、需要 Portal 回答的请求，例如调用动态工具、向用户提问。

## 设计点

- **每个进程内，同一账号的会话共用一个连接。** Dev、Release 和各 Worker 进程分别持有自己的客户端。每个进程的账号路由对应一条长连接，已加载的 thread 在其中复用；不同账号的 socket、连接、锁和登录状态互相隔离。
- **新会话先创建持久 thread；已有会话恢复原来的 thread。** 会话记录中的 thread 不在当前客户端里时，Portal 调用 `thread/resume` 加载同一个 thread，不会悄悄换成新对话。
- **App Server 接受一轮输入后，Portal 立即保存 turn ID。** 此后发生连接中断或 Worker 换代，Portal 只观察这个 turn 直到终态，不重发用户输入；还没有拿到 turn ID 的请求按普通失败处理，不当作可恢复的执行。
- **客户端统一选择最终回答。** 优先使用当前轮次的第一份 `final_answer`；缺少阶段标记时保留 Provider 的普通回答。完成通知遗漏正文时，客户端补入已经收到的真实最终回答条目，再交给执行层保存恢复检查点。没有正文的轮次可按恢复策略续接，连续尝试仍没有回答时失败。收到最终回答只关闭追加输入入口；执行仍等待真实终态，不在宽限期后主动中断并伪造完成。
- **停止、追加输入和修改速度都针对确定的 turn。** 请求中带着目标 turn ID；正在运行的 turn 已经换成另一个时，控制请求被拒绝，不会影响后面的 turn。
- **只有新 thread 创建前才可能换账号。** 首选账号的 App Server 不可用时，服务可以在创建 thread 前换到备用账号（decision_3dd52c0692c1）；thread 已存在、创建结果不确定或 turn 已被接受后，恢复始终留在原账号。
- **App Server 的状态可以重建，Portal 的记录是依据。** App Server 进程退出后，客户端重新连接并按会话记录加载 thread；App Server 的内存和会话文件不作为 Portal 的规范历史或执行终态。

## 一轮对话怎样完成

```text
业务入口提交执行请求
        ↓
ExecutionService 从会话记录得到账号路由与 thread
        ↓
CodexBackend 组装输入、权限、工作目录和动态工具
        ↓
CodexAppServerClient 复用连接；thread 未加载时先 thread/resume
        ↓
turn/start 返回 turn ID → ExecutionService 写入检查点
        ↓
客户端消费事件，处理动态工具、提问和控制请求
        ↓
得到最终回复与用量
        ↓
ExecutionService 保存规范消息、会话记录和执行终态
```

| 组件 | 负责什么 |
| --- | --- |
| `CodexBackend` | 统一请求与 Codex 操作之间的转换 |
| `CodexAppServerClient` | 连接、消息配对、事件分发、thread 缓存、活动 turn 跟踪和空闲回收 |
| 调度器（Scheduler） | 按账号控制并发和派发节奏，不解读 Codex 消息 |
| ExecutionService 运行时 | 检查点、取消状态和最终执行结果 |
| SessionManager 与 AgentData | 会话记录，以及规范历史、事件和终态 |
| Codex App Server | Codex 自己的 thread、turn、原生工具循环和进程内状态 |

## 连接

客户端通过账号的 Unix socket 建立 WebSocket 连接（客户端发出的帧按 WebSocket 规定加掩码），然后先发送 `initialize` 请求，再发送 `initialized` 通知。请求用 ID 与响应配对；没有 ID 的通知和 App Server 主动发来的请求都由同一个读取线程分派。连接断开时，所有还没收到响应的请求都立即得到“连接丢失”的错误。socket 能连上只说明进程在运行，不说明某个 thread 已经加载。

| 等待 | 默认时长 |
| --- | --- |
| 普通请求 | 30 秒 |
| 创建、Fork、恢复 thread 等生命周期请求 | 60 秒 |
| `turn/start` 请求 | 120 秒 |

新输入和恢复输入共用 `_follow_turn`：观察通知，并按统一节奏读取精确 turn 快照；必要时跟随后继轮次。轮次持续缺失超过宽限时间时报连接丢失。最终回答由客户端统一选择；完成通知没有正文时读取快照，仍无正文则由适配器最多续接两次。已接受轮次断线后在 180 秒内退避等待原账号 App Server；这一次断线只消耗一次无进展预算，期限结束保存原 turn ID 并延迟重新领取。

## Thread 的生命周期

**创建。** `thread/start` 带上工作目录、模型、速度、审批方式、沙箱、动态工具、开发者指令和 Codex 配置。`threadSource` 默认为 `user`，`ephemeral` 默认为 `false`（两者都可以由运行设置指定）。Portal 把 Codex 配置中的 `project_doc_max_bytes` 设为 0，不让 Codex 自动读取项目文档；是否允许子 Agent（`allow_subagents`）和 Goal 能力按运行设置写入配置。

**每轮执行身份。** 创建、运行、恢复、Goal 和显式压缩共用 thread 配置。环境只保存每个会话稳定的 `PORTAL_EXECUTION_CONTEXT_FILE` 路径；执行前原子更新文件中的 execution ID 和授权上下文。HATG 客户端调用时读取该文件，避免每轮身份变化触发 thread 配置重载。

**加载与释放。** 已有会话的 thread 不在客户端缓存中时，客户端调用 `thread/resume` 加载原来的 thread。`thread/unsubscribe` 只释放 App Server 和客户端中的缓存，不删除 Portal 的会话记录或历史。

**Fork。** 原生 Fork（`thread/fork`）产生新的 thread ID，保留来源的 Codex 上下文；来源和新会话的记录各自独立。来源 thread 缺失时，适配器从 AgentData 取得有效历史，通过 `thread/inject_items` 恢复来源后再 Fork。首轮导入历史后 thread 又失效时，可创建替代 thread 并重新导入，记录 `fork_thread_replaced`。三处历史交付共用同一个转换与注入入口。

**空闲回收。** 客户端每 60 秒检查一次已加载的 thread：空闲满 5 小时的，以及已加载总数超过 30 个时最久未使用的，会被释放。正在运行 turn、Goal 或压缩的 thread，被锁定的 thread，以及刚创建、还没跑过第一轮的 thread 都不会被释放——后者只存在于 App Server 内存中，释放后无法恢复。

## Turn、事件与工具

**开始一轮。** `run_turn` 把文本和规范化后的图片交给 `turn/start`。App Server 接受后，客户端立即把 turn ID 交给 ExecutionService，建立输入与执行的对应关系和恢复检查点。加载时已有进行中的 turn，只有其中 userMessage.clientId 等于本次输入 ID 才可接回；其他活动轮次按忙碌延后处理。等待开始响应时取消或超时，会从已缓存的 turn/started 取回真实 ID 并中断。

**事件。** App Server 通知中的消息、turn、thread、用量和错误被转换成数量有限的 Provider 事件；命令、文件、网页、MCP、动态工具、协作和图片被归入统一的工具类别。逐字输出的增量不写入 Portal 的活动记录。

**消息身份与唯一录入。** Provider 通知和运行期间轮询到的完成项统一进入实时事件入口，使用稳定的 Provider item 身份生成 Segment。运行内重连可以重放当前接受回合的已完成项；消费者只引用同一个 Segment，不把正文相同的不同事件合并。原生记录还提供恢复时的消息身份、压缩后的替换历史、上下文窗口和终态错误；这些读取不会构成独立的内容补录入口。具体内容边界见 decision_b1ab45c4b3ec。

**接受回合的身份。** start 返回的 turn ID 是该执行等待的回合。内联审查会在同一 thread 的轮询列表中暴露子回合；子回合的结束或中断不能替代父回合的结果。轮询和 Worker 续接先按已接受的确切 ID 读取状态与结果，不能直接改绑到列表顶部的回合。确切回合查询从最新的小页开始，未命中时沿 Provider 游标继续查找，在统一时限内找到目标或读至末页；超时及重复游标属于查询失败，不能当作回合不存在。只有检查点指向的回合确实不存在时，续接才采用当前活动回合；压缩和 Goal 的后继回合仍遵循各自明确的交接协议。

**原生记录定位。** 查找账号下的历史文件时识别符号链接并防止循环，始终在所属账号中寻找已确认身份的会话。

**动态工具。** App Server 请求调用动态工具时，由 Portal 的工具分发器执行。工具名必须是已声明且不冲突的名字；相对工作目录的路径不能包含 `..`，解析后必须仍在工作目录内。Portal 无法识别或处理的 App Server 请求得到明确的协议错误。

**谁回答线程的请求。** App Server 把一个线程的请求发给所有订阅它的客户端，取第一个回答。只有正在运行 turn 或 Goal 的客户端回答动态工具、审批、用户输入和 MCP elicitation 请求；仅保留订阅的客户端不回答。

**提问与原生工具。** 需要用户回答的提问和 MCP 交互交给 Portal 相应的控制入口处理（decision_2fe6fd5367fb 中的 `AnswerResidentRequest`）。Codex 自己的原生工具循环由 App Server 负责；Agent Harness（decision_5c62deb8253a）只处理 Portal 自己的工具批次，对原生工具只能观察事件，并通过追加输入给出软性提示。

## 压缩、Goal 与协作

**压缩（`compact_thread`）。** 压缩在 thread 的独占写入锁内进行。thread 未加载时带完整 thread 配置加载，确保使用 Portal 的压缩提示；已有其他写入或压缩在进行时，客户端短暂等待后返回可识别的“忙碌”或“冲突”。压缩总时长最多 600 秒，连续 300 秒没有进展即失败；期间每 5 秒读取一次状态。成功后把替换后的历史或 Codex 的检查点交给 Portal。

**Goal（`run_goal`）。** Goal 是 Codex 中一个跨多轮推进的目标。`run_goal` 一直等到 Goal 状态成为 `blocked`、`complete`、`usageLimited` 或 `budgetLimited`；其中某一轮结束只说明这一轮生成完了，不代表 Goal 完成。

**子 Agent 重连。** 连接断开后清理连接所属的子 thread 订阅关系。恢复时重放 subAgentActivity，重新加入运行中的子 thread，并重建卡片状态；已经重放的 item 仍按身份去重。

**协作模式。** 协作模式（例如计划模式）和子 Agent 控制通过 thread 配置与原生事件传递。

## 并发、控制与中断

**写入互斥。** 同一 thread 的写操作同时受进程内锁和文件锁（`${staging_root}/portal_locks/codex-app-server-threads/` 下）保护，跨进程也不会并发写入。等待锁期间持续检查 Portal 的取消请求。写入者忙碌时返回可识别的容量或续跑状态并保留检查点；临时容量问题标记为可重试的失败。

**运行中的控制。** 对正在运行的 turn 可以做三件事，都要带上目标 turn ID：

| 控制 | 协议方法 | 作用 |
| --- | --- | --- |
| 追加输入 | `turn/steer` | 把用户的新消息立即交给正在进行的这一轮（decision_2a3ff353165c 中的 `InjectMailboxItem` 与 `DeliverMessage` 的 `inject_active_turn`） |
| 修改速度 | `turn/settings/update` | 把运行设置中的速度（`priority` 或标准速度）立即应用到正在进行的这一轮；App Server 返回 `applied` 表示已生效，其他结果按未生效处理 |
| 中断 | `turn/interrupt` | 停止这一轮，App Server 以“已中断”的错误结束它 |

**合并重复的中断。** 同一 thread 与 turn 上并发发起的中断（例如 Worker 心跳和运行清理各发一次）共用同一个原生请求及其结果，不会重复发送或重复等待。只有没有在途中断、并且已经收到同一 turn 的明确终态时，才直接返回；空闲、普通输出或其他 turn 的终态都不算证据。已经发出的中断必须等到原请求返回后才释放对这次执行的占用，即使期间已收到终态。请求出错时保留原来的错误含义，同一次等待中不盲目重试不确定的控制；网络等待期间不持有客户端的全局状态锁。

**原生工具结果与单一录入边界。** App Server 的 `turn/interrupt` 结束模型的等待，但不保证其所有子进程已经停止。只按原生真实 command/item ID 记录过程结果，不把模型 wrapper 的 aborted 返回改成成功，也不把进程完成归到新的 turn。计划移交保留原 turn，新 Worker 通过该 turn 的实时通知及精确快照投影回同一个 Agent trace；相同命令正文的独立调用仍是独立事实。

**取消的终态。** 用户取消通过精确的 `interrupt_turn(thread_id, turn_id)` 送到 App Server。App Server 以“已中断”结束后，ExecutionService 根据取消请求和 Provider 终态确定这次执行的最终状态。

## Worker 换代时接着运行

ExecutionService 的 Worker 进程需要换代时（例如发布新代码），正在运行的 Codex 执行交给新一代 Worker 继续，App Server 本身不重启。进程交接的整体流程见 decision_075f66430606，检查点与控制协议见 decision_6ad0384e020d。

**旧 Worker 留下交接标记并解除跟随。** 旧 Worker 在检查点中保存 `worker_handover`，记录旧 Worker 的 ID 和确切的 thread 与 turn，并以 `turn_kept_running=true` 保留原生一轮。计划移交不调用 `turn/interrupt`，也不暂停 Goal。客户端的 `detach_requested` 与用户取消分开：原生命令继续运行；旧 Worker 已开始的动态工具调用须先回传真实结果，之后的新请求留给新 Worker，不返回伪造的取消结果。新 Worker 沿用同一次执行、同一账号、同一会话和同一条业务消息，先读取原 turn 的实际状态：

- 原 turn 已经完成，或检查点已记录 Provider 完成：恢复原结果，不再发送输入。
- 原 turn 仍在运行：继续观察直到终态。
- 原 turn 实际被中断、身份不匹配或出现其他真实错误：按 Provider 故障恢复规则处理。

计划交接只改变跟随这一轮的 Worker，不使用 Provider 错误恢复策略，不消耗也不重置快速续跑和无进展预算。真实故障按 decision_425e428410e0 的恢复规则处理。

**跨代的检查点。** 检查点保留同一次执行、Provider thread、turn 和 Worker 的准备状态。下一 Worker 用原 turn ID 恢复观察，沿用原有开始原因与输入身份。错误恢复需要发出新的续接输入时，保存待发提示及已用预算；检查点不复制完整原始输入。

**Goal 与用户停止。** 计划移交只解除跟随，下一 Worker 观察原 Goal，不暂停、重建或重新激活 Goal。用户停止优先生效，停止后不再安排续接。Portal 不把缺失的 Goal `origin` 自动解释为用户授权；协议中的 `user` 来源要求明确的用户操作证据。

## 权限与工作目录

Codex 的权限和工作目录由执行准备阶段确定（decision_6ad0384e020d）。调用方要么选择一个命名的权限预设，要么直接给出沙箱参数，二者只能取其一；通用沙箱值只有 `read-only`、`workspace-write` 和 `danger-full-access`，由权限层展开预设并附加可访问的目录。客户端只传递已经准备好的工作目录、沙箱和能力配置；宿主进程的工作目录、socket 身份或已加载的 thread 都不会扩大 Resident 的文件权限。

## 相关实现与测试

客户端实现在 `management_portal/codex_app_server_client.py`，适配器在 `management_portal/execution/backends.py` 的 `CodexBackend`，执行运行时在 `management_portal/execution/runtime.py`，Worker 交接在 `management_portal/execution/worker.py` 与 `management_portal/execution/scheduler.py`。协议基础参见 OpenAI 的 Codex App Server 文档；Portal 的具体行为由 `tests/cases/test_codex_app_server_client.py` 与 `tests/cases/test_execution_layer.py` 验证。
