---
kb_id: decision_425e428410e0
type: decision
project: AI员工
created_at: '2026-09-03'
version:
  number: 45
  created_at: '2026-09-03'
  created_by:
    user: ''
    agent: ''
  updated_at: '2026-10-19T19:59:40Z'
  updated_by:
    user: tfi-exported-user
    agent: resident_865c3c24f251
importance: 9
metaness: 2
ai_generated: false
links:
  source:
  - ${code_root}/management_portal/execution/claude_transcript_compaction.py
  - ${code_root}/management_portal/society_service/resident_execution_provider.py
  - ${code_root}/management_portal/codex_capabilities.py
  - ${code_root}/management_portal/execution/native_options.py
  - ${code_root}/management_portal/execution/provider_catalog.py
  - ${code_root}/management_portal/execution/backends.py
  - ${code_root}/management_portal/execution/runtime.py
  - ${code_root}/management_portal/execution/contracts.py
  - ${code_root}/tests/cases/test_execution_layer.py
  - ${code_root}/tests/cases/test_execution_context.py
  - ${code_root}/management_portal/execution/codex_recovery.py
  - ${code_root}/tests/cases/test_codex_recovery_budget.py
  - ${code_root}/management_portal/opencode_backend.py
  - ${code_root}/management_portal/opencode_bridge.py
  - ${code_root}/tests/cases/test_opencode_adapter.py
  related:
  - decision_25eb93d803a4
  - decision_d40f59f5508f
  - decision_3948ce9b9443
  - decision_91eb3626d323
  - decision_5c62deb8253a
  - decision_6ad0384e020d
  - decision_3e8c2a7f91d4
  - decision_6b9e42f1c8a7
  - decision_6c2f91a7d84e
  - decision_075f66430606
  supersedes: []
  derived_from: []
---
# Provider Adapter 的统一执行、事件与中断契约

Portal 同时接入了几种模型运行系统：Codex、Claude Code、Kimi Code、DeepSeek Harness、OpenCode，以及只做一次性生成的普通模型接口。它们各有自己的会话、事件和中断协议。**Provider Adapter**（适配器）是执行层中面向某一种模型服务供应方（Provider）的翻译层：它把 Portal 中立的执行请求翻译成这个 Provider 的原生调用，再把原生事件和结果翻译回来。执行编排器（ExecutionRuntime）、会话管理器（SessionManager）和持久调度器（Scheduler）因此不需要了解任何一种原生协议。

本文定义 Adapter 收到什么、返回什么、怎样上报事件、怎样处理中断和恢复，以及哪些职责留在 Runtime、SessionManager 和 Scheduler。源码位于 `management_portal/execution/backends.py`，各 Provider 的声明位于 `execution/provider_catalog.py`。

## 先认识几个概念

- **持续会话（Agent Session）**：有持续 Provider 会话的多轮执行，操作包括新建、进行一轮对话、Fork、压缩和释放。
- **一次性生成（Chat Completion）**：调用 LLM API Registry 中登记的普通模型接口，不保留会话（decision_6c2f91a7d84e）。
- **Provider 声明**：`provider_catalog.py` 中每个持续会话 Provider 的一条 `AgentProvider` 记录，写明它支持哪些操作、哪些运行设置、是否接受账号路由、是否支持图片和运行中输入、启动指令怎样交付，以及使用哪个 Adapter、哪个账号适配器和哪个运行选项适配器。
- **调用说明（BackendInvocation）**：Runtime 交给 Adapter 的一次调用描述，不可修改。
- **回调环境（BackendContext）**：Adapter 运行期间使用的一组受控回调，用来上报事件、保存恢复状态和领取运行中输入。
- **调用结果（BackendResult）**：Adapter 成功时的返回。
- **中断目标**：Provider 已经分配、可以用来中断当前请求的原生标识，例如 Codex 的会话和轮次 ID。
- **Agent Harness**：包在一轮 Provider 调用外面的可选增强管线，负责本轮的工具与上下文治理，不拥有 Provider 会话（decision_5c62deb8253a）。

## 当前接入的 Provider

| Provider | Adapter | 支持的操作 | 说明 |
|---|---|---|---|
| `codex` | `CodexBackend` | `create`、`turn`、`fork`、`compact`、`release` | 通过共享 Codex App Server 调用（decision_3e8c2a7f91d4）；支持图片、运行中追加输入、原生评审和 Goal |
| `claude` | `ClaudeBackend` | `turn`、`fork`、`compact`、`release` | 通过 Claude Code daemon 的 Unix socket 调用（decision_3948ce9b9443）；支持图片、运行中追加输入 |
| `kimi` | `KimiBackend` | `create`、`turn`、`fork`、`compact`、`release` | 通过 Kimi Code Server 调用（decision_6b9e42f1c8a7）；不接受账号路由，图片以文件路径引用交付；支持运行中追加输入 |
| `dsh` | `DshBackend` | `turn`、`compact`、`release` | DeepSeek Harness，通过本地 socket 调用（decision_276105b5d67b）；支持运行中追加输入和子 agent；默认并发 8 |
| `opencode` | `OpenCodeBackend` | `turn`、`compact`、`release` | OpenCode（npm `opencode-ai`），见下文“OpenCode 的接入方式”；提供 OpenCode Zen 的免费模型（路由 `zen_free`，执行账号 `opencode_zen_free`），以及 OpenRouter（路由 `openrouter`，执行账号 `opencode_openrouter`）；支持运行中追加输入和子 agent |
| Chat Completion | `ChatCompletionService` | `generate` | 调用登记的模型接口，一次性生成 |

每个 Provider 只有表中这一个名称：ISA 指令的 `provider` 参数、Resident 的后端、执行请求、Provider 会话、用量记录和磁盘上的运行目录都用它；“Claude Code”“Kimi Code”“DeepSeek Harness”只是显示名称。

执行请求中的服务类型、Provider 和操作组合在进入 Adapter 之前就已校验；不支持的组合直接失败，Adapter 不猜测替代动作。

## 组件责任

| 组件 | 负责 |
|---|---|
| ExecutionRuntime | 会话准备、上下文附加策略、取消检查、AgentData 写入、调用 Adapter 和收尾 |
| Provider Adapter | 原生调用、操作映射、事件翻译、中断、Provider 特有的恢复 |
| SessionManager | 会话地址与 Provider 会话、路由的对应关系（decision_91eb3626d323） |
| Scheduler 与 Worker | 领取资格、租约、运行中控制、调用 Runtime、提交终态 |
| Agent Harness | 本轮的工具与上下文组合 |

## 一次调用怎样流动

```text
ExecutionRuntime
  │ 构造 BackendInvocation + BackendContext
  ▼
持续会话服务（AgentSessionService）
  ├─ 按 Provider 声明应用运行选项（模型、权限预设等）
  ├─ Agent Harness 准备本轮工具与上下文
  └─ Provider Adapter
       ├─ 准备上下文种子（Fork 或重启后需要带入的历史）
       ├─ 翻译操作、提示词、会话和路由
       ├─ 登记中断目标
       ├─ 调用 Provider，把原生事件翻译成规范事件
       └─ 返回 BackendResult
  │
  ▼
Runtime 保存“Provider 已完成”恢复点，写 AgentData，更新会话对应关系
  │
  ▼
Worker 向 Scheduler 提交终态
```

Runtime 在调用前后都检查取消。调用期间的取消由 Scheduler 经 Worker 传到 Adapter 的 `interrupt`，Adapter 对已经登记的中断目标执行 Provider 相应的中断动作。

## Adapter 接口

每个持续会话 Adapter 实现 `Backend` 协议：

- `provider_id`：Provider ID。
- `prepare_context_seed(invocation) -> invocation`：在调用前准备需要带入的上下文种子，例如 Fork 时选定的历史消息。
- `execute(invocation, context) -> BackendResult`：完成一次 Provider 操作。
- `interrupt(backend_session_id, turn_id, backend_route_id, runtime_options) -> bool`：尽力中断已经开始的请求，返回是否发出了中断。Worker 失联后，新 Worker 也用它停下旧 Worker 留下的 Provider 工作（decision_8ac0b5a6c0e5）：`runtime_options` 带 `interrupt_active_turn` 和 `abandoned_execution_id`，轮次 ID 可能为空。这时 Codex 中断 thread 正在进行的一轮，DSH 用执行 ID 推出轮次和新会话 ID，Claude Code 按会话键中断，Kimi Code 中止会话。

接口允许同步实现。多项执行之间的并发由 Worker 进程和 Scheduler 容量隔离。Chat Completion 由 `ChatCompletionService` 实现同样的执行与中断入口，走同一个 Runtime 结果边界。

### 调用说明（BackendInvocation）

| 字段 | 含义 |
|---|---|
| `request` | 已经校验的执行请求 |
| `session` | SessionManager 解析出的会话快照 |
| `user_prompt` | 本次用户输入文本 |
| `system_context` | Runtime 已经组装好的系统上下文 |
| `attach_system_context` | 本次是否要把系统上下文发给 Provider |
| `is_new_backend_session` | 本次是否创建新的原生会话 |
| `recovery_checkpoint` | 恢复所需的持久恢复点 |
| `harness_tools` | 本轮的 Harness 工具定义 |
| `available_harness_tool_names` | 这个会话已经确认可用的 Harness 工具名 |
| `tool_batch_processor` | 处理 Portal 自己执行的工具批次的可选回调 |
| `provider_event_processor` | 观察 Provider 原生事件的可选回调 |
| `provider_error_recovery` | Provider 出错后的恢复策略 |

Adapter 不重新选择会话地址，也不修改 Scheduler 的容量键。恢复点用来判断 Provider 这一步是否已经完成，或向特定 Provider 提供恢复所需的状态；公共字段与 Provider 无关，Provider 独有的数据放在 `provider_recovery_state` 中。

### 调用结果（BackendResult）

| 字段 | 含义 |
|---|---|
| `output` | 规范化的模型输出 |
| `backend_session_id` | Provider 返回的原生会话 ID；无会话结果可以为空 |
| `backend_route_id` | 实际使用的路由 |
| `usage` | 本次 token 用量 |
| `available_harness_tool_names` | Provider 或 Harness 确认可以在这个会话中使用的工具名 |

Adapter 失败时抛出异常，由 Runtime 规范化为执行错误。交给 Scheduler 的最终错误码最长 120 字符，任意 Provider 文本不能直接当作协议字段。

### 回调环境（BackendContext）

| 回调 | 用途 |
|---|---|
| `emit` | 上报已经规范化的执行事件 |
| `observe_provider_event`、`observe_invocation`、`record_compaction` | 记录 Provider 事件、调用和压缩的观察，供 AgentData 保存 |
| `is_cancelled` | 查询是否已请求取消 |
| `set_interrupt_target` | 登记原生中断目标 |
| `set_turn_start_reason` | 说明这一轮为什么开始，例如首次、故障恢复、计划交接或上下文压缩后继续 |
| `set_provider_recovery_state` | 保存 Provider 特有的恢复状态 |
| `bind_input` | 记录 Provider 已经接收某次输入的原生身份 |
| `reassign_backend_route` | 新会话故障切换到另一个路由后，请执行层调整容量键 |
| `observe_context_usage` | 上报当前上下文占用 |
| `has_turn_control_provider`、`next_turn_control`、`record_turn_control_result` | 领取 Scheduler 保存的运行中输入或本轮设置更新，并报告投递结果 |

这些回调让 Adapter 能表达 Provider 之间的差异，同时不直接写 Scheduler 的 PostgreSQL 或 AgentData 表。

## 接入新的 Provider

Portal 的通用部分只通过 Provider 声明和它的能力认识一个 Provider，不按名称分支。接入一个新的 agent Provider 需要的全部内容是下表各项；OpenCode 就是只提供这些内容接入的：

| 内容 | 位置 | 说明 |
|---|---|---|
| 声明 | `provider_catalog.AGENT_PROVIDERS` 中的一条 `AgentProvider` | 名称、显示名、操作、运行设置、并发、媒体、运行中输入、启动指令的交付方式、Harness 能力（decision_5c62deb8253a）、新 Resident 是否默认允许子 agent、上游服务（`upstream`）、会话是否固定启动时的 Portal 工具（`session_tools_fixed`）、Portal 系统指令所在的位置（`system_instructions_place`）、凭据与会话存储的位置（`credential_globs`，测试框架的隔离组据此遮蔽）、自己发起的轮次怎样计入用量（`self_started_usage`）、运行时怎样安装更新（`runtime_updater`，decision_e587fe1c472e） |
| Adapter | `adapter` 指向的类 | 走原生 agent socket 的 Provider 继承 `NativeSocketBackend`，只需给出 `provider_id` 和客户端；这一轮的运行、Portal 工具、运行中输入、交接、错误续跑和“允许开启 sub agent”开关都由基类按声明处理。需要更多时覆盖对应钩子（`turn_options`、`compaction_options`、`observe_turn_usage`、`prepare_native_session` 等） |
| 路由目录 | `catalog_module` 的 `routes()` 与 `catalog(route)` | 有了它，通用账号适配器 `ConfiguredAccounts` 就为每条路由生成执行账号；模型实时列出的 Provider 改用自己的账号适配器（`account_adapter`），Society 工作台和 HATG 面板在选中账号时通过通用接口 `/api/agent-workbench/agent-runtime-catalog?account_id=…` 读取它的目录 |
| 运行选项适配器 | `options_adapter`，默认 `NativeOptions` | 有原生权限预设或特殊运行参数时提供 |
| 上游 | `provider_egress.UPSTREAMS` | 登记上游的探测地址；未登记的上游不会让恢复等待（视为可达） |
| 契约测试 | `tests/cases/capability_contracts.py` 的 `ADAPTER_TESTS` | 声明的每项能力都要登记这个 Provider 的 Adapter 测试，否则目录测试失败 |

Adapter 还要遵守三条由通用部分读取的约定：

- **压缩要留下记录。** 声明了 `compact` 操作的 Provider，每次压缩（`/compact` 或 Provider 自己在上下文将满时压缩）都要在 `record_provider_compaction` 中调用 `record_native_compaction`，写明摘要、压缩时间和保留历史的起点。没有记录的压缩执行会被 Lineage 拒绝（“Compact execution produced no canonical Segment”）。`test_catalog_declares_what_the_adapter_class_implements` 检查走原生 socket、声明了 `compact` 的 Adapter 覆盖了这个钩子。
- **文件工具用统一的参数名上报。** 本轮修改的 diff 和工具卡片按固定名称读取工具调用：`edit` 带 `file_path`（或 `path`）、`old_string`、`new_string`、`replace_all`，`write` 带 `file_path`（或 `path`）、`content`、`mode`（`provider_turn_artifacts.py`）。原生名称不同时由 Adapter 改名，例如 OpenCode 的 `filePath`、`oldString`。
- **执行账号名是 `<provider>_<route>`。** 路由名会出现在账号列表里，应当在拼接后仍然好读，例如 OpenCode 的路由 `zen_free` 得到账号 `opencode_zen_free`。

通用部分还提供 Adapter 直接使用的辅助函数，新 Provider 不需要自己再写一遍：自己启动本地 agent 进程的 Adapter 用 `resident_shell_environment.resident_process_environment` 得到进程环境（校验 Resident 身份，按授权加入 Portal 数据库地址），这个环境只交给进程，不写入任何文件；`ProviderRuntime` 子类用 `provider_updates` 的 `run_command`、`npm_version`、`pypi_version` 和 `linked_version`；压缩记录的替换历史用 `native_session_compaction.summary_history`。

声明和 Adapter 的发布方式不同：`provider_catalog.py` 位于共享源码（`management_portal/execution`），Dev 与 Release 的服务池以及 Release Web 都从 Dev 树加载它；Adapter、路由目录和运行时模块位于各实例自己的树中，Release 要等发布后才有。新 Provider 的声明和它的模块应在同一次发布中上线。在此之前，通用账号适配器 `ConfiguredAccounts` 读不到这个 Provider 的路由目录时只让它不提供账号，其余 Provider 的账号照常可用。

`tests/cases/test_provider_extension.py` 在新进程中只声明一个 Portal 不使用的 Provider（`tests/cases/hypothetical_provider.py`，一个 Qwen Code 式 agent）并检查通用部分都由声明接管：执行请求校验、账号与运行目录、Resident 注册与能力投影、Harness 规划、斜杠命令、提示词位置、会话工具、网络检查、运行时更新、ISA 参数、测试隔离和一轮对话。只提供模型接口的服务（例如 Groq、DashScope 上的 Qwen）不需要新的 agent Provider：它们登记在 LLM API Registry 中，作为 Model API Resident（`provider_api`）或一次性生成使用（decision_6c2f91a7d84e）。

### OpenCode 的接入方式

OpenCode 以本地 HTTP 服务（`opencode serve`）提供会话、事件流（SSE）和中断接口。`opencode_bridge.py` 是每个 Provider 会话一个的桥接进程：它启动并独占一个 `opencode serve`，在 Portal 的原生 agent socket 协议（`tfi.native-agent.v1`）上提供一轮对话、运行中输入、中断、压缩、用量读取和释放，并把 OpenCode 的事件翻译成通用的工具与子 agent 事件。Portal 工具通过桥接进程内的 MCP 服务提供给 OpenCode（OpenCode 给它们加上 `portal_` 前缀，桥接进程上报时去掉）。OpenCode 自己的权限询问一律允许，向用户提问一律拒绝，权限由 Portal 的执行请求决定。OpenCode 使用运行时自己的配置目录（`XDG_CONFIG_HOME` 指向运行时下的 `config`），数据和状态目录放在会话目录中，主机用户自己的 OpenCode 规则、agent 和插件不会进入 Portal 会话；会话目录（`${staging_root}/shared/opencode`）对测试框架的隔离组遮蔽。

OpenCode 的 `system` 字段只作用于它所在的那条消息，所以声明为每轮都交付 Portal 系统指令；运行中追加的输入也带上这一轮的模型、系统指令和子 agent 开关，因为 OpenCode 从最新的一条用户消息读取它们。运行时由 `OpenCodeRuntime` 安装到 `~/.local/share/dirtybase-portal/opencode-<版本>`，安装时把当时免费的模型写入 `models.json`；路由目录只提供这些模型。免费模型不需要账号；免费路由也没有计费账号（目录的 `provider_profile` 为空，执行账号没有 `billing_account_id`），所以 Home 账号面板不为它显示余额，调度也不对它启用额度保护。OpenCode Zen 说明免费期间可能使用请求数据改进模型，并且免费模型的上游会限流（HTTP 429 “Endpoint is unavailable”），失败后由通用的错误续跑重试一次。

OpenCode 也能连接其他模型服务；Portal 只接 OpenRouter（`opencode_provider_catalog.SERVICES`）。每个服务一条路由，路由名就是服务名：OpenRouter 的路由是 `openrouter`，执行账号是 `opencode_openrouter`。路由使用 LLM API Registry 中这个服务第一个启用的条目（目前是 `openrouter_primary`）：密钥和登记的模型来自这个条目，用量计入这个条目的余额（账号的 `billing_account_id`）。2026-10-12 之前每个启用条目各成一条路由、以条目 ID 命名，账号名 `opencode_openrouter_primary` 太长；改名时已有 Resident 的路由、Provider 会话和按小时用量一并迁移到新名称。安装运行时会用一个占位密钥让 `opencode models openrouter --verbose` 列出全部模型，只保留能调用工具的模型写入 `models.json` 的 `openrouter` 项；路由目录把条目登记的默认模型和启用模型排在前面，默认模型沿用条目的 `default_model`，其余按名称排序。路由的上游服务是 `openrouter`，出口按这个上游判断。密钥从这个条目的 `api_key_file` 读取，随桥接进程的环境变量 `PORTAL_OPENCODE_API_KEY` 传入；桥接进程把它从 OpenCode 的环境中去掉，在 `opencode serve` 启动后、任何其他请求之前调用 `PUT /auth/openrouter` 交给 OpenCode（OpenCode 只在第一次请求时加载已配置凭证的服务）。OpenCode 把它写进会话目录下的 `data/opencode/auth.json`（权限 0600）；`launch.json` 和 OpenCode 的进程环境都不含密钥，Resident 在 bash 中看不到它。OpenRouter 按 token 计费，Portal 的用量记录只记 token 数。

OpenCode 的子 agent 在子会话中运行（`task` 调用的 `metadata.sessionId`）。桥接进程读取一轮的用量时，沿这一轮 `task` 调用找到子会话（以及子会话再派生的子会话），把其中的请求一并计入这一轮的用量，标为不在这一轮上下文中；子 agent 卡片随子会话每一步更新它累计的 token。运行中卡片的“停止”经桥接 `subagent/stop` abort 这次 `task` 调用的子会话（子会话还没建立时，建立后立即 abort），`task` 调用以中止失败返回，模型继续这一轮；桥接先发 `subagent/stopping`，卡片以已停止结束。2026-10-11 之前只统计主会话，OpenRouter 上子 agent 的 token 没有记账。

## 运行目录与能力描述

ExecutionService 可以为某个执行账号返回运行目录 `tfi.agent-runtime-catalog.v1`：这个账号可用的模型、每个模型的推理强度与默认值、服务等级和输入类型，以及 `tfi.runtime-capabilities.v1` 能力描述。能力描述声明 `settings`（可调的运行设置）、`operations`、`permission_presets`、`permission_labels`、`default_permission_label` 和 `harness_features`。Codex 的模型列表在选中账号时从它的 App Server 读取（`model/list`，隐藏的模型不列出，缓存 60 秒）；其他 Provider 使用本地声明的目录。目录不包含认证文件和账号路径；读取失败时不会用另一个账号的目录代替。

| Provider | 权限预设 | 默认标签 |
|---|---|---|
| Codex | `default`（Ask）、`auto-review`、`full-access` | Full Access |
| Claude Code | `default`（Restricted）、`full-access` | Full Access |
| Kimi Code | `default`（Manual）、`full-access` | Full Access |
| DeepSeek Harness | 无 | 无 |

- **每个 Provider 的运行选项适配器是它能力描述和原生权限映射的唯一来源。** Resident 修改设置时，用同一个适配器校验字段、权限预设和账号可用的模型；不支持的请求在进入原生执行前失败。
- **Chat 页面按能力描述显示设置和操作。** 没有服务等级可选时不显示 Speed。Provider 名称只用于选择 Adapter 和诊断，Resident 和 Chat 不根据它判断功能。新增一个 Provider 的同类能力时，修改这个 Provider 的声明与映射，上层不增加按 Provider 名称的分支。
- **声明了 `full-access` 就表示用户可以选择这个原生运行预设。** Portal 把所选原生权限模式传给 Adapter；Resident 的文件系统范围和资源授权不随预设改变，资源授权由权限系统按 Resident 自己的权限判定（decision_b820faf42218）。

### 默认原生权限模式

Resident 未显式选择权限预设时，使用所选 Provider 支持的最开放原生模式：Codex 使用 Full Access（`danger-full-access`、审批策略 `never`），Claude Code 的原生订阅与兼容 API 路由使用 `bypassPermissions`，Kimi Code 使用 `yolo`。新建时的初始化、已有设置中未指定或清空预设的解析、运行目录中的默认标签和实际原生调用必须遵循同一份 Provider 声明。Portal 的 Claude 客户端未收到原生模式时也从该声明解析默认值，并在发送给独立部署的 daemon 前明确填写请求参数。

预设 ID `default` 表示用户显式选择的 Ask、Restricted 或 Manual；它与空值“恢复系统默认”含义不同。显式预设继续优先，Fork 沿用来源的显式选择。默认原生模式不修改 Portal 的资源授权、身份、工具可用范围或显式封闭目录约束。DSH 未提供可选的原生权限预设；一次性 API 不执行本地工具，两者不制造 Full Access 开关。

### Runtime Settings 的有效默认值

模型与推理强度未显式指定时，由运行选项解析器结合 Portal 默认设置和所选账号的模型能力决定实际参数。Codex 账号适配器在返回公共运行目录前使用同一个解析器，生成有效默认模型、默认推理强度，以及每个模型在未指定推理强度时的实际默认值。通用运行目录接口、模型选择器和执行提交共同使用这份结果；Codex 专用的目录接口已删除（2026-10-11），Society 工作台和 HATG 面板也按所选账号读取通用接口。

Runtime Settings 的默认选项文字、聊天状态栏和保存校验必须展示、接受同一个有效值。Provider 模型目录中的推荐默认值仅参与能力约束下的解析，不能直接替代 Portal 实际使用的默认值。模型不支持 Portal 默认推理强度时，界面显示解析后支持的值；模型不支持推理强度时，不显示其他模型的推理强度。空选项表示恢复默认解析，不构成显式指定一个模型不支持的参数。已有的显式模型和推理强度继续优先，显式指定不支持的值仍报错。

## 提示词与系统上下文

用户提示词、输入图片和系统指令由请求协议校验（decision_6ad0384e020d）。系统指令有两种附加策略：`session_start` 只在新会话或还没附加过时发送，`every_turn` 每一轮都发送。是否附加由 Runtime 决定，Adapter 只按调用说明发送最终文本。

Harness 可以为本轮加入上下文和工具。Adapter 把 Provider 实际可用的 Harness 工具名放进结果，SessionManager 在完成时并入会话记录；工具能力随会话保留。Codex、Claude Code、Kimi Code 和 DeepSeek Harness 都声明了 Portal 工具能力。

### 启动指令怎样交给模型

Resident 的启动指令（初始说明）由 Resident 业务层提供内容，每个 Provider 在声明中写明交付方式：

| 交付方式 | Provider | 含义 |
|---|---|---|
| `user_prompt` | Codex、DeepSeek Harness | 放进用户提示词 |
| `system_instructions` | Claude Code、Kimi Code、独立模型接口 | 作为结构化系统指令交付 |
| `none` | 无启动指令交付的 Provider | 不附加启动指令 |

SocietyService 中的 Resident 执行桥接组件读取这个声明，决定本次是否把启动指令并入提示词；Runtime 按附加策略决定是否发送，Adapter 完成实际交付。Provider 之间的差异因此集中在交付这一处，Resident 业务代码沿用同一套启动流程。

## 事件与运行中输入

Provider 的流式事件先由 Adapter 翻译为 Portal 的规范事件，再经回调环境交给 Worker 和 Scheduler。活动摘要会压缩并脱敏：token 增量和原样的用户消息不作为普通活动文本透出，单段活动文本最多 16,000 字符；模型文本、工具调用和工具结果使用规范事件类型。

这条事件流用于观察进度。需要长期保存的输入、输出、工具和压缩内容，由 AgentData 投影器同时写成规范记录；Adapter 事件不能代替 AgentData。

运行中追加输入和本轮设置更新先由 Scheduler 持久保存。Adapter 通过回调领取，发给 Provider，再报告投递成功或失败。Codex、Claude Code、Kimi Code 和 DSH 的持续会话 `turn`都接通了追加输入（2026-10-03 起）；本轮设置更新只有 Codex 支持，其他 Adapter 报告投递失败。

- **Codex** 用 App Server 的 `turn/steer`（decision_3e8c2a7f91d4）。
- **Claude Code、Kimi Code、DSH** 的一轮是一个阻塞调用。调用期间，`TurnInputPump`（`execution/turn_input_pump.py`）每 0.25 秒依次领取 Scheduler 的运行中输入和 Agent Harness 循环治理的提醒，交给 Adapter 的投递函数：Claude Code 是 daemon 的 `turn/steer`（decision_3948ce9b9443），DSH 是桥接插件的 `turn/steer`（decision_276105b5d67b），Kimi Code 是把输入排成新提示后并入正在运行的提示（decision_6b9e42f1c8a7）。Provider 在当前一步结束后读到这段输入，这一轮的回答包括对它的回应。
- **投递失败时不丢输入。** 追加输入只接受文字。图片、用户提问的回答、本轮设置更新、预期轮次不匹配或 Provider 已在收尾时，报告投递失败。追加输入随后按 Resident 规则改为下一轮处理，循环治理提醒记为未发送。Worker 接回原 turn 后可以继续接收受该 Provider 支持的追加输入。

### 实时上下文用量

声明 `context_usage_events` 的 Provider 在运行期间发送 `thread/tokenUsage/updated`。`tokenUsage.context` 是主会话当前上下文样本，`modelContextWindow` 是该模型的有效窗口；它们同时进入执行状态与 Harness 的事件观察入口。Harness 按这项能力和实测比例决定提醒，不按 Provider 名称分支。整轮累计用量仍用于结算，子 Agent 用量仍归属其自己的会话，两者都不能替代主会话上下文占用。

| Provider | 当前上下文与窗口来源 |
| --- | --- |
| Codex | App Server 原生 token 通知中的当前请求与窗口 |
| Claude | 每次主会话 AssistantMessage 后读取 SDK `get_context_usage` 的 `totalTokens` 与 `maxTokens` |
| Kimi | 主会话 `turn.step.completed.usage` 的输入及缓存读写；窗口来自所选模型配置的 `max_context_size` |
| DSH | 主会话 `assistant/message.usage` 的输入及缓存读写；窗口来自 `request/context.contextWindow` |
| OpenCode | 主会话 `message.updated.tokens` 的输入及缓存读写；窗口来自模型目录的 `limit.context` |

用量或窗口暂缺时不推算百分比，也不阻止本轮继续。Claude 的读取有短超时；下一次主会话消息可再次提供样本。一次性模型接口没有持续的原生 Agent 轮次，不声明这项实时能力。

## 取消和中断

取消分三层：Scheduler 记录取消意图，Worker 把意图交给 Runtime，Adapter 对已登记的中断目标尝试中断。Provider 还没有分配中断目标时，Runtime 的前置检查可以阻止调用；Provider 返回后 Runtime 再检查一次，已经取消的执行不会被提交成成功。

中断只保证“尝试”。Provider 拒绝中断或连接中断时，Scheduler 仍然通过租约、终态提交和维护流程确定这次执行的最终状态，并保留原因；Adapter 不自行修改 Scheduler 状态。

### 计划内中断

计划内 Worker 交接、用户取消、Provider 故障和失去领取资格分别保留各自原因。旧 Worker 完成持久准备后解除跟随并释放领取资格，新 Worker 恢复观察同一原生轮次。Runtime 保留原执行与恢复点，不把计划交接提交为失败或取消。

Provider 目录的 `handover_keeps_turn` 声明适用于所有 turn Provider：旧 Worker 离开这一轮，新 Worker 以原 turn ID 接回。计划交接不发送新输入，也不消耗故障预算。真实中断和 Provider 故障进入错误恢复策略；为恢复发送的续接输入以 `recovery_input_sent` 记录。身份与 Goal 的处理见 decision_3e8c2a7f91d4，持久准备与释放见 decision_075f66430606。

## 恢复点与故障恢复

Provider 成功返回后，Runtime 先保存“Provider 已完成”恢复点，其中包含规范结果和 Provider 恢复信息。之后即使 AgentData 或会话对应关系的提交失败，下一次恢复也能跳过第二次 Provider 调用，只补完内容记录和会话提交。

故障发生在“Provider 是否已经完成还不清楚”的时间窗里时，能否安全重试取决于 Adapter 提供的恢复能力。统一契约不保证所有 Provider 都恰好执行一次，也不允许在缺少证据时把可能重复的动作描述成已恢复。

路由需要调整时，Adapter 通过回调请求改换，由执行层更新容量和路由记录；Adapter 不直接修改会话对应关系。新增 Provider 因此主要改动适配层，Scheduler 不需要理解新的原生协议。

## Codex 的连续无进展恢复预算

一次执行可以包含多轮 Provider 调用和多次 Worker 领取。Codex Adapter 在观察到故障后，可能为继续同一次执行而再次调用 Provider，这称为一次**恢复尝试**。**连续无进展预算**限制两次有效工作进展之间最多可以有几次恢复尝试。有效工作进展指模型或工具确实重新产生了结果；任务是否完成仍由原来的终态流程判断。

### 预算的范围和耗尽结果

- **每次执行最多允许 3 次连续无进展的恢复尝试。** 首次正常请求不计入。首次请求和之后 3 次恢复都没有产生有效进展时，第四次故障使这次执行失败。
- **恢复路径共用计数。** Provider 系统错误、已进入恢复策略的中断和容量错误、已接受的一轮断线重连共用无进展恢复预算。错误能否续跑由恢复策略判断，直接终止的错误不会被预算机制改为重试。
- **安排恢复前先扣一次预算并保存。** 同一次故障从快速续跑转到通用续跑，只扣一次。一次断线后的 socket 重连在 180 秒内退避等待，期间不重复扣减；期限结束仍不可用时，保存原 turn ID 后延迟重新领取。
- **重新领取不重置已用次数。** 重新排队、更换或重建 Worker、恢复恢复点、开始新的 Provider 轮次，都保留已经用掉的次数。Worker 的领取次数只描述调度，不作为预算依据。
- **计划交接不算故障。** 只恢复观察原 turn，不扣预算、不消耗快速续跑次数，也不重置已用预算；交接后的真实 Provider 故障照常计入。
- **耗尽后明确失败。** 第 3 次恢复仍然失败且没有新进展时，Adapter 抛出 `provider_recovery_exhausted`，错误详情包含 `provider: codex` 和 `max_recovery_attempts: 3`；Worker 提交 `failed`，这次执行之后不再作为恢复任务被领取。
- **业务重试与技术恢复分开。** 技术恢复由 Harness、Runtime 与 Adapter 在同一次执行内完成，受上面的预算约束。执行失败后由用户明确要求继续、或由 Lineage 流程决定的重试，属于新的业务输入；SocietyService 不会自动生成 `continue` 来绕过已经耗尽的预算。恢复策略的归属见 decision_5c62deb8253a。

### 什么算有效进展

Adapter 只从当前轮次的 `item/completed` 事件判断有效进展：

- 事件必须有非空的 `item.id` 和 `turnId`，并且 `turnId` 等于当前已接收的轮次；带有错误或状态为 `failed`、`cancelled`、`interrupted` 的结果不算。
- **完成的模型消息算进展：** `agentMessage` 的文本不是空白。还在流式输出的片段不算。
- **成功的工具结果算进展：** `commandExecution`、`fileChange`、`mcpToolCall`、`dynamicToolCall` 和 `imageGeneration` 的状态为 `completed`；`webSearch`、`imageView` 按已完成事件判断，协议没有 status 字段。工具退出码缺失或为 0，`success` 不为假，嵌套的 `result.isError` 不为真。
- **控制类活动不算进展：** 开始、心跳、状态变化、重连提示、推理片段、压缩完成、空输出和失败的工具结果。压缩成功只表示上下文整理完成，不代表模型重新做了用户的工作。
- **重放的旧事件不会重复生效。** Adapter 记住当前轮次已经见过的结果 ID；恢复同一轮时沿用这些 ID。接收新的一轮时换一个新的记录集合，但保留预算。
- **出现有效进展后，连续无进展计数和快速续跑的局部次数都清零。** 其他控制切换次数的限制保持不变。

### 等待、取消与保存

- **正常等待不扣预算。** Provider 明确报告压缩仍在进行，或 thread 正被另一个写入方占用时，原来的延迟重新排队继续有效；真正抛出的压缩失败或等待失败才计入故障。
- **用户取消立即生效。** Adapter 在恢复路径中检查取消；运行中控制等待或压缩时收到中断，就把取消传回执行层，不再安排恢复。
- **预算通过统一回调保存。** Adapter 调用 `set_provider_recovery_state` 提交状态，由 Runtime 和 Scheduler 持久保存并交给下一次调用。
- **保存的数据很少。** `checkpoint.provider_recovery_state.recovery_budget` 只包含 `attempts`（已扣的次数）、`turn_id`（当前接收的轮次）和 `completed_item_ids`（这一轮已见的有效结果 ID），不保存提示词、模型正文或工具结果；快速续跑已用次数另存在 `provider_error_continue_count`。
- **清理临时恢复阶段时保留预算。** 健康工作期间只在内存中累计结果 ID，不为每个成功的工具结果额外保存；扣预算和清零时保存包含已见 ID 的快照。


`execution/codex_recovery.py` 实现预算、进展判定和重放去重，`backends.py` 在恢复路径中调用它。Codex 的错误续接使用公共 `provider_error_continuation` 处理等待、次数、取消和事件，再保存本次执行的恢复状态。定向测试验证重新领取后预算仍有效、真实进展清零、重放事件不清零、断线重连和取消；外部 Provider 使用测试替身。

## Claude Code 的工具与账号

Claude Adapter 使用 SessionManager 已经解析的路由；它与运行选项中的 profile 冲突时拒绝执行。成功的 `turn` 或 `fork` 把实际路由放进结果，由 SessionManager 提交。原生订阅与 DeepSeek 兼容 profile 的认证选择属于 Claude Code daemon 和它的 profile 目录，不进入 Scheduler 或 AgentData。

Claude 可用的 Harness 工具通过 SDK 的 MCP 接口和 Unix socket 回调到 Worker；工具的处理函数、取消检查、授权和结果后处理都留在 Worker 中。本轮工具目录和 Resident 身份由 Adapter 传给 daemon。这条工具通道不用于交互审批或运行中追加输入；完整协议见 decision_3948ce9b9443。

- **输入图片按路由能力交付。** 请求带图片时，Adapter 检查所选 profile 的目录是否声明支持图片；支持时把图片随本轮交给 daemon，不支持时这次执行失败。
- **Claude Code 自己完成的上下文压缩会被记录下来。** Claude Code 在它的进程内压缩上下文。每一轮结束后，Adapter 读取这个会话的 transcript，发现 AgentData 还没有记录的最新一次压缩时，通过 `record_compaction` 把它记为 Provider 上下文的起点，压缩后原样保留的消息排在它之后。之后从某条消息 Fork 或重启时，就能从这次压缩开始带入上下文。读取失败只记日志，不影响已经完成的这一轮。

## 本地模型请求计时

Adapter 可以在完成事件的 `params.request_timing` 中传递已有的 `started_at` 和 `finished_at`；直连模型 API 在 `provider_output.request_timing` 中传递相同字段。时间采用带时区的 UTC 表示。`started_at` 必须来自本地实际发送这一次模型请求的边界；`finished_at` 优先采用响应收完或原生运行时明确报告请求完成的时间。原生事件只有 Segment 完成观察时可以保存该完成时间，但不能与缺失或不同层级的开始时间组合成请求耗时。turn/step 开始、消息创建、首字时间和 Execution 总跨度都不能替代请求发送时间；未观测到的开始时间留空。

模型传输与时间观察的边界遵循 decision_25eb93d803a4 的“模型请求时间观察”。Portal 直接调用的模型 API 在现有传输边界复制时间；DSH 在 Portal 自己拥有的 `fetch` 边界复制会话身份与时间，原调用参数和返回值保持不变。原生 Provider 只有已有事件明确提供请求边界时才能填写请求时间。当前 Codex、Claude Code、Kimi 和 OpenCode 适配器没有可靠 dispatch 观察的模型段，其 `started_at` 保持为空；原生会话、轮次或输出身份可读，不意味着该身份同时具有发送时刻。


观察仅复制已有的必要时间字段，不转发模型请求，不改变 base URL、认证、代理环境、TLS、压缩、重试或 HTTP/WebSocket 方式，不通过读取响应正文提取关联身份，也不在响应回调中同步查询计时服务。时间副本按实际生成该输出的请求和原生身份关联；重试、主会话与子会话分别归属，缺少唯一关联时保持未知。

观察回调异常、设置不可读、现场时间缺失或关联不确定时，放弃相应时间副本，继续保留模型输出和原来的执行结果，不追加模型重试。历史事件重放不补造发送时刻，也不为了取证改写模型 Segment。可得的请求区间包含网络与远端排队，不能称为纯 Provider 计算时长；同一响应产生的多个 Segment 区间可能重叠，不能相加作为整轮耗时。

新启动的 ExecutionService 不创建模型计时代理监听器，新版四类原生 Adapter 不生成本地计时代理地址，不安装计时插件或附加计时请求头。旧进程或会话已绑定的代理地址按 decision_25eb93d803a4 的“ExecutionService 换代与旧路由退出”处理；保留兼容实现或旧代理文件不能证明当前新请求经过它，也不能证明计时可用。确认一条路线的能力需要实际会话绑定、已加载实现及原生观察事实，不为补时间中断会话、改路由或重发业务。


## 请求计时开关

Portal System Lineage 的 `system:request-timing:enabled` Event 拥有全系统模型请求时间记录开关，默认开启；ExecutionService 的 `request_timing` 读取其本地持久投影。`GetExecutionSettings.model_request_timing_enabled` 返回该投影；`UpdateExecutionSettings` 由 Lineage owner 转为同一 Event 的暂停或恢复。读取和修改仍检查当前调用者的管理员权限，不能借用会话 owner 或历史请求的主体。管理界面是入口，不另保存用户级状态。

关闭只停止后续持久化的可选请求时间副本，不删除已有 Segment 时间，不改变原生完成事件、模型传输、会话路由或容量。设置是否可读、时间记录是否开启与某个原生 Provider 是否提供 dispatch 事件是不同事实。沿用的 `provider_timing_proxy_enabled` 物理列只是滚动升级的投影存储兼容，不表示恢复代理传输职责。

格式名与版本须精确匹配当前规范值，校验规则见 decision_86aed2f0e8c7；本篇同时校验各字段和结构版本，不接受历史前缀或其他版本。
