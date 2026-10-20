"""Apply one write's own changes onto the Lineage definition current at commit.

Instructions on the same Lineage run concurrently.  Each one reads the
definition, computes its result, and commits.  At commit the persistence
layer holds the Lineage row for the duration of the database transaction and
merges:

- ``base``: the definition the instruction read;
- ``ours``: the definition the instruction produced;
- ``current``: the committed definition at commit time.

Objects in keyed collections (nodes, edges, Program components, wires and
Function Block instances, Function Block definitions, fragments) merge by
``id``; other values merge field by field; a value this write did not change
keeps its current value.  Two writes that change the same field of the same
object leave the later value.

References to Residents, components, wires or Function Block instances that
were deleted while the write was in flight are removed the same way a delete
removes them.  The write fails with :class:`ConcurrentChangeConflict` when it
changed an object another write deleted, or when that clean-up has to undo
something this write added or changed: the write referred to an object that
no longer exists.
"""

from __future__ import annotations

import copy
from typing import Any, Callable, Mapping

from . import lineage_program_authoring


class ConcurrentChangeConflict(ValueError):
    """A write changed or referred to an object that another write deleted."""


# Collections whose members are graph objects: changing a member that another
# write deleted is an error for the changing write.
GRAPH_COLLECTIONS = frozenset({
    ("nodes",),
    ("edges",),
    ("function_blocks",),
    ("fragments",),
    ("program", "components"),
    ("program", "wires"),
})
# Canvas coordinates belong to the layout, which has its own writer.
LAYOUT_COLLECTIONS = frozenset({
    ("function_blocks",),
    ("nodes",),
    ("program", "components"),
})
LAYOUT_FIELDS = ("x", "y")
# Every write restamps these on the objects it carries; they are not changes.
MEMBER_STAMP_FIELDS = ("updated_at",)
# Versions and timestamps are assigned by the commit, never merged.
STAMP_FIELDS = frozenset({
    "revision",
    "layout_revision",
    "updated_at",
    "graph_updated_at",
    "layout_updated_at",
})
_MISSING = object()


def _volatile_fields(path: tuple[str, ...]) -> tuple[str, ...]:
    """Member fields of a collection that a merge resolves apart from the object."""

    fields: tuple[str, ...] = ()
    if path in LAYOUT_COLLECTIONS:
        fields += LAYOUT_FIELDS
    if path in GRAPH_COLLECTIONS:
        fields += MEMBER_STAMP_FIELDS
    if path == ("nodes",):
        # These values are projections of Resident identity, not graph edits.
        fields += ("backend", "created_at", "workspace", "role")
    return fields


def _without_layout(value: Any, path: tuple[str, ...] = ()) -> Any:
    if isinstance(value, dict):
        return {
            key: _without_layout(item, (*path, key))
            for key, item in value.items()
            if not (path == () and key in STAMP_FIELDS)
        }
    if isinstance(value, list):
        volatile = _volatile_fields(path)
        if volatile:
            return [
                {key: _without_layout(field, (*path, "*", key)) for key, field in item.items() if key not in volatile}
                if isinstance(item, dict) else item
                for item in value
            ]
        return [_without_layout(item, path) for item in value]
    return value


def _keyed(value: Any) -> dict[str, dict[str, Any]] | None:
    """Members by id when every member is an object with a distinct id."""

    if not isinstance(value, list):
        return None
    members: dict[str, dict[str, Any]] = {}
    for item in value:
        if not isinstance(item, dict):
            return None
        identifier = item.get("id")
        if not isinstance(identifier, str) or not identifier or identifier in members:
            return None
        members[identifier] = item
    return members


def _merge(base: Any, ours: Any, current: Any, path: tuple[str, ...]) -> Any:
    if ours == base:
        return copy.deepcopy(current)
    if isinstance(base, dict) and isinstance(ours, dict) and isinstance(current, dict):
        result: dict[str, Any] = {}
        for key in [*current, *(key for key in ours if key not in current)]:
            b = base.get(key, _MISSING)
            o = ours.get(key, _MISSING)
            c = current.get(key, _MISSING)
            if o == b:
                value = c
            elif o is _MISSING:
                value = _MISSING
            elif b is _MISSING or c is _MISSING:
                value = copy.deepcopy(o)
            else:
                value = _merge(b, o, c, (*path, key))
            if value is not _MISSING:
                result[key] = value
        return result
    keyed = [_keyed(value) for value in (base, ours, current)]
    if all(item is not None for item in keyed):
        return _merge_keyed(*keyed, path)
    return copy.deepcopy(ours)


def _merge_keyed(
    base: dict[str, dict[str, Any]],
    ours: dict[str, dict[str, Any]],
    current: dict[str, dict[str, Any]],
    path: tuple[str, ...],
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for identifier, item in current.items():
        if identifier in base and identifier not in ours:
            continue  # deleted by this write
        if identifier in ours:
            result.append(
                _merge(base[identifier], ours[identifier], item, (*path, "*"))
                if identifier in base
                else copy.deepcopy(ours[identifier])
            )
        else:
            result.append(copy.deepcopy(item))
    for identifier, item in ours.items():
        if identifier in current:
            continue
        if identifier in base:
            if item == base[identifier]:
                continue  # unchanged here, deleted by another write
            if path in GRAPH_COLLECTIONS:
                raise ConcurrentChangeConflict(
                    f"{'.'.join(path)} {identifier} 已被另一条指令删除"
                )
            continue
        result.append(copy.deepcopy(item))
    return result


def _ids(definition: Mapping[str, Any], *path: str) -> set[str]:
    value: Any = definition
    for key in path:
        value = value.get(key) if isinstance(value, Mapping) else None
    return {
        str(item.get("id"))
        for item in value or []
        if isinstance(item, Mapping) and item.get("id")
    }


def _object_versions(definition: Mapping[str, Any]) -> dict[tuple[str, ...], Any]:
    """Each graph object and each other top-level field, for change detection."""

    versions: dict[tuple[str, ...], Any] = {}
    for key, value in definition.items():
        if key == "program" and isinstance(value, Mapping):
            for name, item in value.items():
                members = _keyed(item)
                if ("program", name) in GRAPH_COLLECTIONS and members is not None:
                    for identifier, member in members.items():
                        versions[("program", name, identifier)] = member
                else:
                    versions[("program", name)] = item
            continue
        members = _keyed(value)
        if (key,) in GRAPH_COLLECTIONS and members is not None:
            for identifier, member in members.items():
                versions[(key, identifier)] = member
        else:
            versions[(key,)] = value
    return versions


def _changed(before: Mapping[str, Any], after: Mapping[str, Any]) -> set[tuple[str, ...]]:
    left, right = _object_versions(before), _object_versions(after)
    return {key for key in set(left) | set(right) if left.get(key, _MISSING) != right.get(key, _MISSING)}


def _drop_references(
    definition: dict[str, Any],
    *,
    resident_ids: set[str],
    program_object_ids: set[str],
    successor: Callable[[str, set[str], list[dict[str, Any]]], str],
) -> None:
    """Remove references to deleted objects, as deleting them does."""

    if resident_ids:
        definition["edges"] = [
            edge for edge in definition.get("edges") or []
            if not (isinstance(edge, Mapping) and resident_ids & {str(edge.get("source") or ""), str(edge.get("target") or "")})
        ]
        if not definition["edges"]:
            definition.pop("edges")
        definition["program"] = lineage_program_authoring.program_without_resident_references(
            definition.get("program"), resident_ids,
        )
        leader = str(definition.get("leader_resident_id") or "")
        if leader in resident_ids:
            definition["leader_resident_id"] = successor(leader, resident_ids, list(definition.get("nodes") or []))
    if program_object_ids:
        program = definition.get("program")
        if isinstance(program, dict):
            lineage_program_authoring.delete_program_objects_with_observer_dependents(
                program,
                component_ids=program_object_ids,
                wire_ids=program_object_ids,
            )
    while True:
        present = (_ids(definition, "nodes") | _ids(definition, "program", "components")
                   | _ids(definition, "program", "wires") | _ids(definition, "function_blocks"))
        blocks = definition.get("function_blocks") or []
        for block in blocks:
            block["elements"] = [element for element in block.get("elements") or []
                                 if element["id"] in present]
        kept = [block for block in blocks if block["elements"] or "family" in block]
        if "function_blocks" in definition:
            definition["function_blocks"] = kept
        if len(kept) == len(blocks):
            break


def merge_definition(
    base: Mapping[str, Any],
    ours: Mapping[str, Any],
    current: Mapping[str, Any],
    *,
    successor: Callable[[str, set[str], list[dict[str, Any]]], str],
) -> dict[str, Any]:
    """Merge this write's changes (base -> ours) into the current definition.

    ``successor(old_leader, removed_resident_ids, nodes)`` picks a Leader when
    a write in flight deleted the Leader.  Stamp fields are left for the
    commit to assign.
    """

    plain_base = _without_layout(dict(base))
    plain_ours = _without_layout(dict(ours))
    plain_current = _without_layout(dict(current))
    merged = _merge(plain_base, plain_ours, plain_current, ())
    present = {
        "nodes": _ids(merged, "nodes"),
        "program": _ids(merged, "program", "components")
        | _ids(merged, "program", "wires")
        | _ids(merged, "function_blocks"),
    }
    seen_residents = set().union(*(_ids(item, "nodes") for item in (plain_base, plain_ours, plain_current)))
    seen_program = set().union(*(
        _ids(item, "program", "components")
        | _ids(item, "program", "wires")
        | _ids(item, "function_blocks")
        for item in (plain_base, plain_ours, plain_current)
    ))
    deleted_residents = seen_residents - present["nodes"]
    deleted_program = seen_program - present["program"]
    before_cleanup = copy.deepcopy(merged)
    _drop_references(
        merged,
        resident_ids=deleted_residents,
        program_object_ids=deleted_program,
        successor=successor,
    )
    ours_changes = _changed(plain_base, plain_ours)
    cleanup_changes = _changed(before_cleanup, merged)
    if ("leader_resident_id",) in cleanup_changes and str(plain_base.get("leader_resident_id") or "") in (
        _ids(plain_base, "nodes") - _ids(plain_ours, "nodes")
    ):
        # This write deleted the Leader and picked a successor that another
        # write deleted meanwhile: picking again is the same succession.
        ours_changes.discard(("leader_resident_id",))
    undone = sorted(".".join(key) for key in ours_changes & cleanup_changes)
    if undone:
        raise ConcurrentChangeConflict(
            "这条指令引用的对象已被另一条指令删除：" + "、".join(undone)
        )
    _restore_volatile_fields(merged, base, ours, current)
    return merged


def _members(definition: Mapping[str, Any], path: tuple[str, ...]) -> dict[str, Mapping[str, Any]]:
    value: Any = definition
    for key in path:
        value = value.get(key) if isinstance(value, Mapping) else None
    return {
        str(item.get("id")): item
        for item in value or []
        if isinstance(item, Mapping) and item.get("id")
    }


def _restore_volatile_fields(
    merged: dict[str, Any],
    base: Mapping[str, Any],
    ours: Mapping[str, Any],
    current: Mapping[str, Any],
) -> None:
    """Put back coordinates and member stamps, field by field.

    A field this write changed (or an object it added) keeps this write's
    value; otherwise the committed value stays, so a concurrent move or an
    object another write added keeps its own coordinates.
    """

    for path in LAYOUT_COLLECTIONS | GRAPH_COLLECTIONS:
        fields = _volatile_fields(path)
        target: Any = merged
        for key in path:
            target = target.get(key) if isinstance(target, Mapping) else None
        if not target:
            continue
        before, mine, committed = (_members(item, path) for item in (base, ours, current))
        for item in target:
            if not isinstance(item, dict) or not item.get("id"):
                continue
            identifier = str(item["id"])
            original, own, other = before.get(identifier), mine.get(identifier), committed.get(identifier)
            for field in fields:
                if own is not None and field in own and (
                    original is None or own.get(field) != original.get(field) or other is None
                ):
                    item[field] = copy.deepcopy(own[field])
                elif other is not None and field in other:
                    item[field] = copy.deepcopy(other[field])
                elif own is not None and field in own:
                    item[field] = copy.deepcopy(own[field])
