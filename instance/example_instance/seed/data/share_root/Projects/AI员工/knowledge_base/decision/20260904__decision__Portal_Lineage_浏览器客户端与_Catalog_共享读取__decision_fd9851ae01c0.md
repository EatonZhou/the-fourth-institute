---
kb_id: decision_fd9851ae01c0
type: decision
project: AI员工
created_at: '2026-09-04'
version:
  number: 11
  created_at: '2026-09-04'
  created_by:
    user: ''
    agent: ''
  updated_at: '2026-10-19T08:31:43Z'
  updated_by:
    user: ''
    agent: resident_139519ddd6fa
importance: 9
metaness: 2
ai_generated: false
links:
  source:
  - ${code_root}/management_portal/static/portal_society_service.js
  - ${code_root}/management_portal/static/portal_resident_service.js
  - ${code_root}/management_portal/static/portal_lineage_catalogs.js
  - ${code_root}/management_portal/static/lineage_workbench.js
  - ${code_root}/management_portal/static/hatg_panel.js
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
- **Catalog**：源码中的 `PortalLineageCatalogs`。它在当前页面中缓存选择器用的目录：Resident 目录、用户目录、Codex 运行能力目录、Claude 运行能力目录、模型后端目录和项目目录，同一时刻多处读取时只请求一次。
- **最大缓存年龄**：目录值可以直接复用的时间，默认 15 秒。调用方传入 `maxAgeMs` 可以改变它，传入 `force: true` 可以跳过新鲜值重新读取。

## Lineage 客户端

- **每项 Lineage 修改都是一条具名指令。** 例如修改元数据调用 `UpdateLineage`，在画布上新建节点调用 `CreateResident`，从节点 Fork 调用 `ForkResident`，批量删除选中的对象调用 `ApplyLineageChanges`，组件、连线、运行控制和工作区交接分别调用对应的 Lineage 定义与运行指令（decision_4637ca4a69e2、decision_68215b3a825b）。指令经 `PortalInstructionClient` 提交到 `/api/instructions/execute`（decision_daba6706a788）。
- **图修改默认返回指令结果与重读的 Lineage。** `mutate` 默认在指令完成后用 `GetLineage` 重读，把结果放在 `lineage` 字段中；删除 Lineage、跨 Lineage 移动等明确指定 `reload: false` 的操作直接返回指令结果，布局更新也有独立回执。已提交后重读失败时，错误保留 `committedMutation` 中的指令名和结果，页面据此区分写入成功与刷新失败，不重放已完成的写入。
- **定义修改携带所读版本。** 客户端记录 `GetLineage.result.lineage.revision`；HATG 面板优先使用当前显示的定义版本。组件、连线和 Function Block 的整份配置编辑保留表单读取时的版本，即使保存排队或后台重读，也不把旧草稿配上新版本。`mutate` 把此版本放在调用顶层的 `expected_version`；迁移可按来源与目标 Lineage 传入已经读到的版本映射。未读到版本时不虚构版本。`CreateResident` 新建身份在服务端锁内追加，客户端默认不补定义版本；调用方显式提供 `expected_version` 时仍传递。准备、运行、运行状态及全局 Function Block 操作不使用定义版本前提，布局的 `layout_revision` 也不用于定义比较。
- **冲突只刷新，不重放修改。** 定义操作返回 `instruction.conflict` 后，客户端重新读取相关 Lineage，保留错误码和版本细节，将新图交给页面并提示本次未保存。HATG Inspector 展示重新读取的配置；仍打开的 Function Block 编辑对话框保留原版本，须重新打开后编辑。重读失败时显示刷新失败，原冲突继续以 Promise 拒绝返回。任何冲突都不自动用新版本重试写入。
- **每次修改都带幂等键。** 调用方在参数中提供 `idempotency_key` 时使用它；否则客户端为这次操作生成一个带操作前缀的随机键。页面因网络失败重试同一操作时沿用原键，服务端只执行一次。待投递调用的修改总是使用新键。
- **只读操作直接返回指令结果。** `loadLineage` 读取 `GetLineage` 的 `lineage`；触发路径、组件运行状态、待投递项和跨 Lineage 迁移进度分别使用 `ListTriggerPaths` / `GetTriggerPath`、`GetComponentRuntime`、`ListPendingDeliveries` 与 `GetMoveOperation`。
- **缺少必需 ID 时不发出请求。** 需要 Lineage ID 的方法先去除首尾空白，为空时直接抛出 `TypeError`；`loadNodeMetadata` 同时要求 Lineage ID 与 Resident ID。
- **画布坐标与碎片也是指令。** `updateLayout` 调用 `UpdateLineageLayout`：坐标以后写为准，不携带定义版本，也不重新读取整张图，响应里的 `layout_revision` 更新本地布局计数。`saveFragments` 通过 `mutate` 调用 `UpdateLineage` 的 `changes.fragments`，像其他定义修改一样携带所读 revision，冲突时重读。`listLineages` 与 `loadNodeMetadata` 读取对应的页面接口。
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

- **每个目录有固定的读取来源。** `residents` 默认调用 `PortalResidentService.list`（调用方可以提供等价的 `load` 函数），不同查询条件分别缓存；`users` 读取 `/api/users`；`runtimeCatalog(accountId)` 从 `/api/agent-workbench/agent-runtime-catalog` 按 `account_id` 读取运行能力目录（可选模型与推理强度）；`agentBackends` 读取可选的模型后端；`projectCatalog` 读取 `/projects/api/projects`。
- **缓存按当前登录用户分开。** 缓存键由小写用户名、目录种类和读取地址组成，没有登录名时使用 `anonymous`。换一个用户登录后不会读到前一个用户的目录；每个 HTTP 请求仍由服务端重新鉴权。
- **新鲜值直接复用，同一目录同时只读取一次。** 值的年龄不超过最大缓存年龄时直接返回；同一键已有进行中的请求时，后来的调用等待同一个请求。`force: true` 跳过新鲜值，但仍复用进行中的请求。
- **每个调用方得到独立副本。** 写入缓存前和返回给调用方时都复制一次（优先 `structuredClone`，否则 JSON 复制），调用方修改返回值不影响缓存和其他调用方。
- **失败不留在缓存中。** 读取失败时，若该键的条目仍指向这个请求就删除它，下一次调用重新请求；失败以 Promise 拒绝返回，Catalog 不把失败变成空目录。是否显示错误或改用空结果由页面决定。
- **页面可以让目录失效。** `invalidate()` 清空全部缓存；传入一个或多个目标时，删除缓存键、目录种类或读取地址与之相同的条目。失效只影响浏览器中的副本。

Catalog 的 `normalizeRuntimeCatalog` 统一整理运行能力目录。`routeRuntimeCatalog(route, onLoaded)` 使用 route 已有的模型信息；缺少时按其账号读取目录并在完成后通知视图。`populateBackends` 填充后端下拉框并保留仍然有效的当前值；`renderForkProvider` 为 Fork 表单投影后端和账号候选。辅助函数同样须遵守本节的身份隔离与缓存失效边界，不能跨账号套用另一 Provider 的能力。

## 页面接入

Society Workbench 与 HATG 面板通过 Catalog 读取 Resident、用户、项目、运行能力和模型后端目录；HATG 延迟加载时，页面已经读取的目录直接复用。Chat 工作区与 Workbench Studio 只使用 Lineage 客户端读取和修改 Lineage。

Catalog 只保存在当前页面的内存中，不写入浏览器存储，也不订阅服务端事件；刷新页面、显式失效或超过最大缓存年龄后重新读取。目录内容可能稍有过期，只影响选择器中显示的候选；用户提交操作时，服务端再次检查目标是否存在、状态和权限。
