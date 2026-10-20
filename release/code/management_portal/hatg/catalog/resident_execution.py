"""Resident execution instructions (decision_2a3ff353165c); organising the waiting
messages is in the Mailbox category (``mailbox``)."""
from __future__ import annotations

from ..declarations import ON_RESIDENT, InstructionDeclaration as D, On, P, R


def _d(name, decides, description, parameters=(), result=(), *, mutates=False, target="", one_of=()):
    return D(name=name, category="resident_execution", owner="lineage", description=description,
             parameters=tuple(parameters), result=tuple(result), mutates=mutates,
             target=target, one_of=one_of, decides=decides)


INTAKE_TARGETS = (
    P("resident_id", description="要开关接单的单个 Resident"),
    P("resident_ids", "array", "批量开关的 Resident；按账号批量时取账号详情中的 open_resident_ids 或 closed_resident_ids",
      items={"type": "string"}),
)
INTAKE_RESULT = (
    P("previous", "object", "单个 Resident 时：变化前的状态"),
    P("current", "object", "单个 Resident 时：当前状态"),
    P("changed", "boolean", "单个 Resident 时：本次是否发生变化"),
    P("changed_resident_ids", "array", "批量时：发生变化的 Resident"),
    P("unchanged_resident_ids", "array", "批量时：本来就处于目标状态、未改变的 Resident"),
    P("failed", "array", "批量时：失败的 Resident 与原因"),
    P("changed_count", "integer", "批量时：发生变化的 Resident 数"),
    P("unchanged_count", "integer", "批量时：未改变的 Resident 数"),
    P("failed_count", "integer", "批量时：失败的 Resident 数"),
    P("quota_override_pending", "boolean", "批量时：人工额度放行设置是否还在等待同步（系统自动重试）"),
)
QUEUE_ITEM = (
    R("resident_id", description="消息所在的 Resident；可以是 Family 门面"),
    R("message_id", description="要查看或停止的消息"),
)

DECLARATIONS = (
    _d("ListResidentStates", (On("resident_ids", "resident"),), "批量读取 Resident 的工作状态、接单状态和当前调用者可用的操作；读一个 Resident 时传只含它的列表。", (
        R("resident_ids", "array", "要读取的 Resident；看不到的不返回", items={"type": "string"}),
        P("include_viewer_state", "boolean", "是否计算当前用户的会话未读状态；默认计算"),
    ), (R("states", "array", "每个可见 Resident 的状态与可用操作：work_state、work_phase、当前消息、等待数量、intake_state 与 controls"),)),
    _d("GetInputState", ON_RESIDENT, "读取一条用户消息的处理状态；Family 门面沿转交关系返回实际成员的状态和回复，转交本身不算任务完成。", (
        *QUEUE_ITEM,
        P("include_progress", "boolean", "同时返回未完成消息的进度：排队时前面还有几条（ahead），运行时的 work_state、work_phase 与这条消息的实时活动（activities）"),
        P("include_context", "boolean", "同时返回 in_context：这条消息是否会进入或仍在 Resident 当前的上下文中。等待和执行中的消息会进入；"
          "已处理的消息在清空上下文、换新会话或压缩之后不再在其中，Provider 没有接收的消息也不在其中"),
    ), (
        R("input_state", "object", "状态、回复消息 ID 列表、更新时间和错误；include_progress 时未完成的消息另有 progress，include_context 时另有 in_context"),
    ), target="resident_id"),
    _d("OpenIntake", (On("resident_id", "resident", optional=True), On("resident_ids", "resident", optional=True)), "打开或关闭 Resident 的接单开关。打开后等待中的消息按顺序开始执行；"
       "关闭后新消息只排队不执行，当前工作继续完成。", (
        *INTAKE_TARGETS,
        R("open", "boolean", "true 打开，false 关闭"),
    ), INTAKE_RESULT, mutates=True, target="resident_id",
       one_of=(("resident_id",), ("resident_ids",))),
    _d("CancelInput", ON_RESIDENT, "停止一条消息：正在执行的一轮请求停止，等待中的请求、群体或 info 从 Mailbox 撤回并标为 cancelled"
        "；回执停在 queued 或 running、却不在队列里、不是当前工作、也没有未结束 Execution 的消息"
        "（例如队列丢下的请求）同样标为 cancelled，错误码 lost_input。已经结束的消息保留原结果、不报错。"
        "目标是 Family 门面的消息时，作用于 Router 转交给成员的那条消息；还没转交时从门面撤回。"
        "停止受理不代表后台终态，通过输入状态与对话事件观察完成。", QUEUE_ITEM, (
        R("resident_id", description="状态所属的 Resident；门面转交后为实际工作的成员"),
        R("message_id", description="实际停止的消息；门面转交后为成员上对应的消息"),
        P("cursor", "integer", "停止后 Resident 的对话事件游标"),
        P("state", "object", "停止后 Resident 的工作状态"),
        P("controls", "object", "撤回等待消息时：调用者此后可用的操作"),
        R("input_state", "object", "这条消息此刻的状态；门面转交后为成员上对应消息的状态"),
        P("cancellation_requested", "boolean", "是否已请求停止正在执行的一轮"),
        P("backend_cancelled", "boolean", "后端是否已确认停下；目前总为 false，结果以 input_state 为准"),
        P("already_terminal", "boolean", "消息在调用前已经结束，没有任何改动"),
        P("facade_message_id", description="通过 Family 门面停止时，门面上的原消息"),
        P("member_resident_id", description="目标是 Family 门面时，实际停止工作的成员"),
        P("member_message_id", description="目标是 Family 门面时，成员上对应的消息"),
    ), mutates=True, target="resident_id"),
    _d("CancelSubagent", ON_RESIDENT, "停止 Resident 当前这一轮中正在运行的一个子 agent；这一轮继续，子 agent 卡片随后以已停止结束。只有声明 subagent_stop 能力的 Provider 支持。", (
        R("resident_id", description="正在运行这个子 agent 的 Resident"),
        R("execution_id", description="子 agent 运行中卡片所属的执行"),
        R("tool_use_id", description="子 agent 卡片上的 tool_use_id"),
    ), (
        R("control_id", description="这次停止请求的输入控制 ID"), R("status", description="pending、delivered 或 failed"),
        P("error", description="Provider 没有停下它时的原因"),
    ), mutates=True, target="resident_id"),
)
