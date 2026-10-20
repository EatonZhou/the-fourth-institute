"""Bookmark presentation uses ISA authorization and ResourceService existence."""
from __future__ import annotations

from typing import Any, Callable

from management_portal.resources import get_resource_service
from management_portal.resources.errors import resource_error_http_response


def project_favorite_statuses(
    actor: str, payload: dict[str, Any], forget: Callable[[str], Any] | None = None,
) -> dict[str, Any]:
    """Status of each bookmarked Project and Lineage; a deleted one leaves My Favorite."""
    statuses = {}
    deleted_selections = []
    service = get_resource_service()
    from management_portal.permission_system import readable_ids
    selections = {
        selection: selection.partition(":")[2]
        for selection in payload.get("favorites", [])
        if selection.partition(":")[0] in {"project", "lineage"}
    }
    try:
        readable = readable_ids("ResolveResource", "address", selections.values(), actor=actor)
    except (KeyError, PermissionError, ValueError, RuntimeError, OSError):
        return {**payload, "favorite_statuses": {
            selection: {"status": "unavailable"} for selection in selections
        }}
    for selection, resource_id in selections.items():
        try:
            if resource_id not in readable:
                statuses[selection] = {"status": "denied"}
                continue
            resolved = service.resolve(resource_id, actor=actor)
        except (KeyError, PermissionError, ValueError, RuntimeError, OSError) as exc:
            status, _ = resource_error_http_response(exc)
            statuses[selection] = {"status": {403: "denied", 404: "missing"}.get(status, "unavailable")}
            continue
        record = resolved["record"]
        attributes = record.get("attributes") or {}
        if any(part.get("status") == "deleted" or part.get("deleted_at") for part in (record, attributes)):
            deleted_selections.append(selection)
            continue
        statuses[selection] = {
            "status": "available",
            "title": str(record.get("title") or ""),
            "leader_resident_id": str(attributes.get("leader_resident_id") or ""),
        }
    for selection in deleted_selections:
        if forget is not None:
            forget(selection)
    favorites = [item for item in payload.get("favorites", []) if item not in deleted_selections]
    return {**payload, "favorites": favorites, "favorite_statuses": statuses}
