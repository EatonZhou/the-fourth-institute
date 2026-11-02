---
kb_id: decision_fd9851ae01c0
type: decision
project: AI员工
created_at: '2026-09-04'
version:
  number: 37
  created_at: '2026-09-04'
  created_by:
    user: ''
    agent: ''
  updated_at: '2026-11-02T15:47:45Z'
  updated_by:
    user: ''
    agent: resident_83d75ad3a6c7
importance: 9
metaness: 2
ai_generated: false
links:
  source:
  - ${code_root}/management_portal/static/portal_society_service.js
  - ${code_root}/management_portal/static/portal_resident_service.js
  - ${code_root}/management_portal/static/portal_lineage_catalogs.js
  - ${code_root}/management_portal/static/lineage_workbench.js
  - ${code_root}/management_portal/static/lineage_workbench_location.js
  - ${code_root}/management_portal/static/lineage_workbench_chat_targets.js
  - ${code_root}/management_portal/static/lineage_workbench_layout.js
  - ${code_root}/management_portal/static/hatg_panel.js
  - ${code_root}/management_portal/static/portal_chat_workspace.js
  - ${code_root}/management_portal/http_lineage.py
  related:
  - decision_0820081083f5
  - decision_71aa039e3211
  - decision_898b8911a2ff
  - decision_daba6706a788
  - decision_4637ca4a69e2
  - decision_68215b3a825b
  - decision_d19b9909a674
  - decision_5bf5bfbd52cb
  - decision_6591e7793116
  - decision_b1ab45c4b3ec
  supersedes: []
  derived_from: []
---
# Portal Lineage 浏览器客户端与 Catalog 共享读取

Society 页面（Society Workbench）、HATG 团队图编辑面板、Chat 工作区和 Workbench Studio 都要读取和修改 Lineage，也都要在选择器中列出 Resident、用户、项目和可用的模型后端。本 Design 说明它们共用的两个浏览器模块：把页面操作映射为 HATG ISA 指令的 Lineage 客户端，以及在一个页面中短期共享目录读取结果的 Catalog。

两个模块都不拥有业务事实。Lineage、Resident 及其运行状态由 SocietyService 保存；用户、项目和模型后端目录由各自的服务提供；页面负责选择状态、加载提示、空态和错误展示。

## 基本概念

- **Lineage 客户端**：源码中的 `PortalSocietyService.createClient()` 返回的对象。它为页面上的每种 Lineage 操作提供一个方法，方法内部调用对应的 HATG ISA 指令（decision_898b8911a2ff），或者读取只服务浏览器的 Society 页面接口。
- **页面接口**：只服务 Society 页面的两个只读 HTTP 地址：`/lineage/api/lineages`（列出调用者能看到的 Lineage 摘要）与 `/lineage/api/node-metadata`（读取画布节点的补充信息）。页面上的所有修改都是 ISA 指令。
- **Catalog**：源码中的 `PortalLineageCatalogs`。它在当前页面中缓存选择器用的目录：Resident 目录、用户目录、按执行账号读取的运行能力目录、模型后端目录和项目目录，同一时刻多处读取时只请求一次。
- **最大缓存年龄**：目录值可以直接复用的时间，默认 15 秒。调用方传入 `maxAgeMs` 可以改变它，传入 `force: true` 可以跳过新鲜值重新读取。

## Lineage 客户端

- **每项 Lineage 修改都是一条具名指令。** 例如修改元数据调用 `UpdateLineage`，在画布上新建节点调用 `CreateResident`，从节点 Fork 调用 `ForkResident`，批量删除选中的对象调用 `ApplyLineageChanges`，组件、连线、运行控制和工作区交接分别调用对应的 Lineage 定义与运行指令（decision_4637ca4a69e2、decision_68215b3a825b）。指令经 `PortalInstructionClient` 提交到 `/api/instructions/execute`（decision_daba6706a788）。
- **图修改默认返回指令结果与重读的 Lineage。** `mutate` 默认在指令完成后用 `GetLineage` 重读，把结果放在 `lineage` 字段中；删除 Lineage、跨 Lineage 移动等明确指定 `reload: false` 的操作直接返回指令结果，布局更新也有独立回执。已提交后重读失败时，错误保留 `committedMutation` 中的指令名和结果，页面据此区分写入成功与刷新失败，不重放已完成的写入。
- **定义修改携带所读版本。** 客户端记录 `GetLineage.result.lineage.revision`；HATG 面板优先使用当前显示的定义版本。组件、连线和 Function Block 的整份配置编辑保留表单读取时的版本，即使保存排队或后台重读，也不把旧草稿配上新版本。`mutate` 把此版本放在调用顶层的 `expected_version`；迁移可按来源与目标 Lineage 传入已经读到的版本映射。未读到版本时不虚构版本。`CreateResident` 新建身份在服务端锁内追加，客户端默认不补定义版本；调用方显式提供 `expected_version` 时仍传递。准备、运行、运行状态及全局 Function Block 操作不使用定义版本前提，布局的 `layout_revision` 也不用于定义比较。
- **冲突只刷新，不重放修改。** 定义操作返回 `instruction.conflict` 后，客户端重新读取相关 Lineage，保留错误码和版本细节，将新图交给页面并提示本次未保存。HATG Inspector 展示重新读取的配置；仍打开的 Function Block 编辑对话框保留原版本，须重新打开后编辑。重读失败时显示刷新失败，原冲突继续以 Promise 拒绝返回。任何冲突都不自动用新版本重试写入。
- **每次修改都带幂等键。** 调用方在参数中提供 `idempotency_key` 时使用它；否则客户端为这次操作生成一个带操作前缀的随机键。页面因网络失败重试同一操作时沿用原键，服务端只执行一次。待投递调用的修改总是使用新键。
- **只读操作直接返回指令结果。** `loadLineage` 读取 `GetLineage` 的 `lineage`；触发路径、组件运行状态、待投递项和跨 Lineage 迁移进度分别使用 `ListTriggerPaths` / `GetTriggerPath`、`GetComponentRuntime`、`ListPendingDeliveries` 与 `GetMoveOperation`。
- **缺少必需 ID 时不发出请求。** 需要 Lineage ID 的方法先去除首尾空白，为空时直接抛出 `TypeError`；`loadNodeMetadata` 同时要求 Lineage ID 与 Resident ID。
- **画布坐标也是指令。** `updateLayout` 调用 `UpdateLineageLayout`：坐标以后写为准，不携带定义版本，也不重新读取整张图，响应里的 `layout_revision` 更新本地布局计数。`listLineages` 与 `loadNodeMetadata` 读取对应的页面接口。
- **模型会话释放完成后使绑定显示失效。** `ClearContext` 的回执可能先于异步释放完成；释放成功后，服务端发布已有的 Resident 身份变更通知，页面使相关节点 metadata 失效并重新读取，移除旧会话标题。释放或绑定比较失败时不发布释放完成通知。后续会话绑定以实际的新会话 ID 为准；刷新保留聊天 DOM 与未发送草稿。
- **图修改共享版本与重读入口。** HATG 中没有专门客户端方法的定义修改也通过 `mutate` 提交。`executeInstruction(name, args, controls)` 保留原样调用能力，用于不依赖这套图编辑状态的其他操作。
- **错误交给页面显示。** 参数错误、权限拒绝、对象不存在和服务不可用以 Promise 拒绝返回。定义冲突先尝试重读，再拒绝原操作；错误对象始终保留指令错误码和结构化细节（decision_898b8911a2ff“返回格式”）。

测试或嵌入场景可以向 `createClient` 注入 `get` 与 `execute` 两个传输函数；`getRevision` 可提供页面实际显示的版本，`onConflict` 接收冲突重读后的图。未注入传输函数时分别使用 `PortalAPI.get`（`cache: "no-store"`）与 `PortalInstructionClient.execute`。

## Resident 的创建与派生

Resident 的创建、上下文派生与成员迁移使用具名指令。画布通过 `PortalSocietyService` 的图修改入口提交，Resident 目录已有 Lineage 的添加入口使用 `PortalResidentService.attach`；页面表单只保存当前输入，结果以指令回执和重新读取的记录为准。

- **普通创建建立一个新的 Resident。** `PortalResidentService.attach` 在指定的已有 Lineage 中调用 `CreateResident` 并重读新身份。新建 Lineage 的图入口由 `PortalSocietyService.createLineage` 调用 `CreateLineage`；创建的服务端合同见 decision_d19b9909a674。普通创建只使用新身份的初始设置与工作区参数。
- **Fork 明确指定来源 Resident 和上下文方式。** 画布通过共享图修改入口提交 `ForkResident`。上下文派生和跨 Lineage 归属的精确合同见 decision_5bf5bfbd52cb 与 decision_6591e7793116；页面按各步骤的指令结果处理，失败时保留已经完成的步骤。
- **迁移保留 Resident 身份。** `update` 的归属修改调用 `MoveResident`，页面按返回的当前归属刷新导航。创建、Fork 和移动的服务规则见 decision_d19b9909a674、decision_5bf5bfbd52cb 与 decision_6591e7793116。
- **Resident 的当前归属由 `lineage_id` 表达。** 列表、菜单、导航和刷新签名使用当前 ID 与可用操作，不要求 Resident 提供恒定的 `lineage_membership` 字段。Lineage 自身的 `formal/personal` 分类仍用于团队目录，用户保存的显示偏好由页面状态管理。
- **创建成功表示新 Resident 已有同 ID 的 Agent 和主 trace。** 主 trace 是 AgentData 保存消息与执行历史的有序记录，可以为空；由服务端创建流程保证就绪，浏览器依据成功结果刷新目录，不在读取时补建历史。此时尚不要求模型会话已经启动，规则见 decision_b1ab45c4b3ec。

## Catalog

- **每个目录有固定的读取来源。** `residents` 默认调用 `PortalResidentService.list`（调用方可以提供等价的 `load` 函数），不同查询条件分别缓存；`users` 读取 `/api/users`；`runtimeCatalog(accountId)` 从 `/api/agent-workbench/agent-runtime-catalog` 按 `account_id` 读取运行能力目录（可选模型与推理强度）；`agentBackends` 读取可选的模型后端；`projectCatalog` 经 `PortalInstructionClient` 调用 `ListProjects` 指令。
- **缓存按当前登录用户分开。** 缓存键由小写用户名、目录种类和读取地址组成，没有登录名时使用 `anonymous`。换一个用户登录后不会读到前一个用户的目录；每个 HTTP 请求仍由服务端重新鉴权。
- **新鲜值直接复用，同一目录同时只读取一次。** 值的年龄不超过最大缓存年龄时直接返回；同一键已有进行中的请求时，后来的调用等待同一个请求。`force: true` 跳过新鲜值，但仍复用进行中的请求。
- **每个调用方得到独立副本。** 写入缓存前和返回给调用方时都复制一次（优先 `structuredClone`，否则 JSON 复制），调用方修改返回值不影响缓存和其他调用方。
- **失败不留在缓存中。** 读取失败时，若该键的条目仍指向这个请求就删除它，下一次调用重新请求；失败以 Promise 拒绝返回，Catalog 不把失败变成空目录。是否显示错误或改用空结果由页面决定。
- **页面可以让目录失效。** `invalidate()` 清空全部缓存；传入一个或多个目标时，删除缓存键、目录种类或读取地址与之相同的条目。失效只影响浏览器中的副本。

Catalog 的 `normalizeRuntimeCatalog` 统一整理运行能力目录。`routeRuntimeCatalog(route, onLoaded)` 使用 route 已有的模型信息；缺少时按其账号读取目录并在完成后通知视图。`populateBackends` 填充后端下拉框并保留仍然有效的当前值；`renderForkProvider` 为 Fork 表单投影后端和账号候选。辅助函数同样须遵守本节的身份隔离与缓存失效边界，不能跨账号套用另一 Provider 的能力。

## 页面接入

Society Workbench 与 HATG 面板通过 Catalog 读取 Resident、用户、项目、运行能力和模型后端目录；HATG 延迟加载时，页面已经读取的目录直接复用。Chat 工作区与 Workbench Studio 只使用 Lineage 客户端读取和修改 Lineage。

Catalog 只保存在当前页面的内存中，不写入浏览器存储，也不订阅服务端事件；刷新页面、显式失效或超过最大缓存年龄后重新读取。目录内容可能稍有过期，只影响选择器中显示的候选；用户提交操作时，服务端再次检查目标是否存在、状态和权限。

## HATG 面板中的 Lineage 状态

HATG 面板显示的 Lineage 是服务端数据在页面中的一份副本。副本有三类来源：面板自己修改后返回的 Lineage；后台刷新读到的 Lineage（由变化通知触发，或每 30 秒轮询一次）；其他 Panel 修改后交给面板的 Lineage，例如在 Chat 中 Fork 后的结果。三类来源都经面板内同一个写入入口（`commitLineage`）进入页面状态。打开另一个 Lineage 时，由加载流程整体替换副本，不经过这个入口。

- **按状态领域合并。** 定义（`revision` 与 `refresh_signature`）、布局（`layout_revision`）和运行（`runtime_revision`）分别取较新的一方；`refresh_signature` 只随它所属的定义一起采用。Resident 最近一次执行、连线待投递计数等易变投影总以最新读取为准。
- **派生状态随写入一起更新。** 每次写入都换成一个新的 Lineage 对象并推进本地版本。画布把节点投影缓存在这个对象上，只复用由同一对象算出的结果，因此同一版本中只有执行结果变化时，画布也显示新结果。已删除成员的运行标记、消息计数和上下文压力记录随写入清除；显示的成员集合变化时，重新订阅这些 Resident 的状态。这些步骤对所有来源相同，调用方不各自补做。
- **坐标在副本内原处修改。** 拖动、展开或改名后的自动错开、自动整理会在保存返回前直接修改副本中对象的坐标，不换新对象。这些修改同时推进本地版本，画布据此重新计算投影；错开多个对象时，下一个对象按前一个的新位置测量。
- **后台读取让位于进行中的修改。** 有结构修改或布局保存正在进行时，后台读取直接丢弃，由修改自己的结果带回更新的状态。读到的内容与当前副本一致时，不写入也不重画。
- **重画范围由变化决定。** 面板自己的修改结果重画整个面板。后台读取在定义或布局变化时重画整个面板，并提示新增或移除的节点与候选；只有运行或投影变化时，只更新画布运行状态、检查器和控件。需要保留正在编辑的表单时，调用方只写入、不重画，例如清零连线发送计数和保存前的冲突重读。调用方需要在重画前调整选中状态时，在合并之后、重画之前完成，例如删除节点时先清除对它的选择。
- **回滚本地修改。** 自动整理保存失败时，把保存前的 Lineage 原样装回并重画，不与服务端结果合并。

## HATG 面板的模块结构

HATG 面板的运行时 `hatg_panel.js` 创建下面的模块并把它们接在一起；打开和切换 Lineage、挂载面板与模板、绑定按钮和对话框，以及画布的模型、绘制和各项操作都放在这些模块脚本中。`portal_hatg_loader.js` 在运行时之前加载这些脚本。每个脚本在 `window` 上登记一个对象，例如 `PortalHatgLineageState`，对象中有一个工厂函数和它的依赖清单 `dependencies`。运行时启动时依次调用这些工厂，把每个依赖按名字交给它，再把工厂返回的函数解构为同名常量，其余代码照常调用这些名字。

下表按运行时创建模块的顺序排列：

| 模块 | 职责 |
|---|---|
| `hatg_graph_model.js` | 按 ID 查找节点、连线、组件与 Seed；节点的 Resident、运行状态与 Workspace 绑定 |
| `hatg_graph_search.js` | 画布搜索与搜索结果 |
| `hatg_lineage_state.js` | Lineage 副本的合并与 `commitLineage`、编辑队列、布局保存 |
| `hatg_program_edits.js` | 程序修改的排队提交、HATG 指令执行、连线草稿与端口点击、从原语库拖入组件 |
| `hatg_family_blocks.js` | Function Block 与 Family 的成员、折叠、门面和 Family 视图 |
| `hatg_canvas_selection.js` | 画布选择与多选、选择变化的通知、批量删除、Functionalize 与 Familize 入口 |
| `hatg_node_status.js` | 卡片上的工作状态、执行闸门、入口状态、上下文压力、消息计数与 Event/Effect 开关 |
| `hatg_function_block_menu.js` | Function Block 菜单：对象状态、失败通知人、工作指南、解除与删除 |
| `hatg_leader.js` | Leader 请求 |
| `hatg_seeds.js` | Seed 卡片、预览、升格与驳回 |
| `hatg_resident_dialogs.js` | Resident 的新建、设置、吸收、删除与 Fork |
| `hatg_lineage_dialogs.js` | Lineage 的新建、设置、删除、运行身份与子图迁移 |
| `hatg_layout.js` | 个人视图偏好、坐标同步、自动错开与自动整理 |
| `hatg_toolbar.js` | 同步状态、Lineage 列表条目、标题，工具栏按钮的可用与可见状态，以及工具栏换行后占用的画布高度 |
| `hatg_graph_projection.js` | 由 Lineage 算出 Context 视图与 Workspace 视图要显示的节点和连线 |
| `hatg_program_inspector.js` | Event、Effect、连线与原语的检查器，及其保存、删除、触发和重置；检查器按同样的依赖清单创建自己的部件：引用选择 `hatg_program_inspector_references.js`、原语字段 `hatg_program_inspector_primitive_fields.js`、连线投递 `hatg_program_inspector_delivery.js`、运行观察 `hatg_program_inspector_observation.js`、手动触发与重置计数 `hatg_program_inspector_manual_events.js`、保存与删除 `hatg_program_inspector_saving.js`、绘制 `hatg_program_inspector_render.js` |
| `hatg_relations.js` | 关系检查器与 Workspace Handoff |
| `hatg_graph_view.js` | 画布控制器与卡片内容，绘制、运行状态的局部重绘、导航、聚焦、视图模式与交互提示 |
| `hatg_workspace_results.js` | 所选 Resident 的 Workspace 成果、成果路径解析与统一 Preview，Workspace 视图的绑定读取 |
| `hatg_resident_runtime.js` | Resident 状态订阅、Lineage 变化订阅、运行状态核对、发送 prompt 与停止 Session |
| `hatg_panel_lineage_loading.js` | 面板显示哪个 Lineage：读取 Lineage 列表和对话框用的目录（Resident、Project、模型后端），按要求的 Resident 或程序对象打开 Lineage，没有 Lineage 时的空态，恢复已显示的 Lineage |
| `hatg_panel_template_shelf.js` | Lineage 模板架：在画布上挂载模板，确定模板应用到画布上的位置 |
| `hatg_panel_bindings.js` | 面板控件：关闭对话框、Escape 键、工具栏按钮、模板中的表单和下拉框、组件库分隔条，以及跟随工具栏高度、让 Seed 检查器贴着 Seed 的画布观察器 |
| `hatg_panel_mount.js` | 挂载面板：加载面板模板（含超时与重试），首次初始化时绑定模板并读取 Lineage 目录，以及 Workbench 驱动的控制器 |

- **依赖按名字显式交付。** 工厂只使用它清单中的依赖；缺少任何一个，运行时启动时就报错并指出名字。运行时的函数在启动时交给模块，之后不再替换。会被重新赋值的运行时变量（例如模板控制器）以读取函数交付，名字以 `current` 开头。
- **创建顺序与延迟调用。** 模块按上表的顺序创建，后创建的模块直接收到先创建模块的函数。先创建的模块要调用后创建模块的函数时，收到一个包装函数，调用时才去找真正的目标。运行时把这些包装放在工厂参数的末尾，并用注释 `… is created after this part.` 标出被等待的模块；例如 Lineage 状态模块通过包装调用检查器的 `renderProgramInspector`，画布视图通过包装调用 Resident 运行状态模块的 `stopLineageNode`。
- **不依赖运行时的函数放在模块顶层。** 这类函数随登记对象一起导出，运行时和测试直接调用。例如 `PortalHatgToolbar.observeNavigationHeight(viewport, toolbar)` 测量工具栏高度，在下一帧写入视口的 `--hatg-navigation-height`，使画布从工具栏下方开始；它返回停止测量的函数，运行时在挂载时调用它，在销毁或加载失败时停止。
- **模块状态由模块持有。** 只属于一个模块的状态放在模块内部，例如 Fork 弹窗的请求序号和子图迁移的进行状态。运行时通过模块提供的函数读写这些状态，例如 `subgraphMoveBusy()` 和 `resetBranchModalState()`。
- **测试加载真实模块。** 测试经 `tests/cases/javascript/hatg_modules.cjs` 加载模块脚本。测一个模块时，`createHatgModule` 创建它，只替换清单中的依赖，未替换的依赖一旦被使用就报错。测跨模块的流程时，`startHatgRuntime` 启动真实运行时：执行全部模块脚本和运行时，记录每个模块收到的依赖与返回的函数，测试用 `get(name)` 按名字取用任一模块或运行时中的函数。需要替换某个函数时（通常是绘制、提示或网络边界），`overrideHatgFunctions` 改写该函数所在文件中的声明，所有调用方因此都会用到替换后的函数。测浏览器布局时，Playwright 用例加载真实模板、样式和模块脚本，再调用模块函数；`hatg_mount_layout.cjs` 在 JSDOM 中用真实模板挂载整个运行时，由测试驱动 ResizeObserver 回调和动画帧，检查挂载与销毁的流程。测试不按字符串截取运行时源码来执行。
- **新增模块的登记。** 新模块需要同时登记在加载器的脚本列表、服务端的公开静态资源清单（`http_public_pages.py` 的 `PublicPagesHttp.routes`）和发布单元 `LINEAGE_WORKBENCH_RELEASE_UNIT`（`scripts/portal_release_units.py`）中。

## Society 工作台的模块结构

Lineage 页面的入口脚本 `lineage_workbench.js` 负责首次读取与刷新（`loadAll`）和页面级事件绑定，并创建下面的模块。页面地址、对话目标的选择与切换、选择状态、对话详情、面板布局、编辑器和对话框控件分别在 `lineage_workbench_location.js`、`lineage_workbench_chat_targets.js`、`lineage_workbench_selection.js`、`lineage_workbench_chat_detail.js`、`lineage_workbench_layout.js`、`lineage_workbench_editors.js`、`lineage_workbench_dialog_controls.js`。`lineage.html` 在入口脚本之前加载这些模块脚本。每个脚本在 `window` 上登记一个对象，例如 `PortalLineageWorkbenchSidebar`，对象中有一个工厂函数和它的依赖清单 `dependencies`。入口脚本用 `workbenchModule(name)` 取得对象并调用工厂，把每个依赖按名字交给它，再把工厂返回的函数解构为同名常量。Watchboard 的 `lineage_workbench_watchboard.js` 沿用更早的 `PortalLineageWorkbenchModules.createWatchboard` 接口，在 Which Resident 之后创建。

下表按入口脚本创建模块的顺序排列：

| 模块 | 职责 |
|---|---|
| `lineage_workbench_chatrooms.js` | 聊天室侧栏：列表、置顶、未读、卡片菜单、打开的聊天室，以及新建聊天室和添加成员 |
| `lineage_workbench_which_resident.js` | Which Resident 推荐：请求与进度、推荐卡片与对话、分支建议、路由纠正，以及刷新后恢复的请求状态 |
| `lineage_workbench_preview.js` | Preview 面板：Resident 文件与目录、在其中打开的资源与对话链接、文档与标签页、文本编辑、文件变化后的刷新、打开文档位置的保存；它按依赖清单创建自己的部件 `lineage_workbench_preview_documents.js`、`lineage_workbench_preview_refresh.js`、`lineage_workbench_preview_rendering.js`、`lineage_workbench_preview_editing.js`、`lineage_workbench_preview_files.js`、`lineage_workbench_preview_links.js` 与 `lineage_workbench_preview_chat_links.js` |
| `lineage_workbench_resident_dialogs.js` | Resident 的新建、挂入、编辑、分支与跨 Lineage 分支对话框，以及它们共用的 Backend、账号路由、模型、推理强度与权限设置 |
| `lineage_workbench_card_menus.js` | 侧栏卡片的菜单：按服务端权限给出的操作、卡片控件、可打开的编辑器、收藏，以及删除 Resident 或 Lineage |
| `lineage_workbench_sidebar.js` | Agent 与收藏侧栏：可访问的 Project、Lineage 与 Resident 的筛选与分组、列表快照与定时刷新、个人助手入口、收藏、在列表中定位目标；它按依赖清单创建自己的部件 `lineage_workbench_sidebar_targets.js`、`lineage_workbench_sidebar_filters.js`、`lineage_workbench_sidebar_snapshot.js`、`lineage_workbench_sidebar_agent_list.js`、`lineage_workbench_sidebar_favorites.js` 与 `lineage_workbench_sidebar_reveal.js` |
| `lineage_workbench_recent.js` | 快捷栏：侧栏视图切换，以及最近发出命令的 Resident 的打开、移除与清空 |

依赖交付、创建顺序与延迟调用的规则与 HATG 面板相同：会被入口脚本重新赋值的变量以 `current…` 读取函数交付；先创建的模块要调用后创建模块的函数时收到包装函数，包装放在工厂参数末尾，并用注释 `… is created after this part.` 标出被等待的模块。此外：

- **模块持有的变量由读取函数导出。** 模块内部赋值、入口脚本也要读取的变量，例如 Preview 的标签页控制器和快捷栏实例，由模块导出 `currentPreviewTabsController()`、`currentLineageActivityRail()` 这样的读取函数，入口脚本经它们读取。
- **测试加载真实页面。** 测试经 `tests/cases/javascript/lineage_workbench_modules.cjs` 加载模块。`startWorkbenchPage` 在 JSDOM 中按服务端展开脚本组后的 `lineage.html` 的顺序执行它列出的全部脚本，对接一个假的 Portal 服务：`answers` 按请求路径给出响应，`instructions` 按 ISA 指令给出结果，处理函数抛出的错误按失败的指令返回。它记录每次请求和每个脚本、页面或控制台错误，并在 `parts[global]` 中记下入口脚本交给每个模块的依赖和模块返回的函数，测试由此调用真实部件并检查真实 DOM。只测一个模块时，`createWorkbenchModule` 创建它，只替换清单中的依赖。仍留在入口脚本中的函数由 `findWorkbenchFunction` 按名字在入口脚本和全部模块脚本中查找。`lineage_workbench_start.cjs` 检查页面启动时加载全部脚本、创建每个模块且没有页面错误。
- **新增模块的登记。** 新模块需要同时登记在 `lineage.html` 的脚本列表、服务端的公开静态资源清单（`http_public_pages.py` 的 `PublicPagesHttp.routes`）和发布单元 `LINEAGE_WORKBENCH_RELEASE_UNIT`（`scripts/portal_release_units.py`）中。

## Chat 工作区的模块结构

Chat 工作区的入口脚本 `portal_chat_workspace.js` 保留两个组装部分：`createWorkspace` 创建一个对话工作区，负责消息列表、输入框、历史加载、滚动位置和运行状态的局部更新；`ResidentChatPanel` 把一个 Resident 的对话、提问卡片、子 agent、Family 门面和会话操作接到工作区上。其余部分放在下面的模块脚本中。加载 Chat 的页面只写一个脚本组占位元素 `<script data-portal-script-group="chat-workspace"></script>`，服务端发出页面前把它展开为 `management_portal/script_groups.json` 中这一组的脚本，模块脚本排在入口脚本之前；原生视图的资源清单以 `{"script-group": "chat-workspace"}` 指代同一组。每个脚本在 `window` 上登记一个对象，例如 `PortalChatWorkspaceSubagents`，对象中有一个工厂函数和它的依赖清单 `dependencies`。入口脚本用 `chatWorkspaceModule(name)` 取得对象并调用工厂，把每个依赖按名字交给它，再把工厂返回的函数解构为同名常量。入口脚本文件开头的注释按页面加载顺序列出全部模块和各自的职责。

下表按入口脚本创建模块的顺序排列：

| 模块 | 职责 |
|---|---|
| `chat_workspace_message_content.js` | 交接卡片、转发引用、运行状态、回复来源、附件引用、Markdown 与预览，图片查看器按需加载 |
| `chat_workspace_subagents.js` | 子 agent 的结果卡片、实时卡片与计时、结束提示、标签页标题计数和详情对话框 |
| `chat_workspace_composer.js` | 输入框：附件标记、光标、粘贴与拖放、附件托盘、斜杠命令与 @ 提及菜单的 HTML、发送的内容。这些函数操作的输入框都是 contenteditable 元素，Chat、公告板、知识板和 TAU 看板都这样传入；写入文字或草稿后光标停在末尾 |
| `chat_workspace_composer_menus.js` | 一个工作区的斜杠命令菜单与 @ 提及菜单：按输入内容打开、方向键与回车、选中后写回输入框、提及候选的搜索与取消 |
| `chat_workspace_conversation.js` | 协议消息转为 Chat 消息，随请求送达的 info 折进请求，Family 请求进度，由快照生成 Chat 契约，加载对话 |
| `chat_workspace_question_cards.js` | 异步提问组与按对话保存的作答、原生审批与 MCP 卡片、一轮的计划与修改卡片 |
| `chat_workspace_refs.js` | Refs 弹层：随消息发送的 Skills、Agents 和资源，计数与摘要 |
| `chat_workspace_inputs.js` | 排队输入、上下箭头输入历史、按会话保存的草稿 |
| `chat_workspace_messages.js` | 单条消息：角色与身份、作者与头像、引用与附件、操作菜单、引用和复制文本、折叠、列表键 |
| `chat_workspace_message_list.js` | 分组、列表 HTML、继承卡片、运行流、选区引用、增量对齐 |
| `chat_workspace_message_viewport.js` | 一个工作区的消息视口：哪些滚动来自读者（滚轮、滚动键、拖动滚动条、触摸），跟随底部，工作台面板布局前后保住阅读位置 |
| `chat_workspace_message_actions.js` | 一个工作区的消息操作：右键打开的消息菜单（一次只开一个，消息下方到下一条消息之间的空隙归这条消息），复制单条消息或整组回复，打开链接，宿主动作，交接卡片的“替我问一下”，回复组与 Family 工作的折叠 |
| `chat_workspace_message_folds.js` | 一个工作区的长正文折叠：绘制后、列表宽度变化或正文里的图片加载后测量正文长短，决定哪些消息默认展开，读者的折叠按钮（展开时正文不动，收起时按钮留在指针下）。整表绘制和追加一条消息都用 `updateMessageFoldDefaults` 决定默认展开，读者正在读的回复不会被折走 |
| `chat_workspace_composer_attachments.js` | 一个工作区的输入框附件：加入文件或对话选段时在光标处放入附件标记、在托盘里放入附件并上传，托盘上重试或移除，删掉输入框里的标记也会移除附件；编辑后的图片上传完成后替换原附件；上传完成时附件已被移除或已切换到别的对话，就删除这次上传。由文件生成的预览地址归工作区所有，附件离开或工作区销毁时回收。默认上传（`CreateUpload`）与浏览器文件删除（`DELETE /api/uploads/<upload_id>`）也在这里 |
| `chat_workspace_composer_drafts.js` | 一个工作区的草稿：按对话保存正在写的消息。改动在停顿后保存，页面隐藏或离开、切换对话、工作区销毁时立即写入；只是载入、显示或清空的内容不算改动，不写入；只移动光标时，只有显示的就是已保存的那份草稿才更新它。发送失败时恢复的输入作为这次发送的草稿保存，这条消息到达后与恢复的输入一起移除；切换对话后才失败的发送保存到它自己的对话，除非那里已经有新草稿 |
| `chat_workspace_composer_undo.js` | 一个工作区输入框的撤销：Ctrl/⌘+Z 撤回 Chat 自己做的粘贴和附件插入，从最新的开始，保留最近二十次。每次改动前记下当时的输入，改完后再记下改动后的输入；之后输入又变了（打字、选择引用、手动删掉），按键交给浏览器自己的撤销。撤回附件插入时只移除附件标记和插入时跟在它后面的空格，释放文件预览，删除已上传的文件；撤回粘贴时恢复粘贴前的输入和光标，仍在输入框里的附件保持当前状态，例如粘贴后才上传完成的附件仍是已上传。发送失败时用发送前的快照恢复输入和附件 |
| `chat_workspace_message_history.js` | 一个工作区往回看对话的方式：“加载更早消息”按钮向宿主请求前一页，加载期间不可用；Fork 卡片第一次展开时读取 Fork 开头的那次压缩，读到的摘要保留，每个对话一张卡片；跳到一条消息时加载更早的页直到它出现，展开它所在的折叠，把它放到视野中间并转为读历史；正在写的消息按对话保存一条引用，显示在输入区的引用条里，消息里的引用块跳到被引用的消息。点击失败交给宿主的 `onError`，没有时提示 |
| `chat_workspace_composer_height.js` | 一个工作区的输入框高度：读者拖动输入框上方的分隔条，或用方向键、Home 与 End 移动它，移动结束时按这个 Chat 保存高度并随草稿写入。高度上限为工作区高度的 46%，同时受宿主给出的最小与最大高度约束；窗口变化时把高度收回上限内，跟随底部的读者留在底部。高度从保存的值开始，工作区还没排版时上限按窗口高度计算，第一次画出输入框时的草稿恢复再按工作区高度收紧；切换对话时使用那份草稿的高度 |
| `chat_workspace_runtime_controls.js` | 一个工作区的运行控制，作用于这个 Chat 的 Resident（合同 target 指向的 Resident）：输入框的运行设置按钮显示它的设置并打开运行设置对话框；合同提供 Session 重启时，顶部按钮打开重启对话框，否则刷新对话；进行中的重启显示在顶部。设置、能力目录和对话框由 `PortalResidentRuntimeController` 持有，模块为合同的 Resident 绑定一份，合同换成另一个 Resident 时立即放开旧的，所以迟到的结果不会落到新 Resident 的按钮上。控制器报告设置已保存或重启已结束时，对话刷新一次。读者无权修改设置时，按钮按 Backend 声明的字段显示设置并保持不可用。宿主更换 Resident 期间（`releaseResidentRuntime()`）按钮不可用，顶部按钮只刷新 |
| `chat_workspace_message_drawing.js` | 一个工作区消息列表的绘制：`setMessages` 画整段对话，`appendMessage` 在末尾追加一条，`setRuntime` 原地更新消息之后的运行行，`redrawMessages` 按当前内容强制重画。`setMessages` 保留不会变化的行，只重画变化波及的范围；追加的消息连同它加入的回复组或同一人的连续消息一起画，序号按整个对话计算。列表没有消息、显示的是另一个对话、消息已经显示，或对话进行中追加的是人的消息时，`appendMessage` 返回 false，由宿主整体绘制。每次画完给新画的节点绑定附件、来源、资源标签、头像和链接预览，测量折叠，再通知宿主；消息里的图片在图片编辑器中打开，保存的副本作为附件加入输入框，编辑器打不开时交给宿主的 `onError` |
| `chat_workspace_runtime_settings.js` | Runtime Settings 对话框、保存后的提示、Session 重启对话框与进度组件；对话框由 `PortalResidentRuntimeController` 打开 |
| `chat_workspace_harness_panel.js` | Agent Harness 面板与菜单、提示注入预设与预览 |
| `chat_workspace_resident_messages.js` | Resident 消息的正文、页脚（发送状态、info、Mailbox、Family 回复、收件人、用量）、请求进度、门面停止范围与改派菜单 |
| `chat_workspace_read_observer.js` | 读者看得到对话时发送已读回执，失败后重试 |
| `chat_workspace_family_status.js` | Family 状态面板：成员工作状态、执行账号与周额度、会话迁移、成员运行设置 |
| `chat_workspace_forward_dialog.js` | 转发对话框：选择 Lineage 或项目中的 Resident、按 resident_id 指定或选择聊天室，附加说明后交给调用方发送 |
| `chat_workspace_search.js` | 会话内搜索的查询与结果；消息和跳转仍由工作区与历史加载负责 |
| `chat_workspace_resident_workspace.js` | 单个 Resident 的 Chat 工作区：页头与元素 ID、会话操作、选择要转发的消息 |

依赖交付的规则与 HATG 面板相同：工厂只使用清单中的依赖，缺少任何一个，入口脚本启动时就报错并指出名字；不依赖入口脚本的函数放在模块顶层，随登记对象一起导出。此外：

- **按依赖安排创建顺序。** 模块在入口脚本启动时依次创建，交给工厂的依赖必须已经有值。入口脚本中的函数声明在启动时已经可用，因此 `normalizeResidentTarget`、`residentSessionScope`、`footerTime`、`outwardToResidents` 这类被先创建的模块用到的小函数留在入口脚本中，按名字交给模块。一个模块需要另一个模块的函数时，排在那个模块之后创建，例如 `chat_workspace_resident_messages.js` 用到单条消息模块的 `messageRole`，排在它之后。
- **组装部分留在入口脚本。** `ResidentChatPanel` 使用四十多个模块函数，负责把模块接在一起，不再整体移入模块。`createWorkspace` 持有一个工作区的全部状态，其中的各项功能移入“每个工作区一份”的模块：这类模块的工厂在 `createWorkspace` 里调用，每创建一个工作区就得到自己的一份。工作区共用的数据（例如菜单的候选和当前行）留在工作区的 `state` 中，模块读写它；只属于这项功能的计时器、请求序号和取消句柄留在模块内部。模块需要的监听器在创建时由模块自己注册，例如消息视口监听消息列表的滚动和工作区根节点的工作台布局通知。工作区销毁时调用模块导出的清理函数：`destroyMentionMenu()` 关闭提及菜单并取消尚未绘制的一帧，`destroyMessageViewport()` 解除监听、取消排好的恢复并交还滚动锚定，`destroyMessageActions()` 解除消息列表的点击和右键监听并关闭打开的消息菜单，`destroyMessageFolds()` 停止观察列表宽度和正文图片加载，`destroyComposerAttachments()` 回收工作区仍持有的附件预览地址，`destroyComposerDrafts()` 写入最后的改动并停止监听页面隐藏与离开。撤销模块不持有计时器和监听，没有销毁函数；清空输入框时 `forgetComposerUndo()` 丢掉撤销记录。`destroyMessageHistory()` 解除消息列表和输入区上的点击监听，并让进行中的查找停止；监听挂在工作区自己的元素上，不挂在宿主传入的 root 上，旧工作区不会因宿主的元素继续留在内存里。`destroyComposerHeight()` 解除分隔条和窗口大小监听，销毁前已经开始的拖动也不再改变或保存高度。`destroyRuntimeControls()` 放开 Resident 的运行控制器，停止跟随它的重启，移除顶部的重启进度，并解除顶部按钮的点击监听。绘制模块不持有监听和计时器，没有销毁函数。工作区自己往输入框写附件标记时（加入附件、恢复草稿、撤销、宿主设置草稿），用 `withoutAttachmentReconcile(write)` 暂停按输入框标记对账，写完恢复原来的状态。工作区中定义在工厂调用之后的函数以包装函数 `(...args) => fn(...args)` 交给工厂，工作区的 `controller` 通过 `currentController()` 读取，因为工厂调用时它们还没有值。
- **消息列表的绘制。** 消息列表由 `chat_workspace_message_drawing.js` 的 `setMessages`、`appendMessage` 和 `setRuntime` 三个入口绘制，工作区的 controller 直接导出这三个函数。每条消息的签名与 `state.messages` 同时写入，下一次绘制据此比较。`setMessages` 先比较每条消息的签名和渲染上下文 `messageRenderContext`，其中包括渲染选项、所属 Resident、宿主的渲染状态、读者折叠或展开的组，以及按引用比较的渲染函数。两者都没变时不重画；如果只有实时运行状态变了，交给 `setRuntime` 原地替换运行行。已显示的行可以保留时，往前加载历史用 `prependedMessageRange` 算重画范围，其他变化用 `changedMessageRange`。范围要覆盖改动波及的所有行，例如不再是最新的回复、末尾变了的回复组、标题移到更早一页的 Family 工作。画完之后统一经过 `afterMessagesDrawn(roots, { incremental })`：给新画的节点绑定附件、来源、资源标签、头像和链接预览，测量折叠，再调用宿主的 `onMessagesRendered(列表, controller, incremental, roots)`。`appendMessage` 报告 `incremental: true` 和变化的行，宿主只处理这些行；`setMessages` 报告整表绘制，宿主据此重画它为整段对话维护的内容，例如 Resident Chat 的问题卡片。对应的行为测试有三份：`chat_message_render_paths.cjs` 检查每条绘制路径给画出的按钮绑定点击、按画了多少通知宿主、保持阅读位置；`chat_message_partial_renders.cjs` 检查局部重画波及的行，例如不再是最新的回复和移动的组按钮；`chat_message_drawing.cjs` 用宿主 `renderMessageBody` 的调用记录核对每次绘制渲染了哪些消息及其序号，并要求变化后的列表与新工作区画出的同一组消息一致，同时检查追加的拒绝条件、运行行，以及资源、链接、头像和图片编辑器交给宿主的处理。
- **测试加载真实脚本。** 测试经 `tests/cases/javascript/chat_workspace_modules.cjs` 加载 Chat：`chatWorkspaceScripts()` 按脚本组清单的顺序列出全部 Chat 脚本，用例在 JSDOM 中执行这些脚本，再调用 `PortalChatWorkspace` 导出的函数或某个模块的工厂并检查真实 DOM。需要找到某个声明的源码时，`findChatWorkspaceFunction` 按名字在入口脚本和全部模块脚本中查找，代码在文件之间移动不影响用例。用例结束时先断开 MutationObserver 再关闭窗口；不等待任何异步操作的用例先等文档载入，让页面的增强器在断开之前登记。`test_chat_workspace_hosts.py` 检查脚本组列出全部模块各一次、入口脚本排在最后，页面不再逐个列出 Chat 脚本，每个页面展开后按组内顺序、以占位元素的加载方式加载全部 Chat 脚本，服务端公开了组内每个脚本。
- **新增模块的登记。** 新模块按载入顺序登记在 `script_groups.json` 的 `chat-workspace` 组中，并在入口脚本开头的模块注释中写明职责。页面、原生视图的资源清单、测试框架的沙盒帧和服务端的公开静态资源清单都从这一组取得脚本，不再各自列出。入口脚本、全部 `chat_workspace_*.js` 模块和 `script_groups.json` 是一个发布单元（`scripts/portal_release_units.py`），发布其中任一个文件时，新模块、入口脚本和新清单一起到达 Release。
