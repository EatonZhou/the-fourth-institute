---
kb_id: decision_5bf5bfbd52cb
type: decision
project: AI员工
created_at: '2026-09-01'
version:
  number: 60
  created_at: '2026-09-01'
  created_by:
    user: ''
    agent: ''
  updated_at: '2026-10-19T19:59:40Z'
  updated_by:
    user: tfi-exported-user
    agent: resident_865c3c24f251
importance: 9
metaness: 2
ai_generated: false
links:
  source:
  - ${code_root}/management_portal/society_service/resident_initialization.py
  - ${code_root}/management_portal/society_service/resident_execution_domain.py
  - ${code_root}/tests/cases/test_lineage_fork_identity.py
  - ${code_root}/tests/cases/test_fork_runtime_inheritance.py
  - ${code_root}/management_portal/static/portal_chat_workspace.js
  - ${code_root}/management_portal/static/lineage_graph.js
  - ${code_root}/management_portal/static/hatg_panel.js
  - ${code_root}/management_portal/society_service/isa/residents.py
  - ${code_root}/management_portal/execution/context_seed.py
  - ${code_root}/management_portal/society_service/resident_runtime_domain.py
  - ${code_root}/management_portal/society_service/lineage_branch_transitions.py
  - ${code_root}/management_portal/society_service/resident_fork_domain.py
  - ${code_root}/management_portal/society_service/resident_domain.py
  - ${code_root}/management_portal/execution/agent_data_store.py
  - ${code_root}/management_portal/execution/backends.py
  - ${code_root}/management_portal/execution/codex_history.py
  - ${code_root}/management_portal/execution/codex_history_delivery.py
  - ${code_root}/management_portal/society_service/resident_fork_context.py
  - ${code_root}/management_portal/agent_prompt_context.py
  - ${code_root}/management_portal/execution/agent_data_projection.py
  - ${code_root}/management_portal/execution/session_manager.py
  - ${code_root}/tests/cases/test_lineage_backend.py
  - ${code_root}/tests/cases/test_resident_execution_provider.py
  - ${code_root}/tests/cases/test_resident_folder_creation_contract.py
  - ${code_root}/tests/cases/test_resident_fork_context_delivery.py
  - ${code_root}/tests/cases/test_codex_fork_history.py
  - ${code_root}/tests/cases/test_agent_data_projection.py
  - ${code_root}/management_portal/society_service/resident_settings_contract.py
  - ${code_root}/management_portal/society_service/resident_storage.py
  related:
  - decision_ccb99a00eebd
  - decision_7ef392b5f60d
  - decision_d19b9909a674
  - decision_c616d197abbb
  - decision_6fc653d55c79
  - decision_c47a13e8b962
  - decision_b1ab45c4b3ec
  - decision_0f57656a2acd
  - decision_8838269736d3
  supersedes: []
  derived_from: []
---
# Resident Fork 的上下文继承与 Provider 选择

Resident 是 Portal 中可以长期工作的 AI 节点；一组有协作关系的 Resident 组成一个 Lineage。Fork（派生）是从一个已有 Resident 创建新 Resident 的操作：新 Resident 有自己的身份、对话、模型会话和工作目录，同时按用户选择的方式带上来源 Resident 的一段历史。用户常用它来分出一条独立的尝试路线，或让新成员带着前面的讨论接手工作。

本篇说明 Fork 从哪里发起、有哪些方式、新 Resident 得到什么、历史怎样选取和交给新的模型会话，以及系统怎样记录来源。新 Resident 之后每一轮输入怎样组装，见 decision_c616d197abbb；Resident Folder 与工作区的目录结构见 decision_ccb99a00eebd 与 decision_8838269736d3；`ForkResident` 指令的调用格式见 decision_d19b9909a674。

## 基本概念

| 名称 | 含义 |
| --- | --- |
| 主 Agent 与 trace | 每个 Resident 有同 ID 的主 Agent 和独立有序轨迹；轨迹引用不可变 Segment。不同轨迹可共享内容引用，追加或替换各自的引用互不影响。 |
| 有效上下文 | trace 中最近一次压缩、明确清空或上下文重启边界之后的内容。没有启动过模型会话的 Fork，从自己的已建立轨迹取得上下文。 |
| 直接来源与上下文来源 | 直接来源是新 Resident 在目标 Lineage 中派生所依托的节点；上下文来源提供被引用的内容。跨 Lineage 的“从这里 Fork”允许两者不同。 |
| 轮次 | trace 中一次独立用户输入及其后续回答、工具调用和结果；已知属于同一 Provider 轮次的追加输入归入同一轮。随其他输入送达的背景消息按其持久 `released_with` 关联归入承载轮次，不单独计轮；其余缺少 Provider 轮次记录的用户输入仍按 trace 用户输入边界选择。它不在 Resident 来源表中重复保存。 |
| 上下文交付 | 把目标 trace 中选出的内容送入新模型会话的操作；执行请求可携带临时上下文参数，接收结果由 AgentData 的运行记录保存。 |

## 从哪里发起 Fork

- **Lineage 画布的节点「+」。** 在可编辑的 Lineage 画布上，直接点击 Resident 节点的「+」，或聚焦后按 Enter / Space，立即以该节点为来源执行“完整上下文”Fork，不需要先选择菜单项。把鼠标停在「+」上，或聚焦后按上下方向键，则打开“选择 Fork 方式”菜单；仅打开菜单不创建任何东西。菜单列出分支表单中的五种创建方式，选择后立即按该方式创建；其中 `new session` 创建空白 Resident，不继承历史、也不建立继承边。直接点击与菜单选项共用同一创建入口的有效性和忙碌状态检查，提交前关闭菜单，创建中禁止重复提交。
- **分支表单。** 节点详情中的“创建分支”表单提供同样五种方式，并允许填写名称、选择 AI Provider 和该 Provider 下的账号。画布与侧栏表单共用 Execution 的 Provider、账号及能力目录，提供全部已登记的 Agent Provider 与 Model API 账号。切换 Provider 后重新列出目标账号；没有可用账号时显示原因并禁止提交。
- **对话中的“从这里 Fork”。** 在 Chat Workspace 中，对一条用户消息选择“从这里 Fork”，在当前 Lineage 中创建截止到这一轮的新 Resident；也可以选择另一个 Lineage 和其中的父节点，把新 Resident 建在那里。
- **指令。** Resident 和其他调用者使用 HATG ISA 指令 `ForkResident`。需要把新 Resident 放进另一个 Lineage 时，先 `ForkResident`，再用 `MoveResident` 迁移。

除页面上的 `new session` 调用 `CreateResident` 外，以上入口都执行 `ForkResident`，遵守本篇的全部规则。Fork 只创建 Resident，不投递消息，也不启动工作；需要新 Resident 开始工作时，调用者另行 `DeliverMessage`，或在 Lineage 中用 Event 连到它的 Invoke 端口。

## Fork 方式

`ForkResident` 的 `fork_mode` 决定新 Resident 带走多少历史。页面上的“new session”调用 `CreateResident` 在来源旁边创建一个空白 Resident，使用表单选定的 Provider、账号和目标 Provider 的默认运行设置，不复制历史，也不画继承关系。快捷创建未指定 Provider 时沿用来源 Provider。

| `fork_mode` | 页面名称 | 新模型会话收到的历史 |
| --- | --- | --- |
| `full`（默认） | 完整上下文 | 来源当前的整个有效上下文窗口，已生效的压缩结果包含在内。 |
| `compacted` | 压缩继承 | 先把来源的有效窗口导入新会话，再在新会话开始工作前做一次压缩；新会话之后只使用压缩结果。只有能在首轮之前压缩导入上下文的后端支持（Provider 目录的 `seeded_compaction` 能力，目前只有 Codex），来源窗口必须非空。 |
| `recent-5`、`recent-10` | 最近 5 轮、最近 10 轮 | 有效窗口中最近 5 或 10 轮对话；不足时带走全部轮次。 |
| `message` | 从这里 Fork | 截止到所选用户消息那一轮的历史，从截止点之前最近一次压缩开始。 |

“一轮”指一条用户输入，以及模型对它的回答、工具调用和结果。每轮完整保留，消息中的链接和交付路径保持原值。

## 设计点

### 创建结果

- **每次 Fork 创建一个新的普通 Resident。** 它有新的 `resident_*` ID，加入来源所在的 Lineage（或“从这里 Fork”时选定的目标 Lineage），可管理它的人与该 Lineage 的 owner 相同。系统中没有 Fork 类型的 Resident，之后的对话、权限、队列、目录和删除都按普通 Resident 处理。
- **Family 门面不能作为来源。** `ForkResident` 报冲突“Family 门面不能 Fork；请 Fork 具体的成员”（decision_44cf587c1721）。
- **新 Resident 的名称默认为“<来源名称> · 分支”。** 调用者提供 `title` 时使用它，之后可以随时改名。
- **Lineage 展示来源关系。** 新节点出现在直接来源附近，图上的 `inheritance` 连线由 Resident 派生事实生成。同一直接来源与上下文来源在派生记录中维护，图边不另存一份可以独立修改的来源真值。移动 Resident 可以改变当前图拓扑，历史来源证明仍按真实创建事实保留。
- **新 Resident 的对话由它自己的 trace 投影。** 创建时只引用本次选中的上下文片段；最近 N 轮只包含这些轮次，完整上下文从最近压缩结果开始。压缩点、清空或上下文重启边界之前的历史不进入子轨迹；窗口内已经存在的片段不因缺少 Execution、Provider 接收回执或运行状态而被排除。来源继续工作、压缩或转换内容不改变子轨迹。
- **新 Resident 有同 ID 的独立主 Agent 和自己的 trace。** 登记成功前建立 Agent 与普通可为空的 trace。冻结范围内的不可变片段通过引用共享；之后新内容及当前版本转换只作用于自己的 trace，来源片段的原作者不变。例如来源 A 为 `[1, 2, 3, 4]`，在第 3 段处 Fork 出 B，B 新增一轮后可以为 `[1, 2, 3, 5]`，A 仍为 `[1, 2, 3, 4]`。Portal Fork 的主 Agent 不通过 `parent_agent_id` 表达来源，该字段只属于 Provider 子 Agent 的直接父级。

### 页面创建后的视图

- **页面内收敛创建结果。** Portal 的侧栏、分支表单、节点快捷菜单与 Chat 消息入口在创建成功后取得目标 Lineage 的最新状态，更新已挂载的 HATG 图并定位新 Resident。更新图和切换目标在当前页面内完成，不重新加载整张页面；未参与创建的面板和用户的页面状态保持不变。页面上的 `new session` 虽调用 `CreateResident`，也使用同一视图更新方式。
- **Fork 成功后打开新 Resident 的 Chat。** 从画布节点「+」、快捷菜单、分支表单、侧栏或 Chat 消息发起 Fork，创建成功并取得包含新节点的 Lineage 状态后，选中新卡片，并通过统一对话导航在当前页面打开新 Resident 的 Chat。仅悬停打开菜单、创建尚未完成或创建失败时保持原对话，不提前切换。画布上的 `new session` 创建空白节点后仍只选中新卡片，由用户点击卡片打开 Chat；侧栏的 `new session` 仍直接打开新对话。
- **创建成功与页面定位分别处理。** 指令已创建 Resident，但返回的 Resident ID 或目标 Lineage 状态不完整，或页面随后无法定位新节点时，页面提示“已创建，但暂未定位”，保留当前页面供用户刷新查找；不能把它当作创建失败再自动提交一次。只有创建指令本身失败，才报告创建失败。

### 运行设置与账号

- **相同 Provider 的新 Resident 继承来源当前生效的运行设置。** 包括模型、推理强度、服务等级、权限预设、沙箱与审批方式、Harness 功能、初始指令、子 Agent 和协作模式。创建时经唯一设置 owner 读取，目标账号适配器校准后写入新 Resident 自己的数据库配置；之后双方独立修改。注册完成的 Resident 不读取 `runtime/settings.json`、顶层模型字段或旧 `runtime_settings` 副本来决定当前设置。
- **切换 Provider 时，模型与专用参数由目标 Provider 确定。** 通用 Prompt、Harness 和授权范围继续继承；模型、推理强度、服务等级、协作模式和后端协议选项使用目标的默认配置，再由所选账号校验。目标不支持的子 Agent 开关关闭，权限预设按目标能力校准。来源 Resident 的账号、设置、会话和轨迹保持不变。
- **发起者对新 Resident 没有工作区权限时，权限类设置降为只读。** Fork 不会让发起者得到比原来更大的权限。
- **Provider 与账号是独立的 Fork 选择。** `provider` 省略时沿用上下文来源的 Provider；相同 Provider 且省略 `account` 时继承来源实际账号，显式账号由目标 Provider 解析。跨 Provider 时不携带来源账号，使用指定目标账号或目标默认账号；Model API 要求选择已登记的 API 账号。无效 Provider 或账号在提交新 Resident 前报错。
- **压缩继承由目标 Provider 的能力决定。** 目标必须支持 `seeded_compaction`；表单对不支持的目标禁用此选项，并在切换目标时恢复完整上下文模式。直接指令请求不支持的组合时明确报错。

### 工作区

- **新 Resident 总是有自己的自有工作区，并从那里启动。** Portal 为它创建新的 Resident Folder 和自有工作区。
- **来源的其他工作区绑定被一并复制。** 额外绑定列表全部显式复制，不按用途或交接来源筛选；来源自有 Workspace、Resident Folder 和 Lineage Folder 不作为继承项。复制绑定不复制外部目录。跨 Lineage 消息 Fork 使用所选上下文来源的额外绑定，不混入目标父节点的绑定；新 Resident 的自有 Workspace 始终按目标位置计算。
- **需要隔离的代码副本时，用工作区交接。** 例如为多个 Fork 出的 Resident 各准备一份独立的 Git worktree，使用 decision_7ef392b5f60d 的交接方式。

### 上下文窗口的选取

- **所有 Fork 都从 AgentData 选取内容。** PostgreSQL 中的不可变片段与当前 trace 引用是来源。新 Resident 创建时就建立选中的有序引用，之后不会回到父 Resident 读取初始化材料。
- **完整上下文只继承当时有效的窗口。** A 的轨迹为 `[1,2,3,压缩,4]` 时，B 的初始轨迹是 `[压缩,4]`；B 工作后可以变为 `[压缩,4,5]`。A 后续产生第 6 段或替换第 4 段，都不改变 B。
- **最近 N 轮按当前窗口内的独立用户输入边界选择。** 已记录的同一 Provider 轮次可以将追加输入归为一轮；随请求送达、带有持久 `released_with` 关联的背景消息保留在承载轮次中，不另开一轮，也不截断该轮后续回答或工具内容。背景消息的用户角色与 Segment 引用保持不变，不按作者、提醒文字或缺少 Provider turn ID 来分类。其余没有 Provider 轮次记录的真实用户输入各自开启一轮，后续回复、工具内容及无轮次标注的片段按 trace 顺序保留。不足 N 轮时全部保留。只有选择了窗口的全部轮次时才带上开头的压缩结果，以免最近几轮意外继承更早内容。被排除的段不进入子 trace。
- **“从这里 Fork”的截止点在上下文来源自己的 trace 中解析。** 直接定位所选用户消息在 trace 中的位置；有轮次记录时截到该轮最后一段，没有时截到下一条独立用户输入或后续压缩、清空、重启边界之前。截止选择与最近 N 轮共用上述轮次边界；随该轮送达的背景消息不成为提前截止点。之后再应用截止点之前的压缩与清空规则。缺少旧 Execution 或 Provider Session 不阻止 Fork；Fork 不为此修补运行回执或重写来源 trace。之后发生的压缩或其他会话的历史不进入结果。未运行过的嵌套 Fork 同样受自身 trace 范围约束。
- **找不到必要截止事实时明确报错。** 不调用 Provider 文件或原生会话来补全。已经登记但从未产生模型内容的 Resident 可以正常 Fork 出一个空 trace。
- **不同上下文来源只适用于消息 Fork。** 操作者必须能管理目标 Lineage，并且能读取来源消息。其他方式提供不同的上下文来源时拒绝请求。
- **继承引用与模型交付分别处理。** 来源启动指令、仅属于来源 Provider 的注入内容及专用工具内部段仍按窗口规则排除。其余选中片段保留引用；正文转换不会丢掉子 trace 中的引用。交付时省略不可解码的加密占位，压缩片段需要可恢复的 replacement_history。

### Fork 后的 Chat

- **Chat 只显示新 Resident 自己轨迹中的消息。** 完整上下文从来源最近一次压缩开始，来源在这次压缩之前的对话不在子轨迹里，也就不出现在新 Resident 的 Chat 中；压缩片段本身不是聊天消息。例如来源刚自动压缩完就被 Fork，新 Resident 的 Chat 只有压缩之后的一两条消息，而模型拿到的上下文与来源当时相同：压缩摘要加之后的内容。
- **历史开头显示继承卡片。** Chat 加载到最早一页时，在第一条消息之前显示一张卡片，写明从哪个 Resident Fork 而来、Fork 方式和时间；消息 Fork 的上下文来源与直接来源不同时，另写上下文来源。卡片说明继承了什么：以来源哪个时间的压缩开头，之后有多少条继承的消息显示在下方。之后清除过上下文时，卡片说明继承的上下文已被清除。
- **窄 Chat 中卡片保持可读可操作。** 来源长标题、上下文说明和按钮文字随面板宽度换行，不横向越出卡片或裁掉文字；内容超出高度时随消息区纵向滚动，来源名称链接和压缩摘要入口仍可点击。
- **压缩摘要按需展开。** 继承的窗口以压缩开头时，卡片提供“查看压缩摘要”。展开后读取并显示压缩中可读的用户与 Agent 消息，不显示来源会话的指令；Provider 加密保存的摘要（例如 Codex 的远程压缩）只说明无法显示。
- **来源名称链接到 Fork 位置。** 调用者能看到来源时，卡片中的来源名称是链接，打开来源的 Chat 并定位到 Fork 时最后一条继承的消息；消息 Fork 由上下文来源的名称定位到所选的用户消息。卡片不为此另设按钮：继承的消息已经显示在卡片下方，跳转只用于查看来源在 Fork 之后的对话。看不到来源时，卡片只显示来源 ID，不提供链接。
<!-- wall: {"key": "2118689397d263ea"} -->
- **来源与新 Resident 在浏览器中各自保存消息和订阅。** 两者收到同一消息 ID 的事件时正文互不覆盖；发给另一个 Resident 的操作回执被拒绝；关闭新 Resident 的订阅不影响来源的订阅。
- **卡片的事实来自对话快照。** `GetConversation` 为 Fork 出的 Resident 返回 `inheritance`（decision_2fe6fd5367fb）：来源与方式来自 `resident_derivations`；继承范围来自 AgentData 在创建时写下的 forked 回执，即子轨迹的前 N 个片段；压缩正文只在 `include_inherited_context=true` 时读取。

### 启动指令

- **Fork 不带走来源会话的启动指令片段。** Portal 在模型会话开始时注入一次性启动指令（Portal 核心规则，见 decision_c616d197abbb），它在 AgentData 中是一个单独的隐藏片段。Fork 挑选窗口时跳过这类片段，避免新 Resident 把来源会话的启动内容当成自己的。已经被压缩进压缩片段 `replacement_history` 的启动内容无法单独分出，会随压缩结果一起继承。
- **新 Resident 的第一轮带有自己的启动指令。** Fork 出的 Resident 开始自己的模型会话，第一轮和普通新会话一样注入 Portal 核心规则（`<portal-system-prompt>`），并按 Provider 的首轮规则附上 `<resident-identity>`。来源曾经压缩过时，压缩结果里继承的那份启动内容仍然保留，新 Resident 自己的这份在它之后出现。
- **新 Resident 每一轮都带有自己的运行身份。** 每轮输入开头的 `<resident-runtime-identity>` 写明新 Resident 自己的 ID 和所在 Lineage。
- **纯文本历史注明继承来源，继承的记忆归新 Resident 所有。** 继承的历史里保留着来源 Resident 的 `<resident-runtime-identity>`，而运行身份块要求“不得使用历史上下文中其他 Resident 的身份”。2026-10-11 一个 OpenCode full Fork 因此把继承的暗号当成别人的记忆，拒绝回答（同样输入的 recent-5 Fork 正常回答）。纯文本形式的 `<resident_trace_context>` 开头因此写明“此 Resident 由 <来源> Fork 而来，以下上下文继承自它”，其中的其他 Resident 身份属于继承的历史，这些记忆和工作也属于新 Resident，行动时使用自己的运行身份（`resident_execution_domain._portable_context_execution_job`）。Resident 自己更换 Provider 或重建会话时没有来源 Resident，不加这段说明。

### 来源事实与运行状态的归属

| 信息 | 保存位置与用途 |
| --- | --- |
| 来自哪里 | `resident_derivations`：source_resident_id、context_source_resident_id、forked_from_at、fork_mode；消息 Fork 另保存 source_message_id。 |
| 继承了什么 | 目标 Agent 的 trace 中有序 Segment 引用。内容原作者保持不变；没有第二份 Resident 初始化材料表。 |
| 原始会话、轮次和消息时间 | AgentData 的消息、执行与 Provider 会话事实。来源表不重复存这些数据，也不保存 Codex 专用来源字段。 |
| 首轮历史是否送达 | 当前目标 Provider 会话中本 Agent 的输入接收记录，以及 Provider 导入记录。Lineage 不保存对应镜像标记。 |
| 首轮前压缩 | Resident 执行状态中的有界压缩策略及任务进度，压缩内容保存在 AgentData。 |

`fork_mode` 当前取 full、recent-5、recent-10、message、compacted。historical_native、historical_selected、historical_adoption 仅说明已有来源事实，不开启新的调用路径。任意选择历史的 visible 模式、消息选择列表和对应页面入口已移除；“引用回复”仍是普通消息功能。

### 首次输入与失败恢复

- **首次输入从目标 trace 取得历史。** 读取停在本次新用户消息之前，先发送继承上下文，再发送本轮输入。首轮前的新排队消息不会误混入继承窗口。
- **Provider 已接受输入后，继续同一个会话。** Execution 的接收事实按目标 Agent 与当前会话查询；预先排队的后续任务在开始执行时重新判断，移除过时的首轮材料，防止重复发送。
- **创建和轨迹初始化可幂等重放。** 初始引用列表用数量与摘要记录在 forked 运行回执中；同一创建请求重放复用已完成事实。子节点已经工作后，重放不能覆盖它新增的内容。
- **登记时从来源初始化同样建立独立会话。** 来源只用于确定子 trace 的片段引用；需要预先创建会话的 Provider 使用 `create`。登记准备过程保存这次创建的固定请求与执行标识，连接中断后复用该请求，并在登记失败时完成会话和目录清理；不调用 Provider 原生 Fork。
- **Fork 会话准备使用子 Resident 的 Execution 身份。** 由 Resident 工作状态持有的准备过程，在预留 Execution、保存请求和调度接纳时使用同一个 execution_id 与子 Resident agent_id。没有模型输入的 `create` 操作也保留这个归属；会话 binding 仅用于定位 Provider 会话，不替代 Agent ID。身份不一致必须拒绝，不能通过放宽不可变溯源检查继续执行。
- **创建失败仍保存身份、trace 和失败原因。** 创建任务负责重试独立 Provider 会话，直接使用子 trace，无需来源 Session 继续存在。
- **全新上下文在新会话成功提升后生效。** AgentData 保存模型上下文清空操作及位置，历史聊天仍可查看。初始化失败或取消时不丢弃原上下文；没有会话且尚未工作时，仅修改账号配置保持子 trace 不变。
- **压缩继承完成前不投递首轮工作。** 只有支持 seeded_compaction 的后端可以创建这种 Fork；目前为 Codex。来源窗口为空时报错。

### 初始化状态与原队列恢复

`GetResidentInitialization(resident_id)` 读取初始化状态；`RetryResidentInitialization(resident_id)` 只恢复初始化，不建立新 Resident，不重发业务消息，不改变用户选定的上下文策略。读取要求目标 Resident 的 @viewer，恢复要求 @contributor。初始化尚未完成时，普通输入仍以原身份排队，不能覆盖创建工作；失败收尾与执行确认也不能因为已有 Session 而丢弃初始化工作。阻塞初始化期间的 Mailbox 读取包含已经接纳的原 pending 输入，保留作者、正文、媒体与顺序。

恢复前核对当前绑定、冻结子 trace 的 forked 回执和原执行事实。原执行仍活动、绑定已变化、冻结引用不完整或不匹配时拒绝开始重叠尝试。已有可证明完成的原压缩直接收敛；Worker 失租或 failed 状态本身不证明原生压缩副作用已结束。原结果不确定时，恢复在独立候选 Agent／Session 中导入原冻结引用并压缩，不继续修改旧 Session。只有候选压缩执行已完成且规范 Segment 与候选绑定一致，才在 AgentData 同一事务内比较原绑定、采纳压缩事实并提升新绑定，写入不可变提升凭据。失败或比较冲突保留旧绑定、原队列及审计事实。

候选创建、压缩与提升使用固定身份；丢回执后读取既有事实，不重复已接纳的原生操作。相同请求键重放原受理回执，当前状态另由 Get 读取；不同请求键在进行中的 attempt 上也复用同一初始化身份。明确失败或取消后才允许显式重试新候选。恢复不会改写旧失败为成功，也不证明旧原生进程已经退出。

初始化成功后仍遵守当前接收闸门。关闭接收时可以显式推进初始化，但业务保持排队；开放后按既有队列推进原消息。受理回执中的 pending／running 不表示完成，须读取 completed 后再确认业务已开始。这个流程不使用普通 RestartSession，也不回退为完整或空白上下文。

## 实现说明

### 创建与交付流程

```text
ForkResident → 校验来源、模式与权限
  → 在一次数据库读取快照内，从来源自己的 trace 选择有效段
  → 创建目标 Agent 与 trace，写入准确的 Segment 引用
  → 保存 Resident 身份、来源、执行配置和 Workspace 绑定
  → context_fork_create 创建独立 Provider 会话
  → 首轮读取目标 trace，按当前会话接收事实决定是否交付上下文
```

`AgentDataStore.agent_context_snapshot` 从目标 Agent 的 trace 读取，按压缩点、清空或上下文重启标记及所选消息限制窗口，倒序分批查到最近压缩位置后批量取正文。Fork 不以当前 Provider Session 的 Execution 覆盖范围裁剪 trace。读取在同一数据库快照中完成。`select_segments` 选择窗口内的连续片段引用，创建层直接传给 `fork_agent(segment_ids=...)`；`seed_messages` 只承担交付时的内容转换，不以正文可否转换决定子 trace 的成员。新会话使用自己的启动指令，旧会话启动指令仍不作为继承提示词交付。创建任务临时保存的来源与目标身份、Segment ID 列表只是这一操作的重放参数，不成为 Resident 当前状态的另一份内容来源。

Fork 前端没有历史选择表单；节点、分支表单和消息菜单调用同一个模式契约。普通 full 是默认方式，recent-5、recent-10、compacted 和 message 各有明确入口。图上的继承连线继续由派生事实表达，Portal Fork 不写 Provider 子 Agent 的 parent_agent_id。

### 把轨迹上下文交给新会话

第一次工作时，SocietyService 在执行请求中附上 `fork_context_input`（格式 `tfi.provider-context-seed.v1`），其中包含片段引用、带来源材料的提示词和纯文本形式的提示词。ExecutionService 的各 Provider 适配器按自己的能力交付：

- **Codex**：把片段还原为有序、带类型的 Responses 条目。输入和回答保留角色；工具调用与结果保留调用 ID、参数、结果结构，工具名和命名空间分开传递；图片保持图片条目；压缩片段展开为它保存的 `replacement_history`，其中的图片和加密压缩条目保持原值。适配器在新线程尚未开始任何一轮时调用 `thread/inject_items` 导入历史，再用 `turn/start` 发送本轮新消息。
- **Claude、Kimi 等**：把同一份材料写成纯文本对话记录，放在第一条新消息之前发送。
- **Model API**：创建时保存独立的 trace 和调用配置，无需创建原生模型会话；无状态调用将该 trace 的纯文本上下文放入请求，同一次请求不再叠加一份会话历史。

**纯文本形式只带文字，并且放得进新会话。** 写成纯文本时，历史中的 base64 图片数据（用户发来的截图、工具读到的图片、MCP 返回的图片）换成一行说明，例如“[image/png，约 146 KB，继承的历史只带文字，此处省略]”；读图的工具调用仍保留文件路径，新 Resident 需要时可以重新读取。去掉图片后，如果历史仍超过新会话窗口的 60%，就按同一轮次边界从最早的轮次开始整轮略去；背景消息与承载轮次的回答、工具内容一同保留或略去，压缩摘要始终保留，并在历史开头注明“更早的 N 条历史超出新会话可用的上下文，没有随继承历史提供”。窗口取新 Resident 自己记录的模型窗口，没有时取来源的，都没有时按 200k 计算；token 按非 ASCII 字符 1.25、其他字符 0.42 估算，系数来自 Claude 会话的实际用量。代码在 `resident_fork_context.without_inline_media` 和 `fit_to_window`，由 `resident_directory_domain._resident_trace_context_text` 调用。

图片在最初输入时已随实际 Provider 输入存入 PostgreSQL，Fork 读取保存的图片内容或地址，不再读取原上传路径。Codex 导入在 Provider 线程锁内按账号路线和线程保存导入意图、窗口摘要和确认回执：已有相同窗口的回执时复用；窗口不同则拒绝；有意图而没有确认时报告结果不确定并保留证据，不盲目重复导入。导入回执属于审计记录，不进入模型窗口，也不代替第一条新消息的接收事实。`actual_sent_prompt` 记录本轮文本，完整输入证据由它、来源片段引用、类型化内容和导入回执共同组成。Codex 导入时目标线程放不下整个窗口，发送层报告容量错误，不悄悄丢弃历史；纯文本交付按上一段的规则截取并注明。

### 压缩继承

压缩继承的新 Resident 带有 `context_compaction_policy=before_first_turn`。ExecutionService 在第一轮工作之前执行一次 `compact` 操作：先用 `thread/inject_items` 导入冻结的窗口，再调用 Codex 原生压缩。已知内容继续由目标 trace 引用，会话记录从 trace 投影，导入回执作为运行记录保留；压缩结果成为新会话的有效窗口。压缩成功前不投递第一轮工作；失败或结果不确定时保持创建失败状态，不会改用完整窗口继续运行。来源和其他分支的会话、窗口和轨迹都不变。

### 状态归属

SocietyService 拥有 Resident 身份、当前归属、派生来源、Workspace 绑定及执行配置。AgentData 拥有不可变内容、trace、会话边界和运行证据；Chat service 从 trace 投影页面对话。ExecutionService 创建、绑定并驱动 Provider 会话。


### 当前执行设置的唯一 owner

注册完成的 Resident、配置弹窗、Fork 继承、Conversation 快照和执行请求统一通过 `read_resident_execution_settings` 读取当前数据库配置。更新由 `write_resident_execution_settings` 校验并写入同一 owner，Conversation 只发布带时间的设置变化回执，不维护可独立修改的当前设置 JSON。

`execution_settings` 保存模型、推理强度、服务等级、协作模式、权限/沙箱/审批、初始指令和子 Agent 等模型调用策略；`harness_config` 保存 mode 与已注册 feature 的有限结构参数。API 的 `runtime_settings` 由两者组合生成读投影，未知字段、未知功能或非法参数一律拒绝。Harness 参数不能隐含覆盖 Resident 的当前模型或账号。

未登记的创建候选可以暂存这两部分，准备完成后随 Resident 提交；执行请求、重启候选和历史变化事件可以保存明确归属于该次操作的快照。快照不成为另一份可写当前设置。账号选择与目录同步使用已经过权限判断的候选设置，不在缺少 actor 时重新推导权限或改变已准备的配置。


### Fork 与并发成员删除

Fork 的上下文和 Provider 准备不持有 Lineage 数据库行锁。准备前的图校验与准备后的节点、继承边提交各使用一个短事务，读取并锁定当前 Lineage 定义。提交以当时仍有效的成员集合为基础；期间删除无关成员不阻挡 Fork，也不会把已删除成员重新加入图。若来源成员在准备期间被删除，提交返回冲突，并清理尚未完成的子 Resident。

## 创建分支的完成边界

Fork 成功响应必须在新 Resident 与继承边提交、Resource 身份和创建者凭证发布确认后返回。前端随后在当前页面选中新节点并直接打开它的 Chat，无需重新打开。发布暂时失败时保留已提交的分支，通过同一指令及幂等键补做投影；不得重复创建分支，确认期间不持有图或 Resident 状态锁。

格式名与版本须精确匹配当前规范值，校验规则见 decision_86aed2f0e8c7；本篇同时校验各字段和结构版本，不接受历史前缀或其他版本。
