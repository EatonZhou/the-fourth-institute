"""Personal Mailbox checklist; one row per item, independent of chat receipts."""
from __future__ import annotations

import os
import uuid
from datetime import date as Date, datetime, timezone
from functools import lru_cache
from zoneinfo import ZoneInfo

from management_common.domain_database import DomainDatabase
from management_common.database_time import iso_time
from management_common.principal import actor_principal, validate_user_id
from management_portal.hatg.contracts import InstructionError, invalid, not_found


DDL = """
CREATE TABLE IF NOT EXISTS mailbox_todos (
    todo_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    text TEXT NOT NULL CHECK (char_length(text) BETWEEN 1 AND 100),
    date TEXT NOT NULL,
    completed BOOLEAN NOT NULL DEFAULT FALSE,
    position BIGINT NOT NULL,
    created_by TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,
    indent INTEGER NOT NULL DEFAULT 0 CHECK (indent BETWEEN 0 AND 4)
);
CREATE INDEX IF NOT EXISTS mailbox_todos_user_order
    ON mailbox_todos(user_id, date DESC, completed, position, todo_id);
"""


@lru_cache(maxsize=8)
def _database(dsn, namespace):
    return DomainDatabase("portal_ui", "mailbox_todos", DDL)


def database():
    return _database(os.getenv("PORTAL_DATABASE_URL"), os.getenv("PORTAL_DATABASE_NAMESPACE"))


def owner(caller, user_id=None):
    """Residents may address an existing User when creating; other operations are self-only."""
    principal = actor_principal(caller)
    if principal.kind == "resident" and user_id:
        try:
            return validate_user_id(user_id)
        except ValueError as exc:
            raise invalid("待办接收者必须是 User") from exc
    if principal.kind != "user" or user_id and user_id != principal.id:
        raise InstructionError("instruction.forbidden", "只能管理自己的个人待办；Resident 添加待办须指定 user_id")
    return principal.id


def _text(value):
    if not isinstance(value, str) or not 1 <= len(value.strip()) <= 100:
        raise invalid("每条待办需要 1–100 个字")
    return value.strip()


def _date(value):
    if value is None:
        return datetime.now(ZoneInfo("Asia/Hong_Kong")).date().isoformat()
    try:
        if not isinstance(value, str) or Date.fromisoformat(value).isoformat() != value:
            raise ValueError()
    except ValueError as exc:
        raise invalid("待办日期须为 YYYY-MM-DD") from exc
    return value


def _now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _item(connection, user_id, todo_id):
    row = connection.execute("SELECT * FROM mailbox_todos WHERE user_id=%s AND todo_id=%s", (user_id, todo_id)).fetchone()
    if row is None:
        raise not_found("待办不存在")
    return _record(row)


def _record(row):
    return {**dict(row), 'created_at': iso_time(row['created_at']), 'updated_at': iso_time(row['updated_at'])}


def list_items(caller):
    user_id = owner(caller)
    with database().read() as connection:
        items = [_record(row) for row in connection.execute(
            "SELECT * FROM mailbox_todos WHERE user_id=%s ORDER BY date DESC, position, todo_id", (user_id,))]
    return {"items": items, "pending_count": sum(not item["completed"] for item in items)}


def create(caller, text, *, user_id=None, date=None):
    recipient, text, date = owner(caller, user_id), _text(text), _date(date)
    todo_id, now = uuid.uuid4().hex, _now()
    with database().transaction(recipient) as connection:
        position = connection.execute("SELECT COALESCE(MAX(position),0)+1 FROM mailbox_todos WHERE user_id=%s", (recipient,)).fetchone()[0]
        connection.execute("""INSERT INTO mailbox_todos
            (todo_id,user_id,text,date,position,created_by,created_at,updated_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""",
            (todo_id, recipient, text, date, position, caller, now, now))
        return {"item": _item(connection, recipient, todo_id)}


def update(caller, todo_id, changes):
    user_id = owner(caller)
    if not changes or set(changes) - {"text", "date", "completed", "indent"}:
        raise invalid("只支持修改待办的 text、date、completed、indent")
    values = dict(changes)
    if "text" in values:
        values["text"] = _text(values["text"])
    if "date" in values:
        if values["date"] is None:
            raise invalid("待办日期须为 YYYY-MM-DD")
        values["date"] = _date(values["date"])
    if "completed" in values and type(values["completed"]) is not bool:
        raise invalid("completed 须为布尔值")
    if "indent" in values and (type(values["indent"]) is not int or not 0 <= values["indent"] <= 4):
        raise invalid("indent 须为 0–4 的整数")
    with database().transaction(user_id) as connection:
        item = _item(connection, user_id, todo_id)
        item.update(values)
        connection.execute("""UPDATE mailbox_todos SET text=%s,date=%s,completed=%s,indent=%s,updated_at=%s
            WHERE user_id=%s AND todo_id=%s""", (item["text"], item["date"], item["completed"], item["indent"], _now(), user_id, todo_id))
        return {"item": _item(connection, user_id, todo_id)}


def delete(caller, todo_id):
    user_id = owner(caller)
    with database().transaction(user_id) as connection:
        _item(connection, user_id, todo_id)
        connection.execute("DELETE FROM mailbox_todos WHERE user_id=%s AND todo_id=%s", (user_id, todo_id))
    return {"todo_id": todo_id}


def move(caller, todo_id, direction=None, *, before_todo_id=None, after_todo_id=None):
    user_id = owner(caller)
    if sum(value is not None for value in (direction, before_todo_id, after_todo_id)) != 1:
        raise invalid("direction、before_todo_id、after_todo_id 须且只能指定一个")
    if direction is not None and direction not in {"up", "down"}:
        raise invalid("direction 须为 up 或 down")
    with database().transaction(user_id) as connection:
        item = _item(connection, user_id, todo_id)
        anchor_id = before_todo_id if before_todo_id is not None else after_todo_id
        if anchor_id == todo_id:
            raise invalid("不能以待办自身作为移动目标")
        day = _item(connection, user_id, anchor_id)["date"] if direction is None else item["date"]
        ids = [row[0] for row in connection.execute("""SELECT todo_id FROM mailbox_todos
            WHERE user_id=%s AND date=%s ORDER BY position,todo_id""", (user_id, day))]
        if direction is None:
            ids = [identifier for identifier in ids if identifier != todo_id]
            target = ids.index(anchor_id) + (after_todo_id is not None)
            ids.insert(target, todo_id)
        else:
            current = ids.index(todo_id)
            target = current + (-1 if direction == "up" else 1)
            if not 0 <= target < len(ids):
                return {"item": item}
            ids[current], ids[target] = ids[target], ids[current]
        connection.execute("UPDATE mailbox_todos SET date=%s,updated_at=%s WHERE user_id=%s AND todo_id=%s", (day, _now(), user_id, todo_id))
        for position, identifier in enumerate(ids):
            connection.execute("UPDATE mailbox_todos SET position=%s WHERE user_id=%s AND todo_id=%s", (position, user_id, identifier))
        return {"item": _item(connection, user_id, todo_id)}
