"""Inspector bindings: an Effect's named Target wires and an Event Source's Watch wires,
reconciled with the IDs entered in the component inspector.
"""

from __future__ import annotations

from typing import Any, Iterable

from .lineage_program_event_scope import (
    EVENT_SOURCE_MULTI_RESIDENT_TYPES, _component_resident_scope,
)
from .lineage_program_fields import _resident_id, _stable_id
from .lineage_program_vocabulary import _RESIDENT_ID_RE, authoring_event_type
from .lineage_values import _text, _item_id


def _effect_endpoint_names(program: dict[str, Any], component_id: str) -> list[str]:
    component = next(
        (
            item for item in program.get("components") or []
            if isinstance(item, dict) and str(item.get("id") or "") == component_id
        ),
        {},
    )
    config = component.get("config") if isinstance(component.get("config"), dict) else {}
    return [str(name) for name in config.get("endpoints") or []]


def reconcile_effect_endpoints(program: dict[str, Any], component_id: str) -> dict[str, list[str]]:
    """Drop Target wires whose endpoint the Effect no longer declares."""

    allowed = set(_effect_endpoint_names(program, component_id))
    kept: list[Any] = []
    removed: list[str] = []
    for raw_wire in program.get("wires") or []:
        wire = raw_wire if isinstance(raw_wire, dict) else {}
        if (
            str(wire.get("source") or "") == component_id
            and _text(wire.get("kind"), 32).lower() == "target"
            and (_text(wire.get("source_port"), 80) or "") not in allowed
        ):
            removed.append(str(wire.get("id") or ""))
            continue
        kept.append(raw_wire)
    program["wires"] = kept
    return {"removed_wire_ids": removed}


def reconcile_effect_endpoint_bindings(
    program: dict[str, Any],
    component_id: str,
    endpoint_reference_ids: object,
    *,
    lineage_resident_ids: Iterable[str],
) -> dict[str, list[str]]:
    """Apply authored resource IDs to an Effect's named Target wires.

    The component inspector and canvas ports are two authoring surfaces for
    the same typed wires. This function is their single topology authority:
    entering an ID updates or creates the corresponding wire, while clearing
    an ID removes it without inventing a second endpoint representation.
    """

    allowed_ports = tuple(_effect_endpoint_names(program, component_id))
    requested = (
        endpoint_reference_ids
        if isinstance(endpoint_reference_ids, dict)
        else {}
    )
    unknown_ports = sorted(
        str(port)
        for port, resident_id in requested.items()
        if str(resident_id or "").strip() and str(port) not in allowed_ports
    )
    if unknown_ports:
        raise ValueError("Effect 没有声明这些端点：" + "、".join(unknown_ports))

    resident_ids = {
        _resident_id(resident_id, field="HATG Resident")
        for resident_id in lineage_resident_ids
    }
    component_ids = {
        _text(component.get("id"), 110).lower()
        for component in program.get("components") or []
        if isinstance(component, dict)
    }
    reference_ids = (resident_ids | component_ids) - {component_id}
    desired: dict[str, str] = {}
    for port in allowed_ports:
        reference_id = _text(requested.get(port), 110).lower()
        if not reference_id:
            desired[port] = ""
            continue
        if (
            reference_id not in reference_ids
            and not _RESIDENT_ID_RE.fullmatch(reference_id)
        ):
            raise ValueError(
                f"Effect {port} Reference 不在当前 HATG"
            )
        desired[port] = reference_id

    removed: list[str] = []
    updated: list[str] = []
    created: list[str] = []
    occupied_ports: set[str] = set()
    reconciled: list[dict[str, Any]] = []
    for raw_wire in program.get("wires") or []:
        wire = raw_wire if isinstance(raw_wire, dict) else {}
        wire_kind = _text(wire.get("kind"), 32).lower()
        if (
            str(wire.get("source") or "") != component_id
            or wire_kind != "target"
        ):
            reconciled.append(raw_wire)
            continue

        wire_id = str(wire.get("id") or "")
        source_port = _text(wire.get("source_port"), 80) or "target"
        reference_id = desired.get(source_port, "")
        if (
            source_port not in allowed_ports
            or not reference_id
            or source_port in occupied_ports
        ):
            removed.append(wire_id)
            continue
        changed = (
            str(wire.get("source_port") or "") != source_port
            or str(wire.get("target") or "") != reference_id
            or "target_port" in wire
        )
        wire["kind"] = "target"
        wire["source_port"] = source_port
        wire["target"] = reference_id
        wire.pop("target_port", None)
        occupied_ports.add(source_port)
        reconciled.append(raw_wire)
        if changed:
            updated.append(wire_id)

    for port in allowed_ports:
        reference_id = desired.get(port, "")
        if not reference_id or port in occupied_ports:
            continue
        # Reconnecting a removed endpoint creates a new graph object.
        wire_id = _item_id(None, "wire")
        reconciled.append(
            {
                "id": wire_id,
                "kind": "target",
                "source": component_id,
                "source_port": port,
                "target": reference_id,
            }
        )
        occupied_ports.add(port)
        created.append(wire_id)

    program["wires"] = reconciled
    return {
        "created_wire_ids": created,
        "updated_wire_ids": updated,
        "removed_wire_ids": removed,
    }


def reconcile_event_source_watch_binding(
    program: dict[str, Any],
    component_id: str,
    watch_subject: object,
    *,
    lineage_resident_ids: Iterable[str],
) -> dict[str, list[str]]:
    """Reconcile the Event Source inspector with its canonical Watch wires.

    ``{"kind": "resident", "ids": [...]}`` sets several watched Residents at
    once for a type that watches many; otherwise one ``kind`` and ``id``.
    """

    requested = watch_subject if isinstance(watch_subject, dict) else {}
    subject_kind = _text(requested.get("kind"), 24).lower()
    if subject_kind in {"resident", "effect"} and isinstance(requested.get("ids"), list):
        return _reconcile_event_source_watches(
            program, component_id, requested["ids"], lineage_resident_ids=lineage_resident_ids, subject_kind=subject_kind,
        )
    subject_id = _text(requested.get("id"), 110).lower()
    if bool(subject_kind) != bool(subject_id):
        raise ValueError("Watch Subject 必须同时提供 kind 与 id")
    if subject_kind and subject_kind not in {
        "resident",
        "event_source",
        "effect",
        "wire",
    }:
        raise ValueError("Watch Subject kind 无效")

    components = {
        str(component.get("id") or ""): component
        for component in program.get("components") or []
        if isinstance(component, dict)
    }
    target = components.get(component_id) or {}
    if target.get("kind") != "event_source":
        raise ValueError("Watch 只能绑定到 Event Source")
    residents = {
        _text(value, 80).lower() for value in lineage_resident_ids
    }
    wires = [
        wire
        for wire in program.get("wires") or []
        if isinstance(wire, dict)
    ]
    wires_by_id = {
        str(wire.get("id") or ""): wire for wire in wires
    }
    if (
        subject_kind == "resident"
        and subject_id not in residents
        and not _RESIDENT_ID_RE.fullmatch(subject_id)
    ):
        raise ValueError("Watch Resident 不在当前 HATG")
    if subject_kind in {"event_source", "effect"}:
        subject = components.get(subject_id) or {}
        if subject.get("kind") != subject_kind:
            raise ValueError(f"Watch {subject_kind} 不在当前 HATG")
    if subject_kind == "wire" and subject_id not in wires_by_id:
        raise ValueError("Watch wire 不在当前 HATG")

    current = [
        wire
        for wire in wires
        if wire.get("kind") == "watch"
        and str(wire.get("source") or "") == component_id
    ]
    kept = [wire for wire in wires if wire not in current]
    removed = [str(wire.get("id") or "") for wire in current]
    created: list[str] = []
    updated: list[str] = []
    if subject_id:
        reusable = current[0] if current else None
        wire_id = (
            str(reusable.get("id") or "")
            if reusable
            else _stable_id(
                "wire",
                "watch-subject",
                subject_id,
                component_id,
            )
        )
        watch_wire: dict[str, Any] = {
            "id": wire_id,
            "kind": "watch",
            "target": subject_id,
            "source_port": "watch",
            "source": component_id,
        }
        kept.append(watch_wire)
        if reusable:
            removed.remove(wire_id)
            updated.append(wire_id)
        else:
            created.append(wire_id)
    program["wires"] = kept
    return {
        "created_wire_ids": created,
        "updated_wire_ids": updated,
        "removed_wire_ids": removed,
    }


def _reconcile_event_source_watches(
    program: dict[str, Any],
    component_id: str,
    requested_ids: list[Any],
    *,
    subject_kind: str = "resident",
    lineage_resident_ids: Iterable[str],
) -> dict[str, list[str]]:
    """Make the Event Source watch exactly ``requested_ids``; existing wires are kept by watched subject."""

    subject_ids = list(dict.fromkeys(
        _text(value, 110).lower() for value in requested_ids if _text(value, 110)
    ))
    residents = {_text(value, 80).lower() for value in lineage_resident_ids}
    if subject_kind == "effect":
        residents = {str(c.get("id")) for c in program.get("components", []) if c.get("kind") == "effect"}
    for subject_id in subject_ids:
        if subject_kind == "effect" and subject_id not in residents:
            raise ValueError("Watch Effect 不在当前 HATG")
        if subject_id not in residents and not _RESIDENT_ID_RE.fullmatch(subject_id):
            raise ValueError("Watch Resident 不在当前 HATG")
    target = next((
        component for component in program.get("components") or []
        if isinstance(component, dict) and str(component.get("id") or "") == component_id
    ), {})
    if target.get("kind") != "event_source":
        raise ValueError("Watch 只能绑定到 Event Source")
    event_type = authoring_event_type(target)
    if subject_kind == "resident" and subject_ids and _component_resident_scope(target) != "watched":
        raise ValueError(
            "监听范围是本 Lineage 或 Portal 全部 Resident 的 Event Source 不使用 Watch 连线"
        )
    if len(subject_ids) > 1 and event_type not in EVENT_SOURCE_MULTI_RESIDENT_TYPES:
        raise ValueError(f"{event_type} Event Source 只能监听一个 Resident")
    wires = [wire for wire in program.get("wires") or [] if isinstance(wire, dict)]
    current = [
        wire for wire in wires
        if wire.get("kind") == "watch" and str(wire.get("source") or "") == component_id
    ]
    by_subject = {str(wire.get("target") or ""): wire for wire in current}
    kept = [wire for wire in wires if wire not in current]
    created: list[str] = []
    updated: list[str] = []
    for subject_id in subject_ids:
        reusable = by_subject.get(subject_id)
        if reusable is not None:
            kept.append(reusable)
            updated.append(str(reusable.get("id") or ""))
            continue
        wire_id = _stable_id("wire", "watch-subject", subject_id, component_id)
        watch_wire: dict[str, Any] = {
            "id": wire_id,
            "kind": "watch",
            "target": subject_id,
            "source_port": "watch",
            "source": component_id,
        }
        kept.append(watch_wire)
        created.append(wire_id)
    program["wires"] = kept
    return {
        "created_wire_ids": created,
        "updated_wire_ids": updated,
        "removed_wire_ids": [
            str(wire.get("id") or "") for wire in current
            if str(wire.get("target") or "") not in subject_ids
        ],
    }
