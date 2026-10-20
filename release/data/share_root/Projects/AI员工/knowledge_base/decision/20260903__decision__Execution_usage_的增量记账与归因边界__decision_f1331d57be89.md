---
kb_id: decision_f1331d57be89
type: decision
project: AI员工
created_at: '2026-09-03'
version:
  number: 20
  created_at: '2026-09-03'
  created_by:
    user: ''
    agent: ''
  updated_at: '2026-10-19T19:59:40Z'
  updated_by:
    user: tfi-exported-user
    agent: resident_865c3c24f251
importance: 8
metaness: 2
ai_generated: true
links:
  source:
  - ${code_root}/management_portal/society_service/resident_usage_projection.py
  - ${code_root}/management_portal/execution/accounts.py
  - ${code_root}/management_portal/execution/scheduler.py
  - ${code_root}/management_portal/execution/agent_data_store.py
  - ${code_root}/management_portal/execution/service.py
  - ${code_root}/management_portal/execution/provider_turn_usage.py
  - ${code_root}/management_portal/execution/backends.py
  - ${code_root}/management_portal/codex_app_server_client.py
  - ${code_root}/management_portal/agent_status_backend.py
  - ${code_root}/management_portal/static/home.js
  - ${code_root}/tests/cases/test_execution_token_usage.py
  related:
  - decision_4527448ad07f
  - decision_d40f59f5508f
  - decision_fc26f883773d
  - decision_4dea0db1f45a
  - decision_b1ab45c4b3ec
  - decision_6ad0384e020d
  supersedes: []
  derived_from: []
---
# Execution usage 的增量记账与归因边界

每次模型执行都会消耗 token（模型处理文本时的计量单位）。Execution usage 负责把这些消耗可靠地记下来：执行工作进程（Worker）在执行中或完成时报告用量，Scheduler（持久调度器）去重后累加到按小时汇总的账本，AgentData（规范内容存储）保存每次执行最终报告的用量。Home 页的用量图表、运行环境指令 `GetUsage`（decision_4527448ad07f）和账号面板都从这里读取。

本文说明用量怎样规范化、怎样避免重复记账、按什么归到 Resident 和执行账号，以及查询接口返回什么。Home 页上用量图表的样子和交互见 decision_fc26f883773d，账号面板见 decision_4dea0db1f45a。

## 先认识几个概念

- **五项用量字段**：`input_tokens`（送入模型的内容）、`cached_input_tokens`（其中命中 Provider 缓存的部分）、`output_tokens`（模型输出）、`reasoning_output_tokens`（输出中被 Provider 归为推理的部分）、`total_tokens`（Provider 报告的总数，或输入与输出之和）。缓存输入是输入的一部分，推理输出是输出的一部分，所以五项直接相加会重复计算。
- **增量（delta）**：执行尚未结束时上报的一段新增用量，带一个稳定的事件 ID。
- **最终用量（final usage）**：执行结束时报告的累计用量。
- **小时桶**：按 UTC 整点汇总的一小时。
- **Resident 归因**：按执行请求 `metadata.resident_id` 把用量归到具体 Resident。
- **账号归因**：按执行实际走的路由把用量归到执行账号（decision_d40f59f5508f）。

## 一次用量怎样流动

```text
Provider 返回原生用量
          │
          ▼
Worker / Runtime 规范化为五项字段
          │
     ┌────┴─────────────────────┐
     ▼                          ▼
运行中的增量                  执行结束
     │ 按事件 ID 去重            │ 最终用量 − 已记部分
     └──────────────┬────────────┘
                    ▼
     Scheduler 小时账本（Provider、账号、Resident）
                    │
                    ▼
     SocietyService 按用户可见的 Resident 整理
                    │
                    ▼
     Portal Web 合并账号原生用量 → Home 图表与明细

执行结束 ──> AgentData 保存这次执行的最终用量
```

Scheduler 的小时账本适合按时间、Provider、账号和 Resident 做运营查询；AgentData 的执行记录回答“某一次执行最终用了多少”。两者用 `execution_id` 对齐，但小时账本本身不保存执行 ID 或会话。

## 设计点

- **用量先规范化成五项字段再记账。** 所有值转为非负整数，值为零的字段在结果中省略；Provider 原生的用量结构不保存，查询协议不绑定某个 Provider 的格式。
- **同一个增量事件只记一次。** 事件以“执行 ID + 事件 ID”去重；同一事件再次到达时返回重复，不再累加，即使第二次内容不同也按第一次处理。
- **执行结束时只补记还没记过的差额。** 最终用量减去已经通过增量记下的部分，逐项取不小于零的差，加到当前小时。
- **三种终态都记用量。** 完成、失败、取消都可以带上实际已消耗的用量；终态名称不改变已经发生的成本。
- **Resident 归因只认请求中的确切字段。** 只有 `metadata.resident_id` 完全符合 `resident_` 加 12 位小写十六进制时才归到这个 Resident，读取时先去空白并转小写；其他情况记为字面值 `others`。不从提交者、会话地址、目录、标题或会话反推 Resident。
- **账号归因使用执行实际走的路由。** 持续会话按请求的 Provider 与路由解析执行账号；Chat Completion 按请求的接口 ID 解析登记的 API 账号。不使用 Resident 当前绑定的账号推断历史。
- **Provider 记为执行它的 Provider 名称。** 例如 Claude Code 的执行记为 `claude`，Kimi Code 记为 `kimi`；独立 API 账号保持各自的身份。解析不到账号时 `account_id` 为空，表示“未记录账号”。

## 规范化规则

规范化器接受几种常见命名：snake_case（`input_tokens`）、camelCase（`inputTokens`）、OpenAI 兼容的 `prompt_tokens` / `completion_tokens` 和 `prompt_cache_hit_tokens`、输入与输出明细中的 `cached_tokens` 和 `reasoning_tokens`，以及外层的 `last` 对象（Provider 报告用量的位置：增量里是一次模型请求，轮次结果里是这个 Provider 轮次的合计）。

Anthropic 原生的 `input_tokens` 不包含另外报告的 `cache_creation_input_tokens`（写入缓存）和 `cache_read_input_tokens`（命中缓存）。规范化时三者相加得到统一的输入，命中缓存的部分写入 `cached_input_tokens`；输出明细中的 `thinking_tokens` 记为推理输出。结果只保留规范字段，所以再次规范化不会重复累加。`total_tokens` 缺失或为零时，用输入加输出补出。

记入账本的每一项都满足：输入包含命中缓存的部分，输出包含推理部分，总量等于输入加输出。

**一次执行可以包含多个 Codex 轮次。** Codex 每完成一次模型请求就发一次 `tokenUsage`：`last` 是这一次请求，`total` 是整个 thread 的累计。Worker 把每个 `last` 作为一条增量上报；Codex 客户端同时把同一 Provider 轮次内的请求相加，轮次结束时报告为 `last`，最后一次请求报告为 `context`。Resident 的一轮是一次执行，但执行中途送达的消息、中断后的续跑、Worker 交接和目标续跑都会在同一 thread 中开始新的 Provider 轮次，执行结果只带最后一个 Provider 轮次的合计。所以本轮用量以 Scheduler 的已记用量为准。Codex 子 agent 在自己的 thread 里发出请求，这些通知经 `subagent/event` 转给主轮次；Worker 同样把其中每个 `last` 作为增量上报，事件 ID 以子 thread ID 为前缀，计入本轮用量但不作为上下文大小。

**Codex 的 rollout 补齐 Worker 没收到的请求。** 实时通知只送给当前连着 thread 的 Worker。发布或重启时 Worker 交接的几秒内结束的请求，以及 Portal 加入子 agent thread 之前的请求，没有任何 Worker 收到。Codex 把每条 `tokenUsage` 通知同时写入 thread 的 rollout，记为 `token_count` 事件。所以 Worker 在每次执行尝试结束时（完成、失败、取消和交接都一样）读取主 thread 的 rollout，以及本执行期间工作过的子 agent rollout（按头部的 `parent_thread_id` 逐层找到），取执行开始以后开始的 Codex 轮次中的全部请求。它用与实时通知相同的方式算出事件 ID（子 agent 同样加子 thread ID 前缀），并带上写入时间作为 `occurred_at` 上报；实时已报的请求按事件 ID 去重。执行开始以前就开始的轮次属于更早的执行，不在此补记。rollout 从末尾往前读，遇到执行开始以前写入的请求或轮次开始就停止；持续几小时的执行会写下几百 MB 的 rollout，所以这次读取不受其他读取者 128 MB 的扫描上限约束。

**Claude Code 自己开始的轮次记入 Resident 的账本，不算进任何一轮。** 例外是本轮启动的后台子 agent：它还在运行时 Portal 轮次保持打开，CLI 为取它的 `<task-notification>` 自己开始的那一轮属于这次执行，由 Worker 上报、计入本轮用量（decision_b1ab45c4b3ec 的“子 agent 卡片”）。Portal 的一轮结束后，Claude Code 可能自己再开始一轮：它启动的后台任务完成了（`<task-notification>`）、另一个 Claude 会话发来消息、或者它关注的会话变为空闲。这一轮不属于任何执行，没有 Worker 上报它的请求。ExecutionService 每分钟读一次近期有执行的 Claude 会话记录，把不在该会话任何执行时间段内、也没有被任何执行上报过的请求，按请求发生的小时记入这个 Resident 和账号的账本。这些请求不进入任何执行的已记用量，所以 Chat 中每一轮的统计不变。每个会话已计到的时间点保存在 `execution_store_meta`，同一请求只记一次；最近两分钟内的请求等它写完再记；某个 Resident 还有未结束的执行时，只计到这个执行被接受之前；超过 20 小时的请求不再补记，因为执行的去重记录在执行结束 24 小时后删除。派生出的 Claude 会话的记录开头是来源会话条目的副本，时间仍是原来的时间，这些请求已在来源会话中计过；所以每个会话只计 Portal 登记它（第一次执行开始后几秒内）以后的请求。

**Portal 没有发起的其他用量不计入。** 有人在 Codex 客户端里直接接着 Resident 的 thread 工作，这些请求不属于任何执行，不进入账本，也不进入任何一轮的用量。

**Codex 的上下文估计不是用量。** Codex 压缩上下文后，会发出一条只有 `totalTokens`、输入和输出都为零的 `last`，它是压缩后历史的估计大小，Codex 自己的累计总量并不增加。这条更新只作为当前上下文的大小，不记账，也不计入本轮用量。

**原生 Provider 的用量从它们自己的会话文件读取。** Claude Code、Kimi Code 和 DSH 报告的结果只覆盖最后一次查询，输入也不含缓存；Worker 重启后恢复的轮次会重新提问，较早的查询就丢了。因此适配器在每轮结束时（完成、失败和取消都一样）读取 Provider 的会话文件——Claude Code 的会话记录、Kimi Code 的 agent wire、DSH 的会话日志——取执行开始以来的每一次模型请求，按上面的规则换算，再以“Provider 前缀 + 请求标识”为事件 ID 逐条作为增量上报；恢复后的重报按事件 ID 去重。本轮用量是这些请求之和，最后一次请求作为当前上下文的大小。子 agent 的请求同样计入本轮用量，但不作为上下文大小：Claude Code 读取会话目录 `subagents/` 下本轮写过的记录，Kimi Code 读取同一 session 中各子 agent 自己的 wire。读不到会话文件时，退回到 Provider 报告的结果。

## 运行中的增量记账

Worker 通过 `scheduler.event` 发送类型为 `token_usage_delta` 的事件，`details` 中带非空的 `event_id`（最长 256 字符）和用量。调用要求 Worker 仍是这次执行的当前持有者，执行仍处于领取或运行中；过期的 Worker 或已经结束的执行不能追加。

有效增量在同一个 Scheduler 事务中完成三件事：写入去重表 `execution_token_usage_events`（主键为执行 ID 与事件 ID，执行记录删除时一并删除），累加到小时账本，并加入这次执行的“已记用量”。

增量默认使用 Scheduler 收到它的时间换算小时桶。增量带有 `occurred_at` 时使用这个时间：原生 Provider 的请求在轮次结束后才从会话文件读出，带上请求实际发生的时间，长轮次的用量因此分布在它真正发生的各个小时，而不是都落在结束的那个小时。执行记录的更新时间仍取 Scheduler 收到增量的时间。

## 执行结束时补差

Worker 提交终态时带上最终用量。Scheduler 逐项计算“最终用量 − 已记用量”，只把大于零的差额加到当前小时，并把这次执行标为已完成记账。于是：

1. 没有任何增量的执行，在结束时记下完整的最终用量；
2. 已经记过部分增量的执行，只补记剩余部分；
3. 重复提交终态或恢复重放不会再次记账；
4. Provider 报告的最终用量小于已记增量时，不写负数，也不回扣历史小时。

补差之后，Scheduler 把这次执行的全部已记用量写入结果的 `accounted_usage`。它是本次执行所有模型请求（跨 Provider 轮次、Worker 交接和恢复）之和，就是本轮用量。Scheduler 代为结束的执行（Worker 已不在时的取消、租约过期后的失败）同样带上它。

**Chat 的本轮 token 统计取已记用量。** 消息本身不保存用量，Chat 读取消息时按消息记录的执行 ID 从 AgentData 执行记录取用量。ExecutionService 在执行结束后把 `accounted_usage` 写入这条执行记录用量中的 `accounted`；Worker 写入的 Provider 报告保持原样，重放终态时只与 Provider 报告比较。统计显示在结束本轮的消息上，并优先取 `accounted`：正常完成的轮次是最终回复，停止或失败的轮次是记录了本轮执行 ID 的停止或失败通知；Harness 输入即使属于同一次执行也不显示。Resident 记录的 `last_turn_usage` 和 `/status` 取结果中的 `accounted_usage`，停止和失败的轮次也一样；结果中没有它时才退回 Provider 报告的本轮用量。上下文大小仍取 Provider 报告的 `context`，没有 `context` 时取 `last`。

终态写入、补差和已记账标记在同一个事务中完成。带用量的终态提交后，ExecutionService 发布 `portal.projection.changed.token-usage.snapshot` 通知，提示 Home 刷新。

## 小时账本

Scheduler 用一张小时表 `execution_account_usage_hourly(bucket_hour, provider, account_id, resident_id, 五项用量, updated_at)`，键为“小时 + Provider + 账号 + Resident”。所有用量都写入这张表。

数据库结构版本 15 留下的旧账本 `execution_token_usage_hourly` 没有账号维度；10-07 它的 11745 行按“小时 + Provider + Resident”并入这张表，账号为空（“未记录账号”），合并前后各组合计一致，旧表随后删除（执行库结构版本 18）。这些行的 Provider 字段如果恰好是某个登记 API 账号的 ID，读取时还原为这个账号，并归到它所属的 Backend；其余不推测账号。小时数据保留 400 天，Scheduler 初始化时删除更早的数据；去重表随执行记录一起清理；AgentData 中单次执行的用量按 AgentData 自己的规则保留。

## 查询接口

ExecutionService 的 `execution.token_usage` RPC 读取小时账本，当前 schema 是 `tfi.execution-token-usage.v1`。

| 参数 | 规则 |
| --- | --- |
| `provider` | 必填，最长 120 字符，转小写后按标准身份解析；`*` 表示全部 Provider。也可以传一个登记 API 账号的 ID，这时只返回这个账号的用量。 |
| `start_hour`、`end_hour` | 取整到 UTC 整点，按左闭右开 `[start, end)` 查询；必须递增，跨度不超过 62 天。 |
| `include_accounts` | 可选，为真时按账号分行，每行增加 `account_id`；省略时同一 Provider 与 Resident 的各账号合并为一行。 |

返回 `schema`、实际使用的 `provider`、`start_hour`、`end_hour`、`rows` 和 `updated_at`（返回行中最大的更新时间，没有行时为空字符串）。`rows` 按小时和 Resident 排序，每行包含 `bucket_hour`、`resident_id`、只保留正值的 `usage`；查询全部 Provider 时每行还有 `provider`，按账号分行时还有 `account_id`。

这个 RPC 不接收 `actor`：它运行在可信的 ExecutionService socket 内，用户能看到什么由调用它的 Portal 服务决定。没有 `actor` 参数不代表普通用户可以查询全部账本。

## Home 用量的数据来源

Home 页的用量由两级整理得到。

**SocietyService 按用户可见的 Resident 整理。** 它以当前用户的身份，从 ResourceService 取得这个用户能看到的 Resident 及其 Lineage，然后查询全部 Provider、按账号分行的账本。查询范围从“今年 1 月 1 日”和“29 天前”两者中更早的那天开始，到本地下一个整点结束，按 62 天一段分批读取，再把 UTC 小时换算到 Portal 本地时区；Today、Week、Month、Year 四个页签和滚动 30 天序列都从这批数据得出。

- 可见的 Resident 得到名称、所属 Lineage 和独立的时间序列。Fork 出的 Resident 不折叠到来源 Resident 上。
- 合法但当前用户看不到的 Resident，只计入总量，不显示名称和明细。
- 记为 `others` 或不合法的 Resident ID 进入 `Others`。
- 同时生成计入缓存和不计入缓存两套结果：不计入缓存时，从总量中减去缓存输入，结果不小于零；缓存输入仍作为明细字段保留。
- 另外按“Provider”和“Provider + 账号”各生成一组 `scopes`，每组结构与总览相同，供页面按 Backend 和账号筛选；`account_id` 为空表示未记录账号。
- 查询失败时返回 `status=error`、最长 1000 字符的错误说明和空结果，`updated_at` 为空；页面据此显示失败，不会把“取数失败”当作零消耗。

**Portal Web 合并执行账号的原生用量。** 声明了原生历史用量能力的执行账号（目前是 Codex）会报告每日总用量（decision_d40f59f5508f）。Portal Web 只对已经结算的日期（昨天之前），按 Provider 用账号原生日用量减去账本中同一天同一 Provider 的已记用量，差额计入“未登记”部分。账号原生日用量包含缓存且无法拆分，所以差额只计入“计入缓存”的结果，“不计入缓存”的结果只使用账本并标明差额未计入（decision_fc26f883773d）。它不跨 Provider 抵扣，不推测近期用量，也不把账号总量分配给某个 Resident。

Scheduler 负责时间汇总，AgentData 负责单次执行记录，执行账号的原生接口负责 Provider 侧的总量，ResourceService 负责当前用户能看到哪些 Resident。四个边界分开，才能同时解释成本、执行内容、归因和权限。

## 一致性边界

增量去重、小时累加和已记用量更新在同一个 Scheduler 事务中完成；结束补差、终态写入和已记账标记也在同一个事务中完成。这样 Portal 的重试不会让账本重复计数，但无法纠正 Provider 自己报告错误的数字，也不会让五项细分变成可以相加的总量。

Claude Code 的原生订阅用量从会话记录中的每次请求进入账本，按请求的 Provider 和确切 Resident 归因。订阅本身的剩余额度由执行账号观察负责；订阅不提供额度接口时，页面显示额度不可用，不会把已消耗的 token 换算成剩余百分比。

## 历史数据修复

2026-10-01 用一次性修复脚本把对齐之前记下的用量改成上述口径（脚本 10-07 删除）：

- 修正自相矛盾的账本行：Codex 行去掉计入的上下文估计（总量改为输入加输出）；Kimi Code 和早期 Claude Code 行把缓存补进输入；Gemini 行把 thinking 补进输出和推理。
- 对 `--before` 之前结束的 Claude Code、Kimi Code 和 DSH 执行，按会话文件重算执行期间的请求，把账本缺少的部分加到执行结束的小时，并把 AgentData 中这次执行的用量改为重算结果，所以再次运行不会重复补记。

结果：Codex 去掉约 3.7 亿上下文估计，Claude Code 补记约 21.7 亿（其中 23 次失败的轮次原先没有记账），Kimi Code 和 DSH 补记少量。9 月 26 日之前的 Claude 旧记录没有缓存信息、对应的执行记录也已清理，保持原样。

格式名与版本须精确匹配当前规范值，校验规则见 decision_86aed2f0e8c7；本篇同时校验各字段和结构版本，不接受历史前缀或其他版本。
