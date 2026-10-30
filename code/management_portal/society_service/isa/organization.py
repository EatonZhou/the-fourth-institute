"""Organization instructions owned by SocietyService (decision_6591e7793116)."""
from __future__ import annotations

from typing import Any

from management_portal.hatg.contracts import InstructionError, invalid
from management_portal.hatg.kernel import Invocation

from .common import lineage_moves


def move_resident(inv: Invocation) -> dict[str, Any]:
    resident_id = inv.argument("resident_id")
    source = inv.argument("lineage_id")
    target = inv.argument("target_lineage_id")
    payload: dict[str, Any] = {
        "resident_id": resident_id,
        "target_lineage_id": target,
        "allow_empty_source": bool(inv.argument("allow_empty_source")),
    }
    result = lineage_moves().move_lineage_resident(source, payload, inv.caller)
    return {**result, "operation_id": str(result.get("operation_id") or "")}


def move_lineage_objects(inv: Invocation) -> dict[str, Any]:
    object_ids = [str(item) for item in inv.argument("object_ids") or []]
    if not object_ids:
        raise invalid("object_ids 不能为空")
    source = inv.argument("lineage_id")
    target = inv.argument("target_lineage_id") or ""
    payload: dict[str, Any] = {"object_ids": object_ids, "target_lineage_id": target}
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


def get_knowledge_tags(inv: Invocation) -> dict[str, Any]:
    from management_portal import project_registry

    tags = project_registry.tags()
    return {"tags": tags, "version": project_registry.tags_version(tags)}


def update_knowledge_tags(inv: Invocation) -> dict[str, Any]:
    from management_portal import project_registry

    expected = inv.expected_version
    if isinstance(expected, dict):
        expected = expected.get("tags")
    try:
        saved = project_registry.replace_tags(
            inv.argument("tags"), expected_version=None if expected is None else str(expected),
        )
    except project_registry.TagsChanged as exc:
        raise InstructionError(
            "instruction.version_conflict", "知识标签已被修改；请用 GetKnowledgeTags 重新读取后再提交",
            details={"expected_version": expected, "current_version": exc.current_version},
        ) from None
    except ValueError as exc:
        raise invalid(str(exc)) from None
    return {"tags": saved, "version": project_registry.tags_version(saved)}




HANDLERS = {
    "GetKnowledgeTags": get_knowledge_tags,
    "UpdateKnowledgeTags": update_knowledge_tags,
    "MoveResident": move_resident,
    "MoveLineageObjects": move_lineage_objects,
    "GetMoveOperation": get_move_operation,
}
