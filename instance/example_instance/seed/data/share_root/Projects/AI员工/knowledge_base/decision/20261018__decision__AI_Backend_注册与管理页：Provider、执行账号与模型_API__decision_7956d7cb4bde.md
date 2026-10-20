---
kb_id: decision_7956d7cb4bde
type: decision
project: AI员工
created_at: '2026-10-18'
version:
  number: 17
  created_at: '2026-10-18T14:45:00Z'
  created_by:
    user: tfi-exported-user
    agent: resident_667111522609
  updated_at: '2026-10-19T22:29:27Z'
  updated_by:
    user: ''
    agent: resident_977d1a613d44
importance: 8
metaness: 2
ai_generated: false
links:
  source:
  - ${code_root}/management_portal/execution/account_registrations.py
  - ${code_root}/management_portal/execution/provider_catalog.py
  - ${code_root}/management_portal/execution/accounts.py
  - ${code_root}/management_portal/execution/account_adapters.py
  - ${code_root}/management_portal/claude_provider_catalog.py
  - ${code_root}/management_portal/model_api_store.py
  - ${code_root}/scripts/claude_code_account.py
  - ${code_root}/management_portal/execution/account_login.py
  - ${code_root}/management_portal/execution/account_hosting.py
  - ${code_root}/management_portal/execution/provider_updates.py
  - ${code_root}/management_portal/static/portal_management_backend.js
  - ${code_root}/management_portal/static/portal_management_backend.css
  - ${code_root}/management_portal/static/home.js
  - ${code_root}/management_portal/http_portal_session.py
  related:
  - decision_ab9880dc4c9d
  - decision_15aab18c0ab3
  - decision_d40f59f5508f
  - decision_6c2f91a7d84e
  - decision_4dea0db1f45a
  - decision_3dd52c0692c1
  - decision_e587fe1c472e
  - decision_8b2fa224616b
  - decision_850e6248d680
  - decision_b820faf42218
  supersedes: []
  derived_from: []
---

# AI Backend 注册与管理页：Provider、执行账号与模型 API

Resident 的每一轮工作通过一个 AI Backend 执行：某个 Provider（Codex、Claude Code、Kimi Code、DSH、OpenCode 或直连模型 API）上的一个执行账号。Provider 在代码中声明（`provider_catalog.AGENT_PROVIDERS`），执行账号统一登记在数据库中，账号适配器从登记派生执行身份。管理员通过 Portal 管理的“AI Backend”分栏登记账号、完成订阅登录和管理 Backend 角色；Home 继续展示账号额度与调度状态。本 Design 说明这三层对象、登记与登录的所有权，以及 Backend 角色怎样把随发行数据库带来的 Resident 和站点默认值映射到实际账号。

额度保护、调度设置和账号目录接口见 decision_d40f59f5508f；模型 API 条目格式与 Chat Completion 见 decision_6c2f91a7d84e；Codex 路由与故障切换见 decision_3dd52c0692c1；Provider 运行时的安装与更新见 decision_e587fe1c472e；Resident 的执行配置见 decision_8b2fa224616b。

## 基本概念

- **Provider**：代码中声明的一种后端：它的能力、账号适配器、运行时更新器和会话 Adapter。安装状态（运行时已安装的版本、可用版本、待切换）由运行时更新器给出。
- **执行账号（execution account）**：一个 Provider 上可以承接模型调用的身份，`account_id` 全局唯一（decision_d40f59f5508f）。按取得方式分三种：`subscription_login`，需要交互登录的订阅账号（Codex 账号、Claude 原生订阅）；`api_profile`，引用一个模型 API 条目的账号（DSH 与 OpenCode 的路线、Claude-on-DeepSeek、独立 API 账号）；`service`，Provider 服务自带的单一账号（Kimi Code）。
- **模型 API 条目**：可直接调用的 HTTP 模型接口的登记，表 `model_apis.apis`（decision_6c2f91a7d84e）。
- **账号登记（registration）**：数据库中描述一个执行账号的记录；账号适配器从登记派生 `ExecutionAccount`。
- **登录会话（login session）**：Portal 为一个订阅账号运行的一次登录流程，有设备码或授权链接和状态。
- **Backend 角色（backend role）**：具名的执行配置槽位，例如 `default`、`coding_default`、`router_fast`，映射到一个执行账号加默认模型与推理强度。随发行数据库带来的 Resident、站点默认值和新 Resident 的默认设置都引用角色。
- **站点账号根目录与站点密钥目录**：站点配置中的两个目录（decision_15aab18c0ab3）。前者放各账号的 Provider 目录（`CODEX_HOME`、Claude 配置目录），后者放 Portal 代为写入的密钥文件。

## 设计点

### 登记保存在数据库中

- **执行账号的登记保存在 PostgreSQL schema `execution_accounts`。** 表 `registrations` 记录 `account_id`、`provider`、`kind`、`route_id`、Provider 专有配置（JSONB：Codex 的备用账号列表与是否默认；Claude 的模型目录；`api_profile` 的 `api_id`）、初始并发上限、`enabled`、版本和修改审计。账号适配器从登记读取配置。
- **模型 API 条目只保存可直接调用的 HTTP 接口。** 订阅登录账号一律使用 `subscription_login` 执行账号登记，不在模型 API 表中保存订阅 profile。DSH 与 OpenCode 的服务路线继续用 `model_apis.routes` 绑定条目，每条绑定对应一个 `api_profile` 登记。
- **账号的主机目录由登记推导，不保存在记录里。** Codex 账号的 `CODEX_HOME` 是 `<站点账号根目录>/codex/<account_id>`，App Server 的 socket 是 `<实例根目录>/sockets/codex-<account_id>.sock`；Claude 原生订阅的配置目录是 `<站点账号根目录>/claude/<account_id>`。目录在登记时创建，删除登记时连同其中的凭据删除。
- **Provider 的账号服务由登记驱动。** 需要常驻进程的账号（Codex App Server）使用 systemd 模板单元 `portal-codex-app-server@.service`，实例名就是 `account_id`：登记启用的账号时准备并启动实例，启动受账号认证文件条件约束；停用只停止新工作派发，保留进程以承接既有运行；满足空闲条件后删除登记时移除实例。服务操作经服务控制面执行（decision_850e6248d680）。重新启用账号使用同一个模板实例控制入口。账号目录的创建和删除不接受软链接目录。
- **登记存储与部署变更保留账号身份。** 账号 ID、路由 ID 和容量键保持稳定，已保存的调度设置、额度状态和会话归属继续有效。
- **删除登记要求账号空闲。** 没有会话绑定到它（需要先用 `RestartSession` 迁移）、没有排队或运行中的执行时才能删除；否则只能停用。停用的账号不接收新工作，已绑定会话的 Resident 在会话设置中看到“账号已停用”。
- **调度设置的修改仍走 `UpdateProviderAccount`。** 并发、派发间隔与 Close 阈值的规则不变（decision_d40f59f5508f）。

### 登录在页面中完成

- **订阅账号在页面内登录。** 管理员点“登录”后，Portal 在该账号目录下启动 Provider 原生登录命令。Codex 展示设备码和授权链接；Claude 使用 `claude auth login --claudeai`，展示授权链接，并在 `session.requires_code_input` 为 true 时提供一次性返回码输入。页面通过 `UpdateProviderAccountLogin.authorization_code` 把返回码交给已有的 `awaiting_user` Claude 会话；Codex 不接受该输入。授权完成后，Portal 分别用 `codex login status` 或 `claude auth status --json` 确认成功，账号状态变为 `logged_in`。
- **每个账号同一时间只有一个登录会话。** 会话状态为 `pending`、`awaiting_user`、`succeeded`、`failed`、`expired`；15 分钟内未完成即过期，可以重新开始。跨进程互斥由账号文件锁保证；取消终止登录进程组。`GetProviderAccountLogin` 同时返回 `session` 与 `login_state`，尚未开始过登录时 `session` 为 null。页面只接收解析后的验证码、链接、状态与固定错误提示，不接收原始 CLI 输出或凭据文件。Claude 返回码经权限 600 的私有 Unix socket 送入活跃进程 stdin，不写入数据库、文件或回执，也不回显。
- **登录状态持续观察，并与登记配置分开保存。** `execution_accounts.login_sessions` 保存一次登录流程；`login_states` 以 `account_id` 为主键保存 `state` 和 `observed_at`，观察值为 `logged_in`、`login_expired`、`logged_out`。认证观察不修改登记配置版本，较旧观察不能覆盖较新的登录结果。AccountMonitor（decision_d40f59f5508f）在 Provider 报告认证失败时标为 `login_expired`；有效 Provider 观察或成功登录恢复 `logged_in`。登记公开对象与账号列表共用该观察，Home 和管理分栏显示状态并提供“重新登录”；非订阅账号显示 `not_applicable`。
- **密钥通过单次提交写入文件。** `CreateModelApi`、`UpdateModelApi` 可接收一次性 `secret`，Portal 原子写入 `<站点密钥目录>/<api_id>.key`（权限 600），记录只保存路径；提交内容不回显、不进入返回值、日志和传输诊断。轮换密钥就是再次提交，也可以填写已有文件的路径。`api_profile` 通过 `config.api_id` 引用 HTTP 接口。添加模型 API 作为执行后端时，用户一次填写地址、模型和凭据，页面负责创建接口及关联的账号登记；也可选用已有接口。内部仍复用现有指令，重复提交复用已创建的接口，部分失败时说明结果并允许继续。

### Backend 角色

- **角色表把名字映射到执行配置。** 表 `execution_accounts.backend_roles` 记录 `role`、`account_id`、`model`、`reasoning_effort`、版本和修改审计。站点默认账号作为通常使用的执行配置，页面负责维护其 `default` 映射。用户需要为不同用途指定账号或模型时，再配置其他角色。
- **角色在创建 Resident 或初始化发行数据库时解析成具体设置。** 新 Resident 按角色取得 Provider、路线、模型和推理强度并保存为自己的执行配置（decision_8b2fa224616b）。新建 Resident 未指定 Provider/账号时以及新建 AMA、Project principal 取站点 default。用户可以在配置首个 AI 前取得 AMA 身份。没有路线且没有 Provider 会话绑定的 Resident 在首次执行前按本地角色映射持久保存配置；查询执行门禁使用同一映射。已有路线或会话绑定不随默认角色改变。Kimi 的服务账号沿 Provider 自有路线配置，不把其合法空路线解释为未配置。
- **没有映射的角色回退到 `default`。** 回退发生时 Resident 的会话设置标出“角色 `<名>` 未映射，使用站点默认”。

### 管理页

- **Portal 管理新增“AI Backend”分栏，分四个区域。** Provider 区列出声明的 Provider、运行时已安装与可用版本、待切换状态，提供“检查更新”（调用运行时更新检查）；执行账号区是登记表格，提供新增、登录、停用、启用、删除和编辑初始并发；模型 API 区提供条目的新增、修改、删除和“测试连接”（发送一次最小的 Chat Completion）；Backend 角色区编辑角色映射。
- **Home 账号面板保持运行视图的职责。** 额度、余额、Credits、调度设置和 Resident 明细仍在 Home（decision_4dea0db1f45a）；管理分栏负责登记与生命周期。Home 的账号行只保留“详情”；详情内提供“AI Backend”入口，打开管理分栏并直接定位当前 account_id，沿用统一账号设置、保存和权限检查。只有余额或账单能力的账号也提供详情和该设置入口，不调用其不支持的 Resident 明细接口；账单链接仍在详情中。状态为 `login_expired` 的行直接给出“重新登录”。
- **修改需要“Portal 运维”节点的 admin，查看需要对“共享”的 viewer。** 密钥文件路径与主机目录只对 admin 显示。权限系统关闭时任何已登录 User 可用。
- **初次配置使用现有管理页面的接入流程。** 登记后打开“接入并启用 AI”，已有登记可随时打开“接入与测试”。显示运行环境、授权、模型和声明的工具能力，缺失时准备所选 Provider，完成授权并选择模型，测试通过后可设为默认。允许使用开发机或目标机已经安装的程序、已经授权的账号；不要求重新安装、重新登录或专用隔离目录。高级角色映射继续按需使用。

### 与发行的关系

发行内容附带 Provider 接入代码，使用者通过现有管理页面或指令登记自己的 AI Backend，所需运行时按 decision_e587fe1c472e 安装。

发行数据库保留 Resident 的 Backend 角色，去除源站点的执行账号绑定和认证数据。导入后，Resident 使用本站的角色映射，未配置的角色回退到站点默认（decision_15aab18c0ab3）。

### 接入检查

环境由 Provider 现有 runtime updater 负责，账号授权由原登录/API 配置 owner 负责；接入层只组合观察与动作。GetProviderAccountSetup 返回 runtime_ready、preparation、login_state、models、default_model 和 supports_tools。登记成功不等于可执行；授权观察失败保留登记并显示 unavailable。

CheckProviderAccount 使用普通 ExecutionService 提交最小消息，禁止工具操作，返回持久 execution_id。GetProviderAccountCheck 按调用者权限读取，要求实际完成且回答为 PORTAL_PROVIDER_OK，登记版本与模型 API 版本保持有效。页面“测试并完成”选中默认时将回执交给 UpdateBackendRole，并使用角色版本 CAS；测试失败不得设置默认。工具能力来自 Provider 声明，本测试只证明模型回复。页面关闭不丢失服务端准备任务；失败可重试。

## 实现说明

### 指令

| 指令 | 作用 |
| --- | --- |
| `ListProviders` | 声明的 Provider、能力摘要、运行时安装状态与可用版本；可选 `refresh:true` 检查可用版本并保存观察，不安装或激活运行时 |
| `RegisterProviderAccount` | 新建执行账号登记：`account_id`、`provider`、`kind`、可选 `route_id`、`config`、初始并发和启停状态；`api_profile` 用 `config.api_id` 引用已有接口，可附带一次性 `secret` 更新凭据 |
| `UpdateProviderAccountRegistration` | 业务参数为 `account_id`、`changes`，修改配置、`enabled` 与初始并发；版本前提通过调用信封 `expected_version` 传入 |
| `DeleteProviderAccount` | 删除空闲账号的登记、目录与服务实例 |
| `OpenProviderAccountLogin`、`UpdateProviderAccountLogin`、`GetProviderAccountLogin`、`CancelProviderAccountLogin` | Open 打开订阅登录会话；Update 向现有 Claude 会话提交 `authorization_code`；会话带 `requires_code_input`；Get 同时返回 `login_state`，无会话时 `session:null`；Cancel 取消登录 |
| `ListBackendRoles`、`UpdateBackendRole` | 角色映射的读取与修改；更新参数使用 `backend_role`、`account_id` 及可选的 `model`、`reasoning_effort`，结果行仍使用 `role` |
| `ListProviderAccounts`、`UpdateProviderAccount` | 现有指令：账号目录与额度读数、调度设置；列表附带 `registration`、`registration_version`、`kind`、`enabled`、`login_state`，登记对象提供配置、初始并发及 admin 可见的主机路径 |
| `ListModelApis`、`CreateModelApi`、`UpdateModelApi`、`DeleteModelApi` | 现有指令；创建与更新增加可选的一次性 `secret` |

全部由 ExecutionService 执行。写指令在权限方案中映射到“Portal 运维”节点的 admin。

登记更新和角色更新的业务参数不再重复 `expected_version`。两条指令从 HATG 调用信封读取版本前提，支持标量版本或以对应账号 ID / 角色名为键的映射；新建角色使用版本 0。省略前提时直接更新；版本不符时以 `instruction.version_conflict` 失败，`details` 给出 `object_id`、`expected_version` 与 `current_version`。执行服务只对模型接口指令按模型接口版本比较，执行账号或角色与某个模型接口同名（例如 `claude_one`）时，比较仍以账号登记或角色自己的版本为准。调用方分别使用登记版本与角色版本；`backend_role` 的参数名与 `CreateResident` 保持一致。

登录指令遵循既有 ISA 动词表（decision_898b8911a2ff）：打开会话使用 Open，提交返回码使用 Update。

### 页面行为

Home 与管理页使用同一账号身份和登录状态。模型 API 以 `api_id` 标识，账号登记初值与实时调度值分别展示。服务账号可登记并复用 Provider 已有的服务配置和授权；登记后的服务身份不通过页面编辑、停用或删除；删除繁忙账号时显示关联会话和执行情况。返回码与一次性密钥提交后清空，不回填。

### 运行状态与服务

Provider 版本检查只观察可用版本，不安装或激活运行时，也不改变待切换状态；各 Provider 的检查失败互不影响。安装与切换由现有运行时更新器负责。

登录状态由登录服务统一保存。尚无观察时，在隔离的账号环境中查询原生认证状态；查询不修改目录或启停服务，迟到的结果不能覆盖较新观察。无法查询时返回可重试的 `instruction.owner_unavailable`（reason 为 `account.login_unavailable`），不把失败当作已登出，也不暴露原始异常。

各 Provider 适配器从登记派生执行配置。登录进程只使用对应账号的配置目录与站点出口设置；删除账号前先结束登录进程，再清理状态和凭据。

### 已有账号与部署

账号身份及历史用量归属在登记或部署调整后保持稳定，不把不同 Backend 的同名接口合并。安装服务模板不自动启停现有 Provider 实例，实际出口遵循站点配置。

执行账号迁移已完成；遗留兼容链接的清理条件见 `decision_ab9880dc4c9d`。历史操作可从 Git 提交 `4de8486a282bb2c77af6b44f20f9e5ccd477d718` 追溯。
