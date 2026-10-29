"""Portal Web handlers of the research map instructions (``web.instructions``).

The declarations are in ``research_map_instructions.py``; the map itself in
``research_map_store.py``.  A direction's outputs come from the resource
index: the readable resources of the Project whose ``links.related`` name it.
"""
from __future__ import annotations

from typing import Any

from management_portal.hatg.contracts import InstructionError, invalid, not_found
from management_portal.hatg.declarations import cursor_offset, offset_cursor
from management_portal.hatg.kernel import Invocation

PROJECTION = "research-map"
OUTPUTS_PER_DIRECTION = 30


def _store():
    from management_portal import research_map_store

    return research_map_store


def _project(project_id: str, actor: str) -> dict[str, Any]:
    from management_portal.resources import get_resource_service

    result = get_resource_service().query(actor=actor, resource_types=["project"], q=project_id, limit=20)
    for item in result.get("resources") or []:
        if str(item.get("resource_id") or "") == project_id:
            return item
    raise not_found(f"Project 不存在：{project_id}")


def _outputs(project_id: str, direction_ids: set[str], actor: str) -> dict[str, list[dict[str, Any]]]:
    from management_portal.resources import get_resource_service

    outputs: dict[str, list[dict[str, Any]]] = {}
    if not direction_ids:
        return outputs
    service = get_resource_service()
    offset = 0
    while True:
        page = service.query(actor=actor, projects=[project_id], include_attributes=True, limit=5000, offset=offset)
        resources = [item for item in page.get("resources") or [] if isinstance(item, dict)]
        for item in resources:
            for relation in (item.get("attributes") or {}).get("relations") or []:
                target = relation.get("target_id") if isinstance(relation, dict) else ""
                if relation.get("kind") == "related" and target in direction_ids:
                    outputs.setdefault(target, []).append({
                        "resource_id": str(item.get("resource_id") or ""), "resource_type": str(item.get("resource_type") or ""),
                        "title": str(item.get("title") or ""), "url": str(item.get("url") or ""),
                        "modified_at": str((item.get("source") or {}).get("modified_at") or ""),
                    })
        offset += len(resources)
        if not resources or offset >= int(page.get("total") or 0):
            break
    return {direction_id: sorted(items, key=lambda entry: entry["modified_at"], reverse=True)[:OUTPUTS_PER_DIRECTION]
            for direction_id, items in outputs.items()}


def _changed(project_id: str, actor: str, fields: list[str]) -> None:
    from management_portal.notifications import publish_projection_change

    publish_projection_change(PROJECTION, project_id, actor=actor, fields=fields)


def get_research_map(inv: Invocation) -> dict[str, Any]:
    project_id = inv.argument("project_id")
    project = _project(project_id, inv.caller)
    revision = inv.argument("revision")
    try:
        result = _store().read(project_id, revision=revision, goal=str(project.get("summary") or ""))
    except LookupError as exc:
        raise not_found(str(exc)) from exc
    # Outputs are what the index holds now, so a past revision shows none.
    wanted = revision is None and inv.argument("include_outputs") is not False
    ids = {item["direction_id"] for item in result["directions"]}
    result["outputs"] = _outputs(project_id, ids, inv.caller) if wanted else {}
    return result


def apply_research_map_changes(inv: Invocation) -> dict[str, Any]:
    from management_common.principal import actor_principal

    project_id = inv.argument("project_id")
    project = _project(project_id, inv.caller)
    changes = inv.argument("changes") or []
    teams = {team for change in changes if isinstance(change, dict)
             for team in (change.get("teams") or (change.get("fields") or {}).get("teams") or [])
             if isinstance(team, str)}
    if teams:
        from management_portal.society_service import get_society_service

        team = get_society_service().project.project_teams([project_id], inv.caller)[project_id]
        known = {team["project_lineage_id"], *(item["lineage_id"] for item in team["lineages"])} - {""}
        unknown = sorted(teams - known)
        if unknown:
            raise invalid("teams 只能是这个项目中你能读到的 Lineage：" + "、".join(unknown))
    store = _store()
    try:
        result = store.apply(project_id, inv.argument("expected_revision"), changes, actor=inv.caller,
                             resident=actor_principal(inv.caller).kind == "resident",
                             goal=str(project.get("summary") or ""))
    except store.MapConflict as exc:
        raise InstructionError("instruction.version_conflict", str(exc),
                               details={"project_id": project_id, "current_revision": exc.current}) from exc
    except ValueError as exc:
        raise invalid(str(exc)) from exc
    _changed(project_id, inv.caller, ["map"])
    return result


def list_research_map_changes(inv: Invocation) -> dict[str, Any]:
    project_id = inv.argument("project_id")
    _project(project_id, inv.caller)
    limit = inv.argument("limit") or 100
    after = cursor_offset(inv.argument("cursor"))
    changes = _store().history(project_id, after=after, limit=limit + 1)
    page = {"changes": changes[:limit]}
    if len(changes) > limit:
        page["next_cursor"] = offset_cursor(changes[limit - 1]["revision"])
    return page


def update_research_map_layout(inv: Invocation) -> dict[str, Any]:
    project_id = inv.argument("project_id")
    _project(project_id, inv.caller)
    try:
        result = _store().update_layout(project_id, inv.argument("positions"), actor=inv.caller)
    except LookupError as exc:
        raise not_found(str(exc)) from exc
    except ValueError as exc:
        raise invalid(str(exc)) from exc
    _changed(project_id, inv.caller, ["layout"])
    return {"layout": result["layout"], "layout_revision": result["layout_revision"]}


HANDLERS = {
    "GetResearchMap": get_research_map,
    "ApplyResearchMapChanges": apply_research_map_changes,
    "ListResearchMapChanges": list_research_map_changes,
    "UpdateResearchMapLayout": update_research_map_layout,
}
