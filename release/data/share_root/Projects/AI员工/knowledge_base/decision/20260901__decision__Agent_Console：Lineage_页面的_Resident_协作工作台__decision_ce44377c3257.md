---
kb_id: decision_ce44377c3257
type: decision
project: AI员工
created_at: '2026-09-01'
version:
  number: 38
  created_at: '2026-09-01'
  created_by:
    user: ''
    agent: ''
  updated_at: '2026-10-19T08:25:48Z'
  updated_by:
    user: ''
    agent: resident_511e229bc8c3
importance: 9
metaness: 2
ai_generated: true
links:
  source:
  - ${code_root}/management_portal/http_agent_workbench.py
  - ${code_root}/management_portal/static/portal_conversation_store.js
  - ${code_root}/management_portal/resident_chat_projection.py
  - ${code_root}/management_portal/portal_chat_workspace_projection.py
  - ${code_root}/management_portal/static/portal_chat_workspace.js
  - ${code_root}/management_portal/lineage.html
  - ${code_root}/management_portal/static/lineage_workbench.js
  - ${code_root}/management_portal/static/portal_navigation.js
  related:
  - decision_a6bebf4ac283
  - decision_18abbdef3da3
  - decision_f25266e5a0e7
  - decision_f560803947c6
  - decision_4348ed990621
  - decision_0571f3741e23
  - decision_c524c775e0be
  - decision_538e155e1102
  - decision_9f6135cbbb22
  - decision_79de71d9890d
  - decision_15f7df737ed5
  - decision_61e50306cc79
  supersedes: []
  derived_from: []
---
# Agent Console：Society 页面的 Resident 协作工作台

页面和顶栏入口统一命名为 **Society**，中文翻译显示 **社群**；相关服务称为 **SocietyService**。资源实体仍称 **Lineage**，创建、绑定、Fork 等资源操作不改名。既有 `/lineage` 地址、资源类型/ID及导航和布局持久化标识继续兼容；页面名称与资源名称分别翻译。

Agent Console 是 Portal 的 Society 页面（地址 `/lineage`）中的工作台。用户在这里找到自己的 Resident，与它对话，查看它的工作成果，打开终端，并在 HATG 图中查看和修改它所在 Lineage 的协作关系，不需要在多个页面之间切换。

先说明几个对象：

- **Resident**：有稳定身份、可以跨多次模型会话持续存在的 Agent，ID 形如 `resident_1a2b3c4d5e6f`。
- **Lineage**：一组 Resident 及它们之间的事件、动作和连线组成的协作单元。每个 Resident 只属于一个 Lineage。
- **HATG 图**：Lineage 的可视化编辑器，节点是 Resident 和组件，连线是它们的协作关系。HATG 是 Human–Agent Trajectory Graph 的缩写。
- **Workbench 与 Panel**：Portal 的通用工作台框架。Workbench 把页面划分成可以拖动、分栏、隐藏和恢复的区域，每个区域中放一个 Panel（带标题栏和标签页的独立面板）。Workbench 的通用布局与生命周期见 decision_4348ed990621，Panel 的组合协议见 decision_538e155e1102。

本 Design 负责 Agent Console 在 Society 页面中的组成：有哪些区域，用户选中一个 Resident 后各区域怎样切换到它，状态由谁保存，服务不可用时怎样恢复。消息列表、输入框和会话操作本身由 Chat Workspace（decision_f560803947c6）负责；同一种 Panel 打开多个实例的规则见 decision_79de71d9890d。

## 页面组成

页面从左到右分成活动栏和 Workbench 两部分。

**活动栏**是最左侧的一列竖向按钮，分两段：

- 上段是四个视图按钮，切换导航 Panel 显示的内容：Agent（按 Lineage 分组的 Resident 列表）、Chatroom（用户聊天室，见 decision_a6bebf4ac283）、我的收藏、聊天历史。Agent 和我的收藏两个视图的顶部显示 Which Resident，以及当前身份已解析出的个人助手入口（decision_3e07c6d35cac）；不再固定插入旧 AMA 快捷卡片。由定时或外部事实触发的 Resident 在 Home 的“定时与自动触发”面板中查看（decision_481dc4e6292c），导航中没有为它们单设的视图。
- 下段是“最近 Resident 快捷入口”，列出用户最近发送消息、Attach 或 Fork 后的 Resident，点一下即可回到对应会话（见下文设计点）。

**Workbench** 中有五种 Panel：

| Panel | 用户看到什么 | 跟随什么目标 |
| --- | --- | --- |
| 导航 | 活动栏所选视图的列表：Lineage 卡片及其中的 Resident 卡片、聊天室、收藏或聊天历史。 | 当前 Lineage 和选中的 Resident |
| Chat Workspace | 当前 Resident 的消息、运行状态、输入框、附件和等待队列。Resident 安装了 Watchboard 时，Chat 旁边另显示一块监控板。 | 已经加载完成、可以发送消息的会话目标 |
| 预览 | 文档与资源的预览和编辑区，左侧是 Resident 工作区的文件浏览器。 | 当前要查看的文件或资源 |
| 终端 | 在 Portal 主机上打开的命令行，默认隐藏。只给能执行 OpenTerminal 的 User（默认只有 Portal 管理员，decision_4527448ad07f）：`/api/me` 的 `access.allows.OpenTerminal` 为假时，页面没有这个组件，布局菜单不提供新建终端，保存过的终端标签也不恢复。 | 当前 Resident（没有选中时为 Portal 主机） |
| HATG | 当前 Resident 所在 Lineage 的协作图。 | 当前 Lineage 与图中选中的节点或连线 |

Workbench 提供五种预设布局：完整工作台、对话、对话与成果、成果查看、HATG 协作。第一次打开页面使用“对话与成果”（导航、Chat 与预览，隐藏 HATG）。用户调整后的布局按页面偏好保存（decision_9f6135cbbb22）。

Chat 的身份展示统一遵循 decision_f560803947c6：普通 Resident 标题区显示执行账号与 Resident ID，Provider Session ID 在 Resident Menu → Settings 中查看和复制；Family 门面不显示执行账号。Console 不另定义一套身份字段位置。

Watchboard 是 Resident 可以安装的监控板扩展，用来展示和操作这个 Resident 背后的脚本、配置与状态。它的安装、命令和应用规则见 decision_0571f3741e23。

## 设计点

### 导航卡片的副标题

- **Lineage 卡片的副标题显示类型“Lineage”和成员数，例如“Lineage 8”。** 悬停提示为“8 个 Resident”；卡片展开或收起时成员数不变。
- **导航列出用户能访问的全部 Lineage 和 Resident。** Portal 不保存“在导航中显示或隐藏”的偏好。收藏是另一回事：它只决定“我的收藏”视图和星标，不让未收藏的对象从导航中消失（decision_6eac174d85bf）。
- **成员数按 Resident ID 去重。** 普通成员和 Fork 出的成员都计入。成员数取服务端返回的成员数量与导航中实际列出的 Resident 数中较大的一个，侧栏只拿到部分成员时也不会显示得偏小。
- **Lineages 与 My Favorite 导航不再固定显示 Ask Me Anything 快捷卡片。** 桌面和移动端共用这一规则；已有会话、个人助手与私人 Family、Home 入口和按地址打开 AMA 的能力继续保留（decision_bd5217d08c09）。
- **Which Resident 和个人助手卡片的副标题只显示类型“Resident”。** 这些快捷卡片直接打开对应 Resident，不在卡片中展开成员列表，因此不显示成员计数。
- **导航不显示 Resident 运行状态。** 卡片上没有接单状态、工作动效或未读标记，侧栏也不订阅 Resident 状态；运行状态在 Chat Workspace 中查看。
- **副标题不放其他信息。** 所属 Project、创建来源、模型后端名称、时间和消息数都不出现在导航副标题中：归属由列表层级表达，模型后端名称（例如 Codex）显示为 Chat 中 Agent 消息的署名，来源和时间在 Resident 详情中查看。

这些规则只决定导航怎样显示，不改变成员关系、收藏和接单开关。页面右边缘“我的 Mailbox”的显示规则见 User Mailbox（decision_15f7df737ed5）。

### 选择与打开 Resident

- **单击 Resident 卡片，Chat 切换到这个 Resident。** Console 先让 Chat Panel 获得焦点（即使预览 Panel 当时在前面），再确认用户仍能访问该 Resident，读取它的会话，最后才把它设为 Chat 的目标。HATG Panel 同时切换到这个 Resident 所在的 Lineage。
- **再次单击当前 Resident 不重新加载。** Chat 已经显示这个 Resident 且加载完成时，Console 只重新聚焦 Chat，并在 Lineage 变化时同步地址和 HATG。
- **双击 Resident 卡片，在新标签页中打开它的 Chat。** 同一个 Resident 可以同时在多个 Chat Panel 中打开；每个 Chat 实例各自保存输入草稿、附件选择、队列操作和滚动位置，互不覆盖。多实例规则见 decision_79de71d9890d。
- **地址栏始终指向当前目标。** 选中 Resident 时地址为 `/lineage?resident=<Resident ID>&lineage=<Lineage ID>`，选中聊天室时为 `/lineage?chatroom=<Chatroom ID>`。刷新页面或从外部链接进入时，Console 按地址中的参数重新定位同一目标。`program_item=<对象 ID>` 让 HATG 打开后定位组件、连线或 Function Block；定位 Family 时进入该 Family 的独立视图，包括展开必要的外层 Family。`view=hatg` 与 `lineage` 一起构成显式画布入口：登录身份与布局偏好恢复后，应用已有的“HATG 协作”布局并激活画布，避免隐藏状态或挤窄的恢复布局遮挡目标；沿用布局锁约束。未指定 `view=hatg` 的链接保持原有布局恢复行为；`open_chat=1` 让页面打开后直接显示 Chat，使用后从地址中去掉。
- **Ctrl+~（反引号键）回到这个标签页上次离开的 Resident。** macOS 上也按 Control 键：⌘ 加反引号键和 ⌘+Tab 被系统用来切换窗口和应用，Ctrl+Tab 被浏览器用来切换标签页，网页都收不到；浏览器或系统不拦截这些键时，它们也有同样作用。`/portal-help` 只写“Ctrl + 反引号键”，并说明这个键在 Esc 下方、数字 1 左边；Chat 的 Markdown 不能在代码格式里显示反引号，帮助用文字称呼这个键（TFI portal 2026-10-13）。所有带 Portal 导航的页面都注册这个快捷键，公开的新用户指引页也注册；延迟加载脚本的页面在注册表加载之后注册。每个浏览器标签页单独记录“上一个界面”：在 Society 页面切换 Resident 时，刚离开的 Resident 记为上一个；离开页面时，记录跟随地址栏的最终地址。刷新页面后，Console 按这份记录在页面内切回上一个 Resident；从其他页面按快捷键时，打开离开 Society 页面时正在显示的 Resident。资源详情等内嵌框架加载的页面和其他标签页不改变这份记录。

### 加载与会话操作

Console 负责切换期间冻结旧目标、聚焦正确 Chat，并把当前目标交给共享 Chat 组件。加载失败、消息发送中的临时显示、停止与队列交互由 decision_f560803947c6 统一规定；共享订阅、发送受理与事件/回执合并由 decision_18abbdef3da3 负责。发送中的气泡不表示服务端已受理，Console 不再以等待回执为由禁止该呈现。

Fork、清空上下文与 Session 操作沿用 Chat 的入口以及对应 ISA（decision_d19b9909a674、decision_2fe6fd5367fb），只作用于实例绑定的目标。隐藏与多实例的订阅、草稿和滚动规则仍由 Chat Workspace 负责。

### 最近 Resident 快捷入口

活动栏下段列出用户最近发送消息、Attach 或 Fork 后的 Resident，方便用户回到刚刚操作过的会话。记录保存在当前浏览器中，每位登录用户一份，由 Conversation Store 维护（decision_18abbdef3da3）；活动栏负责显示与打开。

- **消息被服务端接收后，收件人移到快捷入口首位。** 进入等待队列也算接收。只选中、打开会话或发送失败都不改变顺序。同一个 Resident 只保留一个入口。转发消息的目标 Resident 同样计入。成功 Attach 的目标和成功 Fork 创建的 Resident 也移到首位。
- **记录属于发送时的收件人。** 发送时固定目标 Resident、Lineage 和显示标题；等待服务端确认期间，用户即使切换到别的 Chat，记录仍归本次发送的收件人。
- **快捷入口独立于导航列表。** 导航中还没有列出的 Resident，只要发送得到确认，仍保留快捷入口，并用记录中的标题显示；切换会话或重新加载页面后继续保留。
- **最多保存 32 条，活动栏显示最近 8 条。** 记录按最近一次发送确认、Attach 或 Fork 成功的时间从新到旧排列，其余记录通过“聊天历史”视图查看。
- **清空立即执行，6 秒内可以撤销。** 清空按钮固定在快捷入口末尾，用分隔线隔开，没有记录时隐藏。点击后不弹确认框，直接清空，并显示带“撤销”按钮的提示，持续 6 秒。逐项移除后显示“已从快捷栏移除”提示。移除和清空只改变当前用户的快捷入口记录，不删除 Resident、会话或未读状态。
- **入口能辨认身份。** 悬浮提示和无障碍名称包含完整标题、Resident ID 的尾号、最近使用时间和当前状态。两个入口的缩写相同时，入口另外显示 ID 尾号和按身份计算的色标：尾号用于文字辨认，颜色只作辅助。
- **入口显示未读和运行状态。** 有未读回复时右上角显示实心点；Resident 正在运行时左下角显示脉冲环，用户开启“减弱动画”偏好时改为静态环。未读和运行状态来自会话协议，快捷入口随记录变化立即更新。
- **可以只用键盘操作。** Tab 聚焦入口，Enter 打开，Delete 或 Backspace 移除；移除后焦点移到相邻入口，列表为空时回到“聊天历史”按钮。
- **打开入口时重新核对访问权限。** 点击入口时，Console 重新确认 Resident 当前所在的 Lineage 和用户的访问权限；服务端返回无权访问或不存在时，移除这条记录。快捷入口只用于导航，不授予任何访问权限。

### HATG Panel 的加载

- **HATG Panel 第一次显示时才载入图编辑器。** 在那之前它只是一个占位；显示后出现“正在加载 HATG · 图编辑能力仅在需要时载入。”，加载失败时显示“HATG 加载失败”和原因。隐藏的 HATG Panel 不加载。
- **编辑器脚本并行下载、按顺序执行。** 载入时浏览器同时预取全部脚本（D3、Lineage 程序与图模块、HATG 面板模块），再按依赖顺序执行，冷启动不再逐个串行下载约 1 MB 的脚本。之后读取图编辑模板（120 秒超时，超时可重试）和原语定义。
- **HATG 立即读取 Lineage，不等待页面其他内容。** 侧栏的 Resident 列表、会话和各类目录可能在负载下需要数秒，HATG 不等它们完成就读取 Lineage 目录和目标 Lineage；只有这次读取失败时，才等到 SocietyService 就绪（页面完成首轮加载或服务恢复）后再读一次。
- **同一目标只读取一次。** 编辑器启动和页面同步目标时可能对同一个 Lineage、Resident 和组件各要求打开一次；第二次要求在第一次还没完成时直接共用它，不再发出重复的 `GetLineage`。

### HATG 图的整理

- **连续整理可切换密度。** 当前页面首次整理采用舒展布局；按钮随后显示“紧凑整理”，再次点击收紧间距，再次点击“舒展整理”可恢复。按钮文字说明下一次操作，紧凑状态通过按下状态表达；各 Lineage 和 Family 独立视图分别记录本次页面的切换状态。
- **紧凑布局收小留白，保留卡片尺寸与拓扑关系。** 节点仍按分支归组，Leader 侧置，独立子图按可用画布的宽高比例排列，避免仅缩小整个图导致卡片难读。Family 独立视图中只整理该 Family，保留当前视图和外部节点位置。
- **位置沿用共享布局保存。** 整理结果通过现有 layout 保存入口写回对应对象，重新打开仍使用已保存的位置，切换周期重新开始。保存失败恢复先前的位置与密度，并保留重试入口。Workspace 投影视图沿用原有整理限制。

### 各 Panel 之间的隔离

- **每个 Panel 只处理自己目标的事件。** 某个 Resident 的消息事件只更新显示该 Resident 的 Chat；工作状态变化只更新该 Resident 的卡片和快捷入口。
- **HATG 修改 Lineage 使用 HATG ISA 指令。** HATG Panel 通过 Lineage 定义指令（decision_4637ca4a69e2）修改图；Console 只同步“当前选中的图节点或连线”，不代替 HATG 写入 Lineage。
- **预览和终端不改变会话目标。** 移动、关闭或切换预览与终端 Panel，Chat 仍显示原来的 Resident。

## 从选中 Resident 到显示会话

Workbench 中各 Panel 通过共享的上下文频道获得目标。Agent Console 使用其中三个互不覆盖的频道：

| 频道 | 含义 | 使用者 |
| --- | --- | --- |
| `conversationTarget` | 已经确认可以访问、会话数据已加载的 Resident 与 Lineage。 | Chat Workspace |
| `inspectionTarget` | 当前要查看的资源、文件或运行对象。 | 预览 |
| `graphSelection` | HATG 图中当前选中的节点或连线。 | HATG |

一次选择按下面的顺序进行：

```text
导航或快捷入口给出 Resident ID
        ↓
Console 确认用户仍能访问它，并得到它当前所在的 Lineage
        ↓
共享 Chat 并行取得 Resident 身份/显示投影与 PortalResidentProtocol 会话快照
        ↓
身份投影给出 portal.chat_workspace.v3 合同；协议快照给出消息、状态与 controls
（Web 显示投影由 portal_chat_workspace_projection.py 生成）
        ↓
Console 确认这次读取仍对应当前选择，然后发布 conversationTarget
        ↓
Chat Workspace 显示消息、运行状态、输入框和可用操作
```

Chat 界面数据包含消息、运行状态、输入框状态、历史分页和可用操作。Console 只补充当前 Lineage 信息，不从活动事件拼凑消息。

按 Resident ID 核对访问权限和当前 Lineage 是只读定位：它读取该 ID 对应的注册状态并按调用者的读取权限过滤，不在查询中创建或初始化 Agent 数据。Chat 仍以自己的会话读取及权限检查为权威；导航快捷入口不能因为保存了旧 ID 而绕过重新鉴权。

在已经打开的 Console 中切换 Resident 时，导航和收藏列表的内容不因选中目标而变化；Console 只更新现有卡片的选中态与最近会话标记，保持卡片 DOM 和交互处理器不变。通过权限核对后，Console 先启动 Chat 的正常读取，再同步 HATG 和导航显示状态。侧栏目录投影的定时刷新独立于这次选择，不作为会话读取的前置步骤；会话数据到达前，旧目标的发送操作仍保持冻结。

**过时结果在各自的目标边界丢弃。** Console 用选择请求序号保护导航与共享上下文；Resident Chat Panel 用自身目标修订号保护异步加载与订阅。组件内容签名只用于避免重复呈现，不能代替目标有效性检查。宿主的选择和多个 Chat 实例的绑定遵循 decision_79de71d9890d、decision_f560803947c6。

**相同读取由共享状态层合并。** Resident 身份投影与会话协议分别管理在途读取、身份失效及事件缺口补读，见 decision_f560803947c6、decision_18abbdef3da3。Console 负责当前选择与挂载，不另存一份会话缓存。

## Menu 的身份与设置布局

Resident 与 Family Menu 的头部使用紧凑头像和名称，Resident ID、接单开关及所属 Lineage 优先排列在同一行；宽度不足时换行，完整身份仍可读取。接单开关采用无外框的滑动控件，Move 使用淡色底按钮。头部保持紧凑，让 Settings、Harness、Package、Mailbox 和 Work Journal 获得更多可滚动空间。Project 与 Lineage Menu 沿用相同的头像、名称和间距。

普通 Resident 的 Settings 用紧凑卡片呈现 Backend、当前 Provider 支持的 Model、Effort、Speed、Permissions 和 Mode；小字号标签位于当前值上方，右侧箭头表示可编辑。宽屏两列、窄屏一列。卡片继续调用原有的后端切换与运行设置弹窗；设置能力、候选值与保存语义见 decision_8b2fa224616b。

Family Menu 的 Settings 在成员表旁提供「＋ 添加成员」。点击后读取当前 Lineage 定义，按名称或 Resident ID 搜索并多选已有 Resident，提交调用 UpdateFamily 的 add_member_ids。候选只列出同一 Lineage 的可见、未删除对象，排除当前成员、其他 Family 的直接成员、Router、Steward、自身门面、祖先门面和 Lineage 范围 Family 的门面；可加入的局部子 Family 通过其门面选择。服务端继续校验成员约束、调用者权限与 Lineage revision。读取失败可以重试；提交失败保留勾选并显示原因，用户可以取消后重新打开以读取最新名册。提交期间禁止重复提交及其他 Family 修改；成功后关闭选择区，重读成员状态表并通知宿主刷新协作图。成员已添加但宿主刷新失败时明确说明刷新失败，避免把已提交操作显示为添加失败。Family 名册与权限规则见 decision_44cf587c1721。

画布中 Family 展开按钮的右上方约 45 度位置显示 Unfamilize 圆形操作，鼠标悬停或键盘聚焦展开区域时可见。叉号使用居中的线条图标；从展开按钮移向解除按钮时保持可点击。

点击画布叉号或 Menu 的 Unfamilize 时，先显示「Unfamilize（保留成员）？」确认框，说明保留业务成员、子 Family、工作流、会话与工作区，移除门面、Router、Steward 和生成结构。取消、关闭或按 Esc 均不提交解除；确认后才调用 Unfamilize。首次进入画布即可执行该流程，导航入口负责按需加载共享 Family 操作，不要求先打开 Resident Menu。加载失败显示操作错误，解除失败保留 Family 并显示失败原因，用户可以再次尝试；成功后刷新协作图。

Menu 的「删除整个 Family」使用独立的危险操作确认框，明确将停止并删除全部内部角色、业务成员、子 Family 与工作流；取消不调用 DeleteFamily。两种操作共用同一确认规则 owner，具体生命周期与成员保留规则见 decision_44cf587c1721。

## 状态由谁保存

| 状态 | 保存者 |
| --- | --- |
| Resident 身份、访问权限、模型会话、运行状态、消息历史 | 服务端（SocietyService 与 Resident 会话协议） |
| 当前选中的目标、地址栏参数、各 Panel 的目标分配 | Agent Console |
| 分栏、标签页、隐藏状态、移动端当前显示的 Panel | Workbench（按页面偏好保存） |
| 输入草稿、附件选择、消息选择和滚动位置 | 每个 Chat Workspace 实例 |
| 全局对话列表、最近快捷入口、未读回执 | Conversation Store |
| Lineage 的组件、连线和运行 | 服务端；HATG 通过 ISA 指令修改 |

这样分配后，Chat Workspace 可以在其他页面复用，Console 可以调整布局而不影响 Resident 的执行。用户能否访问 Resident、能否发送消息、能否修改图，都由服务端按当前登录用户判断；页面只决定怎样呈现和从哪里发起操作。

页面启动顺序为：确认当前登录身份并建立共享页头；建立 Workbench 和各 Panel；按地址栏参数确定初始目标；读取该 Resident 的会话数据；把数据交给 Chat Workspace。Panel 隐藏时保留自己的状态；Workbench 销毁时，各 Panel 取消事件订阅和正在进行的请求。

## 故障与恢复

- **普通请求失败时，只在发起操作的 Panel 中显示错误。** 单个 Panel 的错误不遮住整个 Workbench。
- **SocietyService 不可用或繁忙时，页面进入组件恢复流程。** 请求返回 `lineage.unavailable` 或 `lineage.busy` 时，Console 在 Workbench 上显示恢复遮罩，禁用页面控件，并轮询 `/lineage/api/service-status`；服务恢复后重新加载全部数据，再恢复控件。组件恢复的通用规则见 decision_f25266e5a0e7。
- **恢复后以服务端数据为准。** 恢复遮罩出现前的消息画面只是旧画面；恢复后 Console 重新读取会话数据，按最新数据决定消息内容和能否发送。
- **目标不可访问时给出返回入口。** 目标 Resident 已删除或用户已无权访问时，导航和 Chat 显示“不可访问”，并提供回到其他可用目标的入口。空 Lineage 显示可以操作的空状态。

## 扩展方式

- 新增一种工具：注册新的 Panel 类型，并声明它订阅哪个上下文频道。
- 改变会话界面的通用交互：修改 Chat Workspace（decision_f560803947c6）。
- 改变 Resident 的执行行为：修改对应的 HATG ISA 指令（decision_898b8911a2ff）。
- 增加跨页面的提醒：修改 Conversation Store（decision_18abbdef3da3）与 User Mailbox（decision_15f7df737ed5）。

Lineage Menu 从已加载的侧栏记录读取标题、设置字段和成员列表，打开时不再为这些字段请求完整 Lineage 定义。Work Journal 按权限批量读取已保存的 Resident 摘要，每次 `GetResidentSummaries` 最多请求 128 人；同一次菜单打开期间复用摘要结果，关闭后清除缓存。
