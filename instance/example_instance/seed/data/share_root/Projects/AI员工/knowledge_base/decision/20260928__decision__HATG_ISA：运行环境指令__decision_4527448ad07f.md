---
kb_id: decision_4527448ad07f
type: decision
project: AI员工
created_at: '2026-09-28'
version:
  number: 51
  created_at: "2026-09-28T10:45:00Z"
  created_by:
    user: "weihao"
    agent: "resident_2a1fc6cd272d"
  updated_at: "2026-10-22T08:00:00Z"
  updated_by:
    user: "huanyu"
    agent: ""
importance: 8
metaness: 2
ai_generated: false
links:
  source:
  - ${code_root}/management_portal/runtime_capacity.py
  - ${code_root}/management_portal/web_isa.py
  - ${code_root}/management_portal/hatg/catalog/environment.py
  - ${code_root}/tests/cases/test_isa_g7_audit.py
  - ${code_root}/scripts/publish_portal_release.py
  - ${code_root}/tests/cases/test_portal_web_isa_environment.py
  - ${code_root}/scripts/portal_services
  - ${code_root}/scripts/publish_portal_release
  - ${code_root}/management_portal/agent_status_backend.py
  - ${code_root}/management_portal/execution/backends.py
  related:
  - decision_8ac0b5a6c0e5
  - decision_fc26f883773d
  - decision_4dea0db1f45a
  - decision_898b8911a2ff
  - decision_08bb2d9141f9
  - decision_850e6248d680
  - decision_37f98f8fcb4b
  - decision_99bb5dc0f96b
  - decision_ed57c4ad1001
  - decision_6c2f91a7d84e
  - decision_fd760e898f9a
  - decision_b820faf42218
  supersedes: []
  derived_from:
  - decision_898b8911a2ff
  - thinking_ca8f397a47de
---
# HATG ISA：运行环境指令

运行环境指令管理 AI 社会运行所依赖的环境：查看 Portal 各服务的状态、请求重启、把 Dev 代码发布到 Release，查看 GPU 与磁盘等设备的状态，查看执行账号的额度与用量并调整调度设置，以及登记和调用普通模型接口。所有指令共同遵守的调用格式、执行流程和错误语义见 decision_898b8911a2ff。

## 操作的对象

- **Portal 服务**：Portal 的 Dev 与 Release 两个实例，以及每个实例中的组件：Web、ResourceService、ExecutionService、ExecutionWorker 与 SocietyService。服务的滚动部署、重启通知和恢复编排见 decision_08bb2d9141f9、decision_850e6248d680 与 decision_37f98f8fcb4b，运行状态投影见 decision_99bb5dc0f96b。
- **设备**：Portal 监控的 GPU 与磁盘。设备本身是登记资源，登记和修改使用资源指令（decision_ad3e21094f14）；本类读取设备的运行状态，并为 Portal 管理员打开 Portal 主机或 Device 的 Terminal。状态的采集与展示见 decision_ed57c4ad1001。
- **执行设置**：由 ExecutionService 保存的全系统设置，包括模型请求计时代理开关；仅管理员可读取或修改。
- **执行账号**：Resident 调用模型时使用的 Provider 账号，例如各个 Codex 账号。每个账号有额度窗口、余额或可用性、调度设置（最大并发、两次派发之间的随机间隔、关闭接单的额度阈值）以及绑定在它上面的 Resident。额度与调度见 decision_4dea0db1f45a 和 decision_8ac0b5a6c0e5。
- **用量**：执行过程中记录的 token 用量，按 Resident、Lineage、账号和时间归因。统计口径见 decision_fc26f883773d。
- **模型接口**：LLM API Registry 中登记的普通模型 HTTP 接口，包括地址、默认模型、超时和密钥文件引用。一次调用以消息列表为输入、以完整回答为输出，不建立会话。见 decision_6c2f91a7d84e。

## 指令一览

| 指令 | 改变状态 | 作用 | 默认方案要求 |
| --- | --- | --- | --- |
| `GetExecutionSettings` | 否 | 管理员读取全系统执行设置 | 对“共享”有 @admin |
| `GetTelepusStatus` | 否 | 读取 Telepus 用户服务的启用与运行状态 | 对“共享”固定判定槽有 @admin；当前启用方案可调整 |
| `SetTelepusEnabled` | 是 | 用必填 enabled 设置 Telepus 服务目标状态；保留联系人与数据 | 对“共享”固定判定槽有 @admin；当前启用方案可调整 |
| `UpdateExecutionSettings` | 是 | 管理员通过系统 Event 修改请求计时与 Harness 记录开关，修改保留天数，开启或关闭权限系统 | 对“共享”有 @admin |
| `GetPortalStatus` | 否 | 读取 Portal 实例与组件的运行状态 | 对“共享”有 @viewer |
| `GetRuntimeCapacity` | 否 | 读取数据库连接、主机磁盘和执行队列的容量快照 | 对“共享”有 @viewer |
| `RestartPortal` | 是 | 重启 Portal 实例或其中的组件 | 对“Portal 运维”有 @admin |
| `PublishRelease` | 是 | 把指定的 Dev 代码发布到 Release | 对“Portal 运维”有 @admin |
| `GetDeviceStatus` | 否 | 读取 GPU 或磁盘状态 | 对“共享”有 @viewer |
| `OpenTerminal` | 是 | 为调用者打开 Portal 主机 shell 或 Device 的 SSH 终端 | 对“共享”有 @admin |
| `ListModelApis` | 否 | 列出已登记的模型接口 | 对“共享”有 @viewer |
| `CreateModelApi` | 是 | 登记一个模型接口 | 对“Portal 运维”有 @admin |
| `UpdateModelApi` | 是 | 修改一个模型接口的登记 | 对“Portal 运维”有 @admin |
| `DeleteModelApi` | 是 | 删除一个模型接口的登记 | 对“Portal 运维”有 @admin |
| `CompleteChat` | 是 | 调用一次模型接口生成文本 | 免检 |
| `ProbeModelApi` | 是 | 检测模型接口为 Agent Harness 提供哪些协议 | 对“Portal 运维”有 @admin |
| `ListProviderAccounts` | 否 | 读取执行账号的额度、余额和调度设置 | 对“共享”有 @viewer |
| `UpdateProviderAccount` | 是 | 修改一个执行账号的调度设置 | 对“共享”有 @admin |
| `PrepareProviderRuntime` | 是 | 复用已有运行时，缺失时按 Provider 异步准备 | 对“共享”有 @admin |
| `GetProviderAccountSetup` | 否 | 读取运行环境、授权、模型与声明能力 | 对“共享”有 @viewer |
| `CheckProviderAccount` | 是 | 通过 ExecutionService 提交最小模型回复测试 | 对“共享”有 @admin |
| `GetProviderAccountCheck` | 否 | 读取测试结果与配置有效性 | 对“共享”有 @admin |
| `ListProviders` | 否 | 列出 Provider、账号种类、能力与运行时状态 | 对“共享”有 @viewer |
| `RegisterProviderAccount` | 是 | 登记执行账号并创建 Provider 目录与服务 | 对“共享”有 @admin |
| `UpdateProviderAccountRegistration` | 是 | 修改执行账号的登记 | 对“共享”有 @admin |
| `DeleteProviderAccount` | 是 | 删除空闲账号及其主机目录与服务实例 | 对“共享”有 @admin |
| `OpenProviderAccountLogin` | 是 | 打开订阅账号的登录会话 | 对“共享”有 @admin |
| `UpdateProviderAccountLogin` | 是 | 向已有的 Claude 登录会话提交一次性授权码 | 对“共享”有 @admin |
| `GetProviderAccountLogin` | 否 | 读取登录会话与账号认证状态 | 对“共享”有 @viewer |
| `CancelProviderAccountLogin` | 是 | 取消订阅账号登录并结束登录进程 | 对“共享”有 @admin |
| `ListBackendRoles` | 否 | 读取 Backend 角色映射 | 对“共享”有 @viewer |
| `UpdateBackendRole` | 是 | 设置一个 Backend 角色指向的账号、模型与推理强度 | 对“共享”有 @admin |
| `GetUsage` | 否 | 读取 token 用量统计 | 对“共享”有 @viewer |
| `GetProviderUsageEvents` | 否 | 分页读取脱敏的 Provider 用量与额度观察 | 对“共享”有 @viewer |

## 全系统执行设置

执行设置由 ExecutionService 保存本地投影并执行。`GetExecutionSettings` 属于 execution owner，`UpdateExecutionSettings` 属于 lineage owner，将修改路由到已安装的 Event 开关。两条指令按启用权限方案判定，默认方案要求“共享”上的 `@admin`；Portal 管理员仅修改 `permission_system_enabled` 时的特殊通道及权限系统关闭时的边界见 decision_b820faf42218。

### `GetExecutionSettings`

**读取全系统执行设置。**

- 参数：无。
- 结果：`settings`，包含 `model_request_timing_enabled`、`harness_recording_enabled`、`permission_system_enabled` 三个布尔值和 `harness_retention_days`。

### `UpdateExecutionSettings`

**修改模型请求时间记录或 Harness 运行证据记录开关。**

- 参数：`model_request_timing_enabled`、`harness_recording_enabled`，可选布尔值；`harness_retention_days`，可选整数（21–365，默认 30），Harness 证据、调用记录和未被引用的产物的保留天数；`permission_system_enabled`，可选布尔值，开启或关闭权限系统（默认开启）。至少提供一个。
- 结果：保存后的 `settings`。开启权限系统时另有 `reconciled`：本次重建权限实例的报告。
- 权限系统开关不经过 Event：关闭后各服务在 3 秒内改用开放实现、不再加载权限代码；开启前，在开关仍关闭时由 ResourceService 按当前模板和全部现有对象重建权限实例（创建者、Family steward、聊天室成员等规则表项随之展开），再写入开关。规则见 decision_b820faf42218。
- 已安装流程以 Event runtime.paused 为真值：请求计时绑定 Portal System 的 `system:request-timing:enabled`，Harness 记录绑定系统演化的 `system:harness-recording:enabled`。修改设置调用同一 PauseComponent 实现，并在返回前更新 Execution 本地投影。删除已绑定 Event 后投影关闭，再修改设置返回 conflict，要求恢复图；未安装过的功能兼容原设置写入。
- 计时使用旁路观察，关闭停止后续时间记录，不改变 Provider 请求路径，不终止在途请求或旧监听器排空。Harness 关闭停止新增运行证据，重开不补写暂停期间排队证据，已有历史及显式质量评审保留。

## Portal 服务

Portal 服务指令由 Portal Web 执行。Dev 与 Release 两个实例各有自己的 Portal Web，调用发给哪个实例，就由哪个实例执行。

### `GetPortalStatus`

**读取 Portal 实例与各组件的运行状态。**

- 参数：`instance`（可选，`dev` 或 `release`；省略时读取全部实例）。
- 结果：每个组件的运行状态、运行版本与健康检查；Release 同时提供当前发布 manifest，其中记录已激活的发布。该查询展示当前运行状态，部署尝试与重启失败的详情由部署流程的运行记录提供。

### `GetRuntimeCapacity`

**读取用于运行容量预警的独立容量观察。** 指令由 Portal Web 执行，无参数，沿正式 ISA 的身份与权限检查；不启动模型、刷新 Provider 额度或改变调度配置。

结果为 `capacity`，新输出 schema 是 `tfi.runtime-capacity.v1`；读取方只接受该精确格式，`observed_at` 是本次开始采样的 UTC 时间。三个来源顺序采集，各自返回 `status=ok|unavailable`；某源异常只返回安全错误码，成功来源继续保留。这是当前观察，不是跨服务事务快照。

- `database`：`connections` 为 PostgreSQL 实例全部 client backend 连接数，含当前观察连接；`max_connections` 为总槽数，`reserved_connections` 包括普通保留与超级用户保留槽，`usable_connections` 是两者相减。`used_percent` 为连接数除以普通可用槽数，可超过 100%，不截断。查询使用短只读连接，退出或异常都释放连接及其数据库句柄。
- `disks.filesystems`：仅观察本机 `/`、`/share`、`/data` 中存在的路径，按文件系统设备号去重，返回 `key`、代表路径 `path`、`total_bytes`、`free_bytes` 与 `used_percent`。使用率按总容量减可用容量计算，包含预留空间，不扫描文件、不连接远程服务器。
- `execution_queue`：复用 ExecutionService 的健康读取；`queued_count` 来自 scheduler 的 queued 状态计数，包含配额、派发间隔与绑定等待；`active_count` 使用该服务的活动数量，`max_concurrency` 使用全部 capacity 分区限制之和 `total_capacity`。单个默认分区上限不能代表总并发，排队数也不能代表可立即派发数量。

预警阈值、持续时间、去重和恢复由 Portal System 的运行容量流程负责，见 decision_5ff0cb51ed84。

### `RestartPortal`

**重启一个 Portal 实例，或实例中的指定组件。**

- 参数：`instance`（`dev` 或 `release`）、`components`（可选，取值为 `web`、`resource`、`execution-service`、`execution-worker`、`lineage`）。Dev 省略时由部署流程按自上次成功部署以来的源码变化选择组件；Release 省略时重启 `web`、`resource`、`execution-service`、`lineage`。
- 结果：`request_id` 与 `components`。Dev 返回已排队请求的 ID 和显式组件；空组件列表表示部署流程执行时计算变化范围。Release 返回本次异步启动的关联 ID 与请求组件，后续运行结果由服务控制流程记录。
- 重启前，打开 Portal 的浏览器显示固定 5 秒的重启倒计时。ExecutionWorker 采用平滑换代，正在执行的模型任务交接给新一代 Worker。
- Dev 提交持久部署请求，Release 启动异步服务控制流程；指令返回后，部署流程按组件执行（decision_08bb2d9141f9）。调用者通过 `GetPortalStatus` 读取当前健康状态；请求受理或进程启动本身只说明重启已经发起。重启的组件包括 Portal Web 自身时，指令先返回结果，再开始重启。

### `PublishRelease`

**把调用者指定的 Dev 代码同步到 Release，并返回发布提交。**

- 参数：`message`（本次发布的提交说明）、`paths`（要发布的文件或目录列表，删除也会同步）或 `all`（为 `true` 时发布全部受 Git 管理的源码），二者必须且只能提供一个。
- 结果：发布提交 ID、同步的路径、Release 发布后的健康状态，以及推送到各 Git 远端的结果 `push`。
- 只能在 Dev 实例上执行；发给 Release 实例时返回错误。
- 指令只提交发布动作；选择范围、撰写提交说明与验证功能由调用者完成。提交、合并发布批次、Release 激活、共享组件和各远端推送的顺序与失败语义统一见 decision_08bb2d9141f9。`push` 单独反映各远端结果，不能仅凭指令受理或部分步骤完成推断全部发布成功。

## 设备

设备状态与 Terminal 指令由 Portal Web 执行。

### `GetDeviceStatus`

**读取 GPU 或磁盘的当前状态。**

- 参数：`kind`（必填，`gpu` 或 `disk`）、`device_id`（可选；省略时读取这一种类的全部设备；GPU 写 Device 的统一 ID，磁盘写磁盘资源 ID，例如 `nas_storage`）、`refresh`（可选，是否立即重新采集）。
- 结果：`devices` 列出读到的设备，另附这一种类的汇总。GPU 为每台服务器和 GPU 的显存、利用率和进程占用，未知的 `device_id` 得到空列表；磁盘为每个磁盘的容量、已用空间和健康状态。

### `OpenTerminal`

**为调用者打开一个 Terminal：Portal 主机上的 shell，或经 SSH 连到一台 Device。**

- 参数：`device_id`（可选；省略时打开 Portal 主机 shell）、`cwd`（主机 shell 的起始目录）、`cols`、`rows`（终端尺寸）。
- 结果：`session_id`、`alive` 与 `cwd`。会话之后由 Portal 页面通过 Terminal 的读写路由使用，会话只属于打开它的 User；Resident 不能打开 Terminal。
- Terminal 等于主机上的 shell。权限系统开启时按启用方案判定，默认方案要求“共享”上的 `@admin`；权限系统关闭时任何已登录的 User 均可打开。User 会话归属与 Resident 不可打开的限制继续由领域实现执行，不因权限系统关闭而取消。
- 页面上的所有启动方式都是这条指令：Society 工作台的终端面板（`/api/agent-workbench/local-terminal/start`）、新建 shell（`control` 的 `restart`）和 Device 详情页的“打开 Terminal”（`/api/terminal/start`）都先判定 OpenTerminal，没有权限时返回 403。`/api/me` 的 `access.allows.OpenTerminal` 为假时，Society 页面不提供终端组件，Device 页面不显示打开按钮。

## 执行账号与用量

执行账号与用量指令由 ExecutionService 执行。按账号打开或关闭 Resident 的接单，使用 `OpenIntake` 的 `resident_ids` 批量形式（decision_2a3ff353165c）。

### `ListProviderAccounts`

**读取执行账号的额度窗口、余额或可用性与调度设置。**

- 参数：`account_id`（可选，只读取一个账号）、`refresh`（可选，是否重新采集外部额度；受刷新节流限制）。
- 结果：每个账号的额度窗口（剩余比例、重置时间、采样时间）、余额或可用性、调度设置，以及 `capabilities`（该账号支持的操作，例如能否修改调度设置、能否读取 Resident 接单明细；页面据此显示可用的操作）。每项数据带有新鲜度与采集错误；某个来源采集失败不影响其他来源。Resident 的接单统计由 SocietyService 维护，Home 的账号详情按调用者可读范围单独查询，见 decision_4dea0db1f45a。

- 结果另含 `default_codex_account_id`，表示全局默认 Codex route，供每日额度快照保持账号归属。

### `UpdateProviderAccount`

**修改一个执行账号的调度设置。**

- 参数：`account_id`、`max_concurrency`（1–256）、`dispatch_interval_min_seconds` 与 `dispatch_interval_max_seconds`（0–3600 秒，最大值不小于最小值）、`close_threshold_percent`（可选，额度进入该阈值时自动关闭该账号上 Resident 的接单）。余额型账号使用 `close_threshold_amount`（可选，余额低于这个原币金额时关闭接单）。
- 结果：保存后的调度设置。
- 设置从下一次派发开始生效，不中断正在执行的工作。

### 账号登记、登录与 Backend 角色

执行账号的登记、页面内登录和 Backend 角色由 ExecutionService 执行，设计见 decision_7956d7cb4bde。调度设置仍由 `UpdateProviderAccount` 修改。

- `ListProviders`：参数 `refresh`（可选，检查可用版本并保存观察，不安装或激活运行时）。结果 `providers`：声明的 Provider、账号种类、能力摘要、运行时安装状态与可用版本。
- `RegisterProviderAccount`：参数 `account_id`、`provider`、`kind`（`subscription_login`、`api_profile` 或 `service`）、`route_id`（可选）、`config`（可选；`api_profile` 用 `config.api_id` 引用已有模型接口）、`max_concurrency`（可选，初始并发）、`enabled`（可选）、`secret`（可选，`api_profile` 的一次性密钥，只写入密钥文件，不回显）。结果 `registration`。
- `UpdateProviderAccountRegistration`：参数 `account_id`、`changes`（配置、`enabled` 与初始并发）。结果 `registration`。调用信封的 `expected_version` 给出登记版本（`ListProviderAccounts` 每个账号的 `registration_version`）时按它比较，版本不符以 `instruction.version_conflict` 失败；省略时直接更新。
- `DeleteProviderAccount`：参数 `account_id`。账号仍有绑定会话或排队、运行中的执行时以 `instruction.conflict` 失败，`details` 给出 `reason: account.busy`、会话数 `session_count` 和执行数 `execution_count`；仍被 Backend 角色引用时以 `instruction.conflict`（`reason: account.role_in_use`）失败。结果为被删除的 `account_id`。
- `OpenProviderAccountLogin`、`UpdateProviderAccountLogin`、`GetProviderAccountLogin`、`CancelProviderAccountLogin`：参数都以 `account_id` 指定订阅账号；`UpdateProviderAccountLogin` 另有 `authorization_code`（Claude 浏览器返回的授权码，只送到当前登录进程，不保存或回显）。Open、Update、Cancel 返回 `session`；Get 返回 `session`（尚未开始时为 null）与 `login_state`。每个账号同一时间只有一个登录会话。
- 账号相关的失败使用通用错误码，`details.reason` 给出具体原因，页面按 `reason` 选择提示：

| 情形 | 错误码 | `details.reason` |
| --- | --- | --- |
| 账号仍有会话或执行（`DeleteProviderAccount`） | `instruction.conflict` | `account.busy` |
| 账号仍被 Backend 角色引用（`DeleteProviderAccount`） | `instruction.conflict` | `account.role_in_use` |
| 上一次登录进程正在结束（Open、Delete），可稍后重试 | `instruction.conflict`，`retryable` | `account.login_busy` |
| 没有等待授权码的 Claude 登录会话（Update） | `instruction.conflict` | `account.login_not_awaiting` |
| 登录进程已不接收授权码，需要取消后重新开始（Update） | `instruction.conflict` | `account.login_unavailable` |
| 尚无认证观察且原生状态查询无法执行（Get、List），可稍后重试 | `instruction.owner_unavailable`，`retryable` | `account.login_unavailable` |
| 无法启动登录进程（Open） | `instruction.failed` | `account.login_unavailable` |
| 站点尚未设置 default Backend 角色，又没有给出其他可用角色 | `instruction.conflict` | `backend_role.default_missing` |
- `ListBackendRoles`：参数无，结果 `roles`。
- `UpdateBackendRole`：参数 `backend_role`（角色名，与 `CreateResident` 的 `backend_role` 同名）、`account_id`、`model`（可选）、`reasoning_effort`（可选）。结果 `role`。调用信封的 `expected_version` 给出角色版本时按它比较，新建角色时为 0；省略时直接写入。


接入流程先调用 `GetProviderAccountSetup(account_id)`，分别展示运行环境、授权状态、模型目录与声明的工具能力。`PrepareProviderRuntime(provider)` 复用已安装程序；缺失时通过原运行时 owner 异步准备，返回 preparation，后续从 ListProviders 或 GetProviderAccountSetup 观察。`CheckProviderAccount(account_id, model?)` 创建普通持久 Execution，返回 execution_id；`GetProviderAccountCheck(account_id, execution_id)` 只读取调用者可访问的执行，核对账号、登记与模型 API 版本、完成状态和预期回复。测试不调用工具，不能据此宣称工具已实测。UpdateBackendRole 可带 `verification_execution_id`；接入页面必须带此回执，只有匹配当前账号与模型的成功测试才能完成默认设置。已有高级角色编辑保持可用。

### `GetUsage`

**读取按 Resident、Lineage 和时间归因的 token 用量。**

- 参数：`period`（`today`、`week`、`month` 或 `year`）、`group_by`（`resident`、`lineage` 或 `project`；`project` 把 Resident 经它当前所属的可读 Lineage 归入该 Lineage 所属的可读 Project，其余归入匿名项）、`provider`（可选）、`account_id`（可选）、`include_cached`（可选，是否计入命中缓存的输入）。
- 结果：周期总量、按时间的序列、按 Resident 或 Lineage 的排名、更新时间与来源状态。token 分为输入、其中命中缓存的输入、输出和其中的推理输出。
- 结果写在 `usage` 中：`totals`、`series`（`today` 按小时，`week` 与 `month` 按天，`year` 按月）、`ranking`（按总量从高到低）、`updated_at` 与 `source`（`status` 为 `ok` 或 `partial`，`partial` 时 `missing_windows` 列出未能读取的时间段；全部读取失败时指令以 `instruction.owner_unavailable` 失败）。每组 token 字段为 `input_tokens`、`cached_input_tokens`、`output_tokens`、`reasoning_output_tokens` 与 `total_tokens`（输入加输出）。`include_cached` 为 `false` 时，输入中减去命中缓存的部分。
- 周期和时间点按服务器的本地时区划分，与 Home 用量归因相同（decision_fc26f883773d）：`today` 从今天 0 点开始，`week` 从本周一开始，`month` 从本月 1 日开始，`year` 从今年 1 月 1 日开始。`series` 中 `today` 的时间点是带时区的本地整点，`week` 与 `month` 是本地日期，`year` 是本地年月。
- 排名只列出调用者有权读取的 Resident 或 Lineage，其余合并为一条匿名项（`id` 为空，`anonymous` 为 `true`），排在最后；规则与 Home 用量归因相同（decision_fc26f883773d）。总量和时间序列包含全部用量。不可读 Resident、未登记会话以及在 Lineage 分组下不可读的 Lineage，不泄露隐藏 ID，合并到该维度的匿名组。可读范围或归属读取无法确认时，指令以 `instruction.owner_unavailable` 失败。
- 返回 `collection_started_at`、`collection_finished_at` 和 `collection_windows`，记录实际采集时间及查询窗口；缓存读取和失败后保留的快照沿用原采集信息，不能以 HTTP 请求时间或 `updated_at` 代替。字段口径统一见 decision_fc26f883773d。
- `bucket_groups` 返回所选 `group_by` 的完整时段归属：`{bucket,groups:[{id,total_tokens,anonymous?}]}`。Today 为本地日小时桶，其他周期沿用日或月粒度；匿名、未观察与零用量的区别以及采集核对规则见 decision_fc26f883773d。
- 本指令汇总 Execution 账本，不包含 Home 的 Codex 账号日总量补差；不能把不同调用的采集区间当作同一瞬时快照。Home 补差、小时分组及账号额度同样本核对按各自领域契约处理，本篇不重复维护界面取证步骤。

### `GetProviderUsageEvents`

额度报告需要原始额度重置观察、每轮 token 计数和 fork 去重所需的会话关系。ExecutionService 从已登记的 Codex 账号读取这些记录，脚本经 ISA 分页接收；账号目录和凭证继续留在服务侧。

- 参数：`account_id`（Codex route ID 或已登记账号 ID）、`since`、`until`（含时区的 ISO 时间，范围左闭右开，最多 60 天）、`cursor`（可选，上一页游标）、`limit`（默认 1000，范围 1–1000）。
- 结果：`account_id`、`records` 和 `next_cursor`。每条记录包含相对来源文件 `source` 与白名单投影 `record`：会话 ID、Provider 与 fork 来源，模型名，用户输入的发生时间，token 数和额度窗口。消息、指令、工具参数、模型回答和凭证不进入结果。
- 为还原模型上下文和 fork 去重，窗口起点之前的会话元数据与模型切换记录可一并返回；用量与用户输入时间严格限制在窗口内。分页按文件和字节位置继续，单页最多扫描 8 MiB；扫描上限先达到时允许记录为空且返回续页游标。调用方持续读取至游标为空。
- 查询只接受登记账号，不接受任意目录；跳过指向账号会话目录外的链接。游标绑定账号与时间范围，源文件分页期间被截断时返回参数错误，调用方重新开始查询。
- 本指令是只读指令，默认方案要求“共享”上的 `@viewer`，并要求有效的 User 或 Resident 身份（decision_b820faf42218）。脚本在私有临时目录重建脱敏输入，复用现有报告的去重与计算规则；输出 schema、文件名与目录保持兼容，审计来源标记为 ISA 投影。

## 模型接口

模型接口的登记与调用由 ExecutionService 执行。登记只保存密钥文件的路径引用。创建或修改时提交的一次性 `secret` 只写入密钥文件，不进入结果、日志和执行记录。

### `ListModelApis`

**列出已登记的模型接口。**

- 参数：无。
- 结果：每个接口的 ID、地址、启用状态、默认模型和超时设置。

### `CreateModelApi`

**登记一个新的模型接口。**

- 参数：`api_id`、`base_url`、`default_model`、`secret_file`（密钥文件路径）、`secret`（可选，一次性密钥：Portal 原子写入站点密钥目录下的 `<api_id>.key` 并把路径记为 `secret_file`，不回显）、`provider`（可选，Provider 标识）、`routes`（可选，每项明确给出 `backend` 和 `route_id`）、`models`（可选，模型目录；每项含唯一 `id`，可声明 `reasoning_efforts` 与 `enabled`）、`protocols`（可选，Agent Harness 可用的其他协议：`openai_responses` 为 `{}`，供 Codex；`anthropic_messages` 可含 `base_url` 与 `auth`（`bearer` 或 `x-api-key`），供 Claude Code；Chat Completions 无需声明）、`default_parameters`（可选，协议允许的默认生成参数）、`timeout_seconds`（可选）、`retries`（可选）、`headers`（可选，不含密钥）、`enabled`（可选，默认 `true`）。
- 结果：登记后的接口信息。

执行后端通过 `routes` 的明确绑定找到模型接口；没有绑定时报告配置缺失，不按登记顺序或启用顺序选择。接口登记与后端路线分别存储，路线通过 `api_id` 关联接口；同一后端路线只能绑定一个接口。

### `UpdateModelApi`

**修改一个模型接口的登记。**

- 参数：`api_id`、`changes`、`secret`（可选，一次性密钥，写法与 `CreateModelApi` 相同；轮换密钥就是再提交一次）。
- 结果：修改后的接口信息与新版本。

`changes.routes` 更新接口的明确路线绑定，`changes.default_parameters` 更新协议允许的默认生成参数；其他字段按登记接口的具名字段校验，拒绝未定义字段。

### `DeleteModelApi`

**删除一个模型接口的登记。**

- 参数：`api_id`。
- 结果：被删除的接口 ID。已经在进行的调用继续完成。

### `CompleteChat`

**调用一个已登记的模型接口，完成一次无状态的文本生成。**

- 参数：`api_id`、`model`（可选，默认使用登记的模型）、`messages`、生成选项（可选）、`response_schema`（可选）。生成选项写在 `options` 中，按上游接口的约定传入，例如 `thinking`、`max_tokens`、`temperature`。接口负责校验支持的选项与值；Portal 将 `options` 传给接口，并忽略其中的 `model`、`messages` 与 `stream`，使用指令参数选择模型和消息并读取完整回答。
- 结果：模型回答与用量；提供 `response_schema` 时另有 `structured`。
- `response_schema` 是回答必须满足的 JSON Schema。Portal 先校验它本身，格式无效时以参数错误拒绝，不调用模型；调用时在消息最前面加一条系统消息，要求只输出满足该 Schema 的 JSON。回答返回后，优先从代码块、其次从正文中取出第一个 JSON 对象或数组，按 Schema 校验后放入 `structured`。回答中没有 JSON 或不满足 Schema 时调用失败，并标记为可重试。
- 调用者必须明确选择接口，系统不根据调用者或任务自动选择。生成的文本不写入任何 Resident 的对话。

### `ProbeModelApi`

**检测模型接口为 Agent Harness 提供哪些协议。**

- 参数：`api_id`（可选，已登记的接口）、`base_url`、`default_model`、`secret_file`、`secret`（可选，一次性密钥，只用于本次检测，不保存）、`headers`（可选，不含密钥）、`anthropic_base_url`（可选）。提供 `api_id` 时，其余参数覆盖它的登记值，用于在保存修改前检测；不提供时须给出 `base_url` 与 `default_model`。
- 结果：检测所用的模型，以及 `openai_chat`、`openai_responses`、`anthropic_messages` 各自的结果：`status`（`supported`、`unsupported`、`auth_failed`、`rejected`、`error` 或 `unreachable`）、`http_status` 与截断的 `message`；`anthropic_messages` 支持时另有可用的 `auth`。
- 每种协议向 Agent Harness 实际使用的地址发一次很短的非流式请求：`<base_url>/chat/completions`、`<base_url>/responses` 与 `<anthropic_base_url 或去掉 /v1 的 base_url>/v1/messages`。Anthropic 先以 Bearer 认证，被拒绝时再以 `x-api-key` 认证。返回 2xx 且内容符合该协议的格式才算支持；部分网关对未知路径返回网页，按不支持处理。
- 检测会调用模型，可能产生少量费用。它不修改登记；密钥不进入结果和日志，`message` 中出现的密钥替换为 `***`。

## 参数名契约

下表列出 arguments 内的顶层参数名；必填条件、类型和枚举以 GetInstructionSchema 为准。调用信封中的 expected_version、idempotency_key 与 wait_seconds 不列入 arguments。

| 指令名 | arguments 参数名 |
| --- | --- |
| `GetExecutionSettings` | 无 |
| `UpdateExecutionSettings` | `model_request_timing_enabled`、`harness_recording_enabled`、`permission_system_enabled`、`harness_retention_days` |
| `GetPortalStatus` | `instance` |
| `GetRuntimeCapacity` | 无 |
| `RestartPortal` | `instance`、`components` |
| `PublishRelease` | `message`、`paths`、`all` |
| `GetDeviceStatus` | `kind`、`device_id`、`refresh` |
| `OpenTerminal` | `device_id`、`cwd`、`cols`、`rows` |
| `ListModelApis` | 无 |
| `CreateModelApi` | `api_id`、`base_url`、`default_model`、`secret_file`、`secret`、`provider`、`routes`、`models`、`protocols`、`default_parameters`、`timeout_seconds`、`retries`、`headers`、`enabled` |
| `UpdateModelApi` | `api_id`、`changes`、`secret` |
| `DeleteModelApi` | `api_id` |
| `CompleteChat` | `api_id`、`model`、`messages`、`options`、`response_schema` |
| `ProbeModelApi` | `api_id`、`base_url`、`default_model`、`secret_file`、`secret`、`headers`、`anthropic_base_url` |
| `ListProviderAccounts` | `account_id`、`refresh` |
| `GetProviderUsageEvents` | `account_id`、`since`、`until`、`cursor`、`limit` |
| `UpdateProviderAccount` | `account_id`、`max_concurrency`、`dispatch_interval_min_seconds`、`dispatch_interval_max_seconds`、`close_threshold_percent`、`close_threshold_amount` |
| `ListProviders` | `refresh` |
| `PrepareProviderRuntime` | `provider` |
| `GetProviderAccountSetup` | `account_id` |
| `CheckProviderAccount` | `account_id`、`model` |
| `GetProviderAccountCheck` | `account_id`、`execution_id` |
| `RegisterProviderAccount` | `account_id`、`provider`、`kind`、`route_id`、`config`、`max_concurrency`、`enabled`、`secret` |
| `UpdateProviderAccountRegistration` | `account_id`、`changes` |
| `DeleteProviderAccount` | `account_id` |
| `OpenProviderAccountLogin` | `account_id` |
| `UpdateProviderAccountLogin` | `account_id`、`authorization_code` |
| `GetProviderAccountLogin` | `account_id` |
| `CancelProviderAccountLogin` | `account_id` |
| `ListBackendRoles` | 无 |
| `UpdateBackendRole` | `backend_role`、`account_id`、`model`、`reasoning_effort`、`verification_execution_id` |
| `GetUsage` | `period`、`group_by`、`provider`、`account_id`、`include_cached` |
| `GetTelepusStatus` | 无 |
| `SetTelepusEnabled` | `enabled` |
