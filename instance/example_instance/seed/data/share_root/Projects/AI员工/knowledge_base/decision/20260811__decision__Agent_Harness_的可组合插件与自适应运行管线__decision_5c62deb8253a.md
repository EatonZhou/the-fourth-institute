---
kb_id: decision_5c62deb8253a
type: decision
project: AI员工
created_at: '2026-08-11'
version:
  number: 32
  created_at: '2026-08-11'
  created_by:
    user: ''
    agent: ''
  updated_at: '2026-10-19T01:48:57Z'
  updated_by:
    user: ''
    agent: resident_60ae6d0cdf74
importance: 9
metaness: 3
ai_generated: true
links:
  source:
  - ${code_root}/management_portal/execution/harness_settings.py
  - ${code_root}/management_portal/execution/harness_effectiveness.py
  - ${code_root}/management_portal/execution/harness_early_compaction.py
  - ${code_root}/management_portal/execution/harness_jev.py
  - ${code_root}/management_portal/execution/harness_isa_tool.py
  - ${code_root}/management_portal/execution/harness.py
  - ${code_root}/management_portal/execution/harness_auto_policy.py
  - ${code_root}/management_portal/execution/harness_native_loop.py
  - ${code_root}/management_portal/execution/harness_tool_output_offloading.py
  - ${code_root}/tests/cases/test_agent_harness.py
  - ${code_root}/tests/cases/test_agent_harness_benchmark.py
  - ${code_root}/management_portal/execution/codex_recovery.py
  - ${code_root}/tests/cases/test_codex_recovery_budget.py
  - ${code_root}/management_portal/execution/harness_capabilities.py
  - ${code_root}/management_portal/execution/harness_policy_table.py
  - ${code_root}/management_portal/execution/provider_catalog.py
  - ${code_root}/management_portal/execution/native_tool_events.py
  - ${code_root}/management_portal/execution/backends.py
  - ${code_root}/management_portal/execution/kimi_tool_gateway.py
  - ${code_root}/management_portal/kimi_portal_tools.py
  - ${code_root}/management_portal/claude_tool_bridge.py
  - ${code_root}/management_portal/dsh_client.py
  - ${code_root}/tests/cases/test_harness_capabilities.py
  - ${code_root}/tests/cases/capability_contracts.py
  - ${code_root}/management_portal/execution/provider_egress.py
  - ${code_root}/tests/cases/test_provider_error_continuation.py
  - ${code_root}/tests/cases/test_kimi_portal_tools.py
  - ${code_root}/tests/cases/test_portal_tool_dedup.py
  - ${code_root}/management_portal/execution/turn_input_pump.py
  - ${code_root}/management_portal/claude_code_daemon.py
  - ${code_root}/management_portal/kimi_code_client.py
  - ${code_root}/tests/cases/test_turn_input_during_turn.py
  - ${code_root}/tests/cases/test_claude_turn_steer.py
  related:
  - decision_6c2f91a7d84e
  - decision_91eb3626d323
  - decision_25eb93d803a4
  - decision_d19b9909a674
  - decision_898b8911a2ff
  - decision_425e428410e0
  - decision_b1ab45c4b3ec
  - decision_3e8c2a7f91d4
  - decision_6ad0384e020d
  - decision_fd760e898f9a
  supersedes: []
  derived_from: []
---
# Agent Harness 的可组合插件与自适应运行管线

Agent Harness 是包在一次模型生成外面的一组可选插件。Portal 把一条消息交给模型处理时，统一执行层先建立一次执行，再由选中的模型运行系统（Provider）完成生成；Harness 在生成前后做一些增强工作：配置原生上下文压缩、把过大的工具结果存到别处、提醒模型别重复读同一个文件、在 Provider 出错时安排一次续跑，并给模型提供调用 Portal 能力的工具。目的是让长会话和工具密集的任务更省上下文、更容易恢复；任何一个插件出错，都尽量不影响模型给出回答。

**一个插件在某个 Provider 上能不能用，只看这个 Provider 的 Adapter 声明了哪些能力，不看它叫什么名字。** 五种 Provider（Codex、Claude Code、Kimi Code、DeepSeek Harness、OpenCode）都使用同一套自动策略；能力满足的插件按同样的信号启用，能力不满足的不启用，并在计划里记下缺的是哪项能力。

本文说明 Harness 怎样配置、有哪些插件、每个插件需要什么能力、自动策略怎样选择插件、插件按什么顺序运行、出错时怎样处理，以及各插件的预算。Provider 自己的会话与事件协议见 decision_425e428410e0；执行的排队、取消、终态和恢复点见 decision_6ad0384e020d 与 decision_25eb93d803a4。

## 先认识几个概念

- **Provider** 是实际运行模型会话的系统：Codex App Server、Claude Code、Kimi Code、DeepSeek Harness（DSH）或 OpenCode。模型的一次生成称为一个 **turn**。
- **Provider Adapter** 是 Portal 面向某一种 Provider 的翻译层，负责它的会话、turn、工具和中断协议（decision_425e428410e0）。
- **能力（capability）** 是 Adapter 能替 Harness 做到的一件事，例如“把 Portal 执行的工具提供给模型”“报告 Provider 自己的工具调用”。每个 Provider 在 Provider 目录（`AgentProvider`）里声明自己具备哪些能力；每个插件声明自己需要哪些能力。
- **执行编排器（`ExecutionRuntime`）** 拥有一次执行的生命周期：何时调用 Provider、保存恢复点、响应取消、提交终态。
- **Agent Harness** 是包围一次 Provider 调用的插件管线，源码中的协调器是 `AgentHarness`。Harness 不创建 Provider 会话，也不提交会话绑定、规范消息或执行终态。
- **插件（feature）** 是 Harness 中职责单一的一项功能，有唯一的插件 ID。每个插件实现 `HarnessFeature` 接口，运行后返回 `HarnessFeatureOutcome`，说明这次是否采取了动作、原因、指标或失败。
- **调用快照（`BackendInvocation`）** 是执行编排器交给 Adapter 的一份不可变输入，包含请求、会话、提示和恢复信息。插件通过返回新快照来修改它，不改写共享对象。
- **计划** 是本 turn 实际启用哪些插件及各自的配置。
- **Harness 工具** 是由 Portal 执行、以工具形式提供给模型的入口，例如读取统一资源、调用 HATG ISA 指令。只有 Adapter 声明了 `portal_tools` 能力的 Provider 才会挂载 Harness 工具。
- **工具批次（`AgentLoopToolBatch`）** 是由 Portal 执行、准备送回模型的一组工具结果。Provider 内部自己执行的原生工具不属于工具批次。
- **原生工具事件** 是 Provider 在 turn 中自己执行工具时发出的调用和结果。各 Provider 的事件形状不同，Adapter 把它们翻译成一种通用形状再交给 Harness；怎样解释一条 Provider 事件仍由 Adapter 决定。

## 设计点

### 配置怎样生效

- **Resident 的 Harness 设置只有 `mode` 和 `features` 两个字段。** `mode` 为 `auto`（自动策略）或 `manual`（固定计划）；`features` 以插件 ID 为键，保存每个插件的配置。设置通过 `UpdateResidentSettings` 的 `agent_harness` 字段修改（decision_d19b9909a674），其他顶层字段会被拒绝。整个设置缺失时按 `auto` 处理。
- **插件用 `enabled` 开关启用。** 某个插件的配置省略 `enabled` 时，按它是否在默认启用集合中决定。`timing` 指定运行时机：`before_turn`（turn 前）、`after_tool_batch`（Portal 工具批次之后）或 `both`。空插件 ID、非对象配置、未登记的插件 ID 和未知参数都被拒绝。只提供 turn 前钩子的插件固定在调用 Provider 前运行；去重、卸载和工具图片治理固定在 `after_tool_batch`；`early_context_compaction` 不保存 `timing` 设置。
- **执行层收到的配置必须写明 `mode`。** 这份配置还可以带内部字段 `policy_overrides`（策略参数的覆盖值），用户设置中不出现这个字段；只有带完整实验标记的配对实验请求才能使用它，并且每个值都要在可调参数的安全范围内（decision_fd760e898f9a）。出现其他未知字段时拒绝执行。
- **`manual` 模式直接使用用户配置。** 用户没有写到的默认启用插件自动补为启用。
- **`auto` 模式由自动策略决定本 turn 的计划。** 规则见下文“自动策略怎样选择插件”。自动策略出错时，本 turn 按 `manual` 规则运行。
- **两种模式都受能力约束。** 见下一节。

### 插件按能力启用

- **每个 Provider 声明自己的能力。** 声明写在 Provider 目录的 `AgentProvider.harness` 中，另有两项由目录里已有的字段推出（见“能力一览”）。能力的名字说的是能力本身，例如 `portal_tools`、`native_tool_events`，不带 Provider 的名字。
- **每个插件声明自己需要的能力。** 插件类上的 `requires` 是一组备选，每个备选是一组必须同时具备的能力；任一备选满足，插件就可以进入计划。没有声明的插件在所有 Provider 上都可用。有几个独立部分的插件（例如既处理工具图片、又限定输入图片清晰度的媒体插件）把每个部分写成一个备选，运行时各部分再各自检查。
- **协调器统一按能力取舍。** 计划里启用了某个插件、而当前 Adapter 不具备它需要的能力时，这个插件不运行，计划中保留它并写明原因 `requires_<能力名>`，例如 `requires_portal_tools`。原因取插件第一个备选（它的主要工作方式）里缺的那项能力。`manual` 和 `auto` 两种模式都这样处理。
- **原因出现在三个地方。** `agent_harness_prepared` 与 `agent_harness_completed` 事件的插件报告里（`lifecycle` 为 `plan`，`status` 为 `skipped`）；自动策略决定的 `unavailable_features` 中；效果账本这次观察的信号里。两个事件还带上本次 Adapter 的能力列表 `capabilities`。这类原因每个 turn 都一样，活动记录里不重复显示。
- **Harness 的代码里没有任何 Provider 名称判断。** 只有某一个 Provider 具备的能力，也通过能力声明表达。测试会扫描 `harness*.py`，出现 Provider 名称的字符串即失败；唯一的例外是下文的策略参数表，它按 Provider 存放数值。
- **设置页显示的可用插件由能力推出。** `runtime_capabilities` 中的 `harness_features` 等于“需要的能力都已具备的插件”，同时给出 `harness_capabilities`。没有任何 Provider 手写插件清单。Resident Menu Harness 分栏里的“允许开启 sub agent”开关同样只在 `harness_capabilities` 含 `subagents` 时出现。

### 每个 turn 前先恢复工具入口

- **模型在同一会话中见过的 Harness 工具，之后仍然可以调用。** 每个 turn 开始前，Harness 让所有已登记的插件检查一遍需要保留的工具，不论本 turn 的计划有没有启用它们。插件已经关闭时，工具仍留在会话中但不再向模型重发说明；模型调用它时得到“本 turn 已关闭”的错误，不会因为工具突然消失而中断。
- **只给具备 `portal_tools` 的 Adapter 挂载工具。** 挂载工具的唯一入口 `attach_harness_tools` 先检查这项能力，不具备时什么都不挂。
- **大结果卸载后，召回工具一直可用。** 只要这个会话还有已卸载的大结果，读取它们的工具就保留，即使卸载插件之后被关闭。
- **`hatg_instruction` 工具在每个 Resident turn 都提供。** 见下文“Resident 指令入口与外部判定工具”。
- 名称冲突时不挂载。Harness 工具只与已挂载的 Harness 工具比对；同名而契约不同的一组工具不挂载。

### 一次运行的结果与失败

- **只运行本 turn 计划启用、能力满足、并且支持当前操作的插件。** 没有这样的插件时，调用快照原样交给 Provider，不发布 Harness 事件。
- **普通插件出错时只跳过它自己。** 这个插件的状态记为 `failed_open`，后面的插件和 Provider 照常运行。工具批次插件出错时，只放过当前这一批结果。
- **取消与中断立即传出。** `InterruptedError` 和执行控制信号表示真正的取消或中断，Harness 不拦截，在自动策略、插件准备和插件运行的任何阶段都立即交回执行编排器。
- **Harness 事件只用于观察。** 调用 Provider 前发布 `agent_harness_prepared`，记录各插件的状态、原因、耗时和指标；调用结束后发布 `agent_harness_completed`，记录 Provider 耗时、总耗时、工具批次数和节省的上下文。事件发布失败不影响 turn 的结果。
- **一次执行只有一份计划，跨 Worker 也一样。** 自动策略的决定按执行 ID 缓存；调用 Provider 之前，Harness 把本次启用的插件及配置写进这次执行的恢复点（`harness_plan`）。执行被另一个 Worker 接手或在同一个 Worker 上重试时，沿用恢复点里的计划，不再重新决定。
- **恢复一个已经开始的 turn 时，提示与第一次发送的一致。** 这时会改变提示的插件不再运行（原因 `turn_recovery_in_progress`），观察和循环治理照常运行。Provider 已经拿到输入的 turn 只用 `continue` 续上，Provider 从未拿到的输入才第一次发送，恢复点只保存计划，输入是否已送达由执行协议记录。

## 能力一览

下表列出核心能力与原有 Provider 的声明；OpenCode 与后续 Adapter 同样在 Provider 目录声明能力。“推出”表示这项能力由 Provider 目录里已有的字段得到，不另外声明。

| 能力 | 含义 | Codex | Claude Code | Kimi Code | DSH |
| --- | --- | --- | --- | --- | --- |
| `portal_tools` | Adapter 把 Portal 执行的工具提供给模型，并在模型读到结果之前把每个结果交给 Harness | 有 | 有 | 有 | 有 |
| `native_tool_events` | Adapter 把 Provider 自己的工具调用和结果，以通用形状报告给 Harness | 有（运行中） | 有（运行中） | 有（运行中） | 有（运行中） |
| `active_turn_input`（推出） | Provider 在 turn 运行中接收新的输入 | 有 | 有 | 有 | 有 |
| `auto_compact_threshold` | Provider 自己压缩上下文，Adapter 能设置触发压缩的位置 | 有 | 有 | 无 | 有 |
| `error_continuation` | Provider 出错后，Adapter 按 Harness 给的恢复策略在同一会话里续跑 | 有 | 有 | 有 | 有 |
| `native_image_input`（推出） | Adapter 接受本地输入图片；Harness 保持输入图片原文件 | 有 | 有（按账号路线） | 无 | 无 |
| `input_image_detail` | Adapter 能要求 Provider 以限定的清晰度读取输入图片 | 有 | 无 | 无 | 无 |
| `turn_reasoning_effort` | 推理强度可以在同一会话的两个 turn 之间改变，不必重启会话 | 有 | 无 | 无 | 无 |
| `subagents` | Provider 的模型能启动子 agent，Adapter 让 Resident 的“允许开启 sub agent”成为 Provider 的开关：关闭时模型启动不了子 agent | 有（新 Resident 默认关） | 有（默认开） | 有（默认开） | 有（默认开） |
| `subagent_events` | Adapter 用通用形状报告子 agent：`subagent/event`（子 agent 自己的每个条目）、`subagent/progress`（运行中的卡片）和 `subagentRun`（结果卡片） | 有 | 有 | 有 | 有 |
| `subagent_stop` | Adapter 能停止模型启动的一个正在运行的子 agent，父 turn 继续：Execution 输入控制 `{"type": "subagent_stop", "tool_use_id": …}` 交给 Adapter，卡片随后以已停止结束；Chat 只在声明它的 Provider 上显示子 agent 卡片的“停止” | 有（中断子线程当前轮） | 有（CLI stop_task） | 有（取消会话任务） | 有（取消这次调用的子 agent） |

能力按当前路线计算：例如 Claude 订阅路线支持图片，DeepSeek 路线不支持。插件声明所需能力，协调器只在满足声明时运行它。Provider 的输入、工具与压缩协议见 decision_425e428410e0；子 agent 事件及卡片见 decision_b1ab45c4b3ec。

## 实时上下文用量

`context_usage_events` 表示 Adapter 能在主轮运行中报告当前请求的上下文占用及窗口。Codex 使用原生通知；Claude 使用 SDK `get_context_usage.totalTokens/maxTokens`；Kimi 使用 `turn.step.completed.usage`（含缓存）和 `model_catalog.max_context_size`；DSH 使用 `request/context.contextWindow` 与 `assistant/message.usage`（含缓存）；OpenCode 使用 `message.updated.tokens` 与 `model.limit.context`。当前请求样本、轮累计成本和子 agent 用量分别记录；子 agent 的用量不进入主轮压力。循环治理优先读取当前上下文样本，其次当前请求 `last`，不以轮累计成本代替压力。

## 当前插件集合

默认构造入口 `default_agent_harness()` 登记 10 个插件，其中 9 个在默认启用集合中。登记只表示插件存在；本 turn 是否运行，由计划和能力决定。构造时拒绝空 ID、重复 ID，以及默认启用集合中未登记的 ID。

| 插件 ID | 作用 | 默认启用 | 需要的能力 |
| --- | --- | --- | --- |
| `tool_call_granularity` | turn 前提示模型把可以并行的搜索、读取和检查合并成批 | 否 | 无 |
| `unified_resource_recall` | 提供按当前调用者权限读取统一资源的工具 | 是 | `portal_tools` |
| `native_tool_loop_governor` | 观察 Provider 的原生工具事件，发现循环压力时发送有限次数的提醒 | 是 | `native_tool_events`（发送提醒还要 `active_turn_input`，调整推理强度还要 `turn_reasoning_effort`） |
| `provider_error_continue` | Provider 出错时安排一次同会话续跑 | 是 | `error_continuation` |
| `early_context_compaction` | 让 Provider 在上下文到达阈值时使用原生压缩 | 是 | `auto_compact_threshold` |
| `artifact_search_and_slice` | 在已卸载的结果中搜索，只取回指定片段 | 是 | `portal_tools` |
| `jev_decision_tool` | 提供 `jev_decide` 工具，调用 JEV 做结构化判定 | 是 | `portal_tools` |

插件按上表顺序运行。某项插件不可用，表示当前 Adapter 缺少它声明的能力；可用插件是否采取动作，还取决于本轮输入和已有历史。

插件 ID、顺序、默认开关、参数字段和是否改写模型输入集中登记在 `harness.py` 的注册表。构造器、自动策略、能力目录、设置校验及界面均使用这一来源。


## 自动策略怎样选择插件

自动策略的接口是 `AdaptiveHarnessPolicy`，当前实现是 `AdaptiveHarnessController`。它读取本 turn 的信号，给出计划，并在 turn 结束后记录效果。**所有 Provider 走同一条选择路径**：先按信号选出想用的插件，再去掉当前 Adapter 做不到的，并记下原因。

**策略使用的信号**：上下文压力、上一 turn 的工具调用数和输出量、是否刚开一个新会话、提示长度（约 24,000 token 以上算长提示）、提示中是否出现统一资源地址、工作目录是否是代码仓库且任务像是改代码、本 turn 的输入图片、本 turn 会不会有 Portal 工具，以及前几个 turn 留下的“保持”标记（例如大输出、重复读取、图片或探索碎片化还需要继续关注几个 turn）。

- **上下文压力是“已用上下文 ÷ 上下文预算”。** 预算通常就是模型的上下文窗口；策略参数给压缩位置设了 token 上限的 Provider，预算是“上限 ÷ 压缩比例”（见“各 Provider 的策略参数”）。Adapter 按 `context_usage_events` 能力上报实时上下文样本；轮结束样本供下一轮计划使用。
- **工具调用数、输出量、重复读取和碎片化来自循环治理插件的账本。** 所以各 Provider 都要有 `native_tool_events`，这些信号才不是空的。
- **“本 turn 会有 Portal 工具”按能力和 turn 的性质判断。** 计划是在工具挂载之前做的；Adapter 具备 `portal_tools`、并且这是一个 Resident turn（总会挂载 `hatg_instruction`）或会话已经见过 Harness 工具时，就算有。

**选择规则**（所有单元格相同；单元格只改变下文“各 Provider 的策略参数”中的取值）：

| 插件 | 何时选用 |
| --- | --- |
| `native_tool_loop_governor` | 总是选用；工具负载越高，检查越频繁、阈值越低 |
| `provider_error_continue` | 用户没有关闭时选用，续跑次数固定为 1 |
| `early_context_compaction` | 压力达到 52% 或有保持标记，或提示很长时选用 |
| `unified_resource_recall` | 提示中出现统一资源地址时选用 |
| `tool_output_offloading` | 本 turn 会有 Portal 工具、会话已有卸载结果，或压力达到 52% 时选用；压力越高，卸载阈值越低（64、48、32 KiB） |
| `hash_aware_read_dedup` | 本 turn 会有 Portal 工具、需要保护历史或探索碎片化时选用 |
| `artifact_search_and_slice` | 会话已有卸载结果时选用 |
| `inline_media_history` | 本 turn 有图片、图片保持标记或会有 Portal 工具时选用；输入图片保持无损 |
| `tool_call_granularity` | 仓库任务、碎片化读取或上一 turn 工具调用达到 32 次，并且合并读取的建议不在运行中提醒时选用：Provider 不能在 turn 运行中接收提醒（缺 `active_turn_input` 或 `native_tool_events`），或者策略参数 `granularity_reminders_during_turn` 为关。其余情况由循环治理插件按实际情况在运行中提醒，不用这段常驻提示 |

选出的插件再过一遍能力：Adapter 不具备所需能力的，从计划中去掉，记下 `requires_<能力名>`。

**推理强度只在能逐 turn 调整的 Provider 上调整。** 具备 `turn_reasoning_effort` 时，自动策略按压力调整本 turn 的推理强度：压力低于 52% 时保持用户的设置；更高时，高强度设置（high 及以上）降为 medium，改代码、长提示或引用资源的任务在压力低于 82% 时保持不变，达到 82% 时只从 xhigh 以上降到 high。质量保护信号出现时不降低。不具备这项能力的 Provider 改变推理强度要重启会话，策略不动它。

**外部能力插件由用户配置决定。** 自动策略对内置插件按信号给出计划；`jev_decision_tool` 等外部插件在配置中 `enabled: true` 时保持启用，原因记为 `external_feature_explicit_contract`，同样受能力约束。

自动计划中的循环治理与错误续跑受能力和用户开关约束；不额外维护空计划旁路。效果观察失败不影响 Provider 的结果。

### 各 Provider 的策略参数

插件能不能用只看能力；用起来的参数（何时启用、阈值、预算）可以因 Provider 而异，但必须有数据依据。这些参数集中放在 `harness_policy_table.py` 的一张表里：所有 Provider 从同一组默认值出发，只有测量结果表明需要时，才给某个 Provider 单独的取值，并把依据写在取值旁边。策略代码只读这张表，不判断 Provider 名称。

| 参数 | 默认值 | 含义 |
| --- | --- | --- |
| `protect_history_pressure_percent` | 52 | 压力达到这个比例时开始保护历史：选用提前压缩，收紧卸载和去重阈值 |
| `compaction_threshold_percent` | 70 | 让 Provider 在上下文达到窗口的这个比例时压缩 |
| `compaction_max_context_tokens` | 0（不设） | 压缩位置的 token 上限；设了以后，上下文预算是“上限 ÷ 压缩比例” |
| `high_tool_load_calls` | 32 | 上一 turn 的工具调用数达到多少算工具负载高 |
| `granularity_reminders_during_turn` | 开 | 能在运行中提醒的 Provider，合并读取的建议是否由循环治理在发现碎片化读取时提醒（关时用常驻提示）。只管合并读取；失败循环、上下文压力、Provider 无进展等提醒照常在运行中发出 |

**参数按日常使用演化。** 每个 Provider 与负载族的组合是一个单元格，单元格可以在一小部分会话上试用改了一个参数的版本，每天比较后回滚、拒绝或建议采用，采用由人确认；见 decision_fd760e898f9a。卸载阈值（`offload_trigger_bytes`，64 KiB）、降低推理强度的压力（`reasoning_downgrade_pressure_percent`，52%）也是这张表里的参数。下面的取值是各 Provider 的起点，单元格的冠军在它之上覆盖。

当前单独取值的有两项。`compaction_max_context_tokens`：Claude Code 40 万、DSH 20 万。`granularity_reminders_during_turn`：Claude Code、DSH、Kimi Code 为关，Codex 为开。参数依据保留在策略参数表注释中。

### 阈值的单一权威层

自动策略 `select()` 生成本 turn 的最终阈值；轮内 `feature_config` 只传递计划，原生循环治理直接检查计划，观察者只记录节省量。当前轮输出或压力增长不会改变已保存计划；这些信号供下一轮选择使用。

- 工具调用检查点：上一轮调用达到高负载阈值或出现碎片化时为 55 次，其余 80 次；每轮最多 5 次提醒。
- 重复读取提醒：有重复保持标记为 4 次，有碎片化为 6 次，其余 8 次。
- 单次大输出提醒：输出保持标记或开始压力至少 60% 时为 32 KiB，其余 64 KiB。
- 累计大输出提醒：开始压力达到保护阈值或有输出、重复、图片保持标记时为 96 KiB，其余 256 KiB。未治理图片累计提醒：有图片保持标记为 256 KiB，其余 512 KiB。
- 上下文压力提醒固定为计划中的 70%；无进展默认 600 秒，连续失败为 3 次。合并读取提示仍按实际碎片化证据触发，受计划内次数限制。
- offload 阈值在开始压力至少 65% 或有输出保持标记时取策略值一半，压力至少 45% 时取四分之三，其余取策略值；策略默认 64 KiB。去重最小输出在重复保持标记或开始压力至少 65% 时为 128 token，其余 256 token。工具批次图片采用计划预算。

迁移在严格配置校验启用前执行：清理 Resident 设置、活动轮准备与进度、未结束 Execution 的请求及恢复计划、待发送输入控制和策略参数。已结束执行的证据不变。迁移脚本默认预览、`--apply` 事务锁行更新，可重复执行；无需修改 AgentData schema。

平滑换代中的旧进程可能再次写出旧恢复计划，切换后必须核对非终态快照并完成收尾迁移。新设置继续严格拒绝未登记插件与未知参数；因配置版本不一致中断的任务，在配置与运行版本一致后通过原消息重新入队恢复。

## 组件怎样协作

| 组件 | 职责 |
| --- | --- |
| `AgentHarness` 协调器 | 规范配置，按能力取舍，恢复工具入口，按顺序运行插件，保存计划，汇总报告 |
| 自动策略 | 根据本 turn 的信号和 Adapter 的能力给出计划，turn 结束后记录效果 |
| 策略参数表 | 存放各 Provider 的策略参数和依据 |
| 插件 | 完成一项局部修改或观察，不提交业务终态 |
| 工具批次处理 | 在 Portal 工具结果送回模型之前，按顺序经过各插件 |
| 原生事件处理（`AgentLoopProviderEventProcessor`） | 观察 Provider 的原生工具事件，形成 turn 报告或有限次数的运行提醒 |
| 原生工具事件翻译（`native_tool_events`） | 把各 Provider 的工具事件翻译成通用形状 |
| Harness 工具（`HarnessTool`） | 由 Portal 执行的工具入口，挂载前检查能力和名称冲突 |

一次 turn 的顺序：

```text
执行编排器准备调用快照
        ↓
决定本 turn 计划（恢复时沿用恢复点里的计划；否则自动策略或 manual 配置），按能力取舍
        ↓
所有已登记插件恢复需要保留的工具入口
        ↓
turn 前插件按顺序运行，配置 Provider 原生压缩与固定循环治理阈值
        ↓
把首次尝试的计划写进恢复点
        ↓
调用快照交给 Provider Adapter
        ↓
每批 Portal 工具结果按需经过 after_tool_batch 插件；原生工具事件交给循环治理
        ↓
Provider 结束，发布报告，异步记录策略效果
```

新增插件时实现行为，在注册表登记元数据，并声明所需能力。新增一个 Provider 只需要在 Provider 目录里声明它的能力。Provider 协议的变化由 Adapter 处理，排队与终态的变化由执行编排器处理。

## 工具批次

- **只有 Harness 工具结果进入工具批次。** 结果来源为 `harness`；Provider 内部执行的原生工具结果只报告事件。Codex 工具分派只接受已挂载的 `HarnessTool`。
- **多个工具批次插件依次处理。** 后一个插件处理前一个的输出，文本、多模态、结构化和失败结果分别统计。
- **每项结果默认最多截取 64 KiB。** 插件可以申请更高的上限，最高 16 MiB。

## 各插件的行为与预算

下面是插件自己的默认值和可配置范围；`auto` 模式会按上文表格调整部分数值。超出范围的配置被收回到范围之内。

- **`tool_call_granularity`**：turn 前附加一段提示，建议模型把相互独立的搜索、读取和检查放在一起执行。
- **`unified_resource_recall`**：读取统一资源时按当前调用者的权限经 ResourceService 检查；只能读取本 turn 提示里明确写出的资源地址。
- **`tool_output_offloading`**：会话有稳定的附件范围、召回工具可用时，把大文本结果存为附件。触发阈值默认 64 KiB（4 KiB–16 MiB），预览默认 2,000 字符（256–16,000），每 turn 默认最多 8 项（最多 100）。名称冲突、结构未知、范围不稳定或结果本身来自召回时保留原结果；图片不走这条路径。
- **`artifact_search_and_slice`**：只在本会话的附件范围内搜索，只返回有限长度的片段。
- **`hash_aware_read_dedup`**：调用参数和结果内容的哈希都相同、结果达到默认 256 token（32–20,000）时，才替换为“与前一次读取相同”的说明；缺少证据时两次都保留。哪些调用算读取：通用的读取工具名（`read_file`、`cat` 等），以及 Portal 工具自己的声明——Harness 工具可以声明某次调用只读（`read_only`）和比较结果时忽略哪些字段（`stable_result`）。`hatg_instruction` 在所调指令既不改变状态、也不是流式时算读取，比较时忽略每次调用各自的 `instruction_id`；资源与卸载结果的读取工具总是只读，资源读取比较时忽略 `cache_hit`。
- **`inline_media_history`**：工具批次图片预算默认 512 KiB（64 KiB–16 MiB），超出后保留最近 1 张（1–20）。工具返回图片可压缩替换，默认长边 1,536 像素、JPEG 质量 82；Provider 原生工具返回的图片仅观察。所有 Provider 的输入图片保持原文件；具备 `input_image_detail` 的 Adapter 按默认 `high` 限定读取清晰度。
- **`early_context_compaction`**：只处理 `create` 与 `turn` 两种操作。它不自己压缩，只把 Provider 原生压缩的触发位置提前：默认是上下文窗口的 70%，自动策略还可以给出一个 token 上限，两者取较小的一个；用户设置不保存比例；自动计划从策略表读取比例，手动计划使用功能默认比例。各 Adapter 的做法见下一节。Adapter 不具备 `auto_compact_threshold` 时原因记为 `requires_auto_compact_threshold`。

### 各 Adapter 怎样设置压缩位置

- **Codex**：每次调用都可以设置。Adapter 从模型目录读出有效窗口，把原生自动压缩的 token 上限设为“窗口 × 比例”与 token 上限中较小的一个；已有更低的显式上限时保留更低的那个。
- **Claude Code**：在 Claude Code 进程启动时设置（环境变量 `CLAUDE_CODE_AUTO_COMPACT_WINDOW`，单位是 token）。Adapter 只有在一个 turn 结束后才知道模型的窗口，所以只接受 token 上限，只给比例时不设置（原因 `model_context_window_unavailable`）。一个会话保留它得到过的最早的压缩位置：只有要求更早压缩时才重启进程，计划里这个插件时开时关不会让进程反复重启。所以上限调高后，已经按较低位置启动的会话保持原来的位置，直到它的进程因空闲或重启而退出。
- **DSH**：在 DSH 进程启动时设置。压缩位置是窗口的一个比例（DSH 自己的默认值 0.8）；Harness 要求更早压缩时，Adapter 把 token 上限按会话日志里记录的窗口换算成比例，进程以更低的比例重新启动。同样只有要求更早时才重启。新会话还没有日志，先按比例设置。
- **Kimi Code**：没有可设置的压缩位置，Kimi 按自己的规则压缩。

## Resident 指令入口与外部判定工具

工具挂载需要 `portal_tools`。Resident ISA 入口由协调器固定挂载，不是计划中的 feature；JEV 是可配置的外部判定插件。

### `hatg_instruction`：调用 HATG ISA 指令

- **每个 Resident turn 都提供 `hatg_instruction` 工具。** 执行请求的 `metadata.resident_id` 指明当前 Resident 时，这个工具在 turn 前挂载，不受本 turn 计划影响。不属于 Resident 的执行不挂载。
- **调用者由执行层注入。** 工具以当前 Resident 作为调用者，把调用交给指令的拥有服务执行，不经过 HTTP 回环；调用只按这个 Resident 自己的权限判定，不携带发起者的授权（decision_b820faf42218）。
- **参数**：`instruction`（指令名）、`arguments`（指令参数）、可选的 `idempotency_key` 与 `expected_version`，以及工具自己的 `wait_seconds`。工具说明中附有按类别分组的指令目录。
- **改变状态的指令自动带幂等键。** 模型没有提供幂等键时，使用 `tool:` 加本次工具调用 ID，所以执行层重试同一个工具调用时，指令只执行一次。
- **流式指令只返回第一批结果。** 收到第一批新结果，或等待 `wait_seconds`（默认 30 秒，最长 300 秒）仍没有结果时返回，结果中带 `items`、下一次续订用的 `next_cursor` 和流是否已经结束的 `ended`。模型用 `after_cursor` 继续订阅。
- 返回内容是 ISA 的标准返回格式（decision_898b8911a2ff）。

### `jev_decision_tool`：结构化判定

- **`jev_decide` 工具把一份状态和一组问题交给外部 JEV 服务判定。** 问题类型为 `choice`（从选项中选一个）、`score`（按 2–10 级打分）或 `noul`（判断真假，返回为真的概率）；每次最多 128 个问题。JEV 只给判定结果，不生成文字。状态和问题会发送到外部 TypeSafe API。
- **JEV 已在 AI Provider 登记中配置、并且密钥文件存在时才提供。** 否则本插件跳过，原因为 `jev_provider_unavailable`；Adapter 不具备 `portal_tools` 时原因为 `requires_portal_tools`。
- **Codex 新会话发送完整的工具说明，续接的会话只保留工具入口。** 报告中的指标 `jev_tool_added` 表示本 turn 新挂载的数量，`tool_schema_exposed_this_turn` 表示本 turn 是否发送了说明。
- JEV 服务的登记方式见 decision_6c2f91a7d84e。

自动策略只处理 `turn` 操作。会话的创建、分叉、释放与显式压缩不会更新自动策略的轮次状态。实验参数在配置入口校验；缺少实验身份、字段未知或值越界时直接拒绝，不会静默改为手动模式。

## 原生循环治理

`native_tool_loop_governor` 观察 Provider 的原生工具事件，记一本账：工具调用数、输出量、重复读取、单项读取连成串的次数、失败的调用。声明 `native_tool_events` 的 Adapter 把工具事件翻译成通用形状，所以账本跨 Provider 使用同一口径。

- **记账与提醒是两件事。** 记账只需要 `native_tool_events`。发现模型陷入低效的工具循环时，要在 turn 运行中通过 Provider 原生的 steering 发送一段提醒，这还需要 `active_turn_input`。不具备这项能力的 Provider 上，插件照常记账，本该发送的提醒记为“未发送”（报告里的 `runtime_controls_withheld` 和 `runtime_control_reasons_withheld`）；账本在 turn 结束后进入自动策略的信号。是否可提醒由 Provider 能力声明决定。
- **不调用工具的输出也算进展。** “Provider 无进展”按插件最后一次收到事件的时间计算。Codex 的 Adapter 把所有事件交给插件；Claude Code、Kimi Code 和 DSH 的 Adapter 把工具以外的输出（文字、思考）报告为 `provider/activity` 事件。工具开始后到结果返回前不计为无进展。否则一段很长的思考或一个很长的工具会被误判为卡住。
- **读取同一个文件的不同部分不算重复读取。** 通用形状里，一次读取的标识包含文件路径和读取范围。
- **提醒不改变任务的完成条件。** 工具调用次数、上下文压力或历史中的交接文字，都不能要求模型在任务没完成时提前给出最终回答。需要缩减上下文时，在安全的时机做原生压缩并继续同一个任务。用户明确停止、真实的阻塞和执行层的安全终止仍按原来的规则处理。
- **提醒只用 Provider 的 steering 发送，不伪造用户消息。** 模型已经给出最终回答或 turn 已经结束时，未发送的提醒被丢弃。提醒文本放在 `<agent-harness-runtime-checkpoint>` 标签中；只有 Portal 生成的这个标签被识别为提醒，用户文字中出现同名标签不会触发。
- **提醒的种类**：重复读取、大输出、大图片、Provider 长时间没有进展（默认 600 秒）、同一种调用反复失败、上下文压力（默认 82%）和工具调用检查点。同一种提醒每 turn 只发一次。
- **次数与阈值在计划中固定。** 自动策略在 turn 开始时一次决定，手动计划采用规范化配置；轮内 runtime 与观察者只记账，不再收紧阈值。计划被保存到恢复点，恢复执行沿用该计划。
- 恢复中的 turn 仍然可以观察，也继续配置 Provider 的原生自动压缩阈值。恢复检查点保留首次执行的功能计划。
- 同一执行进入后续 Provider turn 时重新开启观察并丢弃上轮未送达的控制。实时压力提醒按 `context_usage_events` 能力生效；没有实时样本时不能用累计成本构造压力。
- 工具批处理报告保留实际生效的原因，后续没有改动的批次不会把它覆盖为“无需处理”。

## 执行内的技术恢复

Provider 错误续跑由插件配置、Adapter 执行。用户可以关闭 `provider_error_continue`；开启时普通错误最多续跑一次，`max_continue_attempts` 固定为 1，发送前等待 3 秒，等待期间取消则停止。Resident 设置不保存 `max_continue_attempts` 或 `early_context_compaction.threshold_percent`；续跑次数由恢复实现固定为 1，压缩比例由计划从策略表决定。

出口不可用、断线、压缩失败等底层恢复遵循 Adapter 的恢复预算与可恢复性判断，详见 decision_425e428410e0。取消优先，预算耗尽或不可恢复时提交真实终态。技术续跑沿用当前执行和 Provider 会话，不新增 Resident 用户消息；SocietyService 不再为同一次失败另提一个 `continue`。

恢复同一个 Provider turn 时，协调器按注册表的 `rewrites_model_input` 跳过 turn 前会改变输入的插件，并报告 `turn_recovery_in_progress`。观察、循环治理、Portal 工具批次处理和原生压缩配置继续运行。

## 状态归属

| 状态 | 拥有者 |
| --- | --- |
| 插件计划、插件报告、卸载的附件、策略观察 | Agent Harness |
| 恢复点里的 `harness_plan`（首次尝试的计划） | Agent Harness 写入，执行编排器保存 |
| 能力声明 | Provider 目录（`AgentProvider`） |
| Kimi Code 的 Portal 工具清单、调用便条和会话套接字（`${staging_root}/portal_locks/kimi-tools/`） | Kimi Adapter 与 `kimi_portal_tools.py` |
| 执行生命周期、取消、恢复点、终态 | 执行编排器（decision_6ad0384e020d） |
| Provider 会话、原生事件及其翻译、steering、中断与原生压缩位置 | Provider Adapter（decision_425e428410e0） |
| 规范执行记录 | AgentData（decision_b1ab45c4b3ec） |
| 稳定的 Provider 会话绑定 | SessionManager（decision_91eb3626d323） |

工具批次治理只覆盖 Portal 执行的工具。要改写 Provider 原生工具的结果，需要对应的 Adapter 先提供明确的协议；只用于展示的事件不能当作可以改写的工具结果。

## 策略效果账本

自动策略把效果写入 PostgreSQL 的 `harness_effectiveness` schema，Dev 与 Release 共用。完整的 `agent_harness_completed` 报告同时保存在执行活动 JSONL 中；效果账本提供可查询的插件结果、成本和质量标签。

- `action_outcomes_json` 按插件及阶段记录 `applied`、`no_action`、`skipped` 或失败，以及有限的数字和布尔指标；`actual_actions_json` 只列实际采取动作的插件。批次中曾应用的动作不会被后续无事可做的原因覆盖。
- 原生早压缩插件设置了压缩位置就算应用；实际压缩由 Provider 执行，见 AgentData 的压缩记录。
- 手动计划记为 `production_variant=manual`、`load_family=manual`，不进入自动策略的单元格与质量信号。没有运行插件的手动轮次没有插件观察。
- token 使用本轮增量。最新上下文和会话累计不能当成本轮消耗；明确为零不回退到累计值，缺失值需标明缺失。记录带计量版本，比较时须采用相同口径。
- 账本按执行 ID 保留最近一次观察。重试同时更新计划、任务类别、信号、状态、成本和动作；它不表示一次执行全部尝试的总成本。终态与排队指标需结合执行记录解释。
- 信号保存能力列表和因缺少能力未运行的插件；自动策略的跨轮状态首次写入及随后按时间清理，保留 30 天。
- 测试使用 `PORTAL_DATABASE_NAMESPACE` 隔离数据库，不能写生产账本。观察表和 artifact 的保留期是管理员设置的 Harness 保留天数（默认 30 天，decision_25eb93d803a4）；artifact 超过保留期且没有任何对话或 operation 引用才清理，被引用的继续保留。put、grant、read、search 刷新访问时间。清理由系统演化 Lineage 的产物保留 Event 触发，分批执行（decision_fd760e898f9a）。

