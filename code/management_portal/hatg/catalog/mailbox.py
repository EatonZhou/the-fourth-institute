"""Mailbox instructions (decision_15f7df737ed5).

A User's Mailbox (conversations, notifications and personal Todos) is kept by
Portal Web; a Resident's Mailbox (the waiting requests, groups and infos) by
SocietyService.  An instruction that covers both takes ``resident_id`` or
``user_id`` and routes the Resident form to SocietyService (argument_owners).
"""
from __future__ import annotations

from ..declarations import FIXED, ON_RESIDENT, ON_USER, InstructionDeclaration as D, On, P, R


def _d(name, decides, description, parameters=(), result=(), *, mutates=False, streaming=False,
       target="", owner="web", one_of=(), argument_owners=()):
    return D(name=name, category="mailbox", owner=owner, description=description,
             parameters=tuple(parameters), result=tuple(result), mutates=mutates,
             streaming=streaming, target=target, one_of=one_of, argument_owners=argument_owners,
             decides=decides)


QUEUE_ITEM = (
    R("resident_id", description="Mailbox 所属的 Resident"),
    R("message_id", description="要处理的消息：拆分的群体、放回的请求或注入的等待消息"),
)
EITHER = (("resident_id",), ("user_id",))
RESIDENT_AT_LINEAGE = (("resident_id", "lineage"),)

DECLARATIONS = (
    _d("GetMailbox", (On("resident_id", "resident", optional=True), On("user_id", "user", optional=True)), "读取一个 Mailbox，resident_id 与 user_id 二选一。"
        "带 resident_id：读取这个 Resident 的 Mailbox，即等待中的请求、群体与 info，正在处理的一轮，以及最近结束的几轮与结果。"
        "带 user_id：读取这位 User 的 Mailbox（对话与通知）；user_id 是调用者自己时读取自己的 Mailbox。", (
        P("resident_id", description="要读取 Mailbox 的 Resident"),
        P("user_id", description="要读取 Mailbox 的 User"),
        P("related_resident_id", description="带 user_id 时可选：只读取与这个 Resident 相关的条目"),
    ), (
        P("items", "array", "带 user_id 时：置顶在前，其余按最近活动从新到旧，同一时间按 key 升序；每项是一段对话或一条通知；"
          "Resident 对话条目的 latest_message_id 是这段对话最近一条可通知消息的 ID，用 GetMessage(resident_id, message_id) 读取原文，"
          "input_message_id 是它回应的那条输入；version 是条目阅读状态的修订号，作为 MarkMailboxItemRead 的 expected_version"),
        P("unread_count", "integer", "带 user_id 时：未读条目数"),
        P("pending", "array", "带 resident_id 时：等待中的项，先是队列中的请求与群体（按处理顺序），再是等待中的 info；"
          "每项有 kind（request、group、info）、message_id、author_id、created_at、preview，群体另有 members"),
        P("active", "object", "带 resident_id 时：正在处理的一轮，请求与随它送达的 info（infos）；没有时为空"),
        P("recent", "array", "带 resident_id 时：最近结束的几轮，新的在前：请求、随行 info、结果 state（completed、cancelled、failed）、"
          "finished_at，以及能否放回队列 can_requeue"),
        P("can_organize", "boolean", "带 resident_id 时：调用者能否整理这个 Mailbox（合并、拆分、调序、撤回、放回队列）"),
        P("revision", description="带 resident_id 时：当前等待顺序的标识；有请求到达、离开或调序时改变，与整理指令返回的 revision 相同"),
    ), target="resident_id", one_of=EITHER, argument_owners=RESIDENT_AT_LINEAGE),
    _d("WatchMailbox", ON_USER, "等待与页面相同的 User Mailbox 变化；把返回的 cursor 作为 after_cursor 续读。", (
        R("user_id", description="Mailbox 所属 User 的用户名"),
        P("after_cursor", description="上次返回的 cursor；首次省略，先返回重读检查点"),
        P("timeout_seconds", "integer", "本次订阅时长，默认 30 秒", minimum=1, maximum=60),
    ), (R("keys", "array", "这批有变化的 Mailbox 条目键"), R("cursor", description="下次作 after_cursor 续读的游标"),
        R("snapshot_required", "boolean", "为 true 时须用 GetMailbox 重读整个 Mailbox")), streaming=True),
    _d("PinMailboxItem", FIXED, "置顶或取消置顶调用者 Mailbox 中的对话；可固定尚未交谈的可见 Resident，不改变未读状态。通知不能置顶，最多 50 个。", (
        R("key", description="条目键；Resident 为 resident:resident_<id>"), R("pinned", "boolean", "true 置顶，false 取消置顶"),
    ), (R("item", "object", "改后的对话条目，与 GetMailbox 的条目同形；仍未列出时为空对象"),), mutates=True, target="key"),
    _d("MarkMailboxItemRead", FIXED, "把调用者 Mailbox 中的条目标为已读，或重新标为未读；给出 expected_version 时，条目之后有新提醒或其他变化则拒绝修改。"
        "页面打开一段 Resident Chat 时不带 expected_version 调用，读到它最新的一条提醒。", (
        R("key", description="条目键；Resident 对话为 resident:resident_<id>，可以是 Mailbox 中尚未列出的可见 Resident"),
        P("unread", "boolean", "true 时重新标为未读"),
    ), (R("item", "object", "条目阅读状态：key、unread、read_revision（新的 version），以及 pinned、dismissed_through"),),
       mutates=True, target="key"),
    _d("ClearMailbox", (On("resident_id", "resident", optional=True), On("user_id", "user", optional=True)), "清空一个 Mailbox，resident_id 与 user_id 二选一。"
        "带 resident_id：一次原子地取消这个 Resident Mailbox 中所有还能撤回的等待消息。"
        "带 user_id：按 mode 清理这位 User 的 Mailbox 条目或未读状态；user_id 是调用者自己时清理自己的 Mailbox。", (
        P("resident_id", description="要清空等待消息的 Resident"),
        P("user_id", description="要清理 Mailbox 的 User"),
        P("mode", description="带 user_id 时必填：unread 全部标为已读，all 移除未置顶的条目", enum=("unread", "all")),
        P("section", description="带 user_id 时可选，只清理这一类条目；省略时清理消息栏中的全部对话与通知，不影响 Todo", enum=("conversation", "notification")),
    ), (P("status", description="User Mailbox 的清理结果"), P("cleared", "integer", "User Mailbox 中标为已读或移除的条目数"),
        P("items", "array", "User Mailbox 中状态变化的条目：key、unread、pinned、read_revision、dismissed_through"),
        P("cancelled", "integer", "Resident Mailbox 中被取消的等待消息数"),
        P("resident_id", description="带 resident_id 时：被清空的 Resident"),
        P("cursor", "integer", "带 resident_id 时：清空后的对话事件游标"),
        P("state", "object", "带 resident_id 时：清空后的 Resident 状态"),
        P("controls", "object", "带 resident_id 时：调用者此后可用的操作")),
       mutates=True, one_of=EITHER, argument_owners=RESIDENT_AT_LINEAGE),
    _d("ReorderMailbox", ON_RESIDENT, "调整等待消息的顺序：把一条消息移到另一条之前或之后，或给出全部等待消息的新排列。", (
        R("resident_id", description="Mailbox 所属的 Resident"),
        P("message_id", description="要移动的消息"),
        P("before_message_id", description="移到这条消息之前"),
        P("after_message_id", description="移到这条消息之后"),
        P("order", "array", "当前全部等待消息 ID 的新排列", items={"type": "string"}),
    ), (
        R("items", "array", "调整后的队列"), R("revision", description="调整后等待顺序的标识，与 GetMailbox 的 revision 相同"),
        R("reordered", "boolean", "顺序是否有变化"),
    ), mutates=True, target="resident_id", owner="lineage"),
    _d("MergeMailboxItems", ON_RESIDENT, "把若干条等待消息合并为一条 Mailbox 群体消息，放在其中最早一条的位置；成员原文与作者不变。"
        "不同发送者的消息也可合并：群体输入写明每条的发送者，由收到的 Resident 判断；单一发送者的群体按该发送者执行，多发送者的群体按整理者执行。", (
        R("resident_id", description="Mailbox 所属的 Resident"),
        R("message_ids", "array", "选中的顶层等待消息 ID；选中群体消息时按成员展开，结果仍是一层", items={"type": "string"}),
    ), (
        R("group_message_id", description="合并成的群体消息"), R("member_message_ids", "array", "按处理顺序排列的成员"),
        R("items", "array", "调整后的队列"), R("revision", description="调整后等待顺序的标识，与 GetMailbox 的 revision 相同"),
    ), mutates=True, target="resident_id", owner="lineage"),
    _d("SplitMailboxItem", ON_RESIDENT, "把一条尚未执行的群体消息拆回原来的成员消息，成员按顺序回到它的位置。", QUEUE_ITEM, (
        R("group_message_id", description="被拆开的群体消息"), R("member_message_ids", "array", "拆回队列的成员消息，按处理顺序"),
        R("items", "array", "调整后的队列"), R("revision", description="调整后等待顺序的标识，与 GetMailbox 的 revision 相同"),
    ), mutates=True, target="resident_id", owner="lineage"),
    _d("RequeueMessage", ON_RESIDENT, "把停止或失败的一轮放回队列：原请求以原消息 ID、发送者、时间和正文回到队首，"
        "随它送达的 info 重新等待，随下一条请求送达。接受最近几轮中停止或失败的请求，"
        "以及已接收但丢失执行位置的 queued 请求；后者必须保有同一 Resident 的完整原消息和接收封装。"
        "群体按原成员和顺序恢复，缺原件、前像冲突或成员已有其他执行位置时拒绝。"
        "原请求已在等待或正在处理时不重复入队；不从 prompt 或发送方副本重建原件。"
        "这是显式恢复，User 与 Resident 按相同的整理权限执行，不受后台自动恢复次数限制，也不消耗该次数。", QUEUE_ITEM, (
        R("message_ids", "array", "放回的消息：请求在前，随行 info 按原顺序"),
        R("items", "array", "调整后的队列"), R("revision", description="调整后等待顺序的标识，与 GetMailbox 的 revision 相同"),
    ), mutates=True, target="resident_id", owner="lineage"),
    _d("InjectMailboxItem", ON_RESIDENT, "把一条等待消息立即交给 Resident 当前正在进行的工作。", QUEUE_ITEM, (
        R("status", description="投递回执；pending 表示等待当前工作接收"),
    ), mutates=True, target="resident_id", owner="lineage"),
    _d("ListMailboxTodos", ON_USER, "读取一位 User 的 Mailbox 待办，按日期倒序、同日个人顺序排列；勾选不改变位置。", (
        R("user_id", description="待办所属 User 的用户名"),
    ), (R("items", "array", "待办列表，每项 todo_id、text、date、completed、indent、position 等"),
        R("pending_count", "integer", "未完成的待办数")), target="user_id"),
    _d("CreateMailboxTodo", FIXED, "添加一条 1–100 字的个人待办；User 添加给自己，Resident 添加给指定的 User。", (
        R("text", description="待办文字，1–100 字", max_length=100), R("user_id", description="接收待办的 User；User 只能写自己的用户名"),
        P("date", description="YYYY-MM-DD，省略时为香港时区今天；用于折叠日期分组"),
    ), (R("item", "object", "新建的待办：todo_id、user_id、text、date、completed、indent 等"),), mutates=True),
    _d("UpdateMailboxTodo", FIXED, "编辑自己的待办文字、日期、完成状态或缩进。", (
        R("todo_id", description="要编辑的待办"), R("changes", "object", "text（1–100 字）、date（YYYY-MM-DD）、completed（布尔值）、indent（0–4 整数）"),
    ), (R("item", "object", "修改后的待办：todo_id、text、date、completed、indent 等"),), mutates=True, target="todo_id"),
    _d("DeleteMailboxTodo", FIXED, "删除自己的一条个人待办。", (R("todo_id", description="要删除的待办"),),
       (R("todo_id", description="被删除的待办"),), mutates=True, target="todo_id"),
    _d("MoveMailboxTodo", FIXED, "移动自己的待办：指定同日方向，或另一条待办之前/之后；跨日期时一并更新日期。三种位置须且只能指定一种。", (
        R("todo_id", description="要移动的待办"),
        P("direction", description="在同一天内上移（up）或下移（down）一位", enum=("up", "down")),
        P("before_todo_id", description="放在此待办之前"), P("after_todo_id", description="放在此待办之后"),
    ), (R("item", "object", "移动后的待办，含新的 date 与 position"),), mutates=True, target="todo_id"),
    _d("CreateNotification", FIXED, "向人类参与者发送一条持久通知，写入每位接收者的 Mailbox。", (
        R("title", description="通知标题"), R("message", description="通知正文"),
        P("recipients", "array", "接收者用户名；省略时通知人类发起人，或 Resident 发起人的登记 owner", items={"type": "string"}),
        P("lineage_id", description="通知所属的 Lineage"),
        P("severity", description="通知级别，默认 info", enum=("info", "success", "warning", "error")),
        P("notification_id", description="调用者提供的稳定 ID"),
    ), (R("notification_id", description="通知 ID；提供了 notification_id 时即为它"),
        R("recipients", "array", "实际接收通知的用户名"),
        R("created", "boolean", "是否新建；同一 notification_id 的通知已存在时为 false")),
       mutates=True, target="lineage_id", owner="lineage"),
)
