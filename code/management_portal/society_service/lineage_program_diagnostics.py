"""Diagnostics of a compiled Program: errors that stop a Lineage from running and
warnings worth attention, including each Effect script's analysis.
"""

from __future__ import annotations

import re
from typing import Any, Iterable

from . import lineage_program_compilation
from .lineage_program_arguments import invocation_argument_output_reference_names
from .lineage_program_compilation import event_source_id_for_wire
from .lineage_program_event_scope import (
    RESIDENT_SUBJECT_EVENT_TYPES, event_source_resident_scope, event_source_subject_ids,
)
from .lineage_program_vocabulary import (
    EVENT_SOURCE_DELIVERY_OBSERVER_TYPES, EVENT_SOURCE_EFFECT_OBSERVER_TYPES,
    EVENT_SOURCE_HANDOFF_OBSERVER_TYPES, EVENT_SOURCE_OBSERVER_TYPES,
)


# Effects calling DeliverMessage may leave ``message`` unset: the runtime then
# composes the message from the activation's input (decision_4adcc1ff2f2c).
EFFECT_RUNTIME_SUPPLIED_ARGUMENTS = {"DeliverMessage": frozenset({"message"})}


def _diagnostic(code: str, item_id: str, message: str, level: str = "error") -> dict[str, str]:
    return {"code": code, "item_id": item_id, "level": level, "message": message}


def effect_script_analysis(effect: dict[str, Any]) -> Any:
    """Check one compiled Effect's script against the instruction catalog."""

    from management_portal.hatg.catalog import instruction_catalog

    from .lineage_effect_script import analyze
    from .lineage_program_effect_rules import validate_effect_argument

    catalog = instruction_catalog()
    return analyze(
        str(effect.get("script") or ""),
        declaration_for=catalog.get,
        declared_endpoints=effect.get("endpoint_names") or [],
        connected_endpoints=list((effect.get("endpoints") or {}).keys()),
        endpoint_targets=dict(effect.get("endpoints") or {}),
        runtime_supplied=EFFECT_RUNTIME_SUPPLIED_ARGUMENTS,
        validate_argument=validate_effect_argument,
    )


def _effect_diagnostics(effect_id: str, effect: dict[str, Any]) -> list[dict[str, str]]:
    return [
        _diagnostic(code, effect_id, f"第 {line} 行：{message}" if line else message)
        for code, message, line in effect_script_analysis(effect).diagnostics
    ]


# Payload fields of Event types whose producers are fixed.  An Effect argument
# that reads another ``event.payload`` field from such a source receives
# nothing: a TAU run mapped ``event.payload.prompt`` from a
# resident.work_finished Event and the second stage failed at delivery.
DISPATCH_PAYLOAD_FIELDS = frozenset({
    "trigger_path_id", "route_path", "event_source_id", "source_event_id", "runtime_event_depth", "wire_id",
})
EVENT_PAYLOAD_FIELDS = {
    "portal.release_published": frozenset({"sequence", "publication_id", "commit", "previous_commit", "completed_at", "source"}),
    "script.finished": frozenset({"script_run_id", "status", "exit_code", "outputs", "path", "duration_seconds"}),
    "resident.work_finished": frozenset({"message_id", "execution_id"}),
    "resident.work_failed": frozenset({"message_id", "execution_id"}),
    "resident.work_cancelled": frozenset({"message_id", "execution_id"}),
    "residents.all_finished": frozenset({"resident_ids", "resident_count"}),
}


EVENT_PAYLOAD_REFERENCE_RE = re.compile(r"\$\{event\.payload\.([A-Za-z0-9_-]+)")


def _binding_sources(binding: object) -> set[str]:
    """Reference paths an argument mapping reads (``from`` and nested ``concat``)."""

    if not isinstance(binding, dict):
        return set()
    sources = {str(binding.get("from") or "").lstrip("$")} - {""}
    for part in binding.get("concat") or []:
        sources |= _binding_sources(part)
    return sources


def _effect_payload_diagnostics(
    effect_id: str,
    effect: dict[str, Any],
    event_sources: dict[str, Any],
    wires: dict[str, dict[str, Any]],
    components: dict[str, dict[str, Any]],
) -> list[dict[str, str]]:
    """Arguments that read an Event payload field none of the Effect's Events carries."""

    event_types = set()
    for wire_id in effect.get("incoming_packet_wire_ids") or []:
        wire = wires.get(str(wire_id)) or {}
        source = event_sources.get(event_source_id_for_wire(wire, components)) or {}
        event_types.add(str(source.get("event_type") or ""))
    if not event_types or not event_types <= set(EVENT_PAYLOAD_FIELDS):
        return []
    available = DISPATCH_PAYLOAD_FIELDS.union(*(EVENT_PAYLOAD_FIELDS[name] for name in event_types))
    # Payload fields reach an Effect two ways: the script expands
    # ${event.payload.<field>}, or an activation wire maps one into arguments.
    readers: list[tuple[str, str]] = [
        ("脚本", f"event.payload.{match.group(1)}")
        for match in EVENT_PAYLOAD_REFERENCE_RE.finditer(str(effect.get("script") or ""))
    ]
    for wire_id in effect.get("incoming_packet_wire_ids") or []:
        wire = wires.get(str(wire_id)) or {}
        config = wire.get("config") if isinstance(wire.get("config"), dict) else {}
        profile = (config.get("invoke_profiles") or {}).get("effect") or {}
        for name, binding in sorted((profile.get("arguments") or {}).items()):
            if isinstance(binding, dict):
                readers.extend((f"参数 {name}", source) for source in _binding_sources(binding))
    result = []
    reported: set[tuple[str, str]] = set()
    for reader, source in readers:
        if not source.startswith("event.payload.") or (reader, source) in reported:
            continue
        reported.add((reader, source))
        field = source.split(".", 2)[2].split(".", 1)[0]
        if field not in available:
            result.append(_diagnostic(
                "effect.unavailable_event_payload", effect_id,
                f"{reader}读取 {source}，但上游 {'、'.join(sorted(event_types))} Event 不含 {field}；"
                f"可用字段：{'、'.join(sorted(available))}",
                "warning",
            ))
    return result


def _event_output_names(source: dict[str, Any], effects: dict[str, Any]) -> set[str] | None:
    """Outputs carried by a source's events: the fields an observed Effect's script outputs.

    ``None`` means the outputs cannot be known before the Lineage runs.
    """

    from .lineage_program_effect_rules import effect_failure_outputs

    event_type = str(source.get("event_type") or "")
    if event_type == "script.finished":
        return None
    if event_type not in EVENT_SOURCE_EFFECT_OBSERVER_TYPES:
        return set()
    if event_type.endswith("failed"):
        return set(effect_failure_outputs({}))
    effect = effects.get(str(source.get("subject_effect_id") or "")) or {}
    if not effect:
        return None
    return effect_script_analysis(effect).output_fields


def program_diagnostics(
    program: object,
    nodes: Iterable[dict[str, Any]],
    *,
    _compiled_plan: dict[str, Any] | None = None,
) -> list[dict[str, str]]:
    """Errors that stop a Lineage from running and warnings worth attention."""

    node_list = [node for node in nodes if isinstance(node, dict)]
    plan = _compiled_plan or lineage_program_compilation.compile_program(program, node_list)
    diagnostics: list[dict[str, str]] = []
    event_sources = plan.get("event_sources") or {}
    for source_id, source in event_sources.items():
        if not isinstance(source, dict) or source.get("kind") != "event_source":
            continue
        event_type = str(source.get("event_type") or "")
        if (
            event_type in RESIDENT_SUBJECT_EVENT_TYPES
            and event_source_resident_scope(source) == "watched"
            and not event_source_subject_ids(source)
        ):
            diagnostics.append(_diagnostic("event.missing_subject", source_id, "Event Source 尚未指定目标 Resident"))
        if event_type == "file.changed" and not source.get("path"):
            diagnostics.append(_diagnostic("event.missing_path", source_id, "file.changed Event Source 尚未指定文件"))
        if event_type == "time.reached" and str(source.get("schedule_mode") or "at") == "at" and not source.get("scheduled_at"):
            diagnostics.append(_diagnostic("event.missing_time", source_id, "time.reached Event Source 尚未指定时间"))
        if event_type == "budget.exhausted":
            subject_id = str(source.get("subject_event_source_id") or "")
            if not subject_id:
                diagnostics.append(_diagnostic(
                    "event.missing_event_source_subject", source_id, "budget.exhausted 尚未指定要观察的 Event Source",
                ))
            elif (event_sources.get(subject_id) or {}).get("max_fires") is None:
                diagnostics.append(_diagnostic(
                    "event.unbounded_budget_subject", source_id,
                    "被观察的 Event Source 没有设置最大触发次数，不会产生 budget.exhausted", "warning",
                ))
        elif event_type in EVENT_SOURCE_DELIVERY_OBSERVER_TYPES and not (
            source.get("subject_event_source_id") or source.get("subject_wire_id")
        ):
            diagnostics.append(_diagnostic(
                "event.missing_delivery_subject", source_id, "投递运行 Event Source 尚未指定要观察的 Event Source 或连线",
            ))
        if event_type in EVENT_SOURCE_EFFECT_OBSERVER_TYPES and not source.get("subject_effect_id"):
            diagnostics.append(_diagnostic("event.missing_effect_subject", source_id, f"{event_type} 尚未指定要观察的 Effect"))
        if event_type in EVENT_SOURCE_HANDOFF_OBSERVER_TYPES and not source.get("subject_handoff_id"):
            diagnostics.append(_diagnostic("event.missing_handoff_subject", source_id, f"{event_type} 尚未指定要观察的工作区交接"))
        if not source.get("subscriber_routes") and event_type not in EVENT_SOURCE_OBSERVER_TYPES:
            watched = any(
                (other or {}).get("subject_event_source_id") == source_id for other in event_sources.values()
            )
            if not watched:
                diagnostics.append(_diagnostic("event.unconnected", source_id, "Event Source 尚未连接任何目标", "warning"))
    effects = plan.get("effects") or {}
    components = {str(item.get("id") or ""): item for item in (plan.get("program") or {}).get("components") or []}
    wires_by_id = {str(wire.get("id") or ""): wire for wire in plan.get("wires") or []}
    for effect_id, effect in effects.items():
        diagnostics.extend(_effect_diagnostics(effect_id, effect))
        diagnostics.extend(_effect_payload_diagnostics(effect_id, effect, event_sources, wires_by_id, components))
    for wire in plan.get("wires") or []:
        route = (plan.get("invoke_routes") or {}).get(str(wire.get("id") or "")) or {}
        references = invocation_argument_output_reference_names((route.get("invocation") or {}).get("arguments"))
        if wire.get("kind") != "route" or not references:
            continue
        source = event_sources.get(event_source_id_for_wire(wire, components)) or {}
        available = _event_output_names(source, effects)
        if available is not None and not references <= available:
            diagnostics.append(_diagnostic(
                "wire.invalid_argument_mapping_output", str(wire.get("id") or ""),
                "参数映射引用了上游 Event 不提供的输出：" + "、".join(sorted(references - available)),
            ))
    for operator_id, operator in (plan.get("packet_operators") or {}).items():
        if (operator.get("config") or {}).get("operator_implementation") == "resident" and not operator.get("resident_executor_id"):
            diagnostics.append(_diagnostic(
                "packet_operator.missing_executor", operator_id, "Resident 式算子尚未绑定执行者",
            ))
        if operator.get("incoming_packet_wire_ids") and not operator.get(
            "outgoing_packet_wire_ids"
        ):
            diagnostics.append(_diagnostic(
                "packet_operator.missing_output", operator_id,
                f"{str(operator.get('kind') or 'Packet 算子')} 的输出尚未连接下游",
            ))
    for loop_id, loop in (plan.get("loops") or {}).items():
        if not loop.get("round_wire_ids"):
            diagnostics.append(_diagnostic("loop.missing_input", loop_id, "循环组件尚未连接轮次输入", "warning"))
        continue_routes = (event_sources.get(loop["continue_source_id"]) or {}).get("subscriber_routes") or []
        if loop.get("round_wire_ids") and not continue_routes:
            diagnostics.append(_diagnostic("loop.missing_output", loop_id, "循环组件的 continue 出口尚未连接下游"))
    unique: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for diagnostic in diagnostics:
        key = (diagnostic["code"], diagnostic["item_id"])
        if key not in seen:
            seen.add(key)
            unique.append(diagnostic)
    return unique
