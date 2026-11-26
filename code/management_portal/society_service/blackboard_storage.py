"""Blackboards (decision_ed2c1ed5c245): the graph objects, their entries and read positions.

A blackboard is a Lineage graph object (a component of kind ``blackboard``) and a Resident
joins it by an ``attach`` wire.  Both are kept in their own tables, apart from
``lineage_nodes`` and ``lineage_edges``: those tables are hot and their kind lists are
constraints, so a new kind of object is stored beside them and merged into the definition
when it is read (``read_many``) and split from it when it is written (``put``).

Entries live in ``blackboard_entries``.  ``blackboard_heads`` holds the per-board counters
and is the lock that serializes every write, purge, retraction and read-position change of
one board, so ``seq`` is strictly increasing and never reused.
"""
from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Mapping

from management_common.database_time import decode_times, instant, iso_time
from management_common.json_columns import json_text, json_value

BOARD_KIND = "blackboard"
ATTACH_KIND = "attach"
ATTACH_ACCESS = ("read", "write", "read_write")
BOARD_ID_PREFIX = "blackboard_"

DEFAULT_MAX_ENTRIES = 200
MAX_ENTRIES_LIMIT = 1000
DEFAULT_MAX_TOTAL_BYTES = 1024 * 1024
MIN_TOTAL_BYTES = 64 * 1024
MAX_TOTAL_BYTES_LIMIT = 32 * 1024 * 1024
MAX_AGE_SECONDS_LIMIT = 10 * 365 * 24 * 3600
MAX_KIND_CHARS = 32
MAX_TEXT_BYTES = 16 * 1024
MAX_DATA_BYTES = 16 * 1024
MAX_REFS = 20
MAX_REF_CHARS = 300
PAGE_DEFAULT = 50
PAGE_MAX = 200
# What a retracted entry still occupies: its header, no body.
RETRACTED_BYTES = 0


def ensure_schema(connection: Any) -> None:
    """Create the blackboard tables when absent (new tables only; never alters a live one)."""

    present = connection.execute(
        "SELECT to_regclass(quote_ident(current_schema()) || '.blackboard_entries') AS relation"
    ).fetchone()["relation"]
    if present is not None:
        return
    connection.execute("""
        CREATE TABLE IF NOT EXISTS lineage_blackboards (
            board_id TEXT PRIMARY KEY,
            lineage_id TEXT NOT NULL,
            title TEXT,
            client_key TEXT,
            created_by_principal_id TEXT,
            config_json JSONB,
            deleted_at TIMESTAMPTZ)""")
    connection.execute(
        "CREATE INDEX IF NOT EXISTS lineage_blackboards_lineage_idx "
        "ON lineage_blackboards(lineage_id) WHERE deleted_at IS NULL")
    connection.execute("""
        CREATE TABLE IF NOT EXISTS lineage_blackboard_bindings (
            wire_id TEXT PRIMARY KEY,
            lineage_id TEXT NOT NULL,
            wire_kind TEXT NOT NULL CHECK (wire_kind IN ('attach','watch')),
            source_id TEXT NOT NULL,
            board_id TEXT NOT NULL,
            label TEXT,
            client_key TEXT,
            config_json JSONB,
            deleted_at TIMESTAMPTZ)""")
    connection.execute(
        "CREATE INDEX IF NOT EXISTS lineage_blackboard_bindings_board_idx "
        "ON lineage_blackboard_bindings(board_id, wire_kind, source_id) WHERE deleted_at IS NULL")
    connection.execute(
        "CREATE INDEX IF NOT EXISTS lineage_blackboard_bindings_lineage_idx "
        "ON lineage_blackboard_bindings(lineage_id) WHERE deleted_at IS NULL")
    connection.execute("""
        CREATE TABLE IF NOT EXISTS blackboard_heads (
            board_id TEXT PRIMARY KEY,
            next_seq BIGINT NOT NULL DEFAULT 1,
            entry_count BIGINT NOT NULL DEFAULT 0,
            total_bytes BIGINT NOT NULL DEFAULT 0)""")
    connection.execute("""
        CREATE TABLE IF NOT EXISTS blackboard_entries (
            board_id TEXT NOT NULL,
            seq BIGINT NOT NULL,
            entry_id TEXT NOT NULL UNIQUE,
            author_id TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL,
            kind TEXT NOT NULL DEFAULT '',
            body TEXT NOT NULL DEFAULT '',
            data_json JSONB,
            refs_json JSONB NOT NULL DEFAULT '[]',
            supersedes BIGINT,
            expires_at TIMESTAMPTZ,
            retracted BOOLEAN NOT NULL DEFAULT FALSE,
            retracted_at TIMESTAMPTZ,
            size_bytes BIGINT NOT NULL DEFAULT 0,
            PRIMARY KEY (board_id, seq))""")
    connection.execute(
        "CREATE INDEX IF NOT EXISTS blackboard_entries_supersedes_idx "
        "ON blackboard_entries(board_id, supersedes) WHERE supersedes IS NOT NULL")
    connection.execute("""
        CREATE TABLE IF NOT EXISTS blackboard_read_positions (
            board_id TEXT NOT NULL,
            reader_id TEXT NOT NULL,
            read_seq BIGINT NOT NULL DEFAULT 0,
            updated_at TIMESTAMPTZ NOT NULL,
            PRIMARY KEY (board_id, reader_id))""")


# --- Graph objects: split from and merged into a Lineage definition -----------------------

def is_board(item: Mapping[str, Any]) -> bool:
    return str(item.get("kind") or "") == BOARD_KIND


def board_ids(value: Mapping[str, Any]) -> frozenset[str]:
    program = value.get("program") or {}
    return frozenset(str(item["id"]) for item in program.get("components") or [] if is_board(item))


def is_binding(item: Mapping[str, Any], boards: frozenset[str]) -> bool:
    """A wire that ends at a blackboard: an ``attach`` from a Resident, or a ``watch`` of an Event Source.

    Such a wire is stored with the blackboard, not in ``lineage_edges``: that table's foreign keys
    point at ``lineage_nodes``, which does not hold blackboards.
    """

    kind = str(item.get("kind") or item.get("type") or "")
    return kind == ATTACH_KIND or (kind == "watch" and str(item.get("target") or "") in boards)


def definition_objects(value: Mapping[str, Any]) -> tuple[list[dict], list[dict]]:
    program = value.get("program") or {}
    boards = [item for item in program.get("components") or [] if is_board(item)]
    ids = board_ids(value)
    bindings = [item for item in program.get("wires") or [] if is_binding(item, ids)]
    return boards, bindings


def put(connection: Any, lineage_id: str, value: Mapping[str, Any]) -> None:
    """Write the blackboards and the wires that end at them (the rest is the graph's)."""

    boards, attachments = definition_objects(value)
    for item in boards:
        connection.execute(
            "INSERT INTO lineage_blackboards(board_id,lineage_id,title,client_key,created_by_principal_id,config_json) "
            "VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT(board_id) DO UPDATE SET lineage_id=excluded.lineage_id,"
            "title=excluded.title,client_key=excluded.client_key,"
            "created_by_principal_id=excluded.created_by_principal_id,config_json=excluded.config_json "
            "WHERE lineage_blackboards.deleted_at IS NULL",
            (item["id"], lineage_id, item.get("title"), item.get("client_key"),
             item.get("created_by_principal_id"),
             json_text(item.get("config") or {}, ensure_ascii=False, sort_keys=True, separators=(",", ":"))))
    for item in attachments:
        connection.execute(
            "INSERT INTO lineage_blackboard_bindings(wire_id,lineage_id,wire_kind,source_id,board_id,label,client_key,config_json) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(wire_id) DO UPDATE SET lineage_id=excluded.lineage_id,"
            "wire_kind=excluded.wire_kind,source_id=excluded.source_id,board_id=excluded.board_id,label=excluded.label,"
            "client_key=excluded.client_key,config_json=excluded.config_json "
            "WHERE lineage_blackboard_bindings.deleted_at IS NULL",
            (item["id"], lineage_id, item["kind"], item["source"], item["target"], item.get("label"),
             item.get("client_key"),
             json_text(item.get("config") or {}, ensure_ascii=False, sort_keys=True, separators=(",", ":"))))


def read_many(connection: Any, lineage_ids: list[str]) -> dict[str, dict[str, list[dict]]]:
    """Per Lineage: the blackboard components and attach wires, in definition form."""

    result: dict[str, dict[str, list[dict]]] = {lid: {"components": [], "wires": []} for lid in lineage_ids}
    if not lineage_ids:
        return result
    for row in connection.execute(
        "SELECT * FROM lineage_blackboards WHERE lineage_id=ANY(%s) AND deleted_at IS NULL ORDER BY board_id",
        (lineage_ids,),
    ).fetchall():
        item: dict[str, Any] = {"id": row["board_id"], "kind": BOARD_KIND,
                                "config": json_value(row["config_json"] or {})}
        for field in ("title", "client_key", "created_by_principal_id"):
            if row[field] is not None:
                item[field] = row[field]
        result[row["lineage_id"]]["components"].append(item)
    for row in connection.execute(
        "SELECT * FROM lineage_blackboard_bindings WHERE lineage_id=ANY(%s) AND deleted_at IS NULL ORDER BY wire_id",
        (lineage_ids,),
    ).fetchall():
        item = {"id": row["wire_id"], "kind": row["wire_kind"], "source": row["source_id"],
                "target": row["board_id"], "source_port": row["wire_kind"]}
        config = json_value(row["config_json"] or {})
        if row["wire_kind"] == ATTACH_KIND:
            item["config"] = config
        for field in ("label", "client_key"):
            if row[field] is not None:
                item[field] = row[field]
        result[row["lineage_id"]]["wires"].append(item)
    return result


def active_objects(connection: Any, lineage_id: str) -> dict[str, str]:
    objects = {row["board_id"]: "component" for row in connection.execute(
        "SELECT board_id FROM lineage_blackboards WHERE lineage_id=%s AND deleted_at IS NULL", (lineage_id,)).fetchall()}
    objects.update({row["wire_id"]: "wire" for row in connection.execute(
        "SELECT wire_id FROM lineage_blackboard_bindings WHERE lineage_id=%s AND deleted_at IS NULL",
        (lineage_id,)).fetchall()})
    return objects


def deleted_ids(connection: Any, object_ids: list[str]) -> list[str]:
    """Identities of deleted blackboards or attach wires among ``object_ids`` (never reusable)."""

    if not object_ids:
        return []
    return [row["id"] for row in connection.execute(
        "SELECT board_id AS id FROM lineage_blackboards WHERE board_id=ANY(%s) AND deleted_at IS NOT NULL "
        "UNION ALL SELECT wire_id FROM lineage_blackboard_bindings WHERE wire_id=ANY(%s) AND deleted_at IS NOT NULL",
        (object_ids, object_ids)).fetchall()]


def delete_missing(connection: Any, lineage_id: str, removed: Iterable[str], now: Any) -> None:
    ids = sorted(set(removed))
    if not ids:
        return
    when = instant(now) if now else datetime.now(timezone.utc)
    connection.execute(
        "UPDATE lineage_blackboards SET deleted_at=%s WHERE lineage_id=%s AND board_id=ANY(%s) AND deleted_at IS NULL",
        (when, lineage_id, ids))
    connection.execute(
        "UPDATE lineage_blackboard_bindings SET deleted_at=%s WHERE lineage_id=%s AND wire_id=ANY(%s) "
        "AND deleted_at IS NULL", (when, lineage_id, ids))
    # Entries and positions go with the board.
    gone = [row["board_id"] for row in connection.execute(
        "SELECT board_id FROM lineage_blackboards WHERE lineage_id=%s AND board_id=ANY(%s)", (lineage_id, ids)).fetchall()]
    if gone:
        for table in ("blackboard_entries", "blackboard_read_positions", "blackboard_heads"):
            connection.execute(f"DELETE FROM {table} WHERE board_id=ANY(%s)", (gone,))


def purge_lineage(connection: Any, lineage_id: str) -> None:
    """A deleted Lineage takes its blackboards, their entries and read positions with it."""

    boards = [row["board_id"] for row in connection.execute(
        "SELECT board_id FROM lineage_blackboards WHERE lineage_id=%s", (lineage_id,)).fetchall()]
    if boards:
        for table in ("blackboard_entries", "blackboard_read_positions", "blackboard_heads"):
            connection.execute(f"DELETE FROM {table} WHERE board_id=ANY(%s)", (boards,))
    connection.execute("DELETE FROM lineage_blackboard_bindings WHERE lineage_id=%s", (lineage_id,))
    connection.execute("DELETE FROM lineage_blackboards WHERE lineage_id=%s", (lineage_id,))


def transfer(connection: Any, source: str, target: str, object_ids: list[str]) -> None:
    if not object_ids:
        return
    connection.execute(
        "UPDATE lineage_blackboards SET lineage_id=%s WHERE lineage_id=%s AND board_id=ANY(%s) AND deleted_at IS NULL",
        (target, source, object_ids))
    connection.execute(
        "UPDATE lineage_blackboard_bindings SET lineage_id=%s WHERE lineage_id=%s AND wire_id=ANY(%s) "
        "AND deleted_at IS NULL", (target, source, object_ids))


def lineage_for_object(connection: Any, object_id: str) -> str:
    row = connection.execute(
        "SELECT lineage_id FROM lineage_blackboards WHERE board_id=%s AND deleted_at IS NULL "
        "UNION ALL SELECT lineage_id FROM lineage_blackboard_bindings WHERE wire_id=%s AND deleted_at IS NULL",
        (object_id, object_id)).fetchone()
    return str(row["lineage_id"]) if row else ""


# --- Retention ----------------------------------------------------------------------------

def normalize_retention(raw: object) -> dict[str, Any]:
    """The authored ``retention`` of a blackboard, completed with defaults and checked against limits."""

    source = raw if isinstance(raw, Mapping) else {}
    unknown = sorted(set(source) - {"max_entries", "max_total_bytes", "max_age", "max_entry_ttl"})
    if unknown:
        raise ValueError("blackboard retention 不接受字段：" + "、".join(unknown))

    def bounded(name: str, default: int | None, minimum: int, maximum: int) -> int | None:
        value = source.get(name)
        if value is None or value == "":
            return default
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError(f"retention.{name} 必须是整数")
        if value < minimum or value > maximum:
            raise ValueError(f"retention.{name} 必须在 {minimum}–{maximum} 之间")
        return value

    result: dict[str, Any] = {
        "max_entries": bounded("max_entries", DEFAULT_MAX_ENTRIES, 1, MAX_ENTRIES_LIMIT),
        "max_total_bytes": bounded("max_total_bytes", DEFAULT_MAX_TOTAL_BYTES, MIN_TOTAL_BYTES, MAX_TOTAL_BYTES_LIMIT),
    }
    for name in ("max_age", "max_entry_ttl"):
        value = bounded(name, None, 1, MAX_AGE_SECONDS_LIMIT)
        if value is not None:
            result[name] = value
    return result


def normalize_config(raw: object) -> dict[str, Any]:
    source = raw if isinstance(raw, Mapping) else {}
    unknown = sorted(set(source) - {"description", "retention"})
    if unknown:
        raise ValueError("blackboard config 不接受字段：" + "、".join(unknown))
    description = str(source.get("description") or "").strip()
    if len(description) > 4000:
        raise ValueError("blackboard description 不能超过 4000 字符")
    result: dict[str, Any] = {"retention": normalize_retention(source.get("retention"))}
    if description:
        result["description"] = description
    return result


def new_board_id() -> str:
    return BOARD_ID_PREFIX + secrets.token_hex(6)


# --- Entries ------------------------------------------------------------------------------

def _board(connection: Any, board_id: str, lineage_id: str) -> dict[str, Any]:
    row = connection.execute(
        "SELECT board_id,lineage_id,title,config_json FROM lineage_blackboards WHERE board_id=%s AND deleted_at IS NULL",
        (board_id,)).fetchone()
    if row is None or row["lineage_id"] != lineage_id:
        raise KeyError(f"黑板不存在：{board_id}")
    return {"board_id": row["board_id"], "lineage_id": row["lineage_id"], "title": row["title"] or "",
            "retention": normalize_retention(json_value(row["config_json"] or {}).get("retention"))}


def board_record(connection: Any, board_id: str, lineage_id: str) -> dict[str, Any]:
    return _board(connection, board_id, lineage_id)


def attachment_access(connection: Any, board_id: str, resident_id: str) -> str:
    """The ``access`` of a Resident's attach wire to a board; empty when it has none."""

    rows = connection.execute(
        "SELECT config_json FROM lineage_blackboard_bindings WHERE board_id=%s AND wire_kind='attach' "
        "AND source_id=%s AND deleted_at IS NULL", (board_id, resident_id)).fetchall()
    accesses = {str(json_value(row["config_json"] or {}).get("access") or "") for row in rows}
    if "read_write" in accesses or {"read", "write"} <= accesses:
        return "read_write"
    return next(iter(accesses & {"read", "write"}), "")


def _lock_head(connection: Any, board_id: str) -> dict[str, Any]:
    connection.execute(
        "INSERT INTO blackboard_heads(board_id) VALUES (%s) ON CONFLICT(board_id) DO NOTHING", (board_id,))
    return connection.execute(
        "SELECT next_seq,entry_count,total_bytes FROM blackboard_heads WHERE board_id=%s FOR UPDATE",
        (board_id,)).fetchone()


def _json_bytes(value: Any) -> int:
    return len(json_text(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


def _entry_row(row: Mapping[str, Any], *, superseded_by: Mapping[int, int] | None = None) -> dict[str, Any]:
    retracted = bool(row["retracted"])
    entry: dict[str, Any] = {
        "entry_id": row["entry_id"], "seq": int(row["seq"]), "author_id": row["author_id"],
        "created_at": iso_time(row["created_at"]), "kind": row["kind"],
        "text": "" if retracted else row["body"],
        "data": None if retracted else (json_value(row["data_json"]) if row["data_json"] is not None else None),
        "refs": [] if retracted else json_value(row["refs_json"] or []),
        "retracted": retracted,
    }
    if row["supersedes"] is not None:
        entry["supersedes"] = int(row["supersedes"])
    if row["expires_at"] is not None:
        entry["expires_at"] = iso_time(row["expires_at"])
    if superseded_by and int(row["seq"]) in superseded_by:
        entry["superseded_by"] = superseded_by[int(row["seq"])]
    return entry


def _validate_entry(*, kind: str, text: str, data: Any, refs: Any) -> tuple[str, str, Any, list[str], int]:
    kind = (kind or "").strip()
    if len(kind) > MAX_KIND_CHARS:
        raise ValueError(f"kind 不能超过 {MAX_KIND_CHARS} 个字符")
    if any(ord(ch) < 32 for ch in kind):
        raise ValueError("kind 不能包含控制字符")
    if not isinstance(text, str) or not text.strip():
        raise ValueError("text 不能为空")
    text_size = len(text.encode("utf-8"))
    if text_size > MAX_TEXT_BYTES:
        raise ValueError(f"text 不能超过 {MAX_TEXT_BYTES // 1024} KiB")
    data_size = 0
    if data is not None:
        data_size = _json_bytes(data)
        if data_size > MAX_DATA_BYTES:
            raise ValueError(f"data 不能超过 {MAX_DATA_BYTES // 1024} KiB")
    clean_refs: list[str] = []
    if refs is not None:
        if not isinstance(refs, list) or len(refs) > MAX_REFS:
            raise ValueError(f"refs 必须是最多 {MAX_REFS} 项的资源地址数组")
        for ref in refs:
            if not isinstance(ref, str) or not ref.strip() or len(ref) > MAX_REF_CHARS:
                raise ValueError("refs 的每一项必须是非空资源地址字符串")
            clean_refs.append(ref.strip())
    return kind, text, data, clean_refs, text_size + data_size + sum(len(r) for r in clean_refs)


def append_entry(
    connection: Any, *, board_id: str, lineage_id: str, author_id: str, kind: str = "", text: str,
    data: Any = None, refs: Any = None, supersedes: int | None = None, expires_at: Any = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Write one entry and purge what the retention policy no longer keeps, in one transaction."""

    board = _board(connection, board_id, lineage_id)
    kind, text, data, clean_refs, size = _validate_entry(kind=kind, text=text, data=data, refs=refs)
    retention = board["retention"]
    now = now or datetime.now(timezone.utc)
    expiry = instant(expires_at) if expires_at not in (None, "") else None
    if expiry is not None:
        if expiry <= now:
            raise ValueError("expires_at 必须晚于当前时间")
        ttl = retention.get("max_entry_ttl")
        if ttl is not None and expiry > now + timedelta(seconds=int(ttl)):
            raise ValueError(f"expires_at 不能晚于写入后 {ttl} 秒（黑板的 max_entry_ttl）")
    head = _lock_head(connection, board_id)
    seq = int(head["next_seq"])
    if supersedes is not None:
        if isinstance(supersedes, bool) or not isinstance(supersedes, int) or supersedes < 1 or supersedes >= seq:
            raise ValueError("supersedes 必须是这块黑板上较早条目的 seq")
        found = connection.execute(
            "SELECT 1 AS present FROM blackboard_entries WHERE board_id=%s AND seq=%s", (board_id, supersedes)).fetchone()
        if found is None:
            raise ValueError("supersedes 指向的条目已不存在")
    entry_id = "bbe_" + secrets.token_hex(12)
    connection.execute(
        "INSERT INTO blackboard_entries(board_id,seq,entry_id,author_id,created_at,kind,body,data_json,refs_json,"
        "supersedes,expires_at,size_bytes) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (board_id, seq, entry_id, author_id, now, kind, text,
         json_text(data, ensure_ascii=False, separators=(",", ":")) if data is not None else None,
         json_text(clean_refs, ensure_ascii=False, separators=(",", ":")), supersedes, expiry, size))
    count, total = int(head["entry_count"]) + 1, int(head["total_bytes"]) + size
    removed = _purge(connection, board_id, retention, now, count, total, keep_seq=seq)
    connection.execute(
        "UPDATE blackboard_heads SET next_seq=%s, entry_count=entry_count+1-%s, total_bytes=total_bytes+%s-%s "
        "WHERE board_id=%s", (seq + 1, removed["count"], size, removed["bytes"], board_id))
    row = connection.execute(
        "SELECT * FROM blackboard_entries WHERE board_id=%s AND seq=%s", (board_id, seq)).fetchone()
    earliest = connection.execute(
        "SELECT min(seq) AS earliest FROM blackboard_entries WHERE board_id=%s", (board_id,)).fetchone()["earliest"]
    return {"entry": _entry_row(row), "latest_seq": seq, "earliest_seq": int(earliest or seq),
            "purged": removed["count"]}


def _purge(connection: Any, board_id: str, retention: Mapping[str, Any], now: datetime,
           count: int, total: int, *, keep_seq: int) -> dict[str, int]:
    """Drop expired and aged entries, then the oldest ones until count and bytes fit."""

    removed_count = removed_bytes = 0
    conditions = ["(expires_at IS NOT NULL AND expires_at <= %s)"]
    params: list[Any] = [now]
    if retention.get("max_age") is not None:
        conditions.append("created_at < %s")
        params.append(now - timedelta(seconds=int(retention["max_age"])))
    rows = connection.execute(
        f"DELETE FROM blackboard_entries WHERE board_id=%s AND seq<>%s AND ({' OR '.join(conditions)}) "
        "RETURNING size_bytes", (board_id, keep_seq, *params)).fetchall()
    removed_count += len(rows)
    removed_bytes += sum(int(row["size_bytes"]) for row in rows)
    count -= len(rows)
    total -= removed_bytes
    max_entries, max_bytes = int(retention["max_entries"]), int(retention["max_total_bytes"])
    if count > max_entries or total > max_bytes:
        oldest = connection.execute(
            "SELECT seq,size_bytes FROM blackboard_entries WHERE board_id=%s AND seq<>%s ORDER BY seq", (board_id, keep_seq)
        ).fetchall()
        drop: list[int] = []
        for row in oldest:
            if count <= max_entries and total <= max_bytes:
                break
            drop.append(int(row["seq"]))
            count -= 1
            total -= int(row["size_bytes"])
            removed_count += 1
            removed_bytes += int(row["size_bytes"])
        if drop:
            connection.execute("DELETE FROM blackboard_entries WHERE board_id=%s AND seq=ANY(%s)", (board_id, drop))
    return {"count": removed_count, "bytes": removed_bytes}


def list_entries(
    connection: Any, *, board_id: str, lineage_id: str, reader_id: str, cursor: Any = None, limit: Any = None,
    author_id: str = "", kind: str = "", now: datetime | None = None,
) -> dict[str, Any]:
    """One page of entries after ``cursor`` (or after the reader's read position when no cursor is given)."""

    _board(connection, board_id, lineage_id)
    now = now or datetime.now(timezone.utc)
    page = PAGE_DEFAULT if limit in (None, "") else int(limit)
    page = max(1, min(PAGE_MAX, page))
    head = connection.execute(
        "SELECT next_seq FROM blackboard_heads WHERE board_id=%s", (board_id,)).fetchone()
    latest = int(head["next_seq"]) - 1 if head else 0
    position = connection.execute(
        "SELECT read_seq FROM blackboard_read_positions WHERE board_id=%s AND reader_id=%s",
        (board_id, reader_id)).fetchone()
    read_seq = int(position["read_seq"]) if position else 0
    text = str(cursor or "").strip()
    if text and not text.isdigit():
        raise ValueError("cursor 无效：请原样交回上一页的 next_cursor")
    after = int(text) if text else read_seq
    clauses = ["board_id=%s", "seq>%s", "(expires_at IS NULL OR expires_at > %s)"]
    params: list[Any] = [board_id, after, now]
    if author_id:
        clauses.append("author_id=%s")
        params.append(author_id)
    if kind:
        clauses.append("kind=%s")
        params.append(kind)
    rows = connection.execute(
        f"SELECT * FROM blackboard_entries WHERE {' AND '.join(clauses)} ORDER BY seq LIMIT %s",
        (*params, page + 1)).fetchall()
    more = len(rows) > page
    rows = rows[:page]
    superseded: dict[int, int] = {}
    if rows:
        for item in connection.execute(
            "SELECT supersedes, max(seq) AS by FROM blackboard_entries WHERE board_id=%s AND supersedes=ANY(%s) "
            "GROUP BY supersedes", (board_id, [int(row["seq"]) for row in rows])).fetchall():
            superseded[int(item["supersedes"])] = int(item["by"])
    earliest = connection.execute(
        "SELECT min(seq) AS earliest FROM blackboard_entries WHERE board_id=%s", (board_id,)).fetchone()["earliest"]
    return {
        "entries": [_entry_row(row, superseded_by=superseded) for row in rows],
        "latest_seq": latest,
        "earliest_seq": int(earliest) if earliest is not None else latest + 1,
        "read_seq": read_seq,
        "next_cursor": str(int(rows[-1]["seq"])) if rows and more else "",
    }


def set_read_position(connection: Any, *, board_id: str, lineage_id: str, reader_id: str, seq: int,
                      now: datetime | None = None) -> dict[str, Any]:
    _board(connection, board_id, lineage_id)
    head = _lock_head(connection, board_id)
    latest = int(head["next_seq"]) - 1
    if isinstance(seq, bool) or not isinstance(seq, int) or seq < 0:
        raise ValueError("seq 必须是非负整数")
    if seq > latest:
        raise ValueError(f"seq 不能超过这块黑板的最新序号 {latest}")
    previous = connection.execute(
        "SELECT read_seq FROM blackboard_read_positions WHERE board_id=%s AND reader_id=%s",
        (board_id, reader_id)).fetchone()
    before = int(previous["read_seq"]) if previous else 0
    after = max(before, seq)
    connection.execute(
        "INSERT INTO blackboard_read_positions(board_id,reader_id,read_seq,updated_at) VALUES (%s,%s,%s,%s) "
        "ON CONFLICT(board_id,reader_id) DO UPDATE SET read_seq=GREATEST(blackboard_read_positions.read_seq,"
        "excluded.read_seq), updated_at=excluded.updated_at", (board_id, reader_id, after, now or datetime.now(timezone.utc)))
    return {"read_seq": after, "previous_read_seq": before, "advanced": after > before, "latest_seq": latest}


def retract_entry(connection: Any, *, board_id: str, lineage_id: str, entry_id: str, actor_id: str,
                  any_author: bool, now: datetime | None = None) -> dict[str, Any]:
    """Clear an entry's body, data and refs; the entry keeps its place in the sequence."""

    _board(connection, board_id, lineage_id)
    _lock_head(connection, board_id)
    row = connection.execute(
        "SELECT * FROM blackboard_entries WHERE board_id=%s AND entry_id=%s", (board_id, entry_id)).fetchone()
    if row is None:
        raise KeyError(f"条目不存在或已被清理：{entry_id}")
    if not any_author and row["author_id"] != actor_id:
        raise PermissionError("只能撤回自己写的条目")
    if row["retracted"]:
        return {"entry": _entry_row(row), "retracted": False}
    when = now or datetime.now(timezone.utc)
    freed = int(row["size_bytes"]) - RETRACTED_BYTES
    connection.execute(
        "UPDATE blackboard_entries SET body='', data_json=NULL, refs_json='[]', retracted=TRUE, retracted_at=%s, "
        "size_bytes=%s WHERE board_id=%s AND entry_id=%s", (when, RETRACTED_BYTES, board_id, entry_id))
    connection.execute(
        "UPDATE blackboard_heads SET total_bytes=GREATEST(0,total_bytes-%s) WHERE board_id=%s", (freed, board_id))
    row = connection.execute(
        "SELECT * FROM blackboard_entries WHERE board_id=%s AND entry_id=%s", (board_id, entry_id)).fetchone()
    return {"entry": _entry_row(row), "retracted": True}


def entries_after(connection: Any, board_id: str, after_seq: int, limit: int = 200) -> list[dict[str, Any]]:
    """Entry headers (no body) after ``after_seq``, for the Event Source ``blackboard.entry_created``."""

    return [{"entry_id": row["entry_id"], "seq": int(row["seq"]), "author_id": row["author_id"],
             "kind": row["kind"], "created_at": iso_time(row["created_at"])}
            for row in connection.execute(
                "SELECT entry_id,seq,author_id,kind,created_at FROM blackboard_entries WHERE board_id=%s AND seq>%s "
                "ORDER BY seq LIMIT %s", (board_id, after_seq, limit)).fetchall()]


def latest_seq(connection: Any, board_id: str) -> int:
    row = connection.execute("SELECT next_seq FROM blackboard_heads WHERE board_id=%s", (board_id,)).fetchone()
    return int(row["next_seq"]) - 1 if row else 0
