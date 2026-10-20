---
kb_id: decision_4648f7fa3295
type: decision
project: AI员工
created_at: '2026-09-03'
version:
  number: 32
  created_at: '2026-09-03'
  created_by:
    user: ''
    agent: ''
  updated_at: '2026-10-19T20:21:40Z'
  updated_by:
    user: ''
    agent: resident_8586ec083cd0
importance: 8
metaness: 2
ai_generated: true
links:
  source:
  - ${code_root}/tests/cases/test_project_deletion_purge.py
  - ${code_root}/management_portal/static/portal_resource_registration_service.js
  - ${code_root}/management_portal/discussion_residents.py
  - ${code_root}/management_portal/hatg/catalog/organization.py
  - ${code_root}/management_portal/http_projects.py
  - ${code_root}/management_portal/static/portal_project_creation.js
  - ${code_root}/management_portal/resources/isa.py
  - ${code_root}/management_portal/resources/providers/project.py
  - ${code_root}/management_portal/resource_views.py
  - ${code_root}/management_portal/society_service/project_domain.py
  - ${code_root}/management_portal/serve_management_portal.py
  - ${code_root}/project_board_lan/projects_board.html
  - ${code_root}/management_portal/static/project_board.js
  - ${code_root}/tests/cases/test_project_resource_provider.py
  - ${code_root}/tests/cases/test_project_summary_backend.py
  - ${code_root}/tests/cases/test_project_deletion.py
  related:
  - decision_22227d0ad075
  - decision_b820faf42218
  - decision_600ae27b0538
  - decision_3e07c6d35cac
  - decision_9b94f218a188
  - decision_6591e7793116
  - decision_34158fdd4078
  - decision_b6e74d78f7b6
  supersedes: []
  derived_from: []
---
# Project 的创建、Summary 与删除

Project 是 Portal 中最高一层的协作单元。一个 Project 把某项长期工作的知识、代码、Resident 和成员权限放在一起，例如一个研究课题或一个产品。用户在 Project Board 页面（`/projects`）查找和管理 Project，也可以在 Society 页面的项目列表中新建 Project。

本 Design 说明 Project 由哪些部分组成、谁可以创建、创建时做了什么、怎样修改项目信息和维护摘要，以及怎样删除 Project。相邻主题由其他 Design 说明：
- Project Board 详情中的 Overview 显示结构化的项目状态，文件格式与页面投影见 decision_9b94f218a188。
- Project 在五层组织中的 Project 层 Lineage 见 decision_3e07c6d35cac。
- Project 成员角色（viewer、contributor、admin）已退役，见 decision_34158fdd4078；资源授权由权限系统判定，见 decision_b820faf42218，默认模板见 decision_600ae27b0538。
- `CreateProject`、`GetProject`、`UpdateProject`、`DeleteProject` 四条 HATG ISA 指令的参数和结果见 decision_6591e7793116。

## Project 由什么组成

一个正式的 Project 包含以下三部分。登记与目录先提交，随后建立项目主体；主体尚未就绪时回执明确报告等待恢复。

| 组成部分 | 位置 | 保存的内容 |
| --- | --- | --- |
| 项目登记 | PostgreSQL 中项目登记文档的一条记录（decision_86aed2f0e8c7） | 项目 ID、类型、状态、说明、路径、截止日期（DDL）和标签 |
| 项目目录 | 已配置的 Projects 根目录下的 `<项目 ID>` | 知识库、源码、`summary.md`、项目状态文件和 Agent 入口文件 |
| 项目主体 | SocietyService 中的 Project 层 Lineage 与 Leader | Project 在主体层级中的协作入口，见 decision_3e07c6d35cac |

**项目 ID** 是人可读的名称，例如 `AI员工`，同时用作项目名称和目录名。它由 1 到 80 个字母（包括中文）、数字或 `_`、`.`、`@`、`+`、`-` 组成。项目 ID 是统一资源 ID 规则（`<类型>_<12 位十六进制>`）的例外。项目目录固定为 Project provider 配置的 `PROJECTS_ROOT / <项目 ID>`，创建请求不能自选位置；该根默认取 `portal_paths.path('share_root') / 'Projects'`，部署可由 `PORTAL_SHARE_ROOT`、`PORTAL_PROJECTS_ROOT` 配置（兼容读取对应旧环境键），仍须通过共享根目录边界检查。

**项目 ID 是 Project 唯一的名称。** 项目登记没有单独的显示名或别名字段；资源标题、Project Board、用量统计和 Agent 看到的项目名称都是项目 ID，查找 Project 也只按项目 ID 匹配。一条项目记录只有 Portal 读取的字段：`id`、`status`、`project_type`、`description`、`path`、`ddl` 和 `tags`；写入带其他字段（例如 `name`）的记录会被拒绝，因为没有人读、也没有人维护的字段迟早和真实情况不一致。要换名称时用 `RenameProject` 修改项目 ID，目录和 Portal 中的引用一起改。

**项目登记文档**顶层必须声明 `schema_version: 1`。缺少这个字段或版本不同时，读取登记的服务直接报错，不自动补默认值。登记文档还保存两份词表：`status_vocab`（项目可用的状态）和 `tag_vocab`（知识库标签及其对应目录）。

Project 的创建、读取、修改和删除都通过 HATG ISA 指令完成，权限由权限系统按当前模板判定（decision_b820faf42218）。指令映射的唯一权威是默认模板 decision_600ae27b0538 及正式 ISA schema：`CreateProject` 为 `@free`，`GetProject` 要求对目标 Project 的 `@viewer`，修改与删除按各指令的管理权限要求判定。服务不按 Project 成员角色放行或拒绝。

**项目类型**只有四种：`company`（公司项目）、`academic`（学术项目）、`personal`（个人项目）和 `other`（其他）。创建和修改时也接受常见的别名，例如 `research`、`学术项目`，保存前统一换成这四个值。

## Project Board 页面

Project Board 是用户查找和管理 Project 的页面。它由以下部分组成：

| 区域 | 用户看到和能操作的内容 |
| --- | --- |
| 顶部工具栏 | 搜索框（按名称、ID、标签、说明或路径搜索）；“筛选”按钮打开标签筛选；“重新读取”图标；“新建项目”按钮 |
| Project 卡片 | 项目名称、类型、状态、截止日期、标签和项目状态摘要。状态是一个下拉框，可以直接修改；点击截止日期可以修改；点击标签按该标签筛选；“Summary”和“Chat”按钮打开详情中的对应标签页 |
| 新建项目对话框 | 项目 ID、说明、类型、状态、路径预览、标签和 DDL，以及“取消”和“创建”按钮 |
| 项目详情 | 顶部有项目名称、类型下拉框、“复制链接”和关闭按钮；下方有 Overview、Summary、Chat、Preview 四个标签页 |

详情中的四个标签页各自显示不同来源的内容：

- **Overview**：结构化的项目状态，包括目标、能力交付进度、风险和相关 Lineage。内容来自项目目录中的项目状态文件，见 decision_9b94f218a188。
- **Summary**：项目维护者写给人看的自由文本，即项目根目录下的 `summary.md`。
- **Chat**：与 Project Chat Resident 对话，讨论这个项目。
- **Preview**：浏览 Overview 中引用的项目材料和证据文件。

页面通过 `GET /projects/api/projects` 读取项目列表，打开详情时用 `GetProject` 读取项目记录、Summary 和项目状态，修改项目信息和保存 Summary 使用 `UpdateProject`。

## 设计点

### 谁可以创建 Project

**有效的 User 和 Resident 都可以创建 Project。**

- `CreateProject` 与 `RegisterResource(project)` 的调用权限按当前模板的指令映射判断，默认模板中二者对创建 Project 都不设要求。创建成功后，权限系统记录不可变的创建者事实，创建者就是创建它的认证调用者；默认模板的创建者规则使创建者对新 Project 有 admin。Resident 替 User 创建时，取得 admin 的是这个 Resident；User 需要权限时，由 Resident 签发临时凭证或由管理员在实例中增加表项（decision_b820faf42218）。不恢复 `initial_admin_ids` 参数或 Project 成员角色。

### 创建 Project

**创建流程建立项目登记、目录、Project 层 Lineage 与 Leader。**

Portal 有两个创建入口，它们调用同一个登记流程：
- Project Board 的“新建项目”对话框，可以填写项目 ID、说明、类型、状态、标签和 DDL，通过 `RegisterResource`（资源类型 `project`）提交。
- Society 页面项目列表标题旁的“＋”，打开统一的创建对话框，只填项目 ID、类型和可选说明，通过 `CreateProject` 提交；状态默认为 `active`，标签和 DDL 为空。

登记流程依次执行以下步骤：

1. **检查冲突。** 服务取得项目登记的写锁，确认项目 ID、登记中的路径和目标目录都没有被占用。任何一项冲突都以错误结束，已有的目录和登记保持原样。状态必须属于 `status_vocab`。
2. **写入。** 服务先验证提供的认证创建来源有效且与调用者匹配，再在同一次登记事务中创建项目目录和种子内容、追加登记记录。任何领域写入失败时，服务删除已创建的目录，登记事务不提交。领域创建成功后，资源登记提交，权限系统随即把 Project 放到 R 树“共享”下、记录创建者事实，并展开以它为锚的规则表项；重复请求不改变已记录的创建者。
3. **等待索引。** 登记完成后，服务最多等待 20 秒，让资源索引收录新 Project。超时不回滚登记，回执标明索引尚未确认，索引随后自动补齐。
4. **建立项目主体。** 两个创建入口都调用同一个 SocietyService 内部函数，建立 Project 层 Lineage 与 Leader；已有主体直接复用。失败时以 `instruction.projection_pending` 报告登记已提交和待完成的主体初始化。原幂等键重试或后台恢复只补做初始化，不再创建目录或登记。前端从主体层级读取已有 ID，不在打开页面时创建主体。

创建界面向 `CreateProject` 或 `RegisterResource(project)` 传入当前显示语言 `language`（`zh-CN` 或 `en`）。服务只在初始化时选择相应种子模板；未指定时使用中文。英文模板包含英文的项目目标、目录结构、当前状态和引导占位说明。项目 ID、用户填写的说明及路径逐字保留，`language` 不写入项目登记；切换界面语言不重写既有文件。

新项目目录包含以下种子内容：

| 内容 | 用途 |
| --- | --- |
| `agent_port/skills/`、`agent_port/tools/` | 放置项目专用的 Skill 和工具 |
| `agent_port/boot.md`、`agent_port/index.md` | Agent 进入项目时先读的引导与索引，初始内容包含项目说明 |
| `agents/` | 项目中 Lineage 与 Resident 的工作目录 |
| `knowledge_base/` | 项目知识库；`tag_vocab` 中每个标签对应的子目录同时创建 |
| `src/` | 源码 |
| `to_archive/` | 等待整理归档的材料 |
| `summary.md` | 项目摘要 |
| `article/`、`references/` | 只有学术项目创建，放置论文源码和参考文献 |

### 修改项目信息

**通过 `UpdateProject` 授权的 User 和 Resident 可以修改项目的状态、截止日期和类型；其他字段在创建后保持不变，改名使用 `RenameProject`。**

- 页面上，卡片的状态下拉框、截止日期和详情顶部的类型下拉框提交 `UpdateProject`。页面先显示新值；保存失败时恢复原值并显示错误。
- 服务把每个字段作为一次独立的登记修改写入。修改状态时，新值必须属于 `status_vocab`；修改类型时，别名统一换成四种类型之一。
- 项目说明、标签和路径不能通过这条路径修改。

### 维护 Summary

**`summary.md` 是维护者为人写的项目摘要，读取和修改分别沿用 `GetProject` 与 `UpdateProject` 的授权。**

- Summary 固定保存在项目根目录。它与 Overview 中的项目状态各自独立：Summary 由人编写，项目状态由 Agent 按固定格式发布，两者互不生成、互不覆盖。
- 文件不存在时，读取结果为 `exists: false` 和空文本，页面提示“点击编辑创建”。这属于正常状态，页面不显示错误。
- Summary 标签页提供“编辑”“取消”和“保存”。保存时以完整文本替换文件内容，第一次保存会创建文件。保存失败时页面保留正在编辑的内容。
- Summary 没有版本号。两个人同时编辑时，后保存的内容成为当前文本。

### Project Chat

**详情中的 Chat 标签页让每位用户在每个 Project 中各自与一个 Resident 讨论这个项目。**

- 这些 Resident 都属于 Portal 范围职责 `project-chat` 登记的 Lineage（职责登记见 decision_6591e7793116）。
- 用户第一次在某个 Project 中发送消息时，Portal 为“这位用户、这个 Project”创建对话 Resident；只打开标签页不会创建。之后同一用户在同一 Project 中继续使用这个 Resident，对话历史保留。
- 发送前，Portal 把项目 ID 作为本次对话的上下文交给 Resident。

### 删除 Project

**调用者用 `DeleteProject` 删除 Project 及其 Lineage、Resident；无存活 Resident 使用的工作区和其余项目内容一起清理，仍被使用的 Workspace 保留。**

- Project Board 没有删除按钮，删除只能通过 `DeleteProject` 指令执行。默认模板要求调用者对该 Project 有 admin：创建者、管理员，或经实例中增加的表项、临时凭证取得 admin 的主体（decision_600ae27b0538）。
- 删除可以处理有内容的项目。项目中的 Resident 完成或停止排队任务、运行任务和 Fork 创建任务，相关迁移结束后，可以删除其 Lineage。服务按以下顺序删除：
  1. 先读取项目登记中的原始路径，检查它必须指向 `portal_paths.path('share_root') / 'Projects'` 下的真实目录，不能是符号链接、该 Projects 根本身或它之外的路径；确认之后再使用解析后的路径。独立或共享 Workspace 的存在不拒绝删除；其清理由第 4 步按存活引用判断。ResourceService 注销资源前调用 SocietyService 的路径校验，防止定位到登记范围以外的目录。
  2. ResourceService 分页列出项目下的全部资源登记，逐个注销（Lineage 和 Resident 的登记由第 3 步处理）。这一步在项目登记仍然存在时进行。任何一个资源注销失败时，指令返回错误并列出失败的资源，项目文件不删除。其他功能共享库中的对象不随项目注销，例如论文属于共享论文库：拥有这类对象的功能在功能登记中声明它们的资源类型和 `project.relations` 接入点（decision_15aab18c0ab3），这一步跳过这些类型；删除项目内容前，各功能经接入点去掉对象与项目的关联，对象本身保留。Paper 从论文 manifest 的 `relations.projects` 中去掉这个项目。结果的 `unlinked` 按功能键列出改动过的对象，例如 `unlinked.paper` 是论文 ID；没有安装的功能不出现。
  3. 按 `DeleteLineage` 的规则删除属于该项目的全部 Lineage 及其中的 Resident：先删除普通 Lineage，最后删除 Project 层 Lineage。Lineage 与 Resident 的身份删除，目录由按引用清理任务处理，不移入归档。
  4. 按 decision_8838269736d3 计算项目目录内 Workspace 的存活 Resident 引用。无人引用的注册退役并清理文件；其他 Resident 仍使用的 Workspace 保持 ID、路径、文件和绑定，包括登记在项目根目录的 Workspace。目录清理保留这些工作区及必要父目录，其余内容直接删除。
  5. 从项目登记中移除这条记录，向 `portal_paths.path('share_root') / 'Agents/_archive/deleted_projects/project_deletions.json'` 追加一条审计记录，内容包括项目 ID、路径、说明、删除时间、删除者，以及被删除的 Lineage 和 Resident ID。
  6. 最后撤销项目本身的资源登记，并通知权限系统：项目和随目录删除的 Workspace 离开 P、R 两棵树，以它们为锚的规则表项、指向它们的表项和创建者记录一并删除（decision_b820faf42218）。项目 ID 就是项目名称，以后可以再次用来创建 Project；新建的 Project 是新的实例，按新建规则由新的创建者取得 admin。
- 其他存活 Resident 仍使用的 Workspace 及文件引用保持可用，残留目录不恢复 Project 身份；未保留文件的引用失效。
- 中途失败时，指令返回错误，已经删除的内容不恢复；再次调用 `DeleteProject` 会从剩余部分继续：第 2 步失败后重试会注销剩余资源；项目登记已经移除后，发起这次删除的调用者重试会返回原来的删除记录，并完成第 6 步。资源索引已经不再列出该项目时，这次重试同样有效。

## 组件与流程

| 组件 | 位置 | 职责 |
| --- | --- | --- |
| Project Board 页面 | `project_board_lan/projects_board.html`、`management_portal/static/project_board.js` | 卡片、搜索、筛选、新建对话框、详情标签页和操作反馈 |
| 统一创建对话框 | `management_portal/static/portal_project_creation.js` | Society 页面“＋”使用的创建对话框 |
| Project 登记提供者 | ResourceService 中的 `resources/providers/project.py` | 校验项目 ID、类型和状态，写入项目登记与种子目录，读写 Summary 和项目状态 |
| Project 指令 | ResourceService 中的 `resources/isa.py` | 执行 `CreateProject`、`GetProject`、`UpdateProject`、`DeleteProject`；创建与 `RegisterResource(project)` 共用登记后主体初始化和恢复流程 |
| 项目删除 | ResourceService 中的 `resources/isa.py`、SocietyService 中的 `society_service/project_domain.py` | 先注销项目下的资源登记，再直接删除项目的 Lineage 与 Resident、项目目录和登记记录，写审计记录 |

创建流程：

```text
用户在对话框中提交
        ↓
RegisterResource(project) 或 CreateProject  →  ResourceService
        ↓
取得登记写锁 → 检查 ID、路径、目录冲突
        ↓
创建目录与种子文件 → 追加登记记录
        ↓
等待资源索引（最多 20 秒）
        ↓
建立 Project 层 Lineage 与 Leader（失败则登记待恢复回执）
```

删除流程：

```text
DeleteProject(project_id)  →  ResourceService
        ↓
SocietyService：只读检查登记目录路径
        ↓
ResourceService：逐项注销项目资源；失败时保留项目内容供重试
        ↓
移除保留库对象的项目关联
        ↓
SocietyService：删除普通 Lineage → 删除 Project 层 Lineage
        → 按存活引用保留 Workspace 并清理其余目录 → 移除登记、写审计
        ↓
ResourceService：最后撤销项目本身，退役对应权限记录与头像
```

项目登记与项目目录分开保存：登记文档集中保存所有项目的身份和元数据，便于列表、搜索和调度读取；大量项目文件留在各自的目录中。

## 故障与恢复

- **登记失败**：登记事务不提交，服务删除已经创建的目录；删除目录失败时返回错误并说明目录没有回收。
- **项目主体初始化失败**：已提交的登记与目录保留。调用者用原幂等键重试，或由后台维护继续补齐同一个项目主体；不另设初始化指令。
- **索引未确认**：项目已经创建成功，资源索引稍后自动收录。调用者按原项目 ID 核对状态，不要重复创建。
- **删除中途失败**：已经删除的内容保持删除，再次调用 `DeleteProject` 继续删除剩余部分。
- **登记与目录不一致**：维护者依据删除审计记录和资源索引修复。
