---
kb_id: decision_d918ebebc062
type: decision
project: AI员工
created_at: '2026-09-02'
version:
  number: 50
  created_at: '2026-09-02'
  created_by:
    user: ''
    agent: ''
  updated_at: '2026-10-19T08:44:04Z'
  updated_by:
    user: ''
    agent: resident_2e9ea0072551
importance: 10
metaness: 2
ai_generated: false
links:
  source:
  - ${code_root}/management_portal/society_service/service_invocation_domain.py
  - ${code_root}/management_portal/hatg/store.py
  - ${code_root}/management_portal/hatg/kernel.py
  - ${code_root}/management_portal/society_service/contracts.py
  - ${code_root}/management_portal/society_service/server.py
  - ${code_root}/management_portal/society_service/domain_ports.py
  - ${code_root}/management_portal/society_service/invariant_locks.py
  - ${code_root}/management_portal/society_service/state_store.py
  - ${code_root}/management_portal/society_service/transport.py
  - ${code_root}/management_portal/society_service/resident_protocol.py
  - ${code_root}/management_portal/society_service/resident_execution_work.py
  - ${code_root}/management_portal/society_service/resident_conversation_stream.py
  - ${code_root}/management_portal/society_service/workspace_domain.py
  - ${code_root}/management_portal/society_service/project_domain.py
  - ${code_root}/management_portal/society_service/lineage_domain.py
  - ${code_root}/management_portal/hatg/declarations.py
  - ${code_root}/tests/cases/test_society_service_process.py
  - ${code_root}/management_portal/society_service/changes.py
  - ${code_root}/management_portal/society_service/coordinator.py
  - ${code_root}/management_portal/society_service/runtime/contracts.py
  - ${code_root}/tests/cases/test_lineage_lock_boundaries.py
  - ${code_root}/tests/cases/test_society_service_coordinator.py
  - ${code_root}/management_portal/society_service/lineage_definition_merge.py
  - ${code_root}/tests/cases/test_lineage_definition_merge.py
  - ${code_root}/tests/cases/test_lineage_concurrent_writes.py
  - ${code_root}/management_portal/society_service/resident_storage.py
  - ${code_root}/management_portal/society_service/resident_workspace_storage.py
  - ${code_root}/management_portal/society_service/resident_lifecycle_domain.py
  - ${code_root}/management_portal/society_service/resident_registration_domain.py
  - ${code_root}/management_portal/society_service/resident_fork_domain.py
  related:
  - decision_3e07c6d35cac
  - decision_96a887d83e8a
  - decision_1b2bdf5c33d6
  - decision_6fc653d55c79
  - decision_71aa039e3211
  - decision_c524c775e0be
  - decision_898b8911a2ff
  - decision_6ad0384e020d
  - decision_abaedeefca6f
  - decision_4adcc1ff2f2c
  - decision_d19b9909a674
  supersedes: []
  derived_from: []
---
# SocietyService Domain RPC 与并发控制协议

SocietyService 是 Portal 中负责 Resident 与 Lineage 的后台服务（架构见 decision_1b2bdf5c33d6）。Portal 的其他进程通过两类接口使用它：

- **HATG ISA 指令**：人、Resident 和 Lineage 内部组件读取和修改对象的统一入口，格式与执行规则见 decision_898b8911a2ff。
- **Domain RPC**：Portal 内部受信任进程按“领域.方法”直接调用 SocietyService 的接口。Portal Web 用它组装页面数据；业务功能用它登记交给 Resident 的工作；执行层和资源服务用它读取身份、投影和状态。

本篇是 Domain RPC 的接口合同：先说明谁可以调用、请求怎样传输、服务怎样执行一次调用，再说明并发、锁、授权和错误规则，最后说明正式方法目录及错误恢复合同。

## 先建立共同语言

| 概念 | 含义 | 在接口中的作用 |
| --- | --- | --- |
| actor | 发起操作、接受权限判断的主体，可以是 Portal 用户、Resident 或受信任的服务身份 | 带 `actor` 参数的方法按它检查对象权限 |
| 领域（domain） | 按数据职责划分的一组方法 | 以 DOMAIN_OPERATIONS 登记的领域为准 |
| 方法目录 | `society_service/contracts.py` 中的 `DOMAIN_OPERATIONS`，逐个登记每个方法 | 决定哪些方法可以调用、按什么规则执行 |
| 读方法与写方法 | 读方法只返回当前数据；写方法会修改数据或推进状态 | 读方法可以交给读进程池执行，写方法留在主进程 |
| 状态锁 | 为一组必须保持一致的数据设置的短时互斥 | 修改 Resident 状态用 Resident 锁；Lineage 定义的写入不取锁，在提交时合并 |
| 提交时合并 | 一次 Lineage 定义写入在数据库事务内锁定 Lineage 行，把自己的修改合并到已经提交的定义上 | 同一 Lineage 上的指令并发执行，各自的修改都保留，见“Lineage 定义的并发写入” |
| revision | Lineage 定义的变化计数，每次提交加一 | 用于 WatchLineage 游标、页面显示和定义提交的可选版本前提；具体比较边界见本篇“Lineage 定义的并发写入” |
| 变化收集器 | 在一次修改中收集“哪些对象变了”的组件 | 提交并释放锁后，统一发布一次变化通知 |
| 错误码 | 调用方可以稳定判断的机器错误类别 | 与可读说明和结构化细节一起返回 |

## Domain RPC 与指令的分工

| 调用方 | 使用的接口 | 典型用途 | 权限 |
| --- | --- | --- | --- |
| Portal Web 页面后端 | 页面上的修改（包括画布坐标和碎片）通过 HATG ISA 指令提交；页面数据的读取通过 Domain RPC | 读取 Lineage、Resident 对话和工作区 | 以登录用户为 actor，按对象权限检查 |
| Portal Web 的业务功能 | Domain RPC 的 `service_invocation` 领域 | 把共享搜索等业务工作交给 Resident，并观察结果 | 按业务任务的发起者检查 |
| ExecutionService 与执行层 | 顶层 `runtime.*` 方法与少数内部方法 | 报告 Resident 工作结束、唤醒 Resident、读取身份与授权 | 受信任的服务身份 |
| ResourceService | 内部读方法 | 读取 Lineage、Resident 与组件的资源投影 | 受信任的服务身份 |
| Resident、Effect、Function Block | 只使用 HATG ISA 指令 | 读取和修改对象 | 不在 Domain RPC 的信任范围内 |

Lineage 与 Resident 的修改几乎都通过指令完成：SocietyService 的指令实现（`society_service/isa/`）直接调用领域模块，不经过 Domain RPC。Domain RPC 的方法目录不登记与 ISA 指令等价的写方法，页面和 Resident 的修改只能经过指令入口。目录中的写方法服务两类调用：其他服务中的指令实现跨进程调用 SocietyService 领域，例如 ResourceService 的 `DeleteProject`、`UpdateWorkspaceFile` 和投影重试，这些调用已在指令入口检查过权限；没有对应指令的内部协作，例如附件与粘贴图片保存、个人 AMA 建立、业务功能创建内部 Resident、业务调用的登记与撤回、Resident 发现的摘要重建。目录中的方法必须有 SocietyService 之外的调用方，没有调用方的方法从目录删除。Lineage 的组织归属与 Project 层 Lineage 的规则见 decision_6fc653d55c79 与 decision_3e07c6d35cac。

## Resident 数据的归属与读取

Domain RPC 组合各领域的 Resident 事实用于读取，不产生另一份可整体回写的聚合 JSON。成员关系、配置、来源、Workspace 绑定与运行观察的表归属统一见 decision_71aa039e3211；删除身份与历史读取见 decision_0f57656a2acd。

有效成员关系以 `lineage_nodes.lineage_id` 为准。读取投影不能创建、恢复或改变身份；修改必须调用相应领域入口。Agent、trace 与执行由 AgentData 管理（decision_b1ab45c4b3ec）；创建及 Fork 的确认前置、初始化恢复由 decision_5bf5bfbd52cb 与 Resident 生命周期规范定义，不能因读取到来源字段暗中改为 Fork 或移动。

## 请求格式与传输

Domain RPC 使用 Portal 内部 RPC 的传输方式：本机 Unix socket 上每行一个 JSON 对象，请求带请求 ID，响应用统一的成功或失败信封返回（decision_96a887d83e8a）。能连接这个 socket 只说明请求来自受信任的 Portal 进程；进入方法后，服务仍按 actor、Lineage、Resident 权限和状态逐项检查。

`domain.call` 的参数是 `{domain, name, args, kwargs}`：`domain` 与 `name` 必须在方法目录中登记，`args` 是数组，`kwargs` 是对象。服务按方法的完整函数签名绑定参数，检查必填参数、重复赋值、未知参数和仅限关键字的参数，并补上默认值。绑定失败统一返回 `ValueError`，这一步发生在读取任何数据、查询权限和投递读进程池之前。绑定只检查调用形状；字段类型、取值范围、权限和业务规则由领域方法检查。方法内部抛出的 `TypeError` 属于执行失败，不改判为参数错误。

结果以 JSON 返回：路径编码为字符串，元组和集合编码为数组，映射编码为对象。进程内调用可以得到原生 Python 对象，含义相同。

调用方使用 `society_service/transport.py` 中的 `SocietyServiceClient`。它的默认超时是 120 秒，读取健康状态用 5 秒，唤醒 Resident 用 1 秒。建立连接失败（包括连接阶段超时）时抛出 `SocietyServiceUnavailable`，请求尚未提交；请求发送或等待响应阶段超时抛出 `SocietyServiceBusy`，结果可能未知。连接中断或响应格式错误按本篇错误合同返回 `SocietyServiceUnavailable`。客户端不自动重发结果未知的写请求。

## 顶层方法

| 方法 | 请求 | 行为与结果 |
| --- | --- | --- |
| `health` | 无业务参数 | 返回健康快照，内容与判定规则见 decision_1b2bdf5c33d6 的“健康”。 |
| `domain.call` | `{domain, name, args, kwargs}` | 调用方法目录中的一个方法，返回方法的结果。读方法交给读进程池，写方法在主进程执行，提交并释放锁后发布变化通知。未登记的方法、参数容器类型错误或签名不匹配时返回 `ValueError`。 |
| `instruction.execute` | 一次完整的指令调用、入口种类和调用者身份 | 执行 SocietyService 拥有的 HATG ISA 指令；流式指令在同一连接上逐行返回。格式与错误见 decision_96a887d83e8a 与 decision_898b8911a2ff。 |
| `runtime.resident.wake` | `resident_id`（规范的 `resident_*` ID） | 唤醒协调器推动这个 Resident 的执行，立即返回 `{accepted: true}`。 |

## 方法目录

`DOMAIN_OPERATIONS` 是 Domain RPC 唯一的方法清单。每个方法登记以下内容：

| 字段 | 含义 |
| --- | --- |
| `mode` | `read` 或 `write`。只有 `read` 方法进入读进程池；会顺带核对并写回状态的读取（例如 `service_invocation.get_invocation`）登记为 `write`。 |
| `authority` | 负责这项数据的领域，例如 `resident`、`lineage`、`project`。 |
| `scope` 与 `target` | 这次调用涉及哪个 Lineage 或 Resident：`scope=lineage` 时从名为 `target` 的参数取 Lineage ID，`scope=resident` 时取 Resident ID 并换算为它所属的 Lineage；`scope=owner` 表示涉及多个对象或不针对单个对象，由方法自己处理。它用于附加诊断信息和预取权限，不用于替方法加锁。 |
| `result` | 可选的结果格式名；登记了格式的方法，服务在返回前校验结果。 |
| `implementation` | 实现这个方法的模块。 |

服务按参数名读取 `target`，不按方法名前缀、参数位置、ID 前缀或返回值推断。服务启动时检查每个登记的方法：实现模块中存在同名函数、`mode` 与 `scope` 取值有效、`target` 对应的参数存在、结果格式已定义；任一不满足时服务不启动。

## 一次 Domain RPC 怎样执行

1. `SocietyServiceServer` 解析请求信封，按顶层方法分派。
2. 对 `domain.call`，服务在方法目录中找到方法，完成参数绑定。
3. 读方法交给读进程池。每个读进程在一份读取快照中执行，定义缓存按 revision 与依赖指纹判断有效性，不在每次调用前清空；授权由具体操作经权限系统检查。进程池损坏时，服务重建进程池并重试这一次读取一次；第二次失败报告给调用方。权限拒绝或参数错误以结构化错误返回，不会让读进程退出。
4. 写方法在主进程执行。服务同样先在锁外预取访问控制信息，再在变化收集器中调用方法；方法在短锁内检查并提交。
5. 提交成功并释放全部锁后，变化收集器发布一次变化通知；修改 Lineage 时同时唤醒协调器检查这个 Lineage。
6. 服务把结果或结构化错误放进响应信封返回。

这个顺序让权限查询、慢速外部调用和通知发送都发生在锁外，调用方只看到已经提交的数据。

`instruction.execute` 的非流式只读 ISA 同样进入现有读进程池，按指令目录的 owner、mutates 和 streaming 属性分派，不按名称前缀猜测。子进程接收原始参数与已验证调用者，仍由标准 ISA 内核校验参数和当前权限；普通错误返回原有响应结构。写指令、流式订阅和恢复依赖释放留在主进程。读取的 CPU 工作由此与取消、消息接纳及终态处理分离，数据库和身份仍保持同一事实来源。

## 并发、版本和锁

### 锁的种类

| 锁 | 保护的数据 |
| --- | --- |
| `resident:<id>` | 一个 Resident 的输入接收、运行设置、执行绑定、停止意图、结果归并和跨存储投影的授权 |
| `resident-conversation:<id>` | 一个 Resident 的消息、输入状态、队列、当前活动和递增的事件游标，由唯一的写入者按顺序提交 |
| `catalog:<name>` | 登记目录自身的一致性，例如跨 Lineage 的唯一性检查；`catalog:principal-hierarchy` 让 Project 主图的唯一性检查与组织结构修改依次进行 |
| Program 执行权（每个 Lineage 一个文件锁） | Program 的运行处理：终态处理、对账、Program 控制端口和事件源回收。改变执行契约的定义写入（例如删除或重连正在运行的组件）只在提交和契约清理期间持有它 |

上面的锁由进程内的可重入锁和 `PORTAL_LINEAGE_ROOT/.locks/` 下的文件锁组成，同一线程可以重复进入，多个 Portal 进程之间也互斥。另有两种“归属锁”，以尝试获取的方式使用，取不到就说明另一个进程已经在做这件事，本次放弃：`resident-continuation:<id>` 保证每个 Resident 同时只有一个完成等待者，`resident-lifecycle:<operation_id>` 保证每个生命周期操作同时只有一个执行者。等待或持有超过 `PORTAL_LINEAGE_SLOW_LOCK_SECONDS`（默认 1 秒）的情况计入健康统计的近期慢事件。

### 通用规则

1. **修改只在必要的数据上短暂持锁。** 同一 Lineage 中不同 Resident 的工作可以并行；`resident:<id>` 与 `resident-conversation:<id>` 是两个独立的锁，不合并为跨数据库的长锁。
2. **权限信息在进锁之前准备好。** 进入锁后只使用已经绑定的 actor 和已经取得的授权材料，不在锁内等待其他服务。
3. **未提供版本时按当前定义合并。** 同一 Lineage 上的指令并发执行：修改不同对象或同一对象不同字段的指令都生效；修改同一字段时后提交者的值保留；修改或引用了已被先提交的指令删除的对象时，本条指令失败并返回 `instruction.conflict`，不做部分修改。
4. **变化计数各自推进。** Lineage 定义的 `revision`、布局的 `layout_revision`、共享状态的计数和图代次各自记录变化，只用于游标、显示和内部去重，不会自动成为指令的前提；显式提供定义 revision 的 expected_version 时，只在本次调用对各 Lineage 的首次定义提交短事务内锁定 Lineage 行并比较，冲突拒绝该提交；成功后同一调用的后续提交不重复比较，回滚不消耗版本前提。
5. **外部工作在锁外进行。** 需要调用外部执行或跨存储写入的方法，先在事务内保存意图或登记待办，再在事务外执行，最后按操作 ID、代次、租约、稳定的消息 ID 和幂等条件归并结果。
6. **读取不取写锁。** 对话快照、分页和状态查询不占用 Resident 或对话写入锁。只做判断的读取也一样：执行是否仍是当前工作、是否已请求停止、Send now 有无待处理回执，都读取已提交的执行控制态，不进入 Resident 锁；判断结果在返回时就可能过期，持锁读取不会让它更准，真正的修改在提交前重新核对。同时读取 AgentData 与 LineageData 时，用事件游标在读取前后核对，最多重读一次，之后由游标补读收敛。是否使用读进程池不改变调用者的可见范围和读取结果的含义。
7. **变化通知只在成功提交之后发出。** 事务失败、对象冲突和权限拒绝不发通知。提交后的多个后续效果中有一个失败时，其他效果照常执行，再报告第一个错误。

### Resident 投递准入

Program Route 的消息准备与正式入队使用不同的临界区。路由生成固定消息 ID，在锁外执行指令鉴权、引用解析、附件准备和回执投影；Resident 输入接收者在每次持久变更前进入同一进程中绑定的投递准入边界，先取得来源 Lineage 执行权、重新读取并校验 Route，再取得目标 Resident 锁。准备期间路由被删除或重连时，旧输入在提交前拒绝。普通人类消息不携带图投递准入条件。

输入被接纳后，发送额度回执与 Program 交接关系在释放准入边界前登记，图编辑不会在消息入队与交接登记之间插入。重复 ISA 回执沿用固定消息身份并核对交接关系。后台完成对账只在观察登记与完成游标提交时持有执行权，耗时的完成处理、后续派发与积压消费在其外进行；提交游标前再次核对观察所用定义。终态观察者按当前事件源契约派发，不能在整轮通知期间占有观察者 Lineage 的执行权。

### Program 恢复的临界区与永久失败

恢复 Resident 交接和观察完成消息时，先在执行准入锁外读取当前输入状态与完成证据，再进入 Lineage 执行准入锁核对当前定义、成员、交接步骤和执行绑定。定义在读取期间改变时，本轮丢弃该定义对应的完成观察，后续协调重新读取。结果投影在释放执行锁后组装；消费游标、推进执行和提交终态仍由执行授权边界保护。

持久事件缺少已验证身份、或身份结构无效时，该事件需要显式重新签发。恢复负责人给待投递、到期通知、算子待办和 Resident 交接记录写入 `recovery_blocked`，保留原事件、错误、时间和 `requires_reissue`；重复扫描不再自动恢复它，不补造身份，也不把它计入可调度队列。未阻塞的新事件继续调度，阻塞记录仍可查阅或按已有管理接口清理。恢复候选查询排除只剩这些阻塞工作的记录。

网络、数据库和暂时的权限失败继续走原有重试；不能仅凭一次失败或笼统的异常类别把工作永久停掉。阻塞标记写入失败也按暂时故障报告并重试。

### Lineage 定义的并发写入

Resident 登记在同一事务中提交身份和 Lineage 成员关系。首次规范化和提交时的并发合并校验都接收登记流程提供的同一份新 Resident 引用；此引用只覆盖本次事务正在创建的身份，其余节点仍从正式目录校验。引用仅作为调用参数传递，不写入图定义，也不放宽对已删除或未登记节点的检查。

一条指令读取 Lineage，计算出自己的结果，然后提交。提交在一个数据库事务内进行：先用 `SELECT ... FOR NO KEY UPDATE` 锁定 `lineages` 中这一行，读取已经提交的定义；如果在本指令读取之后已有其他写入提交，就做一次三方合并（`lineage_definition_merge`）：

- 起点：本指令读到的定义；
- 本方：本指令算出的定义；
- 当前：此刻已经提交的定义。

成员、关系、Function Block、碎片、Program 组件、连线和 Function Block 实例按对象 ID 合并，其他值逐字段合并；本指令没有改动的值保持当前值。画布坐标由布局写入单独负责，对象上的 `updated_at` 是每次写入都会重新打的时间戳，二者都不算作修改：合并后逐字段放回，本指令改动过的值（或本指令新增的对象）用本指令的值，否则保留已提交的值，因此另一条指令期间新增的对象和移动保留各自的坐标。`revision`、`updated_at` 等 Lineage 级字段由提交重新赋值。

合并后按删除的规则清理引用：被删除的 Resident 或组件，其关系、连线和职责绑定一并移除；被删除的 Leader 按删除时的继任规则重新选出。本指令修改了另一条指令删除的对象，或清理会撤销本指令新增、修改的内容（即本指令引用了已不存在的对象）时，本指令失败，错误码为 `instruction.conflict`，整次写入回滚。

定义行锁仅在短提交事务期间持有。省略 `expected_version` 时按上述规则合并；同一 Function Block 定义被并发修改或合并结果未通过图归一化校验时，整次提交以 `instruction.conflict` 拒绝。

需要基于所读定义提交时，先取 `GetLineage.result.lineage.revision`，再作为调用顶层的 `expected_version`。ISA 通过调用内 ContextVar 传递前提，定义写入器在本次调用对各 Lineage 的首次提交时锁定并比较 revision；同一调用后续提交不再比较，事务回滚不消耗前提。版本不一致时返回 `instruction.conflict`，`error.details` 包含 `object_id`、`expected_version` 和 `current_version`。跨 Lineage 操作可用 `{Lineage ID: revision}` 指定检查集合，省略某个键不对它附加版本前提。布局的 `layout_revision` 不能代替定义 revision。

外部服务调用、文件复制和整个多步处理器不包在持有 Lineage 行锁的事务内。没有定义提交的 `PrepareWorkspaceHandoff` 不做定义版本比较。原来的 Lineage 作用域只负责把副作用（变化通知、资源投影、成员同步）推迟到最外层操作结束后执行，不再互斥。

### Resident 状态处理与异步执行

Resident 的状态命令在短锁内读取最新状态、校验命令、保存意图后立即返回。每条输入的先后顺序由 Resident 的输入状态记录。模型运行、创建或 Fork 会话、外部状态查询、等待终态、读写对话、组装快照、资源投影和通知，都在锁外进行。停止请求单独提交停止意图，模型仍在运行或对话写入变慢时也能被接收。全局协调器只负责发现和分派可以恢复的工作，各 Resident 各自推进。

额度恢复扫描在锁外批量读取 Resident、对话状态、执行绑定和额度，再在锁外筛选候选，不为确认“没有变化”而逐个进入 Resident 锁。只有真正要执行的关闭、打开或保留状态变化，才作为独立的短修改进入候选 Resident 的锁，并在锁内重新核对快照版本、转换记录、保留标记、人工放行和接单状态。

执行回调携带 Resident ID、输入消息 ID 和 execution ID；归并前重新读取当前绑定，只有与当前有效工作匹配的回调才能改变状态。回复写入分三步：先在 Resident 记录中保存有界的授权意图；释放锁后，用稳定的消息 ID 在对话写入锁内提交消息和对应事件；再短暂提交确认。随后提交引用这条回复的输入终态，并把 Resident 标为空闲。重复回调复用同一个消息 ID。某条输出无法按规范提交时，放入有界的隔离区并推进执行事件游标，不无限重试，也不因此终结执行或阻塞取消与绑定。已经结束的输入不会被迟到的快照改回运行中。

完成等待者处理执行回调时，只有写入才进入 Resident 锁，锁内不读取完整的 Resident 记录：

- **事件游标**：只向前推进，用一条“只在新序号更大时更新”的条件语句提交，不取 Resident 锁；一批事件推进一次。
- **执行快照**：先在锁外读取控制态，判断这次快照是否带来终态或错误字段的变化；没有变化就不取锁。需要写入时在锁内重新核对执行身份后提交。发布对话状态以提交版本号为准：发布后版本号变了就按新状态重新发布一次。
- **回复授权与确认**：锁内只读取不含提示词正文的执行控制态，再写入授权意图或确认。
- **恢复等待者**：先在锁外确认 Job 需要挂接、没有存活的等待者，并完成可能访问 Execution 服务的 Steward 额度检查；锁内只重新核对并保存挂接。

### Lineage 与 Resident 的交界

Fork、迁移与删除先校验成员关系，并保存可恢复的操作意图；文件和 Provider 上的慢操作在锁外完成，结果按操作 ID 和当前成员关系重新校验后提交。一次提交需要同时维护 Lineage 与 Resident 的一致性时，先取排序后的 Resident 锁，再进入数据库事务；Lineage 行在事务内提交时锁定。普通的 Resident 状态提交先释放自己的锁，再请求更新 Lineage。删除成功后，迟到的回调不能清除删除状态、重新接单或启动已删除身份。未完成的操作通过持久记录恢复，清理步骤可以重复执行。

### 创建或迁移 Lineage 时怎样确定项目

服务按请求中的 `project_id` 向 ResourceService 读取这个项目的正式记录和路径，解析一次，把同一结果用于权限检查、目录绑定和保存。项目由稳定的 ID 指定，创建和迁移都只查询目标项目。

### 修改模型设置时怎样保持运行控制可用

读取 Provider 支持的模型和配置可能需要网络请求，这一步在 Resident 锁之外完成。提交设置时重新核对账号路线和当前配置；期间有变化就重新校验，持续变化时返回冲突。读取配置期间，停止、会话重启和输入状态提交照常处理。

复用 Resident 的业务调用使用 `resident_handoff.py`，保留已有 Resident 自身的 Provider、账号和运行设置；调用参数只用于新建初值，不在每次调用时发起迁移或设置更新。共享适配层与显式控制入口的分工见 decision_725dedbe89b2。

### 指令的重复提交

SocietyService 的 ISA 执行使用统一指令内核，幂等键、活动调用锁、中断判定、投影恢复、Effect 恢复保护及到期防重的完整合同以 decision_898b8911a2ff 为准。Domain RPC 不另定义 ISA 重试窗口或固定中断等待时间，也不因响应未知自动重执行业务。SocietyService 所属恢复表与 Effect 清理事务见 decision_71aa039e3211。

## 授权层次

一次 Domain RPC 调用要同时通过四层检查：socket 是内部传输边界，方法目录是方法暴露边界，actor 检查是用户与对象的权限边界，状态锁与提交时合并是一致性边界。

- **带 `actor` 参数的方法按 actor 检查对象权限。** 页面以用户身份调用时，服务检查该用户对 Project、Lineage、Resident、工作区和资源的可见性或管理权；参数有默认值也不免除检查。
- **没有 `actor` 参数的方法只供内部服务调用。** 它们假定调用方已经通过 socket 或上层入口认证，例如资源投影读取和执行恢复。`list_resident_discovery_catalog`、`list_removed_discovery_residents` 与 `filter_visible_resident_ids` 另外要求受信任的服务身份。
- **指令准入由指令层检查。** `domain.call` 不是第二个 ISA 入口，只接受经过认证的 Portal 内部进程；目录不登记与指令等价的写方法。领域方法仍按其合同检查 actor 对目标对象的访问范围，内部进程认证不替代对象权限。新增写方法前先确认没有对应的 ISA 指令。

## 错误和调用方恢复

领域错误统一包含 `code`、`message`、`http_status`、`retryable`、`commit_state` 与 `details`。本地调用、RPC、指令的幂等重放和 HTTP 使用同一套错误定义；错误按错误码传输，不传输 Python 异常类名。`SocietyServiceClient` 收到错误后按错误码还原为调用方可以捕获的类型：

| 错误码 | 还原后的类型 | 含义与调用方动作 |
| --- | --- | --- |
| `operation.invalid` | `ValueError` | 参数、方法名或业务条件不合法；修正请求，原样重试没有意义 |
| `operation.forbidden` | `PermissionError` | 缺少对象权限或内部身份；请求正确的授权，不要通过猜测 ID 探测 |
| `operation.not_found` | `KeyError` | 目标不存在，或调用方不可见；刷新列表后确认目标 |
| `operation.file_not_found` | `FileNotFoundError` | 文件不存在 |
| `operation.conflict` | `FileExistsError` | 目标已经存在或状态冲突 |
| `instruction.conflict`（`details.conflict=true`） | `LineageConflictError` | 本指令修改或引用的对象已被先提交的指令删除，或 Lineage 正在删除；错误中保留 `resource`。重新读取后再决定 |
| 其他 `instruction.*` | `InstructionError` | 指令错误，保留错误码、细节与 HTTP 状态 |
| `projection.pending` | `CommittedProjectionError` | 领域已经提交、投影尚未确认；不能重新执行业务操作，见 decision_1b2bdf5c33d6 |
| `lineage.busy` | `SocietyServiceBusy` | 服务忙；稍后重试读取，写请求先核对是否已经生效 |
| `lineage.unavailable`、`resource.unavailable` | `SocietyServiceUnavailable` | SocietyService 或它依赖的 ResourceService 暂时不可用 |
| `service_invocation.conflict` | `ServiceInvocationConflict` | 业务调用的计划或批次与已登记的内容不符 |
| `contract.invalid_result` | `ResultContractError` | 方法结果不符合登记的结果格式 |

连接尚未建立时（包括 connect 超时）返回 `SocietyServiceUnavailable`，提交状态为 `not_committed` 且可重试；发送或读取阶段中断、超时时，写操作的提交状态为 `unknown`，即使错误带有 `retryable` 提示，客户端也不自动重发写请求。已经取得 Resident 与输入消息 ID 的观察者可以按原 ID 继续查询，保留原截止时间与取消条件；没有收到接受回执时不能据此重新提交输入。

## 方法目录的维护

完整领域、方法名、参数签名、读写模式和锁范围以 `management_portal/society_service/contracts.py` 的 `DOMAIN_OPERATIONS` 为准，服务启动时绑定实际实现并校验签名；文档不复制易漂移的方法清单。`tests/cases/test_lineage_rpc_contracts.py` 与 `test_lineage_server_dispatch.py` 核对声明和分派。

ServiceInvocationStore 按数据库实例初始化并校验结构一次。写锁按 `(actor, surface, search_id)` 区分，读取使用一致快照；轮询和读取计划不重复初始化结构或获取全局写锁。

Lineage 定义指令仅构建其返回值需要的投影：编译和诊断读取已鉴权的定义快照；Handoff、候选 Fork、Function Block 实例和 ApplyLineageChanges 在只返回对象或 ID 时跳过完整运行视图。ApplyLineageChanges 删除 Resident 后读取最终定义，以计入 Function Block 重新展开时可能删除的图对象。Handoff 交接项在图锁外校验，提交时重新核对关系和节点；同一来源与目标的 ISA 创建请求返回已有关系。
