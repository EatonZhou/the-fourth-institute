---
kb_id: decision_276105b5d67b
type: decision
project: AI员工
created_at: '2026-09-30'
version:
  number: 16
  created_at: '2026-09-30T12:40:00Z'
  created_by:
    user: tfi-exported-user
    agent: resident_f46ff0e5d912
  updated_at: '2026-10-19T19:43:58Z'
  updated_by:
    user: tfi-exported-user
    agent: resident_865c3c24f251
importance: 6
metaness: 2
ai_generated: false
links:
  source:
  - ${code_root}/management_portal/dsh_client.py
  - ${code_root}/management_portal/dsh_provider_catalog.py
  - ${code_root}/management_portal/dsh_runtime/bridge.mjs
  - ${code_root}/management_portal/dsh_runtime/package.json
  - ${code_root}/management_portal/execution/backends.py
  - ${code_root}/management_portal/execution/provider_catalog.py
  - ${code_root}/management_portal/execution/native_options.py
  - ${code_root}/management_portal/execution/native_harness_tools.py
  - ${code_root}/management_portal/execution/account_adapters.py
  - ${code_root}/management_portal/execution/provider_updates.py
  - ${code_root}/tests/cases/test_dsh_backend.py
  related:
  - decision_425e428410e0
  - decision_6ad0384e020d
  - decision_25eb93d803a4
  - decision_3948ce9b9443
  - decision_3dd52c0692c1
  - decision_d40f59f5508f
  - decision_e587fe1c472e
  - decision_ef1d6c925955
  - decision_6c2f91a7d84e
  - decision_b1ab45c4b3ec
  - decision_c47a13e8b962
  supersedes: []
  derived_from: []
---
# DeepSeek Harness Provider 的会话接入

DeepSeek Harness（简称 DSH）是 DeepSeek 发布的本地 Agent 运行时。它以 npm 包 `@deepseek-ai/dsh` 的形式安装，在本机用 Node.js 运行，调用 DeepSeek 模型，并提供 shell、文件读写、文件搜索、todo 列表和网页工具。Portal 把它作为一种原生 Provider（后端名 `dsh`）：Resident 选择 DSH 后，每个 Portal 会话按需启动一个自己的 DSH 进程，Portal 通过 Unix socket 请求它运行一轮对话、中断或释放会话。

本 Design 说明 DSH 在 Portal 中能做什么、会话和进程怎样对应、凭据和环境怎样传入、一轮对话怎样运行，以及断线或 Worker 换代后怎样避免重复执行。所有原生 Provider 共用的执行、事件与中断契约见 decision_425e428410e0；执行账号与额度保护见 decision_d40f59f5508f；运行时的安装与自动更新见 decision_e587fe1c472e；系统指令的生成见 decision_ef1d6c925955。

## 基本概念

- **路线**：DSH 使用的模型接口。DSH 只有一条路线，按服务命名为 `deepseek`，执行账号是 `dsh_deepseek`。路线使用 LLM API Registry（decision_6c2f91a7d84e）中由 `model_api_store.route_api_id("dsh", "deepseek")` 显式绑定、已启用且类型为 `openai_compatible_chat`、厂商为 `deepseek` 的条目：密钥、地址和模型来自这个条目，用量计入这个条目的余额（账号的 `billing_account_id`）。会话记录中的 `backend_route_id` 和运行选项中的 `provider_profile` 都保存路线名 `deepseek`。2026-10-12 之前路线以条目 ID 命名、账号名为 `dsh_deepseek_primary`；改名时已有 Resident 的路线、Provider 会话和按小时用量一并迁移，仍按旧路线运行的空闲会话进程被释放，下一轮按新路线重新启动并恢复磁盘上的会话记录。
- **DSH 会话**：DSH 保存的一段连续对话，用会话 ID（形如 `dsh-<32 位十六进制>`）标识，在 Portal 会话记录中作为原生会话 ID 保存。
- **会话进程**：服务一个 DSH 会话的 Node.js 进程。每个会话最多一个进程，第一次需要时启动，空闲一段时间后自行退出，下次需要时再从磁盘上的会话记录恢复。
- **Portal 桥接插件**：Portal 仓库中的 `management_portal/dsh_runtime/bridge.mjs`，以插件 `portal-control` 的身份加载进会话进程。它在 Unix socket 上接收 Portal 的请求，调用 DSH 的公开服务运行对话，并把事件和工具调用转回 Portal。协议名为 `tfi.native-agent.v1`，每条消息是一行 JSON。
- **稳定 turn ID**：Portal 为每一轮对话分配、重试时保持不变的 ID。DSH 用它识别重复请求，保证同一轮只执行一次。
- **运行时目录**：已安装的 DSH npm 包和桥接插件所在目录，默认通过链接 `~/.local/share/dirtybase-portal/dsh-runtime-current` 访问，可用环境变量 `PORTAL_DSH_RUNTIME_ROOT` 改为其他目录。

## 能力与设置

| 项目 | DSH 的行为 |
| --- | --- |
| 支持的操作 | 运行一轮对话（`turn`）、手动压缩（`compact`）、释放会话（`release`） |
| 运行中追加输入 | 支持文字（`turn/steer`，2026-10-03 起） |
| 不支持的操作 | 原生 Fork、直接输入图片、交互式审批 |
| 可修改的运行设置 | 模型、推理强度（Effort）、“允许开启 sub agent”；修改从下一轮生效 |
| 权限预设 | 不可修改；只接受已授权的完整访问执行 |

- **可选模型和推理强度来自路线条目。** 路线条目中已启用的模型都可选；每个模型的推理强度只保留条目声明的 `low`、`high`、`max` 三种。条目的默认模型必须已启用，否则这条路线的目录无效。请求中的模型或推理强度不在目录中时，这一轮在启动 DSH 前失败；推理强度留空时 DSH 使用 `high`。
- **DSH 只接受已授权的完整访问执行。** 一轮请求必须是 owner 执行，权限配置为 `owner` 或 `danger-full-access`，文件系统授权方式为 `A`（完整访问）；其他授权方式在进入 DSH 前就被拒绝。DSH 的沙箱策略固定为完整访问，工作区根目录是本轮的工作目录。
- **直接输入图片的请求失败。** 其他文件通过已授权的资源引用和 shell 工具读取。
- **Fork 和切换 Provider 使用 Portal 保存的上下文。** DSH 没有原生 Fork。`ForkResident`、`RestartSession` 以 DSH 为目标时，Portal 把 AgentData 中保存的可移植上下文作为第一轮的上下文种子交给一个新的 DSH 会话（decision_c47a13e8b962）。继承的记录放在这一轮提示的最前面，后面仍是 Portal 为新 Resident 本轮放上的身份说明、消息和授权上下文；来源会话的启动指令不进入继承记录，包括 DSH 在自己会话里记下的系统消息和开发者消息。
- **Resident 的初始说明随用户消息发送。** Resident 请求附带初始说明时，DSH 把它放进本轮的用户消息；系统指令的传递见下文“一轮对话”。

## 会话与进程

```text
ExecutionService 的 DshBackend
        │ 校验设置与权限，确定会话 ID
        ▼
DshClient（在执行 Worker 进程内）
        │ 持有会话的 launch.lock：探测进程，必要时生成配置并启动
        ▼
Unix socket  <PORTAL_DSH_SOCKETS>/<会话 ID 的摘要>.sock
        │ 一行一条 JSON：health、turn/run、turn/steer、turn/interrupt、session/compact、history/read、session/close
        ▼
会话进程：node …/@deepseek-ai/dsh/lib/bin.js --profile portal-resident
        └─ portal-control（bridge.mjs）调用 DSH 的会话、Agent、工具服务
```

- **会话 ID 由第一轮的 turn ID 决定。** 新会话的 ID 是 `dsh-` 加上第一轮 turn ID 的 SHA-256 摘要前 32 位。第一轮在 Portal 记下会话 ID 之前被打断时，重试同一轮会得到同一个会话 ID，因此可以接回原来的会话。
- **每个会话有独立的状态目录和 socket。** 状态目录是 `<PORTAL_DSH_STATE>/<会话 ID>/`（默认根目录 `${staging_root}/shared/dsh`），其中保存 DSH 的会话记录（`sessions/` 下的 JSONL 文件）、生成的配置、运行日志 `runtime.log` 和启动锁 `launch.lock`。socket 位于 `<PORTAL_DSH_SOCKETS>/<会话 ID 的 SHA-256 前 32 位>.sock`（默认根目录 `${staging_root}/portal_locks/dsh`）。目录权限为 0700，socket 和日志文件为 0600。
- **同一会话的启动和释放串行进行。** DshClient 在会话的 `launch.lock` 上加独占文件锁，先用 1 秒超时探测 `health`；进程已经在运行就直接使用。否则生成配置并启动进程，最多等 25 秒到 `health` 响应。进程提前退出或等待超时，这一轮以“Provider 不可用”失败，错误说明指向该会话的 `runtime.log`。
- **一个会话固定在一个工作目录、一条路线和一个 Resident 身份上。** 会话进程启动时记下这三项；之后的请求中任何一项不同，桥接插件拒绝这一轮。换工作目录或路线要通过 `RestartSession` 迁移会话。
- **启动时确定的设置改变后，下一轮启动新进程。** 自动压缩阈值和是否提供子 Agent 工具只在进程启动时读取；DshClient 把它们记在状态目录的 `launch.json`（`threshold_ratio`、`subagents`）。下一轮开始前，DshBackend 比较本轮要求的值：要求的阈值比启动时更低，或“允许开启 sub agent”与启动时不同（包括没有记录的旧进程），就先释放会话，这一轮由新进程从磁盘记录恢复。模型或推理强度与会话日志最后记下的请求头不同时同样如此。
- **空闲的会话进程自行退出。** 没有进行中的一轮、且距上次使用超过 300 秒时，进程退出（每 15 秒检查一次）。下一轮到来时重新启动，从磁盘上的会话记录恢复对话。
- **释放会话会结束进程。** Portal 在启动锁内发送 `session/close`：有进行中的一轮时请求被拒绝；否则桥接插件写盘、关闭会话后退出。Portal 最多等 10 秒确认 socket 不再响应，超时报告“释放尚未完成”。会话从未启动或进程已经退出时，释放直接成功。

会话进程是启动它的 Worker 的子进程，属于该 Worker unit 的 cgroup。两个 Worker 池共用 STATE 与 SOCKETS 目录；重启一个 unit 会杀掉属于它的全部 DSH 会话进程，另一个池也会观察到这些共享会话断开并按恢复流程处理。

## 凭据与环境

- **模型密钥只通过进程环境变量传给 DSH。** 每次启动会话进程时，DshClient 从 LLM API Registry 为路线条目登记的密钥文件读取凭据，放进子进程的环境变量 `DEEPSEEK_API_KEY`；DSH 的 DeepSeek 插件从这个变量取密钥。凭据为空时启动失败。生成的配置文件、日志和 Portal 消息中都只出现变量名。
- **子进程只继承列出的系统环境变量。** 它们是 `PATH`、`HOME`、`LANG`、`LC_ALL`、`USER`、`LOGNAME`、`XDG_RUNTIME_DIR`、`DBUS_SESSION_BUS_ADDRESS`、`SSL_CERT_FILE`、`SSL_CERT_DIR`、`HTTPS_PROXY`、`HTTP_PROXY`、`NO_PROXY`。Portal 另外设置 `DSH_HOME`（状态目录）、`DEEPSEEK_API_KEY`、`DEEPSEEK_BASE_URL` 和 `DEEPSEEK_SEARCH_BASE_URL`。
- **接口地址使用 DeepSeek 的 Anthropic 兼容入口。** `DEEPSEEK_BASE_URL` 取路线条目的 `base_url`，未填写时为 DeepSeek 官方地址。官方地址 `https://api.deepseek.com` 和 `https://api.deepseek.com/v1` 换成 `https://api.deepseek.com/anthropic`；其他地址原样使用。
- **网页搜索走同一条路线。** `web_search` 是一次单独的 DeepSeek Messages 请求，由服务端检索后回答。Portal 把 `DEEPSEEK_SEARCH_BASE_URL` 设为 `DEEPSEEK_BASE_URL` 加 `/v1`，搜索请求因此发往路线自己的地址，并使用同一个 `DEEPSEEK_API_KEY`。`web_fetch` 匿名抓取公开的 HTTP(S) 地址，经过子进程继承的代理变量。DSH 在会话日志中只记下搜索请求本身（`web/deepseek-search-llm-request`），不记它的 token 用量，所以这一轮的用量统计不含搜索请求。
- **Resident 身份只接受成对的规范 ID。** 请求中的 `resident_environment` 只接受 `PORTAL_RESIDENT_ID` 与 `PORTAL_LINEAGE_ID`。两类 ID 格式分别为 `resident_<12 位十六进制>` 和 `lineage_<12 位十六进制>`，要么都有，要么都没有。验证后的身份以规范键写入子进程环境；无身份时不继承进程外的 Resident 身份。后续请求仍须通过身份核对。
- **获得授权的 Resident shell 可以访问 Portal 数据库。** 请求满足 decision_25eb93d803a4 规定的条件时，Portal 把数据库连接环境交给会话进程；这种请求必须同时带有 Resident 身份。

## 会话配置

DshClient 每次启动进程时在状态目录下重新生成配置目录 `profiles/portal-resident/`：`package.json` 声明配置只在启动时读取，`node_modules` 链接到运行时目录，`cordis.patch.yml` 按顺序列出加载的插件。

| 插件 | 用途与 Portal 给的配置 |
| --- | --- |
| `cordis-plugin-timer`、`dsh-llm`、`dsh-session`、`dsh-session-projection`、`dsh-tools`、`dsh-agent` | DSH 的基础服务：定时器、模型调用、会话、会话消息视图、工具注册、Agent |
| `dsh-system-prompt` | 系统提示；关闭 DSH 自带的身份说明和运行环境说明 |
| `dsh-llm-deepseek-api-key` | DeepSeek 模型接口；密钥取自 `DEEPSEEK_API_KEY`，流式响应 120 秒没有新数据视为中断 |
| `dsh-session-persistence-jsonl`、`dsh-session-checkpoint-policy` | 会话记录以不压缩的 JSONL 写在 `sessions/` 下，并按 DSH 的检查点规则落盘 |
| `dsh-token-meter`、`dsh-compaction-tool-result-pruner`、`dsh-compaction-basic` | 上下文计量与自动压缩；先裁剪过长的工具结果，再在上下文达到阈值时压缩历史。阈值默认 0.8（DSH 自己的默认值），Agent Harness 的提前压缩功能可以要求更低的比例；保留比例为阈值的一半、最多 0.16；阈值在进程启动时确定 |
| `dsh-sandbox-local`、`dsh-sandbox-policy`、`dsh-subprocess-local` | 本地沙箱与子进程；策略为完整访问，工作区根目录为本轮工作目录 |
| `dsh-terminal`、`dsh-terminal-bash`、`dsh-tool-bash-persistent` | shell 工具；单条命令最长 300 秒 |
| `dsh-fs-local`、`dsh-tool-fs` | 文件读写工具 |
| `dsh-tool-fs-search` | 文件搜索工具 `glob`、`grep`，使用运行时自带的 ripgrep；超过上限的 glob 结果不抽样 |
| `dsh-tool-todo` | todo 列表工具 `todo_write`；允许多项同时进行 |
| `dsh-web`、`dsh-web-search-deepseek`、`dsh-web-fetch-http`、`dsh-tool-web` | 网页工具 `web_search`、`web_fetch`；搜索由 DeepSeek 完成（密钥取自 `DEEPSEEK_API_KEY`，单次最长 60 秒），抓取为匿名 HTTP(S) |
| `dsh-subagent`、`dsh-subagent-spawn-in-process`、`dsh-tool-subagent` | 子 Agent 工具 `subagent`，只在“允许开启 sub agent”打开时加载；子 Agent 由 `spawn` 方式在同一进程中启动，上下文是新的，只在前台运行（`enableRunInBackground: false`），嵌套深度为 DSH 默认的 1 层 |
| `dsh-agent-loop` | Agent 循环；`agents` 为空，不声明额外的顶层 Agent |
| `portal-control` | Portal 桥接插件，配置为会话 ID、socket 路径、工作目录、路线和 Resident 身份摘要 |

## 一轮对话

1. DshBackend 校验模型、推理强度和权限，得到会话 ID（已有会话用记录中的 ID，新会话由 turn ID 计算），准备好会话进程后发送 `turn/run`。Portal 随即发出 `thread/started` 事件，携带会话 ID。
2. 桥接插件核对会话 ID、工作目录、路线、Resident 身份和权限，然后加载会话：磁盘上已有记录就恢复，没有就新建。
3. 桥接插件设置本轮的模型和推理强度，登记本轮的 Portal 工具，把用户消息加入会话，等待 Agent 运行到空闲，再把会话写盘。
4. 这一轮以正常完成结束时，返回最后一条助手消息的文本和 token 用量；否则这一轮失败。

- **DSH 只在 Portal 的一轮之内运行。** 桥接插件拦截 Agent 的每一步：当前没有 Portal 请求的一轮时，这一步被拒绝。
- **每轮完整替换系统指令。** Portal 每轮通过 `system_prompt_append` 传入完整系统指令集合，包含核心规则与本轮附加规则。桥接插件设置名为 `portal`、`complete: true` 的唯一系统提示；重新发送完整集合保证到期规则不会覆盖核心规则。
- **Portal 工具每轮重新登记。** Portal 在每轮请求中列出可用工具的名称、说明和参数格式，桥接插件先注销上一轮的工具再登记本轮的工具。模型调用这些工具时，桥接插件向 Portal 发送 `tool_call`；Portal 在执行 Worker 中运行对应的处理函数，把结果作为 `tool_result` 返回。处理函数出错时，结果标为失败，内容是错误类型和前 1000 个字符的说明。
- **事件转换成 Portal 的统一格式。** 助手消息的文本增量转为 `item/agentMessage/delta`，完整消息转为类型 `agentMessage` 的 `item/completed`，工具调用的开始和结果转为类型 `dynamicToolCall` 的 `item/started` 与 `item/completed`（结果带 `completed` 或 `failed` 状态）。DSH 会话格式 v4 中，一条 `tool/result` 的消息本身就是一次工具调用的结果（角色 `tool`，带 `toolCallId`）；替换已有节点的 `tool/result`（裁剪或改写较早的工具结果）不再产生新的工具结果。Chat 的工具步骤和“本轮修改”（decision_1e2eb869bc10）都来自这些事件。`turn/started` 让 Portal 记下本轮输入已被 Provider 接受（decision_b1ab45c4b3ec）。
- **write 工具新建的文件与其他 Provider 一样共享。** DSH 从 0600 的临时文件发布新文件，目录有默认 ACL 时组和 ACL 用户也读不到它。`tool/result` 标明 `operation: create` 时，桥接插件把这个文件的权限改成同一目录中普通新建文件会得到的权限（按 umask 或默认 ACL），覆盖已有文件时保留原权限。
- **提前结束的 bash 结果从 shell 重新读取。** `dsh-terminal-bash` 每 50 毫秒检查一次命令是否结束，其中一项检查只看 /proc：shell 回到读终端的状态就判定命令结束，这时可能还没读到命令最后的输出。命令恰好在这次检查期间结束时，结果没有“[Command finished with exit code N]”一行，输出的结尾（常常是全部输出）丢失；`sleep N; date` 这类先长时间静默的命令最容易遇到，本机记录中约 1% 的 bash 调用如此。桥接插件在 `tools/post-execute` 中处理这种结果：稍等至多 0.5 秒，从同一个 shell 的回滚里按这次命令的起止标记取出完整输出，补上退出码后交给模型。命令确实仍在等待输入、起止标记对不上、或 DSH 改了标记格式时，结果保持原样。
- **用量把缓存命中计入输入。** 输入 token 数是未命中缓存和命中缓存的输入之和，`cached_input_tokens` 是命中缓存的部分；总数优先使用 DSH 报告的值。
- **子 Agent 的请求计入这一轮的用量。** 每一轮结束后，DshBackend 从会话日志读出这一轮的每次模型请求；父会话日志里这一轮的 `subagent/catalog` 事件列出子 Agent 的会话 ID，它们的日志在父会话目录旁边，其中的请求也计入这一轮和账号用量。上下文大小只看父会话自己最后一次请求。

## 子 Agent

Resident 的“允许开启 sub agent”打开时，DSH 的模型多一个 `subagent` 工具，参数是简短的任务描述（`description`）和任务提示（`prompt`）。DSH 在同一进程里为子 Agent 新建一个会话，会话头记下父会话 ID 和来源 `subagent`；子 Agent 使用与父会话相同的路线、模型和本地工具，没有 Portal 工具，也不能再启动子 Agent。工具调用一直等到子 Agent 结束，结果是子 Agent 最后的回答，所以子 Agent 运行在这一轮之内，取消这一轮也会取消它。

- **子 Agent 的步骤按调用归属转给 Portal。** 子会话的记录不带父调用的 ID。子会话第一条 `subagent/descriptor` 事件的 `label` 就是那次调用的 `description`，桥接插件据此把子会话对应到这一轮中还没有对应的 `subagent` 调用（描述相同时按调用顺序）；对应之前的事件在对应后补发。子会话的助手消息、工具调用和任务提示按一轮对话的同样规则转换，包装成 `subagent/event`（`traceKey` 为子会话 ID，`parentToolUseId` 为父调用 ID，`model` 为子会话最近一次请求的模型）；DSH 为子 Agent 生成的系统提示不转发。Worker 交接后，新的 Worker 收到父会话和子会话中尚未发送的事件。
- **Chat 显示与其他 Provider 相同的子 Agent 卡片。** DshClient 把桥接事件交给 `DshSubagentCards`（`management_portal/dsh_subagent_cards.py`）：父会话的 `subagent` 调用开始时发出运行中的卡片（`subagent/progress`），子 Agent 每走一步更新一次，调用返回结果时发出完成卡片（类型 `subagentRun` 的 `item/completed`，状态 `completed`、`failed` 或 `stopped`，种类显示为“通用”）。卡片用量是子会话各次请求之和：桥接插件把子会话每条助手消息的用量作为 `thread/tokenUsage/updated` 放进 `subagent/event`，input 含缓存读写。子 Agent 的步骤另外记入它自己的 `subagent_trace` 序列。
- **可以单独停止一个子 Agent，这一轮继续。** 运行中卡片的“停止”经 Execution 输入控制到 DshClient 的 `stop_subagent`，桥接 `subagent/stop` 找到这次调用对应的子 Agent，按父 Agent 的方式取消它（`ctx.agents.get(子会话).cancel({kind: 'parent'})`）；子会话还没对应到调用时，对应后立即取消。`subagent` 调用以 “subagent run was cancelled” 和已有的部分回答返回，模型接着这一轮工作。桥接先发 `subagent/stopping`，卡片以已停止结束；调用已有结果时返回 `subagent_finished`，这一轮结束后返回 `no_active_turn`。
- **DSH 自己完成的压缩记入 AgentData。** DSH 在会话进程里压缩，并把压缩检查点写进会话日志，被替换范围之后的消息保留。每一轮结束后，DshBackend 读取会话日志，把 AgentData 还没有记录的最新一次压缩记为 Provider 压缩（见 decision_b1ab45c4b3ec）；记录失败只写日志，不影响这一轮。

## 重复请求、断线与中断

- **每一轮必须带稳定 turn ID，一个会话同时只运行一轮。** 那一轮仍在运行时，同一个 turn ID 的请求再次到来，新连接接管之后的事件和工具调用，并等待同一个结果；不同 turn ID 的请求被拒绝。运行中的接管按 turn ID 判断。
- **已经送入会话的输入不会重发。** 桥接插件给每条 Portal 用户消息一个由 turn ID 摘要、请求内容摘要（提示、模型、推理强度、工具列表）和系统指令摘要组成的消息 ID。重试时如果会话中已有这一轮的消息：那一轮已正常完成，就补发它的全部事件并返回原结果；没有完成时，Provider 报告输入已经接受但这一轮被中断；公共恢复流程使用 “continue” 续接，原始输入不重复提交。同一个 turn ID 带着不同的请求内容或系统指令到来时被拒绝。
- **连接断开时轮次继续运行。** Worker 脱离后，桥接插件保留轮次；等待中的 Portal 工具最多等 180 秒供后续 Worker 接管并返回结果。显式取消仍会中断轮次，工具结果无法确认时报告结果可能未知。
- **运行中追加输入只进入指定的那一轮。** `turn/steer` 带会话 ID、turn ID、文字和控制 ID。那一轮不在运行时返回 `steered: false`（`no_active_turn`）；已经收尾或 Agent 不在运行时返回 `turn_closing`。否则桥接插件以 `source: user` 的用户消息调用 `agent.steer`，DSH 在下一步读到它；这一轮要等这些输入都处理完才结束，结果取这一轮最后一次 `turn/end`。消息 ID 由 turn ID 和控制 ID（没有时用文字）计算，同一控制重复投递只送一次。
- **手动压缩在两轮之间运行。** `/compact`、Lineage 的“Compact”组件和聊天页的压缩按钮提交 `compact` 操作。DshClient 发送 `session/compact`，桥接插件在 Agent 空闲时调用 DSH 压缩服务的 `compactNow`（DSH 自己的 `/compact` 也是它）：较早的一段历史换成一份由会话模型写的摘要，最后一条消息保留。正在运行一轮或另一次压缩时返回 `busy`，操作失败；压缩期间新的一轮被拒绝，会话进程也不会空闲退出。没有可压缩的范围，或摘要不比原内容小（很短的历史）时，返回未压缩，适配器回复“没有足够的历史可以压缩，会话未改变”。成功时返回替换的条数、约 token 数和压缩后的上下文估算，适配器从会话日志读出这次压缩记入 AgentData；写摘要那次请求的用量不记。
- **中断只作用于指定的那一轮。** `turn/interrupt` 带会话 ID 和 turn ID，最多等 5 秒；只有它与正在运行的一轮一致时才取消，并返回 `interrupted: true`。会话进程不在运行时返回 `interrupted: false`。
- **异常消息直接断开连接。** 一行无法解析为 JSON，或连接上累积超过 8 MiB 仍没有换行时，桥接插件关闭这个连接。

## 运行时包

`management_portal/dsh_runtime/package.json` 与锁文件固定 DSH 的 npm 依赖：`@deepseek-ai/cordis` 4.0.4、`@deepseek-ai/dsh` 0.2.0-rc.2、`@deepseek-ai/dsh-llm` 0.2.0-rc.2。每次启动会话时，DshClient 从自身 Portal 源码复制 bridge.mjs 到会话配置目录；第三方依赖从运行时目录加载。`health` 报告运行时中实际安装的 `@deepseek-ai/dsh` 版本。运行时目录缺少 DSH 启动程序时，会话无法启动，错误提示安装经过验证的共享运行时。新版本的安装、验证和切换见 decision_e587fe1c472e。

## 相关实现与测试

- `management_portal/execution/provider_catalog.py`：DSH 的 Provider 定义（支持的操作、运行设置、默认路线、默认并发）。
- `management_portal/dsh_provider_catalog.py`：路线校验和模型目录。
- `management_portal/execution/account_adapters.py`：执行账号 `dsh_<路线 ID>`，即 `dsh_deepseek`。
- `management_portal/execution/native_options.py`：授权方式与运行设置的校验。
- `management_portal/execution/backends.py` 的 `DshBackend`（继承 `NativeSocketBackend`）与 `management_portal/execution/native_harness_tools.py`：执行、事件转换入口和 Portal 工具。
- `management_portal/dsh_client.py`：会话进程的启动、中断与释放。
- `management_portal/dsh_runtime/bridge.mjs`：桥接插件。
- `management_portal/dsh_subagent_cards.py`：子 Agent 卡片；`management_portal/execution/provider_turn_usage.py` 的 `read_dsh_turn_usage`：一轮的用量（含子 Agent）。

测试在 `tests/cases/test_dsh_backend.py`：各 Provider 走同一条执行管线、运行设置在适配器边界转换、无法执行的授权方式被拒绝、DSH 只声明已实现的操作、目录与账号不暴露密钥、子 Agent 工具随“允许开启 sub agent”加载与重启。`tests/cases/test_dsh_bridge_handover.py` 在模拟运行时上驱动真实桥接插件，包括子 Agent 事件与卡片；`tests/cases/test_provider_turn_usage.py` 覆盖子 Agent 用量。`test_live_dsh_postgres_result_recovery_and_followup` 在设置 `PORTAL_TEST_DSH_LIVE` 时用真实 DSH 验证结果恢复和后续一轮。
