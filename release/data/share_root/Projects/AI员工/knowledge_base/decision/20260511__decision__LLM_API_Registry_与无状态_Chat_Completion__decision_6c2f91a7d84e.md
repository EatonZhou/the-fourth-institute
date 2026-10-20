---
kb_id: decision_6c2f91a7d84e
type: decision
project: AI员工
created_at: '2026-05-11'
version:
  number: 19
  created_at: '2026-05-11'
  created_by:
    user: ''
    agent: ''
  updated_at: '2026-10-19T19:59:40Z'
  updated_by:
    user: tfi-exported-user
    agent: resident_865c3c24f251
importance: 8
metaness: 2
ai_generated: false
links:
  source:
  - ${code_root}/tests/cases/test_provider_transport_diagnostics.py
  - ${code_root}/management_portal/provider_transport_diagnostics.py
  - ${code_root}/management_portal/execution/requests.py
  - ${code_root}/management_portal/execution/isa.py
  - ${code_root}/management_portal/ai_provider_registry.py
  - ${code_root}/management_portal/model_api_store.py
  - ${code_root}/tests/cases/test_model_api_registry_postgres.py
  - ${code_root}/management_portal/execution/backends.py
  - ${code_root}/management_portal/execution/contracts.py
  - ${code_root}/tests/cases/test_ai_provider_registry.py
  - ${code_root}/tests/cases/test_execution_layer.py
  related:
  - decision_6b9e42f1c8a7
  - decision_3948ce9b9443
  - decision_725dedbe89b2
  - decision_ef1d6c925955
  - decision_c604274e21dd
  - decision_4527448ad07f
  - decision_6ad0384e020d
  - decision_5ff0cb51ed84
  - decision_7956d7cb4bde
  supersedes: []
  derived_from: []
---
# LLM API Registry 与直连 Model API Backend

Portal 的 Resident 大多通过有状态的 Provider 运行时工作，例如 Codex App Server、Claude Code 和 Kimi Code，它们保存会话、调用工具并持续产出事件。除此之外，Portal 还需要直接调用普通的模型 HTTP 接口：一次发出一组消息，拿回一段完整回答，不建立会话。论文评审、局域网设备转发、`provider_api` 类型的 Resident 和 `CompleteChat` 指令都属于这种用法。

本 Design 说明两件事：Portal 在哪里登记这些模型接口（LLM API Registry），以及 ExecutionService 怎样用登记的接口完成一次无状态生成（Chat Completion）。有状态 Provider 的会话协议见各自的 Design；局域网设备通过 Relay 调用本服务时的附加限制见 decision_c604274e21dd。

## 基本概念

- **模型接口（LLM API）**：通过 HTTP 接收消息、返回模型生成内容的外部服务，例如 DeepSeek、Moonshot（Kimi）、OpenRouter、Google Gemini 的 API。
- **登记（Registry）**：PostgreSQL 表 `model_apis.apis`，列出 Portal 可以使用的模型接口。`model_api_store` 负责读写这张表，源码中的 `AIProviderRegistry` 负责读取条目并按条目发送请求。
- **条目（entry）**：登记表中的一行，用唯一的 `id`（下文称条目 ID，对应列 `api_id`）标识。条目保存调用所需的非密钥信息，并用文件路径引用密钥：

  | 字段 | 含义 |
  | --- | --- |
  | `id` | 条目 ID，调用方用它选择接口 |
  | `kind` | 接口协议，见下表 |
  | `provider` | 服务商名称，例如 `deepseek`、`moonshot`、`openrouter`、`google`、`anthropic` |
  | `enabled` | 是否可用，缺省为启用；只有显式 `false` 才停用，所有读取方使用同一规则 |
  | `base_url` | 接口根地址 |
  | `api_key_file` | 密钥文件的绝对路径。登记只保存路径，密钥内容只在发送请求时从文件读出 |
  | `default_model`、`models` | 默认模型和模型对象列表；每个对象含 `id`、可选的 `enabled` 与供原生后端使用的 `supported_efforts`；直连 API 可用 `supported_parameters` 声明参数名、用 `reasoning_efforts` 声明支持的推理强度。未停用对象的 `id` 构成可用模型目录 |
  | `timeout_s`、`max_retries` | 默认超时秒数与失败后的重试次数 |
  | `default_headers` | 附加的请求头，不能包含认证信息 |
  | `default_parameters` | 协议允许的默认生成参数 |

- **接口协议（`kind`）**：说明条目按哪种协议调用。当前使用三种：

  | `kind` | 含义 | 使用者 |
  | --- | --- | --- |
  | `openai_compatible_chat` | 采用 OpenAI 兼容的 `chat/completions` 请求格式。“兼容”只描述请求格式，服务商可以是任何公司 | Chat Completion、Relay、Paper 与 Repo 的模型调用、Kimi Code 与 Claude Code 的兼容路线、DSH |
  | `claude_code_subscription` | Claude 订阅登录。条目只保存 Claude CLI 的配置目录 `config_dir`，不保存地址和密钥 | Claude Code Provider（decision_3948ce9b9443） |
  | `typesafe_system_one` | Jev 接收 state 与 questions，返回结构化判定的专用协议 | Jev Provider、Model API Resident |

- **Chat Completion**：一次以消息列表为输入、以完整回答为输出的无状态生成。它由 ExecutionService 中的 `ChatCompletionService` 执行，是统一执行层的两种服务之一（另一种是有状态的 `agent_session`，见 decision_6ad0384e020d）。
- **执行结果（ExecutionResult）**：统一执行层返回给调用方的结果对象，包含最终文本、用量（usage）、输出消息和实际使用的条目 ID。

## 设计点

### 登记表

- **每个条目是一行，每个字段是一列。** 表 `model_apis.apis` 以 `api_id`（主键，非空）标识条目，其余字段各存为同名的具名列：`models`、`default_headers`、`default_parameters` 使用 JSONB，其他字段使用文本、布尔或数值列。另有 `position`、`version`、`updated_at` 和 `updated_by` 四列。写入包含未登记字段的条目时，`model_api_store` 拒绝整次写入。表中没有行时，Portal 视为没有登记任何接口。
- **条目按登记顺序排列。** 新条目的 `position` 排在已有条目之后。Relay 按 `provider` 选择接口，优先取 ID 为 `<provider>_primary` 的条目，否则取第一个符合条件的条目，因此顺序会影响 Relay 的选择结果。
- **执行后端的服务路线只用显式绑定。** 表 `model_apis.routes` 以 (`backend`, `route_id`) 为主键，用 `api_id` 外键指向一个条目；删除条目时，它的绑定一并删除。当前只有两条服务路线：DSH 的 (`dsh`, `deepseek`) 要求 `provider` 为 `deepseek`，OpenCode 的 (`opencode`, `openrouter`) 要求 `provider` 为 `openrouter`，两者都要求 `kind` 为 `openai_compatible_chat`。同一路线只能绑定一个条目；要换绑，先解除原有绑定。DSH 与 OpenCode 先按路线查到 `api_id`，再从 `AIProviderRegistry` 读取该条目；没有绑定或条目已停用时直接报告配置缺失，不按登记顺序改用其他条目。
- **所有使用方都通过 `AIProviderRegistry` 读取登记。** Relay、Paper、Repo Workspace、Claude Code、Kimi Code、DSH、JEV、账号余额采集和 ExecutionService 都读取它返回的条目，不各自查询表。每个使用方再按自己需要检查 `kind` 和 `provider`，例如 Kimi Code 只接受 `openai_compatible_chat` 条目。Claude Code 与 Kimi Code 守护进程从各自 unit 的 `EnvironmentFile` 取得数据库地址；Telepus Breakglass Worker 不导入 Portal 代码，直接只读查询 `deepseek_primary` 一行。
- **条目只引用密钥文件，不保存密钥。** 所有读取方使用 Registry 的同一函数读取 `api_key_file`，去掉首尾空白；文件缺失、读取失败或内容为空时请求失败。密钥不出现在任何返回值、执行结果、日志或传输诊断中。

普通 HTTP 请求使用去除末尾斜线的 `base_url`。Claude 与 DSH 的 Anthropic 路线共用转换：保留已有 `/anthropic`，否则去掉末尾 `/v1` 后追加 `/anthropic`。不再从 `api_key_env` 读取密钥。

Jev 每次判定只读取一次条目，以该条目校验模型并发送请求；重试复用同一配置，遵循条目的 `max_retries`，只重试传输故障、429 和 5xx。

### 管理登记

- **登记的读取和修改使用 HATG ISA 指令。** `ListModelApis`、`CreateModelApi`、`UpdateModelApi`、`DeleteModelApi` 由 ExecutionService 执行（decision_4527448ad07f）。
  - `CreateModelApi` 新建条目缺省写入 `enabled: true`，协议固定为 `openai_compatible_chat`；条目 ID 已存在时返回冲突。
  - 创建参数为 `api_id`、`base_url`、`default_model`、`secret_file`，以及可选的 `provider`、`routes`、`default_parameters`、`timeout_seconds`、`retries`、`headers`、`enabled`。`routes` 是服务路线绑定数组，每项包含 `backend` 和 `route_id`；更新指令的 `changes` 提交 `routes` 时整体替换该条目的绑定，未提交时保留原有绑定，并按修改后的条目重新检查这些绑定是否仍然成立。`provider` 供 Relay 等入口选择服务商。`secret_file` 写入 `api_key_file`，`timeout_seconds` 写入 `timeout_s`，`retries` 写入 `max_retries`，`headers` 写入 `default_headers`。更新指令的 `changes` 还可修改 `label`、`kind` 和 `models`；未知字段拒绝。
  - `secret_file` 必须是绝对路径；`headers` 必须是映射，并且不能包含名字里带 `authorization` 的请求头。
  - `UpdateModelApi` 每次成功后把条目的 `version` 加一。调用方可以对 `UpdateModelApi` 和 `DeleteModelApi` 提交 `expected_version`，内核按 `api_id` 取该条目的当前版本比较，不一致时返回冲突、不写入。
  - 每次修改在一个数据库事务中只改动目标行，并在该行记录 `updated_at` 与 `updated_by`（执行修改的调用者）。
  - `ListModelApis` 返回每个条目的公开字段和版本，其中 `secret_file` 只是路径；已绑定服务路线的条目还返回 `routes`。

### 一次 Chat Completion

- **调用方必须指定条目 ID。** 条目不存在或 `enabled: false` 时，请求以“接口不可用”失败，服务不会改用其他接口。
- **Chat Completion 只做无状态生成。** 请求的服务类型为 `chat_completion`、操作为 `generate`、会话模式为 `stateless`。它不创建 Provider 会话，也不能中断 Provider 一侧的生成。发出请求前和每次重试前都检查取消；请求发出后在自己的线程里等待回答，这时收到停止，Execution 立即以中断结束，不再等回答。那次请求在自己的超时内结束，之后返回的回答和传输记录都被丢弃；Provider 仍可能对这次回答计费，这部分用量不进入记账。
- **执行服务按 Registry 的协议选择调用方式，不使用流式返回。** `openai_compatible_chat` 调用 `<base_url>/chat/completions`；`typesafe_system_one` 复用 Jev Provider 调用 `<base_url>/systemone`。显式登记为其他协议的条目在发送前失败。
- **消息按固定顺序组合。** 需要附加 Portal 系统指令时（decision_ef1d6c925955），它放在第一条；本次运行的业务系统提示（`system_prompt`）紧随其后；然后是调用方提供的业务消息。最后一条业务消息为 `user` 时，用完成注入后的本轮输入替换其正文；否则在末尾追加这条输入。之前的业务消息保持顺序。因此 prompt 位置的运行身份、授权上下文和 Skill 清单都实际进入最后一条用户消息。
- **Chat Completion 回答取自第一个候选。** 服务从响应的 `choices[0].message.content` 提取文本；文本为空时请求失败。Jev 返回校验后的结构化 answers，规则见下节。
- **成功后返回完整结果。** 执行结果包含回答文本、服务类型、条目 ID 和完整的原始响应；用量在响应的 `usage` 之外补充实际使用的 `api_id` 与 `model`。服务同时发出一条标记为最终输出的事件。

### Jev 直连判定

Model API（`provider_api`）的可选目录包括已启用的 `openai_compatible_chat` 与 `typesafe_system_one` 条目，订阅登录和未实现的协议不进入目录。账号可同时提供 Native Agent 与直连 API 路线；两者继续共用同一个 Execution account、并发上限与记账身份。目录返回 `api_protocol`，模型来自该 API 条目的启用模型列表。

Jev 的问题定义存放在 Resident 运行设置的 `backend_options.parameters.questions`。键是问题 ID，每个问题使用 `choice`、`score` 或 `noul`，带 `instructions` 和该题型要求的 `criteria`。配置保存复用 Jev Provider 的题型校验；选择 Jev 后可以先保存模型，未配置问题的实际调用会在发送 HTTP 前给出明确错误。Jev 不接受 temperature、max_tokens 等 Chat Completion 生成参数；Chat Completion 路线不接受 Jev questions。

例如通过 `UpdateResidentSettings` 的 `changes` 提交以下配置，并在调用顶层指定目标 `resident_id`：

```json
{
  "model": "jev-latest",
  "backend_options": {
    "parameters": {
      "questions": {
        "ready": {"type": "noul", "instructions": "根据本轮输入判断工作是否已经完成。"}
      }
    },
    "timeout_seconds": 30,
    "max_retries": 0
  }
}
```

每轮仍由上游生成 Portal 身份与业务上下文。执行服务把与 Chat Completion 相同顺序的完整消息列表放入 `state.messages`，将已配置的问题放入 `questions`，发送 `{model, state, questions}`；不从自然语言中猜问题，不调用工具，也不创建 Agent loop。响应由共享 Jev Provider 校验各题型的答案、概率、分数及 usage。Execution 输出的 `structured` 保存 answers，`response` 保留原始响应，`message` 用 JSON 代码块显示答案；usage 补上 API 条目与所选模型，进入已有记账流程。

这条路线复用 Execution 的取消等待与传输诊断。已发送的 HTTP 请求可能继续完成，但取消后不再输出或记录迟到结果，也不再发起重试。运行设置中的 `max_retries` 覆盖 Registry 默认值，包括显式 0；Jev 原有工具调用未显式传入该值时仍使用 Registry 默认重试。每次判定用同一份配置校验模型和执行各次 HTTP 尝试。`CompleteChat` ISA 与 Relay 仍只承担文本生成，Jev 直连通过 Model API Resident 的上述配置执行。

### Resident Settings 与协议能力

Model API Resident 的调用行为统一放在 **Menu → Settings**。API 账号和模型、上下文、协议参数、超时与重试各有一个入口，使用同一份运行设置。Harness 负责跨后端的执行增强，只展示该后端支持的能力；历史开关不在 Harness 中重复保存。

协议适配器分别拥有参数兼容性、请求编码、响应解码和可配置能力。Chat Completion 适配器生成文本，Jev 适配器执行结构化判定；两者都属于 `provider_api`，不新增 Agent loop。Execution 继续负责调度、取消等待、最终输出、用量与传输诊断。

运行目录返回 `api_protocol`、当前协议的 `api_settings` 和可选择的 `api_routes`。Settings 按当前账号及模型的声明展示控件：Chat Completion 默认提供通用的 temperature、top_p 和 max_tokens；模型显式声明 `supported_parameters` 时按声明限制参数；推理强度只有模型声明 `reasoning_efforts` 时才提供选项。Jev 隐藏聊天生成参数，展示问题列表：问题 ID、Choice／Score／Noul 类型、文本或 JSON 判定指令、对应类型的标准。界面保留结构化指令，服务端复用 Jev Provider 校验，不从自然语言猜测问题。

API 账号身份只存放在 Resident 的 `backend_route_id`。`UpdateResidentSettings` 可以将 `changes.backend_route_id` 与模型、上下文、协议参数一起提交：先校验目标账号、模型及参数的兼容性，再在同一事务中保存账号和运行设置。校验或保存失败不产生部分更新；进行中的请求保留已构造的配置，新设置从下一轮生效。原生后端的账号切换继续使用 Session 迁移流程。

### 原生 Backend 与 Model API 之间迁移

任意受支持的 Resident 都可以从 **Menu → Settings → Backend** 打开统一迁移面板，选择原生 Agent Provider 或 Model API。目录、账号路线及目标模型能力由服务端返回；目标模型查询使用选中的路线，不受来源 Resident 的账号覆盖。API 路线 ID 与账单账号可能不同，界面提交所选 API 登记 ID。

原生 Backend → Model API 通过 `RestartSession` 提交目标 API 账号、模型、`prompt_context` 和 `backend_options`。服务端先按目标协议验证整份配置，切换时解除当前原生 Session 绑定，保留旧会话供审计；无需初始化请求，也不增加 Agent loop。保存失败时恢复旧绑定与配置；绑定已被其他操作改变时不覆盖它。切换为文本 API 默认携带历史，切换为 Jev 默认关闭历史，均可在面板中修改。同一 API 路线迁移保留已有设置。

Model API → 原生 Backend 按所选上下文方案初始化新 Session；新 Session 就绪后才提交 Backend、账号和运行配置，失败或取消继续保留 Model API 配置。两个方向都复用等待本轮完成、取消、幂等受理和接单恢复流程。选择 Model API 时隐藏原生推理强度和 Fork 能力，展示目标协议的上下文、参数、超时及重试设置；目标目录读取失败时禁止提交。

目标为 Model API 时，非 `fresh` 方案按历史开关和预算选择规范会话记录，不额外调用模型生成交接摘要；`native` 不可用。`fresh` 保留页面对话并写入模型上下文重置边界，后续 API 调用只选取边界之后的历史。

### 会话历史的选择与边界

上下文配置为 `prompt_context.history = {enabled, max_tokens}`。新建文本类 Model API Resident 默认 `enabled: true`，可以关闭；新建 Jev Resident 默认关闭，可显式开启。已有 Resident 缺少该字段时继续按关闭处理，读取或保存其他设置不会自动替它开启历史。历史预算默认 8000，允许 256–200000 的整数。

模型 API 自身仍是无状态请求。Portal 在每次请求之前，从当前 Resident 的规范对话记录组装输入：固定系统指令、预设消息、预算内最近的完整问答，最后放当前输入一次。关闭历史后仍发送固定指令、预设消息和当前输入。独立调用沿用 fresh-context 隔离，既不带历史也不带会话预设消息。

历史只选择当前输入之前、最近一次清空对话或模型上下文重置之后、已经完成的用户输入及其最终回复。回复必须属于该输入的完成记录；失败、排队中、未完成或带独立调用授权范围的输入不参与。当前输入和后来到达的输入均不能成为本次历史，重试也不重复追加它们。历史查询最多读取最近 200 条候选消息，按完整问答对从新到旧纳入预算，遇到超限即停止；不会截断一半问答，也不调用额外模型做摘要。

预算使用保守估算：每条消息的 UTF-8 字节数加 8，累计作为估算 token；它不等同于服务商 tokenizer 或账单 token。`context_history` 执行事件记录开关、纳入的轮数、估算值及是否截断，供检查实际上下文选择。原始消息和完成状态仍由对话存储拥有，适配器不建立另一份历史。

高级设置提供请求超时和重试次数；默认超时 120 秒、默认重试 0 次，重试可设置 0–10 次。Jev 超时上限为 120 秒，Chat Completion 上限为 86400 秒。所有参数由所选协议校验，切换协议时界面清除原协议的生成参数，避免将聊天参数发送给 Jev。

### 选择模型、超时与重试

- **模型**：取本次请求的 `model`，缺少时取条目的 `default_model`；两者都缺失时请求失败。
- **超时**：使用规范化请求的 `timeout_seconds`，默认 120 秒且不少于 1 秒。
- **重试**：使用规范化请求的 `max_retries`，默认 0。需要条目默认值的入口在构造请求时明确传入，例如 Relay。总尝试次数是 1 加重试次数。只有传输故障、HTTP 429 和 5xx 可重试；配置错误、其他 4xx 和响应解析错误直接失败。第 n 次失败后等待 0.25×n 秒再重试，最多等待 2 秒；等待期间收到的取消在下一次尝试前生效。
- **生成参数**：条目的 `default_parameters` 只由 Repo 入口读取。Chat Completion 请求可以提供两类参数。`parameters` 原样合并进请求体；`generation_options` 只接受 `thinking`（`type` 为 `enabled` 或 `disabled`）和 `max_tokens`（1–32768 的整数），不符合时请求失败。最终请求体中的 `model` 和 `messages` 总是由服务确定，不会被参数覆盖。

## 登记条目怎样发出请求

`AIProviderRegistry.request()` 负责一次 HTTP 调用。它按条目 ID 取得已启用的条目，要求条目提供 `base_url` 和 `api_key_file`，读取密钥后发送 JSON POST。

请求头包含 `Authorization: Bearer <密钥>`、`Accept: application/json`、`Content-Type: application/json` 和 `User-Agent: TFIPortal/1.0`。条目的 `default_headers` 中非空的值会加入或覆盖这些请求头；`provider` 为 `openrouter` 时，没有提供 `HTTP-Referer` 和 `X-OpenRouter-Title` 就补上 Portal 的默认值。

响应必须是 JSON 对象。错误按以下方式返回：

| 情况 | 结果 |
| --- | --- |
| 服务商返回 HTTP 错误 | `AIProviderHTTPError`，保存 HTTP 状态；普通路线说明中包含最多 1200 个字符的响应正文 |
| 网络连接、DNS 或 TLS 失败 | 底层的网络异常原样返回 |
| 响应不是合法 JSON | JSON 解析异常原样返回 |
| 响应是 JSON 但不是对象 | `RuntimeError` |

调用方因此可以区分“接口不可用”“网络失败”和“响应格式错误”。

每次 HTTP 尝试都会生成一份传输诊断（`tfi.provider-transport.v1`），记录第几次尝试、停在哪个阶段（等待响应、读取正文、解析 JSON）、结果、错误种类（超时、读取超时、HTTP 错误、TLS 错误、连接错误、地址错误、JSON 无效）、请求与响应的字节数、HTTP 状态和各阶段耗时。诊断只包含数字和固定的枚举值，不包含请求头、请求正文或响应正文。成功时它附在执行结果的 `provider_transport` 中，失败时附在错误详情中，供排查慢请求和失败原因。生成诊断出错不会影响请求结果，也不会引发重试。

```text
调用方给出条目 ID、消息和选项
        ↓
ExecutionService 校验请求：chat_completion / generate / stateless
        ↓
ChatCompletionService 组合消息，确定模型、超时和重试次数
        ↓
协议适配器校验参数：Chat Completion 或 Jev 判定
        ↓
AIProviderRegistry 取得已启用条目，读取密钥文件，组装请求头
        ↓
POST chat/completions 或 systemone（失败时按重试次数再试）
        ↓
文本或结构化 answers + usage + 原始响应 → 执行结果
```

## 使用 Chat Completion 的入口

| 入口 | 说明 |
| --- | --- |
| `CompleteChat` 指令 | 人或 Resident 直接调用一个登记的接口（decision_4527448ad07f）。它由 ExecutionService 同步调用 `AIProviderRegistry.request()`：模型取参数或条目的 `default_model`，超时取条目的 `timeout_s`（默认 120 秒），不重试；`options` 中的 `model`、`messages`、`stream` 被忽略。结果是第一个候选的完整消息对象和用量，没有回答时指令失败。调用方可以给出 `response_schema`（JSON Schema）：Portal 先检查它本身有效，再在消息最前面加一条系统消息，要求只输出满足这个 Schema 的一个 JSON 值；回答返回后取其中第一个 JSON 对象或数组（优先取代码块中的），校验通过的值放在结果的 `structured` 中。回答里没有 JSON 或不满足 Schema 时指令失败并标为可重试，因为模型再答一次可能就对了 |
| `provider_api` Resident | 运行设置选择 `provider_api` 后端的 Resident，每轮输入都按所选接口的协议执行一次 API 请求，模型、系统提示、重试、超时和参数取自该 Resident 的执行设置。这类 Resident 不接受图片等原生输入。它的运行身份块说明它直接通过模型 API 作答、没有任何工具（不能读写文件、运行命令、调用 HATG 指令或启动子 agent），每轮看到本次组装的固定指令、选中的历史和当前输入，需要文件或命令结果时说明需要什么；`GetSentPrompt` 从这条消息所在的那次会话读出实际发给模型的输入；`GetResident` 返回 `provider=provider_api` 和 `account=<API 条目>`，与 `CreateResident` 接受的写法一致 |
| 业务调用 | Paper 的摘要与评审、Repo Workspace 的提交说明与描述生成等后端功能，从登记中挑选 `openai_compatible_chat` 条目和模型，再通过共享的 Resident 调用（decision_725dedbe89b2）交给一个 `provider_api` Resident 执行 |
| LAN AI Socket Relay | 局域网设备按预先登记的资源调用，附加限制见 decision_c604274e21dd |

## 已移除的独立受约束路线

早期 `resolve_route_contract` 路线及其专用执行请求约束已随提交 `739eb66c5d7e6cecafebf73128c6d84be039f33a` 移除，对应的独立路线测试也已删除。它们不再是当前可调用接口或等待实现的方案。当前模型 API 通过本篇的登记条目、账号配置和共享 Execution 请求执行，执行与恢复合同见 decision_6ad0384e020d；不要据旧路线说明绕过现行账号、权限或会话边界。

格式名与版本须精确匹配当前规范值，校验规则见 decision_86aed2f0e8c7；本篇同时校验各字段和结构版本，不接受历史前缀或其他版本。
