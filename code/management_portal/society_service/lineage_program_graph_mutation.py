"""The Graph Mutation language of Effects: normalize a list of operations, and calculate
the revision-ready candidate they produce without writes.
"""

from __future__ import annotations

import re
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any

from . import lineage_program, lineage_program_compilation
from .lineage_program_arguments import event_output_reference_names
from .lineage_program_vocabulary import (
    EFFECT_CONFIG_FIELDS, EVENT_SOURCE_AUTHORING_CONFIG_FIELDS, LOOP_CONFIG_FIELDS,
    PACKET_OPERATOR_CONFIG_FIELDS, SEED_CONFIG_FIELDS, _COMPONENT_ID_RE, _WIRE_ID_RE,
)
from .lineage_values import _clone_json, _text


GRAPH_MUTATION_OPERATIONS = {"set", "add", "clone", "remove"}
GRAPH_MUTATION_KINDS = {"component", "wire"}
GRAPH_MUTATION_COMPONENT_FIELDS = {
    "client_key",
    "title",
    "x",
    "y",
    "config",
    *(
        f"config.{field_name}"
        for field_name in (
            EVENT_SOURCE_AUTHORING_CONFIG_FIELDS
            | EFFECT_CONFIG_FIELDS
            | LOOP_CONFIG_FIELDS
            | SEED_CONFIG_FIELDS
            | set().union(*PACKET_OPERATOR_CONFIG_FIELDS.values())
        )
    ),
}
GRAPH_MUTATION_WIRE_FIELDS = {
    "client_key",
    "source",
    "source_port",
    "target",
    "target_port",
    "label",
    "config",
    "config.invoke_profiles",
    "config.include_envelope",
    "config.parallel",
    "config.unavailable_policy",
    "config.queue_limit",
    "config.max_messages",
    "config.expires_after_seconds",
}
_GRAPH_MUTATION_FIELD_RE = re.compile(
    r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)?$"
)


def normalize_graph_mutations(raw: object) -> list[dict[str, Any]]:
    """Normalize the bounded graph-mutation language stored by one Effect."""

    values = raw if isinstance(raw, list) else []
    if len(values) > 64:
        raise ValueError("Graph Mutation 每次最多包含 64 个操作")
    normalized: list[dict[str, Any]] = []
    for index, raw_value in enumerate(values):
        item = raw_value if isinstance(raw_value, dict) else {}
        operation = _text(item.get("op"), 20).lower()
        kind = _text(item.get("kind"), 20).lower()
        if operation not in GRAPH_MUTATION_OPERATIONS:
            raise ValueError(
                f"Graph Mutation 第 {index + 1} 项 op 无效"
            )
        if kind not in GRAPH_MUTATION_KINDS:
            raise ValueError(
                f"Graph Mutation 第 {index + 1} 项 kind 无效"
            )
        result: dict[str, Any] = {
            "op": operation,
            "kind": kind,
        }
        identifier_pattern = (
            _COMPONENT_ID_RE if kind == "component" else _WIRE_ID_RE
        )
        if operation in {"set", "clone", "remove"}:
            item_id = _text(item.get("id"), 110).lower()
            if not identifier_pattern.fullmatch(item_id):
                raise ValueError(
                    f"Graph Mutation 第 {index + 1} 项 id 无效"
                )
            result["id"] = item_id
        if operation == "set":
            field_name = _text(item.get("field"), 80)
            allowed_fields = (
                GRAPH_MUTATION_COMPONENT_FIELDS
                if kind == "component"
                else GRAPH_MUTATION_WIRE_FIELDS
            )
            if (
                not _GRAPH_MUTATION_FIELD_RE.fullmatch(field_name)
                or field_name not in allowed_fields
            ):
                raise ValueError(
                    f"Graph Mutation 不允许修改 {kind}.{field_name or '（空）'}"
                )
            if "value" not in item:
                raise ValueError("Graph Mutation set 操作缺少 value")
            event_output_reference_names(item.get("value"))
            result["field"] = field_name
            result["value"] = deepcopy(item.get("value"))
        elif operation == "add":
            value = item.get("value")
            if not isinstance(value, dict):
                raise ValueError("Graph Mutation add 操作需要对象 value")
            item_id = _text(value.get("id"), 110).lower()
            if not identifier_pattern.fullmatch(item_id):
                raise ValueError("Graph Mutation add value.id 无效")
            allowed_add_fields = (
                {
                    "id",
                    "kind",
                    "client_key",
                    "title",
                    "x",
                    "y",
                    "config",
                    *GRAPH_MUTATION_COMPONENT_FIELDS,
                }
                if kind == "component"
                else {
                    "id",
                    "kind",
                    "type",
                    "client_key",
                    "source",
                    "source_port",
                    "target",
                    "target_port",
                    "label",
                    "config",
                }
            )
            unknown_fields = sorted(
                set(value) - allowed_add_fields
            )
            if unknown_fields:
                raise ValueError(
                    "Graph Mutation add 包含不可写字段："
                    + "、".join(unknown_fields)
                )
            event_output_reference_names(value)
            result["value"] = deepcopy(value)
        elif operation == "clone":
            new_id = _text(item.get("new_id"), 110).lower()
            if not identifier_pattern.fullmatch(new_id):
                raise ValueError("Graph Mutation clone new_id 无效")
            overrides = item.get("overrides")
            if overrides is not None and not isinstance(overrides, dict):
                raise ValueError(
                    "Graph Mutation clone overrides 必须是对象"
                )
            allowed_override_fields = (
                GRAPH_MUTATION_COMPONENT_FIELDS
                if kind == "component"
                else GRAPH_MUTATION_WIRE_FIELDS
            )
            unknown_fields = sorted(
                set(overrides or {}) - allowed_override_fields
            )
            if unknown_fields:
                raise ValueError(
                    "Graph Mutation clone overrides 包含不可写字段："
                    + "、".join(unknown_fields)
                )
            event_output_reference_names(overrides or {})
            result["new_id"] = new_id
            if overrides:
                result["overrides"] = deepcopy(overrides)
        normalized.append(result)
    return normalized


@dataclass(frozen=True)
class ProgramGraphMutation:
    candidate: dict[str, Any]
    changed: set[tuple[str, str]]
    created: set[tuple[str, str]]
    removed: set[tuple[str, str]]
    preserve_event_source_ids: set[str]


@dataclass
class _GraphDraft:
    """The components and wires one Graph Mutation edits, and the items it touched."""

    components: list[dict[str, Any]]
    wires: list[dict[str, Any]]
    preserve_event_fire_budget: bool
    changed: set[tuple[str, str]] = field(default_factory=set)
    created: set[tuple[str, str]] = field(default_factory=set)
    removed: set[tuple[str, str]] = field(default_factory=set)
    preserve_event_source_ids: set[str] = field(default_factory=set)

    def collection(self, kind: str) -> list[dict[str, Any]]:
        return self.components if kind == "component" else self.wires

    def find(self, kind: str, item_id: str) -> dict[str, Any]:
        item = next(
            (
                candidate
                for candidate in self.collection(kind)
                if str(candidate.get("id") or "") == item_id
            ),
            None,
        )
        if not isinstance(item, dict):
            raise ValueError(
                f"Graph Mutation 找不到 {kind}：{item_id}"
            )
        return item

    def has(self, kind: str, item_id: str) -> bool:
        return any(
            str(item.get("id") or "") == item_id
            for item in self.collection(kind)
        )


def _assign_field(
    item: dict[str, Any],
    field_name: str,
    value: object,
) -> None:
    """Set ``field`` or ``parent.field``; a client_key, once set, keeps its value."""

    if field_name == "client_key" and item.get("client_key"):
        if value and value != item["client_key"]:
            raise ValueError("Program client_key 一旦设置便不能修改")
        value = item["client_key"]
    parts = field_name.split(".")
    if len(parts) == 1:
        item[parts[0]] = _clone_json(value)
        return
    parent = item.get(parts[0])
    if not isinstance(parent, dict):
        parent = {}
        item[parts[0]] = parent
    parent[parts[1]] = _clone_json(value)


def _set_field(draft: _GraphDraft, kind: str, operation: dict[str, Any]) -> None:
    item_id = str(operation["id"])
    item = draft.find(kind, item_id)
    field_name = str(operation["field"])
    if (
        kind == "wire"
        and item.get("kind") == "watch"
        and field_name == "target"
        and draft.preserve_event_fire_budget
    ):
        # A Watch moved to another subject keeps its source's fire budget.
        event_source_id = str(item.get("source") or "")
        if event_source_id:
            draft.preserve_event_source_ids.add(event_source_id)
    _assign_field(
        item,
        field_name,
        operation.get("value"),
    )
    draft.changed.add((kind, item_id))


def _add_item(draft: _GraphDraft, kind: str, operation: dict[str, Any]) -> None:
    value = _clone_json(operation["value"])
    item_id = str(value.get("id") or "")
    if kind == "component" and not value.get("kind"):
        value["kind"] = (
            "event_source"
            if item_id.startswith("event_source_")
            else "effect"
            if item_id.startswith("effect_")
            else ""
        )
    if draft.has(kind, item_id):
        raise ValueError(
            f"Graph Mutation add id 已存在：{item_id}"
        )
    draft.collection(kind).append(value)
    draft.created.add((kind, item_id))
    draft.changed.add((kind, item_id))


def _clone_item(draft: _GraphDraft, kind: str, operation: dict[str, Any]) -> None:
    source_id = str(operation["id"])
    source = _clone_json(
        draft.find(kind, source_id)
    )
    new_id = str(operation["new_id"])
    if draft.has(kind, new_id):
        raise ValueError(
            f"Graph Mutation clone id 已存在：{new_id}"
        )
    source["id"] = new_id
    for field_name, value in (
        operation.get("overrides") or {}
    ).items():
        _assign_field(
            source,
            str(field_name),
            value,
        )
    draft.collection(kind).append(source)
    draft.created.add((kind, new_id))
    draft.changed.add((kind, new_id))


def _remove_item(draft: _GraphDraft, kind: str, operation: dict[str, Any]) -> None:
    """Remove an item; a removed component takes the wires that touch it along."""

    item_id = str(operation["id"])
    draft.find(kind, item_id)
    if kind == "component":
        connected_ids = {
            str(wire.get("id") or "")
            for wire in draft.wires
            if item_id
            in {
                str(wire.get("source") or ""),
                str(wire.get("target") or ""),
            }
        }
        draft.wires = [
            wire
            for wire in draft.wires
            if str(wire.get("id") or "")
            not in connected_ids
        ]
        draft.removed.update(
            ("wire", wire_id)
            for wire_id in connected_ids
        )
        draft.changed.update(
            ("wire", wire_id)
            for wire_id in connected_ids
        )
    target_collection = draft.collection(kind)
    target_collection[:] = [
        item
        for item in target_collection
        if str(item.get("id") or "") != item_id
    ]
    draft.removed.add((kind, item_id))
    draft.changed.add((kind, item_id))


# The step of each operation; any other op removes (normalize_graph_mutations admits only these four).
_OPERATION_STEPS = {
    "set": _set_field,
    "add": _add_item,
    "clone": _clone_item,
    "remove": _remove_item,
}


def mutate_program_graph(
    current: dict[str, Any],
    operations: list[dict[str, Any]],
    preserve_event_fire_budget: bool,
) -> ProgramGraphMutation:
    program = lineage_program.normalize_program(
        current.get("program"),
        lineage_nodes=current.get("nodes") or [],
    )
    draft = _GraphDraft(
        components=_clone_json(program.get("components") or []),
        wires=_clone_json(program.get("wires") or []),
        preserve_event_fire_budget=preserve_event_fire_budget,
    )
    for operation in operations:
        op = str(operation["op"])
        kind = str(operation["kind"])
        _OPERATION_STEPS.get(op, _remove_item)(draft, kind, operation)

    normalized_program = lineage_program.normalize_program(
        {
            "components": draft.components,
            "wires": draft.wires,
        },
        lineage_nodes=current.get("nodes") or [],
    )
    lineage_program_compilation.compile_program(
        normalized_program,
        current.get("nodes") or [],
    )
    body = _clone_json(current)
    body["program"] = normalized_program
    return ProgramGraphMutation(
        candidate=body,
        changed=draft.changed,
        created=draft.created,
        removed=draft.removed,
        preserve_event_source_ids=draft.preserve_event_source_ids,
    )
