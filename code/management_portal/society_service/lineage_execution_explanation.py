from __future__ import annotations

from typing import Any, Iterable, Mapping

from management_common.limit_contract import normalize_optional_limit
from . import lineage_primitive_definitions, lineage_program_event_scope


SCHEMA = "tfi.hatg-execution-explanations.v1"


def _object(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _items(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _text(value: object, fallback: str = "") -> str:
    normalized = str(value or "").strip()
    return normalized or fallback


def _wire_kind(wire: Mapping[str, Any]) -> str:
    return _text(wire.get("kind")).lower()


def _component_config(component: Mapping[str, Any]) -> dict[str, Any]:
    config = component.get("config")
    return config if isinstance(config, dict) else {}


def _field_is_visible(
    field: Mapping[str, Any],
    config: Mapping[str, Any],
) -> bool:
    condition = field.get("visible_when")
    if not isinstance(condition, dict):
        return True
    path = _text(condition.get("path")).removeprefix("config.")
    return str(config.get(path, "")) == str(condition.get("equals", ""))


def _missing_required_config(
    kind: str,
    type_id: str,
    config: Mapping[str, Any],
) -> list[str]:
    missing: list[str] = []
    try:
        fields = lineage_primitive_definitions.fields_for(kind, type_id)
    except KeyError:
        return ["未知类型"]
    effective_config = {
        _text(field.get("path")).removeprefix("config."): field.get("default")
        for field in fields
        if (
            _text(field.get("path")).startswith("config.")
            and "default" in field
        )
    }
    effective_config.update(config)
    for field in fields:
        path = _text(field.get("path"))
        if not path.startswith("config.") or not field.get("required"):
            continue
        if not _field_is_visible(field, effective_config):
            continue
        key = path.removeprefix("config.")
        value = effective_config.get(key)
        if (
            value == ""
            or value == []
            or (
                value is None
                and _text(field.get("type")) != "json"
                and "default" not in field
            )
        ):
            missing.append(_text(field.get("label"), key))
    return missing


def _explanation(
    object_id: str,
    kind: str,
    state: str,
    summary: str,
    *,
    tone: str = "neutral",
    why: str = "",
    last_action: str = "",
    next_action: str = "",
    blocked_reason: str = "",
) -> dict[str, Any]:
    return {
        "object_id": object_id,
        "kind": kind,
        "state": state,
        "tone": tone,
        "summary": summary,
        "why": why,
        "last_action": last_action,
        "next_action": next_action,
        "blocked_reason": blocked_reason,
    }


def _event_explanation(
    component: Mapping[str, Any],
    wires: Iterable[Mapping[str, Any]],
    runtime: Mapping[str, Any],
) -> dict[str, Any]:
    component_id = _text(component.get("id"))
    config = _component_config(component)
    event_type = _text(
        config.get("event_type"),
        lineage_primitive_definitions.default_type("event"),
    ).lower()
    try:
        definition = lineage_primitive_definitions.type_definition(
            "event",
            event_type,
        )
    except KeyError:
        return _explanation(
            component_id,
            "event_source",
            "blocked",
            "Event 类型无法识别",
            tone="danger",
            blocked_reason=f"未知 Event 类型：{event_type}",
            next_action="选择一个已定义的 Event 类型。",
        )
    title = _text(definition.get("title"), event_type)
    watch_bindings = [
        wire for wire in wires
        if _wire_kind(wire) == "watch"
        and _text(wire.get("source")) == component_id
    ]
    outgoing = [
        wire for wire in wires
        if _wire_kind(wire) == "route"
        and _text(wire.get("source")) == component_id
    ]
    missing = _missing_required_config("event", event_type, config)
    if lineage_primitive_definitions.watch_required(event_type, config) and not watch_bindings:
        missing.append("Watch 检查对象")
    if missing:
        reason = "缺少：" + "、".join(dict.fromkeys(missing))
        return _explanation(
            component_id,
            "event_source",
            "blocked",
            f"{title}尚未配置完整",
            tone="danger",
            why=reason,
            blocked_reason=reason,
            next_action="补全配置或 Watch 连线后，Event 才会进入监听。",
        )
    maximum = normalize_optional_limit(
        runtime.get("max_fires", config.get("max_fires")),
        maximum=100_000,
    )
    count = max(0, int(runtime.get("occurrence_count") or 0))
    exhausted = bool(runtime.get("exhausted")) or (
        maximum is not None and count >= maximum
    )
    last_fired_at = _text(runtime.get("last_fired_at"))
    last_action = (
        f"最近一次触发于 {last_fired_at}。"
        if last_fired_at
        else "尚未触发。"
    )
    if runtime.get("paused"):
        # Paused from Home's Scheduled & Triggered panel (or any other pause):
        # the source keeps its configuration but produces no Event.
        return _explanation(
            component_id,
            "event_source",
            "paused",
            "已暂停，不会触发",
            tone="warning",
            why="这个 Event Source 已暂停；暂停期间不会产生事件。",
            last_action=last_action,
            next_action="恢复后从之后到达的事实开始继续监听。",
        )
    if exhausted:
        zero_copy = "触发上限为 0，因此不会产生事件。" if maximum == 0 else "触发次数已经达到上限。"
        return _explanation(
            component_id,
            "event_source",
            "completed",
            "触发次数已耗尽",
            tone="warning",
            why=zero_copy,
            last_action=last_action,
            next_action="重置触发次数，或修改总触发次数。",
        )
    if runtime.get("state") == "blocked":
        reason = _text(runtime.get("error"))
        return _explanation(
            component_id, "event_source", "blocked", "自动运行 Leader 不可用",
            tone="danger", why=reason, blocked_reason=reason,
            last_action=last_action,
            next_action="在 Lineage 中加入 Resident 或设置有效的 Leader；自动任务使用 Leader 自己的权限。",
        )
    if not outgoing:
        return _explanation(
            component_id,
            "event_source",
            "unrouted",
            f"正在监听{title}，但没有下游",
            tone="warning",
            why="Event 可以产生，但当前没有 Event 连线消费它。",
            last_action=last_action,
            next_action="从 Event 出口连接 Resident 或 Effect。",
        )
    wait_copy = {
        "manual.triggered": "等待用户手动触发",
        "time.reached": "等待设定时间到达",
        "residents.all_idle": "等待 Portal 全局全部 Resident 结束工作",
        "residents.all_finished": "等待同一 Run 的指定 Residents 全部成功完成",
        "resident.work_duration_exceeded": "等待 Resident 单轮工作超过时长阈值",
        "resident.queue_pressure_exceeded": "等待 Resident 队列压力越过阈值",
        "resident.message_received": "等待目标 Resident 收到新消息",
        "state.condition_met": "等待 Graph State 条件从不成立变为成立",
    }.get(event_type, f"正在监听{title}")
    resident_scope = _text(config.get("resident_scope"))
    if event_type in lineage_program_event_scope.SCOPED_RESIDENT_EVENT_TYPES and resident_scope in {"lineage", "system"}:
        wait_copy = {
            "lineage": f"正在监听本 Lineage 全部 Resident：{title}",
            "system": f"正在监听 Portal 全部 Resident：{title}",
        }[resident_scope]
    remaining = runtime.get("remaining_fires")
    limit_copy = (
        "不限次数"
        if maximum is None
        else f"还可触发 {max(0, int(remaining if remaining is not None else maximum - count))} 次"
    )
    return _explanation(
        component_id,
        "event_source",
        "waiting",
        wait_copy,
        tone="info",
        why=f"已连接 {len(outgoing)} 条 Event 路径；{limit_copy}。",
        last_action=last_action,
        next_action=f"事实到达后会立即沿 {len(outgoing)} 条路径投递。",
    )


def _effect_explanation(
    component: Mapping[str, Any],
    wires: Iterable[Mapping[str, Any]],
) -> dict[str, Any]:
    """An Effect runs its script; it is ready once its endpoints are bound."""

    from .lineage_effect_script import script_label

    component_id = _text(component.get("id"))
    config = _component_config(component)
    script = str(config.get("script") or "")
    instruction = script_label(script)
    if not instruction:
        return _explanation(
            component_id,
            "effect",
            "blocked",
            "Effect 尚未编写可运行的脚本" if script.strip() else "Effect 尚未编写脚本",
            tone="danger",
            blocked_reason="Effect 的脚本没有可调用的指令，或无法解析",
            next_action="在检查器中编写脚本；保存后查看该脚本的诊断。",
        )
    incoming = [
        wire for wire in wires
        if _wire_kind(wire) in {"route"}
        and _text(wire.get("target")) == component_id
    ]
    bound = {
        _text(wire.get("source_port"))
        for wire in wires
        if _wire_kind(wire) == "target"
        and _text(wire.get("source")) == component_id
    }
    missing = [
        _text(name) for name in config.get("endpoints") or []
        if _text(name) and _text(name) not in bound
    ]
    if missing:
        reason = "缺少端点绑定：" + "、".join(dict.fromkeys(missing))
        return _explanation(
            component_id,
            "effect",
            "blocked",
            f"{instruction} 尚未配置完整",
            tone="danger",
            why=reason,
            blocked_reason=reason,
            next_action="用 Target 连线绑定每个端点。",
        )
    if not incoming:
        return _explanation(
            component_id,
            "effect",
            "waiting",
            f"{instruction} 正在等待 Invoke",
            tone="info",
            why="Target 只绑定资源；Event 或 Packet 到达才会运行脚本。",
            next_action="连接一个 Event 或 Packet 到 Invoke 入口。",
        )
    return _explanation(
        component_id,
        "effect",
        "ready",
        f"{instruction} 已就绪",
        tone="success",
        why=f"有 {len(incoming)} 条 Invoke 路径，端点已绑定。",
        next_action="任一上游 Event 或 Packet 到达时运行一次脚本。",
    )


def _resident_explanation(
    node: Mapping[str, Any],
    wires: Iterable[Mapping[str, Any]],
) -> dict[str, Any]:
    resident_id = _text(node.get("id"))
    incoming = [
        wire for wire in wires
        if _wire_kind(wire) in {"route"}
        and _text(wire.get("target")) == resident_id
    ]
    status = _text(node.get("status"), "idle").lower()
    if status in {"running", "queued"}:
        return _explanation(
            resident_id,
            "resident",
            status,
            "Resident 正在工作" if status == "running" else "工作已排队",
            tone="info",
            why="状态来自 Resident 执行层；HATG 只负责投递。",
            next_action="工作结束后会产生对应的 Resident 系统事件。",
        )
    return _explanation(
        resident_id,
        "resident",
        "waiting" if incoming else "idle",
        "等待 Invoke" if incoming else "当前没有 HATG 输入",
        tone="neutral",
        why=f"已连接 {len(incoming)} 条输入路径。" if incoming else "没有 Event 或 Packet 指向这个 Resident。",
        next_action="上游消息到达时开始工作。" if incoming else "连接 Event、Packet 或 Message Effect。",
    )


def _wire_explanation(
    wire: Mapping[str, Any],
    invoke_runtime: Mapping[str, Any],
) -> dict[str, Any]:
    wire_id = _text(wire.get("id"))
    kind = _wire_kind(wire)
    if kind in {"route"}:
        runtime = _object(invoke_runtime.get(wire_id))
        pending = max(0, int(runtime.get("pending_count") or 0))
        if pending:
            return _explanation(
                wire_id,
                f"{kind}_wire",
                "queued",
                f"{pending} 个 Invocation 正在等待",
                tone="warning",
                why="目标当前不能消费；队列行为由这条边的策略决定。",
                next_action="目标恢复可消费后，Portal 会按边策略继续投递。",
            )
        return _explanation(
            wire_id,
            f"{kind}_wire",
            "ready",
            "触发路径已就绪",
            tone="success",
            why="Activation 与 Arguments 会沿这条边形成一次 Invocation。",
            next_action="上游产生事实或 Packet 时立即投递。",
        )
    copies = {
        "watch": (
            "观察引用已绑定",
            "只声明 Event Source 要检查的对象，不触发目标。",
        ),
        "target": (
            "动作目标已绑定",
            "只为 Effect 选择资源；到达 Invoke 才执行动作。",
        ),
    }
    summary, why = copies.get(kind, ("关系已绑定", "这条边不直接启动工作。"))
    return _explanation(
        wire_id,
        f"{kind}_wire",
        "bound",
        summary,
        tone="neutral",
        why=why,
        next_action="等待拥有这条关系的节点使用它。",
    )


def explain_program(
    lineage: Mapping[str, Any],
    *,
    event_source_runtime: Mapping[str, Any] | None = None,
    invoke_wire_runtime: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Explain current HATG behavior without persisting execution history."""

    program = _object(lineage.get("program"))
    components = _items(program.get("components"))
    wires = _items(program.get("wires"))
    event_runtime = _object(event_source_runtime)
    invoke_runtime = _object(invoke_wire_runtime)
    nodes: dict[str, dict[str, Any]] = {}
    for component in components:
        component_id = _text(component.get("id"))
        if not component_id:
            continue
        if _text(component.get("kind")) == "event_source":
            nodes[component_id] = _event_explanation(
                component,
                wires,
                _object(event_runtime.get(component_id)),
            )
        elif _text(component.get("kind")) == "effect":
            nodes[component_id] = _effect_explanation(component, wires)
    for node in _items(lineage.get("nodes")):
        resident_id = _text(node.get("id"))
        if not resident_id:
            continue
        nodes[resident_id] = _resident_explanation(node, wires)
    return {
        "schema": SCHEMA,
        "nodes": nodes,
        "wires": {
            wire_id: _wire_explanation(wire, invoke_runtime)
            for wire in wires
            if (wire_id := _text(wire.get("id")))
        },
    }
