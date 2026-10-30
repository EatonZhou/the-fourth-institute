"""Program edits of a Lineage: adding, changing and deleting components and wires, and retiring
exhausted Event sources.
"""

from __future__ import annotations

from functools import partial
from typing import Any, Callable

from management_common.limit_contract import normalize_optional_limit

from .lineage_program_queries import _compiled_program
from .lineage_program_contracts import _program_execution_contract_change
from .lineage_values import _clone_json, _safe_actor, _text
from . import lineage_program_authoring
from . import lineage_operator_runtime, lineage_program, lineage_program_runtime
from . import lineage_aggregate
from . import lineage_state, lineage_reads, lineage_resident_nodes


def _mutate_lineage_program(
    lineage_id: str,
    payload: dict[str, Any],
    actor: str,
    mutate: Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]],
    reason: str,
    *,
    full_view: bool = True,
) -> dict[str, Any]:
    """Apply one edit to the program graph; it merges with concurrent edits at commit."""

    actor_id = _safe_actor(actor)
    contract_impact: dict[str, set[str]] = {}
    completion_baselines: list[dict[str, Any]] = []
    with lineage_aggregate.lineage_scope(lineage_id):
        previous = lineage_aggregate.read_lineage(lineage_id, actor=actor)
        lineage_aggregate.ensure_manager(previous, actor_id)
        body = _clone_json(previous)
        program = (
            body.get("program")
            if isinstance(body.get("program"), dict)
            else lineage_program.empty_program()
        )
        result = mutate(body, program)
        body["program"] = program
        lineage_state._require_unchanged_membership(previous, body)
        normalized = lineage_state._normalize_lineage_payload(
            body,
            previous,
            actor_id,
        )
        contract_impact = _program_execution_contract_change(
            previous,
            normalized,
        )
        # Contract fence, persistence, and transient cleanup are one atomic
        # state transition. A newer start must never be able to enqueue a job
        # between the generation bump and cleanup of the old generation.
        with lineage_state._program_contract_authority(lineage_id, contract_impact):
            lineage_state._persist_lineage_state(previous, normalized, reason, placement_request=payload)
            completion_baselines = lineage_state._execute_program_contract_cleanup(
                lineage_id,
                contract_impact,
                previous_lineage=previous,
                current_lineage=normalized,
            )
    lineage_state._establish_resident_completion_baselines(
        lineage_id,
        actor_id,
        completion_baselines,
    )
    response = {"lineage": lineage_reads.get_lineage(lineage_id, actor_id) if full_view else normalized}
    response.update(result)
    return response


def _create_packet_operator_executor(
    lineage_id: str,
    *,
    component_id: str,
    kind: str,
    title: str,
    config: dict[str, Any],
    actor: str,
    x: object = None,
    y: object = None,
) -> dict[str, Any]:
    return lineage_resident_nodes.create_lineage_resident(
        lineage_id,
        {
            "title": f"{title} · Resident 执行器",
            "x": x,
            "y": y,
        },
        actor,
    )


def create_lineage_program_component(
    lineage_id: str,
    payload: dict[str, Any],
    actor: str,
    *,
    full_view: bool = True,
) -> dict[str, Any]:
    kind, component_id, title, requested_config = (
        lineage_program_authoring.new_component_details(payload)
    )
    effective_payload = dict(payload)
    executor_resident_id = ""
    if kind in lineage_program.PACKET_OPERATOR_KINDS:
        if requested_config["operator_implementation"] == "resident":
            created_executor = _create_packet_operator_executor(
                lineage_id,
                component_id=component_id,
                kind=kind,
                title=title,
                config=requested_config,
                actor=_safe_actor(actor),
                x=payload.get("x"),
                y=payload.get("y"),
            )
            executor_resident_id = str(
                (created_executor.get("node") or {}).get("id") or ""
            )
            requested_config["resident_executor_id"] = executor_resident_id

    mutate = partial(
        lineage_program_authoring.create_component,
        component_id=component_id,
        kind=kind,
        title=title,
        payload=payload,
        requested_config=requested_config,
        executor_resident_id=executor_resident_id,
    )

    response = _mutate_lineage_program(
        lineage_id,
        effective_payload,
        actor,
        mutate,
        "创建 HATG 程序组件",
        full_view=full_view,
    )
    component_id = response.pop("component_id")
    response["component"] = next(
        component
        for component in response["lineage"]["program"]["components"]
        if component["id"] == component_id
    )
    if executor_resident_id:
        response["executor_resident_id"] = executor_resident_id
    return response


def save_lineage_program_component(
    lineage_id: str,
    payload: dict[str, Any],
    actor: str,
    *,
    full_view: bool = True,
) -> dict[str, Any]:
    component_id, requested = lineage_program_authoring.component_patch(payload)
    effective_payload = dict(payload)
    effect_changes = payload.get("effect_changes") if isinstance(payload.get("effect_changes"), dict) else {}
    with lineage_aggregate.lineage_scope(lineage_id):
        current = lineage_aggregate.read_lineage(lineage_id, actor=actor)
        lineage_aggregate.ensure_manager(current, _safe_actor(actor))
        current_component = next(
            (
                component
                for component in (current.get("program") or {}).get(
                    "components",
                    [],
                )
                if isinstance(component, dict)
                and str(component.get("id") or "") == component_id
            ),
            None,
        )
        if not current_component:
            raise KeyError("program component not found")
        current_kind = str(current_component.get("kind") or "")
        current_title = str(current_component.get("title") or "")
    if effect_changes:
        config = _clone_json(
            requested.get("config") if isinstance(requested.get("config"), dict)
            else current_component.get("config") or {}
        )
        config.update(_clone_json(effect_changes))
        requested["config"] = config
    if current_kind in lineage_program.PACKET_OPERATOR_KINDS:
        candidate_config = lineage_program_authoring.packet_update_config(
            current_component, requested
        )
        if (
            candidate_config["operator_implementation"] == "resident"
            and not candidate_config.get("resident_executor_id")
        ):
            created_executor = _create_packet_operator_executor(
                lineage_id,
                component_id=component_id,
                kind=current_kind,
                title=_text(requested.get("title"), 120) or current_title,
                config=candidate_config,
                actor=_safe_actor(actor),
                x=requested.get("x", current_component.get("x")),
                y=requested.get("y", current_component.get("y")),
            )
            candidate_config["resident_executor_id"] = str(
                (created_executor.get("node") or {}).get("id") or ""
            )
        requested["config"] = candidate_config
    requested_endpoint_reference_ids = (
        payload.get("endpoint_reference_ids")
        if isinstance(payload.get("endpoint_reference_ids"), dict)
        else None
    )
    requested_watch_subject = (
        payload.get("watch_subject")
        if isinstance(payload.get("watch_subject"), dict)
        else None
    )

    mutate = partial(
        lineage_program_authoring.update_component,
        component_id=component_id,
        requested=requested,
        requested_endpoint_reference_ids=requested_endpoint_reference_ids,
        requested_watch_subject=requested_watch_subject,
    )

    response = _mutate_lineage_program(
        lineage_id,
        effective_payload,
        actor,
        mutate,
        "更新 HATG 程序组件",
        full_view=full_view,
    )
    response.pop("component_id")
    response["component"] = next(
        component
        for component in response["lineage"]["program"]["components"]
        if component["id"] == component_id
    )
    return response


def delete_lineage_program_component(
    lineage_id: str,
    payload: dict[str, Any],
    actor: str,
    *,
    full_view: bool = True,
) -> dict[str, Any]:
    component_id = _text(payload.get("component_id"), 110).lower()

    def mutate(body: dict[str, Any], program: dict[str, Any]) -> dict[str, Any]:
        existing = next((item for item in program.get("components") or [] if item.get("id") == component_id), {})
        removed = lineage_program_authoring.remove_program_components(body, program, {component_id})
        return {
            "deleted_component_id": component_id,
            "client_key": str(existing.get("client_key") or ""),
            "deleted_component_ids": removed["component_ids"],
            "deleted_wire_ids": removed["wire_ids"],
            "detached_executor_resident_id": next(iter(removed["executor_ids"]), ""),
        }

    response = _mutate_lineage_program(
        lineage_id,
        payload,
        actor,
        mutate,
        "删除 HATG 程序组件",
        full_view=full_view,
    )
    return response


def _retire_exhausted_event_sources(lineage_id: str, actor: str) -> list[str]:
    """Retire opted-in sources once graph cleanup can preserve all accepted work."""

    deleted: list[str] = []
    with lineage_aggregate.execution_authority(lineage_id), lineage_aggregate.lineage_scope(lineage_id):
        lineage = lineage_aggregate.read_lineage(lineage_id, actor=actor)
        lineage_aggregate.ensure_manager(lineage, actor)
        candidates = [
            str(component["id"])
            for component in (lineage.get("program") or {}).get("components") or []
            if component.get("kind") == "event_source"
            and (component.get("config") or {}).get("auto_delete_on_exhaustion")
        ]
        for source_id in candidates:
            lineage = lineage_aggregate.read_lineage(lineage_id, actor=actor)
            source = (_compiled_program(lineage).get("event_sources") or {}).get(source_id)
            if not source or not source.get("auto_delete_on_exhaustion"):
                continue
            maximum = normalize_optional_limit(source.get("max_fires"))
            runtime = lineage_program_runtime.read_runtime(lineage_id)
            source_state = (runtime.get("event_sources") or {}).get(source_id) or {}
            if maximum is None or int(source_state.get("fire_count") or 0) < maximum:
                continue
            if maximum and source_state.get("fanout_settled_generation") != int(
                source_state.get("fire_generation") or 0
            ):
                continue
            if source_state.get("pending_scheduled_at"):
                continue
            proposed = _clone_json(lineage)
            removed = lineage_program_authoring.remove_program_graph_objects(proposed["program"], {source_id}, set())
            impact = _program_execution_contract_change(lineage, proposed)
            if any(
                (runtime.get("event_wires", {}).get(wire_id) or {}).get("pending_events")
                for wire_id in impact["invoke_wire_ids"]
            ):
                continue
            owner_states = [
                lineage_operator_runtime.read_operator_runtime(lineage_id, owner_id)
                for owner_id in impact["operator_ids"]
            ]
            if any(state.get("pending_deliveries") or state.get("inbox") for state in owner_states):
                continue

            def mutate(body: dict[str, Any], program: dict[str, Any]) -> dict[str, Any]:
                return lineage_program_authoring.remove_program_graph_objects(program, {source_id}, set())

            _mutate_lineage_program(
                lineage_id, {}, actor, mutate,
                "Event 触发次数耗尽后自动删除",
            )
            deleted.extend(removed["component_ids"])
    return deleted


def save_lineage_program_wire(
    lineage_id: str,
    payload: dict[str, Any],
    actor: str,
    *,
    operation: str = "upsert",
    full_view: bool = True,
) -> dict[str, Any]:
    requested, requested_id, normalized_operation = (
        lineage_program_authoring.wire_request(payload, operation)
    )

    mutate = partial(
        lineage_program_authoring.save_wire,
        requested=requested,
        requested_id=requested_id,
        normalized_operation=normalized_operation,
    )

    response = _mutate_lineage_program(
        lineage_id,
        payload,
        actor,
        mutate,
        "保存 HATG 程序连线",
        full_view=full_view,
    )
    wire_id = response.pop("wire_id")
    response["wire"] = next(
        wire
        for wire in response["lineage"]["program"]["wires"]
        if wire["id"] == wire_id
    )
    return response


def delete_lineage_program_wire(
    lineage_id: str,
    payload: dict[str, Any],
    actor: str,
    *,
    full_view: bool = True,
) -> dict[str, Any]:
    wire_id = _text(payload.get("wire_id"), 110).lower()

    def mutate(
        _body: dict[str, Any],
        program: dict[str, Any],
    ) -> dict[str, Any]:
        before = program.get("wires") or []
        existing = next(
            (
                wire
                for wire in before
                if (
                    isinstance(wire, dict)
                    and str(wire.get("id") or "") == wire_id
                )
            ),
            None,
        )
        if not existing:
            raise KeyError("program wire not found")
        removed = lineage_program_authoring.delete_program_objects_with_observer_dependents(
            program,
            wire_ids=[wire_id],
        )
        return {
            "deleted_wire_id": wire_id,
            "client_key": str(existing.get("client_key") or ""),
            "deleted_component_ids": removed["component_ids"],
            "deleted_wire_ids": removed["wire_ids"],
        }

    return _mutate_lineage_program(
        lineage_id,
        payload,
        actor,
        mutate,
        "删除 HATG 程序连线",
        full_view=full_view,
    )
