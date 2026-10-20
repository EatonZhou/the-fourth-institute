---
kb_id: decision_25eb93d803a4
type: decision
project: AI员工
created_at: '2026-09-03'
version:
  number: 27
  created_at: '2026-09-03'
  created_by:
    user: ''
    agent: ''
  updated_at: '2026-10-18T16:15:48Z'
  updated_by:
    user: tfi-exported-user
    agent: resident_73cb83291316
importance: 10
metaness: 3
ai_generated: false
links:
  source:
  - ${code_root}/management_portal/execution/account_monitor.py
  - ${code_root}/management_portal/execution/provider_catalog.py
  - ${code_root}/management_portal/execution/event_projection.py
  - ${code_root}/management_portal/execution/isa.py
  - ${code_root}/management_portal/execution/contracts.py
  - ${code_root}/management_portal/execution/service.py
  - ${code_root}/management_portal/execution/transport.py
  - ${code_root}/management_portal/execution/scheduler.py
  - ${code_root}/management_portal/execution/worker.py
  - ${code_root}/management_portal/execution/worker_manager.py
  - ${code_root}/management_portal/execution/runtime.py
  - ${code_root}/management_portal/execution/session_manager.py
  - ${code_root}/management_portal/execution/backends.py
  - ${code_root}/management_portal/resident_shell_environment.py
  - ${code_root}/management_portal/execution/agent_data_projection.py
  - ${code_root}/scripts/portal_services.py
  - ${code_root}/tests/cases/test_execution_layer.py
  related:
  - decision_f1331d57be89
  - decision_3dd52c0692c1
  - decision_4527448ad07f
  - decision_96a887d83e8a
  - decision_d40f59f5508f
  - decision_6ad0384e020d
  - decision_8ac0b5a6c0e5
  - decision_075f66430606
  - decision_91eb3626d323
  - decision_425e428410e0
  - decision_b1ab45c4b3ec
  - decision_5c62deb8253a
  - decision_ef1d6c925955
  - decision_86721bb4e8c8
  supersedes: []
  derived_from: []
---
# ExecutionService 模块与运行架构

ExecutionService 是 Portal 中统一执行模型请求的后台服务。Resident 回复一条消息、Paper 页面生成摘要、定时与自动触发的工作调用模型，都会向它提交一次“模型执行”，然后用同一个执行 ID 查询进度、追加输入、取消、等待和读取结果。提交方只关心自己的业务对象；模型服务供应方（Provider，例如 Codex、Claude Code、Kimi Code）的进程、账号容量、崩溃恢复和执行内容的保存都由 ExecutionService 负责。

本文说明 ExecutionService 在 Portal 中的位置、由哪些组件组成、一次模型执行怎样流过这些组件，以及进程失联或重启后怎样恢复。每个远程调用方法的参数、返回和错误见 decision_6ad0384e020d；Scheduler 的排队与租约见 decision_8ac0b5a6c0e5；执行账号见 decision_d40f59f5508f。

## 先理解一次模型执行

**一次模型执行**是从 Portal 接受一个模型请求，到得到完成、失败或取消结果的完整过程。它有一个稳定的 `execution_id`，形如 `execution-` 加 24 位小写十六进制。网络重连、服务滚动更新、Worker 换代都继续使用这个 ID，所以提交方可以把它保存在自己的业务记录里，之后随时找回这次执行。

一次执行依次经过这些对象：

- **请求（ExecutionRequest）**：提交方描述要做什么，包括提交者、提示词、服务类型（持续会话 `agent_session` 或一次性生成 `chat_completion`）、Provider、操作（例如新建会话、进行一轮对话、Fork、压缩、释放）、会话地址和业务归因信息。字段规则见 decision_6ad0384e020d。
- **队列**：已经保存在数据库中、等待执行进程领取的请求。**Scheduler**（持久调度器）拥有队列，按账号容量、优先级、提交者之间的公平性和同一会话串行的要求，决定下一条由谁执行。
- **Worker**：实际运行模型调用的进程。Worker 领取一条执行时，Scheduler 发给它一份**领取资格**（claim）：Worker ID、一个随机的领取令牌和一个到期时间（**租约**）。只有持有当前有效资格的 Worker 可以开始执行、续租、上报事件、保存恢复点或提交结果；资格失效后，旧 Worker 的任何写入都被拒绝。
- **Runtime（ExecutionRuntime）**：Worker 内按固定顺序完成一次执行的编排器：准备会话，记录输入，调用 Provider，保存恢复点，写入执行内容，最后更新会话地址。
- **Provider 会话与会话地址**：Provider 自己的 thread 或 session 称为 Provider 会话；Portal 用稳定的会话地址 `binding_id` 指向它，业务代码因此不依赖 Provider 内部 ID。两者的对应关系由 SessionManager 维护（decision_91eb3626d323）。
- **恢复点（checkpoint）**：Scheduler 为运行中的执行保存的恢复位置，说明 Provider 是否已经接收输入、是否已经完成，以及继续写入执行内容所需的最小状态。
- **AgentData**：保存输入、模型输出、工具调用、上下文和最终结果的内容存储（decision_b1ab45c4b3ec），回答“这次执行产生了什么”。Scheduler 的 PostgreSQL 回答“这次执行现在由谁运行、能否取消、是否需要恢复”。两边用 `execution_id` 对齐。
- **用量**：执行消耗的 token。AgentData 保存执行的唯一身份、生命周期、输入归属与单次执行最终报告的用量；Scheduler 按 UTC 小时累计用量账本，供 Home 页和运营查询使用（decision_f1331d57be89）。

## 设计点

### 提交与观察

- **业务代码通过 ExecutionService 客户端（facade）操作模型执行。** facade 是运行在调用方进程里的一个 Python 类，把提交、恢复、查询、等待、追加输入、取消、调整优先级、改换账号和确认终态包装成方法。Portal Web 和 SocietyService 都不在自己的进程里创建 Provider 运行时。
- **提交重试始终使用原来的 `execution_id`。** 服务暂时不可达时，facade 每 250 毫秒用同一个 ID 重新提交，直到服务回应。Scheduler 在解析账号和准备会话前比较首次提交时保存的请求摘要：相同 ID、相同内容返回已有记录；相同 ID、不同内容被拒绝。之后调整优先级或路由不改变这个摘要。
- **恢复一次执行前先核对业务预期。** `resume` 读取已有执行，可以同时检查关联 ID（`correlation_id`）和执行能力（例如 `agent_session:codex`），任一不符就失败，调用方不会误接管另一项业务的执行。
- **等待可以跨服务重连。** facade 把长时间等待拆成每段最长 30 秒的服务端等待，记住事件游标和 Scheduler 更新时间；服务暂时不可达时每 250 毫秒重连，继续等待同一个执行，不会新建执行。
- **只有调用方明确要求时才发送取消。** 调用方在等待时可以提供一个“业务是否已取消”的检查函数，facade 每 250 毫秒检查一次，结果为真时向原执行发送一次取消。等待到达调用方设置的总时限时，facade 抛出等待超时错误，附带最后一次快照和最后一次连接故障说明，不取消执行；执行继续在后台运行，调用方可以稍后再等。
- **状态快照的形状固定。** 快照始终包含 `execution_id`、`actor`、`status`、`request`、`events`、`result`、`error`、`scheduler` 八个顶层字段。查询时可以不要事件或结果，这时对应字段为空，`request` 与 `scheduler` 摘要始终保留。
- **终态快照必须带匹配的结果。** 等待返回的终态快照缺少对应的执行结果时，facade 再读一次权威状态；仍然缺少时报告协议错误，不把这种快照当作执行结束。

### 通知、健康与运行环境

- **变化通知只提示“有变化”，读者以快照为准。** 执行状态变化时，服务通过 NATS 发布 `portal.execution.changed.<execution_id>`；Worker 上报活动事件时发布 `portal.execution.activity.<execution_id>`；提交终态时如果带有用量，再发布 `portal.projection.changed.token-usage.snapshot` 提示 Home 刷新用量。发布失败不影响已经写入 PostgreSQL 的状态，收到通知的一方重新读取快照。
- **健康查询只读。** 它返回服务实例、构建版本、Scheduler、通知连接和 AgentData 的当前状态，不回收租约、不清理 Worker，也不修正 AgentData 中的状态。
- **获得授权的 Resident shell 可以访问 Portal 数据库。** 一次执行同时满足以下条件时，支持本地 shell 的 Provider 在启动该 Resident 的 shell 前传入 `PORTAL_DATABASE_URL`：请求的 `metadata.business_type` 为 `resident`，`metadata.resident_id` 等于执行的 `agent_id`，权限配置为 `owner`（完整文件访问），`metadata.filesystem_scope.mode` 为 `A`。连接串只在运行时交给 shell，不写入持久请求；其他执行不接收这个变量。连接串从服务环境读取，缺少时读取 `portal-postgres.env`。
- **ExecutionService 也是 HATG ISA 的拥有服务。** 模型接口的登记与调用、执行账号的查看与调度设置、用量查询这几类指令在 ExecutionService 中执行（decision_4527448ad07f）。它们与上面的执行 RPC 共用同一个 Unix socket，通过 `instruction.execute` 方法进入（decision_96a887d83e8a）。

## 持久事实与临时协调

agent.executions 保存唯一的 execution_id 与执行生命周期；agent.execution_inputs 保存合并或追加到该执行的输入。Resident 开始准备即建立 Execution，准备失败仍留下终态。Scheduler 的队列行保存领取资格、租约、重试及事件投递工作，使用同一 execution_id；它与 AgentData 的状态在同一数据库事务中提交。

Lineage 的 resident_execution_work 协调准备、提交、观察、停止和确认，resident_execution_release_work 保存未完成的释放。工作完成后清理这两类协调行，执行历史由 AgentData 保留。不另建 job、turn、mailbox round 或 invocation 的 Resident 执行账本。Trigger Path 自己保存事件起因、步骤与对 Execution 的引用，Resident 输出触发的新 Event 建立新的路径。


ExecutionSettings 持有执行观察开关的本地持久投影。重复打开已具备完整结构的设置表不执行结构变更；初始化仅为确实缺失的字段执行升级，并保留已有开关值与管理归属。正常读取和服务恢复不能因无需变更的 DDL 申请排他表锁而与在线备份互相阻塞。Lineage 继续通过同一设置接口恢复工作流投影，不承担设置表的存储迁移细节。

## 组件及其责任

| 组件 | 负责什么 | 状态保存在哪里 |
| --- | --- | --- |
| 业务调用方（SocietyService、Portal Web、后台脚本） | 组织业务对象和提示词，保存业务记录与 `execution_id` 的关联，解释结果并写回业务状态 | 各自的业务存储 |
| ExecutionService 客户端 facade | 提交、恢复、查询、等待、追加输入、取消、调整优先级、改换账号、确认终态，并处理 socket 短暂中断 | 不保存持久状态 |
| ExecutionServiceServer | 独立进程中的 RPC 服务端，持有 Scheduler、提交准备器、账号观察器、通知发布器，并运行定期维护 | 进程内 |
| ExecutionPreparation（提交准备器） | 入队前校验请求，为持续会话解析执行账号和路由 | 不保存 |
| Scheduler | 队列、优先级、领取资格、租约、取消、Worker 登记、运行中输入、账号额度状态和小时用量账本 | Scheduler PostgreSQL |
| AccountMonitor | 定时读取各执行账号的额度、余额和历史用量（decision_d40f59f5508f） | 进程内样本；历史用量存 PostgreSQL |
| ExecutionWorker | 领取执行，维持心跳和控制循环，调用 Runtime，用当前领取令牌提交结果 | 只持有当前资格的进程内状态 |
| WorkerManager | 启动新一代 Worker、检查就绪、切换领取权、让旧 Worker 退出并处理遗留资格（decision_075f66430606） | 健康文件 |
| ExecutionRuntime | 规定会话准备、Provider 调用、内容写入和完成提交的顺序 | 不保存 |
| SessionManager | 把会话地址解析为 Provider 会话，执行成功后更新对应关系（decision_91eb3626d323） | AgentData |
| Provider Adapter | 把统一请求翻译成 Codex、Claude Code、Kimi Code、DeepSeek Harness 或 Chat Completion 的原生协议、事件和中断（decision_425e428410e0） | 不保存 |
| Agent Harness | 在一次 Provider 调用周围增加上下文治理、工具治理和恢复能力（decision_5c62deb8253a） | 插件自己的产物与效果账本 |
| AgentDataProjector | 把输入、Provider 观察、工具、压缩、输出与用量写入 AgentData；生命周期由接纳协调者和 Scheduler 负责 | AgentData |

## 整体架构

```text
Portal Web / SocietyService / 后台脚本
                   │  ExecutionRequest
                   v
        ExecutionService 客户端 facade
                   │  Unix socket RPC（每条请求一行 JSON）
                   v
          ExecutionServiceServer ──── NATS 变化通知
            │         │
            │         └─ AccountMonitor ── Provider 额度与余额接口
            v
        Scheduler ───── PostgreSQL（队列、领取资格、额度状态、用量账本）
            │ 领取资格 + 租约
            v
       ExecutionWorker  <── WorkerManager 管理代际
            │
            v
      ExecutionRuntime
       │      │        │
       │      │        └─ AgentDataProjector ── AgentData
       │      └───────── SessionManager ─────── 会话地址 → Provider 会话
       └──────────────── Agent Harness → Provider Adapter ── Provider 原生会话
```

Dev 与 Release 两个部署各有一个 ExecutionService 进程和自己的 Unix socket，但连接同一个 Scheduler 数据库，所以队列和执行状态只有一份。两个 Worker 池中处于激活状态的同代 Worker 都可以领取新工作。容量设置在领取和状态查询的事务中从数据库读取，保存后两边立即采用同一份设置。

## 一次工作怎样流过系统

1. 业务调用方构造带稳定 `execution_id` 的请求，并把这个 ID 写入自己的业务记录。
2. facade 通过 Unix socket 提交请求；服务不可达时每 250 毫秒用同一个 ID 重试。
3. 提交准备器校验请求。使用 Provider 路由约束的隔离 Chat Completion 请求，还要按 LLM API Registry 核对约束（decision_6ad0384e020d）。持续会话请求由对应 Provider 的账号适配器解析执行账号：已有会话沿用它原来的路由；新会话没有指定路由时，使用适配器的默认账号，例如 Codex 按账号目录选择新会话的默认账号（decision_3dd52c0692c1）。
4. 新请求准备完成后保存为 `queued`；重复提交在准备前已返回原记录。
5. 激活中、具备相应能力的 Worker 领取它，得到领取令牌和租约，再调用 `start` 把状态改为 `running`，交给 Runtime。
6. Runtime 先由 SessionManager 准备会话，再由 AgentDataProjector 记录本次输入。已经被取消的执行在这里结束，不会调用 Provider。
7. Provider 服务依次应用 Provider 运行选项和 Agent Harness，然后由 Provider Adapter 调用原生 Provider。调用期间 Adapter 通过统一回调报告会话与本轮身份、活动事件、用量、运行中输入的投递结果和可中断的目标。
8. Provider 返回后，Runtime 先把包含输出和用量的“Provider 已完成”恢复点写入 Scheduler。
9. Runtime 写完 AgentData 中的执行内容，再由 SessionManager 更新会话地址。最后 Worker 调用 `finish`，提交 `completed`、`failed` 或 `cancelled`。
10. 调用方通过查询、等待或变化通知发现终态，读取结果后确认（acknowledge）。确认过的终态记录保留 24 小时后被清理。

先写“Provider 已完成”恢复点、再写 Portal 自己的内容，是为了把 Provider 调用和 Portal 写入分开记录。Provider 已经返回而进程还没写完 AgentData 时，新 Worker 从恢复点知道 Provider 这一步已经完成，跳过再次调用，只补写 AgentData 和会话地址。

## RPC 传输和进程边界

每个部署的 socket 位于 `<实例根目录>/<实例名>/execution-service.sock`，环境变量 `PORTAL_EXECUTION_SERVICE_SOCKET` 可以改写本实例的路径。服务端创建 socket 前删除遗留文件，创建后设置权限 `0660`，关闭时删除。

一次连接交换一行 JSON。请求是 `{id, method, params}`；成功响应返回相同 `id`、`ok=true` 和 `result`；失败响应返回相同 `id`、`ok=false`，以及异常类名和消息。服务端为每个连接开一个线程处理；客户端提前断开时，服务端结束这次响应，不再尝试写入。`instruction.execute` 是例外：非流式指令返回一行，流式指令每批一行，最后一行表示结束（decision_96a887d83e8a）。

客户端的连接阶段最多等 5 秒，等待响应默认 30 秒，调用方可以按方法设置，最小 0.1 秒。只有连接尚未建立时的失败属于服务不可用（`ExecutionServiceUnavailable`），客户端可以改试另一实例的 socket。连接建立后发生读取超时、断开、空响应、非法 JSON 或响应 ID 不匹配时，客户端报告结果未知（`ExecutionResponseUnknown`），传输层不重放请求。facade 对只读等待和带稳定执行 ID 的幂等提交继续重试；其他写入由调用方查询权威状态或使用其幂等回执收敛。

远端的 `KeyError`、`ValueError`、`PermissionError` 在客户端还原为同类异常；领取资格已经属于别人时还原为 `ExecutionClaimOwnershipLost`；其他异常还原为 `RuntimeError`。

这组 RPC 信任同机进程和 socket 文件权限。请求中的 `actor` 由可信的 Portal 内部调用方提供，用来说明这次执行归谁所有；能连接 socket 的本地进程可以伪造它。面向不可信客户端的逐请求认证需要作为共享 RPC 安全机制统一增加。

服务启动时由 Scheduler 持有唯一的账号目录与容量设置，ExecutionPreparation 复用该目录并管理会话准备。执行结果反序列化与终态、用量字段、时间格式集中在协议模块中。

## 状态所有权和恢复

Scheduler 的 PostgreSQL 保存全部排队与控制状态。Worker 和 Runtime 只持有当前领取资格期间的进程内状态；资格到期或被转给别人后，旧进程无法再写恢复点、事件或终态。

ExecutionServiceServer 每 5 秒运行一次维护（可配置，最小 0.1 秒）：Scheduler 回收心跳过期的 Worker 和到期的租约，然后把 Scheduler 已经结束、而 AgentData 仍显示运行中的执行补写为对应的终态。一次维护出错只记录日志，下一周期继续。健康查询不触发维护。

租约到期时，执行的去向取决于它走到了哪一步：

| 情况 | 结果 |
| --- | --- |
| 仍在排队 | 保持排队 |
| 已领取但还没开始 | 回到队列 |
| 正在运行，恢复点标记可恢复 | 回到队列，由新 Worker 接续 |
| Provider 已完成 | 新 Worker 跳过 Provider 调用，补写 AgentData 和会话地址 |
| 正在运行的一轮对话或压缩，没有可恢复的证明或已经请求取消 | 回到队列，新 Worker 只中断 Provider 仍在进行的工作，再标记为失败或取消（decision_8ac0b5a6c0e5） |
| 其他正在运行、没有可恢复证明的操作 | 标记为失败 |
| 其他已经请求取消的执行 | 收敛为取消 |

服务重启后重新打开同一个数据库，排队和运行中的执行都还在。服务维护循环清理已确认且超过 24 小时的终态记录，以及超过 7 天的未确认终态；确认和提交终态本身只写当前执行。Provider 的外部动作能否恰好执行一次，取决于 Adapter 和恢复点提供的证据；没有证据时，系统按“可能已经发生”处理，不当作没有发生。

## 执行事件、页面展示与技术恢复

首次 `started` 事件与状态在同一事务中持久保存，另一个实例或重启后的服务也能返回这次开始事实。其余 Scheduler 事件是各进程最多保留最近 256 项的观察窗口；Worker 活动另写入活动日志。观察窗口存在缺口时，调用方读取当前快照和 AgentData。

Provider 的原生事件先由 `execution/event_projection.py` 转换成统一的执行活动和输出：它解释事件类型、输入输出身份、终态、流式文本和活动记录。输出提取、持久记录和事件重放都使用这一套规则，不受页面展示长度的限制；遇到未登记的事件类型，按协议登记的方式处理，不从展示文字推测执行是否成功。

页面需要的摘要由 `portal_chat_workspace_projection.py` 在副本上生成，包括截断和空白压缩。调整页面展示长度不会改变执行输出、已保存的活动、消息身份或重放结果。活动日志的总量和保留期是单独的存储策略；历史中已经丢失的内容无法由展示层重建。

Agent Harness 插件的计划、开关、原生控制和技术续跑属于执行层，分别由 Harness、Runtime、SessionManager 和 Provider Adapter 负责。符合策略的续跑（例如 Provider 出错后发送 `continue`）在原执行和原 Provider 会话内完成，受取消、恢复点、插件自己的次数上限和 Adapter 的连续无进展预算约束（decision_425e428410e0）。策略禁止、被取消、不可恢复或预算用完时，执行提交真实的终态。

SocietyService 决定业务上何时提交工作、何时请求取消、怎样使用终态结果。它可以把用户的运行设置转交给执行层，但不解释插件默认值、恢复资格和技术计数，也不会在执行失败后自动生成一条新的 `continue` 用户输入。用户明确要求继续，或 Lineage 流程决定重试，都作为新的业务输入提交，并带有可追溯的业务来源。

## 计划内 Worker 交接

部署新代码时，WorkerManager 先让新一代 Worker 就绪，再请 Scheduler 把领取权原子地切换给它。旧 Worker 对手里每一项可恢复的执行先在 Scheduler 中保存交接标记，再解除对 Provider 的跟随，等已发出的控制调用返回后逐项释放领取资格。Codex、Claude Code、Kimi Code 和 DSH 保持原生轮次运行；新 Worker 领取同一个执行并接回原轮次，保留请求、会话、路由和业务消息。

对业务调用方来说，交接期间执行一直处于非终态：不会出现失败消息，不会触发 Resident 的失败续跑，也不消耗 Provider 故障恢复预算。用户的停止请求优先于交接；没有接收或恢复证据时不会盲发 `continue`；真实故障和不可恢复的任务仍然明确失败。交接不保证零延迟，也不保证 Provider 的外部动作恰好执行一次。进程管理见 decision_075f66430606，交接相关 RPC 见 decision_6ad0384e020d。

## 执行账号

ExecutionService 维护全部执行账号：账号目录、额度与余额读数、按账号的并发与发送间隔、Close 阈值和额度保护。Scheduler 按账号的容量键限制并发，AccountMonitor 定时读取额度，额度不足时暂停这个账号上的自动工作。完整规则和 `accounts.*` 接口见 decision_d40f59f5508f。

## 相邻 Design

- Portal Business Task（Web 进程内的短期后台函数）不进入这里的队列，也没有 `execution_id`；两者的选择边界见 decision_86721bb4e8c8。
- AgentData 的不可变内容模型：decision_b1ab45c4b3ec。
- 执行时附加给模型的 Portal 上下文怎样生成：decision_ef1d6c925955。


## 模型请求时间观察

Portal System Lineage 的“模型请求计时开关”Event 拥有全系统时间记录的开关状态，ExecutionService 的 request_timing 模块读取它的本地持久投影；AgentDataProjector 消费 Provider Adapter 已取得的时间副本。观察不转发模型请求，不改写 base URL、认证头、代理环境、TLS、压缩、重试或 HTTP/WebSocket 方式，不读取模型正文来提取关联 ID，也不在响应回调中同步请求计时服务。

started_at 表示本地实际发送模型请求的时间。Portal 直接调用的 API 在现有传输边界取时间；DSH 在 Portal 自己拥有的 fetch 边界复制会话 ID 和时间，原参数与返回值原样交给现有运行时。原生 Provider 只有在已有事件明确提供请求边界时才能填入；Codex、Claude Code、Kimi 和 OpenCode 当前适配器未提供可靠 dispatch 事件的段，其 started_at 留空。turn 开始、消息创建、首 token、step 开始不能代替它。finished_at 优先采用真实请求完成时间；缺失时仍可保存已观察到的 segment 完成时间，不能从两者混合推导未知的请求耗时。

观察只复制必要的两个时间字段。观察回调异常、设置不可读或缺失关联时丢弃计时，不丢弃模型输出、不追加重试，也不改写错误状态。直接请求的传输诊断仍独立保留真实状态；上游 403、断连或超时按 Provider 原有语义返回，计时层不能保证远端永不报错。

管理员可以在 Portal System（lineage_ed619225d417）打开或关闭 client_key 为 `system:request-timing:enabled` 的 Event，也可以在 Portal Management 的“执行设置”分栏切换“模型请求时间记录”（decision_30528aff7035）。GetExecutionSettings 返回 `model_request_timing_enabled` 投影；UpdateExecutionSettings 由 Lineage owner 将修改转为对同一 Event 的 PauseComponent（关闭传 `paused: true`，打开传 `paused: false`）。开关默认开启，关闭只停止后续持久化的可选请求时间副本；模型请求、已有 segment、原生完成事件和传输诊断继续遵循原有生命周期。手动触发开关所连的状态 Effect 只读取 GetExecutionSettings，不启动代理进程。

Lineage 在 Event 开关、定义变化和启动恢复时，把有效状态写到 ExecutionSettings 的共享数据库单行投影。投影接口只写本地数据库，Execution 不反向调用 Lineage。`managed_workflow_keys` 只记录哪些开关曾被图接管：从未安装时保留原有设置，已安装后删除 Event 则投影为关闭，Execution 内部直接修改已接管字段会被拒绝。`harness_recording_enabled` 采用同一机制，来源是系统演化 Lineage（lineage_048353291724）的 `system:harness-recording:enabled` Event，同样可以在“执行设置”分栏切换，记录范围见 decision_fd760e898f9a。滚动升级期间计时投影沿用 `provider_timing_proxy_enabled` 物理列，避免旧代进程读列失败；模型完成时持久化计时副本的路径只读这一列，新增设置列尚未升级完成时也不会丢弃计时。

同一行还保存 Harness 保留天数 `harness_retention_days`（21–365 天，默认 30）。它没有 Event 开关，由管理员直接设置；Harness 产物保留清理和每小时调用收集读取它，删除超过天数的 Harness 策略观察、`harness_turn_calls` 调用记录和未被对话或 operation 引用的 Harness 产物。下限是 21 天：挑战者最长运行 21 天，离线重放读取最近 14 天的调用，更短的保留期会让评估缺少数据。

同一行的 `permission_system_enabled`（默认开启）是权限系统开关。它同样没有 Event，由管理员直接切换；每个 Portal 进程最多 3 秒后按新值选择凭证模式或开放模式，开启前先补登关闭期间的变化，规则见 decision_b820faf42218。

UpdateExecutionSettings 接受两项开关、保留天数和权限系统开关中的任意组合，至少一项；先校验全部参数，再切换开关，然后写入天数，最后切换权限系统；开关绑定失效时整个请求失败，天数和权限系统开关也不变。天数修改不经过 Lineage Event，也不进入 `managed_workflow_keys`。保留期只在清理运行时生效：Harness 清理由系统演化 Lineage 的手动 Event 或每日定时 Event 触发，两者暂停时到期数据不会被删除。

### ExecutionService 换代与旧路由退出

新启动的 ExecutionService 不创建模型代理监听器，新版四类原生 Adapter 不生成本地代理地址，也不安装计时插件或附加计时请求头。模型传输的生命期由 Provider 自身管理，ExecutionService 的更新不在新请求的数据路径上。

滚动升级时，旧版 Provider 已经保存的代理地址可能还被原生长会话使用，因此只对继承的旧监听描述符保留兼容转发。此兼容模块始终停止内容观察，health 不再发布可用计时路由；不因升级、开关切换或固定超时强行关闭既有请求。它不承担新版计时，也不能重新开启。既有会话按正常会话生命周期退出后，后继会话使用原有 Provider 地址；无继承描述符的启动不再创建旧代理。

ExecutionService 的 RPC 继续使用 SIGHUP 平滑换代：新进程继承监听 socket，就绪后接管新请求，旧进程结束已接受的请求。新进程初始化失败时当前进程继续服务。兼容期的旧 HTTP 上传、响应及 WebSocket 保持到自然结束；长期存续的旧连接可能延长旧代进程生命周期。进程崩溃、SIGKILL、主机故障或远端断连仍可能打断旧连接。

### 验证边界

定向回归覆盖四类原生 Adapter 不需要计时服务即可执行、原目标地址与认证不被覆盖、未知时间不伪造、设置权限及持久化、观察失败不改变原请求参数和返回值，以及无代理的 RPC 换代与旧传输排空。真实核心 TAU 用于检查模型回复、会话操作和内容持久化；每次交付报告实际运行结果及未覆盖项，不能用本地模拟测试宣称真实账号永不会 403。
