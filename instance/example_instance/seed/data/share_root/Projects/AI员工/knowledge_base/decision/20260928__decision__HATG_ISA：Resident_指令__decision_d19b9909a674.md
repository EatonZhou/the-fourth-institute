---
kb_id: decision_d19b9909a674
type: decision
project: AI员工
created_at: '2026-09-28'
importance: 8
ai_generated: false
links:
  source:
  - ${code_root}/management_portal/society_service/resident_initialization.py
  - ${code_root}/management_portal/society_service/isa/residents.py
  - ${code_root}/management_portal/society_service/resident_fork_domain.py
  - ${code_root}/management_portal/society_service/resident_context
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
  number: 68
  created_at: '2026-09-28T10:45:00Z'
  created_by:
    user: tfi-exported-user
    agent: resident_2a1fc6cd272d
  updated_at: '2026-10-29T15:50:58Z'
  updated_by:
    user: ''
    agent: resident_24014e2b9bf6
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
| `DeleteResident` | 是 | 删除 Resident | 对 `lineage_id` 有 @contributor；对 `resident_id` 有 @admin |
| `GetResidentSummaries` | 否 | 批量读取与工作查看共用的累计摘要，供搜索、路由和摘要维护复用；可附带内容版本与漏写轮次 | 对 `resident_ids` 有 @viewer |
| `UpdateResidentWorkSummary` | 是 | 本人维护工作摘要，Steward 汇总本 Family 门面 | 免检 |
| `RequestResidentSummaryRebuild` | 是 | 请求完整重建独立 Resident 的累计摘要；Family 使用自身维护者 | 对 `resident_id` 有 @contributor |
| `GetResidentSummaryQueue` | 否 | 读取摘要队列中各状态的份数 | 对“共享”有 @viewer |
| `ClaimResidentSummaryJobs` | 是 | 认领待写的独立 Resident 摘要并取得写作说明与证据（仅限全局摘要职责承担者） | 免检 |
| `SubmitResidentSummary` | 是 | 提交一份已认领的摘要 | 免检 |
| `ReleaseResidentSummaryJob` | 是 | 放弃一份已认领的摘要并说明原因 | 免检 |
| `GetResidentSettings` | 否 | 读取运行设置与 Prompt 注入设置 | 对 `resident_id` 有 @viewer |
| `UpdateResidentSettings` | 是 | 修改运行设置，包括 Prompt 注入 | 对 `resident_id` 有 @contributor |
| `PreviewPromptInjection` | 否 | 预览一轮实际带上的注入内容 | 对 `resident_id` 有 @viewer |
| `GetResidentInitialization` | 否 | 读取既有初始化状态与执行身份 | 对 `resident_id` 有 @viewer |
| `RetryResidentInitialization` | 是 | 恢复初始化并保留原队列 | 对 `resident_id` 有 @contributor |
| `RestartSession` | 是 | 切换账号或 Provider（默认完整保留上下文），或显式重新开始模型会话 | 对 `resident_id` 有 @contributor |
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

**从来源 Resident Fork 出一个新 Resident，新 Resident 按选定方式继承来源的上下文。**

- 参数：`lineage_id`、`source_resident_id`、`fork_mode`、`context_source_resident_id`（可选，上下文来源，默认与 `source_resident_id` 相同；两者不同时只适用于 `message` 方式）、`message`（`fork_mode=message` 时必填，历史截止点）、`title`（可选）、`provider`（可选，登记的 Agent Provider 或 `provider_api`）、`account_id`（可选，目标 Provider 账号）、放置参数。
- `fork_mode` 取值：`full`（继承完整的 Portal 冻结上下文，默认方式）、`compacted`（新会话首次工作前压缩继承的上下文）、`recent-5` 与 `recent-10`（继承最近 5 或 10 轮对话）、`message`（继承到指定历史消息为止）。这些方式的操作对象和结果相同，都产生一个新 Resident，只在带入多少上下文上不同，因此使用同一条指令。
- 结果：新 Resident 的 ID 和记录来源的派生关系 ID。
- `fork_mode` 只接受上述取值，`native` 不是可用值或兼容别名。它与来源事实 `fork_method` 分开：历史记录中的 `fork_method=native_fork` 保留原有来源含义，不作为创建请求的参数。
- 省略 `provider` 时继承上下文来源 Provider。同 Provider 且省略 `account_id` 时继承来源实际账号；跨 Provider 时使用目标默认账号或显式账号，Model API 要求显式 API 账号。通用设置继续继承，模型与后端专用设置按目标能力校准；`compacted` 检查目标 Provider 的 `seeded_compaction` 能力。规则见 decision_5bf5bfbd52cb。
- 新 Resident 是普通 Resident，拥有自己的自有工作区，并默认继承来源的其他工作区绑定。来源证明、上下文选择和 Provider 会话的建立方式见 decision_5bf5bfbd52cb。
- Family 门面不能 Fork（decision_44cf587c1721），需要 Fork 具体的成员。

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

### `DeleteResident`

**把 Resident 标记为已删除，移除它的模型会话、成员关系、Lineage 中的引用和自有工作区。身份与历史保留，删除没有恢复入口。**

- 参数：`lineage_id`、`resident_id`。
- 结果：被删除的 Resident ID，以及删除 Leader 时新选出的 Leader ID。
- 被删除的是 Lineage 的 Leader 时，系统从该 Lineage 其余的 Resident 中随机选一个担任新的 Leader；没有其他 Resident 时，普通 Lineage 暂时没有 Leader，Project 层 Lineage 则拒绝这次删除（decision_3e07c6d35cac）。需要指定继任者时，先用 `UpdateLineage`（decision_4637ca4a69e2）更换 Leader，再删除原 Leader。
- Resident 身份永久保留在 `residents` 表中；`deleted_at` 为空表示正常，有值表示已删除，`deleted_by` 记录删除操作者。删除状态以这两个字段表达，不另设删除身份表（decision_0f57656a2acd）。同 ID 的 Agent、主 trace、已有片段和执行历史继续保留，空 trace 也保留。
- 正常目录、发现列表、调度和消息投递排除已删除的 Resident。按 ID 查询历史时仍遵守原有读取权限，可查看历史消息、执行轨迹和 Fork 来源；历史读取不清除删除状态，也不启动工作。
- 删除失败时，身份和节点保持原样。
- Seed 不随 Resident 删除：Seed 不记录写出它的 Resident，它的 Markdown 在 Lineage 目录；以这个 Resident 为来源的候选 Fork 删除，Seed 保留（decision_4adcc1ff2f2c）。
- 一次删除多个 Resident 和组件使用 `ApplyLineageChanges`（decision_4637ca4a69e2），它采用相同的删除语义。删除 Function Block 实例时，实例创建的 Resident 也按这条指令的语义删除。实例创建的 Resident 也可以直接用这条指令删除，实例下一次重新展开时按定义补建。
- Family 的门面和 steward 不能直接删除，需要先 `Unfamilize`（decision_44cf587c1721）。

### `GetResidentSummaries`

**批量读取 Resident 的累计摘要；搜索、路由、工作查看和摘要维护读的是同一份内容。**

- 参数：`resident_ids`，一次最多 128 个；可用 `fields` 选择返回的摘要字段；`include_maintenance` 为真时附带维护所需的信息。
- 结果：`summaries` 与 `unavailable_resident_ids`。每份摘要包含 Resident ID、名称、所属 Lineage、摘要内容（当前重点、进行中的线程、长期经验、近期成果、主要负责、辅助经验、负责的组件、分支差异、适合与不适合接收的任务）、尚未完成的请求、更新时间，以及摘要是否过期。
- `include_maintenance` 时每项另有内容版本 `revision`、汇总来源 `source_versions`、漏写的输入 `missing_message_ids` 和维护者 `writer_id`；尚无摘要但调用者能看到的 Resident 也返回一项，`revision` 为 0，供首次 `UpdateResidentWorkSummary` 使用。
- Family 成员本人维护近期事实，本 Family Steward 归纳阶段与长期层；门面只由本 Family Steward 汇总；全局 resident-summary 职责仅维护独立 Resident。读取以调用者自己的身份进行：调用者看不到或尚无摘要的 Resident 列入 unavailable_resident_ids；摘要存储不可用时返回可重试错误。工作查看、搜索与路由复用三层的统一九字段投影。

### `RequestResidentSummaryRebuild`

**请求完整重建独立 Resident 的累计摘要。**

- 参数：`resident_id`、`reason`（可选，例如“职责调整”）。
- 结果：Resident ID 与请求结果。
- 重建以调用者自己的身份请求，调用者必须能看到该 Resident；Family 成员及门面拒绝全局重建，使用各自维护者。独立 Resident 的请求只加入全局写作队列，由承担者依证据维护同一份累计摘要。

### 工作摘要与覆盖检查

维护者用 GetResidentSummaries（`include_maintenance`）读取自己的摘要、revision 和 missing_message_ids，读取继续按当前调用者权限执行。

UpdateResidentWorkSummary 接收 resident_id、expected_revision、可选 summary、message_ids、source_versions、consolidation。本人用 summary 替换近期层（不是把三层投影抄回）；message_ids 最多 128 个，必须是本人正在运行或已经完成的输入。首次提供有效九字段摘要，已有摘要可省略 summary 只登记覆盖。内容无变化时 revision 与内容更新时间不变，输入覆盖照常推进。门面只允许本 Family Steward 写入，必须保存 GetFamilySummaryInputs 的 source_versions，不登记成员输入 ID。

include_maintenance 另返回 layers（recent、phase、long_term）、maintenance（各层归纳时间和新覆盖轮次水位）、consolidation（due、level、reason、total_chars、policy）。旧摘要无损视作近期层，旧超限摘要仍可读取并进入归纳待办；不自动截断或回放原始对话。

本 Family Steward 可用 consolidation 归纳本组业务成员。Router 和 Steward 免写自身摘要，不参加自身覆盖检查、漏写提醒与时间层归纳；门面由 Steward 汇总。必须给出替换后的 phase，可同时给出 long_term；达到长期阈值时必须包含 long_term。归纳不能同时传 summary、message_ids 或 source_versions。成功提交原子消费已读版本的 recent，替换指定高层并推进对应水位；即使统一投影未变，层内容改变也递增 revision。并发成员更新或重复旧提交引起版本冲突，不消费新事实。外层 Steward 不能归纳内层门面；解除 Family 后的全局写作以当前统一内容为来源，并接管整份摘要。

active_threads 承载未决承诺、阻塞与重要约束。归纳遗漏的旧事项自动补入 phase；关闭必须在 resolved_threads 中提供「原事项：解决、取消或交接说明」。重要项目历史另存工作记录，摘要保留有效结论与记录入口；不改变原始日志保留策略。

所有新写入共用总预算，默认 3000 字符，计算各时间层、各语义字段所有文字之和（包括重复文字、标点和空格）。超限整次写入失败，不静默截断；高层合计最多占 75%，保留近期空间。平台按时间、工作量与容量判断归纳待办，不决定信息重要性。默认阶段 20 个新覆盖轮次或 86400 秒，长期 100 轮或 604800 秒，达到总预算 80% 提前归纳。部署配置 PORTAL_SUMMARY_TOTAL_CHARS、PORTAL_SUMMARY_PHASE_ROUNDS、PORTAL_SUMMARY_PHASE_SECONDS、PORTAL_SUMMARY_LONG_TERM_ROUNDS、PORTAL_SUMMARY_LONG_TERM_SECONDS；预算至少 400，其余为正整数。维护轮次不计数量，重复覆盖不重计。独立 Resident 的认领结果也返回 summary_policy，写入执行相同总预算。

Family 内部检查成员摘要的 CheckResidentWorkSummary 和读取门面汇总材料的 GetFamilySummaryInputs 属于 Family 内部机制类（decision_b77fde81e7a0）。

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

### `UpdateResidentSettings`

**修改 Resident 的运行设置。**

- 参数：`resident_id`、`expected_revision`（可选，读取时得到的 `settings_revision`）、`changes`。`changes` 只接受以下字段：`model`、`reasoning_effort`、`service_tier`、`permission_preset`、`prompt_injection`、`allow_subagents`、`agent_harness`、`capability_config`、`collaboration_mode`、`prompt_context`、`backend_options` 和仅供 Model API 使用的 `backend_route_id`。字段取值由目标 Provider 校验。
- `changes.prompt_injection` 给出 `preset`（`standard`、`full`、`minimal`、`pure`、`custom`）、`parts`（要改的类别及其时机），至少一项：
  - 只给 `preset` 时，设置换成这个预设。
  - 给了 `parts` 时，在所给预设上修改；没给预设时，在当前设置上修改。结果记为自定义；与某个预设完全相同时，记为该预设。
  - 非法的类别或时机直接报错。下一轮生效。
- 结果：保存后的设置、新的 `settings_revision`、`application`（逐项生效状态，不表示后端已接受）、`next_turn_fields`（从下一轮才生效的字段），以及与 `GetResidentSettings` 相同的 `prompt_injection` 读法。
- 修改设置不会中断当前工作。`collaboration_mode` 只适用于 Codex，取值 `default` 或 `plan`，从下一轮开始生效。重复保存相同的值不产生变化。
- 设置按版本条件保存：`expected_revision` 与当前 `settings_revision` 不一致时拒绝，调用者重新读取并合并后再提交；省略只为兼容旧调用，页面保存总是携带已读版本。版本覆盖完整运行配置，规则见 decision_8b2fa224616b。
- 这条指令不改变 Lineage 定义。原生后端切换 Provider 或账号需要迁移模型会话，使用 `RestartSession`。Model API 无原生会话，允许通过 `changes.backend_route_id` 选择登记的 API 账号，并与模型、上下文及协议参数在同一事务中保存；失败时全部回滚，从下一轮生效。账号只存放在 Resident 配置中，不复制到运行设置。
- Model API 的 `prompt_context` 可配置系统提示、预设消息及 `history: {enabled, max_tokens}`；`backend_options` 保存协议参数、超时和重试。新建文本类 Resident 默认携带历史，新建 Jev 默认关闭；已有记录缺少历史设置时保持关闭。Settings 的能力展示、历史选择与 Jev 题型规则见 decision_6c2f91a7d84e。
- 目标是 Family 门面时，读取和修改的是这个 Family 的 Router Resident 的设置；门面自己不运行，没有独立设置。

### `PreviewPromptInjection`

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

- 参数：`resident_id`（必填）。要求目标 Resident 的 @contributor；修改权限拒绝返回 `instruction.forbidden`。内置默认权限方案分别将这两条指令分类为 @viewer 与 @contributor；实际调用仍按当前启用方案鉴权。
- 恢复既有 Resident 的初始化，不重发原业务。返回最小可持久受理回执：`resident_id`、`initialization_id`、`status`、`phase`、`candidate_agent_id`、`candidate_binding_id`；同幂等键重放该回执，当前状态用 `GetResidentInitialization` 读取。进行中的 attempt 被复用，显式失败／取消后的重试才可建立新候选。
- 当前生命周期或业务工作活动、原执行仍活动、绑定已变化、冻结 Fork 凭据不匹配等冲突阻止重叠恢复。原压缩完成事实可核实则直接收敛；原生结果不明时使用隔离候选 Session，验证规范压缩与绑定后原子提升。失败保留旧绑定和原输入，完整恢复合同见 decision_5bf5bfbd52cb。
- 接收开关保持原值；关闭接收也可显式恢复初始化，完成后业务仍等待开放。受理不等于初始化或排队任务已完成。

### `RestartSession`

**切换 Resident 的账号或 Backend，或显式重新开始会话；原生目标建立新会话，Model API 目标直接保存调用配置，并按选定的上下文方案决定历史边界。**

- 参数：`resident_id`；`operation`（可选，`switch_account` 切换账号并保留上下文，`restart` 显式重新开始会话，默认 `restart`）；`provider`（可选，登记的 Agent Provider 或 `provider_api`，省略时保持当前 Provider）、`account_id`（可选）；`model` 与 `reasoning_effort`（可选，必须在目标账号能力内；同 Provider 省略 `model` 时保持当前模型，跨 Provider 须明确选择）；`collaboration_mode`、`service_tier`、`permission_preset`（可选，目标会话的运行设置，切换成功后生效；`service_tier` 为空字符串时使用默认等级）；`context_strategy`、`active_execution_policy`（可选）；`resume_failed_input`（可选，默认 `false`）。
- `context_strategy` 取值：`preserve`（默认，用可验证的原生完整 Fork 或严格的历史导入保留有效上下文，不执行模型轮次；无法完整保留时拒绝并保持原绑定，不降级为其他方案）、`native`（仅同 Provider、同账号的原生 Fork）、`compact` 与 `visible`（明确选择的有损模型交接）、`fresh`（清空有效上下文，下一条真实输入时才建会话，页面对话保留）；`smart` 只为旧调用保留，等同 `preserve`。`operation=switch_account` 只接受 `preserve`，目标与当前账号相同时不做任何操作。各方案的条件与上限见 decision_c47a13e8b962。
- Model API 目标额外接受 `prompt_context` 与 `backend_options`，与账号、模型统一校验并保存；推理参数只在目标模型支持时放入 `backend_options.parameters`。跨入 Model API 须显式选择 API 登记账号。原生目标不接受这两个 API 专用参数。
- 原生与 Model API 之间不能完整保留上下文：有上下文时 `preserve` 被拒绝，原绑定不变。目标为 Model API 时不接受 `native`、`compact` 与 `visible`；原生 → Model API 不发初始化请求，解除当前原生绑定但保留旧会话，配置提交失败恢复旧绑定。Model API → 原生先初始化新会话，成功后切换，失败保留原配置。迁移过程、恢复和接单规则见 decision_c47a13e8b962。
- `resume_failed_input` 为 `true` 时，`restart` 换到其他账号或 Provider 后恢复最近一条失败输入；`switch_account` 不接受 `true`，失败输入由具体任务的恢复入口处理。省略或 `false` 时切换不重试失败输入。
- `active_execution_policy` 取值：`wait`（默认，当前工作已进入执行时持久保存迁移安排，返回 accepted／waiting_current_turn；本轮继续，后续接单暂停，平台扫描在本轮结束后按原调用者重新鉴权并提交迁移）或 `cancel`（先请求停止当前工作，停止确认后重试）。关闭页面或平台重启不丢失等待安排；可通过 CancelSessionRestart 取消，只有原来开放且此后没有人工接单操作时才恢复接单。尚未执行的保留消息随新会话继续。等待安排存在时相同请求幂等返回，修改目标须先取消原安排；已经提交的迁移继续沿用原有替换规则。尚无会话的同 Provider 排队执行可以直接调整账号，规则见 decision_c47a13e8b962。
- 结果：`restart_id`、`status`，以及 `operation_id`（稳定操作 ID；账号未变的 `switch_account` 为空）、`operation`、`phase`（等待、准备、导入、提交或终态阶段）、`context_result`（`pending`、`preserved`、`fresh`、`summarized` 或 `unchanged`）和 `actual_binding`（当前实际使用的绑定，失败时为原绑定）。切换完成前继续保留原会话；完成后新会话成为当前会话，所选 Provider、账号及随请求给出的运行设置写入 Resident 的运行设置。
- 这个 Resident 已有一次尚未完成的重启时，相同参数返回已有重启记录；不同参数先取消已有重启，等生命周期清理完成后按新参数开始。清理超过本次调用的等待期限时返回冲突，调用者稍后重试。其他生命周期操作尚未完成时也返回冲突。
- Resident 还没有模型会话时，这条指令直接保存 Provider 与账号选择。Portal 运行设置卡片的“账号切换”与“重新开始会话”使用这条指令。

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
| `UpdateResidentWorkSummary` | `resident_id`、`expected_revision`、`summary`、`message_ids`、`source_versions`、`consolidation` |
| `CreateResident` | `lineage_id`、`title`、`provider`、`account_id`、`backend_role`、`settings`、`workspace_paths`、`family_id`、`near_resident_id`、`placement`、`x`、`y` |
| `ForkResident` | `lineage_id`、`source_resident_id`、`fork_mode`、`context_source_resident_id`、`message`、`title`、`provider`、`account_id`、`family_id`、`near_resident_id`、`placement`、`x`、`y` |
| `PromoteSeed` | `lineage_id`、`seed_id`、`title`、`purpose` |
| `GetResident` | `resident_id` |
| `SearchResidents` | `q`、`resident_ids`、`can_send`、`limit` |
| `UpdateResident` | `lineage_id`、`resident_id`、`title`、`avatar` |
| `DeleteResident` | `lineage_id`、`resident_id` |
| `GetResidentSummaries` | `resident_ids`、`fields`、`include_maintenance` |
| `RequestResidentSummaryRebuild` | `resident_id`、`reason` |
| `GetResidentSummaryQueue` | 无 |
| `ClaimResidentSummaryJobs` | `limit` |
| `SubmitResidentSummary` | `claim_id`、`summary` |
| `ReleaseResidentSummaryJob` | `claim_id`、`reason` |
| `GetResidentSettings` | `resident_id` |
| `UpdateResidentSettings` | `resident_id`、`expected_revision`、`changes` |
| `PreviewPromptInjection` | `resident_id`、`turn` |
| `GetResidentInitialization` | `resident_id` |
| `RetryResidentInitialization` | `resident_id` |
| `RestartSession` | `resident_id`、`operation`、`provider`、`account_id`、`model`、`reasoning_effort`、`collaboration_mode`、`service_tier`、`permission_preset`、`prompt_context`、`backend_options`、`context_strategy`、`active_execution_policy`、`resume_failed_input` |
| `GetSessionRestart` | `resident_id` |
| `CancelSessionRestart` | `resident_id`、`restart_id` |
| `BindWorkspace` | `resident_id`、`path`、`purpose` |
| `UnbindWorkspace` | `resident_id`、`workspace_id` |

### 累计摘要的内容与维护事实

工作摘要和发现画像共用 `work_summaries` 中的一份内容与 revision，避免不同写作者维护相互冲突的职责、进度和版本。Family 的工作汇总与 Which Resident 的候选推荐复用该内容；候选可见范围、按角色划分的维护责任和输入覆盖仍分别管理。`snapshots` 保存来源游标、指纹、维护状态及错误等元数据，不再保存一份可回退读取的摘要正文。历史迁移仅在缺少有效内容版本时用旧快照填充，已有自写内容优先；这不构成持续双写或回退来源。全局写作与向量结果都检查所依据的内容版本，过期结果不覆盖新内容。本节是共享摘要存储规则的唯一正文；Family 门面身份、推荐权限和模型调用方式仍由各自设计负责。
