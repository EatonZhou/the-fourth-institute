---
kb_id: decision_15f7df737ed5
type: decision
project: AI员工
created_at: '2026-10-13'
version:
  number: 50
  created_at: '2026-10-12T17:40:00Z'
  created_by:
    user: tfi-exported-user
    agent: resident_0b07d8e39e01
  updated_at: '2026-10-20T00:59:11Z'
  updated_by:
    user: ''
    agent: resident_6199a7a36480
importance: 9
metaness: 2
ai_generated: false
links:
  source:
  - ${code_root}/management_portal/society_service/resident_mailbox.py
  - ${code_root}/management_portal/society_service/resident_intake_domain.py
  - ${code_root}/management_portal/society_service/resident_input_recovery.py
  - ${code_root}/management_portal/society_service/resident_execution_domain.py
  - ${code_root}/management_portal/society_service/resident_catalog.py
  - ${code_root}/management_portal/society_service/resident_protocol.py
  - ${code_root}/management_portal/society_service/runtime/backend_adapter.py
  - ${code_root}/management_portal/society_service/outward_messages.py
  - ${code_root}/management_portal/society_service/lineage_human_notifications.py
  - ${code_root}/management_portal/society_service/lineage_failure_notifications.py
  - ${code_root}/management_portal/hatg/catalog/resident_execution.py
  - ${code_root}/management_portal/hatg/catalog/conversation.py
  - ${code_root}/management_common/principal.py
  - ${code_root}/management_portal/web_isa.py
  - ${code_root}/management_portal/mailbox_todos.py
  - ${code_root}/management_portal/static/portal_mailbox_todos.js
  - ${code_root}/management_portal/agent_conversation_index_backend.py
  - ${code_root}/management_portal/conversation_index_projection.py
  - ${code_root}/management_portal/conversation_completion.py
  - ${code_root}/management_portal/http_portal_session.py
  - ${code_root}/management_portal/serve_management_portal.py
  - ${code_root}/management_portal/society_service/resident_conversation_projection_domain.py
  - ${code_root}/management_portal/static/portal_chat_workspace.js
  - ${code_root}/management_portal/static/portal_agent_inbox.js
  - ${code_root}/management_portal/static/portal_conversation_store.js
  - ${code_root}/tests/cases/test_resident_mailbox.py
  - ${code_root}/tests/cases/test_resident_mailbox_requeue.py
  - ${code_root}/tests/cases/test_resident_queue_after_turn.py
  - ${code_root}/tests/cases/test_lineage_batch_deletion.py
  - ${code_root}/tests/cases/test_conversation_pagination.py
  - ${code_root}/tests/cases/test_agent_conversation_index_backend.py
  - ${code_root}/tests/cases/test_conversation_projection_authority.py
  related:
  - decision_c524c775e0be
  - decision_44cf587c1721
  - decision_a6bebf4ac283
  - decision_605810a1292b
  - decision_2fe6fd5367fb
  - decision_2a3ff353165c
  - decision_18abbdef3da3
  - decision_abaedeefca6f
  - decision_34158fdd4078
  - decision_ce44377c3257
  - decision_9f6135cbbb22
  - decision_898b8911a2ff
  - decision_ae422b92c6f3
  - decision_edc470e09f5a
  - decision_b1ab45c4b3ec
  - decision_f560803947c6
  - decision_6fc653d55c79
  - decision_4adcc1ff2f2c
  - decision_b820faf42218
  supersedes: []
  derived_from: []
---
# Mailbox：Resident 和 User 的收件

Portal 中参与对话的主体有两种：Resident 和 User。两者收消息的方式相同：别人用 `DeliverMessage` 把消息发给它，消息写在一段对话里，收件方在自己的 **Mailbox** 中看到发给它的消息和处理情况（concept_83af607c587f）。区别在于怎样处理：

- **Resident 按轮处理。** 它一次只处理一条输入，工作时收到的消息按顺序排队。Resident 的 Mailbox 列出待处理的消息、正在处理的一轮和最近已送达的轮次；有整理权限的人可以合并、拆分、调序和放回其中的消息。
- **User 靠阅读和回复处理。** User 的 Mailbox 是页面右边缘“我的 Mailbox”入口展开的面板，列出他参与的对话和发给他的通知，在“消息”栏统一显示，并标出哪些还没读；另有“Todo”栏保存可编辑的个人待办。User 自己决定先看哪条、回复哪条、完成哪项待办，没有执行队列。

登录为 User 的人和远程 agent 使用同一个 Mailbox 和同一组 HATG ISA 指令；远程 agent 不需要像 Resident 那样排队串行执行，它和人一样自由选择处理什么。

本篇先说明双方共用的消息模型，再分别说明 Resident Mailbox 和 User Mailbox，最后列出指令。

## 共同模型

- **一条消息只保存一份。** 消息写在它所在的对话中：Resident 的对话、Family 门面的对话（decision_44cf587c1721）或聊天室（decision_a6bebf4ac283）。收件方的 Mailbox 只指向它，不复制。消息写入后作者、时间、正文和收件人都不再改变。
- **给 Resident 和给 User 发消息使用同一条指令。** `DeliverMessage` 带 `resident_id` 时，消息作为输入进入这个 Resident 的 Mailbox；带 `user_id` 时，消息以发送方身份写入发送方自己的对话（发送方在 Family 内时写入门面对话），`recipients` 记录这位用户，不开始任何工作（decision_c524c775e0be“发给用户”）。收件用户必须能查看这段对话。
- **用户在消息所在的对话中回复。** 回复是发给这个 Resident 或门面的普通消息，进入它的 Mailbox；回复可以引用原消息，收到的 Resident 在本轮提示中看到引用的作者和摘录。
- **需要交流的进对话，单向告知的进通知。** 发送方希望用户回复、讨论或决定时，用 `DeliverMessage(user_id)`；只需让用户知道、不需要交流时，用 `CreateNotification`（decision_2fe6fd5367fb）。两者都出现在收件用户 Mailbox 的“消息”栏，分别按对话和通知的方式打开。
- **平台产生的消息找一个能对话的署名者。** 平台事件需要和用户交流时，消息以相关 Resident 的名义发出，来源 `origin.producer` 记为 Portal 服务，不启动这个 Resident 的工作；用户回复后，消息照常进入它的 Mailbox。找署名者的规则见“平台事件的收件”。

## 第一部分：Resident Mailbox

每个 Resident 一次只处理一条输入，正在工作时收到的消息按顺序排队等待。几个人先后发来相关的补充时，逐条处理要分好几轮；Mailbox 让有整理权限的人把队列中尚未执行的多条消息合并成一条**群体消息**，Resident 在一次工作中同时看到这些内容。每条原始消息保留真实作者和原文，群体还没开始执行时可以拆回去。

### 基本概念

- **原始消息**：由一个 User 或 Resident 发送的用户输入，有稳定的消息 ID、作者、时间、内容分段和可信来源（decision_ae422b92c6f3）。它是合并和拆分的最小单位。
- **群体消息**：由 Mailbox 署名的一条新消息，有自己的消息 ID，按处理顺序列出至少两条原始消息，成员条数不设固定上限。它在队列中只占一个位置，执行时作为一条完整输入交给 Resident。
- **Mailbox 项**：队列中可以被调度执行的一项，要么是一条原始消息，要么是一条群体消息。已经放进群体的原始消息不再作为独立项出现在队列中。
- **info（背景更新）**：以 `activation_policy=attach_to_next` 投递的消息（concept_43c793b4f3ab）。它不单独开始工作，在 Mailbox 中等待，随下一条请求一起送达；不参与合并和调序。
- **整理者**：发起合并、拆分、调序或放回队列的已登录 User 或 Resident。整理权限由权限系统按相应指令在当前模板中的要求判定（默认模板要求对目标 Resident 有 contributor），owner 关系不产生权限。整理不改变执行时的权限：Resident 处理整理后的消息仍只按它自己的权限判定（decision_b820faf42218）。

Mailbox 是现有 Conversation 的一部分，不新增服务、Principal、账号或资源类型。各部分的保存位置：

| 内容 | 保存在哪里 |
| --- | --- |
| 已接收、还没进入 trace 的消息（原始消息与群体消息） | Mailbox 待交付消息记录，每条消息一行；进入 trace 后这一行删除 |
| 已送达的消息 | Resident 的 trace（消息 Segment），Chat 从 trace 读出 |
| 各条消息的输入状态 | 输入状态表 输入状态记录 |
| 等待顺序；群体项内保存成员原来的队列项 | Mailbox 的 `place` 与 `position`：`queue` 是队列项，`group` 是等待中群体的成员（`group_id` 为群体消息），`info` 是等待中的 info，`pending` 是已接收但不在队列中的消息（例如已离开队列、还没写入 trace 的一轮）；成员原来的队列项在 保留的原队列项 |
| 等待队列（页面、`GetMailbox`、对话快照） | 读取时由 Mailbox 的 `queue` 行组成，被挂起的 Job 首项排在前面；不另存副本。队列与上一次 `resident.queue.changed` 事件告知的不同时，才再记一条这个事件 |
| 哪条消息属于哪个群体 | 等待时为 `group` 行；送达后成员消息在 trace 中带 `grouped_into_message_id` |
| 已送达的 info 随行的请求 | trace 中 info 消息的 `released_with` |
| 每一轮的请求、Job、随行 info 和开始时间，以及每次放回 | 只追加的 轮次收据记录，永久保存 |
| 最近 10 轮的原队列项（放回队列用；完成的一轮不再保留） | Mailbox 最近轮次状态，按 Resident 一份 |

SocietyService 校验并执行整理；ExecutionService 继续负责执行。页面和 Agent 做同一件事时调用同一组 ISA 指令。

### 合并

- **有整理权限的主体选中至少两条待执行的项，服务端生成一条署名为 Mailbox 的群体消息，用一个顶层项替换它们。** 群体项放在被选中项中最早那一项的位置，其他项的相对顺序不变。
- **选中的消息可以来自不同发送者。** 能把消息发给这个 Resident，说明发送当时的权限没有问题。群体输入逐条写明发送者；多发送者时在开头提示“这些消息来自不同发送者”，每条请求是否执行、做到什么程度由收到的 Resident 按发送者判断。
- **执行时只按 Resident 自己的权限判定。** 无论群体有几位发送者，Resident 处理它时调用指令都只用自己的权限；发送者和整理者写在回合的授权说明中，供 Resident 判断该不该替对方做，不参与判定（decision_b820faf42218）。
- **选中已有的群体项时按成员展开，结果仍是一层。** 新群体的成员顺序是各被选项在队列中的先后，已有群体内部保持原顺序；被并入的旧群体输入以 `cancelled`（原因 `mailbox_split`，说明“已并入”新群体）结束。
- **以下情况拒绝合并：** 少于 2 条原始消息；同一条消息出现两次；选中的消息已不在等待队列中；有成员已被执行领取、是 `/compact` 这类控制输入，或带独立上下文范围。
- **客户端只提交消息 ID。** 成员的作者和正文由服务端从队列项和对话存储读取，请求不能替换作者或正文。

### 拆分

- **还没开始执行的群体项可以拆分，成员按原顺序回到群体所在的位置。** 例如队列 [A, X, B] 合并 A、B 后是 [G(A,B), X]；拆分 G 得到 [A, B, X]，想恢复 [A, X, B] 再调一次顺序。
- **拆分恢复的是原来那几条消息本身。** 成员的消息 ID、作者、时间、内容和附件引用都不变，不重新发送，也不产生副本；它们在群体存在期间一直保持 `queued`。群体消息作为记录保留，输入状态结束为 `cancelled`，原因 `mailbox_split`。
- **想调整群体内部的成员，先拆分，再重新选择合并。**

### 调序

- **有整理权限的主体可以把一项移到另一项之前或之后，也可以提交全部顶层项的完整新排列。** 调序只改变等待顺序，不修改创建时间、作者、运行记录和群体内部原文；群体项整体移动。新消息仍排到队尾。
- **已经持有排队执行的等待项随调序同步优先级。** 排在越前的项在调度器中优先级越高；已经开始或结束的执行不受影响。
- **页面显示服务端确认后的顺序。** Mailbox 栏提供调序操作，提交后显示指令返回的最新队列。

### 执行、注入与取消

- **群体项与普通消息使用相同的优先发送、注入和取消操作，另外多一个“拆分”。** 群体消息作为一次输入交给现有处理链，不按成员拆成多轮；这些操作仍受运行状态、Provider 能力和权限约束。
- **群体项一旦被执行领取，成员和顺序就固定下来。** 之后不能再合并、拆分或调序，进入执行后使用现有的停止操作。
- **群体结束时，所有成员得到同样的终态和回复。** 执行结束的事实到达时，系统把群体的终态和回复消息 ID 写给每个成员；未完成的终态在成员的错误信息中注明所属群体。在这次写入之前读取成员状态时，按群体当前状态投影：群体在运行，成员显示运行中；群体已结束（拆分除外），成员显示同样的终态和回复。
- **取消整个群体会一起取消其中尚未处理的成员。** 撤回时移出队列、成员终态与群体终态在同一事务提交。取消群体项的权限与普通排队消息相同，由 CancelInput 在当前权限方案中的要求判定：默认方案要求对目标 Resident 有 `@contributor`。
- **撤回不在对话中留下记录。** 等待中的消息还没有送到 Resident，撤回只结束它在 Mailbox 中的这一项（输入状态记为 `cancelled`）：对话中不写“这条排队消息已取消”这类系统消息，清空队列时尚未开始的队首消息也不写停止提示；撤回的人在页面右下角看到短暂提示“已撤回，这条消息不会被处理”（Chat 排队区、门面请求和 Mailbox 栏相同）。已经开始的一轮被停止，仍在对话中留下停止提示，因为它是 Resident 实际经历的事。

### 节点对话框中的 Mailbox 栏

Mailbox 的历史分组查询从消息 ID 索引定位源 Segment，再沿 `message.group` 关系找到分组，并用当前 trace 的 Segment 引用索引确认归属。它不依赖 Chat 的展示投影，也不对每个成员遍历整个 trace。成员合并后从当前 trace 消失时，通过转换关系读取原始内容供审计和重新排队，不恢复为额外 trace 成员。

节点对话框（双击画布上的 Resident 卡片打开）左侧是一列窄的竖向导航：General、Package、Mailbox，上下方向键与 Home、End 在三栏间切换。切换时卡片大小不变（高 600px，不超过视口高度的 92%）：内容较长的一栏在卡片内滚动，General 的“删除节点／取消／保存节点”固定在卡片底部，每次切换都从该栏顶部开始显示。Mailbox 栏是这个 Resident 收件与处理情况的完整视图；合并、拆分和调序只在这一栏进行：Chat Workspace 排队区保持简洁，每条等待中的消息只有“立即注入”和“删除”，两条以上时标题栏有“全部清除”。标题旁有一个低调的小字链接“在 Mailbox 中合并、拆分、调序 ›”，字号和颜色与标题栏相同，悬停时才显示强调色和下划线；点击后打开这个 Resident 的 Menu 并直接进入 Mailbox 页。栏的内容来自 `GetMailbox`（字段见 decision_2a3ff353165c），撤回与 Chat 排队区的“删除”使用同一条指令 `CancelInput`；只有能整理这个 Mailbox 的人看到勾选框、拖动和操作按钮。

- **待处理**：先按处理顺序列出队列中的请求和群体，再按到达顺序列出等待中的 info。每项显示类型（请求、群体 · N 条、info）、发送者、时间、前 200 字的摘要和附件数。点一项的摘要或“查看全文”打开叠在节点对话框上的消息卡片（见下）。请求和群体可以拖动、或聚焦后用 Alt+↑/↓ 调序（`ReorderMailbox`），勾选两项以上合并（`MergeMailboxItems`）；可勾选的项有两项以上时，工具栏在“合并所选”前有“全选”按钮，一次勾选全部请求和群体，全部勾选后按钮变为“取消全选”，再按清空勾选。群体可以拆分（`SplitMailboxItem`）；三类都可以撤回（`CancelInput`，info 撤回后不再随请求送达）。info 不参与合并和调序。
- **处理中**：Resident 状态中的当前请求，以及这一轮交给执行服务时随它送达的 info（“随行 info N 条”，点开列出发送者、时间和摘要）。
- **最近已送达**：最近 10 轮中已经结束的，新的在前；每轮显示结果（完成、已停止、失败）、请求、结束时间、失败原因和随行的 info。停止或失败、并保留了原队列项的一轮有“放回队列”按钮。
- **放回队列**：停止或失败的一轮可以把它的消息放回等待，包括随请求送达的 info。放回的是原来那些消息本身，消息 ID、发送者、时间和正文都不变，steward 发的 info 仍署名 steward；它们按原顺序回到队首，info 仍随下一条请求送达。那一轮的停止或失败记录保留在对话中。已经完成的请求不能放回，需要重做时发新消息。权限与整理相同。
- **消息卡片**：待处理、处理中、最近已送达和随行 info 中的每一项都能点开卡片。卡片第一次打开时用 `GetMessage` 读取完整正文和图片、文件（群体逐条读取成员原文），栏打开期间再次打开不再读取；Esc 或右上角关闭只关掉卡片，焦点回到该项。卡片顶部的“显示实际发送的全部 prompt”开关用来查看 Portal 在用户输入之外加了哪些内容；按下后，栏打开期间之后打开的卡片也保持这个选择。
  - 已经送达的一轮（处理中或最近已送达）用 `GetSentPrompt` 从 Provider 会话记录读出这一轮实际发送的用户位置内容。消息原文和随行 info 原文高亮，其余是 Portal 加上的部分，例如运行身份、背景更新、授权上下文和 Agent Harness 运行提示。随行 info 的卡片显示带它送达的那一轮。会话记录里查不到这一轮时（会话重置后或较早的轮次）卡片说明查不到；没有读取权限时显示错误。
  - 等待中的消息还没有发送，卡片注明显示的是预览：用 `PreviewPromptInjection`（`turn=later`）按当前 Harness 设置生成，消息原文放在消息位置。随行 info、附带引用和 Agent Harness 运行提示在发送时才确定，不在预览中。
  - 两种情况下，卡片下方都折叠显示系统位置的 Portal 内容（例如 Portal 核心规则），注明按当前设置预览；这部分在会话开始和压缩后发送，不随每条消息重复。
- **刷新**：栏打开时订阅这个 Resident 的对话事件（与 Chat 共用浏览器中的同一份订阅），任何变化后 250 毫秒重读一次 `GetMailbox`；每次操作后也立即重读。重读后保留展开的随行 info、勾选、焦点和滚动位置，打开的卡片不受重读影响；拖动进行时推迟到放下后再显示。切到其他栏或关闭对话框时取消订阅，不再重读。

### 放回队列的实现

- **显式恢复与后台自动恢复按操作来源区分。** User 或 Resident 调用 `RequeueMessage` 都属于显式恢复，按相同的整理权限执行，不受后台自动恢复次数限制，也不消耗或重置该计数。调用者是 Resident、通过工具调用，或操作由另一位主体委托，都不意味着自动恢复。后台扫描与后台发起的 Session 迁移恢复共享每条输入最多两次的持久计数；达到上限只停止后台重试，有权限的主体仍可显式恢复。显式请求的 Session 迁移完成后恢复原输入，保留显式语义。权限、输入状态、最近轮次窗口和幂等约束照常检查。
- **首次预留时保留原输入。** 输入 owner 在首次预留的同一事务中保存原接收项和检查点，早于提示构造与材料化；随后追加随行 info 时只补其附带关系，不替换原接收项。一条请求或一个群体被交给执行服务时，它的消息 ID、Job、开始时间和随行 info（消息 ID、发送者、到达时间）追加到 轮次收据，永久保存；原来的队列项（去掉执行和 Job 加上的字段）是放回用的当前状态，存在 Mailbox 最近轮次状态 中，只留最近 10 轮，记录下一轮时已经完成的轮次不再保留队列项。每次放回也追加一条记录（放回者、时间、放回前的状态和错误）。`/compact`、Fork 的准备、项目创建和授权调用的隔离输入不是一轮。
- **放回时恢复原来的消息。** `RequeueMessage` 取出这一轮的原队列项，换一个新的 Job ID 放到队首；Resident 空闲时它直接成为下一项工作，接单关闭时照常等待。请求（群体连同成员）和随行 info 的输入状态从终态改回 `queued`，回复与错误清空，这是唯一能改变终态的写入；info 回到等待列表的最前面，不再显示在原请求里，随下一条请求送达。群体成员回到 Mailbox 的 `group` 行。图片附件的暂存副本在最近 10 轮的重新排队窗口内保留。输入已经终结且离开窗口后，由轮次记录与结束提交触发清理；同一状态锁内核对窗口和输入状态，群体成员一并保留。窗口内的随行 info 也保留；状态未知或尚未终结的输入不清理。放回后这一轮离开最近记录，最近轮次记录 记下放回者、时间和原来的终态。
- **随行 info 也逐项校验。** 正常随原停止或失败轮次消费的 info 保留随原请求恢复的语义；原消息和原轮次附带关系必须完整、同属目标 Resident。独立撤回、缺原文或身份/归属冲突导致整项恢复拒绝，不能跳过该 info 后宣称恢复成功。
- **对话中的记录不变。** 停止提示（“…已停止当前运行”）和失败说明留在原处；同一条消息再次被停止时得到新的停止提示（提示的消息 ID 由消息 ID 和 Job ID 共同决定）。消息在对话中仍只有一条，它的状态随新的一轮变为排队、运行和结束，新的回复接在对话末尾。
- **来源不变。** 放回的请求保留原发送者，执行时与其他输入一样只按 Resident 自己的权限判定；整理者只需要 RequeueMessage 要求的等级，与合并相同。
- **检查点和执行位置限定恢复范围。** 首次预留后已有完整检查点的停止或失败轮次，即使尚未提交执行，也可在最近 10 轮窗口内恢复；没有可用检查点的历史轮次、预留前已撤回的请求、同步执行命令及窗口外轮次按冲突拒绝。同一顶层请求已经处于活动或等待位置时，重复调用保持现状并返回成功；群体成员和附属 info 不能借此独立恢复。

### 第一轮结束后推进队列，以及没有任何工作在处理的输入

- **第一轮和其他轮一样推进队列。** 每轮确认执行结束后，从队列显式构造下一轮的输入，再由执行层补充新的执行事实；不继承上一轮的失败通知、回执或事件游标。队列为空才清空当前轮次。
- **没有任何工作在处理的输入永远不会结束。** 这类输入的回执停在 `queued` 或 `running`，但没有任何一处持有它：它不在当前轮次和队列里，不是 Resident 正在处理的消息，Mailbox 中只剩 `pending` 行或没有行，也从未有过 Execution。早期版本在 Resident 第一轮结束时清掉 Job、不推进队列，期间排队的请求就这样留在 Mailbox 的 `pending` 行里（队列丢下的请求）；中断的执行也可能留下停在 `running` 的回执。
- **`CancelInput` 结束这类输入。** 普通撤回和停止都找不到它时，`CancelInput` 在 Resident 的状态锁内逐项确认上述条件，然后把它记为 `cancelled`（错误码 `lost_input`，说明“没有任何工作在处理这条消息，它不会再送达，已撤回”），`pending` 行随之移出 Mailbox。任何一处仍持有它时，照常按排队或执行中的消息撤回或停止。已有 Execution 的输入不记为 `lost_input`：运行结束后，结果在状态锁释放后才写进输入状态，这期间它已离开当前轮次，它的终态由这次运行的结果决定。撤回权限与普通排队消息相同。
- **完整接收记录可用于显式恢复。** 输入仍为 `queued`、没有活动或等待执行位置，且同一 Resident 的持久接收项与准备记录或本地检查点能精确证明原请求、群体成员和随行 info 时，有权限的主体可通过 `RequeueMessage` 恢复唯一队列位置。保留原 ID、作者、时间、正文、引用、origin 和成员顺序；info 仍随请求送达。缺失前像、冲突、跨 Resident、成员另有归属、已完成或取消时拒绝；不能从提示词或发件副本猜造原接收消息。校验、原消息恢复、状态和队列发布由输入 owner 在锁与事务内完成；失败不留下部分恢复，再次恢复已有有效位置保持不变。
- **不按等待时长自动结束或重建。** 缺少完整接收记录的输入仍不能放回；`CancelInput` 保留原撤回职责。接单关闭或等待额度时，排队输入可以合法地长时间不动；恢复仍受正常执行门控约束。
- **没有可列出的等待项时，Mailbox 栏和 `GetMailbox` 不展示这类输入**。可按消息 ID 核验恢复条件后调用 `RequeueMessage`，或通过 `CancelInput` 撤回。查找方法：在 输入状态记录 中找状态为 `queued` 或 `running` 的输入，再排除在队列、当前轮次中或有过 Execution 的那些；Mailbox 群体成员的状态跟随群体，按 `GetInputState` 读到的状态判断。

### Resident 删除时关闭 Mailbox

删除标记、当前轮次的归档与删除、待确认 Execution 的生命周期步骤一并提交。重复删除沿用已有的清理操作，不丢失待执行步骤，也不重启已永久失败的操作。

- **删除 Resident 时，它的 Mailbox 在同一事务中关闭。** 等待中的 info 和队列丢下的请求不阻止删除（只有排队或执行中的工作阻止，decision_0f57656a2acd），删除后它们无法再被处理或撤回：删除标记写入后，`CancelInput` 只会回答 Resident 不存在。因此 `ResidentCatalog.remove` 在写入删除标记之前，把仍为 `queued` 或 `running` 的输入结束为 `cancelled`（错误码 `resident_deleted`），并删除这个 Resident 在 Mailbox 中的全部行。尚未送达的消息正文随 Mailbox 行一起删除，与撤回相同；对话中不写记录。

### 群体消息的展示

- **队列中的群体项显示为“Mailbox · N 条消息”，并逐条列出成员的发送者和前 120 字。** 队列计数是顶层项的数量。
- **对话中，群体消息的正文就是它合并的成员，成员消息标注“已并入 Mailbox 群体消息”。** 群体消息的发送者显示为“Mailbox”；气泡第一行是“合并了 N 条消息，作为一次输入处理”，下面按顺序列出成员，每个成员一行发送者（Resident 显示名称）和发出时间，再接 Markdown 渲染的原文。不超过 160 字且只有一行的成员直接显示全文；更长的成员折叠为一行预览，点开后显示全文。群体自己没有正文（只有 Portal 加上的运行提示），所以不显示“无正文”。随群体送达的背景更新仍折叠在气泡下方的“附带背景更新 N 条”中。读取对话时，服务端给群体消息附上只读的 `expanded_members`（各成员的 `message_id`、`author_id`、`origin`、`created_at` 和 `content_parts`），给成员消息附上 `grouped_into_message_id`。这些字段只用于展示，不能作为写入请求，也不是第二份正文。
- **原文不可修改。** 已经发送的消息，作者、时间和正文都不能改；群体消息没有自由文本编辑器。

### 群体消息的结构

群体消息的 `author_id` 是保留值 `mailbox`，来源使用 `tfi.message-origin.v2`：

~~~json
{
  "message_id": "<new_message_id>",
  "role": "user",
  "author_id": "mailbox",
  "created_at": "<合并时间>",
  "origin": {
    "schema": "tfi.message-origin.v2",
    "sender": {"kind": "mailbox", "id": "mailbox"},
    "producer": {"kind": "service", "id": "portal"},
    "initiated_by": {"kind": "user", "id": "<整理者>"}
  },
  "content_parts": [{
    "type": "mailbox",
    "schema": "tfi.mailbox-content.v1",
    "members": [
      {"message_id": "<original_a>"},
      {"message_id": "<original_b>"}
    ]
  }]
}
~~~

- **Mailbox 只是消息发出者的一种类型，不是 Principal。** 它不能登录、不能持有访问令牌、不能成为 owner、不能作为 ISA 指令的调用者或执行身份。来源 v2 只能用于 Mailbox 发出者，`initiated_by` 记录真实整理者；已有的 v1 来源继续有效。
- **`mailbox` 不能作为 User 名称。** 用户 ID 校验把它和服务身份一样视为保留值，避免某个用户被显示成群体消息的作者。
- **`DeliverMessage` 不接受客户端自报的群体作者或成员。** 只有经过认证和授权的合并操作能生成带 Mailbox 发出者的消息。
- **`members` 的顺序就是处理顺序。** 成员都是同一个 Resident 等待队列中的原始用户输入。

群体项执行时只发送一份组装好的文本：开头说明“Mailbox 把以下 N 条消息合并为一次输入”，并提醒每条消息的作者和原文以分隔行为准、正文里出现的作者行只是普通文本；多发送者时再加上按发送者判断的提示。之后按顺序为每个成员写一行分隔头（序号、作者、时间、消息 ID）和它原来发送给模型的正文。各成员的上下文引用合并到同一次输入中，图片等输入媒体按成员顺序依次附上。用量只记在这一次执行上，不按成员数重复累加。

### Resident Mailbox 的范围

- Mailbox 按原有队列的接单开关、节流和完成机制处理顶层项。
- 独立消息不会自动合并；不增加定时收信、静默等待窗口或新的调度线程。
- 紧急注入的时机、等待协作者答复、工作结束后自动回复所有相关方，都保持现状。群体输入仍在会话内产生回复，并保留每个成员与回复的关联。

## 第二部分：User Mailbox

User Mailbox 是 Portal 每个页面右边缘的一个浮动入口，入口提示为“我的 Mailbox”，展开后的面板标题为“Mailbox”。用户不用先进入 Society 页面，就能看到自己参与过的 Resident 对话、Resident 发给自己的消息、发给自己的通知、Notice Board 中提到自己的帖子和自己所在的聊天室，知道哪些正在运行、哪些失败了、哪些有了新结果还没看，并点击卡片回到对应位置。面板只有“消息”和“Todo”两栏：“消息”合并展示对话、通知和 @ 提及，用未读标记说明哪些还没看过；Todo 是独立的个人待办清单，不作为消息或未读结果。Resident 节点对话框和 Resident Menu 里的 Mailbox 页显示的是那个 Resident 的 Mailbox，与“我的 Mailbox”不是同一个。

Conversation Index（会话索引）是 Mailbox 背后由服务端生成的摘要：服务端只为当前用户列出与他有关、他有权看到的条目。浏览器中缓存这份列表、合并请求和处理回执的共享状态层见 decision_18abbdef3da3；打开卡片之后的聊天界面见 decision_ce44377c3257。

### 基本概念

| 名称 | 含义 |
| --- | --- |
| 条目 | 列表中一张卡片背后的一条摘要，包含标题、摘要、来源、更新时间、状态和指向 Portal 页面的地址，不包含聊天正文、附件或草稿。每个条目有一个在当前用户范围内唯一的 `key`，例如 `resident:<Resident ID>`。 |
| 条目种类（`kind`） | `resident-main`：用户直接对话过的 Resident；`resident`：从这些 Resident Fork 出、用户也发过消息的 Resident；`chatroom`：用户所在的聊天室（decision_a6bebf4ac283）；`lineage-notification`：发给用户的通知；`notice-board-mention`：Notice Board 帖子或回复中提到了用户。 |
| 条目类型 | “消息”栏同时展示对话和通知。对话是打开后进入 Chat、可以直接回复的条目：`resident-main`、`resident`、`chatroom`；通知是 `lineage-notification`、`notice-board-mention`。接口的 `section` 仍表示这两种类型，不再对应独立的界面栏。 |
| 完成签名（`completion_signature`） | Resident 条目最近一次值得提醒的结果的引用，例如 Resident 回复了用户的某条消息、某条消息执行失败或有消息发给用户。它记录发起这次请求的用户、输入消息 ID、结果 ID 和完成时间，`GetMailbox(user_id)` 由它给出 `latest_message_id` 和 `input_message_id`。它不参与已读判断。 |
| 阅读状态行 | 服务端为每位用户、每个条目保存的一行（Portal UI 的 `conversation_states`）：最新提醒的位置、读到的位置、置顶位置、清除时间和修订号。最新提醒位置大于读到的位置时条目未读；没有行的条目是已读、未置顶。 |
| 提醒 | 值得用户注意的新事件把这一行的提醒位置推进到新位置：给他的结果、发给他的消息、发给他的通知、Notice Board 中提到他。提醒同时记下事件的标识，同一事件再次送达不再提醒。 |
| 修订号（`read_revision`） | 一行每次变化（提醒、已读、未读、置顶、清除）都从同一个序列取得更大的修订号。条目、角标汇总、Resident 状态快照和指令结果都带着它；浏览器只接受比手中更新的状态（decision_18abbdef3da3），`GetMailbox(user_id)` 条目的 `version` 也是它。 |
| 清除时间 | 用户“清除全部”时，服务端为每个条目记下它当时的活动时间（`updated_at`）。活动时间不晚于清除时间的条目保持隐藏；出现新活动或新提醒后重新显示。分页读取 Resident 对话时，SocietyService 在读取身份和摘要之前就跳过这些隐藏的对话，每页用仍会显示的对话填满（TFI portal 有 502 个隐藏对话时，第一页原先只剩 13 条，后面各页为空）。 |

阅读状态行只属于这位用户，只影响他自己的 Mailbox。

### 消息与 Todo 两栏、对话置顶

- **面板只有“消息”和“Todo”两栏。** “消息”统一展示对话、单向通知和 Notice Board @ 提及，保留条目上的对话／通知类型标记及原有打开方式；打开对话进入 Chat，单向通知打开详情，@ 提及进入原帖。面板默认显示“消息”，上次选中的栏保存在当前设备上；旧的“对话”或“通知”选择归并为“消息”，已有 Todo 选择保持。
- **消息栏统一计数和清理。** “消息”按钮、消息栏标题和入口角标使用对话与通知合计的未读数；消息栏标题的条目数合计已加载的两类条目。“清除未读”“清除全部”同时作用于对话和通知的全部页，不影响 Todo；Todo 保持独立的未完成数。
- **对话可以置顶和取消置顶。** “消息”栏中对话卡片的右键菜单（触屏长按）提供“置顶”或“取消置顶”；通知不能置顶。置顶的对话固定在“消息”栏最上方并显示图钉标记，置顶组和其余条目各自按更新时间从新到旧排列；置顶条目不论活动时间都在第一页返回。置顶不改变未读；“清除全部”不移除置顶的对话。每位用户最多置顶 50 个对话。
- **置顶记录在服务端。** 置顶保存在这位用户的阅读状态行中，换设备、换浏览器后保持，远程 agent 读到的也一样。Resident 被删除、用户退出或失去访问权限而条目消失时，它的置顶随之删除。
- **置顶通过 ISA 指令完成。** `PinMailboxItem`（`key`、`pinned`），由 Portal Web 执行，页面和 Agent 调用同一条指令；`GetMailbox(user_id)` 和 HTTP 读取的条目增加 `section`（`conversation` 或 `notification`）和 `pinned`。
- **节点 Menu 的图钉固定到 User Mailbox。** Resident Menu 右上角使用「固定在 Mailbox／取消 Mailbox 固定」，与 Mailbox 中的置顶共用 `PinMailboxItem` 和服务端状态；普通 Resident、Family 门面与内部 Resident 都适用。它不再修改收藏导航。尚未交谈或曾清理的可见 Resident 也可以固定：服务端核验访问权限后，把该节点加入消息栏，不伪造消息、不标记已读，也不启动工作。取消固定后，没有对话活动的节点从 Mailbox 移除；已有对话仍按原规则展示。状态读取或修改失败不会伪装为成功；服务暂不可用也不删除已有固定。

### 个人 Todo

- **Todo 是可以修改的个人清单，不是对话消息。** 每条有文字、日期、完成状态、个人顺序、缩进层级（`indent`，0–4）和服务端生成的 `todo_id`，另记录真实创建者与创建/更新时间。文字去掉首尾空白后为 1–100 个 Unicode 字符，页面与服务端都校验；不能修改创建者或所属用户。缩进只表达视觉层次，不建立父子任务关系，也不联动完成状态或拖动其他条目。
- **新增、编辑、勾选与删除。** 用户在 Todo 栏输入文字并选择日期。点击条目文字就地编辑文字和日期，没有编辑图标；点击其他区域或编辑区失焦默认保存，Ctrl/Cmd+Enter 也可保存，Escape 取消本次编辑。编辑时 Tab 增加缩进，Shift+Tab 减少缩进。只有点击复选框才切换完成状态，勾选前先保存当前草稿；每项可单独删除。已完成项保留并显示删除线，完成状态变化不移动条目。日期为 `YYYY-MM-DD`，新增时默认香港时区当天，用于分组而非定时提醒；这些操作不启动 Resident 工作。
- **分组与排序。** 日期从新到旧，每天可独立折叠；同一天按用户保存的个人顺序显示，新项追加在当天末尾。拖动行前手柄可将该项放到另一项之前或之后，跨日期组拖动会同步修改日期；界面不提供上移、下移按钮，键盘聚焦手柄后可用 Alt+↑/↓ 在同日调序。一次拖动通过单次事务定位，不依靠连续多次上移或下移。顺序、缩进和完成状态保存在服务端，换设备后保持；展开期间刷新保持日期的折叠状态，上次选中的栏按设备记住。
- **计数和清理独立。** Todo 按钮及该栏标题显示未完成待办数，不表示消息未读；Mailbox 入口角标仍只统计对话与通知。打开、勾选待办不修改消息已读。“清除未读”“清除全部”、对话置顶、`GetMailbox(user_id)` 和 `WatchMailbox` 都只作用于对话与通知，不读取或删除 Todo。
- **人与 Agent 使用相同指令。** User 可以读取和管理自己的待办，不能操作其他 User 的清单。`CreateMailboxTodo(text, user_id, date?)` 的 `user_id` 对 User 和 Resident 都必填：User 写自己的用户名，Resident 写已登记且启用的接收 User（TFI portal 2026-10-18）；真实创建者从认证身份取得。该能力不授予 Resident 读取、编辑、调序或删除用户已有待办的权限。
- **保存和刷新。** `mailbox_todos` 模块负责独立的逐条数据库记录、所有权和校验，Portal Web ISA 与页面共用它；写入按用户加事务锁，防止并发添加或调序覆盖。写入沿用 ISA 幂等键，重放返回已有条目引用，不重复新增。Mailbox 首次展开或重新展开即读取 Todo，无须先选择 Todo 栏；在消息栏也显示当前未完成数。切换到 Todo、窗口恢复焦点、页面恢复可见、手动刷新和操作成功后重读；只要 Mailbox 展开且页面可见，每 15 秒检查一次，不以当前选中的栏目为条件。切换离开 Todo 仍自动保存正在编辑的内容。编辑或拖动期间推迟非必要读取，保护草稿和正在拖动的节点；旧读取不能覆盖操作后的结果，身份变化时清空本地数据。读取/保存失败明确报错，保存失败保留草稿，完成状态写入失败还原复选框。

### 人和远程 agent 用同一套能力

- **页面能做的事，Agent 都能用 ISA 指令做。** 远程 agent 以 User 身份（User Access Token）调用 `GetMailbox(user_id)`、`MarkMailboxItemRead`、`ClearMailbox`、`PinMailboxItem`，并用 `ListMessages`、`GetMessage`、`DeliverMessage`、`AnswerResidentRequest` 和聊天室指令阅读、回复。User Mailbox 没有队列，Agent 自己决定处理哪些条目、以什么顺序处理。
- **页面实时收到的变化，Agent 也能等到。** 页面通过 Portal 变化通知（`agent-conversation-index`、`resident_events`）实时刷新；Agent 用一条流式指令 `WatchMailbox` 等待同样的变化，返回变化的条目键和续读游标，不用反复轮询 `GetMailbox`。

WatchMailbox 的首批检查点要求读取一次当前 Mailbox；之后把返回的 `cursor` 作为 `after_cursor` 续读。订阅复用页面经权限过滤的变化数据，不轮询列表；明确的 Resident 或条目变化返回 `keys`，无法定位到条目键的整表变化返回 `snapshot_required`。游标所在 Web 进程重启、游标超过当前序号或事件已退出缓存窗口时，同样要求重读。每次订阅默认持续 30 秒，可选择 1–60 秒；超时仍返回续读检查点。聊天室免打扰规则继续决定入口角标是否提醒，清除未读仍可作用于免打扰的未读条目。

### 页面上看到什么

置顶与普通条目使用相同的紧凑行布局。图钉作为标题旁的小图标，不占用右侧状态区的新行；长标题单行省略，图钉保持可见。右侧把条目类型、免打扰和接单标记放在同一行，运行状态、未读点或计数及提及提示放在下一行并排显示，置顶或取消置顶不改变条目高度。置顶菜单使用紧凑的图标与文字操作项，字号、留白和阴影与列表相协调；仍支持右键、触屏长按、键盘菜单入口及 Escape 关闭并恢复焦点。

- **入口按钮**：固定在页面右边缘的 44 像素按钮，悬停提示“我的 Mailbox · 按住可上下拖动”，角标显示未读数（超过 99 显示“99+”）。按住可以沿边缘上下拖动，位置按比例保存在当前设备上；窗口尺寸改变后重新限制在可见范围内。
- **个人助手章鱼按钮**：入口按钮下方紧贴着个人助手的章鱼按钮，两者拼成一个不留缝隙的悬浮标签，共用尺寸、外框、拖动位置和整组避让，靠底色区分，两者一起上下拖动；两个面板互斥。鼠标停在章鱼上展开个人助手的门面对话，触屏点按切换；用户没有个人助手入口时不显示（decision_bd5217d08c09）。
- **面板**：从入口按钮旁展开的浮层，与按钮相隔 8 像素，按上下剩余空间选择向上或向下展开。标题栏显示“Mailbox”、未读数、条目数（刷新期间前面加“正在更新 ·”，分页时写“已加载 N 条”），以及“清除未读”“清除全部”和刷新三个按钮。标题栏下方只有“消息”和“Todo”两个栏按钮；消息显示对话与通知合计的未读数，Todo 显示未完成待办数。Todo 栏的标题计数显示未完成数与总条数，隐藏“清除未读”“清除全部”。 两栏共用相同的面板高度（最高 640 像素，并受视口剩余空间约束），Todo 不因条目少或日期组折叠而缩短；内容在面板内滚动。
- **卡片**：每个条目一张，显示图标、标题、摘要、来源、相对时间、状态标记和未读提示。图标按条目种类区分：通知使用提醒图标，Resident 对话使用对话图标；失败和中断的卡片使用告警图标和告警配色。
- **加载更早的 Mailbox 条目**：条目超过一页（50 条）时，列表底部出现这个按钮，点击后读取下一页。分页只限制已显示的条目；入口和分类角标统计全部页需要提醒的未读，提示为“Mailbox 未读数”。标题的“已加载”数量仍只统计实际加载的条目。第一页带回全部通知、公告板 @ 和 Chatroom，Resident 活动才分页；还有下一页时，列表只显示到已加载的最旧一个未置顶 Resident 为止，比它更旧的通知等对应的 Resident 页加载后再按时间出现。这样点击加载不会把 Resident 插到已经显示的通知上方，任意位置的顺序都和 `GetMailbox(user_id)` 一致。
- **空状态**：首次读取时显示“正在读取 Mailbox…”，服务端索引尚未建好时显示“正在建立 Mailbox 索引…”，读取失败时显示错误信息，确认没有条目时显示“Mailbox 中暂无对话”或“Mailbox 中暂无通知”。读取失败不会显示成空列表。

### 读取失败与恢复

Mailbox 列表或状态刷新失败时，保留已加载的条目，在栏按钮下方的独立提示区说明刷新失败，并引导用户点击右上角刷新。首次加载失败时说明暂时无法加载，不显示为空列表；成功读取后撤掉提示。列表的提示不放入某张 Resident 卡片，也不改变该 Resident 的运行状态。提示、可滚动内容和分页按钮各有独立空间，两栏的面板高度一致；切到 Todo 后隐藏收件列表的错误，Todo 继续显示自己的读取和保存结果。提示提供中文与英文文案。

### 展开与收起

Mailbox 收起期间共享缓存仍接收列表事件并保留已加载 Resident 的状态订阅，角标与分类未读从同一缓存计算。打开面板只追加一次校准，不能承担恢复正常事件同步的职责；初始快照、事件定向更新、并发保护和异常恢复由 decision_18abbdef3da3 定义。

- **鼠标悬停时展开，离开入口和面板后收起。** 点击入口或面板内的控件不会把面板固定住，鼠标点击留下的焦点也不阻止收起。
- **键盘焦点在入口或面板内时保持展开。** Tab 移到外部后收起；按 Escape 收起面板并释放面板内的焦点。
- **触屏轻点入口展开，再点入口或点面板外部收起。** 操作面板内的控件时保持展开。
- **拖动入口不会打开面板。** 拖动结束后面板收起，拖动产生的点击被忽略。入口的无障碍展开状态随面板的实际显示更新。

### 卡片的状态与时间

- **相对时间每 60 秒更新一次。** 不到 45 秒显示“刚刚”，之后依次显示分钟（`m`）、小时（`h`）、7 天内的天数（`d`），更早的显示中文月日。
- **状态标记显示 Resident 当前在做什么，以及最后一次结果。** 运行中显示“运行中”，排队显示“排队”；最后一次输入失败显示“失败”，被取消、停止或中断显示“已中断”，归档显示“归档”。后端的 `canceled`、`cancelled`、`stopped`、`interrupted` 都归为“已中断”，`failed`、`error` 都归为“失败”。Resident 的运行状态取自共享的 Resident 状态层，还没有取得时不显示运行中或排队（decision_18abbdef3da3）。
- **Resident 关闭接单时，卡片另显示一个 `closed` 标记。** 悬停提示为“消息接收已关闭，仍可打开对话查看历史”。这个标记与运行状态、失败或中断标记、未读点同时显示；卡片仍可以点击。重新打开接单后标记随状态同步消失。接单开关的变化不产生新的未读，不改变排序时间，也不让已清除的条目重新出现。

### 打开卡片

- **单击卡片打开对应页面。** 鼠标单击等待 240 毫秒再打开，以便区分双击；键盘触发的点击立即打开。同一张卡片正在打开时，再次点击不会重复发起。
- **打开 Resident 卡片前先确认它还在。** 列表通过 Resident 发现服务确认目标仍然存在、当前用户仍有权访问。目标已被删除（404）时移除这张卡片并提示“Resident 已删除。”；无权访问（403）时同样移除并提示“您不是该 Lineage 或 Resident 的 owner。”；其他失败提示“无法确认 Resident 状态，请稍后重试。”，卡片保留。
- **当前页面可以接管打开动作。** 确认通过后，列表发出浏览器事件 `portal:agent-conversation-open`，事件的 `detail` 中带有条目和一个 `handled` 标志。Society 页面这类能直接切换到目标 Resident 的页面把 `handled` 设为 `true` 并自行切换；没有页面接管时，浏览器前往条目的地址。
- **打开 Resident 卡片本身不改变已读。** Resident 卡片的未读在用户真正看到对话时才清除，规则见下一节。
- **打开通知就是读了它。** 通知没有可以确认阅读的 Chat，所以通知条目打开独立详情对话框，显示完整通知正文与来源，成功打开后、跳转之前调用 `MarkMailboxItemRead` 把这个条目标为已读；页面跳转后请求仍然完成，标记失败不阻止打开。详情底部的来源与关闭操作采用共享 `portalButton` 样式。

### 已读与未读

- **在 Chat 中看到 Resident 的对话后，条目自动变为已读。** 用户打开这个 Resident 的 Chat、对话内容已经显示出来时，浏览器以这个条目的 `key`（`resident:<Resident ID>`）调用 `MarkMailboxItemRead`，不带 `expected_version`；服务端先核对调用者能看到这个 Resident，再把这位用户这一行的读到位置推进到最新提醒，不查询历史结果。与滚动位置、最后一条消息是否进入视口无关；只是选中节点、在地址中带上 Resident ID 或从详情页返回都不算看到（decision_18abbdef3da3）。
- **双击卡片切换已读和未读。** 双击取消待打开的单击，任何条目都可以切换。浏览器调用 `MarkMailboxItemRead`，以页面看到的修订号作为 `expected_version`：这之后有新提醒或其他变化时服务端拒绝修改，页面重新读取 Mailbox，新结果保持未读。标为已读把读到位置推进到最新提醒；标为未读把读到位置移到最新提醒之前，从未有过提醒的条目先取得一个提醒位置。Resident 条目先通过 Web 已绑定的 Resident 服务检查当前调用者的访问权限，不扫描消息列表。页面可以先更新红点；保存失败时恢复原状态，并明确提示“未读状态保存失败，请重试。”，不能静默吞掉错误。成功保存后，状态刷新与页面重载保持已读，直到真正的新结果或新消息到达。
- **提醒在事件发生时写入。** Resident 的可提醒结果或发给用户的消息与对话事件在同一事务中推进收件人的提醒，用户离线时照常更新；清空上下文把所有查看者读到最新提醒。Lineage 通知创建时为每位接收者写入提醒；Notice Board 发帖、编辑新增和回复中的 @ 在提及时为被提及者写入提醒。查询只读这位用户的行，不比较历史结果，不遍历活动分页。Family 成员的转交结果推进原始发送者在门面对话上的提醒，转交副本不产生提醒。
- **在 Home 读完帖子也就读了其中的 @。** 用户在 Home 把一个帖子读到最新版本时，Mailbox 中这个帖子的 @ 提及一并变为已读；只读到较早版本时保持未读。
- **聊天室条目的未读就是聊天室自己的未读。** 在聊天室中阅读按展示位置推进已读；双击标为已读、“清除未读”把这位成员的已读位置移到最新消息；双击标为未读让最新一条消息重新未读。别人发言后条目变为未读，自己发言不会。卡片直接显示共享 Conversation Store 中的真实 `unread_count`，超过 99 显示 `99+`，悬停提示和无障碍标签保留准确条数；已读时不显示数字。新消息、重连和阅读回执沿用共享状态层更新计数，列表不自行累计。只有未读结果标记、没有消息条数的 Resident 或通知条目仍显示未读点，不把一个结果标记伪装成一条消息。入口红色总角标只计算需要提醒的未读条目数；聊天室使用服务端的 `attention_unread`。免打扰房间的普通未读数量为灰色并显示静音图标，有未读 @ 时另显示红色“@我 n”，点击进入房间定位消息。灰色未读仍可使用清除未读操作。
2026-10-16 起阅读状态行取代按完成签名比较的已读标记。切换时新表直接带入原有 Resident 未读与置顶，一次性迁移按原规则带入仍然隐藏的“清除全部”、原先未读的通知和 Chatroom 成员状态。验收通过后，旧表 `conversation_receipts`、`conversation_viewers`、`chatrooms.viewers` 和迁移脚本一起删除，没有保留回退路径。

每个进程首次使用阅读状态表时都会运行建表语句。`CREATE INDEX IF NOT EXISTS` 和 `ADD COLUMN IF NOT EXISTS` 即使无事可做也要锁表：锁要等写过这张表的事务结束，后到的读写又排在它后面。所以建表语句先查索引是否已经存在，新列直接写进 `CREATE TABLE`，表已存在时整段语句不锁表。

`MarkMailboxItemRead` 先确认条目在调用者的 Mailbox 中，不拼出 Mailbox 页：Resident 条目由 Resident 服务确认身份，自己记阅读位置的功能条目由该功能确认（Chatroom 检查调用者是否是成员）。通知和 @ 创建时已写入收件人的行，有行就算在 Mailbox 中；只有更早、还没有行的通知才到 Mailbox 页中查找。

### 清除

- **“清除未读”把消息栏的全部未读条目标为已读，条目保留在 Mailbox 中。** 完成后提示“已清除 N 条未读状态”。
- **“清除全部”从消息栏移除未置顶的条目。** 完成后提示“已清除 N 条消息”。服务端把这些条目标为已读并记下各自当时的活动时间；条目出现更晚的活动或新提醒后重新显示。接单开关、账号变化等不改变活动时间的变化不会让它重新出现。
- **清除作用于消息栏中的对话与通知，包括还没加载的页；Todo 不受影响。** 服务端按清除那一刻的条目逐一处理；清除请求发出后才出现的新结果和新活动不受影响。
- **Mailbox 以服务端的结果为准。** 清除失败时列表保持原样，并提示错误。

### 服务端怎样决定条目

服务端从登录会话取得当前用户，只列出与他有关、他有权看到的条目：

- **Resident 对话**：用户在这个 Resident 的对话中发过消息，或对话中有发给他的消息（`recipients` 中有他），才会出现它的条目。Resident 目录的更新、后台任务的执行，以及用户没有参与的继承活动，都不会让条目出现。用户对话过的 Resident 是 `resident-main` 条目；它 Fork 出的 Resident 中，用户也发过消息的，各自成为 `resident` 条目，已经作为顶层条目出现的不再重复。条目地址指向该 Resident 所在 Society 页面。Family 发给用户的消息在门面的对话中，条目是门面；Router 转交给成员的副本不让成员出现在发送者的列表里（decision_44cf587c1721）。
- **通知**：`CreateNotification` 发出的通知，只出现在通知的接收者名单中的用户的列表里。通知属于某个 Lineage 时，地址指向这个 Society 页面。
- **Notice Board 提及**：帖子或回复中提到当前用户时，出现 `notice-board-mention` 条目，每篇帖子一条，地址打开 Home 中的这篇帖子（decision_605810a1292b）。这些条目都放在第一页。
- **聊天室**：用户是成员、已经有消息的聊天室各一条 `chatroom` 条目，键为 `chatroom:<Chatroom ID>`，标题是聊天室名称（没有名称时是成员名单），副标题是“作者：最后一条消息”，更新时间和完成签名都取最后一条消息，地址 `/lineage?chatroom=<Chatroom ID>`。这些条目也放在第一页。

Notice Board 提及和聊天室由各自的功能经功能登记的 `mailbox.source` 接入点提供（decision_15aab18c0ab3），Mailbox 不直接调用这两个功能；打包时没有带上的功能不提供条目，其余条目照常。

同一个 `key` 只保留一条（游标更大、更新时间更近的那条），列表按更新时间从新到旧排列。完成时间比条目更新时间新时，条目的更新时间取完成时间。

Resident 条目的排序时间始终包含当前用户最近发送消息的时间（`actor_last_user_message_at`），再与属于他的最新结果或发给他的消息时间取较新值。即使后面有其他用户或 Resident 发言，也保留当前用户这次交互的时间；尚未得到回复的新请求同样参与排序。顶层 Resident 与 Family 中的 Resident 使用同一口径。目录更新时间、其他人的工作时间不会把该条目排到前面。

用户能否看到一个条目、打开后能否继续访问，都由服务端按当前用户的权限判断；索引批量读取可见 Resident 的身份和当前 Session 账号，不装配 Workspace、Watchboard 或重复查询同一批身份的权限投影。服务端构建索引时使用的内部读取能力只用来组装摘要，不扩大用户可见的范围。

未读汇总先从当前用户的持久未读记录选出候选 Resident，再通过 SocietyService 的批量可见性接口 `filter_visible_resident_ids` 过滤。Portal Web 使用服务客户端调用这个契约；可见性检查及其内部端口由 SocietyService 负责初始化和执行，Web 不直接运行该服务的内部领域函数。这个批量查询不读取对话历史或逐条装配运行状态。

### 哪些结果值得提醒

服务端在用户自己发出的消息得到结果，或有消息发给用户时为他写入提醒，以结果的 `completion_id` 识别同一事件；条目的完成签名取每个 Resident 最新的一项：

- **发给用户的消息**：对话中 `recipients` 包含这位用户、仍在 Chat 中（在最后一次清空之后）的消息本身就值得提醒，签名取最新的一条，`completion_id` 是这条消息的 ID，`input_message_id` 为空。它和这位用户自己请求的结果比较时间，较新的一项是条目的完成签名；打开 Chat 时的 `MarkMailboxItemRead` 照常把它标为已读。服务端用 AgentData `segments` 上只覆盖带收件人消息的部分索引 `segments_addressed_message_idx` 查找发给用户的消息，存储打开时自动补建（decision_b1ab45c4b3ec）。只有收件人得到提醒，能看这段对话的其他人不会。
- **成功的输入**必须有 Resident 的有效回复：输入状态中的 `reply_message_ids` 指向同一 Resident 对话中的 `agent` 消息，回复有内容，并且是对这条输入的回复。签名取最后一条有效回复。转交给 Family 成员的请求由成员答完时，发送者的门面条目显示未读。
- **失败和取消的输入**本身就值得提醒；没有回复时，用输入 ID 和终态生成签名。
- **只确认送达的输入不提醒。** 注入当前正在进行的一轮的消息、`/fast` 这类同步命令的系统回执，以及没有有效回复的成功输入，都不产生新的完成签名，也不覆盖更早的真实结果或清除已有的未读。

完成签名绑定发起请求的用户，其他用户不能把这次结果标为自己的已读。单个 Resident 的读取和整页索引使用同一套规则，判断时只读取候选回复的消息元数据，不读取完整的对话正文。候选输入按从新到旧检查，先读取每个 Resident 最新一条；遇到连续的控制回执时，检查窗口逐轮倍增，每个 Resident 每轮最多读取 64 条。找到最新有效结果后立即停止该 Resident 的历史检查，避免为每条历史回执单独往返数据库，也不截断较早的有效结果。

### 分页与更新

列表请求在 AgentData 的 SQL 中按最近活动时间和 Resident ID 排序，并用页游标取有界候选批次；Mailbox 中等待的输入由数据库按 Resident 汇总最近时间后合并。两边同时有活动的 Resident 取较新的时间，只在列表中出现一次。按批检查当前身份与可见性，跳过已删除或无权访问的对象后继续取下一批，不把全部历史活动装入应用内存。只为本页实际返回的 Resident 读取摘要、队列和未读结果。候选批次超出本页大小时，续页游标指向实际返回的最后一项，保证下一页不漏掉溢出的可见对象。新 HTTP 请求按游标重新读取当前活动；索引不跨请求缓存，可见性检查保持有效。

最近活动时间由 AgentData 为每段对话、每个人各保存一行（`chat_actor_activity`）：这个人在对话中发出的输入（Family 转交副本除外）或发给他的消息写入时，在同一事务里把该行推进到较新的时间；清空上下文和改写历史后按最后一次清空之后的消息重算。列表按用户读取这些行排序，不在每次请求时把这个人的全部消息分组。每段对话另记一个版本标记，表示保存的活动对应对话的哪个版本。发布切换期间还在运行旧代码的写入者追加消息时只推进对话版本，这段对话的最近活动改从消息行计算，新代码下一次写入时重算并更新标记。

一段 Resident 对话对这个人的最近活动，取以下时间中最晚的一个：他发出的消息、发给他的消息、他还在等待处理的输入，以及他的请求的结局（回复或失败）。请求在发出几小时后才完成时，对话按完成时间排到前面。结局时间在提醒写入时一起记下（Portal UI 的 `conversation_states.alerted_at`），服务端分页时与消息活动一起取最晚值；只有结局、这个人没有参与的对话不进入列表。条目显示的 `updated_at` 就是分页用的这个时间，保留到微秒。浏览器列表、`GetMailbox(user_id)` 和分页游标因此按同一个时间排序，后一页的对话不会比已经显示的更新。

首次列表响应补齐置顶条目时，复用本次请求已经鉴权并装配的页内 Resident 条目，只额外读取页外置顶对象。复用不跨请求、不改变条目排序和页游标；页外对象仍按当前用户权限读取，源读取失败时保留原有置顶记录，不把失败当作对象消失。

Portal 的变化通知中，`resident_events` 列出发生变化的 Resident，浏览器只重新读取这些 Resident 的条目；通知无法定位到具体 Resident，或声明整个 `agent-conversation-index` 失效时，才重新读取整个索引。只影响聊天内部的变化，例如当前活动详情、等待队列和运行设置的变化，不会触发列表重新读取。聊天室有新消息、成员变化或某位成员读过时，服务端向这些成员发出 `agent-conversation-index` 变化通知。

### 读取接口与条目字段

浏览器用 HTTP 读取索引：

| 接口 | 结果 |
| --- | --- |
| `GET /api/agent-conversations` | 第一页条目（默认 50 条，`page_size` 可取 1–100），以及下一页的 `next_page_token`。 |
| `GET /api/agent-conversations?page_token=<令牌>` | 下一页条目。 |
| `GET /api/agent-conversations?resident=<Resident ID>` | 这个 Resident 的条目，以及 `target_state`（`present` 存在、`deleted` 已删除、`forbidden` 无权访问）和它当前的游标，供浏览器追赶单个 Resident 的变化。 |

Portal Web 按用户和查询把读取结果缓存 2 秒，并合并同时到达的读取。事件校准遇到过期或作废缓存时等待新结果，不把旧列表当作校准成功；定向 Resident 查询直接读取当前投影，不等待缓存过期；当前用户标记已读、未读（包括打开 Chat 时的标记）或清除后，服务立即丢弃这位用户的缓存副本和正在进行的重建，这样浏览器紧接着的重读一定包含这次修改，不会让未读标记回弹。其他用户的缓存不受影响。

结果的格式为 `tfi.portal.agent-conversation-index.v1`，首批响应同时包含 `mailbox_summary`，其轻量未读成员覆盖全部页；Resident 未读成员从当前用户保存的布尔记录中选择，并过滤已删除或无权访问的对象，不读取历史分页或完成摘要。列表、viewer_state 与摘要读取同一份未读记录。响应包含 `entries`、`unread_count`、按条目类型给出的 `sections`（条目数与未读数）和 `content_signature`（整个列表内容的摘要，Resident 接单状态变化也会改变它，浏览器据此判断是否需要重绘）。Agent 使用 HATG ISA 指令 `GetMailbox`（传 `user_id`）读取同一份 Mailbox，结果包含全部页的条目和未读数。第一页带上全部通知、公告板 @ 和 Chatroom，其中较早的条目可能比第二页的 Resident 更旧，所以 `GetMailbox` 把各页合成一个列表重新排序：置顶在前，其余按最近活动从新到旧，同一时间按 key 升序。这样结果不随页大小变化。

| 字段 | 含义 |
| --- | --- |
| `key` | 当前用户范围内唯一的条目键。 |
| `title` / `subtitle` | 卡片标题和摘要，最多 240 和 320 个字符。 |
| `surface` / `surface_label` | 条目来自哪个页面，例如 `lineage` / “Lineage”、`home` / “Notice Board”。 |
| `kind` | 条目种类，决定图标、所在的栏，以及打开前是否需要确认 Resident 仍然存在。 |
| `url` | 在 Portal 中打开的地址。 |
| `status` | `idle`、`queued`、`running`、`failed`、`interrupted` 或 `archived`。浏览器用 Resident 状态层的当前状态替换其中的运行部分。 |
| `intake_state` | Resident 的接单开关，`open` 或 `closed`，与 `status` 相互独立；缺失时不当作关闭。 |
| `updated_at` | 排序用的时间。 |
| `resident_id` / `account_id` / `cursor` | Resident 条目的目标 Resident、它使用的执行账号，以及服务端对这个 Resident 对话处理到的位置（只增不减）。 |
| `completion_signature` | Resident 与聊天室条目最近结果的引用，见“基本概念”。 |
| `unread` | 当前用户的阅读状态行是否未读；聊天室条目在并入阅读状态行之前取聊天室自己的未读。 |
| `read_revision` / `dismissed_through` | 当前用户这一行的修订号和清除时间，没有行时为 0 和空。 |
| `section` | `conversation` 或 `notification`，由服务端按条目种类决定。 |
| `pinned` | 当前用户是否置顶此对话；通知始终为 `false`。 |

完成签名的格式为 `actor-completion-v7:` 加一个 JSON 对象，其中 `schema_version` 为 1，并带有 `request_actor`、`completion_id`、`input_message_id` 和 `completed_at`；缺少 `completion_id` 的签名视为无效。列表响应另带 `read_revision`：这次快照读到的这位用户最新一行的修订号。角标汇总中缺席、而浏览器手中修订号更新的条目保留到更新的汇总到来。

### Mailbox 面板的挂载与接入

面板的前端模块、HTTP 接口和设备偏好键使用内部名称 `PortalAgentInbox`、`/api/agent-conversations` 和 `agent-inbox`；界面文字和 ISA 指令使用 Mailbox。

`PortalAgentInbox.mount()` 在页面加载完成后创建入口，只在顶层窗口中、共享的会话状态层（`PortalConversationStore`）存在并且尚未挂载时生效。公开方法如下：

| 方法 | 作用 |
| --- | --- |
| `mount()` | 创建入口和面板，订阅会话状态层，连接页面事件。 |
| `destroy()` | 解除订阅、页面监听、定时器和待执行的单击，之后可以重新挂载。 |
| `refresh(options)` | 请会话状态层重新读取。 |
| `markRead(key)` | 请会话状态层把一个条目标为已读。 |
| `entries()` | 返回当前条目。 |
| `verticalPosition()` | 返回入口按钮当前的纵向位置比例。 |

入口角标使用消息栏中对话与通知的未读之和，标题栏在消息栏显示未读和条目数，在 Todo 栏显示未完成数和总条数；入口位置与上次选择的栏保存在设备级偏好 `agent-inbox` 中。条目的地址和来源显示由 `PortalNav.resolveAgentSessionNavigation` 统一整理。列表只负责界面，缓存、游标、回执和清除的状态都由会话状态层管理。

新增一种条目来源时，所属功能在功能登记中提供 `mailbox.source` 接入点：`management_portal/mailbox_sources.py` 中的 `MailboxSource`，包括条目键的前缀、某个用户的条目，以及条目是否属于对话栏。功能自己记录成员阅读位置时（例如 Chatroom），还要提供标记已读或未读、确认条目在某个用户的 Mailbox 中这两个操作，它的条目自带 `unread`；其余条目的阅读状态由 Mailbox 的阅读状态行记录。服务端需要为它规定：哪些用户能看到、属于哪一栏、哪些事件为谁写入提醒、打开后前往哪里。新的状态只需要在列表中补上图标和文字。长正文、讨论、审批和搜索仍由各自的页面承载。

## 平台事件的收件

平台自己检测到的问题按“能不能找到一个可以对话的 Resident”决定怎样告诉用户：

- **事件与某个 Resident 直接相关、但它此刻无法工作时，发通知。** steward 缺少可用后端（`family_quota`）、steward 需要恢复（`resident_work_recovery`）都需要 owner 去修改设置，没有能马上答话的对象，继续用 `CreateNotification` 发给 Lineage 的 owner。
- **Resident 自己需要 owner 决定或亲自处理的事，用 `DeliverMessage(user_id)`。** 消息对应的对话出现在“消息”栏，owner 打开后可以直接回复它。只告知结果、不需要回复的才用 `CreateNotification`。Family steward 也照此办理：需要 owner 回复、讨论或决定的事以门面身份发进对话，owner 引用这条消息回复时，回复直接交给 steward，不经 Router 转给业务成员（decision_44cf587c1721）。
- **Effect 失败、RunScript 失败与 Lineage 中自动触发（定时、文件变化）的 Resident 工作失败，先交给失败处理者，没有时由 Leader 署名。** 手动或自动触发的 Effect 与 RunScript 失败都按 Effect 自己的收件人清单投递（`lineage_failure_notifications`）。
  - 失败处理者是监听这次失败、并有连线把事件交出去的事件源：Resident 失败对应 Watch 了它（或按 `resident_scope` 覆盖它）的 `resident.work_failed`；Effect 失败对应 Watch 了这个 Effect 的 `effect.failed`；脚本失败对应同一 Effect 的 `effect.failed`，或 `outcome` 为 `failed`、`any` 的 `script.finished`。有处理者时平台不再另发消息，由处理者自己检查原因，再用 `DeliverMessage(user_id)` 告诉 owner。
  - 没有失败处理者时，平台以 Lineage Leader 的名义把失败写成一条消息（`outward_messages.post_platform_notice`）：消息写在 Leader 的对话中（Leader 在 Family 内时写在门面对话中，以门面署名），`role` 为 `agent`，`origin.initiated_by` 是 Portal 服务，`recipients` 对 Effect 和 RunScript 取该 Effect 的 `failure_recipients`（最多 20 位 User），只通知这份清单；Resident 自动运行失败仍取 Lineage 管理员；不启动 Leader 的工作。消息 ID 由 Lineage、失败主体和日期推导，同一主体同一天只写一条，之后的同类失败不改动已有消息。Effect 的消息 ID 还包含收件人清单的摘要，修改清单后新失败会按新清单生成消息。owner 引用这条消息回复时，回复进入 Leader 的 Mailbox，Leader 在引用块中看到失败内容，再去查原因或重跑。
  - 有 Resident 的 Lineage 一定有 Leader（decision_6fc653d55c79）。只有两种情况仍发通知：没有 Resident 的 Lineage（例如只有定时 Effect 的 Lineage）没有 Leader；看不到 Leader 对话的指定收件人改收通知。
- **Effect 调用结果未知、重试也无法确认时，按同一渠道请人处置。** 平台停止重试后（decision_4adcc1ff2f2c），以 Leader 署名写一条“Effect 结果未知，等待处置”，说明 Effect、Trigger Path、激活键和执行方回执，请收件人核对外部结果后标记成功或失败。处置是必须有人做的事：收件人取 Effect 的失败收件人，清单为空或未配置时改为 Lineage 管理员；这次没有失败 Event，失败处理者收不到，平台总是发送。消息 ID 只由 Lineage 和激活键推导，每次激活只写一条，隔天重新询问也写回同一条；没有 Leader 或看不到 Leader 对话的收件人改收 Lineage 通知（ID `effect-resolution:<激活键>`）。

## 指令

Resident Mailbox 和 User Mailbox 的修改都使用 HATG ISA 指令（decision_2fe6fd5367fb、decision_2a3ff353165c），页面和 Agent 调用同一组指令。调用者身份来自认证信息，请求正文中的 actor 字段不被当作身份；未登录时拒绝。

### Resident Mailbox 整理

| 指令 | 参数 | 结果 |
| --- | --- | --- |
| `MergeMailboxItems` | `resident_id`、`message_ids`（选中的顶层项 ID） | `group_message_id`、按处理顺序的 `member_message_ids`、调整后的队列 `items` 与 `revision` |
| `SplitMailboxItem` | `resident_id`、`message_id`（群体消息 ID） | `group_message_id`、`member_message_ids`、`items`、`revision` |
| `ReorderMailbox` | `resident_id`，加上 `message_id` 与 `before_message_id`/`after_message_id` 之一，或完整排列 `order` | `items`、`revision`、`reordered`（顺序是否有变化） |
| `RequeueMessage` | `resident_id`、`message_id`（最近几轮中停止或失败的一轮，或接收记录完整但丢失执行位置的 `queued` 请求/群体） | 放回的 `message_ids`（请求在前，随行 info 按原顺序）、`items`、`revision` |

Mailbox 栏用只读指令 `GetMailbox`（传 `resident_id`）读取三部分内容。撤回用 `CancelInput`（`resident_id`、`message_id`）：正在运行的一轮被停止；等待中的请求、群体和 info 移出 Mailbox 并记为 `cancelled`，info 撤回后以 `cancelled`（`info_withdrawn`）结束，没有任何工作在处理的输入以 `cancelled`（`lost_input`）结束；已结束的消息保留原结果，不报错，结果带 `already_terminal: true` 和它的 `input_state`。对 Family 门面消息，它作用于 Router 转给成员的那条消息；尚未转出时从门面的 Mailbox 撤回。字段与失败情况见 decision_2a3ff353165c。

- **参数校验严格。** 消息 ID 不能为空或重复；`order` 必须恰好包含当前全部顶层项；参照消息不能是要移动的消息本身。
- **不带版本条件。** 与 SocietyService 的其他指令一样，整理作用于队列的当前状态（decision_d918ebebc062）；参数中的消息已不在等待队列中或已被领取时，这次操作失败，调用者读取最新状态后再决定。
- **整理在 Resident 状态锁内完成队列改写。** 合并、拆分和调序都在同一把 Resident 锁中重新读取队列并检查成员状态，“执行领取”和“整理”同一时刻只有一个能成功。群体关系和群体消息写入 Mailbox；重试按 ISA 的幂等键规则返回原结果（decision_898b8911a2ff）。

### User Mailbox

| 指令 | 作用 | 执行位置 |
| --- | --- | --- |
| `GetMailbox`（传 `user_id`） | 读取该 User 的 Mailbox（消息栏全部页，含对话与通知），结果为 `items` 与 `unread_count`；`user_id` 为调用者本人时读自己的 Mailbox；可用 `related_resident_id` 只读与某个 Resident 相关的条目。 | Portal Web |
| `MarkMailboxItemRead` | 按 `key` 把一个条目标为已读，或用 `unread: true` 标为未读；可以把条目的 `version`（修订号）作为 `expected_version`，之后有新提醒或其他变化时拒绝修改。 | Portal Web |
| `ClearMailbox` | 传 `user_id`（调用者自己或有 admin 的 User）清理该 User 的 Mailbox；`mode` 为 `unread` 时清除未读状态，为 `all` 时清除未置顶的条目；`section` 可按对话或通知类型筛选，省略时作用于消息栏的全部对话与通知，不影响 Todo；页面清理不传 `section`；`cleared` 是实际处理的条目数。 | Portal Web |
| `PinMailboxItem` | 按 `key` 与 `pinned` 置顶或取消置顶，独立于已读；通知拒绝置顶。 | Portal Web |
| `WatchMailbox` | 以 `after_cursor` 续读页面同源的变化，流式返回 `keys`、`cursor` 与 `snapshot_required`；`timeout_seconds` 控制本次订阅时长。 | Portal Web |
| `ListMailboxTodos` | 读取调用者自己的个人待办及未完成数。 | Portal Web |
| `CreateMailboxTodo` | 添加 1–100 字待办，User 给自己添加，Resident 指定接收 User。 | Portal Web |
| `UpdateMailboxTodo` | 按 `todo_id` 修改自己的文字、日期、完成状态或缩进。 | Portal Web |
| `DeleteMailboxTodo` | 删除自己的一条待办。 | Portal Web |
| `MoveMailboxTodo` | 按另一待办的前/后位置移动（可跨日期），或用 `direction=up/down` 同日调序。 | Portal Web |
| `CreateNotification` | 向用户发送单向通知，写入接收者 Mailbox 的“消息”栏，以通知条目展示。 | SocietyService |

`GetMailbox` 的 `resident_id` 与 `user_id` 恰好传一个：`GetMailbox(user_id)` 读该 User 的 Mailbox，由 Portal Web 执行；`GetMailbox(resident_id)` 读指定 Resident 的 Mailbox（待处理、处理中与最近已送达），由 SocietyService 执行，两者范围不同。原 `ListMailbox` 已并入 `GetMailbox(user_id)`，原来的 `resident_id` 筛选改名为 `related_resident_id`。

请求失败时，浏览器把条目恢复到提交前的状态，不把失败当作已读或已清除。用户回复对话、回答问题和在聊天室发言分别使用 `DeliverMessage`、`AnswerResidentRequest` 和聊天室指令，与页面相同。

## 与相邻设计的衔接

| 影响范围 | 风险 | 当前做法 |
| --- | --- | --- |
| 身份与消息来源 | 字符串 `mailbox` 被当成用户 | 来源 v2 的发出者类型是 `mailbox`；`mailbox` 是保留的非用户名称；来源由服务端生成 |
| 队列与状态 | 群体和成员都被领取，或拆分让已结束的输入复活 | 只有顶层项可调度；领取后不能整理；成员在群体结束时一起推进终态；只有 `RequeueMessage` 能把停止或失败的输入改回 `queued`，其他写入保留第一个终态 |
| 上下文与权限 | 合并让某条请求借到别人的权限 | 群体输入保留各发送者与整理者，供 Resident 判断请求意图；指令调用始终只按执行 Resident 自身权限判定（decision_b820faf42218） |
| 用量与执行 | 用量或运行次数按成员重复统计 | 群体只执行一次，用量记在这次执行上 |
| 发给用户的消息 | 用户看不到消息所在的对话 | 发送时检查收件用户能查看这段对话；消息只存一份，Mailbox 只指向它 |
| 平台署名 | 平台消息冒充 Resident 主动发言 | 署名为 Resident，`origin.producer` 记为 Portal 服务；不启动它的工作 |

相邻 Design 继续负责各自的部分：身份与署名见 decision_ae422b92c6f3，Resident 消息协议与“发给用户”见 decision_c524c775e0be，输入与执行流程见 decision_edc470e09f5a，Segment 见 decision_b1ab45c4b3ec，共享 Chat Workspace 见 decision_f560803947c6，浏览器会话状态层见 decision_18abbdef3da3，聊天室见 decision_a6bebf4ac283，Family 门面的收发见 decision_44cf587c1721，指令定义见 decision_2a3ff353165c 与 decision_2fe6fd5367fb，Leader 的产生与接任见 decision_6fc653d55c79。

格式名与版本须精确匹配当前规范值，校验规则见 decision_86aed2f0e8c7；本篇同时校验各字段和结构版本，不接受历史前缀或其他版本。
