---
kb_id: decision_0f57656a2acd
type: decision
project: AI员工
created_at: '2026-08-05'
version:
  number: 56
  created_at: '2026-08-05'
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
  - ${code_root}/management_portal/society_service/resident_registration_domain.py
  - ${code_root}/management_portal/society_service/resident_storage.py
  - ${code_root}/management_portal/society_service/resident_lifecycle_domain.py
  - ${code_root}/management_portal/society_service/resident_resource_projection.py
  - ${code_root}/management_portal/society_service/resident_workspace_storage.py
  - ${code_root}/management_portal/society_service/state_store.py
  - ${code_root}/tests/cases/test_resident_agent_lifecycle.py
  - ${code_root}/tests/cases/test_resident_deletion_record.py
  - ${code_root}/management_portal/society_service/resident_hatg_credentials.py
  - ${code_root}/management_portal/permission_system/__init__.py
  - ${code_root}/management_portal/society_service/isa/residents.py
  - ${code_root}/management_portal/society_service/lineage_subgraph.py
  - ${code_root}/management_portal/society_service/resident_record_domain.py
  - ${code_root}/management_portal/society_service/resident_workspace_domain.py
  - ${code_root}/management_portal/static/portal_resident_discovery_service.js
  - ${code_root}/management_portal/society_service/resident_catalog.py
  - ${code_root}/management_portal/society_service/resident_lineage.py
  - ${code_root}/tests/cases/test_resident_folder_creation_contract.py
  - ${code_root}/tests/cases/test_resident_store.py
  - ${code_root}/tests/cases/test_lineage_workbench_membership.py
  related:
  - decision_b820faf42218
  - decision_600ae27b0538
  - decision_d19b9909a674
  - decision_c47a13e8b962
  - decision_5bf5bfbd52cb
  - decision_c524c775e0be
  - decision_741b04821171
  - decision_d918ebebc062
  - decision_6fc653d55c79
  - decision_34158fdd4078
  - decision_b1ab45c4b3ec
  - decision_3e07c6d35cac
  supersedes: []
  derived_from: []
---
# Resident 稳定身份、成员归属与字段边界

Resident 是 Portal 中可以被反复找到、继续对话和重新运行的 Agent。本篇说明一个 Resident 的身份由什么决定、它怎样属于一个 Lineage、换了模型会话或工作目录之后为什么仍是同一个 Resident，以及创建、打开、修改和删除 Resident 时身份与成员关系怎样变化。

消息怎样投递和执行见 decision_c524c775e0be 与 decision_edc470e09f5a；Fork 的上下文派生见 decision_5bf5bfbd52cb；Lineage 聚合保存什么见 decision_6fc653d55c79；五层组织见 decision_3e07c6d35cac。

## 基本概念

| 名称 | 含义 |
| --- | --- |
| Resident | Portal 登记的长期 Agent，用不可变的 `resident_<12 位十六进制>` ID 标识。系统中只有一种 Resident，没有类型字段。 |
| Lineage | 一组 Resident 及其协作关系组成的对象，用 `lineage_<12 位十六进制>` ID 标识。 |
| 成员关系（membership） | Resident 属于哪个 Lineage。每个 Resident 恰好属于一个 Lineage。 |
| 来源关系 | Resident 从哪个 Resident、以什么方式派生而来，例如 Fork。它与成员关系分开记录。 |
| Conversation | Portal 为 Resident 保存的对话：消息、每条输入的处理状态和等待队列。它随 Resident 身份一直存在。 |
| 模型会话（Provider Session） | 某个 Provider（Codex、Claude Code、Kimi Code 等）当前使用的会话。它可以被重启或替换，不代表 Resident 身份。 |
| 工作区 | Resident 读写任务文件的目录，见 decision_8838269736d3。 |
| Leader | Lineage 中负责协调的 Resident。 |
| 管理 Resident | Resident 不保存所有者列表。谁能管理它由权限系统判断（领域代码 `_can_manage`，返回字段 `permissions.can_manage`），与 Leader 绑定分离。 |

这些对象的关系如下：

```text
用户页面 / 业务模块 / 外部消息适配器
                 │ 投递消息
                 ▼
        Resident 的 Conversation
                 │ 安排一次执行
                 ▼
        当前的模型会话（可替换）

Resident ──成员关系──> Lineage
Resident ──来源关系──> 来源 Resident（仅派生出的 Resident 有）
Resident ──默认启动目录──> 自有工作区
```

## 设计点

### Resident ID 是连续性的唯一依据

- **Resident 创建时分配一个 `resident_*` ID，之后不再改变。** 名称、Provider、工作目录、模型会话和画布位置都可以变化；凡是“继续同一个 Agent”的功能都以 Resident ID 为准。

### 每个 Resident 只属于一个 Lineage

- **Resident 记录中的 `lineage_id` 是它唯一的成员关系。** 它决定 Resident 出现在哪个 Lineage 中。一个 Resident 不会同时属于两个 Lineage；迁到另一个 Lineage 使用 `MoveResident` 或 `MoveLineageObjects`（decision_6591e7793116）。
- **每个登记的 Resident 都是所在 Lineage 的正式成员。** 个人入口（decision_bd5217d08c09）、资源讨论和定时工作只是使用方式，都由同一种 Resident 承担。成员归属只由 `lineage_id` 表达。Lineage 定义自身保留 `lineage_membership` 的 `formal` / `personal` 分类，用于区分普通与个人 Lineage。

### 成员关系与来源关系分开

- **Lineage 的成员列表只说明“谁在这里”。** `source_resident_id` 保存直接派生父节点，`context_source_resident_id` 保存本次上下文材料的来源。跨 Lineage 使用其他 Resident 的上下文时，两者可以不同；来源消息、方式与时间由来源领域保存。Lineage 中的继承边表现这些派生关系，成员归属仍由 `lineage_id` 决定。删除来源 Resident 后，保留子节点的来源 ID。

### 重启和清空保留 Resident，Fork 创建新 Resident

- **重启会话、清空上下文都保留同一个 Resident。** Resident ID、Conversation、工作区和成员关系不变，只替换或重置模型会话（decision_c47a13e8b962）。
- **Fork 创建一个新的普通 Resident。** 新 Resident 有自己的 ID，用来源关系连接来源 Resident。Family 门面（decision_44cf587c1721）不能被 Fork，需要 Fork 具体的成员。Fork 不是一种 Resident 类型或状态：Resident 记录保存时拒绝 `fork_id`、`fork_type`、`fork_status`、`is_fork` 等字段，也拒绝用 `budded` 这类状态值表示 Fork。

### Resident 的展示与访问

Lineages 导航展示 Project 和 Lineage，卡片打开其 Leader 对话与画布；导航不展开 Resident 成员列表。Resident 在画布、搜索及对话入口的展示由各入口的查询与投影负责，实际访问由权限系统和领域规则判断，Resident 与 Lineage 都不保存可见性字段。

### 浏览器中的身份读取

<!-- wall: {"key": "b2a699fb82632958"} -->
- **身份失效通知让已打开的页面重新读取 Session 绑定。** 页面收到某个 Resident 的身份失效通知后重新读取它的身份投影，更新 Session 绑定，Resident ID 保持不变；重新读取失败时继续使用已有的有效身份。
<!-- wall: {"key": "3751284b5092f9e5"} -->
- **身份读取合并并发请求，只采用最新一代的结果。** 同一 Resident 同时发起的身份读取共用一次请求；读取期间身份失效时，晚到的旧响应被丢弃并重新读取；调用方拿到副本，修改它不影响缓存；Resident ID 与请求目标不符的响应被拒绝。

### 工作目录不决定身份

- **系统不根据当前目录推断正在工作的是哪个 Resident。** Resident 与 Lineage 的身份来自 Portal 为每一轮注入的运行身份，或命令行客户端的显式参数。

### Project 层 Lineage 可以引用子 Lineage 的 Leader

- **Project 层 Lineage 由 Project Resident 担任 Leader，并显示同一 Project 中各子 Lineage 的 Leader。** 这些显示只是引用：被引用的 Resident 仍属于原来的 Lineage，没有第二份身份、Conversation 或工作区。从 Project 层 Lineage 不能删除被引用的 Resident，需要回到它所属的 Lineage 操作。引用随子 Lineage、Leader 和 Project 的变化在同一事务中维护。

## Resident 记录与字段归属

数据库中的 Resident 行不保存 `schema_name` 或 `folder_schema`。当前格式由代码和数据库迁移共同确定；接口需要 `schema`、`folder_schema` 时返回当前常量，写回时忽略这些投影字段。Resident 业务读写不按逐行格式值选择分支；实际文件格式校验仍归文件解析器负责。

对外 Resident 记录的 API 结构保持 `tfi.resident.v6`，Resident Folder 格式保持 `lineage-folder-v3`。LineageData 的当前数据库契约统一见 decision_71aa039e3211，不在身份篇另存结构版本副本。API 将各领域的明确字段和派生结果组合成一条记录；持久化按字段归属写入对应表。`resident_storage.py` 定义允许字段、列名与类型，未知字段或类型错误在写入前拒绝。

| 事实与字段 | 权威位置及负责领域 |
| --- | --- |
| `resident_id`、`title`、`description`、`title_origin`、创建与修改审计字段 | `residents`；Resident 身份与生命周期领域。ID 不变。 |
| `lineage_id` | `lineage_nodes(node_id=resident_id)`；唯一成员归属，Resident API 按 ID 读取投影。跨图画布使用 `lineage_node_references`。 |
| `deleted_at`、`deleted_by` | `residents`；Resident 生命周期领域。`deleted_at` 为空表示正常，有值表示已删除；运行状态与删除状态分别管理，不另存第二份删除状态。 |
| `historical_project_id` | `residents`；删除领域只保留删除时有证据的 Project ID，使原 Lineage 清理后的历史仍能关联 Project；正常 `project_id` 由当前 Lineage 派生。 |
| `backend` | `residents` 的身份列；Provider 配置和执行读取同一份 Backend 身份。 |
| `principal_hierarchy`、`personal_owner_id` | `residents` 的明确列；组织身份及个人 AMA 归属。个人入口用 `personal_owner_id` 保存明确关联。权限判定由权限领域负责。 |
| `backend_route_id`、`execution_settings`、`harness_config`、`quota_reserve_override` | `resident_execution_configuration`；配置领域。所有后端通过同一配置接口读写；账号或模型 API 路由使用 `backend_route_id`，模型只存于 `execution_settings.model`。业务提示上下文 `prompt_context` 和后端专用参数 `backend_options` 是 execution_settings 内的有界结构；未知字段和不支持的能力拒绝，见 decision_8b2fa224616b。 |
| source_resident_id、context_source_resident_id、forked_from_at、fork_mode、source_message_id | resident_derivations；直接来源、上下文来源及初始化方式的事实。 |
| Fork 冻结上下文及继承窗口 | AgentData；按来源轨迹和消息引用保存，不建立第二份 resident_initializations 容器。 |
| registration_state、intake_open、intake_transition_id | residents；登记阶段与人工输入开关由各自 owner 显式提交。 |
| session_state、会话重启、工具刷新、上下文重置或压缩进度 | resident_sessions；会话连续性领域。 |
| 最近错误、用量、模型窗口与交互时间 | resident_observation_cache；可更新的观察缓存，不控制排队或运行。 |
| work_recovery | resident_recovery；当前恢复工作。 |
| 当前输入/执行关联、提交准备、取消、事件消费及确认进度 | resident_active_turns；未完成轮次工作；等待队列属于 Mailbox，长期内容属于 AgentData。 |
| `workspace_bindings` | `resident_workspace_bindings`；Workspace 领域。关系只保存 `resident_id`、`workspace_id`、`purpose`；路径由 Workspace 资源解析。自有归属单独保存在 `residents.owned_workspace_id`，默认 cwd 从自有 Workspace 推导。 |
| Watchboard 页面投影 | 安装事实来自所属 Lineage 的面板标识组件，清单、配置和状态读取 Resident 自有工作区；Watchboard 领域按需投影，不写入 Resident 记录。Fork 复制独立文件，目标显式安装自己的组件。 |
| `family` | `families` 与 `family_residents` 的只读角色投影；Family 仍由 Function Block 领域创建、调整和解散。内部角色与作为外层成员的归属可同时存在。 |
| `project_id`、`lineage_project_id`、`lineage_title` | 从所属 Lineage 读取；Project / Lineage 领域负责归属与名称。HATG `UpdateResident` 只接受 `resident_id` 与 `title`；Project 归属通过 `UpdateLineage` 更新。 |
| `workspace_path`、`workspace_root`、`owned_workspace`、`default_cwd`、`workspace_paths`、`workspace_roots` | 从 Lineage 目录、自有引用、额外绑定与 Workspace 资源派生。读取投影不会创建绑定；绑定写入走显式接口并检查目录存在。 |
| Provider Session、Binding、Execution、Agent、trace 与消息 Segment | 执行领域与 AgentData；按 Resident ID 关联。Provider Session 可以替换，执行历史和轨迹独立于 Resident 配置。 |

Resident 不保存通用 `extensions` 字段。

可选字段使用对应列的空值表达未提供的事实。读取按同一数据库 snapshot 批量装配 Resident 字段和 Workspace Binding，路径解析使用该次读取的资源事实。每次读取都能从明确字段重建记录，不依赖任意附加键或目录投影恢复身份。

Resident 和 Lineage 都不保存所有者列表：`lineage.residents.owner_ids_json` 列于 2026-10-17 删除，注册、Fork 和派生 Resident 不再复制任何 owner 字段。谁能管理 Resident 只由权限系统回答；关闭时任何有效登录的 User 和 Resident 都能管理，个人 AMA 除外。开启时按当前模板判定，其中 Resident 所在的 Lineage 和 Project 决定它在 P、R 两棵树上的位置（decision_b820faf42218、decision_600ae27b0538）。

### 登记必须建立同 ID Agent 与主 trace

每个正式登记的 Resident 都有同 ID 的 Agent。生命周期领域调用 `AgentDataStore.ensure_agent(resident_id, sequence_kind="agent_trace", parent_agent_id=None)`，建立或检查它的主 trace Sequence。普通登记、加入已有 Lineage、Fork、个人入口和 Project 层 Resident 都在返回成功前完成这个保证。已有登记的确认与恢复也重新检查；执行提交、调度及同步后端入口再次检查，防止遗漏进入模型执行。

Agent 已存在时检查 ID、trace 用途与父级一致，重复调用保留同一主 trace。Portal Fork 的直接父节点记录在 `source_resident_id`；`parent_agent_id` 专用于 Provider 子 Agent 的执行关系。Fork 是新的普通 Resident，也拥有自己的主 trace。冻结上下文与轨迹前缀按 AgentData 的真实引用规则处理（decision_b1ab45c4b3ec）。

ResourceService 确认登记前也检查 Agent。确认、建 Agent 或建 trace 失败时，登记不能进入成功的 ready 状态；已提交身份保持待确认，调用者按同一 ID 或幂等操作重试，完成剩余步骤。普通登记只建立身份、归属、工作区和主 trace，模型会话在首次执行时建立。


## Lineage 中的成员节点

- **每个成员自动对应 Lineage 中的一个节点。** 创建、Fork 或迁入 Resident 时，它随成员关系出现在 Lineage 中，不需要另外“加入”；没有连线的成员也显示为普通节点。
- **身份和节点在同一个事务中提交。** 创建、删除和迁移 Resident 时，Resident 记录、成员节点和相关引用一起提交或一起回滚；页面读取时不临时修补成员列表。
- **节点只附加协作信息。** 节点记录名称、位置和角色等协作属性，不复制整份 Resident 记录。运行状态来自执行系统，访问权限来自权限系统。

## 打开一个 Resident

用户在列表、聊天历史或 Lineage 画布中选中一个 Resident 时，各入口使用同一个解析过程：

1. 入口把选中的 `resident_id` 交给浏览器中共享的 Resident 服务。
2. 服务按 ID 读取 Resident 记录，检查当前用户能否读取，返回 `resident_id` 与当前的 `lineage_id`。
3. 浏览器核对返回的 `resident_id` 与所选一致，两个 ID 格式正确。
4. 入口用这两个 ID 打开对话、详情或所属 Lineage。

同一个解析器对同一 Resident 同时发起的请求合并为一次，请求结束后释放，下次打开时重新读取当前归属。读取被拒绝、记录不存在或返回结果不一致时，界面保留原来的选择并显示错误。

## 创建、读取、修改与删除

### 创建

1. 调用者提供名称、Provider、Project、Lineage 和初始配置（`CreateResident`，decision_d19b9909a674）。
2. SocietyService 分配 Resident ID，检查目标 Lineage 和调用者权限，准备 Resident Folder 及严格 Workspace Binding。
3. 按字段归属生成 v6 API 记录；Resident 身份、成员节点、ResourceService 发布工作和生命周期准备在事务中提交。普通新节点不自动产生连线。
4. 建立或检查同 ID Agent 与主 trace，并完成 Resident、自有 Workspace 的资源登记确认；全部完成后才返回成功。
5. 变化通知让页面按当前身份和成员关系刷新。提交后的确认失败保留同一身份，重试补完确认；准备阶段失败按生命周期规则清理未发布产物。

### 读取

Resident 目录读取并检查记录，SocietyService 再按当前调用者附加可见性、可用操作和运行状态。页面看到的 Resident 卡片和详情由这些信息组合而成，不另存一份身份副本。

批量列出 Resident 或 Lineage 时，服务只展示在 SocietyService 与 ResourceService 中都存在的对象；一条残留记录不会让整个列表失败。实际执行操作时，权限检查对不存在的目标仍返回错误。

### 修改

修改在对应 Resident 和 Lineage 的锁以及数据库事务中进行：服务重新读取最新记录，只替换本次操作负责的字段，再写回并更新 `updated_at`。名称、说明、Project、成员关系、Provider、工作区绑定等“身份相关字段”变化时，服务在同一事务中登记一条同步到 ResourceService 的记录，并发出 `resident_changed`（`scope=identity`）通知；只改运行状态时通知 `scope=runtime`。

ResourceService 保存的协作描述按当前领域事实投影关系：Resident 的 `lineage`、`source`、`workspace` 关系，以及 Lineage 的 `member` 关系，每次发布都替换该类关系的全部当前值。迁移移除旧 home Lineage，解除额外 Workspace Binding 移除对应关系，成员迁出后不再保留原 Lineage 的 member 关系。领域以外的关系和描述补充字段继续保留。

同一规范事件重试时，已独立编辑的描述标题和补充字段保持现值，领域拥有的关系仍按锁定的当前规范记录重建。已有投影回执不使累积的旧关系成为当前事实。待发送任务完成描述与授权图同步后才确认；发布失败保留同一任务供重试，不重新迁移 Resident、不创建新身份，也不放宽 Resident 唯一 home Lineage 的校验。

### 删除

- **删除 Resident 使用 `DeleteResident`；一次删除多个 Resident 和组件使用 `ApplyLineageChanges`。** 两者对 Resident 的删除语义相同：Resident 删除标记、活动成员节点、相关连线和引用在一个事务中提交，自有工作区的删除和模型会话的释放作为生命周期步骤登记在同一事务中，之后由后台完成，中断后可以继续。任一目标不能删除时，整批保持原样。
- **Resident 还有排队或执行中的消息、或还有未完成的 Fork 时不能删除。** 报错“Resident … 仍有消息任务或 Fork 创建任务正在排队/处理，完成或停止后再删除”。
- **删除时 Mailbox 一起关闭。** 等待随下一条请求送达的 info，以及没有任何工作在处理、只留在 Mailbox 的请求（例如早期版本队列丢下的请求，decision_15f7df737ed5），不算排队中的工作，不阻止删除；删除后也没有人能再处理或撤回它们。所有删除路径都经过 `ResidentCatalog.remove`，它在写入删除标记的同一事务中、写入之前，把这个 Resident 仍为 `queued` 或 `running` 的输入结束为 `cancelled`（错误码 `resident_deleted`，说明“Resident 已删除，这条消息不会被处理”），并删除它在 `resident_mailbox` 中的全部行（decision_15f7df737ed5）。重复删除时 Mailbox 已经关闭。10-10 之前删除留下的 33 行已用一次性脚本按同样语义处理。
- **删除 Leader 时自动选出新的 Leader。** 服务从该 Lineage 剩下的 Resident 中随机选一个（担任算子执行者的 Resident 除外）作为新 Leader；`DeleteResident` 结果中返回新的 Leader ID，`ApplyLineageChanges` 调用者通过 `GetLineage` 读取当前 Leader；没有剩下的 Resident 时，Lineage 暂时没有 Leader。需要指定继任者时，先用 `UpdateLineage` 更换 Leader，再删除原 Leader。
- **Family 门面、steward 与 Router 不能单独删除。** Resident 删除领域统一检查内置角色；完整实例一并删除时按实例规则处理（decision_44cf587c1721）。
- **删除整个 Lineage 使用 `DeleteLineage`**（decision_4637ca4a69e2）。
- **删除只改变同一份 Resident 身份的状态。** `residents` 永久保留原 ID、创建事实、名称、成员归属、Fork 来源及对应领域记录；第一次删除在该行填入 `deleted_at` 与 `deleted_by`。不再有另一张身份删除表或整份删除快照。重复删除保持首次删除事实，仍检查当前调用者权限。
- **正式删除保留同 ID Agent、主 trace Sequence、Segments 与 Execution。** 删除前确保 Agent 存在。活动 graph 节点、Family 路由和调度退出；清理工作区和执行资源不删除 AgentData 历史（decision_b1ab45c4b3ec）。删除整个 Lineage 也不能级联删除已保留的 Resident 身份。
- **正常读取统一排除已删除 Resident。** 普通目录、搜索、成员列表、初始化恢复、调度及消息投递不把已删除对象视为可用。需要已删除 Resident 的历史时，使用显式历史读取接口按同一 ID 查找身份和对话、Execution 与 trace；读取仍检查权限，身份保留不授予访问权。原 Lineage 或工作目录已清理时，历史身份读取仍可用。

生命周期为“正常 → 已删除”。系统不提供回收站或恢复已删除 Resident 的接口。删除确认仍用于防止误操作。

生命周期清理步骤只携带目标身份、自有 Workspace 关系和释放后端所需的有限字段。Resident 状态修改、资源 tombstone 投影请求和清理意图在同一事务登记。ResourceService 确认已删除投影后才返回成功；确认暂时失败时返回 `instruction.projection_pending`，删除状态已经提交，按同一操作重试补完剩余同步。重复删除确认既有 tombstone，不覆盖首次删除审计，不再分配清理任务，也不创建新身份或删除 AgentData 历史。

## 权限

- **页面操作按当前登录用户检查权限。** 读取需要对 Resident 所在对象的读取权限；删除 Resident、修改名称和运行设置、重启会话、绑定工作区、迁移等需要更高等级（按当前模板的指令映射以及调用点业务规则）。具体规则由权限系统决定（decision_b820faf42218）。
- **Resident 调用 HATG ISA 时，以它自己的身份接受同样的检查。** Resident 在工作中通过 Harness 工具调用，身份由执行层注入；用命令行客户端时，客户端使用 Portal 为该 Resident 签发的 Bearer 令牌。令牌只证明“我是这个 Resident”，能做什么由权限系统按 Resident 自己的权限判断，不继承发消息者的权限。Resident 加入 Lineage 时，Portal 为它准备令牌，权限系统把它挂到该 Lineage 下，默认模板因此给它对该 Lineage 的 contributor 和对所属 Project 的 viewer。
- **工作目录、成员关系和页面上隐藏的按钮都不构成授权。**

## 不变量

1. `resident_id` 一旦登记，不因名称、Provider、模型会话、上下文或工作区变化而改变。
2. 一个 Resident 只有一个 `lineage_id`；成员关系不表达来源。
3. Conversation、输入状态和运行记录都以 Resident ID 关联。
4. 重启和清空上下文不创建 Resident；Fork 总是创建新的普通 Resident，Fork 不成为身份类型或状态。
5. 权限、模型会话和页面投影都不是身份的权威数据。
6. Composite Channel 不自动成为 Resident 或权限主体。
7. 普通 Lineage 的 Resident 节点与正常成员集合一致；Project 层 Lineage 额外包含对子 Lineage Leader 的引用，这些引用不能在上层删除。
8. 登记与恢复成功前完成同 ID Agent 和主 trace 检查；执行入口再次检查。
9. 删除永久保留同一份 Resident 身份和 AgentData，首次写入 `deleted_at` 与 `deleted_by`；活动成员、调度和投递排除已删除身份，没有恢复入口。

## 字段命名

- 创建 Resident 与 Project 时只使用 snake_case 字段：`project_id`、`project_mode`、`new_project`（其中新 Project 只使用 `id`、`type` 等字段）。
- 直接派生来源使用 `source_resident_id`，上下文材料来源使用 `context_source_resident_id`；来源 Codex 轮次使用 `source_codex_last_turn_id`。工作区关系使用 `workspace_bindings`，路径由关系与资源派生。
- 对话消息只用 `message_id` 标识。camelCase 字段、`forked_from_resident_id`、旧的轮次与工作区字段、消息的 `id` 字段都不被接受。
- 修改名称只接受 `resident_id` 与 `title`。

## 删除后的资源投影

Resident 删除后，资源目录保留其描述符并标记为已删除。若删除与最后一次活动投影同秒发生，且当前描述符与资源目录记录的最后活动来源完全一致，认证的 SocietyService 删除投影可以只更新生命周期；已独立修改的描述符仍拒绝同秒删除投影。被删除 Resident 的投影任务成功处理后应从待处理队列移除。

### 实现来源与入口定位

Resident 的公开聚合入口为 `resident_domain.py`，该文件重导出领域函数。注册实现定位到 `resident_registration_domain.py#register_resident`，生命周期推进位于 `resident_lifecycle_domain.py`，删除身份位于 `resident_workspace_domain.py#delete_resident_identities`。上方实现来源列具体实现文件，聚合入口在此保留为调用定位。

格式名与版本须精确匹配当前规范值，校验规则见 decision_86aed2f0e8c7；本篇同时校验各字段和结构版本，不接受历史前缀或其他版本。
