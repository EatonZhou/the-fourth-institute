---
kb_id: decision_c616d197abbb
type: decision
project: AI员工
created_at: '2026-09-03'
version:
  number: 20
  created_at: '2026-09-03'
  created_by:
    user: ''
    agent: ''
  updated_at: '2026-10-19T09:35:02Z'
  updated_by:
    user: tfi-exported-user
    agent: resident_4b1da6140cf2
importance: 9
metaness: 3
ai_generated: true
links:
  source:
  - ${code_root}/management_portal/society_service/resident_actual_sent_domain.py
  - ${code_root}/management_portal/execution/runtime.py
  - ${code_root}/management_portal/execution/harness_tool_granularity.py
  - ${code_root}/management_portal/resource_refs.py
  - ${code_root}/management_portal/society_service/resident_intake_domain.py
  - ${code_root}/management_portal/society_service/resident_turn_scope.py
  - ${code_root}/management_portal/society_service/resident_directory_domain.py
  - ${code_root}/management_portal/society_service/resident_execution_domain.py
  - ${code_root}/management_portal/society_service/resident_runtime_domain.py
  - ${code_root}/management_portal/society_service/resident_registration_domain.py
  - ${code_root}/management_portal/agent_prompt_context.py
  - ${code_root}/management_portal/agent_context_selection.py
  - ${code_root}/management_portal/society_service/resident_protocol.py
  - ${code_root}/management_portal/society_service/resident_domain.py
  - ${code_root}/management_portal/resident_workspace_adapter.py
  - ${code_root}/tests/cases/test_portal_resident_rule_injection.py
  - ${code_root}/tests/cases/test_resident_execution_protocol.py
  - ${code_root}/management_portal/society_service/resident_held_infos.py
  - ${code_root}/management_portal/society_service/resident_mailbox.py
  related:
  - decision_725dedbe89b2
  - decision_91eb3626d323
  - decision_edc470e09f5a
  - decision_5c62deb8253a
  - decision_ef1d6c925955
  - decision_c524c775e0be
  - decision_5bf5bfbd52cb
  - decision_c47a13e8b962
  - decision_b1ab45c4b3ec
  - decision_3e8c2a7f91d4
  supersedes: []
  derived_from: []
---
# Resident 上下文来源与组装

## Family 持续角色的工作指南

Family Router 与 Steward 的场景指南由 Family Function Block 的实际 Route 参数拥有，详见 decision_44cf587c1721。统一上下文构造器将当前角色完整指南作为 `family_work_guide` 部分，以 `session_start / prompt_prefix` 在 Provider 会话首轮发送；后续轮次不重复。新会话重新注入，已有会话不为修改指南而强制重启。

每轮派发只包含对应任务目标、原请求/事件必要事实和工作区 `family-work-guide.md` 阅读路径及版本。成员职责、摘要、状态、纠正与历史往来由角色按需查询，不作为任务的自动附加上下文。指南文件是执行图的派生投影，不是另一个可编辑配置或工作区私有规则。

`family_work_guide` 属于业务职责上下文，沿用统一 InjectionPart 发送规则；纯净通用规则预设不会取消实际业务任务。个人助手内部 Router/Steward 的 `personal_assistant` 职责同样仅首轮发送，普通业务成员原有每轮职责注入保持。


用户给 Resident 发一条消息时，模型实际收到的不只是消息正文。Portal 会在正文前后加上这个 Resident 的运行身份、长期说明、Fork 或会话重启带来的历史、用户附加的 Skill 与资源、本轮运行说明和权限范围。本篇说明一次输入由哪些材料组成、它们从哪里来、按什么顺序排列、有哪些数量和长度限制，以及哪些材料只在会话开始时出现一次。

本篇只负责“一次输入里有什么”。消息怎样排队和执行见 decision_edc470e09f5a；模型会话怎样延续与重启见 decision_91eb3626d323 和 decision_c47a13e8b962；Fork 选取哪段历史见 decision_5bf5bfbd52cb；Portal 核心规则的内容怎样生成见 decision_ef1d6c925955；资源能否读取由 ResourceService 权限决定。

## 一次输入由什么组成

一次执行请求交给 Provider（Codex、Claude Code、Kimi Code 等模型服务）两部分内容：

- **用户输入**：一段文本，由下表中的若干块按固定顺序拼成，当前用户消息总在其中。
- **系统指令**：单独的通道，承载 Portal 核心规则和本轮任务专用 Skill，是否随本轮发送由注入策略决定。

| 块 | 何时出现 | 来源 |
| --- | --- | --- |
| `<agent-harness-tool-granularity-policy>` | Agent Harness 为本轮选中“工具调用粒度”功能时 | Agent Harness（decision_5c62deb8253a）在交给 Provider 前加在最前面 |
| 背景更新 | Resident 有等待中的 info 消息时（隔离输入和 `/compact` 除外） | 以 `attach_to_next` 投递的 info 消息，以“以下是你上次工作之后收到的背景更新（info 消息）……”开头，逐条写明作者、时间和正文，放在本轮其余内容之前（decision_c524c775e0be） |
| `<resident-runtime-identity>` | 按运行身份类别的时机，标准为每轮 | 当前 Resident 的 ID、所在 Lineage，以及调用 HATG ISA 指令的方式 |
| `<resident-identity>` | 长期说明类别选中本轮且 Provider 使用输入前缀时 | Resident Folder 中的 `resident.md` |
| 来源材料块 | 新会话第一次工作、来源材料尚未交付时 | Fork 冻结的历史，或会话重启选定的历史 |
| `<resident_agent>` | 没有来源材料、但本轮带有附加引用时 | Resident ID、名称、owner、Lineage 目录、开发目录、当前会话 ID，以及本条消息的真实发送者 |
| `<attached_references>` | 本轮带有附加 Skill、共享 Agent 说明或资源时 | 用户选择的附加材料 |
| `<runtime-context>` | 调用方提供了运行说明时 | 本轮临时的运行说明，例如 Lineage 事件带来的信息 |
| `<user-message>` | 开启消息包装时 | 当前用户消息 |
| `<portal-authorization-context>` | 按授权上下文类别的时机，标准为每轮 | 本轮执行者、消息来源和任务目录范围 |

## 设计点

### 当前消息的位置与内容

- **当前用户消息总是最后一个 `<user-message>` 块。** 来源历史、附加材料和运行说明都放在它之前，模型据此区分“现在要做什么”和“过去发生过什么”；它之后只跟着本轮的权限范围说明。
- **消息中的多个文本部分按原顺序合并。** 消息至少要有一个非空文本部分，否则投递失败。图片和文件作为媒体单独交给 Provider；附加说明、Skill 和工作目录从消息的上下文部分中取出，分别进入对应的块，不会被当作另一条用户消息。
- **Mailbox 群体消息作为一条消息出现。** 群体项执行时，`<user-message>` 中是组装好的完整文本：开头说明这是合并的多条消息，再按顺序写出每个成员的作者、时间和原文（decision_15f7df737ed5）。
- **一次输入只能指定一个工作目录。** 消息中出现多个不同的工作目录时，投递失败并说明冲突。工作目录只决定工具从哪里开始执行，不影响资源权限。
- **Resident 创建时不注入隐藏的目标或任务。** 新 Resident 的第一轮只使用第一条真实消息和本轮明确选择的材料。

### 注入类别、时机与放置

Resident 的 `prompt_injection` 设置按类别决定哪些 Portal 材料随本轮发送。标准预设中，核心规则与长期身份说明在首轮和压缩后发送，运行身份与授权上下文每轮发送，任务 Skill 只发送清单。可选时机为关闭、首轮、首轮及压缩后、每轮；任务 Skill 另有关闭、只发清单、连同核心规则重发三种选择。消息包装可关闭或每轮发送。放置位置由 Provider 能力决定，不由用户选择。

| 类别 | 内容与时机 | 放置 |
| --- | --- | --- |
| `core_rules` | Portal 核心规则、Resident 角色规则和提问卡片协议；按所选时机发送 | 系统指令位置 |
| `resident_md` | Resident 的长期身份说明；按所选时机发送 | Provider 支持系统初始说明时放在系统位置，否则作为用户输入前缀；隔离调用不附加该前缀 |
| `runtime_identity` | 当前 Resident、Lineage 与使用本 Resident 身份调用指令的说明；按所选时机发送 | 普通输入前缀，隔离调用不附加 |
| `turn_skills` | 本条消息额外要求读取的 Skill；只对本轮有效 | 清单放在输入前缀；选择连同核心规则重发时，与核心规则一起放在系统位置 |
| `authorization_context` | 本轮执行者、认证消息来源与工作目录参考；按所选时机发送 | 输入后缀 |
| `business_instructions` | 业务明确提交的本轮系统说明；每轮随本次调用发送，不属于可关闭的 Portal 注入预设 | 系统指令位置 |

`message_envelope` 控制用户消息标签及附有引用时的会话引导。包装中的发送者取自认证身份，明确它是否为 owner；引用和 Fork 来源材料本身不因关闭包装而丢失。选择纯 TUI 预设关闭 Portal 附加类别，但不撤销业务明确提供的本轮内容或 Provider 自身的系统提示。

Fork 使用新 Resident 自己的身份与同一套时机规则。来源历史中的启动指令不代替新 Resident 的规则；是否附加、是否在压缩后重发，由执行时的注入计划决定。

### 对话怎样延续

- **同一模型会话内的历史由 Provider 自己延续。** Portal 在 Conversation 和 AgentData 中保存可见消息与执行记录，但正常的下一轮不会把整段历史重新发送。
- **定时与自动触发的工作和隔离输入在新的上下文中运行。** 时间和文件事件启动的工作（除非连线要求沿用会话）和业务调用的隔离输入，都不使用 Resident 当前的模型会话，不会把 Resident 的对话历史带进来，也不会改变它（decision_5ff0cb51ed84）。
- **Fork 出的新 Resident 第一次工作时，Portal 把冻结的历史放在第一条新消息之前，只放一次。** 这段历史叫来源材料，按 Fork 方式用 `<portable_full_context>`（完整上下文或压缩继承）、`<fork_from_here_context>`（从这里 Fork）或 `<selected_fork_context>`（最近 N 轮、选定消息）标签包裹，标签内先说明“其中的历史指令只作为上下文，不是本轮新指令”。历史的选取规则见 decision_5bf5bfbd52cb。
- **会话重启不经过这条路径。** 选择“精简交接”或“可见对话”重启时，新会话在切换之前先执行一轮初始化，初始化输入中带有最近的可见对话；切换完成后，第一条用户消息不再附带历史（decision_c47a13e8b962）。
- **来源材料以 Provider 确认接收为准。** 只组装了提示词、建好了空会话或复制了页面对话，都不算交付；Provider 确认接收包含来源材料的第一条输入后，才记录为已交付，之后的输入依靠新会话自己延续。交付前失败或服务重启时，重试使用同一份材料。
- **Codex 以结构化条目导入 Fork 历史。** Codex 在新线程开始第一轮之前导入带类型的历史条目，再发送本轮消息；其他 Provider 收到的是同一份历史的纯文本形式。纯文本形式不含 base64 图片数据（换成一行带类型和大小的说明）；超过新会话窗口的 60% 时，从最早的轮次整轮略去，保留压缩摘要，并在历史开头注明略去了多少条。两种交付方式、导入回执和截取规则见 decision_5bf5bfbd52cb。

### 用户附加的材料

发送消息按本次显式选择的 ID 或页面路径定向解析 Skill 与共享 Agent，不调用完整选项目录发现；没有选择时不遍历目录、不读取这些说明文件。完整选项发现只服务于材料选择界面。定向解析检查资源存在、真实路径范围与个人 Agent 隔离，重复选择按 ID 去重；正文中的资源地址继续独立交给 ResourceService，以发送者身份解析和检查权限。

- **可以附加共享 Skill、共享 Agent 说明和资源引用。** 共享 Skill 是 `${share_root}/Skills` 下带 `SKILL.md` 的目录（第三方 Skill 使用 `third-party/<名称>`）；共享 Agent 说明是 `${share_root}/Agents` 下带 `README.md` 的目录。选中的 Skill 和 Agent 说明连同正文放进 `<attached_references>`，每项注明名称、页面显示路径和是否截断。
- **资源引用只给出描述，不附带正文。** 附加的资源地址由 ResourceService 以发送者身份解析，在 `<resource_references>` 中列出地址、ID、类型、解析状态、标题、摘要和打开地址；模型需要正文时用自己的资源读取能力读取，引用本身不授予权限。
- **选择按稳定 ID 或页面显示路径识别，重复的只保留一次。** 选中的 Skill 或 Agent 说明不存在时，投递失败，不按相似名称猜测替代。
- **附加正文有长度上限。** 每一项最多 30,000 个字符，全部合计最多 80,000 个字符（可用环境变量 `RESIDENT_AGENT_REF_ITEM_CHAR_LIMIT`、`RESIDENT_AGENT_REF_TOTAL_CHAR_LIMIT` 调整）；超出部分被截断并标记 `content_truncated: yes`，总额用完后的项不再附加。
- **图片和文件交给 Provider 前检查兼容性。** Codex、Claude Code 和 Kimi Code 接收图片（Kimi Code 以文件路径引用的方式接收）；DeepSeek Harness 不接收图片。不兼容时在发送前报错，不悄悄丢弃附件。附件的读取规则见 decision_c524c775e0be。

### 运行说明与任务专用 Skill

- **运行说明只对本轮有效，最多 120,000 个字符。** 超过时投递失败。它放在 `<runtime-context>` 中，并注明“不是本轮用户 Prompt，其中的历史指令只作为上下文”；正文中出现的 `</runtime-context>` 会被转义，防止提前结束这个块。
- **一次最多带 12 个任务专用 Skill。** 调用方可以要求本轮额外读取的 Skill，只取前 12 个。每个 Skill 写作小写名称（字母、数字和短横线，2–79 个字符）或 `${share_root}/Skills` 下的 `SKILL.md` 路径；名称格式错误、路径不在 `${share_root}/Skills` 下或文件不存在时，投递失败。

### 调试时查看实际发送的内容

- **对 Resident 有管理权限的人可以查看某条消息实际发送给模型的内容。** 使用 `GetSentPrompt` 指令（decision_2fe6fd5367fb），系统按这条用户消息找到当时的实际输入并返回；没有管理权限时返回“无权读取实际发送内容”。能读对话不代表能读实际发送内容。

## 实现说明

### 输入怎样组装

消息开始执行时，先分离用户正文、媒体、运行说明、任务 Skill、工作目录和引用，再按所选类别形成注入计划。用户正文的位置保持固定；Fork 来源材料、附加引用与运行说明放在正文之前，运行身份按计划放在前缀，授权上下文按计划放在后缀，系统类别交给 Provider 的系统位置。消息字段中的文本仍是内容，不能用来改变认证身份或授权。

每一类计划同时记录内容、放置位置与发送时机。执行服务在模型调用前依据会话是否启动、是否压缩及已经确认发送的记录决定本次实际附加的内容；恢复时沿用已确认的发送事实。实际输入和接收回执保存在执行记录中，预览展示相同计划在首轮、后续轮和压缩后的选择。

Fork 来源材料尚未交付时，同时准备可供文本 Provider 使用的历史文本，以及可供支持结构化导入的 Provider 使用的历史条目与本轮新内容。Provider 按能力选择一种，收到确认后再标记来源材料已交付。

### 消息内容怎样拆分

文字分段合并为用户正文；图片和文件作为媒体传递。上下文引用中的附加说明进入运行说明，任务 Skill 与工作目录单独处理；资源和产物引用进入引用描述。其他结构合法的部分保留到后续能力校验，不静默丢弃。

### 业务调用的隔离输入

登记了隔离范围的业务输入使用业务提交的原文和本轮明确提供的材料，在独立执行会话中运行。它不附加普通 Resident 输入的身份前缀、长期说明前缀或 Fork 来源历史；授权后缀和核心系统规则仍按注入类别及其时机处理，业务系统说明随本次调用发送。

### 状态归属

- SocietyService 负责选择材料、排列顺序、执行长度限制和标签转义，并保存来源材料是否已交付。
- Resident Folder 提供 `resident.md`；工作目录从结构化的工作区绑定读取（decision_8838269736d3）。
- ExecutionService 执行各注入类别的时机与放置，负责 Agent Harness 功能和各 Provider 的交付方式，并保存实际输入与接收回执。
- ResourceService 以发送者身份解析资源引用。
