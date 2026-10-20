"""Resident Route delivery budgets and idempotent admission receipts.

A reservation fences concurrent sends, including a send whose acknowledgement
was lost. Only an accepted message consumes the counter. Receipts survive a
reset so retrying an old message cannot deliver it or count it a second time.
No database lock is held while calling DeliverMessage.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any

from management_common.limit_contract import (
    limit_reached,
    limit_remaining,
    normalize_optional_limit,
)

COLUMNS = {
    "wire_delivery_budgets": ("lineage_id", "wire_id", "generation"),
    "wire_delivery_receipts": (
        "lineage_id",
        "wire_id",
        "message_id",
        "generation",
        "status",
        "accepted_message_id",
    ),
}


def create_schema(connection: Any) -> None:
    connection.execute("""CREATE TABLE wire_delivery_budgets (
        lineage_id TEXT NOT NULL REFERENCES lineages(lineage_id), wire_id TEXT NOT NULL,
        generation BIGINT NOT NULL DEFAULT 0, PRIMARY KEY(lineage_id,wire_id))""")
    connection.execute("""CREATE TABLE wire_delivery_receipts (
        lineage_id TEXT NOT NULL REFERENCES lineages(lineage_id), wire_id TEXT NOT NULL,
        message_id TEXT NOT NULL, generation BIGINT NOT NULL,
        status TEXT NOT NULL CHECK(status IN ('reserved','sent')),
        accepted_message_id TEXT NOT NULL DEFAULT '', PRIMARY KEY(lineage_id,wire_id,message_id))""")
    connection.execute(
        "CREATE INDEX wire_delivery_count_idx ON wire_delivery_receipts(lineage_id,wire_id,generation,status)"
    )


def _database():
    from .state_store import get_lineage_data_store

    return get_lineage_data_store().database


@contextmanager
def _locked(lineage_id: str, wire_id: str):
    database = _database()
    with database.transaction() as connection:
        connection.execute(
            "INSERT INTO wire_delivery_budgets(lineage_id,wire_id) VALUES (%s,%s) ON CONFLICT DO NOTHING",
            (lineage_id, wire_id),
        )
        generation = connection.execute(
            "SELECT generation FROM wire_delivery_budgets WHERE lineage_id=%s AND wire_id=%s FOR UPDATE",
            (lineage_id, wire_id),
        ).fetchone()["generation"]
        yield connection, generation


def _changed(lineage_id: str) -> None:
    from .changes import lineage_changed

    _database().after_commit(lambda: lineage_changed(lineage_id, runtime=True))


def views(
    lineage_id: str, configs: dict[str, dict[str, Any]]
) -> dict[str, dict[str, Any]]:
    if not configs:
        return {}
    with _database().read() as connection:
        rows = connection.execute(
            """SELECT b.wire_id,b.generation,
            COUNT(*) FILTER (WHERE r.status='sent' AND r.generation=b.generation) AS sent_count,
            COUNT(*) FILTER (WHERE r.status='reserved') AS reserved_count
            FROM wire_delivery_budgets b LEFT JOIN wire_delivery_receipts r
              ON r.lineage_id=b.lineage_id AND r.wire_id=b.wire_id
            WHERE b.lineage_id=%s AND b.wire_id=ANY(%s)
            GROUP BY b.wire_id,b.generation""",
            (lineage_id, list(configs)),
        ).fetchall()
    states = {row["wire_id"]: dict(row) for row in rows}
    result = {}
    for wire_id, config in configs.items():
        state = states.get(
            wire_id, {"generation": 0, "sent_count": 0, "reserved_count": 0}
        )
        maximum = normalize_optional_limit(config.get("max_messages"))
        result[wire_id] = {
            **state,
            "max_messages": maximum,
            "remaining_messages": limit_remaining(maximum, state["sent_count"]),
            "message_limit_reached": limit_reached(maximum, state["sent_count"]),
        }
    return result


def reserve(
    lineage_id: str, wire_id: str, message_id: str, maximum: int | None
) -> dict[str, Any]:
    with _locked(lineage_id, wire_id) as (connection, generation):
        receipt = connection.execute(
            "SELECT status,accepted_message_id FROM wire_delivery_receipts WHERE lineage_id=%s AND wire_id=%s AND message_id=%s",
            (lineage_id, wire_id, message_id),
        ).fetchone()
        if receipt:
            return dict(receipt)
        used = connection.execute(
            """SELECT count(*) AS used FROM wire_delivery_receipts
            WHERE lineage_id=%s AND wire_id=%s AND (status='reserved' OR generation=%s)""",
            (lineage_id, wire_id, generation),
        ).fetchone()["used"]
        if limit_reached(maximum, used):
            return {"status": "limited"}
        connection.execute(
            "INSERT INTO wire_delivery_receipts(lineage_id,wire_id,message_id,generation,status) VALUES (%s,%s,%s,%s,'reserved')",
            (lineage_id, wire_id, message_id, generation),
        )
        _changed(lineage_id)
        return {"status": "reserved"}


def accept(
    lineage_id: str, wire_id: str, message_id: str, accepted_message_id: str
) -> None:
    with _locked(lineage_id, wire_id) as (connection, generation):
        changed = connection.execute(
            """UPDATE wire_delivery_receipts SET status='sent',generation=%s,accepted_message_id=%s
            WHERE lineage_id=%s AND wire_id=%s AND message_id=%s AND status='reserved'""",
            (generation, accepted_message_id, lineage_id, wire_id, message_id),
        )
        if changed.rowcount:
            _changed(lineage_id)


def release(lineage_id: str, wire_id: str, message_id: str) -> None:
    """Release only a send known not to have committed; unknown sends stay fenced."""
    with _locked(lineage_id, wire_id) as (connection, _generation):
        changed = connection.execute(
            "DELETE FROM wire_delivery_receipts WHERE lineage_id=%s AND wire_id=%s AND message_id=%s AND status='reserved'",
            (lineage_id, wire_id, message_id),
        )
        if changed.rowcount:
            _changed(lineage_id)


def reset(lineage_id: str, wire_id: str, actor: str) -> dict[str, Any]:
    from . import lineage_domain
    from .lineage_program_queries import _compiled_program

    with lineage_domain._lineage_execution_authority(lineage_id):
        lineage = lineage_domain._read_lineage(lineage_id, actor=actor)
        lineage_domain._ensure_manager(lineage, actor)
        wire = next(
            (
                wire
                for wire in _compiled_program(lineage)["wires"]
                if wire["id"] == wire_id
            ),
            None,
        )
        residents = {node["id"] for node in lineage.get("nodes") or []}
        if not wire or wire["kind"] != "route" or wire["target"] not in residents:
            raise ValueError("只能重置指向 Resident 的 Route 发送次数")
        with _locked(lineage_id, wire_id) as (connection, _generation):
            connection.execute(
                "UPDATE wire_delivery_budgets SET generation=generation+1 WHERE lineage_id=%s AND wire_id=%s",
                (lineage_id, wire_id),
            )
            _changed(lineage_id)
        return views(lineage_id, {wire_id: wire.get("config") or {}})[wire_id]


def forget(lineage_id: str, wire_ids: set[str]) -> None:
    if wire_ids:
        with _database().transaction() as connection:
            for table in COLUMNS:
                connection.execute(
                    f"DELETE FROM {table} WHERE lineage_id=%s AND wire_id=ANY(%s)",
                    (lineage_id, list(wire_ids)),
                )


def move(source_id: str, target_id: str, wire_ids: set[str]) -> None:
    if wire_ids:
        with _database().transaction() as connection:
            for table in COLUMNS:
                connection.execute(
                    f"UPDATE {table} SET lineage_id=%s WHERE lineage_id=%s AND wire_id=ANY(%s)",
                    (target_id, source_id, list(wire_ids)),
                )
