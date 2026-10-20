---
kb_id: decision_9b94f218a188
type: decision
project: AI员工
created_at: '2026-09-03'
version:
  number: 9
  created_at: '2026-09-03'
  created_by:
    user: ''
    agent: ''
  updated_at: '2026-10-19T19:43:45Z'
  updated_by:
    user: tfi-exported-user
    agent: resident_865c3c24f251
importance: 7
metaness: 2
ai_generated: true
links:
  source:
  - ${code_root}/management_portal/resources/isa.py
  - ${code_root}/management_portal/project_state.py
  - ${code_root}/management_portal/resources/providers/project.py
  - ${code_root}/project_board_lan/projects_board.html
  - ${code_root}/management_portal/static/project_page.js
  - ${code_root}/tests/cases/test_project_state.py
  related:
  - decision_4648f7fa3295
  - decision_34158fdd4078
  - decision_9f6135cbbb22
  supersedes: []
  derived_from: []
---
# Project State：项目状态文件与 Overview 显示

Project State 是项目维护者写下的一份结构化“项目现状”：目标是什么、做到了哪一步、下一步做什么、谁在负责、证据在哪里、有哪些风险。它保存在 Project 目录中的一个 YAML 文件里（YAML 是面向人的结构化文本格式）。Project Board 打开项目详情时，Overview 标签页读取这个文件，把它整理成固定格式的页面数据，再显示为摘要、交付进度、待决事项、负责人和证据几个区域。

本 Design 说明项目状态文件的格式要求、Portal 怎样读取和检查它、Overview 显示什么，以及文件缺失或有错时页面怎样表现。Portal 只读取这个文件，不修改它，也不推断文件以外的项目事实。Project 的创建、Summary 和 Project Board 页面见 decision_4648f7fa3295。

## 三层数据

| 层次 | 是什么 | 由谁维护 |
| --- | --- | --- |
| 项目状态文件 | Project 目录下的 `project_state/PROJECT_STATE.yaml` | 项目维护者或负责维护它的 Agent |
| 页面数据 | Portal 读取文件、检查格式、补齐缺省值后生成的固定格式 JSON，格式名为 `portal.project-page.v2` | Portal 的项目状态读取器（`management_portal/project_state.py`） |
| Overview | Project 详情中的标签页，把页面数据排成可浏览的区域 | Project 页面（`management_portal/static/project_page.js`） |

输入文件目前只支持一种格式 `tfi.project_state.v0.2`，页面数据只有 `portal.project-page.v2` 一种格式。无论文件是否存在、是否有效，读取器都返回同样结构的页面数据，并用 `status` 和 `source.code` 说明读取结果，页面据此解释原因。

## Overview 显示什么

打开 Overview 后，页面顶部是一段项目概况（目标、当前阶段、进展、需要关注的事、下一步和几项计数），下面是五个区域，页面上的锚点导航可以跳到任一区域：

| 区域 | 内容 |
| --- | --- |
| 一眼看懂这个项目 | 目标、当前阶段、进展、需要关注、下一步和计数卡片 |
| 交付走到哪一步 | 里程碑、重点能力，以及可以展开的五阶段能力矩阵 |
| 下一步与需要的决定 | 等待人工审阅的事项、已确认和候选的下一步、风险和开放问题 |
| 谁在负责什么 | 相关 Resident 列表和无人负责的部分 |
| 证据、决策与认知健康 | 当前注意事项、知识健康指标，以及可以展开的决策、材料时间线和架构依赖 |

较长的列表和完整矩阵默认收起，点击“展开”查看。证据引用中的统一资源 ID 显示为可以点击的资源标签；文件路径显示为预览入口，点击后在 Preview 标签页打开。每个引用是否能打开，按当前用户对该资源的权限分别判断。

## 项目状态文件的格式

文件固定位于 Project 根目录下的 `project_state/PROJECT_STATE.yaml`。读取器只从项目登记中记录的 Project 目录读取，拒绝绝对路径、包含 `..` 的路径和解析后位于 Project 目录之外的路径。

顶层必须是一个对象，并用 `schema_version` 声明格式：

```yaml
schema_version: "tfi.project_state.v0.2"
```

读取器只接受精确的 `tfi.project_state.v0.2`，不会因读取自动改写状态文件。更早的 `alignme.project_state.v0.2` 不在当前支持集合内。

以下顶层分区必须存在，并且类型正确：

| 必须是对象 | 必须是数组 |
| --- | --- |
| `project`、`snapshot`、`roadmap`、`architecture`、`decisions`、`lineage`、`knowledge_health`、`human_review` | `current_attention`、`capabilities`、`risks`、`open_questions`、`material_timeline` |

`commitments`（承诺事项）也会进入页面数据，但它不是必需分区。

- 文件中的 `project.id` 如果填写，必须等于当前 Project 的 ID；不填写时，读取器使用当前 Project 的 ID。这样一个项目的状态文件不能冒充另一个项目。
- 文件中的 `project.name` 不进入页面数据。项目 ID 是 Project 唯一的名称（decision_4648f7fa3295），状态文件不能再给项目起另一个名字。
- 分区中可以包含格式没有规定的字段，读取器按 JSON 能表示的形式保留它们：日期转换为 ISO 格式的字符串，对象和数组逐层转换，其他值转换为字符串。
- 数组中的条目只保留对象形式的条目。

## 读取与检查

**用户打开 Overview 时，页面通过 `GetProject` 取得项目状态；读取器每次都重新读取文件，按固定顺序检查，把结果转换成页面数据。**

读取请求沿用 `GetProject` 对目标 Project 的读取权限；权限系统按当前启用方案判定，不再依赖已退役的 Project 成员角色（decision_4648f7fa3295、decision_600ae27b0538）。页面在同一次浏览中按项目缓存读取结果，点击刷新时重新读取。

读取器依次检查以下条件，遇到第一个不满足的条件就停止，返回对应的状态：

| 检查 | 不满足时 `status` | `source.code` |
| --- | --- | --- |
| 文件存在 | `missing` | `missing_file` |
| 文件可以读取并按 UTF-8 解码 | `error` | `read_error` |
| YAML 语法正确 | `invalid` | `invalid_yaml` |
| 顶层是对象 | `invalid` | `invalid_document` |
| `schema_version` 精确等于受支持的 `tfi.project_state.v0.2` | `invalid` | `unsupported_schema` |
| `project.id` 与当前 Project 一致 | `invalid` | `project_id_mismatch` |
| 必需分区齐全且类型正确 | `partial` | `partial_sections` |

全部检查通过时，`status` 为 `ready`，`source.code` 为 `ok`。`ready` 只说明文件符合格式要求，文件中写的事实由维护者负责，Portal 不另行核实。

页面数据的 `source` 部分还包含：文件在 Project 中的相对路径、文件声明的格式、`snapshot.generated_at`（快照生成时间）和文件修改时间（UTC）。`partial` 时，`missing_sections` 列出缺少或类型错误的分区。

页面按状态显示：
- `ready`：完整的 Overview。
- `partial`：在顶部提示缺少哪些分区，其余能读出的内容照常显示。
- `missing`：提示这个项目还没有发布项目状态，并提供“查看 Summary”和“打开 Chat”两个入口。
- `invalid`、`error`：显示错误状态和 `source.code`、格式版本，同样提供 Summary 和 Chat 入口。Project 详情的其他标签页不受影响。

## 概况怎样选取内容

页面顶部的概况（页面数据中的 `brief`）从文件各分区中各取一条内容，规则如下：

| 概况字段 | 取值规则 |
| --- | --- |
| 目标（`headline`） | `project.objective` |
| 当前阶段（`current_state`） | 优先取 `project.phase_inference.statement`；没有时取 `project.current_phase`（值为 `unknown` 时留空） |
| 进展（`progress`） | `current_attention` 中第一条 `type` 为 `fact` 的条目的 `statement` 或 `title`；没有这类条目时取第一条 |
| 需要关注（`needs_attention`） | 依次取 `human_review.highest_priority` 的第一项、第一条风险、第一条开放问题 |
| 下一步（`next_move`） | 优先取第一条已确认的里程碑，`next_move_state` 记为 `confirmed`；没有时从候选里程碑中取第一条与“需要关注”文字相似度低于 0.72 的，全部相似时取第一条候选，记为 `candidate`；都没有时为 `unknown` |
| 截止日期与交付物 | `project.registered_deadline` 和 `project.deadline_deliverable` |
| 成功标准 | `project.project_level_success_criteria` |
| 计数（`signals`） | 能力数、Lineage 中的 Resident 数、人工审阅数、有冲突的决策数、风险数 |

相似度按去掉标点和空格后的文本计算。概况只是摘要，完整的分区内容仍在下面的各区域中显示。

## 页面数据的主要内容

- **项目与快照**：`project` 包含 ID、名称、状态、当前阶段、阶段判断、目标、不做的事、成功标准、登记的截止日期和交付物；缺失的值为空或 `unknown`。`snapshot` 包含生成时间、知识库版本、资料覆盖情况和盲区。
- **路线图**：`roadmap` 分为已确认的里程碑和候选里程碑。
- **能力**：`capabilities` 中每条能力有 ID、名称、范围，以及五个独立的阶段状态：

  | 字段 | 表示 |
  | --- | --- |
  | `decision_state` | 需求或决定是否明确 |
  | `implementation_state` | 是否已经实现 |
  | `test_state` | 是否验证通过 |
  | `release_state` | 是否已经发布 |
  | `runtime_state` | 运行中是否正常 |

  缺失的状态记为 `unknown`。每条能力还可以带最近核实时间、证据引用和尚未消除的不确定性。五个阶段分开显示，不合并成一个完成百分比，因为决定、实现、验证、发布和运行是五件不同的事。
- **架构**：工作流、组件、接口、依赖和无人负责的部分。
- **决策**：按 `active`（有效）、`provisional`（暂定）、`superseded`（已被取代）、`conflicted`（相互冲突）分组。
- **人员与其他**：`lineage` 的摘要与 Resident 列表；风险、开放问题、承诺事项、材料时间线和知识健康指标；`human_review` 的待审数量和最优先事项（每项可以是一段文字或一个对象）。

## 组件与流程

| 组件 | 职责 |
| --- | --- |
| Project 指令 `GetProject` | 确认 Project 已登记、调用者通过 `GetProject` 读取授权，调用读取器，把项目状态随项目记录和 Summary 一起返回 |
| 项目状态读取器 | 限定文件路径、解析 YAML、按顺序检查、规范字段、生成概况 |
| Project 页面 | 按 `status` 显示完整 Overview、缺少分区的提示或错误状态 |
| 资源解析 | 把证据引用变成当前用户有权打开的资源标签或预览入口 |

```text
用户打开 Project 详情的 Overview
        ↓
GetProject：确认 Project 与调用者读取授权
        ↓
读取器从 Project 目录读取 project_state/PROJECT_STATE.yaml
        ↓
依次检查：文件 → 编码 → YAML → 顶层对象 → 格式版本 → Project ID → 必需分区
        ↓
生成 portal.project-page.v2 页面数据和概况
        ↓
页面按 ready / partial / missing / invalid / error 显示
```

项目事实由维护者在文件中编辑，Portal 只读取和转换，不修正文件内容。页面只依赖固定的页面数据格式，文件格式升级时只需要调整读取器。

## 权限与维护

- Overview 只显示项目状态，没有编辑控件。项目维护者或 Agent 直接在 Project 目录中编辑文件，这是项目自己的维护流程，不经过页面。
- 读取器进程能读取文件，但调用者须通过 `GetProject` 的读取授权才能看到结果；证据引用另外按各资源的权限解析。

## 出错后怎样恢复

- 读取器不缓存结果，维护者修好文件后，页面刷新即显示新内容。
- `partial` 时其余内容照常显示，维护者可以逐项补齐缺少的分区。
- 格式、YAML 和 Project ID 错误都有固定的 `source.code`，便于定位。
- Portal 不自动转换不支持的格式版本；需要升级时，由维护者修改文件中的 `schema_version` 和对应内容。
