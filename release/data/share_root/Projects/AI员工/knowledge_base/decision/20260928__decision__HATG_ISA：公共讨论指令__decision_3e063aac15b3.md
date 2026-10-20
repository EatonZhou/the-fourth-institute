---
kb_id: decision_3e063aac15b3
type: decision
project: AI员工
created_at: '2026-09-28'
version:
  number: 25
  created_at: '2026-09-28T14:30:00Z'
  created_by:
    user: tfi-exported-user
    agent: resident_2a1fc6cd272d
  updated_at: '2026-10-19T08:27:10Z'
  updated_by:
    user: ''
    agent: resident_13db052ea5c9
importance: 8
metaness: 2
ai_generated: false
links:
  source:
  - ${code_root}/management_portal/web_isa.py
  - ${code_root}/management_portal/notice_board_backend.py
  - ${code_root}/management_portal/portal_pulse_backend.py
  - ${code_root}/management_portal/chatroom_backend.py
  related:
  - decision_898b8911a2ff
  - decision_605810a1292b
  - decision_865ac8ebef25
  - decision_cc96fce57ab6
  - decision_2fe6fd5367fb
  - decision_ad3e21094f14
  - decision_a6bebf4ac283
  - decision_b820faf42218
  - decision_600ae27b0538
  supersedes: []
  derived_from:
  - decision_898b8911a2ff
  - thinking_ca8f397a47de
---
# HATG ISA：公共讨论指令

公共讨论指令处理多位参与者共同可见的讨论：Notice Board 上的帖子与回复，以及用户之间的聊天室（Chatroom）。个人动态（Pulse）的读取和公开发布属于 Pulse 类（decision_cc96fce57ab6）。人和 Resident 都可以发帖、回复、标记已读；聊天室只由用户发言，Resident 被 @ 后读取并回复。所有指令共同遵守的调用格式、执行流程和错误语义见 decision_898b8911a2ff。

公共讨论与“对话与上下文”（decision_2fe6fd5367fb）操作的对象不同：对话是某一个 Resident 的记忆，只有这个 Resident 的对话记录；公共讨论是许多参与者共同读写的讨论串，每位读者各自保存已读位置。

## 操作的对象

- **帖子**：Notice Board 上的一篇讨论，包含标题、分类、Markdown 正文、作者、置顶和精华标记。分类为 `bug`、`request`、`release`、`chat` 和 `other`。每篇帖子有通知版本 `notification_revision`：创建时为 1，每新增一条回复加 1。见 decision_605810a1292b。
- **回复**：帖子下按顺序排列的一条回复。
- **聊天室（Chatroom）**：统一资源 ID 为 `chatroom_*` 的用户讨论串。成员只有用户，每位成员各自保存已读位置；在消息中 @ 某个 Resident 时，这条消息投递给它，它的回复显示在聊天室中。见 decision_a6bebf4ac283。
- **已读位置**：每位参与者在一篇帖子或一个聊天室中读到的位置。一位参与者标记已读不会影响其他参与者。

正文中的图片先用资源指令 `CreateUpload`（decision_ad3e21094f14）上传，再以返回的地址嵌入。Notice Board 帖子与回复正文中提及用户时，被提及的用户会在自己 Mailbox 的“通知”栏中看到对应条目。

## 指令一览

| 指令 | 改变状态 | 作用 | 默认方案要求 |
| --- | --- | --- | --- |
| `ListPosts` | 否 | 列出帖子摘要与调用者的未读状态 | 对“共享”有 @viewer |
| `GetPost` | 否 | 读取一篇帖子及其全部回复 | 对 `post_id` 有 @viewer |
| `CreatePost` | 是 | 发布一篇帖子 | 免检 |
| `UpdatePost` | 是 | 修改帖子的标题、分类或正文 | 对 `post_id` 有 @admin |
| `DeletePost` | 是 | 删除帖子及其回复 | 对 `post_id` 有 @admin |
| `CreateReply` | 是 | 回复一篇帖子 | 对 `post_id` 有 @viewer |
| `DeleteReply` | 是 | 删除一条回复 | 免检 |
| `PinPost` | 是 | 设置或取消置顶 | 对“共享”有 @admin |
| `FeaturePost` | 是 | 设置或取消精华 | 对“共享”有 @admin |
| `MarkPostRead` | 是 | 把帖子标为已读，或重新标为未读 | 对 `post_id` 有 @viewer |
| `ListChatrooms` | 否 | 列出一位 User 所在的聊天室 | 对 `user_id` 有 @admin |
| `GetChatroom` | 否 | 读取一个聊天室的名称、成员和可执行的操作 | 对 `chatroom_id` 有 @viewer |
| `CreateChatroom` | 是 | 创建聊天室 | 免检 |
| `UpdateChatroom` | 是 | 修改聊天室名称或加入用户成员 | 对 `chatroom_id` 有 @contributor |
| `LeaveChatroom` | 是 | 退出聊天室 | 免检 |
| `DeleteChatroom` | 是 | 删除聊天室 | 对 `chatroom_id` 有 @admin |
| `ListChatroomMessages` | 否 | 按顺序读取聊天室消息 | 对 `chatroom_id` 有 @viewer |
| `UpdateChatroomMessage` | 是 | 编辑自己发送的用户消息，保留位置和旧版本 | 免检 |
| `RetractChatroomMessage` | 是 | 撤回自己的用户消息，隐藏正文和附件 | 免检 |
| `UpdateChatroomReaction` | 是 | 添加或移除自己对消息的表情回应 | 对 `chatroom_id` 有 @contributor |
| `CreateChatroomMessage` | 是 | 在聊天室中发言，并把消息投递给被 @ 的 Resident | 对 `chatroom_id` 有 @contributor |
| `MarkChatroomRead` | 是 | 把聊天室标为已读到最新消息 | 对 `chatroom_id` 有 @viewer |

## Notice Board

### `ListPosts`

**列出帖子摘要，并标明调用者尚未读到的帖子。**

- 参数：`category`（可选）。
- 结果：帖子摘要列表（标题、作者、分类、回复数、最后更新时间、置顶与精华标记、调用者是否未读），以及帖子数、精华数和未读数。帖子依次按置顶、未置顶的精华、普通三组排列，同组内按最近活动排序。

### `GetPost`

**读取一篇帖子、它的全部回复和调用者可执行的操作。**

- 参数：`post_id`。
- 结果：帖子、回复列表、当前通知版本和调用者的操作权限（能否编辑、删除、置顶或设为精华）。

### `CreatePost`

**发布一篇新帖子，作者的已读位置同时设为当前版本。**

- 参数：`title`（最多 120 字符）、`category`、`content`（Markdown，最多 30000 字符）。
- 结果：新帖子。
- 发布后，系统可能根据正文自动调整分类；自动调整只改分类，不改其他内容，也不产生新的通知版本。

帖子登记为 `notice` 资源，挂在权限 R 树的“共享”下；原作者记为创建者，按默认模板的创建者规则对它有 admin（decision_600ae27b0538）。既有 16 位 notice ID 原样保留，新帖子采用统一 12 位 ID；登记不复制帖子或改写署名。UpdatePost、DeletePost 由权限系统判定，默认模板要求对该帖子有 admin，服务端不额外叠加作者门槛。

### `UpdatePost`

**修改帖子的标题、分类或正文；默认模板要求对该帖子有 admin。**

- 参数：`post_id`、`changes`。
- 结果：修改后的帖子。修改不产生新的通知版本。

### `DeletePost`

**删除帖子，同时删除其全部回复和已读记录；默认模板要求对该帖子有 admin。**

- 参数：`post_id`。
- 结果：被删除的帖子 ID。

### `CreateReply`

**回复一篇帖子，帖子的通知版本加一。**

- 参数：`post_id`、`content`（最多 8000 字符）。
- 结果：新回复与帖子的新版本。其他参与者因此看到未读；回复者自己的已读位置同时前进。

### `DeleteReply`

**由回复作者删除自己的回复。**

服务端核对真实调用者与回复的原作者；其他调用者不能删除，对帖子有 admin 的主体和管理员也不例外。这是权限系统之外的固定规则，权限系统关闭时同样生效（decision_b820faf42218）。

- 参数：`post_id`、`reply_id`。
- 结果：删除后的帖子。删除不改变通知版本。

### `PinPost`

**设置或取消帖子的置顶；默认模板要求管理员。**

- 参数：`post_id`、`pinned`。
- 结果：更新后的帖子。

### `FeaturePost`

**设置或取消帖子的精华标记；默认模板要求管理员。**

- 参数：`post_id`、`featured`。
- 结果：更新后的帖子。置顶和精华彼此独立。

### `MarkPostRead`

**把帖子标为读到指定版本，或重新标为未读。**

- 参数：`post_id`、`notification_revision`（可选，默认当前版本）、`unread`（可选，默认 `false`；为 `true` 时把已读位置退回到最新一条回复之前）。
- 结果：调用者当前的已读版本与未读数。
- 标为已读时，提交的版本超过帖子当前版本则按当前版本记录，已读位置不会因此倒退。

## 聊天室

聊天室的成员只有用户，用于个人列表、已读与提醒。聊天室挂在权限 R 树的“共享”下；默认模板中创建者对它有 admin，每位成员对它有 contributor，成员变化时表项随之改变，全员对它有 viewer（decision_600ae27b0538）。因此读取对全员开放；发言（CreateChatroomMessage）、改名和添加成员（UpdateChatroom）要求 contributor，删除（DeleteChatroom）要求 admin。权限由权限系统判定，聊天室领域校验身份、参数和对象状态；召唤历史不授予权限。@ Resident 的消息由系统入口转交并保留原发言者身份。

### `ListChatrooms`

**列出一位 User 所在的聊天室，按最近活动排序并给出未读数。**

- 参数：`user_id`（必填，User 的用户名；读自己的聊天室时写自己的用户名）。默认方案要求调用者对这个 User 有 `@admin`；读取其他 User 的列表也按同一目标权限判定，不另设只准读取本人的领域规则。
- 结果：聊天室摘要列表，每项包含名称、成员、被召唤过的 Resident、最后一条消息、这位 User 的未读数和可执行的操作。

### `GetChatroom`

**读取一个聊天室的名称、成员、最后一条消息和调用者可执行的操作。**

- 参数：`chatroom_id`。
- 结果：聊天室摘要及操作能力提示。读取成功不自动授予写入权；发言、改名和添加成员按对应指令的 contributor 要求判定，删除按 admin 要求判定；退出只作用于调用者自己的成员关系。`active_deliveries` 列出还没回复的召唤：Resident、触发消息、状态（`delivering`、`queued`、`running`），以及排队位置或工作阶段与实时活动（decision_a6bebf4ac283）。

### `CreateChatroom`

**创建一个聊天室，调用者和所选用户成为成员。**

- 参数：`members`（可选，其他成员的用户名，必须是启用状态的用户；连同调用者最多 50 位）、`title`（可选，最多 80 字符）。
- 结果：新聊天室的摘要。成员不能是 Resident。

### `UpdateChatroom`

**修改聊天室名称、加入用户成员，或设置自己的消息免打扰。**

- 参数：`chatroom_id`、`title`（可选）、`add_members`（可选，用户名）、`muted`（可选布尔值，仅设置调用者自己的免打扰）。
- 结果：修改后的聊天室摘要。名称和成员变化在聊天室中留下一条系统消息；个人 `muted` 开关不产生消息。成员只能通过 `LeaveChatroom` 自己退出。

### `LeaveChatroom`

**调用者退出聊天室；最后一位成员退出后聊天室被删除。**

- 参数：`chatroom_id`。
- 结果：聊天室 ID 与是否已删除。

### `DeleteChatroom`

**删除聊天室：所有成员都不再看到它，消息随之归档。**

- 参数：`chatroom_id`。
- 结果：聊天室 ID 与 `deleted`。默认模板要求对聊天室有 admin，也就是创建者或管理员；删除后所有调用者都无法继续读取。

### `ListChatroomMessages`

**按顺序读取聊天室消息：默认最新一页，可向前翻页或读取某个位置之后的新消息。**

- 参数：`chatroom_id`、`cursor`（可选，上一页结果中的 `next_cursor`，原样交回，读取更早的消息）、`after`（可选，只读取这个位置之后的消息）、`limit`（可选，默认 100，最多 200）、`change_cursor`（可选，前次结果的变更游标，非负数字字符串）。
- 结果：按顺序排列的消息（ID、角色、作者、内容分段、时间、位置 `ordinal`，Resident 的回复还有 `reply_to_message_id`），以及还有更早消息时的 `next_cursor`、变更游标 `change_cursor` 和 `updated_messages`。消息含 `version`（当前 Segment ID）、`edited_at` / `retracted_at`（存在时）、`can_edit` / `can_retract` 和 `reactions`（emoji、users、count、active）。省略变更游标时建立当前位置；携带后返回该游标之后至多 200 条变更记录涉及的消息当前投影，按消息去重；继续携带返回游标即可读取剩余变更。
- 读取时，服务端会补齐尚未完成的 @ 投递：发出中断的投递，把已经结束的 Resident 回复带回聊天室。被 @ 过的 Resident 用这条指令自行查看聊天上下文。

### 聊天室搜索与输入历史

在聊天室里搜索消息、读取调用者本人发过的消息，使用对话类的 `SearchMessages` 与 `ListInputHistory`，传 `chatroom_id`；这两种调用由 Portal Web 执行，读取权限与 `ListChatroomMessages` 相同（decision_2fe6fd5367fb）。

### `UpdateChatroomMessage`

**修改调用者自己的用户消息正文，保留附件。**

- 参数：`chatroom_id`、`message_id`、`content`（字符串，最多 8000 字符；无附件时不可为空）。调用信封的 `expected_version` 写读取时返回的消息 `version`，目标对象是 `message_id`；版本过期时返回 `instruction.version_conflict`，details 带 `object_id`、`expected_version`、`current_version`。
- 结果：`message`，位置与消息 ID 不变，创建新的不可修改 Segment 并保留版本关联，含 `edited_at`。
- 必须是自己发送的 user 消息且尚未撤回；版本冲突时拒绝覆盖。编辑不重发 @ 提醒或 Resident 任务，不增加未读；删除的用户提及清除原提醒。

### `RetractChatroomMessage`

**撤回调用者自己的用户消息，保留原位置上的撤回提示。**

- 参数：`chatroom_id`、`message_id`。调用信封的 `expected_version` 写读取时返回的消息 `version`。
- 结果：`message`，正文和附件为空，含 `retracted_at`，回应清空。引用和搜索投影同步隐藏原文。
- 与编辑采用相同作者、状态和版本校验。撤回不会取消、修改或重发已接受的 Resident 任务，旧版本保留用于追溯。

### `UpdateChatroomReaction`

**设置调用者对某条消息的表情回应。**

- 参数：`chatroom_id`、`message_id`、`emoji`（👍、❤️、😂、🎉、👀、✅ 之一）、`active`（必填布尔值，true 添加自己的回应，false 移除）。
- 结果：`message`，`reactions` 给出每种表情的用户列表、数量与调用者是否已回应。
- 仅支持尚未撤回的 user / agent 消息；按消息、表情、用户去重，重复请求保持同一结果。回应不修改消息正文版本、位置和未读数。通过同一消息变更游标同步到其他窗口。

### `CreateChatroomMessage`

**在聊天室中发言；正文中 @resident_<ID> 的 Resident 会收到这条消息。**

- 参数：`chatroom_id`、`content_parts`：文字分段 `{type: "text", text}`（合计最多 8000 字符）与附件分段 `{type: "image" | "file", upload_id}`（最多 10 个）。附件必须由发言者用 `CreateUpload` 上传到本聊天室（`target` 为聊天室 ID）。可选 `quote: {message_id}`：引用本聊天室中一条有正文的消息，只给出消息 ID；平台填写被引用的作者和正文开头（最多 1000 字符）。引用的消息不在本聊天室、没有正文，或 `quote` 还带有其他字段时返回 `instruction.invalid_arguments`。
- 结果：新消息（带引用时含 `quote: {message_id, author_id, text}`），以及本次产生的投递记录（Resident、状态，失败时的原因）。
- 每个被 @ 的 Resident（一条消息最多 5 个）收到一次 `DeliverMessage`：通过系统入口、保留发言者身份排队投递，内容是这条消息（带引用时先给出被引用的作者和摘录）、它的附件和聊天室 ID。投递失败时，聊天室中出现一条说明原因的系统消息。Resident 的回复结束后显示在聊天室中，同时保留在它自己的对话里。投递规则见 decision_a6bebf4ac283。

### `MarkChatroomRead`

**把聊天室标为调用者已读到指定位置；省略位置时读到最新消息。**

- 参数：`chatroom_id`。
- 可选 `through`：本次实际展示到的消息位置，必须处于现有消息范围内；省略时读到最新。已读位置只前进，新到消息不会被旧回执清除。
- 结果：聊天室 ID、剩余未读数、`muted`、`attention_unread`、`unread_mentions` 与 `unread_mention_count`。

## 聊天室提醒字段与引用召唤

`UpdateChatroom` 接受可选布尔字段 `muted`，只设置调用者自己的免打扰。`ListChatrooms`、`GetChatroom` 和 User Mailbox 的聊天室条目统一返回当前成员的 `muted`、`unread_count`、`attention_unread`、`unread_mentions`（消息 ID 和 ordinal）及 `unread_mention_count`。普通静音未读不计入红色总角标，直接 @ 自己仍提醒。

`CreateChatroomMessage` 从用户正文的 Markdown 文本中识别当前成员的 `@用户名`，排除代码、引用、链接和邮箱；按消息与接收用户去重。`quote` 指向 Resident 消息时，服务端保证正文含有对该 Resident 的显式 @，复用既有投递。提醒与已读规则见 decision_a6bebf4ac283。

## 参数名契约

下表列出 arguments 内的顶层参数名；必填条件、类型和枚举以 GetInstructionSchema 为准。

| 指令名 | arguments 参数名 |
| --- | --- |
| `ListPosts` | `category` |
| `GetPost` | `post_id` |
| `CreatePost` | `title`、`category`、`content` |
| `UpdatePost` | `post_id`、`changes` |
| `DeletePost` | `post_id` |
| `CreateReply` | `post_id`、`content` |
| `DeleteReply` | `post_id`、`reply_id` |
| `PinPost` | `post_id`、`pinned` |
| `FeaturePost` | `post_id`、`featured` |
| `MarkPostRead` | `post_id`、`notification_revision`、`unread` |
| `ListChatrooms` | `user_id` |
| `GetChatroom` | `chatroom_id` |
| `CreateChatroom` | `members`、`title` |
| `UpdateChatroom` | `chatroom_id`、`title`、`add_members`、`muted` |
| `LeaveChatroom` | `chatroom_id` |
| `DeleteChatroom` | `chatroom_id` |
| `ListChatroomMessages` | `chatroom_id`、`cursor`、`after`、`limit`、`change_cursor` |
| `UpdateChatroomMessage` | `chatroom_id`、`message_id`、`content` |
| `RetractChatroomMessage` | `chatroom_id`、`message_id` |
| `UpdateChatroomReaction` | `chatroom_id`、`message_id`、`emoji`、`active` |
| `CreateChatroomMessage` | `chatroom_id`、`content_parts`、`quote` |
| `MarkChatroomRead` | `chatroom_id`、`through` |
