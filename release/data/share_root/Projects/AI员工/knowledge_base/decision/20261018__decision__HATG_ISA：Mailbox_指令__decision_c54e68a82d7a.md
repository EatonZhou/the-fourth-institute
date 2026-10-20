---
kb_id: decision_c54e68a82d7a
type: decision
project: AI员工
created_at: '2026-10-18'
version:
  number: 11
  created_at: '2026-10-18'
  created_by:
    user: ''
    agent: resident_7765faecc45a
  updated_at: '2026-10-20T00:58:58Z'
  updated_by:
    user: ''
    agent: resident_6199a7a36480
importance: 5
ai_generated: false
links:
  source: []
  related: []
  supersedes: []
  derived_from: []
---

# HATG ISA：Mailbox 指令

Mailbox 指令读取和整理两种收件：User 的 Mailbox 列出这位用户的对话和通知，Resident 的 Mailbox 保存它还没处理的请求、群体与 info。两种 Mailbox 的模型、页面和未读规则见 decision_15f7df737ed5。所有指令共同遵守的调用格式、执行流程和错误语义见 decision_898b8911a2ff；停止一条消息（包括撤回等待中的消息）用 Resident 执行类的 `CancelInput`（decision_2a3ff353165c）。

## 操作的对象

- **User Mailbox**：一位用户的收件索引，分“对话”和“通知”两栏。每个条目是一段 Resident 对话、一个聊天室或一条通知，带条目键 `key`、未读状态、置顶状态和版本 `version`。消息本身留在各自的对话里。条目的已读、置顶与清理状态保存在每位用户的 Portal 偏好中，由 Portal Web 执行；通知由 SocietyService 保存，并投影到接收者 Mailbox 的“通知”栏。
- **Resident Mailbox**：一个 Resident 还没处理的收件：队列中的请求和群体按处理顺序排列，等待中的 info 随下一条请求送达（concept_43c793b4f3ab）。它由 SocietyService 保存；节点对话框的 Mailbox 栏显示它（decision_15f7df737ed5）。
- **群体消息**：把几条等待消息合并成的一条输入，作者是 Mailbox，按顺序引用成员原文。
- **个人 Todo**：User 的待办清单，显示在 User Mailbox 中，不是对话消息，不会启动 Resident 工作。

## 按参数分流的指令

`GetMailbox` 和 `ClearMailbox` 同时覆盖两种 Mailbox：`resident_id` 与 `user_id` 必须且只能给一个。两种 Mailbox 由不同服务保存，指令声明用 `argument_owners` 写明带 `resident_id` 的调用由 SocietyService 执行，带 `user_id` 的调用由 Portal Web 执行（decision_898b8911a2ff）。`user_id` 是调用者自己时操作的就是自己的 Mailbox；默认方案要求对这个 User 账号有 admin，每位 User 对自己的账号有 admin。

## 指令一览

| 指令 | 改变状态 | 作用 | 默认方案要求 |
| --- | --- | --- | --- |
| `GetMailbox` | 否 | 读取一个 Resident 或一位 User 的 Mailbox | 对 `resident_id` 有 @viewer；对 `user_id` 有 @admin |
| `WatchMailbox` | 否（流式） | 从返回游标继续等待 User Mailbox 变化 | 对 `user_id` 有 @admin |
| `PinMailboxItem` | 是 | 置顶或取消置顶调用者 Mailbox 中的对话，最多 50 个，通知不支持置顶 | 免检 |
| `MarkMailboxItemRead` | 是 | 把调用者 Mailbox 中的条目标为已读或未读 | 免检 |
| `ClearMailbox` | 是 | 清空一个 Mailbox：Resident 的全部等待消息，或 User 的 Mailbox 条目与未读状态 | 对 `resident_id` 有 @contributor；对 `user_id` 有 @admin |
| `ReorderMailbox` | 是 | 调整 Resident 等待消息的顺序，包括移到队首 | 对 `resident_id` 有 @contributor |
| `MergeMailboxItems` | 是 | 把若干条等待消息合并为一条 Mailbox 群体消息 | 对 `resident_id` 有 @contributor |
| `SplitMailboxItem` | 是 | 把尚未执行的群体消息拆回原来的消息 | 对 `resident_id` 有 @contributor |
| `RequeueMessage` | 是 | 放回停止或失败的一轮，或恢复接收记录完整但丢失执行位置的排队输入 | 对 `resident_id` 有 @contributor |
| `InjectMailboxItem` | 是 | 把一条等待消息立即交给当前正在进行的工作 | 对 `resident_id` 有 @contributor |
| `ListMailboxTodos` | 否 | 读取一位 User 的待办及未完成数 | 对 `user_id` 有 @admin |
| `CreateMailboxTodo` | 是 | 创建 1–100 字个人待办；Resident 可指定接收 User | 免检 |
| `UpdateMailboxTodo` | 是 | 修改自己的待办文字、日期、完成状态或缩进 | 免检 |
| `DeleteMailboxTodo` | 是 | 删除自己的一条待办 | 免检 |
| `MoveMailboxTodo` | 是 | 按相对位置移动待办（可跨日期），或同日调序 | 免检 |
| `CreateNotification` | 是 | 向人类参与者发送通知，写入其 Mailbox 的“通知”栏 | 对“共享”有 @message |

## 读取

### `GetMailbox`

**读取一个 Mailbox；`resident_id` 与 `user_id` 二选一。**

带 `resident_id`：读取节点对话框 Mailbox 栏显示的内容，即等待中的项、正在处理的一轮和最近结束的几轮。

- `pending`：等待中的项。先是队列中的请求和群体，按处理顺序；再是等待中的 info，按到达顺序。每项有 `kind`（`request`、`group` 或 `info`）、`message_id`、发送者 `author_id`、来源 `origin`、时间 `created_at`、前 200 字的摘要 `preview`，带图片或文件时有 `attachments`（数量）；群体另有 `members`（各成员的消息 ID、发送者、时间和摘要）。
- `active`：正在处理的一轮，有请求的摘要和随它送达的 info（`infos`）；没有正在处理的一轮时为空。
- `recent`：最近结束的几轮（最多 10 轮），新的在前。每轮有请求的摘要、随行的 info、结果 `state`（`completed`、`cancelled` 或 `failed`）、结束时间 `finished_at`、失败时的 `error`，以及 `can_requeue`（这一轮能否用 `RequeueMessage` 放回）。
- `can_organize`：调用者能否整理这个 Mailbox（合并、拆分、调序、撤回、放回队列）。
- `revision`：当前等待顺序的标识，有请求到达、离开或调序时改变，与整理指令返回的 `revision` 相同。
- 正在执行的消息在 `active` 中，不在 `pending` 中；Resident 状态中的当前消息 ID 指向它。完整正文用 `GetMessage` 按需读取。默认方案要求对这个 Resident 有 viewer。

带 `user_id`：读取这位 User 的 Mailbox（对话与通知），`related_resident_id`（可选）只读取与这个 Resident 相关的条目。带 `resident_id` 时不接受 `related_resident_id`。

- 结果：条目列表 `items` 和未读总数 `unread_count`。每项包含条目键、条目种类（对话或通知）、标题、摘要、来源、更新时间、运行或失败状态、是否未读和条目版本 `version`。置顶在前，其余按最近活动从新到旧，同一时间按 `key` 升序。
- Resident 对话条目还给出 `latest_message_id`：这段对话最近一条可通知消息（Resident 的回复或发给这位用户的消息）的 ID，只在它是 `msg_` 消息 ID 时出现；`input_message_id` 是它回应的那条输入。两者取自 `version` 中的同名字段，调用者不必解析 `version`。聊天室条目不带这两个字段，消息用 `ListChatroomMessages` 读取。读原文用 `GetMessage(resident_id, message_id=latest_message_id)`。没有 `input_message_id` 时，这是对方主动发来的消息，按 `recipients` 判断是否发给自己；有 `input_message_id` 时，这是对请求的回复，普通回复不一定带 `recipients`，改用 `GetConversation(resident_id, input_message_id)` 确认那条输入由自己发出、回复在它的 `reply_message_ids` 中。`origin.sender` 说明由谁发出。读取不改变已读状态；标记已读用 `MarkMailboxItemRead` 并以 `version` 作为期望版本。

### `WatchMailbox`

**等待与页面相同的 User Mailbox 变化；使用返回的 `cursor` 续读。**

- 参数：`user_id`（必填，Mailbox 所属 User 的用户名；等待自己的 Mailbox 时写自己的用户名）、`cursor`（可选，上次返回的续读游标；首次省略，先返回重读检查点）、`timeout_seconds`（可选，本次订阅时长，1–60 秒，默认 30 秒）。
- 结果：变化的条目键 `keys`、续读游标 `cursor`，以及是否需要用 `GetMailbox` 重读的 `snapshot_required`。
- 订阅复用页面经权限过滤的变化数据，不轮询列表；无法定位到条目键的整表变化、Web 进程重启或游标已退出缓存窗口时返回 `snapshot_required`。

## User Mailbox 的已读、置顶与清理

### `PinMailboxItem`

**置顶或取消置顶调用者 Mailbox 中的对话。**

- 参数：`key`、`pinned`。
- 结果：更新后的条目 `item`。
- 只有“对话”栏的条目可以置顶，通知拒绝置顶；每位用户最多置顶 50 个对话。置顶独立于已读。可以固定尚未交谈的可见 Resident。

### `MarkMailboxItemRead`

**把调用者 Mailbox 中的一个条目标为已读，或重新标为未读。**

- 参数：`key`、`unread`（可选，默认 `false`；为 `true` 时重新标为未读）。
- 结果：更新后的条目与未读总数。
- 标为已读针对条目当前的活动；之后出现新的完成结果时，条目重新变为未读。
- 条目的版本令牌是它当前的完成签名。Portal 自动确认已读时把读取到的完成签名作为 `expected_version` 交回，服务端已有更新的完成时以 `instruction.version_conflict` 失败，条目保持未读。
- 打开 Resident Chat 时，页面以 `key: resident:<Resident ID>` 调用，不带 `expected_version`：服务端先核对调用者能看到这个 Resident（Mailbox 中尚未列出的可见 Resident 也可以），再以服务端当前已索引的完成结果为准读到最新提醒，页面不需要提供完成签名；这个 Resident 还没有已索引的完成结果时不做修改。

### `ClearMailbox`

**清空一个 Mailbox，整次清理是一个原子操作**（TFI portal 10-18）。`resident_id` 与 `user_id` 必须且只能给一个：

- **带 `resident_id`：取消这个 Resident 全部可以撤回的等待消息。** 由 SocietyService 在一个事务里执行，需要对该 Resident 有 contributor。不接受 `mode` 和 `section`。结果中 `cancelled` 为被取消数量，并返回 `resident_id`、`cursor`、完整 Resident `state`、`controls` 和当前 `queue`。已经开始执行或正在确认投递的消息不受影响，接单开关也保持不变。
- **带 `user_id`：清理这个 User 的 Mailbox。** 由 Portal Web 执行，需要对该 User 账号有 admin。`mode` 必填：`unread` 把未读条目标为已读，`all` 从 Mailbox 移除未置顶的条目；`section`（`conversation` 或 `notification`）限定清理的栏，省略时作用于两栏，不影响 Todo。结果是清理结果 `status`、实际处理的条目数 `cleared` 和 `items`。清理针对条目当前的活动；之后出现新活动的条目会重新出现在 Mailbox 中。

## 整理 Resident 的等待消息

以下指令都指向 Resident Mailbox 中的具体消息。默认方案要求对目标 Resident 有 contributor；领域不以 owner、发送者或同一 Lineage 成员关系另行放行。撤回一条等待消息用 `CancelInput`。

### `ReorderMailbox`

**调整等待消息的顺序（decision_15f7df737ed5 的“调序”）。**

- 参数：`resident_id`；相对移动用 `message_id` 加 `before_message_id` 或 `after_message_id` 中的一个，完整排列用 `order`（当前全部等待消息 ID）。移到队首就是移到当前第一条等待消息之前。
- 结果：调整后的队列、`revision`，以及 `reordered`（顺序是否变化）。
- 只改变等待顺序，不改创建时间、作者和内容；正在执行的消息不参与调序。等待输入仅有消息 ID，调序不操作 Execution 调度器。接单时才分配 Execution ID。
- 不带版本前提。`order` 有重复、遗漏或多出的 ID 时拒绝。要移动的消息或参照消息已不在队列中时，按冲突失败。

### `MergeMailboxItems`

**把若干条等待消息合并为一条 Mailbox 群体消息（decision_15f7df737ed5 的“合并”）。**

- 参数：`resident_id`、`message_ids`（选中的顶层等待消息，至少两条）。
- 结果：`group_message_id`、按处理顺序排列的 `member_message_ids`、调整后的队列与 `revision`。
- 展开后至少包含两条原始消息，成员条数不设固定上限；直接选择多条消息或展开已有群体遵循同一规则。
- 群体消息的作者是 Mailbox（来源 `tfi.message-origin.v2`，`initiated_by` 记录合并者），只有一个 `mailbox` 内容分段，按顺序引用成员。成员消息的原文、作者和时间都不变。
- 群体放在被选中项中最早一项的位置，其他项相对顺序不变；成员按它们在队列中的先后排列。选中已有群体时按成员展开，结果仍是一层，原群体以 `cancelled`（`mailbox_split`）结束。
- 群体作为一次输入执行，交给模型时逐条写出每个成员的作者、时间和原文。群体结束时，成员得到同样的终态和回复；取消群体也取消全部成员。读取成员的输入状态时带 `grouped_into_message_id`，并跟随群体的进展。
- 不同发送者的消息可以合并：能把消息投递给这个 Resident 的发送者本来就被允许这样做。群体输入写明每个成员的发送者，并提示收到的 Resident 按发送者分别判断每条请求是否执行、能做到什么程度。执行群体时，Resident 调用指令只按它自己的权限判定；发送者与合并者只出现在回合的授权说明中，不参与判定（decision_b820faf42218）。
- 执行方式已经确定或独立的输入不能合并，按冲突拒绝：已被执行领取的项、注入当前轮的输入、`/compact` 等控制类输入，以及带独立上下文范围的输入。

### `SplitMailboxItem`

**把尚未执行的群体消息拆回原来的消息。**

- 参数：`resident_id`、`message_id`（群体消息）。
- 结果：`group_message_id`、`member_message_ids`、调整后的队列与 `revision`。
- 成员按顺序回到群体所在的位置；消息 ID、作者、时间和内容都不变，不重新发送。群体以 `cancelled` 结束，错误代码 `mailbox_split`。
- 群体已开始执行或不在等待队列中时按冲突失败。

### `RequeueMessage`

**把停止或失败的一轮放回队列；也可凭完整接收记录恢复丢失执行位置的排队输入（decision_15f7df737ed5 的“放回队列”）。**

- 参数：`resident_id`、`message_id`（这一轮的请求或群体消息）。
- 结果：`message_ids`（放回的消息：请求在前，随它送达的 info 按原顺序在后）、调整后的队列 `items` 与 `revision`。
- 放回的是原来的消息本身：请求以原消息 ID、发送者、时间和正文回到队首，Resident 空闲时直接成为下一项工作；它的输入状态回到 `queued`，群体连同成员一起回到 `queued`。随它送达的 info 回到等待列表的最前面，仍是原发送者，随下一条请求送达。那一轮的停止或失败提示留在对话中。排队输入没有执行位置时，仅在下述完整接收记录条件满足后恢复；缺少原记录时拒绝，不从提示词、发件副本或调用方正文重建消息。
- 同一请求已经占有目标 Resident 的顶层活动或等待位置时，调用保持该位置并返回成功，不新增队列项，也不重开随行 info；群体成员或其他附属位置不能作为顶层请求恢复。尚无执行位置的普通轮次重试，只接受最近 10 轮中有完整检查点并以 `cancelled` 或 `failed` 结束的一轮，包括首次预留后、尚未提交执行就停止的轮次；已完成、无检查点、窗口外及其他仍在运行的输入按冲突拒绝。图片附件的暂存副本和原图均不可取得时拒绝。重试按幂等键返回第一次的结果。
- 恢复已接收但丢失执行位置的输入，是另一种明确受控的情况：输入必须仍为 `queued`，没有活动或等待中的执行位置，且同一 Resident 的持久接收项及准备记录或本地检查点能证明原请求、群体成员和随行 info 的完整原文与身份。参数仍只有 `resident_id`、`message_id`，默认权限不变。记录缺失、冲突、跨 Resident、群体成员另属其他工作、输入已完成或取消时拒绝，失败不留下部分恢复。
- 恢复沿原输入 owner 加锁及事务提交：保留消息 ID、作者、时间、附件、引用、origin 和群体顺序，恢复必要的原消息并建立唯一队列位置，接单和额度门控照常生效。info 仍是 info，不单独激活。相同有效待执行位置再次恢复保持不变；原停止和错误历史不删除。
- 随行 info 必须有同一 Resident 的完整原消息及原轮次附带关系。正常随原失败或停止轮次消费的 info 可随原请求恢复；info 被独立撤回、原文缺失、身份或归属冲突时，整项恢复拒绝且不留下部分写入。
- User 与 Resident 调用均属于显式恢复，不受后台自动恢复次数限制，也不消耗或重置后台计数；无需人类 owner 再次操作。后台扫描与后台发起的 Session 迁移恢复仍受自动恢复上限约束。调用者身份不能代替操作来源分类。

### `InjectMailboxItem`

**把一条等待消息立即交给 Resident 当前正在进行的工作。**

- 参数：`resident_id`、`message_id`。
- 结果：投递回执。`delivered` 表示当前工作已接收，`pending` 表示正在等待当前工作接收，`next_turn` 表示保留原消息交给下一轮。
- 当前工作确认接收后，这条消息才加入对话并移出队列。当前工作在接收前结束时，这条消息保持原 ID，优先交给下一轮执行，不会重复投递。
- 同一条消息已经投递后再次请求，返回原来的投递回执。
- 能否立即注入由当前执行后端的活动轮输入能力决定。支持时送入当前轮；当前轮未接收或已经结束时保留原消息交给下一轮，具体投递状态以回执为准。

## 个人 Todo

Todo 是可编辑的清单记录，不是 Conversation 消息，不会启动 Resident 工作。`GetMailbox`、`WatchMailbox`、`ClearMailbox` 和消息已读指令只处理对话与通知。待办指令由 Portal Web 执行，和页面使用同一个存储与校验模块。

- `ListMailboxTodos(user_id)`：读取 `user_id` 指定 User 的待办，默认方案要求调用者对这个 User 有 `@admin`；返回 `items` 和 `pending_count`。每项包含 `todo_id`、`user_id`、`text`、`date`、`completed`、`position`、`indent`、`created_by`、`created_at`、`updated_at`。按日期从新到旧、同日个人顺序排序；完成状态变化不重新排序。`indent` 为 0–4 的视觉缩进层级，默认 0，不定义父子关系或联动其他条目。
- `CreateMailboxTodo(text, user_id, date?)`：`user_id` 必填（TFI portal 2026-10-18）。User 只能写自己的用户名，给自己添加；Resident 指定已登记且启用的接收 User。文字去掉首尾空白后为 1–100 个 Unicode 字符；日期为 `YYYY-MM-DD`，省略时使用香港时区当天。初始为未完成，同组末尾追加，返回 `item`。创建者取自认证调用者，不能自报。
- `UpdateMailboxTodo(todo_id, changes)`：只可修改自己的待办，`changes` 为非空对象，只允许 `text`、`date`、`completed`、`indent`；`completed` 必须为布尔值，`indent` 必须为 0–4 的整数（不接受布尔值）；返回 `item`。
- `DeleteMailboxTodo(todo_id)`：删除自己的待办，返回 `todo_id`。
- `MoveMailboxTodo(todo_id, direction?, before_todo_id?, after_todo_id?)`：三个定位参数必须且只能提供一个。`before_todo_id` / `after_todo_id` 指定调用者自己的另一条待办，一次事务将源项放到其前面或后面；若目标日期不同，同步修改源项日期。不得以自己为锚点，目标不存在或不属于调用者时不修改任何顺序。`direction` 为 `up` 或 `down`，与同日的相邻待办交换位置，到边界时保持不变。移动不改变完成状态或缩进；返回 `item`。

Resident 的添加能力不授予读取、修改、删除或调序用户已有待办的权限。其他用户的待办 ID 与不存在的 ID 都按不存在返回，不能借此推断他人清单。修改按用户加事务锁；新增和其他写入沿用 ISA 幂等规则，重放返回已写入条目的引用，不再次创建。Todo 面板操作成功后重读，打开期间保持分组折叠状态；读/写失败明确报错并保留未保存草稿。

## 通知人类

### `CreateNotification`

**向人类参与者发送一条持久通知，写入每位接收者的 Mailbox。**

- 参数：`title`、`message`、`recipients`（接收者用户名列表；省略时通知人类发起人，或 Resident 发起人的登记 owner）、`lineage_id`（可选，通知所属的 Lineage）、`severity`（可选，`info`、`success`、`warning` 或 `error`，默认 `info`）、`notification_id`（可选，调用者提供的稳定 ID）。
- 结果：通知 ID、接收者列表，以及本次是否新建（相同通知 ID 重复提交时为 `false`）。
- 接收者用 `GetMailbox` 读取通知，用 `MarkMailboxItemRead` 与 `ClearMailbox` 标记和清理。由 SocietyService 执行。

## 参数名契约

下表列出 arguments 内的顶层参数名；必填条件、类型和枚举以 GetInstructionSchema 为准。调用信封中的 expected_version、idempotency_key 与 wait_seconds 不列入 arguments。

| 指令名 | arguments 参数名 |
| --- | --- |
| `GetMailbox` | `resident_id`、`user_id`、`related_resident_id` |
| `WatchMailbox` | `user_id`、`after_cursor`、`timeout_seconds` |
| `PinMailboxItem` | `key`、`pinned` |
| `MarkMailboxItemRead` | `key`、`unread` |
| `ClearMailbox` | `resident_id`、`user_id`、`mode`、`section` |
| `ReorderMailbox` | `resident_id`、`message_id`、`before_message_id`、`after_message_id`、`order` |
| `MergeMailboxItems` | `resident_id`、`message_ids` |
| `SplitMailboxItem` | `resident_id`、`message_id` |
| `RequeueMessage` | `resident_id`、`message_id` |
| `InjectMailboxItem` | `resident_id`、`message_id` |
| `ListMailboxTodos` | `user_id` |
| `CreateMailboxTodo` | `text`、`user_id`、`date` |
| `UpdateMailboxTodo` | `todo_id`、`changes` |
| `DeleteMailboxTodo` | `todo_id` |
| `MoveMailboxTodo` | `todo_id`、`direction`、`before_todo_id`、`after_todo_id` |
| `CreateNotification` | `title`、`message`、`recipients`、`lineage_id`、`severity`、`notification_id` |

格式名与版本须精确匹配当前规范值，校验规则见 decision_86aed2f0e8c7；本篇同时校验各字段和结构版本，不接受历史前缀或其他版本。
