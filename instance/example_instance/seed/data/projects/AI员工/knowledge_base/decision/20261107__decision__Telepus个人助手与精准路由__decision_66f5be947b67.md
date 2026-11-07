---
kb_id: decision_66f5be947b67
type: decision
project: AI员工
created_at: '2026-11-07'
version:
  number: 3
  created_at: "2026-11-07"
  created_by:
    user: ""
    agent: "resident_7fdebb465cdf"
  updated_at: "2026-11-07T12:09:58Z"
  updated_by:
    user: ""
    agent: "resident_60ae6d0cdf74"
importance: 7
ai_generated: false
links:
  source:
  - projects:AI员工/agents/lineage_c625a4bee56b/script/src/router_core.py
  - projects:AI员工/agents/lineage_c625a4bee56b/script/src/portal_client.py
  - projects:AI员工/agents/lineage_c625a4bee56b/script/src/telegram_adapter.py
  - projects:AI员工/agents/lineage_c625a4bee56b/script/src/router_store.py
  - projects:AI员工/agents/lineage_c625a4bee56b/script/src/main.py
  - ${code_root}/management_portal/personal_ama.py
  - ${code_root}/management_portal/society_service/personal_assistant.py
  related:
  - decision_692a484e9bea
  - decision_328fb2548b8f
  - decision_9ad68dd077d4
  - decision_bd5217d08c09
  - decision_44cf587c1721
  - decision_8fe87d812a4c
  - decision_c524c775e0be
  supersedes: []
  derived_from: []
metaness: 2
---
Telepus 把 Telegram 用户的消息交给这位用户在 Portal 中的个人助手门面，再把门面上的回复发回 Telegram。选人、改派和成员协作都由用户私人 Family 完成；Telepus 只负责身份对应、消息进出和少量会话控制。用户需要直接找某个 Resident 时，可以按 Resident ID 精准路由。

联系人登记、Contacts 界面、管理员代绑和服务开关见 decision_692a484e9bea；私人 Family 与个人助手入口见 decision_328fb2548b8f 和 decision_bd5217d08c09；门面的转交、引用直交与回复投影见 decision_9ad68dd077d4。

## 两种目标

每个 Telegram 聊天在任一时刻处于下列一种目标模式，模式保存在桥接数据库的聊天状态中：

| 模式 | 消息发给谁 | 进入方式 |
| --- | --- | --- |
| 个人助手（默认） | 联系人对应 Portal 用户的个人助手门面 | 新聊天；`/home` |
| 精准路由 | 用户指定的一个 Resident | `/route <resident_id>`，或发送只含一个 Resident ID 的消息 |

精准路由一直保持，直到用户发送 `/home` 或进入另一条精准路由。目标被删除或失去访问权时，Telepus 提示目标不可用并回到个人助手模式。

Telepus 不做目标推荐，不浏览 Resident 目录，也不按话题替用户切换目标。在个人助手模式下，“该交给谁”由门面后面的 Router 按私人 Family 的规则判断；用户在 Telegram 中说“改派给 X”“换一个成员”等，作为普通消息交给门面处理。

## 门面的查找

个人助手门面来自用户的个人助手入口关联（命名空间 `lineage-workbench.personal-assistant`，键 `primary`），网页导航和全局章鱼按钮读的也是这条关联。Portal 提供一个只读函数 `personal_ama.personal_assistant_target(username)`，网页与 Telepus 共用：

1. 读取该用户的关联，取 `resident_id`（门面）和 `lineage_id`。
2. 以该用户身份 `GetResident` 门面，确认可读，并且属于关联中的 Lineage。
3. 通过时返回门面 ID；关联缺失或校验失败时返回空。

Telepus 在每条个人助手模式的消息到达时调用一次，不缓存门面 ID，因此 Portal 重新初始化个人助手或更换门面后，下一条消息立即使用新门面。返回空时，Telepus 调用一次与网页相同的幂等补建 `personal_ama.provision(username)`（同一用户 10 分钟内只试一次），然后重读；仍为空时回复“个人助手尚未就绪”，并提示可以用 `/route <resident_id>` 直接联系某个 Resident。Telepus 不自己创建 Resident，也不按 Lineage 标题或其他规则猜测个人入口。

## 消息进入 Portal

1. 长轮询从 Telegram 取得新消息，已处理的 update 记入 `processed_updates`，重复 update 不再投递。
2. 按发信人的 Telegram 数字 ID 查联系人表，得到 Portal 用户；没有对应时回复未授权说明（decision_692a484e9bea）。
3. 以命令开头的消息按“命令”一节处理；只含一个 Resident ID 的消息切换到精准路由；其余消息按当前模式确定目标 Resident：个人助手模式为门面，精准路由模式为指定 Resident。
4. 以该 Portal 用户为 caller，调用 `DeliverMessage(resident_id=<目标>, activation_policy=enqueue_and_trigger)`。消息 ID 由 Telegram 的聊天 ID 与消息 ID 确定性生成，并作为幂等键，进程重启后重放同一 update 不会产生第二条输入。消息中的图片先存入 Portal 上传目录，以 `[image: <路径>]` 附在文字后；目标能否接收图片由门面和成员的媒体能力决定。
5. 用户在 Telegram 中“回复”Telepus 发出的某条消息时，Telepus 按下文的消息对应表找到这条 Telegram 消息对应的 Portal 消息，在投递中带 `quote: {message_id}`。门面据此把这条消息直接交给写原回复的成员（引用直交），用户无需知道成员 ID。
6. 投递成功后，Telepus 记下这条输入（聊天、目标 Resident、Portal 消息 ID、对应的 Telegram 消息 ID），并简短回复“已收到”。门面原消息转交成员后会结束自己的排队，这只表示已转交；实际进度和回复由后面的回复读取得到。

所有 Portal 调用都经签名的 HATG ISA，入口为 `web`，权限按这位用户自己的表项判定。个人 AMA 及其私人 Family 只对所有者可见，所有者本人可以完整读取门面对话。Telepus 不导入 SocietyService、ResourceService 等 Portal 内部服务，也不以 Telepus Resident 或其他身份代发、代读。

## 回复回到 Telegram

回复刷新服务（`telepus-portal-refresh`）按聊天读取目标 Resident 的对话事件：

- 每个聊天为当前目标保存一个事件游标。首次进入目标时用 `GetConversation` 取得 `cursor`；之后每轮以 `WatchConversationEvents(after_cursor=<游标>)` 读取首批事件后关闭订阅，处理完再保存最后一个游标。
- 门面对话已经是 Family 的对外投影：每条外部原输入下按顺序排列承接成员的中间输出和最终回复，Router、成员间往来和 Steward 巡检留在内部。Telepus 原样使用这份投影，不沿转交链自己去读成员对话。
- 发往 Telegram 的内容包括两类：回复属于本聊天发出的输入（按消息归属落到已记录的 Portal 消息 ID 下）的最终回复；门面或目标 Resident 主动发给这位用户、不属于任何输入的消息，例如 Steward 通过门面提出的组织变更方案。用户在网页上发出的输入及其回复留在网页，不推送到 Telegram。
- 多个成员并行回答时，每条回复独立发送，按事件顺序到达，不再只发送“最新一条”。每条回复以承接成员的标题开头（门面投影给出的回复归属），用户能看出是谁在答复。
- 长文本按每段最多 3000 个字符分段；回复中的附件生成下载按钮，点按后由 Telepus 以当前用户身份读取并发送文件。
- 每发出一段，Telepus 在消息对应表中登记“Telegram 消息 ID → Portal 消息 ID”，供后续引用使用。
- 输入以失败或取消结束时，发送一条说明，内容取自输入状态中的失败原因。

刷新前重新按联系人表确认 Telegram 用户仍对应同一 Portal 用户；联系人已删除或改绑时，该聊天停止刷新，结果记为 `unbound`，不以旧用户身份读取 Portal。

Resident 用 `DeliverMessage(user_id=...)` 发给用户 Mailbox 的消息不经过 Telepus，仍在 Portal 的 Mailbox 中阅读。

## 按需查看完整对话

用户在网页上与门面或精准路由目标的往来不主动推送，网页端大量工作时手机不会被刷屏。需要在手机上接着网页的话题时，用户发送 `/history` 主动拉取：

- `/history` 读取当前目标（门面或精准路由 Resident）最近 3 轮对话，不区分这些消息来自网页还是 Telegram；`/history <n>` 指定 1 到 10 轮。一轮是一条用户输入及其最终回复，按时间从旧到新排列。
- 读取使用 `GetConversation(history_limit)`，更早的部分用返回的 `next_cursor` 经 `ListMessages` 续读；门面对话本身已按私人 Family 的投影规则包含 Familize 之前的 AMA 历史，Telepus 原样展示。
- 为避免一次占满屏幕，每轮的输入摘录前 200 个字符、回复摘录前 1000 个字符，并标注来源（网页或 Telegram）和承接成员。被截断的轮次附“展开”按钮，点按后按“回复回到 Telegram”的分段规则发送该轮全文；列表末尾有“更早”按钮，按保存的 `next_cursor` 继续翻页。
- 拉取到的消息同样登记进消息对应表，用户可以在 Telegram 里直接“回复”其中一条网页上的回复，门面按引用把后续消息交给原成员，话题由此转到手机上继续。
- `/history` 只读，不移动回复刷新的事件游标，也不改变推送范围。

## 命令

| 命令 | 个人助手模式 | 精准路由模式 |
| --- | --- | --- |
| `/start`、`/help` | 说明当前模式和可用命令 | 同左 |
| `/home` | 已在个人助手模式时说明当前目标 | 回到个人助手模式 |
| `/route <resident_id>` | 切换到精准路由 | 切换到另一个 Resident |
| `/where` | 显示门面标题与 ID | 显示目标 Resident 的标题与 ID |
| `/queue` | 列出本聊天尚未结束的输入及其状态（`GetInputState`，沿转交链给出实际成员的状态） | 显示目标 Resident 的队列，可删除、置顶或插入当前工作 |
| `/stop` | 对本聊天尚未结束的输入逐条 `CancelInput`；门面撤回排队中的转交，停止正在执行的副本 | 停止目标当前任务并清空队列；`/stop current` 只停止当前任务 |
| `/now <补充内容>` | 不提供；补充内容直接作为普通消息发给门面 | 插入目标正在进行的工作（`inject_active_turn`） |
| `/refresh` | 立即执行一次回复读取 | 同左 |
| `/history [n]` | 拉取门面最近 n 轮完整对话（含网页端） | 拉取目标 Resident 最近 n 轮对话 |

## 桥接数据

桥接数据库位于 Telepus schema（`TELEPUS_DATABASE_SCHEMA`），由桥接程序维护：

| 表 | 内容 |
| --- | --- |
| `chat_state` | 聊天 ID、Telegram 用户、Portal 用户、模式（`assistant`、`exact`）、精准路由的目标 Resident ID、当前目标的事件游标 |
| `telegram_inputs` | 本聊天发出的每条输入：目标 Resident、Portal 消息 ID、Telegram 消息 ID、是否已结束 |
| `message_links` | Telegram 消息 ID 与 Portal 消息 ID 的对应，供引用 |
| `callback_tokens`、`attachment_deliveries` | 按钮回调（含 `/history` 的展开与翻页游标）与附件发送 |
| `processed_updates`、`audit_events` | 去重与审计 |

门面 ID 不落库，每次从关联读取。联系人表由 Portal 写入，桥接只读（decision_692a484e9bea）。

## 职责划分

- **Portal 负责**个人助手的初始化与入口关联、门面的选人与转交、成员执行、门面对话投影以及全部权限判断。
- **Telepus 负责**Telegram 连接、联系人对应、目标模式、消息与回复的格式转换、按聊天的游标与消息对应表。
- **Telepus Resident**（`resident_7fdebb465cdf`）维护桥接程序；它不在消息路径上，用户要找它时用 `/route resident_7fdebb465cdf`。

## 失败与恢复

| 情况 | 表现 | 处理 |
| --- | --- | --- |
| 个人助手尚未就绪 | 补建一次仍无门面，回复说明 | 稍后重发，或用 `/route` 直接联系 Resident |
| 投递被拒或服务不可用 | 回复错误，不换 caller 或目标重试 | 用户重发；同一 Telegram 消息重放沿用同一幂等键 |
| 精准路由目标被删除或无权访问 | 提示目标不可用并回到个人助手模式 | 重新 `/route` |
| 刷新服务中断 | 回复暂时不到达 | 恢复后从保存的游标继续，中断期间的回复按顺序补发 |
| Telegram 发送失败 | 该段回复未送达，游标不前进 | 下一轮重试；用户也可 `/refresh` |
| 联系人删除或改绑 | 停止刷新，新消息按新绑定处理 | 无需处理 |

## 升级

从旧版本升级时，所有聊天回到个人助手模式，原来保存的 Resident 目标清除，事件游标从门面对话末尾开始，升级前的回复不补发；用户可用 `/history` 查看之前的对话。推荐请求记录和它的表一并删除。桥接 schema 版本加一，运行进程只接受新版本。

Breakglass 应急通道随之删除：停用并移除 `telepus-breakglass-worker` 服务，删除 Breakglass 的会话、任务、租约与历史表及其模型配置读取。Telepus 服务开关此后只管理 `telepus-portal-refresh` 与 `telepus-telegram-router` 两个服务（decision_692a484e9bea 同步修改）。
