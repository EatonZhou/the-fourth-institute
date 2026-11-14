---
kb_id: decision_7956d7cb4bde
type: decision
project: AI员工
created_at: '2026-10-18'
version:
  number: 53
  created_at: "2026-10-18T14:45:00Z"
  created_by:
    user: "ET"
    agent: "resident_667111522609"
  updated_at: "2026-11-14T10:04:39Z"
  updated_by:
    user: ""
    agent: "resident_60ae6d0cdf74"
importance: 8
metaness: 2
ai_generated: false
links:
  source:
  - /opt/tfi/the-fourth-institute-dev/code/management_portal/execution/account_registrations.py
  - /opt/tfi/the-fourth-institute-dev/code/management_portal/execution/provider_catalog.py
  - /opt/tfi/the-fourth-institute-dev/code/management_portal/execution/accounts.py
  - /opt/tfi/the-fourth-institute-dev/code/management_portal/execution/account_adapters.py
  - /opt/tfi/the-fourth-institute-dev/code/management_portal/claude_provider_catalog.py
  - /opt/tfi/the-fourth-institute-dev/code/management_portal/model_api_store.py
  - /opt/tfi/the-fourth-institute-dev/code/scripts/claude_code_account.py
  - /opt/tfi/the-fourth-institute-dev/code/management_portal/execution/account_login.py
  - /opt/tfi/the-fourth-institute-dev/code/management_portal/execution/account_hosting.py
  - /opt/tfi/the-fourth-institute-dev/code/management_portal/execution/provider_updates.py
  - /opt/tfi/the-fourth-institute-dev/code/management_portal/static/portal_management_backend.js
  - /opt/tfi/the-fourth-institute-dev/code/management_portal/static/portal_management_backend.css
  - /opt/tfi/the-fourth-institute-dev/code/management_portal/static/home.js
  - /opt/tfi/the-fourth-institute-dev/code/management_portal/http_portal_session.py
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
  - decision_4aa13bc21e8e
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
- **Backend 角色（backend role）**：具名的执行配置槽位，例如 `default`、`coding_default`、`router_fast`，保存后端/计费范围参考账号、模型与推理强度，以及统一的 account_selection（auto / fixed）。随发行数据库带来的 Resident、站点默认值和新 Resident 的默认设置都引用角色。
- **账号目录与密钥目录**：账号目录是一个订阅账号的 Provider 目录（`CODEX_HOME`、Claude 配置目录等），属于主机，位置记在登记中；密钥目录是实例根目录的 `secrets/`，放 Portal 代为写入的密钥文件（《Portal 实例协议》）。

## 设计点

### 登记保存在数据库中

- **执行账号的登记保存在 PostgreSQL schema `execution_accounts`。** 表 `registrations` 记录 `account_id`、`provider`、`kind`、`route_id`、Provider 专有配置（JSONB：Claude 的模型目录；`api_profile` 的 `api_id`）、初始并发上限、`enabled`、版本和修改审计。账号适配器从登记读取配置。
- **模型 API 条目只保存可直接调用的 HTTP 接口。** 订阅登录账号一律使用 `subscription_login` 执行账号登记，不在模型 API 表中保存订阅 profile。新建 API 执行账号以 config.api_id 显式绑定条目；DSH 与 OpenCode 的既有服务路线保留原有身份及显式绑定。
- **订阅账号目录由系统生成并显示，用户可修改。** 新增页面按当前主机服务进程用户的主目录 `.<provider>-accounts/<account_id>` 显示默认绝对路径；该目录属于主机，位于实例可退役数据区域之外。无需先创建目录或登录，用户填写账号名称即可继续，也可修改路径以复用已有登录。`ListProviders` 仅向有新增账号权限的调用者提供 `account_directory_prefix`，前端据此展示，不硬编码主机目录。`RegisterProviderAccount` 在 `config.account_dir` 省略或留空时使用同一默认规则，保存前解析并校验，持久登记始终保存实际规范绝对路径；目录不能是软链接。登记保存时创建缺失目录，随后自动进入环境准备、辅助登录与连接测试。自定义目录和现有凭据按原规则就地复用，删除登记或退役实例都不删除主机账号目录。App Server 的 socket 留在实例 `runtime/` 下。
- **Provider 的账号服务由登记驱动。** 需要常驻进程的账号（Codex App Server）使用 systemd 模板单元 `<服务前缀>-codex-app-server@.service`（服务前缀是实例的服务名前缀，现在记在 `config/target.json` 的 `service_prefix`；源码模板为 `config/systemd/portal-codex-app-server@.service.in`），实例名就是 `account_id`：登记启用的账号时准备并启动实例，启动受账号认证文件条件约束；停用只停止新工作派发，保留进程以承接既有运行；满足空闲条件后删除登记时移除实例。服务操作经服务控制面执行（decision_850e6248d680）。重新启用账号使用同一个模板实例控制入口。账号目录不接受软链接。
- **登记存储与部署变更保留账号身份。** 账号 ID、路由 ID 和容量键保持稳定，已保存的调度设置、额度状态和会话归属继续有效。
- **删除登记要求账号空闲。** 没有会话绑定到它（需要先用 `RestartSession` 迁移）、没有排队或运行中的执行时才能删除；否则只能停用。停用的账号不接收新工作，已绑定会话的 Resident 在会话设置中看到“账号已停用”。
- **调度设置的修改仍走 `UpdateProviderAccount`。** 并发、派发间隔与 Close 阈值的规则不变（decision_d40f59f5508f）。

### 登录在页面中完成

- **账号目录发现与浏览由 Execution 的主机目录服务统一提供。** 管理页使用已认证的 `GET /api/management/account-directories`，`action=discover&provider=...` 发现候选，`action=browse&path=...` 浏览目录；服务端在读取目录或登记列表前，用真实调用者检查既有 `RegisterProviderAccount` 权限，原方案的允许、拒绝和临时授权直接生效。不新增 HATG 指令、权限元素或权限别名，也不迁移既有方案。HTTP 层向纯发现引擎显式提供本实例全部已登记目录用于排除，离线脚本不访问 Portal 登记。 通用只读发现引擎按各 Provider 的目录和标志规则，检查环境变量、主机用户常见配置位置和 Portal 生成目录；采用有界广度与深度，不遍历整盘、不读取或返回凭据值，不修改发现的目录或启动模型。候选规范化去重，排除本实例已登记路径；不可读位置与截断通过公开提示说明。独立命令行扫描脚本复用同一引擎。管理页目录 HTTP 接口使用既有 `RegisterProviderAccount` 的权限边界，不向只读用户暴露主机目录。
- **目录输入框提供标准文件夹选择器。** 使用 Windows 文件夹选择器与 GNOME Files 已有的布局和操作习惯：顶部为紧凑标题与“Portal 主机”位置标识，导航区提供后退、前进、上一级、可点击的路径面包屑及可编辑地址（Ctrl+L）；左侧为主目录、根目录等服务实际返回的可用位置；主体为文件夹图标、左对齐名称、紧凑行距和选中高亮的目录列表，可过滤当前目录名称。单击只选中，双击或 Enter 进入，方向键与 Home/End 移动选择；触屏有明确进入动作。底部显示当前选择，并以“取消 / 选择文件夹”明确提交。进入目录、编辑地址或取消均不改外层输入，只有成功读取并确认的规范目录可回填；无子目录时仍可选当前目录。
- **目录浏览与确认分别持有状态。** 地址编辑、加载、失败或无效路径时不能误提交上次结果；后退/前进按成功导航维护，迟到响应和关闭后的响应不得回写。列表区域展示加载、空目录、权限拒绝、路径不存在及截断提示，允许从有效位置恢复。Esc 关闭后焦点返回触发入口，窄屏下导航、列表和底部操作保持在可见范围。
- **复用边界。** 继续复用 Portal 对话框生命周期、样式基元与既有管理页目录 HTTP 接口，权限仍由服务器按真实调用者检查 RegisterProviderAccount；不新增 HATG 指令、权限或后端目录能力。Windows/GNOME 原生选择器属于操作系统/桌面工具包，浏览器的本地文件夹 API 指向访问者设备，不能代表远端 Portal 主机，因此沿用它们的交互范式而不将本地路径误交给服务器。Workspace 文件树负责已登记资源、预览和布局，不作为主机原始目录选择器的数据源。只枚举目录名称与规范路径，不返回文件内容，不伪造修改时间、大小等元数据；不存在的待创建默认目录可从主目录开始浏览。
- **界面参照。** [Windows 官方文件夹选择器](https://learn.microsoft.com/en-us/windows/apps/develop/files/using-file-folder-pickers)与[GNOME Files 实际界面](https://apps.gnome.org/Nautilus/)；以沿用熟悉的浏览行为、提高目录列表占比为依据，替换原先居中大按钮与多排路径表单。

- **订阅账号在页面内登录。** 管理员点“登录”后，Portal 在该账号目录下启动 Provider 原生登录命令。Codex 展示设备码和授权链接；Claude 使用 `claude auth login --claudeai`，展示授权链接，并在 `session.requires_code_input` 为 true 时提供一次性返回码输入。页面通过 `UpdateProviderAccountLogin.authorization_code` 把返回码交给已有的 `awaiting_user` Claude 会话；Codex 不接受该输入。授权完成后，Portal 分别用 `codex login status` 或 `claude auth status --json` 确认成功，账号状态变为 `logged_in`。Kimi 使用该账号本地原生服务的设备授权接口，复用尚有效的授权流程并按 Provider 返回的到期时间显示设备码；取消与认证观察也交给同一服务。凭据及托管模型配置由 Kimi 写入并保存，Portal 只记录公开授权提示与认证状态；订阅目录只暴露已认证的 `managed:kimi-code` 模型，不回退为 Moonshot API 计费。Portal 工具配置仅合并自身 hook 和 MCP 接入，不将脱敏配置写回凭据。
- **Kimi 登录先选择账号所属区域。** `GetProviderAccountLogin` 返回公开 `login_options`（`regions` 的 id、label 及当前 `region`）；弹窗提供“中国区 / 国际区”，通过 `OpenProviderAccountLogin.region` 提交 `mainland-cn` 或 `global`。新流程默认建议中国区，已有授权流程显示其实际区域；用户点击获取授权后才开始流程。同区域的有效设备码复用，切换区域后由原生服务替换旧授权，并同步更新 Portal 会话有效期。页面在切换时隐藏旧链接和设备码，迟到的旧轮询不能覆盖新流程；失败可重试。区域只影响该账号本次登录，不重写授权 URL、不退出其他账号。其他 Provider 的登录行为保持不变。
- **Codex 登录同步到运行中的后台。** 原生 CLI 确认设备授权成功后，Portal 启动或复用该账号的 App Server，再调用 `account/read` 并指定 `refreshToken:true`，让后台重新读取最新授权。仅启动已运行的服务不会刷新它持有的登录状态；同步使用原生协议，不由 Portal 读取或转发凭据，也不重启后台或替换 Resident 会话。原生读取成功后记录新的登录观察，读取前开始的迟到观察不能覆盖它。后台暂不可用时保留已完成的设备登录，后续接入测试继续报告实际后台错误。
- **每个账号同一时间只有一个登录会话。** 会话状态为 `pending`、`awaiting_user`、`succeeded`、`failed`、`expired`；15 分钟内未完成即过期，可以重新开始。跨进程互斥由账号文件锁保证；取消终止登录进程组。`GetProviderAccountLogin` 同时返回 `session` 与 `login_state`，尚未开始过登录时 `session` 为 null。页面只接收解析后的验证码、链接、状态与固定错误提示，不接收原始 CLI 输出或凭据文件。Claude 返回码经权限 600 的私有 Unix socket 送入活跃进程 stdin，不写入数据库、文件或回执，也不回显。
- **登录状态持续观察，并与登记配置分开保存。** `execution_accounts.login_sessions` 保存一次登录流程；`login_states` 以 `account_id` 为主键保存 `state` 和 `observed_at`，观察值为 `logged_in`、`login_expired`、`logged_out`。认证观察不修改登记配置版本，较旧观察不能覆盖较新的登录结果。AccountMonitor（decision_d40f59f5508f）在 Provider 报告认证失败时标为 `login_expired`；有效 Provider 观察或成功登录恢复 `logged_in`。登记公开对象与账号列表共用该观察，Home 和管理分栏显示状态并提供“重新登录”；非订阅账号显示 `not_applicable`。

登录状态的持久观察保持时间栅栏：只有来源时间不早于当前记录的观察才可更新，不能因状态看起来相同而放宽旧结果保护。相同账号的 `state` 与 `observed_at` 都完全相同时，重复观察不更新当前 `login_states` 行；同时间但状态变化仍可更新，新时间但状态相同仍推进时间栅栏，较旧观察仍拒绝覆盖，记录删除后相同来源观察仍可重新插入。

当前状态行的同值去重不合并观察尝试历史。AccountMonitor 每次既有观察尝试仍沿原历史记录路径保存，查询、UPSERT 与事务往返也仍执行；不以跨请求缓存替代当前状态核对，不改变登录会话、凭据探测、公开投影或权限语义。
- **密钥通过单次提交写入文件。** `CreateModelApi`、`UpdateModelApi` 可接收一次性 `secret`，Portal 原子写入 `<站点密钥目录>/<api_id>.key`（权限 600），记录只保存路径；提交内容不回显、不进入返回值、日志和传输诊断。轮换密钥就是再次提交，也可以填写已有文件的路径。`api_profile` 通过 `config.api_id` 引用 HTTP 接口。添加模型 API 作为执行后端时，用户一次填写地址、模型和凭据，页面负责创建接口及关联的账号登记；也可选用已有接口。内部仍复用现有指令，重复提交复用已创建的接口，部分失败时说明结果并允许继续。

### Backend 角色

- **角色表把名字映射到执行配置。** 表 `execution_accounts.backend_roles` 记录 `role`、`account_id`、`account_selection`、`model`、`reasoning_effort`、版本和修改审计。站点 `default` 保存模型偏好，账号默认采用 auto；account_id 在 auto 模式仅限定同 Provider、账号种类和计费范围，不赋予优先权；fixed 才固定使用该账号。用户需要为不同用途指定账号或模型时，再配置其他角色。
- **角色在创建 Resident 或初始化发行数据库时解析成具体设置。** 新 Resident 按角色取得 Provider、路线、模型和推理强度并保存为自己的执行配置（decision_8b2fa224616b）。新建 Resident 未指定 Provider/账号时以及新建 AMA、Project principal 取站点 default。用户可以在配置首个 AI 前取得 AMA 身份。没有路线且没有 Provider 会话绑定的 Resident 在首次执行前按本地角色映射持久保存配置；查询执行门禁使用同一映射。已有会话绑定不随默认角色改变；保存为自动模式的 Resident 首次执行前按可用账号确定实际路线。显式账号或手动切换是固定意图，除非用户另行选择自动。Kimi 的服务账号沿 Provider 自有路线配置，不把其合法空路线解释为未配置。
- **没有映射的角色回退到 `default`。** 回退发生时 Resident 的会话设置标出“角色 `<名>` 未映射，使用站点默认”。

### 管理页

首次登录的欢迎卡片保留使用指南与关闭操作，不放置依赖 AI Backend 的 AMA 对话入口。AI Backend 页顶部仅保留一行“添加 AI 后端”，提供“接入订阅帐号”和“新增 API Key”。新增账号仅等待 Provider 接入目录；ListProviders 的 include_runtime=false 返回名称、接入种类、能力、协议及登记信息，不探测运行版本或准备状态，refresh 在此模式下不生效。完整版本与账号、API、角色列表独立加载，不阻塞新增窗口，不禁用已就绪的接入按钮，也不覆盖正在填写的草稿；省略 include_runtime 时继续返回完整运行状态。新增订阅账号提供默认未勾选的“使用本机已有登录”。未勾选时，打开窗口即显示待创建的默认绝对路径；账号名称尚未输入时使用清晰的 `new-account` 预览名，填写名称后自动更新未手动编辑的默认路径。勾选时通过管理页 HTTP 接口按当前 Provider 查找目录，展示尚未由当前 Portal 登记的候选目录、发现依据及未验证登录状态；选择前不替用户认领候选。扫描失败或未发现时仍可手动输入或点击“浏览”，重新扫描与迟到结果不覆盖输入，不把目录存在当作已登录。新登录和已有登录的目录草稿按 Provider 分别保留，切换不丢失手动路径。保存复用目录时提交 `RegisterProviderAccount(reuse_existing=true)`，服务端要求显式存在的规范目录，拒绝以空值生成新目录或在错误路径创建目录；随后由既有设置流程确认真实登录状态。新增执行账号页说明用途和账号指代名称，只展示当前接入必需字段，初始并发上限为 1；备用账号、路线等高级字段在后续管理入口维护。接入方式只有一项时不展示无选择意义的下拉框。

新建 API 账号可在同一流程点击“新建模型 API 并接入”，直接填写接口地址、API Key 和服务商模型列表，并保留所选运行器。模型目录通过逐项表单维护标识、启用状态、默认模型及可选推理强度，不要求输入 JSON；模型标识不得为空或重复，默认模型必须已启用。既有模型的额外元数据与推理强度顺序保留，预设可取消勾选，自定义强度可明确移除。已有兼容接口可以直接复用；没有兼容接口时显示创建入口并禁用空提交。运行器与接口协议仍按显式声明匹配；协议不匹配时要求用户确认，不自动猜测协议或切换运行器。第三方 API 的模型名称沿用服务商实际配置。

接入窗口展示运行环境、登录授权、测试连接三个阶段，并在准备期间显示真实等待时长。服务端在执行依赖检查、版本获取、依赖安装、服务启用前持久化 phase、started_at 与 updated_at，页面轮询显示当前阶段；失败显示脱敏提示和诊断类别，恢复失败时说明需要处理。不伪造下载百分比，准备成功不视为登录或模型检查成功。

- **Portal 管理新增“AI Backend”分栏，分四个区域。** Provider 区列出声明的 Provider、运行时已安装与可用版本、待切换状态，提供“检查更新”（调用运行时更新检查）；执行账号区按显式的模型 API 关联归组：连接卡片显示默认模型、接口地址、连接名称及其他启用模型，折叠区保留各条执行账号的运行器、独立身份、状态与操作；订阅及未关联 API 的账号单列。连接可编辑，各路线可进入“设置与测试”并保存模型偏好，仍保留登录、目录编辑及受引用保护的删除入口；账号区只提供一个“默认帐号选择排序”，不再显示逐账号“设为默认 AI / 设为默认路线”；重新读取保留分组展开状态与定位焦点。分组只改变呈现，不合并账号身份、容量或权限，也不显示停用/启用和初始并发编辑；模型 API 区提供条目的新增、修改、删除和“测试连接”（发送一次最小的 Chat Completion）；Backend 角色区编辑角色映射。
- **Home 账号面板保持运行视图的职责。** 订阅账号展示额度；API 按实际 billing_account_id 合并余额、活动图标与可见 Resident 统计；原币金额旁按约 ¥10 一枚展示金币，外币依赖有效参考汇率。详情包含节点信息、批量接单，以及有效 billing_url 对应的充值或账单入口。Home 详情可按执行路线编辑最大并行数，其余生命周期和连接配置在管理页；Home 顶部保留管理入口，登录失效仍提供重新登录。见 decision_4dea0db1f45a。
- **修改需要“Portal 运维”节点的 admin，查看需要对“共享”的 viewer。** 密钥文件路径与主机目录只对 admin 显示。权限系统关闭时任何已登录 User 可用。
- **初次配置使用现有管理页面的接入流程。** 登记成功即保存连接，页面随后打开“接入并启用 AI”；已有订阅登记通过“登录”完成授权。页面自动检查并准备所选 Provider 的运行环境，持续观察准备结果，不要求用户点击准备或刷新；已有运行环境和进行中的准备任务直接复用，准备或读取失败时才提供重试，关闭页面不取消已启动的服务端准备任务。只在需要授权时显示登录入口；已经登录或无需登录的账号不重复授权。正常状态的收尾操作是“测试并完成”，模型填写可选，留空时由服务端启动并读取账号默认模型，用户自行触发，登记与环境准备不自动消耗模型调用额度。已有账号通过“设置与测试”进入接入检查；勾选“用作默认模型偏好”并测试通过才更新角色，启用自动选择。复测当前默认账号时保持该选项；测试账号与实际模型仍等于当前默认值时，沿用角色原推理强度，切换模型不继承不匹配的强度。测试成功后按勾选项通过正常角色版本检查设为默认，关闭接入窗口并显示完成结果；失败保留输入以便修正和重试。显示声明的工具能力，允许使用开发机或目标机已有程序和授权，不要求重新安装、重新登录或专用隔离目录。高级角色映射继续按需使用。 需要订阅授权时先显示登录指引，隐藏模型与默认选项；Codex 明确引导设备码登录，授权完成后再显示可选模型。准备状态后台轮询不得重置为加载页或重建未变化的模型选项；设备码与授权链接未变化时保留节点及链接，避免轮询闪动和打断复制。API 账号行及接入测试窗口直接提供“编辑连接”，复用既有模型 API 编辑器；保存后回到原账号测试，不重复建立接口或账号，留空密钥继续沿用原密钥。

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
| `DeleteProviderAccount` | 删除空闲账号的登记与服务实例，保留账号目录 |
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

模型 API 编辑器按 Provider 声明提供执行器选择。上游原生协议与本机转换后的 Harness 能力分开呈现，协议名称、转换边界和路线兼容性的唯一规范由 decision_6c2f91a7d84e 拥有：Claude Code 与 Codex 可经声明的本机转换使用三种文本协议，其他运行器保持自身原生要求；Jev 只提供直连。协议探测不把本机转换当成上游原生能力，不能要求用户为选择运行器虚报上游协议。HTTP 协议兼容不证明模型具备 Agent 工具语义；接入仍须用户主动发起真实测试，登记和展示不消耗模型调用。

Kimi API 账号的模型目录来自所绑定的 API，并拥有独立本地服务与会话目录；不修改既有 Kimi 服务账号。DSH 允许多个显式绑定的 Messages API 账号，并依据各自声明传递认证和请求头。账号注册、测试、默认角色、停用和删除复用公共登记及引用保护，不为新增执行器绕开繁忙、会话或角色引用检查。模型与执行器选择不依据模型名称猜测接口协议。

### 使用本机已有的登录

要使用人已经在终端里登录过的 Codex 或 Claude 账号，登记时把那个账号目录填为账号目录，Portal 直接使用它，不复制凭据文件（《Portal 实例协议》「执行账号目录」）。两边用同一个目录，共享同一份登录和会话；复制会让两处各持一份 refresh token，一方刷新后另一方就失效。登记只记录目录，不代替真实连接测试；删除登记不改动这个目录。


## 保存模型 API 后的兼容路线

CreateModelApi 与 UpdateModelApi 成功保存已启用接口后，Execution 按 decision_6c2f91a7d84e 的兼容性合同补齐直连及 Harness 登记并返回 accounts；已有身份与状态复用。前端选择的运行器只决定后续接入测试，不限制 Resident 可选的兼容路线。登记过程不安装运行器、不初始化模型回合。

已有同 API、同 Provider 的路线直接复用（含停用路线），不改变 ID、并发与停用设置。直连优先复用与 API 同 ID 的已有原生路线；新建直连以 API ID 为账号 ID，新 Harness 默认以 <api_id>-<provider> 命名，超长或冲突使用稳定摘要。API ID 必须满足执行账号 ID 格式，不能占用无关账号。新路线初始并发为 1。UpdateModelApi 的空 changes 可用于补齐既有 API；删除 API 或移除协议仍受执行账号引用保护。

路线登记批次由 Execution 的数据库事务与 API 级锁保护。接口保存成功而路线登记失败时，错误 reason=model_api.routes_incomplete 明示 api_saved 和 api_id；页面重试使用 UpdateModelApi，不重复创建接口或提交已清空的密钥。账号读目录不偷偷写登记，原有账号权限和调度入口继续有效。


## 统一账号选择

账号选择是 Execution 的独立策略，输入为后端、模型/推理强度、账号种类及计费范围和账号观察；不负责修改 Session。Backend 角色及 Resident 执行设置的 account_selection 只有 auto 和 fixed。auto 不代表某个优先账号，fixed 才是明确固定账号。旧角色迁移为 auto；已有明确路线且无角色/自动意图的 Resident 保持固定，已有 Session 不在升级时重建。

普通启动的账号候选、登录与额度观察、模型目录新鲜度及预防性迁移策略统一由执行账号协议 decision_d40f59f5508f 拥有，本协议不复制另一套启动准入规则。账号登记、角色和显式后端顺序提供可选账号与计费范围；监控样本或模型目录缺失、过期不额外阻止普通启动，已确认登出或 Scheduler 明确保护的账号仍不可自动选择。实际认证与模型错误由真实调用报告，不把未知观察标成成功。显式付费 credits 与会话替换的受理边界分别沿执行账号协议和 decision_c47a13e8b962。

现有会话有效时保持实际绑定，不能因其他账号余额更大而迁移。自动模式额度不足时沿现有 RestartSession 幂等、等待在途工作、事务回滚流程保留有效上下文；自动路径使用 preserve，不能静默 compact、fresh 或重新投递结果不确定的业务。仅原有失败输入恢复规则可恢复已确认失败的输入。没有完整保留能力时保持原会话并报告等待原因。

Backend 角色以 backend_priority 持久化有序账号种类 ID，和 account_selection、模型偏好共同进行版本比较与保存。auto 新会话按该顺序寻找首个额度和模型目录有效的种类，再按公共额度策略选择同类账号；未显式设置额度自动切换开关的现有会话，耗尽时先寻找同类兼容账号，再按角色顺序切换。跨种类采用目标账号目录的默认模型并清除来源推理强度，保留上下文。未列入的种类不参与跨种类选择；API/服务账号需用户明确加入，API 种类包含计费身份，不隐式动用订阅附加付费 credits。省略 backend_priority 更新时保留原值；空数组保持参考账号同类范围，兼容已有配置。普通自动 Resident 和带 backend_role 的自动 Resident 使用角色排序，Family 的旧显式迁移策略仅在未启用统一自动模式时适用。未显式设置额度自动切换开关时，fixed 忽略排序且不自动迁移。账号观察与模型目录由执行服务提供，UI 不猜测模型兼容性或挑选实际路线。

管理页说明为“不同种类 AI 后端默认选择/自动切换的排序可手动调整，同种 AI 后端的选择由当前额度自动选择”，旁置“默认帐号选择排序”按钮。卡片统一编辑自动/固定、种类上下排序、API/服务种类加入与移除及模型偏好；推荐顺序只包含订阅种类。已有角色严格显示实际保存的 backend_priority，空数组显示未配置跨后端自动切换顺序，不替换为推荐顺序。新角色初始化及“恢复推荐顺序”才填入推荐草稿；保存前只改本地草稿，取消不写入，保存按角色版本比较；固定模式禁用排序，窄屏说明及按钮自然换行。手动切换账号建立固定账号选择意图，但保留独立的 auto_switch_on_quota 开关；修改策略不改写已有 native session 的身份。

模型偏好与推理强度使用下拉选择，复用 Resident Settings 的执行账号运行时目录接口，按模型偏好参考账号读取 models、default_model 和 efforts_by_model。留空分别沿用账号默认模型和模型默认强度；推理强度只展示当前模型声明的值，不提供自由输入。更换参考账号清除原账号的模型和强度偏好，更换模型清除原模型的强度偏好；无强度选项时禁用强度控件。保存的旧值从目录移除后仍显示为不可用，须选择有效项或默认值才能保存。目录读取失败保留偏好并提供重试，加载和失败期间禁用保存；切换账号或关闭卡片后，先前目录请求的迟到结果不覆盖当前选择。

### Resident 的额度自动切换

Resident Settings 提供“在当前帐号额度用完时是否自动切换”开关，以独立的 execution_settings.auto_switch_on_quota 布尔值保存。缺省沿用旧策略；显式开启只按站点 default.backend_priority，显式关闭禁止额度自动迁移，手动固定账号不会覆盖此开关。额度与迁移的唯一决策及状态所有权见 decision_d40f59f5508f。

关闭时，发送被受理且确认额度不足后直接打开既有账号切换卡片；开启但默认目标耗尽时提示“当前没有默认可切换的ai后端, 请手动选择”并提供同一卡片入口。手选列出所有可切换后端，不以默认排序过滤。自动迁移完整保留上下文，不能静默压缩、丢弃上下文或重投业务；取消卡片保留原消息和会话。同一发送不重复弹卡，历史浏览不自动弹卡，未知额度和非额度错误不进入本流程。
