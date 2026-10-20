"""Message references admitted to one Execution; no graph identity or copied input bodies."""
from __future__ import annotations

from typing import Any, Iterable, Mapping
from management_common.database_time import decode_times, iso_time, instant

COLUMNS = ("execution_id", "message_id", "role", "accepted_at")
DDL = """
CREATE TABLE execution_inputs (
    execution_id TEXT NOT NULL REFERENCES executions(execution_id),
    message_id TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('primary','member','info')),
    accepted_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (execution_id,message_id)
);
CREATE INDEX execution_inputs_message_idx ON execution_inputs(message_id,execution_id);
"""


def attach(store: Any, execution_id: str, inputs: Iterable[Mapping[str, str]]) -> None:
    values = list(inputs)
    store.database.write(lambda connection: attach_in(connection, execution_id, values))


def attach_in(connection: Any, execution_id: str, values: Iterable[Mapping[str, str]], *, prefix: str = "") -> None:
    execution = connection.execute(f"SELECT status,created_at FROM {prefix}executions WHERE execution_id=%s FOR UPDATE",
                                   (execution_id,)).fetchone()
    if execution is None:
        raise KeyError(execution_id)
    for item in values:
        message_id = str(item["message_id"])
        if not message_id:
            raise ValueError("Execution input requires a message ID")
        existing = connection.execute(
            f"SELECT role FROM {prefix}execution_inputs WHERE execution_id=%s AND message_id=%s",
            (execution_id,message_id),
        ).fetchone()
        if existing:
            if existing["role"] != item["role"]:
                raise ValueError("Execution input role is immutable")
            continue
        if execution["status"] in {"completed","failed","cancelled"}:
            raise ValueError("Cannot admit new input to a terminal Execution")
        connection.execute(f"INSERT INTO {prefix}execution_inputs VALUES(%s,%s,%s,%s)",
                           (execution_id,message_id,item["role"],
                            instant(item.get("accepted_at")) or execution["created_at"]))


def recent(store: Any, agent_id: str, *, limit: int = 10) -> list[dict[str, Any]]:
    with store.database.read() as connection:
        executions = connection.execute(
            "SELECT e.execution_id,e.created_at,e.started_at,e.finished_at,e.status,i.message_id "
            "FROM executions e JOIN execution_inputs i USING(execution_id) "
            "WHERE e.agent_id=%s AND i.role='primary' "
            "ORDER BY e.created_at DESC,e.execution_id DESC LIMIT %s", (agent_id,limit),
        ).fetchall()
        identities = [row["execution_id"] for row in executions]
        inputs = connection.execute("SELECT * FROM execution_inputs WHERE execution_id=ANY(%s)",
                                    (identities,)).fetchall() if identities else []
    return [{**decode_times(row, {"created_at", "started_at", "finished_at"}), "infos": [{"message_id":i["message_id"],"held_at":iso_time(i["accepted_at"])}
                                   for i in inputs if i["execution_id"]==row["execution_id"] and i["role"]=="info"]}
            for row in executions]


def read_in(connection: Any, execution_id: str) -> list[dict[str, Any]]:
    """Return admitted references only; no message or Provider content is copied."""
    rows = connection.execute(
        "SELECT message_id,role,accepted_at FROM execution_inputs WHERE execution_id=%s "
        "ORDER BY accepted_at,message_id", (execution_id,),
    ).fetchall()
    return [{"message_id": row["message_id"], "role": row["role"], "accepted_at": iso_time(row["accepted_at"])}
            for row in rows]


def latest(store: Any, agent_id: str, message_id: str) -> dict[str, Any] | None:
    """Resolve an input reference to its most recent concrete Execution."""
    with store.database.read_current() as connection:
        row = connection.execute(
            "SELECT e.execution_id,e.status,e.created_at,e.finished_at FROM executions e "
            "JOIN execution_inputs i USING(execution_id) WHERE e.agent_id=%s AND i.message_id=%s "
            "ORDER BY e.created_at DESC,e.execution_id DESC LIMIT 1", (agent_id,message_id),
        ).fetchone()
    return decode_times(row, {"created_at", "finished_at"}) if row is not None else None

