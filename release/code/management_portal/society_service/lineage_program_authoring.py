"""HATG Program graph authoring and deletion rules.

These functions mutate the in-memory aggregate draft. The caller owns locking,
revision checks, persistence and the post-commit runtime cleanup.
"""
from __future__ import annotations

import secrets
from typing import Any, Iterable

from . import lineage_program
from .lineage_values import _clone_json, _text

PACKET_OPERATOR_DEFAULT_TITLES = {
    "filter": "Prompt 过滤器",
    "selector": "Prompt 选择器",
    "reducer": "Prompt 汇合者",
}


# A new Effect delivers its input to the Resident wired to "target"; the
# Portal primitive card creates the same config (lineage_program.js).
DEFAULT_EFFECT_CONFIG = {"script": 'DeliverMessage resident_id="${endpoint.target}"', "endpoints": ["target"]}


def new_component_details(payload: dict[str, Any]) -> tuple[str, str, str, dict[str, Any]]:
    kind = _text(payload.get("kind"), 32).lower()
    if kind not in lineage_program.PROGRAM_COMPONENT_KINDS:
        raise ValueError("未知的 HATG 程序组件类型")
    component_id = f"{kind}_{secrets.token_hex(6)}"
    title = _text(payload.get("title"), 120) or {
        "event_source": "新 Event", "effect": "新 Effect", "seed": "新 Seed", "loop": "循环",
        **PACKET_OPERATOR_DEFAULT_TITLES,
    }[kind]
    defaults = {
        "event_source": {"event_type": lineage_program.DEFAULT_EVENT_SOURCE_TYPE},
        "effect": DEFAULT_EFFECT_CONFIG,
        "loop": {"max_rounds": 3, "completion": "complete"},
        "seed": {},
    }
    config = (
        _clone_json(payload.get("config"))
        if isinstance(payload.get("config"), dict)
        else _clone_json(defaults[kind]) if kind in defaults
        else lineage_program.default_packet_operator_config(kind)
    )
    if kind in lineage_program.PACKET_OPERATOR_KINDS:
        config = lineage_program.normalize_packet_operator_config(
            kind, config, require_executor=False
        )
        if config.get("resident_executor_id"):
            raise ValueError("创建 Packet Operator 时不能复用其他 Resident 执行器")
    return kind, component_id, title, config


def component_patch(payload: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    component_id = _text(payload.get("component_id"), 110).lower()
    requested = _clone_json(
        payload.get("component") if isinstance(payload.get("component"), dict) else {}
    )
    unknown = sorted(set(requested) - {"title", "client_key", "x", "y", "config"})
    if unknown:
        raise ValueError("Program component patch 不接受字段：" + "、".join(unknown))
    return component_id, requested


def packet_update_config(
    current_component: dict[str, Any], requested: dict[str, Any]
) -> dict[str, Any]:
    kind = str(current_component.get("kind") or "")
    current_config = _clone_json(
        lineage_program.authoring_component_config(current_component)
    )
    candidate = _clone_json(
        requested.get("config") if isinstance(requested.get("config"), dict)
        else current_config
    )
    parked_id = str(current_config.get("resident_executor_id") or "")
    requested_id = str(candidate.get("resident_executor_id") or "")
    if requested_id and parked_id and requested_id != parked_id:
        raise ValueError("Packet Operator 不能改绑到其他 Resident")
    if parked_id and not requested_id:
        candidate["resident_executor_id"] = parked_id
    return lineage_program.normalize_packet_operator_config(
        kind, candidate, require_executor=False
    )


def wire_request(
    payload: dict[str, Any], operation: str
) -> tuple[dict[str, Any], str, str]:
    requested = payload.get("wire") if isinstance(payload.get("wire"), dict) else payload
    requested_id = _text(payload.get("wire_id") or requested.get("id"), 110).lower()
    normalized_operation = _text(operation, 16).lower() or "upsert"
    if normalized_operation not in {"create", "update", "upsert"}:
        raise ValueError("未知的 Program wire 保存操作")
    if normalized_operation == "update" and not requested_id:
        raise ValueError("UpdateWire 需要 wire_id")
    return requested, requested_id, normalized_operation

def release_operator_executor_leadership(
    lineage: dict[str, Any],
    executor_resident_id: str,
) -> None:
    """Keep an implementation detail from becoming the HATG leader."""

    if (
        not executor_resident_id
        or str(lineage.get("leader_resident_id") or "")
        != executor_resident_id
    ):
        return
    lineage["leader_resident_id"] = ""


def delete_program_objects_with_observer_dependents(
    program: dict[str, Any],
    *,
    component_ids: Iterable[str] = (),
    wire_ids: Iterable[str] = (),
) -> dict[str, list[str]]:
    """Delete graph objects and explicit runtime watchers that target them."""

    wires = [
        wire
        for wire in program.get("wires") or []
        if isinstance(wire, dict)
    ]
    removed_component_ids = {
        str(component_id) for component_id in component_ids if str(component_id)
    }
    removed_wire_ids = {
        str(wire_id) for wire_id in wire_ids if str(wire_id)
    }
    while True:
        previous = (len(removed_component_ids), len(removed_wire_ids))
        removed_wire_ids.update(
            str(wire.get("id") or "")
            for wire in wires
            if removed_component_ids
            & {
                str(wire.get("source") or ""),
                str(wire.get("target") or ""),
            }
        )
        for wire in wires:
            if str(wire.get("kind") or "") != "watch":
                continue
            source_id = str(wire.get("source") or "")
            target_id = str(wire.get("target") or "")
            if not (
                target_id in removed_component_ids
                or target_id in removed_wire_ids
            ):
                continue
            removed_component_ids.add(source_id)
        if previous == (len(removed_component_ids), len(removed_wire_ids)):
            break
    return remove_program_graph_objects(
        program, removed_component_ids, removed_wire_ids,
    )


def remove_program_graph_objects(
    program: dict[str, Any],
    removed_component_ids: set[str],
    removed_wire_ids: set[str],
) -> dict[str, list[str]]:
    """Remove exact objects and dangling references, including Block membership."""

    components = program.get("components") or []
    wires = program.get("wires") or []
    while True:
        previous_count = len(removed_wire_ids)
        removed_ids = removed_component_ids | removed_wire_ids
        removed_wire_ids.update(
            str(wire.get("id") or "")
            for wire in wires
            if removed_ids & {str(wire.get("source") or ""), str(wire.get("target") or "")}
        )
        if len(removed_wire_ids) == previous_count:
            break
    program["components"] = [
        component
        for component in components
        if str(component.get("id") or "") not in removed_component_ids
    ]
    program["wires"] = [
        wire
        for wire in wires
        if str(wire.get("id") or "") not in removed_wire_ids
    ]
    return {
        "component_ids": sorted(removed_component_ids),
        "wire_ids": sorted(removed_wire_ids),
    }


def remove_program_components(
    body: dict[str, Any], program: dict[str, Any], component_ids: set[str],
) -> dict[str, list[str]]:
    components = {str(item["id"]): item for item in program.get("components") or []}
    if component_ids - components.keys():
        raise KeyError("program component not found")
    executor_ids: set[str] = set()
    for component_id in component_ids:
        existing = components[component_id]
        executor_resident_id = (
            str(
                lineage_program.authoring_component_config(existing).get(
                    "resident_executor_id"
                )
                or ""
            )
            if str(existing.get("kind") or "")
            in lineage_program.PACKET_OPERATOR_KINDS
            else ""
        )
        if executor_resident_id:
            executor_ids.add(executor_resident_id)
    removed = delete_program_objects_with_observer_dependents(program, component_ids=component_ids)
    for node in body.get("nodes") or []:
        if str(node.get("id") or "") in executor_ids:
            node["operator_executor"] = {}
    for executor_id in sorted(executor_ids):
        release_operator_executor_leadership(body, executor_id)
    return {**removed, "executor_ids": sorted(executor_ids)}


def create_component(
    body: dict[str, Any],
    program: dict[str, Any],
    *,
    component_id: str,
    kind: str,
    title: str,
    payload: dict[str, Any],
    requested_config: dict[str, Any],
    executor_resident_id: str,
) -> dict[str, Any]:
    component = {
        "id": component_id,
        "kind": kind,
        "title": title,
        "client_key": payload.get("client_key"),
        "created_by_principal_id": payload.get(
            "created_by_principal_id"
        ),
        "x": payload.get("x"),
        "y": payload.get("y"),
        "config": requested_config,
    }
    if executor_resident_id:
        executor = next(
            (
                node
                for node in body.get("nodes") or []
                if isinstance(node, dict)
                and str(node.get("id") or "") == executor_resident_id
            ),
            None,
        )
        if not executor:
            raise ValueError("Packet Operator Resident 执行器创建失败")
        executor["operator_executor"] = {
            "component_id": component_id,
            "kind": kind,
        }
        release_operator_executor_leadership(
            body,
            executor_resident_id,
        )
    program.setdefault("components", []).append(component)
    return {"component_id": component_id}


def update_component(
    body: dict[str, Any],
    program: dict[str, Any],
    *,
    component_id: str,
    requested: dict[str, Any],
    requested_endpoint_reference_ids: dict[str, Any] | None,
    requested_watch_subject: dict[str, Any] | None,
) -> dict[str, Any]:
    existing = next(
        (
            component
            for component in program.get("components") or []
            if isinstance(component, dict)
            and str(component.get("id") or "") == component_id
        ),
        None,
    )
    if not existing:
        raise KeyError("program component not found")
    if existing.get("client_key") and "client_key" in requested:
        if requested["client_key"] and requested["client_key"] != existing["client_key"]:
            raise ValueError("Program component client_key 一旦设置便不能修改")
        requested = {**requested, "client_key": existing["client_key"]}
    kind = str(existing.get("kind") or "")
    effective_watch_subject = requested_watch_subject
    existing.update(requested)
    existing["id"] = component_id
    existing["kind"] = kind
    if (
        kind == "event_source"
        and effective_watch_subject is None
        and lineage_program._component_resident_scope(existing) != "watched"
    ):
        # Watching the Lineage or all of Portal replaces the Watch wires.
        effective_watch_subject = {"kind": "resident", "ids": []}
    if kind in lineage_program.PACKET_OPERATOR_KINDS:
        executor_id = str(
            (existing.get("config") or {}).get("resident_executor_id")
            or ""
        )
        if executor_id:
            executor = next(
                (
                    node
                    for node in body.get("nodes") or []
                    if isinstance(node, dict)
                    and str(node.get("id") or "") == executor_id
                ),
                None,
            )
            if not executor:
                raise ValueError("Packet Operator Resident 执行器已不存在")
            executor["operator_executor"] = {
                "component_id": component_id,
                "kind": kind,
            }
            release_operator_executor_leadership(body, executor_id)
    transitions: list[dict[str, Any]] = []
    if kind == "effect":
        transitions.append(lineage_program.reconcile_effect_endpoints(program, component_id))
    if kind == "effect" and requested_endpoint_reference_ids is not None:
        transitions.append(
            lineage_program.reconcile_effect_endpoint_bindings(
                program,
                component_id,
                requested_endpoint_reference_ids,
                lineage_resident_ids=(
                    str(node.get("id") or "")
                    for node in body.get("nodes") or []
                    if isinstance(node, dict)
                ),
            )
        )
    if kind == "event_source" and effective_watch_subject is not None:
        transitions.append(
            lineage_program.reconcile_event_source_watch_binding(
                program,
                component_id,
                effective_watch_subject,
                lineage_resident_ids=(
                    str(node.get("id") or "")
                    for node in body.get("nodes") or []
                    if isinstance(node, dict)
                ),
            )
        )
    return {
        "component_id": component_id,
        "created_wire_ids": [
            wire_id
            for transition in transitions
            for wire_id in transition.get("created_wire_ids", [])
        ],
        "updated_wire_ids": [
            wire_id
            for transition in transitions
            for wire_id in transition.get("updated_wire_ids", [])
        ],
        "removed_wire_ids": list(
            dict.fromkeys(
                wire_id
                for transition in transitions
                for wire_id in transition.get("removed_wire_ids", [])
            )
        ),
    }


def save_wire(
    body: dict[str, Any],
    program: dict[str, Any],
    *,
    requested: dict[str, Any], requested_id: str, normalized_operation: str,
) -> dict[str, Any]:
    wires = program.setdefault("wires", [])
    existing = next(
        (
            wire
            for wire in wires
            if isinstance(wire, dict)
            and str(wire.get("id") or "") == requested_id
        ),
        None,
    )
    if normalized_operation == "create" and existing:
        raise ValueError("CreateWire 不能覆盖已有 wire")
    if normalized_operation == "update" and not existing:
        raise KeyError("program wire not found")
    existing_client_key = str(
        (existing or {}).get("client_key") or ""
    )
    requested_client_key = str(
        requested.get("client_key") or ""
    )
    if (
        existing_client_key
        and requested_client_key
        and requested_client_key != existing_client_key
    ):
        raise ValueError("Program wire client_key 一旦设置便不能修改")
    wire_id = (
        requested_id
        or _text(requested.get("id"), 110).lower()
        or f"wire_{secrets.token_hex(6)}"
    )
    wire = {
        **(existing if isinstance(existing, dict) else {}),
        **requested,
        "id": wire_id,
        "client_key": requested_client_key or existing_client_key,
        "kind": _text(
            requested.get("kind")
            or (existing or {}).get("kind"),
            32,
        ).lower(),
    }
    if existing:
        existing.clear()
        existing.update(wire)
    else:
        wires.append(wire)
    return {"wire_id": wire_id}



def program_seed_component_ids(raw_program: object) -> set[str]:
    program = raw_program if isinstance(raw_program, dict) else {}
    return {
        str(component.get("id") or "")
        for component in program.get("components") or []
        if (
            isinstance(component, dict)
            and str(component.get("kind") or "") == "seed"
            and str(component.get("id") or "")
        )
    }


def program_without_resident_references(
    raw_program: object,
    resident_ids: set[str],
) -> dict[str, Any]:
    program = _clone_json(
        raw_program
        if isinstance(raw_program, dict)
        else lineage_program.empty_program()
    )

    # Seeds stay: nothing on a Seed names the Resident that wrote it, and a
    # Seed whose candidate fork starts from a removed Resident only loses
    # that relation.  Only components that depend on removed wires go.
    removed_component_ids: set[str] = set()
    resident_wire_ids = {
        str(wire.get("id") or "")
        for wire in program.get("wires") or []
        if (
            isinstance(wire, dict)
            and resident_ids
            & {
                str(wire.get("source") or ""),
                str(wire.get("target") or ""),
            }
        )
    }
    while True:
        removed_related_wire_ids = resident_wire_ids | {
            str(wire.get("id") or "")
            for wire in program.get("wires") or []
            if (
                isinstance(wire, dict)
                and removed_component_ids
                & {
                    str(wire.get("source") or ""),
                    str(wire.get("target") or ""),
                }
            )
        }
        dependent_event_source_ids = {
            str(wire.get("source") or "")
            for wire in program.get("wires") or []
            if isinstance(wire, dict) and wire.get("kind") == "watch"
            and str(wire.get("target") or "") in removed_component_ids | removed_related_wire_ids
        }
        expanded = removed_component_ids | dependent_event_source_ids
        if expanded == removed_component_ids:
            break
        removed_component_ids = expanded
    program["components"] = [
        component
        for component in program.get("components") or []
        if (
            isinstance(component, dict)
            and str(component.get("id") or "")
            not in removed_component_ids
        )
    ]
    program["wires"] = [
        item
        for item in program.get("wires") or []
        if (
            isinstance(item, dict)
            and not resident_ids
            & {
                str(item.get("source") or ""),
                str(item.get("target") or ""),
            }
            and not removed_component_ids.intersection(
                {
                    str(item.get("source") or ""),
                    str(item.get("target") or ""),
                }
            )
        )
    ]
    for component in program["components"]:
        config = component.get("config") or {}
        if component.get("kind") in lineage_program.PACKET_OPERATOR_KINDS and config.get("resident_executor_id") in resident_ids:
            component["config"] = {key: value for key, value in config.items() if key != "resident_executor_id"}
    return program
