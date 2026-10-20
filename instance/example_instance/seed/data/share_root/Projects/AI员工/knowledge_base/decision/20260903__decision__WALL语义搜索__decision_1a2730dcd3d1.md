---
kb_id: decision_1a2730dcd3d1
type: decision
project: AI员工
created_at: '2026-09-03'
version:
  number: 14
  created_at: '2026-09-03'
  created_by:
    user: ''
    agent: ''
  updated_at: '2026-10-19T08:32:09Z'
  updated_by:
    user: ''
    agent: resident_b1a8c2ce10fa
importance: 8
metaness: 2
ai_generated: true
links:
  source:
  - ${code_root}/management_portal/society_service/service_invocation_domain.py
  - ${code_root}/management_portal/static/wall.js
  - ${code_root}/management_portal/wall_backend.py
  - ${code_root}/management_portal/semantic_search_surfaces.py
  - ${code_root}/management_portal/shared_search_invocation.py
  - ${code_root}/management_portal/shared_search_adapter.py
  - ${code_root}/management_portal/board_semantic_search_backend.py
  - ${code_root}/management_portal/serve_management_portal.py
  - ${code_root}/tests/cases/test_board_semantic_search.py
  related:
  - decision_6591e7793116
  - decision_6e230393221c
  - decision_4a9ba5dfada6
  - decision_eb066fbea4ed
  - decision_06defc47e960
  supersedes: []
  derived_from: []
---
# WALL 语义搜索

WALL 工程墙提供“语义检索”：用户用一句自然语言描述想找的工作，例如“找出仍缺测试证据的发布风险”，Portal 把用户能看到的全部条目交给负责搜索的 Resident，由它挑出相关条目并给出分数和理由，页面再按结果筛选或重排原有卡片。与关键词搜索不同，它不要求条目中出现相同的词。

本 Design 只定义 WALL 的工程范围、候选条目、结果在墙面上的展示和接口。搜索身份、共享 Resident 调用、输入隔离、运行代次和恢复机制的唯一权威为 decision_eb066fbea4ed。相邻主题：
- 各页面共享的语义检索交互（输入框、任务面板、搜索身份与管理规则）见 decision_eb066fbea4ed。
- WALL 的条目与交互见 decision_06defc47e960。
- Knowledge、Skill、Experiment、Paper 等资源页面的语义搜索使用同一套执行方式，但候选目录和接口由各自的 Design 说明。

## 页面上看到什么

WALL 在原有的卡片列表、状态筛选和关键词输入之外，增加以下控件：

| 控件 | 用途 |
| --- | --- |
| 查询输入 | 用自然语言描述要找的工作 |
| “语义检索”按钮 | 创建一个后台搜索任务，页面不需要等待它完成 |
| 任务面板 | 列出当前工程在 WALL 中的搜索任务，显示状态、当前步骤、进度和结果数 |
| 刷新图标 | 用同一个任务重新搜索一次 |
| 删除图标 | 删除任务并从列表中隐藏 |
| 匹配结果 | 原有卡片保持不变，额外显示 0–100 的分数和匹配理由 |

每个任务的页面类型固定为 `wall`，并属于一个已登记的工程；各工程的任务分开显示，候选和结果均来自当前工程墙面。

**搜索任务**有自己的统一 ID（`search_id`），与被匹配的条目 ID 分开保存。搜索只引用已有条目：它不创建条目，不修改条目状态，也不会让用户看到自己本来看不到的条目。

## 设计点

### 创建与查看任务

**用户提交查询后，服务立即保存一个排队中的任务并在后台开始搜索；页面通过任务列表查看进度。**

- 查询去掉首尾空白后不能为空，最多保存 800 个字符。
- 创建请求返回任务当时的状态：可能仍在排队，也可能已经开始运行，后台启动失败时为 `error`。页面随后读取任务列表跟踪进度，一次请求不会等待整个搜索完成。
- 任务没有所有者列表。能阅读该工程的用户都可以查看、刷新或删除工程中的任务；权限系统开启后，统一资源入口再按当前模板判定（decision_eb066fbea4ed）。
- 任务列表按工程分开，按更新时间从新到旧，每次最多列出 80 个。WALL 各工程共用的任务存储最多保留最近 300 个任务记录。

### 候选条目按创建者的权限选出

**Resident 只看到任务创建者有权看到的条目；权限在调用 Resident 之前检查。**

| 页面 | 候选来源 | 每个候选包含 |
| --- | --- | --- |
| WALL | 创建者可以阅读的指定工程墙面上的全部卡片 | 卡片 ID、工程、标题、类别、状态、重要度、模块路径、领域、出处、摘要前 800 个字符和正文前 4,000 个字符 |

- WALL 墙面还在读取工程文档时，任务以错误结束，并提示“等待墙面加载后刷新语义搜索”，服务不把尚未加载的墙面当作空列表。
- WALL 搜索只要求工程的阅读权限。

### 共享执行

WALL 使用 Portal 范围职责 `search.wall` 的 Resident。候选冻结、输入隔离、投递幂等、运行代次和重启后按原消息继续观察，统一遵循 decision_eb066fbea4ed；本篇不另设候选分批或 prompt 容量阈值。新运行提交完整候选目录，历史已冻结的多批次计划继续按原消息恢复。WALL 目录中的摘要和正文长度属于 WALL 候选字段的构造，不是共享适配器的批次限制。

输出只引用与查询相关的候选条目，允许零条结果。

### 结果检查

**服务只保留同时出现在本次输入中、并且创建者现在仍能看到的条目；模型写出的其他 ID 全部丢弃。**

- 同一个条目只保留一次；分数限制在 0 到 100；理由最多 500 个字符。
- 结果按分数从高到低排列，任务中最多公开 200 条。
- Resident 对查询的理解（`query_interpretation`）和补充说明（`notes`）各保留最多 1,000 个字符。
- 页面用结果中的条目 ID 找到已有卡片，显示页面自己的标题和 Project，不采用模型返回的标题。
- WALL 的结果与当前可读的墙面取交集后，继续叠加用户在墙面上选择的模块、重要度和阅读状态筛选，以及当前的排序方式。

### 刷新

**刷新已经结束的任务时，服务保留同一个 `search_id`，清空旧结果，按当前权限重新选出候选并重新搜索。**

- 任务仍在排队或运行时，刷新直接返回当前任务，不会同时启动第二轮。
- 每次刷新开始新一轮运行，任务的运行代次（`run_generation`）加一。上一轮尚未结束的工作即使之后完成，结果也不会写入任务。
- 因为候选重新按当前权限选出，创建者权限的变化在刷新后生效。

### 删除

**删除把任务标记为 `deleted` 并从列表中隐藏，同时取消仍在进行的 Resident 消息。**

- 删除后任务记录仍保留在存储中。之后到达的进度、完成或错误更新都不会改变删除状态。
- 刷新和删除都用 `search_id` 指定任务，并且必须与页面类型一致，并提交同一个工程，而且当前用户仍能阅读该工程。任务不存在、页面类型或工程不一致、已经删除时返回 404。

## 任务状态

| 状态 | 步骤与进度 | 含义 |
| --- | --- | --- |
| `queued` | 排队，0 | 任务已保存，等待后台开始 |
| `running` | 选出候选 10；投递和等待各批 15–90 | 后台正在工作，步骤中显示当前是第几批 |
| `done` | 完成，100 | 结果已检查并保存；零条结果也是成功 |
| `error` | 失败，100 | `error` 说明原因，例如启动失败、墙面未加载、Resident 工作失败或结果格式错误 |
| `cancelled` | 取消，100 | Resident 的这条消息被取消或已被新一轮运行取代 |
| `deleted` | 已删除，100 | 任务已删除，不再出现在列表中 |

每次状态变化都更新 `updated_at`，并发出语义搜索投影变化通知，打开的页面据此刷新任务面板。

## 组件与流程

| 组件 | 位置 | 职责 |
| --- | --- | --- |
| 页面 | `management_portal/static/wall.js` | 提交查询、读取任务列表、显示进度、分数和理由 |
| HTTP 路由 | `wall_backend.py` | 固定页面类型，取得登录用户，检查参数和工程 |
| 搜索任务 | `semantic_search_jobs.py`（所有语义搜索页面共用） | 保存任务、检查结果、刷新与删除；WALL 的搜索必须属于一个可读工程 |
| 候选目录 | `board_semantic_search_backend.py` | 按创建者权限选出 WALL 的候选 |
| 共享搜索执行 | `shared_search_adapter.py` 与 SocietyService 的服务调用 | 按 decision_eb066fbea4ed 执行完整候选输入并恢复历史已冻结运行 |
| 搜索 Resident | Portal 范围职责 `search.wall` 登记的 Resident | 阅读查询和候选，返回相关条目、分数和理由 |

```text
用户在当前工程提交查询 → 保存 WALL 搜索
        ↓
按创建者权限读取已加载的工程墙面 → 构造 WALL 候选
        ↓
共享搜索执行（decision_eb066fbea4ed）
        ↓
按当前可读墙面过滤结果 → 叠加墙面筛选与排序 → 展示分数和理由
```

先按权限选出候选、再交给 Resident，使 Resident 的能力与用户权限分开；任务先保存再运行，页面刷新、Portal 重启和较长的运行都不会丢失任务；结果检查保证模型写错的 ID 不会变成页面上可点击的条目。

## 数据位置

任务和冻结输入的存储权威见 decision_eb066fbea4ed 的“状态归属”：当前分别为 `semantic_search.jobs` 和 `semantic_search.run_inputs`。WALL 的页面范围为 `wall`，保留上限为 300；因删除或容量淘汰而移除的搜索，其冻结输入同时清理。运行记录不成为新的墙面条目。

任务对外公开的字段：

| 字段 | 含义 |
| --- | --- |
| `search_id`、`search_kind` | 任务 ID；`search_kind` 固定为 `semantic` |
| `surface`、`project`、`query` | 页面类型 `wall`；所属工程；查询 |
| `status`、`step`、`progress` | 状态、当前步骤和 0–100 的进度 |
| `created_by` | 创建者 |
| `created_at`、`updated_at` | 创建时间和最后更新时间（Unix 秒） |
| `result_count`、`results` | 结果数和最多 200 条结果，每条包含条目 ID、分数和理由 |
| `query_interpretation`、`notes` | Resident 对查询的理解和补充说明 |
| `error` | 失败原因，成功时为空 |

## 接口

| 页面 | 列出任务 | 创建 | 刷新 | 删除 |
| --- | --- | --- | --- | --- |
| WALL | `GET /api/wall/semantic-searches?project=<工程>` | `POST /api/wall/semantic-search/start`，`{project, query}` | `POST /api/wall/semantic-search/refresh`，`{project, search_id}` | `POST /api/wall/semantic-search/delete`，`{project, search_id}` |

查询为空、缺少 `search_id` 或 WALL 缺少工程时返回 400；任务不存在或不匹配时返回 404。任务创建之后发生的错误（启动、候选目录、Resident 或结果解析）写入任务的 `error`，任务仍可在列表中读取和刷新。

## 出错后怎样恢复

- **启动失败**：任务立即变为 `error`，步骤为 `launch error`，并记录原因。
- **服务重启**：任务记录和本轮输入都已保存。页面读取任务列表时，服务为仍处于 `running` 的任务恢复等待，继续观察已经投递的消息，不重新开始搜索、不重复投递。
- **Resident 工作失败**：任务变为 `error`，记录失败的消息 ID 和原因；用户刷新任务即可重试，刷新会开始新一轮运行。
