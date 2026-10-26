"""Resident instructions (decision_d19b9909a674), executed by SocietyService."""
from __future__ import annotations

from management_portal import prompt_injection
from management_portal.execution.provider_catalog import AGENT_PROVIDERS

from ..declarations import FIXED, ON_LINEAGE, PLACEMENT, ON_RESIDENT, InstructionDeclaration as D, On, P, R


def _d(name, decides, description, parameters=(), result=(), *, mutates=False, target=""):
    return D(name=name, category="resident", owner="lineage", description=description,
             parameters=tuple(parameters), result=tuple(result), mutates=mutates, target=target,
             decides=decides)


# Every registered agent Backend; registering a Backend adds it here.
PROVIDERS = tuple(AGENT_PROVIDERS)
FORK_MODES = ("full", "compacted", "recent-5", "recent-10", "message")
# A Resident's prompt injection (decision_ef1d6c925955).
PROMPT_INJECTION_PARTS = {
    name: {"type": "string", "enum": list(choices), "description": f"{label}：{description}"}
    for name, (label, description, choices) in prompt_injection.CATEGORIES.items()
}
SETTINGS_FIELDS = {
    "model": {"type": "string"},
    "backend_route_id": {"type": "string", "description": "仅 Model API：切换登记的 API 账号，从下一轮生效"},
    "reasoning_effort": {"type": "string"},
    "service_tier": {"type": "string"},
    "permission_preset": {"type": "string"},
    "prompt_injection": {
        "type": "object",
        "description": "Prompt 注入：选一个预设，或改单个类别的时机（在所给预设或当前设置上修改，结果记为自定义；"
                       "与某个预设完全相同时记为该预设）。下一轮生效",
        "properties": {
            "preset": {"type": "string", "enum": list(prompt_injection.PRESET_LABELS), "description": "预设"},
            "parts": {"type": "object", "description": "要改的类别及其时机", "properties": PROMPT_INJECTION_PARTS},
        },
    },
    "allow_subagents": {"type": "boolean"},
    "agent_harness": {"type": "object"},
    "collaboration_mode": {"type": "string", "enum": ["default", "plan"]},
    "prompt_context": {"type": "object", "description": "业务系统提示及预置上下文；system_prompt/messages；Model API 可设置 history: {enabled, max_tokens}，未知字段拒绝"},
    "backend_options": {"type": "object", "description": "后端专用参数；按所选后端的契约严格校验"},
}
PROMPT_INJECTION_VIEW = R(
    "prompt_injection", "object",
    "Prompt 注入的读法：preset 当前预设（custom 为自定义）、preset_label、presets 可选预设 {id, label}、"
    "parts 每个类别 {id, label, description, choice, choices, placement}；placement 由 Provider 决定",
)
# A new Resident joins a Family only where it is made (decision_44cf587c1721).
FAMILY_TO_JOIN = P(
    "family_id",
    description="新 Resident 加入的 Family；在展开的 Family 中新建时填写。省略时，Family 成员或 steward 新建的 "
                "Resident 加入它们所在的 Family，其他调用者新建的不加入任何 Family",
)
FAMILY_JOINED = P("family_id", description="新 Resident 加入的 Family；没有加入时省略")
CREATED = (
    R("resident_id", description="新 Resident ID"),
    R("lineage_id", description="所在 Lineage"),
    FAMILY_JOINED,
    P("backend_role_fallback", "object", "角色未映射时的 requested 与 used"),
)

DECLARATIONS = (
    _d("CreateResident", ON_LINEAGE, "创建一个新的 Resident 身份，并把它作为节点加入指定 Lineage。", (
        R("lineage_id", description="新 Resident 加入的 Lineage"),
        R("title", description="名称"),
        P("provider", description="执行后端；provider_api 使用 account_id 指定已登记的模型 API", enum=(*PROVIDERS, "provider_api")),
        P("account_id", description="账号路线；省略时 Resident 调用者继承自己的账号，其他调用者使用系统默认账号"),
        P("backend_role", description="创建时解析的 Backend 角色；未映射时回退 default"),
        P("settings", "object", "初始运行设置", properties=SETTINGS_FIELDS),
        P("workspace_paths", "array", "额外绑定的工作目录", items={"type": "string"}),
        FAMILY_TO_JOIN,
        *PLACEMENT,
    ), CREATED, mutates=True, target="lineage_id"),
    _d("ForkResident", (On("lineage_id", "lineage"), On("source_resident_id", "resident"),
                        On("context_source_resident_id", "resident", optional=True)), "从来源 Resident Fork 出一个新 Resident，新 Resident 按选定方式继承来源的上下文。", (R("lineage_id", description="对象当前所属的源 Lineage"),
        R("source_resident_id", description="被 Fork 的来源 Resident"),
        P("fork_mode", description="上下文继承方式，默认 full", enum=FORK_MODES),
        P("context_source_resident_id", description="上下文来源，默认与 source_resident_id 相同"),
        P("message", "object", "fork_mode=message 时的历史截止点 {message_id}",
          properties={"message_id": {"type": "string"}}),
        P("title", description="新 Resident 名称"),
        P("provider", description="目标 Provider；省略时继承上下文来源的 Provider", enum=(*PROVIDERS, "provider_api")),
        P("account_id", description="目标 Provider 的账号；同 Provider 默认继承来源账号，切换原生 Provider 使用目标默认账号，切换到 Model API 须指定账号"),
        FAMILY_TO_JOIN,
        *PLACEMENT,
    ), (
        R("resident_id", description="新 Resident ID"),
        R("lineage_id", description="新 Resident 所在的 Lineage"),
        R("inheritance_edge_id", description="记录来源的派生关系 ID"),
        FAMILY_JOINED,
    ), mutates=True, target="source_resident_id"),
    _d("PromoteSeed", ON_LINEAGE, "以 Seed 的候选 Fork 来源为基础 Fork 出一个待命的 Resident，候选 Fork 随之变成继承关系，并把 Seed 标记为已采纳；"
       "不发送任务，调用方随后用 DeliverMessage 把候选与用途交给新 Resident。", (R("lineage_id", description="对象当前所属的源 Lineage"),
        R("seed_id", description="要采纳的 Seed 节点"),
        P("title", description="默认使用 Seed 标题"),
        P("purpose", description="这次采纳的用途"),
    ), (
        R("resident_id", description="新 Resident ID"),
        R("inheritance_edge_id", description="新 Resident 与候选 Fork 来源之间的继承关系 ID"),
        P("candidate_path", description="候选 Markdown 的绝对路径；文件不存在时为空"),
    ), mutates=True, target="seed_id"),
    _d("GetResident", ON_RESIDENT, "读取 Resident 的基本信息。", (R("resident_id", description="要读取的 Resident"),), (
        R("resident", "object", "名称、所属 Lineage、Provider、头像、自有工作区与其他绑定的摘要"),
    ), target="resident_id"),
    _d("SearchResidents", FIXED, "按关键词查找调用者可以看到的 Resident，并说明调用者现在能否给它发消息。", (
        P("q", description="关键词：匹配 resident_id、名称或所在 Lineage 名称；省略时最近工作过的在前"),
        P("resident_ids", "array", "只在这些 Resident 中查找，结果按给出的顺序", items={"type": "string"}),
        P("can_send", "boolean", "只返回调用者现在能发消息的 Resident"),
        P("limit", "integer", "返回数量上限，默认 20", minimum=1, maximum=50),
    ), (
        R("residents", "array", "每项给出 resident_id、名称、所在 Lineage、can_send，以及不能发送时的原因 disabled_reason"),
    )),
    _d("UpdateResident", ON_LINEAGE, "修改 Resident 名称或头像；改名同步它在 Lineage 中节点的显示名称。title 与 avatar 至少提供一项。", (R("lineage_id", description="对象当前所属的源 Lineage"),
        R("resident_id", description="要修改的 Resident"), P("title", description="新名称"),
        P("avatar", "object", "{sprite_index: 0–41} 选择海洋头像，或 {upload_id} 使用 CreateUpload purpose=avatar 的已完成上传"),
    ), (
        R("resident", "object", "修改后的 Resident 基本信息，与 GetResident 的 resident 相同"),
    ), mutates=True, target="resident_id"),
    _d("DeleteResident", (On("lineage_id", "lineage"), On("resident_id", "resident")), "将 Resident 标记为已删除，永久保留同一身份及历史；释放模型会话，移除活动成员关系和 Lineage 引用，并删除文件夹（含自有工作区）。不提供恢复入口。", (R("lineage_id", description="对象当前所属的源 Lineage"),
        R("resident_id", description="要删除的 Resident"),
    ), (
        R("resident_id", description="被删除的 Resident ID"),
        P("new_leader_resident_id", description="删除 Leader 时随机选出的新 Leader"),
    ), mutates=True, target="resident_id"),
    _d("GetResidentSummaries", (On("resident_ids", "resident"),), "批量读取 Resident 的累计摘要，供搜索、路由、工作查看和摘要维护共用。", (
        R("resident_ids", "array", "最多 128 个 Resident ID", items={"type": "string"}),
        P("fields", "array", "只返回 summary 中的这些字段；列出 pending_requests 时才带待处理请求", items={"type": "string"}),
        P("include_maintenance", "boolean", "同时返回维护摘要所需的内容版本 revision、汇总来源 source_versions、"
          "漏写轮次 missing_message_ids 与维护者 writer_id；尚无摘要的可见 Resident 也返回一项（revision 为 0），"
          "另有 layers（recent/phase/long_term）、maintenance（时间/轮次水位）、consolidation（due/level/总量/policy）。UpdateResidentWorkSummary 的 expected_revision 取这里的 revision"),
    ), (
        R("summaries", "array", "每项包含 resident_id、title、lineage_id、summary（当前重点、近期成果、负责范围、"
          "适合与不适合接收的任务等）、pending_requests、summary_updated_at 与 summary_stale；"
          "include_maintenance 时另有 revision、source_versions、missing_message_ids 与 writer_id"),
        R("unavailable_resident_ids", "array", "调用者看不到或尚无摘要的 Resident"),
    )),
    _d("RequestResidentSummaryRebuild", ON_RESIDENT, "请求完整重建独立 Resident 的累计摘要，由 Portal 职责 resident-summary 的承担者重写；Family 成员与门面使用各自维护者，不能全局重建。", (
        R("resident_id", description="要重建摘要的独立 Resident"), P("reason", description="重建原因，例如职责调整"),
    ), (
        R("resident_id", description="请求重建摘要的 Resident"), R("status", description="请求结果"),
    ), mutates=True, target="resident_id"),
    _d("GetResidentSummaryQueue", FIXED, "读取独立 Resident 摘要队列中各状态的份数，不含摘要与证据；定时入口据此决定"
       "是否唤醒 Portal 职责 resident-summary 的承担者。", (), (
        R("ready", "integer", "证据已整理好、可以认领的份数"),
        R("waiting", "integer", "还在等待读取对话的份数"),
        R("claimed", "integer", "已被认领、尚未提交的份数"),
        R("parked", "integer", "多次失败后搁置、等对话再变化的份数"),
    )),
    _d("ClaimResidentSummaryJobs", FIXED, "认领待写的独立 Resident 累计摘要（仅限 Portal 职责 resident-summary 的承担者）；Family 内部和门面不入队。"
       "每份认领有 30 分钟租约，到期未提交会退回队列。认领结果直接带上写作说明和每份的证据。", (
        P("limit", "integer", "最多认领几份；省略时认领全部可认领的份数。证据总量过大时会少给", minimum=1),
    ), (
        R("instructions", description="摘要写作规则，照此写作"),
        R("output_fields", "array", "提交的 summary 必须包含的字段"),
        R("summary_policy", "object", "所有语义字段共用 total_chars 字符总预算；归纳重写，不累积细碎过程"),
        R("jobs", "array", "每项包含 claim_id、resident_id、title、lineage_id、mode（full 或 incremental）、reason、"
          "attempt、evidence_chars、lease_until，以及证据 input：mode、resident（元数据）、"
          "previous_summary（增量时的上一版）与 new_or_rebuild_context（已完成回合的对话证据）"),
        R("remaining_ready", "integer", "认领后仍可认领的份数"),
        R("waiting", "integer", "还在等待读取对话的份数"),
    ), mutates=True),
    _d("SubmitResidentSummary", FIXED, "提交一份已认领的独立 Resident 累计摘要（仅限认领者），与工作摘要共用内容和版本。Portal 记录提交者当前的模型与账号。", (
        R("claim_id", description="ClaimResidentSummaryJobs 返回的认领 ID"),
        R("summary", "object", "current_focus 与 branch_delta 为字符串；active_threads、durable_expertise、recent_results、"
          "primary_ownership、supporting_expertise、route_when、avoid_when 为短字符串数组"),
    ), (
        R("resident_id", description="摘要所属的 Resident"),
        R("mode", description="这份认领的写法：full 或 incremental"),
        R("generation", "integer", "该 Resident 摘要的新代数"),
        R("status", description="recorded"),
    ), mutates=True, target="claim_id"),
    _d("ReleaseResidentSummaryJob", FIXED, "放弃一份已认领的摘要并说明原因（仅限认领者），例如证据不足以写出摘要；"
       "它稍后重试，多次失败后搁置到对话再变化。", (
        R("claim_id", description="ClaimResidentSummaryJobs 返回的认领 ID"), R("reason", description="放弃原因"),
    ), (
        R("resident_id", description="被放弃的摘要所属的 Resident"), R("status", description="retry 或 parked"),
        R("attempts", "integer", "这份摘要累计被认领的次数，含本次"),
    ), mutates=True, target="claim_id"),
    _d("UpdateResidentWorkSummary", FIXED, "本人替换近期事实，本 Family Steward 归纳阶段与长期层或汇总门面；所有层和字段共用总预算。省略 summary 表示仅登记覆盖轮次。", (
        R("resident_id", description="摘要所属的 Resident：本人，或 Steward 汇总的本 Family 门面"),
        R("expected_revision", "integer", "GetResidentSummaries（include_maintenance）返回的 revision"),
        P("summary", "object", "供工作查看、搜索和路由共用的九个语义字段"),
        P("message_ids", "array", "最多 128 个本人正在运行或已完成、被摘要覆盖的输入 ID", items={"type": "string"}),
        P("source_versions", "object", "门面汇总保存 GetFamilySummaryInputs 的来源版本"),
        P("consolidation", "object", "仅本 Family Steward：phase 为替换后的九字段阶段摘要，long_term 可选；消费近期层。resolved_threads 可选，为原未决事项到解决/取消/交接说明的映射，其余未决事项自动保留。不能同时提交 summary/message_ids/source_versions；读取 include_maintenance 的 layers、revision、consolidation.policy 后提交"),
    ), (R("resident_id", description="摘要所属的 Resident"),
        R("summary", "object", "保存后的工作摘要（九个语义字段）"),
        R("revision", "integer", "工作摘要的内容版本；内容有变化时加一，下次提交作 expected_revision"),
        R("source_versions", "object", "保存的门面汇总来源版本；成员本人的摘要为空对象"),
        R("missing_message_ids", "array", "仍未被摘要覆盖的已登记轮次"),
        R("updated_at", description="摘要内容最近一次变化的时间"),
        R("writer_id", description="最近写入摘要内容的身份")), mutates=True, target="resident_id"),
    _d("GetResidentSettings", ON_RESIDENT, "读取 Resident 当前的运行设置，以及 Prompt 注入每个类别的时机和它在当前 Provider 中的位置；"
       "Family 门面不运行，读到的是它的 Router Resident 的设置。", (R("resident_id", description="要读取设置的 Resident"),), (
        R("settings", "object", "当前运行设置"),
        PROMPT_INJECTION_VIEW,
    ), target="resident_id"),
    _d("UpdateResidentSettings", ON_RESIDENT, "修改 Resident 的运行设置；对 Family 门面修改的是它的 Router Resident 的设置。", (
        R("resident_id", description="要修改设置的 Resident"),
        R("changes", "object", "只接受 model、reasoning_effort、service_tier、permission_preset、"
          "prompt_injection、allow_subagents、agent_harness、collaboration_mode、prompt_context、backend_options、backend_route_id（仅 Model API）",
          properties=SETTINGS_FIELDS),
    ), (
        R("settings", "object", "保存后的设置"),
        R("next_turn_fields", "array", "从下一轮才生效的字段"),
        PROMPT_INJECTION_VIEW,
    ), mutates=True, target="resident_id"),
    _d("PreviewPromptInjection", ON_RESIDENT, "预览 Resident 在某种轮次实际会带上的 Prompt 注入内容：每个部分是否发送、位置、字数和正文，"
       "以及拼好的系统提示和本轮消息（消息正文用占位文字）。", (
        R("resident_id", description="要预览的 Resident"),
        P("turn", description="first 为会话第一轮（默认），later 为之后的轮次，after_compaction 为上下文压缩后的第一轮",
          enum=("first", "later", "after_compaction")),
    ), (
        R("provider", description="Resident 当前的执行后端"), R("turn", description="预览的轮次"),
        R("preset", description="当前 Prompt 注入预设；custom 为自定义"),
        R("parts", "array", "每个注入部分 {id, label, timing, placement, sent, chars, content}"),
        R("system_prompt", description="这一轮 Provider 系统位置收到的 Portal 内容"),
        R("prompt", description="这一轮发给模型的消息"),
    ), target="resident_id"),
    _d("GetResidentInitialization", ON_RESIDENT, "读取创建及首轮前压缩状态；不读取输入正文。", (
        R("resident_id", description="要查看初始化状态的 Resident"),
    ), (
        R("resident_id", description="被查看的 Resident"),
        R("initialization_id", description="初始化尝试的 ID；没有恢复记录时为创建任务的执行 ID"),
        R("status", description="completed、running、failed、pending；无需首轮压缩时为 not_required"),
        R("phase", description="当前阶段，例如 prepare、complete；没有恢复记录时为 initialization"),
        R("error", description="status 为 failed 时的原因，其他状态为空"),
        R("queued_count", "integer", "等待初始化完成后执行的输入数"),
        R("source_session", "object", "继承的来源会话：provider、backend_route_id、backend_session_id、provider_session_ref、binding_id"),
        R("candidate_agent_id", description="恢复所用候选会话的 agent ID；没有时为空"),
        R("candidate_binding_id", description="候选会话的绑定 ID；没有时为空"),
        R("execution_ids", "object", "各步骤的执行 ID，例如 {create, compact}"),
    ), target="resident_id"),
    _d("RetryResidentInitialization", ON_RESIDENT, "恢复创建或首轮前压缩；复用在途工作，未知原生结果使用独立候选会话，不重发业务输入。", (
        R("resident_id", description="初始化未完成、要恢复的 Resident"),
    ), (
        R("resident_id", description="被恢复的 Resident"),
        R("initialization_id", description="恢复后的初始化尝试 ID"),
        R("status", description="恢复后的状态：completed、running、failed、pending 或 not_required"),
        R("phase", description="恢复后的当前阶段"),
        R("candidate_agent_id", description="恢复所用候选会话的 agent ID；没有时为空"),
        R("candidate_binding_id", description="候选会话的绑定 ID；没有时为空"),
    ), mutates=True, target="resident_id"),
    _d("RestartSession", ON_RESIDENT, "切换 Resident 后端、账号与上下文；原生后端建立新会话，Model API 保存直连配置。", (
        R("resident_id", description="要切换的 Resident"),
        P("provider", description="目标 Provider；省略时保持当前 Provider", enum=(*PROVIDERS, "provider_api")),
        P("account_id", description="目标 Provider 的账号"),
        P("model", description="目标账号支持的模型；省略时按目标账号配置选择"),
        P("reasoning_effort", description="目标模型支持的推理强度"),
        P("collaboration_mode", description="目标原生会话的协作模式；切换成功后生效", enum=("default", "plan")),
        P("service_tier", description="目标模型的服务等级；空字符串使用默认等级"),
        P("permission_preset", description="目标 Provider 的权限预设；切换成功后生效"),
        P("prompt_context", "object", "仅 Model API：固定上下文和 history 设置"),
        P("backend_options", "object", "仅 Model API：协议参数、超时和重试"),
        P("context_strategy", description="上下文迁移方式，默认 smart（能原生 Fork 时 native，否则 compact）；"
          "compact 精简交接，visible 交接更多可见对话，native 原生 Fork，fresh 全新上下文",
          enum=("smart", "native", "compact", "visible", "fresh")),
        P("active_execution_policy", description="有正在进行的轮次时：wait 等本轮结束再切换（默认），cancel 先取消它",
          enum=("wait", "cancel")),
        P("resume_failed_input", "boolean", "切换 Provider 或账号成功后恢复最近的失败输入，默认 true"),
    ), (
        R("restart_id", description="这次重启的 ID，供 GetSessionRestart、CancelSessionRestart 使用"),
        R("status", description="当前进度"),
    ), mutates=True, target="resident_id"),
    _d("GetSessionRestart", ON_RESIDENT, "读取 Resident 最近一次会话重启的进度。", (R("resident_id", description="要查看的 Resident"),), (
        P("restart_id", description="最近一次重启的 ID；从未重启时为空"),
        R("status", description="accepted、cancelling、completed、failed 或 cancelled；从未重启时为 none"),
        P("source_session", "object", "原会话：provider、account_id、has_session、backend_session_id 等"),
        P("target_session", "object", "目标 {provider, account_id}；从未重启时为空"),
    ), target="resident_id"),
    _d("CancelSessionRestart", ON_RESIDENT, "取消一次尚未完成的会话重启，保留原会话。", (
        R("resident_id", description="正在重启会话的 Resident"),
        R("restart_id", description="要取消的重启，取 RestartSession 返回的 restart_id"),
    ), (R("restart_id", description="被取消的重启"),
        R("status", description="cancelling 正在取消，或 cancelled 已取消"),), mutates=True, target="resident_id"),
    _d("BindWorkspace", ON_RESIDENT, "为 Resident 绑定一个已存在的工作目录。", (
        R("resident_id", description="要绑定目录的 Resident"),
        R("path", description="已存在的绝对路径", max_length=4096),
        P("purpose", description="目录用于什么工作"),
    ), (
        R("binding", "object", "绑定记录，包括工作区 ID 和规范路径"),
    ), mutates=True, target="resident_id"),
    D(name="UnbindWorkspace", category="resident", owner="lineage", decides=ON_RESIDENT,
      description="解除 Resident 与一个工作目录之间的绑定。",
      parameters=(R("resident_id", description="要解除绑定的 Resident"), R("workspace_id", description="要解除绑定的 Workspace")),
      result=(R("bindings", "array", "解除后的绑定列表"),),
      mutates=True, target="resident_id"),
)
