---
kb_id: decision_725dedbe89b2
type: decision
project: AI员工
created_at: '2026-09-04'
version:
  number: 35
  created_at: '2026-09-04'
  created_by:
    user: ''
    agent: ''
  updated_at: '2026-10-19T08:43:17Z'
  updated_by:
    user: ''
    agent: resident_2e9ea0072551
importance: 9
metaness: 3
ai_generated: true
links:
  source:
  - ${code_root}/management_portal/application_bindings.py
  - ${code_root}/management_portal/resource_lineages.py
  - ${code_root}/management_portal/discussion_residents.py
  - ${code_root}/management_portal/service_invocation_contract.py
  - ${code_root}/management_portal/society_service/service_invocation_store.py
  - ${code_root}/management_portal/society_service/service_invocation_domain.py
  - ${code_root}/management_portal/shared_search_invocation.py
  - ${code_root}/management_portal/resident_handoff.py
  - ${code_root}/tests/cases/test_business_execution_handoff.py
  - ${code_root}/tests/cases/test_resident_invocation_backend_binding.py
  - ${code_root}/tests/cases/test_resident_invocation_wait.py
  related:
  - decision_2fe6fd5367fb
  - decision_eb066fbea4ed
  - decision_6591e7793116
  - decision_edc470e09f5a
  - decision_c524c775e0be
  - decision_86721bb4e8c8
  - decision_abaedeefca6f
  - decision_91eb3626d323
  supersedes: []
  derived_from: []
---
# Portal Resident Invocation 的共享业务调用与结果收敛

Portal 的很多功能需要让 AI 做一件事：深读一篇论文、给一条 Notice 分类、在知识条目旁边讨论、为 WALL 的设计点实现代码、做一次语义搜索。这些工作都由长期存在的 Resident 完成。**Resident Invocation** 是业务代码调用 Resident 的共享适配层（`management_portal/resident_handoff.py`）：业务模块提交一次任务，立即拿到可以长期追踪的句柄，也可以同步等到标准化的结果；业务模块不需要各自实现“找到或创建 Resident、投递消息、补读事件、解析回复”这一整套逻辑。

本篇面向业务模块的开发者，说明调用前要提供什么、同一业务范围怎样复用同一个 Resident、等待怎样经得起断线和重启、以及业务任务与 Resident 输入各自负责什么。消息和状态的对外规则见 decision_c524c775e0be；执行链路见 decision_edc470e09f5a。

## 基本概念

- **业务模块**：发起 AI 工作的 Portal 功能。它拥有自己的任务记录（业务 job）、进度、取消入口和结果解释。
- **一次调用（Invocation）**：一次提交，以及可选的等待。提交最终都成为一次 `DeliverMessage`（或一次 Lineage 事件触发），在目标 Resident 中形成一条标准输入。
- **Resident scope**：业务定义的稳定字符串（字段 `resident_scope`），用来为某个调用者找回同一个 Resident，例如 `resource-chat:<用户>:<资源 ID>`。
- **Lineage scope**：业务定义的稳定字符串（字段 `lineage_scope`），用来找回承载这些 Resident 的 Lineage；省略时等于 Resident scope。
- **关联（association）**：保存在 Portal 页面状态存储中的一条轻量记录，按“调用者 + 命名空间 + scope”记下这个业务范围当前指向哪个 Resident 或 Lineage，只存 ID，不复制对象。Resident 关联的命名空间是 `resident-invocations`，Lineage 关联是 `resident-invocation-lineages`，正在进行的创建记录在 `resident-invocation-creations`。
- **句柄（handle）**：保存原请求、目标和 `submission_state` 的恢复记录；`pending` 保留只读核对定位，`submitted` 另带已确认的实际 `message_id`，`not_submitted` 表示已有明确未受理证据。只有实际输入身份能用于等待或取消。
- **结果收敛**：从通知、持久事件、输入状态、回复消息和产物中，得到同一个最终结果的过程。
- **取消回调**：业务模块提供的只读函数 `cancellation_requested`，返回业务任务是否要求取消。

## 设计点

### 找到或创建 Resident

- **目标 Resident 有四种来源，按优先级依次是：**
  1. `resident_binding`：业务直接给出一个已确定的 Resident（例如应用配置中已确定的承担者）。调用层核对它当前所在的 Lineage 与绑定一致，不写关联；给出 `cwd` 且目录存在时，把目录绑定为这个 Resident 的工作区（`BindWorkspace`）。
  2. 已有关联：同一调用者、同一 `resident_scope` 之前关联过的 Resident。
  3. 新建：在明确的 Lineage 中创建一个普通 Resident 并写入关联。Lineage 依次取自 `lineage_binding`（业务显式绑定）、调用者本身是 Resident 时它所在的 Lineage、以及 Lineage 关联。
  4. 以上都没有时，调用失败并提示“缺少权威 Lineage 绑定”。**调用层从不自动新建 Lineage。**
- **同一调用者、同一 scope 复用同一个 Resident。** 不同调用者的关联互相隔离。关联的 Resident 已被删除或当前调用者无权访问时，调用失败并保留关联，由业务负责人修复绑定；调用层不会拿缓存或新团队替代。给了 `lineage_binding` 而 Resident 已不在这个 Lineage 时，同样失败。
- **已存在的 Resident 沿用自己的运行配置。** Provider、账号、模型、推理强度和权限由 Resident 自身决定；调用方的 `backend`、`backend_route_id` 与 `runtime_settings` 仅用于新建时提供初值，复用时不据此改写或拒绝 Resident 当前配置。
- **运行配置的修改使用显式控制入口。** 更换 Provider 或账号由 `RestartSession` 负责，其他运行设置由 `UpdateResidentSettings` 负责（decision_d19b9909a674）；共享业务调用层不因一次调用传入不同初值而发起迁移或修改设置。
- **新建的 Resident 是普通 Resident。** 以 `new_session` 方式登记，创建时不启动模型；第一条输入到达后才按普通生命周期执行。项目归属取自目标 Lineage。`title` 用作新 Resident 的名称。
- **并发创建只产生一个 Resident。** 查找和创建在“调用者 + scope”的关联锁内完成；创建过程的状态保存在创建记录中，响应丢失、关联保存失败后重试会继续原来那次创建，而不是再建一个。

### 页面讨论

Paper、WALL、资源页和 Project 页中的“讨论”面板，都用 Invocation 为“某个用户对某个条目的讨论”找到固定的 Resident。

- **讨论 Resident 按“用户 + 条目”复用。** Paper 按用户与论文，WALL 按用户与工程中的条目，资源页按用户与资源，Project 页按用户与项目。从不同页面打开同一讨论，使用同一个 Resident 和会话。
- **每类讨论放在明确绑定的共享 Lineage 中。** Paper 与 WALL 读取各自应用的主 Lineage 绑定；资源讨论按资源类型分组：知识条目（`resources.portal.KNOWLEDGE_ENTRY_TYPES` 中的类型）进入知识条目讨论 Lineage，Article、Repo、Skill、Experiment、Device 各有对应目标，Project 讨论使用项目讨论目标。资源与项目讨论由 `application_targets` 的 `resource_chat:<类型>` 配置指定；`resource-chat:<类型>` 与 `project-chat` 是调用适配层的应用名，分别映射到该表的对应类型及 `resource_chat:project`。配置维护与权限边界见 decision_6591e7793116，不再使用已移除的 Duty 指令。解析目标不授予目标权限，也不自动创建 Lineage；资源类型取自当前用户有权读取的资源记录，不从标题或页面筛选推断。
- **打开讨论不创建 Resident。** 打开讨论、读取历史、刷新或输入草稿只查找已有关联。还没有 Resident 时显示空对话和可编辑的输入框，草稿按用户与条目分开保存。
- **第一次发送非空消息时才创建讨论。** 页面在发送时检查当前用户对条目的访问权限，再幂等地找到或创建讨论 Resident，然后通过标准消息投递。空消息和权限被拒时不创建。并发准备同一用户与条目时复用同一个身份。
- **起始上下文只随第一条用户消息提供。** 之后的消息读取真实会话状态，已经有用户消息时不再重复附加起始说明。绑定后的发送、停止和刷新由共享的 Resident 会话组件负责。
- **切换条目会让旧的发送准备失效。** 旧请求返回后不能发送消息，也不能覆盖新条目的草稿；再次打开原条目时可以复用已准备好的 Resident。
- **讨论使用普通 Lineage 和普通 Resident。** 讨论只是业务用途，不是新的资源类型，也没有讨论专用的指令；成员、迁移、生命周期和访问控制都与其他 Lineage 相同。加入同一个共享 Lineage 不会让讨论公开：资源读取、Resident 会话和成员操作仍按当前调用者鉴权。绑定缺失、目标不可访问或成员关系不一致时报告错误，由管理入口修复。
- **合并讨论保留原身份和历史。** 用 `MoveResident` 转移已有成员，等迁移的持久回执完成后，再删除空的源 Lineage。用户与条目的原关联仍指向原 Resident，所属 Lineage 以当前的成员关系为准；迁移不创建替代讨论。

### 提交

- **受理身份与提交请求分开保存。** `submit_invocation` 使用稳定 `request_id` 提交，返回 `submission_state=pending/submitted/not_submitted` 的句柄。只有正式回执或原始因果路径确认的 `message_id` 才是已受理输入身份；只有明确未受理证据才能标为 `not_submitted`。`pending` 保留原请求和恢复定位，不重新发送；已受理后用 `resume_and_wait` 等待同一次输入。
- **请求 ID 按固定顺序确定。** 显式给出 `message_id` 参数时将它作为请求 ID；否则给了 `business_ref` 时，对 `resident_scope + NUL 字符 + business_ref` 计算 SHA-256，取前 32 个十六进制字符并加前缀 `msg_`；两者都没有时随机生成。直接 DeliverMessage 使用该 ID 作为消息请求 ID，Lineage 工作流实际创建的输入 ID 可以不同，必须读取实际回执。`business_ref` 同时随消息保存（decision_2fe6fd5367fb）。
- **投递前持久保存完整恢复句柄。** `on_assigned` 在提交指令之前得到独立副本，包含 `resident_id`、`lineage_id`、`request_id`、原提交指令/幂等定位及工作流的 `event_source_id`、`effect_id`，此时状态为 `pending`，不填猜测的 `message_id`。回调异常则不执行提交；修改副本不改变提交目标。此回调只证明分配和恢复意图已保存，不证明业务已受理或工作区已就绪。
- **准备阶段可以重试，投递不会重复。** 找到或配置 Resident 的步骤遇到 SocietyService、ResourceService 暂时不可用或同一指令仍在执行时，每秒重试一次，期间先检查取消回调，已取消则不投递。重试只覆盖准备步骤，不重复整次提交。
- **工作流受理沿原因果链恢复。** 给出 `workflow_binding` 时，提交 TriggerEvent，由事件与 Effect 投递实际 Resident 输入。回执丢失、指令中断或进程重启时，`resolve_invocation_submission` 仅以原请求定位读取指令执行与 TriggerPath，校验 Lineage、源、Effect 和 Resident，再确认实际 `message_id`。指令 `failed/commit_state=unknown` 可以已有下游输入；不得据此重发、改选最新路径或取消准备 ID。缺失、歧义和暂不可访问保持 `pending`。

### 等待

- **通知只负责唤醒，结论来自持久状态。** `wait_for_input` 先读这条输入的快照，然后等待这个 Resident 的对话通知；收到通知后按游标补读事件（每批最多 500 条），看到这条消息的终态事件才结束。通知游标出现缺口、网关重连或补读发现游标不连续时，重新读取快照；15 秒没有任何通知时也主动读一次快照。
- **有取消回调时，每秒检查一次。** 取消回调第一次返回真时，等待器发送一次取消（见下一节），之后继续观察直到终态，不重复发送。
- **等待有可选的时间预算。** 给出 `timeout_seconds` 时，预算用完抛出“观察超时”，不发送取消，也不投递新输入；调用方之后可以继续观察同一条输入。
- **终态决定返回方式。** `completed` 进入结果读取；`failed` 与 `cancelled` 抛出 `ResidentInputTerminated`，其中带有输入状态和错误消息，业务模块自己决定怎样映射到业务任务状态。

### 取消

`cancel_resident_invocation` 用 `CancelInput` 取消输入：正在处理的消息被停止，排队中的消息从 Mailbox 撤回。先通过可信业务 RPC cancel_resident_service_input 核对不可变输入范围记录；当前认证调用者是该服务输入的发起者时，直接取消并返回该输入收据。没有匹配记录时调用 `CancelInput`，不先读取隔离输入或其对话。指令回执返回输入状态；输入已经结束时回执带 `already_terminal: true` 和原终态，状态在请求期间变化而返回冲突时最多重试 3 次。它只取消这条输入，不终止业务任务，也不删除消息。普通取消由 CancelInput 在当前权限方案中的要求判定，默认方案要求对目标 Resident 有 `@contributor`（decision_b820faf42218）；服务调用的隔离输入既可由发起者经业务收据路径取消，也可由对目标 Resident 有 `@contributor` 的调用者经 ISA 取消。

### 结果读取

中间进展通过 Conversation 读取；调用结果按聊天投影选定的最终回复收敛，不把所有中间输出拼成业务结果。

- **结果由回复消息拼成。** `observe_invocation` 读取输入状态中的最终回复 ID，再读取对应的最终回复消息：这些回复中的文本分段按换行拼成 `message`；读取到的最终回复列表放在 `messages`；回复带的用量放在 `usage`；回复通过 `artifact_reference` 引用、且内容是对象的产物放在 `response`（有多个时取最后读到的那个）。
- **回复可能比终态晚到。** 输入的终态和回复消息分别写入。`resume_and_wait` 在输入完成后，如果回复还没全部可读（数量、ID 或内容对不上），按 0.1、0.2、0.4、0.8、1.5、2、3、4、5、8、12、15 秒的间隔重读（合计约 51 秒）；仍然读不到时抛出“观察超时”，调用方可以继续观察原输入，不能把缺失的回复当成成功的空结果。
- **读不到回复时先确认输入还在。** 读取回复时遇到记录暂时不存在，观察层重新读取原输入：输入已失败或被取消时按终态返回；权限被拒、输入被删除按原有含义报错，不归入“回复延迟”。
- **业务只解释结果内容。** 业务模块根据结果更新自己的对象，不自己解析另一套 Resident 运行状态。例如后台分类遇到真实失败或取消就结束，遇到观察超时继续等同一条输入；投影工坊用同一套结果读取取得回复，再核对页面或计划是否已经保存。

## 接口

| 函数 | 作用 |
| --- | --- |
| `ensure_invocation_resident(...)` | 只找到或创建 Resident 并写关联，不投递消息；返回 `tfi.resident-invocation.v1` 身份，含 `resident_id`、`lineage_id`、`resident_scope`、`lineage_scope`、`backend`、`created` |
| `lookup_invocation_resident(actor, resident_scope, lineage_binding)` | 只读已有关联，不创建也不配置；没有关联时返回空 |
| `submit_invocation(...)` | 准备并提交一次输入，返回明确的提交状态与恢复句柄，不等待 |
| `wait_for_input(actor, resident_id, message_id, ...)` | 等待一条输入进入终态 |
| `observe_invocation(actor, resident_id, message_id, ...)` | 读取一次输入状态和完整回复，不等待 |
| `resume_and_wait(actor, resident_id, message_id, ...)` | 用已有句柄等待并读取结果 |
| `invoke_and_wait(...)` | 提交后观察实际输入；`on_submitted` 只收到已受理句柄；未确认或明确未受理时抛出携带完整句柄的 `WorkflowPending` / `WorkflowFailed` |
| `cancel_resident_invocation(resident_id, actor, message_id)` | 取消一条输入 |
| `run_resident_turn(...)` | 以“提示词 + Provider + 工作目录”的形式调用，映射到 `invoke_and_wait`；`allow_subagents` 写入运行设置 |
| `run_resident_completion(...)` | 以“一次模型补全”的形式调用：`provider_id`、`model`、`system_prompt`、`user_prompt`、`parameters`、可选历史 `messages`、`timeout_seconds`、`max_retries` 组成 `provider_api` 后端的执行配置 |
| `run_shared_search_turn(...)` | 语义搜索的隔离调用，见下文 |

`submit_invocation` 的参数：

| 参数 | 说明 |
| --- | --- |
| `actor` | 必填，当前已认证的调用者 |
| `resident_scope`、`lineage_scope` | 复用范围，见“基本概念” |
| `resident_binding`、`lineage_binding` | 目标来源，见“找到或创建 Resident” |
| `message` | 用户文本 |
| `input_media` | 可选图片或文件，只接受 `image` 与 `file` 两种分段 |
| `context_refs`、`runtime_context`、`additional_skills`、`cwd` | 合成一个 `context_reference` 分段：文本放入 `context_text`，Skill 名称（去重、保留首次顺序）放入 `instruction_skills`，目录放入 `working_directory`；多个来源给出不同目录时由消息协议拒绝 |
| `backend`、`backend_route_id` | 后端（默认 `codex`）与账号路线 |
| `runtime_settings`、`execution_profile` | 运行设置；`provider_api` 后端的执行配置 |
| `title` | 新建 Resident 的名称，默认 `Embedded Resident` |
| `message_id`、`business_ref` | 消息 ID 的来源，见“提交” |
| `metadata` | 关联中保存的展示信息（例如资源 ID 与类型），不参与鉴权 |
| `on_assigned`、`cancellation_requested`、`workflow_binding` | 见上文 |

## 语义搜索的隔离调用

语义搜索由共享的搜索 Resident 为许多用户服务。每个搜索页面（surface）在 Portal 范围职责登记中有一项 `search.<surface>`，指定负责这个页面的 Resident（decision_eb066fbea4ed）。一次搜索被整理成一个或多个批次，每个批次是投递给这个 Resident 的一条标准输入，并登记“输入范围授权”，使发起用户只能读取自己的这几条输入，并能通过业务收据路径取消它们；目标 Resident 的 admin 也可通过 ISA 取消（decision_c524c775e0be）。

### 搜索任务的身份

- **搜索由页面的任务记录发起。** 页面适配器从自己的任务存储中读出权威的 `created_by`、`surface`、`search_id`、`run_generation`：发起者必须是当前活跃的 Portal 用户；`search_id` 是稳定的搜索 ID（最长 200 字符）；`run_generation` 是每次刷新递增并持久保存的正整数，同一代的重试沿用原值。浏览器提交的请求不能指定调用者、Resident、Provider、历史、工具或执行配置。
- **一次搜索的计划在准备时冻结。** 准备阶段把本次要发送的每批提示词算成指纹，组成有序的批次清单（`batch-000001`、`batch-000002`……），再算出整个计划的指纹，连同当时的职责绑定一起保存。每个批次的消息 ID 由计划指纹和批次 ID 推导，因此重试总落到同一条输入。同一代中计划或某批输入发生变化时报冲突。

### 新调用的授权

- **每次准备和首次发送前都重新检查授权。** 检查内容：职责绑定的调用策略是“向活跃 Portal 用户开放”（`active_portal_users`）；发起者仍然活跃；以发起者身份能读到搜索 Resident 所在 Lineage 的资源记录，且这个 Lineage、Resident 及其成员关系都有效、未停用或删除；权限系统允许发起者调用这项服务。任何一项不满足都拒绝新调用，不借用配置负责人的身份，也不换用其他目标。
- **授权范围最小。** 这项授权只允许对已授权的候选发起登记过的搜索，不给予管理搜索 Resident、读写它的普通对话或读取其他用户调用的权限。候选正文、摘要、结果、缓存和任务存储仍由页面负责，并按真实调用者检查权限。
- **绑定在首次发送前变化时报冲突。** 职责绑定在准备之后、首次发送之前被改掉时，这次发送报冲突，需要重新准备。

### 发送、观察与取消

- **每个批次只发送一次。** 发送前登记这条输入的输入范围授权（记录发起者和提示词摘要），并取得一次性的发送权，然后以幂等键 `search:<message_id>` 投递。确认丢失时按同一个消息 ID 查询或重试，不会新建第二条输入。
- **等待使用普通的结果读取。** 批次发送后用 `resume_and_wait` 等待这条输入的结果；观察预算用完抛出“观察仍在进行”，调用方之后可以继续观察同一张收据；输入失败抛出搜索失败。成功时返回完整结果以及 `status`、`binding_fingerprint`、`run_generation`、收据、`batch_id` 和 `plan_fingerprint`；输入没有完成或回复为空时不返回成功文本。
- **取消不放宽隔离读取。** 取消指令可以读取目标输入的状态来完成操作；普通输入、消息和对话读取仍校验原调用者范围。普通消息的发送者身份不授予取消权限；服务输入的取消依据不可变业务收据，不依赖全局 Resident admin。取消失败时保留屏障和待取消状态，不借用其他主体。
- **取消和删除先写“屏障”。** `cancel_invocation` 先持久写入一条带原因的屏障（fence），再对仍在进行的批次发送取消；`deleted=True` 时同时关闭这个搜索之后的所有代次。还没准备时取消也会留下屏障。屏障让“准备前取消”“发送中取消”和“晚到的成功”都不会覆盖业务终态；它不保证已经在执行的模型调用立刻停止。每条屏障都必须写明原因，系统不从执行状态推断原因。
- **刷新产生新的一代。** 刷新保持 `search_id`，把 `run_generation` 加一，新一代可以使用当前重新批准的绑定。旧代已经结束的历史保留原状态和结果，读取时标为 `is_current_generation=false`，但不能再准备、发送或作为当前结果返回；还没结束的旧代运行和它晚到的成功都会被拒绝。页面写回自己的任务记录时，还要检查期望的代次、当前状态和删除标记。

### 调用记录

调用表把身份、Resident/Lineage、消息与执行 ID、状态和指纹存为明确列。绑定、授权依据、路由约束、结果引用和批次使用用途明确的 JSONB；批次不重复父调用的身份和共享绑定，接口读取时组装这些字段。`schema`、`batch_count`、是否当前代次及屏障信息由代码、批次数量和专属状态表投影，不写入整份 `record`。搜索任务仅保存自身业务状态，恢复时读取调用领域的同一份计划。

- 调用记录由 SocietyService 的服务调用领域负责（`society_service/service_invocation_domain.py`、`service_invocation_store.py`），保存在 PostgreSQL 的 `service_invocations` schema 中：主记录以 `(发起者, surface, search_id, 代次)` 为键，连接搜索 Resident 与 Lineage、冻结的绑定及其指纹、授权核查依据、计划指纹、每个批次的消息 ID 与状态；屏障保存在 `service_run_fences` 表。记录不包含候选正文、模型结果或密钥，也不作为工作区或页面状态公开。
- `completed`、`failed`、`cancelled` 终态不会被迟到的非终态观察改回。
- 服务端入口（都由可信的业务适配器传入权威任务，没有让浏览器自报调用者的 HTTP 入口）：

  | 方法 | 作用 |
  | --- | --- |
  | `prepare_batches(job, surface, batches)` / `prepare_invocation(job, surface, prompt)` | 冻结计划与绑定，检查新调用授权，保存收据，不发送 |
  | `submit_batch(...)` / `submit_invocation(job, surface, prompt)` | 再次检查授权与冻结的绑定，取得一次发送权并投递 |
  | `get_batch(...)` / `get_invocation(job, surface)` | 按发起者读取收据与输入状态；有屏障的记录不返回输入内容 |
  | `cancel_invocation(job, surface, deleted=False)` / `fail_invocation(...)` | 写入屏障并取消仍在进行的输入 |
  | `list_for_service(actor, resident_id, lineage_id, limit=100)` | 列出这个调用者对指定搜索服务的调用，最多 100 条，不含其他用户的记录 |

- 业务侧的等待与结果整理在 `management_portal/shared_search_invocation.py`；公开入口是 `resident_invocation.run_shared_search_turn(job, surface, prompt, timeout_seconds=120, cancellation_requested, on_assigned)`，它为单个提示词准备一个批次并执行。`on_assigned` 在发送前收到完整收据的副本，页面必须先保存成功才会发送。

## 其他后台能力使用的 Lineage

- Repo 简介与提交信息生成使用 Repo 的讨论 Lineage；Notice Board 的分类由 Portal 范围职责登记指定的 Resident 完成。调用时把目标 Lineage 作为 `lineage_binding` 传入，已有的调用者、scope 与 Resident ID 不变，成员关系以当前 Lineage 为准。登记不存在或无法访问时明确失败，不另建团队。
- 把旧的讨论 Resident 或定时工作的 Resident 合并到新的分组时：先创建普通的目标 Lineage，用 `MoveResident` 迁移已有成员并确认工作目录迁移完成，再更新职责登记中的引用、删除空的源 Lineage。分组不引入专用的 Resident 类型或指令，也不扩大任何用户的访问权限。
- **迁移前的身份取证。** Portal Admin 可以对自己有管理权的 Lineage 调用 `diagnose_lineage_invocations`，读出其中每个 Resident 被哪个用户的哪个 scope 关联、对应的资源 ID 和记录的资源类型；结果不包含消息、提示词或凭据。读取结束时重新核对 Lineage 版本和管理权限，期间有变化则报错。
- 通用的成员迁移可以迁入空的目标 Lineage；源与目标的成员变化在同一个事务中提交，工作目录搬移随后由持久的生命周期操作完成，重试使用原来的幂等回执。全部成员迁移并核验后才删除空的源 Lineage。

## 授权与失败恢复

- **目标由指令系统按真实调用者鉴权。** Invocation 在已有的 Lineage 中找到或创建目标，鉴权由目标 Resident、Lineage 和对应的 ISA 指令按真实调用者完成；调用层不额外要求“能创建 Project 团队”的资格，也不扫描其他 Project 作为退路。业务资源本身的访问检查由业务入口负责；失效的绑定保留并明确报错，由有权的主体修复。
- **提交前的失败不产生句柄。** 缺少调用者、scope 为空、找不到或无法创建身份时，在投递前失败；消息校验、接单、Provider 能力或投递失败由标准消息协议返回，不会伪造句柄。
- **身份已分配、提交结果未确认时，保留已记录的身份与请求。** 业务可以据此显示状态和对话入口，沿原指令与路径只读恢复；未知不授权重新发送。工作区交接门禁仍由交接协议负责。只有明确未受理后才能由业务另作重试决定；已确认输入仅继续等待。
- **业务事件触发的中断恢复。** 业务通过 Lineage 事件触发调用时，遇到传输中断或指令仍在执行，保留原请求参数和幂等键，恢复同一次指令的回执后再观察原消息。发送前的 Resident 绑定核对、账号迁移和配置准备也接续原来保存的意图；这一阶段收到取消就不发送消息，鉴权被拒立即结束。Paper、WALL 和 TAU 通过 Workbench 的公共调用适配层共享这套恢复边界：准备恢复期间已确认的 Resident 身份保持不变，模型调用不在重试范围内。客户端没拿到回执不能作为重新创建事件、Resident 或业务运行的理由。

## 约束

1. 同一调用者、同一 scope 只关联一个有效、后端一致的 Resident。
2. 提交请求持久保存恢复定位；只有确认实际输入后才填 message_id。未受理证据不足时保持 pending，不补造完成或重发业务。
3. 通知只负责唤醒，终态必须从持久状态或事件确认。
4. 业务取消只转发一次，输入的终态由 Resident 消息协议决定。
5. 业务任务和 Resident 输入各自有状态负责者，Invocation 只负责两者之间的映射。

业务工作目录通过 `GetResident` 核对现有绑定，再以 `BindWorkspace` 绑定缺少的目录。新建和复用业务 Resident 共用这一入口；生命周期操作尚在完成时沿用既有重试，绑定通过实际调用者的权限校验。
