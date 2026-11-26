"""User-owned Todo resources; the existing table is the sole storage owner."""
from __future__ import annotations
import hashlib
import json
import os
import re
import uuid
from datetime import date as Date, datetime, timezone
from functools import lru_cache
from zoneinfo import ZoneInfo
from management_common.domain_database import DomainDatabase
from management_common.database_time import iso_time
from management_common.principal import actor_principal, validate_user_id
from management_portal.resources.errors import ResourceVersionConflict

RESOURCE_TYPE = "mailbox_todo"
ID_PATTERN = re.compile(r"mailbox_todo_[0-9a-f]{12}\Z")
LEGACY_PATTERN = re.compile(r"[0-9a-f]{32}\Z")
DDL = """
CREATE TABLE IF NOT EXISTS mailbox_todos (
 todo_id TEXT PRIMARY KEY, user_id TEXT NOT NULL,
 text TEXT NOT NULL CHECK (char_length(text) BETWEEN 1 AND 100),
 date TEXT NOT NULL, completed BOOLEAN NOT NULL DEFAULT FALSE, position BIGINT NOT NULL,
 created_by TEXT NOT NULL, created_at TIMESTAMPTZ NOT NULL, updated_at TIMESTAMPTZ NOT NULL,
 indent INTEGER NOT NULL DEFAULT 0 CHECK (indent BETWEEN 0 AND 4)
);
CREATE INDEX IF NOT EXISTS mailbox_todos_user_order ON mailbox_todos(user_id,date DESC,completed,position,todo_id);
ALTER TABLE mailbox_todos ADD COLUMN IF NOT EXISTS version BIGINT NOT NULL DEFAULT 1 CHECK (version > 0);
ALTER TABLE mailbox_todos ADD COLUMN IF NOT EXISTS legacy_todo_id TEXT;
ALTER TABLE mailbox_todos ADD COLUMN IF NOT EXISTS creation_fingerprint TEXT;
CREATE UNIQUE INDEX IF NOT EXISTS mailbox_todos_legacy ON mailbox_todos(legacy_todo_id);
"""


def migrated_id(identifier):
    if not isinstance(identifier, str) or not LEGACY_PATTERN.fullmatch(identifier):
        raise ValueError("Invalid legacy Todo identity")
    return "mailbox_todo_" + hashlib.sha256(("mailbox_todo:" + identifier).encode()).hexdigest()[:12]


def migrate(connection):
    """Owner lifecycle migration, serialized with DDL and rolled back on any conflict."""
    connection.execute("LOCK TABLE mailbox_todos IN ACCESS EXCLUSIVE MODE")
    rows = connection.execute("SELECT todo_id,legacy_todo_id FROM mailbox_todos ORDER BY todo_id").fetchall()
    targets, changes = set(), []
    for row in rows:
        old = row["todo_id"]
        if LEGACY_PATTERN.fullmatch(old):
            target = migrated_id(old)
            if row["legacy_todo_id"] not in (None, old):
                raise ValueError("Conflicting legacy Todo identity")
            changes.append((target, old, old))
        elif ID_PATTERN.fullmatch(old):
            target = old
            if row["legacy_todo_id"] is not None and migrated_id(row["legacy_todo_id"]) != old:
                raise ValueError("Conflicting Todo migration mapping")
        else:
            raise ValueError("Invalid stored Todo identity")
        if target in targets:
            raise ValueError("Todo identity migration collision")
        targets.add(target)
    for values in changes:
        connection.execute("UPDATE mailbox_todos SET todo_id=%s,legacy_todo_id=%s WHERE todo_id=%s", values)


class TodoDatabase(DomainDatabase):
    def ensure(self):
        if self._ready:
            return
        with self._lock:
            if self._ready:
                return
            with self.database.transaction() as connection:
                connection.execute("SELECT pg_advisory_xact_lock(hashtextextended(current_schema() || ':' || %s,0))", (self.owner + ":ddl",))
                connection.executescript(self.ddl)
                migrate(connection)
            self._ready = True


@lru_cache(maxsize=8)
def _database(dsn, namespace):
    return TodoDatabase("portal_ui", "mailbox_todos", DDL)


def database():
    return _database(os.getenv("PORTAL_DATABASE_URL"), os.getenv("PORTAL_DATABASE_NAMESPACE"))


def _identity(value):
    if not isinstance(value, str) or not ID_PATTERN.fullmatch(value):
        raise KeyError(value)
    return value


def _require_active_user(user_id):
    from management_portal.portal_access_control import get_portal_access_store, PortalCredentialStore
    user = get_portal_access_store().user(user_id)
    if not user.get("configured") and user_id not in PortalCredentialStore().load():
        raise KeyError(user_id)
    if user.get("status") == "disabled":
        raise ValueError("待办接收用户已停用")


def authorize(caller, user_id, *, operation="read"):
    """Domain privacy also applies when the generic permission system is open."""
    from management_portal import permission_system
    principal = actor_principal(caller)
    user_id = validate_user_id(user_id)
    if principal.kind == "user" and principal.id == user_id:
        return user_id
    if principal.kind == "resident":
        if operation == "create":
            return user_id
        if operation in {"read", "update"} and permission_system.allows_roles(caller, [("@admin", user_id)]):
            return user_id
    raise PermissionError("只能管理本人待办；Resident 读取或修改须用户 admin，禁止删除和调序")


def _text(value):
    if not isinstance(value, str) or not 1 <= len(value.strip()) <= 100:
        raise ValueError("每条待办需要 1–100 个字")
    return value.strip()


def _date(value):
    if value is None:
        return datetime.now(ZoneInfo("Asia/Hong_Kong")).date().isoformat()
    if not isinstance(value, str) or Date.fromisoformat(value).isoformat() != value:
        raise ValueError("待办日期须为 YYYY-MM-DD")
    return value


def _now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _body(row):
    return {"resource_id": row["todo_id"], **{key: row[key] for key in (
        "user_id", "text", "date", "completed", "indent", "position", "created_by", "version")},
        "created_at": iso_time(row["created_at"]), "updated_at": iso_time(row["updated_at"])}


def _row(connection, resource_id):
    row = connection.execute("SELECT * FROM mailbox_todos WHERE todo_id=%s", (_identity(resource_id),)).fetchone()
    if row is None:
        raise KeyError(resource_id)
    return row


def _authorized(connection, resource_id, caller, operation):
    row = _row(connection, resource_id)
    try:
        authorize(caller, row["user_id"], operation=operation)
    except PermissionError:
        if actor_principal(caller).kind == "user":
            raise KeyError(resource_id) from None
        raise
    return row


def lookup_resource(resource_id):
    """Internal permission/index fact lookup; not a public read entry."""
    with database().read() as connection:
        return _body(_row(connection, resource_id))


def all_items():
    """Internal projection snapshot; consumers must apply caller authorization."""
    with database().read() as connection:
        return [_body(row) for row in connection.execute("SELECT * FROM mailbox_todos ORDER BY user_id,date DESC,position,COALESCE(legacy_todo_id,todo_id)")]


def get(caller, resource_id):
    with database().read() as connection:
        return _body(_authorized(connection, resource_id, caller, "read"))


def resource_record(item):
    return {"resource_id": item["resource_id"], "resource_type": RESOURCE_TYPE,
            "title": item["text"], "version": item["version"], "user_id": item["user_id"],
            "project_ids": [], "attributes": dict(item)}


def create(caller, text, *, user_id, date=None, resource_id=None):
    from management_portal import permission_system
    recipient = authorize(caller, user_id, operation="create")
    _require_active_user(recipient)
    text = _text(text)
    # Retrying an unspecified date after midnight still identifies the same request.
    fingerprint = hashlib.sha256(json.dumps([caller, recipient, text, date], ensure_ascii=False).encode()).hexdigest()
    day, now = _date(date), _now()
    identifier = _identity(resource_id) if resource_id is not None else "mailbox_todo_" + uuid.uuid4().hex[:12]
    with database().transaction(recipient) as connection:
        authorize(caller, recipient, operation="create")
        _require_active_user(recipient)
        existing = connection.execute("SELECT * FROM mailbox_todos WHERE todo_id=%s", (identifier,)).fetchone()
        if existing is not None:
            if existing["creation_fingerprint"] != fingerprint:
                raise ValueError("Todo creation identity was reused for another request")
            return _body(existing)
        position = connection.execute("SELECT COALESCE(MAX(position),0)+1 FROM mailbox_todos WHERE user_id=%s", (recipient,)).fetchone()[0]
        connection.execute("""INSERT INTO mailbox_todos
            (todo_id,user_id,text,date,position,created_by,created_at,updated_at,creation_fingerprint)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""", (identifier,recipient,text,day,position,caller,now,now,fingerprint))
        item = _body(_row(connection, identifier))
        permission_system.sync_resource(resource_record(item), resource_type=RESOURCE_TYPE, connection=connection)
        return item


def update(caller, resource_id, changes, *, expected_version):
    if type(expected_version) is not int or expected_version < 1:
        raise ValueError("expected_version 必须为读取时返回的正整数版本")
    if not isinstance(changes, dict) or not changes or set(changes) - {"text", "date", "completed", "indent", "order"}:
        raise ValueError("只支持 text、date、completed、indent 或独立的 order")
    values = dict(changes)
    if "order" in values:
        if set(values) != {"order"}:
            raise ValueError("order 不得与普通字段混用")
        order = values["order"]
        if not isinstance(order, dict) or len(order) != 1 or not set(order) <= {"direction", "before_id", "after_id"}:
            raise ValueError("order 须且只能指定 direction、before_id、after_id 中一个")
        if "direction" in order and order["direction"] not in {"up", "down"}:
            raise ValueError("direction 须为 up 或 down")
        if "direction" not in order:
            _identity(next(iter(order.values())))
    else:
        if "text" in values:
            values["text"] = _text(values["text"])
        if "date" in values:
            if values["date"] is None:
                raise ValueError("date 不可为空")
            values["date"] = _date(values["date"])
        if "completed" in values and type(values["completed"]) is not bool:
            raise ValueError("completed 须为布尔值")
        if "indent" in values and (type(values["indent"]) is not int or not 0 <= values["indent"] <= 4):
            raise ValueError("indent 须为 0–4 的整数")
    operation = "order" if "order" in values else "update"
    with database().read() as connection:
        user_id = _authorized(connection, resource_id, caller, operation)["user_id"]
    with database().transaction(user_id) as connection:
        row = _authorized(connection, resource_id, caller, operation)
        if row["version"] != expected_version:
            raise ResourceVersionConflict(expected_version=expected_version, current_version=row["version"])
        if operation == "order":
            _reorder(connection, row, values["order"], caller)
        elif any(row[key] != value for key, value in values.items()):
            item = {**dict(row), **values}
            connection.execute("""UPDATE mailbox_todos SET text=%s,date=%s,completed=%s,indent=%s,updated_at=%s,version=version+1
                WHERE todo_id=%s""", (item["text"],item["date"],item["completed"],item["indent"],_now(),resource_id))
        return _body(_row(connection, resource_id))


def _reorder(connection, row, order, caller):
    identifier, user_id = row["todo_id"], row["user_id"]
    anchor_id = order.get("before_id", order.get("after_id"))
    if anchor_id == identifier:
        raise ValueError("不能以待办自身作为移动目标")
    anchor = _authorized(connection, anchor_id, caller, "order") if anchor_id is not None else row
    if anchor["user_id"] != user_id:
        raise KeyError(anchor_id)
    day = anchor["date"]
    rows = connection.execute("SELECT * FROM mailbox_todos WHERE user_id=%s AND date=%s ORDER BY position,COALESCE(legacy_todo_id,todo_id)", (user_id,day)).fetchall()
    ids = [item["todo_id"] for item in rows]
    previous_order = {value: position for position, value in enumerate(ids)}
    previous_rows = {item["todo_id"]: item for item in rows}
    if anchor_id is not None:
        ids = [value for value in ids if value != identifier]
        ids.insert(ids.index(anchor_id) + ("after_id" in order), identifier)
    else:
        current = ids.index(identifier)
        target = current + (-1 if order["direction"] == "up" else 1)
        if not 0 <= target < len(ids):
            return
        ids[current], ids[target] = ids[target], ids[current]
    now = _now()
    for position, value in enumerate(ids):
        old = previous_rows.get(value)
        if old is not None and old["position"] == position and previous_order[value] == position:
            continue
        connection.execute("""UPDATE mailbox_todos SET position=%s,date=%s,updated_at=%s,version=version+1
            WHERE todo_id=%s""", (position,day,now,value))


def delete(caller, resource_id):
    from management_portal import permission_system
    with database().read() as connection:
        user_id = _authorized(connection, resource_id, caller, "delete")["user_id"]
    with database().transaction(user_id) as connection:
        item = _body(_authorized(connection, resource_id, caller, "delete"))
        connection.execute("DELETE FROM mailbox_todos WHERE todo_id=%s", (resource_id,))
        permission_system.unregister_resources([resource_id], connection=connection)
        return item


def search_resources(caller, *, user_id, q="", limit=50, cursor=None, readable_ids=None):
    user_id = authorize(caller, user_id)
    if type(limit) is not int or not 1 <= limit <= 5000:
        raise ValueError("limit 必须为 1–5000")
    if not isinstance(q, str):
        raise ValueError("q 必须为字符串")
    if cursor is None or cursor == "":
        offset = 0
    elif isinstance(cursor, str) and cursor.isascii() and cursor.isdigit():
        offset = int(cursor)
    else:
        raise ValueError("无效的 Todo 分页 cursor")
    with database().read() as connection:
        authorize(caller, user_id)
        rows = connection.execute("SELECT * FROM mailbox_todos WHERE user_id=%s ORDER BY date DESC,position,COALESCE(legacy_todo_id,todo_id)", (user_id,)).fetchall()
        selected = [_body(row) for row in rows if q.casefold() in row["text"].casefold() or q == row["todo_id"]]
    if readable_ids is not None:
        permitted = readable_ids([item["resource_id"] for item in selected])
        selected = [item for item in selected if item["resource_id"] in permitted]
    end = offset + limit
    return {"items": [resource_record(item) for item in selected[offset:end]], "total": len(selected),
            "pending_count": sum(not item["completed"] for item in selected),
            "next_cursor": str(end) if end < len(selected) else None}
