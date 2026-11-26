"""Daily research-map policy, run through RunScript in the Project Lineage.

Only the existing ISA read/message/shared-state mechanisms are used. The
RunScript relay authenticates every call as the original caller.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

STATE_KEY = "research_map.digest_until"

STANCE_LABELS = {"candidate": "候选", "focus": "主攻", "explore": "探索", "watch": "观望", "concluded": "已收束", "dropped": "放弃"}
NOTE_LABELS = {"progress": "进展", "decision": "决定", "note": "批注"}
RELATION_LABELS = {"builds_on": "建立在……之上", "alternative": "互为替代", "depends_on": "依赖"}
FIELD_LABELS = {"title": "名称", "question": "问题", "approach": "打法", "expectation": "期望", "teams": "负责团队"}
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
        raise ValueError(f"since 不是 ISO 8601 时间：{value}") from exc
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



def build_digest(current, revisions, *, caller, since=None, until=None):
    until = until or datetime.now(timezone.utc).replace(microsecond=0)
    since = _since(since, until)
    if since > until:
        raise ValueError("research_map.digest_until 不能晚于本次截止时间")
    directions = {item["direction_id"]: item for item in current["directions"]}
    touched, goal_changes = {}, []
    for entry in revisions:
        if entry["changed_by"] == caller or not since < _since(entry["changed_at"], until) <= until:
            continue
        at_revision = {item["direction_id"]: item for item in entry["directions"]}
        for change in entry["changes"]:
            notes = {change.get("note_id"): change} if change.get("note_id") else {}
            ids, summary = _change_lines(change, at_revision, notes)
            line = {"revision": entry["revision"], "changed_at": entry["changed_at"],
                    "changed_by": entry["changed_by"], "summary": summary}
            if not ids:
                goal_changes.append(line)
            for direction_id in ids:
                touched.setdefault(direction_id, []).append(line)
    fresh = {
        direction_id: [item for item in items if item.get("modified_at")
                       and since < _since(item["modified_at"], until) <= until
                       and item.get("modified_by") != caller]
        for direction_id, items in current.get("outputs", {}).items()
    }
    order = list(directions) + [identifier for identifier in touched if identifier not in directions]
    entries = []
    for direction_id in order:
        changes, outputs = touched.get(direction_id, []), fresh.get(direction_id, [])
        if not changes and not outputs:
            continue
        item = directions.get(direction_id) or {"title": direction_id, "stance": "dropped", "teams": []}
        entries.append({"direction_id": direction_id, "title": item["title"], "stance": item["stance"],
                        "teams": item["teams"], "changes": changes, "outputs": outputs})
    count = len(entries) + len(goal_changes)
    return {"project_id": current["project_id"], "since": _iso(since), "until": _iso(until),
            "revision": current["revision"], "count": count, "directions": entries, "goal_changes": goal_changes,
            "prompt": _digest_prompt(current["project_id"], current["revision"], _iso(since), _iso(until),
                                     entries, goal_changes) if count else ""}


def run(call, *, lineage_id, caller, target, until=None):
    """Collect all pages. A rejected/unknown delivery never advances the watermark."""
    if not lineage_id or not caller or not target:
        raise ValueError("RunScript 身份、Lineage 和投递目标不能为空")
    until = until or datetime.now(timezone.utc).replace(microsecond=0)
    lineage = call("GetLineage", lineage_id=lineage_id)["lineage"]
    project_id = lineage.get("project_id")
    if not project_id:
        raise ValueError("每日研究地图整理需要 Project Lineage")
    reviewed = call("GetSharedState", lineage_id=lineage_id, key=STATE_KEY).get("value")
    current = call("GetResearchMap", project_id=project_id, include_outputs=True)
    revisions, cursor, seen = [], "", set()
    while True:
        page = call("ListResearchMapChanges", project_id=project_id, limit=500, **({"cursor": cursor} if cursor else {}))
        revisions.extend(page["changes"])
        cursor = page.get("next_cursor") or ""
        if not cursor:
            break
        if cursor in seen:
            raise ValueError("研究地图修改记录分页未向前推进")
        seen.add(cursor)
    digest = build_digest(current, revisions, caller=caller, since=reviewed, until=until)
    # Check for a concurrent/manual watermark change before sending anything.
    if call("GetSharedState", lineage_id=lineage_id, key=STATE_KEY).get("value") != reviewed:
        raise RuntimeError("研究地图整理水位已变化；本次未投递")
    result = {"status": "quiet", "count": digest["count"], "until": digest["until"]}
    if digest["count"]:
        sent = call("DeliverMessage", resident_id=target,
                    message={"content_parts": [{"type": "text", "text": digest["prompt"]}]})
        if not sent.get("message_id") or sent.get("accepted") is False:
            raise RuntimeError("研究地图整理投递未确认；保留水位")
        result.update(status="requested", message_id=sent["message_id"])
    updated = call("UpdateSharedState", lineage_id=lineage_id, key=STATE_KEY,
                   value=digest["until"], expected_value=reviewed)
    if updated.get("matched") is not True:
        raise RuntimeError("研究地图整理水位冲突；未覆盖新水位")
    return result


def main():
    from portal_script import inputs, isa, set_output

    request = inputs()
    # Identity fields are written by RunScript; arguments cannot override them.
    result = run(isa, lineage_id=request["lineage_id"], caller=request["caller_id"],
                 target=(request.get("arguments") or {})["target"])
    set_output(result)


if __name__ == "__main__":
    main()
