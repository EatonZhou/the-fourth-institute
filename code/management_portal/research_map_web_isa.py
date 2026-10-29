"""Portal Web handlers of the research map instructions (``web.instructions``).

The declarations are in ``research_map_instructions.py``; the map itself in
``research_map_store.py``.  A direction's outputs come from the resource
index: the readable resources of the Project whose ``links.related`` name it.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
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
    from management_portal import research_map_automation

    research_map_automation.ensure(project_id, inv.caller)
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


STANCE_LABELS = {"candidate": "候选", "focus": "主攻", "explore": "探索", "watch": "观望", "concluded": "已收束", "dropped": "放弃"}
NOTE_LABELS = {"progress": "进展", "decision": "决定", "note": "批注"}
RELATION_LABELS = {"builds_on": "建立在……之上", "alternative": "互为替代", "depends_on": "依赖"}
FIELD_LABELS = {"title": "名称", "question": "问题", "approach": "打法", "expectation": "期望", "teams": "负责团队"}
DIGEST_DIRECTIONS = 60
DIGEST_LINE = 300


def _clip(text: Any, limit: int = DIGEST_LINE) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[:limit - 1] + "…"


def _since(value: Any, until: datetime) -> datetime:
    if value in (None, ""):
        return until - timedelta(hours=24)
    try:
        moment = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError as exc:
        raise invalid(f"since 不是 ISO 8601 时间：{value}") from exc
    return (moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)


def _iso(moment: datetime) -> str:
    return moment.isoformat(timespec="seconds").replace("+00:00", "Z")


def _change_lines(change: dict[str, Any], directions: dict[str, dict[str, Any]],
                  notes: dict[str, dict[str, Any]]) -> tuple[list[str], str]:
    """The directions one applied change concerns and how it reads in the digest."""
    title = lambda direction_id: (directions.get(direction_id) or {}).get("title") or direction_id  # noqa: E731
    op = change.get("op")
    direction_id = str(change.get("direction_id") or "")
    if op == "add_direction":
        reason = (directions.get(direction_id) or {}).get("proposal_reason")
        return [direction_id], (f"新增方向（{STANCE_LABELS.get(change.get('stance'), change.get('stance'))}）"
                                + (f"，理由：{_clip(reason)}" if reason else ""))
    if op == "update_direction":
        current = directions.get(direction_id) or {}
        parts = []
        for field in change.get("fields") or []:
            if field == "stance":
                parts.append(f"姿态改为{STANCE_LABELS.get(current.get('stance'), current.get('stance') or '')}")
            elif field == "conclusion":
                conclusion = (current.get("conclusion") or {}).get("text")
                parts.append(f"结论：{_clip(conclusion)}" if conclusion else "清除了结论")
            else:
                parts.append(f"修改{FIELD_LABELS.get(field, field)}")
        return [direction_id], "；".join(parts)
    if op == "move_direction":
        parent = change.get("parent_id")
        return [direction_id], f"移到「{title(parent)}」之下" if parent else "移为顶层方向"
    if op == "remove_direction":
        return [direction_id], "删除了这个方向"
    if op in {"add_relation", "remove_relation"}:
        verb = "连接" if op == "add_relation" else "去掉关系"
        return [change["from"], change["to"]], (
            f"{verb}：「{title(change['from'])}」{RELATION_LABELS.get(change.get('kind'), change.get('kind'))}「{title(change['to'])}」")
    if op == "add_note":
        note = notes.get(str(change.get("note_id") or "")) or {}
        refs = "、".join(note.get("refs") or [])
        return [direction_id], (f"{NOTE_LABELS.get(change.get('kind'), change.get('kind'))}：{_clip(note.get('text'))}"
                                + (f"（引用 {refs}）" if refs else ""))
    if op == "set_goal":
        return [], f"目标改为：{_clip(change.get('goal'))}"
    return [direction_id] if direction_id else [], str(op or "")


def _digest_prompt(project_id: str, revision: int, since: str, until: str, entries: list[dict[str, Any]],
                   goal_changes: list[dict[str, Any]]) -> str:
    from management_portal import research_map_automation

    lines = [f"研究地图每日整理：项目 {project_id} 在 {since} 至 {until}（UTC）之间有新动静。",
             f"按 {research_map_automation.skill_path()} 的步骤整理下面列出的方向；没有列出的方向这次不用看。",
             f"地图：/projects?project={project_id}&view=map（当前修订 {revision}）", ""]
    for change in goal_changes:
        lines.append(f"- 修订 {change['revision']} · {change['changed_by']}：{change['summary']}")
    for number, entry in enumerate(entries, 1):
        teams = "、".join(entry["teams"])
        lines.append(f"{number}. 《{entry['title']}》{entry['direction_id']} · {STANCE_LABELS.get(entry['stance'], entry['stance'])}"
                     + (f" · 负责团队 {teams}" if teams else ""))
        for change in entry["changes"]:
            lines.append(f"   - 修订 {change['revision']} · {change['changed_by']}：{change['summary']}")
        for output in entry["outputs"]:
            lines.append(f"   - 新产出：{output['resource_type']}《{_clip(output['title'], 120)}》{output['resource_id']}")
    return "\n".join(lines)


def get_research_map_digest(inv: Invocation) -> dict[str, Any]:
    project_id = inv.argument("project_id")
    project = _project(project_id, inv.caller)
    until = datetime.now(timezone.utc).replace(microsecond=0)
    since = _since(inv.argument("since"), until)
    store = _store()
    current = store.read(project_id, goal=str(project.get("summary") or ""))
    directions = {item["direction_id"]: item for item in current["directions"]}
    revisions = store.activity(project_id, since=_iso(since), until=_iso(until), exclude=inv.caller)
    notes = store.notes_by_id(project_id, (change.get("note_id") for entry in revisions for change in entry["changes"]))
    touched: dict[str, list[dict[str, Any]]] = {}
    goal_changes: list[dict[str, Any]] = []
    for entry in revisions:
        for change in entry["changes"]:
            ids, summary = _change_lines(change, directions, notes)
            line = {"revision": entry["revision"], "changed_at": entry["changed_at"], "changed_by": entry["changed_by"],
                    "summary": summary}
            if not ids:
                goal_changes.append(line)
            for direction_id in ids:
                touched.setdefault(direction_id, []).append(line)
    fresh = {direction_id: [item for item in items if item["modified_at"] > _iso(since)]
             for direction_id, items in _outputs(project_id, set(directions), inv.caller).items()}
    order = [item["direction_id"] for item in current["directions"]]
    order += [direction_id for direction_id in touched if direction_id not in directions]
    entries = []
    for direction_id in order:
        changes, outputs = touched.get(direction_id, []), fresh.get(direction_id, [])
        if not changes and not outputs:
            continue
        item = directions.get(direction_id) or {"title": direction_id, "stance": "dropped", "teams": []}
        entries.append({"direction_id": direction_id, "title": item["title"], "stance": item["stance"],
                        "teams": item["teams"], "changes": changes, "outputs": outputs})
    entries = entries[:DIGEST_DIRECTIONS]
    count = len(entries) + len(goal_changes)
    return {"project_id": project_id, "since": _iso(since), "until": _iso(until), "revision": current["revision"],
            "count": count, "directions": entries, "goal_changes": goal_changes,
            "prompt": _digest_prompt(project_id, current["revision"], _iso(since), _iso(until), entries, goal_changes)
            if count else ""}


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
    "GetResearchMapDigest": get_research_map_digest,
}
