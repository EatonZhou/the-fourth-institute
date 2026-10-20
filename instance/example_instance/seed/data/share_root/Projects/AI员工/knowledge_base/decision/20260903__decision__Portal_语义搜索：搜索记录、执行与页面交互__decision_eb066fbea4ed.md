---
kb_id: decision_eb066fbea4ed
type: decision
project: AI员工
created_at: '2026-09-03'
version:
  number: 23
  created_at: '2026-09-03'
  created_by:
    user: ''
    agent: ''
  updated_at: '2026-10-19T08:23:07Z'
  updated_by:
    user: ''
    agent: resident_83d75ad3a6c7
importance: 9
metaness: 2
ai_generated: false
links:
  source:
  - ${code_root}/management_portal/society_service/resident_input_scope.py
  - ${code_root}/management_portal/search_identity_backend.py
  - ${code_root}/management_portal/semantic_search_jobs.py
  - ${code_root}/management_portal/static/portal_semantic_search.js
  - ${code_root}/management_portal/board_semantic_search_backend.py
  - ${code_root}/knowledge_board_lan/knowledge_backend.py
  - ${code_root}/management_portal/paper_backend.py
  - ${code_root}/management_portal/serve_management_portal.py
  - ${code_root}/tests/cases/test_board_semantic_search.py
  related:
  - decision_528bf9985705
  - decision_f67ae5e70793
  - decision_8dc6b3d03ca7
  - decision_1a2730dcd3d1
  - decision_2f67ed28db67
  - decision_e37f240e186d
  - decision_3ece26686464
  - decision_06defc47e960
  - decision_c524c775e0be
  - decision_edc470e09f5a
  - decision_18abbdef3da3
  - decision_f560803947c6
  supersedes: []
  derived_from: []
---
# Portal 语义搜索：搜索记录、执行与页面交互

Portal 的普通筛选适合已知关键词。语义搜索让用户用一句自然语言描述想找的内容：Portal 先按用户权限准备候选，再交给一个负责搜索的 Resident 理解问题、挑选并排序，最后只展示当前用户有权读取的已有资源或条目。

本 Design 说明页面上怎样发起、查看、切换和管理语义搜索，搜索记录怎样保存，每次搜索怎样交给负责的 Resident 执行，以及怎样保证不同用户的搜索互不泄露。资源摘要与查询见 `decision_8dc6b3d03ca7`；Resident 消息与输入状态见 `decision_c524c775e0be`。

## 基本概念

- **搜索页面（surface）**：接入语义搜索的一个页面范围。当前有 7 个：`knowledge`（知识库）、`skill`、`repo`、`article`、`experiment`、`paper`、`wall`。每个页面决定自己的候选范围、结果卡片和详情布局。
- **已保存搜索（Saved Search）**：用户发起的一次语义搜索，使用稳定的 `search_*` 资源 ID，保存问题、页面、创建者、运行状态和结果。它也是一个资源，由资源索引收录（`decision_8dc6b3d03ca7`）。
- **运行代次（run_generation）**：一个已保存搜索的第几次运行。第一次运行为 1，每次刷新加一。
- **搜索 Resident**：负责某个页面搜索的 Resident，由 Portal 的应用配置 `application_targets` 中 `semantic_search:<页面>` 选定。它是普通 Resident，所在的 Lineage 也是普通 Lineage。
- **执行输入**：新运行把完整的已授权候选目录作为一条隔离消息投递给搜索 Resident。已经冻结的多批次运行继续使用原消息及回执，直到该代完成、取消或删除；刷新产生的新代使用完整目录输入。
- **创建者（created_by）**：发起搜索的用户，用于署名和输入隔离。搜索不保存所有者列表；谁能查看、刷新和删除由权限系统回答（decision_b820faf42218）。

## 页面上能看到什么

| 区域或控件 | 含义 |
| --- | --- |
| 搜索输入框 | 输入自然语言问题；页面原有的关键词筛选照常可用 |
| 语义搜索按钮 | 用输入框中的问题创建一次新的语义搜索 |
| 任务面板 | 这个页面的搜索列表（WALL 按当前工程） |
| 任务行 | 显示问题、运行状态、结果数量和时间 |
| 刷新 | 重新运行这个搜索 |
| 关闭 | 确认后删除这个搜索 |
| 当前搜索栏 | 结果区顶部显示正在查看哪一次语义搜索的结果 |

接入语义搜索的页面有知识库、Paper、WALL，以及 Skill、Experiment、Repo、Article 的资源目录页面。语义搜索只提供“哪些条目匹配、相关度顺序如何”；卡片、摘要和详情由各页面自己渲染。页面可以在匹配集合上继续应用自己的筛选与排序，例如 WALL 保留阶段、聚类以及用户选择的重要度、标题或最近打开顺序（`decision_06defc47e960`）。

## 设计点

### 创建与查看

- **用户输入非空问题并点击语义搜索按钮后，页面创建一个搜索，任务面板立即显示它的排队或运行状态。** WALL 中按 Enter 也会创建；输入时仍只做关键词筛选。
- **运行完成后，任务行显示结果数量。** 结果数大于零的已完成搜索可以选中；结果数为零时保留状态和提示，不能选中。
- **选中一个搜索后，页面只展示它的结果中当前用户可读的条目，并显示当前搜索栏。** 再次点击当前搜索可以退出，回到普通列表。
- **搜索的状态**为 `queued`（排队）、`running`（运行中）、`done`（完成）、`error`（失败）和 `deleted`（已删除）。

### 管理与共享

- **搜索没有所有者列表。** 搜索领域代码不判断谁能管理；权限系统关闭时，任何有效登录的 User 和 Resident 都能查看、刷新和删除页面上的搜索。权限系统开启时，统一资源入口按当前模板判定（例如 `UnregisterResource` 要求对该搜索有 admin）。
- **删除是软删除。** 记录和审计信息保留，普通列表不再显示。删除时，正在运行的这一代立即停止。统一资源删除入口 `UnregisterResource` 委托同一页面删除逻辑；重复删除保持幂等。测试请求新建的搜索自动进入所属用例的资源生命周期，结束时经该统一入口回收，读取或刷新既有搜索不会取得其清理归属。
- **能看到一个搜索，不代表能看到创建者看到的全部结果。** 搜索本身的可见范围和结果资源的读取权限分别计算；每次展示结果时，都按查看者当前的权限重新过滤。

### 失败与刷新

- **运行失败时，任务行显示 `error` 和原因；之前成功的结果仍可选中。** 页面不会把失败显示为零结果。
- **刷新会开始新的一代运行。** 如果被刷新的搜索正处于选中状态，页面先退出它，等新结果到达。退出选中不会取消已经开始的运行。

## 谁执行搜索

### 每个页面由一个配置的 Resident 负责

- **一次搜索交给 `semantic_search:<页面>` 当前配置的 Resident。** 同一页面的所有用户和所有搜索共用这个 Resident，不同页面可以由不同 Resident 负责。`application_targets.search_resident` 读取该应用的目标，搜索代码不自行登记或修改它；本文不再提供已撤销的 `UpdateDutyBindings` 入口。
- **选择 Resident 只看页面。** 调用者、问题、WALL 工程、筛选条件和候选集合只进入本次搜索的输入，不影响由谁执行。
- **所有活动的 Portal 用户都可以使用登记的搜索 Resident。** 每次投递前检查：搜索的创建者仍是活动用户，目标 Resident 仍存在、可以服务，应用配置仍指向它，并且它仍是所在 Lineage 的成员。使用搜索服务不赋予调用者对该 Lineage 的任何权限。
- **缺少登记或登记失效时，搜索明确失败。** 系统不按用户或问题临时创建替代的 Resident，也不改用其他页面的 Resident。
- **一次运行在第一次投递前记录选中的 Resident。** 之后应用配置改变，只影响新的运行；已经投递的批次继续由原 Resident 执行，并沿原消息观察结果。旧 Resident 及其对话不删除、不迁移。

### 每次输入彼此隔离

同一个搜索 Resident 服务所有用户，每条搜索输入按以下方式隔离：

- **每条输入登记它的真实创建者。** 投递前，Portal 为这条输入登记“输入范围”（input scope）：输入属于哪位用户，以及输入内容的摘要。登记之后不能修改。
- **每条输入在新的模型会话中执行。** 执行层不接续这个 Resident 之前的会话；调用模型接口的 Resident 不带历史消息。模型因此只看到本次问题和本次候选。
- **只有输入的创建者能看到这条输入的消息、状态、事件和回复。** 按 Resident 读取对话或订阅事件时，其他用户的搜索输入被过滤掉。
- **候选只包含创建者当前有权读取的条目。** 每个候选只带结果标识和约定的检索字段：标题、预览、摘要、论文摘要、正文片段、Project、类型、状态、作者、发表处、年份、领域，以及 Project 列表、标签、模块路径和负责人；不附带文件或目录路径。共享搜索适配器保留这些字段提供的完整文字和列表，不按字符数、列表长度或候选数量截短。
- **输入容量由 AI 执行处理。** 搜索层不设置字符数、UTF-8 字节数或候选数量上限，也不据此拒绝或拆分目录。ExecutionRequest 校验输入非空，不另设 prompt 字符上限。实际模型上下文容量由 Provider 与 Harness 处理；无法处理时按真实执行结果报错，不通过省略候选将不完整处理标为成功。候选授权、运行隔离和快照完整性校验持续生效。

**搜索 Resident 按自己的运行设置执行。** 是否能够读取文件、执行命令及其工具能力由运行设置决定（`decision_d19b9909a674`）；页面数和应用目标不是固定数量的全权限 Resident 清单。提示词要求它只依据内联候选判断相关性；隔离只靠上面两条：输入只对创建者可见，候选只包含创建者有权读取的条目。

## 一次搜索怎样完成

```text
用户在页面提交问题
  → 页面后端创建或刷新已保存搜索，推进运行代次
  → 按页面范围准备当前用户可读的候选，冻结为本次运行的输入快照
  → 为完整候选目录生成一条消息，登记输入范围
  → 用 DeliverMessage 投递给该页面配置的 Resident
  → 按消息 ID 观察输入状态与回复
  → 从回复中取出结果，只保留候选中存在的标识
  → 保存为本代运行的 done 结果，或保存 error
  → 页面重新读取列表，按查看者当前权限展示结果
```

### 候选与结果标识

- **候选由页面范围决定。** 资源类页面从资源索引取候选；知识库、Paper 和 WALL 由各自的领域后端提供当前用户可读的目录。WALL 的候选是当前工程中可读的设计点、改进点、方向和其他墙面点。
- **每个页面只使用一种结果标识字段。** 知识库用 `kb_id`，Paper 用 `paper_id`，WALL 用 `item_id`，其他资源页面用 `resource_id`。结果解析、模型输出校验和候选校验使用同一个字段，不接受其他字段作为别名。
- **模型输出只能引用候选中的标识。** 不在候选中的标识被丢弃，不按标题或路径猜测资源。模型只给出相关性判断，不决定权限。

### 统一的执行方式

所有页面的已保存搜索由同一个任务模块（`semantic_search_jobs.py`）管理：列表、创建、刷新、删除、读取和结果过滤只有一份实现，各页面只声明自己的差别——搜索记录属于哪个业务范围、结果用哪个标识字段、公开记录保留多少条结果、是否属于某个工程（WALL 必须有且检查可读，Paper 可选），以及刷新时是否保留上一代结果（只有 Paper 保留并合并，其他页面清空后重排）。所有页面使用同一个执行契约 `system_surface_search.v1`。后台任务只检查搜索记录和运行代次是否仍是当前的，然后交给共享的搜索适配器；准备完整候选快照、投递消息、解析回复和写回结果都由共享适配器完成。每批的计划只保存消息 ID、候选指纹和运行代次。诊断入口读取同一份计划；本代还没有形成计划时返回 `not_recorded`。

## 运行代次与重试

- **同一代的重试沿用原输入消息。** 同一次运行的重试使用原来的运行代次、候选快照、执行计划和消息 ID，投递使用幂等键 `search:<消息 ID>`，不会重复执行。恢复已经冻结的多批次计划时，不重新分配候选或重新应用历史容量阈值；仍检查每份输入的查询、页面范围以及全部候选覆盖情况。
- **刷新在一次原子更新中把运行代次加一。** 并发的多次刷新只产生一个新代次。刷新只取消上一代仍在进行的批次，不改写已经完成的历史。
- **晚到的结果不覆盖新一代。** 完成、失败、状态更新和取消都带着自己的运行代次；代次已经不是当前的，或搜索已经删除、取消或完成时，这些写回被忽略。
- **每代运行的输入快照、回执和产物分开保存。**

## 读取权限

- **读取搜索要求有效登录身份。** 搜索本身不再按所有者限制可见范围；WALL 的搜索还要求查看者能阅读所属工程。已删除和不存在的搜索都返回相同的 404。
- **命中的条目再按查看者当前的权限过滤。** 权限被撤回后，对应的结果和理由都不再返回。

## 浏览器中的协调

多个页面复用 `portal_semantic_search.js` 中的共享控制器 `PortalSemanticSearch.create`。

- **创建参数**：必需的输入框、启动按钮、任务面板和页面的搜索接口地址（列表、创建、刷新、删除）；可选的身份显示区域、文字标签和禁用条件。
- **公开方法**：`load`（读取列表）、`start`（创建搜索）、`jobs`（当前列表）、`activeId` 与 `activeJob`（当前选中的搜索）、`setActiveId` 与 `clearActive`（选中与退出）、`filterAndSort`（把页面记录按选中搜索过滤排序）、`resultFor`（读取某条记录的匹配说明）、`destroy`（解除监听并关闭菜单）。刷新和删除由任务面板中的按钮触发。
- **读取调度**：列表读取统一由 `PortalRefreshCoordinator` 管理。同一控制器串行读取，进行中的读取收到强制刷新时排队补读；销毁控制器时停止订阅、轮询和未完成的读取。
- **变化通知**：`portalSubscription` 只转交与页面范围匹配的投影变化。协调器将连续事件在 100 毫秒内合并刷新，响应事件通道就绪，并以两秒轮询作为事件断开时的读取保障。
- **排序**：默认按服务端给出的相关度排序；WALL 在匹配集合上继续使用页面选择的排序。
- **防止重复与串页**：创建进行中时阻止重复提交；销毁后迟到的响应不再更新页面；WALL 切换工程时新建控制器，避免不同工程的搜索和回调混在一起。

重新载入页面后，搜索列表从服务端重新读取。

## 页面接口

| 页面 | 列表 | 创建、刷新、删除 |
| --- | --- | --- |
| 知识库 | `GET /knowledge/api/semantic-searches` | `POST /knowledge/api/semantic-search/start`、`refresh`、`delete` |
| Paper | `GET /paper/api/semantic-searches` | `POST /paper/api/semantic-search/start`、`refresh`、`delete` |
| 资源目录（Skill、Experiment、Repo、Article） | `GET /api/resource-semantic-searches` | `POST /api/resource-semantic-search/start`、`refresh`、`delete` |

WALL 使用墙面后端的同类接口。WALL 的搜索链接带回 `/wall` 的 `project` 与 `semantic` 参数；列表按当前工程读取；创建搜索要求能阅读这个工程，不要求编辑权限。

新页面接入时，复用搜索记录、执行契约和权限过滤，只增加页面自己的候选来源与结果展示。资源索引收录了某个类型，不会自动让它出现在每个语义搜索页面中。

## 故障与恢复

| 情况 | 处理 |
| --- | --- |
| Portal 进程重启 | 已投递的输入由 Resident 执行链继续运行；搜索任务恢复后按原 Resident 与消息 ID 继续观察 |
| 投递回执丢失 | 用原消息 ID 重试投递 |
| 本次观察等待到期 | 只结束这一次等待，下次继续观察同一条输入；只有显式刷新才开始新一代 |
| 模型失败 | 保存 `error` 和可显示的原因，不生成结果 |
| 输出中有不属于候选的标识 | 丢弃该项 |
| 搜索完成后资源被删除或权限改变 | 查看时重新过滤 |
| 资源索引暂未就绪 | 按 ResourceService 的“服务不可用”处理，不用空结果掩盖 |
| 浏览器请求失败 | 保留已显示的列表，可以刷新重试 |

## 状态归属

| 状态 | 保存者 |
| --- | --- |
| 搜索记录与生命周期 | `semantic_search.jobs` 以全局 `search_id` 为主键；`surface` 直接保存 paper、knowledge、skill、repo、article、experiment 或 wall。查询、创建者、状态、运行代次、进度、秒级 Unix 时间和消息 ID 使用明确列；结果、刷新排除项及输入失败诊断使用限定用途的 JSONB。`result_count` 从结果列表计算；不保存顺序、整条 job JSON 或调用记录副本。普通更新按 ID 修改单行；运行恢复通过 SocietyService 的 `read_plan` 读取权威调用计划 |
| 冻结的运行输入 | `semantic_search.run_inputs` 按搜索 ID 保存当前运行身份与输入；删除搜索时清理对应输入 |
| 搜索 ID | 搜索身份后端（`search_identity_backend.py`） |
| 批次计划与投递回执 | SocietyService 的服务调用记录 |
| 输入范围 | SocietyService 的输入范围表 |
| 消息、输入状态、事件与回复 | 搜索 Resident 的对话（`decision_c524c775e0be`） |
| 资源摘要 | 资源索引 |
| 资源读取权限 | 权限系统（关闭时不限制，个人 AMA 除外） |

搜索事务提交后才通知资源索引刷新。身份操作嵌套搜索列表写入时，通知等待最外层身份事务提交；回滚不通知。通知失败由索引协调恢复，不回滚已经提交的搜索。
