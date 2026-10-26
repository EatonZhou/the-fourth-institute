---
kb_id: decision_075f66430606
type: decision
project: AI员工
created_at: '2026-09-03'
version:
  number: 22
  created_at: '2026-09-03'
  created_by:
    user: ''
    agent: ''
  updated_at: '2026-10-26T16:00:00Z'
  updated_by:
    user: ''
    agent: resident_8b24fa3ef913
importance: 8
metaness: 2
ai_generated: false
links:
  source:
  - ${code_root}/management_portal/execution/worker_manager.py
  - ${code_root}/management_portal/execution/worker.py
  - ${code_root}/tests/cases/test_execution_worker_manager.py
  - ${code_root}/tests/cases/test_execution_layer.py
  - ${code_root}/management_portal/execution/runtime.py
  - ${code_root}/management_portal/execution/provider_catalog.py
  related:
  - decision_25eb93d803a4
  - decision_850e6248d680
  - decision_6ad0384e020d
  - decision_8ac0b5a6c0e5
  - decision_3e8c2a7f91d4
  - decision_425e428410e0
  supersedes: []
  derived_from: []
---
# ExecutionWorker Generation 与滚动交接

ExecutionWorker 是实际领取并运行模型执行的进程；WorkerManager 是管理 Worker 进程的父进程。部署新代码时，WorkerManager 先启动新一代 Worker 并确认它就绪，再把领取新工作的权利切换给它，然后让旧 Worker 把手里可以恢复的执行逐项交给新 Worker。Codex、Claude Code、Kimi Code 和 DSH 正在进行的一轮保持运行，新 Worker 用同一个 turn_id 接回。等待结果的业务调用方看到的一直是原来那次执行。

本文负责 Worker 的代际（generation）、进程生命周期、计划内交接和故障兜底。Scheduler 的领取与租约规则见 decision_8ac0b5a6c0e5，交接用到的 RPC 与恢复点字段见 decision_6ad0384e020d，Codex 原生轮次的核对与续接见 decision_3e8c2a7f91d4。

## 对象与状态

- **代际（generation）**：一代 Worker 对应的代码版本。WorkerManager 从部署写下的 `component-generations.json` 中读取 `execution_worker` 的构建版本；没有时读取 `source-generation.env` 的 `PORTAL_BUILD_ID`。
- **Worker ID**：由配置前缀、代际、WorkerManager 进程 ID 和启动序号组成，例如 `<前缀>:<代际>:<pid>:<序号>`。每启动一个新 Worker，序号加一。
- **Worker 状态**：
  - `standby`：已经登记，等待健康检查，还不能领取新工作。
  - `active`：可以领取新工作的 Worker。同一代的 Worker 可以同时处于 `active`：Dev、Release 两组 WorkerManager 运行同一份代码、读同一个代际文件、共用一个调度库，各有一个 `active` Worker；不同代不会同时处于 `active`。
  - `draining`：停止领取新工作，主动交接手里的执行；每项执行安全释放前继续持有它。
  - `offline`：已经退出调度。
- **领取资格与租约**：Scheduler 交给某个 Worker 的执行所有权及有效期限。每次重新领取都会生成新的领取令牌。
- **计划内交接**：同一个执行从旧 Worker 转给新 Worker 的过程，包括持久准备、解除跟随、释放资格、重新领取和恢复，全程不产生终态。

Scheduler 保存队列、Worker 状态、领取资格、租约、恢复点和取消意图。WorkerManager 负责启动、检查、激活和退休 Worker 进程，不决定队列顺序。Worker 在有效资格下调用执行编排器（Runtime），Runtime 与 Provider Adapter 负责 Provider 阶段的恢复和内容写入。

## 设计点

- **新 Worker 就绪后才会得到领取权。** WorkerManager 显式传入初始状态 `standby`，Worker 启动和注册不提供隐式激活默认值；WorkerManager 读到它的健康文件报告就绪、Worker ID 和构建版本都与启动时一致后，才请 Scheduler 激活它。30 秒内没有就绪（`PORTAL_EXECUTION_WORKER_READY_TIMEOUT`，最少 2 秒），新 Worker 被终止，旧一代继续服务。
- **激活在一个事务里完成领取权切换。** Scheduler 把新 Worker 设为 `active`，把它的前任和所有其他代的 `active` Worker 改为 `draining`，并唤醒领取和控制等待；旧 Worker 随即开始准备交接。另一组里同一代的 Worker 照常领取。所以一次发布先后重载两组时，Dev 组的新一代接走旧一代的全部执行，Release 组随后激活同一代，不会让这些执行再交接一次。
- **重载只在有新一代时更换 Worker。** 收到重载信号时，当前 Worker 已经在运行代际文件里的构建版本，WorkerManager 就保留它；只有版本变了才启动新一代。WorkerManager 自身代码变了时，它排空 Worker 后以 75 退出，由 systemd 用新代码重启。
- **显式重启更换当前 Worker，即使构建版本相同。** `RestartPortal(instance, components=["execution-worker"])` 和管理命令 `portal_services restart <instance> --component execution-worker` 明确请求新 Worker。Dev 请求由现有重启请求队列和源码协调器处理，Release 通过同一管理入口处理；显式意图只作用于所请求的实例，不强制另一组同代 Worker 再交接。新 Worker 仍先以 `standby` 启动，核验就绪后经 Scheduler 激活，再让前任优雅交接；启动或激活失败时保留前任，不杀 Provider、不重发业务输入。普通发布、自动源码部署和程序调用 `restart(..., force_worker_restart=False)` 保留代际幂等；源码协调器的自动部署使用 `--generation-only`，不能把每次发布变成强制重启。
- **被退休的 Worker 自己离开。** 领取时 Scheduler 说明这个 Worker 已经是 `draining` 或 `offline`（`worker_not_active` 附带 `worker_state`），或者它已不在登记里（`worker_not_registered`），Worker 就停止领取，交接手里的执行后退出，健康文件记 `retired`。`standby` 的 Worker 收到 `worker_not_active` 时每秒再试一次。WorkerManager 看到当前 Worker 以 `retired` 退出，立即按代际文件启动本组的最新一代；代际文件里仍是被退休的版本时（两组对当前代看法不一致），按启动失败的退避等待，避免两组来回抢回执行。
- **正在运行的执行先保存交接意图，再解除跟随。** 对恢复点可以恢复的执行，旧 Worker 先在 Scheduler 中保存交接标记，再停止跟随原轮次；Provider 继续运行。
- **每项执行单独释放。** 一项执行的跟随和相关控制调用都返回后，旧 Worker 立即释放它的资格，新 Worker 马上可以领取，不等其他执行，也不等整个旧进程退出。
- **交接不改变业务看到的任何东西。** 交接沿用原来的 `execution_id`、请求、会话、路由、会话地址和业务消息，执行始终处于非终态。Chat 不会因为这次计划交接出现失败消息，Resident 也不会因此另外生成一条 `continue` 输入。
- **用户停止优先于交接。** 真实故障、失去领取资格和缺少安全恢复证据，各自保留原来的错误或阻塞处理。
- **激活中的 Worker 意外退出时，WorkerManager 重新启动一个。** 启动失败按指数退避重试：首次 5 秒，每次翻倍，最长 300 秒，带 20% 抖动（`PORTAL_EXECUTION_WORKER_RETRY_*`）；同样的错误 300 秒内只记一次。Worker 的 systemd 单元为 `Restart=on-failure`，5 分钟内最多重启 5 次。
- **交接保证领取权归属清楚。** Provider 的外部动作能否恰好执行一次，取决于原生协议和恢复点；交接不保证零延迟，也不保证所有 Provider 都能无损续接。

## 一次计划内滚动交接

```text
部署发送 SIGHUP
      │
      ▼
新 Worker 以 standby 登记 → 健康文件就绪 → Scheduler 原子激活
                                      │
                          旧 Worker 进入 draining
                                      │
                        为每项执行持久准备交接
                                      │
                    解除跟随，并等待旧控制调用结束
                                      │
                  逐项释放领取资格，原执行回到 queued
                                      │
                      新 Worker 领取同一个 execution
                                      │
                               核对原来那一轮
                         ┌────────────┴────────────┐
                         ▼                         ▼
                 已经完成：补交结果         仍在运行：接回原轮次
```

WorkerManager 是一个常驻循环，每 0.5 秒检查一次：有没有收到重新加载请求、激活中的 Worker 是否还活着、正在退休的 Worker 是否已经退出，并更新自己的健康文件。部署通过向它发送 SIGHUP 请求换代。收到请求时，如果 WorkerManager 自己的源码（`worker_manager.py`）已经改变，它让所有 Worker 进入退休流程后退出，由 systemd 用新代码重启；否则在当前进程内启动新一代 Worker 完成替换。换代失败时记录日志，当前一代继续服务。

显式重启通过管理接口向 WorkerManager 主进程发送 `SIGUSR1`，普通 reload 仍发送 `SIGHUP`。管理入口先核对健康文件的 `manager_pid` 与 systemd 当前主进程一致，并确认 `explicit_restart_supported=true` 和原 Worker ID；旧 manager 尚未加载该处理器时拒绝请求，须先通过正常部署加载，不能直接给旧进程发送未支持的信号。管理入口在既有健康及构建版本检查之外，等待当前健康 Worker ID 与请求前的 ID 不同，才确认完成，并返回 `worker_restart` 中的 `previous_worker_id`、新 `worker_id`、`build_id` 和 `manager_pid`。受理请求、同代健康或浏览器刷新均不代表 Worker 已更换；超时保留验证失败，不能返回假成功。该回执证明新 Worker 激活，不保证旧 Worker 全部执行已交接完毕；验证某任务恢复还须沿原 `execution_id` 核验实际 owner 与接管事实。

## 准备、释放与接管

旧 Worker 通过 `scheduler.handover.prepare` 在有效资格下保存交接恢复点。只有恢复点可以使用时结果才是“就绪”；还没有输入已接收或可恢复的证据时，不会把原任务改写成猜测性的 `continue`。用户已经取消时，按取消流程收尾。

交接恢复点记下原 Worker、Provider 会话和精确的那一轮。Codex、Claude Code、Kimi Code 和 DSH 的计划交接均保留 Provider 中正在运行的一轮，准备结果为“无需中断”；旧 Worker 解除跟随，新 Worker 接回同一 turn。不能用 `turn/interrupt` 代替解除跟随：模型的工具等待被中断，不证明原生子进程或副作用已经停止。真正的用户停止仍走精确中断；已经发出的控制请求须返回后才释放资格，避免迟到的请求影响新的一轮。Runtime 保留执行内容和恢复位置，计划移交不产生失败或取消终态。

每项执行退出后，旧 Worker 调用 `scheduler.requeue`，保留原请求和恢复点，释放原领取令牌。Scheduler 在释放事务中再次检查取消；已经有停止请求且 Provider 仍可能运行时，先进入待中断队列，由下一位 Worker 中断后收敛为 `cancelled`；没有 Provider 工作时直接取消。新 Worker 用新的令牌恢复同一个执行，旧 Worker 之后的写入都会被拒绝。

Codex Adapter 先在同一个路由和 thread 上核对原来那一轮：仍在运行就通过 `resume_turn` 接回，已经完成就经既有实时投影入口补交真实结果，均不发送新输入。无法核对原轮次或工具交付时，Adapter 不发送继续输入，延期计入持久的无进展恢复预算。原生命令后来完成也不能证明结果已交给模型；连续三次无新工作后报告恢复耗尽，保留原执行和工具证据。Provider 接收新的一轮后，原交接标记被清除。跨代准备状态和 Goal 的处理见 decision_3e8c2a7f91d4。

Codex、Claude Code、Kimi Code 和 DSH 的交接不中断这一轮（Provider 目录 `handover_keeps_turn`）。这一轮由 Provider 自己的进程运行（Claude 守护进程、Kimi Code 服务、DSH bridge），同一会话和 turn_id 的一轮可以换一个 Worker 继续：

- **Codex 的跟随边界。** Codex 客户端以 `detach_requested` 区分计划移交与用户取消。已开始的 Worker 动态工具调用先回传真实结果；进入解除跟随状态后不再接新工具请求，也不向模型返回虚构的取消结果，由 App Server 将待处理请求交给新订阅者。原生命令继续属于 App Server 的原 turn。新 Worker 按真实 command/item ID 重放同 turn 的已完成结果到同一个 Agent trace，不按正文或 native_id 去重，不另设历史写入入口。成功通过资格校验重新排队后，Worker 返回 `requeued`，不会把本地解除跟随标志误报为资格被抢走。

- **旧 Worker 离开。** 准备结果为“无需中断”，交接恢复点的交接标记带 `turn_kept_running`。Claude 和 DSH 的旧 Worker 只在两条消息之间断开连接；Kimi 的旧 Worker 停止轮询，不中止这一轮。旧 Worker 已经开始执行的工具调用先执行完并回传结果；交接开始后才到达旧 Worker 的工具调用不执行，原样交给下一个 Worker 执行（`ToolCallHandedOver`），不会以“已取消”失败返回模型。Adapter 报告可恢复的延期，Worker 照常 `requeue`。
- **新 Worker 接回。** 新 Worker 用同一个 turn_id 接回这一轮（这一轮是 Provider 错误后的续接时，用续接那一轮的 ID）。Claude 守护进程和 DSH bridge 把这一轮接到新连接上，DSH 只按 turn_id 认这一轮，不再比对请求内容；Kimi 的新 Worker 继续跟随同一个 prompt，并照常处理审批、提问和工具状态。接回时不发送任何输入，只记 `worker_handover_continuing`（`input_delivery=kept_running`）。
- **空档里不丢东西。** 没有 Worker 连接期间，Claude 守护进程保留这一轮的事件（最多 5000 条），DSH bridge 从会话日志补发新 Worker 没收到的事件；Portal 工具调用等待新 Worker，最多 3 分钟（Kimi 的工具调用等待新 Worker 的工具网关出现）。
- **空档里的停止。** 释放领取或排队期间收到停止时，执行不直接结束，按丢失 Worker 的方式标记 `abandoned`：下一个 Worker 先中断这一轮，再以取消结束，所以 Portal 显示空闲时 Provider 也已停下。新 Worker 接回前发现执行已停止，就不再接回。

**同轮跟随和故障恢复各自遵循 Provider 的协议。** 正常交接只观察原轮次，真实故障由 Provider Adapter 处理。Claude Code、DSH 和 Kimi Code 可以按下列原生交付证据决定是否续接；不能把其他 Provider 的恢复方式套到 Codex。

- **Codex** 按已接受的精确 turn ID 接回或读取结果。计划交接不根据 rollout 重发原输入或注入 `continue`；真实中断按统一的 Provider 错误规则处理。Goal 只观察原状态，保留用户暂停，不重新激活。
- **Claude Code** 给每一轮的原消息一个由会话键和 turn_id 推出的 UUID，CLI 把它写进会话记录；守护进程没有这一轮时在会话记录里查这个 UUID，有就发 `continue`，没有才第一次发原消息（守护进程能力 `turn_continue`，0.2.162-9 起）。
- **DSH** bridge 在会话日志里找这一轮的消息：已经完成就返回原结果；中断过或进程停在这一轮中间，就发 `continue`。
- **Kimi Code** 跟随的 prompt 已结束却没有回答时发 `continue`。

为恢复发出的每一条输入，在交给 Provider 时记一个运行事件 `recovery_input_sent`（`provider`、`reason`、`prompt`、`input_delivery`、`turn_id`，首次送达带图时有 `image_count`）。`reason` 为 `worker_handover`（Provider 明确支持的交接恢复）、`turn_interrupted`（Provider 进程已不在运行这一轮）、`provider_error` 或 `provider_transition`；`input_delivery` 为 `continued` 或 `first_delivery`。Claude 和 DSH 由守护进程或 bridge 决定发什么，并用 Provider 事件 `turn/recovered` 报给 Adapter。这条输入像其他消息一样进入记录、显示在 Chat；被中断那次已经显示的文字照常保留，不再撤回。

恢复点还保存这次执行已经选定的 Agent Harness 计划（启用哪些功能）。新 Worker 按同一计划准备请求；改写提示的功能在恢复时不再介入，因为 Provider 仍持有这一轮，Adapter 只接回或续接，不会再发一遍提示。

处于 `draining` 的 Worker 等待控制消息时，即使没有待投递的输入也会收到交接指示。待投递的输入不交给旧 Worker；新 Worker 恢复后按原来的控制协议处理，精确轮次校验和投递结果照常生效。

## 正常运行与写入资格隔离

Worker 激活后通过等待式领取取得任务，调用 `scheduler.start` 进入运行，并启动续租心跳。心跳间隔取租约时长的三分之一与 5 秒中较大的那个；准备退出时缩短为 0.25 秒，以便尽快完成交接。Runtime 运行期间，Worker 转交执行事件、恢复点、用量增量和运行中控制；正常返回后通过 `scheduler.finish` 提交结果、错误和最终用量。计划交接走逐项 `requeue`，不提交只因换代产生的终态。

一个 Worker 进程可以同时运行多项执行。并发数由 `PORTAL_EXECUTION_WORKER_PARALLELISM` 设置；为 0 或未设置时，Worker 启动后读取 Scheduler 报告的总容量作为并发数。

用户请求停止后，Worker 停止拉取新的轮内输入，但继续续租，直到 Provider 停止、结果回执写入完成或领取资格失效。取消意图不等于中断已送达：Provider 尚未注册活动调用、控制响应丢失以及已接受中断但尚未退出，都继续按心跳周期向该执行当前绑定的精确会话和轮次重试中断。重试不创建新轮次，也不提前提交取消终态；领取资格失效后停止以旧资格驱动该执行。

ExecutionService 更换连接期间，尚未建立连接与请求已送达但响应丢失都属于暂时的控制连接故障。领取循环保留当前 Worker 及其正在执行的任务，继续等待服务恢复；未观察到的领取由 Scheduler 租约回收。心跳在本地租约有效期内重新连接，不因一次响应丢失取消 Provider；只有明确失去领取资格或超过本地租约才执行隔离。

Worker 在本地记录租约截止时间。与 Scheduler 的连接中断超过本地租约，或 Scheduler 表明领取资格已经属于别人时，Worker 立即停止写入，并按需要中断 Provider（写入资格隔离）。真正失去资格时按资格丢失处理，不能仅凭“正在更新代码”就推定它属于一次已经准备好的交接。

## 进程退出与故障兜底

WorkerManager 让旧 Worker 进入退休流程时，先在 Scheduler 中把它标为 `draining`，由既有领取和控制等待唤醒 Worker，执行持久交接；不向 Provider 后代发送停止信号来代替交接。退出等待默认 180 秒（`PORTAL_EXECUTION_WORKER_DRAIN_TIMEOUT_SECONDS`，最少 1 秒），超时后再给 30 秒宽限（`PORTAL_EXECUTION_WORKER_KILL_GRACE_SECONDS`，最少 1 秒），仍未退出就强制结束所拥有的 Worker 进程树。这个期限只约束遗留任务和卡住的进程，正常的逐项交接不需要等到它。

生命周期控制 RPC（draining、handover、unregister）不等待服务重启：连接不可用立即返回，单次响应等待最多 2 秒；Manager 整体退出时还受同一个剩余退出预算约束，不能在截止之后再发起请求。整站停服在同一 systemd 事务内提交所有停止任务，按依赖关系先退出 Worker，再停止 ExecutionService，避免退出清理依赖已经关闭的服务。

Linux 整站停止由统一控制器管理一份最多 20 秒的墙钟预算，服务发现、依赖队列、进程退出确认和控制命令都共享其剩余时间，不能给每个服务分别重新计时。实例管理界面的响应投递延迟也计入这份预算。前 15 秒允许正常退出；此后向所有尚存活的服务进程组发送 SIGKILL，包括停止任务仍在排队的服务，并在剩余时间内确认退出。无法确认全部退出必须报告失败，不把停止命令超时当作进程已经结束。强制退出可能中断当前工作，其持久状态由既有租约与恢复协议处理，不声称无损交接。

整站停止包括 Dev/Release Web、公共路由、共享后台和本实例前缀下的 Provider 服务。存储维护使用同一控制器的应用停止模式，保留 PostgreSQL 与 NATS；复制前仍须独立确认没有文件使用者。普通代码发布与 Worker 代际交接不经过整站强制停止流程，继续保留原有交接等待预算。单独直接执行 systemctl stop 的每单元超时并不构成整站时间保证，运维脚本必须调用统一实例停止入口。

Manager 已确认所拥有的全部进程树退出后，不因 Scheduler 暂不可达而一直等待注销。尚未确认的交接、注销及健康记录保留，Manager 健康文件的 `shutdown_pending_recovery` 列出这些 Worker；调度资格仍由原 Scheduler、租约和恢复协议处理，不伪造交接成功或提前释放所有权。仍有未清空进程树时继续报告退出失败。

旧进程退出后，WorkerManager 调用 `scheduler.worker.handover` 处理没有释放的资格：已领取但未开始的执行回到队列；运行中的执行需要可以恢复的恢复点，并保留交接信息。不能安全移交的执行作为“无法移交”返回，由相应的失败或租约恢复路径处理，不会被描述成无损成功；这时 WorkerManager 保留这个旧 Worker 的记录并在后续循环重试交接，直到其遗留资格已经收敛。全部移交后，WorkerManager 注销旧 Worker，删除它的健康文件。每次成功激活新 Worker 后，它还会注销本前缀下其他已经 `offline` 的登记。

| 情况 | 直接处理者 | 结果 |
|---|---|---|
| 新 Worker 启动失败或健康信息不符 | WorkerManager | 不激活新一代，旧一代继续服务 |
| 正常计划交接 | Worker、Scheduler、Runtime 与 Adapter | 持久准备、解除跟随、逐项重新领取同一 turn，执行保持非终态 |
| 交接过程中用户停止 | Scheduler 与当前 Worker | 取消优先，不安排计划续接 |
| 激活中的 Worker 意外退出 | WorkerManager | 按退避重新启动 Worker |
| Worker 被其他代退休 | Worker 与 WorkerManager | Worker 交接后退出，WorkerManager 启动本组的最新一代 |
| Worker 心跳中断或租约过期 | Scheduler | 按恢复点和执行阶段重新排队、取消或失败 |
| 旧 Worker 退出超时 | WorkerManager | 先终止，再强制结束，然后处理遗留资格 |
| 旧 Worker 失去领取资格 | Worker | 自行停止用旧令牌写入 |
| Provider 已产生外部动作但结果没写回 | Runtime、Adapter 与 Scheduler | 依据恢复点核对，不假定可以安全重复 |

## 健康文件

Worker 健康文件使用 `tfi.execution-worker-health.v1`，记录 Worker ID、构建版本、进程、能力、并发数、当前执行、是否正在退休、是否已被 Scheduler 退休（`retired`）以及退休开始时间和期限。WorkerManager 只接受与启动预期一致的健康文件。

WorkerManager 健康文件使用 `tfi.execution-worker-manager-health.v2`，记录 WorkerManager 自身的状态（`running`、`degraded` 或 `stopping`）、激活中和正在退休的 Worker 进程状态，以及启动失败的恢复情况（连续失败次数、最近错误、下次重试时间）；`explicit_restart_supported` 标明当前进程已安装显式重启处理器。子 Worker 成功注销后才删除它的健康文件；注销失败时保留现场以便排查。

健康文件只说明进程状态，不保存执行结果，也不是队列的依据。Scheduler 用 Worker ID、领取令牌和租约判断写入资格；只有当前合法持有者可以开始、续租、上报事件、保存恢复点、记录用量和提交终态。

格式名与版本须精确匹配当前规范值，校验规则见 decision_86aed2f0e8c7；本篇同时校验各字段和结构版本，不接受历史前缀或其他版本。
