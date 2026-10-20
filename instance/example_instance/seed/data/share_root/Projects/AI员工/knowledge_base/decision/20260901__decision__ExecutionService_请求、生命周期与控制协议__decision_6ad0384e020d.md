---
kb_id: decision_6ad0384e020d
type: decision
project: AI员工
created_at: '2026-09-01'
version:
  number: 39
  created_at: '2026-09-01'
  created_by:
    user: ''
    agent: ''
  updated_at: '2026-10-19T19:59:40Z'
  updated_by:
    user: tfi-exported-user
    agent: resident_865c3c24f251
importance: 10
metaness: 2
ai_generated: false
links:
  source:
  - ${code_root}/management_portal/execution/provider_failures.py
  - ${code_root}/management_portal/execution/diagnostics.py
  - ${code_root}/management_portal/execution/provider_catalog.py
  - ${code_root}/management_portal/execution/transport.py
  - ${code_root}/management_portal/execution/contracts.py
  - ${code_root}/management_portal/execution/scheduler.py
  - ${code_root}/management_portal/execution/service.py
  - ${code_root}/management_portal/execution/runtime.py
  - ${code_root}/tests/cases/test_execution_layer.py
  - ${code_root}/tests/cases/test_execution_context.py
  - ${code_root}/management_portal/execution/requests.py
  related:
  - decision_ae422b92c6f3
  - decision_f1331d57be89
  - decision_425e428410e0
  - decision_91eb3626d323
  - decision_d40f59f5508f
  - decision_25eb93d803a4
  - decision_b1ab45c4b3ec
  - decision_abaedeefca6f
  - decision_850e6248d680
  - decision_c47a13e8b962
  - decision_e30897dd3dc3
  - decision_4dea0db1f45a
  - decision_fc26f883773d
  - decision_075f66430606
  - decision_8ac0b5a6c0e5
  - decision_3e8c2a7f91d4
  supersedes: []
  derived_from: []
---
# ExecutionService 请求、生命周期与控制协议

本 Design 是 Portal 业务代码与 ExecutionService（统一执行模型请求的后台服务）之间的接入协议。它说明调用方怎样描述一次模型执行、怎样用稳定 ID 观察和控制它，以及执行工作进程（Worker）怎样在 Scheduler（持久调度器）授予的写入资格内推进执行。实现调用方或 Worker 时，可以只读本文，不需要了解某个模型服务供应方（Provider）的原生协议。

ExecutionService 的组件和恢复架构见 decision_25eb93d803a4；Scheduler 的排队、公平和租约规则见 decision_8ac0b5a6c0e5；执行账号的 `accounts.*` 接口见 decision_d40f59f5508f。本篇负责请求字段、执行状态、远程调用方法、返回形状、权限依据和错误含义。

## 先理解协议中的一次执行

一次执行由 `execution_id` 唯一标识。业务调用方提交请求后，Scheduler 把它保存为排队状态。某个 Worker 领取它时，Scheduler 发给这个 Worker 一份**领取资格**（claim）：Worker ID、一个随机生成的**领取令牌**（claim token）和一个到期时间（**租约**，lease）。Worker 只有在资格仍然有效时，才能开始模型调用、续租、上报事件、保存恢复点或提交终态。

**Provider 会话**是 Codex、Claude Code 等原生的 thread 或 session。Portal 用稳定的会话地址 `binding_id` 指向它，由 SessionManager 在执行时找到对应的 Provider 会话（decision_91eb3626d323）。**恢复点**（checkpoint）是 Scheduler 为运行中的执行保存的恢复位置。**AgentData** 保存输入、输出、工具调用和最终结果；Scheduler 保存队列、领取资格、控制消息和用量账本。两边用同一个 `execution_id` 对齐。

下图给出各方法发生的位置：

```text
业务调用方
  ├─ submit / status / wait / input / cancel / priority / acknowledge ...
  v
ExecutionService RPC
  v
Scheduler: queued ──claim──> claimed ──start──> running ──finish──> 终态
               ^                                  │
               │                                  ├─ heartbeat / control.wait / event
               │                                  └─ checkpoint / 用量增量
               └──── requeue / 租约恢复 / 计划交接
```

## Resident 执行与调度工作的边界

Resident 接纳输入时先在 AgentData 建立 preparing 的 Execution，生成唯一 execution_id，并在 execution_inputs 中记录本次接纳的输入。完成准备后提交给 Scheduler；Scheduler 与 AgentData 在同一 PostgreSQL 事务中推进排队、运行和终态。仅等待接纳的 Mailbox 消息没有 Execution。

下文 claimed 是 Scheduler 持有租约的状态，AgentData 对应的执行仍是 queued。AgentData 的长期状态集合为 preparing、queued、running、completed、failed、cancelled。准备阶段的失败或取消同样保留 Execution；执行协调工作及 Scheduler 队列可以在完成确认后清理。

一次提交的传输重试、Worker 恢复和计划交接使用同一个 execution_id；用户显式重试已经结束的输入创建新的 execution_id。多个输入可以属于同一次执行。Trigger Path 只在自己的关联表引用 Execution，Execution 请求和持久记录不携带 Trigger Path、旧 Run 或业务 job 身份。Provider 原生 turn 只用于适配器解释会话协议。

## 执行的状态

```text
queued ──claim──> claimed ──start──> running ──finish──> completed
   │                  │                 │                failed
   │                  │                 └──────────────> cancelled
   │                  └──── 恢复 / 计划交接 ────────────> queued
   └──── cancel ─────────────────────────────────────────> cancelled
```

- `queued`：请求已经持久保存，没有 Worker 持有它。
- `claimed`：某个 Worker 已经取得领取令牌和租约，还没有开始调用。
- `running`：同一个 Worker 已经调用 `start`，进入执行编排器（Runtime）。
- `completed`、`failed`、`cancelled`：唯一的三个终态，写入后不再改变。

**取消优先于恢复、延迟重试和交接。** 排队中的执行收到取消时，若 Provider 的原轮次仍可能运行，先进入待中断路径；其余直接变为 `cancelled`。已领取或运行中的执行收到取消时，Scheduler 先记下取消请求，再由 Worker 提交终态或由租约恢复收敛为 `cancelled`。持有它的 Worker 失联时，一轮对话或压缩先回到队列等新 Worker 中断 Provider，再写入 `cancelled`；这段时间快照的 `cancel_requested` 仍为真（decision_8ac0b5a6c0e5）。Scheduler 在释放领取资格的同一个事务里检查取消标志，发现已取消就直接写入取消结果，并把尚未投递的运行中输入标为失败。仍有有效 Worker 持有的执行，不会仅因为等待时间长被判为结束。

**计划内 Worker 交接保持执行为非终态。** 部署新代码时，旧 Worker 经过持久准备、解除对 Provider 当前轮次的跟随、释放资格，执行回到 `queued`，新 Worker 领取后继续同一个请求、会话和业务消息。这个过程不产生失败或取消终态，也不触发业务层的失败续跑。

## 执行请求（ExecutionRequest）

当前请求使用 `tfi.execution-request.v8`（`schema_version` 为 `8`）。原带 `provider_route_constraint` 的 v9 独立路线已移除，历史状态见 decision_6c2f91a7d84e，不再作为当前请求格式接受。`ExecutionRequest.new()` 自动生成 `execution-` 加 24 位小写十六进制的 ID；没有显式给出 `agent_id` 时，依次使用 `binding_id` 和新生成的执行 ID。

### 通用字段

| 字段 | 含义与限制 |
| --- | --- |
| `execution_id` | 必填，匹配 `^execution-[0-9a-f]{24}$`。同一次提交或运行恢复沿用原 ID；已结束后的显式重新执行分配新 ID。 |
| `actor` | 必填，提交者身份，去掉空白后非空，最长 120 字符。它是执行的所有者，控制类方法用它核对调用方。 |
| `agent_id` | 必填，执行者身份，最长 240 字符，不含 NUL。Resident 的执行使用它的 `resident_*` ID。 |
| `prompt` | 必填，去掉空白后非空。ExecutionService 不设置独立的字符数上限，保留调用方的完整输入；实际模型上下文容量由 Provider 与 Harness 处理，无法处理时返回真实执行错误，不静默截短。 |
| `service_type` | `agent_session`（持续会话）或 `chat_completion`（一次性生成）。 |
| `provider` | Provider ID：小写字母开头，最长 64 字符，可含数字、下划线和连字符。 |
| `operation` | 操作名，受服务类型和 Provider 的组合限制，见下表。 |
| `priority` | 默认 0，范围 -1,000,000 到 1,000,000；数值越大越先领取。 |
| `correlation_id` | 可空，最长 240 字符，用来和业务任务核对。 |
| `cwd` | 可空，最长 4,096 字符，Provider 的工作目录。 |
| `permission_profile` | 默认 `default`，最长 120 字符。 |
| `input_media` | 输入图片数组，字段必须存在，最多 20 项。 |
| `runtime_options` | JSON 对象，放 Provider 或 Runtime 的运行选项。 |
| `session` | 会话对象，见下文。 |
| `system_instructions` | 调用方已经生成好的系统指令及附加策略。 |
| `metadata` | JSON 对象，放业务归因和追踪信息，例如 `resident_id`、`business_type`。 |
| `schema`、`schema_version` | 当前请求格式与结构版本；完整格式名及结构版本均须与当前规范值精确一致。 |

请求在传输中必须携带 `identity` 对象（`tfi.operation-identity.v1`），记录这次执行的执行者、发起者和产生它的服务。它由 `agent_id` 和 `actor` 推导出来；缺少 `identity` 或它与这两个字段不一致时请求被拒绝。归属规则见 decision_ae422b92c6f3。

直接构造对象时 `input_media` 必须是元组，传输中的请求必须是数组；`runtime_options` 和 `metadata` 必须是对象。`session` 只接受 `mode`、`binding_id`、`backend_route_id`、`context_source_binding_id` 四个字段，出现其他字段即失败。

### 服务类型、Provider 与操作

每个持续会话 Provider 在 `execution/provider_catalog.py` 中声明它支持的操作：

| 服务类型 | Provider | 可用操作 |
| --- | --- | --- |
| `agent_session` | `codex` | `create`、`turn`、`fork`、`compact`、`release` |
| `agent_session` | `kimi` | `create`、`turn`、`fork`、`compact`、`release` |
| `agent_session` | `claude` | `turn`、`fork`、`compact`、`release` |
| `agent_session` | `dsh`（DeepSeek Harness） | `turn`、`compact`、`release` |
| `chat_completion` | LLM API Registry 中登记的接口 | `generate` |

组合在入队前校验，不支持的组合直接失败。Chat Completion 必须使用无状态会话。ExecutionService 只执行模型调用。

### 会话字段

- `mode` 默认 `stateless`（无状态），也可以是 `persistent`（持续）。持续会话必须提供 `binding_id`；无状态请求不能带 `binding_id` 和 `context_source_binding_id`。
- `binding_id` 与 `context_source_binding_id` 最长 192 字符，以字母或数字开头，后面可以有字母、数字、点、下划线、冒号和连字符。Resident 的会话地址形如 `resident:<resident_id>`。
- `create`、`compact`、`release` 必须使用持续会话。`fork` 必须声明一个持续的目标会话和唯一的 `context_source_binding_id`（上下文来源）；其他操作不能声明上下文来源。
- `backend_route_id` 指定执行账号的路由（decision_d40f59f5508f）。只有声明接受路由的 Provider 可以携带它，目前 Kimi Code 不接受；取值遵循 Provider ID 的字符规则，最长 64 字符。

SessionManager 在调用 Provider 前继续检查：会话地址是否存在、是否属于同一个执行者、Provider 是否一致。`fork` 只在目标会话还不存在时使用来源会话；恢复同一次执行时，如果目标已经建立，以目标为准。

### 系统指令

系统指令支持 `parts`：每部分带唯一的 `name`、非空 `content`、附加时机 `timing` 与放置位置 `placement`，总正文最长 500,000 字符。时机可为 `session_start`（首轮）、`every_turn`（每轮）、`session_start_and_after_compaction`（首轮及压缩后）；位置可为 `system`、`prompt_prefix` 或 `prompt_suffix`。调用方负责生成正文，执行层按附加记录及压缩代次选择本轮到期部分。现有单段 `content` / `injection_policy` 形式不能与 `parts` 同时使用。

Runtime 只在 `turn` 和 `generate` 上附加指令；会话操作不计为注入。恢复同一 Provider 轮次时不会重发。ExecutionService 把正文作为不透明文本传输，不解释其中的 Portal 角色。

### 输入图片

每项的 `media_type` 只能是 `image`，`source_type` 只能是 `local_path`（绝对路径）或 `url`（带主机名的 HTTP/HTTPS 地址）。`source` 非空、不含 NUL、最长 16,384 字符；`name` 最长 512 字符；`mime_type` 最长 255 字符，非空时必须以 `image/` 开头。不合法的图片在进入 Provider 前被拒绝。

### Chat Completion 的执行边界

独立 Chat Completion 继续使用普通 Execution 请求的 `chat_completion` 操作及其账号、权限和运行参数校验。模型 API 的登记与直连规则见 decision_6c2f91a7d84e；不再复制已移除的 v9 约束字段与专用路线规则。

## 运行中的控制对象

调用方可以向正在排队或运行的一轮对话发送两种控制。两者使用同一张持久表，控制 ID 都匹配 `^control-[0-9a-f]{24}$`（未指定时自动生成），`actor` 必填且最长 120 字符，`expected_turn_id` 可空、最长 512 字符。

**追加输入**（`tfi.execution-input-control.v1`）包含 1 到 16 项输入：

| 项 | 形式 |
| --- | --- |
| 文本 | `{type: "text", text}`，文本非空，所有文本合计最多 200,000 字符 |
| 本地图片 | `{type: "localImage", path}`，路径非空 |
| 远程图片 | `{type: "image", url}`，地址非空 |
| 回答 Provider 的提问或批准请求 | `{type: "user_input_response", ...}`，必须是唯一一项，并且必须指定 `expected_turn_id` |

还可以带 `source_segment_id`（以 `segment_` 开头、最长 160 字符），指明这段输入对应 AgentData 中的哪条原始消息。完整的图片安全规则由 Provider 传输层执行。

**本轮设置更新**（`tfi.execution-turn-settings-control.v1`）只允许修改 `service_tier`，取值为空字符串或 `priority`。Chat 页面切换 Speed 时用它让当前这一轮立即生效。

两种控制都只接受 `service_type=agent_session`、`operation=turn`、Provider 声明支持运行中输入的执行（Codex、Claude Code、Kimi Code、DSH；本轮设置更新只有 Codex 能投递，其他 Provider 报告失败），并且执行处于 `queued`、`claimed` 或 `running`。调用方给出 `expected_turn_id` 时，它必须等于恢复点记录的当前一轮；没有当前一轮时，控制绑定到空的轮次 ID。相同控制 ID 和相同内容重复提交返回已有记录；同一个 ID 用于不同内容时失败。

控制的状态是 `pending`（已持久排队）、`delivered`（Worker 已交给 Provider）或 `failed`。Scheduler 接受只表示已经排队；Worker 必须明确报告投递结果。追加输入投递成功后，服务同时把它写入 AgentData 的对话记录。持久回执保存控制 ID、执行、提交者、输入、预期轮次、状态、错误和时间。

## Worker 上报的对象

- **执行事件**：Worker 向 `scheduler.event` 提交事件种类和详情；Scheduler 生成执行内递增的 `sequence` 和时间。快照中的事件包含 `execution_id`、`sequence`、`type`、`created_at` 和 `details`。
- **输入接收身份（ProviderInputBinding）**：Provider 已经接收某次输入后的原生身份，字段为 `provider`、`turn_id`（必填，最长 512 字符）、`backend_session_id`、`backend_route_id` 和 `provider_message_id`（各最长 512 字符）。
- **执行结果（ExecutionResult）**：Worker 提交的终态，字段为 `execution_id`、`status`、`output`、`backend_session_id`、`backend_route_id`、`usage`、`error`、`error_code`、`error_details`。`status` 只能是三个终态之一，`error_code` 最长 120 字符。

## 业务调用方 RPC

### 服务与目录

- **`health`**：返回 `tfi.execution-service.v1` 健康对象，固定字段为 `ok`、`schema`、`instance`、`build_id`、`scheduler`、`notifications`、`agent_data`。只读，不触发租约回收、Worker 清理或 AgentData 状态修正。
- **`runtime.catalog`**：读取某个执行账号可用的模型、推理强度和服务等级。参数 `account_id` 或 `backend` 选定账号（都省略时用 Codex 的默认账号）；`include_backends=true` 时改为返回全部 Backend 及其账号路由（`tfi.agent-backends.v1`）。返回形状见 decision_425e428410e0。
- **`accounts.list`、`accounts.resolve`、`accounts.usage`**：执行账号目录、额度与历史用量，见 decision_d40f59f5508f。

### 提交与观察

- **`execution.submit`**：接收完整请求，先按执行 ID 比较首次提交的请求摘要；只有新执行才解析执行账号和准备会话，再持久入队。相同 `execution_id` 与相同请求返回已有记录；相同 ID 与不同请求被拒绝。
- **`execution.status`**：参数为 `execution_id`、可选 `actor`、非负 `after_sequence`（只返回这个序号之后的事件），以及 `include_events`、`include_result`。`actor` 非空时必须等于执行所有者，否则按“不存在”处理；可信内部调用方可以传空 `actor`。返回八个固定字段的快照；关闭事件时 `events` 为空数组，关闭结果时 `result` 为空对象。
- **`execution.wait`**：参数为 `execution_id`、必填 `actor`、事件游标 `after_sequence`、Scheduler 更新时间 `after_updated_at`、单段等待秒数（限制在 0.1 到 60 秒，默认 30），以及事件和结果开关。出现新事件、更新时间变化、进入终态或本段超时时返回快照。等待不续租，也不改变执行状态。服务端除了接收本进程的即时通知，还监听 Scheduler 数据库的变化通知，并每 5 秒复查一次，所以另一个进程（例如另一个部署）提交的终态也能及时唤醒等待者。facade 的分段、重连和取消行为见 decision_25eb93d803a4。
- **`execution.token_usage`**：读取小时用量账本，参数和返回见 decision_f1331d57be89。它不接收 `actor`，也不改变任何执行。

### 运行中的控制

- **`execution.input.append`**：接收完整的追加输入。必须显式携带 `schema=tfi.execution-input-control.v1`，缺少或版本不同直接失败。它为非终态的、支持运行中输入的一轮对话新增一条 `pending` 控制，并唤醒 Worker。权限在对应 ISA 入口由权限系统检查（decision_b820faf42218）；这里校验执行状态、Provider 能力与目标轮次。终态、不支持的 Provider、非 `turn` 操作或当前一轮已经改变都会失败。
- **`execution.turn_settings.update`**：接收本轮设置更新，规则同上。
- **`execution.input.get`**：参数为 `control_id` 和 `agent_id`，返回这条控制的持久回执。控制不存在时返回 `null`；控制存在但所属执行缺失或 `agent_id` 不符时以 `KeyError` 拒绝，不返回其他执行者的回执。重复读取不会重复投递。
- **`execution.cancel`**：参数为 `execution_id`、必填 `actor`、可选 `reason`、可选 `performed_by`（实际执行取消的人，默认等于 `actor`）和可选 `queued_only`。`actor` 必须等于执行所有者，`performed_by` 记录实际操作者。排队中的执行立即变为 `cancelled`；已领取或运行中的执行记下取消请求并通知 Worker。`queued_only=true` 时只取消排队中的执行，其他状态原样返回。重复取消保持已有的终态或标志。
- **`execution.priority`**：参数为 `execution_id`、必填 `actor`、新的 `priority` 和可选 `performed_by`（记录实际操作者）。只能修改排队中的执行；Scheduler 同时更新结构化优先级和持久请求，并追加 `priority_changed` 事件。
- **`execution.route.reassign`**：参数为 `execution_id`、必填 `actor`、目标 `backend_route_id` 和可选 `performed_by`。Scheduler 只允许改动排队中的持续会话执行；调用方负责确认该请求尚未使用 Provider 会话，执行账号的适配器同时改写路由和相关运行选项，执行 ID 保持不变，并追加 `route_changed` 事件。Resident 切换账号时用它移动还没开始的排队工作。
- **`execution.acknowledge`**：参数为 `execution_id` 和必填 `actor`。只有所有者的终态执行可以确认，非终态确认失败。确认后 Scheduler 清空保存的请求和恢复点，保留完整的执行结果，让重连或同时观察的一方仍能读到结果。确认可以重复，不改变结果；确认后超过 24 小时的记录被清理。

### 输入是否已被接收

调用方在自己的进程内读取 AgentData 中的执行和 Provider 轮次身份，按执行 ID 与 `agent_id` 精确匹配。排队状态、会话存在或另一次执行的轮次都不能证明本次输入已送达。具体数据接口见 decision_91eb3626d323。

### 会话地址

会话地址的读取与改动不经过 ExecutionService RPC：SocietyService 在自己的进程内直接调用 SessionManager，接口与规则见 decision_91eb3626d323。

## Worker 与 Scheduler RPC

### Worker 登记与代际切换

- **`scheduler.worker.register`**：提交 `worker_id`、`build_id`、非空能力列表、进程 ID 和初始状态（`standby`、`active`、`draining` 或 `offline`；必须显式提供）。同一 Worker ID 不能改绑到另一个构建；已经是 `draining` 或 `offline` 的 Worker 不能通过重新登记回到 `standby` 或 `active`。
- **`scheduler.worker.activate`**：激活目标 Worker，可以声明 `previous_worker_id`。目标必须是 `standby` 或 `active`；Scheduler 在一个事务里把前任和其他 `active` Worker 改为 `draining`，再把目标改为 `active`，并唤醒领取和控制等待。
- **`scheduler.worker.state`**：只能把 Worker 改为 `draining` 或 `offline`，不能用来激活。
- **`scheduler.worker.handover`**：只接受 `draining` 或 `offline` 的 Worker，在旧进程退出后处理它遗留的领取资格。已领取未开始的执行回到队列；运行中的执行只有恢复点标记可恢复时才回到队列，并保留计划交接信息。返回回到队列和无法移交的执行 ID，随后把 Worker 置为 `offline`。
- **`scheduler.worker.unregister`**：只有 `offline` 且没有已领取或运行中工作的 Worker 可以删除；不存在时返回 `removed=false`。

### 领取、开始和续租

- **`scheduler.claim.wait`**：按 Worker 状态、能力、延迟时间、同一会话串行、账号额度保护、容量、派发间隔、优先级和提交者公平性领取执行。等待时间限制在 0.1 到 30 秒，默认 15 秒；租约默认 90 秒，最短 15 秒。没有可领取执行时返回空结果和原因：`worker_not_registered`、`worker_not_active`、`quota_reserve`、`capacity`、`dispatch_interval`、`deferred` 或 `empty`。
- **`scheduler.start`**：当前 Worker 用领取令牌把 `claimed` 原子地改为 `running`。令牌已经失效的 Worker 收到“领取资格已变化”。
- **`scheduler.heartbeat`**：当前持有者延长 Worker 和这次执行的租约，返回是否已请求取消（`cancel_requested`）。运行中输入和交接指示通过 `scheduler.control.wait` 获取。

### 计划内交接准备

**`scheduler.handover.prepare`** 接收 `execution_id`、`worker_id`、`claim_token`，要求调用方仍是这次已领取或运行中执行的有效持有者，否则按资格错误拒绝。准备不释放资格，也不把执行改为终态。

| 情况 | 返回与持久效果 |
| --- | --- |
| 已请求取消 | `ready=false`、`cancel_requested=true`，按用户取消处理 |
| 恢复点不可恢复 | `ready=false`、`cancel_requested=false`，不猜测 Provider 是否已经接收，不安排 `continue` |
| 恢复点可恢复 | 先保存交接恢复点，再返回 `ready=true`、`cancel_requested=false` 和 `interrupt_required` |

Provider 声明 `handover_keeps_turn` 的 turn 操作保持原生轮次运行，`interrupt_required=false`；其余已有轮次且尚未完成的操作按能力决定中断。交接恢复点记下原 Worker、这一轮的 ID 和原生会话 ID，恢复点发生变化时追加 `handover_prepared` 事件；重复准备不会产生第二个执行。

Worker 得到 `ready=true` 后按 `interrupt_required` 解除跟随或中断，等旧跟随和相关控制调用都返回后，逐项调用 `scheduler.requeue` 释放资格。Provider 已经完成时，直接用恢复点补交结果，不发多余的中断或 `continue`。准备和释放各自检查取消，两者之间到达的停止请求不会被覆盖。

### 可恢复控制与运行中输入

- **`scheduler.requeue`**：当前持有者把恢复点已标记可恢复的执行放回队列。它释放资格、保留恢复点，`retry_after_seconds` 限制在 0 到 3,600 秒。恢复点不可恢复时失败。
- **`scheduler.control.wait`**：当前持有者长轮询控制信息，等待时间限制在 0.1 到 60 秒，默认 30。返回 `cancel_requested`、`handover_requested`，以及待投递的 `turn_controls`（追加输入和本轮设置更新）。处于 `draining` 的 Worker 即使没有待投递输入也会收到交接指示；待投递的控制不交给即将退休的 Worker，由新持有者恢复后按原来的轮次规则处理。
- **`scheduler.input.complete`**：当前持有者把一条待投递控制标为 `delivered` 或 `failed`（默认错误为 `delivery_failed`）。重复完成保持已有状态。

控制长轮询和输入完成等待同样监听数据库变化通知并每 5 秒复查，另一个部署提交的取消、交接和追加输入不会等到长轮询超时才被发现。复查不改变资格、租约、投递或终态的判断规则。

### 事件、恢复点与容量

- **`scheduler.event`**：当前持有者为执行追加事件。普通事件得到严格递增的序号，并追加到这次执行的活动日志；`provider_event` 先递归脱敏。特殊类型 `token_usage_delta` 改为记入用量账本，`details` 中必须有稳定的 `event_id`（decision_f1331d57be89）。
- **`scheduler.checkpoint`**：当前持有者写入非空恢复点，Scheduler 同时追加 `checkpointed` 事件，记录 Provider、会话、轮次和是否可恢复。旧持有者迟到的恢复点和新持有者对原一轮的恢复都保留交接标记，Provider 接收新的一轮后才清除。Provider 已完成的恢复点允许新 Worker 跳过重复调用。
- **`scheduler.capacity.reassign`**：新会话在 Provider 故障切换确定实际路由后，调整执行占用的容量键。只有当前持有者、且会话还没有被固定到原路由时有效。
- **`scheduler.capacity.configure`**、**`scheduler.quota_overrides.configure`**：账号调度设置与 binding 例外，见 decision_d40f59f5508f。
- **`scheduler.quota_overrides.forget`**：按 `binding_ids` 删除已移除执行者的额度例外，重复删除无副作用。

### `scheduler.finish`

当前持有者提交执行结果。结果中的 `execution_id` 必须匹配，`status` 只能是三个终态之一。已经请求取消时，Scheduler 强制保存为 `cancelled`。同一个事务里保存结果、错误和剩余用量，清除持有者、令牌和租约，把未投递的运行中输入标为失败，并唤醒等待者。

## 快照、排队原因与事件游标

快照的 `scheduler` 字段包含执行能力、优先级、关联 ID、状态、持有者 Worker、租约截止时间、取消标志、尝试次数、确认时间、创建与更新时间。排队中的执行还可能包含：

| 字段 | 含义 |
| --- | --- |
| `queue_reason` | `ready`（可以领取）、`quota_reserve`（账号额度保护中）、`deferred`（延迟时间未到）、`capacity`（账号并发已满）、`dispatch_interval`（离上次派发太近）或 `session_binding`（同一会话已有执行在运行） |
| `queue_position` | 在当前可比较队列中的位置 |
| `capacity_key`、`capacity_limit`、`active_capacity_count` | 所在容量分区、并发上限和当前占用 |
| `last_dispatched_at`、`next_dispatch_at` | 分区最近一次和下一次允许派发的时间 |
| `dispatch_interval_seconds`、`dispatch_interval_remaining_seconds` | 本次抽到的派发间隔和剩余秒数 |
| `retry_after_remaining_seconds` | 延迟恢复的剩余秒数 |

事件序号以微秒级时间戳为起点，在单个执行内严格递增。首次 `started` 事件与状态一起保存，跨实例和重启后仍可返回。其余事件由每个服务进程最多为一个执行保留最近 256 条；该窗口在服务替换后不会恢复。调用方看到序号缺口时以快照的当前状态为准；这个窗口不是完整的审计日志，完整内容在 AgentData 中。

## 执行操作与输入关联的持久观察

Execution 的完成状态、请求的操作类型和实际接纳的输入是不同事实。持续会话的创建、派生、压缩和释放可以没有业务输入；缺少输入或最终消息本身不能用于推断操作类型，更不能证明 Provider 是否执行了未观察到的动作。

经校验的执行操作与服务类型属于这次 Execution 的长期来源信息。Scheduler 在 queued 提交事务中向同一 AgentData owner 的 `agent_operations` 写入不可变 `execution_submission` 事实，只保留 `service_type`、`operation`、原请求摘要和提交时间；它与 execution_id 绑定，不依赖尚未清理的请求正文、进程内事件窗口或当前 Resident 配置。输入关联沿用 `execution_inputs` 的实际接纳关系，保留消息身份、角色与接纳时间，不复制输入文本，也不把准备请求 ID 或最终回复 ID 当成已接纳输入。

正式长期读取使用 `execution.record`（客户端 `ExecutionService.record(execution_id, actor=...)`），`actor` 必须匹配执行的提交者。它返回长期 Execution 记录及 `service_type`、`operation`、`submitted_at` 和 `inputs[{message_id,role,accepted_at}]`。缺少提交事实时，前三项均为 null；准备阶段可以已经接纳输入但尚未形成提交事实。缺失或提交者不匹配的执行按既有隐藏规则拒绝，不返回他人的记录。Scheduler 终态工作被回收后，这个读取仍从 AgentData 取长期事实，不能用已清理的临时请求代替它。

确认完成和清理调度工作不得删除这组非正文来源信息。prompt、system instructions、原请求 metadata、图片与恢复点仍遵循原有清理规则，不为可观察性永久保存完整请求。重启及调度终态记录回收后，正式长期记录仍能回答“提交了什么操作、关联哪些实际输入”。

历史记录在这些事实已经丢失时明确表达未知，不能从 capability、空输入、状态或时间相近的另一执行猜测回填。新事实必须来自受校验的原执行请求或实际输入接纳；同一 execution_id 的来源冲突明确拒绝。

幂等重试仅沿仍可核对的原提交回执返回原结果。长期记录已有提交事实、而 Scheduler 回执已回收时，不能以缺少临时记录为由重新执行相同 execution_id；调用方改用 `execution.record` 观察长期事实。历史执行已结束但提交事实未曾保存时，同样不能把缺失当作重新准入许可。用户明确重新执行时使用新的 execution_id。

## 恢复、确认和保留

只有能证明可以恢复的执行会回到队列：已领取但还没调用 Provider，或者运行中且恢复点可恢复。Provider 已完成的恢复点保存输出、用量、路由和工具名，新 Worker 可以跳过 Provider 调用，继续写 AgentData 和会话地址。Provider 可能已经产生外部动作、又无法证明可以恢复时，执行明确失败。

维护循环清理已确认且超过 24 小时的终态记录，以及超过 7 天的未确认终态。调用方确认与 Worker 提交终态不触发全表清理。

## 权限依据与错误

业务控制和 Worker 写入使用两套依据。`status`、`wait`、`input`、`cancel`、`priority`、`route.reassign`、`acknowledge` 比较请求中的 `actor` 与执行所有者；`performed_by` 记录实际操作者。Worker 方法依据 Worker 状态、Worker ID、领取令牌和租约。`actor` 不能代替领取令牌，领取令牌也不授予业务所有者的权限。

这组 RPC 运行在权限为 `0660` 的同机 Unix socket 上，请求本身不带凭据。`actor` 由可信的 Portal 内部调用方提供；能连接 socket 的本地进程可以伪造它。

| 情况 | 客户端收到的异常 |
| --- | --- |
| 请求模型或参数错误、状态不允许的修改 | `ValueError` |
| 执行不存在，或需要隐藏的所有者不匹配 | `KeyError` |
| 取消时所有者不匹配 | `PermissionError` |
| 领取资格已经属于别人 | `ExecutionClaimOwnershipLost` |
| socket 尚未连接成功 | `ExecutionServiceUnavailable` |
| 连接已接受请求，但连接中断、空响应、非法 JSON 或响应 ID 不匹配，无法确认结果 | `ExecutionResponseUnknown` |
| 其他远端错误（例如确认非终态执行） | `RuntimeError` |

只读的 `wait` 在两类连接中断后继续观察原 Execution，并保留事件游标；它们不构成 Provider 失败。`submit` 使用同一个 Execution ID 幂等提交，因此回执未知时也可重试同一请求。其他写操作由其业务 owner 根据幂等身份或状态回读确认结果，传输层不自动重放结果未知的写入。业务 Job 更换 Execution 时，清除上一执行的确认标记与事件游标；继续观察同一 Execution 时保留它们。

## Provider 失败的表达

- **Provider 明确报告当前这一轮不再重试的错误时，这个错误就是失败依据。** 之后到达的空完成或系统错误不能把它改写成“缺少最终回答”，也不能触发无进展恢复。Provider 声明仍会重试的通知不会提前结束执行；其他轮次的错误不影响当前执行。
- **额度用尽统一表达为 `error_code=provider_usage_limit`，并带 `error_details.retryable=false`。** 即使上游给出的错误码未知，只要结构化错误消息表明额度用尽（例如 usage limit、insufficient_quota），也归为这个错误码，并保留原始说明。预付费账号余额不足同样归为额度用尽：Kimi 的 “credit balance … please recharge”（它以 HTTP 429 返回）、Anthropic 的 “credit balance is too low” 和 DeepSeek 的 “Insufficient Balance” 都不当作速率限制；不带这些说明的普通 429 仍是速率限制。上层遵守“不可自动重试”，不通过发送新的输入反复消耗恢复次数。识别规则集中在 `execution/provider_failures.py`。


## 批量控制状态读取

`execution.states` 接受至多 500 个 `execution_id` 到提交 `actor` 的映射，只为匹配提交者的执行返回状态。返回 `execution_id`、`status` 和 `scheduler.cancel_requested`；排队执行还返回 `queue_reason`。缺失或提交者不匹配的执行不返回。服务批量读取容量、会话绑定、额度和发送间隔，不加载提示词、结果正文或事件，也不计算完整队列排名。

输入控制与设置控制共用持久提交路径，统一校验幂等内容、支持能力、执行状态和精确轮次。状态批量查询与领取复用同一准入判断；完整快照另行计算队列位置。长期模型输入输出以 AgentData 为准，调度器清理不删除模型历史。

旧 Worker 已中断的轮次也使用同一份无进展恢复预算。原生快照不能确认工具交付时，不发送继续输入，也不重做工具；每次延后计入持久预算，重新领取队列和回放已有结果不能重置次数。连续三次无新工作后明确报告恢复耗尽，保留原 Execution 与工具证据。

格式名与版本须精确匹配当前规范值，校验规则见 decision_86aed2f0e8c7；本篇同时校验各字段和结构版本，不接受历史前缀或其他版本。
