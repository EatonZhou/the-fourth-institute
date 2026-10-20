---
kb_id: decision_1b2bdf5c33d6
type: decision
project: AI员工
created_at: '2026-09-03'
version:
  number: 34
  created_at: '2026-09-03'
  created_by:
    user: ''
    agent: ''
  updated_at: '2026-10-19T19:42:46Z'
  updated_by:
    user: tfi-exported-user
    agent: resident_865c3c24f251
importance: 10
metaness: 3
ai_generated: false
links:
  source:
  - ${code_root}/management_portal/society_service/resident_resource_projection.py
  - ${code_root}/management_portal/society_service/function_block_domain.py
  - ${code_root}/management_portal/society_service/templates/__init__.py
  - ${code_root}/management_portal/hatg/socket_rpc.py
  - ${code_root}/management_portal/hatg/routing.py
  - ${code_root}/management_portal/hatg/kernel.py
  - ${code_root}/management_portal/society_service/isa/__init__.py
  - ${code_root}/management_portal/society_service/contracts.py
  - ${code_root}/management_portal/society_service/server.py
  - ${code_root}/management_portal/society_service/transport.py
  - ${code_root}/management_portal/society_service/runtime
  - ${code_root}/management_portal/society_service/application.py
  - ${code_root}/management_portal/society_service/changes.py
  - ${code_root}/management_portal/society_service/config.py
  - ${code_root}/management_portal/society_service/coordinator.py
  - ${code_root}/management_portal/society_service/invariant_locks.py
  - ${code_root}/management_portal/society_service/state_store.py
  - ${code_root}/management_portal/static/hatg_panel.js
  - ${code_root}/management_portal/static/lineage_graph.js
  - ${code_root}/management_portal/static/lineage_workbench.js
  - ${code_root}/tests/cases/test_lineage_lock_boundaries.py
  - ${code_root}/tests/cases/test_society_service_coordinator.py
  related:
  - decision_abaedeefca6f
  - decision_86aed2f0e8c7
  - decision_96a887d83e8a
  - decision_5b5653c3eaa5
  - decision_d918ebebc062
  - decision_0f57656a2acd
  - decision_6fc653d55c79
  - decision_4adcc1ff2f2c
  - decision_898b8911a2ff
  - decision_25eb93d803a4
  - decision_71aa039e3211
  supersedes: []
  derived_from: []
---
# SocietyService 模块与运行架构

Portal 是 TFI Portal 中让人管理 Agent 身份、协作关系和运行过程的网页应用。Resident 是可以跨多次对话和执行持续存在的 Agent 身份；Lineage 是一组 Resident 以及它们之间的协作定义：哪些事件会触发谁工作、结果交给谁、由哪些组件筛选和汇总。

SocietyService 是 Portal 的四个服务进程之一，负责 Resident 与 Lineage 这两类对象。另外三个进程是：浏览器应用层 Portal Web、管理资源与权限的 ResourceService、管理模型执行的 ExecutionService。SocietyService 保存 Resident 的身份、消息和工作状态，保存 Lineage 的成员、组件与连线，并在 Lineage 运行时推动事件、调用 Resident、执行动作。用户在 Society 页面编辑团队或启动工作，Resident 在工作中调用指令，Portal 的其他服务查询协作事实，最终都由 SocietyService 读取或修改这些数据。

本篇说明 SocietyService 负责什么、由哪些模块组成、一次指令和一次运行怎样流过这些模块，以及进程重启后怎样恢复。逐个内部方法的参数、返回值与并发条件见《SocietyService Domain RPC 与并发控制协议》（decision_d918ebebc062）；指令本身的格式与执行规则见 decision_898b8911a2ff。

## 先建立共同语言

| 概念 | 含义 | 在本篇中的用途 |
| --- | --- | --- |
| Lineage 定义 | Lineage 中的 Resident 节点、组件和连线 | 回答“谁在什么事件之后做什么、结果流向哪里” |
| 组件 | Lineage 中除 Resident 外的对象：事件源、Effect、Filter、Selector、Reducer、循环和 Seed | 画布上显示为组件卡片；组件卡片不是 Resident 成员 |
| Event | 已经发生并保存下来的事实，例如“用户手动触发”“某个 Resident 完成了一轮工作” | 沿连线激活后续对象 |
| Proposal | 等待筛选、选择或归并的结构化候选 | 让多个 Resident 的结果先进入可比较的中间阶段 |
| Effect | 执行动作的组件，用脚本调用一条或多条 HATG ISA 指令（语法与恢复规则见 decision_4adcc1ff2f2c） | 让 Lineage 在运行中发消息、Fork、写共享状态或修改自身 |
| HATG ISA 指令 | Portal 中所有对象操作共用的具名指令，例如 `ForkResident`、`CreateWire` | SocietyService 执行其中 Resident 与 Lineage 相关的指令（decision_898b8911a2ff） |
| 指令内核 | 各服务共用的指令执行代码（`management_portal/hatg/kernel.py`）：校验参数、请权限系统检查、处理幂等与执行记录 | SocietyService 在自己的进程中运行一份内核 |
| Program | 从 Lineage 定义规范化、校验并编译出的可执行结构 | 运行器只按当前 Program 推进 |
| Domain RPC | Portal 内部进程之间、按“领域.方法”调用 SocietyService 的受信任接口 | 供 Portal Web 页面后端和相邻服务读取页面数据，以及少数没有对应指令的内部协作 |
| 共享状态（Graph State） | 一个 Lineage 在运行中保存的有界键值数据 | 保存计数、进度和标记，见 decision_71ba0494b93e |
| revision | Lineage 定义的变化计数，每次成功提交加一 | 页面判断数据新旧，WatchLineage 以它为游标；定义提交的条件比较见 decision_d918ebebc062 |
| layout_revision | 节点坐标的独立版本号 | 只改坐标不影响 revision，也不重新编译 Program |
| graph generation（图代次） | 每次 Lineage 定义修改成功后产生的新代次 | 旧代次迟到的运行写入被拒绝，避免旧任务影响新定义 |
| 对账（reconcile） | 对照已经保存的 Event、待办和终态，找出可以安全推进的下一步并执行 | 服务启动、收到唤醒或定时重试时推动 Lineage 运行 |
| claim 与 lease | 运行器在处理一项待办之前先登记“由我处理”（claim），并给这份登记设置有效期（lease，默认 120 秒） | 进程崩溃后，过期的登记可以被重新领取 |

一次最短的运行链是：事件源观察到事实并写入 Event；Event 沿连线激活 Resident、Effect、算子或循环；Resident 可以返回 Proposal，由 Filter、Selector、Reducer 筛选、选择和归并；Effect 按脚本发出一条或多条指令，每条由拥有目标对象的服务执行；观察这个结果的事件源再产生下一项 Event。Inspect、Target 和 Watch 端口只传递“可读取的对象”“动作的目标”“观察的对象”这三类引用，自身不触发执行。组件与 Event 目录见 decision_4adcc1ff2f2c。

## 谁通过什么方式使用 SocietyService

| 调用者 | 使用方式 | 身份与权限 |
| --- | --- | --- |
| 在页面上操作的人 | Portal Web 把页面操作转换为 HATG ISA 指令，通过 SocietyService 的 `instruction.execute` 接口提交，画布坐标与碎片也是指令；页面需要的列表与详情通过 Domain RPC 读取 | 身份来自登录 Session，权限由权限系统检查 |
| 正在工作的 Resident | ExecutionWorker 中的 Harness 工具把模型的工具调用转换为指令，经 `instruction.execute` 交给 SocietyService；命令行客户端经 Portal Web 的指令地址转交 | 身份由执行层注入；Resident 不调用 Domain RPC |
| Lineage 内部组件 | Effect 与 Function Block 运行在 SocietyService 进程内发出指令；目标对象属于其他服务时，经对方的 `instruction.execute` 转交 | 调用者是本次运行的发起者 |
| 以客户端模式运行的进程 | 保存 Resident 的新工作后通过 `runtime.resident.wake` 唤醒协调器；Resident 工作的结束由 SocietyService 进程内的完成等待者交给协调器归并 | 服务身份由内部 RPC 确认（decision_96a887d83e8a） |
| Portal Web 中的业务功能 | 通过 Domain RPC 读取状态投影；共享搜索等业务调用通过 `service_invocation` 领域登记和观察交给 Resident 的工作 | 同上 |

浏览器不直接连接 SocietyService。Portal Web 以客户端模式运行（`PORTAL_SOCIETY_SERVICE_MODE=client`），通过 SocietyService 的 Unix socket（本机进程间套接字）调用它；只有 SocietyService 进程自己以本地模式直接读写 LineageData。

## 设计点

### 数据归属

- **SocietyService 是 Resident 与 Lineage 数据的唯一写入者。** 页面本地状态只保存正在进行的交互和待提交的编辑，提交后以服务返回的结果为准。
- **相邻领域的数据只保存引用。** 资源正文与访问控制由 ResourceService 拥有，Provider 会话、执行记录和消息分段由 ExecutionService 拥有，Project 登记由 ResourceService 的 Project 领域拥有；SocietyService 只保存它们的 ID 和必要的快照。
- **页面是否提供某个控件，不影响指令能否被调用。** 一条指令能否执行，只由指令声明和权限系统决定。

### 修改怎样提交

- **并发保护由修改的数据负责者承担。** Resident 状态以 Resident 为临界区，定义写入在短事务中锁定对应 Lineage 行；不会把整个多步指令串行化。跨对象取锁顺序、版本比较与并发合并统一见 decision_d918ebebc062。
- **慢操作在锁外执行。** 调用 ExecutionService、Provider 或 ResourceService 之前，先在锁内保存“准备做什么”（执行意图），释放锁后再做外部调用，最后按 Message ID、execution ID 和代次把结果写回。停止一项工作（Stop）是独立的短命令，不需要等待正在执行的调用。
- **定义提交支持条件比较，也支持省略条件的并发合并。** `expected_version` 的适用范围、首次提交检查、冲突与回滚规则以 decision_d918ebebc062 为准；指令通用信封见 decision_898b8911a2ff。
- **节点坐标与 Lineage 定义分开保存。** 坐标属于所有协作者共享的布局，使用独立的 `layout_revision`；只改坐标不重新编译 Program。
- **视口位置、缩放和纯展示的折叠状态属于个人显示偏好。** 它们按“用户 + Lineage”保存在 Portal 的用户界面状态中，不推进 `layout_revision`，也不影响其他协作者。打开 Lineage 时恢复这份个人视口；通过链接指定了 Resident，或用户主动选中节点时，定位到该节点优先。会改变 Function Block 实例或 Program 含义的展开和折叠属于 Lineage 定义修改。
- **三种计数各自推进。** Lineage 的 revision、共享状态的变化计数、运行记录所属的图代次互相独立，不能拿一个代替另一个。
- **变化通知在提交之后、所有锁释放之后发出一次。** 页面和其他消费者看到的都是已经提交的数据（decision_abaedeefca6f）。

### 领域已提交、投影待确认

SocietyService 修改 Lineage 或 Resident 后，还要把结果同步到 ResourceService 的资源索引，让搜索和资源页面看到新名称、新成员或删除状态。这一步称为投影，共有三种：Lineage 的资源身份、Resident 注册后的资源条目、Resident 删除后的资源删除标记。

- **投影失败不撤销已经提交的修改。** 指令返回 `status=failed`、错误码 `instruction.projection_pending`、`commit_state=committed`，`details.projection_failures` 列出每个未完成的投影，`details.domain_committed=true`。调用者不能据此判断修改没有发生。
- **带幂等键的调用可以用同一调用重试来补齐投影。** 这时错误中 `retryable=true`；重试时服务找到原执行记录，重新检查调用者权限，只重新发布未完成的投影，不重复执行原修改，也不重发变化通知。补齐成功后返回原执行记录中的结果。没有幂等键的调用不能由调用者同键重试；拥有服务的后台维护仍可按原执行记录补齐投影，不重执行业务（decision_898b8911a2ff）。
- **同一 Lineage 的投影发布逐个进行，每次发布当前状态。** 发布在 Lineage 运行目录下的 `runtime/resource-projection.lock` 文件锁内进行：读取此刻已经提交的 Lineage，写入 ResourceService，再在 `lineage_resource_projection`记下已经发布到的 revision。一次提交的发布发现记录已经覆盖自己的 revision 时不再发布，因为更晚的发布已经带上了它的修改；同一 Lineage 的并发修改因此不会互相使对方的投影变为待确认。补齐投影的重试同样发布当前状态，并重新检查调用者的管理权。新建的 Lineage 尚未进入 ResourceService，它的创建发布创建时的快照。
- **Resident 注册后的资源条目经待发送记录发布，每次认领一份。** 新 Resident 的资源条目（Resident 与它的 Workspace）先写入待发送记录，`CreateResident` 确认时直接认领并发布自己的条目，最多等待 5 秒；后台发布线程每次也只认领一个 Resident 的条目。一个确认因此最多等待另一份正在发布的条目，多个新 Resident 的条目可以并行发布。发布或确认超过 0.5 秒时，SocietyService 向 stderr 写一行以 `SocietyService slow Resident Resource` 开头的 JSON：发布记录发起方、条目数、写入 ResourceService 与标记待发送记录完成各自的耗时；确认记录它是否自己发布了条目，以及因其他发布者持有租约而等待的次数。
- **所有者以 ResourceService 为准。** 没有 Lineage 写入修改所有者时，发布保留 ResourceService 中现有的所有者，ResourceService 直接做的所有者修改不会被覆盖；修改了所有者的 Lineage 写入在提交事务中登记（键 `owners`），之后的发布写入 Lineage 的所有者。访问控制写入携带发布时读到的访问控制指纹，发布期间 ResourceService 中的访问控制发生变化时这次发布失败，投影记为待确认。领域事务在调用 ResourceService 之前提交，所以领域修改与资源投影不在同一时刻提交；慢投影期间可能短暂看到旧快照，后续发布会使两边一致。

### 副作用与恢复

- **外部调用之前先保存待办。** 运行器先保存 Event、Effect 调用或待投递记录，再调用外部系统，完成后保存结果。重启后据此区分哪些工作还没开始、正在由谁处理、已经结束。
- **Resident 的工作结果按“Resident、消息、终态、Lineage”归并。** 重复送来的结束通知不会让 Lineage 重复前进；失败结果保留，供事件源、诊断和人工检查使用。
- **每次读取都重新检查权限。** 组装一次 Lineage 视图时复用同一份数据和同一次权限计算；视图缓存不保存授权结论，权限变化立即生效。
- **每个 Resident 同时只有一个“完成等待者”。** 完成等待者负责等待这个 Resident 当前执行的结束，并写入结果和对话。多个 Portal 进程共享数据时，已有进程持有等待者时，其他进程的恢复检查不再重复派发、改写输入与对话或发出恢复通知；等待者释放后，正常恢复流程可以接管。

## 状态与记录分别由谁拥有

| 数据 | 内容 | 版本或保护方式 | 恢复方式 |
| --- | --- | --- | --- |
| Lineage 聚合 | 元数据、成员、Resident 节点、组件与连线、工作区交接、Lineage 内的 Function Block 定义与实例 | revision；提交时锁定 Lineage 行并合并并发写入 | 从 LineageData 重新加载，并重新编译 Program |
| 共享布局 | 节点坐标 | `layout_revision` | 从布局记录重新加载；缺失时重新排布 |
| 个人显示偏好 | 视口、缩放和纯展示折叠 | 按“用户 + Lineage”保存 | 读取失败时使用本机视口或默认显示 |
| Program | 规范化后的组件、端口、连线、诊断和编译结果 | 图代次与 revision | 从当前定义重新编译 |
| 运行记录 | Event、Effect 调用、claim 与 lease、待投递调用、事件源的观察进度 | 图代次、幂等键和租约 | 扫描未完成的记录并对账 |
| 共享状态 | 有界键值单元 | 独立的变化计数；需要条件写入时用比较并写入 | 读取当前单元并继续写入 |
| 指令执行记录 | SocietyService 执行的改变状态的指令 | ISA 恢复窗口、依赖保护和回收规则见 decision_898b8911a2ff | 按原指令身份恢复；到期栅栏不允许旧键自动重放 |
| Resident 数据 | 身份、输入与工作状态、对话、生命周期操作 | Resident 锁 | 恢复未完成的执行和生命周期操作 |
| 执行引用 | execution ID、消息 ID、终态引用 | 由 ExecutionService 的协议保护 | 向 ExecutionService 重新查询并归并 |
| 页面与 Markdown 投影 | 列表、摘要、详情、诊断和可读关系 | 不单独保存业务事实 | 从上述数据重新生成 |

上述数据保存在 PostgreSQL 的 LineageData 分区中，当前结构与迁移规则见 decision_71aa039e3211，各服务的数据所有权见 decision_86aed2f0e8c7。

ISA 记录服务于有限恢复窗口，不是永久审计。指令活动性、投影恢复、依赖释放和到期防重统一见 decision_898b8911a2ff；本服务的表与事务归属见 decision_71aa039e3211。长期 Trigger Path 事实和 Agent trace 由各自领域保留，不受 ISA 窗口清理影响。

## 组件怎样组成服务

```text
Portal Web ──instruction.execute / Domain RPC──┐
ExecutionWorker（Harness 工具）──instruction.execute──┤
客户端模式的进程 ──runtime.resident.wake──────────────┼──> SocietyServiceServer（socket 入口、读进程池、健康）
                                                      │         │
                                                      │         ├─ 指令内核 + isa/ 指令实现 ──┐
                                                      │         ├─ Domain RPC 方法目录 ────────┤
                                                      │         └─ LineageCoordinator（恢复与唤醒）
                                                      │                                        v
                                                      │                     Lineage / Resident / Program 领域模块
                                                      │                                        │
                                                      │                              LineageData（PostgreSQL）
                                                      │
          Effect 发出的其他服务指令 <──hatg 路由──────┘      变化通知 ──> Core NATS ──> 页面
```

| 模块 | 位置 | 职责 |
| --- | --- | --- |
| 服务入口 | `society_service/server.py` | `SocietyServiceServer` 在 Unix socket 上接收请求，分派顶层方法，管理只读请求的进程池，启动后台线程并汇总健康状态 |
| 指令实现 | `society_service/isa/` | 按指令类别分模块；`isa/__init__.py` 把它们登记到指令内核，并提供执行记录分组、权限锚点和投影补齐的实现 |
| 指令内核与路由 | `hatg/kernel.py`、`hatg/routing.py`、`hatg/socket_rpc.py`、`hatg/store.py` | 内核按 decision_898b8911a2ff 执行指令并保存执行记录；路由把目标属于其他服务的指令送到对方的 `instruction.execute` |
| Domain RPC 目录 | `society_service/contracts.py` | `DOMAIN_OPERATIONS` 列出全部内部方法及其读写性质、锁定对象和实现模块 |
| 客户端 | `society_service/transport.py` | `SocietyServiceClient`，Portal Web 等进程用它调用 Domain RPC 和 `runtime.resident.wake` |
| 协调器 | `society_service/coordinator.py` | `LineageCoordinator` 在启动时恢复工作，合并重复唤醒，按对象有界并行地推动 Resident 执行、Lineage 对账、生命周期操作和周期清理 |
| 运行端口 | `society_service/runtime/` | 把“归并 Resident 终态、轮询系统事件”两类运行操作接到 Program 运行器 |
| Lineage 与 Program 领域 | `lineage_domain.py`、`lineage_program*.py`、`function_block_domain.py` 等 | Lineage 定义的读写、Program 编译、事件分发、Effect 执行、Function Block 定义与实例 |
| Resident 领域 | `resident_*_domain.py`、`resident_protocol.py` 等 | Resident 身份、输入、执行、对话、Fork 与生命周期 |
| 数据存储 | `society_service/state_store.py` | LineageData 的 PostgreSQL 读写 |
| 锁与变化通知 | `invariant_locks.py`、`changes.py`、`lineage_definition_merge.py` | Resident 锁、目录锁及其等待统计；Lineage 作用域把副作用推迟到最外层操作结束，不互斥；提交时的定义合并；在锁外归并并发布变化通知 |
| 后台转发 | `resident_conversation_event_relay.py`、`resident_resource_projection.py` | 把新的对话事件转发给通知系统；把 Resident 的资源投影同步到 ResourceService |
| 领域端口装配 | `society_service/application.py` | 把 ResourceService、ExecutionService、Project 领域的客户端接到领域模块 |

`SocietyServiceServer` 的顶层方法如下：

| 方法 | 用途 |
| --- | --- |
| `instruction.execute` | 执行 SocietyService 拥有的 HATG ISA 指令；流式指令在同一连接上逐行返回（decision_96a887d83e8a） |
| `domain.call` | 调用 Domain RPC 目录中的一个方法，参数形式为 `{domain, name, args, kwargs}` |
| `runtime.resident.wake` | 唤醒协调器，推动某个 Resident 的执行 |
| `health` | 返回健康状态 |

Domain RPC 的领域和方法以 `society_service/contracts.py` 的 `DOMAIN_OPERATIONS` 为唯一目录。Lineage 与 Resident 的修改通过指令完成；Domain RPC 的目录不登记与指令等价的写方法，写方法只服务其他服务中指令实现的跨进程调用和没有对应指令的内部协作，例如附件保存、业务功能创建内部 Resident 和业务调用登记（decision_d918ebebc062）。

## 一次指令怎样在 SocietyService 中执行

1. 入口（Portal Web、Harness 工具或另一个服务）确认调用者身份，把调用按指令声明中的拥有服务送到 SocietyService 的 `instruction.execute`；Lineage 运行中的 Effect 在进程内直接调用。
2. 指令内核取得指令声明，校验参数，并请权限系统判断调用者能否执行。Resident 与组件的权限以其所在 Lineage 为准。
3. 只读指令直接调用 `isa/` 中的实现并返回；流式指令先送出当前数据，再持续送出后续变化。
4. 改变状态的指令写入执行记录。以 Lineage 为目标的记录归入该 Lineage，其他记录归入调用者；带幂等键的重复请求直接返回原结果。
5. 同一 Lineage 上的指令并发执行，不以全程作用域锁串行化。修改 Lineage 定义时，`expected_version` 在首次实际定义提交的短事务中与锁定行的当前版本比较；冲突拒绝提交，后续定义提交使用该次调用已经确认的版本上下文（decision_d918ebebc062）。不修改定义的准备操作不因此凭空产生定义版本检查；其他对象的版本语义以其所属操作契约为准。
6. `isa/` 中的实现调用领域模块读取、检查并计算修改，在数据库事务中锁定 Lineage 行，把修改合并到已经提交的定义上后提交（decision_d918ebebc062）。修改 Lineage 定义时，服务校验端口和连线、移除已经不兼容的连线、重新编译 Program 并产生新的图代次。
7. 提交后，变化收集器在锁外发布一次变化通知；内核保存执行结果。投影未完成时按“领域已提交、投影待确认”返回。

## 一次 Domain RPC 调用怎样执行

Domain RPC 通过 `DOMAIN_OPERATIONS` 绑定调用形状，再交给登记的领域方法检查权限与业务条件。只读调用由读进程池承担，写调用留在服务主进程；RPC 层不替领域方法选择业务锁。请求验证、读池恢复、缓存与错误合同统一见 decision_d918ebebc062，PostgreSQL 连接池和连接预算规则见 decision_86aed2f0e8c7。工作进程数量是部署配置，不在本总览重复记录某次现场的 CPU 与连接数。

## 一次 Program 运行怎样流动

1. 手动触发、计时、Resident 状态变化或其他事实进入事件源。协调器检查当前图代次、事件源的触发预算和事件源规则。
2. Event 先保存，再按连线创建待投递记录。连线的并发策略决定目标忙碌时丢弃、只保留最新还是排队。
3. 目标组件在租约内领取待办。Filter、Selector 和 Reducer 只处理本次激活时的候选；Effect 先保存调用记录，再按脚本逐条发出指令，每条完成并保存结果后再继续。
4. Effect 的指令由 `hatg/routing.py` 路由：由 SocietyService 执行的指令在进程内交给内核，由其他服务执行的指令通过对方的 `instruction.execute` 转交。调用者是本次运行的发起者，幂等键由 Event ID、Effect ID 与调用在脚本中的位置生成，入口种类为 `lineage`。
5. Resident 或其他系统在锁外工作。结束、失败或超时的事实写回后，协调器释放或完成这项待办，观察它的事件源决定是否产生下一项 Event。
6. 重复回调、服务重启和网络重试按相同的因果身份和幂等键归并；旧图代次送来的结果只保留为诊断，不推进当前 Program。

模板施加（`ApplyTemplate`）不创建临时运行结构：结构类模板通过定义提交写入新的 Resident、组件和连线；脚本类模板按施加者的权限直接执行一次 Effect 脚本，脚本发出的指令照常经过 ISA，Lineage 中不留下结构（decision_b04cac4188bb）。运行器因此总是按 Lineage 中已经保存的 Program 分发，不存在另一份只在内存中的执行计划。

## 并发、进程恢复与健康

### 锁的划分

SocietyService 按数据一致性边界保护提交；锁种类、取锁顺序、短事务及锁外调用规则统一见 decision_d918ebebc062。健康统计记录锁等待和持有时间，用于区分存储故障、清理失败与锁竞争。

### Resident 登记、Fork 与生命周期操作

- **准备工作不持有 Lineage 或 Resident 的锁。** 创建 Resident 文件夹、派发原生 Fork 等准备步骤只持有这次操作自己的执行权；身份与 Lineage 成员在最后一个短事务中一起发布，发布时重新检查来源 Resident 的成员身份和授权。
- **准备之前先保存操作记录和撤销方法。** 登记在创建 Resident 文件夹或派发原生 Fork 之前，把准备操作和出错时的撤销步骤保存在 `resident_lifecycle_operations` 表中；原生 Fork 的完整请求在派发前保存。进程在发布前退出时，执行权自动释放，由生命周期执行器继续撤销。
- **结果未知时不重复派发。** 恢复时先查找原来的执行；请求尚未被接受时，只能用同一请求、同一执行身份重新提交。结果仍然未知时保留撤销记录，不猜测失败，也不发起第二次 Fork。
- **只在拿到确定的终态后才撤销。** 撤销只释放本次新建的绑定，并归档本次新建的 Resident 文件夹；已有的 Lineage 文件夹、开发目录和之后发布的 Resident 保持不变。准备失败的文件移入同一父目录下、以操作 ID 标记的归档。
- **同一 Resident 同时只有一个未完成的生命周期操作。** 不同 Resident 的清理与执行使用各自独立的有界工作额度；扫描时跳过仍被占用的操作，再按批量上限处理，避免一批长时间操作占满扫描、耽误其他 Resident 的恢复。
- **跨文件系统搬移先完整复制再发布。** 目标侧先在临时目录中复制完整，再发布目录；清理源目录中断后重试时，不用残缺的源覆盖完整的目标。
- **区分“结果未知”和“确认失败”。** 断线、超时或回执丢失时，保持同一操作、同一步骤和同一次尝试的身份，接续已经被接受的执行。可以安全重试的会话释放收到确认的失败或取消终态后，生命周期执行器保存新的尝试序号，用新的执行身份重试这一步；它不会停留在同一个失败结果上，也不会跳到后续步骤或解除 Resident 的清理屏障。每次重试都重新核对会话身份，旧的清理不会释放新的绑定。
- **跨 Lineage 迁移先提交成员变化，再搬文件。** 两个 Lineage 的成员变化、目标 Resident 的路径和搬移意图在同一事务中提交；文件复制和生成文档的更新由生命周期执行器随后完成，完成之前这个 Resident 不开始新的执行（decision_6fc653d55c79）。
- **Lineage 文件夹属于 Lineage。** 修改单个 Resident 的项目或完成项目初始化，只更新项目关联和开发路径，不搬移整个 Lineage 文件夹；跨 Lineage 迁移只搬移明确的 Resident 子目录。

### 删除 Lineage

提交删除意图后，服务先把 Lineage 在资源索引中标记为已删除，再执行可能较慢的 Provider 会话释放，让搜索和引用尽快停止显示已经无法打开的 Lineage。资源标记与目录清理分步进行，目录解绑与归档在会话释放之后。每一步都记录在生命周期操作中，中断后从原步骤继续，重复执行结果相同。资源已标记为删除、定义尚未移除时中断的，再次执行 `DeleteLineage` 照常由权限系统判定（默认模板要求对该 Lineage 有 admin，decision_b820faf42218）并完成删除；从未在资源服务登记过的 Lineage 仍报找不到。

### 进程重启

协调器根据持久的 Resident 工作、生命周期操作与 Program 恢复候选接续未完成工作；Program 的恢复候选与时间唤醒分开维护，不以启动时全量扫描所有 Lineage 代替恢复索引。Event 投递、结算及协调器唤醒与重试语义见 decision_4adcc1ff2f2c。

- **同一时刻只有一个实例为定时 Event Source 唤醒 Lineage。** Dev 与 Release 共用 Lineage 数据，为到期的 `time.reached` 等定时来源唤醒 Lineage 的工作只由持有 LineageData 中“定时租约”的协调器执行；租约有效期 90 秒，持有者每 30 秒续约，持有者停止后另一个实例接手，两个实例因此不会同时对同一个 Lineage 做定时对账。设置 `PORTAL_LINEAGE_TIMER_INSTANCE` 时，只有名称相同的实例有资格持有租约。已经耗尽触发次数的来源和计划时间已过的来源不再登记唤醒时间，到期的工作由当次对账或重试处理。
- **进入队列的消息立即派发自己的 Event。** 消息被 Resident 接受后，SocietyService 通知协调器；协调器为每条消息在单独的工作线程（8 个）中派发它引起的 `resident.message_received` Event，并随后对这个 Lineage 做一次对账，确保派发失败时 Event 仍会产生。Family Router 因此在消息到达时立即开始路由，多条消息并行路由（decision_44cf587c1721）。没有派发入口时，协调器只请求对这个 Lineage 对账。对账只依据已经保存的 Event、claim、lease、终态和当前图代次推进安全的步骤，不凭内存推测外部动作是否已经发生。协调器的执行、生命周期和取消三类工作各使用 8 个工作线程，同一对象的重复唤醒合并为一次；Resident 终态归并失败时，30 秒后重试。周期清理默认每 300 秒一次（`PORTAL_LINEAGE_HOUSEKEEPING_SECONDS`，最短 60 秒），每次最多检查 128 条已过期的执行活动轨迹。

### 健康

`health` 返回服务实例、构建 ID、进程号、启动时间、读进程池、协调器、锁统计、通知发布器、对话事件转发、Resident 资源投影、生命周期操作和 LineageData 的状态。以下任一情况都会使 `ok=false`：协调器、清理、对话事件转发、资源投影或生命周期操作最近出错，协调器线程不在运行，或者启用投影的服务进程中这两个后台转发任一停止。健康结果用于运维判断，不替代具体请求的权限与一致性检查。服务进程崩溃不会丢失已经提交的数据；尚未提交的页面编辑由页面提示用户重新提交。

### 读取性能

- Resident 对话快照只读取当前一页消息需要的已结束输入状态，以及这个 Resident 全部尚未结束的输入状态；读取成本随页面大小和在途输入增长，不随历史增长。
- 批量读取 Resident 状态时，先通过输入状态索引找到每种状态的最近记录，再选出最新输入；已结束的历史越来越多，每次扫描的范围也不扩大。
- 活跃 Agent 列表只为正在工作或有排队输入的 Resident 读取执行账号，空闲的 Resident 跳过这一步；每次仍读取当前状态，并按当前调用者的资源权限显示或匿名化。
