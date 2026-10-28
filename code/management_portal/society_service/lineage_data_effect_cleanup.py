"""Effect recovery cleanup tasks: instructions to acknowledge once an activation settles."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

from management_common.database_time import iso_time
from management_common.json_columns import json_value
from .lineage_data_columns import json_column


def _effect_cleanup_row(row: Any) -> dict[str, Any]:
    return {"lineage_id": row["lineage_id"], "activation_key": row["activation_key"],
            "instructions": json_value(row["instructions_json"]), "created_at": iso_time(row["created_at"])}


class EffectCleanupQueue:
    """Queue of Effect recovery cleanup tasks."""

    def __init__(self, database: Any) -> None:
        self.database = database

    def enqueue_effect_cleanup(self, lineage_id: str, activation_key: str, instructions: dict[str, list[str]]) -> None:
        if not lineage_id or not activation_key or not isinstance(instructions, dict):
            raise ValueError("Effect cleanup requires its activation identity and caller mapping")
        for caller, ids in instructions.items():
            if not isinstance(caller, str) or not caller or not isinstance(ids, list) or not all(isinstance(i, str) and i for i in ids):
                raise ValueError("Effect cleanup instructions must map callers to instruction IDs")
        def enqueue(connection: Any) -> None:
            connection.execute("INSERT INTO effect_recovery_cleanup(lineage_id,activation_key,instructions_json,created_at) "
                               "VALUES (%s,%s,'{}',%s) ON CONFLICT DO NOTHING",
                               (lineage_id, activation_key, datetime.now(timezone.utc).isoformat()))
            row = connection.execute("SELECT instructions_json FROM effect_recovery_cleanup "
                                     "WHERE lineage_id=%s AND activation_key=%s FOR UPDATE", (lineage_id, activation_key)).fetchone()
            stored = json_value(row["instructions_json"])
            merged = deepcopy(stored)
            for caller, ids in instructions.items():
                merged[caller] = sorted(set(merged.get(caller, [])) | set(ids))
            if merged != stored:
                value = json_column(merged)
                connection.execute("UPDATE effect_recovery_cleanup SET instructions_json=%s WHERE lineage_id=%s AND activation_key=%s",
                                   (value, lineage_id, activation_key))
        self.database.write(enqueue)

    def effect_cleanup_task(self, lineage_id: str, activation_key: str) -> dict[str, Any] | None:
        with self.database.read() as connection:
            row = connection.execute("SELECT * FROM effect_recovery_cleanup WHERE lineage_id=%s AND activation_key=%s",
                                     (lineage_id, activation_key)).fetchone()
        return _effect_cleanup_row(row) if row else None

    def effect_cleanup_tasks(self, *, lineage_id: str | None = None, limit: int = 100,
                             after_key: tuple[str, str, str] | None = None) -> list[dict[str, Any]]:
        predicates, values = [], []
        if lineage_id is not None:
            predicates.append("lineage_id=%s")
            values.append(lineage_id)
        if after_key is not None:
            if len(after_key) != 3:
                raise ValueError("Effect cleanup cursor requires created_at, Lineage and activation")
            predicates.append("(created_at,lineage_id,activation_key)>(%s,%s,%s)")
            values.extend(after_key)
        with self.database.read() as connection:
            rows = connection.execute("SELECT * FROM effect_recovery_cleanup " +
                                      ("WHERE " + " AND ".join(predicates) + " " if predicates else "") +
                                      "ORDER BY created_at,lineage_id,activation_key LIMIT %s",
                                      (*values, max(1, min(int(limit), 1000))),).fetchall()
        return [_effect_cleanup_row(row) for row in rows]

    def complete_effect_cleanup(self, lineage_id: str, activation_key: str, *, expected_instructions: dict[str, list[str]]) -> bool:
        expected = json_column(expected_instructions)
        return bool(self.database.write(lambda connection: connection.execute(
            "DELETE FROM effect_recovery_cleanup WHERE lineage_id=%s AND activation_key=%s AND instructions_json=%s",
            (lineage_id, activation_key, expected),
        ).rowcount))
