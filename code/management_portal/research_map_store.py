"""Research maps: each Project's research directions and how they move.

A Project has one map.  Its document holds the goal, the directions (a tree
with an order among siblings) and the relations between directions; each
change commits a new revision whose document is kept, so the map can be
shown as it was at any revision.  Notes (progress, the people's decisions,
remarks) hang on directions and keep their time; node positions are kept
apart and are not revisions.  Directions are resources ``direction_<hex>``
that the permission system places under their Project.

Validation and the change operations are plain functions over the document
(``apply_changes``); the store only adds identity, locking and history.
"""
from __future__ import annotations

import os
import re
import secrets
from copy import deepcopy
from datetime import datetime, timezone
from functools import lru_cache
from typing import Any, Callable, Iterable

from management_common.domain_database import DomainDatabase

STANCES = ("candidate", "focus", "explore", "watch", "concluded", "dropped")
RELATION_KINDS = ("builds_on", "alternative", "depends_on")
NOTE_KINDS = ("progress", "decision", "note")
DIRECTION_ID = re.compile(r"direction_[0-9a-f]{12}")
LINEAGE_ID = re.compile(r"lineage_[0-9a-f]{12}")
TEXT_LIMITS = {"title": 200, "question": 4000, "approach": 4000, "expectation": 4000, "proposal_reason": 2000,
               "goal": 4000, "note": 8000, "conclusion": 8000}
MAX_DIRECTIONS = 500
MAX_REFS = 40
NOTES_PER_DIRECTION = 20

DDL = """
CREATE TABLE IF NOT EXISTS maps (project_id TEXT PRIMARY KEY, revision BIGINT NOT NULL, document JSONB NOT NULL,
 layout JSONB NOT NULL, layout_revision BIGINT NOT NULL, updated_at TIMESTAMPTZ NOT NULL, updated_by TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS revisions (project_id TEXT NOT NULL, revision BIGINT NOT NULL, changed_at TIMESTAMPTZ NOT NULL,
 changed_by TEXT NOT NULL, changes JSONB NOT NULL, document JSONB NOT NULL, PRIMARY KEY (project_id, revision));
CREATE TABLE IF NOT EXISTS directions (direction_id TEXT PRIMARY KEY, project_id TEXT NOT NULL, created_by TEXT NOT NULL,
 removed BOOLEAN NOT NULL);
CREATE INDEX IF NOT EXISTS directions_project_idx ON directions(project_id);
CREATE TABLE IF NOT EXISTS notes (note_id TEXT PRIMARY KEY, project_id TEXT NOT NULL, direction_id TEXT NOT NULL,
 revision BIGINT NOT NULL, noted_at TIMESTAMPTZ NOT NULL, noted_by TEXT NOT NULL, kind TEXT NOT NULL, text TEXT NOT NULL,
 refs JSONB NOT NULL);
CREATE INDEX IF NOT EXISTS notes_direction_idx ON notes(project_id, direction_id, noted_at);
"""


class MapConflict(Exception):
    """The map moved past the revision the change was made against."""

    def __init__(self, current: int):
        super().__init__(f"研究地图已更新到修订 {current}")
        self.current = current


@lru_cache(maxsize=8)
def _database(dsn, namespace):
    return DomainDatabase("research_map", "maps", DDL)


def database() -> DomainDatabase:
    return _database(os.getenv("PORTAL_DATABASE_URL"), os.getenv("PORTAL_DATABASE_NAMESPACE"))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _iso(value: Any) -> str:
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    return str(value or "")


def empty_document(goal: str = "") -> dict[str, Any]:
    return {"goal": goal, "directions": [], "relations": []}


def _text(value: Any, field: str, *, required: bool = False) -> str:
    if value is None:
        value = ""
    if not isinstance(value, str):
        raise ValueError(f"{field} 必须是文本")
    text = value.strip()
    if required and not text:
        raise ValueError(f"{field} 不能为空")
    limit = TEXT_LIMITS.get(field, 4000)
    if len(text) > limit:
        raise ValueError(f"{field} 不能超过 {limit} 个字符")
    return text


def _stance(value: Any) -> str:
    if value not in STANCES:
        raise ValueError(f"stance 只能是 {'、'.join(STANCES)}")
    return value


def _teams(value: Any) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) and LINEAGE_ID.fullmatch(item) for item in value):
        raise ValueError("teams 必须是 lineage_<12 位十六进制> 的数组")
    return list(dict.fromkeys(value))


def _refs(value: Any) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(item, str) and item.strip() for item in value):
        raise ValueError("refs 必须是资源地址的数组")
    refs = list(dict.fromkeys(item.strip() for item in value))
    if len(refs) > MAX_REFS:
        raise ValueError(f"refs 最多 {MAX_REFS} 项")
    return refs


def _index(document: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {item["direction_id"]: item for item in document["directions"]}


def _children(document: dict[str, Any], parent_id: str) -> list[dict[str, Any]]:
    return [item for item in document["directions"] if item["parent_id"] == parent_id]


def _ancestors(directions: dict[str, dict[str, Any]], direction_id: str) -> set[str]:
    found: set[str] = set()
    current = directions.get(direction_id, {}).get("parent_id", "")
    while current and current not in found:
        found.add(current)
        current = directions.get(current, {}).get("parent_id", "")
    return found


def _place(document: dict[str, Any], item: dict[str, Any], index: Any) -> None:
    """Put ``item`` among its siblings at ``index`` (the end when omitted); siblings keep list order."""
    siblings = [other for other in _children(document, item["parent_id"]) if other is not item]
    position = len(siblings) if index is None else index
    if type(position) is not int or not 0 <= position <= len(siblings):
        raise ValueError(f"index 必须是 0 到 {len(siblings)} 之间的整数")
    document["directions"] = [other for other in document["directions"] if other is not item]
    if position < len(siblings):
        at = document["directions"].index(siblings[position])
    elif siblings:
        at = document["directions"].index(siblings[-1]) + 1
    else:
        at = len(document["directions"])
    document["directions"].insert(at, item)


def apply_changes(document: dict[str, Any], changes: Iterable[Any], *, actor: str, now: str,
                  new_id: Callable[[], str], resident: bool) -> dict[str, Any]:
    """Apply ``changes`` to a copy of ``document``.

    Returns ``{"document", "applied", "created", "removed", "notes"}``: the
    new document, the changes as applied (with real IDs), the IDs a change
    named by ``key`` received, the removed direction IDs and the notes to add.
    A later change may name a direction added in the same call as ``$<key>``.
    """
    document = deepcopy(document)
    created: dict[str, str] = {}
    removed: list[str] = []
    notes: list[dict[str, Any]] = []
    applied: list[dict[str, Any]] = []

    def direction_ref(value: Any, field: str, *, allow_empty: bool = False) -> str:
        if allow_empty and value in (None, ""):
            return ""
        if isinstance(value, str) and value.startswith("$") and value[1:] in created:
            value = created[value[1:]]
        if not isinstance(value, str) or value not in _index(document):
            raise ValueError(f"{field} 不是这张地图上的方向：{value}")
        return value

    for position, change in enumerate(changes):
        if not isinstance(change, dict) or not isinstance(change.get("op"), str):
            raise ValueError(f"第 {position + 1} 项改动缺少 op")
        op = change["op"]
        try:
            if op == "set_goal":
                document["goal"] = _text(change.get("goal"), "goal")
                applied.append({"op": op, "goal": document["goal"]})
            elif op == "add_direction":
                if len(document["directions"]) >= MAX_DIRECTIONS:
                    raise ValueError(f"一张地图最多 {MAX_DIRECTIONS} 个方向")
                key = change.get("key")
                if key is not None and (not isinstance(key, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,40}", key) or key in created):
                    raise ValueError("key 必须是本次提交中唯一的 1–40 个字母、数字、_ 或 -")
                direction_id = new_id()
                item = {
                    "direction_id": direction_id,
                    "parent_id": direction_ref(change.get("parent_id"), "parent_id", allow_empty=True),
                    "title": _text(change.get("title"), "title", required=True),
                    "question": _text(change.get("question"), "question"),
                    "approach": _text(change.get("approach"), "approach"),
                    "stance": _stance(change.get("stance") or ("candidate" if resident else "explore")),
                    "teams": _teams(change.get("teams") or []),
                    "expectation": _text(change.get("expectation"), "expectation"),
                    "conclusion": None,
                    "proposed_by": actor,
                    "proposal_reason": _text(change.get("proposal_reason"), "proposal_reason"),
                    "created_at": now, "created_by": actor, "updated_at": now, "updated_by": actor,
                }
                document["directions"].append(item)
                _place(document, item, change.get("index"))
                if key:
                    created[key] = direction_id
                applied.append({"op": op, "direction_id": direction_id, "parent_id": item["parent_id"],
                                "title": item["title"], "stance": item["stance"]})
            elif op == "update_direction":
                direction_id = direction_ref(change.get("direction_id"), "direction_id")
                fields = change.get("fields")
                allowed = {"title", "question", "approach", "stance", "teams", "expectation", "conclusion"}
                if not isinstance(fields, dict) or not fields or set(fields) - allowed:
                    raise ValueError("fields 只能包含 " + "、".join(sorted(allowed)))
                item = _index(document)[direction_id]
                for field, value in fields.items():
                    if field == "stance":
                        item["stance"] = _stance(value)
                    elif field == "teams":
                        item["teams"] = _teams(value)
                    elif field == "conclusion":
                        text = _text(value, "conclusion")
                        item["conclusion"] = {"text": text, "by": actor, "at": now} if text else None
                    else:
                        item[field] = _text(value, field, required=field == "title")
                item["updated_at"], item["updated_by"] = now, actor
                applied.append({"op": op, "direction_id": direction_id, "fields": sorted(fields)})
            elif op == "move_direction":
                direction_id = direction_ref(change.get("direction_id"), "direction_id")
                parent_id = direction_ref(change.get("parent_id"), "parent_id", allow_empty=True)
                directions = _index(document)
                if parent_id == direction_id or direction_id in _ancestors(directions, parent_id):
                    raise ValueError("方向不能移到它自己或它的子方向之下")
                item = directions[direction_id]
                item["parent_id"] = parent_id
                _place(document, item, change.get("index"))
                item["updated_at"], item["updated_by"] = now, actor
                applied.append({"op": op, "direction_id": direction_id, "parent_id": parent_id})
            elif op == "remove_direction":
                direction_id = direction_ref(change.get("direction_id"), "direction_id")
                item = _index(document)[direction_id]
                # Its sub-directions move up to its parent, in its place.
                for child in _children(document, direction_id):
                    child["parent_id"] = item["parent_id"]
                document["directions"] = [other for other in document["directions"] if other is not item]
                document["relations"] = [relation for relation in document["relations"]
                                         if direction_id not in (relation["from"], relation["to"])]
                removed.append(direction_id)
                applied.append({"op": op, "direction_id": direction_id})
            elif op in {"add_relation", "remove_relation"}:
                source = direction_ref(change.get("from"), "from")
                target = direction_ref(change.get("to"), "to")
                kind = change.get("kind")
                if kind not in RELATION_KINDS:
                    raise ValueError(f"kind 只能是 {'、'.join(RELATION_KINDS)}")
                if source == target:
                    raise ValueError("关系的两端不能是同一个方向")
                relation = {"from": source, "to": target, "kind": kind}
                exists = relation in document["relations"]
                if op == "add_relation" and not exists:
                    document["relations"].append(relation)
                if op == "remove_relation" and exists:
                    document["relations"].remove(relation)
                applied.append({"op": op, **relation})
            elif op == "add_note":
                direction_id = direction_ref(change.get("direction_id"), "direction_id")
                kind = change.get("kind") or "note"
                if kind not in NOTE_KINDS:
                    raise ValueError(f"kind 只能是 {'、'.join(NOTE_KINDS)}")
                note = {"note_id": secrets.token_hex(8), "direction_id": direction_id, "kind": kind,
                        "text": _text(change.get("text"), "note", required=True), "refs": _refs(change.get("refs")),
                        "noted_at": now, "noted_by": actor}
                notes.append(note)
                applied.append({"op": op, "direction_id": direction_id, "kind": kind, "note_id": note["note_id"]})
            else:
                raise ValueError(f"未知的改动：{op}")
        except ValueError as exc:
            raise ValueError(f"第 {position + 1} 项改动（{op}）：{exc}") from None
    if not applied:
        raise ValueError("changes 不能为空")
    return {"document": document, "applied": applied, "created": created, "removed": removed, "notes": notes}


def _new_direction_id(connection) -> str:
    while True:
        candidate = "direction_" + secrets.token_hex(6)
        if not connection.execute("SELECT 1 FROM directions WHERE direction_id=%s", (candidate,)).fetchone():
            return candidate


def _map_row(connection, project_id: str, *, lock: bool = False) -> dict[str, Any] | None:
    row = connection.execute(
        "SELECT revision, document, layout, layout_revision, updated_at, updated_by FROM maps WHERE project_id=%s"
        + (" FOR UPDATE" if lock else ""), (project_id,)).fetchone()
    return dict(row) if row else None


def _notes(connection, project_id: str, *, revision: int | None = None) -> list[dict[str, Any]]:
    """The latest notes of each direction, newest first (at or before ``revision`` when given)."""
    rows = connection.execute(
        "SELECT note_id, direction_id, revision, noted_at, noted_by, kind, text, refs FROM ("
        " SELECT *, row_number() OVER (PARTITION BY direction_id ORDER BY noted_at DESC, note_id) AS n"
        " FROM notes WHERE project_id=%s AND (%s::bigint IS NULL OR revision <= %s::bigint)) ranked"
        " WHERE n <= %s ORDER BY noted_at DESC, note_id",
        (project_id, revision, revision, NOTES_PER_DIRECTION)).fetchall()
    return [{**dict(row), "noted_at": _iso(row["noted_at"])} for row in rows]


def read(project_id: str, *, revision: int | None = None, goal: str = "") -> dict[str, Any]:
    """The map now, or as it was at ``revision``; a Project without a map has revision 0."""
    with database().read() as connection:
        current = _map_row(connection, project_id)
        if current is None:
            if revision not in (None, 0):
                raise LookupError(f"研究地图没有修订 {revision}")
            return {"project_id": project_id, "revision": 0, "updated_at": "", "updated_by": "", **empty_document(goal),
                    "layout": {}, "layout_revision": 0, "notes": []}
        document, changed_at, changed_by = current["document"], current["updated_at"], current["updated_by"]
        if revision is not None and revision != current["revision"]:
            row = connection.execute("SELECT document, changed_at, changed_by FROM revisions WHERE project_id=%s AND revision=%s",
                                     (project_id, revision)).fetchone()
            if row is None:
                raise LookupError(f"研究地图没有修订 {revision}")
            document, changed_at, changed_by = row["document"], row["changed_at"], row["changed_by"]
        return {"project_id": project_id, "revision": revision if revision is not None else current["revision"],
                "updated_at": _iso(changed_at), "updated_by": changed_by, **document,
                "layout": current["layout"], "layout_revision": current["layout_revision"],
                "notes": _notes(connection, project_id, revision=revision)}


def history(project_id: str, *, after: int = 0, limit: int = 100) -> list[dict[str, Any]]:
    """Revisions after ``after`` in order: who changed what, and the directions as they then stood."""
    with database().read() as connection:
        rows = connection.execute(
            "SELECT revision, changed_at, changed_by, changes, document FROM revisions"
            " WHERE project_id=%s AND revision > %s ORDER BY revision LIMIT %s", (project_id, after, limit)).fetchall()
    return [{"revision": row["revision"], "changed_at": _iso(row["changed_at"]), "changed_by": row["changed_by"],
             "changes": row["changes"],
             "directions": [{key: item.get(key) for key in ("direction_id", "parent_id", "title", "stance", "teams")}
                            for item in row["document"]["directions"]]}
            for row in rows]


def apply(project_id: str, expected_revision: int, changes: list[Any], *, actor: str, resident: bool,
          goal: str = "") -> dict[str, Any]:
    """Commit ``changes`` as the next revision, refusing a map that moved past ``expected_revision``."""
    from psycopg.types.json import Jsonb

    now = _now()
    with database().transaction(key=project_id) as connection:
        current = _map_row(connection, project_id, lock=True)
        revision = current["revision"] if current else 0
        if expected_revision != revision:
            raise MapConflict(revision)
        document = current["document"] if current else empty_document(goal)
        result = apply_changes(document, changes, actor=actor, now=now, new_id=lambda: _new_direction_id(connection),
                               resident=resident)
        revision += 1
        if current:
            connection.execute("UPDATE maps SET revision=%s, document=%s, updated_at=%s, updated_by=%s WHERE project_id=%s",
                               (revision, Jsonb(result["document"]), now, actor, project_id))
        else:
            connection.execute("INSERT INTO maps VALUES (%s,%s,%s,%s,0,%s,%s)",
                               (project_id, revision, Jsonb(result["document"]), Jsonb({}), now, actor))
        connection.execute("INSERT INTO revisions VALUES (%s,%s,%s,%s,%s,%s)",
                           (project_id, revision, now, actor, Jsonb(result["applied"]), Jsonb(result["document"])))
        for change in result["applied"]:
            if change["op"] == "add_direction":
                connection.execute("INSERT INTO directions VALUES (%s,%s,%s,FALSE)", (change["direction_id"], project_id, actor))
        for direction_id in result["removed"]:
            connection.execute("UPDATE directions SET removed=TRUE WHERE direction_id=%s", (direction_id,))
        for note in result["notes"]:
            connection.execute("INSERT INTO notes VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                               (note["note_id"], project_id, note["direction_id"], revision, note["noted_at"],
                                note["noted_by"], note["kind"], note["text"], Jsonb(note["refs"])))
    return {"project_id": project_id, "revision": revision, "created": result["created"], "applied": result["applied"]}


def update_layout(project_id: str, positions: dict[str, Any], *, actor: str) -> dict[str, Any]:
    """Merge node positions (``{direction_id: {"x", "y"}}``; ``null`` drops one) into the map's layout."""
    from psycopg.types.json import Jsonb

    if not isinstance(positions, dict) or not positions:
        raise ValueError("positions 不能为空")
    with database().transaction(key=project_id) as connection:
        current = _map_row(connection, project_id, lock=True)
        if current is None:
            raise LookupError("这个项目还没有研究地图")
        known = _index(current["document"])
        layout = dict(current["layout"])
        for direction_id, position in positions.items():
            if direction_id not in known:
                raise ValueError(f"不是这张地图上的方向：{direction_id}")
            if position is None:
                layout.pop(direction_id, None)
                continue
            if not isinstance(position, dict) or set(position) != {"x", "y"} or not all(
                    isinstance(position[axis], (int, float)) and not isinstance(position[axis], bool) and abs(position[axis]) < 1e6
                    for axis in ("x", "y")):
                raise ValueError("位置必须是 {x, y} 数值")
            layout[direction_id] = {"x": round(float(position["x"]), 1), "y": round(float(position["y"]), 1)}
        layout_revision = current["layout_revision"] + 1
        connection.execute("UPDATE maps SET layout=%s, layout_revision=%s WHERE project_id=%s",
                           (Jsonb(layout), layout_revision, project_id))
    return {"project_id": project_id, "layout": layout, "layout_revision": layout_revision, "updated_by": actor}


def direction(direction_id: str) -> dict[str, Any] | None:
    """The Project of a direction and whether it was removed; None for an unknown ID."""
    if not DIRECTION_ID.fullmatch(str(direction_id or "")):
        return None
    with database().read() as connection:
        row = connection.execute("SELECT project_id, created_by, removed FROM directions WHERE direction_id=%s",
                                 (direction_id,)).fetchone()
    return dict(row) if row else None


def permission_records() -> list[dict[str, Any]]:
    """Every direction on a map, placed under its Project (``permission.records``)."""
    with database().read() as connection:
        rows = connection.execute("SELECT direction_id, project_id, created_by FROM directions WHERE NOT removed").fetchall()
    return [{"resource_id": row["direction_id"], "resource_type": "direction", "project_id": row["project_id"],
             "created_by": row["created_by"]} for row in rows]


def project_relations(project_id: str, new_project_id: str = "", *, write: bool = True) -> list[str]:
    """Move a Project's map to its new ID, or delete it with the Project (``project.relations``)."""
    with (database().transaction(key=project_id) if write else database().read()) as connection:
        ids = [row["direction_id"] for row in connection.execute(
            "SELECT direction_id FROM directions WHERE project_id=%s ORDER BY direction_id", (project_id,)).fetchall()]
        if not write:
            return ids
        tables = ("maps", "revisions", "directions", "notes")
        if new_project_id:
            for table in tables:
                connection.execute(f"UPDATE {table} SET project_id=%s WHERE project_id=%s", (new_project_id, project_id))
        else:
            for table in tables:
                connection.execute(f"DELETE FROM {table} WHERE project_id=%s", (project_id,))
    return ids
