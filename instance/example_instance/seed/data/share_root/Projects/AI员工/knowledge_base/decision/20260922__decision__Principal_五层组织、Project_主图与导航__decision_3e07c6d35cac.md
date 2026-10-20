---
kb_id: decision_3e07c6d35cac
type: decision
project: AI员工
created_at: '2026-09-22'
version:
  number: 37
  created_at: '2026-09-22'
  created_by:
    user: ''
    agent: resident_7d35b0a2ea1e
  updated_at: '2026-10-19T01:17:56Z'
  updated_by:
    user: tfi-exported-user
    agent: resident_dbfb834a0539
importance: 8
ai_generated: true
links:
  source:
  - ${code_root}/management_portal/static/portal_project_creation.js
  - ${code_root}/management_portal/static/lineage_workbench.js
  - ${code_root}/management_portal/static/principal_navigation.js
  - ${code_root}/management_portal/society_service/state_store.py
  - ${code_root}/management_portal/society_service/principal_hierarchy_domain.py
  - ${code_root}/management_portal/society_service/principal_hierarchy.py
  related:
  - decision_a6bebf4ac283
  - decision_4648f7fa3295
  - decision_3fe3cca8fd95
  - decision_b820faf42218
  - decision_600ae27b0538
  - decision_6591e7793116
  - decision_0f57656a2acd
  - decision_6fc653d55c79
  - decision_8fe87d812a4c
  - decision_bd5217d08c09
  - decision_44cf587c1721
  supersedes: []
  derived_from: []
metaness: 5
---

# Principal 五层组织、Project 层 Lineage 与导航

Portal 中能被授权、能在消息和记录上署名的主体称为 **Principal**。Principal 只有两种实体：人类用户（User）和 Resident（可以跨多次模型会话持续存在的 Agent 身份，decision_0f57656a2acd）。为了让许多用户和 Resident 长期协作，Portal 把它们放进五层组织：Portal Admin、User、Project、Lineage、Resident。组织层级说明“谁负责协调什么”，用来决定默认分类和页面导航；它本身不授予任何权限，实际授权由权限系统判定（decision_b820faf42218）。

本 Design 说明五层组织怎样落在 Lineage 上、Resident 怎样分类，以及 Portal 左侧导航怎样按这套组织展示 Project 和 Lineage。

## 基本概念

- **Lineage**：一组 Resident 及其协作关系组成的对象，包含成员、Leader、组件、连线和设置（decision_6fc653d55c79）。页面上显示 Lineage 的画布称为 HATG。每个 Resident 只属于一个 Lineage。
- **Leader**：Lineage 中负责协调的那个 Resident，记录在 Lineage 的 `leader_resident_id` 中。
- **Lineage 的组织层级 `principal_hierarchy`**：读取接口依据所属 Project、`project_lineage_entries` 和个人 Lineage 关系派生的视图，包含 `level` 和 `scope_id` 两项，不另存一份 JSON。`level` 取以下值之一：
  - `lineage`：普通 Lineage，`scope_id` 是所属 Project ID；
  - `project`：Project 层 Lineage，负责协调整个 Project，`scope_id` 是 Project ID；
  - `user`：User 层 Lineage，用于个人承载，`scope_id` 为这个个人 Lineage 的 ID。普通正式 Lineage 不允许单独改成 User 层。
- **Project 层 Lineage**：`level=project` 的 Lineage。它和普通 Lineage 是同一种对象，使用相同的 ID、数据、文件夹、指令和生命周期；每个 Project 最多有一个。它的 Leader 称为 Project Resident。
- **结构引用**：Project 层 Lineage 的画布中，除了自己的成员，还会显示同一 Project 下各个普通 Lineage 的 Leader。这些节点只是引用，节点 ID 就是原 Resident ID；被引用的 Resident 仍然只属于它自己的 Lineage，对话、模型会话和工作区都只有一份。
- **Resident 分类**：每个 Resident 记录上的 `principal_hierarchy` 字段，包含三项：`level`（`user`、`project`、`lineage` 或 `resident`）、`category`（用途标签，非空文本，最长 120 字符）和 `scope_ids`（服务对象的 ID 列表，例如所服务的 Project）。分类用于导航和审计，不授予权限。

五层组织与承担者：

| 层级 | 承担者 | 负责什么 |
| --- | --- | --- |
| Portal Admin | 管理员名单中的 User，可以有多个 | 管理整个 Portal |
| User | User，以及 `level=user` 的 Resident（例如个人 Ask Me Anything、Which Resident 推荐等跨项目服务） | 使用和管理多个 Project |
| Project | Project 层 Lineage 的 Leader | 协调本 Project 下各个 Lineage 的 Leader |
| Lineage | 普通 Lineage 的 Leader | 协调本 Lineage 的成员 |
| Resident | 普通成员 | 完成分到的工作 |

## 设计点

### 组织怎样落在 Lineage 上

- **Project 层 Lineage 自动显示同一 Project 下各普通 Lineage 的 Leader。** 系统在每次读取和每次成员变化时按当前数据计算这些结构引用：同一 Project、层级为 `lineage`、不是个人承载图的 Lineage，只要有 Leader，就把 Leader 作为节点放进 Project 层画布；子 Lineage 更换 Leader 后，引用在同一个数据库事务中跟着更新。
- **结构引用不复制身份，也不能用来删除身份。** 在 Project 层画布上删除引用节点不会删除子 Lineage 的 Leader；这个 Resident 的删除和迁移只能在它自己的 Lineage 中进行。
- **被 Project 层连线或组件引用的 Leader 离任时，这次修改失败。** 子 Lineage 更换 Leader 或删除 Leader 后，如果旧 Leader 仍被 Project 层 Lineage 的连线或组件配置引用，整次修改回滚并报错“Project graph references a departing Lineage leader”；调用者先解除这些引用再修改。
- **每个 Project 最多有一个 Project 层 Lineage。** 创建 Project 时，服务内部调用幂等创建函数建好这一层 Lineage 和 Leader；已有时复用原身份。创建过程不投递消息，也不调用模型。项目登记成功但主体创建失败时，指令保留恢复回执；用同一幂等键重试只补齐主体，不重复创建项目目录。
- **Project 层 Lineage 始终有自己的 Leader。** 它只有一个 Resident 时，这个 Resident 就是 Leader。Leader 离开（删除或迁出）时，继任者只从这个 Lineage 自己的成员中随机选出；要移除的是最后一个成员时，删除或迁移被拒绝，需要先加入其他 Resident，或用 `DeleteProject` 删除整个 Project。
- **Project 层 Lineage 不能改成其他层级，也不能换到其他 Project。** 把一个 Lineage 设为 `project` 层时，它必须已经有自己的 Leader。
- **还在协调子 Lineage 的 Project 层 Lineage 不能单独删除。** `DeleteLineage` 发现它仍有子 Lineage Leader 时拒绝；删除整个 Project 时一并删除（decision_6591e7793116）。
- **普通 Lineage 必须属于一个 Project。** 创建 Lineage 和修改 Lineage 设置时必须给出非空的 `project_id`；页面上没有可选项目时，表单显示“项目必填”并保持无法提交。
- **Portal 现有功能的 Lineage 是 AI员工 的普通 Lineage。** Portal-Manager 以及各页面的 Knowledge Base、Paper、Experiment、Article、Repo、Skill、Project Lineage 都是 `level=lineage`、`scope_id=AI员工`，显示在 AI员工 项目下；用户通过 AI员工 的项目角色使用这些功能。User 层 Lineage 用于每个用户的 Ask Me Anything，并建成私人 Family 作为个人助手，默认标题「Ask Me Anything | My Lineage」（decision_44cf587c1721）；Portal System 保持普通正式 Lineage。

### Resident 怎样分类

- **Resident 第一次保存时自动得到分类。** 规则按顺序判断：
  1. 所在 Lineage 是个人承载图：`level=user`，`category=personal_ama`；
  2. 所在 Lineage 是 User 层：`level=user`，`category=user_scope_member`；
  3. 是 Project 层 Lineage 的 Leader：`level=project`，`category=project_leader`；
  4. 是普通 Lineage 的 Leader：`level=lineage`，`category=lineage_leader`；
  5. 其他：`level=resident`，`category=lineage_member`。

  `scope_ids` 默认填所在 Lineage 的 Project ID。
- **下列用途的 Resident 属于 User 层：** 个人 AMA（`personal_ama`，个人 Lineage 中的私人 Family 门面、Router、steward 和成员同属此类）、Resident 推荐（`resident_routing`）、后台模型与后台管理（`background_model`、`background_manager`）和共享服务（`shared_service`）。资源对话（`resource_conversation`、`paper_conversation`）和平台能力（`platform_capability`）住在对应页面的 AI员工 Lineage 中，是普通成员（`level=resident`），分类保留用途和所服务的用户。
- **`project` 和 `lineage` 层级跟随 Leader 绑定。** 只有当前担任对应 Leader 的 Resident 才能被设为这两个层级；更换 Leader 后，新旧 Leader 的有效层级按新的绑定重新计算。
- **层级和分类在创建时确定，之后没有改写入口。** Lineage 的层级由创建它的流程写入：CreateProject 建立 Project 层 Lineage，个人 AMA 建立 User 层 Lineage，其余 Lineage 为 `lineage` 层；Resident 的分类由登记它的入口按上面的规则给出。HATG ISA 不提供事后修改层级或分类的指令，组织层级也不在权限树中，权限由 PAR 权限系统单独决定（decision_b820faf42218）。
- **只有 Resident 有分类。** 不以 Resident 身份运行的模型调用（例如 `CompleteChat` 直接调用模型接口）没有分类。

### 组织层级不授予权限

- **能否读取或操作一个对象，只由权限系统决定。** 出现在 Project 层画布上、担任 Leader、被收藏，都不带来任何权限。Project 层画布中当前用户无权读取的引用节点显示为灰色占位，标题为“无权访问的 Lineage”，不含私有标题和运行内容，也不能打开或操作。
- **权限树按对象归属分层，与组织层级无关。** 默认模板中 Resident 在 P 树和 R 树上都挂在所在 Lineage 之下，Lineage 挂在所属 Project 之下：Lineage 内的 Resident 对该 Lineage 有 contributor，Project 内的 Resident 对该 Project 有 viewer。层级分类与 Leader 身份不进入权限树（decision_600ae27b0538）。
- **Portal Admin 层对应管理员名单。** 管理员名单中的主体对“共享”节点有全部权限；个人空间和个人 AMA 不在其中。管理员身份不改变 owner 列表，也不恢复已删除对象（decision_3fe3cca8fd95、decision_b820faf42218）。

### 左侧导航：Lineages 面板

Portal 左侧导航有四个面板：Lineages、Chatroom（decision_a6bebf4ac283）、My Favorite 和 Chat History。本节说明按组织展示的 Lineages 面板和 My Favorite 面板。

定时或自动触发的 Resident 与其他 Resident 共用 Lineages 和收藏导航；工作看板扩展不决定它所在的导航面板。定时与自动触发工作的汇总与控制由 Home 的“定时与自动触发”面板提供（decision_481dc4e6292c）。浏览器中保存的旧导航视图 `background` 已删除，选择它时自动回到 Lineages。

Lineages 面板从上到下依次是：当前用户的个人 AMA Resident 卡片、Which Resident 推荐卡片、个人助手卡片，以及 **My Projects** 分组。My Projects 列出当前用户有读取权限的 Project，每个 Project 是一张可以展开的卡片，展开后列出其中当前用户有读取权限的普通 Lineage。

- **点击 Project 卡片展开或收起它的 Lineage 列表。** 卡片右侧依次是收藏星标、「＋」、打开箭头和更多菜单。
- **Project 卡片上的打开箭头进入 Project 层 Lineage。** 箭头同时打开 Project Resident 的对话和 Project 层画布。页面读取导航中已有的 Lineage 与 Leader ID；目标缺失时箭头不可用，点击不创建任何对象。聊天面板被关闭时，箭头同时恢复聊天面板和画布。当前用户无权打开时，卡片显示锁，箭头不可用。
- **Project 卡片上的「＋」在这个 Project 中新建 Lineage。** 它打开 HATG 的 Lineage 创建表单并预选这个 Project；点击不改变卡片的展开状态，Project 层 Lineage 尚未创建或 HATG 面板收起时也可以使用。填写名称后提交 `CreateLineage`，服务端按目标 Project 检查权限；失败时显示原因并保留表单，成功后进入新 Lineage 并打开添加首个 Resident 的表单。HATG 工具栏中也有独立的 Lineage 创建入口，不需要先选中任何对象。
- **点击 Lineage 卡片打开它的 Leader 对话和画布。** Lineage 卡片不展开成员列表，也没有单独的打开箭头；卡片本身就是入口。新建的 Lineage 还没有 Leader 时，点击只打开画布，用户可以在画布中添加第一个 Resident。
- **My Projects 标题右侧的「＋」新建 Project。** 已登录用户都能看到这个入口，列表收起或还没有任何 Project 时也可以使用。表单填写 Project 名称、类型和可选说明，提交 `CreateProject`；服务端以当前登录用户为创建者，并把创建者设为新 Project 的 admin。提交期间按钮不能重复点击；失败时保留表单并显示原因；成功后清空搜索与筛选、刷新导航，并提示在项目卡片中添加 Lineage。
- **搜索框按 Lineage 名称和项目名称筛选。** 输入框提示“搜索 Lineage 或项目名称”。Project 名称匹配时显示它的全部可见 Lineage；只有某个 Lineage 名称匹配时，只显示这个 Lineage 及其所属 Project。
- **筛选面板有“显示范围”和“项目”两组选项。** 显示范围取“有内容”（默认，隐藏既没有可见 Lineage、也没有 Project 层 Lineage 的 Project）、“运行中”（只显示有 Resident 正在工作的 Lineage 及其 Project）或“全部”；“项目”选择一个 Project 时只显示这个 Project。筛选条件不是默认值时，面板下方显示当前条件，并提供一键清除。

### 左侧导航：My Favorite 面板

- **My Favorite 顶部与 Lineages 共用 Ask Me Anything、Which Resident 和个人助手三个入口。** 三张卡片按此顺序位于收藏分组上方，不要求先收藏。它们打开同一个已登记 Resident 的对话，并复用菜单、运行状态、未读状态与权限提示；收藏列表为空或读取失败时仍展示这些入口。个人 AMA 尚未关联时沿用 Lineages 的展示行为。
- **My Favorite 面板收藏 Project 和 Lineage。** Project 收藏的地址是 `project:<project_id>`，Lineage 收藏的地址是 `lineage:<lineage_id>`，因此还没有 Project 层 Lineage 的 Project 也可以收藏。收藏的 Project 和 Lineage 使用与 My Projects 相同的卡片、展开规则、箭头和菜单。
- **星标只表示是否收藏，锁只表示授权被拒绝。** 空心星表示未收藏，实心星表示已收藏。收藏的 Project 或 Lineage 被删除后，它的卡片随之消失，收藏自动取消，个人导航（`GetUserNavigation`）中也不再出现。收藏对象找不到或服务暂时不可用时，卡片显示对应原因，不显示锁；这些收藏始终可以取消。

### User 层入口：AMA、Which Resident 与个人助手

- **Which Resident 卡片打开负责推荐 Resident 的那个 Resident 的对话。** 这个 Resident 由应用配置 `resident_discovery` 指定（decision_6591e7793116）；卡片使用与 AMA 相同的 Resident 菜单。用户在普通聊天输入框中描述需求，推荐结果、候选的 Open/Fork 选择和纠正表单以卡片形式出现在消息区（decision_8fe87d812a4c）。接收者配置缺失和明确的权限拒绝分别显示不同的提示。
- **个人助手卡片打开用户私人 Family 的门面对话。** 卡片读取用户的个人助手入口关联（`lineage-workbench.personal-assistant`），确认门面可读后显示个人 Lineage 的当前标题（默认「Ask Me Anything | My Lineage」）；用户改名后，Lineage 变更事件让卡片重新读取标题。关联缺失或目标不可读时不显示这张卡片，其他入口照常（decision_bd5217d08c09）。
- **全局悬浮的「我的 Mailbox」按钮下方有个人助手的章鱼按钮。** 两者拼成一个不留缝隙的悬浮标签，共用尺寸、拖动位置和避让，靠底色区分，面板互斥；鼠标停在章鱼上展开门面对话，触屏点按切换。细节见 decision_bd5217d08c09。

## 实现说明

### 组件分工

| 组件 | 位置 | 负责什么 |
| --- | --- | --- |
| 组织层级规则 | `society_service/principal_hierarchy.py` | 校验 Lineage 与 Resident 的层级字段、计算 Resident 的有效层级、计算 Project 层 Lineage 的结构引用、为无权节点生成灰色占位、自动分类 |
| 组织层级领域 | `society_service/principal_hierarchy_domain.py` | Project 创建流程中的主体初始化、个人 AMA 的建立与删除，以及导航数据的读取 |
| 结构引用维护 | `society_service/state_store.py` | 在成员或 Leader 变化的同一事务中重算 Project 层 Lineage 的节点，检查离任 Leader 是否仍被引用 |
| 画布投影 | `society_service/lineage_view_projection.py` | 读取 Lineage 时按当前用户权限处理结构引用节点 |
| 导航树 | `static/principal_navigation.js` | 把导航数据整理成 Project 分组，并应用搜索和筛选 |
| Lineages 面板 | `static/lineage_workbench.js`、`static/portal_navigation.js` | 渲染卡片、处理展开、打开、收藏与筛选 |
| 新建 Project 对话框 | `static/portal_project_creation.js` | 收集表单并提交 `CreateProject` |

### 数据怎样到达导航

```text
SocietyService.navigation(当前用户)
  ├─ 可读的 Project 列表：每项含 Project 层 Lineage 与 Leader（有权时）、
  │   可读的子 Lineage 列表
  └─ User 层 Resident 列表
        │ 随侧栏快照送到页面（principal_hierarchy 字段）
        ▼
principal_navigation.projectGroups(导航数据, 侧栏成员, 搜索与筛选条件)
        │
        ▼
My Projects / My Favorite 的卡片
```

## 相邻 Design

- Resident 身份与唯一所属 Lineage：decision_0f57656a2acd。
- Lineage 的成员、Leader 与协作定义：decision_6fc653d55c79。
- Project、应用接收配置与组织类指令：decision_6591e7793116；Project 的创建与删除：decision_4648f7fa3295。
- 权限系统：decision_b820faf42218；默认模板的三棵树与表规则：decision_600ae27b0538；身份认证与领域授权：decision_3fe3cca8fd95。
- Which Resident：decision_8fe87d812a4c。

当前存储以 `lineage_nodes` 确定 Resident 的唯一归属，以 `lineage_node_references` 表示跨 Lineage 的画布引用。Resident 的有效层级随当前 Leader 和所属 Lineage 计算；用途 category 和 scope_ids 仍是明确的 Resident 分类数据。Portal System 保持普通正式 Lineage，不依赖旧 User 层分类标记运行。

## 导航首屏的读取范围

Lineage Navigation 的首屏快照只读取 Project 层级和 Lineage 摘要，使用摘要中的标题、Leader ID 与成员 ID/数量构造卡片，不读取 Resident 目录。个人 AMA、Which Resident、个人助手等 Chat 快捷入口通过独立请求加载；打开 Lineage Menu 时才用 `GetLineage` 读取成员标题。侧栏不显示 Resident 运行状态、工作标记或未读标记，也不订阅批量 Resident 状态。
