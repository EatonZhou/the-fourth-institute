---
kb_id: decision_8fe87d812a4c
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
importance: 7
metaness: 2
ai_generated: false
links:
  source:
  - ${code_root}/management_portal/society_service/resident_context/service.py
  - ${code_root}/management_portal/society_service/resident_context/store.py
  - ${code_root}/management_portal/society_service/resident_context/evidence.py
  - ${code_root}/management_portal/society_service/resident_context/worker.py
  - ${code_root}/management_portal/society_service/resident_context/embedder.py
  - ${code_root}/management_portal/society_service/isa/residents.py
  - ${share_root}/Agents/lineage_ed619225d417/residents/resident_fbaf22f40dc6/workspace/duties/resident-summary.md
  - ${code_root}/management_portal/static/home.js
  - ${code_root}/management_portal/resident_recommendation_backend.py
  - ${code_root}/management_portal/resident_discovery_client.py
  - ${code_root}/management_portal/static/portal_resident_discovery_service.js
  - ${code_root}/management_portal/static/lineage_workbench.js
  - ${code_root}/tests/cases/test_resident_recommendation_backend.py
  - ${code_root}/tests/cases/test_resident_discovery_internal_contract.py
  - ${code_root}/tests/cases/test_resident_discovery_portal.py
  - ${code_root}/tests/cases/test_resident_context.py
  related:
  - decision_f560803947c6
  - decision_725dedbe89b2
  - decision_6591e7793116
  - decision_0f57656a2acd
  - decision_34158fdd4078
  - decision_b1ab45c4b3ec
  - decision_5bf5bfbd52cb
  - decision_3e07c6d35cac
  - decision_d19b9909a674
  - decision_44cf587c1721
  - decision_bd5217d08c09
  supersedes: []
  derived_from: []
---
# Which Resident 的候选发现、推荐、输入预算与纠正

Portal 中有大量长期存在的 Resident，每个都带着自己的对话历史和工作上下文。用户有新问题时，常常不知道该找哪一个。**Which Resident** 帮用户在自己能看到的现有 Resident 中挑选协作者：用户用一句话描述任务，Portal 先找出一组候选，再交给一个专门负责推荐的 Resident 判断应该直接打开哪一个（Open），或者从哪一个 Fork 出新的 Resident 来隔离这项任务（Fork）。推荐不准时，用户可以指出正确答案，这条纠正进入路由评测集，用来改进推荐。

推荐只在用户已经能看到的 Resident 中进行，不扩大任何可见范围；Open 和 Fork 由用户点击后通过正式入口执行，推荐本身不创建 Resident。

候选检索和摘要写作是全站共享的能力。用户的个人助手（私人 Family，decision_44cf587c1721）也用它们找人：负责对话的成员结合当前对话和个人偏好挑选候选，在对话中给出交接卡片，见下文“个人助手中的交接卡片”。推荐 Resident 继续服务 Which Resident 入口。

## 基本概念

- **推荐 Resident**：负责分析查询、给出推荐的普通 Resident。它由应用配置 `application_targets` 中 `resident_discovery` 一行指定（decision_6591e7793116），用户在导航中看到的 Which Resident 卡片就是它。它使用自己的运行设置（模型、推理强度、账号），推荐流程不另外覆盖。
- **Resident 发现（Discovery）**：SocietyService 的 `resident_context` 模块，数据存于 PostgreSQL 的同名 schema。它为 Resident 保存候选摘要，按调用者权限搜索，并保存路由评测集。向量属于可选索引；默认关闭语义嵌入时，发现服务仍提供词法检索。它自己不调用模型。
- **候选摘要**：直接使用 Resident 的累计摘要，包括 current_focus、branch_delta、active_threads、durable_expertise、recent_results、primary_ownership、supporting_expertise、route_when、avoid_when 九个语义字段。GetResidentSummaries 读取这份内容，带 include_maintenance 时另给出内容版本；Family 成员本人维护，门面由本 Family Steward 汇总，独立 Resident 由 lineage.resident_summary_configuration 指定的全局摘要职责根据已完成对话维护。不把成员或门面的内容再交全局写作者改写。
- **候选快照**：一次推荐开始时固定下来的一组候选身份、显示名称与简短职责说明，用于结果校验和刷新恢复。推荐 Resident 的提示词另带有预算的职责摘要，模型只能从本次候选 ID 中选择。
- **Open 建议**：直接打开现有 Resident，在它当前的对话和上下文中继续。
- **Fork 建议**：以某个候选为来源 Fork 出新 Resident，继承它有价值的上下文，同时让新任务有独立的身份和工作分支。
- **Ground Truth 与路由评测集**：用户纠正时提交的正确 Resident 和正确动作称为 Ground Truth；发现模块把它和本次查询、推荐结果一起存入路由评测集。

## 用户看到什么

### 入口

- **Lineages 与 My Favorite 两个导航面板顶部都有 Which Resident 卡片，打开推荐 Resident 的对话。** 卡片位于个人 AMA 入口之后、个人助手入口之前，使用推荐 Resident 的真实 ID，菜单与 AMA 卡片相同（decision_3e07c6d35cac）。系统从应用配置 `resident_discovery` 读取推荐 Resident；读取导航不会创建任何 Resident。导航数据首次读到之前，卡片显示“正在读取 推荐服务…”，读取失败时显示失败原因；配置没有登记或服务故障时显示“推荐服务暂不可用”；当前用户无权访问推荐 Resident 时，卡片显示锁和“没有访问权限”。重复点击卡片保留输入框中的草稿。
- **Home 的 Agent Access 面板中也有 “Which resident?” 链接。** 链接在拿到推荐 Resident 的 ID 后，用标准的 `resident` 与 `lineage` 页面参数打开同一个对话，并把焦点放到输入框；没有取得绑定时显示“当前暂不可用”。

### 在对话中提问

推荐对话复用 Portal 的共享 Chat Workspace，通过其 `conversationView` 扩展点换成推荐视图：Resident 目标、菜单、状态订阅和权限都与普通对话相同，消息区显示的是当前用户最近一次推荐请求的查询与结果。

- **用户在普通输入框中描述任务，最多 4000 字符，后端同样检查这一上限。** 超长查询在候选搜索前拒绝，并保留原文供用户修改。 发送快捷键与普通聊天相同；这个视图中附件和资源引用入口关闭。发送被拒绝时草稿保留。
- **一次推荐完成前不能再次发送。** 等待期间输入区显示“正在寻找合适的 Resident，请等待本次推荐完成。”，这条状态由推荐视图给出，不会显示成权限或接单问题。
- **查询和结果以消息形式出现在消息区。** 请求 ID、消息 ID 和状态收在可展开的“请求详情”中。同一请求更新状态或出现结果时，当前面板保持用户选择的展开或收起状态；新请求重新使用默认的收起状态。展开选择只保存在当前页面内存中。加载中、没有足够匹配、失败和目录检索回退都在消息区给出反馈。这些消息是推荐请求的展示，不会写回推荐 Resident 的对话记录。
- **刷新页面后恢复当前用户最近一次请求。** 草稿和已提交的查询分开保存；纠正始终针对已提交的那次查询。

### 结果卡片

- **每个推荐的 Resident 显示为一张卡片。** 卡片包含 Resident 名称、所属 Lineage 名称或 ID、推荐理由和可信度，并同时提供“打开”和“Fork”两个按钮；模型建议的那个按钮带“（推荐）”字样和强调样式，用户仍然可以选另一个。
- **模型单独建议 Fork 时，另显示一张 Fork 建议卡片。** 卡片说明来源 Resident、来源 Lineage、理由和建议的新名称，并提供“Fork（推荐）”按钮。
- **“打开”直接进入该 Resident 的对话；“Fork”打开正式的 Fork 表单。** 用户可以在 Fork 表单中检查名称和上下文方式后再创建（decision_5bf5bfbd52cb）。候选在推荐之后被删除或权限变化时，打开和 Fork 的正式入口会重新检查权限并报告失败。
- **推荐 Resident 不能接单时，结果来自目录检索。** 消息区以“目录检索回退”标明，说明这些结果按目录匹配给出，没有经过模型分析；刷新后仍保持这一标记。
- **按钮使用统一的紧凑样式。** 查询提交、结果动作、Fork 建议和纠正入口的按钮文字与正文同一大小；推荐状态用颜色、边框和背景区分，较长的动作名称在窄窗口中自然换行。

### 纠正

- **有推荐结果时，消息区出现“纠正并加入评测集”按钮。** 展开后的表单包含：
  - Resident 搜索框（最多 300 字符，按名称、Lineage 或 Resident ID 查找，每次最多返回 30 条）；
  - 搜索结果列表和已选卡片（显示名称和完整 Resident ID）；
  - 动作选择：“打开现有 Resident”（`open`）或“Fork 到 HATG”（`fork`）；
  - 可选理由（最多 1200 字符）；
  - “提交并加入评测集”按钮，提交中显示“提交中…”并禁用。
- **提交结果区分两种情况。** 评测集中还没有相同 Ground Truth 时提示“已加入路由评测集”；已经存在时提示“评测集已有相同 Ground Truth”，只记录本次纠正。
- **推荐系统不会把用户的点击当作 Ground Truth。** 只有用户主动提交纠正才会写入评测集。

### 已读确认

推荐视图复用共享 Chat 的可见性观察器与 Conversation Store 提交已读。只有当推荐结果或失败终态已经加载、卡片在前台可见，并且当前用户、Resident、展示的输入消息和已完成的索引位置都一致时，才把这条推荐标为已读。后台加载、目录检索回退、尚未展示的新请求和读取失败都不清除未读提示。

## 设计点

### 候选怎样产生

- **候选按当前用户的权限搜索。** Portal 以当前登录用户身份请求发现模块；调用者可以用 `resident_ids` 缩小范围，但不能把自己看不到的 Resident 加入候选。
- **私人 Family 的角色不成为候选。** 个人助手的门面、Router、steward 和成员在默认检索和用 `resident_ids` 限定的检索中都被排除，所有者本人检索也一样；默认检索另外只保留普通 Family 的门面，排除其内部角色和成员。
- **候选数量有上下限。** 一次推荐最多返回 `limit` 个 Resident（默认 5，范围 1–8）。用户在查询中明确要求推荐一位或其他数量时，后端将该数量与接口上限取较小值，作为本次结果上限；为此发现模块先取 `max(24, limit × 8)` 个候选，最多 48 个。
- **交给推荐 Resident 的摘要先按字段上限投影，再按提示词总字节预算压缩。**

  | 内容 | 上限 |
  | --- | --- |
  | Resident 名称、Lineage 名称 | 各 160 字符 |
  | 当前焦点 | 500 字符 |
  | 分支差异 | 350 字符 |
  | 长期专长、主要职责、辅助专长、适合与不适合的情形 | 每类最多 5 项，每项 180 字符 |

  推荐 Resident 只看到这些摘要，不读取候选的完整对话，也不能自己再查询其他 Resident。

### 推荐请求的输入预算

推荐后端拥有一次查询的资料选择和预算。共享 Invocation 与 Effect 继续执行其通用校验，不为推荐请求放宽上限。

- **模型提示词与结果校验快照使用不同投影。** 提示词包含本次查询、全部候选及其职责摘要；结构化上下文中的候选只保留 Resident ID、Lineage ID、两者名称和最多 100 字符的当前焦点。后者供结果卡片的备用说明使用，职责列表不在结构化上下文中重复存储。
- **结构化参数预算为 56,000 字节。** 推荐后端按最终的 `content_parts` 包装计算，计入查询、结果数量、请求 ID、候选快照、工作目录和 JSON 结构，给共享层 64,000 字节上限留出余量。计算使用 `ensure_ascii=False`、紧凑分隔符与 UTF-8 编码，包含中文、emoji 和 JSON 转义开销。
- **提示词预算为 128,000 UTF-8 字节。** 计算包含固定指令、完整查询和候选 JSON；同步、异步推荐共用同一组装规则。此预算独立于结构化参数预算，也不表示 Provider 的 token 上限。
- **预算压缩保留所有候选 ID、Lineage ID 和原始排序。** 字段投影能整体装下时保留全部内容；超出预算时，将剩余候选空间均分，优先缩短辅助专长、长期专长和排除条件，再处理适用条件、分支差异与当前焦点，尽量保留主要职责。名称仅在仍然超限时缩短，按 Unicode 字符边界截取。查询保持原文，数据库中的完整摘要和证据不改写。
- **无法装下的请求在投递前终止。** 异步接口返回 `status=failed`、`error_code=recommendation.input_too_large`、`retryable=false`，同时返回超限部分、实际字节数与预算；最近请求记录保存这些诊断，刷新后继续显示。同步接口抛出对应的输入错误，不以目录结果掩盖它。系统不通过重试同一超限请求解决大小问题。

结果校验继续以原输入消息中的固定快照为准；刷新、读取回复和 Fork 建议不会重新搜索并换一批候选。历史输入中已有的完整候选快照也遵循相同的字段读取方式，无需重建摘要或迁移旧消息。

### 推荐怎样判断

- **推荐 Resident 按以下要求作答：** 用户明确指定数量时遵守本次上限；没有指定时尽量给出 3–5 个相关 Resident 的排序清单。不用无关 Resident 凑数；区分“要找的目标”和“提到的证据来源”；主要职责比话题相近更重要，辅助专长不等于职责；上下文合适时选 `open`，适合作为起点但任务是独立分支时选 `fork`；每个推荐单独给出可信度 `high`、`medium` 或 `low`；没有合适候选时返回空列表并在 summary 中说明原因。
- **回复必须是一个 JSON 对象。** 回复可以是纯 JSON，也可以放在 Markdown 代码块中。系统把它整理成 `tfi.resident-discovery.recommendation.v2` 格式，字段为 `summary`、`recommendations`、`fork_advice`，以及标明来源的 `fallback`、`fallback_reason` 和 `recommendation_source`。
- **整理规则保证结果只指向候选中的 Resident：**
  - 推荐项的 Resident ID 必须在候选快照中，重复的只保留第一项，项数不超过 `limit`；
  - 理由最多 800 字符，summary 最多 1000 字符；
  - 可信度不是 `high`、`medium`、`low` 时记为 `low`；动作不是 `open`、`fork` 时记为 `open`；
  - `fork_advice` 只有在 `recommended` 为 `true` 且来源 Resident 在候选中时才有效，理由最多 800 字符，建议名称最多 200 字符。
- **明确返回空列表是正常结果。** 系统保留 summary，页面显示“当前可访问摘要中没有足够匹配的 Resident。”
- **格式错误的回复让本次推荐失败。** 回复不是 JSON 对象、`recommendations` 不是数组，或非空列表中没有一项在候选中时，本次请求以错误结束，错误码 `recommendation.invalid_result`，不可重试；用户需要重新提交查询。系统不会拿候选第一项顶替，也不会把编造的 ID 当作“无匹配”。
- **页面只根据整理后的结果生成按钮。** 模型回复中的 URL、权限说法或候选外的 ID 都不会成为导航目标。

### 三种结果来源

`recommendation_source` 标明结果从哪里来：

| 取值 | 何时出现 | 说明 |
| --- | --- | --- |
| `resident-analysis` | 推荐 Resident 正常作答 | 模型分析结果 |
| `candidate-search` | 当前用户可见范围内没有候选 | 直接返回空结果，不调用模型 |
| `directory-search` | 推荐 Resident 的接单开关关闭，或其流程 Event / Effect 关闭或连接未就绪 | 按目录匹配返回前 `limit` 个候选，可信度都为 `low`、动作都为 `open`；`fallback=true`，`fallback_reason=recommendation-capability-intake-closed` |

## 实现说明

### 一次推荐怎样完成

```text
用户在推荐视图提交查询
        │ POST /api/agent-workbench/resident-discovery/recommend
        ▼
推荐后端（resident_recommendation_backend.py）
  ├─ 分配 request_id，向发现模块取候选
  ├─ 没有候选 ───────────────> 完成：candidate-search 空结果
  ├─ 读取应用配置 resident_discovery 的 Resident；接单已关闭 ─> 完成：directory-search 回退
  └─ TriggerEvent → Effect（DeliverMessage）→ 推荐 Resident，携带查询与候选快照
        │ 立即返回 accepted 与 resident_id、lineage_id、message_id
        ▼
页面订阅这条消息的输入状态，完成后读取结果
        │ POST .../recommend/result
        ▼
推荐后端读取回复 → 整理并校验 → 结果卡片
```

- **开始推荐。** 后端为每次提交分配 `request_id`（形如 `resident-recommendation-<24位十六进制>`），然后按上面的分支处理。投递给推荐 Resident 的消息包含提示词和一个 `resident_routing` 上下文部件（查询、`limit`、`request_id` 与候选快照），并带上业务引用 `business_ref` 和元数据 `conversation_index=false`，因此这些消息不会出现在普通会话列表中。消息投递使用共享的 Resident Invocation 适配层的 workflow_binding 路径（decision_725dedbe89b2），实际经 TriggerEvent → Effect → DeliverMessage，不直接绕图投递。
- **保存最近请求。** 每次开始推荐后，后端把结果写入当前用户私有的页面状态（命名空间 `resident-recommendation`，键 `latest`）：已完成的请求保存完整结果（包括回退标记），已受理的请求保存 `resident_id`、`lineage_id`、`message_id`，输入预算失败保存错误码、超限部分、字节数和预算。
- **恢复最近请求。** `recommend/latest` 读取这份记录：已完成的直接返回结果；已受理的返回消息身份，页面继续等待；没有记录时返回 `status=none`，不去猜测更早的结果。页面刷新优先恢复同一 `request_id` 的最新终态，同步完成的结果不会再启动等待。
- **读取结果。** `recommend/result` 只读取指定消息：
  - 消息还在排队或执行时返回当前状态；此时推荐 Resident 的接单已关闭，则返回失败“Resident discovery capability is not accepting work”；
  - 消息已完成但回复还不可读时，仍返回 `running`，页面继续等待；
  - 消息中找不到 `resident_routing` 上下文时返回失败，错误码 `recommendation.context_missing`，不可重试；
  - 回复可读时整理并校验，成功返回 `completed` 与结果。
- **等待方式。** 页面通过 Resident 输入状态事件观察这条消息；连接断开后从持久的输入状态恢复。状态观察失败时重建观察器，结果读取失败时直接重新读取，两者互不替代；页面不使用定时器猜测完成。
- **同步接口。** 后端还提供同步的 `recommend_residents`，在一次调用中完成投递和等待。只有调用方显式传入 `allow_fallback=true` 时，普通模型调用或结果失败才改用目录检索结果。接单、Event 或 Effect 关闭时始终拒绝请求；流程等待投递或终态失败时也保留流程错误，不进入此回退。推荐页面不使用这条同步路径。

### Portal System 中的推荐流程

“Which Resident 推荐”Function Block 包含请求 Event、派发 Effect 和推荐 Resident。应用按当前承担者所在 Lineage 查询 `system:resident_discovery:event`、`system:resident_discovery:effect` 的 client_key，核验 Event 经唯一 route 连接到 Effect、Effect 的 target 端点连接到当前承担者。应用目标只指定承担者；开关以 GetLineage 的 Event / Effect runtime 投影为准，不复制到 ApplicationTarget。组件改标题不影响绑定，改承担者须同步修复图中连线。

关闭 Event 或 Effect 后不启动推荐模型，页面按现有目录检索回退协议返回当前用户可见的候选，并标明没有模型分析。候选检索、触发和后续观察始终保留当前网页调用者身份，Effect 继续由内核检查真实发起者权限；不提升为 System Lineage 的运行身份。关闭功能不会取消已受理的 Resident 输入或隐藏已有结果。

应用请求保存 Event ID 和 Trigger Path ID。若触发过程中 Effect 被关闭，且 Event 已被接收但尚无消息回执，共享适配层返回 WorkflowPending；后端保存原查询、request_id 及流程引用，向页面说明等待投递。刷新和再次提交先观察这一条流程，取得原 message_id 后继续现有输入状态与回包协议，不重复生成 Event。流程终态失败时报告路径与失败；候选快照、结果校验和当前用户的 latest 隔离规则保持一致。

### HTTP 接口

推荐页面属于 Agent Workbench，接口前缀是 `/api/agent-workbench/resident-discovery`，全部使用 POST。成功时返回 HTTP 200；未登录、参数错误、服务不可用和服务异常由工作台统一映射为对应的错误状态。

| 路径 | 请求字段 | 作用 |
| --- | --- | --- |
| `/recommend` | `query`、可选 `limit`、可选 `resident_ids` | 开始一次推荐 |
| `/recommend/latest` | 无 | 恢复当前用户最近一次请求 |
| `/recommend/result` | `resident_id`、`message_id` | 读取一次推荐的结果 |
| `/search` | `query`、可选 `limit`、可选 `resident_ids` | 在当前用户可见范围内搜索 Resident |
| `/contexts` | `resident_ids` | 读取指定 Resident 的候选摘要（Home 的 Agent Access 面板也用它显示摘要） |
| `/routing-evals/residents/search` | `query`、可选 `limit` | 为纠正表单搜索 Resident |
| `/routing-evals/submit` | `query`、`predicted`、`ground_truth_resident_id`、`ground_truth_action`、`rationale` | 提交 Ground Truth 和本次推荐结果 |

应用配置只保存推荐 Resident 的稳定 ID。读取绑定时通过 `lineage_nodes` 取得它当前所属的 Lineage；Resident 转移后立即沿用新关系，不在 Resident 记录或应用配置中复制 Lineage ID。Resident、成员关系或 Lineage 已删除时，入口按不可用处理。读取这一配置不初始化或迁移整个 Lineage schema。

### 与发现模块通信

Portal 通过 `ResidentDiscoveryClient` 调用 SocietyService 的 `resident` 领域：`search_residents`、`resident_routing_candidates`、`search_resident_directory`、`get_resident_contexts`、`request_resident_summary_rebuild` 与 `submit_routing_eval_correction`，每次携带调用者身份并按可见范围作答。搜索在 SocietyService 主进程执行；模型仅在管理员开启可选嵌入且依赖齐备时按需加载。SocietyService 不可用或繁忙时客户端抛出 `ResidentDiscoveryUnavailable`，页面保留查询并显示原因。

搜索默认使用词法排序，不要求本机安装 `numpy`、`onnxruntime`、`tokenizers` 或模型。管理员可在 Portal Management 中开启语义嵌入；依赖或模型缺失时，开关保持关闭并提示安装，确认后由独立安装任务安装包与模型，成功后自动开启，失败则保留关闭与错误状态。开启后词法与向量结果融合；向量模型是 `paraphrase-multilingual-MiniLM-L12-v2` 的量化 ONNX 版本，平均池化并做 L2 归一化。模型不可用时查询退回词法结果，摘要变化后向量可重建。

### 摘要怎样写成

默认候选只包含独立 Resident 和最外层 Family 门面；Family 成员、Router、Steward 与内层门面不进入外部默认候选。目录保留内部记录，供授权的显式摘要读取使用。候选可见性与全局写作责任分开：所有 Family 成员和门面都不进入全局摘要队列。归属在认领、证据读取、提交及重建请求时重新核对，旧全局认领不能覆盖已归 Family 管理的摘要。

Family 成员用 GetResidentSummaries（include_maintenance）读取版本与漏写轮次、用 UpdateResidentWorkSummary 自写累计摘要；Steward 按需写门面摘要并保存成员内容、名单和职责的 source_versions。内容统一保存在 work_summaries，内容版本与输入覆盖记录分开；省略 summary 的无新增确认不增加版本或改变作者。GetResidentSummaries、搜索及路由直接使用该内容，不再按内部/外部选择两份摘要。全局摘要职责只为独立 Resident 运行下述证据与认领流程；它的模型设置不变。Family 工作流见 decision_44cf587c1721。


独立 Resident 的全局摘要维护分两步完成，机械部分和写作部分由不同的执行者负责。

1. **SocietyService 准备证据。** 发现模块的后台工作每 5 分钟同步一次 Resident 目录，并每 30 秒轮流检查 8 个 Resident 的对话；发现有了新的已完成轮次，2 分钟后读出它的对话，整理并脱敏成证据，把这份摘要标为“可认领”。没有新的已完成轮次的变化只更新记录，不进入写作。承担者执行这项职责的轮次就是职责本身，不算它要被摘要的工作：交给它这一轮的消息带 `business_ref` = `duty/resident-summary`，这一轮的输入和回复既不算它的对话变化，也不进入它自己摘要的证据，所以写摘要不会让承担者的摘要排队重写。增量更新累计 20 次时，或上次完整重写超过 30 天且其间有过增量更新时，下一份改为完整重写；没有新内容的 Resident 不会被重写。Dev 与 Release 两个 SocietyService 共用这份数据，靠存放在数据中的租约保证同一时刻只有一个在做这些工作，另一个在它停止后接手。
2. **`resident-summary` 的承担者写作。** 承担者是一个普通 Resident（由应用配置 `resident_summary` 指定）。它所在 Lineage 中的 `time.reached` 事件源每 10 分钟触发一次，另有一个手动事件源可以立即触发一轮；两者都先进同一个 Effect：脚本用 `GetResidentSummaryQueue` 查到有可认领的摘要、且 `ListResidentStates` 显示承担者空闲时，才用 `ClearContext` 清空它的上下文，再用 `DeliverMessage` 把这一轮交给它（带上面的 `business_ref`）；队列为空或承担者正忙时跳过这次触发，不产生模型调用。交给它的任务指向它工作区中的职责说明 `duties/resident-summary.md`。它按说明用 `ClaimResidentSummaryJobs` 认领并取得写作说明与证据、`SubmitResidentSummary` 提交或 `ReleaseResidentSummaryJob` 说明原因放弃，直到队列写空（指令定义见 decision_d19b9909a674）。它只依据证据写作，不读取额外材料，也不写运行日志。

摘要不写测试或检查的数量，例如“28 项测试通过”“97 passed、3 skipped”。这类数字对寻找合适 Resident 的人没有帮助，摘要应写改了什么、交付了什么；实验与评测指标（成功率、延迟、样本数等）照常保留。写作说明中写明这一条，`SubmitResidentSummary` 还会检查提交内容：出现“数字 + 项/个 + 测试/用例/检查 + 通过/失败”或“数字 passed/failed/skipped”这类片段时拒绝提交，并在错误中列出这些片段，承担者删去后用同一份认领重新提交。

目录只收录能接收工作的 Resident：已删除 Resident 不出现在搜索和推荐候选中，也不再更新摘要；删除后不提供恢复入口。从目录消失的 Resident 立即标为不活跃，退出搜索和推荐。每次目录同步时，发现模块再向 SocietyService 查询这些不活跃 Resident 是否已有删除记录（内部服务方法 `list_removed_discovery_residents`）：Resident 本身已删除、所在 Lineage 已删除，或者已没有它的记录，就在这次同步中删除它的摘要和向量，Portal 不保留存档。没有删除记录的 Resident 只是因为所在 Lineage 这一次读取失败才没出现在目录里，它的摘要和向量一直保留，Lineage 恢复可读后它重新出现，摘要可以继续使用。

`resident_context.residents` 只投影摘要检索需要的身份、标题、Lineage 名称/简介、项目、状态及调度字段；元数据指纹由这组明确字段计算。目录不保存重复的 `metadata_json`，也不保留已退役的 `goal`、`deliverable`、`origin_seed_id`。摘要、向量、证据与认领任务继续由各自的表保存，目录精简不清除已有摘要。

每份全局认领有 30 分钟租约，到期未提交的退回队列。放弃或失败稍后重试，连续 6 次失败后搁置，直到该 Resident 的对话再次变化。认领记录其依据的摘要内容版本；提交时若该版本已变，拒绝旧结果并重新排队。写作运行的 Provider、模型、推理强度和账号等证据元数据保存在 snapshots，累计内容及实际作者保存在 work_summaries；换写作模型仍通过承担者运行设置或更换承担者完成。

### 权限

- 候选搜索、摘要读取、打开、Fork 和纠正时的 Resident 搜索，都按当前用户分别检查权限。
- 推荐 Resident 只看到本次候选快照，用户对候选的可见性不会让推荐 Resident 获得更大的目录权限。

## 个人助手中的交接卡片

个人助手的成员需要外部协作者时，在用户发起的回合中以发起者权限调用 `SearchResidents`，读取候选的共享摘要，并参考个人偏好 `family_preferences_<门面ID>`。用户说过“这类事我习惯问 X”时，同类任务把 X 排在前面。选择由成员在对话中完成，不经过推荐 Resident；行为规则由 Portal 每轮注入成员（decision_44cf587c1721）。

**卡片协议。** 成员在回复中输出 `portal-resident-handoff` 代码围栏，内容是一个 JSON 对象或对象数组，每项字段为 `resident_id`、`lineage_id`、`title`、`lineage_title`、`reason`、`recommended_action`、`ask_message`。一次性问题的 `recommended_action` 为 `ask`，需要持续合作的为 `open`。`ask_message` 是将发给对方的完整任务描述和已经展示给用户的材料。

**渲染。** 卡片由共享 Chat 的消息渲染层显示，普通对话和 Chatroom 使用同一组动作。渲染时校验 `resident_…`、`lineage_…` ID 格式，链接只用校验过的 ID 构造，忽略模型给出的 URL，文本全部转义。每张卡片显示名称、所属 Lineage 和理由，提供两个动作，推荐的那个带“（推荐）”字样和强调样式，与结果卡片一致：

- **打开对话**：进入该 Resident 的对话页面（`/lineage?resident=…&lineage=…`），适合持续合作。
- **替我问一下**：只在有 `ask_message` 时显示。点击后在当前对话中发送一条普通用户消息，固定格式为“替我问一下 <title>（<resident_id>）：<ask_message>”，不附带当前草稿、附件或引用，草稿保留。成员收到这条明确授权后才以门面署名用 `DeliverMessage` 投递，对方的回复回到个人对话并标明来源。

**纠正分流。** 纠正某个 Resident 能力描述的，沿用上文纠正流程进入共享摘要和路由评测集；属于个人习惯的，只写入个人偏好，不进入评测集。

## 相邻 Design

- Which Resident 在五层组织与导航中的位置：decision_3e07c6d35cac。
- 推荐与摘要承担者分别登记在应用配置表 `application_targets` 的 `resident_discovery`、`resident_summary` 两行中。
- 投递消息与等待回复的共享适配层：decision_725dedbe89b2；可追踪的 Event → Effect 派发：decision_4adcc1ff2f2c。
- Fork 的上下文方式与来源证明：decision_5bf5bfbd52cb。
- 共享 Chat Workspace：decision_f560803947c6。

### 共享摘要的职责边界

Which Resident 使用 Resident 的累计摘要作为候选资料；内容存储、历史迁移和版本防覆盖规则见 decision_d19b9909a674。本篇继续负责候选可见范围、推荐、纠正和输入预算，不另维护一份发现画像正文。

格式名与版本须精确匹配当前规范值，校验规则见 decision_86aed2f0e8c7；本篇同时校验各字段和结构版本，不接受历史前缀或其他版本。
