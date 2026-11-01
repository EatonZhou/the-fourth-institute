---
kb_id: decision_7956d7cb4bde
type: decision
project: AI员工
created_at: '2026-10-18'
version:
  number: 37
  created_at: '2026-10-18T14:45:00Z'
  created_by:
    user: tfi-exported-user
    agent: resident_667111522609
  updated_at: '2026-10-31T18:16:20Z'
  updated_by:
    user: ''
    agent: resident_24014e2b9bf6
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
- **执行账号（execution account）**：一个 Provider 上可以承接模型调用的身份，`account_id` 全局唯一（decision_d40f59f5508f）。按取得方式分三种：`subscription_login`，需要交互登录的订阅账号（Codex、Claude Code、Kimi Code 原生订阅）；`api_profile`，引用一个模型 API 条目的账号（DSH 与 OpenCode 的路线、Claude-on-DeepSeek、独立 API 账号）；`service`，Provider 已有服务身份（例如既有 Kimi Code 服务）；Kimi 也支持独立绑定 API 的 api_profile 账号。
- **模型 API 条目**：可直接调用的 HTTP 模型接口的登记，表 `model_apis.apis`（decision_6c2f91a7d84e）。
- **账号登记（registration）**：数据库中描述一个执行账号的记录；账号适配器从登记派生 `ExecutionAccount`。
- **登录会话（login session）**：Portal 为一个订阅账号运行的一次登录流程，有设备码或授权链接和状态。
- **Backend 角色（backend role）**：具名的执行配置槽位，例如 `default`、`coding_default`、`router_fast`，映射到一个执行账号加默认模型与推理强度。随发行数据库带来的 Resident、站点默认值和新 Resident 的默认设置都引用角色。
- **账号目录与密钥目录**：账号目录是一个订阅账号的 Provider 目录（`CODEX_HOME`、Claude 配置目录等），属于主机，位置记在登记中；密钥目录是实例根目录的 `secrets/`，放 Portal 代为写入的密钥文件（《Portal 实例协议》）。

## 设计点

### 登记保存在数据库中

- **执行账号的登记保存在 PostgreSQL schema `execution_accounts`。** 表 `registrations` 记录 `account_id`、`provider`、`kind`、`route_id`、Provider 专有配置（JSONB：Codex 的备用账号列表与是否默认；Claude 的模型目录；`api_profile` 的 `api_id`）、初始并发上限、`enabled`、版本和修改审计。账号适配器从登记读取配置。
- **模型 API 条目只保存可直接调用的 HTTP 接口。** 订阅登录账号一律使用 `subscription_login` 执行账号登记，不在模型 API 表中保存订阅 profile。新建 API 执行账号以 config.api_id 显式绑定条目；DSH 与 OpenCode 的既有服务路线保留原有身份及显式绑定。
- **账号目录记在登记里，放在哪里由主机用户决定。** 订阅账号的 Provider 目录（Codex 的 `CODEX_HOME`、Claude 原生订阅的配置目录、Kimi 订阅账号的本地服务与配置目录）保存在登记的 `config.account_dir` 中，是规范绝对路径，不能是软链接（《Portal 实例协议》「执行账号目录」）。登记时必须给出这个目录，目录不存在时 Portal 创建空目录；删除登记不删除目录。App Server 的 socket 放在实例 `runtime/` 下。
- **Provider 的账号服务由登记驱动。** 需要常驻进程的账号（Codex App Server）使用 systemd 模板单元 `<服务前缀>-codex-app-server@.service`（服务前缀是实例的服务名前缀，现在记在 `config/target.json` 的 `service_prefix`；源码模板为 `config/systemd/portal-codex-app-server@.service.in`），实例名就是 `account_id`：登记启用的账号时准备并启动实例，启动受账号认证文件条件约束；停用只停止新工作派发，保留进程以承接既有运行；满足空闲条件后删除登记时移除实例。服务操作经服务控制面执行（decision_850e6248d680）。重新启用账号使用同一个模板实例控制入口。账号目录不接受软链接。
- **登记存储与部署变更保留账号身份。** 账号 ID、路由 ID 和容量键保持稳定，已保存的调度设置、额度状态和会话归属继续有效。
- **删除登记要求账号空闲。** 没有会话绑定到它（需要先用 `RestartSession` 迁移）、没有排队或运行中的执行时才能删除；否则只能停用。停用的账号不接收新工作，已绑定会话的 Resident 在会话设置中看到“账号已停用”。
- **调度设置的修改仍走 `UpdateProviderAccount`。** 并发、派发间隔与 Close 阈值的规则不变（decision_d40f59f5508f）。

### 登录在页面中完成

- **订阅账号在页面内登录。** 管理员点“登录”后，Portal 在该账号目录下启动 Provider 原生登录命令。Codex 展示设备码和授权链接；Claude 使用 `claude auth login --claudeai`，展示授权链接，并在 `session.requires_code_input` 为 true 时提供一次性返回码输入。页面通过 `UpdateProviderAccountLogin.authorization_code` 把返回码交给已有的 `awaiting_user` Claude 会话；Codex 不接受该输入。授权完成后，Portal 分别用 `codex login status` 或 `claude auth status --json` 确认成功，账号状态变为 `logged_in`。Kimi 使用该账号本地原生服务的设备授权接口，复用尚有效的授权流程并按 Provider 返回的到期时间显示设备码；取消与认证观察也交给同一服务。凭据及托管模型配置由 Kimi 写入并保存，Portal 只记录公开授权提示与认证状态；订阅目录只暴露已认证的 `managed:kimi-code` 模型，不回退为 Moonshot API 计费。Portal 工具配置仅合并自身 hook 和 MCP 接入，不将脱敏配置写回凭据。
- **Kimi 登录先选择账号所属区域。** `GetProviderAccountLogin` 返回公开 `login_options`（`regions` 的 id、label 及当前 `region`）；弹窗提供“中国区 / 国际区”，通过 `OpenProviderAccountLogin.region` 提交 `mainland-cn` 或 `global`。新流程默认建议中国区，已有授权流程显示其实际区域；用户点击获取授权后才开始流程。同区域的有效设备码复用，切换区域后由原生服务替换旧授权，并同步更新 Portal 会话有效期。页面在切换时隐藏旧链接和设备码，迟到的旧轮询不能覆盖新流程；失败可重试。区域只影响该账号本次登录，不重写授权 URL、不退出其他账号。其他 Provider 的登录行为保持不变。
- **Codex 登录同步到运行中的后台。** 原生 CLI 确认设备授权成功后，Portal 启动或复用该账号的 App Server，再调用 `account/read` 并指定 `refreshToken:true`，让后台重新读取最新授权。仅启动已运行的服务不会刷新它持有的登录状态；同步使用原生协议，不由 Portal 读取或转发凭据，也不重启后台或替换 Resident 会话。原生读取成功后记录新的登录观察，读取前开始的迟到观察不能覆盖它。后台暂不可用时保留已完成的设备登录，后续接入测试继续报告实际后台错误。
- **每个账号同一时间只有一个登录会话。** 会话状态为 `pending`、`awaiting_user`、`succeeded`、`failed`、`expired`；15 分钟内未完成即过期，可以重新开始。跨进程互斥由账号文件锁保证；取消终止登录进程组。`GetProviderAccountLogin` 同时返回 `session` 与 `login_state`，尚未开始过登录时 `session` 为 null。页面只接收解析后的验证码、链接、状态与固定错误提示，不接收原始 CLI 输出或凭据文件。Claude 返回码经权限 600 的私有 Unix socket 送入活跃进程 stdin，不写入数据库、文件或回执，也不回显。
- **登录状态持续观察，并与登记配置分开保存。** `execution_accounts.login_sessions` 保存一次登录流程；`login_states` 以 `account_id` 为主键保存 `state` 和 `observed_at`，观察值为 `logged_in`、`login_expired`、`logged_out`。认证观察不修改登记配置版本，较旧观察不能覆盖较新的登录结果。AccountMonitor（decision_d40f59f5508f）在 Provider 报告认证失败时标为 `login_expired`；有效 Provider 观察或成功登录恢复 `logged_in`。登记公开对象与账号列表共用该观察，Home 和管理分栏显示状态并提供“重新登录”；非订阅账号显示 `not_applicable`。
- **密钥通过单次提交写入文件。** `CreateModelApi`、`UpdateModelApi` 可接收一次性 `secret`，Portal 原子写入 `<站点密钥目录>/<api_id>.key`（权限 600），记录只保存路径；提交内容不回显、不进入返回值、日志和传输诊断。轮换密钥就是再次提交，也可以填写已有文件的路径。`api_profile` 通过 `config.api_id` 引用 HTTP 接口。添加模型 API 作为执行后端时，用户一次填写地址、模型和凭据，页面负责创建接口及关联的账号登记；也可选用已有接口。内部仍复用现有指令，重复提交复用已创建的接口，部分失败时说明结果并允许继续。

### Backend 角色

- **角色表把名字映射到执行配置。** 表 `execution_accounts.backend_roles` 记录 `role`、`account_id`、`model`、`reasoning_effort`、版本和修改审计。站点默认账号作为通常使用的执行配置，页面负责维护其 `default` 映射。用户需要为不同用途指定账号或模型时，再配置其他角色。
- **角色在创建 Resident 或初始化发行数据库时解析成具体设置。** 新 Resident 按角色取得 Provider、路线、模型和推理强度并保存为自己的执行配置（decision_8b2fa224616b）。新建 Resident 未指定 Provider/账号时以及新建 AMA、Project principal 取站点 default。用户可以在配置首个 AI 前取得 AMA 身份。没有路线且没有 Provider 会话绑定的 Resident 在首次执行前按本地角色映射持久保存配置；查询执行门禁使用同一映射。已有路线或会话绑定不随默认角色改变。Kimi 的服务账号沿 Provider 自有路线配置，不把其合法空路线解释为未配置。
- **没有映射的角色回退到 `default`。** 回退发生时 Resident 的会话设置标出“角色 `<名>` 未映射，使用站点默认”。

### 管理页

首次登录的欢迎卡片保留使用指南与关闭操作，不放置依赖 AI Backend 的 AMA 对话入口。新增执行账号页说明用途和账号指代名称，只展示当前接入必需字段，初始并发上限为 1；备用账号、路线等高级字段在后续管理入口维护。接入方式只有一项时不展示无选择意义的下拉框。

新建 API 账号可在同一流程点击“新建模型 API 并接入”，直接填写接口地址、API Key 和服务商模型名称，并保留已填账号名称及所选运行器。已有兼容接口可以直接复用；没有兼容接口时显示创建入口并禁用空提交。运行器与接口协议仍按显式声明匹配；协议不匹配时要求用户确认，不自动猜测协议或切换运行器。第三方 API 的模型名称沿用服务商实际配置。

接入窗口展示运行环境、登录授权、测试连接三个阶段，并在准备期间显示真实等待时长。服务端在执行依赖检查、版本获取、依赖安装、服务启用前持久化 phase、started_at 与 updated_at，页面轮询显示当前阶段；失败显示脱敏提示和诊断类别，恢复失败时说明需要处理。不伪造下载百分比，准备成功不视为登录或模型检查成功。

- **Portal 管理新增“AI Backend”分栏，分四个区域。** Provider 区列出声明的 Provider、运行时已安装与可用版本、待切换状态，提供“检查更新”（调用运行时更新检查）；执行账号区是登记表格，提供新增、登录、停用、启用、删除和编辑初始并发；模型 API 区提供条目的新增、修改、删除和“测试连接”（发送一次最小的 Chat Completion）；Backend 角色区编辑角色映射。
- **Home 账号面板保持运行视图的职责。** 订阅账号展示额度；API 按实际 billing_account_id 合并余额、活动图标与可见 Resident 统计；原币金额旁按约 ¥10 一枚展示金币，外币依赖有效参考汇率。详情包含节点信息、批量接单，以及有效 billing_url 对应的充值或账单入口。调度与生命周期配置在管理页；Home 顶部保留管理入口，登录失效仍提供重新登录。见 decision_4dea0db1f45a。
- **修改需要“Portal 运维”节点的 admin，查看需要对“共享”的 viewer。** 密钥文件路径与主机目录只对 admin 显示。权限系统关闭时任何已登录 User 可用。
- **初次配置使用现有管理页面的接入流程。** 登记成功即保存连接，页面随后打开“接入并启用 AI”；已有登记可随时打开“接入与测试”。页面自动检查并准备所选 Provider 的运行环境，持续观察准备结果，不要求用户点击准备或刷新；已有运行环境和进行中的准备任务直接复用，准备或读取失败时才提供重试，关闭页面不取消已启动的服务端准备任务。只在需要授权时显示登录入口；已经登录或无需登录的账号不重复授权。正常状态的收尾操作是“测试并完成”，模型填写可选，留空时由服务端启动并读取账号默认模型，用户自行触发，登记与环境准备不自动消耗模型调用额度。测试成功后按勾选项设为默认，关闭接入窗口并显示完成结果；失败保留输入以便修正和重试。显示声明的工具能力，允许使用开发机或目标机已有程序和授权，不要求重新安装、重新登录或专用隔离目录。高级角色映射继续按需使用。 需要订阅授权时先显示登录指引，隐藏模型与默认选项；Codex 明确引导设备码登录，授权完成后再显示可选模型。准备状态后台轮询不得重置为加载页或重建未变化的模型选项；设备码与授权链接未变化时保留节点及链接，避免轮询闪动和打断复制。API 账号行及接入测试窗口直接提供“编辑连接”，复用既有模型 API 编辑器；保存后回到原账号测试，不重复建立接口或账号，留空密钥继续沿用原密钥。

原生 Claude 订阅账号的模型目录与默认模型属于账号登记。新增账号时自动带入本机显式模型偏好；没有偏好时使用 Claude 原生 `sonnet` 别名，并将该默认项登记为初始模型目录。新建表单展示只读默认模型，不要求用户手填；接入后可在“编辑模型目录”中调整。编辑目录时，默认模型必须是其中已启用的一项，模型别名及其他能力字段保留。已有登记通过带登记版本 CAS 的“编辑模型目录”修正，缺目录或目录无效时接入窗口也提供该入口；保存后回到原账号的接入测试，不自动测试、修改默认角色或重建会话。接入测试中的模型字段只选择本次测试模型，不修改登记，不替代目录校验。Claude API 账号的目录仍属于绑定的模型 API，通过“编辑连接”维护。模型名称按用户明确配置传递，不推断或改写上游模型身份。

### 与发行的关系

发行内容附带 Provider 接入代码，使用者通过现有管理页面或指令登记自己的 AI Backend，所需运行时按 decision_e587fe1c472e 安装。

发行数据库保留 Resident 的 Backend 角色，去除源站点的执行账号绑定和认证数据。导入后，Resident 使用本站的角色映射，未配置的角色回退到站点默认（decision_15aab18c0ab3）。

### 接入检查

环境由 Provider 现有 runtime updater 负责，账号授权由原登录/API 配置 owner 负责；接入层只组合观察与动作。GetProviderAccountSetup 返回 runtime_ready、preparation、login_state、models、default_model 和 supports_tools。登记成功不等于可执行；授权观察失败保留登记并显示 unavailable。

CheckProviderAccount 使用普通 ExecutionService 提交最小消息，禁止工具操作，返回持久 execution_id。GetProviderAccountCheck 按调用者权限读取，要求实际执行完成并返回非空文本，执行结果不得带错误或失败/取消终态，登记版本与模型 API 版本保持有效。连通性不依赖回复的固定措辞、语言或标点；不将非文本对象转换为可用回复。页面“测试并完成”选中默认时将回执交给 UpdateBackendRole，并使用角色版本 CAS；测试失败不得设置默认。工具能力来自 Provider 声明，本测试只证明模型回复。页面关闭不丢失服务端准备任务；失败可重试。接入测试的失败展示由 account_setup 根据已有 Execution 结果与脱敏传输观察生成，区分认证、访问限制、接口或模型不存在、额度、网络、服务商异常和空回复；未识别原因必须明确为未知，不推断密钥无效。返回固定提示、下一步建议与白名单诊断类别，可附实际记录的 HTTP 状态码；页面保留输入并显示可复制的测试编号。不得回传原始异常、服务商响应正文、请求头或凭据。 测试结束且未通过时，页面提供“显示详细日志”按钮，可展开或收起只读、可选择复制的本次诊断日志；正常及测试进行中不显示该按钮，重试立即清除旧日志。日志仅包含经脱敏的测试编号、账号、运行器、模型、提交时间、终态、固定失败分类与建议，以及白名单传输观察（实际记录的 HTTP 状态、失败阶段、耗时等）；任意异常正文、请求提示和模型响应正文均不进入日志。沿用执行读取授权及账号匹配校验，不提供任意服务器日志读取入口。

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

Home 的订阅行沿用执行账号身份与登录状态，API 行按计费 API 汇总；管理页仍保留每条执行路线。模型 API 以 `api_id` 标识，账号登记初值与实时调度值分别展示。服务账号可登记并复用 Provider 已有的服务配置和授权；登记后的服务身份不通过页面编辑或停用；已登记的服务账号允许通过页面删除登记，仍受会话、执行和角色引用保护，删除不管理 Provider 自有共享服务；没有登记版本的 Provider 内置身份不提供删除；删除繁忙账号时显示关联会话和执行情况。返回码与一次性密钥提交后清空，不回填。

### 运行状态与服务

Provider 版本检查只观察可用版本，不安装或激活运行时，也不改变待切换状态；各 Provider 的检查失败互不影响。安装与切换由现有运行时更新器负责。

登录状态由登录服务统一保存。尚无观察时，在隔离的账号环境中查询原生认证状态；查询不修改目录或启停服务，迟到的结果不能覆盖较新观察。无法查询时返回可重试的 `instruction.owner_unavailable`（reason 为 `account.login_unavailable`），不把失败当作已登出，也不暴露原始异常。

各 Provider 适配器从登记派生执行配置。登录进程只使用对应账号的配置目录与站点出口设置；删除账号前先结束登录进程，再清理状态和凭据。

### 已有账号与部署

账号身份及历史用量归属在登记或部署调整后保持稳定，不把不同 Backend 的同名接口合并。安装服务模板不自动启停现有 Provider 实例，实际出口遵循站点配置。

执行账号迁移已完成；遗留兼容链接的清理条件见 `decision_ab9880dc4c9d`。

## API 与 Agent 运行器的兼容入口

模型 API 编辑器按 Provider 声明提供执行器选择：Chat Completions 可用于 OpenCode、Kimi Code 和直连；Anthropic Messages 可用于 Claude Code、DeepSeek Harness；OpenAI Responses 可用于 Codex。未声明所需协议的执行器仍显示，但禁用并解释所需协议；移除声明时清除不兼容的选择。支持 HTTP 协议不等于模型支持完整 Agent 工具调用，接入模型需具备相应工具能力。用户可手动声明协议，或主动触发已有协议探测；添加、登记及展示选项本身不调用模型。

Kimi API 账号的模型目录来自所绑定的 API，并拥有独立本地服务与会话目录；不修改既有 Kimi 服务账号。DSH 允许多个显式绑定的 Messages API 账号，并依据各自声明传递认证和请求头。账号注册、测试、默认角色、停用和删除复用公共登记及引用保护，不为新增执行器绕开繁忙、会话或角色引用检查。模型与执行器选择不依据模型名称猜测接口协议。

### 使用本机已有的登录

要使用人已经在终端里登录过的 Codex 或 Claude 账号，登记时把那个账号目录填为账号目录，Portal 直接使用它，不复制凭据文件（《Portal 实例协议》「执行账号目录」）。两边用同一个目录，共享同一份登录和会话；复制会让两处各持一份 refresh token，一方刷新后另一方就失效。登记只记录目录，不代替真实连接测试；删除登记不改动这个目录。


## 保存模型 API 后的兼容路线

CreateModelApi 与 UpdateModelApi 成功保存已启用接口后，Execution 自动补齐直连路线及所有声明协议兼容的 Harness 登记；返回 accounts。Chat Completions 对应 Kimi、OpenCode；Anthropic Messages 对应 Claude、DSH；Responses 对应 Codex。Jev（typesafe_system_one）只提供直连，不进入文本 Agent Harness。前端选择一个运行器只决定后续接入测试，不限制创建 Resident 的可选范围。登记过程不安装运行器、不初始化模型回合。

已有同 API、同 Provider 的路线直接复用（含停用路线），不改变 ID、并发与停用设置。直连优先复用与 API 同 ID 的已有原生路线；新建直连以 API ID 为账号 ID，新 Harness 默认以 <api_id>-<provider> 命名，超长或冲突使用稳定摘要。API ID 必须满足执行账号 ID 格式，不能占用无关账号。新路线初始并发为 1。UpdateModelApi 的空 changes 可用于补齐既有 API；删除 API 或移除协议仍受执行账号引用保护。

路线登记批次由 Execution 的数据库事务与 API 级锁保护。接口保存成功而路线登记失败时，错误 reason=model_api.routes_incomplete 明示 api_saved 和 api_id；页面重试使用 UpdateModelApi，不重复创建接口或提交已清空的密钥。账号读目录不偷偷写登记，原有账号权限和调度入口继续有效。
