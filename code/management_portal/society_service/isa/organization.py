"""Organization instructions owned by SocietyService (decision_6591e7793116)."""
from __future__ import annotations

from typing import Any

from management_portal.hatg.contracts import invalid
from management_portal.hatg.kernel import Invocation

from .common import lineage_moves


def move_lineage_objects(inv: Invocation) -> dict[str, Any]:
    object_ids = [str(item) for item in inv.argument("object_ids") or []]
    if not object_ids:
        raise invalid("object_ids 不能为空")
    source = inv.argument("lineage_id")
    target = inv.argument("target_lineage_id") or ""
    payload: dict[str, Any] = {"object_ids": object_ids, "target_lineage_id": target}
    if inv.argument("allow_empty_source") is not None:
        payload["allow_empty_source"] = inv.argument("allow_empty_source")
    result = lineage_moves().move_lineage_subgraph(source, payload, inv.caller)
    target = str(result.get("target_lineage_id") or target)
    return {
        **result,
        "operation_id": str(result.get("operation_id") or ""),
        "source_lineage_id": source,
        "target_lineage_id": target,
        "detached": list(result.get("detached_edges") or []),
    }


def get_move_operation(inv: Invocation) -> dict[str, Any]:
    result = lineage_moves().get_move_operation(inv.argument("operation_id"), inv.caller)
    return {**result, "phase": str(result.get("phase") or ""), "status": str(result.get("status") or "")}


HANDLERS = {
    "MoveLineageObjects": move_lineage_objects,
    "GetMoveOperation": get_move_operation,
}
