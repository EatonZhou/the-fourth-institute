"""Lineage run instructions (decision_68215b3a825b)."""
from __future__ import annotations

from ..declarations import CURSOR, NEXT_CURSOR, ON_LINEAGE, InstructionDeclaration as D, P, R


def _d(name, description, parameters=(), result=(), *, mutates=False, streaming=False, one_of=()):
    return D(name=name, category="lineage_run", owner="lineage", description=description,
             parameters=tuple(parameters), result=tuple(result), mutates=mutates,
             streaming=streaming, target="lineage_id", one_of=one_of, decides=ON_LINEAGE)


LIMIT = P("limit", "integer", "每页最多的条数，默认 50", minimum=1, maximum=200)
# Event input shares the composed message's delivery budget (decision_4adcc1ff2f2c).
INPUT_MAX_CHARS = 1_000_000
CONTENT_PARTS = P("content_parts", "array", "图片、文件或上下文引用，最多 32 项", max_length=32,
                  items={"type": "object"})
RUNTIME = R("runtime", "object", "组件运行状态")
PENDING = (R("lineage_id", description="连线所在的 Lineage"), R("wire_id", description="调用所在的 Invoke 连线"),
           R("delivery_id", description="待投递调用的 ID"))

DECLARATIONS = (
    _d("ResolveEffectActivation", "由 Lineage admin 人工终结结果未知的 Effect 激活，记录处置人与结果；不重试脚本或补造调用结果。", (
        R("lineage_id", description="Effect 所在的 Lineage"), R("activation_key", description="GetTriggerPath 返回的 Effect 激活键"),
        R("status", description="处置结果：成功或失败；已终结的激活不能改成另一结果", enum=("succeeded", "failed")), P("note", description="处置说明", max_length=4000),
    ), (R("activation", "object", "终态记录，包含 disposition 处置人、结果、说明和时间"),), mutates=True),
    _d("TriggerEvent", "手动触发一个 manual.triggered 事件源，并带上本次运行的输入；"
        "其他类型的 Event 由运行时根据真实事实产生，不能手动触发。", (
        R("lineage_id", description="事件源所在的 Lineage"),
        P("event_source_id", description="要触发的 manual.triggered 事件源"),
        P("wire_id", description="只经这条 Route 投递；与事件源同时提供时须属于它"),
        P("prompt", description="本次输入，与组合后的消息共享最多 1000000 字符的投递预算", max_length=INPUT_MAX_CHARS),
        CONTENT_PARTS,
        P("event_name", description="写入 Event 的名称，供接收方区分同一事件源的不同用途"),
        P("subject_resident_id", description="这次 Event 涉及的 Resident"),
        P("payload", "object", "随 Event 送出的结构化数据"),
    ), (R("event_id", description="写入的 Event ID"), R("trigger_path_id", description="这次 Event 开始的触发流程"),
        R("delivery", "object", "本次投递摘要")),
       mutates=True, one_of=(("event_source_id",), ("wire_id",))),
    _d("GetComponentRuntime", "读取 Lineage 中事件源、Effect 与循环组件的运行状态。", (
        R("lineage_id", description="要读取的 Lineage"), P("component_id", description="省略时读取全部事件源、Effect 与循环组件"),
    ), (R("components", "array", "每个组件的运行状态"),)),
    _d("PauseComponent", "关闭或打开事件源与 Effect，暂停或恢复循环组件；关闭状态持久保存，关闭期间禁止新的触发或指令执行。", (
        R("lineage_id", description="组件所在的 Lineage"), R("component_id", description="事件源、Effect 或循环组件"),
        R("paused", "boolean", "true 关闭或暂停，false 打开或恢复"),
    ), (RUNTIME,), mutates=True),
    _d("ResetComponentRuntime", "清零运行计数：事件源的已触发次数、循环组件的已进行轮数，或指向 Resident 的 Route 已发送消息次数。"
        "清零 Route 时保留上限和投递去重记录，不补发已丢弃的消息。", (
        R("lineage_id", description="组件所在的 Lineage"), P("component_id", description="事件源或循环组件"),
        P("wire_id", description="指向 Resident 的 Route"),
    ), (
        R("runtime", "object", "组件的运行状态；Route 为发送计数、上限、剩余次数和运行代次"),
        R("generation", "integer", "新的运行代次"),
    ), mutates=True, one_of=(("component_id",), ("wire_id",))),
    _d("ListTriggerPaths", "列出从独立 Event 或 Effect 开始、在 Resident 处终止的触发流程。", (
        R("lineage_id", description="要读取的 Lineage"), P("resident_id", description="仅包含到达该 Resident 的流程"), CURSOR(), LIMIT,
    ), (R("paths", "array", "流程记录，从新到旧：trigger_path_id、root_kind、cause_kind、status、occurred_at、input…"),
        R("total", "integer", "符合条件的流程总数"), NEXT_CURSOR())),
    _d("GetTriggerPath", "读取触发原因、图步骤、因果边和关联的 Execution；执行内容仍由 Agent 保存。"
       "steps、edges、execution_links 与 executions 各是一页 items，还有后续时各带 next_offset；offset 对四个列表同时生效。", (
        R("lineage_id", description="流程所在的 Lineage"), R("trigger_path_id", description="要读取的触发流程"),
        P("artifact_activation_key", description="同时读取这个 Effect 激活的产物，放入 artifact_output"),
        P("offset", "integer", "四个列表都从第几项读起，默认 0；按位置读取，取各列表 next_offset 中最小的继续", minimum=0), LIMIT,
    ), (R("path", "object", "流程记录：trigger_path_id、root_kind、cause_kind、input、status、occurred_at、finished_at、cursor…"),
        R("steps", "object", "图步骤：items（step_id、node_id、kind、activation_key、status、checkpoint、result…）与 next_offset"),
        R("edges", "object", "因果边：items（source_step_id、target_step_id、wire_id）与 next_offset"),
        R("execution_links", "object", "步骤与 Execution 的关联：items（step_id、execution_id）与 next_offset"),
        R("executions", "object", "关联的 Execution：items（execution_id、agent_id、status、起止时间、final_message_id）与 next_offset"),
        P("artifact_output", "object", "给 artifact_activation_key 时：这个 Effect 激活的产物"))),
    _d("WatchTriggerPath", "订阅流程当前进展；流程终结后结束，不追踪 Resident 后续产生的新流程。", (
        R("lineage_id", description="流程所在的 Lineage"), R("trigger_path_id", description="要订阅的触发流程"),
        P("after_cursor", "integer", "上次收到的 cursor，默认 0；只返回之后的进展", minimum=0),
    ), (R("change", "object", "一次进展：type 为 trigger_path.updated，其余同 GetTriggerPath 的结果（每个列表最多 200 项）"),
        R("cursor", "integer", "这次进展的游标，下次作为 after_cursor 交回")), streaming=True),
    _d("ListPendingDeliveries", "读取一条连线上正在等待投递的调用。", (
        R("lineage_id", description="连线所在的 Lineage"), R("wire_id", description="要读取的 Invoke 连线"),
    ), (
        R("deliveries", "array", "调用 ID、输入和修订号"),
    )),
    _d("UpdatePendingDelivery", "修改一条待投递调用的输入；传入空文本时恢复使用连线配置中的输入。", (
        *PENDING, P("prompt", description="新的输入；空文本恢复连线配置中的输入", max_length=INPUT_MAX_CHARS),
    ), (R("deliveries", "array", "修改后这条连线上仍在等待的调用，格式同 ListPendingDeliveries"),), mutates=True),
    _d("DeletePendingDelivery", "删除一条待投递调用。", PENDING,
       (R("deliveries", "array", "删除后这条连线上仍在等待的调用，格式同 ListPendingDeliveries"),), mutates=True),
)
