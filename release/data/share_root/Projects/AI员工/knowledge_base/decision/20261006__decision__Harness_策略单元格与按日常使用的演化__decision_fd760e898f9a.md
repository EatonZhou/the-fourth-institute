---
kb_id: decision_fd760e898f9a
type: decision
project: AI员工
created_at: '2026-10-06'
importance: 7
ai_generated: false
links:
  source:
  - ${code_root}/management_portal/execution/harness_policy_table.py
  - ${code_root}/management_portal/execution/harness_evolution.py
  - ${code_root}/management_portal/execution/harness_evolution_evaluation.py
  - ${code_root}/management_portal/execution/harness_evolution_offline.py
  - ${code_root}/management_portal/execution/harness_auto_policy.py
  - ${code_root}/scripts/harness_evolution.py
  - ${code_root}/tests/cases/test_harness_evolution.py
  - ${code_root}/tests/cases/test_harness_evolution_offline.py
  - ${code_root}/scripts/harness_lineage.py
  - ${code_root}/management_portal/execution/harness_evolution_runner.py
  related:
  - decision_5c62deb8253a
  - decision_5ff0cb51ed84
  - decision_8b510cbd9c58
  - decision_b820faf42218
  supersedes: []
  derived_from: []
version:
  number: 25
  created_at: '2026-10-06T10:53:38Z'
  created_by:
    user: tfi-exported-user
    agent: resident_57f585eb8ae9
  updated_at: '2026-10-19T10:40:15Z'
  updated_by:
    user: ''
    agent: resident_60ae6d0cdf74
metaness: 2
---
# Harness 策略单元格与按日常使用的演化

AgentHarness 的自动策略（decision_5c62deb8253a）按信号选插件，再按一组参数决定插件怎样用：压缩位置、卸载阈值、何时降推理强度等。这些参数原来只在人工实验后修改。现在，参数按日常使用自动演化：

- 系统把 Provider 和负载族的每种组合当作一个**单元格**；
- 一步改动能省多少，用记录下来的模型调用**离线重放**算出：同一批会话分别按新旧参数重放，比较两次的输入 token；
- 改动会不会伤害结果，让一小部分会话**线上试用**新参数，用失败率和评审质量判断，要求“不比冠军差出限度”；
- 每天据此决定回滚、拒绝、继续，或建议采用新版本；采用新版本由人确认。

成本以同一批会话的离线重放比较，减少会话长度差异带来的噪声；线上分组用于评估失败率与结果质量。

## 单元格、冠军与挑战者

- **单元格**由 Provider 和负载族（`load_family`，如 `short_text`、`repository_work`、`tool_heavy`）组成。
  - 负载族按 turn 开始时的信号算出，在算出参数之前确定，所以单元格不随参数变化。
  - 手动计划（`load_family=manual`）不属于任何单元格。
- **冠军**是这个单元格里每个 turn 使用的参数覆盖值。
  - 它覆盖的是 `harness_policy_table.py` 中这个 Provider 的参数；
  - 空的冠军就是 Provider 自己的参数。
- **挑战者**是冠军改动一个可调参数、只走一步得到的参数集。
  - 它在一部分会话上运行，默认占 10%，上限 25%。
  - 一个单元格同时最多有一个挑战者。
- **单元格状态**：
  - `stable`：只跑冠军；
  - `exploring`：冠军与挑战者同时运行；
  - `promotion_recommended`：日评估建议采用挑战者，两组继续按原比例运行，等人确认；
  - `frozen`：不再有任何自动变化，冻结时结束挑战者。
- **可调参数**登记在 `harness_policy_table.TUNABLES`，每项有安全范围和步长：

  | 参数 | 范围 | 步长 |
  | --- | --- | --- |
  | `compaction_threshold_percent` | 55–85 | 5 |
  | `compaction_max_context_tokens` | 10 万–80 万，0 表示不设上限 | 10 万 |
  | `protect_history_pressure_percent` | 40–70 | 6 |
  | `high_tool_load_calls` | 16–64 | 8 |
  | `offload_trigger_bytes` | 16–128 KiB | 16 KiB |
  | `reasoning_downgrade_pressure_percent` | 40–88 | 6 |

  - 任何覆盖值（单元格里的、配对实验的）都先按这张表校验。
  - Provider 自己处于“关”值（如 Codex 的压缩上限 0）的参数，演化不会自动打开；打开它要由人决定。

## 一个 turn 怎样拿到参数

1. 自动策略算出负载族后，从**本进程的单元格副本**查到这个 turn 的单元格。
   - 副本到期后由后台线程刷新，刷新间隔为 60 秒；轮次读取已有副本，不等待数据库。
   - 刷新失败时继续使用上一份副本；没有副本时用 Provider 自己的参数。
   - 因此单元格读取失败不会让 turn 失败。
2. 用会话键、单元格和单元格版本的哈希决定这个会话属于哪一组。
   - 同一会话在单元格版本不变期间始终在同一组，对话中途不会换参数。
   - 单元格版本在开始、结束挑战者或采用新版本时加一；版本变化后重新分组。
3. 把这组的覆盖值套到 Provider 参数上，再按新参数计算上下文压力和各插件的配置。
   - 覆盖值不能用时（例如代码已经不认识的参数），这个 turn 退回已经校验的冠军覆盖，并在信号中记 `policy_override_error`。
4. 生成的计划是本 turn 的阈值权威层，保存到恢复点；运行期和观察者只计量，不再按档位收紧参数。下一 turn 根据更新后的信号重新生成计划。
5. 信号中记下 `policy_cell`、`policy_arm`（`champion` / `challenger`）、`policy_version` 和 `policy_overrides`。效果账本按这些字段把 turn 归到单元格和组。

内部实验请求只有携带完整实验标记（schema、run ID、pair ID）才能使用 `policy_overrides`，每个覆盖值必须落在允许范围内；其余请求直接拒绝。这类请求记为 `policy_arm=experiment`，不参与生产单元格评估。

## 每天的评估

只有 Release 树中的演化源码与 main 一致（`release_rules_current`）时，系统才提出新挑战者；已有挑战者仍按评估规则处理。

系统演化 Lineage（lineage_048353291724）复用原 Harness 演化评审流程，包含“记录与采集”“每日评估与演化评审”“产物保留”三个具名 Function Block。收集、每日评估与质量评审的时间事件源分别按每小时、香港时间每天 07:00 和 07:30 触发；日历计划随机延迟不超过 300 秒。维护 Effect 调用 RunScript，脚本直接复用 `collect_turn_calls` 和 `run_daily`。人工 `scripts/harness_evolution.py` 使用相同算法。关闭相应定时 Event 即停止以后由该计划产生的执行；已接收的脚本继续按 RunScript 生命周期完成。

### 记录开关

“记录与采集”中的 `system:harness-recording:enabled` Event 是新增运行证据的总开关。PauseComponent（`paused: true|false`）把它的有效状态投影为 ExecutionSettings 的 `harness_recording_enabled`；投影的登记、删除后关闭和恢复规则见 decision_25eb93d803a4。手动触发相连的状态 Effect 只读当前投影。关闭记录后，后台效果账本写线程不再提交新的 turn 观察，每小时收集脚本也跳过执行历史扫描与复制。

异步写线程在短提交事务中检查投影，关闭操作等待已获准的写入结束，再阻止之后的写入。重新打开记录会保存新的准入时间，暂停期间及此前尚未写入的排队观察不会补写；重复恢复同一开启状态不会改变这个时间。采集任务在扫描前检查一次，并在写入前再次检查，扫描期间关闭开关也不会继续写入。

记录开关不撤销当前 Harness 插件计划、治理检查或执行恢复点。已有证据保留，对已有样本的显式质量评审仍可写回；每日评估与评审有各自的 Event，需要一并停止时分别关闭。暂停记录可能减少日评估可用样本，原有最小样本要求继续生效。

### 日常评估的执行与证据

RunScript 在主机上以 dirty-base 用户执行 Release 树内的 `scripts/harness_lineage.py`，算法从当前 Release 树加载，数据库地址从主机配置读取。自动每日评估始终关闭 `auto_promote`；演化源码与 main 不一致或无法确认版本时，只评估已有挑战者，不提出新挑战者。

收集和每日评估各有独立的 RunScript 排他键，防止同类工作重叠。两个脚本还共享维护文件锁，按取得锁的顺序执行；收集进行中到达的每日评估会等待，不会被当成重复收集丢弃。Effect 的幂等回执和 RunScript 的运行记录共同防止同一激活重复启动。

运行状态、退出码、输出和失败证据由 ScriptRun 保存，使用 `ListScriptRuns`、`GetScriptRun` 和 `ReadScriptRunOutput` 查询；这些查询都传入 `lineage_id=lineage_048353291724`，详情与日志查询另传 `script_run_id`。Effect 完成表示脚本已启动，`script.finished` 的成功事件表示工作实际完成。脚本由独立 systemd 单元运行；进程丢失或运行失败时保留终态，不自动重放可能已部分生效的每日评估。失败日志记录异常类型及文件、行号、函数，不输出可能含凭据的原始异常消息。

日报位于 `${share_root}/Agents/lineage_048353291724/scripts/harness-evolution/state/latest-report.md`，状态摘要为同目录 `status.json`。评审 Resident 同时核对每日 ScriptRun 的终态、日报生成日期和单元格事件。报告保留最近成功写出的内容，文件存在不能单独证明当天任务完成。

**参与比较的 turn**：

- 只计用户和 Resident 发起的 turn，不计测试流量（`actor_ref=TFI portal`）。
- 只计真正的 turn（信号 `operation=turn`）。会话的创建、分叉、释放和压缩不参与自动策略及其状态更新。
- 只计已经结束的执行。服务重启会给仍在运行的执行留下一条失败的尝试记录；这条记录要等执行结束才有意义。
- 与策略无关的失败不计入任何一组，包括取消、额度、凭据、限流、Provider 过载、Worker 丢失等。
  - 观察记录为失败、但执行最终被取消的，按取消算。
  - 观察记录为失败、但执行后来由新的尝试完成的（重启后接续），按中断算，也不计入。
- **策略失败**只包括 `execution_failure`、`timeout`、`provider_bad_request`、`context_overflow`（提示超过上下文上限）。
- 失败分类（`classify_failure`）按异常类型和报错文字区分：空的 `CancelledError`、“interrupted”“aborted”算取消，“overloaded”等算 Provider 不可用。

**受影响的 turn**：多数参数只作用于少数 turn，例如压力阈值只在会话接近它时才起作用。在全部 turn 上平均，这样的改动会淹没在噪声里。因此先按 turn 开始时的信号和计划，挑出两组参数会表现不同的 turn：

- 压缩参数：计划中有提前压缩。压缩改变之后每个 turn 带的历史，所以越过阈值以后的 turn 都算。
- 卸载阈值：计划中有卸载，且上一 turn 的输出量达到较小的那个阈值。
- 逐 turn 判断的阈值只在两个值之间起作用；高于两者或低于两者的 turn，两组的处理相同：
  - 保护历史的压力阈值：压力在两个值之间；
  - 降推理强度的压力阈值：压力在两个值之间，且配置的推理强度为 high 及以上；
  - 工具负载阈值：上一 turn 的工具调用数在两个值之间。

**比较与决定**（按顺序判断，第一条成立即为结论）：

| 结论 | 条件 | 执行 |
| --- | --- | --- |
| 回滚 | 挑战者的策略失败率比冠军高 5 个百分点以上，且显著（单侧 z ≥ 1.645）。受影响的 turn 与全部 turn 各看一次，至少 15 个 turn | 立即结束挑战者 |
| 拒绝（质量） | 两组各有至少 20 个评审标签，挑战者的评审通过率低 10 个百分点以上，且显著 | 结束挑战者 |
| 拒绝（成本） | 离线重放显示输入 token 至少多 5%，且 90% 区间整体低于 0 | 结束挑战者 |
| 拒绝（省得太少） | 离线重放的 90% 区间上界低于 5%：这一步不可能达到建议采用的门槛 | 结束挑战者 |
| 建议采用 | 离线重放：输入 token 至少节省 5%，90% 区间下界大于 0，且不增加压缩等待。线上（非劣效）：运行满 7 天；挑战者受影响的 turn 至少 30 个，策略失败率比冠军高出的 90% 上界低于 5 个百分点；两组各有至少 20 个评审标签，评审通过率比冠军低出的 90% 上界低于 10 个百分点 | 状态改为 `promotion_recommended`，等人确认 |
| 取舍 | 满足“建议采用”的其他条件，但重放显示压缩次数增加、每天要多等压缩 | 记录本次评估，挑战者继续运行，由人决定采用还是撤下 |
| 无结论 | 运行满 21 天仍不满足以上任一条 | 结束挑战者 |
| 继续 | 其余情况 | 记录本次评估，日报写明还缺什么 |

- 显著性用两比例 z 检验；非劣效的上界用 Agresti–Caffo 区间（每组各加一次成功、一次失败），一组还没有失败时也不会过于乐观。
- 评审标签很少，所以通过率在全部带标签的 turn 上比较，不只看受影响的 turn。
- 不能离线重放的参数（见下节）没有成本证据，挑战者只能被停下，不会被建议采用。
- `auto_promote` 默认关闭；打开后，“建议采用”会直接采用。

**开新挑战者**：

- 候选单元格：最近 7 天日均至少 10 个 turn（不计测试），没有挑战者，没有冻结，上一个挑战者结束已满 2 天。
- 每天最多开 2 个，按流量从高到低。
- 候选改动只有能离线重放的参数（压缩阈值、压缩上限），冠军的每个参数走一步；30 天内试过的参数集不再试。
- 改动的作用面要够大：按 10% 份额，21 天内预计能攒够 30 个受影响的挑战者 turn（日均 turn × 作用面 × 10% × 21 ≥ 30），并且至少作用于 10% 的 turn。
- 离线重放要显示节省至少 5%，且 90% 区间下界大于 0；满足的改动里选节省最多的。没有满足的，这个单元格不开挑战者。

**日报**写在 `state/latest-report.md`，状态写在 `state/status.json`。

策略状态按时间清理过期记录，进程首次写入也会执行清理。推理质量信号只在当前 Provider 与配置支持自适应推理强度时读取。

## 离线重放

`harness_evolution_offline.py` 按会话重放记录下来的模型调用，估算一步压缩参数改动对输入 token 的影响。

**数据**：执行服务只在执行记录保留期间（约 7 天）保存每次调用的用量，所以每小时由同一 Lineage 的间隔事件源激活 Effect，通过 RunScript 执行收集脚本，把已结束 turn 的调用复制到 `harness_evolution.harness_turn_calls`，按 Harness 保留天数保留（默认 30 天，见“产物清理的触发与扫描范围”）。每个 turn 一行，内容包括：

- 每次调用的时间、输入 token、缓存命中的 token；
- 这个 turn 配置的提前压缩上限，以及它的来源（窗口、阈值、上限）；
- 这个 turn 记录的压缩时间（AgentData 的 compaction 段）。

**重放**：

- 按（Agent，Provider）把 turn 连成会话，按时间排序。
- 相邻两次调用之间上下文从 5 万 token 以上降到 60% 以下：会话在这中间记录了压缩的，算压缩；否则算上下文被重置（新 Provider 会话），重放从实际大小重新开始。
- 其余调用按实际的增量累加上下文；真实的压缩由重放自己决定在哪里发生。
- 各 Provider 的压缩方式：
  - Codex：每个 turn 按自己配置的上限压缩，上限 = 有效窗口 × 阈值%，再受压缩上限约束；没有配置的 turn 用 Codex 自己的上限（由数据校准）。
  - Claude Code：只看压缩上限，忽略阈值；会话一直保留它收到过的最低上限（`claude_code_daemon`）。在数据开始前就压缩过的会话，按它压缩时的大小推断已有的上限；从未设过上限的会话在约 80 万 token 时压缩。
  - 其他 Provider 不重放。
- 按数据校准：触发点是上限的多少（取每次压缩前两次调用的中点，Codex 约 94%，Claude 约 91%），压缩后剩多少（会话自己的中位数，没有时用 Provider 的中位数：Codex 约 8.9 万，Claude 约 3.1 万）。
- 未缓存 token 按调用新增的内容计，压缩或重置后的第一次调用整段未缓存。
- 压缩等待：压缩前后两次调用的间隔减去 turn 内正常的调用间隔，取中位数。估算给出这一步每天多等（或少等）多少秒压缩。

更低的上限省 token，但压缩更频繁，用户等得更久。Claude Code 的 40 万上限正是按这个取舍定的（decision_5c62deb8253a：每次压缩 2–4 分钟，20 万时总用时几乎翻倍）。所以重放显示省 token 但多等压缩的改动，即使通过线上护栏，也只记为“取舍”，由人决定。

**估算**：对单元格里配置过提前压缩的 turn 所在的会话，分别按冠军和挑战者的参数重放；其他单元格的 turn 保持自己的上限。节省 = 1 − 挑战者合计 / 冠军合计，90% 区间按会话重抽样得到。至少要 5 个会话。

**不能重放的参数**：卸载阈值（开始时按压力与保持标记一次缩放，模型行为及本轮输出不能从已有记录重放）、保护历史与降推理强度的压力阈值、工具负载阈值。它们改变模型的行为，重放算不出来。这些参数不自动探索，人可以手工开挑战者，但没有成本证据，系统不会建议采用。

## 质量标签与评审 Resident

质量拒绝采用 z ≤ −1.282 的边界，并要求满足评估中的样本条件。

隐式信号只能说明失败和成本，回答是否满足请求由评审 Resident 判断。每天 07:30 的定时事件和手动事件进入同一条流程：抽样 Effect → 抽样脚本成功事件 → 评审 Resident → 结果提交事件 → 写回 Effect → 写回成功事件 → 评审 Resident 汇报。

- **抽样脚本保存独立批次。** RunScript 调用 `sample_for_review`，读取前一天用户发起、已完成、未评审的 turn。默认回看 24 小时，每个单元格每组最多 4 条，总共最多 24 条。正在运行挑战者的单元格优先，两组轮流取样，组内优先取受改动影响的 turn；其余单元格按流量轮流取样。失败的 turn 已计入失败率，不再抽样。样本写入 `state/reviews/<抽样 ScriptRun ID>/samples.json`，后续步骤使用同一批次。
- **Resident 判断并保存结果。** Resident 读取请求、最终回答与同一会话的下一条输入，判断 pass、fail 或跳过。评价结果是否满足请求，不评价风格；用户紧接着纠正、重复或抱怨计为 fail，2 秒内排队消息及其他 Resident 的汇报不作为反应。判断写入同批次 `results.json`，随后用 TriggerEvent 提交批次 ID。
- **写回脚本校验整批结果后保存标签。** ScriptRun 先确认结果的批次、execution_id、唯一性、pass/fail、理由与分数合法，才调用 `record_review`。理由为 1–512 字符，分数可省略或为 0–1。标签来源保持 `resident_review`，只写 `harness_quality_labels`；观察查询通过视图立即得到新标签。异步账本写入失败时脚本失败，不记成功回执。遗漏的样本视为跳过。
- **同批次重试复用同一份结果。** 写回使用批次锁及内容摘要；完成后生成 `recorded.json`。原样重试返回已有回执，结果变化则拒绝覆盖。部分写入后失败时可重试原结果，质量账本按 execution_id 更新，不重复增加标签。
- **成功写回后再汇报。** 写回的 `script.finished` 成功事件把已记录、已跳过的数量交给评审 Resident。它检查维护脚本状态及日报，有建议采用、回滚、拒绝、无结论、新挑战者、运行失败或每周一周报时，用 DeliverMessage 向 TFI portal 汇报，并附确认、撤下、冻结命令。脚本失败事件进入同一 Resident 的诊断分支，保留 ScriptRun ID；每日评估的重试需先核对已生效的策略。

评审 Resident 负责质量判断和报告，文件与脚本承接数据读取和标签写回。Resident 不修改策略单元格或代码。调用 RunScript 及调整 Lineage 定义的权限由权限系统按评审 Resident 自己的权限判定（decision_b820faf42218）。

## 人的操作

在 Release 代码树下运行 `scripts/harness_evolution.py`：

| 命令 | 作用 |
| --- | --- |
| `status` | 查看单元格和最近的事件 |
| `tunables` | 查看可调参数及安全范围 |
| `traffic` | 查看各单元格日均 turn 数 |
| `daily --dry-run` | 预览当天的评估，不生效 |
| `estimate <provider> <family> --set 参数=值` | 在记录的调用上重放一步改动，看能省多少 |
| `collect` | 立即收集最近 turn 的调用记录 |
| `promote` | 确认采用挑战者 |
| `withdraw` | 撤下挑战者 |
| `freeze` / `unfreeze` | 冻结或解冻单元格 |
| `start --set 参数=值 [--share]` | 手工开挑战者 |

`--actor` 可选，用于记录操作者。

## 存储

PostgreSQL 的 `harness_evolution` schema 保存：

- `harness_policy_cells`：每个单元格一行，存版本、状态、冠军、挑战者、份额、开始时间和最近一次评估。
- `harness_policy_events`：只追加的事件表，记录每次变化的种类、版本、冠军、相关的挑战者、证据和操作者。
- `harness_turn_calls`：Harness 保留天数内（默认 30 天）每个 turn 的模型调用，供离线重放。
- `maintenance_runs`：旧维护入口已完成的运行报告，按原任务 ID 保存操作、调用者、时间、状态与结果；由通用状态表显式迁入，不承担当前调度。

每次修改先取这个单元格的 advisory 锁，在同一事务里更新这一行并追加一条事件，然后让本进程的副本失效；其他进程在 60 秒内看到变化。

效果账本 `harness_effectiveness` 保存观察（`harness_strategy_observations`）和质量标签（`harness_quality_labels`）；观察在 Harness 保留天数内保留，由系统演化 Lineage 的产物保留流程分批清理。质量结果、来源、分数与理由只保存在标签表，`harness_observations` 视图按 execution_id 连接两表，标签指定的负载分类优先；评估、离线重放与抽样使用此视图。迁移已有观察标签时只补缺失标签，已有独立标签不被旧副本覆盖。

删除策略参数前先运行幂等配置迁移，清理冠军、挑战者及待执行请求和恢复计划里的覆盖值。清理后与冠军相同的挑战者结束，单元格版本增加并追加迁移事件；冻结状态保留。迁移不修改历史评估或已结束执行的证据，也不更改数据库 schema。

## 已知局限

- 离线重放只覆盖压缩阈值和压缩上限。其他参数没有成本证据，不自动探索。
- 重放是模型：Codex 的重放比实际多算约 20% 的压缩次数，Claude 只有十几个会话的数据。两组参数用同一个模型重放，误差大部分抵消，但区间不包含模型误差。
- 重放只回答 token；压缩更早会不会丢掉后面需要的上下文，只能靠线上的失败率和评审质量判断。
- 线上分组按会话：Resident 的会话很长、数量少（一个单元格每天几十个），10% 份额往往只落到两三个会话上。线上护栏的证据因此集中在少数会话，统计上把 turn 当独立样本，偏乐观。
- Claude 会话一直保留收到过的最低压缩上限，所以挑战者会话在其他单元格的 turn 也按挑战者的上限压缩，那些单元格的冠军组会混入少量挑战者的效果。
- 卸载阈值的受影响判断用上一 turn 的输出量近似本 turn，有误判。
- 同一会话一直在同一组，所以 turn 开始时的压力等信号也受这一组参数影响。受影响的 turn 因此不是严格的随机对照，失败护栏同时看全部 turn 来兜底。
- 用户流量小的单元格（日均不到 10 个 turn）不会开挑战者。
- 2026-10-06 以前的失败观察用旧分类写入，过载等失败仍记为 `execution_failure`；按执行的最终状态只能纠正取消和重启接续两类。

## 产物清理的触发与扫描范围

Harness 产物与策略证据清理由系统演化 Lineage（lineage_048353291724）的“产物保留”Function Block 配置。“手动清理 Harness 产物” Event 和可选定时 Event 共用一个清理 Effect，通过 RunScript 执行现有保留算法。定时 Event 为香港时间每天 04:40，随机延迟不超过 600 秒，安装时关闭；开启后才自动执行。手动与定时 Event 的开关独立，关闭清理 Effect 则两个入口都不能执行清理。ExecutionService 启动和周期维护均不执行这项清理。

保留期限是管理员在 Portal Management“执行设置”中设置的 Harness 保留天数（`harness_retention_days`，21–365 天，默认 30，decision_25eb93d803a4）。下限 21 天覆盖挑战者最长运行时间，也覆盖离线重放读取的 14 天。一次清理删除三类到期数据：策略观察、`harness_turn_calls` 调用记录和未引用的产物。每小时收集也按同一天数删除旧调用记录，但 Harness 记录关闭时收集直接跳过，到期调用记录只由清理删除。产物部分先检查磁盘上是否存在超过保留期限的产物；没有到期产物时不扫描数据库 Payload，也不获取引用锁。有到期产物时仍先扫描数据库引用，再按原有规则删除未引用的产物。
