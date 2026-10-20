---
kb_id: decision_8b2fa224616b
type: decision
project: AI员工
created_at: '2026-09-30'
version:
  number: 23
  created_at: '2026-09-30T11:40:00Z'
  created_by:
    user: tfi-exported-user
    agent: resident_57f585eb8ae9
  updated_at: '2026-10-19T08:25:13Z'
  updated_by:
    user: ''
    agent: resident_511e229bc8c3
importance: 8
metaness: 2
ai_generated: false
links:
  source:
  - ${code_root}/management_portal/static/portal_chat_workspace.js
  - ${code_root}/management_portal/static/portal_lineage_catalogs.js
  - ${code_root}/management_portal/execution/resident_runtime_settings.py
  - ${code_root}/management_portal/execution/codex_runtime_settings.py
  - ${code_root}/management_portal/execution/slash_commands.py
  - ${code_root}/management_portal/society_service/resident_runtime_domain.py
  - ${code_root}/management_portal/society_service/resident_settings_contract.py
  - ${code_root}/management_portal/society_service/resident_storage.py
  - ${code_root}/management_portal/society_service/state_store.py
  - ${code_root}/management_portal/society_service/resident_conversation_stream.py
  - ${code_root}/tests/cases/test_resident_settings_authority.py
  - ${code_root}/management_portal/execution/provider_catalog.py
  - ${code_root}/management_portal/execution/native_options.py
  - ${code_root}/management_portal/codex_app_server_client.py
  - ${code_root}/management_portal/society_service/isa/residents.py
  - ${code_root}/management_portal/hatg/catalog/resident.py
  - ${code_root}/management_portal/static/portal_resident_runtime_controller.js
  - ${code_root}/management_portal/static/portal_resident_service.js
  - ${code_root}/management_portal/static/portal_resident_menu.js
  - ${code_root}/tests/cases/javascript/resident_runtime_controller.cjs
  related:
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

每个 Resident 都有一份自己的**运行设置**，决定它下一次工作时用哪个模型、思考得多深、是否使用加速服务、在机器上有多大操作权限，以及按普通流程还是先做规划。用户在聊天输入框下方看到当前设置的摘要，点击后在 **Runtime Settings** 弹窗中修改；Agent 通过 HATG 指令 `GetResidentSettings` 和 `UpdateResidentSettings` 读写同一份设置（decision_d19b9909a674）。

本 Design 说明这些设置项是什么、各 Provider 支持哪些、候选值从哪里来、默认值怎样确定，以及修改后何时生效。聊天区域本身见 decision_f560803947c6；更换 Resident 的账号或 Provider 属于 Session 迁移，见 decision_c47a13e8b962。

## 四入口共享 Resident 运行流程

Family 门面 Chat 下拉、Family 门面 Menu/settings、普通 Resident Menu/settings、Resident Chat 输入框底部都通过同一 ResidentRuntimeController 操作运行设置、迁移与重启。Family 两处继续共用成员状态视图；目标为用户选中的成员 resident_id，不以门面身份代替成员。入口只负责挂载、目标、焦点与刷新适配。

调用与责任路径为：四视图 → interface.conversation 的 `portal_resident_runtime_controller.js` → interface.shell 的 `portal_resident_service.js` → 既有 Society ISA / 已鉴权目录读取。控制器按 resident_id 共用设置、目录、权限和刷新状态，统一差量保存与弹窗生命周期；Service 是浏览器 ISA/HTTP 与重启 receipt 的唯一责任方。通用 Workspace action、Harness 与 Prompt 保持既有职责。

- 每次打开运行设置或迁移弹窗时重新读取权限和当前 Backend/账号。仅可编辑设置时不提供重启；仅可重启时直接进入迁移流程，不额外调用 GetResidentSettings；无权限、加载、提交或迁移中入口不可用。账号停用与角色回落信息使用同源数据展示。
- 保存只提交相对于打开时初值的实际字段变化，按对象内容比较；保存从下一轮生效，不隐式重启。需要迁移时用户显式进入 Session 迁移。尚无 Session 时沿服务端配置模式处理。
- 提交前再次校验 resident_id 对应的权限和账号绑定。目标切换、关闭、刷新失效或迁移开始均使旧交互失效；等待新目标投影期间阻止旧入口打开和写入。已发出的请求保留其原目标，不将异步结果套用到新目标。
- 同一 Resident 的入口共享保存进度；保存后失效并读回供其他入口刷新。未知设置响应保留原请求身份与差量，相同重试复用该身份；读回确认设置已提交后消除未确认标记。
- 重启的幂等键、pending receipt、未知受理回读、取消与观察仍完全由 PortalResidentService 负责。控制器只串行发起语义操作，防止双击新建第二次请求，不另建重启状态机。
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
- **模型目录**：某个账号当前可用的模型列表及每个模型支持的推理强度、服务等级和输入类型。Codex 的目录由该账号的 Codex 程序通过 `model/list` 返回，再加上 Portal 明确列出的已实测补充模型；其他 Provider 的目录来自 Portal 中各自的 Provider 目录模块。

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

- `prompt_context`：`system_prompt` 是 Resident 的业务指令，经 Portal 统一物化为每轮的 `business_instructions` system part；`messages` 是直接 API 调用的预置文本上下文，最多 200 条，只接受 system/user/assistant 的 role 与文本 content。原生 Agent 后端不接受预置结构化 messages，已有会话上下文仍由初始化与 Fork 领域负责。
- `backend_options`：按后端校验的专用参数。直接 API 支持 `parameters`、`timeout_seconds`（1–86400 秒）、`max_retries`（0–10）。parameters 只接受已声明的采样、输出长度、停止条件、种子、响应格式和 thinking 参数；不得覆盖模型、路由、消息或认证。原生后端目前没有在该结构中登记额外参数，非空未知参数拒绝。

Prompt 注入策略 `prompt_injection` 决定 Portal 各类上下文的时机，业务 `prompt_context` 保存业务指令与预置上下文；两者分别承担策略和内容职责。Harness 继续独立存于 `harness_config`，额度保留策略例外继续由调度领域管理。

直接 API 请求从同一份当前配置取模型和参数，业务指令通过统一 SystemInstructions 传输。预置消息之后始终追加本轮输入；隔离调用跳过预置 messages，保留本轮输入和业务指令。执行请求中的配置只是提交时快照。业务调用者只在首次登记时提供配置，后续调用不能覆盖已登记 Resident 的当前选择。

所有配置写入拒绝未知字段、非法类型和后端不支持的能力。页面和 HATG 的 GetResidentSettings / UpdateResidentSettings 使用同一份配置；CreateResident 的 provider_api 通过 account 指定已登记的模型 API。模型目录同时提供直接 API 的模型与能力。存储中不保留 `execution_profile_json` 或未被执行路径消费的 `portal_handler`。

### 唯一设置来源与 Harness 边界

- **已登记 Resident 的当前设置以 PostgreSQL 为唯一权威。** 模型执行配置与 Harness 结构分别保存在 `resident_execution_configuration` 的 `execution_settings_json` 和 `harness_config_json`，由同一个 Resident 设置 owner 提供读写。`GetResidentSettings`、`UpdateResidentSettings`、页面、斜杠命令、Fork 继承和实际执行都使用这一个当前来源。
- **执行配置与 Harness 结构分开。** 执行配置包括模型、推理强度、服务等级、权限与审批、沙箱、初始指令、子 Agent 开关和协作模式；Harness 结构保存 `agent_harness` 的模式、已登记功能开关、参数与时机。未知设置字段、未知功能或未登记参数在写入时拒绝；两者均为受约束结构，不能用任意 JSON 扩展。
- **Provider 身份与配置保持一致。** 当前 Provider 的身份来自 Resident 的明确 `backend` 字段；设置读取时组合这个身份，不在配置 JSON 再维护一个可独立修改的 Provider 副本。原生后端更换 Provider 或账号遵循 Session 迁移规则；Model API 在同一后端内切换已登记 API 路线可通过 UpdateResidentSettings 的 backend_route_id 从下一轮生效，跨后端切换仍遵循 decision_c47a13e8b962。
- **`runtime_settings` 是读取投影。** 它由执行配置与 Harness 结构组合产生，用于 API、页面和执行请求；Resident 顶层的模型字段、旧 `runtime_settings` 副本及 Conversation 状态不构成另一份可写当前真值。Conversation 可以发布带时点的设置变更回执，当前设置仍从数据库 owner 读取。
- **`runtime/settings.json` 不决定 Resident 的当前设置。** 已登记 Resident 不用该文件读取、覆盖或回退当前设置；通用文件读写工具可以服务其他独立调用者，但不能成为 Resident 的第二个设置入口。首次登记的候选可以暂存归一后的 `execution_settings` 与 `harness_config`，登记提交后由数据库接管。
- **执行快照有明确时点。** 某次 ExecutionRequest 可以保存实际提交时的配置；它用于说明这一轮如何运行，不回写为新的当前设置。普通运行状态、Workspace 或图布局保存由存储层保留数据库已有的当前配置与 backend；即使调用者持有旧内存 record，也不能覆盖用户刚保存的 manual 模式或 disabled 功能。
- **Fork 与 Restart 使用明确的配置边界。** Fork 从来源当前数据库设置生成新 Resident 的初始配置，之后双方独立修改（decision_5bf5bfbd52cb）。Restart 的候选配置在准备阶段只是候选快照；Provider 与账号迁移成功提交时才成为当前配置，不在准备期间提前覆盖活动设置（decision_c47a13e8b962）。

### 摘要与弹窗

四入口的设置读写、保存刷新、迁移与重启流程统一见本篇“四入口共享 Resident 运行流程”；本节只规定摘要与控件的呈现。原生迁移按 decision_c47a13e8b962 提交，待确认候选不覆盖活动设置；配置模式与原生 Session 模式保持该合同的区别。

- **`/fast` 回复只显示开启或关闭的确认及生效说明。** 回复不追加 Session、Backend、Model、Reasoning effort、Fast mode、Permissions 配置列表；当前设置在输入框下方的摘要、Runtime Settings 弹窗和 `/status` 中查看。
- **聊天输入框下方显示一行运行设置摘要，点击打开 Runtime Settings 弹窗。** 摘要只列出当前 Provider 支持的项：模型、推理强度、`Fast on` 或 `Fast off`、权限名称；协作模式为 Plan 时追加 `Plan`，为 Default 时省略。
- **Family 成员表每行只有一个运行设置入口。** 统一按钮内保留 Backend／账号和推理强度／Fast 两行信息，只显示一个下拉提示；鼠标与键盘使用同一个入口，关闭后焦点返回该成员按钮。Chat 弹层与 Family Menu 共用成员表，读取、差量保存、权限分流、错误重试及迁移中禁用均采用“四入口共享 Resident 运行流程”，目标始终是所选成员。
- **弹窗只显示能力描述声明的设置项。** 当前模型没有服务等级候选时隐藏 Speed；Provider 没有声明协作模式时既不显示也不接受它。
- **斜杠命令菜单由 Provider 的声明推出。** `AgentProvider.native_commands` 按声明决定菜单里有哪些原生命令：`/model`、`/fast`、`/permissions`、`/plan` 分别要求能力描述的 `runtime_settings` 含 `model`、`service_tier`、`permissions`、`collaboration_mode`；`/compact` 要求 `operations` 含 `compact`；`/goal` 要求 `native_goals`；`/status` 总是提供。按当前声明，Codex 提供全部七条，Claude Code 与 Kimi Code 提供 `/status`、`/model`、`/permissions`、`/compact`，DSH 提供 `/status`、`/model`、`/compact`。用户仍然输入了没有声明的命令时，Portal 回复“该 Provider 没有这条命令”，不把它当作提示词交给模型。此前菜单按 Provider 名称写死，Claude Code、Kimi Code 和 DSH 也会显示无法执行的 `/fast`、`/goal`、`/permissions`。
- **`/status` 列出 Provider、设置和 token 统计。** 每个 Provider 都先列出 Provider 名称。Codex 接着列出模型、推理强度、Fast 和权限；Claude Code、Kimi Code 和 DSH 接着列出路线（接受路线的 Provider）、模型、推理强度（运行设置含 `reasoning_effort` 的 Provider）和权限（运行设置含 `permissions` 的 Provider，显示该 Provider 的权限名称）。两者都在后面附上与 `/portal-tokens` 相同的 token 统计（上一轮用量，即这次执行计入的全部请求之和，decision_f1331d57be89）。
- **`/permissions` 查看或切换 Provider 自己的权限预设。** 不带参数时，回复当前权限名称和可选预设（例如 Claude Code 的 `default`（Restricted）、`full-access`（Full Access），Kimi Code 的 `default`（Manual）、`full-access`（Full Access））；`/permissions <预设>` 由能管理该会话权限的用户切换，与 Runtime Settings 弹窗中的 Permissions 写入同一个设置，后续 turn 生效。Codex 的 `/permissions` 另外提供它自己的三种预设。2026-10-11 之前，Claude Code 和 Kimi Code 的 `/permissions` 只回复“由 Resident owner 关系管理”，既不显示真实预设，也不能切换。
- **菜单只来自会话合同。** Chat 输入框只列 `composer.slash_commands` 中的命令，前端不再内置命令清单；给出空列表的会话（Family 门面）和没有给出列表的 Chat（Resident 以外的 Chat、聊天室）不显示菜单。`/portal-help` 按当前 Provider 的声明列出“当前会话命令”和 Portal 私有命令，标题不写 Provider 名称（TFI portal 2026-10-16）；未知命令的回复列出当前会话支持的命令。没有 Provider 会话的 Resident（Portal 规则）只有 Portal 私有命令。
- **Fast 只表示是否使用加速服务等级。** 页面显示 `Fast on` 或 `Fast off`，不显示 `1x`、`2x` 之类的倍率：实际速度和额度消耗随模型、账号和计费方式变化，Portal 没有可靠的统一倍率。
- **能力描述声明了 Full Access，用户就可以选择它。** Portal 不另按管理员或 owner 身份限制；Resident 实际能读写的文件和资源仍受 Portal 授权检查约束。

### 候选值与默认值

- **模型和推理强度的候选值来自当前账号的模型目录，加上一份明确列出的补充。** Codex 目录按页读取 `model/list`，按账号短期缓存，不合并其他账号的缓存。目录读取失败时，页面报告错误，再次打开弹窗会重新读取；页面不会用旧列表冒充读取成功。
- **补充列表只收已实测、目录遗漏的模型。** 补充列表是 `management_portal/execution/codex_runtime_settings.py` 中的 `CODEX_LIVE_VERIFIED_UNLISTED_MODELS`，当前只有 `gpt-6.1-sol`。目录成功读取（`source` 为 `model/list`）时，补充中目录没有的模型追加到候选末尾，并在结果的 `verified_unlisted_models` 中列出；目录已经包含的模型以目录为准。一个模型同时满足三个条件才能加入补充：
  1. 当前 Codex 版本的 `model/list` 没有返回它；
  2. 在每个已配置的 Codex 账号上都实际完成过一轮对话；用 Fast 时，还要完成过一轮 `priority` 服务等级的对话；
  3. 推理强度、默认强度、服务等级和输入类型按 Provider 官方模型规格填写，并在代码注释中写明实测所用的 Codex 版本。
  Codex 更新后目录开始返回这个模型时，从补充中删除它。
- **Portal 的默认模型和推理强度独立于 Provider 的推荐值。** Codex 的内置默认为 `gpt-6.1-sol` 和 `high`，部署可以用环境变量 `PORTAL_CODEX_DEFAULT_MODEL`、`PORTAL_CODEX_DEFAULT_EFFORT` 覆盖。Resident 的设置为空时继承这两个值；Provider 目录中的推荐模型、推荐强度和列表首项都不覆盖它们。
- **推理强度为空时，页面显示执行实际会用的强度。** 目录为每个模型给出 `default_efforts_by_model`：所选模型支持 Portal 默认强度时就是它，不支持时是执行层为该模型解析出的回退强度。摘要和弹窗按所选模型取这个值显示，不显示 Provider 的推荐强度，也不对所有模型套用同一个默认值。
- **弹窗回填已保存的值，即使目录里暂时找不到它。** 目录缺少当前模型时，弹窗保留原模型和推理强度，并在控件的悬停提示中说明“Provider 目录未返回此模型的能力信息”；页面不会自动换成其他模型或降低强度。已保存的 Fast 在当前模型不支持时显示为“Fast (saved; unavailable)”。
- **只校验用户这次改动的模型或推理强度。** 用户只修改协作模式等其他项时，已保存的模型即使不在目录中也不受影响。切换模型时保留原推理强度；新模型不支持它时，要求用户明确选择，页面不自动套用推荐值或第一个可选值。通过 `UpdateResidentSettings` 只改模型时，Claude、Kimi、DeepSeek Harness、OpenCode 同样拒绝，错误中列出新模型可选的推理强度（空值表示 Provider 默认），调用方连同推理强度一起再提交。
- **目录刷新不覆盖用户尚未保存的选择。**

### 何时生效

- **协作模式从下一轮生效。** 只修改协作模式时，Portal 不向正在进行的一轮注入文字，也不中断或重启它。保存结果的 `next_turn_fields` 列出下一轮才生效的项；此时 `active_turn_injection` 为 `requested=false`、`status=next_turn`，页面提示“下一轮生效”。
- **同一次保存中的其他改动可以通知正在进行的一轮，通知内容不包含协作模式。** 重复保存相同的值不产生通知。
- **保存失败时，弹窗保留用户的选择并允许重试。**
- **页面显示的是已保存的设置。** 它说明下一轮会怎样运行，不代表正在进行的那一轮已经切换。
- **选择 Plan 只改变 Provider 使用的流程。** 权限预设保持原样，Resident 也不会因此自动向用户提问；只有 Provider 真正发起提问时，聊天中才出现提问卡片（decision_1e2eb869bc10）。

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
        ├─ execution_settings_json：模型与调用策略
        └─ harness_config_json：Harness 结构
        │
        ▼
当前设置读取投影 → Runtime Settings 摘要 / ExecutionRequest 的时点快照
```

实现入口为 `resident_runtime_domain._read_resident_runtime_settings` 与 `_write_resident_runtime_settings`，分别调用存储层的 `read_resident_execution_settings` 与 `write_resident_execution_settings`。`resident_settings_contract` 负责有限字段的拆分、组合与候选暂存；`execution.resident_runtime_settings` 负责通用归一，`execution.resident_configuration` 定义 Prompt 与后端专用参数约束，`execution.slash_commands` 解释命令。

全记录保存通过 `catalog.upsert` 与 `_save_resident_record` 传播 `commit_configuration=False`。首次登记写入初始配置；已登记 Resident 的普通保存由存储事务保留当前 `CONFIGURATION_FIELDS` 与 `backend`，仅更新其他状态。只有明确配置 owner 的提交才能传入 `commit_configuration=True`。`_write_resident_runtime_settings` 直接写唯一配置 owner，无需该标记。Restart 成功提升候选会话绑定后才提交配置；失败或取消只保存运行状态。账号校准将设置写入、账号路由与 Provider 配置提交放入同一数据库事务，事务失败时一起回滚。候选和执行快照不会自动获得当前配置的写入权。

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

Portal Web 进程只通过 SocietyService 的已鉴权读取接口取得 Resident 的 Provider 和账号，再向 ExecutionService 查询能力；它不直接调用 SocietyService 的内部函数，也不连接 Resident 的运行端口。账号取自 Resident 当前 Session 的绑定，Resident 记录上的旧路由字段不参与判断。

页面、`/model` 等斜杠命令和 HATG 指令使用同一份设置和同一套校验：服务端的 Provider 适配器检查每个字段是否在能力描述中、权限预设是否存在、模型是否在账号目录中。页面只提交用户实际改动、且能力描述允许的字段。Provider 名称只用于选择适配器和诊断，页面按能力描述决定显示哪些控件。

Society 页面创建 Resident 时的模型和推理强度选择框使用同一个目录（`portal_lineage_catalogs.js`）。目录读取失败时，选择框只显示“Portal 默认模型 (default)”。

### 设置怎样进入下一轮

构造每次执行请求时读取数据库 owner 的当前设置，再交给 Provider 适配器；Harness 结构作为这次执行的配置进入 `runtime_options.agent_harness`。请求中的这份数据是明确执行时快照，不能替代之后的当前设置读取。

Codex 开始新一轮时，Provider 适配器把协作模式、模型和推理强度写入 `turn/start` 请求的 `collaborationMode`，并把 `settings.developer_instructions` 设为 `null`，让 Codex 使用自己内置的模式说明。从 Plan 切回 Default 时同样显式传 `default`，不依赖 Codex 线程沿用上一轮的模式。Resident 不需要调用任何 Portal 接口来切换模式。
