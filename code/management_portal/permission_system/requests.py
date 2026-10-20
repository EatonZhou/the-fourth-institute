"""The request adapter: one ISA call becomes a set of ``(p, a, r)`` requests (decision_b820faf42218 §3.2).

The caller is ``p``.  Each decision slot the instruction declares names its
A element, which the scheme places under an A class node (an unclassified
one sits under the default node, "未归类" in the default scheme); the
resources an argument names are its ``r`` (one request per resource; an
address such as ``workspace_x/a.md`` counts as its root ID).  A fixed slot
asks about the R node the scheme gives it, else the R default node
("共享").  The call is allowed when every request is.

A slot with ``via`` (``<record>.<field>``) takes its argument as IDs of saved
records: each record is read here, at the entry, and the object it names in
that field is the ``r`` (decision_898b8911a2ff "判定对象可以来自已保存的记录").
The caller passes a reader per kind of record (``PARPermissions`` passes the
readers of the code that keeps each).  A record that does not exist ends the
call as not found; one that names no object in that field (a receipt scoped
to its caller) makes no request.

A slot with ``place`` judges a creating call where the new object will sit:
its argument is the object's type, ``place`` names the arguments giving its
related objects, and the request is about the R node it will go under —
the same place the fact adapter gives it once it exists.
"""
from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from .scheme.compiler import CompiledScheme, Slot
from .scheme.vocabulary import NAMED_TYPES

_UNIFIED_ID = re.compile(r"^([a-z][a-z0-9]*(?:_[a-z0-9]+)*)_[0-9a-f]{12}$")

# (record IDs, the call's arguments) -> record ID -> the record's fields naming objects ("" for none);
# a record that does not exist is left out.
RecordReader = Callable[[list[str], Mapping[str, Any]], Mapping[str, Mapping[str, str]]]


class Unresolved(ValueError):
    """The call does not name a resource a requirement needs, or names the wrong kind."""


class RecordNotFound(LookupError):
    """A record an argument names does not exist."""


def _owners(slot: Slot, identifiers: list[str], arguments: Mapping[str, Any],
            readers: Mapping[str, RecordReader] | None) -> list[str]:
    """The objects the records ``identifiers`` name in the field ``slot.via`` says."""
    record, field = slot.via.split(".", 1)
    reader = (readers or {}).get(record)
    if reader is None:
        raise Unresolved(f"这个服务不能读取 {record} 记录，无法判定 {slot.argument}")
    found = reader(identifiers, arguments)
    owners = []
    for identifier in identifiers:
        if identifier not in found:
            raise RecordNotFound(f"{slot.argument} 指向的记录 {identifier} 不存在")
        if found[identifier].get(field):
            owners.append(str(found[identifier][field]))
    return owners


def _placed_under(slot: Slot, arguments: Mapping[str, Any]) -> dict[str, str]:
    """The related objects a creating call names for ``slot.place``: the first ID each related type's paths give.

    A class node such as ``@public`` names no object, so the new object takes
    its pattern's fallback, as the fact adapter places it.
    """
    owners = {}
    for related, paths in slot.place:
        given = next((values[0] for path in paths if (values := _resources(arguments, path))), "")
        if not given or given.startswith("@"):
            continue
        if (resource_type(given) or (related if related in NAMED_TYPES else "")) != related:
            raise Unresolved(f"{'、'.join(paths)} 应指向 {related} 资源")
        owners[related] = given
    return owners


@dataclass(frozen=True)
class Request:
    action: str
    resource: str
    slot: Slot | None = None


def resource_type(resource_id: str) -> str:
    """The type a resource ID names, or "" when it is not a unified resource ID."""
    match = _UNIFIED_ID.fullmatch(resource_id)
    if match:
        return match.group(1)
    from management_common.resource_identity import get_resource_type_registry

    try:
        return get_resource_type_registry().parse(resource_id).resource_type
    except ValueError:
        return ""


def _resources(arguments: Mapping[str, Any], path: str) -> list[str]:
    value: Any = arguments
    for part in path.split("."):
        if not isinstance(value, Mapping):
            return []
        value = value.get(part)
    values = value if isinstance(value, (list, tuple)) else [value]
    identifiers = []
    for item in values:
        if isinstance(item, str):
            root = item.strip().split("/", 1)[0]
            if root:
                identifiers.append(root)
    return list(dict.fromkeys(identifiers))


def requests(scheme: CompiledScheme, instruction: str, arguments: Mapping[str, Any], *,
             readers: Mapping[str, RecordReader] | None = None) -> list[Request]:
    """The requests one call makes under ``scheme``; ``readers`` read the records ``via`` slots name."""
    slots = scheme.slots(instruction)
    if not slots:
        return [Request(instruction, scheme.unmapped_resource)]
    found: list[Request] = []
    for slot in slots:
        if slot.resource:
            found.append(Request(slot.element, slot.resource, slot))
            continue
        identifiers = _resources(arguments, slot.argument)
        if not identifiers:
            if slot.optional:
                continue
            raise Unresolved(f"{instruction} 需要 {slot.argument} 指明资源")
        if slot.via:
            found.extend(Request(slot.element, owner, slot) for owner in _owners(slot, identifiers, arguments, readers))
            continue
        if slot.place:
            owners = _placed_under(slot, arguments)
            found.extend(Request(slot.element, scheme.placement("R", kind, owners), slot) for kind in identifiers)
            continue
        named = next((kind for kind in slot.types if kind in NAMED_TYPES), "")
        for identifier in identifiers:
            if slot.types and (resource_type(identifier) or named) not in slot.types:
                raise Unresolved(f"{slot.argument} 应指向 {'、'.join(slot.types)} 资源")
            found.append(Request(slot.element, identifier, slot))
    if not found:
        # Every slot optional and none given: asking nothing would pass everyone, so it is judged as unmapped.
        # The instruction's own element sits under its mapped node, so the request names the A default node.
        return [Request(scheme.trees["A"].default_parent, scheme.unmapped_resource)]
    return list(dict.fromkeys(found))
