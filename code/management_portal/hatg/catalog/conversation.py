"""Conversation and context instructions (decision_2fe6fd5367fb)."""
from __future__ import annotations

from ..declarations import CURSOR, NEXT_CURSOR, ON_RESIDENT, InstructionDeclaration as D, On, P, R


def _d(name, decides, description, parameters=(), result=(), *, mutates=False, streaming=False,
       target="", one_of=(), argument_owners=()):
    return D(name=name, category="conversation", owner="lineage", description=description,
             parameters=tuple(parameters), result=tuple(result), mutates=mutates,
             streaming=streaming, target=target, one_of=one_of, argument_owners=argument_owners,
             decides=decides)


ACTIVATION = ("enqueue_and_trigger", "inject_active_turn", "attach_to_next")
# concept_43c793b4f3ab: the kind of a message is the delivery, never words in its text.
ACTIVATION_DESCRIPTION = (
    "enqueue_and_trigger（默认）：请求，收件人空闲时立即处理，忙碌时排队；"
    "inject_active_turn：插入收件人正在进行的工作；"
    "attach_to_next：info 消息，只告知变化，不单独触发执行，随收件人的下一条请求作为背景送达。"
    "消息的性质由这个参数表达，收件人会被告知这是无需单独回复的背景更新；"
    "正文只写内容本身，不必再写“这是 info”“无需回复”。"
)
MESSAGE = {
    "message_id": {"type": "string"},
    "role": {"type": "string", "enum": ["user"]},
    "content_parts": {"type": "array"},
    "quote": {"type": "object", "properties": {"message_id": {"type": "string"}}},
}
DELIVERED = (
    R("message_id", description="投递出的消息 ID"),
    P("accepted", "boolean", "普通异步请求已持久接收；后续状态通过快照和事件获取"),
    P("input_state", "object", "已返回投影时的消息输入状态；异步受理回执可省略"),
)

DECLARATIONS = (
    _d("DeliverMessage", (On("resident_id", "resident", optional=True), On("user_id", "user", optional=True)), (
        "向 Resident 投递一条用户消息；Resident 空闲时立即开始处理，忙碌时按接收顺序排队。"
        "也可以由 Resident 发给用户：消息不进入队列，写在发送方自己的对话中并注明收件人，用户在自己的 Mailbox 中看到未读。"
        "Family 的成员或 steward 发给 Family 以外的用户或 Resident 时，以门面身份发出。回复不会自动送回，收件方自己决定是否回复。"
        "收件 Resident 回复门面发来的消息时，门面把回复直接交给写那条消息的成员：在处理那条消息的一轮里发给门面，"
        "平台自动认作回复；之后的轮次用 reply_to_message_id 指明。"
    ), (
        P("resident_id", description="收件 Resident；与 user_id 二选一"),
        P("user_id", description="把消息发给这位用户；只有 Resident 可以使用，消息只包含文字和资源引用"),
        R("message", "object", "只包含 message_id（可省略）、role（可省略，只能是 user）、content_parts，"
          "以及可选的 quote：引用这段对话中的一条消息，只给出 {\"message_id\": ...}，作者和摘录由平台填写；"
          "info 消息和发给用户的消息不带引用",
          properties=MESSAGE),
        P("activation_policy", description=ACTIVATION_DESCRIPTION, enum=ACTIVATION),
        P("business_ref", description="调用方业务记录的统一 ID 或地址"),
        P("reply_to_message_id", description="回复 Family 门面发来的哪条消息：填你对话中收到的那条消息的 ID，"
          "平台引用门面对它的发件记录，门面把回复交给写它的成员；不和 quote 同用，发给用户和 info 消息不带它"),
        P("temporary_credentials", "array",
          "随这条消息交给收件 Resident 的临时凭证，每项 {a, resource_id}：调用者此刻在该资源上持有的 A 树节点；"
          "收件 Resident 对这条消息的输入完成、失败或取消时失效，最迟 7 天。只用于发给 Resident",
          items={"type": "object", "properties": {"a": {"type": "string"}, "resource_id": {"type": "string"}}}),
    ), (
        R("resident_id", description="消息所在的对话：发给 Resident 时是收件 Resident，发给用户时是发出消息的 Resident 或门面"),
        *DELIVERED,
        P("sender_id", description="消息实际以谁的身份发出：调用者自己，或它所在 Family 的门面"),
        P("recipients", "array", "发给用户时的收件人", items={"type": "object"}),
        P("temporary_credentials", "array", "随消息签发的临时凭证：credential_id、principal_id、a、resource_id、issued_by、"
          "issued_at、expires_at、message_id", items={"type": "object"}),
    ), mutates=True, target="resident_id",
       one_of=(("resident_id",), ("user_id",))),
    _d("ForwardMessages", (On("target_resident_id", "resident", optional=True), On("target_chatroom_id", "chatroom", optional=True)), "把选中的若干条消息整理成一条新消息，投递给 Resident 或发到聊天室。", (
        P("target_resident_id", description="接收转发的 Resident；与 target_chatroom_id 二选一"),
        P("target_chatroom_id", description="接收转发的聊天室；与 target_resident_id 二选一"),
        R("messages", "array", "每项包含来源 resident_id 与 message_id；聊天室中的消息给出 chatroom_id 与 message_id",
          items={"type": "object", "properties": {
              "resident_id": {"type": "string"}, "chatroom_id": {"type": "string"}, "message_id": {"type": "string"},
          }}),
        P("note", description="附加说明"),
        P("activation_policy", description="仅用于 Resident 目标。" + ACTIVATION_DESCRIPTION, enum=ACTIVATION),
    ), (R("message_id", description="转发生成的新消息 ID"), P("input_state", "object", "发给 Resident 时的输入状态"),
        P("chatroom_id", description="发到聊天室时：接收转发的聊天室"), P("message", "object", "发到聊天室的新消息"),
        P("deliveries", "array", "发到聊天室时：新消息中 @ 到的 Resident 的投递，同 CreateChatroomMessage")),
       mutates=True, target="target_resident_id", one_of=(("target_resident_id",), ("target_chatroom_id",))),
    _d("GetConversation", ON_RESIDENT, "读取一个 Resident 的对话快照，用于首次加载或断线恢复。", (
        R("resident_id", description="要读取对话的 Resident"),
        P("history_limit", "integer", "快照中最近消息的条数，默认 200；更早的消息用 ListMessages 续读", minimum=1, maximum=1000),
        P("input_message_id", description="只读取这一条输入及其回复"),
        P("include_context_evidence", "boolean", "按需读取轨迹、有效上下文与 Fork 导入的摘要证据，不返回原文或图片"),
        P("include_inherited_context", "boolean", "Fork 出的 Resident 继承的上下文以来源的一次压缩开头时，"
          "在 inheritance.opening_compaction 中返回这份压缩的可读消息"),
    ), (
        R("resident", "object", "Resident 记录：resident_id、title、lineage_id、project_id、provider、account、workspaces…"),
        R("messages", "array", "最近的消息，从旧到新：message_id、role、author_id、content_parts、created_at…"),
        R("input_states", "array", "输入状态：每项 message_id、state、updated_at，失败时带 error，结束的带 reply_message_ids"),
        R("state", "object", "Resident 状态与可用操作"),
        R("cursor", "integer", "事件游标：交给 WatchConversationEvents 的 after_cursor，接着读快照之后的事件"),
        NEXT_CURSOR("还有更早的消息时返回，交给 ListMessages 的 cursor"),
        P("context_evidence", "object", "显式请求时返回的 PostgreSQL 上下文与导入摘要"),
        P("inheritance", "object", "Fork 出的 Resident 才有：来源 Resident 与上下文来源（只有调用者能看到时才给标题）、"
          "fork_mode、forked_from_at、source_message_id，创建时继承的片段数和其中的对话消息数、"
          "最后一条继承消息的 ID、之后是否清除过上下文，以及开头的压缩（时间；按需带可读消息）"),
    ), target="resident_id"),
    _d("ListMessages", ON_RESIDENT, "从 GetConversation 快照往前分页读取更早的消息。", (
        R("resident_id", description="要读取消息的 Resident"),
        CURSOR("GetConversation 或上一页 ListMessages 返回的 next_cursor，原样交回", required=True),
        P("limit", "integer", "默认 200", minimum=1, maximum=1000),
    ), (R("messages", "array", "本页更早的消息，从旧到新，格式同 GetConversation 的 messages"),
        NEXT_CURSOR("还有更早的消息时返回")), target="resident_id"),
    _d("ListInputHistory", (On("resident_id", "resident", optional=True), On("chatroom_id", "chatroom", optional=True)), "读取调用者本人发过的输入，供输入框的上下箭头历史使用；resident_id 与 chatroom_id 二选一。"
       "带 resident_id：调用者作为用户在这个 Resident 会话里的输入，包括 Fork 继承的和清除上下文之前的输入，"
       "不含 Resident 投递、Mailbox 合并和系统消息。带 chatroom_id：调用者在这个聊天室发过的消息，撤回的消息返回撤回前的内容。", (
        P("resident_id", description="读取这个 Resident 会话里的输入"),
        P("chatroom_id", description="读取这个聊天室里的输入"),
        CURSOR("上一页返回的 next_cursor，原样交回；不填时从最新的输入读起"),
        P("limit", "integer", "默认 50", minimum=1, maximum=200),
    ), (R("messages", "array", "从新到旧"), NEXT_CURSOR("还有更早的输入时返回")),
       target="resident_id", one_of=(("resident_id",), ("chatroom_id",)),
       argument_owners=(("chatroom_id", "web"),)),
    _d("GetMessage", ON_RESIDENT, "按消息 ID 读取一条消息。", (
        R("resident_id", description="消息所在对话的 Resident"), R("message_id", description="要读取的消息"),
    ), (
        R("message", "object", "消息：message_id、role、author_id、content_parts、created_at…"),
    ), target="resident_id"),
    _d("ListRecentRounds", ON_RESIDENT, "读取一个 Resident 最近几轮往来：每轮的请求开头、由谁答复、结果和回复开头；可以只看某位发送者。", (
        R("resident_id", description="要查看的 Resident，可以是 Family 门面"),
        P("sender_id", description="只列出这位发送者（用户或 Resident ID）的请求，以及发给这位发送者的消息"),
        P("before_message_id", description="只列出这条消息之前的轮次"),
        P("limit", "integer", "默认 3", minimum=1, maximum=10),
    ), (
        R("rounds", "array", "从旧到新；每项包含 kind（request 是请求；sent 是 Resident 或 Family 主动发给这位发送者的消息）、message_id、sender、created_at、request、answered_by（门面上是转交的成员，sent 是写这条消息的成员）、state 与 reply"),
        P("direct", "array", "Family 门面且指定 sender_id 时给出：这位发送者在门面之外直接和成员的最近对话，从新到旧，最多 3 个成员；每项包含 resident_id、title、created_at、kind（request 是发送者最后发给成员的消息，sent 是成员最后发给发送者的消息）、message_id 与 text"),
    ), target="resident_id"),
    _d("SearchMessages", (On("resident_ids", "resident", optional=True), On("chatroom_id", "chatroom", optional=True)), "按内容搜索消息；resident_ids 与 chatroom_id 二选一。"
       "带 resident_ids：在这些 Resident 的对话中搜索。带 chatroom_id：在聊天室全部当前历史中搜索正文和附件名称。", (
        P("resident_ids", "array", "在这些 Resident 的对话中搜索", items={"type": "string"}),
        P("chatroom_id", description="在这个聊天室中搜索"),
        R("query", description="要查找的文字", max_length=200),
        CURSOR("上一页返回的 next_cursor，原样交回；不填时从最新的结果读起"),
        P("limit", "integer", "默认 50；聊天室每页最多 100", minimum=1, maximum=200),
    ), (P("results", "array", "带 resident_ids 时：每项包含 Resident ID、消息与前后消息 ID"),
        P("messages", "array", "带 chatroom_id 时：匹配的聊天室消息，从新到旧"),
        NEXT_CURSOR("还有更多结果时返回")),
       one_of=(("resident_ids",), ("chatroom_id",)), argument_owners=(("chatroom_id", "web"),)),
    _d("GetMessageArtifact", ON_RESIDENT, "读取 Agent 消息引用的一项产物。", (
        R("resident_id", description="消息所在对话的 Resident"), R("artifact_id", description="消息中引用的产物 ID"),
    ), (
        R("artifact", "object", "产物：artifact_id、media_type、value（Provider 原始响应）"),
    ), target="resident_id"),
    _d("WatchConversationEvents", ON_RESIDENT, "从指定游标之后订阅一个 Resident 的对话事件，按顺序持续送出新事件；"
       "首批立即返回，只需读一次时取首批后关闭订阅。", (
        R("resident_id", description="要订阅对话事件的 Resident"),
        R("after_cursor", "integer", "从这个游标之后送出：GetConversation 返回的 cursor，或上次收到的最后一个 cursor", minimum=0),
        P("limit", "integer", "每批最多送出的事件数，默认 500", minimum=1, maximum=2000),
        P("input_message_id", description="只送出这一条输入的事件正文"),
    ), (P("event", "object", "事件项的正文"),
        R("cursor", "integer", "这一项的事件游标；检查点项为已送出的最后一个事件的游标"),
        P("high_watermark", "integer", "每批末尾的检查点项携带服务端最新游标；首批立即返回，空页也有检查点")), streaming=True, target="resident_id"),
    _d("GetSentPrompt", ON_RESIDENT, "读取某条用户消息实际发送给模型的完整内容。", (
        R("resident_id", description="收到这条消息的 Resident"), R("message_id", description="用户消息的 ID"),
    ), (
        R("prompt", "object", "发送给 Provider 的完整输入"),
    ), target="resident_id"),
    _d("GetSubagentRun", ON_RESIDENT, "读取 Resident 的模型启动的一个子 agent 的全部步骤：它收到的任务、每次工具调用的完整输入和输出摘要、说过的话和起止时间。", (
        R("resident_id", description="启动这个子 agent 的 Resident"),
        R("trace_ref", description="子 agent 卡片上的 trace_ref：这个子 agent 自己那条轨迹的键"),
        P("offset", "integer", "从第几条记录开始，默认 0；按位置读取，可以直接从末尾附近读起", minimum=0),
        P("limit", "integer", "默认 200", minimum=1, maximum=500),
    ), (
        R("run", "object", "trace_ref、total（记录总数）、offset、updated_at 与 steps；steps 每项的 kind 为 input、tool、message 或 thinking"),
    ), target="resident_id"),
    _d("ClearContext", ON_RESIDENT, "归档 Resident 当前的对话，清空模型上下文，Resident 身份保持不变。", (
        R("resident_id", description="要清除上下文的 Resident"),
    ), (
        R("cleanup_pending", "boolean", "归档对话与释放会话的后续清理已排队"),
        R("operation_id", description="这次后续清理的生命周期操作 ID"),
    ), mutates=True, target="resident_id"),
    _d("AbsorbContext", (On("source_resident_id", "resident"), On("target_resident_id", "resident")), "让目标 Resident 吸收来源 Resident 在 Fork 之后的讨论和工作理解。", (
        R("source_resident_id", description="来源 Resident：吸收它在 Fork 之后的讨论和工作理解"),
        R("target_resident_id", description="吸收这些理解的 Resident"),
    ), (
        R("source_resident_id", description="被吸收的来源 Resident"), R("target_resident_id", description="吸收理解的目标 Resident"),
        R("relation_id", description="Lineage 中记录这次吸收的连线 ID"),
        R("message_id", description="投给目标 Resident 的吸收消息 ID"),
    ), mutates=True, target="target_resident_id"),
    _d("ListResidentRequests", (On("resident_ids", "resident"),), "列出一个或多个 Resident 等待回答的请求。", (
        R("resident_ids", "array", "要列出请求的 Resident", items={"type": "string"}),
    ), (R("requests", "array", "Resident ID、消息 ID、请求种类、问题与可选答案、发出时间"),),
       target="resident_ids"),
    _d("GetResidentRequest", ON_RESIDENT, "读取一个请求及其回答状态。", (
        R("resident_id", description="发出请求的 Resident"), R("message_id", description="请求所在的消息"),
    ), (
        R("request", "object", "请求：resident_id、message_id、kind（question 或 approval）、questions、status、answers、created_at"),
        R("status", enum=("pending", "answered", "declined", "cancelled", "expired"), description="请求的回答状态，同 request.status；declined 表示已拒绝"),
    ), target="resident_id"),
    _d("AnswerResidentRequest", ON_RESIDENT, "回答一个请求，并把回答送回 Resident 正在等待的那一轮工作；对消息中的问题组只能用 action=cancel 关闭，不通知 Resident。", (
        R("resident_id", description="发出请求的 Resident"), R("message_id", description="请求所在的消息"),
        R("answers", "object", "按问题序号给出的回答；批准请求给出批准或拒绝"),
    ), (R("status", enum=("pending", "answered", "declined", "cancelled", "expired"),
          description="回答后的状态：已送达为 answered、declined 或 cancelled，仍在排队为 pending，没能送达为 expired"),),
       mutates=True, target="resident_id"),
)
