---
kb_id: decision_ef1d6c925955
type: decision
project: AI员工
created_at: '2026-09-03'
version:
  number: 16
  created_at: '2026-09-03'
  created_by:
    user: ''
    agent: ''
  updated_at: '2026-10-19T19:43:45Z'
  updated_by:
    user: tfi-exported-user
    agent: resident_865c3c24f251
importance: 9
metaness: 3
ai_generated: false
links:
  source:
  - ${code_root}/tests/cases/test_portal_resident_rule_injection.py
  - ${code_root}/management_portal/society_service/resident_execution_domain.py
  - ${code_root}/management_portal/execution/backends.py
  - ${code_root}/management_portal/agent_prompt_context.py
  - ${code_root}/management_portal/agent_skill_context.py
  - ${code_root}/management_portal/execution/contracts.py
  - ${code_root}/management_portal/execution/runtime.py
  - ${code_root}/management_portal/execution/session_manager.py
  - ${code_root}/tests/cases/test_execution_context.py
  related:
  - decision_6c2f91a7d84e
  - decision_6b9e42f1c8a7
  - decision_3948ce9b9443
  - decision_3e8c2a7f91d4
  - decision_b1ab45c4b3ec
  - decision_91eb3626d323
  - decision_c47a13e8b962
  - decision_5c62deb8253a
  - decision_6ad0384e020d
  supersedes: []
  derived_from: []
---
# Portal Prompt Context 的物化与注入边界

## Family 工作指南的物化边界

Family 的工作场景配置属于执行图：实际 Route 参数保存角色、触发对应指南和每轮任务模板；UI 与运行时访问同一事实。角色工作区中的 `family-work-guide.md` 由此投影，附内容版本，不能作为反向写配置的第二个入口。

上下文构造器用统一 `InjectionPart("family_work_guide", ..., "session_start", "prompt_prefix")` 在 Provider 会话首轮发送完整指南。后续任务只带当前事实、指南路径和版本；修改指南下一任务可查，新会话会收到新版完整指南。业务职责不属于用户选择的通用规则预设，不能借此重新引入私人 AGENTS 或每轮全量资料注入。个人助手 Router/Steward 的补充职责也遵守首轮策略，业务成员原策略不变。

配置、投影与按需查资料的完整契约见 decision_44cf587c1721；注入与预览遵守本文件的统一构造边界。


每次模型执行开始前，模型都需要知道自己在 TFI Portal 中是什么身份、要遵守哪些核心规则、这次任务要读哪些专用规则。Portal 在请求进入模型之前，把这些内容整理成系统指令；Resident 的系统指令由若干部分组成（核心规则、resident.md、运行身份、授权上下文），每个部分有自己的时机。ExecutionService 逐轮决定这一轮带哪些部分。这样，无论 Resident 使用 Codex、Claude Code、Kimi Code、DSH 还是普通模型接口，得到的身份和规则都一致；断线恢复或续跑时也不会重复发送同一份内容。

本 Design 说明系统指令从哪里来、怎样生成、何时发送，以及各 Provider 把它放在哪里。任务模板、工作区说明、选中的历史消息等业务上下文由各业务领域自己提供，本 Design 只负责系统指令的生成与发送。

## 基本概念

- **系统指令（`SystemInstructions`）**：统一执行请求中的一个对象，最多 500,000 个字符，有两种写法：
  - 一份正文 `content` 加一个发送策略 `injection_policy`；
  - 若干**注入部分**（`parts`）。每个部分有自己的名字、正文、时机和位置。
- **时机**：
  - `session_start`：只在会话第一次注入时发送；
  - `session_start_and_after_compaction`：会话第一次注入时发送，会话上下文压缩后的第一轮再发送；Provider 声明其系统位置跨压缩保留时，系统部分只在首次发送，prompt 部分仍按压缩时机发送；
  - `every_turn`：每轮都发送。
  - 正文为空时，策略只能是 `session_start`。
- **位置**：
  - `system`：Provider 的系统位置（开发者指令、session 或进程的系统提示、system 消息）；
  - `prompt_prefix`、`prompt_suffix`：本轮消息的前面、后面。
- **核心规则**：共享 Skill 目录中的 `tfi-agent-rule/SKILL.md`，以及当前角色对应的角色文件。它们是所有 Portal Agent 都要遵守的规则。
- **角色（agent role）**：调用方明确给出的身份，只有三种：`general`（只读取 `SKILL.md`）、`resident`（再读取 `portal-resident.md`）和 `development`（再读取 `portal-development.md`）。其他名字直接报错，不会换成相近的角色。
- **附加 Skill**：本次任务需要额外遵守的 Skill。系统指令只列出它们 `SKILL.md` 的路径并要求“行动前完整读取”，不把正文放进指令，模型在任务真正用到时自己去读。
- **生成（物化）**：服务端读取真实的规则文件、去掉重复内容、拼成完整正文的过程。统一入口是 `portal_system_instructions(...)`。
- **指纹（fingerprint）**：完整正文的 SHA-256 摘要，用来识别发送的是哪个版本。
- **附加次数**：SessionManager 为每个会话记录的注入次数（`context_injection_count`）：一轮带了任何部分，就算一次注入。同时记录最近一次的指纹，以及最近一次注入的时间（`context_injected_at`），这个时间取这一轮开始运行之前（decision_91eb3626d323）。
- **检查点中的 `context_attached`**：ExecutionService 在一次执行的恢复检查点中记录“系统指令已经交给 Provider”，续跑时据此不再发送（decision_6ad0384e020d）。

## 设计点

- **所有角色从同一个入口生成，每次只选一个角色文件。** 未知角色直接失败。调用方不能提交自定义正文来替换核心规则。
- **规则文件暂时读不到时，只能用同一角色上一次成功生成的结果。** 这份缓存（last-good）同样要满足字符预算；没有可用缓存时，这次请求在调用模型之前失败。
- **只有生成类操作会发送系统指令。** 有状态会话的 `turn` 和 Chat Completion 的 `generate` 才判断是否发送；创建、Fork、压缩、释放等操作不发送；新建 Codex thread 也只接收本轮注入计划实际选择的系统部分。RestartSession 的初始化轮次按目标 Provider 和运行设置构造完整的 Resident 注入部分，resident.md 与核心规则一起送达，成功后的绑定保留这次实际注入记录。
- **每个部分按自己的时机发送。**
  - 会话第一次注入时，所有部分都发送。第一次注入指附加次数为 0，或者 Provider 会话是新开的。
  - 之后，`every_turn` 的部分每轮发送。
  - `session_start_and_after_compaction` 的部分在会话上下文压缩过之后再发送一次；Codex 的 developer instructions 跨压缩保留，因此不重发系统部分，避免每次压缩在用户消息中堆积规则。判断方法：会话当前上下文开头的压缩片段（AgentData 记录的 Portal 压缩，以及 Codex、Claude Code、Kimi Code 自己做的压缩），时间晚于最近一次注入。
- **续跑以检查点为准。**
  - 恢复一次执行时，检查点已经记录 `context_attached`，系统位置的部分就不再发送。
  - 消息前后的部分和第一次尝试拼得完全一样，因为它们依据的会话状态只在这一轮完成时才变。
- **Provider 适配器只使用已经生成好的系统指令。** 适配器不自己读取角色文件或 Skill，也不根据工作目录推断身份或权限。

## 核心规则怎样生成

`materialize_portal_skills` 读取 `${share_root}/Skills/tfi-agent-rule/SKILL.md` 和当前角色的角色文件，为每个文件计算 SHA-256，内容相同的文件只保留一份。拼好的正文带有当前角色、每个来源的路径与摘要，以及状态：

| 情况 | 结果 |
| --- | --- |
| 成功读取且不超过 450,000 字符的软预算 | 状态 `fresh`，同时更新该角色的缓存 |
| 文件暂时读不到（系统错误或编码错误），有缓存 | 使用缓存，状态 `stale`，原因 `source_temporarily_unavailable` |
| 当前内容超过软预算，有缓存 | 使用缓存，状态 `stale`，原因 `current_sources_over_soft_budget` |
| 上面两种情况没有缓存，或缓存本身超过预算 | 请求失败 |

文件读取按修改时间和大小缓存，内容没变时不重复读取。

`portal_system_instructions(...)` 在核心规则外面加上一层 `<portal-system-prompt role="...">` 包装，内容依次为：

1. 生命周期说明：`session_start` 时说明“这是当前 Provider session 的启动指令，在整个 session 中有效；Portal 不在每个 turn 刷新这些核心规则，无论在系统提示、会话历史还是压缩内容中看到本段，都不要重复读取这些 Skill”（Claude Code 每轮都在系统提示里看到它，见下表）；`every_turn` 时说明“这是 Portal 为当前轮次明确发送的规则刷新”。
2. 核心规则正文。
3. 角色为 `resident` 时，附加 Resident 提问卡片的内容规范。
4. 有附加 Skill 时，列出它们的 `SKILL.md` 路径，要求读完统一入口后再读取。
5. 允许子 Agent 时，写明“允许使用 sub agent。”。
6. 结尾说明本段由 Portal 显式注入，不要把 `AGENTS.md` 或平台私有指令当作它的来源。

## 附加 Skill 怎样解析

附加 Skill 可以用三种方式给出，按首次出现的顺序去重，与核心入口相同的路径不再重复列出，空值忽略：

| 写法 | 解析方式 |
| --- | --- |
| 统一资源 ID，例如 `skill_1a2b3c4d5e6f` | 以当前调用者的身份通过 ResourceService 解析。资源必须存在、类型为 `skill`、入口以 `SKILL.md` 结尾；不存在、无权读取、类型不对或没有入口时生成失败 |
| 以 `/SKILL.md` 结尾的路径 | 展开用户目录、解析相对路径与符号链接；真实路径必须在 `${share_root}/Skills` 内，且文件存在 |
| 普通名称，例如 `scheduled-work` | 名称由小写字母、数字、短横线组成，长度为 2–79，首字符为字母或数字；解析为 `${share_root}/Skills/<名称>/SKILL.md`，同样检查真实路径范围与文件存在 |

附加 Skill 的路径范围与 Resident 输入采用同一规则：最多处理输入列表的前 12 项，所有路径最终都必须指向 `${share_root}/Skills` 内存在的 `SKILL.md`。资源 ID 通过当前调用者解析后也接受相同路径检查；路径越界、名称无效或文件缺失时，在调用模型前失败。

## 一次生成怎样决定是否发送

```text
业务领域准备本轮消息，以及带时机和位置的注入部分
        ↓
ExecutionService 按会话附加次数、最近注入时间、上下文压缩和恢复检查点，决定本轮带哪些部分
        ↓
系统位置的部分交给 Provider 适配器放到原生位置；消息前后的部分拼进本轮消息
        ↓
本轮完成后，SessionManager 把附加次数加一，并保存指纹和注入时间
```

只有同时满足以下条件，才会注入：
- 服务类型是 `agent_session` 或 `chat_completion`；
- 操作是 `turn` 或 `generate`。

只给 `content` 的调用方等于一个系统位置的部分，时机就是它的 `injection_policy`。检查点同时保存 Provider、会话、轮次、路由、指纹和发送状态，连接重建或轮次续跑时据此避免重复发送。

## Resident 的 Prompt 注入设置

Resident 的运行设置 `prompt_injection` 决定它的每一轮带哪些 Portal 内容、在什么时机带。设置有两种形式：
- 选一个预设：`{preset}`；
- 自定义：`{preset: custom, parts}`，逐类给出时机。

可以在 Chat 的“Prompt 注入”面板里设置，也可以用 HATG ISA（decision_d19b9909a674）：`GetResidentSettings` 在 `settings` 旁返回 `prompt_injection` 视图（`preset`、`preset_label`、`presets` 和带 `placement` 的 `parts`）；`UpdateResidentSettings` 通过 `changes.prompt_injection` = `{preset?, parts?}` 修改，`parts` 叠加在给出的预设上，没给预设时叠加在当前设置上，结果返回同样的视图；`PreviewPromptInjection` 预览各轮实际发送的内容。内容放在哪里由 Provider 决定，用户不能选。

| 类别 | 内容 | 可选时机 | 位置 |
| --- | --- | --- | --- |
| `core_rules` Portal 核心规则 | `<portal-system-prompt>`：核心规则、提问卡片规范、子 Agent 许可 | 关闭、首轮、首轮 + 压缩后、每轮 | 系统位置 |
| `resident_md` | Resident 的长期身份说明 `<resident-identity>` | 同上 | Claude Code、Kimi Code、Provider API 在系统位置；Codex、DSH 在消息前 |
| `runtime_identity` 运行身份 | `<resident-runtime-identity>`：resident_id、lineage_id、以本 Resident 身份调用 HATG ISA 的说明 | 同上 | 消息前 |
| `authorization_context` 授权上下文 | `<portal-authorization-context>`：本轮执行者、消息来源、工作目录参考 | 同上 | 消息后 |
| `message_envelope` 消息包装 | `<user-message>` 标签；带附件时还有会话说明、发送者和 Resident 信息；来自门面的消息附带回复提示 | 关闭、每轮 | 包住消息 |
| `business_instructions` 业务指令 | `prompt_context.system_prompt` | 每轮 | 系统位置 |
| `turn_skills` 任务 Skill 清单 | 某条消息额外要求读取的 Skill | 关闭、只发清单、连同核心规则重发 | 消息前；连同核心规则重发时随核心规则 |

预设：

| 预设 | 核心规则 | resident.md | 运行身份 | 授权上下文 | 消息包装 | 任务 Skill |
| --- | --- | --- | --- | --- | --- | --- |
| `standard` 标准（默认） | 首轮 + 压缩后 | 首轮 + 压缩后 | 每轮 | 每轮 | 每轮 | 只发清单 |
| `full` 每轮完整 | 每轮 | 每轮 | 每轮 | 每轮 | 每轮 | 只发清单 |
| `minimal` 精简 | 首轮 | 首轮 | 首轮 | 首轮 | 每轮 | 只发清单 |
| `pure` 纯 TUI | 关闭 | 关闭 | 关闭 | 关闭 | 关闭 | 关闭 |

**改单个类别后记为自定义；** 结果与某个预设完全相同时，记为该预设。

**纯 TUI 不带任何 Portal 内容，** 模型只收到消息原文，就像直接使用 Provider 自己的 TUI。Provider 自带的系统提示（例如 Claude Code 的 preset）照常保留，在它自己的 TUI 里也有。

**每条消息自带的内容不受这些设置影响：** Fork 继承的历史、附加引用、系统提供的运行上下文都随消息发送。Agent Harness 的运行提示由 Harness 自己的设置控制。

**规则与权限的边界：** 关闭运行身份和授权上下文只是不再告诉模型这些信息，服务端仍按当前 Resident 检查权限。

**这些设置在下一轮生效，不中断当前工作。** 2026-10-09 起取代旧的布尔设置 `include_initial_instructions`：没有任何 Resident 打开过它，关闭时等同于原来的“只在会话第一次发送”。

### 其他规则
- 业务调用方的隔离输入按调用方写的原文发送，不加运行身份，也不在消息前加 resident.md。
- 是否允许子 Agent 取自这条输入，没有指定时取 Resident 的运行设置 `allow_subagents`。
- 需要解析 Skill 资源时，使用发送这条输入的调用者身份做权限检查。

### resident.md 为什么总由 Portal 发送
预览显示实际送达位置：Codex、Kimi 后续轮次到期的系统部分显示在用户消息前；Provider API 的 prompt 部分进入最后一条用户消息。

没有 Provider 会自己读取它：Codex 关闭了原生项目文档（`project_doc_max_bytes = 0`），其他 Provider 读取的文件名也不同。所以不论工作目录在哪里，都由 Portal 发送。

**Fork 出的 Resident 在第一轮收到自己的启动指令。** Fork 的种子跳过来源会话的启动指令片段（它写的是来源 Resident），子 Resident 开始自己的 Provider 会话，第一轮和普通新会话一样附加所有到期的部分（decision_5bf5bfbd52cb）。来源曾经压缩过时，压缩结果里继承的那份启动内容仍然保留。工作区和会话记录只提供业务上下文，不用来推断角色、Skill 或权限。

## 各 Provider 把系统指令放在哪里

| Provider | 放置位置 |
| --- | --- |
| Codex | 新 thread 把所有系统位置的部分放入开发者指令（developer instructions）；已有 thread 把本轮到期的系统部分放在用户消息前；首轮 + 压缩后策略的系统部分因跨压缩保留而不重复发送（decision_3e8c2a7f91d4） |
| Claude Code | **每轮都把所有未关闭的系统位置部分追加在 Claude Code 自带的系统提示（SDK preset `claude_code`）之后，不论时机。** Claude Code 的系统提示是 CLI 进程启动时的配置，不保存在会话记录里；恢复会话的进程没收到系统提示，就以空系统提示运行（SDK 把缺省翻译成 `--system-prompt ""`）。所以不论附加策略，适配器每轮都发送这个会话的系统指令（provider catalog `system_context_every_turn`），守护进程每次启动都带 preset，没有正文时只带 preset，从不使用空系统提示。正文不变时运行签名不变，第二轮起复用同一个进程，缓存前缀保持稳定（decision_3948ce9b9443）。有 every_turn 部分时每轮记录附加次数和对应 trace；每次进程启动实际使用的系统提示见 Claude Code CLI 会话 JSONL 附件 `prompt_snapshot`。 |
| DSH | 每轮发送所有未关闭的系统部分作为完整系统提示（decision_276105b5d67b） |
| Kimi Code | 新 session 写入 session 的系统提示，第一条用户输入不再重复；已有 session 需要发送时，把正文与用户输入组合后提交（decision_6b9e42f1c8a7） |
| Chat Completion | 需要发送时作为第一条 system 消息，本次运行的业务系统提示随后，业务消息最后（decision_6c2f91a7d84e） |

这些规则只决定正文放在哪里；Provider 的会话、工具、事件和失败处理由各自的适配器负责。

## 错误与职责

未知角色、无效或无权的 Skill、错误的资源类型、无效的发送策略、超长正文和 `every_turn` 空正文，都在调用模型之前失败。last-good 缓存只处理规则文件暂时读不到或超出软预算，不掩盖调用方的配置错误。

| 部分 | 负责 |
| --- | --- |
| 业务领域 | 业务上下文的内容与来源 |
| Prompt Context（本 Design） | 生成系统指令 |
| ExecutionService 运行时 | 决定是否发送，保存检查点 |
| SessionManager | 附加次数与最近指纹 |
| Provider 适配器 | 正文在 Provider 中的放置位置 |

这样，新增一个业务上下文只需要提供正文，不需要在每个领域重新实现发送与去重。

