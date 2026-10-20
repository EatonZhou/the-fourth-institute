---
kb_id: decision_528bf9985705
type: decision
project: AI员工
created_at: '2026-09-01'
version:
  number: 20
  created_at: '2026-09-01'
  created_by:
    user: ''
    agent: ''
  updated_at: '2026-10-19T19:42:46Z'
  updated_by:
    user: tfi-exported-user
    agent: resident_865c3c24f251
importance: 8
metaness: 2
ai_generated: true
links:
  source:
  - ${code_root}/management_portal/paper_projection.py
  - ${code_root}/management_portal/paper_backend.py
  - ${code_root}/management_portal/paper.html
  - ${code_root}/management_portal/resource_views.py
  - ${code_root}/tests/cases/test_paper_registration_authors.py
  - ${code_root}/management_portal/serve_management_portal.py
  - ${code_root}/tests/cases/test_paper_store_index.py
  - ${code_root}/tests/cases/test_paper_library_search.py
  - ${code_root}/tests/cases/test_paper_registration_upload.py
  related:
  - decision_1a2730dcd3d1
  - decision_e37f240e186d
  - decision_f67ae5e70793
  - decision_eb066fbea4ed
  - decision_f560803947c6
  - decision_b6e74d78f7b6
  - decision_6ad0384e020d
  - decision_61e50306cc79
  - decision_3ece26686464
  supersedes: []
  derived_from: []
---
# Paper 资源、Library 与基础文件生命周期

登录用户从 `/paper` 进入 Portal 的论文工作区。Paper 是 Portal 中一篇有稳定身份的逻辑论文；Research Chat 是页面右侧围绕当前论文或论文集合展开的聊天区。页面把论文发现、登记、阅读、研究产物和聊天协作放在同一处，使用户可以从一组论文缩小到一篇论文，再围绕它阅读和协作。本文负责 Paper 的稳定身份、Library 查询、页面主要区域、登记与去重、PDF 和整个资源的基础生命周期。

生成 Review、Deep Read、Experiment、图表解释、语义搜索和 Collector 都会在这个页面出现，但它们有独立运行状态。`decision_e37f240e186d` 负责这些后台任务怎样排队、产生长期产物和恢复；本文只说明页面怎样进入它们，以及终态产物怎样归入 Paper。

## 先认识页面

### 三个主要区域

| 可见区域 | 位置与内容 | 用户目的 |
|---|---|---|
| Paper Library | 左侧栏；顶部有折叠、刷新和加号登记图标，下方可在 `Library` 与中国计算机学会（CCF）的 A/B 目录两个入口之间切换 | 查找本地 Paper，浏览 CCF 目录，登记论文，启动语义搜索或 Collector。 |
| Paper Research Workspace | 中间主区域；顶部显示当前 Paper 身份、摘要详情和模式按钮 | 在 `Library`、`Today`、`Reader`、`Review`、`Exp`、`Figure`、`Notes`、`Refs` 模式之间切换，处理当前论文。 |
| Research Chat | 右侧协作区，可独立折叠 | 把当前 Paper、当前 Library 集合或明确选择的文本作为受控上下文加入聊天。 |

左栏的向左图标只折叠 Library，右侧 Chat 的向右图标只折叠 Chat。刷新图标重新读取当前 Library，加号图标打开 Paper 登记窗口。折叠不改变 Paper 数据。

### 列表、筛选和详情对象

- **Paper 卡片或列表项** 是一篇已登记论文的摘要，保留稳定 Paper ID，并显示标题、作者、年份、出版地点、标签、关联 Project 和可用研究状态。
- **Library 搜索框** 匹配标题、标签、analysis 和稳定 ID。`analysis` 指对论文生成并长期保存的深度分析文本。
- **筛选控件** 包括年份、出版地点、标签、关联 Project 和文件夹树。它们共同限定当前集合，不修改 manifest。
- **语义检索** 根据自然语言在当前用户可见的 Paper 中排序，运行记录归后台协议。
- **Collector** 根据研究方向或查询收集并登记论文，任务列表显示进度、所有者和结果，运行归后台协议。用户提交前可以把单次最多收集数量设为 1–50；页面与后端共同限制该范围，实际找到数量与新登记数量分别显示。
- **Research Workspace 模式** 是中间区域的八个按钮。模式切换只改变当前工作内容，稳定 Paper 身份保持不变。
- **Reader** 是阅读模式，内部把 Analysis 和 PDF 分为标签页。页面分别保持它们的滚动状态，切换时已挂载 PDF 不会重新加载。
- **文本选择菜单** 出现在 Reader 的选区旁。`Add to Chat` 把文字加入聊天草稿；`Translate` 翻译；`Find Ref` 查找引用；`Regist Ref` 登记引用；`Explain` 解释选区。Add to Chat 不自动发送。
- **登记窗口** 是上传 PDF、观察提取进度和修正元数据的表单。它在正式 Paper 创建前使用临时登记草稿。

## Paper 的核心概念

- **Paper** 是 Portal 中一篇逻辑论文，正式身份为 `paper_*`。标题、PDF 文件名或存储路径变化不会产生新身份。
- **Manifest** 是 Paper 的当前业务清单文件，新建写出的格式为 `tfi.paper.v1`，读取只接受该精确格式，不接受其他名称或版本，也不因读取重写历史清单，文件名是 `manifest.json`。它保存身份相关元数据和当前状态。
- **Bundle** 是 Paper 专属的文件目录。它把 PDF、派生文本和长期研究产物限定在同一个资源边界内。
- **Library** 是全部已登记 Paper 的查询视图。它由 manifest 和可重建索引构成，不拥有论文文件事实。
- **索引** 是从 manifest 汇总出的快速查询副本。索引损坏或过期时可以从 bundle 重建。
- **Fingerprint** 是去重键。Portal 依次使用去版本 arXiv ID、DOI、OpenAlex ID 和规范化标题 fingerprint 判断两次登记是否指向同一论文。
- **Asset** 是 bundle 中通过受控入口读取的文件，例如 PDF、图表或生成图片。Asset resolver 只允许解析已登记 bundle 内的路径。
- **Registration draft**，页面称登记草稿，是用户上传 PDF 后产生的临时、私有解析记录。`upload_id` 是它的定位键。
- **CCF A/B 目录** 是中国计算机学会按类别、等级和出版批次组织的会议或期刊论文目录。目录条目在登记前还没有本地 Paper ID。
- **PDF** 是 Portable Document Format 格式的论文文件。Portal 可以在 Paper 没有 PDF 时先保留身份，也可以在以后补传或替换文件。
- **外部论文标识符** 用于跨来源识别同一论文：arXiv ID 标识预印本，DOI 是数字对象标识符，OpenAlex ID 标识 OpenAlex 书目记录。
- **Contributor** 是可以修改 Paper 的用户权限；**viewer** 是可以读取 Paper 和 asset 的权限。这些用户权限与后台 worker 的内部文件权限分开判断。

Manifest 当前记录标题、作者、年份、venue（出版地点）、摘要、外部 identifiers（标识符）、去重 fingerprints、来源、文件、关系、标签和业务状态。Bundle 按职责保存 `text` 派生文本、`reviews` 长期 Review、`notes` 笔记、`refs` 引用、`figures` 图表和 `runs` 运行材料。

## Paper 页面背后的 Lineage

### 一个页面对应一个 Lineage

Paper 页面的 AI 工作都由一个普通 Lineage 完成，本文称它为 **Paper Lineage**。页面的绑定记录保存这个 Lineage 的 ID。Portal 不按用户、论文、后台任务或聊天再建其他 Paper Lineage，Lineage 本身也没有任何 Paper 专用的字段或类型。

谁能使用 Paper 页面，由 Paper Lineage 的 owner 名单决定：名单中的每个人都能打开和使用同一个 Paper 页面。页面或资源的访问记录需要保存这份名单时，从 owner 名单同步，不另外维护一份。页面筛选、布局和未提交的草稿仍然按用户各自保存。能使用页面，不会扩大用户对各篇 Paper、Project 和外部资源原有的读写权限。

用户既可以在 Paper 页面使用功能，也可以直接打开这个 Lineage 查看其中的 Resident、对话和运行情况；两处看到的是同一批 Resident 和同一份对话。

### 三类 Resident 的归属

| 类型 | 属于哪里、怎样共享 | 页面怎样使用 |
| --- | --- | --- |
| 语义搜索 Resident | Portal 范围职责 `search.paper` 登记的 Resident，由各页面共用，不属于 Paper Lineage | 页面通过共享的语义搜索提交当前用户、查询和按权限选出的候选论文，规则见 decision_eb066fbea4ed 与 decision_1a2730dcd3d1 |
| 九个功能 Resident | 属于 Paper Lineage，所有 owner 共用，不按用户、论文或任务复制；角色键和产物职责统一见 decision_e37f240e186d 的“固定功能对应表” | 页面按钮触发 Lineage 中对应的事件，由 Effect 把本次任务投递给对应 Resident；功能对应表与结果协议见 decision_e37f240e186d |
| Research Chat Resident | 属于 Paper Lineage，每位用户在每篇论文上各有一个 | 单篇阅读问答只使用当前用户在当前论文上的 Resident；回到同一篇论文时继续原来的对话 |

语义搜索 Resident 所在的 Lineage 是 Portal 的公共搜索服务，不是第二个 Paper Lineage；使用搜索也不带来对那个 Lineage 的管理权限。

### Research Chat 的身份与可见性

单篇 Research Chat 的唯一业务键为 `(Paper Lineage ID, Paper ID, 当前用户 ID)`。用户首次实际发起该论文的聊天时，在 Paper Lineage 内幂等创建并绑定一个普通 Resident；并发打开、刷新或重新进入不能创建副本。尚未开始聊天时，页面可以显示空会话，不应因浏览论文而批量创建成员。

第一次使用时，Lineage 中的流程先创建这个 Resident，再把论文上下文和第一个问题投递给它。之后，Paper 页面的 Chat 面板直接显示它的对话，每次发言都走 Portal 统一的聊天发送流程，与在 Society 页面中对话相同。创建、初始化、重试和绑定的流程见 decision_e37f240e186d。

不同用户针对同一 Paper 使用不同 Resident，隔离的是默认聊天目标和各自的会话上下文，不是当前访问权限。Paper 页面不自动绑定其他用户的论文聊天；但所有 Paper Lineage owner 都能在 Society 页面看到这些 Resident、阅读其会话、继续输入并参与对话。页面必须将这种共享团队可见性说明清楚，不能称为私有聊天，也不能在后端按聊天创建者新增 owner 之间的隐藏或禁止输入规则。

聊天原始绑定用户与实际发送者分别记录。其他 owner 直接参与一个 Resident 的对话，不改变它在 Paper 页面上属于哪个用户与论文的绑定；消息仍保留真实发送者。以后若引入更细粒度权限，需要另行修改授权协议，不能把尚未提供的隐私能力描述为现有保证。

未选中单篇 Paper 的集合聊天仍属于同一个 Paper Lineage，以当前用户和明确的集合 workspace 为目标解析上下文，不伪造 Paper ID，也不创建集合专属 Lineage。它不改变单篇聊天的上述业务键。

### 绑定与删除

九个功能 Resident 在 Paper 页面可以使用时已经绑定好，用户不需要额外“连接团队”。Research Chat Resident 在实际使用时才创建。它们都是普通 Resident，页面的 AI 操作全部通过它们完成。

已绑定的功能 Resident 或聊天 Resident 被删除、移出 Lineage 或解除绑定时，对应操作返回 `projection.resident_unbound`；Paper Lineage 不可用时返回 `projection.lineage_unbound` 或实际的权限错误。服务不会自动新建替代的 Resident、改用其他 owner 的聊天或改绑到其他 Lineage；第一次创建聊天和恢复已被删除的绑定是两种不同的操作，后者由 owner 明确处理。

删除页面不删除 Paper Lineage、其中的 Resident 或对话；删除论文也不删除已经产生的对话。搜索、筛选、阅读、草稿和不涉及 AI 的文件操作由页面和 Paper 服务直接完成，不经过 Lineage。

## 用户完成工作的路径

### 搜索、筛选、打开和导出

1. 用户打开 `/paper`。Library 只返回当前用户可见的 Paper。
2. 用户输入文本，并按年份、venue、标签、Project 或 folder tree 组合筛选。文本相关性按标题优先、精确标签高于摘要的顺序计算，作者、摘要和其他元数据参加后续匹配。
3. 页面先显示前 100 条结果，每次“加载更多”再追加 100 条；用户也可以把当前筛选后的列表导出为 Markdown。
4. 用户选择 Paper。中间区域按需请求 manifest 和指定长期产物，PDF 通过单独受控 URL 加载，不嵌入详情 JSON。
5. 页面重新加载时会重新读取 Library 和当前 Paper 详情；左右栏折叠状态只影响布局。 页面先取得当前用户身份，再恢复该用户保存的筛选条件、所选论文和阅读模式；恢复后的详情读取不自动启动 AI 研究任务。

Library 响应还提供 folder tree、可用筛选词表和研究工作流摘要，页面用这些辅助数据组织当前集合。

- **Library 包含当前用户可见的全部 Paper。** 服务端按页读取统一资源索引中的 Paper 记录，单次查询的 5,000 条上限不会截断列表；筛选、排序和收藏过滤都在这份完整集合上进行。 查询使用资源类型 `paper` 限定候选；`paper` 分类标签也用于 Review，不能用于决定 Library 中的论文身份。Review 继续通过所属 Paper 的研究产物入口展示，不单独计作论文。
- **文件夹树按 venue → 年份 → session 三层组织。** venue 和年份默认展开，session 默认折叠；用户点击节点切换展开状态，状态只保留在当前页面。展开的 session 列出其中的论文，可直接打开。
- **视口外的 venue 节点不参与首次布局。** 文件夹树可能有上万行，页面让滚动区域外的 venue 节点跳过布局与绘制，接近视口时再排版；显示内容和交互不受影响。

### 上传 PDF 并登记 Paper

1. 用户点击加号，上传一个不超过 512 MiB（536870912 字节） 且通过 PDF 内容验证的文件。
2. 后端创建只属于当前用户的登记草稿，以 `upload_id` 返回。状态使用 `queued`、`done` 或 `error`，并包含当前步骤、可编辑元数据和提取错误。
3. 提取器优先读取 PDF 中明确的 arXiv ID，缺失时再从文件名识别。正文标题优先于 arXiv 页眉噪声；模型补充不会覆盖已经识别的 identifier。作者列表只有读起来像署名时才保留：PDF 属性、正文首页和模型返回的作者逐项检查是否像人名（首字母大写的词、缩写首字母、姓氏前缀、2–8 字的中文姓名，脚注标记会去掉），不足八成像人名的列表整体丢弃，留空由用户填写，避免把正文句子登记为作者。
4. 用户检查并修正元数据，然后选择一个或多个关联 Project。登记也可以直接引用本地 PDF、本地 analysis 或 PDF URL；请求必须提供最小元数据和至少一种可用来源。
5. 后端逐个检查关联 Project 的 contributor 权限。受 Project scope 限制的用户至少要关联一个允许的 Project，否则返回 403。
6. PaperStore 在锁内按 arXiv、DOI、OpenAlex、标题 fingerprint 的顺序去重。索引过期时先从 manifest 重建，再进行比较。
7. 命中现有论文时返回原 Paper ID，并合并允许补充的元数据、来源、标签或关系。全部 fingerprint 都未命中时才分配新 `paper_*` ID。命中现有论文且没有带来新 PDF 时只合并，不重新启动摘要与资产任务；每日论文搜集用这一点把已有论文关联到新的 Project。
   每次写入 manifest 时，`relations.projects` 只保留项目登记中存在的 Project ID；`project_ids`、`session` 或已有关联里的其他名称不会成为项目关联。Project 改名或删除时由 `RenameProject`、`DeleteProject` 同步改写或去掉关联（`decision_4648f7fa3295`）。
8. 登记成功会消费草稿中的上传 PDF。草稿过期后不能继续登记，同一临时文件不会重复消费。

参数错误返回 400，Project 权限失败返回 403，文件超过 512 MiB（536870912 字节） 返回 413。下载或解析失败保留在草稿或登记结果中，用户可以修正元数据、改用其他来源或重新上传。

### 从 CCF 目录登记

1. 用户在左栏切到 `CCF A/B`，按 venue、年份或 issue 展开目录，也可以搜索 venue 或年份。
2. 列表标明目录条目是否已有本地 Paper。显式刷新会更新目录缓存。
3. 用户登记条目时，后端按相同身份规则复用或创建 Paper，并保留已有 `related_projects`。
4. 元数据和网页补全属于 CCF enrichment 后台任务。目录只显示它的投影；状态、缓存和错误由 `decision_e37f240e186d` 负责。

### 阅读并把上下文交给 Research Chat

1. 用户选择一篇 Paper 后进入 Reader。页面可以展示 Analysis 或 PDF，并保持两个标签页各自的滚动位置。
2. 用户选择文本并执行菜单动作。Add to Chat 只填入草稿，由用户决定何时发送。
3. 选中 Paper 时，页面向规范 Chat Workspace route 发送受控单篇上下文：Paper ID、标题、作者、venue、年份、bundle root、可用文件路径、摘要和 analysis 摘录。
4. 未选中 Paper 时，页面发送当前 Library collection：模式、查询和筛选，以及数量受控的候选 Paper。
5. CCF 等尚未登记的目录条目只有在用户显式附加时进入 Chat；它们不会因此获得本地 Paper 身份。

Paper 页面按本文的用户与论文绑定规则解析 Paper Lineage 内的 Research Chat Resident，再组装研究上下文；Chat Workspace/Resident 协议拥有消息、会话和执行生命周期。Translate、Explain、Find Ref 和 Regist Ref 等选区问答复用当前用户的这个聊天 Resident，不新增功能团队或专用翻译成员。

### 替换 PDF、只删 PDF 或删除 Paper

- Paper contributor 可以补传或替换当前 PDF。成功后更新 manifest 的当前文件并刷新派生文本状态，Paper ID 和其他长期产物不变。
- Paper contributor 可以只删除 PDF。后端同时删除派生文本 cache，并把 Paper 标为缺少 PDF；manifest、Reviews、Notes、Refs 和 Figures 保留。
- 远程 PDF 获取失败不会删除 Paper 身份。Contributor 可以稍后补传文件。
- 删除整个 Paper 会把完整 bundle 移入 Paper trash，并通知统一资源索引。Trash 中的条目不再出现在普通 Library。
- 读取不存在的 Paper 或 asset 返回 404。后端不会用相似论文或其他文件替代目标。

## 个人收藏

收藏帮助用户从可访问的论文库中保留自己的阅读清单。收藏的身份是已认证用户与稳定 Paper ID 的组合，个人状态由 Paper 收藏领域维护，使用 Portal 共享状态数据库持久化；刷新、更换浏览器或 Dev/Release 切换后仍可读取。收藏不属于 manifest、标签或共享论文产物，不修改 Paper 的 ACL，也不授予访问权限。账号个人数据清理同时删除其收藏。

列表、左侧目录和论文详情提供同一收藏开关。点击后保存明确的收藏布尔状态，重复请求保持幂等；同一页面的各入口同步显示，等待保存期间禁用该论文的重复操作，失败保留原状态并显示错误。收藏不会切换当前论文或重新加载阅读内容。

新增收藏必须验证调用者对目标 Paper 的当前读取权限；只读用户也能维护自己的收藏，无须论文 contributor 权限。身份仅从服务端认证上下文取得，忽略请求自报用户。取消收藏只操作调用者的个人记录，因此论文被删除或权限收回后仍可清理自己的失效收藏。

“只看收藏”与文本、年份、venue、tag、project、Today、Collector 和语义搜索结果共同过滤；它与当前可见论文取交集，保留原有相关性分数和顺序，不为收藏额外提升排名。Library 返回当前用户的 `favorite_ids` 供各入口显示；失效收藏 ID 可以保留，但不会使不可见论文的元数据进入结果。并发收藏写入由持久状态事务串行化，避免覆盖其他论文或其他用户的更新。

## 设计点

- **稳定身份贯穿所有入口。** Library、CCF 登记、详情、Research Chat 和后台任务都继续引用同一 Paper ID。
- **Manifest 拥有当前业务元数据。** Bundle 拥有文件事实；可重建索引只加速查询和去重。
- **Asset 读取不能逃出 bundle。** 绝对路径、父目录逃逸或未登记目标会被拒绝。
- **查询只覆盖当前用户可见的 Paper。** 筛选和排序不会改变 ACL，也不会产生第二份身份。
- **去重与合并在锁内完成。** Manifest、fingerprint 表和索引保持一致。
- **基础资源生命周期与后台运行分开。** 活动 job 不写入 manifest；只有成功形成的长期业务结果才进入 bundle 或更新当前状态。

## 组件怎样协作

| 组件 | 负责什么 | 故障后的处理 |
|---|---|---|
| Paper 页面 | 三栏布局、查询条件、当前 Paper、模式切换和可见反馈 | 刷新后重新读取 Library 与详情 |
| PaperStore | 登记、稳定身份、manifest、去重 fingerprint 和 bundle 生命周期 | 在锁内重建过期索引后继续去重 |
| 统一资源索引的 Paper 记录 | Library 的可见集合、筛选输入和语义搜索候选；每次查询按当前用户鉴权 | ResourceService 从全部 manifest 重新投影 |
| PaperStore 索引 | 本地登记匹配与 fingerprint 去重 | 从全部 manifest 重建 |
| Asset resolver | 在 viewer 权限和 bundle 边界内返回文件 | 缺失返回 404，越界拒绝 |
| Registration draft store | 保存当前用户上传、提取状态和可编辑元数据 | 错误留在草稿；过期后要求新建草稿 |
| Research Workspace | 按需请求 Review、Exp、Figure、Notes 和 Refs 等产物 | 各后台动作按自己的协议恢复 |
| Chat Workspace route | 消费 Paper 领域已解析并鉴权的 Resident 目标，读写其规范会话 | 由 Chat/Resident 协议恢复，不重复创建成员或发送初始化输入 |

```text
PDF 或外部来源 → 私有登记草稿 → 权限检查 → fingerprint 去重
                                   ↓
                      复用现有 Paper 或创建 paper_*
                                   ↓
                         manifest + bundle
                                   ↓
                     可重建索引 → Library → Research Workspace
```

这种结构让同一论文从不同来源登记时仍保持唯一身份；索引可以重建，长期文件不会依赖查询缓存；后台任务也可以围绕稳定 Paper ID 独立演进。

Research Workspace 按论文与正式产物类型查询内容。通过 Lineage 功能流程或授权工具提交的有效产物，与页面按钮产生的产物使用同一发现和显示规则；无需本页面先启动一个 job。页面依据领域变更重新查询，并在重新打开或刷新时重建内容。正式产物的关联、提交和入口无关更新由 `decision_e37f240e186d` 定义。

`review_*` 的注销由 Paper 领域接管，通过 `UnregisterResource` 调用已有 Review 删除流程，释放关联工作、数据库记录及评审文档。Paper 已级联删除的 Review 再次注销可以成功；权限拒绝、工作尚未释放或记录仍存在时保留失败，不能报告清理完成。

## HTTP 接口

- **Library 查询：** `GET /paper/api/library` 返回当前用户可见的 Paper 列表、filters、folder tree、workflow metadata 和个人 `favorite_ids`；`favorite=1` 将收藏与其他查询条件取交集。页面使用 `compact=1`：列表项只带显示与筛选所需字段，摘要截到 300 字，folder tree 只含各层名称和数量，session 下的论文由页面从列表分组得到。
- **个人收藏：** `POST /paper/api/favorites` 接受 `{paper_id, favorite: boolean}`，幂等设置当前已认证用户的收藏状态；新增检查读取权限，取消允许清理已不可访问的 Paper。
- **Paper 详情：** `GET /paper/api/papers/detail?id=&sections=` 返回指定 Paper 的 manifest 和所选长期产物；PDF 以受控入口表示，不内嵌字节。
- **创建登记草稿：** `POST /paper/api/papers/registration-draft` 校验 512 MiB（536870912 字节） 上限和 PDF 格式，创建当前用户私有草稿并启动元数据提取。
- **查询登记草稿：** `GET /paper/api/papers/registration-draft?upload_id=` 只向创建者返回状态、步骤、可编辑元数据和错误。
- **正式登记：** `POST /paper/api/papers/register` 校验来源与关联 Project，复用既有 Paper 或创建新 Paper。
- **CCF 登记：** `POST /paper/api/ccf/register` 把目录条目变为正式 Paper，复用或创建身份，并保留已有 `related_projects`。
- **上传或替换 PDF：** `POST /paper/api/papers/upload-pdf` 要求 contributor，保存当前 PDF 并刷新派生文本状态。
- **只删除 PDF：** `POST /paper/api/papers/delete-pdf` 要求 contributor，删除 PDF 和派生文本 cache，保留 manifest 与长期研究产物。
- **删除 Paper：** `POST /paper/api/papers/delete` 要求 contributor，把整个 bundle 移入 trash 并通知统一资源索引。
- **读取 asset：** `GET /paper/api/asset?id=&kind=&figure_id=&generated_id=` 校验 Paper viewer 和 bundle 路径后返回受控文件。
- **CCF venue：** `GET /paper/api/ccf/venues` 返回按类别和 rank 组织的 venue 数据。
- **CCF 年份：** `GET /paper/api/ccf/years?venue=` 返回指定 venue 可用的年份或 issue。
- **CCF 论文：** `GET /paper/api/ccf/papers?venue=&year=` 返回指定 venue 与年份或 issue 下的论文，并标记本地登记状态。
- **Paper API 文档：** `GET /paper/api/docs` 返回当前公开 HTTP 契约。

## 引用标题识别

引用正文先做统一换行、断词、编号和空白归一化，再按有顺序的规则表提取标题候选。规则覆盖 DOI 前标题、Open X 规范名、作者后引号标题、结构化作者/年份/venue 模式、typed marker、冒号和句子 fallback；先命中的有效候选结束识别。

所有规则候选必须经过同一个验证器。验证器负责清理 venue、URL、DOI、年份与尾部标点，并拒绝过短文本、纯标识符、作者列表和 venue tail。规则本身不能直接返回最终标题，也不保留另一套分支式识别器；新增格式通过插入具名规则和验证样例扩展。

## 页面 URL 的身份参数

Paper 页面与 Library API 的 URL 身份参数固定为 `paper`、`semantic` 和 `collector`；前后端不接受 `paper_id`、`search`、`semantic_id` 或 `collector_id` 别名。

登记成功并打开论文后，地址栏立即以 `paper` 参数保存返回的 Paper ID，与在 Library 中选择论文使用同一套地址同步规则。刷新页面或复制地址后仍能打开同一篇论文。
