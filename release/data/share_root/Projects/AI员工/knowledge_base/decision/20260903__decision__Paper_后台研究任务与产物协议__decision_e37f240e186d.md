---
kb_id: decision_e37f240e186d
type: decision
project: AI员工
created_at: '2026-09-03'
version:
  number: 35
  created_at: '2026-09-03'
  created_by:
    user: ''
    agent: ''
  updated_at: '2026-10-19T19:42:46Z'
  updated_by:
    user: tfi-exported-user
    agent: resident_865c3c24f251
importance: 9
metaness: 2
ai_generated: true
links:
  source:
  - ${code_root}/management_portal/shared_search_adapter.py
  - ${code_root}/management_portal/paper_backend.py
  - ${code_root}/management_portal/paper_state_store.py
  - ${code_root}/management_portal/paper_projection.py
  - ${code_root}/management_portal/serve_management_portal.py
  - ${code_root}/tests/cases/test_paper_review_execution.py
  - ${code_root}/tests/cases/test_paper_deep_read_execution_boundary.py
  - ${code_root}/tests/cases/test_paper_ccf_enrichment_execution.py
  related:
  - decision_1a2730dcd3d1
  - decision_f67ae5e70793
  - decision_eb066fbea4ed
  - decision_528bf9985705
  - decision_6ad0384e020d
  - decision_3ece26686464
  - decision_f560803947c6
  - decision_4adcc1ff2f2c
  - decision_898b8911a2ff
  supersedes: []
  derived_from: []
---
# Paper 后台研究任务与产物协议

Paper 是 Portal 中一篇有稳定身份的逻辑论文。Paper 页面提供一组读取论文并产生研究结果的动作：短动作可以在一次请求内返回，耗时动作会等待模型、Resident 或外部进程。本文说明每种动作如何接收请求、记录进度、保存长期产物，以及中断后能否恢复；各动作的名称和用途在后文逐项定义。

`decision_528bf9985705` 解释用户怎样从 `/paper` 进入 Library 和 Research Workspace，以及 Paper ID、manifest、bundle、PDF 和基础文件生命周期。本文继续使用这些概念，并把重点放在后台执行。页面可以展示本文的状态，但页面当前显示了一个按钮不代表调用者自动取得对应权限。

## 持久存储边界

`paper.reviews` 和 `paper.notes` 保存长期内容；`review_jobs`、`deep_read_jobs`、`exp_jobs`、`collector_jobs` 保存各自业务任务。六张表使用具名业务字段，身份与关系不放入通用 JSON 容器；有明确语义的结构化结果、用量、输入回执和进程身份可以使用 JSONB。

Review job 以 `review_id` 关联 `reviews`，读取时组装 `result`，任务记录不重复保存评审正文。列表按创建时间和稳定 ID 排序，不保存列表 position。Paper 和搜索记录均不按 1000 条或其他数量裁剪；读取分页只限制当次返回量，不删除历史。SQL 的绝对时间采用 TIMESTAMPTZ，接口继续返回各字段规定的秒值或 UTC 字符串；未发生的时刻使用 NULL。

## 参与者、组件和记录

### Paper 输入回执

Paper 领域调用共享 Resident 时，适配器接收共享 handle 中的 `message_id`，并立即转换成 schema 为 `tfi.paper-resident-input.v1` 的 Paper 输入回执；读取只接受该精确格式，不因读取改写原回执或输入身份。回执用 `input_message_id` 表示实际已经投递的输入；`requested_message_id` 记录调用方请求，`planned_message_id` 只记录尚未投递的计划身份，`reply_message_ids` 与 `usage_message_id` 分别关联回复和计费消息。未取得实际输入身份时，状态为 `unconfirmed`，任务不得据此恢复、重投或切换 provider。

经 Lineage 工作流投递时，共享提交回执和 Paper 输入回执保留本次 `trigger_path_id`，用于查找 Event 到 Resident 的实际触发路径；提交、完成和中断恢复均保留同一引用。回执不再记录旧的 Resident `invocation_id`。Resident 本轮的实际执行仍由独立的 Execution 记录，路径通过自己的关联表引用 Execution。

持久记录只保存当前回执结构。Figure、Collector 和 Deep Read 的恢复都先校验精确 schema，再以 `input_message_id` 继续等待同一输入。Deep Read job 只保存 `resident_input_message_id`；运行时不读取 `message_id`、`message_id_kind` 或 `resident_message_id` 历史别名。共享 Resident API 的 `message_id` 仍是其当前接口字段，差异只在 Paper adapter 边界转换。

### 九个共享功能 Resident

Paper 的功能型 AI 统一由 Paper Lineage 中九个固定 Resident 执行，每个角色只绑定一个持续身份。所有用户共享同一组成员，也可以直接在普通 Society 页面与任意成员对话。Paper Lineage、页面权限和 Research Chat 的身份规则以 `decision_528bf9985705` 为准。

角色键、流程入口与产物只在下文“固定功能对应表”维护；本节定义这些角色共有的身份和调用边界。

固定角色的身份解析键是 `(Paper Lineage ID, 角色名)`，不包含 actor、paper_id、figure_id、review_id、job_id 或模型名。不同论文、用户和任务是同一成员接收的不同输入，而不是创建新成员的理由。图表解释与图片生成是两个不同角色；CCF 的不同处理阶段仍是一个角色。

功能任务由页面或直接操作 Lineage 的调用者触发同一个已绑定流程入口。Paper 领域把目标 Paper 或登记草稿、输入资源和产物要求组织为通用 prompt，消息 Effect 将固定功能提示词与本次 invocation prompt 组合后投递给共享角色。真实发起者来自受信请求上下文。执行通过既有 HATG ISA 与普通 Resident 队列，共享成员按规范输入顺序处理任务，实际队列和运行状态由 Resident/Execution 提供。

每次任务将业务运行与目标 Resident、规范输入消息 ID、预期产物关联。进度和阶段续接按本次输入及其对应回复核对，直接聊天或其他用户的后续输入不能被误收为这次任务的结果。长期产物按 Paper 及产物类型发现，包含所有获授权入口产生的正式结果，不以是否存在当前页面发起的 job 为显示前提。共享执行身份保留实际发起者，也不扩大源资源或写入目标的授权。

功能成员可以调用模型、检索工具、解析器、图片生成器或 Collector 程序作为执行工具，但这些工具不能另行拥有与 Paper Lineage 脱节的业务 Resident。模型或 provider 选择不改变角色、团队与会话归属。解析 PDF、提取已有图表、普通笔记写入等非 AI 操作仍可由领域服务直接处理。

语义搜索不使用这九个角色，由 Portal 范围职责登记指定的公共搜索 Resident 提供；Research Chat 则按用户与论文动态绑定，两者不能落入通用的 `paper:background-ai` 替代团队。

### 谁发起、谁执行、谁保存结果

| 名称 | 普通语言定义 | 职责 |
|---|---|---|
| Actor | 当前通过人类 Session 或 Agent token 认证的调用者；Session 是人的登录会话，Agent token 是 Agent 的受控访问凭证 | 发起请求，记为运行的创建者，并作为权限系统的判定对象 |
| Paper viewer | 有权读取指定 Paper 的用户 | 读取 Paper 和受控 asset |
| Paper contributor | 有权修改指定 Paper 的用户 | 启动会写入长期产物的 Paper 研究动作 |
| Paper 路由保护 | HTTP 入口的统一认证和权限检查 | 要求有效认证；写请求顶层带 `paper_id` 或 `id` 时统一要求 contributor |
| Job | 一次后台工作的持久或半持久运行记录 | 保存业务键、状态、步骤、进度、错误、用量和结果位置。Review、Deep Read、Experiment 与 Collector 的 job 记录，以及 Review 列表、Notes 列表和精选会议，保存在 PostgreSQL `paper` schema 中，每条记录以 ID 为主键一行 |
| Business task | Paper 领域协调业务记录、输入与产物的任务 | 解析功能流程绑定、关联规范输入及对应结果，校验并保存业务产物；AI 执行由 Lineage/Resident 负责，非 AI 处理可以调用普通工具 |
| Provider | 实际提供模型或执行能力的后端 | 由功能 Resident 自己的设置决定；Paper 领域只投递任务，不选择 Provider 或模型 |
| Canonical execution | 已提交给 Resident 的规范执行，使用稳定 Resident 消息标识 | 让恢复过程继续等待同一工作，而不重复提交输入 |
| Paper bundle | 指定 Paper 的长期文件边界 | 保存成功的 Analysis、Review、Experiment、Refs、Figures 和 Notes |
| Manifest | Paper 当前业务元数据 | 只接收已经形成业务结果的状态，不充当活动任务队列 |

### 状态和业务键

- **业务键** 是判断两次请求是否属于同一工作的一组字段，通常包含 Paper、Actor 和动作目标。命中同一业务键的活动 job 时，start/create 返回原运行。
- **排队状态**通常写作 `queued` 或 `pending`，表示记录已创建，执行尚未开始。
- **运行状态**写作 `running`，部分 CCF web 阶段使用 `web_pending`，表示工作仍未形成终态。
- **成功状态**写作 `done`，表示对应 Paper job 已保存长期产物。
- **失败状态**写作 `error`。错误保留在运行记录或 metadata 中，调用方可以区分失败和无结果。
- **跳过状态** `skipped` 用于自动提取在缺少本地 PDF 时没有执行。
- **删除状态** `deleted` 用于语义搜索或 Collector 的软删除记录。
- **陈旧运行**是长时间没有更新、已经无法当作活动工作的记录。只有明确声明 stale 处理的动作才会把它改为 `error` 并允许新运行。
- **Usage** 是一次模型或执行消耗的公开统计。部分动作把它与结果一起保存在 job 或 metadata 中。

一个动作进入 `done` 只说明该动作已经完成约定产物。它不会改变其他动作的状态，也不会隐式删除 Paper。

## 共同调用和恢复流程

```text
Paper 页面 / 直接操作 Lineage / 授权 Agent 工具
          ↓
对应入口认证 + Paper 领域资源权限和业务参数校验
          ↓
Paper 领域：关联业务键、规范输入与预期产物，复用或建立业务记录
          ↓
AI 功能：请求 Event → Effect invocation → 共享 Resident canonical execution
已建立的 Research Chat：Chat Workspace → 通用 Resident 消息入口
非 AI 动作：同步函数 / 普通工具；语义搜索：Portal 公共搜索入口
          ↓
按规范消息观察执行 → 校验并提交 bundle 产物 → 更新索引/manifest → 页面重新查询
```

业务运行保存步骤和进度，并引用 Resident/Execution 的实际执行身份。各入口的重试与恢复继续等待原输入，不另起一套 AI 调度。Library 与详情按 Paper 读取成功产物，业务运行记录是否仍存在不影响长期产物发现。

共同规则如下：

- Paper HTTP 入口先要求有效的人类 Session 或 Agent token。
- 顶层含 `paper_id` 或 `id` 的写请求在路由层统一要求 Paper contributor。搜索和收集任务不保存所有者列表，记录的 `created_by` 只表示发起者；记录发起者不等于其执行 Resident 对其他用户私有，也不能因此按 actor 创建固定角色副本。
- 参数错误返回 400；Paper 或运行不存在返回 404；权限拒绝返回 403；未处理执行失败返回 500。
- 同一业务键的活动请求返回原记录。状态查询读取或恢复既有运行，不创建第二份业务工作。
- 各动作分别声明跨重启保证。调用方不能根据“后台任务”四个字推断它会自动续跑。

## Paper 功能与 Lineage 流程

Paper 的功能按钮绑定流程入口，流程再绑定职责稳定的 Resident。下面的“摘要请求”“评审请求”是 Event Source 的业务名称，人工入口使用 `manual.triggered`，定时入口使用 `time.reached`。HATG 保持通用指令与参数，Paper 的资源引用、关注点和输出要求由业务领域组织到 prompt 中。

消息由调用 `DeliverMessage` 的 Effect 投递。固定功能提示词由 Effect 保存，本次任务文本属于 invocation，两段用两个换行组合；空段、长度和失败规则沿用 `decision_4adcc1ff2f2c`、`decision_898b8911a2ff`。普通 Lineage 与 Paper 页面可以触发同一个功能入口。

### 固定功能对应表

| Paper 功能 | Event Source 入口 | Effect → 共享 Resident | 本次输入与结果 |
| --- | --- | --- | --- |
| Paper Collector | 收集论文请求；定时收集使用 `time.reached` | 收集任务 Effect → `paper:collector` | 研究方向、来源和筛选要求进入 prompt；返回候选、筛选依据及受授权的登记结果。定时与手动入口共享成员。 |
| PDF 登记信息提取 | 提取登记信息请求 | 登记信息提取 Effect → `paper:registration-metadata` | 输入上传材料引用及说明，返回可编辑元数据草稿；此时可以尚无 Paper ID。 |
| CCF 论文信息补全 | 补全论文信息请求；进一步调查请求 | 信息补全 Effect／调查 Effect → `paper:ccf` | 返回目录补全结果、摘要与来源证据；不同固定提示词的 Effect 共享同一成员。 |
| 简短论文摘要 | 生成简短摘要请求 | 简短摘要 Effect → `paper:summary` | 输入论文引用、语言和侧重点，返回列表及详情使用的简短介绍。 |
| Deep Read | 深度阅读请求 | 深度阅读 Effect → `paper:deep-read` | 输入论文引用与阅读目标，返回正式 Analysis 产物。 |
| AI Review | 论文评审请求 | 评审 Effect → `paper:review` | 输入论文、关注点及参考材料，返回正式 Review、判断与行动建议；多个 reviewer 视角可由同一成员完成。 |
| Experiment Extraction | 提取实验信息请求 | 实验信息提取 Effect → `paper:exp` | 返回实验设置、指标、对照、消融与复现信息，不代表实际运行实验。 |
| Figure Explanation | 解释图表请求 | 图表解释 Effect → `paper:figure` | 输入图表引用与问题，返回图意及论文上下文解释。 |
| Figure Generation／Editing | 生成图片请求；编辑图片请求 | 图片生成 Effect／图片编辑 Effect → `paper:figure-generation` | 输入描述、参考材料或原图与修改要求，返回新图片及输入、产物关联。两种操作共享同一成员。 |

这些 Resident 和流程均属于同一个 Paper Lineage。功能入口、Effect 与 Resident 不要求一一对应：Effect 表达明确操作，Resident 承担稳定职责。

### 公共能力与持续对话

| Paper 能力 | 流程或组件连接 | 所有权 |
| --- | --- | --- |
| 语义搜索 | 公共搜索入口 → 职责登记 `search.paper` → 共享语义搜索 Resident | 搜索由该 Resident 承担；Paper 提供受权限约束的检索范围和本次查询。能力调用不授予其所在 Lineage 的成员管理权。 |
| Research Chat 首次实际发言 | 解析业务绑定 → 缺失时触发首次开通流程 → 创建 Resident → 绑定并发送初始化输入 → Chat Workspace 展示同一会话 | Paper 领域拥有目标解析与开通幂等；Resident、会话和消息属于普通 Paper Lineage 成员及公共聊天机制。 |
| Research Chat 后续发言 | 已绑定 Resident → Chat Workspace → 通用 Resident 消息入口 | 与 Society 页面打开同一 Resident 的 Chat Workspace 相同，不为每轮问答增加 Event／Effect 流程。 |

### Research Chat 首次开通与后续对话

单篇对话按 `(Paper Lineage ID, Paper ID, 当前用户 ID)` 解析。只浏览论文或挂载空 Chat Workspace 不创建成员；首次实际发言才申请开通。已存在绑定时直接挂载该 Resident，刷新、重新进入和后续发言复用同一身份。

首次开通包含两个不同动作：

```text
首次发言 → Paper 绑定解析与幂等占位
             ├─ 已有有效 Resident → Chat Workspace 正常发送
             └─ 尚未创建 → 开通 Event → 调用 CreateResident 的 Effect → 普通 Chat Resident
                                      ↓ effect.succeeded 返回规范 resident_id
                         保存本次绑定、建立初始化 Effect 的明确 Target
                                      ↓
                         调用 DeliverMessage 的 Effect → 论文上下文 + 第一条用户问题
                                      ↓
                         Chat Workspace 展示同一 Resident 的消息与回复
```

创建使用调用 `CreateResident` 的 Effect；初始化使用调用 `DeliverMessage` 的 Effect。创建回执中的规范 `resident_id` 用于绑定本次实例的初始化目标，目标关系通过既有 HATG 图操作建立。每个开通实例独立保存其目标，不临时改写一个共享 Effect 指向“当前用户”。创建成功事件仅说明身份已产生，初始化输入是否投递以及模型是否完成仍有各自回执。

初始化输入由固定阅读协助说明、当前论文的受授权资源引用和首条问题组成，可以在一个规范输入中完成。首次开通流程已投递该输入时，Chat Workspace 只展示回执对应的消息，不再次提交首条问题。后续输入由公共聊天发送入口直接提交，仍沿用 HATG/Resident 消息与权限协议，但无需先触发 Paper 专属 Event 或重发固定初始化文本。

Paper 绑定 owner 对同一业务键的并发首发做幂等控制，持久关联创建回执、目标 Resident 与初始化消息。创建成功而初始化失败时重试初始化；不能再次创建 Resident。重复请求不重复发送首条问题，不同的新问题保持独立消息身份。已绑定成员被删除或移出团队属于明确的失效绑定，按 `decision_528bf9985705` 报错，不当作首次访问静默重建。

Paper 页面与 Society 页面展示的是同一份会话事实。在任一页面发送的消息均能在另一个入口读取；用户之间没有额外的对话隐藏权限。换用户默认解析另一个 Resident，不改变原成员的可见范围。

### 多阶段功能对应表

| 组合功能入口 | Lineage 与业务步骤 | 衔接约束 |
| --- | --- | --- |
| 上传 PDF 并完成登记 | 提取请求 → Metadata Effect → `paper:registration-metadata` → 登记草稿 → 用户确认、Paper 领域登记成功 → 摘要请求 → Summary Effect → `paper:summary` | 提取完成不等于登记成功。登记、去重和权限检查由 Paper 领域负责；后续摘要使用登记成功的论文引用。 |
| 一键研究论文 | 研究请求 → Deep Read Effect → `paper:deep-read` → 本次 Analysis 有效 → Experiment Effect → `paper:exp` → 本次实验说明有效 → Review Effect → `paper:review` | 每个阶段引用本次输入对应的正式产物，Review 可同时使用论文、深读和实验材料。串行是基础形式，并行汇合需要单独声明完成条件。 |
| 解释图表后改绘 | 图表研究请求 → 解释 Effect → `paper:figure` → 本次解释有效 → 可选用户确认 → 改绘 Effect → `paper:figure-generation` | 保留原图、解释产物和修改要求的对应关系；确认步骤是独立边界。 |
| 收集并生成阅读清单 | 收集请求 → Collector Effect → `paper:collector` → 候选确认与登记成功 → 对选中论文逐项触发摘要流程 | 候选不自动等于已登记 Paper；逐项分发需有数量、队列和失败边界。 |

组合流程可封装为 Function Block，复用上述功能成员。Function Block 的展开、续接和执行仍由 HATG Program 负责；Paper 领域保存业务记录并完成确定性处理，不另建平行 AI 流程引擎。

### 完成事件、失败与阶段续接

| 阶段事实 | 使用方式 |
| --- | --- |
| `manual.triggered` | 记录本次请求，并向流程投递 invocation。请求接受不代表所有目标已经执行成功。 |
| `effect.succeeded` | 读取相应 Instruction 的提交回执和 typed outputs。调用 `CreateResident` 的 Effect 成功说明 Resident 已创建；调用 `DeliverMessage` 的 Effect 成功说明输入已投递，不说明 AI 已完成。 |
| `resident.work_started`／`resident.work_finished` | 展示本次实际执行状态；续接前核对规范输入和对应回复的因果身份。 |
| `resident.work_failed`／`resident.work_cancelled`／`effect.failed`／`event.delivery_failed` | 保留失败或取消事实、对应阶段和可执行的重试边界，不以空产物冒充成功。 |
| Paper 产物校验和提交成功 | 更新该论文的正式产物查询结果，并作为需要有效产物的后续阶段的前提。 |

共享 Resident 可能处理不同用户、不同论文及直接聊天的输入，完成监听必须按本次输入消息和回复身份关联。不能把“成员最近结束了一次工作”当作当前研究阶段的完成；也不能在前一步仅完成投递时提前启动依赖其研究结果的下一步。Invoke 的队列、幂等、失败和恢复沿用 HATG 公共机制。

### 产物发现与页面更新不依赖操作入口

同一个 Paper 的正式产物通过一套 Paper 领域协议校验、提交和发现。页面按钮、Lineage 功能入口、定时流程和受授权工具产生的结果遵守同一份论文关联、产物类型、规范身份、输入／回复来源和权限规则。

Paper 页面以当前论文的正式产物查询为显示依据，不以“本浏览器点击过按钮”“当前用户拥有某个 job”或“启动请求仍在内存”为显示前提。Lineage 中执行“一键研究论文”后，成功登记的 Analysis、Experiment 和 Review 应分别出现在该论文对应区域；任务失败、被取消或尚未形成有效产物时显示相应状态，不伪造内容。

产物提交通过既有资源索引、Paper manifest/bundle 和领域变更通知使查询结果更新。已打开的页面收到相关变更后重新查询；页面重新打开、切换回该论文或手动刷新时也从同一事实源重建，不能把启动按钮的回调作为唯一刷新机制。更新通知用于触发查询，长期产物才是内容依据。

直接在 Resident 中讨论一篇论文、在任意 Workspace 留下文件或仅声称“研究完成”，不会自动形成正式 Paper 产物。只要通过受授权的 Paper 产物协议登记并关联到该论文，即使没有由 Paper 页面创建的 job，也应能被页面发现。任意路径扫描、提取成员最后回复或仅按文件名猜测论文归属都不能替代该协议。

长期产物的可见性沿用 Paper 和资源权限；Lineage 成员权限不替代 Paper 写权限。运行记录关联和页面默认聊天目标也不应成为额外的正式产物隐藏规则。

## 设计点

### Paper Review：生成可独立发现的长期评审

- Paper Review 是写入 Paper bundle `reviews` 区域、拥有稳定 `review_*` 身份的 Markdown 产物。

- Contributor 用 `paper_id` 发起 create，可以附加标题。内容由共享 `paper:review` 角色 Resident 按自己的设置生成。
- 同一 Actor 与 Paper 已有 `queued` 或 `running` job 时，重复 create 返回原 job；旧 job 进入 `error` 后，下一次 create 才建立新 job。
- Builder 在启动时分配 Review ID。Builder、最终 Markdown 和公开 Review Resource 投影始终使用同一个 ID。
- HTTP create 会同步等待内部 business task。成功时直接返回长期 Review 对象；执行失败或等待超时时返回错误，不返回空对象伪装完成。
- 页面采用非阻塞的 start/status 流程：start 返回原有活动任务或新建任务，页面保存 job_id 并查询状态，不占用一次请求等待完整生成。status 只读取任务，按当前 actor 与 Paper 查找最近运行；按 job_id 查询时校验创建者和可选 Paper ID，未知任务明确报错。刷新页面重新关联同一运行，不自动生成第二份评审。
- Job 从 `queued`、`running` 进入 `done` 或 `error`。`done` 写 Review Markdown 和 `paper.reviews` 中的列表记录，并通知资源索引重新扫描评审；即使没有列表记录，Review adapter 仍能从持久文件发现它。
- Job 在排队持久化时记录执行进程身份（host、boot ID、PID、启动时钟），用于判断重启中断，不能仅根据当前 Web 进程的线程列表判断。状态读取、同步等待与再次启动先核对该身份；确认原进程已退出或 PID 被复用后，在任务锁内重读当前状态。已保存的完整 Review 恢复为 done，否则置为明确的 interrupted/error，允许用户重新生成，不自动重复调用模型；不能证明进程已退出时保留现状。
- Review job 关联共享 `paper:review` 角色的规范输入，review ID 只标识产物而不创建评审 Resident。绑定执行身份本身不承诺重启后自动续跑；删除按 `review_id` 移除长期 Markdown 和索引记录，不删除共享成员。

- Paper Review 是论文研究产物，由 Paper 页面展示、读取和删除；其资源链接打开所属论文。

### Review 如何从模型输出中提取

Review 解析器优先读取模型输出中的 JSON 代码块，再用 JSON 解码器取出首个完整对象。输出没有 JSON 代码块时，从正文中查找该对象。对象之后的工具记录、保存回执或重复结果留在原始输出中，正式评审使用解析得到的这一个对象。

解析后按同一份 Review 数据结构检查字段和 reviewer（评审者信息）。缺少内容时保留原始输出，并把评审标为 `incomplete`，让用户看到仍待补齐的部分。长期 Review 只采用模型实际返回且通过校验的内容。

### Deep Read：可恢复的深度分析

- Deep Read 对本地 PDF 运行深度分析，并把成功结果写成长期 Analysis Markdown。

- Start 要求 `paper_id`、contributor 权限和本地 PDF，并复用该 Paper 的 `queued` 或 `running` job。
- Job 保存共享 `paper:deep-read` 角色的 Resident binding 和规范 Resident 消息。恢复时继续等待原消息；不按论文、发起者或 Provider 创建另一个深读成员。
- 一次 Deep Read 只向角色 Resident 投递一次任务。执行失败时 job 进入 `error`，不把同一任务再投递一次；用户可以重新启动。
- Status 可以用 `job_id` 或 `paper_id` 定位，返回 `idle|queued|running|done|error`、step、progress、`analysis_path`、usage 和 error。
- Status GET 命中非终态 job 时重新启动等待逻辑，不重复提交 Resident Input。
- 成功时写 Analysis Markdown，并把 Paper 更新为 analysis-ready；提取、执行或写入失败时 job 进入 `error`。

- Deep Read status 按任务关联的真实 Paper 检查当前 actor 的 viewer 权限，随后才能返回状态或恢复等待。按 job_id 查询也执行同一检查；同时给出 paper_id 时必须与任务所属论文一致。未找到指定任务返回 404，缺少定位参数或论文不匹配返回 400，无阅读权限返回 403。

### Experiment：生成长期实验说明

- Experiment 动作围绕一个 Paper 生成实验 Markdown，`exp/create` 和 `exp/start` 进入同一协议。

- 两个入口都要求 `paper_id` 和 contributor，并复用同一 Paper 尚未陈旧的活动 job。
- 显式 start 发现长时间未更新的活动记录时，先把旧 job 标记为 `error/stale`，再创建新 job。
- Status 可按 job 或 Paper 查询，返回 `idle|queued|running|done|error`、step、progress、`exp_path`、usage 和 error。
- 成功后写长期 Markdown，job 进入 `done` 并返回 `exp_path`；失败保留 `error`。
- 当前 Experiment 只提供 stale 检测后的显式重启。它没有规范 Resident 恢复保证；Portal 重启后 status 不会自行续跑旧执行。

- Experiment status 按任务关联的真实 Paper 检查当前 actor 的 viewer 权限；授权通过后才读取状态或把已陈旧任务标记为 error。job_id、paper_id 和空闲状态的校验规则与 Deep Read status 一致。

### CCF enrichment：补全目录论文元数据

论文预览的 Sources 同时展示默认元数据补全的 `sources` 和进一步调查的 `web_sources`，按来源链接去重；无需启动额外网页调查即可查看已有默认来源。刷新、重新打开条目后仍从缓存结果显示同一来源。只有 HTTP/HTTPS 来源作为外部链接，其他来源文本只展示，不执行。

- CCF enrichment 接收带标题的 CCF 目录论文描述，补全元数据和摘要；`web=true` 在同一条目上增加网页调查阶段。

- 处理器按论文 identity 复用缓存和任务。状态使用 `pending|running|web_pending|done|error`。
- 重复 POST 返回现有状态；需要 web 阶段时在同一缓存条目上继续，元数据和 web 调查共享结果。
- Portal business task 产生终态。`done` 返回补全元数据与摘要；外部抓取或生成失败写入 `error`。
- 该动作没有独立 GET status，调用方继续 POST 读取缓存状态。
- 只有顶层携带 Paper ID 的请求进入通用 Paper 权限预检（经权限系统，decision_b820faf42218）。没有 Paper ID 的目录 enrichment 依赖调用入口边界，缓存服务自身不做权限判断。

### Reference：同步提取并登记引用

- Reference extract 要求源 `paper_id`，从该 Paper 的本地 PDF 同步提取引用，成功直接返回 `refs` 和 `count`，不创建后台 status。
- Refs 页面展示当前 Paper、当前提取请求的状态：同步请求未完成时显示正在提取，成功无引用时显示空结果，失败时显示本次错误。历史 `assets_job` 的 done 文本或计数不能代替当前同步请求的加载状态；仅正在运行的后台素材任务显示其当前进度。
- 引用解析识别带冒号的标准 bibliography heading。已确认引用章节允许不足五条的真实引用；无 heading 的启发式仍保留最少条数约束。不能仅因主题词或不在预设 venue 列表而丢弃有效编号引用。标题规则统一验证候选，并区分作者缩写、Unicode 姓名、姓氏撇号、标题引号和 DOI。特殊 PDF 排版与损坏源文件单列，不按论文 ID 增加特例。
- Reference register 要求 `ref_id`，可以附源 `paper_id`。后端优先复用已登记 Paper；没有命中时查询外部元数据，并可以登记新 Paper。
- Register 响应包含引用、Paper 和 detail，页面可以跳转到复用或新建资源。
- 省略源 `paper_id` 时不会补做源 Paper 的权限检查。参数、缺失目标或执行错误分别以 400、404 或 500 结束。

### Figure 提取和额外输入

图题位于图形上方时，提取图须包含该图题。坐标标签下方紧邻的显式 Caption、Note、Disclaimer 说明及连续换行属于图形内容，保留其完整像素；准入距离从已识别的轴标签下沿计算，不能因说明是完整句子而截掉。普通正文、后续章节和下一图题仍限制裁切范围。

图像裁切保留图内标题、坐标轴与标签的完整像素和必要留白。下方 caption 对应的图形上方若还有同编号图标题，标题与图形之间的空白不应导致标题被截去；收录范围受标题字号、相邻距离及已有正文、表格边界约束，保留标题完整像素。单栏页面的短 caption 不限定图形宽度；提取区域须覆盖对应图形的完整内容，包括 caption 横向范围以外的柱形、标签和数值，再收缩多余留白。明确的分图边界和双栏布局仍限制对应提取区域，不能因此合并邻图或正文。图题处于图框内部时，紧邻标题上方的外框线不应被误判为另一张图；只有左右框线连续穿过标题区域并延伸至图形主体，才将该外框与下方图形作为同一提取区域。独立横线和标题上方的独立图形仍按原边界处理。上方正文或表格形成的是不可越过的下边界；正文已经远离图像时，不应取消图像自身的上边距。验收同时比较原 PDF、提取图及刷新后的图像，不能用重新生成的图片替代原图验证。

- Figure extract 要求 contributor 和本地 PDF，直接返回 `figures` 和 `count`。同一 Paper 的提取串行化，先在独立目录生成完整候选，提交时重新检查 Paper/PDF 与当前 metadata，再原子切换 metadata。解析、渲染或提交前失败保留旧图；提交结果无法确定时保留候选目录并报错，不猜测写入失败后删除文件。metadata 已确认提交、后续通知失败时返回 `persistence_warning`。
- `figure_id` 是稳定局部身份，`index` 只表示展示顺序。caption 模式按 label 与完整 caption 唯一匹配旧图；embedded 模式按解码像素摘要唯一匹配。只有唯一匹配才保留旧 ID、解释、usage、invocation 与生成图；新图分配不回收的不透明 ID，不以序号继承旧任务。提交前读取最新 metadata，保留提取期间完成的解释。
- 嵌入图按像素内容去重，不以尺寸或文件大小近似判断；输出重新编码为真实 PNG，使用原提取编号关联页码。`pdfimages` 非零退出为失败。成功提交后只清理已失去引用的旧提取图，保留仍被当前图或 generated 使用的文件。损坏或无法读取的旧 metadata 拒绝覆盖。
- Figure input-image 把一个额外输入文件保存到目标 Paper 的受控范围。越界文件不会进入 generation。
- Viewer 通过 asset GET 读取提取图或生成图。入口在返回文件前检查 Paper viewer 和 bundle 路径。

### Figure explanation：解释一张图

- 请求以 `paper_id` 和 `figure_id` 指定图片。相同 Figure 已有 `queued` 或 `running` explanation 时返回原任务。
- Figure metadata 保存 explanation 状态、文本、usage 和错误。成功进入 `done`，执行失败进入 `error`。
- 图表解释启动或执行失败时，metadata 保存 explanation_status=error 与具体错误，不发布替代文本为成功的 AI 解释。后续显式重试仍复用该图及共享角色。
- 该动作没有独立 status GET。页面轮询 Paper detail 中的 figure metadata；当前 usage 会暴露规范 Resident 消息身份。

### Figure generation：生成或编辑图片

- 请求要求 `paper_id`、`figure_id` 和自然语言 `request`。它可以用既有 `generated_id` 指定来源，也可以附加受控输入图。
- 相同请求会复用尚未过期的活动任务。陈旧任务先进入 `error`，确需新任务时才分配新的 `generated_id`。
- Generation metadata 保存 request 和输入图路径，供页面和后续编辑识别依据。
- 成功同时写生成图片和 metadata，并进入 `done`；失败进入 `error`。
- Delete 同时校验 Paper、Figure 和 generated ID，只移除指定 metadata 与图片。

### Notes：直接写长期笔记

- Contributor 可以创建 Paper bundle 内的 Markdown Note，用来记录问题、判断、摘录或后续 Todo 文本。Paper detail 提供读取。
- Delete 按 note ID 删除一条长期 Note，不改变 Paper 身份或其他 bundle 产物。

### Paper semantic search：搜索记录与候选授权

- AI 执行身份来自 Portal 范围职责登记指定的公共语义搜索 Resident，而不是 Paper Lineage 或 actor 专属搜索成员。任务与 Saved Search 的创建者不决定执行 Resident 的归属，共享机制见 `decision_eb066fbea4ed`。
- Actor 提交非空 query 后获得稳定 `search_*` ID。状态在 `queued|running|done|error|deleted` 中推进。
- 只对该 Actor 可见的 Paper 排序。模型返回的 ID 必须属于本轮 catalog，未知或越权 ID 被丢弃。
- `done` 保存 results、result_count、query interpretation 和 notes。List 和 get 向有效登录身份返回最近或指定搜索。
- 活动搜索 refresh 只返回现状，不重复启动搜索。终态 refresh 复用同一 search ID，并把既有结果与新排序合并。
- Delete 软删除记录。记录不存在返回 404。deleted 为终态；任务存储拒绝在途成功、失败、进度或刷新写入覆盖删除记录。
- 搜索执行、完整候选输入、历史已冻结多批次运行的恢复，以及重启后按原消息继续观察，统一遵循 decision_eb066fbea4ed。Paper 使用职责 `search.paper`；候选仍按创建者权限选取，结果按查看者权限再次过滤。

### Collector：共享收集 Resident 与受控执行工具

- Actor 用 `direction` 或 `query` 发起 start，并提供 `max_papers`。后端把数量限制在 1 至 50，并把 job 归当前 Actor 所有。
- 任务投递给 Paper Lineage 的共享 `paper:collector` Resident。该成员可使用受控 Collector 程序收集候选和登记 Paper，但程序不是另一个 Paper 业务团队或独立 Agent 身份入口。状态从 `queued|running` 进入 `done|error`。
- 成功结果保存逐项登记、`registered_count` 和摘要；失败保留错误文本。
- Jobs list 返回未删除的任务；任务不保存所有者列表。当前没有单 job GET，页面通过列表观察状态。
- Delete 把 job 标记为 `deleted`，普通列表随后隐藏它。
- Web 重启后，原请求者经当前认证访问 Jobs list 时，已持久化规范输入回执和原论文库快照的活动任务恢复等待同一 input_message_id，并按原协议校验、保存结果，不再次投递收集请求。其他用户的查看不会借用原请求者身份。仍有存活处理进程、输入未确认、缺少快照或旧协议的任务不会自动恢复；同一进程的重复轮询只保留一个观察任务，跨进程通过持久任务存储锁认领观察责任。删除或已结束任务保持原终态。

### PDF 登记草稿与用户确认

上传 PDF 的元数据提取结果是可编辑草稿。作者必须来自明确署名，缺少署名时留空；方法、模型或实验组标签不能当作作者。用户可在登记前纠正提取结果。

登记时以用户提交的字段为准：只对未提交的字段采用草稿默认值。明确提交的空字符串、空列表或 null 表示清空，不得再次用草稿回填作者、摘要、标签或标识符。提取仍在运行和提取已完成时遵循同一规则；必填字段仍按登记规则校验。

### 登记后的自动提取

- Paper 登记或补齐 PDF 后可以自动提取引用（Refs）与图表（Figures）。
- 提取状态为 `queued`、`running`、`done`、`skipped` 或 `error`。本地 PDF 缺失（包括解析路径抛出 `FileNotFoundError`）时为 `skipped`；权限与其他运行错误仍为 `error`；`done` 分别记录引用和图表数量；`error` 保存一个或多个原因。任何终态都不改变 Paper ID。

## 状态所有权和恢复总表

| 动作 | 活动状态由谁保存 | 成功产物 | Portal 重启后的当前保证 |
|---|---|---|---|
| Paper Review | Review job | `review_*` Markdown 与索引 | 不续跑旧活动生成 |
| Deep Read | 含规范执行身份的 Deep Read job | Analysis Markdown 与 analysis-ready 状态 | Status 恢复等待同一消息；失败后由用户重新启动 |
| Experiment | Experiment job | Experiment Markdown | 无 canonical 恢复；stale 后显式 start |
| CCF enrichment | identity 缓存记录 | 补全元数据、摘要和 web 结果 | 通过重复 POST 读取已有缓存状态 |
| Reference extract/register | 同步调用 | Refs 或复用/新建 Paper | 请求结束即终态 |
| Figure extract/input | 同步调用和 figure metadata | 提取图、metadata、输入图 | 持久文件继续存在 |
| Figure explanation | figure metadata | Explanation 文本与 usage | 页面从 Paper detail 读取 metadata |
| Figure generation | generated metadata | 图片和 generated metadata | 陈旧任务变 error，再创建新 ID |
| Notes | 同步调用 | Note Markdown | 持久文件继续存在 |
| Semantic search | Saved Search 记录（候选输入按创建者隔离） | 排序结果和解释 | 列表或读取时恢复等待已投递的消息，不重复投递 |
| Collector | Actor-owned job | 登记结果和摘要 | 原请求者的认证列表读取恢复等待已确认输入，不重复投递 |

## HTTP 接口

### Review

- **非阻塞启动：** `POST /paper/api/reviews/start` 校验 Paper contributor，返回按当前 actor 与 Paper 创建或复用的 job。
- **运行状态：** `GET /paper/api/reviews/status?job_id=|paper_id=` 返回当前 actor 对应的 `job`，按论文查询且无任务时为 `null`；页面使用此入口恢复运行展示。
- **创建：** `POST /paper/api/reviews/create` 校验 `paper_id` 和 contributor，创建或复用 job，并同步等待长期 Review 或错误。
- **读取：** `GET /paper/api/reviews?paper_id=` 从 bundle 与索引返回指定论文可发现的长期 Review。
- **删除：** `POST /paper/api/reviews/delete` 按 `review_id` 删除对应 Markdown 和索引记录。

### Deep Read 与 Experiment

- **启动 Deep Read：** `POST /paper/api/deep-read/start` 校验 Paper、contributor 和 PDF，返回活动运行。
- **查询或恢复 Deep Read：** `GET /paper/api/deep-read/status?job_id=|paper_id=` 返回完整状态，并为非终态 job 继续等待原规范消息。
- **创建 Experiment：** `POST /paper/api/exp/create` 校验 Paper contributor，创建或复用 Experiment job。
- **启动 Experiment：** `POST /paper/api/exp/start` 与 create 使用同一业务键；发现陈旧记录后按需创建新 job。
- **查询 Experiment：** `GET /paper/api/exp/status?job_id=|paper_id=` 返回状态、进度、`exp_path`、usage 和 error。

### CCF、Reference、Figure 与 Notes

- **CCF enrichment：** `POST /paper/api/ccf/enrich` 按论文 identity 返回缓存状态；`web=true` 可以启动 web 阶段。
- **提取 Reference：** `POST /paper/api/refs/extract` 要求源 `paper_id`，同步返回 `refs` 和 `count`。
- **登记 Reference：** `POST /paper/api/refs/register` 要求 `ref_id`，可带源 Paper，返回引用、Paper 和 detail。
- **提取 Figure：** `POST /paper/api/figures/extract` 要求 Paper contributor，从本地 PDF 写图片与 metadata 并返回数量。
- **保存 Figure 输入图：** `POST /paper/api/figures/input-image` 把额外图片写入目标 Paper 允许范围。
- **解释 Figure：** `POST /paper/api/figures/explain` 以 `paper_id` 和 `figure_id` 创建或复用解释，并更新 figure metadata。
- **生成 Figure：** `POST /paper/api/figures/generate` 校验请求和输入，返回活动或新 `generated_id`。
- **删除生成 Figure：** `POST /paper/api/figures/generated/delete` 校验 Paper、Figure 与 generated ID，删除 metadata 和图片。
- **创建 Note：** `POST /paper/api/notes/create` 要求 Paper contributor，写长期 Markdown 并返回 Note 身份。
- **删除 Note：** `POST /paper/api/notes/delete` 按 note ID 删除目标 Note。

### Semantic search

- **开始：** `POST /paper/api/semantic-search/start` 要求非空 query，返回 `queued` 的 `search_*`。
- **列表：** `GET /paper/api/semantic-searches` 列出 Paper 页面的搜索。
- **读取：** `GET /paper/api/semantic-search?id=` 返回指定记录。
- **刷新：** `POST /paper/api/semantic-search/refresh` 对活动搜索只读状态，对终态搜索复用同一 ID 重排并合并结果。
- **删除：** `POST /paper/api/semantic-search/delete` 把记录置为 `deleted`。

### Collector

- **开始：** `POST /paper/api/collector/start` 接受方向、查询和 1 至 50 的限制数量，创建以 Actor 为 `created_by` 的 job 并向绑定的 Collector Resident 投递规范输入。
- **列表：** `GET /paper/api/collector/jobs` 返回未删除的 jobs；没有单 job GET。
- **删除：** `POST /paper/api/collector/delete` 把 job 标记为 `deleted`，普通列表随后隐藏。

图题在图上方时，提图区域保留图形及相邻说明文字；距离判断以图内标签字号为基准，后续章节大标题不能扩大自身的收录距离。裁剪留白若落在未收录正文的一行中间，应收缩到该行之前，避免留下半行文字。已保存的生成图及原始成功/失败回执保持其历史事实；验证刷新恢复时分别记录源文件缺损和页面额外裁切，新生成/提取产物仍须验证文字完整性。
