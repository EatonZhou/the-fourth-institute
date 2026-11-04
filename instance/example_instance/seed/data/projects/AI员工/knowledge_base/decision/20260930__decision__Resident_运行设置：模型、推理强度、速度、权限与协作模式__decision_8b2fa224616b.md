---
kb_id: decision_8b2fa224616b
type: decision
project: AI员工
created_at: '2026-09-30'
version:
  number: 34
  created_at: "2026-09-30T11:40:00Z"
  created_by:
    user: "weihao"
    agent: "resident_57f585eb8ae9"
  updated_at: "2026-11-04T19:19:33Z"
  updated_by:
    user: ""
    agent: "resident_6199a7a36480"
importance: 8
metaness: 2
ai_generated: false
links:
  source:
  - /opt/tfi/the-fourth-institute-dev/code/management_portal/static/portal_chat_workspace.js
  - /opt/tfi/the-fourth-institute-dev/code/management_portal/static/portal_lineage_catalogs.js
  - /opt/tfi/the-fourth-institute-dev/code/management_portal/execution/resident_runtime_settings.py
  - /opt/tfi/the-fourth-institute-dev/code/management_portal/execution/codex_runtime_settings.py
  - /opt/tfi/the-fourth-institute-dev/code/management_portal/execution/slash_commands.py
  - /opt/tfi/the-fourth-institute-dev/code/management_portal/society_service/resident_runtime_domain.py
  - /opt/tfi/the-fourth-institute-dev/code/management_portal/society_service/resident_settings_contract.py
  - /opt/tfi/the-fourth-institute-dev/code/management_portal/society_service/resident_storage.py
  - /opt/tfi/the-fourth-institute-dev/code/management_portal/society_service/state_store.py
  - /opt/tfi/the-fourth-institute-dev/code/management_portal/society_service/resident_conversation_stream.py
  - /opt/tfi/the-fourth-institute-dev/code/tests/cases/test_resident_settings_authority.py
  - /opt/tfi/the-fourth-institute-dev/code/management_portal/execution/provider_catalog.py
  - /opt/tfi/the-fourth-institute-dev/code/management_portal/execution/native_options.py
  - /opt/tfi/the-fourth-institute-dev/code/management_portal/codex_app_server_client.py
  - /opt/tfi/the-fourth-institute-dev/code/management_portal/society_service/isa/residents.py
  - /opt/tfi/the-fourth-institute-dev/code/management_portal/hatg/catalog/resident.py
  - /opt/tfi/the-fourth-institute-dev/code/management_portal/static/portal_resident_runtime_controller.js
  - /opt/tfi/the-fourth-institute-dev/code/management_portal/static/portal_resident_service.js
  - /opt/tfi/the-fourth-institute-dev/code/management_portal/static/portal_resident_menu.js
  - /opt/tfi/the-fourth-institute-dev/code/tests/cases/javascript/resident_runtime_controller.cjs
  related:
  - decision_ccbb1cac24aa
  - decision_f560803947c6
  - decision_d19b9909a674
  - decision_c47a13e8b962
  - decision_5bf5bfbd52cb
  - decision_3dd52c0692c1
  - decision_3948ce9b9443
  - decision_6b9e42f1c8a7
  supersedes: []
  derived_from:
  - decision_f560803947c6
---
# Resident 统一执行配置：模型、Prompt、后端参数与 Harness

每个 Resident 通过统一设置 owner 保存下一轮使用的模型、推理强度、速度、Provider 权限、协作模式，以及能力与 Prompt 配置。用户从聊天运行摘要或 Resident/Family 设置入口进入，按操作目的修改；Agent 通过 GetResidentSettings 与 UpdateResidentSettings 读写同一份有版本的设置（decision_d19b9909a674）。

汇报主动性和质量通过同一设置接口读写，由 Society 拥有独立的 `reporting` 策略，不属于模型或 Harness 参数。

统一规范入口为 decision_ccbb1cac24aa。本篇细化配置字段、能力目录、并发保存与生效时机；会话切换见 decision_c47a13e8b962，上下文装配见 decision_c616d197abbb，聊天区域见 decision_f560803947c6。保存参数、保存能力配置、切换账号和显式重新开始会话分别提交，不把它们混成一次保存。

## 四入口共享 Resident 运行流程

Family 门面 Chat 下拉、Family 门面 Menu/settings、普通 Resident Menu/settings、Resident Chat 输入框底部都通过同一 ResidentRuntimeController 操作运行设置、迁移与重启。Family 两处继续共用成员状态视图；目标为用户选中的成员 resident_id，不以门面身份代替成员。入口只负责挂载、目标、焦点与刷新适配。

调用与责任路径为：四视图 → interface.conversation 的 `portal_resident_runtime_controller.js` → interface.shell 的 `portal_resident_service.js` → 既有 Society ISA / 已鉴权目录读取。控制器按 resident_id 共用设置、目录、权限和刷新状态，统一差量保存与弹窗生命周期；Service 是浏览器 ISA/HTTP 与重启 receipt 的唯一责任方。通用 Workspace action、Harness 与 Prompt 保持既有职责。

- 四入口共用当前 Resident 的权限、设置版本、Provider/账号与能力目录。参数编辑、能力与 Prompt、切换账号、显式重新开始会话按目的分区，独立核验各动作权限；仅有其中一种权限时不要求获得其他操作权限。
- “模型与运行参数”显示目标支持的 Model、Mode、Effort、Speed、Permissions 及 API 参数；“能力与 Prompt”显示 Tools、MCP、Skills、Prompt 注入与业务指令的期望配置、实际观察和生效时机。
- “保存参数”与“保存配置”只提交相对已读取版本的实际变化，携带 settings_revision 作为 expected_revision；从适用的下一轮生效，不换账号、不创建会话、不发起模型整理。冲突时保留用户编辑并重新读取，由调用方合并，不能覆盖较新设置。
- 账号切换表单根据后端 context_options 自动选用可用的完整保留方案；无法完整保留且支持 compact 时，展示摘要交接的范围、可能遗漏细节与工具过程的影响，主按钮显示“交接并切换”。用户无需选择底层策略，点击按钮才提交；选账号本身不执行操作。不支持完整保留或摘要交接时禁止切换，不自动使用 visible 或 fresh。完整保留提交 operation=switch_account、context_strategy=preserve；已说明影响的摘要交接提交 operation=restart、context_strategy=compact。两者默认 wait，准备失败均保留原会话；失败后不得静默降级。更换目标重新计算方案并更新说明，刷新不覆盖编辑。跨 Provider 仍须明确选择目标模型。空白开始只在独立“重新开始会话”入口明确选择。停止策略、失败任务重试不放进账号切换表单。
- “重新开始会话”是独立显式操作。只有用户明确发起 compact/visible（包括已说明影响的交接按钮）才执行模型交接；fresh 清空有效上下文后懒建，preserve 必须验证原生复制或严格历史导入。切换成功不自动重试失败输入，具体失败任务由独立恢复入口处理。
- 提交前再次核验 resident_id、权限、绑定和设置版本。目标变更、关闭或身份失效时旧交互失效；发出的请求保留其原目标，异步结果不套用到新对象。目录刷新保留未提交编辑，关闭已受理操作的面板不取消后台流程。
- 同一 Resident 的入口共享保存进度，提交后读取权威结果刷新其他入口；未知响应保留原请求身份与差量，先核对原回执再重试。会话操作的幂等键、pending receipt、取消与观察由 PortalResidentService 负责，不另建客户端状态机。
- 新入口绑定后立即收到当前快照，包括加载中或上次错误；有效缓存的 load 仍向该调用入口回放状态，不要求等待全局刷新，也不重新请求或重绘其他入口。在途加载按 resident_id 合并，失败退出加载并可重试；销毁后不再向该入口通知或由其 load 发起读取，其他有效订阅继续工作。
- 控制器由原生页面脚本及 Menu/Chat 延迟加载器在使用前加载。弹窗在交互时取得依赖，关闭后恢复当前入口焦点。

定向验证使用生产控制器、Service、共用弹窗、Family 视图及普通入口适配函数；覆盖四入口同目标调用、跨入口刷新、权限组合、停用账号/角色回落、目标切换与关闭、双提交、未知回执复用和取消。测试中的 API/ISA 为隔离替身，不等同真实账号迁移或登录整页验收。架构来源登记于 `resident-runtime-settings` 协议。

### 重启客户端观察

四入口的控制器通过共享客户端 `PortalResidentService` 调用 `RestartSession`（会话重启的规则见 decision_c47a13e8b962）。请求带有幂等键，发出前在当前标签页的 `sessionStorage` 中保存一条待确认记录。

- **响应丢失时先查询，不重复提交。** 超时、断线或 408 都可能发生在服务端已经接受请求之后。客户端读取这个 Resident 的重启状态，只有其中的 `request_id` 等于原幂等键时才采用它；还不能确认时保留原请求，提示“受理结果待确认，正在查询；请勿提交其他迁移。”，每 1.5 秒查询一次，也不接受新的重启请求。用户重试时使用同一个幂等键。403、404 等确定的拒绝直接显示错误。
- **跟踪已接受的重启直到结束。** 状态为 `accepted` 或 `cancelling` 时每 1.5 秒查询同一次重启；`completed`、`failed` 和 `cancelled` 是服务端给出的最终结果。
- **关闭 Session 重启对话框时释放它的观察订阅。** 后台迁移继续执行，重新打开后读取当前迁移状态。
- **每个订阅者得到独立的副本。** 订阅者修改收到的对象不影响其他订阅者；一个订阅者的回调出错只报告它自己的错误，不影响其他订阅者、查询和取消订阅。

## 基本概念

- **Provider**：实际运行 Resident 的 Agent 程序，如 Codex、Claude Code、Kimi Code 和 DeepSeek Harness。每个 Resident 当前绑定一个 Provider 和一个账号（或 Claude Code 的路线）。
- **Harness 结构配置**：`agent_harness` 描述 ExecutionService 使用哪些处理功能、各功能的开关、参数与执行时机。它与模型设置分开保存；其中的 `mode=auto/manual` 不等于 Codex 的 `collaboration_mode=default/plan`，也不表示更换名为 DeepSeek Harness 的 Provider。
- **能力描述**：ExecutionService 为每个 Provider 和账号给出的一份说明，列出它支持哪些设置项、每个模型可选的推理强度和服务等级，以及权限预设的名称。页面和服务端都只按能力描述决定显示和接受哪些设置。
- **模型目录**：某个账号当前可用的模型列表及每个模型支持的推理强度、服务等级和输入类型。Codex 的目录由该账号的 Codex 程序通过 `model/list` 返回；其他 Provider 的目录来自 Portal 中各自的 Provider 目录模块。

各 Provider 支持的设置项：

| Provider | 模型 | 推理强度 | 速度 | 权限 | 协作模式 |
| --- | --- | --- | --- | --- | --- |
| Codex | ✓ | ✓ | ✓ | ✓ | ✓ |
| Claude Code | ✓ | ✓ | | ✓ | |
| Kimi Code | ✓ | | | ✓ | |
| DeepSeek Harness | ✓ | ✓ | | | |

## 设置项

- **Model（模型）**：Resident 使用的模型，只能从当前账号的模型目录中选择。
- **Effort（推理强度）**：模型思考的深度，如 `low`、`medium`、`high`、`xhigh`、`max`。可选值随模型不同。
- **Speed（速度）**：Codex 的服务等级。`Standard (default)` 为普通等级，`Fast` 对应服务等级 `priority`。只有当前模型提供服务等级时才显示这一项。
- **Permissions（权限）**：Resident 在机器上执行命令和改文件的权限预设，由能力描述的 `permission_presets` 列出可选值、`permission_labels` 给出显示名称，未显式选择时显示 `default_permission_label`。当前各 Provider 的预设：

  | Provider | 可选预设（显示名称） | 未选择时显示 |
  | --- | --- | --- |
  | Codex | `default`（Ask）、`auto-review`（Auto-review）、`full-access`（Full Access） | Full Access |
  | Claude Code | `default`（Restricted）、`full-access`（Full Access） | Full Access |
  | Kimi Code | `default`（Manual）、`full-access`（Full Access） | Full Access |

  三个 Provider 未选择预设时都按 `full-access` 运行（ExecutionService 的 `execution/native_options.py` 中各 Provider 的 `default_permission_preset`），所以显示 Full Access；用户需要更严格的权限时显式选择 `default`。
- **Mode（协作模式）**：Codex 的 `collaboration_mode`，取 `default` 或 `plan`。Default 使用 Provider 的普通执行流程，Plan 使用 Provider 内置的先规划后执行流程；未设置时为 Default。

## 设计点

### 所有后端共用的执行配置

每个 Resident 都使用同一个配置 owner 和同一组读写接口，包括 Codex、Claude Code、Kimi Code、DSH 和直接调用模型接口的 `provider_api`。Resident 的 `backend` 选择适配器，`backend_route_id` 选择后端账号或已登记模型 API；后者在 `provider_api` 中就是 Registry 条目 ID。模型只保存在 `execution_settings.model`，不再另设 API 执行配置对象或第二个模型来源。

`execution_settings` 包含模型与执行选项，以及两个受约束结构：

- `prompt_context`：system_prompt 是业务指令，经统一物化成为每轮的 business_instructions；messages 是直接 API 的预置文本上下文，最多 200 条，只接受 system/user/assistant 和文本 content。原生 Agent 不接受该预置 messages 字段；其既有历史由会话与 Fork 上下文 owner 管理。注册或保存这些内容不会执行初始化模型轮次。
- `backend_options`：按后端校验的专用参数。直接 API 支持 `parameters`、`timeout_seconds`（1–86400 秒）、`max_retries`（0–10）。parameters 只接受已声明的采样、输出长度、停止条件、种子、响应格式和 thinking 参数；不得覆盖模型、路由、消息或认证。原生后端目前没有在该结构中登记额外参数，非空未知参数拒绝。

Prompt 注入策略 prompt_injection 决定 Portal 各类上下文的发送时机，prompt_context 保存业务指令与预置内容。capability_config v1 保存 Tools、MCP、Skills 的结构化选择，三者属于 execution_settings，由同一个设置 owner 管理；Harness 继续独立存于 harness_config，额度保留策略仍由调度 owner 管理。

capability_config 的工具和 Skill 使用正式引用；MCP 选择同时指定连接引用及选中的工具名称。MCP 的 tools 字段省略或显式 [] 都表示不选择该连接的工具，不能解释为全部工具。未知字段、重复/非法引用和错误类型拒绝；目录发现结果不自动扩大选择。版本化配置记录期望范围，实际可用、已发送、已接受及调用结果分别记录，不把保存成功当作装配成功。

hatg_instruction 的参数定义与执行处理由正式 ISA 接口拥有；Markdown 可以强调和说明其使用方式。Prompt 开关不隐含修改 Tools/MCP 选择，Skill 的选择不等于正文已读取，也不安装软件或授予资源权限。

直接 API 请求从同一份当前配置取模型和参数，业务指令通过统一 SystemInstructions 传输。预置消息之后始终追加本轮输入；隔离调用跳过预置 messages，保留本轮输入和业务指令。执行请求中的配置只是提交时快照。业务调用者只在首次登记时提供配置，后续调用不能覆盖已登记 Resident 的当前选择。

所有配置写入拒绝未知字段、非法类型和后端不支持的能力。页面和 HATG 的 GetResidentSettings / UpdateResidentSettings 使用同一份配置；CreateResident 的 provider_api 通过 account 指定已登记的模型 API。模型目录同时提供直接 API 的模型与能力。存储中不保留 `execution_profile_json` 或未被执行路径消费的 `portal_handler`。

### 唯一设置来源与 Harness 边界

- **PostgreSQL 当前设置 owner 是唯一可写来源。** execution_settings_json 保存执行、capability_config 与 Prompt 配置；harness_config_json 保存 Harness 结构，统一通过 resident_execution_configuration 管理。GetResidentSettings、UpdateResidentSettings、页面、斜杠命令、Fork 和执行使用同一来源。
- **版本校验覆盖完整配置。** GetResidentSettings 返回 settings_revision；UpdateResidentSettings 接收 expected_revision，过期提交拒绝并要求重新读取合并。旧调用省略该参数仅作兼容，新界面保存必须携带已读版本；不能以普通 Resident 状态保存绕过配置 CAS。
- **执行与 Harness 字段受约束。** execution_settings 包含模型、推理强度、速度、审批/沙箱、协作模式、子 Agent、业务指令、Prompt 注入策略及 capability_config v1；harness_config 保存 agent_harness 的模式、功能开关、参数与时机。未知字段和未登记功能在写入时拒绝。
- **Provider 身份与配置一起提交。** backend 选择 Provider，backend_route_id 指向稳定账号或已登记模型 API；当前配置不保存独立可写的 Provider 副本。换账号走明确的切换操作，包括 Model API 路线切换，不能借参数保存静默替换执行账号。
- **runtime_settings 是读取投影。** Resident 顶层模型字段、旧副本、Conversation 状态和 runtime/settings.json 都不拥有当前设置。首次注册可暂存完整候选，提交后由数据库接管；执行请求及通知只保存当时的快照或回执。
- **普通状态保存不覆盖配置。** 即使调用方持有旧内存 record，也必须保留数据库中较新的参数、能力、Prompt 与 Harness 设置。
- **Fork 与会话切换使用完整配置边界。** Fork 从当前权威配置生成独立初值；切换候选只暂存目标配置，验证源绑定与设置版本后才提交。Tools/MCP/Skills、Prompt 和 Harness 配置保持完整；不兼容时明确拒绝，不静默恢复默认值。

### 摘要与弹窗

四入口共用读取、并发保存与状态刷新；参数、能力与 Prompt、切换账号及重新开始会话按目的展示。会话候选遵守 decision_c47a13e8b962，准备中的值不能冒充当前已生效配置；没有原生会话时配置仍可保存，首条真实输入才触发模型执行。

运行参数、能力配置、账号切换和重新开始会话在同一张尺寸稳定的卡片内切换标签，标题、标签栏及底部操作保持可见，长内容仅在中间区域滚动。切换标签保留未保存编辑。关闭按钮、取消、Escape 与点击卡片外区域均可退出并恢复页面交互；关闭页面不取消已受理的操作。模型与运行参数区不显示绑定诊断；保留非主要的刷新操作，能力生效明细默认折叠。

- **`/fast` 回复只显示开启或关闭的确认及生效说明。** 回复不追加 Session、Backend、Model、Reasoning effort、Fast mode、Permissions 配置列表；当前设置在输入框下方的摘要、Runtime Settings 弹窗和 `/status` 中查看。
- **运行摘要显示已保存参数，点击进入按目的组织的控制面板。** 摘要仅显示当前 Provider 支持的模型、推理强度、Fast、权限，Plan 时追加 Plan；能力与 Prompt 生效状态在其独立区域查看。
- **Family 成员表每行只有一个运行设置入口。** 统一按钮内保留 Backend／账号和推理强度／Fast 两行信息，只显示一个下拉提示；鼠标与键盘使用同一个入口，关闭后焦点返回该成员按钮。Chat 弹层与 Family Menu 共用成员表，读取、差量保存、权限分流、错误重试及迁移中禁用均采用“四入口共享 Resident 运行流程”，目标始终是所选成员。
- **弹窗只显示能力描述声明的设置项。** 当前模型没有服务等级候选时隐藏 Speed；Provider 没有声明协作模式时既不显示也不接受它。
- **斜杠命令菜单由 Provider 的声明推出。** `AgentProvider.native_commands` 按声明决定菜单里有哪些原生命令：`/model`、`/fast`、`/permissions`、`/plan` 分别要求能力描述的 `runtime_settings` 含 `model`、`service_tier`、`permissions`、`collaboration_mode`；`/compact` 要求 `operations` 含 `compact`；`/goal` 要求 `native_goals`；`/status` 总是提供。按当前声明，Codex 提供全部七条，Claude Code 与 Kimi Code 提供 `/status`、`/model`、`/permissions`、`/compact`，DSH 提供 `/status`、`/model`、`/compact`。用户仍然输入了没有声明的命令时，Portal 回复“该 Provider 没有这条命令”，不把它当作提示词交给模型。此前菜单按 Provider 名称写死，Claude Code、Kimi Code 和 DSH 也会显示无法执行的 `/fast`、`/goal`、`/permissions`。
- **`/status` 列出 Provider、设置和 token 统计。** 每个 Provider 都先列出 Provider 名称。Codex 接着列出模型、推理强度、Fast 和权限；Claude Code、Kimi Code 和 DSH 接着列出路线（接受路线的 Provider）、模型、推理强度（运行设置含 `reasoning_effort` 的 Provider）和权限（运行设置含 `permissions` 的 Provider，显示该 Provider 的权限名称）。两者都在后面附上与 `/portal-tokens` 相同的 token 统计（上一轮用量，即这次执行计入的全部请求之和，decision_f1331d57be89）。
- **`/permissions` 查看或切换 Provider 自己的权限预设。** 不带参数时，回复当前权限名称和可选预设（例如 Claude Code 的 `default`（Restricted）、`full-access`（Full Access），Kimi Code 的 `default`（Manual）、`full-access`（Full Access））；`/permissions <预设>` 由能管理该会话权限的用户切换，与 Runtime Settings 弹窗中的 Permissions 写入同一个设置，后续 turn 生效。Codex 的 `/permissions` 另外提供它自己的三种预设。2026-10-11 之前，Claude Code 和 Kimi Code 的 `/permissions` 只回复“由 Resident owner 关系管理”，既不显示真实预设，也不能切换。
- **菜单只来自会话合同。** Chat 输入框只列 `composer.slash_commands` 中的命令，前端不再内置命令清单；给出空列表的会话（Family 门面）和没有给出列表的 Chat（Resident 以外的 Chat、聊天室）不显示菜单。`/portal-help` 按当前 Provider 的声明列出“当前会话命令”和 Portal 私有命令，标题不写 Provider 名称（weihao 2026-10-16）；未知命令的回复列出当前会话支持的命令。Model API Resident 没有 Provider 声明的命令，只有 Portal 私有命令。
- **Fast 只表示是否使用加速服务等级。** 页面显示 `Fast on` 或 `Fast off`，不显示 `1x`、`2x` 之类的倍率：实际速度和额度消耗随模型、账号和计费方式变化，Portal 没有可靠的统一倍率。
- **能力描述声明了 Full Access，用户就可以选择它。** Portal 不另按管理员或 owner 身份限制；Resident 实际能读写的文件和资源仍受 Portal 授权检查约束。

### 候选值与默认值

- **模型和推理强度的候选值来自当前账号的模型目录。** Codex 目录按页读取 `model/list`，按账号短期缓存，不合并其他账号的缓存。目录读取失败时，页面报告错误，再次打开弹窗会重新读取；页面不会用旧列表冒充读取成功。
- **Portal 的默认模型和推理强度独立于 Provider 的推荐值。** Codex 的内置默认为 `gpt-6.1-sol` 和 `high`，部署可以用环境变量 `PORTAL_CODEX_DEFAULT_MODEL`、`PORTAL_CODEX_DEFAULT_REASONING_EFFORT` 覆盖。Resident 的设置为空时继承这两个值；Provider 目录中的推荐模型、推荐强度和列表首项都不覆盖它们。
- **推理强度为空时，页面显示执行实际会用的强度。** 目录为每个模型给出 `default_efforts_by_model`：所选模型支持 Portal 默认强度时就是它，不支持时是执行层为该模型解析出的回退强度。摘要和弹窗按所选模型取这个值显示，不显示 Provider 的推荐强度，也不对所有模型套用同一个默认值。
- **弹窗回填已保存的值，即使目录里暂时找不到它。** 目录缺少当前模型时，弹窗保留原模型和推理强度，并在控件的悬停提示中说明“Provider 目录未返回此模型的能力信息”；页面不会自动换成其他模型或降低强度。已保存的 Fast 在当前模型不支持时显示为“Fast (saved; unavailable)”。
- **只校验用户这次改动的模型或推理强度。** 用户只修改协作模式等其他项时，已保存的模型即使不在目录中也不受影响。 仅修改 Harness、协作模式或权限等非模型字段时，服务端只做本地字段与能力校验，不读取 Codex 模型目录、不要求已登记 Codex 账号；非法 Harness 配置仍拒绝。Codex 命令适配器先识别命令，只在 `/model` 读取账号目录；Portal 私有命令、普通文本与文件路径继续交给原有受理链，`/status`、`/goal` 等本地状态操作不依赖登录。已有 Session 的账号绑定保持不变。切换模型时保留原推理强度；新模型不支持它时，要求用户明确选择，页面不自动套用推荐值或第一个可选值。通过 `UpdateResidentSettings` 只改模型时，Claude、Kimi、DeepSeek Harness、OpenCode 同样拒绝，错误中列出新模型可选的推理强度（空值表示 Provider 默认），调用方连同推理强度一起再提交。
- **已保存设置的执行翻译不重新发现动态模型目录。** 原生适配器把已保存 model、effort 和权限参数翻译到真实调用；每轮 turn、会话创建与 release 不为验证模型额外查询 auth/config、创建目录探测空会话或等待目录新鲜。模型为空时沿各 Provider 的原生默认选择，Codex 继续使用本篇明确的 Portal 默认值。设置编辑、显式跨 Provider 切换及主动目录刷新保留原校验；实际认证、模型不可用、余额不足与限流错误由真实调用返回并沿执行失败处理。

- **目录刷新不覆盖用户尚未保存的选择。**

### 何时生效

- **保存只更新期望配置。** 不创建模型会话、初始化任务或“请整理确认”的隐藏输入；正在执行的一轮继续使用已提交快照。参数与支持续轮更新的配置在适用的下一轮生效。
- **注入时机按各部分合同执行。** 首轮专用 Prompt 在当前会话已启动后修改，显示“后续新会话生效”；Provider 支持续轮更新时记录实际发送位置和接受结果。不能通过保存宣称旧 system prompt 已被替换。
- **能力变化不自动重建会话。** 能热更新的部分记录发送/接受事实；只支持建会话时装配的部分显示“待显式重新开始会话”。配置缺记录或工具名称缺失不触发自动 compact 或重建。
- **设置通知不改变本轮执行快照。** 需要发出的参数变更通知只说明后续行为，不隐含再次执行、重启或失败任务重试；重复保存同值不重复通知。协作模式明确为 next_turn，保持原有 requested=false/status=next_turn 回执。
- **保存失败或冲突保留编辑。** 页面区分期望选择、当前实际情况与生效时机；关闭 Prompt 不从已有历史中抹除已送达内容。
- **选择 Plan 只改变 Provider 的执行流程。** Provider 权限预设与 Portal 资源授权分别保持原语义，不自动提问。

## 实现

### 当前设置的读写路径

```text
页面 / 斜杠命令 / GetResidentSettings / UpdateResidentSettings
        │
        ▼
SocietyService Resident 设置 owner：鉴权、能力校验与归一
        │
        ▼
PostgreSQL resident_execution_configuration
        ├─ execution_settings_json：模型、调用策略、Prompt 与 capability_config v1
        ├─ harness_config_json：Harness 结构
        └─ reporting_json：Society 汇报策略，独立于 Execution 参数
        │  settings_revision / expected_revision 条件提交
        ▼
当前设置读取投影 → 参数/能力面板与摘要 / ExecutionRequest 时点快照
```

实现入口为 `resident_runtime_domain.read_resident_runtime_settings` 与 `write_resident_runtime_settings`，分别调用存储层的 `read_resident_execution_settings` 与 `write_resident_execution_settings`。`resident_settings_contract` 负责有限字段的拆分、组合与候选暂存；`execution.resident_runtime_settings` 负责通用归一，`execution.resident_configuration` 定义 Prompt 与后端专用参数约束，`execution.slash_commands` 解释命令。

全记录保存通过 catalog.upsert 与 _save_resident_record 保留现有配置；首次注册写入初值，后续只有设置 owner 或会话候选的明确事务提交可修改当前配置。设置更新按 expected_revision 比较；会话操作同时检查源绑定与配置 revision，完整提交 Provider、账号、execution_settings 与 harness_config。事务失败保持原状态，不能用候选快照覆盖并发保存。普通状态写入不获得配置写权限。

### 读取能力与目录

```text
Runtime Settings 弹窗 / /model 命令 / UpdateResidentSettings
        │
        ▼
SocietyService：按调用者权限读取 Resident，解析它当前的 Provider 与账号
        │
        ▼
ExecutionService：返回该 Provider 与账号的能力描述和模型目录
        │  （Codex：codex_runtime_settings.codex_runtime_catalog → 账号的 model/list）
        ▼
弹窗显示候选；保存时由 Provider 适配器逐项校验
```

Portal Web 通过已鉴权 SocietyService 接口读取当前 Provider、稳定账号、绑定及设置版本，再向 ExecutionService 查询能力目录；不直接连接 Resident 运行端口。有会话时核对正式绑定，无会话时使用当前设置 owner 的登记路线；不以旧内存字段替代权威状态。

页面、斜杠命令与 HATG 使用同一设置及校验：适配器验证字段、权限预设、模型目录和能力选择。页面只提交实际改动，携带已读 settings_revision；跨 Provider 切换必须提交明确选择的模型。Provider 名称用于选择适配器和诊断，控件由能力声明决定。

创建 Resident 的模型与推理强度选择使用同一目录；普通注册可使用已声明的初始默认值，不由此启动模型。跨 Provider 切换必须显式选择目标模型，目录读取失败时禁止切换，不以“Portal 默认模型”替代用户选择。

### 设置怎样进入下一轮

构造执行请求时从唯一设置 owner 读取完整配置，校验 capability_config 引用并形成当轮工具/MCP/Skill 与 Prompt 装配计划；Harness 结构进入 runtime_options.agent_harness。记录配置版本、实际发送位置及接受证据。该请求只是一轮快照，不能替代之后的权威设置读取，也不能把 Provider 未确认的能力标记为已生效。

Codex 开始新一轮时，Provider 适配器把协作模式、模型和推理强度写入 `turn/start` 请求的 `collaborationMode`，并把 `settings.developer_instructions` 设为 `null`，让 Codex 使用自己内置的模式说明。从 Plan 切回 Default 时同样显式传 `default`，不依赖 Codex 线程沿用上一轮的模式。Resident 不需要调用任何 Portal 接口来切换模式。


## 汇报主动性、质量与门面策略

`settings.reporting` 与 `changes.reporting` 使用完整对象 `{initiative, quality}`。主动性 `off|low|medium|high` 与质量 `low|medium|high` 独立设置，缺省为 off/medium。Society 保存 `resident_execution_configuration.reporting_json`，统一版本校验覆盖本次可读运行参数、汇报策略及是否已配置。非法值和未知字段拒绝；显式保存默认值同样记录为配置。页面保存冲突时保留草稿，回读最新值，由用户选择采用最新内容或按新版本重试，不静默覆盖。

Family 门面拥有自身汇报策略。调用者只需门面 viewer/contributor 即可读取或保存该策略；Router 模型参数另按原有权限核验，不因策略授权而泄露。没有 Router 读取权限时返回可读的 reporting，并明确运行设置与 Prompt 注入观察 unavailable。`reporting_effective` 保存来源 Resident、resident/family_facade 来源类型、known/unknown、configured 与 next_input 生效说明，默认不能冒充显式配置。

执行成员依据已核验的持久转交链取得最外层门面在本次输入上的策略。直接成员输入使用其自身策略，不向成员批量复制门面配置。每条主输入与追加输入都由 Society 重新物化策略提示；已送达内容不重写，保存不重启模型、不生成报告、不发起审查。该业务约束独立于可选 message envelope 和 pure 注入预设；Execution 接收物化后的提示，不解释主动性、报告质量或审核业务。Home 上下文只提供能力与展示合同，不覆盖执行时的权威策略。

主动性 off 关闭额外主动汇报，保留普通答复、任务交付与用户显式请求。低档仅在重要成果、风险或决策有汇报价值时考虑；中档在实质成果和解释能帮助理解时考虑；高档更积极判断信息增量与用户需要，仍不要求每次请求生成。明确的格式、取消及展示限制优先。汇报质量低使用 Markdown，中使用现有交互网页模板，高由独立 Resident 检查并修改复审至达标；模板、媒体与审查结果由各业务 owner 保存，不能将提示注入当成作品已经通过审查。

新增 nullable reporting_json 列由 Society 正式升级入口处理。未升级的读取按默认 off/medium、configured=false 解释，无汇报字段的普通设置写入继续工作；显式保存汇报策略缺列时返回真实错误，不能声称配置成功。非法非空值不默认化。`scripts/migrate_resident_reporting.py` 默认事务预演回滚，`--apply` 才提交；使用初始化锁和有界锁等待，按本次增列需要的表、字段和类型核对，不依赖描述性 schema 标签相等。列已存在且类型合法时不再 ALTER，不重写已有策略。旧安装转换与新安装 seed 使用同一 owner 结构，实例特殊策略通过正式 UpdateResidentSettings 设置，不硬编码本站 AMA 身份。
