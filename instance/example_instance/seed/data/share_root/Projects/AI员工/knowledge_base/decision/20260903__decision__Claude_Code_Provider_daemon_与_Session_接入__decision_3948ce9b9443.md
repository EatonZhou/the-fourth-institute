---
kb_id: decision_3948ce9b9443
type: decision
project: AI员工
created_at: '2026-09-03'
version:
  number: 28
  created_at: '2026-09-03'
  created_by:
    user: ''
    agent: ''
  updated_at: '2026-10-19T19:42:46Z'
  updated_by:
    user: tfi-exported-user
    agent: resident_865c3c24f251
importance: 8
metaness: 2
ai_generated: false
links:
  source:
  - ${code_root}/tests/cases/test_fork_runtime_inheritance.py
  - ${code_root}/tests/cases/test_claude_compaction_projection.py
  - ${code_root}/management_portal/execution/claude_transcript_compaction.py
  - ${code_root}/management_portal/claude_account_usage.py
  - ${code_root}/management_portal/claude_input_media.py
  - ${code_root}/management_portal/claude_tool_bridge.py
  - ${code_root}/management_portal/claude_provider_catalog.py
  - ${code_root}/management_portal/native_agent_transport.py
  - ${code_root}/management_portal/claude_code_client.py
  - ${code_root}/management_portal/claude_code_daemon.py
  - ${code_root}/management_portal/execution/backends.py
  - ${code_root}/tests/cases/test_claude_code_daemon.py
  - ${code_root}/tests/cases/test_execution_layer.py
  related:
  - decision_d40f59f5508f
  - decision_d19b9909a674
  - decision_4dea0db1f45a
  - decision_f1331d57be89
  - decision_5c62deb8253a
  - decision_91eb3626d323
  - decision_e587fe1c472e
  - decision_3dd52c0692c1
  - decision_6c2f91a7d84e
  - decision_ef1d6c925955
  - decision_c47a13e8b962
  - decision_6ad0384e020d
  - decision_b1ab45c4b3ec
  supersedes: []
  derived_from: []
---
# Claude Code Provider daemon 与 Session 接入

Resident 可以选择 Claude Code 作为 Provider，在连续的多轮对话中使用 Claude 或 DeepSeek 模型工作。Portal 主机上运行一个常驻的 Claude Code 守护进程（daemon），它通过 Claude Agent SDK 创建和保持模型会话；ExecutionService 通过 Unix socket 请求它运行一轮对话、派生会话、停止工作或读取账号额度。Portal 自己保存会话归属、规范消息和执行终态，守护进程只负责模型会话本身。

本 Design 说明两种 Claude 账号路线、守护进程的协议、一轮对话怎样运行、权限与工具、会话压缩的记录、Fork 与回收、中断与错误，以及账号额度的读取。统一执行生命周期见 decision_6ad0384e020d；系统指令的生成与附加见 decision_ef1d6c925955；守护进程运行时的安装与升级见 decision_e587fe1c472e。

## 基本概念

- **Claude Agent SDK**：Anthropic 提供的 Python 程序库，内含 `claude` 命令行程序，用来创建 Claude Code 会话并运行一次次查询（query）。一次 query 就是会话中的一轮生成。
- **守护进程（daemon）**：用户级服务 `portal-claude-code.service` 运行的 `ClaudeCodeDaemon`。它在多个 Portal 请求之间保留已经加载的会话，避免每轮都重新启动模型会话。
- **会话句柄（session handle）**：守护进程内存中的一个已加载会话，包括 SDK 客户端、锁、正在运行的任务和“运行签名”。
- **运行签名**：决定句柄能否复用的一组设置：工作目录、账号配置目录、账号路线、模型、推理强度、是否 owner、权限预设、系统提示、附加目录、本轮工具列表、Resident 身份环境变量和是否允许子 agent。
- **session key**：Portal 用来找到句柄的稳定键；**原生 session ID**：SDK 会话自己的 ID。
- **transcript**：Claude Code 把会话记录写在账号配置目录 `projects/` 下的 JSONL 文件。守护进程重启后可以据此恢复或派生会话。
- **会话记录（binding）**：Portal 保存的会话归属（decision_91eb3626d323）。守护进程可以释放句柄，但会话记录和 transcript 不随之删除。

## 两种账号路线

Claude Code 的账号路线都登记在 LLM API Registry（decision_6c2f91a7d84e）中，路线 ID 同时是执行账号 ID 和 `backend_route_id`（执行账号见 decision_d40f59f5508f）。

| | 订阅路线（`claude_one`） | DeepSeek 兼容路线（`deepseek_primary`） |
| --- | --- | --- |
| 登记条目的协议 | `claude_code_subscription`，服务商 `anthropic` | `openai_compatible_chat`，服务商 `deepseek` |
| 登录方式 | Claude 订阅登录，保存在该账号自己的配置目录（`config_dir`）中 | API 密钥，先取条目 `api_key_env` 指定的环境变量，再取 `api_key_file`；接口地址自动补上 `/anthropic` |
| 可用模型 | `claude-opus-5-5`（默认，别名 `opus`）、`claude-sonnet-5`（`sonnet`）、`claude-haiku-4-5-20251001`（`haiku`）、`claude-haiku-5-5`、`claude-fable-5-1`（`fable`） | `deepseek-v4-pro`（默认）、`deepseek-v4-flash` |
| 推理强度 | Opus、Sonnet、Haiku 5.5 与 Fable 5.1 支持 `low`、`medium`、`high`、`xhigh`、`max`；Haiku 4.5 不提供 | v4-pro 支持五档；v4-flash 支持 `low`、`medium`、`high` |
| 图片输入 | 支持 | 不支持 |
| 额度 | 订阅的 5 小时与每周窗口 | 按 DeepSeek 余额 |

- **订阅路线不保存任何密钥。** 条目不能写 API 密钥、密钥文件或接口地址；守护进程通过环境变量 `CLAUDE_CONFIG_DIR` 选择账号目录，并把继承来的 API 密钥、OAuth 令牌、云厂商开关和模型覆盖变量全部置空。登录和令牌刷新由 `claude` 程序自己负责，Portal 不读取或复制令牌。
- **模型与强度按条目声明校验。** 只有条目中启用的模型可以选；保存的别名（例如 `opus`）会换成对应的模型 ID，但选择列表中只显示模型 ID。模型没有声明推理强度时，不能显式指定强度。强度取值只有 `low`、`medium`、`high`、`xhigh`、`max`。
- **没有速度（service tier）设置。** Claude Code 的运行设置只有模型、推理强度和权限，修改后从下一轮开始生效。
- **新账号不影响已有 Resident。** 新增订阅账号不会改变现有 DeepSeek Resident 的路线。模型与推理强度统一由路线目录校验；无可用模型时明确失败。

## 设计点

- **一个 Portal 会话对应一个句柄。** 普通一轮使用运行设置中的 `session_key`；没有时依次使用会话记录 ID 和执行 ID，最终键不能为空。
- **只有运行签名完全一致时才复用句柄。** 签名变化且会话空闲时，守护进程关闭旧句柄并用新设置重建；会话正在运行时拒绝重建，保护正在进行的 query。上一轮以“认证失败”结束的句柄会被标记为失效，下一轮重建。
- **同一轮的重复请求共用一次运行。** 守护进程用 `(session_key, turn_id)` 识别一轮；同一轮的并发或重复请求等待同一个任务，调用方放弃等待不会取消它。一轮结束后结果保留 300 秒，供同一 turn ID 的重试读取。
- **Worker 换代时正在运行的一轮继续进行。** 新的 Worker 用同一 turn ID 再次请求时，守护进程把这一轮的事件输出和工具回调转到新的连接上，Provider 那一侧的生成不中断。
- **每次启动都带 Claude Code 自带的系统提示和同一份 Portal 系统指令。** 系统提示是 CLI 进程的启动配置，会话记录不保存它，恢复会话的进程只有这次启动给的系统提示。适配器每轮都发送这个会话的 Portal 系统指令（不论附加策略，decision_ef1d6c925955）；守护进程以 SDK preset `claude_code` 启动并把它追加在后面，没有系统指令时只带 preset，从不以空系统提示启动。每轮的系统指令相同，运行签名不变，第二轮起复用同一个句柄和进程，缓存前缀保持稳定。适配器不自己读取 TFI Portal 核心规则。
- **会话配置只来自 Portal。** 守护进程关闭 SDK 的设置文件加载、Skill 自动加载、逐字增量消息和非必要网络请求，用户或项目的 MCP 与 hooks 也不会被加载。
- **会话压缩被记录到 AgentData。** Claude Code 在自己的进程内压缩长会话；每轮结束后 Portal 读取 transcript 中最新的压缩，把它记到 AgentData，Fork 因此从压缩后的上下文开始（见“会话压缩的记录”）。
- **守护进程退出不丢会话。** 退出时释放全部句柄并删除 socket；会话记录和 transcript 保留，之后可以恢复。

## 守护进程协议

守护进程监听 `${staging_root}/portal_locks/claude-code-daemon.sock`，socket 权限为 `0600`，服务以 `UMask=0077` 运行。客户端每次请求建立一个连接，发送一行 JSON（`id`、`method`、`params`），然后逐行读取回复。回复行的 `type` 有：`event`（进展事件）、`tool_call`（请求 Worker 执行一个 Portal 工具，Worker 回一行 `tool_result`）、`error` 和 `result`（最终结果）。客户端只处理 `id` 与本次请求相同的行。

| 方法 | 作用 | 客户端等待上限 |
| --- | --- | --- |
| `health` | 守护进程、默认路线、模型、容量和工具桥接能力 | 3 秒 |
| `session/status` | 已加载的会话和正在运行的一轮 | 3 秒 |
| `account/usage` | 读取一个路线的额度快照，可要求刷新 | 3 秒 |
| `turn/run` | 创建或复用句柄，运行一轮 | 不限，由取消或终态结束 |
| `turn/interrupt` | 中断会话正在运行的 query | 10 秒 |
| `turn/steer` | 把文字输入交给会话正在运行的一轮 | 15 秒 |
| `session/compact` | 在两轮之间压缩会话的历史（Claude Code 的 `/compact`） | 590 秒 |
| `session/fork` | 从 transcript 派生一个新会话 | 不限 |
| `session/close` | 会话空闲时释放句柄 | 10 秒 |

建立连接最多等待 5 秒。连接失败、超时或连接在返回结果前关闭，都报告为 `ClaudeCodeUnavailable`（守护进程不可用）；守护进程返回的 `error` 保留原来的错误说明。

ExecutionService 的 Claude 适配器提供四种操作：`turn`、`fork`、`compact` 和 `release`。第一轮对话自动建立会话，没有单独的“创建”操作；`compact` 见下文“手动压缩”。追加输入到正在运行的一轮见下文“运行中追加输入”。

## 一轮对话怎样运行

```text
ExecutionService 准备请求、会话记录和系统指令附加决定
        ↓
Claude 适配器确定 session key、模型、推理强度、权限、系统提示和本轮工具
        ↓
守护进程按运行签名复用或创建句柄，建立 query
        ↓
立即发出 turn/started（带 session key 和 turn ID），原生 session ID 出现或变化时发出 thread/attached
        ↓
SDK 消息和工具调用转换成 Portal 事件；需要 Portal 工具时回调 Worker
        ↓
成功的 Result 形成唯一的最终回复和用量
        ↓
ExecutionService 保存规范消息、会话记录和执行终态，并记录新的会话压缩
```

守护进程同时最多运行 8 个 query（环境变量 `PORTAL_CLAUDE_CODE_MAX_CONCURRENCY`），每个会话另有独占锁。一轮无论成功还是失败，结束时都会清除“正在运行”标记，使释放、回收和重试看到一致的状态。

**事件与最终回复。** SDK 的初始化消息和 Result 原样保留；Bash 工具调用转换为命令事件，其他工具调用和结果转换为通用条目。助手文本先暂存：之后出现工具调用时，之前的文本作为过程说明（commentary）发出，使用稳定的 turn、消息与块 ID；成功的 Result 到达后，最终文本合并成唯一一条 `final_answer`，随后发出 turn 完成。最终文本优先取 Result 的文本，没有时取最后一个工具之后还没发出的助手文本。统一的 AgentData 和对话展示都使用这同一条最终回复。Result 缺失、标记为错误或被取消时，暂存的文本作为过程说明保留，不生成成功的最终回复。发送事件失败只影响观察，不会中断 query。子 agent 的消息按 `parent_tool_use_id` 对应父调用，转换为 `subagent/event`，驱动独立的子 agent 步骤与进度卡片。思考条目统一使用 `reasoning` 类型。

**用量。** 成功 Result 中的原生用量经统一执行链路记账。Anthropic 的普通输入、缓存写入和缓存读取分别报告，规范输入数包含三者，缓存命中只计缓存读取；统计规则见 decision_f1331d57be89。

## 图片输入

订阅路线可以直接接收图片；DeepSeek 路线收到图片时这一轮失败。每轮最多 20 张图片，只接受 PNG、JPEG、WebP 和 GIF。本地图片必须是可读的绝对路径文件，编码后每张不超过 10 MiB，一轮合计不超过 28 MiB；网页地址只接受不带账号密码的 HTTP(S) 地址，由 Claude 读取，Portal 不下载。其他文件通过已授权的资源引用使用。

## 权限

- **原生权限模式。** Resident 的权限预设映射为 Claude 的 `dontAsk` 或 `bypassPermissions`（完全访问），默认权限预设为完全访问，对应 `bypassPermissions`。能否选择完全访问由适配器的能力说明决定。
- **owner 与非 owner 的工具。** 调用者对这个 Resident 的工作区有管理权（owner）时，可用工具为 Read、Write、Edit、Glob、Grep、Bash、NotebookEdit、WebFetch 和 WebSearch；非 owner 只能用 Read、Glob、Grep、WebFetch 和 WebSearch。owner 身份和权限模式都进入运行签名，任一变化都重建句柄。
- **子 agent 开关。** Resident 设置 `allow_subagents` 决定模型能否启动子 agent：关闭时禁用 Agent 与 Task，打开或客户端未说明时允许。Claude 与 Kimi 的 Resident 默认打开。子 agent 的步骤记录见 decision_b1ab45c4b3ec。

Portal 的权限预设决定业务上允许什么，守护进程内的工具检查负责执行限制；两层同时生效，守护进程内部的句柄身份不能扩大用户权限。

## Harness 工具与身份

**Portal 工具。** Agent Harness 的工具（例如 HATG ISA 工具）由 ExecutionWorker 负责授权和执行（decision_5c62deb8253a）。适配器只把本轮工具的名称、说明和参数格式交给守护进程，守护进程登记为 `mcp__portal__<名称>`。模型调用这些工具时，守护进程通过当前连接发出 `tool_call`，Worker 检查本轮的授权目录和取消状态、执行工具并完成批次后处理，再回传结果或错误；调用 ID 必须对应，连接上不传输令牌或可执行代码。工具列表进入运行签名；每轮挂接本轮的分发器，结束即清除，前一轮的工具不会留在后一轮。工具执行出错时返回 `Harness tool failed: <错误类型>: <说明>`，取消照常传递。

**Resident 身份环境。** 请求可以带 `resident_environment`，只接受成对且格式正确的 `PORTAL_RESIDENT_ID` 与 `PORTAL_LINEAGE_ID`。验证后向 `claude` 子进程注入这两个规范键；不带身份时清空继承的身份键，不能继承其他 Resident 身份。请求要求继承 Portal 数据库环境（`inherit_portal_database`）时，必须同时带有有效 Resident 身份。命令行中的资源与 HATG 客户端仍用 Portal 发放的凭证认证，工作目录不决定身份。

## 会话压缩的记录

Claude Code 在上下文过长时会在自己的进程内压缩会话：transcript 中写入一条 `compact_boundary` 标记、一条取代旧历史的摘要，以及原样保留的最近消息。Portal 的 AgentData 必须记录同样的替换，否则 Fork 时交给新会话的仍是未压缩的完整历史。

- **每轮结束后记录最新的压缩。** Claude 适配器按账号配置目录和原生 session ID 找到 transcript（`<config_dir>/projects/*/<session ID>.jsonl`，必须恰好一个），读取最新的压缩；AgentData 还没记录它时，把摘要和保留的消息记为一次 Provider 压缩（decision_b1ab45c4b3ec）。读取或记录失败只写日志，不影响已经完成的这一轮。
- **压缩放在它实际发生的位置。** 事后记录的压缩被插在它保留的第一条消息之前，而不是放在历史末尾。这样从某条历史消息 Fork 时，新会话从截断点之前最近的一次压缩开始。

## Fork、切换账号与回收

**Fork。** Resident 的上下文分叉先从 AgentData 取出当前有效的 Provider 上下文（从最近一次压缩开始），再通过统一的上下文种子适配器交给新会话；查询时使用 Provider `claude`、实际的路线 ID 和原生 session ID。新 Resident 使用自己的工作区，并继承来源的账号和全部运行设置；显式指定了其他账号时，由统一的账号解析和运行设置校验确认模型与推理强度可用。新会话同时保存 `backend_route_id` 和 `provider_profile`，之后每一轮都使用这个账号。

守护进程的 `session/fork` 接收来源原生 session ID、目标工作目录、账号配置目录和可选标题。来源已加载时在它的会话锁内执行；派生程序最多运行 30 秒，错误说明截取前 500 个字符。成功后来源 transcript 保留，派生出的 transcript 移到目标工作目录对应的 Claude 项目目录。恢复或派生都必须恰好找到一个非空的 transcript；目标位置已有非空的同名文件时立即失败，不覆盖已有记录。

**切换账号或 Provider。** 已有 Resident 换账号或换 Provider 使用 `RestartSession`（decision_d19b9909a674）：同一 Provider 同一路线时用原生 Fork，跨账号或跨 Provider 时使用 AgentData 中保存的可移植上下文。已绑定会话的请求所带路线必须与运行设置中的 `provider_profile` 一致，不一致时这一轮失败并提示需要迁移会话。旧历史、搜索、导出和可移植上下文都读取 AgentData，不要求旧账号仍能登录。

**独占写入。** 一个 Portal 会话只由守护进程写入。可以在同一账号目录中用交互式 `claude` 登录或查询状态，但不能同时恢复、派生或修改 Portal 正在使用的 transcript。不同账号使用不同目录；备份要同时包含账号目录中的 transcript 和 PostgreSQL 业务数据，恢复时保持两者对应。

**释放与回收。** `release` 只尝试关闭空闲句柄，默认保留 transcript 和会话记录；会话不存在或正在运行时返回 `closed: false` 和原因。守护进程每 60 秒检查一次，空闲超过 3,600 秒（`PORTAL_CLAUDE_CODE_IDLE_SECONDS`，最小 60）的句柄被释放，已加载的会话最多保留 24 个（`PORTAL_CLAUDE_CODE_MAX_LOADED_SESSIONS`）；正在运行或被锁定的句柄不回收。回收不删除 transcript。

## 手动压缩

`/compact` 命令、Lineage 的“Compact”组件和聊天页的压缩按钮都通过 Resident 队列提交 `compact` 操作（2026-10-05 起，应用服务器 0.2.162-7）。适配器调用守护进程的 `session/compact`，守护进程向会话发送 Claude Code 自己的 `/compact`：Claude Code 用会话的模型把较早的历史写成一份摘要，保留最近的消息。

- **在两轮之间运行。** 压缩占用会话的锁和一个并发名额，期间会话显示为有一轮在运行，追加输入被拒绝。适配器先对齐原生会话身份、模型和子 agent 设置，再使用或恢复对应的 CLI。压缩请求带独立 UUID；守护进程收到这条用户消息的回显后才接受它的 Result，忽略先前后台操作的结果。
- **结果。** 守护进程读取 CLI 报告的压缩边界，返回压缩前后的 token 数和用时。历史太短时（Claude Code 回答“Not enough messages to compact.”）返回未压缩，适配器回复“没有足够的历史可以压缩，会话未改变”；Result 标记为错误时操作失败。
- **记录。** 压缩成功后，适配器与每轮结束后一样从会话记录读出这次压缩，记入 AgentData。Claude Code 不报告写摘要那次请求的用量，所以这次操作不记 token 消耗；压缩后的上下文大小用来更新上下文用量。客户端最多等待 590 秒。

## 运行中追加输入

`turn/steer` 带 session key、turn ID、文字和控制 ID（2026-10-03 起，应用服务器 0.2.162-6）。守护进程把文字作为一条带 UUID 的用户消息写入正在运行的 query；Claude Code 在当前一步（通常是正在运行的工具）结束后读到它。

- **只进入指定的一轮。** 没有正在运行的一轮时返回 `steered: false`（`no_active_turn`），原生 session ID 或 turn ID 不一致时返回 `session_changed` 或 `turn_changed`。这一轮已被中断，或成功 Result 已经到达且没有等待中的追加输入时，接收关闭（`turn_closing`），以免它开出一轮 Portal 不读取的回答。同一控制 ID（没有时用文字）重复投递只写入一次。
- **CLI 已经回答时，回答这条输入的一轮仍属于这一轮。** 追加输入写入后，守护进程以 CLI 回显的 UUID 判断它是否已被读取。Result 到达时仍有未读取的追加输入，CLI 会在这次回答之后单独回答它：守护进程把先到的回答作为过程说明发出，继续读取下一次 Result，把它作为这一轮的最终回答，用量和耗时相加。CLI 在 120 秒内没有开始回答时，以先到的回答结束这一轮。

## 中断

中断优先用运行设置中的 `session_key` 定位句柄；没有时，适配器把 ExecutionService 保存的原生 session ID 交给守护进程反查。两者都没有时中断失败。请求带 turn ID 时必须与正在运行的一轮相同，带原生 session ID 时也必须相同；迟到的中断返回 `interrupted: false`，不会中断后面的一轮。没有正在运行的一轮时同样返回 `interrupted: false` 和原因。适配器只有在守护进程回复 `interrupted: true` 时才认为中断成功。

## 账号状态与额度

- **`health` 不代表每个账号都可用。** 它只描述守护进程本身、默认路线和工具桥接能力。
- **账号状态。** Home 通过 `account/usage` 读取订阅路线的额度与可用性；失败时保留旧样本并显示读取错误。
- **额度读取。** `ClaudeAccountUsage` 负责额度：它用固定参数运行 `claude --print /usage --output-format json`，关闭工具、MCP、hooks 和 transcript 保存，只接受订阅窗口数据和零费用的本地命令结果，异常输出按失败处理。普通读取最短间隔 180 秒，手动刷新或上次失败后为 60 秒；同一账号同一时间只有一次查询，单次最多 20 秒。失败时保留已有样本并记录错误；不同账号互相隔离。
- **运行中的额度事件。** 正常一轮收到的 RateLimitEvent 也会更新支持的窗口、状态、比例和重置时间；事件不带比例时不影响执行，也不清除或延长同一窗口的命令行查询结果。样本超过 300 秒或到达重置时间即视为过期。守护进程重启后，Home 读取额度可以直接发起命令行查询，不需要先调用模型。
- **Home 的展示。** Home 显示登录可用性和额度；比例未知时显示为空，不推算订阅额度。展示规则见 decision_4dea0db1f45a。小时用量按 Provider 和确切的 Resident 归因，与订阅剩余额度是两回事。

## 部署与升级

`scripts/install_claude_code_backend` 把守护进程代码安装到 `~/.local/lib/dirtybase-claude/claude-app-server-<版本>` 的不可变副本，并更新当前链接与服务配置。修改 Dev 源码后，需要安装新的 app-server 版本并在没有运行中轮次时重启服务。守护进程用 `claude-agent-sdk-current` 虚拟环境中的 Python 运行（`python -m management_portal.claude_code_daemon`），SDK 版本的安装、切换与回退见 decision_e587fe1c472e。切换只在没有进行中的 Claude 执行、且守护进程报告没有运行中的轮次时进行；切换后核对 `health` 中的版本、能力和实际账号状态。执行完成后的检查点与 PostgreSQL 提交遵循统一的执行恢复规则，守护进程不维护第二份 Portal 历史。

Model API 表结构或随包复制的 `model_api_store`、`ai_provider_registry`、数据库访问模块发生变化时，也必须递增 app-server 版本并重新安装不可变副本。Portal Web 和 ExecutionWorker 发布不会替换已安装的 Claude 包。切换前同时核对调度器没有运行中的 Claude Execution、daemon 的 `active=0`；切换后须确认 `provider_ready=true`、`provider_error` 为空及 app-server 版本，不能只以 systemd active 判断完成。

## 相关实现与测试

实现在 `management_portal/claude_code_daemon.py`（守护进程）、`management_portal/claude_code_client.py` 与 `management_portal/native_agent_transport.py`（客户端与逐行协议）、`management_portal/claude_provider_catalog.py`（路线与模型目录）、`management_portal/claude_tool_bridge.py`（Portal 工具）、`management_portal/claude_input_media.py`（图片）、`management_portal/claude_account_usage.py`（额度）、`management_portal/execution/claude_transcript_compaction.py`（读取压缩）和 `management_portal/execution/backends.py` 的 `ClaudeBackend`。主要测试是 `tests/cases/test_claude_code_daemon.py`、`tests/cases/test_claude_compaction_projection.py`、`tests/cases/test_fork_runtime_inheritance.py` 与 `tests/cases/test_execution_layer.py`。SDK 字段参见 https://github.com/anthropics/claude-agent-sdk-python/blob/main/src/claude_agent_sdk/types.py 。
