---
kb_id: decision_d40f59f5508f
type: decision
project: AI员工
created_at: '2026-09-30'
version:
  number: 18
  created_at: '2026-09-30T12:00:00Z'
  created_by:
    user: tfi-exported-user
    agent: resident_36c860ac51b6
  updated_at: '2026-10-19T19:59:40Z'
  updated_by:
    user: tfi-exported-user
    agent: resident_865c3c24f251
importance: 8
metaness: 2
ai_generated: false
links:
  source:
  - ${code_root}/management_portal/execution/accounts.py
  - ${code_root}/management_portal/execution/account_adapters.py
  - ${code_root}/management_portal/execution/account_monitor.py
  - ${code_root}/management_portal/execution/account_quota.py
  - ${code_root}/management_portal/execution/codex_quota.py
  - ${code_root}/management_portal/execution/account_usage_store.py
  - ${code_root}/management_portal/execution/provider_balance_adapters.py
  - ${code_root}/management_portal/execution/provider_catalog.py
  - ${code_root}/management_portal/execution/scheduler.py
  - ${code_root}/management_portal/execution/service.py
  - ${code_root}/management_portal/execution/isa.py
  - ${code_root}/tests/cases/test_account_close_threshold.py
  - ${code_root}/tests/cases/test_codex_quota_reserve.py
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

账号由各 Provider 的账号适配器生成，ExecutionService 启动时合并成一份目录。账号 ID 必须全局唯一，重复时服务拒绝启动。

| Provider | 账号 | 容量键 | 保护方式 |
| --- | --- | --- | --- |
| Codex | `codex_zero` 至 `codex_five`，来自 `codex_accounts.yaml`；`zero` 至 `five` 是只在适配器内使用的别名 | `codex:<名称>` | 按订阅窗口剩余百分比 |
| Claude Code | LLM API Registry 中已启用的 Claude 原生订阅 profile，以及 DeepSeek 兼容 profile，账号 ID 等于 profile ID | `account:<profile ID>` | 原生订阅按剩余百分比；DeepSeek profile 按余额 |
| Kimi Code | `kimi_default` | `account:kimi_default` | 按其 provider profile 的余额 |
| DeepSeek Harness | 路由目录中的每条路由，账号 ID 为 `dsh_<路由>` | `account:dsh_<路由>` | 按路由关联 profile 的余额；默认并发 8 |
| 独立 API 账号 | LLM API Registry 中其余已启用的条目，每条一个账号 | `account:<条目 ID>` | 按余额 |

没有对应执行账号的请求（例如没有登记账号的 Chat Completion）使用共享容量键 `shared-ai`。会话释放请求使用 `session-control:<provider>`，不占模型执行容量。

## 设计点

### 账号身份与调度设置

- **并发按账号计算。** 同一个账号上的原生 Agent 调用和 API 调用共同占用该账号的并发上限，不按 API 或 Provider 另分一份容量。DeepSeek API 与 Claude-on-DeepSeek 都使用 `deepseek_primary`，共用 `account:deepseek_primary` 的并发和派发间隔。

- **每个执行账号有一组独立的调度设置：最大并发、两次派发之间的随机间隔范围和 Close 阈值。** 设置按容量键保存在 Scheduler 的 PostgreSQL 中，保存后立即影响这个账号的领取和发送节奏，其他账号不受影响。
- **最大并发是 1 到 256 的整数，间隔的最小值和最大值都在 0 到 3600 秒之间且最小值不大于最大值。** 布尔值、小数、非有限数和空的操作者都会被拒绝。账号没有保存过设置时，并发使用适配器声明的默认值，间隔使用服务默认的 2 到 6 秒。
- **Close 阈值的单位由账号决定。** 百分比账号的单位是 `%`，默认 2，最大 100；余额账号的单位是它实际读到的币种，默认 0。提交的单位与账号实际单位不一致，或账号还没有读到可用的币种时，阈值不能保存。
<!-- wall: {"key": "7e96ce327dba907d"} -->
- **Close 阈值为 0 表示不设阈值。** 阈值为 0 时，余额账号始终可以接收新工作，即使余额为零、读数过期或未知，账号自然用光为止；百分比账号在读数过期或未知时也继续接收工作，只在额度用完时暂停（见“额度用完的百分比账号总是暂停”）。余额账号的默认阈值是 0，所以默认不做余额保护；需要保护时由管理员设置一个正数。
- **切换账号只改变尚未绑定模型会话的排队执行。** 调用方把这样的执行改到另一个账号时，账号适配器同时改写路由和与路由相关的运行选项，例如 Claude 的 `provider_profile`，避免路由已经改变而配置仍指向旧账号。已经绑定会话的执行保持原账号。

### 只读目录批次

Home 会话列表、活跃 Resident 和 Resident 状态列表等同步只读入口，在一次列表读取中按需构建账号适配器目录，并复用该次构建结果。因权限过滤而继续读取多个候选页时，所有候选页仍属于同一次读取，不逐 Resident 或逐候选页重建目录。可见性、已有 Session 的实际绑定、显式路由与默认账号选择仍沿各自原有规则执行。

批次作用域内不修改账号配置；嵌套读取复用外层目录，空请求或无需解析账号的请求不构建目录。正常返回或异常退出时释放该作用域，独立线程和后续请求各自重新读取配置，不形成跨请求的陈旧配置缓存。这个作用域只复用账号目录构建，不扩展用户权限，不读取模型回复，也不替代额度观察、运行会话绑定或调度状态的权威来源。

### 额度保护

Resident 的接单开关只表达人工输入控制。额度保留、等待与恢复由 Execution Scheduler 负责；账号详情分别显示人工打开/关闭数量和 Scheduler 额度状态。

- **账号额度进入保护状态后，这个账号上的自动工作暂停。** 新鲜的剩余额度或余额小于等于正数阈值时，账号状态变为 `reserved`：它的排队执行不再被领取，快照中的排队原因显示 `quota_reserve`；它正在运行、且没有 binding 例外的执行被请求取消，取消原因记为 `quota_reserve`。
- **额度用完的百分比账号总是暂停，不论 Close 阈值。** Codex 套餐额度用完后仍会接受请求，改用账号的付费 credits 计费；Portal 不使用 credits。新鲜读数中任一窗口剩余小于等于 0，或 Codex 报告 `ordinaryUsageAllowed=false` 或 `rateLimitReachedType` 非空时，账号进入 `reserved`，`quota_reserve.used_up` 为真，排队执行停止领取，运行中的执行按上文取消。阈值为 0 时同样如此。这个暂停沿用“只有新鲜的恢复读数才会解除”的规则，读不到额度的样本不会解除它。
- **修改 Close 阈值时已经在运行的工作继续完成。** 新阈值从下一次派发开始生效：仅在阈值数值或单位实际改变时，把这个账号上已领取或正在运行的执行记入豁免名单，之后的额度采样也不会因为额度保护取消它们；修改之后才开始的执行仍按上一条规则受额度保护。额度用完时豁免名单不再生效，这些执行同样被取消；只有 binding 例外继续运行。
- **Close 阈值为正数时，读数未知的百分比账号暂停，余额账号不因此暂停。** 百分比账号读数过期、读取失败或窗口不完整时，自动工作同样暂停；阈值为 0 时仍按前文的禁用阈值及额度用完规则判断。余额账号只有明确读到低于阈值的余额才会进入保护，余额未知不会被当成零。
- **保护阈值设置不变时，已经进入保护的账号只有取得新鲜的恢复读数才会解除。** 读取失败或仅仅过了额度重置时间，都保持 `reserved`。显式将阈值改为 0 可解除由阈值造成的保护；已确认额度用完的百分比账号仍按前文保持暂停。解除后，原来排队的工作按原顺序继续领取；被取消的执行保持取消，不会自动重放。
- **Resident 被人工打开接单时，会为它当前的会话记录一条 binding 例外；再被人工关闭时，这条例外改为停用。** 例外只作用于这个 binding，不会传给其他 Resident 或 Fork 出的新会话。修改阈值不会撤销已有例外。
- **创建或迁移模型会话前，调用方检查目标账号还有剩余额度。** 目标账号的 Close 阈值为正数时，`quota_reserve.remaining`（原币金额或百分比）必须新鲜且严格大于零，否则不能创建新会话；阈值为 0 时只拒绝 `quota_reserve.used_up` 为真的账号，不要求新鲜的正余量。准备中的临时会话、取消迁移和保留原会话的流程见 decision_c47a13e8b962。

### 额度与余额的读取

- **账号观察由 ExecutionService 内的 AccountMonitor 后台线程完成，不依赖任何页面请求。** 它每 15 秒对全部账号并行读取一次；调用方请求强制刷新时立即读取，但两次强制刷新至少间隔 60 秒。
- **额度窗口来自 Provider 自己的接口。** Codex 从账号对应的 App Server 读取 `account/rateLimits/read`，得到 Codex 当前报告的额度窗口、套餐额度是否已用完，以及账号的 credits 余额；Claude 原生订阅从 Claude Code daemon 读取，daemon 每 3 分钟查询一次 `/usage`。每个窗口带有效期 `fresh_until`：Codex 窗口为采样后 60 秒，Claude 窗口为采样后 300 秒（不晚于重置时间）。`fresh_until` 用于额度保护的新鲜度判断；页面展示的刷新与过期规则统一见 decision_4dea0db1f45a，不因超过该时间自行变灰。
- **余额来自计费账号的接口。** 每个 ExecutionService 实例内，同一个计费账号每 60 秒最多读取一次，多条执行路线共用这次结果；展示读数的过期状态遵循 decision_4dea0db1f45a：读取失败才标为过期，不按读数年龄自行过期。余额采集同时更新展示用汇率快照；Web 只读取账号快照。原生余额接口、响应解析和币种规则集中在 `provider_balance_adapters.py`。
- **某个账号读取失败只影响它自己。** 失败时保留上一次读数并标为过期，状态为 `unavailable`，错误说明写入 `error`；其他账号继续正常更新。
- **声明了原生历史用量能力的账号，另外读取每日 token 用量。** 目前 Codex 从 App Server 读取 `account/usage/read`。成功结果缓存 1800 秒，失败后至少 60 秒再试，结果保存在 PostgreSQL 的 `execution_account_usage` schema 中，服务重启后仍可读取。读取失败时保留上一次的每日用量并标为 `stale`（从未成功读到时为 `unavailable`），同时在 `error` 中记下失败类型和时间（`{code, at}`，`code` 是异常类名）；失败类型变化时服务日志记录一条去掉敏感内容的原因。Home 的用量统计据此点名用量没有更新的账号（decision_fc26f883773d）。没有这项能力的账号不参与，也不会被填成零用量。历史用量读取失败不影响额度保护。

### 用量的账号身份

- **用量记账使用执行实际走的路由确定账号。** 一次执行的账号身份来自它请求中的 Provider 和路由，不使用 Resident 当前绑定的账号。
- **用量中的 `provider` 表示执行来源。** 原生 Provider 使用 `codex`、`claude`、`kimi`、`dsh` 等名称；API 调用使用登记的厂商名，如 `deepseek`、`moonshot`、`openrouter`、`google`、`typesafe`。原生订阅在 LLM API Registry 中的厂商字段不另外形成一个 Backend；独立 API 账号保持独立身份，不按厂商名或共享账号名合并。

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
| `credits` | Codex 账号的 credits：`balance`（数值，未知为 null）、`has_credits`、`unlimited`。只用于展示；Portal 在额度用完时暂停账号，不消耗 credits。读取失败时保留上一次的值。 |
| `balance` | 关联了计费账号时的余额读数：`metric_kind`（`balance` 或 `availability`）、`status`、`display`、`amount`、`currency`、`updated_at`、`stale`、`source_account_id`、`billing_url`。零余额保留为零，未知不填零。 |
| `status`、`error`、`refreshing` | 最近一次读取的结果。 |
| `capabilities` | 这个账号是否可以修改调度设置、是否可以查看 Resident 明细。 |

`scheduler.quota_overrides.configure` 接收 `policies` 数组。每项只包含唯一且合法的 `binding_id`、大于零的整数 `revision` 和布尔值 `enabled`。缺少字段、类型错误或额外字段均被拒绝。同一 binding 的策略按 `revision` 决定新旧：更低的版本被忽略；相同版本且内容相同是重复提交；相同版本内容不同时整批回滚并报错；更高的版本覆盖旧策略。撤销例外要提交 `enabled=false` 的更高版本，提交空数组不会删除已有策略。返回 `{"updated": N}`，有更新时 Scheduler 立即重新判断额度保护并唤醒等待中的领取和控制。这个 RPC 由可信的同机服务调用；用户是否有权放行由调用它的 SocietyService 先检查。Resident 的 `quota_reserve_override` 配置和 Scheduler 的控制状态使用同一份三字段契约：`binding_id` 限定放行对象并防止跨 Resident 继承，`revision` 防止乱序同步和旧记录覆盖，`enabled` 表达当前放行状态。配置不重复保存操作者；会话重启临时 binding 的放行也遵守同一契约。

## 相邻 Design

- ExecutionService 的组件、进程和传输：decision_25eb93d803a4。
- Scheduler 的容量分区、领取顺序和租约：decision_8ac0b5a6c0e5。
- Home 页账号表、余额金币示意、跨币种比较和调度设置表单：decision_4dea0db1f45a。
- Codex 新会话的默认账号、按项目的默认账号与故障切换：decision_3dd52c0692c1。
- Resident 按账号批量打开或关闭接单：decision_2a3ff353165c。
- 执行账号的数据库登记、页面内登录、Backend 角色与 Portal 管理的“AI Backend”分栏：decision_7956d7cb4bde。

格式名与版本须精确匹配当前规范值，校验规则见 decision_86aed2f0e8c7；本篇同时校验各字段和结构版本，不接受历史前缀或其他版本。
