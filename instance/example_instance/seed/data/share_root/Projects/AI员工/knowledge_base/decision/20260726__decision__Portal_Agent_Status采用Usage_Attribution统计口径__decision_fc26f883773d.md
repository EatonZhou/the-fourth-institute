---
kb_id: decision_fc26f883773d
type: decision
project: AI员工
created_at: '2026-07-26'
version:
  number: 27
  created_at: '2026-07-26'
  created_by:
    user: ''
    agent: ''
  updated_at: '2026-10-19T19:42:22Z'
  updated_by:
    user: tfi-exported-user
    agent: resident_865c3c24f251
importance: 8
metaness: 2
ai_generated: true
links:
  source:
  - ${code_root}/management_portal/http_agent_status.py
  - ${code_root}/management_portal/execution/account_monitor.py
  - ${code_root}/management_portal/token_usage_snapshot_cache.py
  - ${code_root}/management_portal/society_service/resident_usage_projection.py
  - ${code_root}/management_portal/codex_app_server_client.py
  - ${code_root}/management_portal/agent_status_backend.py
  - ${code_root}/management_portal/index.html
  - ${code_root}/management_portal/static/home.js
  - ${code_root}/management_portal/static/portal_home_components.js
  - ${code_root}/management_portal/static/portal_workbench.js
  related:
  - decision_4527448ad07f
  - decision_8ac0b5a6c0e5
  - decision_00546e28e1e0
  - decision_4dea0db1f45a
  - decision_3e8c2a7f91d4
  supersedes: []
  derived_from: []
---
# Home Usage Attribution 的 token 统计

Usage Attribution 是 Home 页 Agent Status 区域中的一个子面板（Home 的整体布局见 decision_00546e28e1e0），用来查看模型用掉了多少 token，以及这些 token 用在了哪些 Project、Lineage 或 Resident 上。**token** 是模型处理文本的计量单位，输入和输出都按 token 计数；**归因**是把一次模型调用的用量记到发起它的 Resident 名下。

统计包括 Portal 中所有 Resident（包括定时与自动触发的工作）通过各个模型服务（Provider，例如 Codex、Claude、Kimi、DeepSeek、OpenRouter）发起的调用。Codex 账号还能从 OpenAI 读到每天的总用量，其中 Portal 没有记录到的部分（例如在 Portal 之外使用同一账号）也补进统计，但不归给任何 Resident。执行账号的额度与余额由 decision_4dea0db1f45a 说明。

## 面板上的内容

面板第一行是控件，右上角是“刷新”按钮：

| 控件 | 作用 | 默认值 |
| --- | --- | --- |
| Backend | 只看某个 Provider 的用量 | 全部 Backend |
| 账号 | 只看某个执行账号的用量；选项按 Provider 分组，另有“未记录账号” | 全部账号 |
| Include cache | 是否把命中缓存的输入 token 计入用量（见“统计口径”） | 不计入 |
| Today / Week / Month / Year | 图表和明细使用的时间范围 | Today |
| Project / Lineage / Resident | 图表颜色和明细按 Project、Lineage 还是 Resident 分组 | Lineage |

控件下方依次是：

- **四个周期总量**：Today、Week、Month、Year 各一个，只显示周期名称和 token 总量，始终同时可见。
- **堆叠柱状图**：按所选时间范围显示用量随时间的变化。Today 固定为 24 根柱，每小时一根；Week 从本周一到今天、Month 从本月 1 日到今天，每天一根；Year 从 1 月到本月，每月一根。每根柱按分组对象分成不同颜色的色段。鼠标悬停或用键盘聚焦一根柱时，提示显示时间、总量以及每个色段的名称和数值。
- **图例**：说明每种颜色代表哪个 Project、Lineage 或 Resident。
- **状态提示**：只在读取中（“Loading usage…”）、读取失败（“Usage unavailable.”）或数据不完整时显示；仅含缓存视图在原生用量不完整时点名没有更新的账号，例如“部分账号的原生用量未更新：codex_zero（stale，CodexTurnAuthenticationError）；其余数据已刷新”，服务器没有给出账号时显示“Usage data incomplete.”；刷新失败时显示“Usage refresh failed: <原因>”。选中某个账号且这个 Provider 有部分历史记录没有账号信息时，显示“部分历史用量未记录账号，可在“未记录账号”中查看。”不勾选 Include cache 且所选范围有 Codex 补差时，显示“账号自报的日用量含缓存且无法拆分，此视图不计入；勾选 Include cache 后计入。”
- **Details**：点击展开，按当前分组列出所选周期中用量最多的前 20 个 Project、Lineage 或 Resident，以及周期总量和数据更新时间。

数字按 K（千）、M（百万）、B（十亿）缩写，保留一位小数。

图表和明细中有两个固定分组，不对应具体的 Project、Lineage 或 Resident：

- **Other resident(s)**：确实由某个 Resident 发起，但不能在这里显示名字的用量。包括当前用户无权读取的 Resident、已经删除的 Resident、排在前 20 名以外的对象，按 Lineage 分组时所属 Lineage 不可读的 Resident，以及按 Project 分组时所属 Lineage 不可读、该 Lineage 不属于任何 Project 或所属 Project 不可读的 Resident。
- **Unregistered session(s)**：没有 Resident 归属的用量。包括执行记录中没有 Resident ID 的调用，以及从 Codex 账号日总量中补进来的用量。

## 设计点

- **切换时间范围、分组、Include cache 和筛选条件都不向服务器发请求。** 服务器一次返回全部组合所需的数据，页面只切换显示；切换后，四个周期总量、图表、图例和明细同时更新。
- **切换一个控件时，其他控件的选择保持不变。** 例如在 Resident 视图中切到 Year，再切回 Lineage 视图，时间范围仍是 Year；Details 已展开时保持展开。
- **控件选择按 Portal 用户保存在浏览器中。** Include cache、Project / Lineage / Resident 分组、Backend 和账号筛选分别保存；同一浏览器中换一个用户登录，读取的是那个用户自己的选择。时间范围和 Details 是否展开只在当前页面中保留。
- **保存的筛选对象不再存在时回到“全部”。** 例如保存的账号已经不在账号列表中，页面自动改回“全部账号”。
- **当前用户无权读取的 Resident 保持匿名。** 它的用量计入 Other resident(s)，名字、ID 不出现在图表、图例、提示和明细中；Lineage 和 Project 也只在当前用户能读取它们时才显示名字。匿名用量不转给其他 Resident。
- **已删除 Resident 的历史用量仍然算在统计里。** 执行记录保存的是 Resident ID，Resident 删除后这些用量计入 Other resident(s)。
- **刷新只刷新本面板。** “刷新”按钮要求服务器重新统计；统计期间按钮显示“刷新中…”并禁用。两次手动刷新至少间隔 5 分钟，间隔不够时服务器不重新统计，页面提示“Please retry in N minutes.”。
- **读取失败时保留已显示的图表。** 页面显示错误提示，已经显示的数据不清空；Agent Status 的其他子面板不受影响。数据缺失或不完整时显示对应提示，不把缺失的数据显示成零。
- **调整面板布局不影响统计。** 拖动、折叠、隐藏和恢复这个面板，不改变控件选择，也不发起统计请求。

## 统计口径

### 数据来源

统计使用两份数据：

1. **执行账本**：Portal 执行层（ExecutionService，decision_8ac0b5a6c0e5）记录的每一次模型调用的用量，按小时、Provider、执行账号和 Resident 汇总保存。Resident ID 在写入时确定；无法确定 Resident 的调用记作没有 Resident 归属。Claude Code 在 Portal 的轮次之外自己开始的轮次（后台任务完成通知、跨会话消息、空闲通知）由执行层从会话记录补进账本，归到所在的 Resident（decision_f1331d57be89）。较早的记录没有保存执行账号，这些记录在账号筛选中显示为“未记录账号”。账本持续保留历史记录。
2. **Codex 账号日总量**：执行层定期通过 Codex 的 `account/usage/read` 接口读取每个 Codex 账号每天用掉的 token 总数，并保存在本地。它只有日期和总数，没有 Resident 信息；总数包含命中缓存的输入，但无法拆出这一部分。

### token 的计数方式

每条记录包含 `input_tokens`（输入）、`cached_input_tokens`（输入中命中缓存、由模型复用的部分）、`output_tokens`（输出）、`reasoning_output_tokens`（输出中的推理部分）和 `total_tokens`（总量）。

- 总量优先使用 Provider 报告的 `total_tokens`；没有报告时用输入加输出。推理部分已经包含在输出中，不再另加。
- Provider 原始字段的归一化、增量记账和结束补差以 decision_f1331d57be89 为唯一权威；Home 消费已经规范化的账本字段，不在展示层重新解释各 Provider 的原始 token 字段。
- **Include cache 不勾选时**，每条记录的总量减去命中缓存的输入（最小为零）；勾选时使用原总量。两者之差正好等于这段时间命中缓存的输入。

### Codex 账号补差

执行账本只记录经过 Portal 执行层的调用。为了让总量接近账号实际用量，对 Codex 按天补差：

```text
某个统计范围、某个 UTC 日的补差 = max(0, 该范围账号当日总量 − 该范围执行账本的 Codex 当日用量（含缓存）)
```

- 只对前天及更早的日期补差；今天和昨天的账号日总量还没有稳定，只使用执行账本。
- 全部账号、某个 Provider 和某个账号分别按对应范围对账。单个账号有自己的补差；“未记录账号”只含账本记录，不分配补差。
- Codex 报告的日总量采用 UTC 日。对账使用账本按 UTC 日生成的 Provider 合计；页面把补差放在账号报告标注的日期，不转换为本地日期，也不把这份只有日粒度的数据分摊到小时。执行账本的曲线仍按服务器本地日期显示。
- 补差计入 Unregistered session(s)，不分给任何 Resident，也不写成执行记录。账号报告的总量小于账本时补差为零，账本中的用量保持不变。
- 账号日总量包含缓存且无法拆分，所以补差只计入“含缓存”的统计；“不含缓存”的统计只使用执行账本，并在页面上注明补差未计入。
- 目前只有 Codex 账号提供日总量，其他 Provider 只使用执行账本。

用公式表示：对前天及更早的某一天，设 `A` 为所选范围 Codex 账号当个 UTC 日的总量，`C` 为该范围账本中 Codex 同一 UTC 日含缓存的用量，`E` 和 `N` 分别为账本中全部 Provider 当天含缓存和不含缓存的用量，补差 `D = max(0, A − C)`。这一天含缓存的总量为 `E + D`，不含缓存的总量为 `N`；今天和昨天 `D = 0`。

### 时间范围

- 日期按服务器的本地时区划分。Today 是今天；Week 从本周一开始；Month 从本月 1 日开始；Year 从今年 1 月 1 日开始。
- 服务器读取的记录覆盖今年全年、完整的本周和最近 30 天。本周跨年时，去年那几天计入 Week，不计入 Year。
- 每个周期的总量等于图表中这个周期所有柱的和，每根柱的色段之和等于柱的总量。

### 按 Backend 和账号筛选

选择 Backend 或账号时，四个周期总量、图表、图例和明细都只统计所选范围内的记录：

| 选择 | 统计范围 | 是否包含 Codex 补差（只在勾选 Include cache 时） |
| --- | --- | --- |
| 全部 Backend、全部账号 | 所有记录 | 包含 |
| 某个 Backend、全部账号 | 该 Provider 的全部记录 | 选中 Codex 时包含 |
| 某个 Backend、某个账号 | 该账号的记录 | 该账号提供日总量时包含 |
| 某个 Backend、未记录账号 | 该 Provider 中没有账号信息的记录 | 不包含 |

补差保持来源账号身份，计入对应账号范围。只选账号时，页面同时选中这个账号所属的 Backend。账号列表来自执行层登记的账号和账本中出现过的账号。

## 分组与匿名规则

分组只决定色段、图例、提示和明细中显示哪些对象，不改变总量。

| 用量的归属和当前用户的权限 | Resident 视图 | Lineage 视图 | Project 视图 |
| --- | --- | --- | --- |
| Resident、它所属的 Lineage 和该 Lineage 所属的 Project 都可读 | 该 Resident | 计入该 Lineage | 计入该 Project |
| Resident 和所属 Lineage 可读，该 Lineage 不属于任何 Project 或 Project 不可读 | 该 Resident | 计入该 Lineage | Other resident(s) |
| Resident 可读，所属 Lineage 不可读 | 该 Resident | Other resident(s) | Other resident(s) |
| Resident 不可读，或已经删除 | Other resident(s) | Other resident(s) | Other resident(s) |
| 没有 Resident 归属，或来自 Codex 补差 | Unregistered session(s) | Unregistered session(s) | Unregistered session(s) |

- 图表在每个时间范围内分别按用量选出前 20 个可命名的对象，作为有名字的色段；其余可归属的用量合并到 Other resident(s)。
- Lineage 视图先把每个 Lineage 下所有可读 Resident 的用量加起来，再选前 20 名。
- Project 视图把 Resident 经它现在所属的 Lineage 归入这个 Lineage 登记的 Project（Lineage 的 `project_id`），先把每个 Project 下各 Lineage 的可读 Resident 用量加起来，再选前 20 名。Project 按 ID 区分和显示，例如 `AI员工`；Project 没有 ID 以外的名称。Lineage 改到别的 Project 后，它的成员过去的用量也随之计入新的 Project；没有可读 Lineage 时不读取 Project，Lineage 不可读时不显示它所属的 Project。
- Lineage 按 ID 区分，同名的两个 Lineage 分别统计。Resident 归到它现在所属的 Lineage；Resident 迁移到别的 Lineage 后，它过去的用量也随它计入新的 Lineage。 当前归属按需批量读取仍存活 Resident 的规范 `resident_id`、`lineage_id`，不从资源索引残留的关系推断，也不为统计组装完整工作区、扩展、运行设置或 Lineage 图；未知或已删除身份不作为活动归属返回。Resident 与目标 Lineage 的可读性仍分别由 ResourceService 检查，轻量归属查询不授予权限、也不跨请求缓存权限；不可读归属仍按匿名规则计入总量，无法确认授权范围或归属读取失败时明确报告不可用。

## 刷新与缓存

服务器为每个 Portal 实例（Dev 或 Release）和每个用户分别保存一份统计快照。快照中包含全部统计范围（全部、每个 Provider、每个账号、未记录账号），每个范围又包含含缓存和不含缓存两种统计，以及按 Resident、Lineage 和 Project 的图表与排名。

- **服务器先返回已有的快照，需要时在后台重新统计。** 没有快照、快照超过 10 分钟，或者用户手动刷新时，服务器在后台启动一次统计，同一用户同时只运行一次。统计只读取执行账本和本地保存的账号日总量，不等待任何外部服务。
- **统计不完整或失败时 60 秒后可以重试。** 某个账号没有可用的日总量、日总量已经过期或格式有误时，服务器仍用其余数据完成统计，只把含缓存视图标记为不完整（`partial`）；不含缓存视图不依赖账号日总量，保持账本的状态，在 `warning` 中点名这些账号和最近一次读取失败的类型。不完整的新结果照常替换已有快照：日总量过期的账号仍使用最近一次读到的日总量，其余账号和执行账本都是最新的，个别账号（例如登录过期）不能让所有人的用量停在旧快照上。只有统计本身失败时才保留已有快照，并在响应中附上 `refresh_error`。
- **每份快照有递增的版本号 `snapshot_version`。** 页面只接受版本不低于当前显示的快照，较早发出、较晚返回的响应不会覆盖新数据；内容没有变化的响应不重新绘制图表。
- **页面定时读取快照。** 正常每 5 分钟读取一次；服务器正在统计时每 3 秒读取一次，直到统计完成；浏览器标签页在后台时每 5 分钟读取一次。
- **页面在浏览器中保存上一次的快照。** 每个用户一份，保存在浏览器的 IndexedDB 数据库 `tfi.home.tokenUsage.v1` 中。再次打开面板时先显示这份快照，再向服务器读取最新数据。

`snapshot_version` 表示快照发布顺序，`updated_at` 表示所读账本的最新写入时间。`collection_started_at`、`collection_finished_at` 和 `collection_windows` 记录本次后台统计的实际采集时间与查询窗口。缓存读取及失败时保留的旧快照继续使用原采集信息。这些字段属于统计读模型，不写入 AgentData 或业务审计事实。

Today 的 `bucket_groups` 提供小时与完整归属的账本用量，不经过图表前 20 名截断。每个桶用带时区偏移的完整日期时间标识，组以 ID 区分；不可读或无归属记录合并为 `id: ""`、`anonymous: true`，不暴露隐藏 ID。页面每个范围、每种缓存口径同时提供 Resident、Lineage 和 Project 三套分组；`GetUsage` 返回所选分组（`group_by` 为 `resident`、`lineage` 或 `project`）。两者复用同一授权范围和规范归属的批量读取，从已有账本行汇总，不为完整分组另查账本或逐 Resident 读取归属。小时分组只含执行账本，Codex 日总量补差没有小时证据，不分摊到小时；页面原有周期总量和补差显示保持其既有口径。成功观察到的零用量小时用空分组表示，未观察到或读取失败须保留缺失或错误状态，不能当作零用量。

## 实现

### 组件分工

| 组件 | 位置 | 职责 |
| --- | --- | --- |
| 执行账本 | ExecutionService 的 `execution_account_usage_hourly` 表（2026-09-26 以前的记录账号为空） | 保存每小时、每个 Provider、账号和 Resident 的用量 |
| 账号用量监视器 | ExecutionService（`execution/account_monitor.py`） | 读取并保存 Codex 账号日总量；成功后 30 分钟内不重复读取，失败 60 秒后重试；超过 31 分钟没有更新的数据标记为过期 |
| Resident 用量投影 | SocietyService（`society_service/resident_usage_projection.py`） | 读取账本，向 ResourceService 查询当前用户能读取的 Resident、Lineage 和 Project，按权限规则生成两种缓存口径、各个统计范围的时间序列和排名 |
| Agent Status 后端 | Portal Web（`agent_status_backend.py`） | 加入 Codex 补差，生成图表色段和排名，保存和返回快照 |
| Home 前端 | `static/home.js` | 按控件选择从快照中取出对应数据，绘制总量、图表、图例和明细 |

### 一次统计怎样形成

1. Resident 用量投影从执行账本读取覆盖统计时间的全部记录。
2. 它向 ResourceService 分别查询当前用户可读的 Lineage（连同各自登记的 Project）、可读的 Project 和可读的 Resident，再对可读 Resident 批量读取当前身份及 Lineage 归属；Project 归属由 Lineage 推出，不另外读取。该读取只提供统计需要的两个身份字段，不读取完整对象；发现后已删除或不存在的身份不复活，读取失败不伪装为成功的空结果。
3. 对每条记录：有 Resident ID 的记为已归属，Resident 可读时记到这个 Resident 名下，不可读时只计入已归属总量；没有 Resident ID 的记为无归属。含缓存和不含缓存分别计算。
4. 同样的计算对全部记录、每个 Provider、每个账号和每个 Provider 的无账号记录各做一遍，得到各个统计范围。
5. Agent Status 后端向 ExecutionService 读取 Codex 账号日总量，按上文公式计算补差，加入对应统计范围含缓存统计的无归属用量。
6. 后端生成 Today 的 24 个小时、Week 和 Month 的每天、Year 的每月时间点，按 Resident、Lineage 和 Project 分别选出前 20 名，生成色段、周期总量和排名。
7. 快照保存在 Agent Status 配置的缓存位置，并返回给页面。默认状态缓存为 `portal_paths.staging_root/shared/portal_cache/agent_status/status_cache.json`，可用 `AGENT_STATUS_CACHE_PATH` 覆盖；统计快照由 `token_usage_snapshot_cache` 写入该配置路径派生的 `.usage/` 目录，按环境与用户身份保存，独立于状态缓存文件。

### 接口

| 请求 | 作用 |
| --- | --- |
| `GET /api/agent-status/usage` | 返回当前用户的快照；需要时在后台启动统计 |
| `POST /api/agent-status/usage/refresh` | 要求重新统计；距上次手动刷新不足 5 分钟时不重新统计 |

两个请求返回相同结构。顶层是“全部 Backend、全部账号”的统计，包含：

- `windows`：Today、Week、Month、Year 的总量（`total_tokens`、`assigned_tokens` 已归属、`other_tokens` 无归属）；
- `chart_series`、`lineage_chart_series` 与 `project_chart_series`：按 Resident、Lineage 和 Project 分组的图表，各有 `today`、`week`、`month`、`year` 四组时间点，每个时间点包含总量和色段列表；
- `bucket_groups`：Today 的完整账本小时分组，含 `resident`、`lineage` 和 `project` 三组数组；每项为 `{bucket, groups: [{id, total_tokens, anonymous?}]}`。它随范围和缓存口径一起生成，原有图表及排名字段继续承担显示用途；
- `residents`、`lineages` 与 `projects`：可读 Resident、可读 Lineage 和可读 Project 在四个周期中的用量，用于明细排名；Project 项为 `{project_id, title, periods}`；
- `variants`：`including_cached_input` 和 `excluding_cached_input` 两种缓存口径，每种都包含上面的字段；`excluding_cached_input` 不含 Codex 补差，有补差未计入时带 `account_totals_omitted: true`，页面据此显示说明；
- `scopes`：按 Provider 和账号划分的统计范围，每项有 `provider`、`account_id`（`null` 表示该 Provider 的全部账号，空字符串表示未记录账号）、`account_history_incomplete` 和与顶层相同的字段；
- `filters`：可选的 Backend 列表和账号列表；
- `status`（`ok`、`partial`、`error` 或 `loading`）、`updated_at`、`snapshot_version`、`refreshing`（服务器是否正在统计）、`refresh_error`（统计失败、仍显示旧快照时的原因）、`warning`（`partial` 时点名原生用量未更新的账号）、`incomplete_accounts`；
- `collection_started_at`、`collection_finished_at`：该服务器快照的真实后台采集起止；`collection_windows`：原有账本读取的 `{start, end, read_started_at, read_finished_at, status}`。这组元数据在快照顶层，适用于同次采集得到的全部范围与缓存口径；
- `manual_refresh_skipped` 与 `manual_refresh_retry_after_seconds`：手动刷新被间隔限制跳过时，说明还要等多少秒。

同样的账本也可以通过 HATG 指令 `GetUsage` 读取（decision_4527448ad07f）；该指令直接汇总执行账本，不包含 Codex 补差；除既有总量、序列和排名外，返回同一格式的采集起止及读取窗口，以及所选维度的 `bucket_groups`。Today 为本地日的 24 个小时，其他周期沿用指令现有的日或月桶粒度。
