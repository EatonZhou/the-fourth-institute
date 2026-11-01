---
kb_id: decision_46c37026e8ea
type: decision
project: AI员工
created_at: '2026-09-03'
version:
  number: 14
  created_at: '2026-09-03'
  created_by:
    user: ''
    agent: ''
  updated_at: '2026-10-31T01:31:09Z'
  updated_by:
    user: ''
    agent: resident_73cb83291316
importance: 9
metaness: 2
ai_generated: false
links:
  source:
  - ${code_root}/management_portal/http_projects.py
  - ${code_root}/management_portal/projects.html
  - ${code_root}/management_portal/static/project.js
  - ${code_root}/management_portal/static/project_list.js
  - ${code_root}/management_portal/static/project_home.js
  - ${code_root}/management_portal/static/project_settings.js
  - ${code_root}/management_portal/static/project_common.js
  - ${code_root}/management_portal/resources/isa.py
  - ${code_root}/management_portal/society_service/project_domain.py
  related:
  - decision_4648f7fa3295
  - decision_6591e7793116
  - decision_3e07c6d35cac
  - decision_15aab18c0ab3
  - decision_f560803947c6
  - decision_e5f66e2f6a5e
  - decision_9f6135cbbb22
  derived_from: []
---
# 项目页：项目列表、项目页与 Project Resident 对话

项目页是人查看和管理 Project 的地方。一个 Project 的知识、团队、资源和研究工作分散在 Society、资源页和工作台中，项目页把它们汇总在一处：列表里看每个项目是否在动、谁在做；进入一个项目，看它的概况、团队、资源和最近变化，并在侧栏和这个项目的 Project Resident 对话。页面上的每个操作都是一条 HATG 指令，所以对 Project Resident 说一句话，也能完成同样的操作。

页面不读取需要人另外维护的格式化状态文件。它显示的内容全部来自系统已有的正式对象：项目登记、`summary.md`、Lineage 与 Resident、资源索引。

## 地址

| 地址 | 内容 |
| --- | --- |
| `/projects` | 项目列表 |
| `/projects?project=<项目 ID>` | 一个项目的项目页 |
| `/projects?project=<项目 ID>&view=map` | 这个项目的研究地图（decision_9d15ae2053c4）；带 `&direction=<方向 ID>` 时选中该方向 |
| `/projects?direction=<方向 ID>` | 解析方向所属的项目后，换成上一行的地址 |

它们是同一个页面，地址是页面唯一的状态：打开链接、前进和后退都显示地址所指的内容；在地图上选中或关闭方向时，地址中的 `direction` 随之更新。项目页的地址也是 `project` 资源的 Portal 地址，资源标签、搜索结果和导航里的 Project 链接都打开它；方向资源的 Portal 地址是 `/projects?direction=<方向 ID>`。

## 项目列表

- **每个项目一张卡片**，显示头像、项目 ID、类型、状态（下拉框，可直接修改）、说明前两行、标签和三项信号：正在工作的 Resident 数、团队（普通 Lineage）数、最近一次变化距今多久。未结束的项目还显示 DDL：没有截止时间时不显示；未来显示“还剩 N 天”，七天以内用警告色；当天显示“今天截止”；已过显示“已逾期 N 天”；无法识别显示“日期无效”。
- **卡片的左边条和底色随最近变化变亮**：刚有变化的项目最亮，一个月没有变化逐渐褪成边框色。
- **按状态分三组**：进行中；未开始与暂停；已完成与归档。最后一组默认折叠。组内按最近变化从新到旧排列。
- **筛选**：搜索覆盖项目 ID、说明和标签；可按类型和标签筛选，点击卡片上的标签也按它筛选。搜索词、筛选和折叠状态按用户保存在 `PortalPageState("projects")`（decision_9f6135cbbb22）。
- **改状态**：选择后立即调用 `UpdateProject`，`changes` 只含 `status`；服务拒绝时恢复原值并提示原因。
- **新建项目**：打开 Society 也使用的同一个创建对话框（`PortalProjectCreation`），填写项目 ID、类型、说明、状态、标签和截止时间，调用 `CreateProject`。创建成功后打开新项目的项目页。

页头显示项目总数、进行中的项目数和全部正在工作的 Resident 数。

## 项目页

### 页头

页头是一块紧凑的卡片：头像右侧第一行是项目 ID，同一行接着类型、状态（可直接修改）、DDL 和标签，第二行是说明，右侧有“复制链接”和菜单。菜单中的三项操作：

- **编辑信息**：在一个对话框中修改说明、状态、类型、截止时间、标签和头像。只提交改动过的字段，一次 `UpdateProject`；头像由头像编辑器单独保存。
- **改名**：输入新 ID 后先以 `dry_run` 调用 `RenameProject`，列出目录的移动、改写的 Portal 数据行数、暂停接单的 Resident、随之改用新 ID 的共享库对象，以及提到旧目录、需另行检查的服务脚本。有 Resident 正在执行或目录被 Portal 之外的挂载与服务使用时，不能继续；检查通过后按钮变为“确认改名”，再次提交才真正改名，页面随后换到新 ID 的地址。修改了新 ID 会重新检查。
- **删除**：对话框说明将删除的 Lineage 与 Resident 数量、项目目录文件的去留和资源登记的注销；输入与项目 ID 完全相同的文字后才能提交 `DeleteProject`。删除成功后回到项目列表。

### 统计

页头最后一行以分隔线隔开，并排四项数字：团队数、Resident 数（有正在工作的 Resident 时注明数量）、资源总数和最近一次变化距今多久。它们不单独占卡片，整个页头约 130 像素高。

### 研究地图

页头下方第一张卡片是研究地图的入口：方向树的缩略图（放弃的方向不画）、各姿态的方向数、方向总数、最近修订号和修订时间。卡片以 `GetResearchMap`（`include_outputs` 为 false）读取地图；还没有方向时，卡片邀请人开始绘制。点击打开地图页，地图页的工作方式见 decision_9d15ae2053c4。

### 概况

渲染后的 `summary.md`。点击“编辑”在原位编辑全文，保存时以 `UpdateProject` 的 `summary` 字段整体替换。有未保存的修改时，离开页面、在页面内跳转或浏览器后退都先确认；选择留下时地址回到当前项目。没有 `summary.md` 时提示用户撰写，或请 Project Resident 起草。

### 团队

- **Project Resident** 单独置顶：它是 Project 层 Lineage 的 Leader（decision_3e07c6d35cac），协调项目里各 Lineage 的 Leader。卡片显示它的实时状态和当前重点，并能打开 Project 层 Lineage 的 HATG。项目主体尚未初始化完成时，这里说明 Project Resident 尚未就绪。
- **每个普通 Lineage 一张卡片**：头像、名称、成员数、正在工作的成员数、Leader 工作摘要中的当前重点和最近一条成果、Leader 的名称与实时状态（正在工作、排队中或空闲）。点击打开这个 Lineage。

团队中只出现当前用户能读的 Lineage，个人与 User 层 Lineage 不算在内。

### 资源与最近变化

- **资源**按类型计数，分为“知识”（各类知识条目）和“研究与工程”（论文、仓库、实验、工作台等）两组。有按项目筛选的页面的类型可以点击：知识条目打开按类型和项目筛选的知识页，Skill、仓库、文章、实验、设备打开各自按项目筛选的资源页，工作台打开工作台目录。
- **最近变化**列出最近修改的 20 项资源：类型、标题（链接到资源）和修改时间。

Lineage、Resident、Workspace 和 Family 由团队一节显示，不计入资源和最近变化。

### 对话

项目页右侧是这个项目的 Project Resident 对话，所有用户共用。它是普通的 Resident Chat（decision_f560803947c6），同一项目内在项目页与地图页之间切换不重建；侧栏可以收起成一条窄栏，收起状态按用户保存。在地图页选中一个方向时，发出的消息附带这个方向的内容。Project 没有每位用户各自的项目对话 Resident。

## 数据从哪里来

| 内容 | 来源 |
| --- | --- |
| 列表 | `ListProjects`：登记字段、头像、Project Resident、团队与 Resident 数、正在工作的 Resident 数、最近变化时间，以及状态词表 |
| 项目页 | `GetProject`：项目记录（含登记字段与头像）、`summary.md`、团队、按类型的资源数、最近变化，以及状态词表 |
| 团队的实时状态与工作摘要 | `ListResidentStates`（不计算查看者未读）与 `GetResidentSummaries`（当前重点、近期成果），只读 Project Resident 和各 Lineage 的 Leader |
| Lineage 与 Resident 头像 | 头像读取接口，读不到时显示名称首字 |

团队由 SocietyService 的 Project 领域给出（`project_teams`）：它按调用者的 Lineage 读取权限过滤，Project 层 Lineage 画布中引用的子 Lineage Leader 只算作其所在 Lineage 的成员。资源数与最近变化由 ResourceService 按调用者能读的资源计算。指令的参数和结果见 decision_6591e7793116。

### 读取顺序

统计项目下的资源要逐一检查调用者能读的全部资源，是读取中最慢的一步（约半秒）。页面先画出不依赖统计的部分，统计结果随后填入：

- **列表**：`ListProjects` 返回前显示“正在读取项目…”。服务端同时读取团队（SocietyService）和各项目的最近变化（资源索引），耗时取两者中较慢的一个。
- **项目页**：同时发出 `GetProject`（`include_resources` 为 false）、`GetResearchMap`（`include_outputs` 为 false）和完整的 `GetProject`。前两者到达后立即画出页头、统计中的团队数与 Resident 数、概况、团队和地图卡片；资源总数与最近变化时间显示“…”，资源与最近变化两节显示“正在统计资源…”。完整的 `GetProject` 到达后填入这几处；统计失败时提示刷新页面重试。完整的 `GetProject` 同样同时读取团队和资源统计。
- **地图页**：见 decision_9d15ae2053c4。

## Society 中的 Project 菜单

Society 的 Project 菜单只保留权限：Package（这个 Project 上的权限表项）和 Lineages（对下属 Lineage 批量授予）。“设置”页签只放一个“打开项目页”的入口，项目信息、头像、改名和删除都在项目页修改；Project 没有 Work Journal 页签，它的概况和团队在项目页查看。

## 功能打包

项目页属于可选功能 `projects`（decision_15aab18c0ab3），它提供：

- `web.routes`：`/projects` 页面；
- `web.navigation`：主导航中的 Project 入口；
- `resource.portal_routes`：把 `project` 资源的 Portal 地址换成项目页，`direction` 资源的地址换成地图页；
- `isa.instructions`、`web.instructions`、`permission.records`、`project.relations`：研究地图的指令、执行、权限位置和随项目改名与删除的数据（decision_9d15ae2053c4）。

没有安装这个功能时，Project 链接打开按项目筛选的知识页，也没有研究地图。Project 的登记与指令、Project 层 Lineage 和 Project Resident 都属于核心，与是否安装项目页无关。

## 组件

| 文件 | 职责 |
| --- | --- |
| `http_projects.py` | 页面路由、导航入口和 `project` 的资源地址 |
| `projects.html`、`static/project.css` | 页面外壳与样式 |
| `static/project.js` | 按地址显示列表、项目页或地图页，管理浏览器历史、未保存修改的确认和 Project Resident 对话侧栏 |
| `static/project_list.js` | 项目列表 |
| `static/project_home.js` | 项目页（含研究地图卡片） |
| `static/project_map.js`、`project_map_drawer.js`、`project_map_layout.js`、`project_map.css` | 地图页、方向抽屉和方向排布 |
| `static/project_settings.js` | 编辑信息、改名和删除对话框 |
| `static/project_common.js` | 状态与类型名称、DDL、相对时间、热度、头像和资源页地址 |

## 失败与恢复

- 列表或项目读取失败时显示原因；项目不存在或无权读取时提示并提供返回列表的入口。
- 团队实时状态或工作摘要读取失败时，团队卡片保留来自 `GetProject` 的结构，只是不显示状态与摘要。
- 改状态、保存概况、编辑信息失败时保留原值或正在编辑的内容，并显示服务给出的原因。
- 改名与删除失败时对话框保持打开并显示原因；删除的中途失败按 `DeleteProject` 的重试规则继续（decision_4648f7fa3295）。
