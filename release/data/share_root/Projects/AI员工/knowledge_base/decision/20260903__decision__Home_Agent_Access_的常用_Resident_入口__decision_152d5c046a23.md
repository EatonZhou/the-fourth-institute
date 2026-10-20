---
kb_id: decision_152d5c046a23
type: decision
project: AI员工
created_at: '2026-09-03'
version:
  number: 7
  created_at: '2026-09-03'
  created_by:
    user: ''
    agent: ''
  updated_at: '2026-10-19T08:45:56Z'
  updated_by:
    user: ''
    agent: resident_6199a7a36480
importance: 8
metaness: 1
ai_generated: true
links:
  source:
  - ${code_root}/management_portal/resident_discovery_client.py
  - ${code_root}/management_portal/resident_sidebar_backend.py
  - ${code_root}/management_portal/index.html
  - ${code_root}/management_portal/static/home.js
  - ${code_root}/management_portal/static/portal_home_components.js
  - ${code_root}/management_portal/static/portal_workbench.js
  - ${code_root}/management_portal/static/portal_conversation_store.js
  - ${code_root}/management_portal/static/portal_resident_discovery_service.js
  related:
  - decision_f560803947c6
  - decision_18abbdef3da3
  - decision_00546e28e1e0
  - decision_bd179998ba08
  - decision_ef80c6d3b5ee
  - decision_8fe87d812a4c
  - decision_bd5217d08c09
  - decision_9f6135cbbb22
  supersedes: []
  derived_from: []
---
# Home Agent Access 的常用 Resident 入口

Agent Access 是 Home 页上的一个小面板，让登录用户从首页直接进入三类对话：自己的个人问答 Resident、帮助挑选 Resident 的推荐 Resident，以及最近用过的 Resident。它位于 Home 的“Get Me Aligned”工作台中，与 Notice Board、Daily News 并列（Home 的整体布局见 decision_00546e28e1e0）。

Agent Access 只负责组织这些入口：点击任何一行都只是跳转到对应 Resident 的对话页面，不在 Home 上发送消息或启动模型。对话页面怎样工作由 Resident 聊天相关的 Design 说明（decision_f560803947c6）。

## 面板上的内容

**Resident** 是 Portal 中长期存在的 Agent 身份，每个 Resident 属于一个 Lineage（一组协作的 Resident）。**对话页面**是 Society 工作台中打开某个 Resident 聊天的页面，地址形如 `/lineage?resident=<Resident ID>&lineage=<Lineage ID>`。

面板从上到下显示三部分，每一个可点击的入口都是一行：左侧是标题和一行说明，右侧是箭头。

| 部分 | 标题 | 说明文字 | 点击后打开 |
| --- | --- | --- | --- |
| 个人问答 | Ask Me Anything | 打开个人 Agent | 当前用户的个人问答 Resident 的对话页面 |
| 推荐 Resident | Which resident? | 根据任务推荐合适的 Resident | 承担 Portal 职责 `resident-discovery` 的 Resident 的对话页面，用户可以在那里描述任务、请它推荐合适的 Resident（推荐流程见 decision_8fe87d812a4c） |
| 最近使用（Recently used） | 会话标题 | 活动状态和任务摘要 | 该 Resident 的对话页面 |

## 设计点

- **个人问答入口只在找到个人问答 Resident 时可以点击。** 页面读取用户的个人问答绑定，并在当前用户可见的 Resident 目录中找到这个 Resident 后，才显示可点击的“Ask Me Anything”。没有绑定、绑定读取失败或目录中找不到它时，这一行显示“当前暂不可用”，没有链接。
- **推荐入口只在推荐 Resident 可用时可以点击。** 页面从 Resident 目录的结果中取得承担 `resident-discovery` 职责的 Resident。职责没有登记、调用者无权读取它或推荐服务不可用时，这一行显示“当前暂不可用”。
- **最近使用列表最多显示 6 个不同的 Resident，最近活动的在前。** 列表来自用户的会话索引，同一个 Resident 只显示最靠前的一条；没有记录时显示“最近还没有打开过 Resident。”。
- **正在进行的会话在说明前显示活动状态。** 会话状态为 `queued` 时显示“排队中 · ”，`dispatching` 时显示“启动中 · ”，`running` 时显示“运行中 · ”；其他状态不显示前缀。
- **最近使用的 Resident 不在目录中时仍然显示。** 这一行使用会话索引中保存的标题、摘要和链接，用户可以继续从这里进入；进入对话页面时，服务端再检查用户能否访问这个 Resident。
- **会话索引变化时列表立即更新。** 其他页面或服务端推送改变了会话索引，Agent Access 用已经读取的 Resident 目录重新绘制列表，不重新请求目录。
- **加载失败只影响这个面板。** 读取当前用户失败时显示“当前用户读取失败，无法加载个人 Agent 入口。”；读取 Resident 目录失败时显示“Resident 入口读取失败：<原因>”。Home 的其他面板照常工作。

## 任务摘要从哪里来

每一行最近使用的说明文字尽量显示一段任务摘要，让用户想起这个 Resident 在做什么。页面按下面的顺序取第一个非空的值，并把连续空白合并成一个空格：

1. Resident 上下文服务生成的当前关注点（见下文）；
2. 会话索引记录中的 `current_focus`；
3. 记录中结构化摘要对象里的 `current_focus`、`current_goal` 或 `summary`；
4. 记录中文字形式的 `summary`；
5. 记录中的 `description`、`current_job` 或 `last_user_message`；
6. Resident 目录中这个 Resident 的摘要；
7. 都没有时显示“暂无任务摘要”。

第 2 到第 5 项是会话索引中不同来源的记录使用的字段名，页面只读取它们来选择显示文字。

## 实现：页面怎样组合这些入口

Agent Access 的代码在 `static/home.js` 中，它组合三份数据：

| 数据 | 来源 | 提供什么 |
| --- | --- | --- |
| 个人问答绑定 | 用户的 UI 界面关联，命名空间 `lineage-workbench.personal-ama`，键 `primary`（decision_bd179998ba08） | 个人问答 Resident 的 ID |
| Resident 目录 | `GET /api/agent-workbench/residents`，服务端按用户缓存 15 秒 | 当前用户可见的 Resident 及其派生出的 Resident、每个 Resident 的标题和 Lineage；个人问答 Resident 和推荐 Resident 也会加入目录结果；推荐 Resident 另外放在 `recommendation_resident` 字段 |
| 会话索引 | 浏览器中的 Conversation Store（`static/portal_conversation_store.js`），数据来自 `GET /api/agent-conversations`（decision_18abbdef3da3、decision_15f7df737ed5） | 用户参与的会话，按最近更新时间排序；键以 `resident:` 开头的是 Resident 会话 |

加载顺序：

```text
Agent Access 面板第一次显示，且当前用户已经读取成功
  → 同时发出：读取个人问答绑定、读取 Resident 目录
  → 目录返回 ready=false（服务端还在准备）时等待后重试：
    第一次等 250 毫秒，之后每次加倍，最长 2 秒
  → 两份结果都到齐后绘制面板
  → 对当前显示的最近使用行，批量请求任务摘要
```

每次加载有一个递增的序号。较早发出、较晚返回的结果发现序号已经过时，就直接丢弃，不覆盖新结果。

任务摘要由 `POST /api/agent-workbench/resident-discovery/contexts` 取得。Portal 以当前用户身份把请求转给 SocietyService 中的 Resident 发现模块（decision_8fe87d812a4c），它为每个 Resident 返回一段结构化摘要，页面取其中的 `current_focus`。每个 Resident 在当前页面中只请求一次，结果保存在内存里；请求失败时静默忽略，行上继续显示其他来源的摘要。

## 链接怎样生成

- 会话索引记录中保存了站内地址（以 `/` 开头）时，直接使用这个地址，它可能带有目标页面需要的更多上下文。
- 没有保存地址时，页面用 Resident ID 和 Lineage ID 生成 `/lineage?resident=<Resident ID>&lineage=<Lineage ID>`；不知道 Lineage 时省略 `lineage` 参数。

Home 不代替目标页面检查权限。用户点击后，对话页面按当前用户读取 Resident，无权访问时由对话页面显示错误。
