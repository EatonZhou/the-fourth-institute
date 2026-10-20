---
kb_id: decision_6fc653d55c79
type: decision
project: AI员工
created_at: '2026-09-01'
version:
  number: 51
  created_at: '2026-09-01'
  created_by:
    user: ''
    agent: ''
  updated_at: '2026-10-19T08:30:01Z'
  updated_by:
    user: ''
    agent: resident_2e9ea0072551
importance: 10
metaness: 3
ai_generated: false
links:
  source:
  - ${code_root}/management_portal/society_service/resident_execution_domain.py
  - ${code_root}/management_portal/static/portal_society_service.js
  - ${code_root}/management_portal/hatg/catalog/organization.py
  - ${code_root}/management_portal/society_service/isa/organization.py
  - ${code_root}/management_portal/society_service/lineage_read_cache.py
  - ${code_root}/management_portal/society_service/templates/builtins.py
  - ${code_root}/management_portal/society_service/lineage_definition_normalization.py
  - ${code_root}/management_portal/society_service/lineage_subgraph.py
  - ${code_root}/management_portal/society_service/lineage_domain.py
  - ${code_root}/management_portal/society_service/lineage_definition_contract.py
  - ${code_root}/management_portal/society_service/lineage_program.py
  - ${code_root}/management_portal/society_service/lineage_recurring_schedule.py
  - ${code_root}/tests/cases/test_lineage_generic_primitives.py
  - ${code_root}/management_portal/society_service/resident_domain.py
  - ${code_root}/management_portal/static/hatg_panel.js
  - ${code_root}/tests/cases/test_lineage_backend.py
  - ${code_root}/tests/cases/test_lineage_lock_boundaries.py
  - ${code_root}/tests/cases/test_lineage_state_domains.py
  - ${code_root}/tests/cases/test_resident_execution_protocol.py
  - ${code_root}/tests/cases/test_resident_folder_creation_contract.py
  related:
  - decision_ae422b92c6f3
  - decision_d19b9909a674
  - decision_5bf5bfbd52cb
  - decision_1b2bdf5c33d6
  - decision_5b5653c3eaa5
  - decision_4637ca4a69e2
  - decision_6591e7793116
  - decision_34158fdd4078
  - decision_4adcc1ff2f2c
  - decision_898b8911a2ff
  - decision_71aa039e3211
  - decision_d918ebebc062
  - decision_c47a13e8b962
  - decision_3e07c6d35cac
  supersedes: []
  derived_from: []
---
# Lineage 的成员集合、协作定义与协调角色

Portal 让多个长期存在的 Agent 身份围绕一项工作组成协作集合。这样的 Agent 身份叫 Resident，这个协作集合叫 Lineage。本篇规定 Lineage 里有哪些成员、成员怎样加入和离开、谁管理 Lineage、谁协调运行，以及 Lineage 的版本与保存方式。

相关规范：
- HATG 的对象、端口、连线和运行语义见 decision_4adcc1ff2f2c。
- SocietyService 的进程、锁和恢复见 decision_1b2bdf5c33d6。
- Fork 的上下文继承方式见 decision_5bf5bfbd52cb。
- Project 层 Lineage 和组织导航见 decision_3e07c6d35cac。
- 相关指令的参数见 decision_4637ca4a69e2（Lineage 定义）、decision_d19b9909a674（Resident）和 decision_6591e7793116（组织与分工）。

## 基本概念

| 概念 | 含义 | 与 Lineage 的关系 |
| --- | --- | --- |
| Resident | 能跨多次模型会话延续的 Agent 身份，ID 形如 `resident_*` | 必须且只能属于一个 Lineage；换 Lineage 不改变身份 |
| Lineage | 一组 Resident 和它们的协作定义组成的整体 | 保存成员、Leader、组件、连线、布局和当前 revision |
| HATG | Lineage 内部的协作图（人机协同 Agent 轨迹图） | 是 Lineage 的显示和操作视图，不是另一个容器 |
| 节点 | Resident 在 HATG 中的显示 | 每个成员自动有一个节点，不需要另行加入图 |
| Program 组件 | Event Source、Effect、Filter、Selector、Reducer、Loop 与 Function Block 实例等图元素 | 属于协作定义，不是成员 |
| wire | 从一个端口指向另一个端口的有向连线 | 决定工作在组件之间怎样流动 |
| leader | 当前负责协调的 Resident | 承担需要 leader 身份的协调操作 |
| capability | 某个调用者被授予的一项具体能力 | 由权限系统检查，与 Leader 绑定分离 |
| Fork | 从已有 Resident 的上下文派生一个新 Resident | 新身份带着一次性起步上下文，之后独立延续 |

HATG 页面的画布把 Resident 和 Program 组件都画成卡片。Resident 卡片显示名称、状态和端口，点开后在检查器里查看身份、连接和可编辑字段。工具栏提供：
- 新节点：新建 Resident，或按 Resident ID 把另一个 Lineage 的 Resident 迁进来。
- 轨迹分支：对选中的 Resident 执行 Fork。
- 封装选区：把选中的元素包成可复用的 Function Block。
- 批量删除和迁移选区：多选之后出现。
- 工作交接。
- 全局停止。
- 视图工具：缩放、适配、自动整理、深度重排、Program 诊断、跳转到 leader。

页面只是服务能力的一个入口。工具栏上没有的能力，也可能通过授权的指令提供。

**画布刷新保留当前团队。** 用户显式切换 HATG 到另一个 Lineage 后，导航目录或服务状态刷新继续显示当前画布所在的 Lineage；仍打开的旧 Chat 不会把画布切回旧团队，也不会把旧团队的 Resident 作为当前图的选中项。用户主动选择新的 Chat 时，按该次选择明确切换画布和对话。 通过团队或 Resident 入口打开对话时，优先使用该 Resident 的正式成员归属；其它图中的节点引用不改变归属，也不能因侧栏排序靠前而替换目标团队。迁移后的旧链接仍按当前归属解析。

## 设计点

### 成员资格

**`lineage_nodes(node_id=resident_id).lineage_id` 是 Resident 归属的唯一依据。接口中的 `resident.lineage_id` 由此读取。** 下面这些都不产生成员资格：
- 图节点的附加属性（角色、目标、连线引用、布局）；
- 相同的工作区路径；
- 同一个 Fork 家族；
- 能读取相同的 Resource；
- 在消息里提到另一个 Resident。

**每个成员都有 HATG 节点。** 不存在"属于 Lineage 但不在图上"的成员。没有连线、没有放进 Function Block、来自 Fork 或属于个人用途的 Resident 都照常显示。页面的筛选和折叠只影响显示，不改变成员集合或权限。

**创建身份和确定归属是同一个操作。** 创建 Resident 或 Fork 时，归属随身份一起提交，提交后立刻出现在该 Lineage 的 HATG 中。普通登记准备身份、成员关系、工作区和运行设置，不因此派发模型业务。Fork 的上下文、原生会话准备及首轮压缩有独立初始化前置，不能一概推迟到第一条业务消息；具体合同见 decision_5bf5bfbd52cb。

**离开 Lineage 只有两条路：迁移或删除。**
- 迁移：用 `MoveResident` 或 `MoveLineageObjects` 迁到别的 Lineage，保留身份。
- 删除：`DeleteResident`，将身份标记为已删除并移出有效成员集合；身份与历史记录保留。
- 不提供"移出 HATG 但仍属于该 Lineage"的操作。清理连线或解除角色都不会移除成员。

### Project 绑定

**正式 Lineage 必须属于一个 Project。** 每个 Lineage 的 `lineage_membership` 取以下两值之一：
- `formal`：正式 Lineage。创建时和修改后都必须有 Project，不能把 `project_id` 清空。
- `personal`：个人用途的 Lineage。可以不绑定 Project。

**有未结束任务时不能换 Project。** Lineage 还有排队、运行或正在停止的任务时，修改 Project 被拒绝。这样可以避免任务用到的工作区、Resource 和权限在中途变化。换 Project 后，Lineage 目录会搬到目标 Project 的 `knowledge_base/agents` 下。

**Project 层 Lineage 使用同一种 Lineage。** 它通过 `project_lineage_entries` 登记为 Project 入口，接口派生 `principal_hierarchy.level=project`，并在图中引用同 Project 各子 Lineage 的 leader。这些引用复用原 `resident_id`，不复制身份，也不改变被引用 Resident 的归属，因此在上层图里删除引用不会删除下层 Resident。详见 decision_3e07c6d35cac。

### 权限与 Leader

权限系统判断调用者能否执行指令；Leader 绑定说明当前由谁协调。Lineage 不保存 owner 或自动运行 Resident 身份。定时触发、完成通知和重启恢复以 Portal 服务身份运行，并保留事件的真实发起人；Effect 的业务操作继续按发起人检查权限。

**有 Resident 的 Lineage 一定有 leader。** 平台以 Leader 的名义给 owner 写自动运行失败等消息（decision_15f7df737ed5），所以这个位置不能空着。
- Leader 由明确绑定决定，算子执行者不能担任 Leader。
- Leader 位置空着、Lineage 中又有 Resident 时，服务在写入 Lineage 的同一事务中让最早创建的成员担任：新建的空 Lineage 中第一个 Resident 成为 Leader；用 `UpdateLineage` 清空 Leader 时，位置立即交给最早创建的成员。普通成员优先，Function Block 槽位 Resident（如 Family 门面、Router、steward）只在没有普通成员时担任。Project 层 Lineage 沿用自己更严格的 Leader 规则。
- 当前 leader 被删除或迁出时，服务在同一事务中从剩下的 Resident 里随机选一个接任（不选算子执行者）。
- 只有没有 Resident 的 Lineage 没有 leader；它在加入第一个 Resident 时得到 leader。
- 需要指定继任者时，先换 leader，再删除或迁出原 leader。

**任务结束时，负责分工的 Resident 要向任务提出者汇报。** 团队任务完成后，负责这次分工的 Resident 要主动向任务提出者提交最终总结，内容包括：
- 完成了什么；
- 验证结果；
- 交付或部署状态；
- 未完成或待确认的事项；
- 下一步建议。

未实施的迁移、配置或部署必须明确标出。团队内部的交付回执和收口消息不能代替这份总结。这项责任由分工负责人承担，与是否担任 Lineage leader 无关。这是对 Resident 的工作要求，Portal 不做自动检查。

### 版本

**成员和协作图共用一个 `revision`，布局单独用 `layout_revision`。** 只移动卡片不会改变 Program 的语义。

定义修改的可选 `expected_version`、首次提交比较、回滚与跨 Lineage 合并统一见 decision_d918ebebc062；本篇只规定成员与协作定义的归属。



**运行以运行快照为准。** 读取接口返回可编辑的定义。实际运行使用当前定义的编译结果及调用、Event、claim、lease 等运行身份。迟到结果提交时核对当前目标仍有效以及运行状态，失效结果不能推进 Program。

**集合没有统一的数量上限。** 成员、语义边和编排定义都没有聚合级的总数上限，但各对象仍受自己的结构、负载、队列、预算和历史限制。

## 实现说明

### Lineage 聚合保存什么

持久化结构名为 `lineage-folder-v3`，当前 `schema_version` 为 `1`。

| 部分 | 保存内容 |
| --- | --- |
| metadata | 标识、名称、Project 绑定、`lineage_membership` 和生命周期信息 |
| 成员 | Resident 的归属，是成员集合的唯一依据 |
| 协作图 | Resident 节点、Program 组件、端口关系和语义边；修改后重新诊断和编译 |
| 可复用定义 | Lineage 范围的 Function Block 定义和草稿 |
| 布局 | 共享节点坐标，用 `layout_revision` 计数；个人视口与缩放不属于此聚合（decision_4adcc1ff2f2c） |

以下内容不写进 Lineage 聚合，因此成员编辑、运行恢复和 Resident 的连续性可以各自调整：
- HATG 运行状态：Event、Effect 调用、待投递记录、claim、lease、观察游标和 Graph State，保存在 HATG 运行状态中。
- Resident 自己的数据：模型会话、消息历史和自有工作区，由各自领域保存。

### 成员生命周期

```text
创建 Resident（同时确定归属）──> 成员 ──> 自动显示为 HATG 节点
                                  ├─ Fork ──────────> 新 Resident + 归属 + 一次性起步上下文
                                  ├─ MoveResident ──> 同一身份 + 新归属
                                  ├─ MoveLineageObjects ──> 同一身份随选区迁到新归属
                                  └─ DeleteResident ──> 已删除（保留身份与历史）
```

创建和迁移先验证调用者、Project、目标可见性和并发条件，再提交成员关系。HATG 读取时直接使用完整成员集合，不需要第二次"加入"。

### 读取与可见范围

- 能读取某个 Lineage 的用户看到它的全部成员。
- 在 Project 层 Lineage 中，读者无权访问的子 Lineage 引用只保留 ID 和位置，标题显示为"无权访问的 Lineage"。
- 读取结果按读者缓存 5 秒，并用定义指纹校验。Resident 事实变化时，服务主动让缓存失效。
- 读取不创建身份、不写成员记录，也不启动执行。

### Leader 资料

协调章程由每次运行请求传入，不保存 `leader_profile`、固定角色模板或重复的权限标记。

leader 运行前读取当前成员、Program、Graph State 和待协调事实的快照。派发前服务再次核对当前有效成员、Program 与待处理运行事实。leader 的实际动作仍通过指令执行，照常接受能力、范围、目标权限和幂等检查。

### Fork 的起步上下文

Fork 固定来源 Resident 的有效上下文窗口，并为新身份准备独立的上下文与会话。模式选择、完整轮次边界、初始化状态、失败恢复与幂等入口以 decision_5bf5bfbd52cb 为唯一权威。成员关系提交不代表初始化已完成；初始化未就绪时保留原输入排队，不改写用户选择或回退为完整上下文投递。

三种来源记录分开保存，互相不能推断：
- 继承边：图上的关系；
- provenance：身份怎样产生；
- 上下文来源：第一次工作读了什么。

只创建新身份不会让来源 Resident 自动成为运行上游。删除图中的中间继承节点时，服务可以补一条"继承链重连"边，保持原继承路径连通。这条边只修复图的拓扑，不压缩任何历史，也不代表一次 Fork。

### 迁移单个 Resident：MoveResident

`MoveResident` 把一个没有被流程引用的 Resident 迁到目标 Lineage，保留身份和会话。页面"新节点"中按 Resident ID 加入已有 Resident、在侧栏跨 Lineage 移动 Resident，都走这条路径。

以下情况被拒绝，且什么都不移动：
- 被连线、组件或 Function Block 实例引用（它所在 Family 为它生成的元素除外）；
- 是算子执行者，或由 Function Block 实例创建；
- 是某个 Family 的门面、Router 或 steward：这些角色随 Family 一起存在，迁移整个 Family 或先解除；
- 是个人助手的默认 AMA：它不能离开私人 Lineage；
- 是源 Lineage 的 Leader，需要先改派。

节点关系（继承、吸收、交接和候选 Fork 边）不阻止迁移。迁移时断开这些边，不补建继承链重连边，结果的 `detached` 列出它们；Resident 自身记录的来源和上下文来源保留。

Family 成员迁出时离开它的 Family，规则见 decision_44cf587c1721 的“成员迁出”。这次迁移和 Family 名册的修改在同一个提交中完成，并与 Program 执行契约的变更同步处理，Router 不会再把新请求交给它。

`allow_empty_source=true` 允许迁走源 Lineage 里唯一的 Resident。前提是源 Lineage 没有任何流程，也没有未完成的 HATG 工作。

### 迁移选区：MoveLineageObjects

用户在画布上框选或多选 Resident、Event Source、Effect、算子和 Function Block 实例后，可以把这段协作定义迁到另一个 Lineage。

**目标列表在同一对话框内加载和重试。** 打开迁移选区时显示加载提示，读取成功前禁用目标选择与确认迁移。加载失败时保留当前选择并显示错误，用户点击“刷新预览”会重新读取目标列表。刷新成功后保留仍可用的目标；原目标已不可用时清空选择，要求用户重新选择后才能确认迁移。

**不需要先整理选区，也不需要先停下工作。** 服务自动处理跨边界的关系：
- 选区内部的连线和节点关系随对象迁移。
- 选区与外部之间的关系全部断开，包括连线、任意类型的节点关系（含继承边）、算子执行者绑定和 Function Block 槽绑定。
- 被断开的关系列在回执的 `detached` 中，每项写明 `id`、`source`、`target` 和 `type`。
- 源图不会绕过迁出的节点补建继承链重连边。

**Function Block 实例整体迁移。** 选中实例中的任一对象，等于选中整个实例；实例内部组件和它创建的 Resident 一起迁移。实例继续引用同一个 Function Block 定义 ID，目标 Lineage 新建引用关系；定义的所属 Lineage 不改变，修改定义仍从所属 Lineage 发起。没被选中的绑定 Resident 留在源 Lineage，对应的槽变为空。

**其他引用：**
- 组件配置里引用了已断开对象的，照常迁移，这些引用失效。
- 目标里已存在同 ID 的对象时，整批失败。

**协调角色：**
- 选区带走源 Leader 时，源 Lineage 按上面的接任规则随机选出新的。
- 整个图迁出时，源 Lineage 的协调角色清空，空的源 Lineage 保留。
- 空目标继承选区原有的协调角色；已有成员的目标保留自己的协调角色。

**新建目标：** 新 Lineage 的 Project 与源相同，创建者记录实际发起人。指令自动新建目标时，先按空目标验证迁移计划；后续失败且目标仍为空时删除本次新建的目标。页面单独创建的目标按页面操作保留。新建有两种方式：
- 页面：用户在迁移对话框中填写名称，页面先调用 `CreateLineage`，再调用 `MoveLineageObjects`。
- 指令：省略 `target_lineage_id` 时，服务自动新建标题为"<源标题> · 迁出"的 Lineage，然后迁移。

**进行中的工作：**
- 所选 Resident 正在做的工作照常继续，排队的输入随 Resident 迁移。
- Event Source 的游标和触发预算、Effect 最近结果、算子状态，以及内部连线上待投递的调用随对象迁移，避免重放已消费的事实。
- 断开的跨边界连线上待投递的调用随连线删除。
- 迁移前开始的运行记录留在源 Lineage。
- 外部资源和源 Lineage 的 Graph State 不复制。

**提交方式：**
1. 服务校验两边的管理权，并按 decision_d918ebebc062 的固定顺序取得执行保护和所需 Resident 锁；Lineage 定义行只在短提交事务内保护，不把整个迁移包在图锁内。
2. 在同一个事务里提交全部成员归属、两个图（各增加一次 `revision`）以及工作区搬迁意图。任一校验或写入失败，整批回滚。
3. 工作区文件在提交后由持久 outbox 搬迁。页面显示"工作区处理中"，可用 `GetMoveOperation` 查询这次迁移的阶段和终态。
4. 网络响应不确定时，用原幂等键重试；重新读取后发起的新请求使用新键。

### 删除 Resident

对本图成员，以下入口的删除语义相同：
- 画布删除；
- `DeleteResident`；
- `ApplyLineageChanges` 中的 Resident 删除；
- 调用 `DeleteResident` 的 Effect。

删除时，服务保留 `residents` 中的身份，通过 `deleted_at`、`deleted_by` 记录删除状态与操作者，并移除有效成员关系、模型会话、图中引用和自有工作区。删除提交失败时，删除状态和图保持原样。页面保留直接删除确认，不提供恢复入口；确认说明对话与运行轨迹保留。正常列表、调度与消息投递排除已删除身份，按 ID 的历史读取继续检查权限。

页面批量删除用一个幂等键、一次 `ApplyLineageChanges` 提交全部 Resident 和组件。服务在同一事务中删除它们及相关连线；选区包含 leader 时按接任规则选出新 leader。任一目标校验或提交失败，整批回滚。多条指令同时删除同一 Lineage 中的不同 Resident 时各自提交：每次写入都会重新标记的 `updated_at` 和画布坐标不算修改，一条删除不会因为另一条删除而冲突。页面收到成功结果后才清空选区；失败或响应不确定时保留选区，等权威图确认结果，不报告部分成功。

归档或删除 Lineage 时，如果搬迁 Resource 失败，聚合修改一并回滚，避免出现 Lineage 已消失而资源仍留在原处的情况。

### Leader Pulse

Leader Pulse 是内置的 Function Block，用来定期提醒 leader 检查全局。它由三部分组成：
- 周期 Event Source：`time.reached` 固定间隔，默认 900 秒，不限触发次数；
- 发送消息的 Effect：由 Event Source 经 Event wire 激活；
- Target wire：指向用户绑定的 leader Resident。

**调度策略：**
- 错过的计划合并为最近一次（`coalesce_latest`）。
- 上一轮未结束时丢弃本次触发（`drop`）。

默认任务要求 leader 检查节点状态、最近结果和工作区变化，归纳阻塞、偏航和下一步协调事项。用户可以在实例里修改周期和任务文字。

计时由服务端的 Event Source 保存和推进。多个标签页、页面隐藏或关闭浏览器都不会产生额外的计时。Leader Pulse 不修改 leader 记录。

### 从修改图到运行

1. 用户提交成员或协作图修改，服务在同一个 `revision` 下校验节点、端口、连线和 leader 条件。
2. 诊断通过后，服务编译当前 Program；定义 revision 用于失效通知，不建立图版本历史。
3. `TriggerEvent` 产生 `manual.triggered` Event；其他 Event Source 观察 Resident、Graph State、Effect 或时间。
4. 激活连线把 Event 送到 Resident、Effect、算子或循环。指向 Resident 调用端口的连线配置提供具体任务，不需要绕过图逐个发送提示词。
5. Resident 完成后形成终结结果或候选（Proposal），后续 Filter、Selector、Reducer 和 Effect 继续推进，直到没有可运行的激活或出现需要处理的失败。

`time.reached` 的 interval/calendar 计划、错过计划、忙时处理、时区与夏令时规则统一见 decision_4adcc1ff2f2c。成员与 Leader Pulse 使用这套 Event Source 合同，不另维护一套计时解释。

## 失败与恢复

- 聚合写入失败时，成员和图的 `revision` 保持原值。
- 读取投影失败时，权威定义已经提交，页面重新加载即可。
- 运行进程失败时，服务用持久化的 Event、claim、lease、终结结果和当前有效目标对账。Resident 身份和会话不依赖浏览器内存恢复。

## 扩展约束

1. 新增成员相关的关系时，先说明它改变的是正式成员资格、只是一条语义边，还是只记录来源。
2. 新增协调角色时，分别定义所需权限、Leader 条件、快照要求和失败接替规则。
3. 新增页面控件时，复用已有的指令语义，不让画布操作和服务接口形成两套身份生命周期。


页面在「Lineage 设置」中提供明确的 Leader 选择；Lineage 有 Resident 时选项中没有“未指定”，只有空 Lineage 显示“未指定”。新建普通 Lineage 时可以不指定 Leader，加入的第一个 Resident 自动担任。保存 Leader 通过 UpdateLineage 写入唯一的 leader_resident_id，刷新后保持，保存本身不触发消息或工作。删除已任命 Leader 时沿用随机接任规则。
