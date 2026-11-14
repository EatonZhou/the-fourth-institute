---
kb_id: decision_4dea0db1f45a
type: decision
project: AI员工
created_at: '2026-09-03'
version:
  number: 74
  created_at: "2026-09-03"
  created_by:
    user: ""
    agent: ""
  updated_at: "2026-11-14T00:29:41Z"
  updated_by:
    user: ""
    agent: "resident_60ae6d0cdf74"
importance: 8
metaness: 1
ai_generated: false
links:
  source:
  - /opt/tfi/the-fourth-institute-dev/code/management_portal/static/home.js
  - /opt/tfi/the-fourth-institute-dev/code/management_portal/agent_status_backend.py
  - /opt/tfi/the-fourth-institute-dev/code/management_portal/http_agent_status.py
  - /opt/tfi/the-fourth-institute-dev/code/management_portal/serve_management_portal.py
  - /opt/tfi/the-fourth-institute-dev/code/tests/cases/test_claude_account_usage.py
  - /opt/tfi/the-fourth-institute-dev/code/management_portal/execution/account_resources.py
  - /opt/tfi/the-fourth-institute-dev/code/management_portal/execution/account_monitor.py
  - /opt/tfi/the-fourth-institute-dev/code/management_portal/claude_account_usage.py
  - /opt/tfi/the-fourth-institute-dev/code/management_portal/execution/account_exchange_rates.py
  - /opt/tfi/the-fourth-institute-dev/code/management_portal/execution/provider_balance_adapters.py
  - /opt/tfi/the-fourth-institute-dev/code/management_portal/execution/isa.py
  - /opt/tfi/the-fourth-institute-dev/code/management_portal/society_service/resident_intake_domain.py
  - /opt/tfi/the-fourth-institute-dev/code/management_portal/society_service/isa/execution.py
  - /opt/tfi/the-fourth-institute-dev/code/tests/cases/test_account_resources.py
  - /opt/tfi/the-fourth-institute-dev/code/tests/cases/test_account_exchange_rates.py
  related:
  - decision_898b8911a2ff
  - decision_4527448ad07f
  - decision_2a3ff353165c
  - decision_481dc4e6292c
  - decision_ed57c4ad1001
  - decision_fc26f883773d
  - decision_00546e28e1e0
  - decision_d40f59f5508f
  - decision_c524c775e0be
  - decision_f560803947c6
  - decision_e30897dd3dc3
  - decision_ef80c6d3b5ee
  - decision_3e8c2a7f91d4
  - decision_6c2f91a7d84e
  - decision_6ad0384e020d
  - decision_7956d7cb4bde
  supersedes: []
  derived_from: []
---
# Home AI 账号面板：订阅额度、API 金额与节点状态

Home 页的 AI Activity & Accounts 面板（页面结构见 decision_00546e28e1e0）上方显示活动 Resident 图标带，下方显示订阅额度和 API 金额。订阅账号保留额度窗口摘要，API 使用与订阅对齐的名称与金额行；“详情”显示当前账号的 Credits 余额、节点信息与批量接单操作，API 额外提供充值或账单入口。账号管理通过面板的“管理 AI 连接”入口进入。

账号数据由 ExecutionService（Portal 统一执行模型请求的后台服务）维护：有哪些账号、额度怎样读取、额度快用完时怎样暂停自动工作、调度设置怎样保存，都见 decision_d40f59f5508f。本文只说明 Home 怎样读取、展示和修改这些数据。

## 先认识几个概念

- **执行账号**：一条可以承接模型调用的账号，例如 Codex 的 `codex_one`、Claude Code 的原生订阅、DeepSeek 的 API 账号。每个执行账号用全局唯一的 `account_id` 标识；Home 订阅行显示该 ID，API 行显示实际计费 API 的 ID。
- **额度窗口**：订阅类账号在一段滚动时间内允许使用的量，例如 Codex 的“5 小时”和“Weekly”窗口、Claude 的“5 小时”和“每周”窗口。Home 显示每个窗口的剩余百分比和重置时间。
- **余额**：按量计费账号在 Provider（模型服务供应方）那里剩下的钱或点数，例如 DeepSeek、Kimi API、OpenRouter。有些 Provider 只能确认 API 能否使用、读不到余额，例如 Gemini 和 TypeSafe，Home 对它们显示“—”，悬浮提示说明可用性。
- **计费账号**：执行账号从哪个 API 账户扣费。几条执行路线可以共用一个计费账号，也就共用同一份余额。
- **调度设置**：每个执行账号的最大并发数、相邻两次派发之间的随机间隔（最小值到最大值），以及 Close 阈值。
- **Close 阈值与额度保护**：正阈值按原规则保护；阈值为 0 时不做阈值保护，但原生额度耗尽默认暂停。Codex 订阅账号明确开启“允许使用 credits”，且认证与付费余额新鲜可用时，阈值 0 可继续自动工作。人工 Closed 保持优先。权威规则见 decision_d40f59f5508f。
- **接单开关**：每个 Resident 有 Open 和 Closed 两种接单状态。Closed 时新消息照常收下并排队，但不开始执行；当前正在执行的工作继续完成（decision_2a3ff353165c）。
额度保护只控制 Scheduler 的领取和运行，不自动改变 Resident 的接单开关。
- **人工额度放行**：与 Resident 当前模型会话 binding 关联的额度保护例外。生效、撤销和同步语义见 decision_d40f59f5508f；Home 显示返回的放行数量和待同步提示，不用人工 Open 状态推定保护已经解除。

## 页面上看到什么

### 账号表

订阅账号使用整行额度摘要，保留账号名、额度窗口、详情按钮以及刷新或恢复状态。其窗口、错误和过期读数语义见下文。

API 与订阅使用相同的整行网格、名称字体和详情按钮列；窄屏允许换行。API 行显示 API ID、原币金额、金币可视化与详情。Home 按显式 `billing_account_id` 合并所有 Harness 路线，每个 API 只出现一行，余额取共享样本而不相加。返回 `details_scope=model_api`、`execution_account_ids` 及各路线的调度摘要 `execution_accounts`；活动图标带也按这些执行账号到 API 的映射合并，图标保留原 Resident 链接。无计费 API 的服务 Harness 不单列余额行。

金额未知或仅提供 API 可用性时显示“—”，悬浮提示区分查询不支持、查询失败、未提供余额及加载状态；保留上次金额时加“*”，实际零余额仍显示零。悬浮提示保留错误、计费账号来源和采样时间。没有节点查询能力的纯计费条目不显示空的详情入口。

### 账号详情浮层

“详情”显示 Credits 余额、节点相关信息及 API 的充值入口，内容从上到下为：

1. 当前账号标题与关闭按钮；账号提供 `credits` 时，在正文顶部显示 Credits 余额。
2. 最大并行数；API 按执行路线逐项显示。统计范围“你可见的 Resident”。
3. 节点总数、待发消息、接单状态（Open、Closed、未知）和工作状态（运行、排队、空闲、停止中、未知）。
4. 默认折叠的补充统计：采样时间、未创建 Session、人工额度放行、最近输入失败和取消的数量。
5. 刷新、全部关闭和全部打开；读取失败或读取中不能执行批量开关。
6. API 提供有效 `billing_url` 时，在底部显示“充值 / 账单”，新窗口打开；只接受绝对 HTTP(S) 地址，并设置 `noopener noreferrer`。DeepSeek 指向 `https://platform.deepseek.com/top_up`。节点读取中或失败不影响该链接；缺少地址时不显示。

浮层保留当前最大并行数、派发间隔最小值与最大值、Close 阈值编辑，并在支持的账号上增加“允许使用 credits”。不展开 API 余额、全部额度窗口、额度保护摘要或 AI Backend 配置。调度参数的权威保存与校验由 ExecutionService 拥有，Home 通过统一指令读写。

同一账号刷新保留补充统计展开状态、操作焦点与滚动位置；切换账号后重置。浮层限制宽度与最大高度，窄屏不横向溢出；超长时中部滚动，标题和底部操作保持可见。

## 设计点

### 读取与刷新

- **用量归属只读取仍有效的 Resident 与其当前所属 Lineage。** 已删除的 Resident 不参与当前账号详情。

- **页面可见时，账号表每 30 秒读取一次 Agent Status 摘要；页面隐藏时每 5 分钟读取一次。** 摘要或某个账号还在加载时，3 秒后再读一次，直到数据就绪。
- **普通轮询不会增加对 Provider 的访问。** Codex 额度百分比来自 ExecutionService 每 15 秒一次的后台采样；Claude 订阅额度来自 Claude Code daemon 每 3 分钟一次的 `/usage` 查询（CLI 本身需要 12–20 秒，daemon 在后台最多等 60 秒，超时记为“Claude 用量查询超时”），Claude 回合中带百分比的额度事件也会立即更新读数；执行账号的余额来自 ExecutionService 每 60 秒一次的读取。Kimi Code 订阅额度从账号原生 OAuth 服务读取，跟随 ExecutionService 的后台采样；中国区与国际区均使用已登录账号自己的区域。Home 只读取这些已有结果。
- **公开的 Codex 重置信号只用于提前触发原生重读。** ExecutionService 每 60 秒检查一次 `https://divin.cc/api/events` 中新增或更新的 Codex 用量重置、重置卡、Credits 发放和额度政策事件；第一次成功读取只建立基线，之后的新信号只强制重读 Codex 账号的 `account/rateLimits/read` 与 `account/usage/read`。第三方响应不直接修改剩余比例、Credits、日用量或 `resets_at`，Home 仍只展示账号原生读数；信号服务超时、不可用或返回异常结构时保留现有账号事实，继续按原来的 15 秒采样。来源地址可用 `PORTAL_CODEX_RESET_SIGNAL_URL` 替换或置空关闭。
- **读数只在读取失败时过期。** 一次成功读取得到的额度和余额一直是当前读数，放多久、过没过重置时间都不改变；只有对它的下一次读取失败时，才保留原读数并标为过期。每一层都按这条规则处理：ExecutionService 某个账号读取失败时，把这个账号的读数标为过期；Claude 的 `/usage` 查询失败时，把 Claude 窗口标为过期；余额读取失败时，把余额标为过期；Home 读取摘要失败时，把页面上已显示的读数标为过期。下一次读取成功后恢复正常显示。
- **摘要在页面读取时取当前结果，不按时间缓存。** Agent Status 摘要只组合 ExecutionService 内存里已有的账号结果，组合一次只要几毫秒，所以每次页面读取都取一次当前结果，没有缓存时限，也不按计时重建。读取方最多等 1 秒；1 秒内没组合完（例如 ExecutionService 正在重启）时，先返回上次结果并标 `refreshing`，页面 3 秒后再读。最后一次结果存到状态缓存文件，读取失败时据此返回标为过期的读数。Web 进程不另外缓存摘要。
- **点击面板顶部的“刷新”，依次刷新账号、GPU 与磁盘、定时与自动触发。** 页面先请求强制刷新 Agent Status，再重新读取 GPU / Disk Resources 和 Scheduled & Triggered 面板（只刷新当前可见的面板）。页面没有刷新单个账号的按钮。
- **强制刷新有最短间隔。** ExecutionService 两次强制重新读取账号至少相隔 60 秒。强制刷新因间隔未到被拒时，ExecutionService 返回 `refresh_retry_after_seconds` 表示下一次允许强制读取的剩余时间，之后的普通读取也带着它，直到间隔走完或下一次强制刷新获准；获准的强制刷新返回 0。读取尚未结束时持续返回 `refreshing`；Home 跟进直到读取完成，期间摘要每秒可重新组合，页面每 3 秒重读。间隔未到时保留已有结果，页面显示还需等待多久。余额缓存自身的读取间隔仍独立管理；Home 同一次操作刷新账号与余额，摘要的 `refresh_retry_after_seconds` 取账号与余额剩余等待时间的较大值。余额因间隔限制跳过时，页面同样显示等待提示；下一次获准刷新完成后清除提示。
- **某个账号读取失败只影响它自己的行。** 其他账号照常显示；失败的账号保留上次读数并标为过期，状态说明显示“用量刷新失败”。摘要请求失败时也保留已经显示的账号，追加错误提示，后续读取成功再清除提示。

### 额度窗口怎样显示

- **每个额度窗口独立展示自己的剩余读数。** 已知比例按实际数值绘制额度条并显示百分比；耗尽时显示空条和 0%，过期的零读数显示 0%*。同组其他窗口耗尽不隐藏本窗口比例，也不替换成斜纹条、“受限”或“待确认”。未知比例仍显示 —，过期比例仍保留 *。账号是否可执行由 Scheduler 的额度保护决定，账号状态继续说明预计恢复或等待确认；额度条不改写原始读数或调度行为。初次渲染和行内刷新使用同一规则。

- **首页额度恢复为一行摘要。** Antigravity 显示 Gemini Models 的 Five Hour Limit Remaining 与 Weekly Limit Remaining，并在每周后显示 Gemini 4 Argon 的 Gemini 4 Argon Weekly Limit 专属窗口，依次使用短标签“5 小时”“每周”“Argon”；各窗口沿用额度条、百分比及过期读数语义。Claude/GPT 与其余单模型额度窗口不在首页展开，完整数据仍由账号查询接口提供。悬浮提示保留完整窗口名称，数据刷新不能把摘要替换回全部窗口或长标签。Kimi Code 订阅同样只显示 5 小时与每周窗口，月度总额度、月度编程额度保留在账号查询数据中，不在首页展开。其他 Provider 保留自己的额度窗口。
- **摘要不能掩盖其他窗口的耗尽。** 未在摘要中展示的窗口，如果返回明确的数值 0，状态栏显示“其他额度已耗尽 · 见详情”，悬浮提示与详情中的简短说明列出这些窗口的完整名称、读数、采样时间与预计重置时间。全部为过期的零读数时显示“其他额度待确认”，逐项标明上次读数；未知值不按零处理，重置时间已到也不推定恢复，取得非零新读数后移除提示。通用额度余额不代表每个模型都可用；提示不改写原始额度读数、账号调度或模型选择；各窗口始终按本节规则保留自己的读数。行内刷新同时更新提示与已打开的详情。
- **详情显示 Credits 与节点状态，并解释未展示额度的耗尽。** 详情仅在存在上述耗尽读数时增加简短说明，不展开全部额度条或配置。额度条只出现在订阅行摘要；首页可用宽度不足时自然换行，短标签、额度条和百分比仍在同一行。百分比保留 `100%*` 的完整空间，不发生重叠。

- 额度标签、剩余比例、过期提示与用量汇总统一使用页面翻译服务。正常、有过期读数或额度耗尽的账号都必须能完成渲染，单个窗口不能让整个 Agent Status 面板报错。

- **额度条按读数是否过期显示。** 窗口的 `stale` 标记是唯一依据，只在读取失败时为真（见“读数只在读取失败时过期”）。读数有效时显示实色进度条和“X%”；读数过期时显示淡色进度条和“X%*”，悬浮提示写明“数据已过期，百分比为上次采样”和采样时间；剩余比例未知时显示空条和“—”。页面不按采样时间或重置时间计时变灰。Claude 窗口里的 `fresh_until` 只供 ExecutionService 的额度保护判断读数还能信多久，页面不使用。
- **行内百分比统一为整数摘要。** 所有订阅 Provider 使用相同显示规则：按最近整数显示，如剩余 2.17% 显示 2%；大于 0 且小于 1% 显示 <1%，大于 99% 且小于 100% 显示 >99%，避免把仍有额度或尚未满额显示成边界值。悬浮提示保留采样原始比例，额度条宽度与无障碍 meter 数值也使用原读数。过期摘要仍加 *，未知状态仍显示 —；该规则只调整显示，不改变采样或调度阈值。
- **已确认用完的账号在恢复前一直显示 0%。** 账号处于保护中、最后确认的剩余额度为 0，而当前窗口已经过期或读不到时，行内改为显示那些已确认为 0 的窗口，标为“0%*”；到了重置时间也保持 0%，直到取得新的读数确认恢复。
- **尚有额度的账号统一显示周刷新时间。** 即使因 Close 阈值进入额度保护，只要剩余额度大于 0，首页仍按普通账号显示“周刷新：<每周窗口的重置时间>”，不追加“额度不足，等待恢复”或“额度恢复待确认”。额度保护由原有 Close 状态呈现，账号阈值与自动执行暂停规则不变；读取中、读取失败及登录失效仍显示相应状态。
- **状态说明按以下顺序选择一项显示：**
  0. 登录凭据失效：显示“登录凭据失效 / 请重新登录该账号”；
  2. 已用尽（保护中且剩余额度为 0）的账号：所有低于阈值的窗口都知道重置时间且最晚时间未到时显示“预计恢复 <最晚的重置时间>”，否则显示“等待额度恢复确认”；
  2a. 未展示的窗口明确为零：显示“其他额度已耗尽 · 见详情”；这些零读数全部已过期时显示“其他额度待确认 · 见详情”；
  3. 正在读取：“正在刷新用量…”；
  4. 读取失败：“用量刷新失败”；
  5. 有未来的每周窗口重置时间：“周刷新：<每周窗口的重置时间>”；只取每周窗口，多个每周窗口取最早值，不用 5 小时等其他周期的时间替代；
  6. 其他情况：“等待用量刷新”。

### 余额怎样显示

- **余额与聊天能力分开。** 账号未提供余额查询不等于无法调用模型；页面显示“—”，通过悬浮提示说明未提供查询或失败原因，不伪造零余额。
- **保留原币金额，并以金币辅助展示余额。** 使用 Provider 返回的金额和币种，零值照常显示。按约 ¥10 一枚、四舍五入计算金币数量；非人民币余额仅在已有有效参考汇率时换算，悬浮提示显示折合人民币金额及汇率日期。零、负数、未知金额、仅可用性信息或缺少有效汇率时不显示金币。最多绘制 24 枚，剩余数量用“+数量”表示。读取失败时保留旧金额并加“*”，金币变淡；汇率过期样本同样变淡，下一次成功读取恢复正常。
- **只提供可用性的账号没有金额。** 显示“—”，在悬浮提示说明 API 可用性和未提供余额。
- **采样来源放在悬浮提示。** 共用计费账号、更新时间及读取错误不占列表列，也不加入节点详情。
- **账号详情显示付费 Credits。** 使用账号查询返回的 `credits` 样本，在节点统计上方显示余额，最多保留两位小数；零值显示 0，不限额显示“不限”，未知数值显示“—”，未提供 `credits` 的账号不显示该项。读取失败保留旧余额并加“*”，悬浮提示说明旧读数及采样时间；不按采样年龄自行过期。面板刷新同步更新已打开详情中的 Credits，节点统计读取中或失败也保留已有余额。显示余额不代表付费准入；只有满足账号级允许策略或现有 Session 范围授权才可使用 credits。

### 调度设置的边界

Home 账号详情提供最大并行数、派发间隔最小值/最大值、Close 阈值及受支持的 credits 选项的就地编辑。最大并行数范围为 1–256 的整数。订阅行编辑本执行账号；按 API 合并的行列出每条执行路线及其独立并行数，不为计费 API 虚构共享并行上限，也不批量覆盖其他路线。API 摘要的 `execution_accounts` 只携带各路线的账号 ID、最大并行数及派发间隔，来自 Execution 的已有账号快照；不包含连接配置或凭据。

只有具备 UpdateProviderAccount 权限的用户看到保存按钮，其余用户只读。保存前通过 ListProviderAccounts 读取目标执行账号的当前设置，再提交 UpdateProviderAccount；保存最大并行数时只提交该项变更，派发间隔沿用刚读取的值，Close 阈值不提交；保存 Close 阈值时保留刚读取的最大并行数与派发间隔，只提交该执行账号对应的 close_threshold_percent 或 close_threshold_amount。权威校验、持久化、权限检查与调度生效始终由 ExecutionService 持有（decision_d40f59f5508f）。缺少设置时禁用保存并提示刷新，不猜测默认值。保存期间防止重复提交，失败保留输入并显示错误，成功显示服务端返回的设置。普通摘要刷新保留未保存的输入及焦点；节点统计读取失败不妨碍独立的并行数与 Close 阈值设置。

Close 阈值位于并行数下方，打开详情及主动刷新时通过 ListProviderAccounts 按执行账号读取 close_threshold，不增加摘要轮询。API 合并行按每条执行路线分别展示和保存；不将 API 余额摘要当作执行账号配置。百分比阈值显示 %，允许 0–100；余额阈值显示服务返回的原币种，允许非负小数；0 关闭阈值保护，原生额度耗尽默认保护；仅在支持且明确允许 credits、新鲜认证付费余额可用时按账号策略放行。supported=false、数据缺失或读取失败时禁用保存；后台查询不反复插入“正在查询阈值”等状态文字，真实错误在固定区域显示，不臆造默认值。保存前核对最新 metric、unit 与当前表单一致，变化时要求刷新后重新输入。普通刷新保留未提交草稿及焦点，关闭或切换详情后到达的旧读取不能覆盖当前详情。


账号详情的表单与反馈区域保持稳定尺寸。打开或刷新时后台更新原值；已显示的值、未保存草稿、焦点与派发间隔编辑保留，不因 loading 文字反复插入而跳动。尚未取得设置时禁用保存，真实错误仍可见，不用布局稳定掩盖失败。

`credits_supported=true` 的账号显示“允许使用 credits”复选框，其值来自 `allow_credits`，首次默认 false，普通刷新不覆盖未提交选择。保存时通过 ListProviderAccounts 取得最新设置，保留最大并行数、派发间隔和 Close 阈值，只提交本次改动及所需原设置；修改其他字段时省略 allow_credits 以保留原值。界面说明正阈值继续保护、阈值 0 与允许且新鲜可用 credits 同时满足才放行原生耗尽；人工全部打开/全部关闭选项保留，不自动打开人工 Closed。未知或无支持能力时禁用付费选项，失败保留原输入并显示真实错误。保存继续受原 UpdateProviderAccount 权限控制，不扩大管理能力。

### 查看账号详情

- **登录的用户都可以打开账号详情，只统计自己看得到的 Resident。** 一个 Resident 计入统计，当且仅当调用者对它的 GetResident 判定通过；人类、Resident 和服务身份使用同一口径（weihao 2026-10-18）。
- **Resident 属于哪个账号，由它当前的模型会话决定。** 会话绑定的路线对应的执行账号就是它的账号；还没有模型会话的 Resident 按运行设置中的默认账号归类。 若当前没有可用默认账号，该 Resident 为未归属，不计入任何账号；查看其他账号详情不要求先补配默认路线。Execution 对此提供明确的 `DefaultAccountUnavailable` 状态，只读 `accounts.resolve` 的 `allow_unassigned=true` 可将该状态投影为空归属，同一选择器在一次查询内缓存空结果。该读取选项不影响执行提交的严格路线选择；未知显式路线、冲突配置、目录不一致及通信错误仍应失败。权限过滤必须先于归属解析。 API 详情通过 `account_scope=model_api` 查询，服务端从 Execution 目录解析所有引用该计费 API 的执行账号；在既有 GetResident 可见性过滤后汇总节点与批量接单 ID。默认 `account_scope=execution` 保留单执行账号查询；未知 scope/API 拒绝。不依赖客户端传入的执行账号清单。
- **详情只在打开时读取，不随面板轮询。** 浮层中的“刷新”会同时强制刷新整个面板和重新读取详情。读取中显示“正在读取账号节点状态…”，读取失败显示错误，两种情况下批量按钮都不可用。

### 一次打开或关闭一个账号的接单

- **“全部打开”“全部关闭”是按 Resident 的批量操作。** 页面把账号详情中的 `closed_resident_ids` 或 `open_resident_ids` 作为 `resident_ids` 交给 OpenIntake（打开传 `open: true`，关闭传 `open: false`），每个 Resident 按权限方案对它的 `resident_id` 要求单独判定，与在 Resident 上逐个开关相同（weihao 2026-10-18）。额度是否可用不影响这两个操作。
- **“全部打开”把这个账号上所有 Closed 的 Resident 设为 Open。** 每个被打开的 Resident 同时得到人工额度放行；它排队的消息按顺序继续执行，已经取消的执行不会重放。
- **“全部关闭”把这个账号上的 Resident 设为手动关闭。** Open 的 Resident 改为 Closed，已经 Closed 的保持关闭。人工额度放行同时撤销。关闭只改变接单状态：正在执行的工作继续，排队的消息保留，不会取消或删除任何工作。
- **没有需要处理的 Resident 时，按钮保持显示但不可用。** “全部打开”要求有 Closed 的 Resident；“全部关闭”要求有 Open 的 Resident。
- **同一个账号的批量操作不能叠加。** 操作进行中，这个账号的“刷新”“全部打开”“全部关闭”都不可用，正在执行的按钮显示“打开中…”或“关闭中…”；其他账号不受影响。
- **操作结束后页面报告结果并重新读取详情。** 结果写明改变了几个 Resident、跳过了几个（提交时已经处于目标状态的 Resident）、失败了几个；有失败时提示可以重试，已经处理的 Resident 不会被重复提交。人工额度放行暂时没能同步给 ExecutionService 时，提示“人工额度放行设置尚待同步，系统将自动重试”。随后页面重新读取详情和活动 Resident，不强制刷新外部额度。

## 实现：数据从哪里来

```text
ExecutionService ──accounts.list──> Agent Status 后端
（额度 15 秒 / 余额 60 秒采样）          │ 账号与余额快照
                                        v
                          GET /api/agent-status ──> home.js 账号表
Codex 公开重置信号 ──仅触发重读──> ExecutionService AccountMonitor
SocietyService ──get_account_resident_summary──> GET /api/agent-status/account-residents ──> 账号详情
home.js ──OpenIntake──> /api/instructions/execute
```

- **Agent Status 后端**读取 ExecutionService 的 `accounts.list`，把账号携带的原币余额样本转换为展示字段。账号、计费账号的关联由 ExecutionService 按显式的 `billing_account_id` 确定。没有余额样本时显示读取中或不可用。

- **余额采集**由 ExecutionService 的 AccountMonitor 负责。每个实例内，同一计费账号的读数至少复用 60 秒，共用计费账号的执行路线共用结果。
- **Codex 外部重置信号观察**也由 AccountMonitor 负责。它是可替换、可关闭的刷新触发器，不是额度事实来源；观察到新信号时只刷新 Codex，其他 Provider 不受影响，强制读取可以绕过原生日用量成功后 30 分钟的复用期。
- **参考汇率**随余额观察更新。汇率采集从 ECB 每日数据按欧元交叉换算人民币，12 小时内复用；失败时保留原汇率和日期，至少 300 秒后再试。Web 只读取快照；首页仅用有效期内（不超过 7 天）的参考汇率辅助金币可视化，Close 阈值始终按原币金额比较。
- **摘要读取与回退**按“读取与刷新”中的当前读取规则执行：同一实例共用不含用户身份的上次结果，每次请求重新组合 ExecutionService 当前快照；它用于超时或失败回退，没有另一个 30 秒过期周期。首次读取尚无余额时先返回账号，余额显示读取中。
- **账号详情**由 SocietyService 按 Resident 的会话绑定解析执行账号，批量读取接单状态、工作状态、排队数和最近输入状态。
- **节点批量操作**使用 HATG ISA 的 OpenIntake，由 SocietyService 逐个核对并保存，随后把人工额度放行同步给 Scheduler。

## 接口

### `GET /api/agent-status`

返回 Agent Status 各面板共用的摘要。与账号表有关的字段：

| 字段 | 含义 |
| --- | --- |
| `accounts` | 订阅账号保留原 Execution 样本；API 行按 billing_account_id 投影为唯一 account_id，带 details_scope=model_api、execution_account_ids、execution_accounts（各路线 account_id、max_concurrency、dispatch_interval_min/max_seconds）、balance 与节点查询能力。该投影不修改 Execution 身份或容量键。 |
| `accounts[].balance` | 余额读数：`metric_kind`（`balance` 或 `availability`）、`status`、`display`（带币种的显示文本）、`amount`、`currency`、`updated_at`、`stale`、`source_account_id`（计费账号）、`billing_url`、`error`；能换算人民币时另有 `comparison_amount`、`comparison_currency`（`CNY`），跨币种时还有 `exchange_rate_date`、`exchange_rate_source`、`comparison_stale` |
| `refreshing`、`status` | 摘要或账号监视器是否正在刷新、是否还在加载 |
| `refresh_retry_after_seconds` | ExecutionService 接受下次账号强制刷新的剩余秒数 |
| `accounts_error` | 读取 ExecutionService 失败时的说明，此时 `accounts` 为空 |

`POST /api/agent-status/refresh` 请求强制刷新，返回同样结构的摘要。

### `GET /api/agent-status/account-residents?account_id=<ID>`

返回一个账号的 Resident 统计：

| 字段 | 含义 |
| --- | --- |
| `account_id`、`observed_at` | 账号与统计时间 |
| `scope` | 固定为 `visible`（调用者可见的 Resident） |
| `total`、`queued_messages` | Resident 总数、待发消息总数 |
| `intake` | `open`、`closed`、`unknown` |
| `open_resident_ids`、`closed_resident_ids` | Open、Closed 的 Resident；“全部关闭”“全部打开”把它们作为 `resident_ids` 交给 OpenIntake（分别传 `open: false`、`open: true`）。所有计数和列表都只含调用者看得到的 Resident，其他 User 的个人 AMA 不计入 |
| `work` | `idle`、`queued`、`running`、`stopping`、`unknown` |
| `latest_input` | 最近一条输入为 `failed` 和 `cancelled` 的数量 |
| `without_session`、`quota_exempt` | 没有模型会话的数量、有人工额度放行的数量 |
| `quota` | 账号的 `quota_reserve` |

没有登录身份时返回 403，缺少 `account_id` 或账号不存在时返回 400，其他服务故障返回 503。

### 指令

- **`UpdateProviderAccount`**（decision_4527448ad07f）：参数 `account_id`、`max_concurrency`、`dispatch_interval_min_seconds`、`dispatch_interval_max_seconds`，以及可选的 `close_threshold_percent`（百分比账号）、`close_threshold_amount`（余额账号，币种取实际读数）和布尔 `allow_credits`（省略保留）。只允许 Portal 管理员；参数越界、最小间隔大于最大间隔、阈值单位与账号不符时失败。结果中的 `settings` 是保存后的设置，含 `allow_credits` 与 `credits_supported`；账号摘要沿用这两个字段。
- **`OpenIntake`**（decision_2a3ff353165c）带 `resident_ids` 和 `open`（true 打开、false 关闭）：结果包含 `changed_resident_ids`、`unchanged_resident_ids`、`failed`（每项是 Resident 与原因）、`changed_count`、`unchanged_count`、`failed_count` 和 `quota_override_pending`。

## 相邻 Design

- 执行账号目录、额度采样、额度保护、调度设置的保存与校验：decision_d40f59f5508f。
- Home 页的结构和各面板的加载方式：decision_00546e28e1e0。
- 活动 Resident 图标带：decision_e30897dd3dc3。
- Token 用量统计、按账号筛选：decision_fc26f883773d。
- GPU 与磁盘面板：decision_ed57c4ad1001；“定时与自动触发”面板：decision_481dc4e6292c。
- Resident 接单开关与按账号批量操作的指令：decision_2a3ff353165c；运行环境指令：decision_4527448ad07f。
