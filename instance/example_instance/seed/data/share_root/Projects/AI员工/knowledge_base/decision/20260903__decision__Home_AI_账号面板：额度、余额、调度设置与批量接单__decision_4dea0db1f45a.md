---
kb_id: decision_4dea0db1f45a
type: decision
project: AI员工
created_at: '2026-09-03'
version:
  number: 52
  created_at: '2026-09-03'
  created_by:
    user: ''
    agent: ''
  updated_at: '2026-10-19T11:51:01Z'
  updated_by:
    user: ''
    agent: resident_511e229bc8c3
importance: 8
metaness: 1
ai_generated: false
links:
  source:
  - ${code_root}/management_portal/static/home.js
  - ${code_root}/management_portal/agent_status_backend.py
  - ${code_root}/management_portal/http_agent_status.py
  - ${code_root}/management_portal/serve_management_portal.py
  - ${code_root}/tests/cases/test_claude_account_usage.py
  - ${code_root}/management_portal/execution/account_resources.py
  - ${code_root}/management_portal/execution/account_monitor.py
  - ${code_root}/management_portal/claude_account_usage.py
  - ${code_root}/management_portal/execution/account_exchange_rates.py
  - ${code_root}/management_portal/execution/provider_balance_adapters.py
  - ${code_root}/management_portal/execution/isa.py
  - ${code_root}/management_portal/society_service/resident_intake_domain.py
  - ${code_root}/management_portal/society_service/isa/execution.py
  - ${code_root}/tests/cases/test_account_resources.py
  - ${code_root}/tests/cases/test_account_exchange_rates.py
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
# Home AI 账号面板：额度、余额、调度设置与批量接单

Home 页的 Agent Status 区（页面结构见 decision_00546e28e1e0）有一个 **AI Activity & Accounts** 面板。面板上方是正在工作的 Resident 图标带（decision_e30897dd3dc3），下方是本文说明的**账号表**：Portal 能调用的每个 AI 账号占一行，显示它还剩多少额度或余额及状态说明；调度设置和这个账号上的 Resident 情况收在“详情”中。管理员在这里调整账号的并发和节奏；登录的用户可以一次打开或关闭某个账号上自己看得到的 Resident 的接单。

账号数据由 ExecutionService（Portal 统一执行模型请求的后台服务）维护：有哪些账号、额度怎样读取、额度快用完时怎样暂停自动工作、调度设置怎样保存，都见 decision_d40f59f5508f。本文只说明 Home 怎样读取、展示和修改这些数据。

## 先认识几个概念

- **执行账号**：一条可以承接模型调用的账号，例如 Codex 的 `codex_one`、Claude Code 的原生订阅、DeepSeek 的 API 账号。每个执行账号用全局唯一的 `account_id` 标识，Home 直接显示这个 ID。
- **额度窗口**：订阅类账号在一段滚动时间内允许使用的量，例如 Codex 的“5 小时”和“Weekly”窗口、Claude 的“5 小时”和“每周”窗口。Home 显示每个窗口的剩余百分比和重置时间。
- **余额**：按量计费账号在 Provider（模型服务供应方）那里剩下的钱或点数，例如 DeepSeek、Kimi、OpenRouter。有些 Provider 只能确认 API 能否使用、读不到余额，例如 Gemini 和 TypeSafe，Home 对它们只显示“可用”或“不可用”。
- **计费账号**：执行账号从哪个 API 账户扣费。几条执行路线可以共用一个计费账号，也就共用同一份余额。
- **调度设置**：每个执行账号的最大并发数、相邻两次派发之间的随机间隔（最小值到最大值），以及 Close 阈值。
- **Close 阈值与额度保护**：剩余额度百分比或余额小于等于 Close 阈值时，ExecutionService 把账号标为 `reserved`（保护中），暂停这个账号上的自动工作。阈值为 0 表示不设阈值；百分比账号额度用完时仍会暂停，不改用付费 credits。完整规则见 decision_d40f59f5508f。
- **接单开关**：每个 Resident 有 Open 和 Closed 两种接单状态。Closed 时新消息照常收下并排队，但不开始执行；当前正在执行的工作继续完成（decision_2a3ff353165c）。
额度保护只控制 Scheduler 的领取和运行，不自动改变 Resident 的接单开关。
- **人工额度放行**：与 Resident 当前模型会话 binding 关联的额度保护例外。生效、撤销和同步语义见 decision_d40f59f5508f；Home 显示返回的放行数量和待同步提示，不用人工 Open 状态推定保护已经解除。

## 页面上看到什么

### 账号表

面板顶部一行是活动 Resident 图标带和 **刷新** 按钮，下面每个账号一行，从左到右依次是：

| 部分 | 内容 |
| --- | --- |
| 账号名 | `account_id`，单行显示 |
| 额度或余额 | 有额度窗口的账号显示每个窗口的名称、进度条和剩余百分比；没有额度窗口、但关联了计费账号的账号显示余额；两者都没有时显示“正在读取额度…”或“额度数据不可用” |
| 详情 | “详情”按钮，打开账号详情浮层 |
| 状态说明 | 额度何时刷新、何时预计恢复，或读取失败的提示 |

几个账号共用同一套行布局。额度窗口保留显示完整百分比所需的宽度，不能被详情按钮遮挡。面板不足以容纳整行时，额度窗口先独占一行，其余控件按可用宽度换行；更窄时账号名单独占一行。长账号名及英文控件也遵循同一规则，全部内容留在卡片内。

### 账号详情浮层

点击“详情”在按钮附近打开紧凑浮层。调度设置按两列排列，统计以小型标签与数值行展示；限制宽度和最大高度，内容超长时只在中部滚动，标题、关闭按钮与底部操作保持可见。窄屏内容不能横向溢出。内容从上到下是：

1. 标题“`<account_id>` · 详情”和关闭按钮“×”。标题下是调度设置：并发上限、最小和最大间隔（秒）、Close 阈值及账号单位；三项共用一个“保存”按钮。未关联可调度执行路线的账号不显示调度表单。
2. 这个账号的余额、共用余额的计费账号来源、额度窗口、Codex Credits 余额，以及最近一次读取错误。Credits 只在对应账号的详情中展示，账号列表保留额度窗口的进度条和剩余百分比。
3. 统计范围：“统计范围：你可见的 Resident”。
4. 额度保护摘要，取值为下列之一：
   - “额度已用完 · 暂停自动工作，等待额度恢复”（百分比账号额度用完，不论阈值）；
   - “不设 Close 阈值限制 · 按账号实际可用额度运行”（阈值为 0）；
   - “额度可用 · 最低剩余 X 单位”（百分比或账号币种）；
   - “额度保护中 · 最低剩余 X 单位”；
   - “额度状态未知”。
5. 节点总数和待发消息数。
6. 接单状态：Open、Closed 的数量，有无法读取的 Resident 时另显示“状态未知”。
7. 工作状态：运行中、排队中、空闲、停止中的数量，有无法读取的 Resident 时另显示“状态未知”。
8. 默认折叠的“补充统计”：统计时间、还没有模型会话的 Resident 数、有人工额度放行的 Resident 数、最近一条输入失败的数量和被取消的数量。同一账号刷新保留展开状态；切换账号或关闭后重开恢复折叠。额度保护摘要、未知状态及读取错误始终直接显示，不收入折叠区。
9. 底部左侧是“刷新”、“AI Backend”和（有账单地址时）“账单”链接，右侧是“全部关闭”和“全部打开”。“AI Backend”关闭当前浮层并定位该 `account_id` 的管理设置；管理页的编辑、保存和权限合同见 decision_7956d7cb4bde。账号行不另放重复设置入口。只有余额或账单能力、`capabilities.resident_details=false` 的账号仍提供详情和设置入口，但不读取 Resident 明细、不显示批量接单按钮。

接单状态和工作状态分开统计：Open 或 Closed 说的是能否开始新工作，运行中或空闲说的是此刻有没有在工作。浮层中不常驻“无需操作”一类的状态建议；按钮的行为说明放在按钮的悬浮提示中。操作结果和错误只在出现时显示。

## 设计点

### 读取与刷新

- **用量归属只读取仍有效的 Resident 与其当前所属 Lineage。** 已删除的 Resident 不参与当前账号详情。

- **页面可见时，账号表每 30 秒读取一次 Agent Status 摘要；页面隐藏时每 5 分钟读取一次。** 摘要或某个账号还在加载时，3 秒后再读一次，直到数据就绪。
- **普通轮询不会增加对 Provider 的访问。** Codex 额度百分比来自 ExecutionService 每 15 秒一次的后台采样；Claude 订阅额度来自 Claude Code daemon 每 3 分钟一次的 `/usage` 查询（CLI 本身需要 12–20 秒，daemon 在后台最多等 60 秒，超时记为“Claude 用量查询超时”），Claude 回合中带百分比的额度事件也会立即更新读数；执行账号的余额来自 ExecutionService 每 60 秒一次的读取。Home 只读取这些已有结果。
- **读数只在读取失败时过期。** 一次成功读取得到的额度和余额一直是当前读数，放多久、过没过重置时间都不改变；只有对它的下一次读取失败时，才保留原读数并标为过期。每一层都按这条规则处理：ExecutionService 某个账号读取失败时，把这个账号的读数标为过期；Claude 的 `/usage` 查询失败时，把 Claude 窗口标为过期；余额读取失败时，把余额标为过期；Home 读取摘要失败时，把页面上已显示的读数标为过期。下一次读取成功后恢复正常显示。
- **摘要在页面读取时取当前结果，不按时间缓存。** Agent Status 摘要只组合 ExecutionService 内存里已有的账号结果，组合一次只要几毫秒，所以每次页面读取都取一次当前结果，没有缓存时限，也不按计时重建。读取方最多等 1 秒；1 秒内没组合完（例如 ExecutionService 正在重启）时，先返回上次结果并标 `refreshing`，页面 3 秒后再读。最后一次结果存到状态缓存文件，读取失败时据此返回标为过期的读数。Web 进程不另外缓存摘要。
- **点击面板顶部的“刷新”，依次刷新账号、GPU 与磁盘、定时与自动触发。** 页面先请求强制刷新 Agent Status，再重新读取 GPU / Disk Resources 和 Scheduled & Triggered 面板（只刷新当前可见的面板）。页面没有刷新单个账号的按钮。
- **强制刷新有最短间隔。** ExecutionService 两次强制重新读取账号至少相隔 60 秒。强制刷新因间隔未到被拒时，ExecutionService 返回 `refresh_retry_after_seconds` 表示下一次允许强制读取的剩余时间，之后的普通读取也带着它，直到间隔走完或下一次强制刷新获准；获准的强制刷新返回 0。读取尚未结束时持续返回 `refreshing`；Home 跟进直到读取完成，期间摘要每秒可重新组合，页面每 3 秒重读。间隔未到时保留已有结果，页面显示还需等待多久。余额缓存自身的读取间隔仍独立管理；Home 同一次操作刷新账号与余额，摘要的 `refresh_retry_after_seconds` 取账号与余额剩余等待时间的较大值。余额因间隔限制跳过时，页面同样显示等待提示；下一次获准刷新完成后清除提示。
- **某个账号读取失败只影响它自己的行。** 其他账号照常显示；失败的账号保留上次读数并标为过期，状态说明显示“用量刷新失败 · 查看详情”。摘要请求失败时也保留已经显示的账号与未提交设置，追加错误提示，后续读取成功再清除提示。

### 额度窗口怎样显示

- 额度标签、剩余比例、过期提示与用量汇总统一使用页面翻译服务。正常、有过期读数或额度耗尽的账号都必须能完成渲染，单个窗口不能让整个 Agent Status 面板报错。

- **额度条按读数是否过期显示。** 窗口的 `stale` 标记是唯一依据，只在读取失败时为真（见“读数只在读取失败时过期”）。读数有效时显示实色进度条和“X%”；读数过期时显示淡色进度条和“X%*”，悬浮提示写明“数据已过期，百分比为上次采样”和采样时间；剩余比例未知时显示空条和“—”。页面不按采样时间或重置时间计时变灰。Claude 窗口里的 `fresh_until` 只供 ExecutionService 的额度保护判断读数还能信多久，页面不使用。
- **已确认用完的账号在恢复前一直显示 0%。** 账号处于保护中、最后确认的剩余额度为 0，而当前窗口已经过期或读不到时，行内改为显示那些已确认为 0 的窗口，标为“0%*”；到了重置时间也保持 0%，直到取得新的读数确认恢复。
- **保护中但没有用完的账号显示等待恢复。** 状态说明显示“额度不足，等待恢复”；所有低于阈值的窗口都已过了重置时间、仍未确认恢复时，改为“额度恢复待确认”。
- **状态说明按以下顺序选择一项显示：**
  0. 登录凭据失效：显示“登录凭据失效 / 请重新登录该账号”；
  1. 只显示余额的账号：“更新 <时间>”，还在读取时显示“正在刷新余额…”；
  2. 保护中的账号：所有低于阈值的窗口都知道重置时间且时间未到时显示“预计恢复 <最晚的重置时间>”，否则显示“等待额度恢复确认”；
  3. 正在读取：“正在刷新用量…”；
  4. 读取失败：“用量刷新失败 · 查看详情”；
  5. 有未来的重置时间：“预计刷新 <最早的重置时间>”；
  6. 其他情况：“等待用量刷新”。

### 余额怎样显示

- **余额行显示原币金额。** 标签为“剩余余额”，值为 Provider 返回的金额和币种；余额读取失败、保留上次读数时在后面加“（上次记录）”，零余额显示为零，读不到时显示“余额暂不可用”，还在读取时显示“正在读取余额…”。
- **只能确认可用性的账号不显示金额。** 标签为“账号状态”，值为“API 可用 · 余额未提供”或“API 状态不可用 · 余额未提供”。
- **金币示意用来比较不同账号的余额大小。** 余额大于零且能换算成人民币时，余额后面画一排金币。金币的长度按人民币等值金额的对数计算（`2 × log2(1 + 金额)`，限制在 1 到 24 之间），只表示大小，不代表百分比或固定币值。非人民币余额按欧洲央行（ECB）的参考汇率折算；汇率超过 7 天或读不到时不画金币。汇率使用缓存结果时金币变淡。悬浮提示写明计费账号、约合人民币金额、汇率日期和更新时间。
- **共用计费账号的来源在详情中显示。** 执行账号的余额来自另一个计费账号时，只在该执行账号的详情浮层内，用独立段落显示“共用 `<计费账号>` 余额”。账号列表保留余额数值与金币示意，不为来源说明分配列；长计费账号名在详情内按可用宽度换行。
- **Codex 账号显示 Credits 余额。** Credits 是 Codex 套餐额度用完后继续计费的付费点数。仅在对应账号的详情浮层中，紧跟额度窗口的百分比后面显示“Credits 61,989”：标签与窗口名一样用灰色，数值用正文颜色和等宽数字，不用余额的绿色和金币。数值带千分位，100 及以上取整，不足 100 最多两位小数；无限额度显示“不限”，读不到显示“—”。账号读取失败时保留上次数值并加“*”、数值变灰。悬浮提示说明 Portal 在额度用完时暂停该账号的自动工作，不使用 credits。

### 修改调度设置

- **只有 Portal 管理员可以修改调度设置。** 其他用户看到的输入框和“保存”按钮都不可用，悬浮提示为“仅 Portal 管理员可修改账号调度设置”；服务端同样只接受管理员的修改。
- **在详情中一次保存当前账号的全部调度设置。** 一个“保存”按钮提交并发上限、最小间隔、最大间隔，以及可用时的 Close 阈值，沿用 UpdateProviderAccount 权限与幂等合同；提交期间禁用表单并阻止同账号重复提交。
- **提交前页面先检查输入。** 并发上限必须是 1–256 的整数；两个间隔必须填写，在 0–3600 秒之间，最大值不小于最小值；百分比阈值在 0–100 之间，余额阈值为非负金额。不符合时在对应输入框旁提示，不发送请求。
- **保存成功后，页面按服务返回的设置更新账号数据和当前详情表单，并提示“`<account_id>` 调度设置已更新”。** 保存失败时保留用户输入，恢复按钮，显示错误。新设置从下一次派发开始生效，正在运行的工作不会因为调低并发而停止。
- **Close 阈值的单位由账号决定。** 百分比账号的单位是 `%`，默认 2；余额账号的单位是它实际读到的币种，默认 0。还没有读到币种的账号，阈值输入框不可用，悬浮提示为“未提供可用的余额或额度单位，暂不可设置”。阈值为 0 表示不设阈值：余额账号可以一直用到自然用光，百分比账号在额度用完时暂停。悬浮提示为“剩余额度或原币余额小于等于正阈值时自动 Close；0 表示不设阈值，余额账号自然用光，额度账号在额度用完时暂停”。
- **详情刷新保留同一账号未保存的输入与焦点。** 账号表轮询不重建详情表单；详情异步读取或刷新重画时恢复当前草稿。切换账号或关闭后重新打开时读取对应账号设置，旧请求不能覆盖另一账号的输入。保存失败保留草稿；Resident 详情读取失败不阻止已有调度设置编辑。

### 查看账号详情

- **登录的用户都可以打开账号详情，只统计自己看得到的 Resident。** 一个 Resident 计入统计，当且仅当调用者对它的 GetResident 判定通过；人类、Resident 和服务身份使用同一口径（TFI portal 2026-10-18）。
- **Resident 属于哪个账号，由它当前的模型会话决定。** 会话绑定的路线对应的执行账号就是它的账号；还没有模型会话的 Resident 按运行设置中的默认账号归类。
- **详情只在打开时读取，不随面板轮询。** 浮层中的“刷新”会同时强制刷新整个面板和重新读取详情。读取中显示“正在读取账号节点状态…”，读取失败显示错误，两种情况下批量按钮都不可用。

### 一次打开或关闭一个账号的接单

- **“全部打开”“全部关闭”是按 Resident 的批量操作。** 页面把账号详情中的 `closed_resident_ids` 或 `open_resident_ids` 作为 `resident_ids` 交给 OpenIntake（打开传 `open: true`，关闭传 `open: false`），每个 Resident 按权限方案对它的 `resident_id` 要求单独判定，与在 Resident 上逐个开关相同（TFI portal 2026-10-18）。额度是否可用不影响这两个操作。
- **“全部打开”把这个账号上所有 Closed 的 Resident 设为 Open。** 每个被打开的 Resident 同时得到人工额度放行；它排队的消息按顺序继续执行，已经取消的执行不会重放。
- **“全部关闭”把这个账号上的 Resident 设为手动关闭。** Open 的 Resident 改为 Closed，已经 Closed 的保持关闭。人工额度放行同时撤销。关闭只改变接单状态：正在执行的工作继续，排队的消息保留，不会取消或删除任何工作。
- **没有需要处理的 Resident 时，按钮保持显示但不可用。** “全部打开”要求有 Closed 的 Resident；“全部关闭”要求有 Open 的 Resident。
- **同一个账号的批量操作不能叠加。** 操作进行中，这个账号的“刷新”“全部打开”“全部关闭”都不可用，正在执行的按钮显示“打开中…”或“关闭中…”；其他账号不受影响。
- **操作结束后页面报告结果并重新读取详情。** 结果写明改变了几个 Resident、跳过了几个（提交时已经处于目标状态的 Resident）、失败了几个；有失败时提示可以重试，已经处理的 Resident 不会被重复提交。人工额度放行暂时没能同步给 ExecutionService 时，提示“人工额度放行设置尚待同步，系统将自动重试”。随后页面重新读取详情和活动 Resident，不强制刷新外部额度。

## 实现：数据从哪里来

```text
ExecutionService ──accounts.list──> Agent Status 后端
（额度 15 秒 / 余额 60 秒采样）          │ 账号、余额与汇率快照
                                        v
                          GET /api/agent-status ──> home.js 账号表
SocietyService ──get_account_resident_summary──> GET /api/agent-status/account-residents ──> 账号详情
home.js ──UpdateProviderAccount / OpenIntake──> /api/instructions/execute
```

- **Agent Status 后端**读取 ExecutionService 的 `accounts.list`，把账号携带的余额样本和汇率转换为展示字段。账号、计费账号的关联由 ExecutionService 按显式的 `billing_account_id` 确定。没有余额样本时显示读取中或不可用。

- **余额采集**由 ExecutionService 的 AccountMonitor 负责。每个实例内，同一计费账号的读数至少复用 60 秒，共用计费账号的执行路线共用结果。
- **参考汇率**随余额观察更新。汇率采集从 ECB 每日数据按欧元交叉换算人民币，12 小时内复用；失败时保留原汇率和日期，至少 300 秒后再试。Web 只读取快照；汇率只用于画金币，Close 阈值始终按原币金额比较。
- **摘要读取与回退**按“读取与刷新”中的当前读取规则执行：同一实例共用不含用户身份的上次结果，每次请求重新组合 ExecutionService 当前快照；它用于超时或失败回退，没有另一个 30 秒过期周期。首次读取尚无余额时先返回账号，余额显示读取中。
- **账号详情**由 SocietyService 按 Resident 的会话绑定解析执行账号，批量读取接单状态、工作状态、排队数和最近输入状态。
- **修改和批量操作**使用 HATG ISA 指令：UpdateProviderAccount 由 ExecutionService 执行；OpenIntake 由 SocietyService 逐个核对并保存，随后把人工额度放行同步给 Scheduler。

## 接口

### `GET /api/agent-status`

返回 Agent Status 各面板共用的摘要。与账号表有关的字段：

| 字段 | 含义 |
| --- | --- |
| `accounts` | 账号列表。执行账号的字段与 ExecutionService 的 `accounts.list` 相同（`account_id`、`rate_limits`、`quota_reserve`、`close_threshold`、调度设置、`capabilities`、`status`、`error`、`refreshing`，含义见 decision_d40f59f5508f），另加 `balance` |
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

- **`UpdateProviderAccount`**（decision_4527448ad07f）：参数 `account_id`、`max_concurrency`、`dispatch_interval_min_seconds`、`dispatch_interval_max_seconds`，以及可选的 `close_threshold_percent`（百分比账号）或 `close_threshold_amount`（余额账号，币种取账号实际读到的币种）。只允许 Portal 管理员；参数越界、最小间隔大于最大间隔、阈值单位与账号不符时失败。结果中的 `settings` 是保存后的设置。
- **`OpenIntake`**（decision_2a3ff353165c）带 `resident_ids` 和 `open`（true 打开、false 关闭）：结果包含 `changed_resident_ids`、`unchanged_resident_ids`、`failed`（每项是 Resident 与原因）、`changed_count`、`unchanged_count`、`failed_count` 和 `quota_override_pending`。

## 相邻 Design

- 执行账号目录、额度采样、额度保护、调度设置的保存与校验：decision_d40f59f5508f。
- Home 页的结构和各面板的加载方式：decision_00546e28e1e0。
- 活动 Resident 图标带：decision_e30897dd3dc3。
- Token 用量统计、按账号筛选：decision_fc26f883773d。
- GPU 与磁盘面板：decision_ed57c4ad1001；“定时与自动触发”面板：decision_481dc4e6292c。
- Resident 接单开关与按账号批量操作的指令：decision_2a3ff353165c；运行环境指令：decision_4527448ad07f。
