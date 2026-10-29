---
kb_id: decision_9d15ae2053c4
type: decision
project: AI员工
created_at: '2026-10-29'
version:
  number: 4
  created_at: '2026-10-29'
  created_by:
    user: tfi-exported-user
    agent: resident_dbfb834a0539
  updated_at: '2026-10-29T11:24:19Z'
  updated_by:
    user: ''
    agent: resident_dbfb834a0539
importance: 8
ai_generated: false
links:
  source:
  - ${code_root}/management_portal/research_map_store.py
  - ${code_root}/management_portal/research_map_web_isa.py
  - ${code_root}/management_portal/research_map_instructions.py
  - ${code_root}/management_portal/research_map_automation.py
  - ${share_root}/Skills/write-research-map/SKILL.md
  - ${code_root}/management_portal/static/project_map.js
  - ${code_root}/management_portal/static/project_map_drawer.js
  - ${code_root}/management_portal/static/project_map_layout.js
  related:
  - decision_46c37026e8ea
  - decision_4648f7fa3295
  - decision_3e07c6d35cac
  - decision_898b8911a2ff
  - decision_b820faf42218
  - decision_600ae27b0538
  supersedes: []
  derived_from: []
metaness: 2
---

# 研究地图：项目研发方向的地图、修订与指令

研究地图是一个 Project 的研发方向图：人在这里看清项目正在攻哪几条路线、每条路线走到了哪一步、谁在做、拿到了什么成果，并在这里定方向、定主次、派团队、下结论。细节工作由负责的团队完成，人不在地图上写任务清单，也不填进度。每个 Project 有一张地图，大的研究主题作为顶层方向，主题下的路线作为子方向。

地图属于可选功能 `projects`（decision_46c37026e8ea）：数据保存在 PostgreSQL 的 `research_map` schema，由 Portal Web 执行本篇的四条指令。人在页面上的操作和 Resident 的修改使用同一组指令，权限由权限系统按当前方案判定。

## 地图里有什么

| 部分 | 含义 |
| --- | --- |
| 目标 | 地图中心显示的项目目标；第一次修改地图时取项目说明 |
| 方向 | 统一资源 `direction_<12 位十六进制>`，组成一棵树，同级之间有次序 |
| 关系 | 两个方向之间的“建立在……之上”（`builds_on`）、“互为替代”（`alternative`）或“依赖”（`depends_on`） |
| 时间线 | 挂在方向上的记录：进展（`progress`）、人的决定（`decision`）或批注（`note`），带时间、作者和引用的资源 |
| 布局 | 人在画布上排好的方向位置 |

每个方向记录：名称、要回答的问题、打法、姿态、负责的 Lineage、人写给团队的期望、结论（文本、确认人和时间），以及提出者和提出理由。姿态由人决定：

| 姿态 | 含义 |
| --- | --- |
| `candidate` | 候选：Resident 提出、等人接纳的方向 |
| `focus` | 主攻 |
| `explore` | 探索 |
| `watch` | 观望 |
| `concluded` | 已收束，并写下结论 |
| `dropped` | 放弃 |

Resident 增加方向时，不写姿态即为候选；User 增加时默认是探索。明确给出的姿态总是优先。

## 修订与回放

一次修改提交一组改动，成为地图的下一个修订。修改要带上所依据的修订号，地图已经被别人改过时整次拒绝，调用者读取新地图后再提交，因此两份同时进行的修改不会互相覆盖。每个修订保存修改者、时间、按执行结果记录的改动和修订后的地图，任何修订时的地图都可以读回；页面的时间回放按修订顺序读出这些记录。

时间线也属于修订：一条记录记在提交它的修订上，读回过去的地图时只带出当时已有的记录。布局不是修订，拖动方向不产生修订，也不和地图修改互相冲突。

删除方向时，它的子方向上移到它原来的位置，与它相关的关系一并去掉；方向的身份仍然登记在项目之下，历史修订中照常出现。

## 产出从哪里来

方向的产出是项目中引用了它的资源。知识条目在 YAML 头部的 `links.related` 中写上方向 ID，资源索引就把它记为这个条目的 `related` 关系；读取地图时，按调用者能读的资源找出这些条目，每个方向列出最近的 30 项。团队不需要另外登记产出。读取历史修订时不带产出，因为索引只反映现在。

## 地图页面

项目页上的“研究地图”卡片显示地图缩略图、各姿态的方向数和最近修订，点开进入地图页 `/projects?project=<项目>&view=map`；方向链接 `/projects?direction=<方向 ID>` 解析出所属项目后打开同一页并选中这个方向。地图页右侧仍是 Project Resident 的对话，选中方向时，发出的消息附带这个方向的内容，Resident 知道人在说哪条路线。

打开地图页时，页面先以 `GetResearchMap`（`include_outputs` 为 false）读出方向树并立即画出；同时读取项目信息（`GetProject`，`include_resources` 为 false，供团队与对话侧栏使用）和带产出的完整地图。完整地图到达后补上各方向的产出数和“最近有新产出”的亮度；它的修订号比已画出的地图旧时不采用。

画布以项目目标为中心，方向按树排成同心环：每棵子树按叶子数分得一段圆周，人拖动过的方向停在保存的位置。方向的样子表达人的判断和团队的动静：

- 姿态决定形状与颜色：主攻最大最醒目，探索次之，观望淡出，候选以虚线框出并直接带“接纳”“丢弃”两个按钮，已收束显示结论，放弃的方向默认隐藏（隐藏时连同它的子方向），可以切换显示。
- 最近三周内有进展记录、新产出或修改的方向更亮；负责团队的 Leader 正在执行时，方向上显示“正在工作”。方向上还显示最新一句进展或结论、产出数和负责团队的头像。
- 关系以带箭头的曲线连接两个方向。

点开一个方向，侧边抽屉列出它的全部内容，人在这里做决定：

- 姿态：候选方向可接纳为主攻、探索或先观望，也可以丢弃；其他方向在主攻、探索、观望之间切换，或收束（写下结论）、放弃。每个决定都和一条 `decision` 时间线记录一起提交，团队和 Project Resident 读地图时能看到人在什么时候做了什么决定。丢弃候选即把它标为放弃，提出它的 Resident 由此知道这个提议没有被接受。
- 问题、打法与期望：可直接编辑，只提交改动的字段。
- 负责团队：列出各团队 Leader 的执行状态和工作摘要。“交给已有团队”先在地图上登记团队并记一条决定，再以当前用户的名义把方向发给该 Lineage 的 Leader（`DeliverMessage`，`business_ref` 为方向 ID）。消息说明工作方式：产出的知识条目在 `links.related` 写上方向 ID，阶段进展用 `add_note`（`progress`）记在方向上，问题有答案时告诉人，由人确认结论。“组建新团队”把同样的方向发给 Project Resident，请它用 `CreateLineage` 在项目中新建 Lineage、设好 Leader，再把方向以“创建团队：”开头发给这位 Leader 组建团队，最后把新 Lineage 登记为负责团队。
- 时间线：进展、决定、批注与产出按时间合在一起；人可以在这里写批注。
- 关系与位置：增删关系、添加子方向、移到其他方向之下或删除方向。

页面上的每次修改都带着当前修订号提交；地图已被别人改过时，页面重新读取地图并提示再操作一次。收到 `research-map` 变更通知时，页面在没有编辑中的内容时自动重新读取。

“回放”读出全部修订，沿时间轴逐个显示每次修订后的方向树，标出含人工决定的修订，并用一句话说明这次改了什么；回放期间地图只读，“回到现在”恢复当前地图。

## 工作方式

地图把人、Project Resident 和团队的工作接在一起，各方的操作规则写在 Skill `write-research-map`（`skill_5c7f37840b1b`）中：

- **人**决定方向的姿态，接纳或丢弃候选，收束并写结论，把方向交给团队。这些决定都是地图修订，并带有 `decision` 记录。
- **Project Resident** 起草候选方向，回答人在项目对话中的请求，每天整理有新动静的方向。它不替人改姿态或写结论；认为某个方向可以收束时写批注并请人确认。
- **负责方向的团队**（Lineage）由 Leader 接手：产出的知识条目在 `links.related` 写方向 ID，阶段进展以 `progress` 记录写在方向上，问题有答案时告诉人。

### 每日整理

每个 Project 层 Lineage 有一条“研究地图每日整理”流程：每天 07:30（主机所在时区）的周期 Event 启动一个 Effect，Effect 以 Lineage Leader（即 Project Resident）的身份运行：

1. 从 Lineage 共享状态 `research_map.digest_until` 读出上次整理的截止时间；
2. 调用 `GetResearchMapDigest` 汇总这之后别人提交的修订和新产出，Project Resident 自己的修订不计入；
3. 没有新动静时只把截止时间向前推，不叫醒任何 Resident，不调用模型；
4. 有新动静时把汇总文字发给 Project Resident，再推进截止时间。投递失败时截止时间不变，下一次仍会包含这些动静。

这条流程在地图第一次被修改时画进 Project 层 Lineage：`ApplyResearchMapChanges` 成功后，以修改者的身份调用 `ApplyLineageChanges` 新建 Event、Effect 和两条连线（`client_key` 为 `research-map:digest:*`），并在 `research_map.automation` 表记下项目与 Lineage。修改者不能修改该 Lineage 时不画，地图修改照常成功，下一次修改再试。画好之后有人删除或暂停它，系统不会重新画；改时间、暂停都在 Lineage 中直接操作。

人在地图页把方向交给已有团队、请 Project Resident 组建团队时，页面立即发出消息；人的其他决定由每日整理带给 Project Resident，再由它转告受影响的团队。

## 身份、权限与项目生命周期

- 方向是统一资源：`ResolveResource` 能解析它，链接打开 `/projects?direction=<方向 ID>`，页面定位到地图上的这个方向。权限系统把方向放在所属 Project 之下（`permission.records`），读取和修改都按项目判定。
- 改名项目时地图随项目改用新 ID；删除项目时地图、修订、时间线、方向登记和每日整理记录一起删除（`project.relations`）。每日整理的 Effect 运行时从 Lineage 读出项目 ID，改名后无须修改。
- 负责团队只能是这个项目中调用者能读到的 Lineage（Project 层 Lineage 或普通 Lineage）。
- 每次修改地图或布局后，打开这张地图的页面收到 `research-map` 变更通知，重新读取。

## 指令一览

| 指令 | 改变状态 | 作用 | 默认方案要求 |
| --- | --- | --- | --- |
| `GetResearchMap` | 否 | 读取地图，或某次修订时的地图 | 对 `project_id` 有 @viewer |
| `ApplyResearchMapChanges` | 是 | 以一组改动修改地图，成为一次新修订 | 对 `project_id` 有 @contributor |
| `ListResearchMapChanges` | 否 | 按修订顺序列出修改记录，供回放 | 对 `project_id` 有 @viewer |
| `UpdateResearchMapLayout` | 是 | 保存方向在画布上的位置 | 对 `project_id` 有 @contributor |
| `GetResearchMapDigest` | 否 | 汇总一段时间内别人提交的修订和新产出，供每日整理 | 对 `project_id` 有 @viewer |

### GetResearchMap

- 参数：`project_id`；`revision`（可选，读那次修订时的地图）；`include_outputs`（可选，默认 true；只需要地图本身时传 false，例如项目页的地图卡片）。
- 结果：`revision`（还没有地图时为 0）、`goal`、`directions`（同级按数组顺序）、`relations`、`layout` 与 `layout_revision`、`notes`（每个方向最近 20 条，新的在前）、`outputs`（方向 ID → 产出），以及这次修订的 `updated_at`、`updated_by`。

### ApplyResearchMapChanges

- 参数：`project_id`、`expected_revision`、`changes`。`changes` 按顺序执行，每项一个 `op`：
  - `set_goal{goal}`；
  - `add_direction{title, question, approach, stance, parent_id, index, teams, expectation, proposal_reason, key}`；带 `key` 时，同一次提交中后面的改动可以用 `"$<key>"` 指代这个新方向；
  - `update_direction{direction_id, fields}`，`fields` 可含 `title`、`question`、`approach`、`stance`、`teams`、`expectation`、`conclusion`；写入结论时记录确认人和时间，写空文本清除结论；
  - `move_direction{direction_id, parent_id, index}`，不能移到自己或自己的子方向之下；
  - `remove_direction{direction_id}`；
  - `add_relation` / `remove_relation{from, to, kind}`；
  - `add_note{direction_id, kind, text, refs}`。
- 结果：新的 `revision`、`created`（key → 新方向 ID）与 `applied`（按执行结果记录的改动）。
- 任何一项改动无效时整次提交不生效，错误指出是第几项改动。所依据的修订已经过期时返回版本冲突，并带上当前修订号。

### ListResearchMapChanges

- 参数：`project_id`、`limit`（可选，默认 100）、`cursor`（可选）。
- 结果：`changes` 按修订顺序排列，每项是 `revision`、`changed_at`、`changed_by`、`changes` 与修订后各方向的 `direction_id`、`parent_id`、`title`、`stance`、`teams`；还有下一页时带 `next_cursor`。

### UpdateResearchMapLayout

- 参数：`project_id`、`positions`（方向 ID → `{x, y}`；值为 `null` 时恢复自动排布）。
- 结果：保存后的全部位置 `layout` 与 `layout_revision`。项目还没有地图时返回不存在。

### GetResearchMapDigest

- 参数：`project_id`；`since`（可选，ISO 8601；省略或为空时取最近 24 小时）。
- 结果：`since`、`until`（下一次从这里算起）、`revision`、`count`（有新动静的方向数加目标修改数）、`directions`（按地图顺序，每项 `direction_id`、`title`、`stance`、`teams`、`changes` 与 `outputs`）、`goal_changes`，以及交给 Project Resident 的整理请求 `prompt`（`count` 为 0 时为空）。
- 新动静是 `since` 之后、调用者以外的人或 Resident 提交的修订，以及在 `links.related` 中引用方向、修改时间在 `since` 之后的资源。每条修订写成一句话：新增方向及姿态与理由、姿态改为什么、结论、移动、删除、关系，以及时间线记录的种类和文字。

## 参数名契约

下表列出 arguments 内的顶层参数名；必填条件、类型和枚举以 GetInstructionSchema 为准。

| 指令名 | arguments 参数名 |
| --- | --- |
| `GetResearchMap` | `project_id`、`revision`、`include_outputs` |
| `ApplyResearchMapChanges` | `project_id`、`expected_revision`、`changes` |
| `ListResearchMapChanges` | `project_id`、`limit`、`cursor` |
| `UpdateResearchMapLayout` | `project_id`、`positions` |
| `GetResearchMapDigest` | `project_id`、`since` |
