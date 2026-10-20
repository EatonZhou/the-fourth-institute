---
kb_id: decision_c47a13e8b962
type: decision
project: AI员工
created_at: '2026-08-22'
version:
  number: 30
  created_at: '2026-08-22'
  created_by:
    user: ''
    agent: ''
  updated_at: '2026-10-19T21:27:27Z'
  updated_by:
    user: ''
    agent: resident_2e9ea0072551
importance: 9
metaness: 2
ai_generated: false
links:
  source:
  - ${code_root}/management_portal/society_service/isa/conversation.py
  - ${code_root}/management_portal/society_service/isa/residents.py
  - ${code_root}/management_portal/society_service/resident_lifecycle_domain.py
  - ${code_root}/management_portal/society_service/resident_intake_domain.py
  - ${code_root}/management_portal/society_service/resident_restart_domain.py
  - ${code_root}/management_portal/execution/session_manager.py
  - ${code_root}/management_portal/society_service/resident_backend.py
  - ${code_root}/management_portal/society_service/resident_domain.py
  - ${code_root}/management_portal/static/portal_chat_workspace.js
  - ${code_root}/management_portal/execution/agent_data_store.py
  - ${code_root}/tests/cases/test_session_restart.py
  - ${code_root}/tests/cases/test_resident_context_reset.py
  - ${code_root}/tests/cases/test_agent_data_store.py
  - ${code_root}/management_portal/society_service/resident_settings_contract.py
  - ${code_root}/management_portal/society_service/resident_runtime_domain.py
  related:
  - decision_5bf5bfbd52cb
  - decision_4dea0db1f45a
  - decision_91eb3626d323
  - decision_2fe6fd5367fb
  - decision_d19b9909a674
  - decision_0f57656a2acd
  - decision_4adcc1ff2f2c
  - decision_898b8911a2ff
  - decision_b1ab45c4b3ec
  - decision_6ad0384e020d
  - decision_3e8c2a7f91d4
  - decision_bd5217d08c09
  supersedes: []
  derived_from:
  - decision_0f57656a2acd
  - decision_898b8911a2ff
---
# Resident Session 重启、账号切换与上下文清空

Resident 是 Portal 中有稳定身份的 AI 节点。它长期保留 ID、所属 Lineage、Resident Folder 和工作区，同时通过某个模型服务商（Provider，例如 Codex、Claude Code、Kimi Code）的会话继续对话。会话会变长、账号会用完额度、有时需要换一个 Provider，这时用户需要在不改变 Resident 身份的前提下更换它的模型会话。

本篇说明两项操作：

- **重启会话（Restart Session）**：为同一个 Resident 准备一个新的模型会话，可以同时切换 Provider 或账号，并选择带入多少旧内容。新会话准备好之后才替换旧会话。
- **清空上下文（Reset Context）**：归档当前对话和运行资料，释放当前会话，让 Resident 从空白开始。

两项操作都保留 Resident 的身份、所属 Lineage、Resident Folder、工作区绑定和工作区中的文件。对应的 HATG ISA 指令是 `RestartSession`、`GetSessionRestart`、`CancelSessionRestart`（decision_d19b9909a674）和 `ClearContext`（decision_2fe6fd5367fb）。

## 基本概念

| 名称 | 含义 |
| --- | --- |
| 模型会话 | Provider 为一段连续对话保存的状态。重启和清空改变的是它，不是 Resident。 |
| 会话绑定（Provider binding） | ExecutionService 的会话管理器（SessionManager，decision_91eb3626d323）保存的一条记录，说明 Resident 当前使用哪个 Provider、哪个账号路线和哪个会话，以及配置指纹、系统指令已附加的次数和 Agent Harness 工具状态。这些字段只能整体替换。 |
| 对话 | Portal 保存并在页面显示的用户消息和 Agent 回复。 |
| 运行资料 | 当前任务、最近的错误、本轮用量、模型上下文窗口状态和会话的创建方式。当前控制事实由 Resident 与执行服务保存，已经结束的轮次和执行收据保存在持久运行记录中。 |
| 接单开关 | Resident 是否开始执行新到的消息。关闭时消息仍被接收并排队，只是不开始执行（decision_2a3ff353165c）。 |
| 重启记录 | 一次重启的进度，保存在 Resident 记录的 `session_restart` 中，包括 `restart_id`、来源与目标、所选策略、状态和阶段。 |

## 重启会话

### 用户在哪里操作

在 Resident **Menu → Settings → Backend** 或 Chat Workspace 中打开“Session 操作”面板。面板显示当前 Provider、账号和是否已有会话，提供“目标 Provider”“Account / Route”“上下文方案”“正在运行的工作”四个选择，以及“只刷新数据”“取消迁移”“关闭”“重启 Session”按钮。选项由服务端返回，页面不自行推断。重启进行中时，对话区上方显示进度条“Session：<来源> → <目标> · <阶段>”和已耗时；关闭窗口不会取消迁移。

### 弹窗刷新与提交校验

运行设置和 Session 操作由共享的 Resident 运行控制器管理。弹窗打开后，普通运行通知、Resident 数据刷新和快照补读保留用户已选的目标及提交能力；缓存版本只用于丢弃过时的读取结果。

提交前重新读取该 Resident 的权威投影，核对当前 Backend、执行账号、会话绑定的 Provider 与账号，以及当前操作权限。实际路线变化或权限不足时拒绝提交；读取失败时不发送修改请求。浏览器登录身份的变化单独使已打开的弹窗失效，异步校验前后都检查身份与弹窗是否仍然有效。进行中的迁移仍阻止冲突提交，同一弹窗的重复点击合并为一次请求；服务端继续负责最终授权、额度准入与迁移幂等。

### 设计点

- **只有 Resident 的 owner 可以重启会话。** 能看到 Resident 的人可以读取重启进度，但不能发起或取消。
- **新会话准备好之后才替换旧会话。** 准备期间旧会话保持不变；新会话准备失败、被取消或替换后的保存失败时，Resident 继续使用旧会话，页面提示“迁移未完成，原会话已保留”。被替换的旧会话保留下来，供审计和必要时恢复。
- **新会话与普通会话带同样的 Portal 工具。** 重启或切换账号时建立的新会话，在会话请求中带上 Resident 身份，因此和 Resident 平常的会话一样挂载 HATG 指令工具 `hatg_instruction` 等 Agent Harness 工具（decision_5c62deb8253a）。
- **重启期间 Resident 暂停开始新工作。** 发起重启时，Portal 关闭这个 Resident 的接单开关；新消息照常排队，等新会话切换完成或旧会话恢复后才执行。完成后，Portal 只重新打开由本次重启关闭、且之后没有被人工改动过的接单开关。
- **可以切换 Provider 和账号。** 可选 Provider 来自统一运行目录，包括 Portal 登记的全部 Agent Provider 和 Model API。没有指定账号时，同一 Provider 沿用当前账号；换了 Provider 时使用新 Provider 的默认账号。同一 Provider、同一账号时保留当前运行设置，否则按目标 Provider 和账号重新校准。候选准备时冻结目标运行配置，初始化上下文采用目标 Provider 的指令传输约定，不读取旧 Session 的账号或模型能力；切换完成后，目标 Provider、账号与两部分执行配置在同一次 Resident 业务提交中保存；准备期间不改动当前配置。
- **创建新会话使用目标账号的通用额度保护。** 目标账号具备额度保护能力时，Portal 读取 Scheduler 的 `quota_reserve`。Close 阈值为正时，额度状态须可用或处于预留区、数据新鲜且 `remaining` 严格大于零；阈值为零时跳过数值检查。`remaining` 的单位沿用账号的百分比或原币金额。受理记录的 `quota_admission` 保存本次实际读取的完整额度快照，包括状态、剩余值、阈值、新鲜度和采样时间，供调用方核对；不支持额度保护的账号记录为空。准备中的新会话获得一次 binding 放行，重启结束后撤销。账号契约见 decision_d40f59f5508f。
- **已有一次重启没有完成时，相同参数返回已有记录，不同参数先取消旧重启。** 通过 `RestartSession` 发起的新请求等待旧重启的生命周期清理完成后再开始；清理超过本次调用的等待期限时返回冲突，调用者稍后重试。
- **Codex 原生目标在重启后暂停。** Resident 有进行中的 Codex 原生目标（`/goal`）时，切换完成后该目标变为暂停，原因记为 `session_restart`，避免新会话在没有确认的情况下自动继续旧目标。

### 上下文方案

上下文方案决定新会话能看到多少旧内容。

| 方案 | 页面名称 | 新会话怎样建立 |
| --- | --- | --- |
| `smart`（默认） | 智能选择 | 同一 Provider、同一账号且 Provider 支持原生复制时按 `native` 处理，否则按 `compact` 处理。 |
| `native` | Provider 原生 Fork | RestartSession 的明确策略：让 Provider 直接从旧会话复制出新会话。只在同一 Provider、同一账号且 Provider 支持时可用；条件不满足时报错，不改用其他方案。它与当前 Portal Fork 冻结 trace 后创建独立 Session 的流程不同。 |
| `compact` | 精简交接 | 新会话先执行一轮初始化，初始化输入带上最近 12 条可见消息，合计最多 24,000 个字符。 |
| `visible` | 可见对话 | 同上，最多带 36 条消息、60,000 个字符。 |
| `fresh` | 全新上下文 | 新会话执行一轮空白初始化，不带任何旧对话。 |

`compact` 和 `visible` 的初始化输入以 `[Portal Session Initialization Context]` 开头，写明 Resident ID、当前 Codex 目标（没有时写“未设置”）和所选方案，要求模型整理与当前职责有关的事实、进度、决定、未决问题和约束，并说明本次初始化不派发业务任务、不启动后台目标。超过上限时先舍去较早的消息，单条消息过长时保留它较新的末尾部分。初始化完成后，新会话已经带着这些内容，之后的用户消息不再附带历史。

“全新上下文”也用于明确放弃尚未交付的当前 Fork 初始化材料（decision_5bf5bfbd52cb）。新的初始化任务既无材料也无接收确认时，保持任务失败事实，由用户作出这次清空选择；一次性历史整理无法恢复的内容则直接使用正常空 trace，不新增历史缺失状态。这时“Session 操作”面板显示阻塞原因，即使 Resident 还没有会话也允许选择方案，并说明“全新上下文”会实际创建会话、保留页面历史。新会话提升成功后，Portal 记录 `context_seed_discarded_at` 和 `context_seed_discarded_by_restart`，之后不再尝试交付原来的来源材料；失败或取消时不记录，旧会话、历史消息和来源记录都不变。以后再重启也不会撤销这个选择，Fork 出的新 Resident 不继承这两个字段。

### 正在运行的工作

- **选择“取消后重启”时，Portal 先请求停止当前工作。** 停止还没完成时，这次请求返回错误“当前工作正在停止，请稍后重试 session 重启”。
- **选择“等待完成”时，Portal 持久保存迁移请求并等待当前轮结束。** 关闭接单与保存延迟请求在同一事务提交，返回 `status=accepted`、`phase=waiting_current_turn`；正在执行的工作继续完成，新到的消息排队等待。服务恢复后按已保存的请求继续迁移，不需要用户再次提交。
- **只是在排队、还没有进入执行的消息不妨碍重启。** 消息、任务和队列位置保留。迁移记录接单开关的变更编号；成功、失败或取消时，只在编号仍一致且确由本次迁移关闭时重新打开。原先人工关闭的开关保持关闭，期间发生的新开关操作优先。工具刷新交给重启处理时沿用本次刷新的关闭编号。
- **已提交但尚未被 Worker 领取的额度等待请求可原子交接。** Society 持久记录原输入、迁移操作与原/替换 Execution 身份，再由 ExecutionService 在 Scheduler 行锁内确认仍为 queued、没有领取者、尝试、checkpoint 或 Stop。确认后只撤回内部 Execution，并在同一事务保存可回读的交接凭据；业务输入不记为取消，也不生成某人主动停止的通知。已经领取或事实不明时不强行交接。会话迁移完成后，以原消息和队列位置在新绑定上重新准备执行，不复用旧路由的 prepared prompt；迁移失败或取消则在原绑定恢复准备。公开 restart_id/request_id 保持不变，内部 execute 幂等键独立；丢失回执按固定交接身份核对，不能重复执行或凭超时推断成功。
- **没有会话、只有一条尚未被执行层认领的 Codex 执行时，直接改它的账号。** 这种情况下选择“等待完成”并换到同一 Provider 的另一个账号，Portal 把这条排队执行的账号路线改为目标账号，并保存为 Resident 的默认账号；执行、任务、消息和输入的 ID 都不变，也不创建会话。执行已被认领或已有会话时，不使用这条路径。 这项操作先核对当前调用者的 Resident 管理权限，再用原输入已核验的发起身份定位排队执行；实际切换账号的人单独记入操作审计。两种身份可以不同，切换不改变原输入的归属。缺少原输入身份时拒绝改派，不借用当前操作人或 Resident owner 的身份。
- **旧会话和新会话不会同时接收用户输入。**

### 还没有会话时只保存配置

原生 Backend 的 Resident 还没有模型会话（包括清空上下文之后还没有新会话）、也不需要用“全新上下文”恢复时，重启只保存目标 Provider、账号和运行设置，不创建会话，也不影响尚未交付的 Fork 来源材料。结果中 `status=completed`、`configuration_only=true`、`previous_session_retained=false`，页面显示“账号配置已保存，尚未创建 Session”。保存前重新检查会话绑定；这期间已经建立了会话时报错“当前 Session 已变化，请刷新后重试”。

### 与 Model API 双向迁移

迁移面板展示目标账号的模型能力。选择 Model API 后，可配置会话历史开关与预算、协议参数、超时和重试；Jev 使用 Choice／Score／Noul 问题表单。参数通过同一 `RestartSession` 请求交给目标协议校验，目标目录不可用时禁止提交。API 路线与记账账号身份分开，模型查询和迁移目标使用 API 登记 ID。

目标为 Model API 时，等待或停止当前执行后，在配置提交中切换 Backend 和账号、保存运行设置，解除当前原生会话绑定，返回 `configuration_only=true`，不启动模型初始化。原生会话仍保留；保存失败时只在绑定仍为空的前提下恢复原绑定，避免覆盖并发修改。原生 Codex goal 暂停。同一 API 路线保留已有设置，跨路线采用目标协议默认值：文本 API 默认携带历史、Jev 默认关闭，都允许显式覆盖。

Model API 不支持 `native`。非 `fresh` 方案直接按历史开关与预算读取规范对话，不额外生成交接摘要；`fresh` 写入规范的 `provider_context_reset` 边界，保留页面对话，但 API 后续历史不会越过该边界。

Model API Resident 迁移到原生 Backend 时，按选定上下文方案准备新 Session，即使来源没有原生会话，也要在初始化成功后才切换配置。失败或取消保留原来的 Model API 配置。两个方向复用当前执行等待、幂等、接单关闭与恢复机制，不另建迁移状态机。

### 进度与取消

- **新重启受理后，初始化在后台完成。** 返回结果中 `status=accepted`，带有 `restart_id` 和实际采用的方案；页面之后查询同一个 `restart_id` 的进度，看到 `completed` 才显示切换成功。页面刷新、网络中断或短暂的 HTTP 504 都不会撤销后台操作，也不能当作失败的证据。
- **进度分阶段显示。** `accepted` 状态下的阶段依次可能是：等待当前轮结束（`waiting_current_turn`）、排队（`queued`，或因额度预留、容量、派发间隔、会话绑定而等待的 `waiting_*`）、创建会话（`creating_session`）、初始化（`initializing`）、切换（`switching`）。终态是 `completed`、`failed` 或 `cancelled`；取消请求受理后到结束前为 `cancelling`。
- **可以按 `restart_id` 取消尚未完成的重启。** 页面上的“取消迁移”和 `CancelSessionRestart` 都按精确的 `restart_id` 取消；取消不检查账号额度，不提升准备中的新会话，旧会话保留。等待当前轮结束时也可以取消；删除延迟请求并按本次迁移的开关编号释放等待，不停止原来的当前轮。关闭“Session 操作”面板不等于取消。

## 清空上下文

### 设计点

- **只有 Resident 的 owner 可以清空上下文。** 其他人调用时报错“只有 Resident Owner 可以清空上下文”。
- **有任务正在排队或执行时不能清空。** 报错“当前仍有任务正在排队或处理，停止或完成后再清空上下文”；Resident 另有生命周期操作（例如重启）尚未完成时，也要稍后重试。
- **清空立即生效，归档在后台完成。** 服务在一次数据库事务中把 Resident 恢复为可以新建会话的状态，清除当前任务关联，清掉最近的错误、本轮用量、上下文窗口状态和会话创建方式，并登记归档步骤，然后立即返回 `cleanup_pending=true` 和操作 ID。
- **归档只包括清空开始时已有的内容。** 后台依次把当时已有的对话消息标为已归档，释放原来的会话绑定。这些步骤由持久的生命周期队列执行，服务重启后继续，直到完成。
- **清空动作保存为有位置的运行记录。** `context_archive` 保存 `context_reset` 操作身份、清空时间、执行者及归档消息边界，位置固定在清空时记录的边界；它不形成 Segment、不加入内容 trace，也不保存旧正文副本（decision_b1ab45c4b3ec）。清空后到达的新内容位于此边界之后，迟到的归档投影不能掩盖新消息。同一次操作幂等复用同一身份；`context_reset_at` 是最近一次状态观察，历次操作及顺序由运行记录承载。旧清空 Segment 在结构整理中转为运行记录并保留原位置。
- **清空期间新到的消息排队等待。** 归档完成后按顺序执行，由新的空白会话处理。
- **清空之前的会话不再算作上下文。** 清空时 Resident 记录 `context_reset_at`（毫秒精度）；在此之前运行过的 Provider 会话都属于被清空的上下文。所以清空后、发新消息之前重启，只保存配置（见“还没有会话时只保存配置”），既不跑交接，也不带上一会话的压缩摘要；这时 Fork 这个 Resident 得到空白上下文。清空之后开始的会话（包括绑定前就失败的一轮）照常处理。
- **尚未交付的 Fork 来源材料一并清空。** 还没开始第一轮的 Fork 被清空时，Portal 记录 `context_seed_discarded_at`，第一轮不再收到来源上下文，与“全新上下文”重启相同。
- **会话还在运行一轮时，释放等这一轮结束。** Claude 守护进程按 session key 复用存活的会话；它还在运行一轮时（例如工作进程交接后留下的一轮），释放报告失败，生命周期队列 5 秒后重试，以免下一轮接着旧会话。DSH 在这种情况下同样报错重试。
- **长期说明和文件保持不变。** Resident Folder 中的 `resident.md` 等长期说明、工作区绑定和工作区中的任何文件都不受影响。

### 页面行为

- 用户在 Chat Workspace 中确认“清空上下文”后，页面立即清除当前可见消息，并暂时禁用发送和停止按钮。
- 页面忽略清空开始之前到达的旧状态快照，避免已归档的消息重新出现在空白界面上。
- 成功后，页面让相关缓存失效并重新读取空白状态；失败时重新读取对话和会话状态，回到操作前的样子。

## 实现说明

### 重启的执行过程

```text
RestartSession（SocietyService，resident_restart_domain.restart_resident_session）
  1. 校验 owner、方案和运行中工作的处理方式；需要时停止工作或改写排队执行的账号
     选择等待当前轮：原子关闭接单并保存延迟请求，当前轮结束后继续以下步骤
  2. 没有会话：只保存配置，返回 completed + configuration_only
  3. 有会话：检查额度 → 生成 ExecutionRequest（native 用 fork，其余用一轮初始化 turn）
     → 在一个事务中关闭接单、写入 session_restart（accepted）、登记生命周期步骤
     → 立即返回 accepted
生命周期队列（_resume_resident_session_restart）
  4. 用保存的执行编号提交或重连同一次执行，新会话挂在临时绑定 session-restart:<restart_id> 下
     未结束时记录阶段、稍后再检查，不重新调用模型
  5. 执行成功后比较并替换正式绑定：只有正式绑定仍是开始时读到的那一条才替换
  6. 将目标 Provider、账号与候选的 execution_settings/harness_config 一起提交，暂停 Codex 原生目标，重新打开由本次关闭的接单开关
     业务保存失败时恢复原绑定；最后确认执行回执
```

发起请求时计算来源会话的“代次”（由 Provider、账号和会话标识算出的摘要），记录在重启记录中。`RestartSession` 不接受版本前提：与 SocietyService 的其他指令一样，调用者提供的 `expected_version` 不做比较（decision_d918ebebc062），重启作用于发起请求时的当前会话；同一 Resident 已有未完成的重启时，相同参数返回已有记录，不同参数的新请求取消并接替它（decision_d19b9909a674）。进程在替换之后中断时，恢复流程先核对已提升的目标会话身份，再继续保存业务结果。

### 清空的执行过程

`resident_intake_domain` 中的清空入口在 Resident 状态锁内完成检查，在同一个事务里保存重置后的 Resident 记录、清除当前任务，并向生命周期队列登记三个步骤：`context_archive`（保存带位置的清空运行记录，再按开始时的消息边界归档对话）、`archive`（用一次目录改名把运行资料移入归档目录，目标已存在时报错、不覆盖）和 `release`（释放原会话绑定）。归档不改变 Resident 身份，也不移动 Resident Folder 或工作区。

### 状态归属

- SocietyService 保存 Resident 身份、owner 权限、接单开关、有限重启记录和生命周期队列；当前执行配置由 `resident_execution_configuration` 唯一拥有，不由 `runtime/settings.json`、Resident 顶层模型字段或 Conversation 的设置副本决定。
- SessionManager 负责会话绑定的创建、验证、替换和释放。
- AgentData 保存不可变内容、当前 trace 和清空等运行操作；Chat service 负责页面消息的展示与归档窗口，不把清空动作变成模型内容。
- Resident Folder 与工作区的结构分别见 decision_ccb99a00eebd 和 decision_8838269736d3。

### 设置读取与候选提交边界

模型、推理强度、服务等级、权限/沙箱/审批、初始指令、子 Agent 和协作模式属于 `execution_settings`；Harness mode 与已注册 feature 的结构参数属于 `harness_config`。两部分在数据库中分别保存为受约束 JSON，组合后的 `runtime_settings` 只是 API 读投影。页面、重启、Fork 与执行层统一通过当前设置 owner 读取，未知字段或功能不进入持久配置。

开始重启时读取一份当前配置，经目标 Provider 与账号校准后保存为该次操作的 `target_runtime_settings` 快照。候选 ExecutionRequest 使用这份快照；它明确属于一个 restart_id，并不是另一份可写当前设置。配置变化通知只保留操作时的回执，不保存 Conversation 当前设置副本。

`_apply_resident_session_target` 只把 Provider、账号及目标两部分配置暂存到候选 Resident，不能在准备新会话时直接调用当前设置写入 API。准备失败、取消或被替换时，当前 Provider 与配置仍为原值。会话提升按原 Binding 比较并切换；随后一次 Resident 业务提交同时保存目标 Provider、账号与候选配置，保存失败时按生命周期恢复契约撤销提升。只有配置的路径也使用同一提交规则，不建立 Provider 与配置短暂不匹配的当前真值。

清空上下文改变会话和窗口边界，保留当前执行配置、Harness 结构以及真实来源证明。历史 `native_fork` 与 `historical_adoption` 只说明过去的来源和初始化方法；它们不要求恢复旧 Session，不生成运行分支，也不改变 RestartSession 显式选择的当前策略。无法恢复的历史使用普通空 trace，后续运行仍走唯一实时内容入口。

### 实现来源与入口定位

会话重启与清空上下文的实际实现分别位于 `resident_restart_domain.py` 和 `resident_intake_domain.py`，公开 Resident 操作经 `resident_domain.py` 聚合。`lineage_domain.py` 是 Lineage 聚合装配入口，不另行实现重启或清空；保留该入口说明，不把它列作本规则的具体实现文件。

原语静态定义 `lineage_primitive_definitions.json` 提供通用图编辑和事件字段，不包含 `RestartSession` 或 `ClearContext` 操作实现；重启与清空通过上述实际领域函数和 Session 操作面板进入。此文件作为编辑器背景定位保留，不列作本规则的直接实现来源。
