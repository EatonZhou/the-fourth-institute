"""Program graph mutation policy: calculate a revision-ready candidate without writes."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from . import lineage_program
from .lineage_values import _clone_json


@dataclass(frozen=True)
class ProgramGraphMutation:
    candidate: dict[str, Any]
    changed: set[tuple[str, str]]
    created: set[tuple[str, str]]
    removed: set[tuple[str, str]]
    preserve_event_source_ids: set[str]


def mutate_program_graph(
    current: dict[str, Any],
    operations: list[dict[str, Any]],
    preserve_event_fire_budget: bool,
) -> ProgramGraphMutation:
    program = lineage_program.normalize_program(
        current.get("program"),
        lineage_nodes=current.get("nodes") or [],
    )
    components = _clone_json(program.get("components") or [])
    wires = _clone_json(program.get("wires") or [])
    changed: set[tuple[str, str]] = set()
    created: set[tuple[str, str]] = set()
    removed: set[tuple[str, str]] = set()
    preserve_event_source_ids: set[str] = set()

    def collection(kind: str) -> list[dict[str, Any]]:
        return components if kind == "component" else wires

    def find_item(kind: str, item_id: str) -> dict[str, Any]:
        item = next(
            (
                candidate
                for candidate in collection(kind)
                if str(candidate.get("id") or "") == item_id
            ),
            None,
        )
        if not isinstance(item, dict):
            raise ValueError(
                f"Graph Mutation 找不到 {kind}：{item_id}"
            )
        return item

    def assign_field(
        item: dict[str, Any],
        field_name: str,
        value: object,
    ) -> None:
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

    for operation in operations:
        op = str(operation["op"])
        kind = str(operation["kind"])
        if op == "set":
            item_id = str(operation["id"])
            item = find_item(kind, item_id)
            field_name = str(operation["field"])
            if (
                kind == "wire"
                and item.get("kind") == "watch"
                and field_name == "target"
                and preserve_event_fire_budget
            ):
                event_source_id = str(item.get("source") or "")
                if event_source_id:
                    preserve_event_source_ids.add(event_source_id)
            assign_field(
                item,
                field_name,
                operation.get("value"),
            )
            changed.add((kind, item_id))
            continue
        if op == "add":
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
            if any(
                str(item.get("id") or "") == item_id
                for item in collection(kind)
            ):
                raise ValueError(
                    f"Graph Mutation add id 已存在：{item_id}"
                )
            collection(kind).append(value)
            created.add((kind, item_id))
            changed.add((kind, item_id))
            continue
        if op == "clone":
            source_id = str(operation["id"])
            source = _clone_json(
                find_item(kind, source_id)
            )
            new_id = str(operation["new_id"])
            if any(
                str(item.get("id") or "") == new_id
                for item in collection(kind)
            ):
                raise ValueError(
                    f"Graph Mutation clone id 已存在：{new_id}"
                )
            source["id"] = new_id
            for field_name, value in (
                operation.get("overrides") or {}
            ).items():
                assign_field(
                    source,
                    str(field_name),
                    value,
                )
            collection(kind).append(source)
            created.add((kind, new_id))
            changed.add((kind, new_id))
            continue

        item_id = str(operation["id"])
        find_item(kind, item_id)
        if kind == "component":
            connected_ids = {
                str(wire.get("id") or "")
                for wire in wires
                if item_id
                in {
                    str(wire.get("source") or ""),
                    str(wire.get("target") or ""),
                }
            }
            wires = [
                wire
                for wire in wires
                if str(wire.get("id") or "")
                not in connected_ids
            ]
            removed.update(
                ("wire", wire_id)
                for wire_id in connected_ids
            )
            changed.update(
                ("wire", wire_id)
                for wire_id in connected_ids
            )
        target_collection = collection(kind)
        target_collection[:] = [
            item
            for item in target_collection
            if str(item.get("id") or "") != item_id
        ]
        removed.add((kind, item_id))
        changed.add((kind, item_id))

    normalized_program = lineage_program.normalize_program(
        {
            "components": components,
            "wires": wires,
        },
        lineage_nodes=current.get("nodes") or [],
    )
    lineage_program.compile_program(
        normalized_program,
        current.get("nodes") or [],
    )
    body = _clone_json(current)
    body["program"] = normalized_program
    return ProgramGraphMutation(
        candidate=body,
        changed=changed,
        created=created,
        removed=removed,
        preserve_event_source_ids=preserve_event_source_ids,
    )
