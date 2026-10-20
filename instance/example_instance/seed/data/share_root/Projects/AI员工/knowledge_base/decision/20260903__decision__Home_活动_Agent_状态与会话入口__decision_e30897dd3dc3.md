---
kb_id: decision_e30897dd3dc3
type: decision
project: AI员工
created_at: '2026-09-03'
version:
  number: 23
  created_at: '2026-09-03'
  created_by:
    user: ''
    agent: ''
  updated_at: '2026-10-19T11:33:54Z'
  updated_by:
    user: ''
    agent: resident_511e229bc8c3
importance: 8
metaness: 1
ai_generated: false
links:
  source:
  - ${code_root}/management_portal/serve_management_portal.py
  - ${code_root}/management_portal/society_service/active_agent_projection.py
  - ${code_root}/management_portal/index.html
  - ${code_root}/management_portal/static/home.js
  - ${code_root}/management_portal/static/portal_home_components.js
  - ${code_root}/management_portal/static/portal_workbench.js
  - ${code_root}/tests/cases/test_agent_status_active_agents.py
  - ${code_root}/tests/cases/test_management_portal_resilience.py
  - ${code_root}/tests/cases/test_home_frontend_contract.py
  - ${code_root}/tests/cases/javascript/home_activity_links.cjs
  related:
  - decision_4dea0db1f45a
  - decision_3dd52c0692c1
  - decision_ef80c6d3b5ee
  - decision_6ad0384e020d
  - decision_0f57656a2acd
  - decision_abaedeefca6f
  - decision_5bc3fdfc7eec
  supersedes: []
  derived_from: []
---
# Home 活动 Agent 状态、头像与会话入口

Home 首页 Agent Status 区域的 **AI Activity & Accounts** 子面板最上方有一行**活动图标带**。它让登录用户一眼看出哪些 Resident 正在工作、有多少消息在排队、哪些对话有未读回复，并可以点击图标直接回到对应的对话。

本 Design 说明图标带显示什么、图标怎样分组、谁能看到身份和链接，以及图标带怎样保持最新。面板在 Home 中的位置、折叠和关闭由共享 Workbench 负责（decision_00546e28e1e0）；Resident 怎样处理消息见 decision_c524c775e0be；账号怎样分配任务见 decision_3dd52c0692c1。

## 基本概念

- **活动**：一条正在被处理或等待处理的消息。Resident 正在处理（`running`）或正在停止（`stopping`）的那条消息是一条“运行中”的活动；它队列里等待的每条消息各是一条“排队”活动。
- **未读入口**：Resident 已经空闲、但当前用户还没读过它最新回复的对话。未读状态来自 Conversation Store（decision_18abbdef3da3）。
- **执行账号**：承担 Resident 工作的模型账号或执行后端，如 Codex 的 `codex_zero`、`codex_one`，或 Claude Code 的 `claude_one`。一个 Resident 的活动归入它当前 Session 绑定的账号；读不到时归入 `other`。
- **固定头像**：每个 Resident 的一个深海生物像素图形或上传图片。第一次被看到时随机分配图形，此后只在有人修改头像时改变（decision_5bc3fdfc7eec）。

## 用户看到什么

**活动图标带**是一行可换行的 34×34 像素深海生物图标。没有活动和未读时，这一行不显示。整行的辅助说明给出运行中、排队、未读的数量和已知的总并发上限。

每个图标对应一条活动或一个未读入口，外观表示状态：

| 外观 | 含义 |
| --- | --- |
| 较亮，上下游动 | 消息正在处理，或停止请求还在处理中 |
| 较暗、偏灰、静止 | 消息正在排队 |
| 未读外框 | Resident 已空闲，有未读回复 |
| 金色或彩色发光 | 头像的稀有度较高，只是装饰 |

一个 Resident 正在处理一条消息、后面还有两条在排队时，只出现一个游动的图标；它没有消息在处理、只有两条在排队时，出现两个静止的图标。图标都使用这个 Resident 的固定头像。

**账号组**把同一执行账号的图标放在一起，组内依次是运行中、排队和未读。同一账号的图标连续排列，账号 ID 居中放在整组图标的正下方；相邻两组之间保留细分隔线。没有任何图标的账号不显示。

**数量省略**：同一账号组中某一种状态超过 18 个时，只画 18 个，其余显示为 `+N`。

**提示与链接**：当前用户能读取这个 Resident 时，鼠标悬停显示 Resident 标题和稀有度，点击进入该 Resident 的对话。不能读取时，图标名称统一为 **Agent**，保留状态外观，但不能点击。

## 设计点

- **只显示当前仍在进行的活动和未读回复。** 已完成或失败的消息不留在图标带中；Resident 空闲且没有未读时不出现。
- **停止中的消息按运行中显示。** 停止完成前它仍占用执行位置；停止完成后图标消失。
- **正在运行的 Resident 只占一个图标。** Resident 有消息在处理时，活动汇总只选择它的运行中条目用于绘制；筛选发生在返回条目数量限制之前。排队总数仍包括它等待处理的消息，用于整行辅助说明。
- **没有运行中消息的 Resident，每条排队消息各有一个图标。** 用户看到的是这些 Resident 的真实队列长度，而不是排队的 Resident 数。
- **每个图标的外观只由它那条消息的状态决定。** 正在创建 Session 的排队消息按运行中显示。
- **未读入口只给没有活动的 Resident。** 一个 Resident 有运行或排队的消息时，只显示活动图标，不再为它显示未读图标。未读图标放在它所属账号的组里。
- **账号组按 Agent Status 账号列表的顺序排列。** 这个顺序与同一面板中账号额度行的顺序一致；不在列表中的账号（如 `other`）排在后面。
- **点击图标只打开对话。** 链接带有 Resident ID、Lineage ID 和一次性参数 `open_chat=1`，Society 页面打开并聚焦对话后去掉这个参数（decision_f560803947c6）。点击不会创建 Session，也不会触发新的工作。
- **默认头像保留真实 Resident 的交互入口。** 头像数据缺失或无效时复用普通小动物，标题、Resident 身份、鼠标点击和键盘打开对话与有效头像一致。每个图标来自真实活动条目；条目未返回时只以 `+N` 表达数量，不按数量补画没有身份的图标。
- **同一 Resident 始终使用同一个头像。** 头像按 Resident ID 分配一次并保存，新消息、换 Session、换观看者都不会重新抽取。修改头像（UpdateResident 的 `avatar`）后，图标带和背包都改用新头像；上传的图片以图片地址（`avatar_url`）代替图形序号和稀有度，没有稀有度光效。
- **稀有度只影响外观。** 稀有度分为 `R`、`SR`、`SSR`、`SSSR`，显示在提示中；SSR 和运行中的 SSSR 有发光效果。稀有度与调度和权限无关。
- **所有 Resident 的活动都计入数量，权限只决定是否公开身份。** TAU、后台流程和其他页面创建的 Resident 与普通 Resident 一样出现在图标带中。当前用户不能读取的活动，服务端去掉 Resident、Lineage 和会话身份，标题固定为 `Agent`，不生成链接，但仍计入运行和排队数量。
- **折叠、关闭或移动面板只改变布局。** 任务状态、未读状态和执行都不受影响。

### 保持最新

- **活动变化后，Home 重新读取完整的活动状态。** Resident 开始、排队、停止或结束工作时，服务端发出“active-agents 已变化”的通知；通知本身不带状态。Home 把 1.5 秒内的多次通知合并为一次读取。
- **结果不变时保留现有图标。** 新旧内容相同时不替换页面元素；账号额度刷新重绘面板时，也把原有图标带移回，避免打断焦点和页面位置。
- **活动状态尚未就绪或读取失败时自动重试。** 服务端返回尚未就绪时，750 毫秒后重读；请求失败时从 1 秒开始每次加倍，最长 30 秒，成功一次后重新计数。
- **每 5 分钟完整读取一次。** 即使事件连接正常也会读取，浏览器断线或漏收通知后最终仍能显示正确状态。浏览器标签页隐藏时暂停快速重试，重新可见后，如果之前未就绪或失败就立即重读。

## 活动状态怎样形成

| 部分 | 负责 |
| --- | --- |
| Resident 的执行记录 | 每个 Resident 当前的工作状态、正在处理的消息和排队消息 |
| SocietyService 的活动汇总（`active_agent_projection.py`） | 把各 Resident 的活动整理成活动条目、账号组和数量 |
| 权限检查 | 按当前用户判断哪些活动可以公开身份和链接 |
| 头像存储（`resident_avatar.py`） | 每个 Resident 的固定头像 |
| Home 活动控制器（`home.js`） | 画图标、合并未读入口、接收通知、重读和重试 |

```text
Resident 的执行记录（运行中消息 + 排队消息）
          │
          ▼
SocietyService 整理成活动条目和账号组
          │
          ▼
按当前用户去掉不可读活动的身份
          │
          ▼
Home 加上未读入口，画出图标带

执行状态变化 → 发出 active-agents 通知 → 作废该用户的旧缓存 → Home 重新读取
```

Home 只读取这份汇总，不查看模型进程，也不根据动画推断状态。执行后端改变内部结构时，图标带的含义保持不变。

### 活动条目

SocietyService 读取每个 Resident 当前的活动：正在处理的消息生成一条运行中条目（`running` 和 `stopping` 都算运行中），队列中每条消息各生成一条排队条目。空闲且没有排队消息的 Resident 不生成条目，也不读取它的 Session 账号。

条目的 `session_title` 使用 Resident 标题。

可读的条目包含 `type`、`status`、`resident_id`、`lineage_id`、`title`、`session_title` 和 `account_id`。不可读的条目只保留显示状态所需的字段，`title` 为 `Agent`，不包含 `resident_id`、`lineage_id`、owner、消息作者和 `session_title`。条目内部用消息 ID、创建时间或 `unknown` 排序并决定匿名图标的样子；匿名图标不使用 Resident 的真实头像。

后端在完整活动快照中选出正在运行的 Resident，再从用于绘制的排队条目中排除这些 Resident；随后才匿名化并在全局及每个账号组中各最多返回 48 条运行中和排队条目。全局和组内的 `queued` 保留全部排队消息数，`queued_display_count` 表示可绘制的排队条目总数，`queued_runs` 是这些条目的有界列表。Home 直接消费这些条目，不再依据截断列表重复筛选。每组每种状态最多画 18 个真实条目，其余以 `+N` 显示；条目不足时仍只画实际返回项。总并发上限是各执行账号并发上限之和，包含独立 API 账号（默认并发 8），读取账号失败时为 1；响应中没有这个值时，Home 显示 8。这些值只用于显示。

Portal Web 为每个用户缓存最近一次读取结果 5 秒，合并同一用户的并发读取。后台重建期间返回旧图标并标记 `refreshing`；重建失败时保留旧图标、返回 `error`，页面按失败退避重试。向该用户发送 active-agents 变化通知之前，先作废这份缓存，并让之后的读取不再等待通知前已经开始的那次读取，这样通知触发的读取一定拿到新结果。

### 未读入口

Home 作为会话索引的独立订阅方，在 Mailbox 收起时继续读取和接收索引变化；关闭 Mailbox 不会停止 Home 所需的未读入口更新。索引无消费者时才停止列表刷新，其他页面仍可只读取未读总数。

Home 从 Conversation Store 中取出当前用户未读、且状态不是运行或排队的 Resident 对话，排除已经有活动图标的 Resident，按对话记录中的 `account_id` 放进对应账号组；没有账号的放入 `other`。Conversation Store 的对话记录在权限过滤后批量读取账号，账号取 Resident 当前 Session 绑定的账号，没有时取 Resident 选定的账号或默认账号，各 Provider 都使用解析后的执行账号 `account_id`。账号变化只更新记录内容，不产生新的未读。

### 头像与稀有度

头像分配、稀有度概率、上传、持久存储与跨位置更新由 decision_5bc3fdfc7eec 统一定义；Home 通过 `PortalResidentAvatar` 消费已确定的头像并叠加本页的状态动画和稀有度光效。活动投影只为可读 Resident 获取真实头像；匿名条目继续使用匿名图案。Home 沿用图集位置：0–35 为普通图形，36–41 为 SSSR 图形。无效头像保持原绑定并使用普通海洋生物回退，不显示圆形占位、不为回退图案赋予稀有度，也不因展示失败重抽或覆盖数据。
