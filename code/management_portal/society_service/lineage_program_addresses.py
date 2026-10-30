"""Addresses of Program items inside their Lineage (``program/component/<id>`` and the like).

The parent Lineage stays the authority for identity, access and lifecycle.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from .lineage_program_vocabulary import (
    _COMPONENT_ID_RE, _FUNCTION_BLOCK_ID_RE, _LINEAGE_ID_RE, _WIRE_ID_RE,
)


def parse_program_resource_suffix(suffix: object) -> tuple[str, str]:
    """Parse the one canonical nested address contract for HATG program items.

    Event Sources, Effects, typed wires and persistent Function Block
    boundaries are owned by a Lineage.  Their local ids are intentionally not
    registered as top-level TFI Portal resource ids; the parent Lineage remains
    the authority for identity, access and lifecycle.
    """

    clean = str(suffix or "").strip().strip("/")
    parts = clean.split("/") if clean else []
    if len(parts) != 3 or parts[0] != "program":
        raise ValueError(
            "Lineage program address must use "
            "program/component/<component_id>, program/wire/<wire_id>, "
            "or program/function-block/<instance_id>"
        )
    namespace, item_id = parts[1], parts[2]
    if namespace == "component":
        if not _COMPONENT_ID_RE.fullmatch(item_id):
            raise ValueError("Lineage program component address has an invalid id")
        return namespace, item_id
    if namespace == "wire":
        if not _WIRE_ID_RE.fullmatch(item_id):
            raise ValueError("Lineage program wire address has an invalid id")
        return namespace, item_id
    if namespace == "function-block":
        if not _FUNCTION_BLOCK_ID_RE.fullmatch(item_id):
            raise ValueError(
                "Lineage Function Block address has an invalid id"
            )
        return namespace, item_id
    raise ValueError(f"unsupported Lineage program address namespace: {namespace}")


def _program_resource_address(
    lineage_id: object,
    namespace: str,
    item_id: object,
) -> str:
    root_id = str(lineage_id or "").strip().lower()
    if not _LINEAGE_ID_RE.fullmatch(root_id):
        raise ValueError("Lineage program resource requires a canonical lineage_* id")
    parsed_namespace, parsed_item_id = parse_program_resource_suffix(
        f"program/{namespace}/{str(item_id or '').strip().lower()}"
    )
    return f"{root_id}/program/{parsed_namespace}/{parsed_item_id}"


def program_component_address(lineage_id: object, component_id: object) -> str:
    return _program_resource_address(lineage_id, "component", component_id)


def find_program_resource(
    definition: object,
    suffix: object,
) -> tuple[str, str, dict[str, Any] | None]:
    """Return ``(namespace, local_id, item)`` without inventing another store."""

    namespace, item_id = parse_program_resource_suffix(suffix)
    body = definition if isinstance(definition, dict) else {}
    payload = body if namespace == "function-block" else body.get("program", {})
    collection = {
        "component": "components",
        "wire": "wires",
        "function-block": "function_blocks",
    }[namespace]
    item = next(
        (
            value
            for value in payload.get(collection) or []
            if isinstance(value, dict)
            and str(value.get("id") or "").strip().lower() == item_id
        ),
        None,
    )
    return namespace, item_id, deepcopy(item) if item is not None else None
