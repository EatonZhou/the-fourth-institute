"""Facade membership, request relay and summary reads."""
from ..declarations import FIXED, ON_RESIDENT, InstructionDeclaration as D, P, R
from .conversation import MESSAGE


def _d(name, decides, description, parameters, result, *, mutates=True, one_of=()):
    return D(name=name, category="family_internal", owner="lineage", description=description,
             parameters=tuple(parameters), result=tuple(result), mutates=mutates, target="resident_id",
             decides=decides, discovery_group="internal", one_of=one_of)


DECLARATIONS = (
    _d("UpdateFacadeMembers", ON_RESIDENT, "整体替换门面的转交对象。空集合取消门面身份。", (
        R("resident_id", description="门面 Resident"),
        R("member_ids", "array", "新的转交对象集合", items={"type": "string"}),
    ), (
        R("resident_id", description="门面 Resident"),
        R("member_ids", "array", "保存后的转交对象集合"),
    )),
    _d("CheckRelayTarget", ON_RESIDENT, "读取门面可转交成员的接单状态和额度。", (
        R("resident_id", description="门面 Resident"),
        R("member_id", description="候选成员"),
    ), (
        R("status", description="候选状态", enum=("ready", "quota_low", "unavailable", "unknown")),
        P("reason", description="状态原因"),
        P("remaining_percent", "number", "已知的可用额度百分比"),
    ), mutates=False),
    _d("GetRelayContext", ON_RESIDENT, "按需读取门面请求、候选成员或受理时冻结的上一轮问答。", (
        R("resident_id", description="门面 Resident"),
        R("message_id", description="门面的原请求 ID"),
        P("section", description="返回哪部分内容，默认 members", enum=("members", "previous_round", "request")),
    ), (
        R("resident_id", description="门面 Resident"),
        R("message_id", description="原请求 ID"),
        P("members", "array", "候选居民的 ID、角色、名称与读取可用性"),
        P("chat_preferences", "object", "原发送者的路由约束"),
        P("previous_round", "object", "冻结的上一轮问答"),
        P("request", "object", "门面收到的原请求"),
    ), mutates=False),
    _d("ResolveRelayedRequest", ON_RESIDENT, "用已完成的复核结果归档同一原失败请求，保留原失败回执。", (
        R("resident_id", description="门面 Resident"),
        R("message_id", description="门面内尚未转交的原失败请求"),
        R("review_message_id", description="已完成且有实际回复的复核输入，须绑定到原请求"),
        R("reason", description="复核结果满足原请求的依据"),
    ), (R("status", description="archived"), R("message_id", description="原请求 ID"),
        R("review_message_id", description="已完成的复核输入 ID"),
        R("reply_message_ids", "array", "原请求下可读取的实际回复 ID"))),
    _d("RelayMessage", ON_RESIDENT, "转交门面请求、收下告知、改派或回复门面外发的消息。", (
        R("resident_id", description="门面 Resident"),
        P("message_id", description="门面中的原请求"),
        P("member_id", description="转交或改派的成员"),
        P("notice", "boolean", "由门面收下结果报告或告知"),
        P("continue_previous", "boolean", "附带已核定的上一轮背景"),
        P("reassign", "boolean", "把尚可改派的工作交给新成员"),
        P("reason", description="转交、收下告知或改派的理由"),
        P("reply_to_message_id", description="要回复的门面外发消息 ID"),
        P("message", "object", "回复内容，含正文或附件 content_parts", properties=MESSAGE),
        P("business_ref", description="回复的调用方业务记录"),
    ), (
        R("status", description="转交结果"),
        P("message_id", description="承接或收到的消息 ID"),
        P("member_id", description="实际承接的成员"),
        P("previous_member_id", description="改派前的成员"),
    ), one_of=(("message_id",), ("reply_to_message_id", "message"))),
    _d("GetFacadeSummaryInputs", FIXED,
       "门面本人、本 Family Steward 或拥有该门面 @admin 权限的调用者可以读取成员摘要与来源版本。"
       "无变化时 needs_update=false。", (
        R("resident_id", description="门面 Resident"),
        P("overview_only", "boolean", "只返回是否变化及待归纳 Resident/版本/层级，不返回摘要正文"),
    ), (R("resident_id", description="门面 Resident"), R("title", description="Family 名称"), R("goal", description="Family 共同目标"),
        R("members", "array", "每个成员 {resident_id, title, summary, summary_stale}"),
        P("notices", "array", "门面作为告知收下的最近结果报告 {message_id, author_id, kept_at, reason, text}；按需写入门面摘要，供成员查阅"),
        R("source_versions", "object", "来源版本：名册 roster、各成员摘要与门面收下的告知 notices 的指纹；提交时原样交回"),
        R("previous_summary", "object", "门面当前的工作摘要"),
        R("consolidation_due", "array", "达到时间、轮次或容量条件的成员及内部角色；按需读取其维护视图"),
        R("facade_needs_update", "boolean", "门面来源是否变化"),
        P("notification", "object", "overview_only 时返回可直接交给 DeliverMessage 的短通知；同材料使用相同消息 ID"),
        P("notification_delivery", "object", "概览有待办且已确定 Steward 时返回 {resident_id,status}。"
          "unsubmitted 表示暂无受理记录，accepted 表示本门面的同批任务已受理，conflict 表示身份或受理证据冲突。"),
        R("needs_update", "boolean", "来源版本与门面摘要上次保存的不同时为 true")),
       mutates=False),
)
