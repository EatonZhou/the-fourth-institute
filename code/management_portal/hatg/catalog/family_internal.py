"""Family internal steps (decision_44cf587c1721): the instructions a Family's Router,
steward and supervising Effects run to relay requests, keep summaries and recover
failures.  Callers outside a Family use the lifecycle instructions in ``family``."""
from ..declarations import FIXED, ON_LINEAGE, ON_RESIDENT, InstructionDeclaration as D, P, R


def _d(name, decides, description, parameters, result, *, mutates=True, target="lineage_id"):
    return D(name=name, category="family_internal", owner="lineage", description=description,
             parameters=tuple(parameters), result=tuple(result), mutates=mutates, target=target,
             decides=decides)


DECLARATIONS = (
    _d("ResolveFamilyRequest", ON_RESIDENT, "把本 Family Steward 已完成的路由复核成果归档到同一原失败请求；保留原失败回执，不重新执行或复制业务输入。限本 Steward 或门面管理者。", (
        R("resident_id", description="Family 门面"),
        R("message_id", description="门面内尚未转交的原失败请求；取消或正在执行的请求不能归档"),
        R("steward_message_id", description="同一 Family Steward 已完成且有真实回复的 route_review 输入，必须绑定该原请求"),
        R("reason", description="已完成组织成果满足原请求的依据，写入审计记录"),
    ), (R("status", description="archived；重复归档相同来源不产生新工作"),
        R("message_id", description="保持不变的门面原请求 ID"),
        R("steward_message_id", description="实际完成组织工作的复核输入 ID"),
        R("reply_message_ids", "array", "原请求可读取的真实 Steward 回复 ID")), target="resident_id"),
    _d("CheckFamilyRoleAvailability", ON_LINEAGE, "维护 Family Router 与 Steward 接单状态并返回已有迁移状态；不查询额度或发起迁移，无需模型轮次。门面关闭时暂停；额度检查由消息执行准入负责。", (
        R("lineage_id", description="Family 所在的 Lineage"),
        R("family_id", description="Family 资源 ID family_… 或 Function Block 键 fbi_…"),
    ), (R("family_id", description="Family Function Block 键"), R("paused", "boolean", "门面是否暂停"),
        R("waiting_count", "integer", "等待账号迁移的角色数"),
        P("roles", "array", "两个角色的 resident_id、status、reason、intake"))),
    _d("DispatchFamilyTask", ON_RESIDENT, "依照 Family 当前 Route 中的工作模板向 Router 或 Steward 派发精简任务；指南首轮注入，后续按路径读取。", (
        R("resident_id", description="Family 门面"),
        R("trigger", description="route、queue、review、summary、progress、recruit、route_review、recovery、quota 或 organization"),
        P("facts", "object", "本次任务事实和资源引用"),
        P("source_message_id", description="门面中的原消息；route 必须给出此参数或 facts.message_id"),
        P("message_id", description="可选稳定派发消息 ID，用于幂等"),
        P("event", "object", "触发 Event，只传身份及主题，不附全量历史"),
    ), (R("message_id", description="发给内部角色的任务消息"),), target="resident_id"),
    _d("RelayFamilyMessage", ON_RESIDENT, "按 Router 的选择转交门面原请求；成员周额度低于 5% 时先交 steward 复核；"
       "带 scope_gap 时请求不在现有成员职责内，先交 steward 决定是否新建成员，steward 安排后以 scope_reviewed=true 转交给所选成员。"
       "reassign=true 时把已交给其他成员的请求改派给所选成员，并记下这次纠正。", (
        R("resident_id", description="Family 门面"), R("message_id", description="门面中的原请求"),
        R("member_id", description="Router 选中的成员；带 scope_gap 时为最接近的成员；steward 安排时为它选定的成员（可以是刚新建的）；改派时为新成员；"
          "组织请求或引用 Steward 消息的回复可选择本 Family Steward，不能选择其他 Family 的角色"),
        P("quota_reviewed", "boolean", "仅本 Family steward 可设 true，表示已判断任务强度或完成迁移"),
        P("scope_gap", description="Router 判断没有成员的职责覆盖这条请求时写明缺少的职责；请求留在门面等 steward 安排"),
        P("scope_reviewed", "boolean", "本 Family steward 安排好成员（新建或最接近的成员）后设 true，转交等它安排的请求"),
        P("reassign", "boolean", "改派：撤回原成员尚未开始的副本，交给所选成员重新处理；原成员正在处理时先停止本轮。"
          "限原发送者、能管理门面的身份与本 Family steward"),
        P("reason", description="steward 放行或安排成员时必填，说明判断；改派时说明为什么原成员不合适；Router 转交时为它的理由"),
    ), (R("status", description="relayed、waiting_for_quota_review、waiting_for_steward、arranged、reassigned，"
          "或 withdrawn（请求已撤回，不再转交）"),
        P("message_id", description="已转交时为成员收到的消息；等待复核或安排时为门面原请求；withdrawn 时没有"),
        R("member_id", description="承接的成员；等待 steward 安排时为 Router 建议的成员"),
        P("steward_message_id", description="等待复核或安排时发给 steward 的消息"),
        P("previous_member_id", description="改派前承接的成员")),
       target="resident_id"),
    _d("CheckFamilyProgress", ON_LINEAGE, "登记门面收到的工作轮次，达到间隔后请 Steward 撰写 Family 进展简报；同一输入只计一次。", (
        R("lineage_id", description="Family 所在的 Lineage"),
        R("family_id", description="Family 资源 ID family_… 或 Function Block 键 fbi_…"),
        P("event", "object", "门面 resident.message_received 事件"),
        P("every_turns", "integer", "每几轮简报一次，默认 20；由 Family 内部对象的简报设置保存", minimum=1),
    ), (R("family_id", description="Family 的 Function Block 键 fbi_…"), R("count", "integer", "门面已登记的工作轮次总数"),
        R("requested_batch_ids", "array", "本次请 Steward 撰写简报的批次号"))),
    _d("PublishFamilyProgress", FIXED, "Steward 保存最多 500 字的 Family 简报并更新门面工作摘要，不向成员群发；Steward 按相关性另行定向通知，失败可用同批次同内容重试。", (
        R("lineage_id", description="Family 所在的 Lineage"),
        R("family_id", description="Family 资源 ID family_… 或 Function Block 键 fbi_…"),
        R("batch_id", description="简报请求中给出的批次号；更早的批次须先发布"),
        R("report", description="正文 1–500 字，标点、空格和换行均计数"),
        R("summary", "object", "九字段门面工作摘要，所有字段文字合计不超过 500 字"),
        R("expected_revision", "integer", "GetFamilySummaryInputs 返回的 expected_revision，原样交回；"
          "门面摘要已被改过时拒绝，需重新读取后提交"),
        R("source_versions", "object", "GetFamilySummaryInputs 的来源版本"),
    ), (R("family_id", description="Family 的 Function Block 键 fbi_…"), R("batch_id", description="已发布的简报批次号"),
        R("status", description="published"))),
    _d("CheckFamilyRecovery", ON_LINEAGE, "核验 Family 原失败任务及关闭成员的当前阻塞队列；交给 steward 恢复原输入，无法恢复时以门面身份通知实际真人创立者。关闭门面暂停自维护。", (
        R("lineage_id", description="Family 所在的 Lineage"),
        R("family_id", description="Family 资源 ID family_… 或 Function Block 键 fbi_…"),
        P("event", "object", "Family 监督 Effect 的触发 Event；省略时复查待恢复任务及关闭成员当前队列，不扫描历史失败输入"),
        P("escalate_reason", "string", "steward 确实无法继续修复时填写具体原因，升级现有未解决故障"),
    ), (R("family_id", description="Family 的 Function Block 键 fbi_…"),
        R("incidents", "array", "登记的故障：key、resident_id、message_id、request_message_id、status（open、waiting、"
          "escalated、resolved、cancelled 等）、attempts、observed（含 intake_state 与 blocked_reason）"),
        R("notice", "object", "最近交给 steward 的恢复消息：message_id、incident_keys、delivered；没有时为空对象"),
        P("paused", "boolean", "门面关闭或删除时为 true，暂停自维护且保留记录"))),
    _d("CheckResidentWorkSummary", FIXED, "Family Effect 检查完成轮次；遗漏挂下一轮提醒，已覆盖时返回是否达到归纳条件。", (
        R("resident_id", description="完成这一轮的 Family 成员"),
        R("message_id", description="要检查的已完成输入；未完成的轮次跳过"),
    ), (R("status", description="facade、skipped、covered、reminder_pending 或 reminder_attached"),
        P("consolidation_due", "boolean", "已覆盖时返回归纳条件是否满足，Effect 据此尝试短通知 Steward")), target="resident_id"),
    _d("GetFamilySummaryInputs", FIXED, "仅本 Family 门面和 Steward 读取成员摘要与来源版本，无变化时 needs_update=false。", (
        R("resident_id", description="Family 门面"),
        P("overview_only", "boolean", "只返回是否变化及待归纳 Resident/版本/层级，不返回摘要正文"),
    ), (R("resident_id", description="Family 门面"), R("title", description="Family 名称"), R("goal", description="Family 共同目标"),
        R("members", "array", "每个成员 {resident_id, title, summary, summary_stale}"),
        R("source_versions", "object", "来源版本：名册 roster 与各成员摘要的指纹；提交时原样交回"),
        R("previous_summary", "object", "门面当前的工作摘要"),
        R("expected_revision", "integer", "门面工作摘要的内容版本；提交时作 expected_revision"),
        R("consolidation_due", "array", "达到时间、轮次或容量条件的成员及内部角色；按需读取其维护视图"),
        R("facade_needs_update", "boolean", "门面来源是否变化"),
        P("notification", "object", "overview_only 时返回可直接交给 DeliverMessage 的短通知；同小时同材料使用相同消息 ID"),
        R("needs_update", "boolean", "来源版本与门面摘要上次保存的不同时为 true")),
       mutates=False, target="resident_id"),
)
