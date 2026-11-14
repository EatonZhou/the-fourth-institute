---
kb_id: decision_d40f59f5508f
type: decision
project: AI员工
created_at: '2026-09-30'
version:
  number: 36
  created_at: "2026-09-30T12:00:00Z"
  created_by:
    user: "weihao"
    agent: "resident_36c860ac51b6"
  updated_at: "2026-11-14T00:29:41Z"
  updated_by:
    user: ""
    agent: "resident_60ae6d0cdf74"
importance: 8
metaness: 2
ai_generated: false
links:
  source:
  - /opt/tfi/the-fourth-institute-dev/code/management_portal/execution/accounts.py
  - /opt/tfi/the-fourth-institute-dev/code/management_portal/execution/account_adapters.py
  - /opt/tfi/the-fourth-institute-dev/code/management_portal/execution/account_monitor.py
  - /opt/tfi/the-fourth-institute-dev/code/management_portal/execution/account_quota.py
  - /opt/tfi/the-fourth-institute-dev/code/management_portal/execution/codex_quota.py
  - /opt/tfi/the-fourth-institute-dev/code/management_portal/execution/account_usage_store.py
  - /opt/tfi/the-fourth-institute-dev/code/management_portal/execution/provider_balance_adapters.py
  - /opt/tfi/the-fourth-institute-dev/code/management_portal/execution/provider_catalog.py
  - /opt/tfi/the-fourth-institute-dev/code/management_portal/execution/scheduler.py
  - /opt/tfi/the-fourth-institute-dev/code/management_portal/execution/service.py
  - /opt/tfi/the-fourth-institute-dev/code/management_portal/execution/isa.py
  - /opt/tfi/the-fourth-institute-dev/code/tests/cases/test_account_close_threshold.py
  - /opt/tfi/the-fourth-institute-dev/code/tests/cases/test_codex_quota_reserve.py
  related:
  - decision_25eb93d803a4
  - decision_6ad0384e020d
  - decision_8ac0b5a6c0e5
  - decision_4dea0db1f45a
  - decision_3dd52c0692c1
  - decision_4527448ad07f
  - decision_f1331d57be89
  - decision_c47a13e8b962
  - decision_2a3ff353165c
  - decision_7956d7cb4bde
  supersedes: []
  derived_from:
  - decision_6ad0384e020d
  - decision_25eb93d803a4
---
# ExecutionService 执行账号：目录、额度保护、绑定放行与调度设置

Resident 调用模型时，最终要落到某个模型服务供应方（Provider）的某个账号上，例如 Codex 的 `codex_one` 或 Claude Code 的某个订阅。这样的账号称为**执行账号**。ExecutionService（Portal 统一执行模型请求的后台服务，见 decision_25eb93d803a4）负责维护全部执行账号：列出有哪些账号，读取每个账号的剩余额度或余额，按账号限制同时运行的任务数和发送节奏，并在额度快用完时暂停这个账号上的自动工作。

Home 页的账号面板、Resident 的会话设置、Scheduler（持久调度器，见 decision_8ac0b5a6c0e5）和 HATG ISA 的运行环境指令（decision_4527448ad07f）读取的都是这里的同一份账号数据。本文说明账号数据包含什么、额度保护怎样工作、调度设置怎样保存，以及对外接口。页面怎样展示这些数据见 decision_4dea0db1f45a，Codex 新会话怎样选择账号见 decision_3dd52c0692c1。

## 先认识几个概念

- **执行账号**：一条可以承接模型调用的账号，由全局唯一的 `account_id` 标识。每个执行账号属于一个 Provider，并记录它对应的原生路由、容量键、额度策略和计费账号。
- **路由（route）**：Provider 自己识别账号的方式，例如 Codex 账号对应的 App Server socket，或 Claude 的 profile ID。路由记录在会话和请求的 `backend_route_id` 中。已经建立的模型会话固定属于它的路由；切换账号需要迁移会话（decision_c47a13e8b962）。
- **容量键（capacity key）**：Scheduler 按它把会争用同一账号资源的执行归为一组，限制并发和发送间隔。容量键由账号适配器给出，例如 `codex:one`、`account:deepseek_primary`。公开的账号名改变时容量键保持不变，已保存的调度设置不会丢失。
- **账号适配器**：每个 Provider 一个的翻译层。它知道原生账号目录、别名、默认账号、模型目录和额度读取方式，把这些翻译成统一的执行账号。账号适配器之外的代码只使用 `account_id`，不根据 Provider 名称走不同的代码路径。
- **额度策略**：`reserve` 表示账号有订阅额度窗口（例如 5 小时和每周窗口），按剩余百分比保护；`none` 表示没有额度窗口。账号关联了计费账号时按余额保护。
- **计费账号（`billing_account_id`）**：账号适配器声明的“这个执行账号从哪个 API 账户扣费”。多条执行路线可以共用同一个计费账号和同一份余额读数。
- **额度样本**：一次读取到的剩余额度或余额，带采样时间和有效期。
- **Close 阈值**：账号级设置。剩余额度或余额小于等于这个值时，账号进入保护状态。
- **binding 例外**：某个 Resident 会话被人工放行后记录的例外。例外期间，这个会话的工作不受账号额度保护影响。

## 当前登记的执行账号

账号来自 ExecutionService 的账号登记：PostgreSQL schema `execution_accounts` 的表 `registrations`，每行一个执行账号，记录 Provider、取得方式（订阅登录 `subscription_login`、引用模型 API 的 `api_profile`、Provider 已有服务身份 `service`）、路线、初始并发（默认 8）和 Provider 专有配置；登记由 `RegisterProviderAccount` 等指令维护（decision_7956d7cb4bde）。模型 API 是在 Portal 登记的可直接调用的 HTTP 模型接口，`api_profile` 账号用 `config.api_id` 引用它。各 Provider 的账号适配器把登记行翻译成执行账号，ExecutionService 启动时合并成一份目录。账号 ID 必须全局唯一，重复时服务拒绝启动。

| Provider | 账号 | 容量键 | 保护方式 |
| --- | --- | --- | --- |
| Codex | Provider 为 `codex` 的登记，例如 `codex_one`；去掉 `codex_` 前缀后的名称（如 `one`）是只在适配器内使用的别名 | `codex:<名称>` | 订阅登录按订阅窗口剩余百分比；`api_profile` 按所引用模型 API 的余额 |
| Claude Code | Provider 为 `claude` 的登记：原生订阅账号，以及引用模型 API 的账号（例如 Claude-on-DeepSeek 的 `deepseek_primary`） | `account:<账号 ID>` | 原生订阅按剩余百分比；`api_profile` 按余额 |
| Kimi Code | Provider 为 `kimi` 的登记：原生订阅、既有 Kimi Code 服务（例如 `kimi_default`）和引用模型 API 的账号 | `account:<账号 ID>` | 原生订阅从账号原生 OAuth 服务读取订阅额度窗口，按剩余百分比保护，不关联 API 余额；既有服务以 `PORTAL_KIMI_CODE_PROVIDER_PROFILE` 指定的模型 API 为计费账号（默认 `kimi_primary`）；站点没有登记这个模型 API 时，`ListProviders` 不提供 Kimi 的 `service` 接入方式，已有 `service` 账号的“测试并完成”直接说明缺少共享服务并建议改用订阅登录或模型 API 账号，不提交注定失败的测试，`api_profile` 以所引用的模型 API 为计费账号 |
| DeepSeek Harness、OpenCode、Google Antigravity | 对应 Provider 的登记，每行一个账号，例如 `dsh_deepseek`、`opencode_zen_free` | `account:<账号 ID>` | 引用了模型 API 时按其余额；没有引用时不做额度保护 |
| 独立 API 账号 | Provider 为 `provider_api` 的登记，每个引用一个模型 API | `account:<账号 ID>` | 按余额 |

没有对应执行账号的请求（例如没有登记账号的 Chat Completion）使用共享容量键 `shared-ai`。会话释放请求使用 `session-control:<provider>`，不占模型执行容量。

## 设计点

### 账号身份与调度设置

- **并发按账号计算。** 同一个账号上的原生 Agent 调用和 API 调用共同占用该账号的并发上限，不按 API 或 Provider 另分一份容量。DeepSeek API 与 Claude-on-DeepSeek 都使用 `deepseek_primary`，共用 `account:deepseek_primary` 的并发和派发间隔。

- **每个执行账号有一组独立的调度设置：最大并发、两次派发之间的随机间隔范围、Close 阈值和允许使用 credits。** 设置按容量键保存在 Scheduler 的 PostgreSQL 中，保留操作者与更新时间，保存后按统一调度规则生效，其他账号不受影响。
- **最大并发是 1 到 256 的整数，间隔的最小值和最大值都在 0 到 3600 秒之间且最小值不大于最大值。** 布尔值、小数、非有限数和空的操作者都会被拒绝。账号没有保存过设置时，并发使用适配器声明的默认值，间隔使用服务默认的 2 到 6 秒。
- **Close 阈值的单位由账号决定。** 百分比账号的单位是 `%`，默认 2，最大 100；余额账号的单位是它实际读到的币种，默认 0。提交的单位与账号实际单位不一致，或账号还没有读到可用的币种时，阈值不能保存。
<!-- wall: {"key": "7e96ce327dba907d"} -->
- **Close 阈值为 0 表示不设阈值。** 余额账号在余额为零、过期或未知时不做阈值保护；百分比账号未知读数不触发阈值保护，已确认原生耗尽仍按耗尽保护及明确的 credits 策略处理。阈值 0 本身不构成付费授权，也不自动修改 Resident 的人工 Open/Closed。
- **切换账号只改变尚未绑定模型会话的排队执行。** 调用方把这样的执行改到另一个账号时，账号适配器同时改写路由和与路由相关的运行选项，例如 Claude 的 `provider_profile`，避免路由已经改变而配置仍指向旧账号。已经绑定会话的执行保持原账号。

### 只读目录批次

Home 会话列表、活跃 Resident 和 Resident 状态列表等同步只读入口，在一次列表读取中按需构建账号适配器目录，并复用该次构建结果。因权限过滤而继续读取多个候选页时，所有候选页仍属于同一次读取，不逐 Resident 或逐候选页重建目录。可见性、已有 Session 的实际绑定、显式路由与默认账号选择仍沿各自原有规则执行。

批次作用域内不修改账号配置；嵌套读取复用外层目录，空请求或无需解析账号的请求不构建目录。正常返回或异常退出时释放该作用域，独立线程和后续请求各自重新读取配置，不形成跨请求的陈旧配置缓存。这个作用域只复用账号目录构建，不扩展用户权限，不读取模型回复，也不替代额度观察、运行会话绑定或调度状态的权威来源。

### 额度保护

Resident 的接单开关只表达人工输入控制。额度保留、等待与恢复由 Execution Scheduler 负责；账号详情分别显示人工打开/关闭数量和 Scheduler 额度状态。

- **账号额度进入保护状态后，这个账号上的自动工作暂停。** 新鲜的剩余额度或余额小于等于正数阈值时，账号状态变为 `reserved`：它的排队执行不再被领取，快照中的排队原因显示 `quota_reserve`；它正在运行、且没有 binding 例外的执行被请求取消，取消原因记为 `quota_reserve`。
- **百分比账号原生额度耗尽默认暂停；只有满足明确付费策略时才继续。** 新鲜读数任一窗口剩余小于等于 0、`ordinaryUsageAllowed=false` 或 `rateLimitReachedType` 非空均保留原生耗尽事实，`quota_reserve.used_up` 不因付费放行变成假。默认 `allow_credits=false` 时，阈值为 0 也保持耗尽保护。Codex 订阅账号在阈值为 0、持久允许 credits 且认证与付费余额满足下述条件时，自动暂停可解除；阈值大于 0 时仍执行原阈值保护，不用账号级 credits 选项绕过。人工 Closed 的 Resident 仍不接纳新工作。
- **修改 Close 阈值时已经在运行的工作继续完成。** 新阈值从下一次派发开始生效：仅在阈值数值或单位实际改变时，把这个账号上已领取或正在运行的执行记入豁免名单，之后的额度采样也不会因为额度保护取消它们；修改之后才开始的执行仍按上一条规则受额度保护。额度用完时阈值调整豁免名单不再生效；只有满足账号级 credits 策略或现有 binding/Session 范围例外时才按各自合同继续。
- **Close 阈值为正数时，读数未知的百分比账号暂停，余额账号不因此暂停。** 百分比账号读数过期、读取失败或窗口不完整时，自动工作同样暂停；阈值为 0 时仍按前文的禁用阈值及额度用完规则判断。余额账号只有明确读到低于阈值的余额才会进入保护，余额未知不会被当成零。
- **保护阈值设置不变时，已经进入保护的账号只有取得新鲜的恢复读数才会解除。** 读取失败或仅仅过了额度重置时间，都保持 `reserved`。显式将阈值改为 0 可解除由阈值造成的保护；已确认额度用完的百分比账号仍按前文的耗尽与 credits 规则判断。解除后，原来排队的工作按原顺序继续领取；被取消的执行保持取消，不会自动重放。
- **Resident 被人工打开接单时，会为它当前的会话记录一条 binding 例外；再被人工关闭时，这条例外改为停用。** 例外只作用于这个 binding，不会传给其他 Resident 或 Fork 出的新会话。唯一的传递发生在 Family：门面的放行由 Family 监督同步为 Router、Steward 各自 binding 的例外，规则见 decision_a64b3ea2c3c5。修改阈值不会撤销已有例外。
- **普通创建与迁移直接提交持久执行，由 Scheduler 统一执行额度保护。** Society 不读取整份健康快照预检余额、不要求新鲜正余量，也不为普通准备会话自动授予额度豁免。仅人类明确确认本次目标 Session 使用 credits 时，读取并验证最新认证与 credits 样本，授予目标账号和唯一准备 Execution 的范围授权；同一次受理只验证一次。准备、取消及保留原会话的流程见 decision_c47a13e8b962。

### 账号级允许使用 credits

`UpdateProviderAccount` 接受可选布尔参数 `allow_credits`，默认未保存时为 false，省略时保留原值，显式 false 撤销账号级允许；非布尔值拒绝。仅 Provider 为 Codex 且 kind 为 `subscription_login` 的账号声明 `credits_supported=true`；其他账号不能开启。设置沿用原调度设置的权限、持久审计和容量键，不改变账号登记或权限方案。

`ListProviderAccounts` 的账号设置、`UpdateProviderAccount.settings` 与 Home 账号摘要均回传 `allow_credits` 和 `credits_supported`。这两个字段描述持久偏好与支持能力，不等于当前余额可用。只有阈值为 0、原生额度已耗尽、允许且支持 credits 时，Scheduler 才以已认证、非 stale、60 秒内采样、`has_credits=true` 且余额严格大于 0 或 unlimited 的观察继续自动工作。无法确认付费余额时恢复耗尽保护；原生剩余比例、status/used_up 与采样来源保持真实，不伪造原生 headroom。人工 Closed、账号停用、实际 Provider 限流及原有并发/派发间隔规则继续优先适用。

账号级策略不创建或扩大既有 Session scoped credits 授权；关闭账号级选项不删除旧授权记录，原 Session 继续按其范围、新鲜余额与生命周期合同判断。手动 Open/Close 入口保留，各自接单和放行语义沿用原合同。阈值大于 0 时账号级选项可被保存，但不能解除该阈值或原生耗尽保护。

### 人类确认本次目标 Session 使用 credits

会话设置中的切换账号和重新开始会话提供默认不勾选的付费确认。页面展示本次目标稳定账号，说明授权只适用于本次新建或迁移得到的目标会话；切换目标账号、改变操作或再次打开页面时必须重新选择。只有已认证的人类且具有该 Resident 管理权限才可确认，Resident 和 service actor 不能授予或重放该授权。

正式 `RestartSession` 指令接受 `credits_consent={account_id,confirmed:true}`，并要求本次幂等键与明确的目标账号。服务把确认账号与解析后的稳定账号ID精确比较；同一幂等键的目标、上下文策略和确认内容必须相同。授权随本次会话操作持久保存，包含操作ID、目标账号、人类确认主体及时间。等待当前轮结束或重启服务时沿同一操作恢复；不会因为恢复而生成另一份授权或自动重试历史失败输入。

Fork 初始化的付费确认由初始化自身持有。人类在正式 `RetryResidentInitialization` 中同时提交刚读取的 `expected_initialization_id`、`credits_consent={account_id,confirmed:true}` 和本次幂等键；账号必须与该初始化的稳定目标账号相同，主体、管理权限及新鲜 credits 读数沿用同一准入规则。已完成的初始化和已变化的尝试不能复用旧确认；没有确认时不增加额度例外。

确认恢复原创建工作及冻结历史，不调用普通会话迁移，不重发排队消息。创建和首轮前压缩分别只授权当前步骤的唯一 Execution；恢复候选的授权只属于该候选。初始化完成后，授权提升到实际目标 Provider Session 引用，原队列按既有接单状态继续。中断恢复复用同一授权及执行身份，已提交结果只重试授权同步；明确失败后换用新候选时须重新确认，后续 ClearContext、重新开始、换账号和新 Fork 不继承。

额度准入和运行保护由 Execution Scheduler 的同一账号契约解释。人类确认仅允许越过本次目标账号的订阅额度保护，不改变原始额度、Close阈值、站点默认策略或账号登记。准入要求目标账号启用，最新观察确认已认证，credits样本不超过60秒且读取未失败，has_credits为真，余额严格大于零或unlimited为真。额度读数不足仍如实保留。认证失效、停用、credits不足或不可确认、Provider实际限流及执行失败继续按其原有错误与恢复规则处理。

授权分两个不可互换的范围。准备阶段只授权本次候选binding、目标账号和唯一Execution；成功提升后，授权绑定到该Resident的binding、目标账号和真实Provider Session引用。Scheduler在读取、派发及运行中额度保护时都校验同一范围与credits观察。后续重新开始、ClearContext、换账号、迁移、Fork或其他Resident不会继承；同一个Resident的稳定binding ID也不能代替具体Session身份。取消、准备失败及回滚不提升目标授权，临时候选例外按生命周期收尾。成功后的控制同步可重试，不能因此重复创建Session。

为在授权时固定确切会话，有本次credits确认的空白新建通过候选创建与条件提升建立一个真实空Session，再绑定授权；创建操作不调用模型、不发送初始化消息。没有该确认时保持原有配置保存及首条输入懒建协议。已有会话的OpenIntake放行是独立操作，不把此次会话确认传播为Family或其他binding的授权。

### 额度与余额的读取

每个订阅额度窗口 `rate_limits[]` 可携带非空 `quota_group`，表示在同一账号内共同约束一个额度池的窗口。分组由 Provider 适配器拥有：Codex 的 primary/secondary 共用一组；Claude 的 five_hour/seven_day 共用一组，模型专属窗口保留独立组；Antigravity 使用原生 /usage 的分组标题，Gemini Models、Gemini 4 Argon 与 Claude/GPT 互相独立；Kimi Code 的 5 小时、每周及原生月度限制归属同一订阅组。观察失败保留旧样本时也保留分组。未提供分组表示依赖未知，消费者不能仅凭名称、时长或同一账号猜测关联。

这个字段仅供显示同组窗口的耗尽约束，原始 `remaining_percent`、采样时间、过期标记和重置时间照常保留；不改变 Scheduler 额度保护或自动恢复规则。Home 的“受限”“待确认”与刷新行为见 decision_4dea0db1f45a。

- **账号观察由 ExecutionService 内的 AccountMonitor 后台线程完成，不依赖任何页面请求。** 它每 15 秒重新读取账号目录并调度各账号观察；调用方请求强制刷新时唤醒调度，但两次强制刷新至少间隔 60 秒。同一账号至多有一个未完成观察，慢账号不阻止其他账号在下一轮刷新；并行池有上限（当前为 32），新增账号随下一次目录读取进入观察。账号目录或数据库暂时不可用时保留已有样本并按轮询间隔重试；单个观察及其错误处理再次失败时只影响该账号，不退出监控线程，也不把失败视作空目录或成功样本。日志只记录账号标识与异常类型，不输出连接串或凭据。

账号观察有独立关闭作用域。停止 AccountMonitor 时先拒绝新观察及后续 usage/认证探测，再取消当前观察持有的 IO，等待监测线程与并行池实际退出；不能仅缩短 join 后遗留线程或子进程。Codex 只取消当前只读 RPC 等待，原生共享服务不受影响；Claude 关闭本次观察连接，账号认证状态 CLI 和 Antigravity 独立 ACP 观察进程由本次作用域终止进程树并回收。独立 daemon 自己拥有的后台观察继续由该 daemon 管理，不取消模型执行、会话、共享服务或登录流程。

HTTP 账号观察（Kimi 原生服务与计费余额）使用可回收的独立短进程，标准 urllib 保持既有代理与 TLS 校验。URL、认证头、正文与结果经私有管道传递，不写 argv 或文件；连接前、DNS/TCP/TLS、响应头及正文读取都在同一关闭范围内。原有请求 timeout 也是短进程结果读取的总上限，超时或取消均回收进程；HTTP 状态、响应正文及超时分类保留，未知不转换成可用。普通业务 HTTP 与真实模型执行不进入该作用域。

取消观察不生成登录、额度或 usage 成功/失败结论，不覆盖已持久保存的成功事实，也不从取消推断登录失效。已进入提交的数据库事务与原子文件写入依既有 owner 完成；关闭不绕过事务、不清空账本或缓存。线程 join 以资源回收完成为准，而非超时后宣称关闭；数据库提交、操作系统进程创建/回收本身发生故障时仍按原错误与恢复语义处理，不伪造关闭成功。

- **额度窗口来自 Provider 自己的接口。** Codex 从账号对应的 App Server 读取 `account/rateLimits/read`，得到 Codex 当前报告的额度窗口、套餐额度是否已用完，以及账号的 credits 余额；Claude 原生订阅从 Claude Code daemon 读取，daemon 每 3 分钟查询一次 `/usage`。每个窗口带有效期 `fresh_until`：Codex 窗口为采样后 60 秒，Claude 窗口为采样后 300 秒（不晚于重置时间）。`fresh_until` 用于额度保护的新鲜度判断；页面展示的刷新与过期规则统一见 decision_4dea0db1f45a，不因超过该时间自行变灰。
- **余额来自计费账号的接口。** 每个 ExecutionService 实例内，同一个计费账号每 60 秒最多读取一次，多条执行路线共用这次结果；展示读数的过期状态遵循 decision_4dea0db1f45a：读取失败才标为过期，不按读数年龄自行过期。余额采集同时更新展示用汇率快照；Web 只读取账号快照。原生余额接口、响应解析和币种规则集中在 `provider_balance_adapters.py`。
- **某个账号读取失败只影响它自己。** 失败时保留上一次读数并标为过期，状态为 `unavailable`，错误说明写入 `error`；其他账号继续正常更新。
- **Kimi 订阅额度由原生 OAuth 服务提供。** ExecutionService 从账号已运行的服务读取 `auth` 和 `oauth/usage`，沿用原生账号的中国区或国际区配置，不启动模型回合、不访问其他账号或模型 API 余额。原生 `usedRatio` 转换为剩余百分比；5 小时、7 天及当前套餐提供的月度总额度、月度编程额度统一进入 `rate_limits`。缺少的可选窗口不伪造为 0 或 100%。额度保护使用已返回窗口的最小剩余比例，样本有效期为 60 秒且不跨越将来的重置时刻，沿用统一 Close 规则。HTTP 成功但原生结果为 `kind=error`、缺少可用窗口或比例格式错误均算读取失败；保留上次读数并标记过期，不能据此改判登录失效。账号登录状态仍只由原生 `auth` 观测决定。
- **声明了原生历史用量能力的账号，另外读取每日 token 用量。** 目前 Codex 从 App Server 读取 `account/usage/read`。成功结果缓存 1800 秒，失败后至少 60 秒再试，结果保存在 PostgreSQL 的 `execution_account_usage` schema 中，服务重启后仍可读取。读取失败时保留上一次的每日用量并标为 `stale`（从未成功读到时为 `unavailable`），同时在 `error` 中记下失败类型和时间（`{code, at}`，`code` 是异常类名）；失败类型变化时服务日志记录一条去掉敏感内容的原因。Home 的用量统计据此点名用量没有更新的账号（decision_fc26f883773d）。没有这项能力的账号不参与，也不会被填成零用量。历史用量读取失败不影响额度保护。

### 用量的账号身份

- **用量记账使用执行实际走的路由确定账号。** 一次执行的账号身份来自它请求中的 Provider 和路由，不使用 Resident 当前绑定的账号。
- **用量中的 `provider` 表示执行来源。** 原生 Provider 使用 `codex`、`claude`、`kimi`、`dsh` 等名称；API 调用使用登记的厂商名，如 `deepseek`、`moonshot`、`openrouter`、`google`、`typesafe`。以厂商名 `anthropic` 记录的 Claude 原生订阅用量归入同 ID 的 Claude 订阅账号，不另外形成一个 Backend；独立 API 账号保持独立身份，不按厂商名或共享账号名合并。

API 账号在账本中同时保留厂商 `provider` 与登记账号 `account_id`。

小时账本、Home 用量图表和按账号筛选的口径见 decision_f1331d57be89。

## 组件怎样协作

```text
Provider 原生接口 ──读取──> 账号适配器（每个 Provider 一个）
                                  │ 标准执行账号 / 额度样本
                                  v
                          AccountMonitor（ExecutionService 内，每 15 秒）
                            │                        │
                            │ update_account_quota   │ 最新样本
                            v                        v
            Scheduler：保存额度状态、执行保护     accounts.list / accounts.usage
                            │
                            v
              领取时跳过 reserved 账号；取消其运行中的工作
```

- **账号适配器**负责原生账号目录、别名、默认账号、模型目录、额度和用量读取。新增的 Backend 如果提供声明式路由目录，可以直接使用默认适配器 `ConfiguredAccounts`，不需要新建持久化表。
- **AccountMonitor** 定时读取样本，把原始额度样本交给 Scheduler。公开账号状态只使用 Scheduler 按当前阈值计算的 `quota_reserve`，不混入以默认阈值解释的原始 `quota`。声明式路由目录在 ExecutionService 中读取失败会使启动失败，以便重试；Web 的只读目录入口可暂时略过不可用 Provider。
- **Scheduler** 按容量键保存调度设置、Close 阈值、最近一次额度状态和 binding 例外，在同一个数据库事务中判断和执行额度保护。额度状态保存在 PostgreSQL 中，Dev 与 Release 两个 ExecutionService 看到的是同一份。

## 接口

`ListProviderAccounts` 另返回 `default_routes`，按 Provider 列出默认路由。

账号接口运行在 ExecutionService 的 Unix socket RPC 上（传输规则见 decision_6ad0384e020d）。人和 Resident 通过 HATG ISA 的 `ListProviderAccounts`、`UpdateProviderAccount` 和 `GetUsage` 使用这些能力（decision_4527448ad07f）；这三条指令由 ExecutionService 执行。

| RPC | 作用 |
| --- | --- |
| `accounts.list` | 返回 `tfi.execution-accounts.v1`，包含 `accounts`、余额展示所需的 `exchange_rates`、持续到读取结束的 `refreshing`、下一次强制读取前的 `refresh_retry_after_seconds`。可选 `force_refresh` 请求立即重新读取。 |
| `accounts.resolve` | 按 `provider`、`backend_route_id` 和运行选项解析出标准账号，返回 `account_id`、`provider`、`backend_route_id`、`capacity_key` 与布尔值 `quota_protection_enabled`（是否支持额度或余额保护）；别名在这里转换。 |
| `accounts.usage` | 返回 `tfi.execution-account-usage.v1`：`accounts` 中每项包含 `account_id`、标准 `provider` 和 `usage`（`status`、`checked_at`，可用时还有 `daily_buckets[{date, tokens}]`，最近一次读取失败时还有 `error`）；`catalog` 列出全部已配置的执行账号和 API 账号，包括用量为零的账号。 |
| `runtime.catalog` | 读取某个账号可用的模型、推理强度和服务等级，见 decision_425e428410e0。 |
| `scheduler.capacity.configure` | 保存一个账号的 `max_concurrency`、`dispatch_interval_min_seconds`、`dispatch_interval_max_seconds`，以及可选的 `close_threshold: {value, unit}`；`actor` 必填。省略 `close_threshold` 时保留原阈值。 |
| `scheduler.quota_overrides.configure` | 按 binding 同步 binding 例外，规则见下文。 |

`accounts.list` 中每个账号包含：

| 字段 | 含义 |
| --- | --- |
| `account_id`、`provider`、`backend_route_id`、`billing_account_id` | 账号身份。`provider` 与 `backend_route_id` 对调用方是不透明的值。 |
| `max_concurrency`、`dispatch_interval_min_seconds`、`dispatch_interval_max_seconds` | 当前生效的调度设置。 |
| `close_threshold` | `{metric, unit, value, supported}`：`metric` 为 `percent` 或 `balance`；`supported` 为假时这个账号暂时不能设置阈值。 |
| `quota_reserve` | 当前额度保护状态，包括 `status`（`available`、`reserved` 或 `unknown`）、`remaining`、`fresh`、`close_threshold`、`threshold_enabled`、`used_up`（百分比账号额度已用完而暂停）、`automatic_paused`、原始的 `remaining_percent` 或 `remaining_balance`，以及采样时间。 |
| `rate_limits` | 额度窗口数组，每项包含 `id`、`label`、`remaining_percent`（未知为 null）、`resets_at`、`observed_at` 和 `stale`。 |
| `credits` | Codex 账号的 credits：`balance`（数值，未知为 null）、`has_credits`、`unlimited`。展示原生付费余额；默认自动工作在订阅额度用完时暂停，账号级允许策略见上文。人类按binding放行或明确确认本次目标Session后可使用credits。展示可保留失败前读数，但该旧读数不能通过新会话授权的准入与运行检查。 |
| `balance` | 关联了计费账号时的余额读数：`metric_kind`（`balance` 或 `availability`）、`status`、`display`、`amount`、`currency`、`updated_at`、`stale`、`source_account_id`、`billing_url`。零余额保留为零，未知不填零。 |
| `status`、`error`、`refreshing` | 最近一次读取的结果。 |
| `capabilities` | 这个账号是否可以修改调度设置、是否可以查看 Resident 明细。 |

`scheduler.quota_overrides.configure` 接收 `policies` 数组。每项包含唯一且合法的 `binding_id`、大于零的整数 `revision` 和布尔值 `enabled`。本次Session付费授权另外携带 `credits_scope`：必须包含 `account_id`、`operation_id`，并且在候选阶段只含 `execution_id`、在提升后只含 `provider_session_ref`，二者不能同时出现。缺少字段、类型错误或额外字段均被拒绝。同一 binding 的策略按 `revision` 决定新旧：更低的版本被忽略；相同版本且内容相同是重复提交；相同版本内容不同时整批回滚并报错；更高的版本覆盖旧策略。撤销例外要提交 `enabled=false` 的更高版本，提交空数组不会删除已有策略。返回 `{"updated": N}`，有更新时 Scheduler 立即重新判断额度保护并唤醒等待中的领取和控制。这个 RPC 由可信的同机服务调用；用户是否有权放行由调用它的 SocietyService 先检查。Resident 的 `quota_reserve_override` 配置和 Scheduler 的控制状态使用同一份基础字段及可选credits范围契约：`binding_id` 限定放行对象并防止跨 Resident 继承，`revision` 防止乱序同步和旧记录覆盖，`enabled` 表达当前放行状态。配置不重复保存操作者，人类审计归本次会话操作所有；会话重启临时 binding 的放行也遵守同一契约。

## 相邻 Design

- ExecutionService 的组件、进程和传输：decision_25eb93d803a4。
- Scheduler 的容量分区、领取顺序和租约：decision_8ac0b5a6c0e5。
- Home 页账号表、余额金币示意、跨币种比较和调度设置表单：decision_4dea0db1f45a。
- Codex 新会话的默认账号、按项目的默认账号与故障切换：decision_3dd52c0692c1。
- Resident 按账号批量打开或关闭接单：decision_2a3ff353165c。
- 执行账号的数据库登记、页面内登录、Backend 角色与 Portal 管理的“AI Backend”分栏：decision_7956d7cb4bde。

格式名与版本须精确匹配当前规范值，校验规则见 decision_86aed2f0e8c7；本篇同时校验各字段和结构版本，不接受历史前缀或其他版本。


## 同一 API 的多 Harness 展示

保存模型 API 时，Execution 按声明协议自动补齐兼容路线（decision_7956d7cb4bde），已有路线优先复用。Home 的展示 owner 按 billing_account_id 把多条路线合为一个 API 行，使用一份余额；Society 的 API 节点详情跨这些路线汇总可见 Resident。执行账号目录、容量键、历史账本与逐路线调度设置不因此合并；订阅账号继续独立显示。Home 的 API 投影身份不可用作执行账号调度写入的目标。

### 接入测试等待原因

排队 Execution 的 Scheduler 快照携带该执行容量键对应的 quota_reserve，与同一次准入判断共用状态，不借用默认账号或其他账号的样本。账号接入测试将缺少新鲜完整样本（包括保留中的过期样本）、已确认订阅额度耗尽和触及保护阈值分别提示；排队期间展示监控刷新建议。提示不改变额度准入，不将未知视为不足或可用，不重提 Execution，也不自动发送模型测试。


## 统一账号选择

账号选择是 Execution 的独立策略，输入为后端、模型/推理强度、账号种类及计费范围和账号观察；不负责修改 Session。Backend 角色及 Resident 执行设置的 account_selection 只有 auto 和 fixed。auto 不代表某个优先账号，fixed 才是明确固定账号。旧角色迁移为 auto；已有明确路线且无角色/自动意图的 Resident 保持固定，已有 Session 不在升级时重建。

新会话从已启用且计费范围符合用户意图的账号中选择。已确认登出、登录过期，以及 Scheduler 已报告 reserved、used_up 或 automatic_paused 的账号不参与自动选择。登录观察、额度样本或模型目录缺失和过期不额外阻止普通启动；目录新鲜时仍用于排除已知不兼容的模型和推理强度。没有指定模型且无可用目录时交由 Provider 使用默认模型，实际认证、模型不可用、余额不足和限流错误沿真实调用的失败路径返回。固定账号与已有会话维持原绑定。

Scheduler 保留显式 Close 阈值、订阅额度耗尽和并发保护；候选选择不复制数值准入。预防性迁移要求可验证的新鲜正余量和新鲜模型兼容性，不能因普通启动允许未知而放宽。候选保持当前绑定优先，再按空余并发、可测余量和负载排序，稳定账号 ID 最终排序。选择默认限于相同运行器、账号种类和 API 计费范围；跨范围必须由后端顺序明确允许。

余额查询是 API 的可选观察能力，不是已配置连接的必需能力；将其缺失等同于不可用会使已明确选择且不要求余额保护的 API 无法开始工作。这里区分“按当前策略可使用”和“有可测的正余量”，同时保留订阅额度保护、显式计费边界和预防性迁移的验证要求。

现有会话有效时保持实际绑定，不能因其他账号余额更大而迁移。自动模式额度不足时沿现有 RestartSession 幂等、等待在途工作、事务回滚流程保留有效上下文；自动路径使用 preserve，不能静默 compact、fresh 或重新投递结果不确定的业务。仅原有失败输入恢复规则可恢复已确认失败的输入。没有完整保留能力时保持原会话并报告等待原因。

Backend 角色以 backend_priority 持久化有序账号种类 ID，和 account_selection、模型偏好共同进行版本比较与保存。auto 新会话按该顺序寻找首个符合启动策略的种类，再按公共额度策略选择同类账号；未显式设置额度自动切换开关的现有会话，耗尽时先寻找同类兼容账号，再按角色顺序切换。跨种类采用目标账号目录的默认模型并清除来源推理强度，保留上下文。未列入的种类不参与跨种类选择；API/服务账号需用户明确加入，API 种类包含计费身份，不隐式动用订阅附加付费 credits。省略 backend_priority 更新时保留原值；空数组保持参考账号同类范围，兼容已有配置。普通自动 Resident 和带 backend_role 的自动 Resident 使用角色排序，Family 的旧显式迁移策略仅在未启用统一自动模式时适用。未显式设置额度自动切换开关时，fixed 忽略排序且不自动迁移。账号观察与模型目录由执行服务提供，UI 不猜测模型兼容性或挑选实际路线。

管理页说明为“不同种类 AI 后端默认选择/自动切换的排序可手动调整，同种 AI 后端的选择由当前额度自动选择”，旁置“默认帐号选择排序”按钮。卡片统一编辑自动/固定、种类上下排序、API/服务种类加入与移除及模型偏好；推荐顺序只包含订阅种类。保存前只改本地草稿，取消不写入，保存按角色版本比较；固定模式禁用排序，窄屏说明及按钮自然换行。手动切换账号建立固定账号选择意图，但保留独立的 auto_switch_on_quota 开关；修改策略不改写已有 native session 的身份。

### Resident 的额度自动切换开关

Resident 的 `execution_settings.auto_switch_on_quota` 是独立的布尔策略。显式 `false` 禁止额度自动迁移；显式 `true` 仅使用站点 `default.backend_priority` 中列出的账号种类，并严格按该顺序寻找可用目标，不能先尝试排序外的同类账号。空排序即没有默认可切换目标。字段缺省保留既有角色、自动账号选择及 Family 策略，不在升级时改写旧配置。该策略优先于 `account_selection=fixed`，手动选择账号、跨 Provider 或 API 会话切换都保留它。保存只修改策略，不立即切换会话；后续额度检查按新策略执行。

只有执行服务已确认 `quota_used_up` 或 `quota_reserved` 才进入此流程；未知额度、人工关闭接单、认证与权限错误、服务不可达不冒充额度耗尽。Society 统一决定有效开关、候选和迁移状态；浏览器只消费 `controls.quota_switch` 的 `state/reason/message/auto_switch_on_quota`。读取该投影不探测 Provider、不发起迁移。迁移账本变化发布对话状态通知，重连快照也返回当前状态。

开启时按默认排序逐个尝试候选，沿现有 RestartSession 的幂等、preserve、在途等待与失败回滚。没有默认候选或候选已尝尽时显示“当前没有默认可切换的ai后端, 请手动选择”，并提供手动选择入口；无法完整保留上下文或迁移失败时保留原会话和输入，说明原因并提供手选。对额度阻塞但尚未开跑的 Execution，沿已有 handoff/withdraw_unstarted 流程交接同一条消息，不重新投递业务。已提交、已运行或结果不明的历史输入不因切换重发。

menu → Settings 提供“在当前帐号额度用完时是否自动切换”滑动开关，显示后台的有效状态，保存使用原设置版本检查。开关关闭时，本页面发送消息被受理且确认额度不足后直接打开既有账号切换卡片。开启且默认候选耗尽时显示指定提示及入口。手选复用 Runtime Settings / Session 的原卡片，列出全部有权限切换的后端与账号，不受默认排序过滤；仍按原卡片解释上下文能力并由用户提交。单次发送只提示一次，取消后刷新不反复弹卡；浏览历史只显示状态和入口。取消手选不切换、不取消原消息、不重投；手动切换不隐式启用失败输入续接。
