---
kb_id: decision_3ece26686464
type: decision
project: AI员工
created_at: '2026-09-01'
version:
  number: 14
  created_at: '2026-09-01'
  created_by:
    user: ''
    agent: ''
  updated_at: '2026-10-19T08:31:20Z'
  updated_by:
    user: ''
    agent: resident_b1a8c2ce10fa
importance: 8
metaness: 2
ai_generated: false
links:
  source:
  - ${code_root}/management_portal/http_knowledge.py
  - ${code_root}/knowledge_board_lan/knowledge_board.html
  - ${code_root}/management_portal/knowledge_item_chat_backend.py
  - ${code_root}/management_portal/knowledge_archive_backend.py
  - ${code_root}/management_portal/resource_views.py
  - ${code_root}/management_portal/static/portal_preview_runtime.js
  - ${code_root}/management_portal/static/portal_workspace_surface.js
  - ${code_root}/knowledge_board_lan/knowledge_backend.py
  - ${code_root}/management_portal/resources/locking.py
  - ${code_root}/management_portal/resources/providers/knowledge.py
  - ${code_root}/management_portal/serve_management_portal.py
  - ${code_root}/tests/cases/test_knowledge_backend_entry_roots.py
  - ${code_root}/tests/cases/test_knowledge_http_workflow.py
  - ${code_root}/tests/cases/test_knowledge_resource_provider.py
  - ${code_root}/tests/cases/test_knowledge_versioning.py
  related:
  - decision_ad3e21094f14
  - decision_61e50306cc79
  - decision_b6e74d78f7b6
  - decision_8dc6b3d03ca7
  - decision_4a9ba5dfada6
  supersedes: []
  derived_from: []
---
# Knowledge Resource 的登记、版本与受控修改

## 这项设计解决什么问题

Knowledge Resource 是 Portal 统一资源体系中的“项目知识条目”。它让人和 Agent 可以用一个不会随文件改名而变化的地址，找到项目知识库中的一份正式知识，查看其正文与伴随文件，并在权限和版本约束下修改或删除它。

本篇负责解释 Knowledge Resource 怎样取得身份、怎样呈现在 Knowledge 页面、正文修改怎样形成版本、冲突怎样反馈，以及删除的边界。私有 AI 对话由 decision_58db6282c244 负责；尚未入库材料的整理与应用由 decision_4666241592d4 负责。本篇只在需要理解入口时说明两者。

## 先认识页面和资源

### Knowledge Resource

一个 Knowledge Resource 由一份位于 Project 的 `knowledge_base` 目录中的规范 Markdown 主文件定义。Project 是 Portal 登记的项目；`knowledge_base` 是该项目保存正式知识的目录。Markdown 是带有结构化文首信息和正文的纯文本格式。

主文件文首的 `kb_id` 是统一资源 ID，例如 `decision_3ece26686464`。ID 同时表达资源类型和稳定地址：`decision` 表示 Design 的兼容类型名，后面的十二位小写十六进制码保证全局唯一。标题、文件名、目录名和路径可以改变，ID 不变。

主文件所在目录还可以包含图片、PDF、代码和其他伴随文件。主条目称为资源根；伴随文件用 `资源ID/file/相对路径` 这样的嵌套地址读取。嵌套地址没有独立身份、版本或删除权，它们始终归资源根所有。

知识索引收录以下知识类型：`meeting`、`question`、`idea`、`insight`、`decision`、`plan`、`experiment`、`thinking`、`risk`、`concept`、`information`、`patent`、`material`、`skill`、`todo`、`review`。其中 Experiment 和 Skill 还有各自的资源工作台与专用登记规则；Knowledge 列表收录 TODO 与 Review 知识文件，详情使用同一个知识资源宿主；Skill 与 Experiment 的专用详情由各自工作台负责。

### Knowledge 页面可见区域

Knowledge 页面是人查看普通知识的工作台，主要区域如下。

| 区域 | 读者看到什么 | 用途 |
| --- | --- | --- |
| 顶部工具栏 | 搜索框、语义搜索按钮、漏斗形筛选按钮、Archive 按钮、刷新按钮，以及列表与关系图图标 | 查找、筛选、归档输入、刷新或切换浏览方式 |
| 列表区 | 按条件得到的知识卡片或列表项；卡片显示标题、类型、Project、摘要和状态信息 | 快速判断一条知识是否相关并打开详情 |
| 筛选摘要 | 当前日期、状态、类型、Project、重要性和语义任务条件 | 解释当前结果为什么被保留或隐藏 |
| 关系图区 | 节点画布、搜索与建议、Project/类型/关系筛选、只看相连项与邻居开关、缩放和适配视图控件 | 从引用关系浏览知识网络 |
| 详情层 | 顶部身份与操作区、左侧文件树/关系图、中部正文预览或编辑器、右侧私有 Chat | 阅读一个资源及其文件，编辑正文，或围绕它协作 |

“卡片”是列表中的一个可点击摘要；“详情层”是在当前页面之上打开的资源详情；下载图标下载当前选中的文件，铅笔图标进入编辑，垃圾桶图标请求删除，叉号关闭详情。Chat 是当前用户私有的 AI 会话，身份、历史隔离和发送流程统一见 decision_58db6282c244。

## 设计点

### 发现与阅读

- Portal 只列出当前用户有权读取的资源。搜索、筛选和关系图不会绕过 Project 或资源访问权限。
- 点击卡片后，Portal 解析稳定 ID，再读取当前主文件。文件名或目录变化不会要求用户换一个资源地址。
- 点击文件树中的伴随文件时，Portal 在资源根的目录边界内解析相对路径。越界路径、隐藏路径和不存在的文件不会被当作资源内容返回。
- 文本和代码显示为可复制文本，图片与 PDF 使用相应预览；不适合文本编辑的音频、二进制文件、目录、图片、Office 文档、PDF、远程链接和视频只提供读取或预览。
- 当前选中文件具有 `download_url` 时，详情顶部（Header）显示下载图标。点击后在新的浏览上下文中请求该地址，不替换当前 Portal 页面。
- 下载请求重新经过 ResourceService 的当前 actor 授权，并使用内容解析结果中的规范原文件名。按钮链接属性由 Preview Runtime 的共享下载契约配置，详情页不复制文件名或安全属性算法。
- 关系图只使用主文件中明确声明并能解析的关系。目录中的任意字符串或未声明文件不会自动变成知识关系。

### 登记

- 登记把一份符合规范的 Markdown 主文件接入统一资源服务。ResourceService（统一资源服务）先保留新 ID，再由 Knowledge provider 创建或确认主文件，成功后写入登记记录。
- 通用 Markdown 登记只接受 provider 支持且允许登记的知识类型。Skill 与 Experiment 走各自 provider，避免两套组件同时拥有同一目录。
- 主文件必须使用本次保留的 ID；已有另一个 ID、重复 ID、非法类型、非法 Project 或越出项目知识库的路径都会使登记失败。
- 同一资源再次以相同幂等请求到达时，服务返回已经登记的身份，不另建副本。失败的登记不会留下可被误认为成功资源的服务记录。

### 修改与版本

- 页面编辑只改变当前打开的文本内容。保存成功后，详情重新展示服务端保存的版本；保存失败时，页面保留当前编辑稿并给出错误，用户可以修正或重试。
- ResourceService 的受控更新要求调用方同时提供资源根 `address`、完整 `text` 和可选的 `expected_version`。`expected_version` 是调用方开始编辑时读到的版本记录。
- 提供 `expected_version` 时，服务只在它仍等于当前版本时写入。版本已经前进会返回版本冲突，并附带期望版本和当前版本；调用方应重新读取、合并自己的改动，再提交新期望版本。
- 每次成功修改主文件或其伴随文本，资源根的版本号加一。版本属于主资源，伴随文件不维护独立版本号。
- 同一根资源的受控登记、更新和删除必须串行提交；不同根资源可以同时写入，慢写入不会阻塞另一条资源。服务以规范根资源 ID 选择跨进程资源锁，主文件、伴随文件和版本记录共用该锁。
- 版本比较与文件写入位于同一个资源锁内。因此两个同时携带相同 `expected_version` 的更新只有一个能够提交，后一个会读取到已经前进的版本并返回冲突。
- 历史主文件缺少版本记录时，读取不会编造创建者。第一次受控写入会建立规范版本记录。
- 当前 Knowledge 页面保存接口只发送资源地址和正文，没有发送 `expected_version`。因此页面层采用最后一次成功保存的内容；需要防止并发覆盖的 Agent 或接口调用方必须使用 ResourceService 的受控更新能力。
- 用户或外部程序直接写底层文件时不会取得 ResourceService 的资源锁，也不会自动递增版本。文件监测与索引对账会重新发现其结果，但不能把这种旁路写入包装成受控并发事务；需要冲突保护和完整审计的修改必须通过 ResourceService。

### 已版本化正文与受损版本恢复

已经具有版本记录的 Knowledge 主文件，受控更新必须提供带 YAML 元数据的完整 Markdown。只提交正文、空 YAML 或无法解析的 YAML 时，写入被拒绝，原文件和版本保持不变。提交文本中的版本号和创建审计不能替代服务端锁内读取的前一版本；只省略完整头中的 `version` 块时，服务仍按原版本递增并保留原创建审计。未版本化的普通文本、伴随文件和 Skill 继续遵守各自既有写入规则。

已有受损知识版本可以通过同一 `UpdateResource` 路径恢复，Knowledge 更新字段增加可选 `version_recovery_change_ids`：按发生顺序提供一至八个不同的服务端内容变更回执 ID，并且必须同时提供当前 `expected_version` 和逐字不变的当前完整 `text`。此字段只适用于 Knowledge Markdown 主文件，不接受客户端声明的历史版本下限或任意历史内容。正常资源写入身份与授权检查不变；回执 ID 本身不授予资源或他人执行回执的读取权限。

服务在同一资源锁内按唯一 ID 有界读取已保存回执，验证真实资源 ID、根地址、文件路径、回执身份与顺序、前后正文摘要和版本记录。连续内容链必须结束于当前主文件的精确摘要，且已有记录必须证明历史版本高于当前版本。恢复只把本次写入的版本推进到可证高水位加一，保留当前正文及其它元数据，并从可证版本保留原创建审计；它不回放旧正文、不改写旧回执，也不为受损时期补造中间版本。证据缺失、断链、跨资源、摘要失配、版本冲突或不存在真实回退时全部拒绝写入。成功恢复后旧 CAS 请求不能再次提交；有执行关联的正常新内容变更回执保存所使用的恢复回执 ID。

### 删除

- 删除只接受资源根 ID。嵌套文件不能作为独立资源删除。
- 删除需要页面确认和服务端权限检查。普通知识条目被移入所属 Project 的 `knowledge_base/.trash`，以便运维恢复，而非立即物理清除。
- 通用 Knowledge 页面保护 `article` 与 `experiment`，不显示其普通删除动作。Skill 的删除由 Skill provider 负责整个包；Experiment 的退役由 Experiment provider 负责整个实验目录。
- 资源提供者在完成权限与业务检查后、移动文件前，按资源根 ID 调用统一投影生命周期服务，释放该资源所有用户的专属 Resident、Event、Effect、连线、角色绑定和创建关联。Skill 包也遵循此规则；共享团队与公共功能成员保留。清理失败时保留源资源，调用方可重试；已完成的部分无需重建。协议见 decision_f67ae5e70793。
- 删除成功后，Portal 刷新列表和详情状态。删除失败时，原资源和登记仍保持可解析，页面显示错误。

## 组件各自负责什么

| 组件 | 责任 | 不负责的事项 |
| --- | --- | --- |
| Knowledge 页面 | 呈现列表、关系图、详情、编辑反馈和删除确认 | 不直接决定文件身份或越权读写 |
| HTTP 路由层 | 取得当前登录用户、校验输入和角色、把异常转换为状态码 | 不直接扫描知识目录 |
| ResourceService | 统一解析 ID、执行访问控制、保留登记身份并协调 provider | 不解释 Markdown 内部结构 |
| Knowledge provider | 把统一的登记、读取、更新、删除调用转换成知识后端操作 | 不拥有私有聊天或共享留言状态 |
| Knowledge backend | 扫描规范主文件、解析嵌套地址、按根资源隔离并发、维护版本、原子写入和移入回收目录 | 不把未归档输入自动写入项目 |

一次受控修改按下面的顺序流动：

```text
调用方读取资源与版本
        ↓
ResourceService 校验身份、权限和资源根
        ↓
Knowledge provider 校验 address、text、expected_version 和可选恢复回执
        ↓
Knowledge backend 取得根资源锁，比较当前版本并原子写入
        ↓
版本前进，索引与页面读取新内容
```

根资源锁保证一条资源内的正文、伴随文件和版本共同变化，同时让不同资源互不等待；版本前提让并发修改显式冲突；原子写入避免读取到半份 Markdown；ResourceService 保留身份，使路径调整后仍然可以使用同一个 ID。

## 精确接口与白盒契约

### 资源指令

Agent 和服务通过 HATG 资源指令读写 Knowledge（decision_ad3e21094f14）：`SearchResources`、`ResolveResource`、`ReadResource` 读取，`RegisterResource`、`UpdateResource`、`UnregisterResource` 登记、修改和删除。登记和修改需要编辑权限（contributor），删除需要管理权限（admin）。这些指令由 ResourceService 执行。`UpdateResource` 对 Knowledge 的 `changes` 只接受：

| 字段 | 含义 |
| --- | --- |
| `address` | 资源根或归属于该根的可修改地址；其根段必须等于本次 `resource_id` |
| `text` | 要保存的完整 UTF-8 文本，必填 |
| `expected_version` | 可选版本前提，可传版本号或包含 `number` 的版本对象；执行版本恢复时必填 |
| `version_recovery_change_ids` | 可选的有序内容变更回执 ID 列表；只用于上文规定的受损版本恢复，必须同时提交当前版本和逐字不变的完整文本 |

出现未知字段、缺少 `text`、地址根不匹配或版本冲突时，provider 拒绝更新。版本冲突被统一转换为 `ResourceVersionConflict`，其中保留 `expected_version` 与 `current_version`。

### 变更结果的规范项目归属

Knowledge backend 的登记、更新与删除结果都必须携带规范的 `project_ids`，经 Knowledge provider 原样传给 ResourceService；其项目集合与同一根资源的 `ResolveResource` 结果一致。已有的 `project` 仍是领域内字段，不代替授权模块要求的规范归属。解析和变更结果共用项目归属转换，嵌套文件修改沿用根资源的项目归属；Skill 的受控修改与删除也保留其解析记录中的项目归属。

ResourceService 在 provider 完成写入后，用规范归属同步授权资源：登记建立 Project 父关系和实际创建者的管理凭证，再发布变更通知。Knowledge 不通过放宽共享授权解析来容忍缺失归属。对已经落盘但授权同步失败的历史资源，应通过受控授权修复补齐关系和原创建者凭证，不重放正文写入、不另建资源、不额外推进 Knowledge 版本。

### 当前页面接口

| 请求 | 可观察结果 |
| --- | --- |
| `GET /knowledge/api/index` | 返回当前普通 Knowledge surface 的可见索引 |
| `GET /knowledge/api/item?kb_id=<地址>` | 返回单条公开记录 |
| `GET /knowledge/api/detail?kb_id=<地址>` | 返回详情、文件、预览描述，以及当前文件的 `download_url`；媒体另有不强制下载的 `raw_url` |
| `GET /knowledge/api/graph` | 返回当前用户可见的知识关系图 |
| `GET /knowledge/api/item-graph?kb_id=<地址>` | 返回一个资源周围的关系图 |
| `POST /knowledge/api/detail/save` | 以 `kb_id` 和 `text` 保存页面编辑 |
| `POST /knowledge/api/detail/delete` | 以资源根 `kb_id` 请求删除 |

缺少参数或非法地址返回 `400`；资源不存在返回 `404`；无权读取或修改返回 `403`；后端不可用或未处理故障返回 `500`，由相应路由呈现错误。页面提供列表、详情、关系图、编辑和删除；登记和带版本前提的更新只通过资源指令进行。

## 状态归属与恢复

资源身份与访问控制由 ResourceService 拥有；主文件、伴随文件和规范版本记录由 Project 知识库拥有；页面筛选、当前详情和未保存编辑稿只属于浏览器会话。私有 Chat 状态由对应 Resident Workspace 拥有，遵循 decision_58db6282c244。

服务重启后，Knowledge backend 会从规范主文件重新扫描资源；ResourceService 的登记用于维持统一发现。原子写入失败时旧文件仍可读。版本冲突不自动合并，调用方重读后只重放自己的修改。移入 `.trash` 的删除可由运维从原 Project 边界恢复，但 Portal 没有面向用户的一键恢复控件。

## Knowledge 输入字段

Knowledge 的当前契约使用单一字段：登记正文只接受 `body`，版本号只读取 `version.number`，文件定位只读取 `relative_path`。

## 界面与读取约束

图谱索引读取保留当前访问者，缓存按访问者隔离，返回前复核可见节点与关系。

TODO 文件使用 front matter 的 `id` 作为稳定身份，其余知识文件使用 `kb_id`。TODO 的指派人、任务状态和归档时间随知识文件保存，格式由 `${share_root}/Skills/manage-knowledge-base/archive-todo-item.md` 定义。读取和搜索保留 TODO 与 Review 的资源类型，并共同归入 `knowledge` facet。
