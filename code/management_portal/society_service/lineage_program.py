"""A Lineage Program definition: the empty Program and normalizing a whole Program.

Components and wires are normalized one by one (``lineage_program_components``,
``lineage_program_wires``); this module checks what only the whole graph shows:
unique IDs and endpoints, acyclic operator routes, Watch subjects and observers.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Iterable

from .lineage_definition_contract import PROGRAM_SCHEMA
from .lineage_program_components import _normalize_component
from .lineage_program_event_scope import (
    EVENT_SOURCE_MULTI_RESIDENT_TYPES, RESIDENT_SUBJECT_EVENT_TYPES, _component_resident_scope,
)
from .lineage_program_fields import _reject_unknown_fields, _resident_id
from .lineage_program_vocabulary import (
    EVENT_SOURCE_DELIVERY_OBSERVER_TYPES, EVENT_SOURCE_EFFECT_OBSERVER_TYPES,
    EVENT_SOURCE_HANDOFF_OBSERVER_TYPES, EVENT_SOURCE_OBSERVER_TYPES, PACKET_OPERATOR_KINDS,
    _HANDOFF_ID_RE, _RESIDENT_ID_RE, authoring_event_type,
)
from .lineage_program_wires import _normalize_wire
from .lineage_values import _text


def empty_program() -> dict[str, Any]:
    return {
        "schema": PROGRAM_SCHEMA,
        "components": [],
        "wires": [],
    }


def _program_residents(lineage_nodes: Iterable[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    nodes = [
        node for node in lineage_nodes
        if isinstance(node, dict) and _text(node.get("id"), 80)
    ]
    return {
        _resident_id(node.get("id"), field="HATG node"): node
        for node in nodes
    }


def _authored_items(item: dict[str, Any], before: dict[str, Any], name: str) -> list[Any]:
    """The authored collection, else the previous one, as a private copy."""

    return deepcopy(
        item.get(name)
        if isinstance(item.get(name), list)
        else before.get(name)
        if isinstance(before.get(name), list)
        else []
    )


def _require_unique(values: list[Any], message: str) -> None:
    if len(values) != len(set(values)):
        raise ValueError(message)


def _check_operator_routes_acyclic(
    components_by_id: dict[str, dict[str, Any]],
    wires: list[dict[str, Any]],
) -> None:
    operator_ids = {
        component_id
        for component_id, component in components_by_id.items()
        if component.get("kind") in PACKET_OPERATOR_KINDS
    }
    dependencies: dict[str, set[str]] = {operator_id: set() for operator_id in operator_ids}
    for wire in wires:
        if (
            wire["kind"] == "route"
            and wire["source"] in operator_ids
            and wire["target"] in operator_ids
        ):
            dependencies[wire["source"]].add(wire["target"])
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(operator_id: str) -> None:
        if operator_id in visiting:
            raise ValueError("Packet Operator 的 Packet 路由不能形成循环")
        if operator_id in visited:
            return
        visiting.add(operator_id)
        for target_id in dependencies.get(operator_id, set()):
            visit(target_id)
        visiting.remove(operator_id)
        visited.add(operator_id)

    for operator_id in sorted(operator_ids):
        visit(operator_id)


def _watch_subject(
    event_type: str,
    subject_id: str,
    *,
    watches_resident: bool,
    components_by_id: dict[str, dict[str, Any]],
    wires_by_id: dict[str, dict[str, Any]],
) -> dict[str, str]:
    """The runtime subject field the first Watch wire of an Event Source sets."""

    if watches_resident:
        if event_type not in RESIDENT_SUBJECT_EVENT_TYPES:
            raise ValueError(f"{event_type} Event Source 不能观察 Resident")
        return {"subject_resident_id": subject_id}
    if subject_id in components_by_id:
        subject_kind = components_by_id[subject_id].get("kind")
        if subject_kind == "effect":
            if event_type not in EVENT_SOURCE_EFFECT_OBSERVER_TYPES:
                raise ValueError(f"{event_type} Event Source 不能观察 Effect")
            return {"subject_effect_id": subject_id}
        if subject_kind == "event_source":
            if event_type not in EVENT_SOURCE_OBSERVER_TYPES:
                raise ValueError(f"{event_type} Event Source 不能观察 Event Source")
            return {"subject_event_source_id": subject_id}
        raise ValueError("Watch Subject 原语类型无效")
    if _HANDOFF_ID_RE.fullmatch(subject_id):
        if event_type not in EVENT_SOURCE_HANDOFF_OBSERVER_TYPES:
            raise ValueError(f"{event_type} Event Source 不能观察工作区交接")
        return {"subject_handoff_id": subject_id}
    subject_wire = wires_by_id.get(subject_id)
    if (
        event_type not in EVENT_SOURCE_DELIVERY_OBSERVER_TYPES
        or not subject_wire
        or subject_wire.get("kind") != "route"
    ):
        raise ValueError("投递运行 Event 只能 Watch 进入 Invoke 的 Route 连线")
    return {"subject_wire_id": subject_id}


def _check_additional_watch(
    event_type: str,
    subject_id: str,
    first_subject: dict[str, str],
    watched: set[str],
    *,
    watches_resident: bool,
    components_by_id: dict[str, dict[str, Any]],
) -> None:
    """Several Residents (or Effects) may share one Event Source of a type that watches many."""

    many = event_type in EVENT_SOURCE_MULTI_RESIDENT_TYPES
    if (
        many
        and (components_by_id.get(subject_id) or {}).get("kind") == "effect"
        and "subject_effect_id" in first_subject
    ):
        duplicate = "同一个 Effect 不能重复连接同一个事件源"
    elif many and watches_resident and "subject_resident_id" in first_subject:
        duplicate = "同一个 Resident 不能重复连到同一个 Event Source"
    else:
        raise ValueError(
            "每个 Event Source 只能有一个 Watch Subject；"
            "只有 Resident 类 Event 可以同时监听多个 Resident"
        )
    if subject_id in watched:
        raise ValueError(duplicate)
    watched.add(subject_id)


def _watch_subjects(
    components_by_id: dict[str, dict[str, Any]],
    wires: list[dict[str, Any]],
    wires_by_id: dict[str, dict[str, Any]],
    residents: dict[str, dict[str, Any]],
) -> dict[str, dict[str, str]]:
    """Check every Watch wire against its Event Source; each source's first subject."""

    subjects: dict[str, dict[str, str]] = {}
    watched: dict[str, set[str]] = {}
    for wire in wires:
        if wire["kind"] != "watch":
            continue
        observer_id = str(wire["source"])
        observer = components_by_id.get(observer_id) or {}
        event_type = authoring_event_type(observer)
        if _component_resident_scope(observer) != "watched":
            raise ValueError(
                "监听范围是本 Lineage 或 Portal 全部 Resident 的 Event Source "
                "不使用 Watch 连线；请删除由它发出的 Watch 连线，或把监听范围改回 Watch 连线指定"
            )
        subject_id = str(wire["target"])
        watches_resident = bool(
            subject_id in residents or _RESIDENT_ID_RE.fullmatch(subject_id)
        )
        if observer_id in subjects:
            _check_additional_watch(
                event_type, subject_id, subjects[observer_id], watched[observer_id],
                watches_resident=watches_resident, components_by_id=components_by_id,
            )
            continue
        subjects[observer_id] = _watch_subject(
            event_type, subject_id, watches_resident=watches_resident,
            components_by_id=components_by_id, wires_by_id=wires_by_id,
        )
        watched[observer_id] = {subject_id}
    return subjects


def _check_observer_dependencies(
    components: list[dict[str, Any]],
    wires_by_id: dict[str, dict[str, Any]],
    subjects: dict[str, dict[str, str]],
) -> None:
    """A run Event Source must not observe itself, directly or through other sources."""

    observed_by: dict[str, str] = {}
    for component in components:
        if (
            component.get("kind") != "event_source"
            or authoring_event_type(component) not in EVENT_SOURCE_OBSERVER_TYPES
        ):
            continue
        component_id = str(component["id"])
        subject = subjects.get(component_id) or {}
        observed_id = str(subject.get("subject_event_source_id") or "")
        if subject.get("subject_wire_id"):
            observed_id = str(wires_by_id[subject["subject_wire_id"]]["source"])
        if observed_id == component_id:
            raise ValueError("运行 Event Source 不能监听自身")
        if observed_id:
            observed_by[component_id] = observed_id
    for watcher_id in observed_by:
        seen: set[str] = set()
        current_id = watcher_id
        while current_id in observed_by:
            if current_id in seen:
                raise ValueError("运行 Event Source 监听关系不能形成循环")
            seen.add(current_id)
            current_id = observed_by[current_id]


_PROGRAM_FIELDS = frozenset({"schema", "components", "wires"})


def normalize_program(
    raw: object,
    *,
    lineage_nodes: Iterable[dict[str, Any]],
    previous: object = None,
) -> dict[str, Any]:
    """Normalize a persistable, partially wired HATG program.

    This layer validates graph identity and typed endpoints. It deliberately
    permits disconnected components so drag/drop authoring can be incremental;
    executable diagnostics are produced by :func:`program_diagnostics`.
    """

    item = raw if isinstance(raw, dict) else {}
    _reject_unknown_fields(item, _PROGRAM_FIELDS, "Lineage program")
    before = previous if isinstance(previous, dict) else {}
    residents = _program_residents(lineage_nodes)
    components_raw = _authored_items(item, before, "components")
    wires_raw = _authored_items(item, before, "wires")
    components = [
        _normalize_component(raw_component, residents)
        for raw_component in components_raw
    ]
    component_ids = [component["id"] for component in components]
    _require_unique(component_ids, "Lineage 程序 component id 不能重复")
    if set(component_ids) & set(residents):
        raise ValueError("Lineage 程序 component id 不能与 Resident id 冲突")
    components_by_id = {component["id"]: component for component in components}
    wires = [
        _normalize_wire(raw_wire, residents=residents, components=components_by_id)
        for raw_wire in wires_raw
    ]
    _require_unique([wire["id"] for wire in wires], "Lineage 程序 wire id 不能重复")
    _require_unique(
        [
            str(item.get("client_key") or "")
            for item in [*components, *wires]
            if str(item.get("client_key") or "")
        ],
        "Lineage 程序 client_key 不能重复",
    )
    _require_unique(
        [
            (wire["kind"], wire["source"], wire["source_port"], wire["target"], wire.get("target_port", ""))
            for wire in wires
        ],
        "相同端点与类型的 Lineage 程序连线不能重复",
    )
    _check_operator_routes_acyclic(components_by_id, wires)
    wires_by_id = {str(wire["id"]): wire for wire in wires}
    subjects = _watch_subjects(components_by_id, wires, wires_by_id, residents)
    _check_observer_dependencies(components, wires_by_id, subjects)
    _require_unique(
        [(str(wire["source"]), str(wire["source_port"])) for wire in wires if wire["kind"] == "target"],
        "每个 Effect 的一个具名 Endpoint 只能连接一个 Reference",
    )
    return {
        "schema": PROGRAM_SCHEMA,
        "components": components,
        "wires": wires,
    }
