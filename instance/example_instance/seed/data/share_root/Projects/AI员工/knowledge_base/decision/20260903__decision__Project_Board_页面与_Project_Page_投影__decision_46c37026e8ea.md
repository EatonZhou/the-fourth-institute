---
kb_id: decision_46c37026e8ea
type: decision
project: AI员工
created_at: '2026-09-03'
version:
  number: 8
  created_at: '2026-09-03'
  created_by:
    user: ''
    agent: ''
  updated_at: '2026-10-19T08:27:19Z'
  updated_by:
    user: ''
    agent: resident_96243cc26373
importance: 9
metaness: 2
ai_generated: true
links:
  source:
  - ${code_root}/management_portal/resources/isa.py
  - ${code_root}/management_portal/http_projects.py
  - ${code_root}/project_board_lan/projects_board.html
  - ${code_root}/management_portal/static/project_board.js
  - ${code_root}/management_portal/static/project_page.js
  - ${code_root}/management_portal/project_state.py
  related:
  - decision_e5f66e2f6a5e
  - decision_769a35d6b5c7
  - decision_6591e7793116
  - decision_9b94f218a188
  - decision_4648f7fa3295
  - decision_b6e74d78f7b6
  - decision_61e50306cc79
  - decision_538e155e1102
  - decision_9f6135cbbb22
  supersedes: []
  derived_from: []
---
# Project Board 页面与 Project Page 投影

Portal 的 Project 区（地址 `/projects/`）有两层页面。**Project Board（项目列表）**列出当前用户能看到的全部项目，让用户搜索、筛选、调整状态和截止时间，并展开一段简短的项目摘要。**Project Page（项目详情）**是单个项目的页面，在同一个地址下组合五个视图：项目状态概览、研究路线图（Research Map）、可编辑的项目说明（Summary）、项目对话（Chat）和资源预览（Preview）。

本篇说明两层页面的区域、它们从哪里取得数据、地址和浏览器历史怎样对应，以及有未保存修改时怎样处理离开。项目的登记、创建和删除见 decision_4648f7fa3295；项目状态文件（Project State）的格式、读取和校验见 decision_9b94f218a188。本篇只规定浏览器怎样读取和显示它们。

## 基本概念

| 名称 | 含义 |
| --- | --- |
| 项目登记 | ResourceService 中每个项目的登记记录：项目 ID、名称、说明、状态、类型、截止时间（DDL）、标签和目录。项目列表由它生成。 |
| Project State | 项目目录中由 Agent 或人维护的结构化状态文件，描述项目的方向、交付、计划、人员和证据。 |
| 项目详情合同 | 服务端把 Project State 整理成页面可以直接显示的结构，版本为 `portal.project-page.v2`。它带有数据状态：`ready`（完整）、`partial`（部分可用）、`missing`（没有状态文件）、`invalid`（文件无效）或 `error`（读取出错）。 |
| 项目摘要（brief） | 从项目详情合同中取出的一段短概述，显示在 Project Board 展开的项目下方。 |
| Summary | 项目目录中人工撰写的 `summary.md`，在详情页中可以编辑。 |

## 用户看到的页面

### Project Board

- **页头**显示“项目总览”，以及全部项目数、进行中的项目数、待推进的项目数（状态为已暂停或未开始），筛选生效时另显示当前显示的数量。工具栏中有“新建项目”按钮。
- **搜索与标签筛选**：搜索框在项目 ID、名称、说明、状态、类型、目录和标签中不区分大小写地查找；标签筛选要求项目的标签中包含所选标签。
- **项目行**：每个项目一行，显示标题（没有名称时显示项目 ID）、类型、说明、标签、状态下拉框、截止时间按钮和“进入项目”按钮。点击标题展开这个项目的摘要，同一时间只展开一个项目，各行宽度保持一致；展开控件同步 `aria-expanded` 与 `aria-controls`。
- **项目摘要**：显示概述、当前进展（PROGRESS）、需要关注的事项（NEEDS ATTENTION）和下一步，以及交付能力、参与的 Resident、需要人工审查的事项和冲突提示。项目状态文件缺失、无效或读取出错时，显示原因，并提供打开 Summary 和 Chat 的入口。

状态显示为“未开始、进行中、已暂停、已完成、已归档”，类型显示为“公司项目、学术项目、个人项目、其他”。

截止时间按钮按以下规则显示：没有设置时显示“设置截止时间”；无法识别时以危险色显示“日期无效”；当天显示“今天截止”，未来显示“还剩 N 天”（7 天以内用警告色），已过显示“已逾期 N 天”（危险色）。点击按钮弹出输入框，可以修改或留空清除。

### Project Page

- **概览**（Overview）把项目详情合同分成五段：一眼看懂（`overview`）、交付进度（`delivery`）、下一步（`plan`）、协作团队（`people`）、证据与认知（`evidence`），每段可以通过地址直接定位。
- **Research Map** 展示项目的研究主题、方向与任务，支持选择地图和打开资源。
- **Summary** 显示并编辑 `summary.md`。
- **Chat** 是这个项目的对话，使用共享的 Chat Workspace（decision_f560803947c6）。
- **Preview** 打开概览中引用的证据文件，以及 Research Map 的主题和任务资源。

详情页按数据状态显示不同内容：`ready` 显示完整概览；`partial` 显示已有内容并列出缺少的段落；`missing`、`invalid` 和 `error` 显示原因，并提供 Summary 和 Chat 入口；项目不存在时显示“Project not found”和返回项目列表的入口。

## 设计点

### 列表与摘要

- **项目列表只包含当前用户能看到的项目。** 页面读取 `GET /projects/api/projects`，结果中的项目和状态选项（`status_vocab`）来自项目登记，已按当前用户的权限过滤。搜索和标签筛选只在这个集合中进行。
- **展开项目时才读取它的摘要。** 同一项目尚未返回的读取合并为一次，读到的结果留在页面缓存中，再次展开不重新读取。用户快速切换展开的项目时，较早的响应仍写入对应项目的缓存，但只有这个项目仍处于展开状态时才更新页面。
- **一个项目读取失败只影响它自己。** 项目列表读取失败时，页面显示错误和重试入口；单个项目的摘要读取失败只影响这一行，其他行、筛选和缓存保持不变。

### 修改项目

- **用户在列表中修改状态和截止时间，在详情页修改类型，页面立即显示新值。** 页面随后调用 HATG ISA 指令 `UpdateProject`（decision_6591e7793116），`changes` 中只包含这一个字段。服务端拒绝时，页面恢复原值，并在页面顶部显示“保存失败：”加原因。是否允许修改由权限系统按当前方案判断；`UpdateProject` 默认要求对该 Project 有 admin，具体合同见 decision_6591e7793116 和 decision_4648f7fa3295。页面不再以 Project 成员角色推断权限。
- **新建项目**：用户点击“新建项目”，填写项目 ID、说明、类型、状态、标签和截止时间后提交。页面通过资源登记（`RegisterResource`，资源类型 `project`）创建项目，成功后刷新列表并打开新项目的详情页。创建规则见 decision_4648f7fa3295。

### 详情页的数据

- **Overview 和 Summary 通过项目指令读取。** 页面调用 `GetProject`，结果中的 `state` 是项目详情合同，`summary` 是 `summary.md` 的内容；目标不存在时返回 `instruction.not_found`，页面显示“Project not found”。
- **切换视图共用同一份项目数据。** 在概览、Research Map、Summary、Chat 和 Preview 之间切换不重新创建项目数据。Summary 的草稿由文档会话（decision_769a35d6b5c7）管理，Chat 的对话由 Chat Workspace 管理，文件显示由预览组件（decision_61e50306cc79）负责，详情页只负责组合它们。
- **保存 Summary 使用 `UpdateProject` 的 `summary` 字段**，用完整文本替换 `summary.md`，保存后重新读取。

### Research Map

Research Map 用主题（Topic）组织一个顶层研究问题，用方向（Direction）描述候选路线，用任务（Task）关联具体研究工作。主题和方向保存在项目 `knowledge_base/plan/` 下的 Markdown 文件中，任务引用独立的 `todo_*` 资源。方向状态属于地图正文，任务标题与状态属于通用资源记录。

- **每个项目都有 Research Map 标签。** 首次打开时读取该项目的地图；没有可读地图时显示“这个项目还没有 Research Map”。重新打开标签复用已读取结果，刷新项目后重新读取。
- **多张地图通过选择器切换。** 选择按用户和项目保存到共享 Page State；保存的地图已不存在时选择返回结果中的第一张。地图按修改时间从新到旧排列。
- **流程摘要显示当前地图的范围和完成情况。** Topic 是当前主题数，固定为 1；Direction 是方向数；Task 是去重后的任务 ID 数；Synthesis 是状态为 `done` 的方向数。
- **主题卡片显示标题、摘要、主题描述与背景边界。** 点击卡片，以 plan 的 `kb_id` 在本页 Preview 中打开源文件。
- **方向卡片按状态着色并列出任务。** 方向允许 `candidate`、`planned`、`doing`、`done`、`paused`。任务优先显示通用资源记录中的标题与状态；资源缺失、无读取权限或没有状态时，以任务 ID 和地图中的 `map_status` 补足显示。没有任务的方向显示“尚无研究任务”。点击任务，以 `todo_*` 地址打开 Preview，资源读取失败由 Preview 显示原因。
- **地图加载失败保留可重试的错误。** 请求失败和无地图分别显示，不把失败解释为空数据。失败局限在 Research Map 面板，其他视图继续可用；切换项目后，较早返回的地图不会覆盖当前项目。

页面通过 `GET /projects/api/research-maps?project=<项目ID>` 按需读取。这延续 Project 的 HTTP 读取入口，使地图扫描和失败处理独立于 Overview 使用的 `GetProject`。服务按当前会话 actor 检查 Project 读取权限，再扫描该项目 plan 目录，识别正文中的 `**Research Map: …**`，复用知识库 front matter 解析器，以 `kb_id` 作为地图唯一身份。地图与任务读取继续遵守各自资源权限；通用资源服务提供任务标题和 `attributes.status`。解析和呈现不依赖 TODO 看板、任务运行元数据或个人看板状态。

### 地址、历史与未保存的修改

- **每个视图有自己的地址。** 列表为 `/projects/`，详情为 `/projects/<项目 ID>`；地址的 hash 为 `research`、`summary`、`chat` 或 `preview` 时打开相应视图，为 `project-section-<段落>` 时打开概览并定位到这一段。从列表打开详情时新增一条浏览器历史，后退、前进和直接打开链接都恢复对应的视图。
- **Summary 有未保存的修改时，离开前先确认。** 关闭详情、浏览器后退或前进、在页面内切换视图，都先交给文档会话询问是否放弃修改。用户选择留下时，页面恢复原来的地址和视图；确认离开后才前往新位置，地址与看到的内容始终一致。
- **页面的界面状态按页面保存。** 搜索词、标签、展开的项目及每个项目选中的 Research Map ID通过 `PortalPageState.create("projects", …)` 保存和恢复（decision_9f6135cbbb22）。

## 数据与呈现的归属

Project 页面控制器持有唯一的项目详情缓存，并合并同一项目尚未完成的读取。Project Board 通过 `loadProjectState(projectId)` 使用这份缓存；列表和详情概览共享同一份结果。刷新由页面控制器清除缓存后重新读取，列表组件只保存项目集合、筛选和展开状态。

列表摘要与详情概览通过 `PortalProjectSummary` 共同生成进展、需关注事项、下一步和信号指标。共享呈现负责值的归一化、缺失说明、转义与状态色；两种布局只选择紧凑或完整外观。日期统一通过 `PortalUI.formatDateTime` 遵守界面语言。

## 组件接口

### `PortalProjectBoard`

`PortalProjectBoard.create(options)` 创建列表控制器，必须提供 `root`（根元素）和 `loadProjectState(projectId)`（读取一个项目的详情合同），否则抛出错误。可选回调有 `onExpandedChange`、`onOpenProject`、`onStatusChange` 和 `onVisibleChange`。控制器只在用户选择状态时调用 `onStatusChange`，保存由页面负责。

| 方法 | 作用 |
| --- | --- |
| `setProjects(projects, config)` | 用新的项目集合替换当前集合并重新显示。 |
| `setFilters({ query, tag })` | 同时设置搜索词和标签。 |
| `render()` | 按当前集合、筛选和展开状态显示。 |
| `snapshot()` | 返回当前项目、筛选和展开状态。 |
| `root` | 控制器使用的根元素。 |

控制器没有 `destroy`。

### `PortalProjectPage`

`PortalProjectPage.create({ root, onRequestView, onAnchor, onRendered })` 创建详情概览：`onRequestView` 在用户点击“打开 Summary”“打开 Chat”这类入口时切换视图，`onAnchor` 在用户点击段落链接时更新地址，`onRendered` 在每次显示完成后调用。

| 方法 | 作用 |
| --- | --- |
| `render(contract)` | 按项目详情合同的数据状态显示。 |
| `renderLoading()` | 显示加载中。 |
| `renderError(error)` | 显示错误和恢复入口。 |
| `scrollTo(sectionId)` | 定位到五段中的一段。 |
| `snapshot()` | 返回当前显示的状态。 |
| `root` | 根元素。 |

概览中的资源引用和文件链接使用资源引用标记（decision_e5f66e2f6a5e）与文件链接组件显示，概览本身不直接读取后端。
