---
kb_id: decision_605810a1292b
type: decision
project: AI员工
created_at: '2026-09-01'
version:
  number: 19
  created_at: '2026-09-01'
  created_by:
    user: ''
    agent: ''
  updated_at: '2026-10-19T08:29:18Z'
  updated_by:
    user: ''
    agent: resident_13db052ea5c9
importance: 8
metaness: 1
ai_generated: false
links:
  source:
  - ${code_root}/management_portal/resources/isa.py
  - ${code_root}/management_portal/notice_board_backend.py
  - ${code_root}/management_portal/notice_board_classifier.py
  - ${code_root}/management_portal/notice_board_home.html
  - ${code_root}/management_portal/static/notice_board_home.js
  - ${code_root}/management_portal/static/notice_board_home.css
  - ${code_root}/management_portal/static/portal_home_components.js
  - ${code_root}/management_portal/static/portal_workbench.js
  related:
  - decision_3fe3cca8fd95
  - decision_dca903d71575
  - decision_61e50306cc79
  - decision_15f7df737ed5
  - decision_b820faf42218
  - decision_600ae27b0538
  supersedes: []
  derived_from: []
---
# Home Notice Board 的发布与讨论

Notice Board 是 Portal 所有登录用户共享的公告与讨论区，位于 Home 首页 Get Me Aligned 区域的一个子面板中。用户在这里发布 Bug、需求、发布说明和闲谈，其他人阅读、回复，并通过未读标记知道哪些帖子有新动态。

本 Design 说明面板中的可见对象、发帖和回复的反馈、分类、未读记录、权限和图片。面板在 Home 中的位置、折叠和关闭由共享 Workbench 负责（decision_00546e28e1e0）；提及用户后的提醒由 User Mailbox 负责（decision_15f7df737ed5）；图片插入和查看组件见 decision_dca903d71575。

## 设计点

### 从列表到详情

- Workbench 标签行显示 **Notice Board**，正文直接从分类筛选和帖子列表开始，不重复显示标题。
- 面板可以拖拽、分组、折叠、关闭和恢复；这些布局操作只改变页面布局。
- **帖子列表**是 Home 中的紧凑概览。顶部有六个筛选按钮：All、Bug、需求、Portal Release News、闲谈、其他。All 显示全部帖子，其余按钮只保留对应分类。

- 每个**帖子行**显示标题、作者、回复数、最后更新时间和分类。蓝色未读点表示当前用户尚未读到该帖最新通知版本；置顶和精华使用独立标签。超过 30 天没有更新的帖子以较弱样式折叠显示，但仍可打开。

- 列表底部显示帖子数、精华数和未读数，并提供刷新图标与 **+ 发布新帖**按钮。刷新只重新读取 Notice Board。
- 面板首次显示时读取列表，之后每 60 秒刷新；收到服务端 Notice Board 变化通知时合并 200 毫秒内的通知再读取。发帖、回复、编辑、删除及手动刷新后读取当前结果。

- 点击帖子行会打开**详情对话框**。对话框标题区显示帖子标题、作者和管理标签；右上角是关闭按钮。编辑铅笔、删除垃圾桶、置顶与精华控件对所有已登录用户显示。正文区显示分类、时间、完整 Markdown 和按顺序排列的回复。

- 详情底部是**回复编辑器**。文本框旁的 `@` 按钮插入用户提及，`+` 按钮打开图片托盘，提交按钮发送回复。**发帖对话框**包含标题、分类、正文、图片托盘、Markdown 帮助、取消和发布按钮；编辑帖子复用同一表单并填入已有内容。

### 用户行为与反馈

- 切换分类只筛选已经加载的列表，不改变帖子数据。
- 列表按置顶、精华和最近活动组织；置顶内容保持在普通内容之前，最新活动决定同组内顺序。
- 打开详情后，页面先读取完整帖子，再把当前 `notification_revision`（通知版本）回报为已读。列表中的未读点和计数随接受的版本更新。
- 新建帖子成功后关闭表单、刷新列表并打开新帖；失败时保留输入并显示错误。
- 回复成功后刷新当前详情和列表；新回复使其他用户看到未读，回复作者自己的已读位置同时前进。
- 编辑帖子成功后更新详情与列表；编辑不会产生新的通知版本。
- 删除帖子后关闭详情并从列表移除；关联回复和该帖已读基线同时失效。
- 删除回复后更新详情；删除动作不增加通知版本。只有回复原作者可以删除，详情按每条回复的 `can_delete` 显示删除控件。
- 管理员可以置顶和设为精华（默认模板要求对“共享”有 admin）。两个标记彼此独立，操作后页面立即用服务返回结果重绘。
- 图片上传期间，相关提交按钮保持等待状态；页面等待单张图片上传最多一小时。上传失败时，已经写好的正文保持不变。
- 发帖或回复编辑器最多插入 8 张图片。这个数量由页面限制；服务端逐张校验图片，没有按篇计数的上限。

### 分类、未读与提及

- 五个分类的接口标识与页面标签如下：

| 标识 | 页面标签 |
| --- | --- |
| `bug` | Bug |
| `request` | 需求 |
| `release` | Portal Release News |
| `chat` | 闲谈 |
| `other` | 其他 |

- 用户发布时选择的分类立即生效。后台分类器还会读取帖子内容并给出自动分类；它只更新分类字段，不改标题、正文、作者或通知版本。

- 页面用 `notification_revision` 表示当前读到的帖子版本；版本生成与已读位置推进遵循 decision_3e063aac15b3。Home 只在读取详情后回报实际展示的版本，不提供“标为未读”按钮；Agent 可使用 `MarkPostRead` 的该项能力。

- **提及**是正文中的用户引用。页面提供用户选择与插入，后端从帖子和回复中提取事件交给被提及用户的 Mailbox，出现在“通知”栏。提及通知和帖子未读分开记录：读过帖子后，Mailbox 中的提及条目仍需在 Mailbox 中处理；提及投递失败时，帖子照常发布。

## 组件与一次发布流程

参与的部分和各自保存的内容：

| 部分 | 负责 |
| --- | --- |
| Home 面板片段（`notice_board_home.html`、`static/notice_board_home.{css,js}`） | 分类筛选、详情与发帖对话框、未提交的草稿；作为 Notice Board 功能的 `web.home_panel` 插入 Home，登记为 Get Me Aligned 的子面板（decision_00546e28e1e0） |
| Notice Board 后端（`notice_board_backend.py`） | 帖子、回复、已读记录、置顶与精华标记 |
| 上传服务 | 保存帖子图片的字节 |
| 自动分类器（`notice_board_classifier.py`） | 在后台读取帖子内容并修改分类 |
| User Mailbox | 接收帖子和回复中的提及事件 |

发布一篇帖子的流程：

1. 用户在对话框中写标题、选分类、写 Markdown，并可以插入图片。
2. 每张图片先通过 Portal 的通用上传通道上传，用途标为 `post_image`。上传服务校验格式、大小和像素数，返回图片地址 `/api/uploads/<upload_id>`，页面把它写进正文。
3. 页面调用 `CreatePost`。后端校验长度，在一个数据库事务中保存帖子，并把作者的已读版本设为当前版本。
4. 页面收到新帖子后刷新列表并打开详情。
5. 后端把分类任务放进队列，任务带着帖子内容的指纹。分类完成时，只有指纹仍与帖子当前内容一致才写入结果，这样作者在分类期间修改过的帖子保留新内容对应的分类。
6. 后端扫描正文中的提及，为每个被提及的用户生成事件，交给这位用户的 Mailbox。

帖子、回复、管理标记和已读记录保存在 PostgreSQL 的 `notice` schema 中，每个业务动作在一个事务内提交或整体回滚。帖子创建、修改和删除提交后，ResourceService 同步统一资源登记，并通知权限系统：创建时把帖子放到 R 树“共享”下并记录作者为创建者，删除时把它移出权限树。同步失败明确返回 `resource.mutation_post_commit_failed`、`domain_committed: true` 和帖子 ID，原业务记录已经提交，调用者须先核对原对象再恢复投影，不能重复创建帖子。分类任务在 `pending`（等待）或 `claiming`（已被领取）状态时遇到进程重启，会被重新领取；每个任务最多尝试 5 次，之后标为失败。自动分类由经过认证的 Portal 服务主体（kind=service、id=portal）在系统入口触发 Portal System 中“Notice Board 自动分类”Function Block 的 Event，再由 Effect 调用 DeliverMessage 交给配置的分类 Resident；结果继续由同一服务主体观察，不借用帖子作者或指定用户名的权限。

分类 Event 与 Effect 的关闭状态由 Lineage 持久保存，均通过 PauseComponent 的 `paused: true|false` 控制。关闭任一节点，或绑定、连线缺失时，分类任务保留在 durable 队列中等待，不消耗五次尝试额度；帖子仍能正常发布和手工选分类。恢复开启后处理原任务，内容指纹仍须匹配。触发过程中遇到暂停，已产生的 Event 和 Trigger Path 保存到任务中，恢复只观察这一条流程，不重复触发。流程已投递的消息继续按原 message_id 等待；流程终态失败则保留错误和路径，供检查。

应用配置只指定承担者，图中的组件以 client_key `system:notice_classifier:event`、`system:notice_classifier:effect` 定位。Event 必须经唯一 route 进入该 Effect，Effect 的 target 端点必须指向当前配置的 Resident；改标题不改变绑定，切换承担者时须显式更新图中连线。不在应用配置中另存一个开关。

## 指令与限制

页面和 Agent 都通过 HATG 指令读写 Notice Board。指令清单、参数、结果、默认授权及已读版本更新规则统一见 decision_3e063aac15b3；本 Design 保留面板反馈、分类流程、存储和图片边界。

图片通过通用上传通道写入，页面通过 `/api/uploads/<upload_id>` 读取。所有登录用户都能读取用途为 `post_image` 的上传，因为帖子本身对所有登录用户可见。较早帖子中的图片地址 `/api/notice-board/image?image_id=<图片 ID>` 继续可以读取。

数量和长度限制：

- 标题最多 120 个字符，帖子正文最多 30,000 个字符，回复最多 8,000 个字符。
- 最多保存 500 篇帖子，每篇最多 1,000 条回复。
- 帖子登记为 `notice` 资源。新帖子 ID 为 `notice_` 加统一 12 位小写十六进制；既有 16 位帖子 ID 保持有效，不改 ID 或署名。回复 ID 仍为 `reply_` 加 16 位小写十六进制。
- 图片接受 PNG、JPEG、GIF 和 WebP；单张最多 12 MiB，解码后的像素总数最多 40,000,000。

编辑或删除正文后，原先上传的图片文件仍然保留。

权限：

- 所有指令都要求调用者已登录 Portal。
- 帖子修改/删除、置顶/精华和回复删除的授权边界统一见 decision_3e063aac15b3，权限模板与固定领域限制分别见 decision_b820faf42218、decision_600ae27b0538。前端不能以控件可见代替提交授权。
- 当前 `GetPost` 返回的帖子 `can_edit` 和 `can_delete` 对已登录调用者为 true，详情据此显示编辑和删除；提交时仍由权限系统判定。回复删除控件读取每条回复的 `can_delete`，保留仅原作者可删除的领域限制。
