---
kb_id: decision_8ac0b5a6c0e5
type: decision
project: AI员工
created_at: '2026-09-03'
version:
  number: 19
  created_at: '2026-09-03'
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
  - ${code_root}/tests/cases/test_account_close_threshold.py
  - ${code_root}/management_portal/execution/accounts.py
  - ${code_root}/management_portal/execution/account_quota.py
  - ${code_root}/management_portal/execution/scheduler.py
  - ${code_root}/management_portal/execution/service.py
  - ${code_root}/tests/cases/test_execution_layer.py
  - ${code_root}/tests/cases/test_execution_token_usage.py
  related:
  - decision_f1331d57be89
  - decision_25eb93d803a4
  - decision_d40f59f5508f
  - decision_6ad0384e020d
  - decision_850e6248d680
  - decision_4dea0db1f45a
  - decision_075f66430606
  supersedes: []
  derived_from: []
---
# Execution Scheduler 的容量分区、公平准入与租约恢复

Execution Scheduler 是 ExecutionService（统一执行模型请求的后台服务，见 decision_25eb93d803a4）内部的持久调度器。调用方提交一次模型执行后，Scheduler 决定它什么时候可以开始、由哪个执行工作进程（Worker）来运行，并在 Worker 失联时决定这次执行是重新排队、取消还是失败。Portal 的开发部署（Dev）和发布部署（Release）各有一个 ExecutionService 进程，但它们共用同一个 Scheduler 数据库，所以两边看到的是同一份队列、同一份账号容量和同一份公平记录。

本文解释 Scheduler 的队列、容量、公平、租约、恢复和持久状态。请求字段和每个远程调用方法见 decision_6ad0384e020d；执行账号的额度保护和调度设置见 decision_d40f59f5508f；Worker 的代际替换见 decision_075f66430606。

## 先认识几个概念

- **执行**：由稳定 `execution_id` 标识的一次模型请求。
- **队列**：已经保存、尚未开始的执行。
- **Worker**：实际调用模型 Provider 的进程。
- **领取资格（claim）**：Scheduler 把一条执行暂时交给某个 Worker 的记录，包括 Worker ID、随机的领取令牌和到期时间。
- **租约（lease）**：领取资格的有效期限。Worker 定期续租；到期不续，资格失效。
- **恢复点（checkpoint）**：Worker 保存的持久标记，说明执行走到了哪一步、能否从这里恢复。
- **容量键（capacity key）**：把会争用同一资源的执行放进同一个**容量分区**。一个分区有自己的并发上限和派发间隔。
- **派发间隔（dispatch interval）**：同一分区两次成功派发之间至少等待的时间。
- **提交者（actor）**：提交执行的业务主体，也是公平轮转的分组依据。

Scheduler 把队列、执行快照、Worker 登记、租约、容量设置、账号额度状态、运行中控制、用量增量和小时账本都保存在 PostgreSQL 的 `execution_runtime` schema 中。它是这些运行状态的唯一可信来源；Worker 进程内的状态、事件通知和页面显示都只是读取它或从它派生。

## 一次执行在 Scheduler 中的路径

```text
业务调用方提交
      │
      ▼
 queued（持久保存）
      │  账号未暂停、分区有空位、间隔已到、轮到这个提交者
      ▼
 claimed + 租约
      │ Worker 调用 start
      ▼
 running
      │ 事件 / 恢复点 / 用量增量 / 运行中输入 / 心跳
      ├──────────────────────────────┐
      ▼                              ▼
completed / failed / cancelled   租约到期或 Worker 失联
      │                              │
      ▼                              ▼
确认后按保留期清理            重新排队、取消或失败
```

`queued`、`claimed`、`running` 是非终态；`completed`、`failed`、`cancelled` 是终态。取消请求可以先记在非终态执行上，终态在 Worker 提交结果或维护恢复时确定。执行详情、结果和最近事件通过同一个快照读取。

## 设计点

### 持久与共享

- **提交先写入数据库，再等待 Worker。** 服务或 Worker 重启时，已经入队的执行仍在数据库中，不会因为进程内存丢失而消失。
- **Dev 与 Release 共用一个 Scheduler 数据库。** 两个部署有各自的 Unix socket，但共享总容量、公平记录和账号额度状态。

### 容量与节奏

- **每个容量分区独立限流。** 分区的并发上限默认取账号声明的值（没有账号的分区默认 8），可以按账号设置为 1 到 256。
- **领取之间不互相等待，上限允许短暂超出。** 各 Worker 的领取各自读取分区占用、派发时间和公平记录后独立判断，不排队等待全局准入锁。同一时刻有几个 Worker 同时领取，分区最多就超出上限几条：例如上限 10、已运行 9 条时两个 Worker 同时领取，会运行 11 条。派发间隔同样可能在同一瞬间放行两条。超出只来自同一时刻的并发领取，分区占满后新的领取照常被挡住。派发时间或公平记录正被另一个领取写入时，本次领取跳过这次记录更新，不等待对方提交。
- **同一分区相邻两次派发之间随机等待一段时间。** 默认在 2 到 6 秒之间均匀抽取，可以按账号设置为 0 到 3600 秒的范围；会话释放分区固定为 0 秒。每次成功派发时重新抽取一次。
- **账号处于额度保护时，这个账号上的排队执行暂停领取。** 保护规则见 decision_d40f59f5508f。

### 顺序与公平

- **高优先级先于低优先级；同一优先级内，先在提交者之间轮转，再按每个提交者自己的提交顺序派发。** 一次成功领取才会推进这个分区的派发时间和这个提交者的公平记录；只是查看候选或被容量挡住，不消耗轮次。
- **同一会话的执行串行运行。** 带有同一个非空 `binding_id` 的持续会话执行，前一条处于领取或运行中时，后一条留在队列里，防止两轮对话同时改写同一个 Provider 会话。这条规则不允许超出：领取一条带 `binding_id` 的执行前，Scheduler 先对这个 `binding_id` 尝试取得事务级咨询锁（取不到立即返回，不等待），取到后在同一事务内重新核对这个会话有没有领取或运行中的执行；取不到说明另一个领取正在处理同一会话，本次跳过这条候选。

### Worker 与写入资格

- **只有处于激活状态的 Worker 可以领取新工作。** `standby` 等待激活；`draining` 停止领取，主动交接手里的工作，并在每项工作安全释放前保留资格；`offline` 不参与派发。
- **每次受保护的写入都核对 Worker ID 和领取令牌。** 开始、续租、上报事件、保存恢复点、记录用量和提交终态都要求调用方仍是当前持有者；旧代 Worker、重复 Worker 或超时的请求都被拒绝。持有者写入与租约回收、交接锁定同一执行行；领取只更新仍为 `queued`、未请求取消且没有被其他事务锁住的执行行（`FOR UPDATE SKIP LOCKED`），并核对实际更新行数，两个 Worker 同时领取同一条执行时只有一个得到它，另一个不等待，继续看下一条候选。资格检查和更新之间不能被另一个实例夺走所有权。这项规则称为 fencing（写入资格隔离）。
- **Worker 或租约失效后，按执行阶段和恢复点决定去向。** 不能证明可以安全重做的 Provider 外部动作，不会被当作可以重试。

### 维护

- **健康查询只读。** 持续的恢复工作由 ExecutionService 每 5 秒一次的维护完成：回收过期租约和失联 Worker，并修正 AgentData 中的执行状态。领取不顺带回收过期租约，过期执行最多等一个维护周期回到队列。Dev 与 Release 的维护可以同时运行，各自只处理自己锁住的行。

## 容量键怎样确定

Scheduler 按执行类型选择容量分区：

| 请求 | 容量键 | 原因 |
|---|---|---|
| 持续会话的 `release` 操作 | `session-control:<provider>` | 释放会话不占模型执行容量，派发间隔固定为 0 |
| 能解析到执行账号的请求（持续会话，或已登记为账号的 Chat Completion 接口） | 账号适配器给出的容量键，例如 `codex:one`、`account:deepseek_primary` | 按账号隔离容量、间隔和额度保护 |
| 其他请求 | `shared-ai` | 共用通用容量 |

持续会话请求在入队前由账号适配器解析路由；Codex 请求解析不到账号时直接报错，不会静默落入 `shared-ai`。容量设置持久保存在数据库中。每次领取、排队状态和健康查询在事务内读取当前设置，Dev 与 Release 的服务进程无需重启即可采用修改后的上限和派发间隔。Worker 完成、租约恢复或容量键调整后，Scheduler 按实际占用重新计算，避免只看队列标签而超额放行。

## 公平准入和队列顺序

每次领取时，Scheduler 先筛掉当前不能运行的候选：延迟时间未到、账号处于额度保护、分区已满、离上次派发太近，或者同一会话已有执行在运行。剩下的候选先按数值优先级从高到低分组；在同一优先级内，选出最久没有成功派发的提交者，再取这个提交者最早提交的执行。

公平记录按“优先级 + 提交者”保存。当整个 Scheduler 中已经没有任何排队、领取或运行中的执行时，新提交的请求会先清空全部公平记录，长期不用的历史因此不会影响新一轮竞争。公平只约束领取顺序，不保证各提交者得到相同的 Provider 响应时间。

排队中的执行在快照里用 `queue_reason` 说明为什么还在等：

| `queue_reason` | 含义 |
|---|---|
| `ready` | 本次检查时可以领取 |
| `quota_reserve` | 所在账号处于额度保护 |
| `deferred` | 延迟时间还没到 |
| `capacity` | 所在分区并发已满 |
| `dispatch_interval` | 离这个分区上次成功派发太近 |
| `session_binding` | 同一会话已有执行在领取或运行中 |

Worker 调用领取时得到的是这一次尝试的结果，原因取值不同：`worker_not_registered`、`worker_not_active`、`quota_reserve`、`capacity`、`dispatch_interval`、`deferred` 或 `empty`（没有候选）。前一组说明某条执行为什么还在等，后一组说明这个 Worker 这次为什么没有领到。`worker_not_active` 同时给出 `worker_state`：`standby` 表示还在等激活，`draining` 或 `offline` 表示这个 Worker 已经退休，它会交接手里的执行后退出。

## Worker 登记、领取与租约

Worker 登记时提供稳定的 Worker ID、构建版本、能力列表和初始状态（`standby`、`active`、`draining` 或 `offline`）。激活是单独的动作；状态修改接口只能把 Worker 改为 `draining` 或 `offline`。同一部署的代际切换由 WorkerManager 协调。

执行租约默认 90 秒，最短 15 秒。Worker 心跳超时默认 60 秒，最短 1 秒。Worker 心跳证明进程整体还活着；执行续租延长某一份领取资格，两者是不同的记录。

领取成功时，执行从 `queued` 变为 `claimed`，记录 Worker、领取令牌和租约截止时间，并占用分区容量。Worker 随后用同一个令牌调用 `start` 进入 `running`。

### 等待与唤醒

Worker 等待领取、调用方等待执行结果、Worker 等待控制消息时，都不需要不停地查询数据库。Scheduler 的相关数据表上有 PostgreSQL 触发器，事务提交后在频道 `tfi_execution_scheduler_changed` 上发出通知，通知按用途分为三类：

| 类别 | 何时发出 | 唤醒谁 |
| --- | --- | --- |
| `execution` | 执行记录发生任何变化 | 等待执行结果的调用方 |
| `claim` | 新任务、排队属性、容量、账号额度或 Worker 状态变化 | 等待领取的 Worker |
| `control` | 状态、取消标志、持有者变化，或有新的运行中输入 | 等待控制消息的 Worker |

流式文本、恢复点和心跳只更新执行记录，不会让空闲 Worker 反复查询队列。每个服务进程在有人等待时开一个监听线程接收这些通知；Dev 和 Release 两个进程都能收到对方提交的变化。等待者另外每 5 秒复查一次数据库，通知连接中断或消息丢失时也能发现已经提交的状态。通知只负责缩短等待，数据库记录负责恢复。

## 恢复点、交接与恢复

Worker 可以保存恢复点，说明哪些外部步骤已经完成、执行能否恢复。Provider 已完成的恢复点让 Runtime 在重试时跳过再次调用 Provider，直接补写 AgentData 并收尾。

### 计划内交接

新 Worker 激活后，Scheduler 把它的前任和其他构建版本的 `active` Worker 改为 `draining`（同一版本的 Worker 可以同时处于 `active`，见 decision_075f66430606），并唤醒领取和控制等待。旧 Worker 在有效资格下调用 `scheduler.handover.prepare`：Scheduler 先检查取消和恢复点，再保存原 Worker、会话和这一轮的交接标记，此时不释放资格。缺少恢复证据时返回未就绪，不会把还没进入会话的原始输入换成一条 `continue`。

Codex、Claude Code、Kimi Code 和 DSH 的旧 Worker 解除对原轮次的跟随，保持 Provider 继续运行；等已发出的控制调用返回后，逐项调用 `scheduler.requeue`。Scheduler 在释放事务中再次检查取消，清除旧资格，保留原请求和恢复点；新的激活 Worker 领取同一个 `execution_id`，容量、公平、同一会话串行和写入资格隔离照常适用。这条路径不经过终态，不触发失败续跑，也不等待整个旧进程退出。

交接标记随原一轮的恢复点保留：旧持有者迟到的写入和新持有者恢复原一轮，都不会提前清除它；Provider 接收新的一轮后才清除。Provider 已完成时直接补交结果。字段与 RPC 见 decision_6ad0384e020d，进程管理见 decision_075f66430606。Scheduler 只保存这些控制事实，不解释 Codex 的中断错误，也不构造续接提示词。

### 租约到期、失联与遗留资格

维护周期检查租约和 Worker：

| 失效时的执行情况 | 结果 |
|---|---|
| `running` 的 `turn` 或 `compact`，已请求取消或不能恢复 | 释放旧资格，标记为“待中断”回到 `queued`；新 Worker 中断 Provider 后，按已定去向设为 `cancelled` 或 `failed` |
| 其他已请求取消的执行 | 终态设为 `cancelled` |
| `claimed`，尚未开始 | 释放旧资格，回到 `queued` |
| `running` 且恢复点可恢复 | 释放旧资格，回到 `queued` |
| 其他 `running` 且不能恢复的执行（创建会话、Fork、释放） | 终态设为 `failed` |

**Portal 放弃一项执行之前，先停下它的 Provider 工作。** Codex App Server、Claude Code daemon、DSH 进程和 Kimi Code Server 都在 Worker 进程之外运行一轮对话，Worker 失联后这一轮还会继续。恢复点含有未完成的 Provider 轮次，且 Provider 声明交接后继续运行时，即使执行已经重新排队或刚被领取，取消也先安排中断。这类执行回到队列，恢复点里记下 `abandoned`（已定的去向 `failed` 或 `cancelled` 和错误文字），执行在此期间仍是非终态：

- 新 Worker 领取它时不再运行这一轮，只按旧 Worker 记下的会话和轮次 ID 中断 Provider；恢复点里还没有会话 ID 时，用这项执行绑定的会话。中断之后执行按已定去向结束，执行事件里有一条 `provider_interrupt` 记录是否发出了中断。
- 领取时它排在最前，不受账号额度保护、分区容量和派发间隔限制（它不调用模型），但同一会话仍有执行在领取或运行中时照样等待。
- 等待期间用户再点停止，去向改为 `cancelled`，仍先中断；快照的 `cancel_requested` 为真，Resident 显示为“停止中”。
- 负责中断的 Worker 也失联时，执行直接按已定去向结束，不会反复排队。

Codex 恢复点里还没有轮次 ID 时中断这个 thread 正在进行的一轮；DSH 用执行 ID 推出它自己命名的轮次（新会话的 ID 也由此推出）；Claude Code 中断会话键上正在进行的一轮；Kimi Code 中止整个会话。

Worker 主动重新排队时可以要求最多 3600 秒的延迟，并且必须有可恢复的恢复点。WorkerManager 在旧进程退出后通过 `scheduler.worker.handover` 处理遗留资格，使用同一份 Scheduler 记录，并保留可恢复任务的交接信息；不能安全恢复的任务作为“无法移交”返回，租约到期后按上表处理；已请求取消的 `turn` 或 `compact` 同样先标记为待中断再结束。这个兜底路径和正常的逐项交接一样遵守取消优先和写入资格隔离。

Worker 登记时也会在同一个事务中触发一次到期恢复和过期 Worker 清理；普通健康查询没有这些副作用。定时维护才是持续恢复的入口。

领取、完整快照与批量状态查询复用相同的准入判断。每轮批量读取容量、额度阈值与例外、活动绑定和发送间隔；逐条判断时复用这份读数。完整快照另外计算排名，批量状态只返回等待原因。

## 运行中控制、取消与完成

运行中输入先保存为持久的控制记录。Codex、Claude Code、Kimi Code 和 DSH 持续会话的 `turn` 操作支持追加输入，本轮设置更新只有 Codex 支持；提交者必须与原执行一致。同一个控制 ID 和相同内容重复提交返回已有记录，同一个 ID 用于不同内容时被拒绝。控制状态为 `pending`、`delivered` 或 `failed`。

Worker 等待控制时，Scheduler 只把控制交给当前持有者。处于 `draining` 的 Worker 无论有没有待投递的输入都会收到交接指示；待投递的输入暂不交给旧持有者，由新持有者恢复后按原来的精确轮次规则处理。Worker 报告投递结果后，Scheduler 把对应控制标为 `delivered` 或 `failed`。回执连接失败或结果未知时，Worker 保存 Provider 的投递结果，只重试幂等回执，不再次发送输入；回执写入期间继续续租，直到成功或失去领取资格。

取消先记录请求并通知 Worker。排队中的执行若没有仍在运行的 Provider 工作，可以直接取消；否则进入待中断路径。已领取或运行中的执行由当前持有者中断 Provider，并在提交终态或维护时收敛。提交终态时再次应用取消优先，并把仍为 `pending` 的输入标为失败，终态执行不会留下永远等待的控制。

提交终态时，Scheduler 保存终态、结果或错误和最终用量，释放资格和容量，并发出状态通知。只有当前持有者能提交非终态执行的终态；重复或过期的持有者不能覆盖已有终态。

## 事件、确认与保留

首次 `started` 事件与执行状态一起持久保存，跨实例等待和服务重启后仍可读取。其余运行事件由每个服务进程为一条执行最多保留最近 256 个，供页面和等待方观察进展；完整的执行内容由 AgentData 保存。

调用方确认终态后，请求正文与恢复点被清空，执行结果继续保留。维护循环清理已确认且超过 24 小时的终态记录，以及超过 7 天的未确认终态记录；查询、确认和提交终态不触发全表清理。

增量用量和最终用量也在 Scheduler 事务中去重和汇总。小时账本的归因、持续保留和查询范围见 decision_f1331d57be89。AgentData 保存单次执行的最终用量，Scheduler 保存运营查询需要的小时汇总，两者用 `execution_id` 对齐。

## 持久结构与版本

Scheduler 协议版本是 `tfi.execution-scheduler.v10`，数据库结构版本是 `19`（`scheduler.py` 的 `EXECUTION_STORE_SCHEMA_VERSION`）。启动时，Scheduler 为全新的空数据库创建该版本的全部表，或者严格核对已有数据库的表、列、索引和版本号；只接受当前版本，结构不符时拒绝启动。

这个持久边界让 Dev、Release、Worker 各代和服务进程可以各自重启。Scheduler 只负责准入、运行所有权、恢复控制和运营账本；它不保证 Provider 的外部动作天然只执行一次，也不保存完整的会话内容。

格式名与版本须精确匹配当前规范值，校验规则见 decision_86aed2f0e8c7；本篇同时校验各字段和结构版本，不接受历史前缀或其他版本。
