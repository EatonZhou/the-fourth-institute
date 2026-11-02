---
kb_id: decision_c47a13e8b962
type: decision
project: AI员工
created_at: '2026-08-22'
version:
  number: 45
  created_at: "2026-08-22"
  created_by:
    user: ""
    agent: ""
  updated_at: "2026-11-02T21:33:17Z"
  updated_by:
    user: ""
    agent: "resident_dbcd3920b7cc"
importance: 9
metaness: 2
ai_generated: false
links:
  source:
  - /opt/tfi/the-fourth-institute-dev/code/management_portal/society_service/isa/conversation.py
  - /opt/tfi/the-fourth-institute-dev/code/management_portal/society_service/isa/residents.py
  - /opt/tfi/the-fourth-institute-dev/code/management_portal/society_service/resident_lifecycle_domain.py
  - /opt/tfi/the-fourth-institute-dev/code/management_portal/society_service/resident_intake_domain.py
  - /opt/tfi/the-fourth-institute-dev/code/management_portal/society_service/resident_restart_domain.py
  - /opt/tfi/the-fourth-institute-dev/code/management_portal/execution/session_manager.py
  - /opt/tfi/the-fourth-institute-dev/code/management_portal/society_service/resident_backend.py
  - /opt/tfi/the-fourth-institute-dev/code/management_portal/society_service/resident_domain.py
  - /opt/tfi/the-fourth-institute-dev/code/management_portal/static/portal_chat_workspace.js
  - /opt/tfi/the-fourth-institute-dev/code/management_portal/execution/agent_data_store.py
  - /opt/tfi/the-fourth-institute-dev/code/tests/cases/test_session_restart.py
  - /opt/tfi/the-fourth-institute-dev/code/tests/cases/test_resident_context_reset.py
  - /opt/tfi/the-fourth-institute-dev/code/tests/cases/test_agent_data_store.py
  - /opt/tfi/the-fourth-institute-dev/code/management_portal/society_service/resident_settings_contract.py
  - /opt/tfi/the-fourth-institute-dev/code/management_portal/society_service/resident_runtime_domain.py
  related:
  - decision_ccbb1cac24aa
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

能力配置、会话装配与账号切换的统一规范入口为 decision_ccbb1cac24aa。本篇规定会话生命周期，按用户目的区分三项操作：

- **切换账号**：优先保留当前有效上下文，无法完整保留时按下文展示摘要交接并由用户提交；等待当前工作完成后切换，准备失败保留原账号与会话。
- **重新开始会话**：显式选择保留有效上下文、模型交接或空白开始，不承担普通换账号的入口含义。
- **清空上下文（Reset Context）**：归档当前对话和运行资料，释放当前会话；下次真实输入才建立空白会话。

这些操作都保留 Resident 的身份、所属 Lineage、Resident Folder、工作区绑定和文件。公开接口复用 `RestartSession`、`GetSessionRestart`、`CancelSessionRestart`（decision_d19b9909a674）和 `ClearContext`（decision_2fe6fd5367fb），各操作意图及上下文策略必须明确。

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

Resident Menu、Chat 运行摘要和 Family 成员入口共用运行控制器。“保存参数”和“保存配置”不换账号、不创建模型轮次。账号切换表单根据后端 context_options 自动选用可用的完整保留方案；无法完整保留且支持 compact 时，展示摘要交接的范围、可能遗漏细节与工具过程的影响，主按钮显示“交接并切换”。用户无需选择底层策略，点击按钮才提交；选账号本身不执行操作。不支持完整保留或摘要交接时禁止切换，不自动使用 visible 或 fresh。完整保留提交 operation=switch_account、context_strategy=preserve；已说明影响的摘要交接提交 operation=restart、context_strategy=compact。两者默认 wait，准备失败均保留原会话；失败后不得静默降级。更换目标重新计算方案并更新说明，刷新不覆盖编辑。跨 Provider 仍须明确选择目标模型。空白开始只在独立“重新开始会话”入口明确选择。运行参数、能力配置、账号切换和重新开始会话在同一张尺寸稳定的卡片内切换标签，标题、标签栏及底部操作保持可见，长内容仅在中间区域滚动。切换标签保留未保存编辑。关闭按钮、取消、Escape 与点击卡片外区域均可退出并恢复页面交互；关闭页面不取消已受理的操作。模型与运行参数区不显示绑定诊断；保留非主要的刷新操作，能力生效明细默认折叠。

相同 Provider、相同稳定账号 ID 的普通切换是 noop，不创建候选，也不显示 zero → zero。用户明确要求同账号重建时从“重新开始会话”入口发起。刷新保留未提交编辑；关闭界面不取消已经受理的操作。进度与错误注明动作、时间及当前实际绑定，后续成功后旧失败归入历史。

### 弹窗刷新与提交校验

运行设置和 Session 操作由共享的 Resident 运行控制器管理。弹窗打开后，普通运行通知、Resident 数据刷新和快照补读保留用户已选的目标及提交能力；缓存版本只用于丢弃过时的读取结果。

提交前重新读取该 Resident 的权威投影，核对当前 Backend、执行账号、会话绑定的 Provider 与账号，以及当前操作权限。实际路线变化或权限不足时拒绝提交；读取失败时不发送修改请求。浏览器登录身份的变化单独使已打开的弹窗失效，异步校验前后都检查身份与弹窗是否仍然有效。进行中的迁移仍阻止冲突提交，同一弹窗的重复点击合并为一次请求；服务端继续负责最终授权、额度准入与迁移幂等。

### 设计点

- **发起和取消会话重启需要对该 Resident 的 `@contributor` 权限。** 读取重启进度需要 `@viewer`；两者都由当前权限方案判定（decision_b820faf42218）。
- **新会话准备好之后才替换旧会话。** 准备期间旧会话保持不变；新会话准备失败、被取消或替换后的保存失败时，Resident 继续使用旧会话，页面提示“迁移未完成，原会话已保留”。被替换的旧会话保留下来，供审计和必要时恢复。
- **候选保留完整平台配置。** 切换或重启使用当前设置 owner 的完整能力与 Prompt 配置，包括 Tools、MCP、Skills、Portal 注入策略和业务指令；按目标 Provider 验证并记录实际装配结果。只复制一个工具名称不能证明配置已保留。`hatg_instruction` 的参数定义与执行处理来自正式 ISA，Markdown 可以强调其使用方式。
- **普通输入沿用当前会话。** 工具名称记录缺失、为空或不包含 `hatg_instruction`，均不得自动关闭接单、创建替代 Session 或执行精简交接。会话工具元数据不构成自动重建的触发条件；显式 `RestartSession` 的策略选择、已受理迁移的恢复与原会话故障恢复各自遵守原有契约。历史自动工具刷新记录保留用于审计，不据此发起新的重建。
- **重启期间 Resident 暂停开始新工作。** 发起重启时，Portal 关闭这个 Resident 的接单开关；新消息照常排队，等新会话切换完成或旧会话恢复后才执行。完成后，Portal 只重新打开由本次重启关闭、且之后没有被人工改动过的接单开关。
- **跨 Provider 显式选择模型。** 目标 Provider 与账号来自已鉴权目录；同 Provider 未指定账号时沿用当前账号，跨 Provider 的目标账号和模型由调用方明确选择，不静默采用默认模型。完整保存平台能力与 Prompt 配置，逐项校验目标 Model、Effort、Mode、Speed、Permissions 及 Provider 专属参数；不兼容时明确拒绝，不静默丢字段。准备时冻结目标配置，成功提交后才替换当前 Provider、账号和配置。
- **创建新会话使用目标账号的通用额度保护。** 目标账号具备额度保护能力时，Portal 读取 Scheduler 的 `quota_reserve`。未提供本次credits授权时，Close阈值为正须额度状态可用或处于预留区、数据新鲜且remaining严格大于零；阈值为零时跳过数值检查，但已确认额度用完仍拒绝。人类可以在会话设置明确确认本次目标账号与新建/迁移Session使用credits；确认、采样保护及范围契约统一见decision_d40f59f5508f。`remaining` 的单位沿用账号的百分比或原币金额。受理记录的 `quota_admission` 保存本次实际读取的完整额度快照，包括状态、剩余值、阈值、新鲜度和采样时间，供调用方核对；不支持额度保护的账号记录为空。准备中的新会话获得临时binding放行，重启结束后撤销。付费确认的候选例外只允许目标账号的唯一准备Execution，成功后才持久绑定到实际提升的目标Session，取消或失败不产生目标授权。账号契约见 decision_d40f59f5508f。
- **已有一次重启没有完成时，相同参数返回已有记录，不同参数先取消旧重启。** 通过 `RestartSession` 发起的新请求等待旧重启的生命周期清理完成后再开始；清理超过本次调用的等待期限时返回冲突，调用者稍后重试。
- **Codex 原生目标在重启后暂停。** Resident 有进行中的 Codex 原生目标（`/goal`）时，切换完成后该目标变为暂停，原因记为 `session_restart`，避免新会话在没有确认的情况下自动继续旧目标。

### 上下文方案

上下文方案区分完整保留、明确发起的模型交接和空白开始。界面优先 preserve，目标只支持摘要交接时先说明影响并显示“交接并切换”；API 的 preserve 仍是严格保留合同，不能在执行失败后隐式降级。

| 方案 | 用途 | 新会话怎样建立 |
| --- | --- | --- |
| `preserve`（默认） | 保留有效上下文 | 使用可验证的原生完整 Fork，或严格的 canonical history 导入；不执行总结模型轮次。 |
| `smart`（旧调用兼容） | preserve 的旧名称映射 | 归一为 preserve；不得自动选择 compact。新界面不提供该选项。 |
| `native` | 显式原生 Fork | 仅在同 Provider、同账号且已验证支持时使用；不支持则拒绝，原绑定不变。 |
| `compact` | 显式精简交接 | 独立、可审计的模型交接，输入最多最近 12 条可见消息、24,000 字符，并说明可能丢失细节。 |
| `visible` | 显式可见对话交接 | 独立模型交接，最多 36 条可见消息、60,000 字符；不声称完整保留。 |
| `fresh` | 显式空白开始 | 通常提交清空有效上下文边界并进入pending_create，由下一条真实输入懒建；本次人类credits确认时先以无模型候选创建固定目标Session，再条件提交清空边界与授权，不执行整理或空白初始化轮次。 |

preserve 保留已有清除、压缩边界之后的有效摘要、消息、工具调用与对应结果、必要媒体及来源。canonical history 导入必须核对内容类型、调用/结果配对、顺序、媒体可读性和目标容量，并取得可验证的导入结果；历史工具调用只作为记录，不重新执行。不能用截断的文本历史、隐式 compact 或确认话术代替完整导入。

Codex 跨账号可使用严格、可验证的 canonical history 导入。其他 Provider 的跨账号及跨 Provider 完整导入当前未支持时，明确拒绝 preserve，保持原绑定和配置；不能自动降级到 compact、visible 或 fresh。源会话不存在时仍须验证有效上下文，单有旧 session ID 不构成恢复证据。 对 Portal 已记录且明确为空的 Provider 上下文，保留重建可以创建无模型轮次的新线程，不要求空线程已有 rollout；仍待交付的 Fork 来源材料保留在原有效上下文中，供首条真实输入交付。未知或无法验证的上下文不能按空上下文处理。

只有用户明确发起 compact 或 visible 才发送模型交接输入（账号切换页通过已说明影响的“交接并切换”提交 compact）；它以 `[Portal Session Initialization Context]` 标明身份、方案与目的，要求整理事实、进度、决定、未决问题和约束。这轮有独立输入、用量与结果记录，不派发业务任务或启动后台目标。超过各方案上限时按该显式交接合同截取，成功后后续消息沿用新会话。

fresh 也可显式放弃尚未交付的 Fork 来源材料（decision_5bf5bfbd52cb）。没有材料或接收确认的历史初始化失败保持可审计，不能伪造完成。fresh 提交成功时在 AgentData 记录一次 `provider_context_reset` 操作，作为有效上下文清除边界，保留页面历史和来源证明；失败或取消保留原状态。没有本次credits确认时，由之后真实输入创建空白会话；明确确认时沿候选创建协议固定目标空Session。

### 正在运行的工作

- **普通切换默认等待当前工作完成。** Portal 持久保存 preserve 请求，在同一事务关闭接单并记录 `status=accepted`、`phase=waiting_current_execution`；当前工作继续，新输入排队。当前轮结束后冻结最新有效上下文边界，服务恢复后沿原操作继续。
- **停止当前工作必须显式选择。** 用户可在等待状态请求“停止当前工作并切换”；先走正式停止机制，结果明确后再推进。停止未完成或工具业务结果未知时不推断完成、不重做业务。最近失败输入不随账号切换自动重试，须另行选择具体任务的正式恢复入口。
- **只是在排队、还没有进入执行的消息不妨碍重启。** 消息、任务和队列位置保留。迁移记录接单开关的变更编号；成功、失败或取消时，只在编号仍一致且确由本次迁移关闭时重新打开。原先人工关闭的开关保持关闭，期间发生的新开关操作优先。
- **已提交但尚未被 Worker 领取的额度等待请求可原子交接。** Society 持久记录原输入、迁移操作与原/替换 Execution 身份，再由 ExecutionService 在 Scheduler 行锁内确认仍为 queued、没有领取者、尝试、checkpoint 或 Stop。确认后只撤回内部 Execution，并在同一事务保存可回读的交接凭据；业务输入不记为取消，也不生成某人主动停止的通知。已经领取或事实不明时不强行交接。会话迁移完成后，以原消息和队列位置在新绑定上重新准备执行，不复用旧路由的 prepared prompt；迁移失败或取消则在原绑定恢复准备。公开 restart_id/request_id 保持不变，内部 execute 幂等键独立；丢失回执按固定交接身份核对，不能重复执行或凭超时推断成功。
- **仅保存配置、没有会话且只有一条尚未被执行层认领的 Codex 执行时，可直接改它的账号。** 这种情况下选择“等待完成”并换到同一 Provider 的另一个账号，Portal 把这条排队执行的账号路线改为目标账号，并保存为 Resident 的默认账号；执行、任务、消息和输入的 ID 都不变，也不创建会话。执行已被认领或已有会话时，不使用这条路径。需要创建候选 Session 的请求（包括人类明确确认本次 credits 的 fresh 或 preserve）不使用直接改派；已提交未领取的原输入按上述原子交接协议保留，再准备并条件提升目标 Session，不能先改派旧执行再让它与新会话创建竞争。 这项操作先核对当前调用者的 Resident 管理权限，再用原输入已核验的发起身份定位排队执行；实际切换账号的人单独记入操作审计。两种身份可以不同，切换不改变原输入的归属。缺少原输入身份时拒绝改派，不借用当前操作人或 Resident owner 的身份。
- **旧会话和新会话不会同时接收用户输入。**

### 无会话、注册与配置保存

注册身份、保存运行参数或能力配置、建立空会话都不产生“请整理并确认初始化”的隐藏模型任务。还没有原生会话且没有本次credits确认时，切换通过配置事务保存目标账号和完整配置；保留已暂存且仍有效的来源上下文，由首条真实输入完成懒建与交付。结果可返回 `completed`、`configuration_only=true`，明确尚未创建 Session。提交前检查绑定与设置版本，已经变化则拒绝过期候选。

无原生会话不等于没有上下文：待交付来源材料或 API 有效历史仍需按 preserve 校验；目标无法完整承载时拒绝，不能借配置保存路径丢弃历史。fresh按显式清空合同提交，不影响审计历史；没有本次credits确认时不提前创建会话。提供确认时以候选创建、条件提升和范围授权事务固定实际目标会话，不发送模型请求。

### 与 Model API 的边界

Model API 继续使用统一设置与已登记路线 ID，配置目录展示其协议参数、超时、重试、历史开关与预算；Jev 保留自身问题表单。目标模型必须显式校验，目录不可用时拒绝提交。

原生 Provider 与 Model API 之间的 preserve 属于跨 Provider 完整保留；当前完整导入未支持时明确拒绝并保留原绑定，不把 API 历史开关或预算截取当作完整保留。账号切换页仅在目标支持 compact 时展示摘要交接及其影响；目标不支持保留与摘要时禁止切换。fresh必须单独明确发起，通常写入规范清除边界并解除活动绑定，由下一条真实输入执行；本次credits确认则按候选创建与提升协议固定目标空Session；旧原生会话与审计历史保留，API 后续历史不越过清除边界。所有候选仍使用同一等待、幂等、取消和条件提交机制。

### 进度与取消

迁移进度入口与修改入口分开控制。已有迁移处于 accepted、submitting、confirming 或 cancelling 时，仍可打开原迁移的进度面板、刷新状态及按权限取消；这些状态仅阻止冲突提交和运行设置修改，不阻止读取进度。打开与提交仍分别核验当前身份和权限。

延迟迁移按 Resident 独立推进。一个 Resident 的交接回执暂时不可确认或发生身份冲突时，保留其持久意图供后续核对并记录对应 Resident 的错误，不中断其他迁移或后续执行恢复扫描。独立上下文（例如定时触发）的 Resident turn 可以是无会话绑定的 stateless 执行。交接这类未启动输入时，须由不可变 Execution 请求同时确认 agent_id、metadata.resident_id、business_type=resident 及原输入 client_user_message_id，且逻辑 binding_id 必须属于同一 Resident；缺原输入归属、其他业务和跨 Resident 均不得交接。普通 stateless 调用仍返回未撤回并保留原执行，持久会话的绑定不匹配仍拒绝。两类合法交接都必须在同一行锁下确认尚未领取、尝试次数为零、无 checkpoint、无 Stop，保留原输入与随附 info、唯一替代 Execution 及幂等回执；迁移失败/取消则按原交接合同在保留账号上恢复准备。任何错误都不能被解释成已撤回或迁移成功。

- **受理后在后台准备候选。** 返回 `status=accepted`、`restart_id` 和实际方案；页面查询同一操作，只有 `completed` 才显示切换成功。刷新、断线或 HTTP 504 不撤销操作，也不证明失败。
- **进度反映实际动作。** 等待、排队、创建或导入、核验与切换分别可观察；只有显式 compact/visible 才有模型交接阶段。终态为 completed、failed 或 cancelled，取消处理期间为 cancelling。无模型轮次的 preserve/fresh 不展示正在模型初始化。
- **取消指向确切 restart_id。** 取消候选不提升新绑定、不检查额度；等待时取消不停止原工作，按本次接单开关编号释放等待。已提升并接受目标输入后，不能用取消回滚历史；返回旧账号需要新的操作。关闭面板不等于取消。

## 清空上下文

### 设计点

- **清空上下文需要对该 Resident 的 `@admin` 权限。** 没有权限的调用按权限系统返回拒绝。
- **有任务正在排队或执行时不能清空。** 报错“当前仍有任务正在排队或处理，停止或完成后再清空上下文”；Resident 另有生命周期操作（例如重启）尚未完成时，也要稍后重试。
- **清空立即生效，归档在后台完成。** 服务在一次数据库事务中把 Resident 恢复为可以新建会话的状态，清除当前任务关联，清掉最近的错误、本轮用量、上下文窗口状态和会话创建方式，并登记归档步骤，然后立即返回 `cleanup_pending=true` 和操作 ID。
- **归档只包括清空开始时已有的内容。** 后台依次把当时已有的对话消息标为已归档，释放原来的会话绑定。这些步骤由持久的生命周期队列执行，服务重启后继续，直到完成。
- **清空动作保存为有位置的运行记录。** `context_archive` 保存 `context_reset` 操作身份、清空时间、执行者及归档消息边界，位置固定在清空时记录的边界；它不形成 Segment、不加入内容 trace，也不保存旧正文副本（decision_b1ab45c4b3ec）。清空后到达的新内容位于此边界之后，迟到的归档投影不能掩盖新消息。同一次操作幂等复用同一身份；`context_reset_at` 是最近一次状态观察，历次操作及顺序由运行记录承载。旧清空 Segment 在结构整理中转为运行记录并保留原位置。
- **清空期间新到的消息排队等待。** 归档完成后按顺序执行，由新的空白会话处理。
- **清空之前的会话不再算作上下文。** 清空时 Resident 记录 `context_reset_at`（毫秒精度）；在此之前运行过的 Provider 会话都属于被清空的上下文。所以清空后、发新消息之前重启，只保存配置（见“还没有会话时只保存配置”），既不跑交接，也不带上一会话的压缩摘要；这时 Fork 这个 Resident 得到空白上下文。清空之后开始的会话（包括绑定前就失败的一轮）照常处理。
- **尚未交付的 Fork 来源材料一并清空。** 还没开始第一轮的 Fork 被清空时，它从来源得到的上下文同属被清空的内容，第一轮不再收到来源上下文，与“全新上下文”重启相同。
- **会话还在运行一轮时，释放等这一轮结束。** Claude 守护进程按 session key 复用存活的会话；它还在运行一轮时（例如工作进程交接后留下的一轮），释放报告失败，生命周期队列 5 秒后重试，以免下一轮接着旧会话。DSH 在这种情况下同样报错重试。
- **长期说明和文件保持不变。** Resident Folder 中的 `resident.md` 等长期说明、工作区绑定和工作区中的任何文件都不受影响。

### 页面行为

- 用户在 Chat Workspace 中确认“清空上下文”后，页面立即清除当前可见消息，并暂时禁用发送和停止按钮。
- 页面忽略清空开始之前到达的旧状态快照，避免已归档的消息重新出现在空白界面上。
- 成功后，页面让相关缓存失效并重新读取空白状态；失败时重新读取对话和会话状态，回到操作前的样子。

## 实现说明

### 重启的执行过程

~~~text
RestartSession（SocietyService，会话操作 owner）
  1. 校验权限、明确操作意图、策略、目标模型与完整配置；
     普通切换同一稳定账号直接 noop，smart 旧值只映射 preserve。
  2. 默认等待当前工作结束；持久保存请求并关闭由本操作管理的接单。
  3. 在短锁内冻结源绑定、设置版本、有效上下文边界与目标配置快照。
  4. 锁外准备：preserve 验证原生 Fork 或严格导入；
     compact/visible 才提交显式模型交接；fresh通常准备清空边界与懒建状态，本次credits确认则先创建无模型候选Session。
  5. 验证候选绑定、上下文与能力装配回执；
     按源绑定与设置版本做 CAS，拒绝过期候选。
  6. 原子提交目标 Provider、账号、完整配置及上下文状态；
     恢复本操作关闭且未被外部改动的接单，排队输入保留原 ID 与顺序。
~~~

原操作身份、幂等键、源绑定和配置版本随持久记录保存。慢步骤重连同一次执行或导入，不以超时判定成功；响应丢失先查询原操作。设置保存携带 GetResidentSettings 返回的 settings_revision 作为 expected_revision；会话候选提交同时验证绑定和配置版本。失败或 CAS 冲突保留当前权威绑定与配置，不用旧快照覆盖并发新状态。进程在提升后中断时，先核对目标绑定身份与已保存回执，再完成同一业务提交；不能重复模型交接或自动重试旧失败输入。

### 清空的执行过程

`resident_intake_domain` 中的清空入口在 Resident 状态锁内完成检查，在同一个事务里保存重置后的 Resident 记录、清除当前任务，并向生命周期队列登记三个步骤：`context_archive`（保存带位置的清空运行记录，再按开始时的消息边界归档对话）、`archive`（用一次目录改名把运行资料移入归档目录，目标已存在时报错、不覆盖）和 `release`（释放原会话绑定）。归档不改变 Resident 身份，也不移动 Resident Folder 或工作区。

### 状态归属

- SocietyService 保存 Resident 身份、owner 权限、接单开关、有限重启记录和生命周期队列；当前执行配置由 `resident_execution_configuration` 唯一拥有，不由 `runtime/settings.json`、Resident 顶层模型字段或 Conversation 的设置副本决定。
- SessionManager 负责会话绑定的创建、验证、替换和释放。释放操作使用生命周期记录捕获的精确绑定、Provider 和账号路由，不启动模型轮次，执行请求的 cwd 为空。Workspace 已删除或默认绑定已解除时仍可释放；当前绑定已被替换则拒绝释放新会话。释放沿用持久操作与 Execution 身份恢复，只有确认成功后才删除对应绑定。工作目录存在性继续由创建会话和执行输入的入口校验。
- AgentData 保存不可变内容、当前 trace 和清空等运行操作；Chat service 负责页面消息的展示与归档窗口，不把清空动作变成模型内容。
- Resident Folder 与工作区的结构分别见 decision_ccb99a00eebd 和 decision_8838269736d3。

### 设置读取与候选提交边界

模型、推理强度、服务等级、权限/沙箱/审批、业务指令、Prompt 注入策略、子 Agent、协作模式及 capability_config 属于 execution_settings；Harness 结构单独保存在 harness_config。数据库当前设置 owner 统一负责这两部分，runtime_settings 只是读投影，字段边界与 expected_revision 见 decision_8b2fa224616b。

候选 target_runtime_settings 冻结当前完整平台配置及显式目标改动。capability_config v1 的 Tools、MCP、Skills 和 Prompt 配置不能因换 Provider 被默认值覆盖；不兼容字段明确报告。该快照属于一个 restart_id，不成为第二份可写当前配置。

_apply_resident_session_target 仅准备候选。提升和配置提交须同时验证原 binding 与设置 revision；保存失败按恢复合同处理，绝不覆盖并发更新后的绑定。只有配置的路径也使用同样的条件提交规则，保持 Provider、账号与配置一致。

清空保留执行配置、Harness 和真实来源证明。历史 native_fork/historical_adoption 只说明过去的来源，不强制恢复旧 Session。preserve 无法恢复所需历史时明确失败，保留原绑定；只有用户显式 fresh 或 ClearContext 才建立空白有效上下文。

### 实现来源与入口定位

会话重启与清空上下文的实际实现分别位于 `resident_restart_domain.py` 和 `resident_intake_domain.py`，公开 Resident 操作经 `resident_domain.py` 聚合。`lineage_domain.py` 是 Lineage 聚合装配入口，不另行实现重启或清空；保留该入口说明，不把它列作本规则的具体实现文件。

原语静态定义 `lineage_primitive_definitions.json` 提供通用图编辑和事件字段，不包含 `RestartSession` 或 `ClearContext` 操作实现；重启与清空通过上述实际领域函数和 Session 操作面板进入。此文件作为编辑器背景定位保留，不列作本规则的直接实现来源。


### Codex 原生有效上下文与账号切换

账号是执行资源，不以展示用思考摘要是否可重放限制普通切号。同 Provider 切号优先从当前绑定账号登记的原生会话取得有效上下文，核验原生 session 身份、完整记录及读取期间文件稳定性。最新原生 compaction 的 replacement_history 替换之前历史，再保留之后全部原生 response_item；消息、媒体、工具调用与结果、加密 reasoning 和原生压缩项目原样保存，不另外压缩、不展开已淘汰的窗口。无法确认有效窗口时不把未知读成空白。

原生窗口在执行数据 owner 中冻结为不可变、带原会话来源的 Segment 引用，沿既有严格导入及请求/确认回执进入候选会话。快照不改写旧 Segment、不补造 Chat 消息，不按文本相似度匹配历史身份。新会话确认后才条件提交绑定，原会话保留；权限、在途等待、幂等和取消机制保持。找不到原生归档时使用已有可完整验证的 canonical history；仅保存摘要的历史不冒充完整原生数据。

展示思考与迁移记录分工：展示只使用可读正文、摘要或加密占位，不能输出密文；Provider 明确提供的完整原生 reasoning（含 encrypted_content）保存原样用于迁移，不能仅记录 encrypted=true 后丢弃原件。实时完成事件未提供密文时如实记录展示摘要，迁移从已验证的原生有效窗口恢复，不伪造或重复补录思考事件。
