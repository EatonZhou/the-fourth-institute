"""Conversation and context instructions (decision_2fe6fd5367fb)."""
from __future__ import annotations

from ..declarations import CURSOR, NEXT_CURSOR, ON_RESIDENT, InstructionDeclaration as D, On, P, R


def _d(name, decides, description, parameters=(), result=(), *, mutates=False, streaming=False,
       target="", one_of=(), argument_owners=()):
    return D(name=name, category="conversation", owner="lineage", description=description,
             parameters=tuple(parameters), result=tuple(result), mutates=mutates,
             streaming=streaming, target=target, one_of=one_of, argument_owners=argument_owners,
             decides=decides)


ACTIVATION = ("enqueue_and_trigger", "attach_to_next")
# concept_43c793b4f3ab: the kind of a message is the delivery, never words in its text.
ACTIVATION_DESCRIPTION = (
    "enqueue_and_trigger（默认）：请求，收件人空闲时立即处理，忙碌时排队；"
    "attach_to_next：info 消息，只告知变化，不单独触发执行，随收件人的下一条请求作为背景送达。"
    "消息的性质由这个参数表达，收件人会被告知这是无需单独回复的背景更新；"
    "正文只写内容本身，不必再写“这是 info”“无需回复”。"
)
MESSAGE = {
    "metadata": {"type": "object", "description": "可选business_context导航元数据，不授予权限"},
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
DELIVERY_CONTEXT = (
    P("context_policy", description="fresh 使用独立上下文，仅用于 enqueue_and_trigger", enum=("fresh", "session")),
    P("execution_constraints", "object", "显式受限文本任务，仅支持 version=1,text_only=true,context=fresh"),
    P("business_ref", description="调用方业务记录的统一 ID 或地址"),
)

FORWARD_INPUT = (
    R("messages", "array", "来源 Resident 或聊天室 ID 及消息 ID",
      items={"type": "object", "properties": {
          "resident_id": {"type": "string"}, "chatroom_id": {"type": "string"}, "message_id": {"type": "string"},
      }}),
    P("note", description="附加说明"),
)
RESIDENT_REQUEST = (
    R("resident_id", description="发出请求的 Resident"),
    R("message_id", description="请求所在的消息"),
)
REQUEST_STATUS = (
    R("status", enum=("pending", "answered", "declined", "cancelled", "expired"),
      description="处理后的请求状态"),
)


DECLARATIONS = (
    _d("GetDeletedResidentHistory", ON_RESIDENT,
       "按当前目标管理权限分页读取已删除 Resident 的保留身份、对话、执行与 Segment/产物引用索引；不恢复或运行。", (
        R("resident_id", description="已删除 Resident 的精确统一 ID"),
        P("section", description="默认 identity；messages 包含保留对话，executions 为执行索引，segments 仅无正文索引",
          enum=("identity", "messages", "executions", "segments", "tool_evidence")),
        P("ordinal", "integer", "tool_evidence必填：目标原trace的精确位置", minimum=1),
        P("tool_index", "integer", "省略只给无正文项索引；指定后只读该工具项", minimum=0),
        P("expected_hash", description="读取工具正文时必填：索引给出的Segment内容SHA256"),
        P("tool_item_id", description="读取工具正文时必填：索引给出的项ID；不接受任意payload/sequence"),
        P("cursor", description="上一页 next_cursor，仅适用于同一 Resident 和 section；每页重新鉴权"),
        P("limit", "integer", "每页上限，默认 50", minimum=1, maximum=100),
    ), (
        R("resident_id", description="同一历史身份"),
        R("identity", "object", "身份墓碑：resident_id、title、deleted_at、deleted_by"),
        R("section", description="本页类别"),
        R("items", "array", "有界保留事实；附件和外部来源只提供引用，不读取字节"),
        P("next_cursor", description="有后续页时提供；游标不授予权限"),
        P("availability", "object", "权威 Agent/trace 的保留情况；不证明原目录、原生会话或附件仍可用"),
    ), target="resident_id"),
    _d("DeliverMessage", ON_RESIDENT, "向 Resident 投递新请求或下一轮告知。", (
        R("resident_id", description="收件 Resident"),
        R("message", "object", "消息正文、附件和可选引用。引用由平台读取。", properties=MESSAGE),
        P("activation_policy", description=ACTIVATION_DESCRIPTION, enum=ACTIVATION),
        *DELIVERY_CONTEXT,
    ), (R("resident_id", description="收件 Resident"), *DELIVERED,
        P("sender_id", description="实际发送身份")), mutates=True, target="resident_id"),
    _d("PrepareResidentInput", ON_RESIDENT, "为随任务授权预备一条 Resident 请求。固定正文和消息 ID 后签发凭证，再用相同参数投递。", (
        R("resident_id", description="收件 Resident"),
        R("message", "object", "包含固定 message_id 的消息", properties=MESSAGE),
        P("activation_policy", description="仅可预备独立排队的新请求", enum=("enqueue_and_trigger",)),
        *DELIVERY_CONTEXT,
    ), (R("resident_id", description="收件 Resident"), R("message_id", description="预备请求的固定 ID"),
        R("status", description="请求的预备或终态"),
        P("credential_recipient_id", description="审批输入返回的凭证接收 Resident，用作 CreateTemporaryCredential 的 principal_id"),
        P("credential_message_id", description="审批输入绑定的消息 ID，用作 CreateTemporaryCredential 的 message_id")),
       mutates=True, target="resident_id"),
    _d("DeliverUserMessage", (On("user_id", "user"),), "Resident 向用户投递消息，写入发送方对话及收件人 Mailbox。", (
        R("user_id", description="收件用户"),
        R("message", "object", "消息正文与附件引用", properties={key: val for key, val in MESSAGE.items() if key != "quote"}),
        P("business_ref", description="调用方业务记录的统一 ID 或地址"),
    ), (R("resident_id", description="发送方对话所属 Resident"), *DELIVERED,
        P("sender_id", description="实际发送身份"), P("recipients", "array", "收件人")), mutates=True, target="user_id"),
    _d("ForwardMessagesToResident", (On("target_resident_id", "resident"),), "把可读消息转发给指定 Resident。", (
        R("target_resident_id", description="收件 Resident"), *FORWARD_INPUT,
        P("activation_policy", description=ACTIVATION_DESCRIPTION, enum=ACTIVATION),
    ), DELIVERED, mutates=True, target="target_resident_id"),
    _d("ForwardMessagesToChatroom", (On("target_chatroom_id", "chatroom"),), "把可读消息转发到指定聊天室。", (
        R("target_chatroom_id", description="接收转发的聊天室"), *FORWARD_INPUT,
    ), (R("message_id", description="转发生成的新消息 ID"), R("chatroom_id", description="收到转发的聊天室"),
        P("message", "object", "新消息"), P("deliveries", "array", "@ Resident 的投递结果")),
       mutates=True, target="target_chatroom_id"),
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
        R("input_states", "array", "输入状态：每项 message_id、state、updated_at，失败时带 error，结束的带 reply_message_ids；work 独立给出可信执行关联、工作状态及 updated_at，追加输入送达与工作完成分离"),
        R("state", "object", "Resident 状态与可用操作"),
        R("cursor", "integer", "事件游标：交给 WatchConversationEvents 的 after_cursor，接着读快照之后的事件"),
        NEXT_CURSOR("还有更早的消息时返回，交给 ListMessages 的 cursor"),
        P("context_evidence", "object", "显式请求时返回的 PostgreSQL 上下文与导入摘要"),
        P("inheritance", "object", "Fork 出的 Resident 才有：来源 Resident 与上下文来源（只有调用者能看到时才给标题）、"
          "fork_mode、forked_from_at、source_message_id，创建时继承的片段数和其中的对话消息数、"
          "最后一条继承消息的 ID、之后是否清除过上下文，以及开头的压缩（时间；按需带可读消息）"),
    ), target="resident_id"),
    _d("ListMessages", ON_RESIDENT, "从 GetConversation 的游标读取更早的消息。", (
        R("resident_id", description="对话所属 Resident"),
        R("cursor", description="上一页 next_cursor"),
        P("limit", "integer", "默认 200", minimum=1, maximum=1000),
    ), (R("messages", "array", "从旧到新"), NEXT_CURSOR("还有更早的消息时返回")), target="resident_id"),
    _d("ListMessageAttempts", ON_RESIDENT, "读取按业务对象权限过滤的尝试记录。", (
        R("resident_id", description="对话所属 Resident"),
        P("input_message_id", description="再次授权后读取这次输入及正式回复"),
        CURSOR("上一页 next_cursor，省略时从最新窗口读取"),
        P("limit", "integer", "默认 50", minimum=1, maximum=1000),
    ), (P("attempts", "array", "已授权的尝试记录"),
        P("conversation", "object", "已授权的单次正式对话快照"),
        P("default_view", description="工作流 fresh 策略的默认视图"),
        NEXT_CURSOR("还有更早的尝试记录时返回")), target="resident_id"),
    _d("ListResidentInputHistory", ON_RESIDENT, "读取调用者本人在该 Resident 会话发过的输入，包括其可读的继承输入。", (
        R("resident_id", description="对话所属 Resident"),
        CURSOR("上一页 next_cursor，省略时从最新输入读起"),
        P("limit", "integer", "默认 50", minimum=1, maximum=200),
    ), (R("messages", "array", "从新到旧"), NEXT_CURSOR("还有更早的输入时返回")), target="resident_id"),
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
    _d("SearchResidentMessages", (On("resident_ids", "resident"),), "在指定 Resident 的对话中按内容搜索。", (
        R("resident_ids", "array", "搜索范围", items={"type": "string"}),
        R("query", description="要查找的文字", max_length=200),
        CURSOR("上一页 next_cursor，省略时从最新结果读起"),
        P("limit", "integer", "默认 50", minimum=1, maximum=200),
    ), (R("results", "array", "每项包含 Resident ID、消息与前后消息 ID"),
        NEXT_CURSOR("还有更多结果时返回")), target="resident_ids"),
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
    _d("ClearContext", ON_RESIDENT, "提交上下文重置并归档当前对话；后续输入待清理完成后从新的模型上下文开始，Resident 身份保持不变。此操作不保证删除 Provider 端保留的会话历史。", (
        R("resident_id", description="要清除上下文的 Resident"),
    ), (
        R("cleanup_pending", "boolean", "归档对话与释放旧会话绑定已排队；新输入可排队等待清理，不表示 Provider 端历史已删除"),
        R("operation_id", description="清理生命周期 ID"),
    ), mutates=True, target="resident_id"),
    _d("CreateContextReset", ON_RESIDENT, "安排当前轮结束后、下一条输入提交前清空上下文，保留排队消息及原账号。", (
        R("resident_id", description="要清除上下文的 Resident"),
    ), (
        R("cleanup_pending", "boolean", "是否已有归档与释放旧绑定工作待处理"),
        R("operation_id", description="本次等待清空的操作 ID"),
        R("restart_id", description="等待操作的回执 ID，供 GetSessionRestart 查询结果与 CancelSessionRestart 取消"),
        R("status", description="accepted 表示等待清空已受理"),
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
    _d("AnswerResidentRequest", ON_RESIDENT, "回答问题或作出审批决定，送回等待中的轮次。", (
        *RESIDENT_REQUEST,
        P("answers", "object", "按问题序号给出的回答"),
        P("decision", description="审批决定", enum=("accept", "decline")),
        P("content", "object", "接受 MCP 请求时返回的内容"),
    ), REQUEST_STATUS, mutates=True, target="resident_id", one_of=(("answers",), ("decision",))),
    _d("CancelResidentRequest", ON_RESIDENT, "关闭等待中的请求或消息问题组。", (
        *RESIDENT_REQUEST,
    ), REQUEST_STATUS, mutates=True, target="resident_id"),
)
