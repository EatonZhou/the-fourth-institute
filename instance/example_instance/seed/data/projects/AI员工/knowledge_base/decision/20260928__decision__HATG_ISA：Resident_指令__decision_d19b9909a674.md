---
kb_id: decision_d19b9909a674
type: decision
project: AI员工
created_at: '2026-09-28'
importance: 8
ai_generated: false
links:
  source:
  - /opt/tfi/the-fourth-institute-dev/code/management_portal/society_service/resident_initialization.py
  - /opt/tfi/the-fourth-institute-dev/code/management_portal/society_service/isa/residents.py
  - /opt/tfi/the-fourth-institute-dev/code/management_portal/society_service/resident_fork_domain.py
  - /opt/tfi/the-fourth-institute-dev/code/management_portal/society_service/resident_context
  related:
  - decision_898b8911a2ff
  - decision_0f57656a2acd
  - decision_ccb99a00eebd
  - decision_5bf5bfbd52cb
  - decision_8838269736d3
  - decision_4637ca4a69e2
  - decision_2fe6fd5367fb
  - decision_b1ab45c4b3ec
  - decision_6591e7793116
  supersedes: []
  derived_from:
  - decision_898b8911a2ff
  - thinking_ca8f397a47de
version:
  number: 77
  created_at: "2026-09-28T10:45:00Z"
  created_by:
    user: "weihao"
    agent: "resident_2a1fc6cd272d"
  updated_at: "2026-11-19T08:46:57Z"
  updated_by:
    user: ""
    agent: "resident_60ae6d0cdf74"
metaness: 2
---
# HATG ISA：Resident 指令

Resident 指令管理 Resident 本身：创建和删除 Resident、从已有 Resident Fork 出新的 Resident、修改名称和运行设置、切换模型会话使用的 Provider 与账号、为它绑定工作目录，以及把候选 Seed 提升为 Resident。所有指令共同遵守的调用格式、执行流程和错误语义见 decision_898b8911a2ff。

Resident 是可以跨多次模型会话持续存在的 Agent 身份。系统中只有一种 Resident，Resident 没有类型字段。一个 Resident 承担什么职责、什么时候开始工作，由它所在 Lineage 中的连接决定。例如，需要每天定时执行的工作，由一个 `time.reached` 事件源连接这个 Resident 的 Invoke 端口表达，任务说明写在这条连线上；评审、监督等职责由连线、循环组件和 Function Block 组合表达（decision_4637ca4a69e2、decision_5b5653c3eaa5）。

## 操作的对象

- **Resident 身份**：稳定的 `resident_*` ID、名称、所属 Lineage、固定拥有的自有工作区，以及系统分配的头像。每个 Resident 只属于一个 Lineage，由 `lineage_id` 表达当前归属，并在该 Lineage 中显示为一个节点。Resident 不保存恒定的 membership 分类；Lineage 自身的 `formal/personal` 分类保留。身份与成员归属见 decision_0f57656a2acd，Resident Folder 与自有工作区见 decision_ccb99a00eebd。
- **Agent 与主 trace**：AgentData 中与 Resident 同 ID 的独立 Agent，以及记录它的消息和执行历史的主轨迹。主 trace 可以为空，模型会话尚未建立时也存在；存储与读取规则见 decision_b1ab45c4b3ec。
- **运行设置**：Resident 使用的 Provider、账号路线、模型、推理强度、服务等级、权限预设、是否注入初始指令、是否允许子 Agent、Agent Harness 设置、能力配置（工具、MCP 与 Skill 的选用）和协作模式。设置带版本号 `settings_revision`，并发保存按版本拒绝过期提交。
- **模型会话**：Resident 当前使用的 Provider 会话。Resident 身份长期不变，模型会话可以重启或迁移到另一个 Provider 或账号。会话迁移的策略与恢复见 decision_c47a13e8b962。
- **工作区绑定**：Resident 与工作目录之间的关联。自有工作区是默认启动目录，不能解除；其他目录通过绑定加入。规则见 decision_8838269736d3。
- **Seed**：Lineage 中尚未成为 Resident 的候选组件，记录一个想法或方案；它从哪个 Resident 的工作出发由候选 Fork 关系表达（decision_4637ca4a69e2）。Seed 的创建、修改和放弃使用 Lineage 定义指令（decision_4637ca4a69e2），提升为 Resident 使用本类的 `PromoteSeed`。

## 指令一览

| 指令 | 改变状态 | 作用 | 默认方案要求 |
| --- | --- | --- | --- |
| `CreateResident` | 是 | 创建 Resident 并加入 Lineage | 对 `lineage_id` 有 @contributor |
| `ForkResident` | 是 | 从已有 Resident Fork 出新 Resident | 对 `lineage_id` 有 @contributor；对 `source_resident_id` 有 @viewer；对 `context_source_resident_id` 有 @viewer |
| `PromoteSeed` | 是 | 把 Seed 提升为待命的 Resident | 对 `lineage_id` 有 @contributor |
| `GetResident` | 否 | 读取 Resident 的基本信息 | 对 `resident_id` 有 @viewer |
| `SearchResidents` | 否 | 按关键词查找 Resident，并说明调用者能否给它发消息 | 对“共享”有 @viewer |
| `UpdateResident` | 是 | 修改 Resident 名称或头像 | 对 `lineage_id` 有 @contributor |
| `GetResidentSummaries` | 否 | 批量读取与工作查看共用的累计摘要，供搜索、路由和摘要维护复用；可附带内容版本与漏写轮次 | 对 `resident_ids` 有 @viewer |
| `UpdateResidentWorkSummary` | 是 | 本人替换近期事实。各层各字段合计上限默认 5000 字符。省略 summary 表示仅登记覆盖轮次。 | 免检 |
| `UpdateResidentSummaryConsolidation` | 是 | 本 Family Steward 归纳业务成员的阶段与长期层 | 免检；处理器核验调用者是该 Family 的 Steward |
| `UpdateFacadeSummary` | 是 | 本 Family Steward 写入门面摘要并保存来源版本 | 免检；处理器核验调用者是该 Family 的 Steward |
| `RequestResidentSummaryRebuild` | 是 | 请求完整重建独立 Resident 的累计摘要；Family 使用自身维护者 | 对 `resident_id` 有 @contributor |
| `GetResidentSummaryQueue` | 否 | 读取摘要队列中各状态的份数 | 对“共享”有 @viewer |
| `ClaimResidentSummaryJobs` | 是 | 认领待写的独立 Resident 摘要并取得写作说明与证据（仅限全局摘要职责承担者） | 免检 |
| `SubmitResidentSummary` | 是 | 提交一份已认领的摘要 | 免检 |
| `ReleaseResidentSummaryJob` | 是 | 放弃一份已认领的摘要并说明原因 | 免检 |
| `GetResidentSettings` | 否 | 读取运行设置与 Prompt 注入设置 | 对 `resident_id` 有 @viewer |
| `GetResidentTextInputSnapshot` | 否 | 读取显式 fresh 文本的当前配置和完整系统 parts 版本，不提交模型 | 对 `resident_id` 有 @viewer |
| `UpdateResidentSettings` | 是 | 修改运行设置，包括 Prompt 注入 | 对 `resident_id` 有 @contributor |
| `GetPromptInjectionPreview` | 否 | 预览一轮实际带上的注入内容 | 对 `resident_id` 有 @viewer |
| `GetResidentInitialization` | 否 | 读取既有初始化状态与执行身份 | 对 `resident_id` 有 @viewer |
| `RetryResidentInitialization` | 是 | 恢复初始化并保留原队列 | 对 `resident_id` 有 @contributor |
| `RestartSession` | 是 | 等当前轮结束后重启或切换 Resident 后端或账号，保留上下文；保存配置不调用模型，首条新消息交付历史。 | 对 `resident_id` 有 @contributor |
| `GetSessionRestart` | 否 | 读取会话重启进度 | 对 `resident_id` 有 @viewer |
| `CancelSessionRestart` | 是 | 取消尚未完成的会话重启 | 对 `resident_id` 有 @contributor |
| `BindWorkspace` | 是 | 为 Resident 绑定一个工作目录 | 对 `resident_id` 有 @contributor |
| `UnbindWorkspace` | 是 | 解除一个工作目录绑定 | 对 `resident_id` 有 @contributor |

## 创建与派生

创建类指令建立身份、Lineage 中的节点，以及同 ID 的独立 Agent 和主 trace。登记成功前，公共创建流程确保 AgentData 中的这些对象已就绪；该步骤失败时指令报告失败或待完成状态，调用者沿原请求继续确认，不提前把登记当作完成。重复确认复用已有 Agent 和 trace，保留已经存在的片段与顺序。创建本身不投递消息，也不启动工作。需要派发任务时，调用者另行使用 `DeliverMessage`（decision_2fe6fd5367fb），或通过 Lineage 中的 Event 与 Invoke 连线启动。

新 Resident 的主 Agent 使用 `agent_trace`，`parent_agent_id` 为空。Portal Fork 产生独立主 Agent，来源关系和冻结上下文由派生记录保存；`parent_agent_id` 只表达 Provider 执行中创建的子 Agent 的直接父子关系。

`CreateResident` 的账号按以下顺序确定：调用中显式指定的账号优先；由 Resident 发起且未指定账号时，继承该 Resident 当前使用的账号（仅限同一 Provider）；由页面或 Lineage 内部组件发起且未指定账号时，使用系统默认账号。

`ForkResident` 默认继承上下文来源的 Provider、实际账号和运行设置，也接受显式 `provider` 与 `account_id`。跨 Provider 时按目标账号校准配置，保留所选上下文与通用运行策略。`PromoteSeed` 沿用来源 Provider、账号与运行设置。

创建类指令修改 Lineage 定义。同一 Lineage 上的多个创建可以同时进行，各自的新节点都会保留（decision_d918ebebc062）。新节点在画布上的位置由放置参数决定，放置参数的含义见 decision_4637ca4a69e2。

页面通过 `CreateResident` 新增独立身份时，定义提交沿 decision_d918ebebc062 的短事务与合并规则追加节点，页面不自动附带打开表单时的整图版本。因此其他成员的新增、改名不会阻断这次独立创建。调用者显式提供 `expected_version` 时仍严格检查；编辑既有定义继续使用其读取版本，冲突后只刷新，不自动重放写入。创建失败且未提交时，表单保留输入并持续展示错误；创建已提交但刷新失败时，关闭表单并提示刷新，避免重复创建。

### `CreateResident`

**创建一个新的 Resident 身份，并把它作为节点加入指定 Lineage。**

- 参数：`lineage_id`、`title`、`provider`（可选，`codex`、`claude`、`kimi` 或 `dsh`）、`account_id`（可选，账号路线）、`backend_role`（可选，Backend 角色名，见 decision_7956d7cb4bde）、`settings`（可选，初始运行设置）、`workspace_paths`（可选，额外绑定的工作目录）、放置参数。新 Resident 总是从新的模型会话开始。
- 给出 `backend_role` 时，创建时解析一次角色：角色指向的账号决定 Provider 和账号路线，角色的模型与推理强度写入初始设置；显式的 `provider` 优先。角色未映射时回退 `default`，结果的 `backend_role_fallback` 记下请求的角色与实际使用的角色；角色引用的账号已停用或不存在时以参数错误拒绝。之后修改角色映射不影响已创建的 Resident。
- 结果：新 Resident 的 ID 和所在 Lineage；回退了角色时另有 `backend_role_fallback`。

### `ForkResident`

从指定来源创建新 Resident、独立 trace 和记忆交接意图；不投递业务、不调用模型。

参数：`lineage_id`、`source_resident_id`；可选 `fork_mode`（`full`、`compacted`、`recent`、`message`，默认 `full`）、`recent_rounds`（整数，`fork_mode=recent` 时必填，最少 1）、`context_source_resident_id`（上下文来源，默认与来源 Resident 相同）、`message`（`fork_mode=message` 时的历史截止点 `{message_id}`）、`title`、`provider`、`account_id`、`family_id`、`near_resident_id`、`placement`、`x`、`y`。非法值拒绝；`recent_rounds` 只在 `fork_mode=recent` 时接受。

新 Resident 的上下文取自来源的有效窗口：从来源最近一次压缩起，或按 `fork_mode` 选定的轮次、`message` 给出的历史截止点；上下文由 AgentData 提供，任何能用该上下文打开新会话的 Provider 都支持 Fork，不依赖厂商原生 fork，跨账号或 Provider 同样适用。模型、effort 与账号切换复用校验；Model API 要求登记账号，不使用原生 effort 字段。见 decision_5bf5bfbd52cb。

返回新 Resident 和继承关系 ID。自身工作区、权限与工作区只读降权规则保持；Family 门面不能作为来源，需要具体成员。

### `PromoteSeed`

**以 Seed 的候选 Fork 来源为基础 Fork 出一个待命的 Resident，并把 Seed 标记为已采纳。**

- 参数：`lineage_id`、`seed_id`、`title`（可选，默认使用 Seed 标题）、`purpose`（可选，记录这次采纳的用途）。
- 结果：新 Resident 的 ID、派生关系 ID，以及候选 Markdown 的绝对路径 `candidate_path`（文件不存在时为空）。
- 只有状态为 `active` 且有候选 Fork 的 Seed 可以提升；没有候选 Fork 时失败，提示先建立候选 Fork。这条候选 Fork 随之变成从来源到新 Resident 的继承关系（标签“Seed 采纳”），结果中的派生关系 ID 就是它原来的 ID。这条指令不给新 Resident 发送消息；页面采纳时随后用 `DeliverMessage` 把行动目的和 `candidate_path` 作为第一条任务发给它（decision_4adcc1ff2f2c）。

## 身份与设置

### `GetResident`

**读取 Resident 的基本信息。**

- 参数：`resident_id`。
- 结果：名称、所属 Lineage、Provider、头像、自有工作区与其他绑定的摘要。结果字段为 `resident_id`、`title`、`lineage_id`、`project_id`、`provider`、`account` 与 `workspaces`。

### `SearchResidents`

**按关键词查找调用者可以看到的 Resident，并说明调用者现在能否给它发消息。**

- 参数：`q`（可选，关键词）、`resident_ids`（可选，只在这些 Resident 中查找）、`can_send`（可选，只返回调用者现在能发消息的）、`limit`（可选，默认 20，最多 50）。
- 结果：`residents`，每项为 `resident_id`、`title`、`lineage_id`、`lineage_title`、`can_send` 与 `disabled_reason`（不能发送时的原因：没有发消息的权限，或上下文来源有问题）。
- 关键词匹配 `resident_id`（可以省略 `resident_` 前缀，开头的 `@` 忽略）、名称和所在 Lineage 名称，完全一致的在前，其次是开头一致、包含关键词、只有 Lineage 名称匹配的。没有关键词时最近工作过的在前；给出 `resident_ids` 时按给出的顺序。调用者看不到的 Resident 和尚未登记完成的 Resident 不出现。
- `can_send` 与 `DeliverMessage` 的权限规则相同，与 `ListResidentStates`（decision_2a3ff353165c）中 `controls.can_send` 一致。聊天室输入框的 @ 候选列表使用这条指令（decision_a6bebf4ac283）。

### `UpdateResident`

**修改 Resident 名称或头像；修改名称时同步它在 Lineage 中节点的显示名称。**

- 参数：`lineage_id`、`resident_id`、`title`（可选）、`avatar`（可选）。`title` 与 `avatar` 至少提供一项；只给 `avatar` 时名称不变。`avatar` 为 `{"sprite_index": 0–41}` 选择海洋图案，或 `{"upload_id": …}` 使用 `CreateUpload`（用途 `avatar`）的已完成上传（decision_5bc3fdfc7eec）。
- 结果：修改后的 Resident 基本信息；修改了头像时带上保存后的 `avatar`。
- Portal 页面节点设置中的名称修改也使用这条指令。

### 删除 Resident

通过 `ApplyLineageChanges` 提交删除 Resident 的操作。提交核对对象本身的删除权限及所属范围，活动身份的移除和历史保留规则见对应的 Lineage 定义。

### `GetResidentSummaries`

**批量读取 Resident 的累计摘要；搜索、路由、工作查看和摘要维护读的是同一份内容。**

- 参数：`resident_ids`，一次最多 128 个；可用 `fields` 选择返回的摘要字段；`include_maintenance` 为真时附带维护所需的信息。
- 结果：`summaries` 与 `unavailable_resident_ids`。每份摘要包含 Resident ID、名称、所属 Lineage、摘要内容（当前重点、进行中的线程、长期经验、近期成果、主要负责、辅助经验、负责的组件、分支差异、适合与不适合接收的任务）、尚未完成的请求、更新时间，以及摘要是否过期。
- `include_maintenance` 时每项另有内容版本 `revision`、汇总来源 `source_versions`、漏写的输入 `missing_message_ids`、待归纳的覆盖输入 `pending_message_ids` 和维护者 `writer_id`；尚无摘要但调用者能看到的 Resident 也返回一项，`revision` 为 0。`revision` 只表示内容版本，不是写入指令的参数。
- `include_maintenance=true` 时，`summaries[i].reminder` 可选，存在时包含 `message_id` 和 `content_parts`。负责摘要维护的 Effect 将 `reminder` 原样作为 `DeliverMessage.message` 交给该项对应的 Resident，指定 `business_ref=duty/work-summary-reminder` 和 `activation_policy=attach_to_next`。Society 先登记输入 `completed` 终态对应的漏写事实，再让 Effect 观察该终态。提醒由 Effect 投递，核心只提供事实和待投递内容。
- Family 成员本人维护近期事实，本 Family Steward 归纳阶段与长期层；门面只由本 Family Steward 汇总；全局 resident-summary 职责仅维护独立 Resident。读取以调用者自己的身份进行：调用者看不到或尚无摘要的 Resident 列入 unavailable_resident_ids；摘要存储不可用时返回可重试错误。工作查看、搜索与路由复用三层的统一九字段投影。

### `RequestResidentSummaryRebuild`

**请求完整重建独立 Resident 的累计摘要。**

- 参数：`resident_id`、`reason`（可选，例如“职责调整”）。
- 结果：Resident ID 与请求结果。
- 重建以调用者自己的身份请求，调用者必须能看到该 Resident；Family 成员及门面拒绝全局重建，使用各自维护者。独立 Resident 的请求只加入全局写作队列，由承担者依证据维护同一份累计摘要。

### 工作摘要与覆盖检查

`UpdateResidentWorkSummary` 用于本人提交近期工作事实和已覆盖的消息。Family Steward 用 `UpdateResidentSummaryConsolidation` 归纳业务成员，用 `UpdateFacadeSummary` 提交门面汇总与 `GetFacadeSummaryInputs` 返回的 `source_versions`。各入口按对应固定调用者身份核验。


维护者用 GetResidentSummaries（`include_maintenance`）读取自己的摘要和 missing_message_ids，读取继续按当前调用者权限执行。

摘要的每一层有固定的写入者，各由一条指令写入，不要求交回读到的版本：近期层和输入覆盖由本人用 `UpdateResidentWorkSummary(resident_id, summary?, message_ids?)` 写，阶段与长期层由 Steward 用 `UpdateResidentSummaryConsolidation(resident_id, consolidation)` 归纳，门面摘要由 Steward 用 `UpdateFacadeSummary(resident_id, summary, source_versions)` 汇总；后写的内容直接替换同一层的前一份内容。本人用 summary 替换近期层（不是把三层投影抄回）；message_ids 最多 128 个，必须是本人正在运行或已经完成的输入。首次提供有效九字段摘要，已有摘要可省略 summary 只登记覆盖。内容无变化时 revision 与内容更新时间不变，输入覆盖照常推进。`UpdateFacadeSummary` 只允许本 Family Steward 写入门面，必须保存 GetFacadeSummaryInputs 的 source_versions，不登记成员输入 ID。

include_maintenance 另返回 layers（recent、phase、long_term）、maintenance（各层归纳时间和新覆盖轮次水位）、consolidation（due、level、reason、total_chars、policy）。旧摘要无损视作近期层，旧超限摘要仍可读取并进入归纳待办；不自动截断或回放原始对话。

`UpdateResidentSummaryConsolidation` 只允许本 Family Steward 归纳本组业务成员。Router 和 Steward 免写自身摘要，不参加自身覆盖检查、漏写提醒与时间层归纳；门面由 Steward 汇总。`consolidation` 必须给出替换后的 phase，可同时给出 long_term；达到长期阈值时必须包含 long_term；用 `consolidated_message_ids` 列出本次已纳入归纳的覆盖输入（取自 GetResidentSummaries 的 `pending_message_ids`），可选 `resolved_threads`。成功提交替换指定的高层，并只推进这些输入对应的水位；归纳不改写也不清空近期层，近期层仍由本人下一次提交替换。读取之后才新增覆盖的输入继续留在待归纳列表，由下一次归纳纳入；重复提交同一批输入不再推进水位。层内容改变时 `revision` 随之递增。外层 Steward 不能归纳内层门面；解除 Family 后的全局写作以当前统一内容为来源，并接管整份摘要。

active_threads 承载未决承诺、阻塞与重要约束。归纳遗漏的旧事项自动补入 phase；关闭必须在 resolved_threads 中提供「原事项：解决、取消或交接说明」。重要项目历史另存工作记录，摘要保留有效结论与记录入口；不改变原始日志保留策略。

摘要各时间层、各语义字段的文字值合计最多 5000 字符（默认），重复文字逐次计数；不另设阶段与长期层占比限制。提交内容超过总量上限时整次拒绝，版本、覆盖轮次和已有内容保持原状，不静默截断。归纳时先保留未关闭事项再检查总量；身份、维护职责、版本比较与未决事项保留仍按原规则校验。历史超长摘要继续可读，省略正文时仍可登记已覆盖轮次，后续归纳须满足总量上限。平台按新增工作量与容量判断归纳待办，不决定信息重要性：默认阶段 20 个新覆盖轮次，长期 100 轮，合计文字达到上限的 80% 提醒归纳。单纯经过时间不形成待办，没有新工作的成员摘要保持原状，闲置不会让它反复进入归纳。部署配置 PORTAL_SUMMARY_TOTAL_CHARS 定义合计字符上限（默认 5000，至少 400），PORTAL_SUMMARY_PHASE_ROUNDS、PORTAL_SUMMARY_LONG_TERM_ROUNDS 为正整数。维护轮次不计数量，重复覆盖不重计。独立 Resident 的认领结果也返回同一 summary_policy，单份摘要写入受相同总量上限约束。

读取门面汇总材料的 GetFacadeSummaryInputs 属于门面与转交类（decision_b77fde81e7a0）；成员摘要遗漏的检查由团队模板的 Effect 用 GetResidentSummaries 完成。

全局摘要认领只允许当前职责承担者，任务仅覆盖独立 Resident；Family 成员和门面不进入队列。认领、提交及请求重建时重新核对当前归属；变为 Family 管理后旧认领失效。GetResidentSummaries 始终读取同一份累计内容，不保留内部成员首次自写前的另一条读取回退。具体 Family 工作流见 decision_44cf587c1721。

### 摘要的写作

独立 Resident 的累计摘要由 Portal 职责 resident-summary 的承担者亲自维护（职责绑定见 decision_8fe87d812a4c）。SocietyService 只做目录检查、读取已完成对话、证据整理脱敏、排队及认领等机械工作，不调用模型。既有定时 Effect 仍只在有可认领任务且承担者空闲时清空其上下文并投递职责请求；承担者按以下指令处理队列。写作运行的模型和账号等证据信息独立保存，实际内容与版本写入统一摘要存储。Family 的成员及门面由各自维护者处理，不进入这个全局写作流程。

#### `GetResidentSummaryQueue`

**读取摘要队列中各状态的份数。**

- 参数：无。
- 结果：`ready`（证据已整理好、可以认领）、`waiting`（还在等待读取对话）、`claimed`（已被认领、尚未提交）与 `parked`（多次失败后搁置，等对话再变化）的份数。
- 任何调用者都可以读：结果只有份数，不含摘要、证据或 Resident ID。定时入口的 Effect 据此判断是否需要唤醒承担者，队列为空时不产生模型调用。

下面三条认领与提交指令都只允许 `resident-summary` 的当前承担者调用，其他调用者得到 `forbidden`。

#### `ClaimResidentSummaryJobs`

**认领待写的摘要，并一次取得写作所需的说明与证据。**

- 参数：`limit`（可选；省略时认领全部可认领的份数，没有固定上限）。
- 结果：写作说明 `instructions`、必须输出的字段 `output_fields`、`jobs`、`remaining_ready`（认领后仍可认领的份数）与 `waiting`（还在等待读取对话的份数）。`jobs` 每份含 `claim_id`、Resident ID、名称、所属 Lineage、写作方式 `mode`、排队原因、第几次尝试、证据字数、租约到期时间，以及证据 `input`：写作方式（`incremental` 在上一份摘要上增量更新，`full` 完整重写）、Resident 元数据、增量时的上一版摘要和已完成轮次的对话证据。证据已脱敏，只交给认领者。
- 每份认领有 30 分钟租约，到期未提交或放弃时退回队列，由下一次认领拿到。证据总量过大时少给几份。

#### `SubmitResidentSummary`

**提交一份已认领的摘要。**

- 参数：`claim_id`、`summary`（对象：`current_focus` 与 `branch_delta` 为字符串，其余字段为短字符串数组）。
- 结果：Resident ID、写作方式与新的摘要代数 `generation`。提交后摘要立即可读，向量索引随后更新。
- Portal 校验字段、当前职责归属、认领租约及认领依据的摘要内容版本后写入同一份累计摘要，并记录提交者和写作运行信息。租约失效或归属变化时拒绝提交；内容版本过期时拒绝旧结果并重新排队。摘要变化立即用于文本搜索，旧向量先失效再重建；向量计算期间内容版本改变则丢弃过期向量并重新排队。

#### `ReleaseResidentSummaryJob`

**放弃一份已认领的摘要并说明原因。**

- 参数：`claim_id`、`reason`。
- 结果：Resident ID、已尝试次数和之后的状态：`retry`（稍后重试）或 `parked`（多次失败后搁置，直到该 Resident 的对话再次变化）。
- 用于证据不足以写出摘要等情况；承担者不得为了凑数提交空泛的摘要。

### `GetResidentSettings`

**读取 Resident 当前的运行设置和 Prompt 注入设置。**

- 参数：`resident_id`。
- 结果：
  - `settings`：当前运行设置。页面设置面板读取的是同一份设置；其中 `prompt_injection` 是保存的原值（`{preset}` 或自定义时的 `{preset: custom, parts}`）。
  - `settings_revision`：当前设置版本，保存时作为 `UpdateResidentSettings` 的 `expected_revision`。
  - `application`：逐项配置的生效时机与实际交付观察；未观察到不等于缺失。
  - `prompt_injection`：Prompt 注入的读法。`preset`、`preset_label` 为当前预设；`presets` 为可选的预设；`parts` 为六个类别，每项给出 `id`、`label`、`description`、当前时机 `choice`、可选时机 `choices`，以及它在当前 Provider 中的位置 `placement`。位置由 Provider 决定，不能修改。各类别、时机和预设的含义见 decision_ef1d6c925955。

### `GetResidentTextInputSnapshot`

按调用者读取目标 Resident 的实际配置并装配本次显式文本的系统 parts，返回配置版本、系统 parts 版本、backend、account 和 settings。文本必须非空且 UTF-8 大小不超过 16000 字节。读取沿用运行设置 owner 的权限校验；装配前后校验配置版本，不提交消息或模型请求。结果证明本次配置与输入材料，不证明工具已在 Provider 侧禁用。

### `UpdateResidentSettings`

**修改 Resident 的运行设置。**

- 参数：`resident_id`、`expected_revision`（可选，读取时得到的 `settings_revision`）、`changes`。`changes` 只接受以下字段：`model`、`reasoning_effort`、`service_tier`、`permission_preset`、`prompt_injection`、`allow_subagents`、`agent_harness`、`capability_config`、`collaboration_mode`、`prompt_context`、`backend_options` 、`reporting` 和仅供 Model API 使用的 `backend_route_id`。模型相关字段取值由目标 Provider 校验，汇报策略由 Society 校验。
- `changes.prompt_injection` 给出 `preset`（`standard`、`full`、`minimal`、`pure`、`custom`）、`parts`（要改的类别及其时机），至少一项：
  - 只给 `preset` 时，设置换成这个预设。
  - 给了 `parts` 时，在所给预设上修改；没给预设时，在当前设置上修改。结果记为自定义；与某个预设完全相同时，记为该预设。
  - 非法的类别或时机直接报错。下一轮生效。
- 结果：保存后的设置、新的 `settings_revision`、`application`（逐项生效状态，不表示后端已接受）、`next_turn_fields`（从下一轮才生效的字段），以及与 `GetResidentSettings` 相同的 `prompt_injection` 读法。
- 修改设置不会中断当前工作。`collaboration_mode` 只适用于 Codex，取值 `default` 或 `plan`，从下一轮开始生效。重复保存相同的值不产生变化。
- 设置按版本条件保存：`expected_revision` 与当前 `settings_revision` 不一致时拒绝，调用者重新读取并合并后再提交；省略只为兼容旧调用，页面保存总是携带已读版本。版本覆盖完整运行配置，规则见 decision_8b2fa224616b。
- 这条指令不改变 Lineage 定义。原生后端切换 Provider 或账号需要迁移模型会话，使用 `RestartSession`。Model API 无原生会话，允许通过 `changes.backend_route_id` 选择登记的 API 账号，并与模型、上下文及协议参数在同一事务中保存；失败时全部回滚，从下一轮生效。账号只存放在 Resident 配置中，不复制到运行设置。
- Model API 的 `prompt_context` 可配置系统提示、预设消息及 `history: {enabled, max_tokens}`；`backend_options` 保存协议参数、超时和重试。新建文本类 Resident 默认携带历史，新建 Jev 默认关闭；已有记录缺少历史设置时保持关闭。Settings 的能力展示、历史选择与 Jev 题型规则见 decision_6c2f91a7d84e。
- 目标是 Family 门面时，模型、Prompt 与 Harness 等运行设置仍来自 Router，按 Router 的相应权限读取和修改；汇报策略 `reporting` 属于门面自身，按门面的 viewer/contributor 权限读写。仅有门面权限时仍可读写汇报策略，结果 `settings` 仅含可读字段，`runtime_settings_access=unavailable`、`prompt_injection.state=unavailable`，不暴露或猜测 Router 参数。
- `reporting` 是完整对象 `{initiative, quality}`：主动性为 `off|low|medium|high`，质量为 `low|medium|high`，缺省 `off/medium`；未知键、缺键、非法枚举拒绝。`reporting_effective` 给出策略、`state`、`source_resident_id`、`source`、`configured` 与 `application=next_input`；`configured=false` 表示默认，不能称为用户已配置。显式保存默认值同样形成配置事实。版本校验覆盖可读运行设置、策略及是否已配置。
- `application.items` 的 `reporting` 项记录 `next_turn` 与未知交付观察；保存不重启会话、不调用模型、不生成报告或发起审查。每条适用的新主输入或追加输入从可信转交链取得源门面策略；直接成员输入使用自身策略，已送达输入不被改写。主动性 off 保留正常答复、业务交付及用户明确要求的汇报；高主动性仍由 Resident 判断价值。质量与独立检查规则见 decision_b12782e7f490、decision_e3ced6d30f6b。

### `GetPromptInjectionPreview`

**预览 Resident 在某种轮次实际会带上的注入内容。**

- 参数：`resident_id`；`turn`，取值：
  - `first`（默认）：会话第一轮；
  - `later`：之后的轮次；
  - `after_compaction`：上下文压缩后的第一轮。
- 结果：
  - `parts`：每个注入部分的 `id`、`label`、时机、位置、这一轮是否发送（`sent`）、字数和正文。
  - `system_prompt`：这一轮 Provider 系统位置收到的 Portal 内容。
  - `prompt`：这一轮发给模型的消息，消息正文用占位文字。
- 用发送 Resident 所用的同一套规则拼出内容。授权上下文里的消息来源写作调用者。
- 不改变任何状态。

## 模型会话

### `GetResidentInitialization`

- 参数：`resident_id`（必填）。要求目标 Resident 的 @viewer；不可见目标遵循隐藏规则返回 `instruction.not_found`。
- 只读返回 `resident_id`、`initialization_id`、`status`、`phase`、`error`、`queued_count`、`source_session`、`candidate_agent_id`、`candidate_binding_id` 及 `execution_ids`。`status` 为 `not_required`、`pending`、`running`、`failed` 或 `completed`；`phase` 为 `initialization`、`prepare`、`create`、`compact`、`promote` 或 `complete`。
- `source_session` 仅含 Provider、账号路线、原生 Session、会话引用与绑定身份；`execution_ids` 标识原或候选 create／compact。返回不含请求正文。成功后可从不可变提升凭据读回 attempt，不依赖短期调度回执存续。

### `RetryResidentInitialization`

`expected_initialization_id`：确认 credits 时必填：刚读取的初始化尝试 ID；状态变化后须重新确认。

`credits_consent`：仅人类：{account_id:本次目标稳定账号ID,confirmed:true}；须有幂等键，仅授权本次初始化及其目标Session，不重发业务输入，不继承给后续会话。

- 参数：`resident_id`（必填）。要求目标 Resident 的 @contributor；修改权限拒绝返回 `instruction.forbidden`。内置默认权限方案分别将这两条指令分类为 @viewer 与 @contributor；实际调用仍按当前启用方案鉴权。
- 恢复既有 Resident 的初始化，不重发原业务。返回最小可持久受理回执：`resident_id`、`initialization_id`、`status`、`phase`、`candidate_agent_id`、`candidate_binding_id`；同幂等键重放该回执，当前状态用 `GetResidentInitialization` 读取。进行中的 attempt 被复用，显式失败／取消后的重试才可建立新候选。
- 当前生命周期或业务工作活动、原执行仍活动、绑定已变化、冻结 Fork 凭据不匹配等冲突阻止重叠恢复。原压缩完成事实可核实则直接收敛；原生结果不明时使用隔离候选 Session，验证规范压缩与绑定后原子提升。失败保留旧绑定和原输入，完整恢复合同见 decision_5bf5bfbd52cb。
- 接收开关保持原值；关闭接收也可显式恢复初始化，完成后业务仍等待开放。受理不等于初始化或排队任务已完成。

### `RestartSession`

保存 Resident 的 Provider、账号、参数和记忆交接意图。操作不调用模型、不改变原 Segment 和 trace，不生成摘要或重投失败输入；首条真实输入才建立目标会话并交接。

- 参数：resident_id、provider、account_id、account_selection、model、reasoning_effort、collaboration_mode、service_tier、permission_preset、prompt_context、backend_options。
- 上下文交付复用完整 Fork 的有效窗口，校验通过后保存目标配置。首条真实输入负责交付；清空使用 `ClearContext` 或 `CreateContextReset`。
- 同 Provider、同稳定账号切换无参数变更时返回 unchanged；带模型、effort 等参数变更时明确提示使用 UpdateResidentSettings，避免误建记忆交接。运行设置界面直接保存原会话参数，不发 RestartSession。
- 目标模型和 effort 必须符合该账号目录；跨 Provider 显式选择 model。Model API 要求登记账号，推理参数按模型能力置于 backend_options.parameters；原生目标拒绝 API 专属字段。与 Fork 使用相同目标校验。
- 切换到同一实际账号且没有配置改动时 noop。其他配置提交以来源绑定和设置 revision 为栅栏。保存失败恢复原绑定，不能覆盖并发修改。
- `RestartSession` 等待当前工作真正结束。服务持久保存原请求并暂时关闭接单，后到输入按原 ID 排队；完成、失败或取消时，仅恢复本操作关闭且未被外部改动的接单。已提交但未开始的输入沿用原安全改派与撤回合同。调用者明确选择停止当前工作时，先对该条输入调用 `CancelInput`，通过 `GetInputState` 或对话事件确认执行实际结束，再调用 `RestartSession`。队列中的其他输入保留。
- 新请求不接受 resume_failed_input 或 credits_consent；内部入口对要求重投或使用 credits 的参数也拒绝。首条实际输入仍经正常额度准入；历史已受理操作的恢复不因此被重新解释。
- 配置完成返回 completed/configuration_only=true/model_operation=false，context_result=pending_first_input。这不表示原生 Fork 已执行或模型已接收第一条输入。详情见 decision_4dea0db1f45a 的 session-account-switch.md。

### `GetSessionRestart`

**读取 Resident 最近一次会话重启的进度。**

- 参数：`resident_id`。
- 结果：重启 ID、状态、来源会话与目标会话，以及最近一次操作的 `operation_id`、`operation`、`phase`、`context_result` 和独立于原始来源与目标的 `actual_binding`。页面的“只刷新数据”重新加载对话；运行设置卡片通过会话选项接口读取可选 Provider、账号、方案和重启进度。

### `CancelSessionRestart`

**取消一次尚未完成的会话重启，保留原会话。**

- 参数：`resident_id`、`restart_id`。
- 结果：取消后的重启状态。取消不查询目标账号的额度。

## 工作区读取与绑定

一个 Resident 可以使用的文件根有四类：Resident Folder、所在 Lineage 的 Lineage Folder、自有 Workspace 和额外绑定的 Workspace。GetResident 返回 `lineage_id` 和 `workspaces`（含自有 Workspace 的路径与 `is_owned`），调用者据此得到每个根的 ID：Resident Folder 用 `resident_<id>`，Lineage Folder 用 `lineage_<id>`，Workspace 用 `workspace_<id>`。

读取统一使用 ReadResource 的文件地址 `<根 ID>/file/<相对路径>`（decision_ad3e21094f14）。`<根 ID>/file` 或指向目录的地址返回直接子项，每个子项带自己的文件地址；指向文件时返回正文，`preview` 只读前 `max_bytes` 字节，`meta_only` 只返回元数据。原始字节和下载使用结果中的 `content_url`。Resident Folder 和 Lineage Folder 的地址要求对该 Resident 或 Lineage 有 viewer，Workspace 地址按 Workspace 的规则检查（decision_8838269736d3）。解除绑定或删除对象后，原地址随之失效。
- 文件路径必须留在选定根内，拒绝 `..` 逃逸和指向根外的符号链接，继续执行已有的隐藏文件规则。读取不会赋予文件写入权限。



### `BindWorkspace`

**为 Resident 绑定一个已存在的工作目录。**

- 参数：`resident_id`、`path`（已存在的绝对路径，最长 4096 字符）、`purpose`（可选，说明目录用于什么工作）。
- 结果：绑定记录，包括工作区 ID 和规范路径。
- 同一 Resident 再次绑定同一规范路径时返回已有绑定。绑定只建立 Portal 中的关联；实际能否读写仍由文件系统权限和运行沙箱决定。

### `UnbindWorkspace`

**解除 Resident 与一个工作目录之间的绑定。**

- 参数：`resident_id`、`workspace_id`（必填，要解除绑定的 Workspace；按路径定位时先从 `GetResident` 的 `workspaces` 取得 ID）。
- 结果：解除后的绑定列表。
- 自有工作区和默认启动目录不能解除。解除只删除关联，磁盘上的目录和文件保持不变。

## 参数名契约

下表列出 arguments 内的顶层参数名；必填条件、类型和枚举以 GetInstructionSchema 为准。调用信封中的 expected_version、idempotency_key 与 wait_seconds 不列入 arguments。

| 指令名 | arguments 参数名 |
| --- | --- |
| `UpdateResidentWorkSummary` | `resident_id`、`summary`、`message_ids` |
| `UpdateResidentSummaryConsolidation` | `resident_id`、`consolidation` |
| `UpdateFacadeSummary` | `resident_id`、`summary`、`source_versions` |
| `CreateResident` | `lineage_id`、`title`、`provider`、`account_id`、`backend_role`、`settings`、`workspace_paths`、`family_id`、`near_resident_id`、`placement`、`x`、`y` |
| `ForkResident` | `lineage_id`、`source_resident_id`、`fork_mode`、`recent_rounds`、`context_source_resident_id`、`message`、`title`、`provider`、`account_id`、`family_id`、`near_resident_id`、`placement`、`x`、`y` |
| `PromoteSeed` | `lineage_id`、`seed_id`、`title`、`purpose` |
| `GetResident` | `resident_id` |
| `SearchResidents` | `q`、`resident_ids`、`can_send`、`limit` |
| `UpdateResident` | `lineage_id`、`resident_id`、`title`、`avatar` |
| `GetResidentSummaries` | `resident_ids`、`fields`、`include_maintenance` |
| `RequestResidentSummaryRebuild` | `resident_id`、`reason` |
| `GetResidentSummaryQueue` | 无 |
| `ClaimResidentSummaryJobs` | `limit` |
| `SubmitResidentSummary` | `claim_id`、`summary` |
| `ReleaseResidentSummaryJob` | `claim_id`、`reason` |
| `GetResidentSettings` | `resident_id` |
| `GetResidentTextInputSnapshot` | `resident_id`、`text` |
| `UpdateResidentSettings` | `resident_id`、`expected_revision`、`changes` |
| `GetPromptInjectionPreview` | `resident_id`、`turn` |
| `GetResidentInitialization` | `resident_id` |
| `RetryResidentInitialization` | `resident_id`、`expected_initialization_id`、`credits_consent` |
| `RestartSession` | `resident_id`、`provider`、`account_id`、`account_selection`、`model`、`reasoning_effort`、`collaboration_mode`、`service_tier`、`permission_preset`、`prompt_context`、`backend_options` |
| `GetSessionRestart` | `resident_id` |
| `CancelSessionRestart` | `resident_id`、`restart_id` |
| `BindWorkspace` | `resident_id`、`path`、`purpose` |
| `UnbindWorkspace` | `resident_id`、`workspace_id` |

### 累计摘要的内容与维护事实

工作摘要和发现画像共用 `work_summaries` 中的一份内容与 revision，避免不同写作者维护相互冲突的职责、进度和版本。Family 的工作汇总与 Which Resident 的候选推荐复用该内容；候选可见范围、按角色划分的维护责任和输入覆盖仍分别管理。`snapshots` 保存来源游标、指纹、维护状态及错误等元数据，不再保存一份可回退读取的摘要正文。历史迁移仅在缺少有效内容版本时用旧快照填充，已有自写内容优先；这不构成持续双写或回退来源。全局写作与向量结果都检查所依据的内容版本，过期结果不覆盖新内容。本节是共享摘要存储规则的唯一正文；Family 门面身份、推荐权限和模型调用方式仍由各自设计负责。
